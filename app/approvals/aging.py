# -*- coding: utf-8 -*-
"""
TC Platform — approval aging: how long a request has been sitting on a signature.

WHY THIS EXISTS. The engine already escalates a rung that is ABSENT a signer
(services.resolve_escalation, at ladder-build time) and stamps one that passed
its SLA (services.run_escalations). Neither of those ANSWERS the buyer's
question, which is not "did a rule fire" but "what is stuck right now, and who
is sitting on it". Until this screen there was nowhere in the platform to ask
it, so it was asked by walking the corridor.

WHAT "AGED" MEANS HERE. Two different clocks, and they are not interchangeable:

  * days on the CURRENT rung — from pr_steps.activated_at, which is written
    exactly once per rung: at submit for rung 1 (services.submit_pr) and on the
    advance that opens each later rung (services.act_on_step). That is the
    number a signer is answerable for, and the only one worth belling anyone
    about. A request three weeks old whose rung activated an hour ago is not
    stuck on anybody.
  * days since the request was SUBMITTED — pr_requests.submitted_at. That is
    what the requester feels, and it is why the screen shows both.

pr_events is NOT used for either. It records that an approval happened, in prose
meant for a human ("Warehouse approved (R) — ..."), so deriving a rung's start
from it would mean parsing sentences; activated_at is the column that genuinely
records the fact.

THRESHOLDS ARE ADMIN KNOBS, not constants: constants.WORKFLOW_SETTINGS carries
aging_warn_days and aging_overdue_days, read through services.num_setting like
every other knob, so a plant that signs weekly is not permanently red.

THE BELL FIRES ONCE PER RUNG PER PERSON, EVER — not once per run. See
run_aging_alerts(): the dedupe stamp is a pr_events row keyed on the STEP id,
which is created once and never re-activates, so a daily job cannot nag.
"""
import logging

from flask import Blueprint, render_template, request

from app.auth import login_required, permission_required, current_user
from app.db import get_db
from app.approvals import constants as C
from app.approvals import services as svc

log = logging.getLogger("tc.procurement.aging")

bp = Blueprint("proc_aging", __name__, url_prefix="/procurement")

HOURS_PER_DAY = 24.0

# Client-side strings for this screen. Kept here rather than in
# app/static/i18n/*.json for the reason app/approvals/catalogue.py keeps its
# own: that file is shared by every module and merged on release.
I18N = {
    "aging.nav": ("Aging", "زمن الانتظار", "Bekleme süresi"),
    "aging.title": ("Approval aging", "زمن انتظار الاعتمادات",
                    "Onay bekleme süresi"),
    "aging.sub": (
        "Every request still waiting on a signature, the one it has waited "
        "longest on first — which rung it is on, who can sign it, and how long "
        "it has been there.",
        "كل طلب ما زال ينتظر توقيعاً، والأطول انتظاراً أولاً — عند أي مرحلة "
        "يقف، ومن يستطيع توقيعه، ومنذ متى وهو واقف.",
        "İmza bekleyen her talep, en uzun bekleyen başta — hangi basamakta "
        "olduğu, kimin imzalayabileceği ve ne zamandır beklediği."),
    "aging.k_waiting": ("Waiting on a signature", "بانتظار توقيع",
                        "İmza bekleyen"),
    "aging.k_overdue": ("Overdue", "متأخر", "Gecikmiş"),
    "aging.k_warn": ("Due soon", "يقترب من التأخر", "Gecikmek üzere"),
    "aging.k_value": ("Value held up (EGP)", "القيمة المحتجزة (ج.م)",
                      "Bekleyen tutar (EGP)"),
    "aging.rule": ("Amber at {warn} days on a rung, red at {overdue}. "
                   "Both are set on the Workflow & Governance page.",
                   "اللون الكهرماني عند {warn} يوم على المرحلة، والأحمر عند "
                   "{overdue}. يُضبط الرقمان من صفحة سير العمل والحوكمة.",
                   "Bir basamakta {warn} günde sarı, {overdue} günde kırmızı. "
                   "İkisi de İş Akışı ve Yönetişim sayfasından ayarlanır."),
    "aging.dept": ("Department", "الإدارة", "Departman"),
    "aging.all_depts": ("All departments", "كل الإدارات", "Tüm departmanlar"),
    "aging.rung": ("Rung", "المرحلة", "Basamak"),
    "aging.all_rungs": ("All rungs", "كل المراحل", "Tüm basamaklar"),
    "aging.apply": ("Apply", "تطبيق", "Uygula"),
    "aging.clear": ("Clear", "مسح", "Temizle"),
    "aging.stuck": ("What is stuck", "الطلبات المتوقفة", "Bekleyen talepler"),
    "aging.request": ("Request", "الطلب", "Talep"),
    "aging.value": ("Value", "القيمة", "Tutar"),
    "aging.signers": ("Who can sign", "من يستطيع التوقيع", "Kim imzalayabilir"),
    "aging.on_rung": ("On this rung", "على هذه المرحلة", "Bu basamakta"),
    "aging.since_submit": ("Since submitted", "منذ الإرسال",
                           "Gönderildiğinden beri"),
    "aging.days": ("days", "يوم", "gün"),
    "aging.nobody": ("Nobody active can sign this — delegate the authority or "
                     "add a role holder.",
                     "لا يوجد شخص نشط يستطيع التوقيع — فوّض الصلاحية أو أضف من "
                     "يحمل الدور.",
                     "Bunu imzalayabilecek aktif kimse yok — yetkiyi devredin "
                     "veya rolü taşıyan birini ekleyin."),
    "aging.none": ("Nothing is waiting on a signature.",
                   "لا يوجد ما ينتظر توقيعاً.",
                   "İmza bekleyen bir şey yok."),
}

