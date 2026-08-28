"""
TC Platform — main application routes.

Command Center, App Launcher, module pages (integrated + future-ready),
Reports, Health, Roadmap, and user preference persistence.
"""
import csv
import io
import sys
import platform as pyplatform
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, abort, g, Response, flash, send_file)

from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.db import get_db, log_audit, utcnow
from app.auth import login_required, permission_required, current_user, user_can
from app.security import (has_permission, role_label, ROLES, validate_password,
                          user_has_permission, system_scope)
from app.navigation import NAV
from app.services import health as health_svc
from app.services import seed_content as sc
from app.services import reports as reports_svc
from app.services.notify import (health_sync_due, sync_health_notifications,
                                 sync_system_notifications)

bp = Blueprint("main", __name__)

# The factory's clock, not the process's. Render runs UTC and the plant does not,
# so between 21:00 local and midnight every "days ago" / "days late" figure on the
# front page was a day short. Resolved once at import.
try:
    _TZ = ZoneInfo(Config.TC_TZ)
except Exception:      # host with no IANA database (bare Windows) — Türkiye is UTC+3, no DST
    _TZ = _timezone(_timedelta(hours=3))


def _today():
    return _datetime.now(_TZ).date()


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _systems():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM systems WHERE enabled = 1 ORDER BY sort_order").fetchall()
    finally:
        conn.close()
    return rows


def _system_by_key(key):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM systems WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row


def _unread_notifications(username=None):
    """Bell feed: broadcast notifications (target_user IS NULL) plus any addressed
    to this user. Older rows created before the target_user column are broadcast."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE target_user IS NULL OR target_user = ? "
            "ORDER BY id DESC LIMIT 30", (username,)).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE is_read = 0 "
            "AND (target_user IS NULL OR target_user = ?)", (username,)).fetchone()["c"]
    finally:
        conn.close()
    return rows, unread


# --------------------------------------------------------------------------
# Context processor — inject shared data into every template
# --------------------------------------------------------------------------
def _brand_logo_file():
    """Use a real uploaded logo if present (img/logo.png|jpg|svg|webp), else the
    built-in SVG mark. Drop your file at app/static/img/logo.<ext> to use it."""
    from flask import current_app
    import os
    base = os.path.join(current_app.static_folder, "img")
    for name in ("logo.png", "logo.svg", "logo.jpg", "logo.jpeg", "logo.webp"):
        if os.path.exists(os.path.join(base, name)):
            return "img/" + name
    return "img/logo-mark.svg"


# Blueprints a scope-locked user may reach: infra + their own system's launch
# goes through main/sso (self-gated). Feature blueprints are bounced. `api` and
# `garamento` stay open so the global header polling / assistant widget keep
# working on the pages they CAN see.
_SCOPE_OK_BLUEPRINTS = {None, "main", "auth", "sso", "accounts", "api", "garamento"}


@bp.before_app_request
def _enforce_system_scope():
    """Belt-and-suspenders for scope-locked roles (e.g. itsm_user): block every
    out-of-scope feature blueprint outright, so a hand-typed URL like /bi or
    /production can't bypass the hidden nav. Redirects them to their own system."""
    scope = system_scope(current_user())
    if scope is None:
        return
    if request.endpoint == "static" or request.blueprint in _SCOPE_OK_BLUEPRINTS:
        return
    return redirect(url_for("main.dashboard"))


@bp.app_context_processor
def inject_globals():
    user = current_user()
    notifs, unread = ([], 0)
    visible_nav = []
    if user:
        notifs, unread = _unread_notifications(user["username"])
        from app.navigation import WIP_KEYS
        is_admin = user_has_permission(user, "access_admin")
        scope = system_scope(user)   # None = unrestricted; a set = only those keys
        if scope is not None:
            # Scope-locked users only see bell alerts for their own system(s).
            notifs = [n for n in notifs if not n["module"] or n["module"] in scope]
            unread = sum(1 for n in notifs if not n["is_read"])
        for section in NAV:
            items = [it for it in section["items"]
                     if user_has_permission(user, it[4]) and it[0] not in WIP_KEYS
                     and (scope is None or it[0] in scope)]
            if items:
                visible_nav.append({"section": section["section"], "items": items})
        # Placeholder modules are hidden from the sidebar for EVERYONE now,
        # admins included. Six "coming soon" rows sitting among working modules
        # was a real part of why this menu felt confusing, and an admin does not
        # need a menu entry to reach an unbuilt page: they stay in the App
        # Launcher, which lists the `systems` table rather than NAV.
        del is_admin
    return {
        "cu": user,
        "cu_role_label": role_label(user["role"]) if user else "",
        "nav": visible_nav,
        "notifications": notifs,
        "unread_count": unread,
        "can": user_can,
        "app_name": "TC Platform",
        "app_subtitle": "Unified Digital Operations, IT, AI & Business Control Center",
        "brand_logo": _brand_logo_file(),
    }


# --------------------------------------------------------------------------
# Factory pulse — the cross-module executive risk strip on the front page.
# --------------------------------------------------------------------------
# Every string on the strip is COMPUTED here, not looked up in a dictionary, so
# it is emitted as data-loc-en/ar/tr (the mechanism app.js already swaps for
# server-produced text). A data-i18n key would render as the literal raw key to
# every user in every language unless it also exists in app/static/i18n/*.json,
# and this lane does not own those files.
def _loc(en, ar, tr):
    return {"en": en, "ar": ar, "tr": tr}


def _ar_count(n, dual, plural, singular):
    """Arabic counted-noun agreement, which has three forms where English has two:
    the dual carries the count itself (يومين, not "2 يومًا"), 3–10 take the plural
    (5 أيام) and 11+ take the accusative singular (13 يومًا). One form for all
    of them reads as broken Arabic to the reader this work exists for."""
    if n == 2:
        return dual
    return "%d %s" % (n, plural if 3 <= n <= 10 else singular)


# Severity order for the strip. Tiles are emitted in code order (one block per
# module) and sorted by this at the end, so "19 spare parts at zero stock" cannot
# sit below "0 materials short" just because warehouse.py is read first.
_PULSE_RANK = {"crit": 0, "warn": 1, "info": 2, "good": 3}

# tone -> (kpi class, badge class, state label). Only classes that already exist
# in app.css / the badge set are used.
_PULSE_TONE = {
    "crit": ("warn", "critical", _loc("Act now", "تحرك الآن", "Şimdi harekete geç")),
    "warn": ("warn", "warning", _loc("Watch", "راقب", "İzle")),
    "info": ("info", "info", _loc("Due", "مستحق", "Vadesi geldi")),
    "good": ("good", "live", _loc("On track", "على المسار", "Yolunda")),
}

