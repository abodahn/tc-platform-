# -*- coding: utf-8 -*-
"""
TC Platform — new-item requests: the ONE door into the item catalogue.

Before this, an item that was not in the ERP master was simply typed as free
text on the PR line and never reached the catalogue: the master was accurate on
import day and decayed from the next purchase onwards. This module adds the
missing path — a requester ASKS for the item, Purchasing (who own item and
supplier data, and are the only people able to tell a genuinely new part from
the same part spelled differently) approve it and TYPE THE ERP CODE.

Two rules shape everything here:

  * THE COMMERCIAL LOCKOUT. A requester states WHAT they need and never what it
    costs. There is no price column on proc_item_requests and no function here
    reads proc_items.cost_price. The created catalogue row is deliberately born
    with has_cost = 0: value enters the platform exactly once, at the Purchasing
    pricing gate.
  * THE ERP KEEPS THE CODES. Nothing here generates a code. The approver types
    the code the ERP already issued, so the two systems cannot drift, and
    `source` marks the row as platform-created so the next ERP import can tell
    it apart — upsert_items matches BY CODE, so that import updates this row
    instead of creating a second one.

Free text is untouched. Requesting an item is an EXTRA path, never a gate: a
line whose item is not in the catalogue still creates, submits and is approved
exactly as it always has, whether or not a request was ever raised for it.
"""
import re

from app.db import get_db
from app.approvals.services import _now, audit, notify_users

# Marks a catalogue row that was born here rather than in an ERP export. Read by
# a human at the next import; never parsed.
SOURCE = "platform:new-item-request"

MAX_SIMILAR = 5          # a warning list nobody reads is not a warning
_TOKEN = re.compile(r"[a-z0-9؀-ۿ]{3,}")