# The pending set: one row per request, for the rung it is ACTUALLY sitting on.
# `s.seq = p.current_seq` is what makes it one row — a ladder holds a pending row
# for every rung ahead of the current one, and those have not started waiting.
_PENDING_SQL = """
SELECT s.id AS step_id, s.seq AS seq, s.stage AS stage, s.esc_role AS esc_role,
       s.activated_at AS activated_at,
       p.id AS pr_id, p.pr_no AS pr_no, p.title AS title,
       p.department AS department, p.requester AS requester,
       p.requester_name AS requester_name, p.currency AS currency,
       p.total AS total, p.tax_rate AS tax_rate, p.fx_rate AS fx_rate,
       p.submitted_at AS submitted_at, p.created_at AS created_at
FROM pr_steps s JOIN pr_requests p ON p.id = s.pr_id
WHERE s.status = 'pending' AND p.status = 'pending'
  AND s.seq = p.current_seq AND COALESCE(p.is_active, 1) = 1
"""


def _days(ts):
    """Days elapsed since an ISO timestamp, or None. services._age_hours does the
    parsing (and the "never negative" clamp) for the escalation engine; sharing it
    is what keeps this screen and that engine measuring the same thing."""
    hrs = svc._age_hours(ts)
    return None if hrs is None else hrs / HOURS_PER_DAY


def _pending_rows(conn, department=None, stage=None):
    sql, args = _PENDING_SQL, []
    if department:
        sql += " AND p.department = ?"
        args.append(department)
    if stage:
        sql += " AND s.stage = ?"
        args.append(stage)
    return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]


def _aged(row, warn_days, overdue_days):
    """One pending row, with both clocks and the band it falls in."""
    on_rung = _days(row.get("activated_at") or row.get("submitted_at")) or 0.0
    since = _days(row.get("submitted_at") or row.get("created_at")) or 0.0
    row["days_on_rung"] = round(on_rung, 1)
    row["days_since_submit"] = round(since, 1)
    # Compared on the RAW figure, not the rounded one, so a row is never shown
    # as "4.0 days" while being judged on 3.96.
    row["level"] = ("overdue" if on_rung >= overdue_days
                    else "warn" if on_rung >= warn_days else "ok")
    row["value_egp"] = svc.egp_commitment(row)
    return row


def _signers(conn, rows, lang):
    """{(stage, esc_role): {roles, role_names, people}} for the rows in hand.

    Cached per rung KIND rather than per row: a queue of forty requests parked on
    the warehouse rung asks the same question forty times, and eligible_approvers
    costs two queries each time."""
    roles_map = svc.stage_roles_map(conn)
    names = {r["username"]: (r["full_name"] or r["username"])
             for r in conn.execute(
                 "SELECT username, full_name FROM users WHERE is_active=1").fetchall()}
    out = {}
    for row in rows:
        key = (row["stage"], row.get("esc_role") or "")
        if key in out:
            continue
        esc = svc._csv_set(row.get("esc_role"))
        roles = sorted(svc.step_roles(row, roles_map))
        who = sorted(svc.eligible_approvers(conn, row["stage"],
                                            roles=esc or None))
        out[key] = {
            "roles": roles,
            "role_names": svc.role_names(",".join(roles), lang),
            "people": [names.get(u, u) for u in who],
        }
    return out


def t(key, lang="en"):
    """One I18N string for a reader. Server-side because the numbers in
    'aging.rule' have to sit inside the sentence, and Arabic and Turkish do not
    put them where English does."""
    idx = {"en": 0, "ar": 1, "tr": 2}.get(lang, 0)
    return I18N[key][idx]