# Every tile label and denominator caption, in the three platform languages.
_PULSE_TEXT = {
    "exec.orders_late": _loc("Orders behind schedule", "طلبات متأخرة عن الجدول",
                             "Programın gerisindeki siparişler"),
    "exec.orders_week": _loc("Shipping within 7 days", "الشحن خلال 7 أيام",
                             "7 gün içinde sevkiyat"),
    "exec.mat_short": _loc("Materials at or below reorder", "مواد عند حد إعادة الطلب أو أقل",
                           "Yeniden sipariş seviyesindeki malzemeler"),
    "exec.qc_rate": _loc("Defect rate, last 7 days", "معدل العيوب، آخر 7 أيام",
                         "Hata oranı, son 7 gün"),
    "exec.qc_failed": _loc("Lots rejected, last 7 days", "دفعات مرفوضة، آخر 7 أيام",
                           "Reddedilen partiler, son 7 gün"),
    "exec.lines_below": _loc("Lines below target", "خطوط أقل من الهدف",
                             "Hedefin altındaki hatlar"),
    "exec.spares_out": _loc("Spare parts at zero stock", "قطع غيار برصيد صفر",
                            "Stoğu sıfır yedek parçalar"),
    "exec.pr_sign": _loc("Requests waiting for my signature", "طلبات بانتظار توقيعي",
                         "İmzamı bekleyen talepler"),
    "exec.certs_expiring": _loc("Certificates expiring within 30 days",
                                "شهادات تنتهي خلال 30 يومًا",
                                "30 gün içinde biten sertifikalar"),
    "exec.scope_orders": _loc("live orders", "طلبات جارية", "aktif sipariş"),
    "exec.scope_materials": _loc("materials tracked", "مادة متابعة", "izlenen malzeme"),
    "exec.scope_lots": _loc("lots inspected", "دفعة تم فحصها", "denetlenen parti"),
    "exec.scope_lines": _loc("lines reporting", "خط منتِج", "raporlayan hat"),
    "exec.scope_spares": _loc("spare parts", "قطعة غيار", "yedek parça"),
    "exec.scope_prs": _loc("requests open", "طلب شراء مفتوح", "açık talep"),
    "exec.scope_certs": _loc("certificates held", "شهادة", "sertifika"),
}

_PULSE_HEAD = {
    "title": _loc("Factory pulse — what needs action now",
                  "نبض المصنع — ما يحتاج إلى إجراء الآن",
                  "Fabrika nabzı — şimdi aksiyon gerekenler"),
    "my_work": _loc("My action centre", "مركز مهامي", "Eylem merkezim"),
    "none": _loc("No factory data has been recorded yet — these indicators appear "
                 "as the production modules are used.",
                 "لم تُسجَّل بيانات المصنع بعد — تظهر هذه المؤشرات مع استخدام وحدات الإنتاج.",
                 "Henüz fabrika verisi kaydedilmedi — bu göstergeler üretim "
                 "modülleri kullanıldıkça görünür."),
    # "Nothing measured" and "nothing you are allowed to see" are different
    # sentences. Telling a user with no module permissions that the factory has
    # no data is a lie while 3 orders and 10 spare parts sit in the same DB.
    "locked": _loc("You do not have access to any production module yet — ask an "
                   "administrator to grant it.",
                   "لا تملك صلاحية الوصول إلى أي وحدة إنتاج بعد — اطلب من المسؤول منحها لك.",
                   "Henüz hiçbir üretim modülüne erişiminiz yok — bir yöneticiden "
                   "yetki isteyin."),
}


def _greeting(user):
    """Server-computed, so it is emitted as data-loc-* — a Jinja-concatenated
    greeting cannot be translated. Clock is the factory's, not the process's."""
    h = _datetime.now(_TZ).hour
    name = (user or {}).get("full_name") or (user or {}).get("username") or ""
    en, ar, tr = (("Good morning", "صباح الخير", "Günaydın") if h < 12 else
                  ("Good afternoon", "طاب مساؤك", "İyi günler") if h < 18 else
                  ("Good evening", "مساء الخير", "İyi akşamlar"))
    # No name -> "Good morning." and not "Good morning, ."
    return _loc(en + (", %s." % name if name else "."),
                ar + ("، %s." % name if name else "."),
                tr + (", %s." % name if name else "."))


def _verdict(tiles):
    """'3 of 7 indicators need action' — no query, derived from the tiles in hand.
    Whole sentences per language: number order differs in Turkish."""
    total = len(tiles)
    if not total:
        return None
    need = sum(1 for t in tiles if t["rank"] <= 1)      # crit + warn
    if not need:
        return _loc("All %d indicators are on track." % total,
                    "جميع المؤشرات (%d) على المسار الصحيح." % total,
                    "%d göstergenin tamamı yolunda." % total)
    return _loc("%d of %d indicators need action." % (need, total),
                "%d من %s تحتاج إلى إجراء." % (need, _ar_count(total, "مؤشرين", "مؤشرات", "مؤشرًا")),
                "%d göstergeden %d tanesi aksiyon gerektiriyor." % (total, need))


def _freshness(asof):
    """'Production last reported 2026-08-06 · 13 days ago.' Reuses the MAX(work_date)
    the lines aggregate already selected — zero extra queries."""
    if not asof:
        return None
    try:
        d = _date.fromisoformat(str(asof)[:10])
    except ValueError:
        return None
    n = (_today() - d).days
    if n <= 0:
        ago = ("today", "اليوم", "bugün")
    elif n == 1:
        ago = ("yesterday", "أمس", "dün")
    else:
        ago = ("%d days ago" % n, "قبل %s" % _ar_count(n, "يومين", "أيام", "يومًا"),
               "%d gün önce" % n)
    return _loc("Production last reported %s · %s" % (d.isoformat(), ago[0]),
                "آخر تقرير إنتاج %s · %s" % (d.isoformat(), ago[1]),
                "Üretim son olarak %s tarihinde raporlandı · %s" % (d.isoformat(), ago[2]))


