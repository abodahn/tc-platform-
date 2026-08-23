# -*- coding: utf-8 -*-
"""
Items typed onto a request line that are not in the catalogue — and the two
things Purchasing can now DO about each one.

WHAT WAS MISSING. A requester may always type a line in their own words; that
freedom is deliberate and stays. Purchasing could already SEE the result — the
"Bought off-catalogue" report groups every unlinked line by the text people
typed — but the report is a list and nothing more. There was no way to act on a
line from it: no way to add the item, and no way to say it should not be added.
So the same text got typed again next month, and the master went on decaying
exactly as the report's own description predicted.

This module closes that. Each distinct typed text becomes a decision:

    ADD     Purchasing type the Optima code. The catalogue row is created with
            that code and the requester's own words as its name, and EVERY
            request line carrying that text is linked to it — including the ones
            already submitted, which is the whole point: the next person to type
            it will find it in the picker instead.

    REJECT  Purchasing record why, usually naming the code that should have been
            used. The text is remembered as decided, so it stops reappearing in
            the queue, and the people who typed it are told what to use instead.

TWO RULES CARRIED OVER, because they are the same rules the new-item door keeps:

  * THE ERP KEEPS THE CODES. Nothing here generates one. Adding requires the
    code Optima already issued, and a code the catalogue already holds is
    refused with the row that holds it — matching by case-insensitively, so
    "AB-100" cannot become a second row beside "ab-100".
  * THE COMMERCIAL LOCKOUT. The row is born unpriced. Value enters the platform
    at the Purchasing pricing gate and nowhere else, so adding an item can never
    be a way to set what it costs.
"""
from app.db import get_db
from app.approvals import constants as C


# Every data-i18n key this feature introduces, EN / AR / TR — the same pattern
# app/approvals/catalogue.py uses, so the strings live beside the code that
# renders them rather than only in the shared dictionary.
I18N = {
    "offc.title": ("Typed, not in the catalogue", "مكتوب وغير موجود في الكتالوج",
                   "Yazılmış, katalogda yok"),
    "offc.sub": (
        "Text people typed on a request line that matches no catalogue item. Add "
        "it with the code Optima issued, or reject it and say which code to use "
        "instead. Until one of those happens the same text gets typed again next "
        "month.",
        "نصوص كتبها المستخدمون في سطور الطلبات ولا تطابق أي صنف في الكتالوج. "
        "أضفه بالكود الذي أصدره أوبتيما، أو ارفضه مع بيان الكود الذي يُستخدم "
        "بدلاً منه. وما لم يحدث أحدهما فسيتكرر كتابة النص نفسه الشهر القادم.",
        "Kullanıcıların talep satırına yazdığı, hiçbir katalog kalemiyle "
        "eşleşmeyen metinler. Optima'nın verdiği kodla ekleyin ya da reddedip "
        "hangi kodun kullanılacağını yazın. İkisi de olmazsa aynı metin gelecek "
        "ay yine yazılır."),
    "offc.text": ("What was typed", "النص المكتوب", "Yazılan metin"),
    "offc.lines": ("Lines", "عدد السطور", "Satır"),
    "offc.requests": ("Requests", "عدد الطلبات", "Talep"),
    "offc.qty": ("Total quantity", "إجمالي الكمية", "Toplam miktar"),
    "offc.last_seen": ("Last typed", "آخر مرة", "Son yazılma"),
    "offc.who": ("Typed by", "بواسطة", "Yazan"),
    "offc.code": ("Optima code", "كود أوبتيما", "Optima kodu"),
    "offc.unit": ("Unit", "الوحدة", "Birim"),
    "offc.category": ("Category", "الفئة", "Kategori"),
    "offc.add": ("Add to catalogue", "إضافة إلى الكتالوج", "Kataloga ekle"),
    "offc.reject": ("Reject", "رفض", "Reddet"),
    "offc.reason": ("Why not, and what to use instead",
                    "سبب الرفض، والكود الذي يُستخدم بدلاً منه",
                    "Neden olmadığı ve bunun yerine ne kullanılacağı"),
    "offc.empty": ("Nothing waiting. Every line on every request is linked to a "
                   "catalogue item.",
                   "لا يوجد شيء في الانتظار. كل سطر في كل طلب مرتبط بصنف في الكتالوج.",
                   "Bekleyen yok. Her talepteki her satır bir katalog kalemine bağlı."),
    "offc.linked": ("lines linked", "سطر تم ربطه", "satır bağlandı"),
    "offc.decided": ("Decided", "تم البت فيه", "Karara bağlandı"),
    "offc.show_decided": ("Show rejected", "إظهار المرفوض", "Reddedilenleri göster"),
    "offc.nav": ("Off-catalogue lines", "سطور خارج الكتالوج", "Katalog dışı satırlar"),
}

# The text as a comparison key. Same shape the report groups by, so this queue
# and that report can never disagree about what counts as one item.
_KEY = "LOWER(TRIM(i.item))"


def _norm(s):
    return " ".join((s or "").strip().lower().split())