# Every data-i18n key these pages introduce, with real EN / AR / TR.
# app/static/i18n/** belongs to the orchestrator, so this dict is the source it
# merges from — and the self-test asserts every key rendered on these pages is
# covered here (or already live in en.json). A key missing from en.json renders
# as the literal string to EVERY user, in every language.
I18N = {
    "nir.title": ("Request a new item", "طلب صنف جديد", "Yeni kalem talebi"),
    "nir.sub": ("Not in the catalogue? Describe what you need. Purchasing check it against the master and add it with its ERP code. Your purchase request is not held up — carry on and submit it, the line stays as you typed it.",
                "الصنف غير موجود في الكتالوج؟ صف ما تحتاجه. تراجعه المشتريات مقابل قائمة الأصناف وتضيفه بكود ERP الخاص به. طلب الشراء لا يتوقف — أكمله وأرسله، ويبقى السطر كما كتبته.",
                "Katalogda yok mu? İhtiyacınızı tanımlayın. Satın alma, ana listeye karşı kontrol edip ERP koduyla ekler. Satın alma talebiniz beklemez — devam edip gönderin, satır yazdığınız gibi kalır."),
    "nir.name": ("Item name", "اسم الصنف", "Kalem adı"),
    "nir.name_hint": ("Exactly as you typed it on the request line.",
                      "كما كتبته تماماً في سطر الطلب.",
                      "Talep satırına yazdığınız şekliyle."),
    "nir.unit": ("Unit", "الوحدة", "Birim"),
    "nir.category": ("Category", "الفئة", "Kategori"),
    "nir.category_hint": ("Where it belongs in the master, if you know.",
                          "موقعه في قائمة الأصناف، إن كنت تعرفه.",
                          "Ana listede nereye ait, biliyorsanız."),
    "nir.reason": ("Why it is needed", "سبب الحاجة إليه", "Neden gerekli"),
    "nir.reason_hint": ("What it is for and any spec, model or size. No prices — Purchasing price it later.",
                        "ما الغرض منه وأي مواصفة أو موديل أو مقاس. بدون أسعار — المشتريات تُسعّره لاحقاً.",
                        "Ne için ve varsa özellik, model veya ölçü. Fiyat yazmayın — fiyatlandırmayı satın alma yapar."),
    "nir.send": ("Send request", "إرسال الطلب", "Talebi gönder"),
    "nir.back": ("Back", "رجوع", "Geri"),
    "nir.ask": ("Request a new item", "طلب صنف جديد", "Yeni kalem talebi"),
    "nir.sent": ("Request sent to Purchasing. Close this tab and carry on with your purchase request.",
                 "تم إرسال الطلب إلى المشتريات. أغلق هذه النافذة وأكمل طلب الشراء.",
                 "Talep satın almaya gönderildi. Bu sekmeyi kapatıp satın alma talebinize devam edin."),
    "nir.no_price": ("A new-item request carries no price. You say what you need; Purchasing add the value later.",
                     "طلب الصنف الجديد لا يحمل أي سعر. أنت تحدد ما تحتاجه، والمشتريات تضيف القيمة لاحقاً.",
                     "Yeni kalem talebinde fiyat yoktur. Siz ihtiyacı belirtirsiniz, değeri satın alma sonra ekler."),
    "nir.queue": ("New-item requests", "طلبات الأصناف الجديدة", "Yeni kalem talepleri"),
    "nir.queue_sub": ("Items the floor asked for that are not in the master. Approve one by typing the code the ERP issued — the platform never invents a code.",
                      "أصناف طلبها الموقع وغير موجودة في قائمة الأصناف. اعتمد الصنف بكتابة الكود الصادر من نظام ERP — المنصة لا تنشئ كوداً من عندها.",
                      "Sahanın istediği, ana listede olmayan kalemler. ERP'nin verdiği kodu yazarak onaylayın — platform kendiliğinden kod üretmez."),
    "nir.pending": ("Pending", "قيد الدراسة", "Beklemede"),
    "nir.approved": ("Approved", "معتمد", "Onaylandı"),
    "nir.rejected": ("Rejected", "مرفوض", "Reddedildi"),
    "nir.requested_by": ("Requested by", "مقدّم الطلب", "Talep eden"),
    "nir.code": ("ERP code", "كود ERP", "ERP kodu"),
    "nir.code_hint": ("Type the code from the ERP. There is no auto-generated code — the ERP stays the single source of truth.",
                      "اكتب الكود من نظام ERP. لا يوجد كود يُنشأ تلقائياً — يبقى ERP المصدر الوحيد للأكواد.",
                      "Kodu ERP'den yazın. Otomatik kod üretilmez — kodların tek kaynağı ERP'dir."),
    "nir.approve": ("Approve", "اعتماد", "Onayla"),
    "nir.reject": ("Reject", "رفض", "Reddet"),
    "nir.note": ("Reason", "السبب", "Gerekçe"),
    "nir.empty": ("Nothing waiting. Requests raised from the purchase-request form land here.",
                  "لا يوجد ما ينتظر. تصل هنا الطلبات المرفوعة من نموذج طلب الشراء.",
                  "Bekleyen yok. Satın alma talebi formundan yükseltilen talepler buraya düşer."),
    "nir.dupe_code": ("That code is already in the catalogue. Nothing was created.",
                      "هذا الكود موجود بالفعل في الكتالوج. لم يتم إنشاء أي صنف.",
                      "Bu kod katalogda zaten var. Hiçbir şey oluşturulmadı."),
    "nir.dupe_existing": ("Item already on that code", "الصنف المسجّل على هذا الكود",
                          "Bu koda kayıtlı kalem"),
    "nir.similar": ("Possible duplicate — similar items are already in the catalogue. Your call: approve anyway, or reject and tell the requester which code to use.",
                    "احتمال تكرار — توجد أصناف مشابهة في الكتالوج بالفعل. القرار لك: اعتمده رغم ذلك، أو ارفضه وأخبر مقدّم الطلب بالكود الذي يستخدمه.",
                    "Olası mükerrer — katalogda benzer kalemler var. Karar sizin: yine de onaylayın ya da reddedip talep edene hangi kodu kullanacağını söyleyin."),
    "nir.also_asked": ("Also requested by someone else — decide once.",
                       "طلبه شخص آخر أيضاً — احسمه مرة واحدة.",
                       "Aynı kalemi başkası da istedi — bir kez karara bağlayın."),
    "nir.off_cat": ("Bought off-catalogue", "المشتَرى خارج الكتالوج",
                    "Katalog dışı alınanlar"),
    "nir.decided": ("Decision", "القرار", "Karar"),
    "nir.status": ("Status", "الحالة", "Durum"),
    "nir.filter_all": ("All", "الكل", "Tümü"),
    "nir.mine": ("My recent new-item requests", "طلباتي الأخيرة للأصناف الجديدة",
                 "Son yeni kalem taleplerim"),
}