def _factory_pulse(user):
    """One cross-module "what is at risk right now" snapshot for the command center.

    The order page got _order_360 (see app/routes/orders.py); the front page never
    did, so a director could not see the state of the factory without opening
    eleven modules. Same defensive contract as _order_360, plus two rules the
    most-loaded page in the platform needs:

      * every block is permission-gated with the module's OWN view permission, so
        a user never sees a number from a module they cannot open;
      * every block is ONE aggregate statement over the whole table — never a
        query per order — and is independently wrapped: an absent module, a failed
        query or an empty table drops that ONE tile instead of 500-ing the page.
        The rollback matters because PostgreSQL aborts the transaction on a failed
        statement, which would otherwise poison every later block.

    A block with nothing to measure emits NO tile at all: a fabricated "0 defects"
    on an empty quality table would read as a real measurement.

    Query cost: 6 aggregate statements on one shared connection + up to 3 from the
    procurement signature queue = 9 max, constant in the number of orders.
    """
    # "permitted" separates the two empty states the page must not confuse: no
    # data recorded, versus no module this user is allowed to look at.
    out = {"tiles": [], "asof": None, "orders": [], "orders_empty": False,
           "permitted": False}
    tiles = out["tiles"]
    if not user:
        return out
    today = _today()
    d_today = today.isoformat()
    d_week = (today + _timedelta(days=7)).isoformat()
    d_month = (today + _timedelta(days=30)).isoformat()
    d_since = (today - _timedelta(days=7)).isoformat()

    try:
        conn = get_db()
    except Exception:
        return out

    def tile(k, icon, label, n, url, tone, total=None, scope=None, suffix=""):
        klass, badge, state = _PULSE_TONE[tone]
        # label/scope/state are resolved to {en,ar,tr} here and rendered as
        # data-loc-* — never as data-i18n, which would ship a raw key.
        return {"k": k, "icon": icon, "label": _PULSE_TEXT[label], "n": n, "url": url,
                "klass": klass, "badge": badge, "state": state, "rank": _PULSE_RANK[tone],
                "total": total, "scope": _PULSE_TEXT.get(scope), "suffix": suffix}

    def block(perm, fn):
        if not user_has_permission(user, perm):
            return
        out["permitted"] = True
        try:
            got = fn()
            if got:
                tiles.extend(got)
        except Exception:
            try:
                conn.rollback()   # PostgreSQL aborts the whole tx on a failed statement
            except Exception:
                pass

    # Orders behind schedule / shipping this week — the two numbers a director
    # acts on first: a missed T&A milestone is the earliest signal a ship date
    # will slip, and the 7-day window is this week's actual commitment.
    def _orders():
        r = conn.execute(
            "SELECT COUNT(DISTINCT o.id) AS live, "
            "COUNT(DISTINCT CASE WHEN m.actual_date IS NULL AND m.planned_date IS NOT NULL "
            "                    AND m.planned_date < ? THEN o.id END) AS late, "
            "COUNT(DISTINCT CASE WHEN o.ship_date IS NOT NULL AND o.ship_date >= ? "
            "                    AND o.ship_date <= ? THEN o.id END) AS wk "
            "FROM ord_orders o LEFT JOIN ord_milestones m ON m.order_id = o.id "
            "WHERE o.status NOT IN ('shipped','closed','cancelled')",
            (d_today, d_today, d_week)).fetchone()
        live = int(r["live"] or 0)
        if not live:
            return None
        late, wk = int(r["late"] or 0), int(r["wk"] or 0)
        return [
            tile("orders_late", "clock", "exec.orders_late", late,
                 url_for("orders.tna"), "crit" if late else "good",
                 live, "exec.scope_orders"),
            tile("orders_week", "rocket", "exec.orders_week", wk,
                 url_for("orders.listing"), "info" if wk else "good",
                 live, "exec.scope_orders"),
        ]
    block("view_dashboard", _orders)

    # Material at or below reorder level — this is what stops the cutting room.
    def _material():
        r = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN (stock_qty - reserved_qty) <= reorder_level THEN 1 ELSE 0 END) AS short "
            "FROM wh_materials WHERE is_active = 1").fetchone()
        total = int(r["total"] or 0)
        if not total:
            return None
        short = int(r["short"] or 0)
        return [tile("mat_short", "boxes", "exec.mat_short", short,
                     url_for("warehouse.materials"), "crit" if short else "good",
                     total, "exec.scope_materials")]
    block("wh_view", _material)

    # Quality over the last 7 days: the defective-unit rate (the trend a director
    # steers on) and the count of lots actually rejected (the units on hold now).
    def _quality():
        r = conn.execute(
            "SELECT COUNT(*) AS lots, SUM(units_inspected) AS u, SUM(defective_units) AS d, "
            "SUM(CASE WHEN verdict = 'fail' THEN 1 ELSE 0 END) AS failed "
            "FROM qc_inspections WHERE inspection_date IS NOT NULL AND inspection_date >= ?",
            (d_since,)).fetchone()
        lots = int(r["lots"] or 0)
        units = float(r["u"] or 0)
        if not lots or units <= 0:
            return None      # nothing inspected this week — "0%" would be a lie
        rate = round(100.0 * float(r["d"] or 0) / units, 1)
        failed = int(r["failed"] or 0)
        return [
            tile("qc_rate", "shield", "exec.qc_rate", rate, url_for("quality.index"),
                 "crit" if rate >= 2.5 else ("warn" if rate >= 1.0 else "good"),
                 lots, "exec.scope_lots", suffix="%"),
            tile("qc_failed", "alert", "exec.qc_failed", failed,
                 url_for("quality.inspections"), "crit" if failed else "good",
                 lots, "exec.scope_lots"),
        ]
    block("qc_view", _quality)

    # Lines that missed target on the last day production actually reported —
    # the output gap, before it becomes a late shipment.
    def _lines():
        r = conn.execute(
            "SELECT COUNT(*) AS lines_n, SUM(CASE WHEN a < t THEN 1 ELSE 0 END) AS below, "
            "(SELECT MAX(work_date) FROM mes_hourly) AS asof FROM ("
            "SELECT line_id, SUM(actual_qty) AS a, SUM(target_qty) AS t FROM mes_hourly "
            "WHERE work_date = (SELECT MAX(work_date) FROM mes_hourly) "
            "GROUP BY line_id HAVING SUM(target_qty) > 0) q").fetchone()
        # Same statement, one more column: the day production last reported.
        # A fresh-looking output number over two-week-old data is the same lie as
        # an inert control, so the page states the date it is reading.
        out["asof"] = r["asof"]
        n = int(r["lines_n"] or 0)
        if not n:
            return None
        below = int(r["below"] or 0)
        return [tile("lines_below", "factory", "exec.lines_below", below,
                     url_for("mes.board"), "warn" if below else "good",
                     n, "exec.scope_lines")]
    block("mes_view", _lines)

    # Spare parts at zero stock — the direct cause of a machine standing idle.
    def _spares():
        r = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN stock_qty <= 0 THEN 1 ELSE 0 END) AS zero_n "
            "FROM mnt_spare_parts WHERE is_active = 1").fetchone()
        total = int(r["total"] or 0)
        if not total:
            return None
        zero_n = int(r["zero_n"] or 0)
        return [tile("spares_out", "settings", "exec.spares_out", zero_n,
                     url_for("maintenance.spares"), "crit" if zero_n else "good",
                     total, "exec.scope_spares")]
    block("maint_view", _spares)

    # Purchase requests blocked on THIS user's signature — the one number on the
    # page only this user can clear.
    def _sign():
        from app.approvals import services as proc
        r = conn.execute("SELECT COUNT(*) AS total FROM pr_requests WHERE is_active = 1").fetchone()
        total = int(r["total"] or 0)
        if not total:
            return None
        mine = len(proc.my_queue(user) or [])
        return [tile("pr_sign", "check", "exec.pr_sign", mine,
                     url_for("approvals.index"), "crit" if mine else "good",
                     total, "exec.scope_prs")]
    block("proc_view", _sign)

    # Certificates lapsing inside 30 days — an expired cert stops a shipment at
    # the buyer, long after the goods are made.
    def _certs():
        r = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN expiry_date IS NOT NULL "
            "AND expiry_date <= ? THEN 1 ELSE 0 END) AS soon "
            "FROM cmp_certs WHERE status <> 'revoked'", (d_month,)).fetchone()
        total = int(r["total"] or 0)
        if not total:
            return None
        soon = int(r["soon"] or 0)
        return [tile("certs_expiring", "book", "exec.certs_expiring", soon,
                     url_for("compliance.certs"), "crit" if soon else "good",
                     total, "exec.scope_certs")]
    block("cmp_view", _certs)

    # The one place home lists records instead of counting them. An order IS an
    # aggregate of milestones and only a handful are ever live, so the row grain
    # here is the ORDER (3 rows), never the milestone (18) — that is My Work's
    # job. Capped at 6: if this ever needs a scrollbar it has become My Work.
    def _order_rows():
        rows = conn.execute(
            "SELECT o.id, o.order_no, o.buyer, o.style_ref, o.qty, o.ship_date, "
            "COUNT(CASE WHEN m.actual_date IS NULL AND m.planned_date IS NOT NULL "
            "           AND m.planned_date < ? THEN 1 END) AS late_ms, "
            "COUNT(CASE WHEN m.actual_date IS NULL THEN 1 END) AS open_ms "
            "FROM ord_orders o LEFT JOIN ord_milestones m ON m.order_id = o.id "
            "WHERE o.status NOT IN ('shipped','closed','cancelled') "
            "GROUP BY o.id, o.order_no, o.buyer, o.style_ref, o.qty, o.ship_date "
            "ORDER BY late_ms DESC, o.ship_date ASC LIMIT 6", (d_today,)).fetchall()
        # Permitted but nothing on file is a state worth naming, not a blank gap:
        # the template says "no live orders on file yet" instead of printing 0.
        out["orders_empty"] = not rows
        for r in rows:
            late_days = 0
            if r["ship_date"] and r["ship_date"] < d_today:
                try:
                    late_days = (today - _date.fromisoformat(r["ship_date"][:10])).days
                except ValueError:
                    late_days = 0
            late_ms, open_ms = int(r["late_ms"] or 0), int(r["open_ms"] or 0)
            # An order with a missed milestone is NOT "on track" just because its
            # ship date is still in the future — that is the slip, three weeks
            # early. Saying "on track" here would contradict the orders_late tile
            # sitting directly above it, on the same screen.
            if late_days:
                tone, state = "critical", _loc(
                    "%d days late" % late_days,
                    "متأخر %s" % _ar_count(late_days, "يومين", "أيام", "يومًا"),
                    "%d gün gecikmeli" % late_days)
            elif late_ms:
                tone, state = "warning", _loc("Behind schedule", "متأخر عن الجدول",
                                              "Programın gerisinde")
            elif r["ship_date"]:
                tone, state = "live", _loc("On track", "على المسار", "Yolunda")
            else:
                tone, state = "info", _loc("No ship date", "بدون تاريخ شحن",
                                           "Sevk tarihi yok")
            out["orders"].append({
                "id": r["id"], "order_no": r["order_no"] or "—",
                "buyer": r["buyer"] or "—", "style": r["style_ref"] or "—",
                "qty": int(r["qty"] or 0), "ship_date": r["ship_date"] or "",
                "tone": tone, "ship_state": state,
                "ms": _loc("%d late" % late_ms, "%d متأخر" % late_ms, "%d geciken" % late_ms)
                if late_ms else _loc("%d open" % open_ms, "%d مفتوح" % open_ms,
                                     "%d açık" % open_ms),
                "ms_late": bool(late_ms),
            })
        return None
    block("view_dashboard", _order_rows)

    try:
        conn.close()
    except Exception:
        pass
    tiles.sort(key=lambda t: t["rank"])   # severity, not source-file order
    return out