def pending(conn, include_decided=False, limit=200):
    """Distinct typed texts with no catalogue link, commonest first.

    A text that has already been REJECTED is excluded unless asked for: the
    decision was "do not add this", and showing it forever would train people to
    scroll past the queue.
    """
    rows = conn.execute(
        "SELECT MAX(i.item) AS item_text, "
        "       MAX(COALESCE(i.unit,'')) AS unit, "
        "       COUNT(*) AS lines, "
        "       COUNT(DISTINCT i.pr_id) AS requests, "
        "       SUM(COALESCE(i.qty,0)) AS qty, "
        "       MAX(COALESCE(p.request_date, p.created_at)) AS last_seen, "
        "       MAX(COALESCE(p.requester,'')) AS who, "
        f"      {_KEY} AS k "
        "FROM pr_items i JOIN pr_requests p ON p.id = i.pr_id "
        "WHERE (i.item_id IS NULL OR i.item_id = 0) "
        "  AND TRIM(COALESCE(i.item,'')) <> '' "
        f" GROUP BY {_KEY} "
        " ORDER BY COUNT(*) DESC, MAX(COALESCE(p.request_date, p.created_at)) DESC "
        " LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["decision"] = _decision(conn, d["k"])
        if d["decision"] and not include_decided:
            continue
        out.append(d)
    return out


def _decision(conn, key):
    """The recorded decision for a typed text, or None."""
    try:
        r = conn.execute(
            "SELECT decision, note, decided_by, decided_at FROM proc_off_catalogue "
            "WHERE text_key=?", (key,)).fetchone()
    except Exception:
        return None                 # table not migrated yet: nothing is decided
    return dict(r) if r else None


def link_existing_lines(conn, key, item_id):
    """Point every unlinked line carrying this text at the new catalogue row.

    Including lines on requests already submitted or approved. Linking does not
    change what was ordered, priced or signed — item_id is a reference to the
    master, not a term of the purchase — and leaving history unlinked would mean
    the report still showed the text as off-catalogue after it had been added.
    """
    cur = conn.execute(
        "UPDATE pr_items SET item_id=? "
        f"WHERE (item_id IS NULL OR item_id = 0) AND {_KEY.replace('i.', '')}=?",
        (item_id, key))
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


def add_to_catalogue(text, code, user, unit="", category_name="", ip=""):
    """Create the catalogue row and link every line that typed this text.

    Returns (ok, msg, linked_count).
    """
    from app.approvals import items_admin as IA
    key = _norm(text)
    if not key:
        return False, "no_text", 0
    code = (code or "").strip()[:60]
    if not code:
        return False, "code_required", 0
    conn = get_db()
    try:
        clash = IA.item_by_code(conn, code)
        if clash:
            return False, "duplicate_code", 0
        ok, res = IA.create_item(
            conn, {"code": code, "name": (text or "").strip()[:200],
                   "unit": unit, "category_name": category_name}, user, ip)
        if not ok:
            return False, res, 0
        row = IA.item_by_code(conn, code)
        n = link_existing_lines(conn, key, row["id"])
        _record(conn, key, "added", "code %s" % code, user)
        conn.commit()
        _tell_requesters(conn, key, "added",
                         "“%s” is now in the catalogue as %s. Pick it from "
                         "the list next time." % ((text or "").strip()[:80], code))
        conn.commit()
        return True, "added", n
    finally:
        conn.close()


def reject(text, note, user, ip=""):
    """Record that this text should not become an item, and why."""
    key = _norm(text)
    if not key:
        return False, "no_text"
    note = (note or "").strip()[:2000]
    if not note:
        return False, "note_required"
    conn = get_db()
    try:
        _record(conn, key, "rejected", note, user)
        conn.commit()
        _tell_requesters(conn, key, "rejected",
                         "“%s” will not be added to the catalogue. %s"
                         % ((text or "").strip()[:80], note))
        conn.commit()
        return True, "rejected"
    finally:
        conn.close()


def reopen(text, user, ip=""):
    """Undo a decision, so the text returns to the queue."""
    key = _norm(text)
    conn = get_db()
    try:
        conn.execute("DELETE FROM proc_off_catalogue WHERE text_key=?", (key,))
        conn.commit()
        return True, "reopened"
    finally:
        conn.close()


def _record(conn, key, decision, note, user):
    from app.approvals.services import _now
    who = (user or {}).get("username") or "system"
    conn.execute(
        "INSERT INTO proc_off_catalogue (text_key, decision, note, decided_by, decided_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(text_key) DO UPDATE SET decision=excluded.decision, "
        "note=excluded.note, decided_by=excluded.decided_by, "
        "decided_at=excluded.decided_at",
        (key, decision, note, who, _now()))
    try:
        from app.db import log_audit
        log_audit(who, "off_catalogue_" + decision, "%s — %s" % (key, note))
    except Exception:
        pass


def _tell_requesters(conn, key, decision, message):
    """Tell the people who typed it what was decided.

    They are the ones who will type it again otherwise, so they are the audience
    — not Purchasing, who just made the decision.
    """
    from app.approvals.services import notify_users
    who = [r["requester"] for r in conn.execute(
        "SELECT DISTINCT p.requester FROM pr_items i JOIN pr_requests p ON p.id=i.pr_id "
        f"WHERE {_KEY}=? AND COALESCE(p.requester,'') <> ''", (key,)).fetchall()]
    if who:
        notify_users(conn, who, "info", "Item request decided", message,
                     link="/procurement/off-catalogue")