def _unit_for_store(raw):
    """The unit as the CATALOGUE spells it, not as it was typed."""
    from app.approvals.catalogue import norm_unit
    return norm_unit((raw or "").strip()[:40])


def _norm(s):
    return " ".join((s or "").strip().lower().split())


def _code_taken(conn, code):
    """The catalogue row already holding `code`, IGNORING CASE, or None.

    The UNIQUE on proc_items.code is case-SENSITIVE, so "AB-100" and "ab-100"
    are two rows to the database and one part to a human. Typing the second is
    exactly the duplicate this whole flow exists to prevent, and it is the
    expensive kind: the next ERP export carries the code in its own case,
    upsert_items matches by exact code, and the master ends up with two rows
    that never merge. So the door refuses a code that differs only in case and
    shows the approver the row that already holds it.

    ponytail: LOWER(code) has no index, so this is one scan of ~19k rows —
    paid once per approval, which happens a few times a week. Add a functional
    index the day approvals are a bulk operation.
    """
    row = conn.execute(
        "SELECT id, code, name, unit, category_name FROM proc_items "
        "WHERE code=? OR LOWER(code)=LOWER(?)", (code, code)).fetchone()
    return dict(row) if row else None


def _pr_exists(conn, pr_id):
    """The PR a request came from may have been rejected, cancelled or deleted
    while the request sat in the queue. Checked BEFORE writing anything against
    it: pr_events carries a foreign key, and on PostgreSQL a failed statement
    aborts the whole transaction — which would lose the approval itself."""
    if not pr_id:
        return False
    try:
        return conn.execute("SELECT 1 FROM pr_requests WHERE id=?",
                            (int(pr_id),)).fetchone() is not None
    except Exception:
        return False


# --------------------------------------------------------------------------
# Requester side
# --------------------------------------------------------------------------
def create_item_request(data, user, ip=None):
    """Raise a new-item request. Returns (request_id, error_key).

    `data` is the posted form. Any price-ish field it carries is IGNORED — the
    columns simply do not exist, which is the point.
    """
    name = (data.get("name") or "").strip()[:200]
    if not name:
        return None, "name_required"
    who = (user or {}).get("username") or "system"
    cat = (data.get("category_code") or "").strip()[:40]
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO proc_item_requests (pr_id, line_no, name, unit, "
            "category_code, category_name, reason, requested_by, requested_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'pending')",
            (int(data["pr_id"]) if str(data.get("pr_id") or "").isdigit() else None,
             int(data["line_no"]) if str(data.get("line_no") or "").isdigit() else None,
             name, (data.get("unit") or "").strip()[:40], cat,
             (data.get("category_name") or "").strip()[:120],
             (data.get("reason") or "").strip()[:2000], who, _now()))
        req_id = cur.lastrowid
        notify_users(conn, eligible_approvers(conn), "info", "New item requested",
                     f"{who} asked for an item that is not in the catalogue: {name}",
                     link="/procurement/item-requests")
        conn.commit()
        return req_id, None
    finally:
        conn.close()


def eligible_approvers(conn):
    """Usernames that hold proc_purchasing. Roles carry permissions, and a user
    may hold extra ones — ask the security layer rather than guessing a role
    name, so a DB-edited role keeps working."""
    from app.security import user_has_permission
    out = []
    try:
        rows = conn.execute("SELECT * FROM users WHERE is_active=1").fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            if user_has_permission(dict(r), "proc_purchasing"):
                out.append(r["username"])
        except Exception:
            pass
    return out


def my_requests(username, limit=20):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM proc_item_requests WHERE requested_by=? "
            "ORDER BY id DESC LIMIT ?", (username or "", int(limit))).fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Purchasing side