# --------------------------------------------------------------------------
# Command Center (executive dashboard)
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    # Scope-locked users (e.g. itsm_user) never see the command center — send
    # them straight to their system (single scope) or the filtered launcher.
    scope = system_scope(current_user())
    if scope:
        if len(scope) == 1:
            return redirect(url_for("main.module", key=next(iter(scope))))
        return redirect(url_for("main.launcher"))
    # Nothing on this page reaches the factory LAN any more. The four 127.0.0.1
    # systems are unreachable from Render BY DESIGN, so every number scraped from
    # them rendered 0 — and sla_health's `else 100` fallback rendered an
    # unreachable service desk as "SLA 100%, green". System status lives on
    # /health and the launcher, which both probe live; outage alerts reach the bell
    # from the notifications feed poller, which already writes (a GET must not).
    user = current_user()
    pulse = _factory_pulse(user)
    briefing = None
    if user_has_permission(user, "maint_view"):
        from app.insights import executive_summary
        briefing = executive_summary()
    return render_template(
        "dashboard.html",
        pulse=pulse["tiles"], orders=pulse["orders"],
        orders_empty=pulse["orders_empty"], pulse_head=_PULSE_HEAD,
        permitted=pulse["permitted"],
        greeting=_greeting(user), verdict=_verdict(pulse["tiles"]),
        freshness=_freshness(pulse["asof"]), briefing=briefing,
        active="command_center")