def aging_view(department=None, stage=None, lang="en"):
    """Everything the /procurement/aging screen renders, longest wait first."""
    conn = get_db()
    try:
        warn = int(svc.num_setting(conn, "aging_warn_days"))
        overdue = int(svc.num_setting(conn, "aging_overdue_days"))
        rows = [_aged(r, warn, overdue)
                for r in _pending_rows(conn, department, stage)]
        signers = _signers(conn, rows, lang)
        # The filter options come from the UNFILTERED pending set: options built
        # from `rows` would vanish the moment one was picked, leaving no way back
        # except editing the URL. Unfiltered, the rows in hand ARE that set.
        every = rows if not (department or stage) else _pending_rows(conn)
    finally:
        conn.close()

    for r in rows:
        r["signers"] = signers[(r["stage"], r.get("esc_role") or "")]
    rows.sort(key=lambda r: (-r["days_on_rung"], r["pr_no"] or ""))

    stage_labels = svc.labels(lang if lang in ("en", "ar", "tr") else "en")["stage"]
    order = {s: i for i, s in enumerate(C.DOAM_LADDER)}
    for r in rows:
        r["stage_label"] = stage_labels.get(r["stage"], svc.stage_label(r["stage"]))
    return {
        "rows": rows,
        "warn_days": warn, "overdue_days": overdue,
        "department": department or "", "stage": stage or "",
        "departments": sorted({(r["department"] or "").strip()
                               for r in every if (r["department"] or "").strip()}),
        "stages": [{"key": s, "label": stage_labels.get(s, svc.stage_label(s))}
                   for s in sorted({r["stage"] for r in every},
                                   key=lambda s: order.get(s, len(order)))],
        "n": len(rows),
        "n_overdue": sum(1 for r in rows if r["level"] == "overdue"),
        "n_warn": sum(1 for r in rows if r["level"] == "warn"),
        "value_egp": round(sum(r["value_egp"] for r in rows), 2),
        "rule": t("aging.rule", lang).format(warn=warn, overdue=overdue),
    }


def _alert_stamp(step_id, username):
    return "%d|%s" % (int(step_id), username)


def run_aging_alerts():
    """Bell the people who can sign an OVERDUE rung. Returns how many were told.

    Idempotent per (rung, person), not per day: each notification writes a
    pr_events row stamped with the STEP id and the username, and a step id is
    minted once when the ladder is built and never re-activates. So the daily job
    can run every hour and a signer still hears about a given rung exactly once —
    which is the only version of this that stays believed. A person who becomes
    eligible LATER (a delegation, a new role holder) is still told, because the
    stamp is per person and theirs does not exist yet.

    Deliberately silent about rungs that are merely in the amber band: the screen
    is where "due soon" belongs. A bell is for the line that was crossed.
    """
    conn = get_db()
    told = 0
    try:
        overdue_days = int(svc.num_setting(conn, "aging_overdue_days"))
        rows = [r for r in _pending_rows(conn)
                if (_days(r.get("activated_at") or r.get("submitted_at")) or 0.0)
                >= overdue_days]
        if not rows:
            return 0
        pr_ids = sorted({r["pr_id"] for r in rows})
        ph = ",".join(["?"] * len(pr_ids))
        sent = {r["detail"] for r in conn.execute(
            "SELECT detail FROM pr_events WHERE pr_id IN (%s) AND action='aging_alert'"
            % ph, tuple(pr_ids)).fetchall()}
        for r in rows:
            esc = svc._csv_set(r.get("esc_role"))
            targets = svc.eligible_approvers(conn, r["stage"], roles=esc or None)
            fresh = sorted(u for u in targets
                           if u and _alert_stamp(r["step_id"], u) not in sent)
            if not fresh:
                continue
            days = round(_days(r.get("activated_at") or r.get("submitted_at")) or 0.0)
            label = svc.stage_label(r["stage"])
            svc.notify_users(
                conn, fresh, "warning", "Approval waiting %d days" % days,
                "%s has been waiting %d days for your %s signature — %s."
                % (r["pr_no"], days, label, r["title"] or "no title"),
                link=svc._pr_link(r["pr_id"]))
            for u in fresh:
                svc.audit(conn, r["pr_id"], "system", "aging_alert",
                          _alert_stamp(r["step_id"], u))
            told += len(fresh)
        conn.commit()
    finally:
        conn.close()
    return told


@bp.route("/aging")
@login_required
@permission_required("proc_view")
def index():
    user = current_user() or {}
    # Same bargain as approvals.index makes with run_escalations: opening the
    # screen is the scheduler, so the bell works on a host with no cron. Failure
    # to notify must never cost the reader the page they asked for.
    try:
        run_aging_alerts()
    except Exception:                                        # noqa: BLE001
        log.warning("aging alerts skipped", exc_info=True)
    v = aging_view(department=(request.args.get("dept") or "").strip() or None,
                   stage=(request.args.get("stage") or "").strip() or None,
                   lang=user.get("lang_pref") or "en")
    return render_template("approvals/aging.html", active="proc_aging", v=v)