# --------------------------------------------------------------------------
def similar_items(conn, name, limit=MAX_SIMILAR):
    """Catalogue rows whose NAME looks like `name`. This is the whole reason a
    human approves: "M6 BOLT", "Bolt M6" and "bolt m-6" are one part, and a
    master that holds all three is the failure this flow exists to prevent.

    ponytail: token LIKE, no trigram/fuzzy index. It reads a name, not a corpus;
    move to pg_trgm the day Purchasing say it misses real duplicates.
    """
    toks = _TOKEN.findall(_norm(name))[:3]
    if not toks:
        return []
    where = " OR ".join(["LOWER(name) LIKE ?"] * len(toks))
    try:
        rows = conn.execute(
            f"SELECT id, code, name, unit FROM proc_items WHERE active=1 AND ({where}) "
            "ORDER BY code LIMIT ?", tuple(f"%{t}%" for t in toks) + (int(limit),)
        ).fetchall()
    except Exception:
        return []
    # NOTE: code / name / unit only. cost_price is never selected here — this
    # list is rendered on a page, and pages get read over shoulders.
    return [{"id": r["id"], "code": r["code"], "name": r["name"] or "",
             "unit": r["unit"] or ""} for r in rows]


def list_requests(status="pending"):
    """Requests for the Purchasing queue, newest first, each carrying the
    catalogue items it might duplicate and a flag when somebody else is asking
    for the same thing — two people asking must be ONE decision, not two rows
    quietly approved into two codes."""
    conn = get_db()
    try:
        sql = "SELECT * FROM proc_item_requests"
        params = ()
        if status in ("pending", "approved", "rejected"):
            sql += " WHERE status=?"
            params = (status,)
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 200",
                                              params).fetchall()]
        pending_names = {}
        for r in conn.execute("SELECT name FROM proc_item_requests WHERE status='pending'"
                              ).fetchall():
            k = _norm(r["name"])
            pending_names[k] = pending_names.get(k, 0) + 1
        # ponytail: the near-duplicate lookup is a leading-wildcard LIKE, i.e. one
        # scan of the catalogue per row, so it is computed for the first 25 rows
        # only — a queue longer than that is a backlog to clear, not a page to
        # render. Lift the cap the day it is a real queue depth.
        for r in rows[:25]:
            r["similar"] = similar_items(conn, r["name"]) if r["status"] == "pending" else []
        for r in rows:
            r.setdefault("similar", [])
            r["also"] = pending_names.get(_norm(r["name"]), 0) - 1 if r["status"] == "pending" else 0
        return rows
    finally:
        conn.close()