# --------------------------------------------------------------------------
# Application Launcher
# --------------------------------------------------------------------------
@bp.route("/launcher")
@permission_required("open_module")
def launcher():
    scope = system_scope(current_user())
    systems = [s for s in _systems() if scope is None or s["key"] in scope]
    statuses = health_svc.check_all(systems)
    return render_template("launcher.html", systems=systems, statuses=statuses,
                           active="launcher")


# --------------------------------------------------------------------------
# Generic module dispatcher (integrated apps + future-ready modules)
# --------------------------------------------------------------------------
MODULE_TABLES = {
    "ai_hub": {
        "intro": "module.ai_intro",
        "columns": ["Use Case", "Department", "Business Problem", "Expected Saving",
                    "Status", "Owner", "Priority", "Phase"],
        "rows": sc.AI_USE_CASES,
    },
    "automation": {
        "intro": "module.automation_intro",
        "columns": ["Process", "Department", "Manual Effort", "Frequency", "Tool",
                    "Status", "Time Saving", "Risk", "Owner"],
        "rows": sc.AUTOMATION_PROCESSES,
    },
    "finance": {
        "intro": "module.finance_intro",
        "columns": ["Initiative", "Type", "Expected Saving", "Status", "Owner", "Notes"],
        "rows": sc.FINANCE_ITEMS,
    },
    "bi": {
        "intro": "module.bi_intro",
        "columns": ["Dashboard", "Owner", "Refresh", "Status", "Last Update"],
        "rows": sc.BI_DASHBOARDS,
    },
    "production": {
        "intro": "module.production_intro",
        "columns": ["Line", "Status", "Efficiency", "Quality Issues", "Maint. Requests", "Risk"],
        "rows": sc.PRODUCTION_LINES,
    },
    "hr": {
        "intro": "module.hr_intro",
        "columns": ["Service", "Status", "Description"],
        "rows": sc.HR_SERVICES,
    },
    "procurement": {
        "intro": "module.procurement_intro",
        "columns": ["PR #", "Items", "Department", "Status", "Value"],
        "rows": sc.PROCUREMENT_ITEMS,
    },
    "governance": {
        "intro": "module.governance_intro",
        "columns": ["Area", "Status", "Description"],
        "rows": sc.GOVERNANCE_ITEMS,
    },
    "kb": {
        "intro": "module.kb_intro",
        "columns": ["Article", "Category", "Summary"],
        "rows": [("Reset your TC Platform password", "Account", "Step-by-step password reset"),
                 ("How to open a service desk ticket", "ITSM", "Submitting and tracking tickets"),
                 ("Requesting a new asset", "Assets", "Procurement and assignment flow"),
                 ("Reading the monitoring dashboard", "Monitoring", "Understanding server status"),
                 ("Submitting a project task", "Work", "Creating tasks in CommandTrack")],
    },
    "saplite": {
        "intro": "module.saplite_intro",
        "columns": ["Request", "Module", "Status"],
        "rows": [("Master data update", "MM", "Open"),
                 ("Cost center mapping", "CO", "In progress"),
                 ("Vendor onboarding", "MM", "Planned")],
    },
}


@bp.route("/module/<key>")
@permission_required("open_module")
def module(key):
    # Scope-locked users can only open their own system(s).
    scope = system_scope(current_user())
    if scope is not None and key not in scope:
        abort(403)
    # Production Visibility, BI and Probation are real working modules with their
    # own blueprints — open them directly (fully online, no external host needed).
    if key == "production":
        return redirect(url_for("production.index"))
    if key == "bi":
        return redirect(url_for("bi.index"))
    if key == "probation":
        return redirect(url_for("probation.dashboard"))

    row = _system_by_key(key)
    if not row or not row["enabled"]:
        abort(404)

    # Integrated apps -> embedded viewer (the platform concept) by default.
    # ?view=details shows the technical/launch info page instead.
    if row["is_integrated"]:
        status = health_svc.check_system(row)
        # Single Sign-On: when enabled, the iframe/open links go through the
        # platform's launch route (which mints a token and hands off), so the
        # user is signed straight into the system. Falls back to the raw URL.
        from app.routes.sso import sso_target_url
        embed_url = sso_target_url(key, row["base_url"])
        if request.args.get("view") == "details":
            return render_template("modules/integrated.html", system=row,
                                   status=status, active=key, embed_url=embed_url)
        return render_template("modules/embedded.html", system=row, status=status,
                               active=key, embed_url=embed_url)

    # Future-ready modules -> explanation + structured placeholder tables
    table = MODULE_TABLES.get(key)
    about = sc.MODULE_ABOUT.get(key)
    return render_template("modules/generic.html", system=row, table=table,
                           about=about, active=key)


# --------------------------------------------------------------------------
# Reports & Exports Center
# --------------------------------------------------------------------------
@bp.route("/reports")
@permission_required("view_reports")
def reports():
    """Catalogue of live reports the current user may view, grouped by module."""
    groups = reports_svc.catalog(user_can)
    return render_template("reports.html", groups=groups, active="reports")


@bp.route("/reports/<key>")
@permission_required("view_reports")
def report_view(key):
    """A single report: filters + on-screen preview (first 300 rows) + exports."""
    spec = reports_svc.get(key)
    if not spec or not user_can(spec["perm"]):
        abort(404)
    result = reports_svc.run(key, request.args, limit=300)
    return render_template("report_view.html", spec=spec, rows=result["rows"],
                           count=result["count"], args=request.args, active="reports",
                           can_export=user_can("export_reports"))


@bp.route("/reports/<key>.<fmt>")
@permission_required("export_reports")
def report_export(key, fmt):
    """Export a report honouring the current filters as CSV / Excel / PDF."""
    spec = reports_svc.get(key)
    if not spec or not user_can(spec["perm"]) or fmt not in ("csv", "xlsx", "pdf"):
        abort(404)
    result = reports_svc.run(key, request.args, limit=20000)
    try:
        payload, mime, ext = reports_svc.export(spec, result["rows"], fmt)
    except Exception:  # noqa: BLE001 — e.g. reportlab/openpyxl missing
        abort(500)
    log_audit(current_user()["username"], "report_export",
              f"{key}.{fmt} ({result['count']} rows)", request.remote_addr or "")
    return send_file(io.BytesIO(payload), as_attachment=True,
                     download_name=f"tc_{key}.{ext}", mimetype=mime)


# --------------------------------------------------------------------------
# Technical Health page
# --------------------------------------------------------------------------
@bp.route("/sw.js")
def service_worker():
    """Serve the service worker from root so its scope covers the whole app."""
    from flask import send_from_directory, current_app
    resp = send_from_directory(current_app.static_folder, "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/health")
@permission_required("view_system_health")
def health():
    systems = _systems()
    statuses = health_svc.check_all(systems, use_cache=False)
    sync_health_notifications(systems, statuses, force=True)
    info = {
        "python": sys.version.split()[0],
        "platform": pyplatform.platform(),
        "env": Config.ENV,
        "db_path": str(Config.DB_PATH),
        "db_ok": Config.DB_PATH.exists(),
        "backup_dir": str(Config.BACKUP_DIR),
        "backup_ok": Config.BACKUP_DIR.exists(),
    }
    return render_template("health.html", systems=systems, statuses=statuses,
                           info=info, active="health")


# --------------------------------------------------------------------------
# Roadmap
# --------------------------------------------------------------------------
@bp.route("/roadmap")
@login_required
def roadmap():
    return render_template("roadmap.html", roadmap=sc.ROADMAP, active="roadmap")


# --------------------------------------------------------------------------
# Shared registry (Phase 5) — one directory of people + assets across systems,
# each row deep-links (via SSO) straight to its record in the owning system.
# --------------------------------------------------------------------------
@bp.route("/registry")
@permission_required("open_module")
def registry():
    if system_scope(current_user()) is not None:   # scope-locked roles can't see the cross-system registry
        abort(403)
    from app.services.registry import fetch_registry
    data = fetch_registry(_systems())
    return render_template("registry.html", registry=data, active="registry")


@bp.route("/api/registry/search")
@login_required
def api_registry_search():
    from app.services.registry import search_registry
    q = request.args.get("q", "")
    return jsonify(search_registry(_systems(), q))


# --------------------------------------------------------------------------
# Profile + self-service password change
# --------------------------------------------------------------------------
@bp.route("/profile")
@login_required
def profile():
    return render_template("profile.html", active="profile")


# --------------------------------------------------------------------------
# Digital signature — generate from a typed name (4 styles) and save one.
# The rendered PNG is stored on the user so it can be reused for sign-offs.
# --------------------------------------------------------------------------
_SIG_STYLES = {"great-vibes", "dancing-script", "sacramento", "satisfy"}


@bp.route("/profile/signature", methods=["POST"])
@login_required
def save_signature():
    user = current_user()
    data = request.get_json(silent=True) or {}
    style = (data.get("style") or "").strip().lower()[:40]
    name = (data.get("name") or "").strip()[:120]
    png = data.get("png") or ""
    if style and style not in _SIG_STYLES:
        return jsonify(error="unknown style"), 400
    if png and not png.startswith("data:image/png;base64,"):
        return jsonify(error="invalid image"), 400
    if len(png) > 400_000:                      # ~300 KB rendered PNG ceiling
        return jsonify(error="signature image too large"), 413
    if not (style and name and png):
        return jsonify(error="name, style and image are all required"), 400
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=?, sig_name=?, sig_png=?, sig_updated_at=? WHERE id=?",
                     (style, name, png, utcnow(), user["id"]))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_save", f"style={style}", request.remote_addr or "")
    return jsonify(ok=True)


@bp.route("/profile/signature/quick", methods=["POST"])
@login_required
def quick_signature():
    """One-click signature: render the current user's name server-side (Great
    Vibes) and save it. The simplest way to get a usable signature on all papers."""
    user = current_user()
    from app.services.signature import generate_png, DEFAULT_STYLE
    name = (user.get("full_name") or user.get("username") or "").strip()
    png = generate_png(name)
    if not png:
        return jsonify(error="Could not generate a signature."), 500
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=?, sig_name=?, sig_png=?, sig_updated_at=? WHERE id=?",
                     (DEFAULT_STYLE, name, png, utcnow(), user["id"]))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_quick", "", request.remote_addr or "")
    return jsonify(ok=True, png=png, name=name)


@bp.route("/profile/notifications", methods=["POST"])
@login_required
def save_notif_prefs():
    """Save the current user's notification preferences (currently: email on/off)."""
    import json as _json
    user = current_user()
    data = request.get_json(silent=True) or {}
    prefs = {"email": bool(data.get("email", True))}
    conn = get_db()
    try:
        conn.execute("UPDATE users SET notif_prefs=? WHERE id=?",
                     (_json.dumps(prefs), user["id"]))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, prefs=prefs)


@bp.route("/profile/signature/clear", methods=["POST"])
@login_required
def clear_signature():
    user = current_user()
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=NULL, sig_name=NULL, sig_png=NULL, sig_updated_at=NULL WHERE id=?",
                     (user["id"],))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_clear", "", request.remote_addr or "")
    return jsonify(ok=True)


@bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    user = current_user()
    f = request.form
    current = f.get("current_password") or ""
    new = f.get("new_password") or ""
    confirm = f.get("confirm_password") or ""

    conn = get_db()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current):
            flash("pw_current_wrong", "error")
            return redirect(url_for("main.profile"))
        if new != confirm:
            flash("pw_mismatch", "error")
            return redirect(url_for("main.profile"))
        ok, msg = validate_password(new)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("main.profile"))
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (generate_password_hash(new), user["id"]))
        conn.commit()
        log_audit(user["username"], "password_change", "User changed own password",
                  request.remote_addr or "")
        flash("pw_changed", "success")
    finally:
        conn.close()
    return redirect(url_for("main.profile"))


# --------------------------------------------------------------------------
# User preferences (theme + language) — persisted to profile
# --------------------------------------------------------------------------
@bp.route("/prefs", methods=["POST"])
@login_required
def prefs():
    user = current_user()
    data = request.get_json(silent=True) or request.form
    theme = data.get("theme")
    lang = data.get("lang")
    fields, values = [], []
    if theme in ("light", "dark", "auto"):
        fields.append("theme_pref = ?"); values.append(theme)
    if lang in ("en", "ar", "tr"):
        fields.append("lang_pref = ?"); values.append(lang)
    if fields:
        values.append(user["id"])
        conn = get_db()
        try:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
        g.pop("user", None)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Notifications: mark read