def counts():
    conn = get_db()
    try:
        return {r["status"]: int(r["n"]) for r in conn.execute(
            "SELECT status, COUNT(*) n FROM proc_item_requests GROUP BY status").fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def approve_item_request(req_id, code, user, ip=None, unit=None, category_name=None):
    """Approve with the Optima code. Returns (ok, msg_key, existing_row).

    `unit` and `category_name` are Purchasing's to set. The requester states
    WHAT they need in their own words and nothing else; asking the floor to
    guess a unit of measure or a catalogue category produced guesses, and the
    person typing the Optima code is the person who knows both.

    Refuses on a code that is already in the catalogue and hands back the row
    that holds it, so Purchasing see WHAT they would have duplicated. Nothing is
    written on a refusal.

    A similar NAME is a warning, never a block: telling one part from another is
    exactly the judgement Purchasing are being asked for, so `msg_key` comes
    back as "approved_similar" and the near-duplicates are recorded on the
    request for the audit trail.
    """
    code = (code or "").strip()[:80]
    if not code:
        return False, "code_required", None
    who = (user or {}).get("username") or "system"
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM proc_item_requests WHERE id=?",
                           (int(req_id),)).fetchone()
        if not req:
            return False, "not_found", None
        if (req["status"] or "") != "pending":
            return False, "not_pending", None
        clash = _code_taken(conn, code)
        if clash:
            return False, "duplicate_code", clash

        near = similar_items(conn, req["name"])
        now = _now()
        try:
            cur = conn.execute(
                "INSERT INTO proc_items (code, name, unit, category_code, category_name, "
                "cost_price, has_cost, source, active, updated_by, updated_at) "
                "VALUES (?,?,?,?,?,0,0,?,1,?,?)",
                (code, req["name"],
                 # norm_unit, not _norm: _norm lower-cases for COMPARISON and
                 # would store "pcs" where the catalogue holds "Pcs".
                 _unit_for_store(unit) or req["unit"] or "",
                 req["category_code"] or "",
                 (category_name or "").strip()[:120] or req["category_name"] or "",
                 SOURCE, who, now))
            item_id = cur.lastrowid
        except Exception:
            # Two approvers on the same code at the same instant. The UNIQUE on
            # code held; on PostgreSQL the failed statement also aborts the
            # transaction, so roll back before reading anything else.
            conn.rollback()
            return False, "duplicate_code", _code_taken(conn, code)

        conn.execute(
            "UPDATE proc_item_requests SET status='approved', decided_by=?, decided_at=?, "
            "assigned_code=?, item_id=?, decision_note=? WHERE id=?",
            (who, now, code, item_id,
             ("similar: " + ", ".join(s["code"] for s in near))[:2000] if near else "",
             int(req_id)))

        # Link back. The PR is usually still being TYPED when the request is
        # raised, so pr_id is normally NULL and the honest link is the text the
        # requester used: every free-text line carrying that exact item text and
        # no catalogue link is the line that raised this. Guarded so a PR that
        # was rejected, cancelled or deleted meanwhile is simply not there to
        # update — it can never leave a broken row.
        # TWO forms on purpose. _norm collapses runs of whitespace, SQL TRIM only
        # strips the ends — so a line typed "Bolt  M6" (or with a tab in it)
        # normalises to "bolt m6" here and stays "bolt  m6" in the column, and
        # the link silently never happened: the item entered the catalogue while
        # the line stayed off-catalogue for ever. Matching the as-typed form as
        # well costs one placeholder. SQLite and PostgreSQL both take IN (?,?).
        conn.execute(
            "UPDATE pr_items SET item_id=? WHERE item_id IS NULL "
            "AND LOWER(TRIM(item)) IN (?, ?)",
            (item_id, _norm(req["name"]), (req["name"] or "").strip().lower()))
        if _pr_exists(conn, req["pr_id"]):
            conn.execute("UPDATE pr_items SET item_id=? WHERE pr_id=? AND seq=? "
                         "AND item_id IS NULL", (item_id, req["pr_id"], req["line_no"] or 1))
            audit(conn, req["pr_id"], who, "item_request_approved",
                  f"{req['name']} -> {code}", ip=ip)

        notify_users(conn, [req["requested_by"]], "info", "New item approved",
                     f"{req['name']} is now in the catalogue as {code}.",
                     link="/procurement/item-request")
        conn.commit()
        return True, ("approved_similar" if near else "approved"), None
    finally:
        conn.close()


def reject_item_request(req_id, note, user, ip=None):
    """Reject with a reason. The PR line is left exactly as it is — free text,
    still valid, still able to be approved. A rejected request changes nothing
    about the purchase request it came from."""
    note = (note or "").strip()[:2000]
    if not note:
        return False, "reason_required"
    who = (user or {}).get("username") or "system"
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM proc_item_requests WHERE id=?",
                           (int(req_id),)).fetchone()
        if not req:
            return False, "not_found"
        if (req["status"] or "") != "pending":
            return False, "not_pending"
        conn.execute("UPDATE proc_item_requests SET status='rejected', decided_by=?, "
                     "decided_at=?, decision_note=? WHERE id=?",
                     (who, _now(), note, int(req_id)))
        if _pr_exists(conn, req["pr_id"]):
            audit(conn, req["pr_id"], who, "item_request_rejected",
                  f"{req['name']}: {note}"[:400], ip=ip)
        notify_users(conn, [req["requested_by"]], "warning", "New item not added",
                     f"{req['name']} was not added to the catalogue: {note}",
                     link="/procurement/item-request")
        conn.commit()
        return True, "rejected"
    finally:
        conn.close()