# --------------------------------------------------------------------------
@bp.route("/notifications/read", methods=["POST"])
@login_required
def mark_notifications_read():
    # With an `id` (JSON or form) mark just that one read (clicking a single
    # notification); without one, mark all unread read ("Mark all read").
    nid = None
    if request.is_json:
        nid = (request.get_json(silent=True) or {}).get("id")
    nid = nid or request.form.get("id")
    try:
        nid = int(nid) if nid not in (None, "") else None
    except (TypeError, ValueError):
        nid = None
    conn = get_db()
    try:
        me = (current_user() or {}).get("username")
        if nid:
            # Scoped to the reader. Without this any signed-in account could clear
            # the CFO's "request to sign" by guessing an id.
            conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ? "
                         "AND (target_user IS NULL OR target_user = ?)", (nid, me))
        else:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0 "
                         "AND (target_user IS NULL OR target_user = ?)", (me,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/notifications/feed")
@login_required
def notifications_feed():
    """Lightweight JSON feed the top bar polls so brand-new notifications can
    chime, pop up and update the bell live without a page reload. Also pulls the
    four systems' own notifications into the feed (throttled), so any alert from
    ITSM / Assets / Monitoring / CommandTrack shows up here too."""
    try:
        systems = _systems()
    except Exception:
        systems = []
    try:
        sync_system_notifications(systems)
    except Exception:
        pass
    try:
        # Outage alerts (and their auto-resolve) used to ride on a Command Center
        # GET, which must not write. They cannot ride on /health either: that page
        # needs view_system_health, which only the IT roles hold, so nobody else
        # could ever raise or clear one. This poller is the endpoint that already
        # writes, runs for every signed-in browser, and is throttled the same way
        # — checked before check_all(), because probing four hosts is the
        # expensive half of this, not the four SELECTs.
        if health_sync_due():
            sync_health_notifications(systems, health_svc.check_all(systems))
    except Exception:
        pass
    try:
        from app.services.alerts import dispatch_pending_alerts
        dispatch_pending_alerts()   # email/webhook any new critical alerts (once)
    except Exception:
        pass
    try:
        from app.services.auto_ticket import auto_create_tickets
        auto_create_tickets(systems)   # open ITSM tickets for new critical alerts (if enabled)
    except Exception:
        pass
    notifs, unread = _unread_notifications((current_user() or {}).get("username"))
    items = [{
        "id": n["id"], "severity": n["severity"], "module": n["module"],
        "title": n["title"], "message": n["message"],
        "created_at": n["created_at"], "is_read": n["is_read"],
        "link": (n["link"] if "link" in n.keys() else None),
    } for n in notifs]
    max_id = max((it["id"] for it in items), default=0)
    return jsonify({"unread": unread, "max_id": max_id, "items": items})


# --------------------------------------------------------------------------
# Global search (phase 1: platform metadata + app names)
# --------------------------------------------------------------------------
@bp.route("/search")
@login_required
def search():
    """Platform-wide smart search (offline): modules + maintenance machines, spare
    parts and tickets + production lines. Substring + fuzzy (difflib) matching."""
    import difflib
    q = (request.args.get("q") or "").strip().lower()
    results = []
    if not q:
        return jsonify({"q": q, "results": results})

    user = current_user()

    def fuzzy(hay):
        hay = (hay or "").lower()
        if q in hay:
            return True
        # typo tolerance on individual words
        return any(difflib.SequenceMatcher(None, q, w).ratio() >= 0.82 for w in hay.split())

    # 1) modules / apps
    for row in _systems():
        hay = " ".join(filter(None, [row["name_en"], row["name_ar"], row["name_tr"],
                                     row["desc_en"], row["key"], row["category"]]))
        if fuzzy(hay):
            results.append({"type": "module", "name": row["name_en"],
                            "sub": row["category"], "url": url_for("main.module", key=row["key"])})

    conn = get_db()
    try:
        if has_permission(user["role"], "maint_view"):
            for m in conn.execute("SELECT id,code,name FROM mnt_machines WHERE is_active=1"):
                if fuzzy(f"{m['code']} {m['name']}"):
                    results.append({"type": "machine", "name": f"{m['code']} · {m['name']}",
                                    "sub": "Machine", "url": url_for("maintenance.machine_profile", mid=m["id"])})
            for s in conn.execute("SELECT id,code,name FROM mnt_spare_parts WHERE is_active=1"):
                if fuzzy(f"{s['code']} {s['name']}"):
                    results.append({"type": "spare", "name": f"{s['code']} · {s['name']}",
                                    "sub": "Spare part", "url": url_for("maintenance.spare_profile", sid=s["id"])})
            for t in conn.execute("SELECT id,ticket_no,machine_code,description FROM mnt_tickets WHERE is_active=1 ORDER BY id DESC LIMIT 200"):
                if fuzzy(f"{t['ticket_no']} {t['machine_code']} {t['description']}"):
                    results.append({"type": "ticket", "name": f"{t['ticket_no']} · {t['machine_code']}",
                                    "sub": "Ticket", "url": url_for("maintenance.ticket_detail", tid=t["id"])})
        if user_has_permission(user, "proc_view"):
            for pr in conn.execute(
                    "SELECT id,pr_no,title,status FROM pr_requests WHERE is_active=1 "
                    "ORDER BY id DESC LIMIT 300"):
                if fuzzy(f"{pr['pr_no']} {pr['title']}"):
                    results.append({"type": "pr", "name": f"{pr['pr_no']} · {pr['title'] or ''}",
                                    "sub": "Purchase request",
                                    "url": url_for("approvals.detail", pr_id=pr["id"])})
        if user_has_permission(user, "proc_catalogue"):
            # The item master, searched in the database rather than pulled into
            # memory: it holds five figures of rows, and the fuzzy() helper above
            # would have to walk every one of them on every keystroke.
            like = "%" + q + "%"
            for it in conn.execute(
                    "SELECT id, code, name, unit, active FROM proc_items "
                    "WHERE LOWER(code) LIKE LOWER(?) OR LOWER(name) LIKE LOWER(?) "
                    "ORDER BY CASE WHEN LOWER(code) LIKE LOWER(?) THEN 0 ELSE 1 END, "
                    "active DESC, code "
                    "LIMIT 20", (like, like, q + "%")):
                results.append({
                    "type": "item",
                    "name": f"{it['code']} · {it['name']}",
                    "sub": "Item" + ("" if it["active"] else " (retired)"),
                    "url": url_for("approvals.items", q=it["code"])})

        if has_permission(user["role"], "open_module"):
            for p in conn.execute("SELECT name,area FROM production_lines"):
                if fuzzy(f"{p['name']} {p['area']}"):
                    results.append({"type": "production", "name": p["name"], "sub": "Production line",
                                    "url": url_for("production.index")})

        # --- records from the newer modules -------------------------------
        # Each block is guarded: a deployment whose migrations have not created a
        # module's tables yet must never break the platform search box.
        def _scan(sql, hay, make):
            try:
                rows = conn.execute(sql).fetchall()
            except Exception:
                return
            for r in rows:
                try:
                    if fuzzy(hay(r)):
                        results.append(make(r))
                except Exception:
                    continue

        if user_has_permission(user, "view_dashboard"):
            _scan("SELECT id,order_no,buyer,style_name,style_ref FROM ord_orders "
                  "ORDER BY id DESC LIMIT 300",
                  lambda r: f"{r['order_no']} {r['buyer']} {r['style_name'] or ''} {r['style_ref'] or ''}",
                  lambda r: {"type": "order",
                             "name": f"{r['order_no']} · {r['buyer'] or ''}",
                             "sub": r["style_name"] or "Order",
                             "url": url_for("orders.detail", order_id=r["id"])})
        if user_has_permission(user, "cmp_view"):
            _scan("SELECT id,ref,scheme,site FROM cmp_audits ORDER BY id DESC LIMIT 200",
                  lambda r: f"{r['ref']} {r['scheme']} {r['site'] or ''}",
                  lambda r: {"type": "audit",
                             "name": f"{r['ref']} · {r['scheme']}",
                             "sub": "Compliance audit",
                             "url": url_for("compliance.audit_detail", audit_id=r["id"])})
    finally:
        conn.close()
    return jsonify({"q": q, "results": results[:25]})


# ---------------------------------------------------------------------------
# My Work & Action Centre — one page with everything waiting on THIS user:
# approvals to sign, maintenance part-requests to decide, probation evaluations
# due, own PRs and maintenance tickets, and the latest unread alerts. Every
# section is permission-gated and defensively wrapped so one module's failure
# never blanks the page.
# ---------------------------------------------------------------------------
@bp.route("/my-work")
@login_required
def my_work():
    user = current_user()
    data = {"sign_queue": [], "maint_approvals": [], "prob_pending": [],
            "my_prs": [], "my_tickets": [], "alerts": [],
            "cmp_caps": [], "cmp_expiring": [], "late_milestones": []}

    if user_has_permission(user, "proc_view"):
        try:
            from app.approvals import services as proc
            data["sign_queue"] = proc.my_queue(user)[:15]
            data["my_prs"] = [p for p in proc.list_prs(requester=user["username"], limit=10)
                              if p.get("status") not in ("closed", "cancelled")][:8]
        except Exception:
            pass

    if user_has_permission(user, "maint_approve"):
        try:
            conn = get_db()
            try:
                data["maint_approvals"] = conn.execute(
                    "SELECT a.id, a.level, a.approver_role, a.created_at, r.request_no, "
                    "r.id AS request_id FROM mnt_approvals a "
                    "JOIN mnt_requests r ON r.id = a.request_id "
                    "WHERE a.status='pending' ORDER BY a.id DESC LIMIT 10").fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    if user_has_permission(user, "maint_view"):
        try:
            conn = get_db()
            try:
                data["my_tickets"] = conn.execute(
                    "SELECT id, ticket_no, machine_code, status, priority, created_at "
                    "FROM mnt_tickets WHERE requester=? AND is_active=1 "
                    "AND status NOT IN ('closed','cancelled','rejected') "
                    "ORDER BY id DESC LIMIT 8", (user["username"],)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    if user_has_permission(user, "prob_view"):
        try:
            from app.probation import services as prob
            data["prob_pending"] = prob.my_pending(user)[:10]
        except Exception:
            pass

    # Compliance: corrective actions that are open past their due date, and
    # certificates about to lapse — both are "act now or a shipment is at risk".
    if user_has_permission(user, "cmp_view"):
        try:
            conn = get_db()
            try:
                today = _date.today().isoformat()
                soon = (_date.today() + _timedelta(days=30)).isoformat()
                data["cmp_caps"] = conn.execute(
                    "SELECT f.id, f.finding, f.severity, f.due_date, f.audit_id, a.ref, a.scheme "
                    "FROM cmp_findings f JOIN cmp_audits a ON a.id = f.audit_id "
                    "WHERE f.status IN ('open','in_progress') AND f.due_date IS NOT NULL "
                    "AND f.due_date <= ? ORDER BY f.due_date ASC LIMIT 8", (soon,)).fetchall()
                data["cmp_expiring"] = conn.execute(
                    "SELECT id, name, expiry_date FROM cmp_certs "
                    "WHERE expiry_date IS NOT NULL AND expiry_date <= ? AND status != 'revoked' "
                    "ORDER BY expiry_date ASC LIMIT 6", (soon,)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    # Orders: Time & Action milestones whose planned date has passed with no
    # actual — the earliest signal that a ship date is about to slip.
    if user_has_permission(user, "view_dashboard"):
        try:
            conn = get_db()
            try:
                data["late_milestones"] = conn.execute(
                    "SELECT m.id, m.name, m.planned_date, o.id AS order_id, o.order_no, o.buyer "
                    "FROM ord_milestones m JOIN ord_orders o ON o.id = m.order_id "
                    "WHERE m.actual_date IS NULL AND m.planned_date IS NOT NULL "
                    "AND m.planned_date < ? "
                    "AND o.status NOT IN ('shipped','closed','cancelled') "
                    "ORDER BY m.planned_date ASC LIMIT 8",
                    (_date.today().isoformat(),)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    try:
        notifs, _unread = _unread_notifications(user["username"])
        scope = system_scope(user)
        if scope is not None:
            notifs = [n for n in notifs if (n["module"] or "") in scope or not n["module"]]
        data["alerts"] = [n for n in notifs if not n["is_read"]][:8]
    except Exception:
        pass

    total_actions = (len(data["sign_queue"]) + len(data["maint_approvals"])
                     + len(data["prob_pending"]) + len(data["late_milestones"])
                     + len(data["cmp_caps"]) + len(data["cmp_expiring"]))
    return render_template("my_work.html", user=user, total_actions=total_actions,
                           active="my_work", **data)
