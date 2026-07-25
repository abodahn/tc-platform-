"""
PLM-lite services — style master, versioned tech packs, style BOM, sample rounds.

Two invariants carry the module:
  * a tech-pack VERSION is immutable — publishing v(n+1) snapshots v(n) and only
    the newest version accepts edits, so what was sent to the buyer stays intact;
  * a style cannot be released to production until its PP sample is approved.
"""
from datetime import datetime
from math import isfinite

from app.db import get_db
from .constants import (STYLE_STATUS, PRODUCTION_STATUSES, SAMPLE_STAGES,
                        SAMPLE_VERDICT, PP_STAGE, DEFAULT_SECTIONS,
                        BOM_KINDS, BOM_UNITS)


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _num(v, default=0.0):
    """Forgiving read of a stored or posted numeric — blank, None or garbage falls
    back, never 500. Non-finite is NOT a quantity: float('nan')/float('inf')
    parse happily and then satisfy every `> 0` / `<= 100` test, so they must be
    treated as garbage here too."""
    try:
        s = str(v).strip()
        n = float(s) if s not in ("", "None") else default
    except (TypeError, ValueError):
        return default
    return n if isfinite(n) else default


def _qty(v, default=None):
    """STRICT read of a quantity a human typed. Returns None for garbage and for
    nan / inf / 1e400 so the caller REFUSES the line instead of silently booking
    a zero (under-buying fabric) or an infinity. Blank -> `default`."""
    s = "" if v is None else str(v).strip()
    if s in ("", "None"):
        return default
    try:
        n = float(s)
    except ValueError:
        return None
    return n if isfinite(n) else None


def _iso_date(v):
    """Blank, or a real YYYY-MM-DD. Free text in sent_date silently corrupts the
    dashboard's ORDER BY sent_date and can never be reported on. Returns
    (value, ok)."""
    s = (v or "").strip()
    if not s:
        return None, True
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None, False
    return s, True


def _bell(conn, severity, title, message, link="/plm"):
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "plm", title, message, link, _now()))


def gross_consumption(consumption, wastage_pct):
    """INVARIANT: gross = net * (1 + wastage%/100). Every consumption figure that
    leaves this module is gross — buying net is how a cut room runs short."""
    return _num(consumption) * (1.0 + _num(wastage_pct) / 100.0)


# --- styles ---------------------------------------------------------------
def list_styles(status=None, buyer=None, q=None):
    conn = get_db()
    try:
        sql = "SELECT * FROM plm_styles WHERE 1=1"
        args = []
        if status:
            sql += " AND status=?"; args.append(status)
        if buyer:
            sql += " AND buyer=?"; args.append(buyer)
        if q:
            # LOWER on both sides: SQLite's LIKE ignores ASCII case, PostgreSQL's
            # does NOT, so a bare LIKE makes the search box work locally and
            # silently return nothing in production.
            sql += " AND (LOWER(style_ref) LIKE ? OR LOWER(name) LIKE ?)"
            args += [f"%{q.lower()}%", f"%{q.lower()}%"]
        # COALESCE, not a bare updated_at: PostgreSQL sorts NULLs FIRST on DESC
        # (SQLite sorts them last), so one NULL timestamp would pin that row to
        # the top of the list in production and the bottom locally.
        sql += " ORDER BY COALESCE(updated_at, created_at, '') DESC, id DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def buyers():
    conn = get_db()
    try:
        return [r["buyer"] for r in conn.execute(
            "SELECT DISTINCT buyer FROM plm_styles WHERE buyer IS NOT NULL AND buyer!='' "
            "ORDER BY buyer").fetchall()]
    finally:
        conn.close()


def create_style(data, user):
    """Returns (style_id, error). style_ref is the key orders quote — it must be unique."""
    ref = (data.get("style_ref") or "").strip()
    if not ref:
        return None, "style_ref_required"
    conn = get_db()
    try:
        # Case-INSENSITIVE uniqueness: "TC-KNIT-01" and "tc-knit-01" are the same
        # style to every human, but two rows here would split that style's tech
        # pack and BOM in half and only one of them would match the order.
        if conn.execute("SELECT id FROM plm_styles WHERE LOWER(style_ref)=LOWER(?)",
                        (ref,)).fetchone():
            return None, "style_ref_exists"
        # A style always starts in development. It advances to 'sampling' on its
        # first sample round and to approved/in_production only through
        # set_style_status, so the PP-sample gate can never be bypassed at create.
        now = _now()
        try:
            cur = conn.execute(
                "INSERT INTO plm_styles (style_ref,name,buyer,season,category,fabric,description,"
                "status,designer,merchandiser,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ref, data.get("name"), data.get("buyer"), data.get("season"), data.get("category"),
                 data.get("fabric"), data.get("description"), "development", data.get("designer"),
                 data.get("merchandiser"), (user or {}).get("username"), now, now))
        except Exception as exc:
            # The unique index is the real guard (see schema._ci_unique_ref); the
            # SELECT above only buys a friendly message. A concurrent create that
            # slipped between the two lands here — report the duplicate, not a 500.
            conn.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                return None, "style_ref_exists"
            raise
        conn.commit()
        return cur.lastrowid, None
    finally:
        conn.close()


def update_style(style_id, data, user):
    """Partial update of the descriptive fields. Status is NOT settable here —
    it goes through set_style_status so the PP gate cannot be bypassed."""
    fields = ["name", "buyer", "season", "category", "fabric", "description",
              "designer", "merchandiser"]
    sets, args = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f}=?"); args.append(data.get(f) or None)
    if not sets:
        return False
    conn = get_db()
    try:
        # Without this the UPDATE touches 0 rows and the caller still reports
        # success, so a stale link silently "saves" a style that no longer exists.
        if not conn.execute("SELECT id FROM plm_styles WHERE id=?", (style_id,)).fetchone():
            return False
        sets.append("updated_at=?"); args.append(_now())
        args.append(style_id)
        conn.execute("UPDATE plm_styles SET " + ", ".join(sets) + " WHERE id=?", args)
        conn.commit()
        return True
    finally:
        conn.close()


def _pp_approved(conn, style_id):
    """INVARIANT: only the LATEST pp round counts. An approved PP does not stay
    approved once a newer PP round has been sent — if the buyer rejects round 2,
    or round 2 is still out with them, bulk is NOT cleared. Counting "any
    approved pp round ever" would release fabric against a spec the buyer has
    since turned down."""
    # COALESCE: round_no is nullable, and PostgreSQL sorts NULLs FIRST on DESC
    # (SQLite last) — a NULL round would decide the gate in production only.
    r = conn.execute(
        "SELECT verdict FROM plm_samples WHERE style_id=? AND stage=? "
        "ORDER BY COALESCE(round_no,0) DESC, id DESC LIMIT 1", (style_id, PP_STAGE)).fetchone()
    return bool(r) and r["verdict"] == "approved"


def set_style_status(style_id, status, user):
    """BUSINESS RULE: a style may not be marked approved / in_production while its
    PP (pre-production) sample is unapproved. The PP sample is the buyer's last
    gate; releasing bulk without it is how a factory cuts fabric against a spec
    the buyer later rejects. Returns (ok, message_key)."""
    if status not in STYLE_STATUS:
        return False, "bad_status"
    conn = get_db()
    try:
        st = conn.execute("SELECT status FROM plm_styles WHERE id=?", (style_id,)).fetchone()
        if not st:
            return False, "style_not_found"
        if status in PRODUCTION_STATUSES and not _pp_approved(conn, style_id):
            return False, "pp_sample_not_approved"
        was = st["status"]
        conn.execute("UPDATE plm_styles SET status=?, updated_at=? WHERE id=?",
                     (status, _now(), style_id))
        if status == "approved" and was != "approved":   # only on transition — no repeat bells
            s = conn.execute("SELECT style_ref,name,buyer FROM plm_styles WHERE id=?",
                             (style_id,)).fetchone()
            _bell(conn, "info", f"Style approved: {s['style_ref']}",
                  f"{s['style_ref']} — {s['name'] or ''} ({s['buyer'] or 'no buyer'}) is approved "
                  f"for production; PP sample signed off.", f"/plm/styles/{style_id}")
        conn.commit()
        return True, status
    finally:
        conn.close()


def get_style(style_id, techpack_id=None):
    """Full style bundle. techpack_id opens a past version read-only; default is
    the newest version."""
    conn = get_db()
    try:
        s = conn.execute("SELECT * FROM plm_styles WHERE id=?", (style_id,)).fetchone()
        if not s:
            return None
        style = dict(s)
        versions = [dict(r) for r in conn.execute(
            "SELECT * FROM plm_techpacks WHERE style_id=? ORDER BY version DESC",
            (style_id,)).fetchall()]
        tp = None
        if techpack_id:
            # compared as text: ?v= comes straight off the query string and int()
            # would raise a 500 on "abc". No match simply falls back to the newest
            # version — and a techpack id of ANOTHER style can never match here.
            want = str(techpack_id).strip()
            tp = next((v for v in versions if str(v["id"]) == want), None)
        if not tp:
            tp = versions[0] if versions else None
        sections, specs = [], []
        if tp:
            sections = [dict(r) for r in conn.execute(
                "SELECT * FROM plm_techpack_sections WHERE techpack_id=? ORDER BY seq, id",
                (tp["id"],)).fetchall()]
            specs = [dict(r) for r in conn.execute(
                "SELECT * FROM plm_techpack_specs WHERE techpack_id=? ORDER BY seq, id",
                (tp["id"],)).fetchall()]
        bom = [dict(r) for r in conn.execute(
            "SELECT * FROM plm_bom WHERE style_id=? ORDER BY "
            "CASE kind WHEN 'fabric' THEN 0 WHEN 'trim' THEN 1 ELSE 2 END, id", (style_id,)).fetchall()]
        for b in bom:
            # 6dp, same as bom_for_order: a trim at 0.00012 kg/unit rounds to 0.0001
            # at 4dp, so the screen would show a figure 17% below the one the costing
            # hook hands out for the same line.
            b["gross"] = round(gross_consumption(b["consumption"], b["wastage_pct"]), 6)
        samples = [dict(r) for r in conn.execute(
            "SELECT * FROM plm_samples WHERE style_id=? ORDER BY id DESC", (style_id,)).fetchall()]
        return {
            "style": style,
            "versions": versions,
            "techpack": tp,
            "is_latest": bool(tp) and tp["id"] == versions[0]["id"],
            "sections": sections,
            "specs": specs,
            "bom": bom,
            "samples": samples,
            "pp_ok": _pp_approved(conn, style_id),
            "orders": _orders_for(conn, style["style_ref"]),
        }
    finally:
        conn.close()


def _orders_for(conn, style_ref):
    """Customer orders quoting this style_ref. ord_orders may not exist yet on a
    fresh database — degrade to an empty list rather than 500."""
    style_ref = (style_ref or "").strip()
    if not style_ref:
        return []
    try:
        # TRIM on the column: a stray trailing space in the ORDER's style ref is
        # invisible on both screens and would silently empty this panel, which is
        # the only place the two modules are visibly linked.
        # COALESCE, not a bare ship_date: SQLite sorts NULLs FIRST on ASC and
        # PostgreSQL sorts them LAST, so an order with no ship date would head
        # the list locally and tail it in production. Undated goes last, always.
        return [dict(r) for r in conn.execute(
            "SELECT id,order_no,buyer,qty,ship_date,status FROM ord_orders "
            "WHERE LOWER(TRIM(style_ref))=LOWER(?) "
            "ORDER BY COALESCE(ship_date,'9999-12-31') ASC, id DESC LIMIT 30",
            (style_ref,)).fetchall()]
    except Exception:
        return []


# --- versioned tech pack --------------------------------------------------
def _latest(conn, style_id):
    r = conn.execute("SELECT * FROM plm_techpacks WHERE style_id=? ORDER BY version DESC LIMIT 1",
                     (style_id,)).fetchone()
    return dict(r) if r else None


def publish_version(style_id, change_note, user):
    """Publish the next tech-pack version. v1 starts from the default section
    titles; every later version SNAPSHOTS the current one — copying its sections
    and specs into new rows. Editing the new version therefore cannot reach back
    and alter what an earlier version said. Returns (techpack_id, error)."""
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM plm_styles WHERE id=?", (style_id,)).fetchone():
            return None, "style_not_found"
        prev = _latest(conn, style_id)
        cur = conn.execute(
            "INSERT INTO plm_techpacks (style_id,version,change_note,published_by,published_at) "
            "VALUES (?,?,?,?,?)",
            (style_id, (prev["version"] + 1) if prev else 1, (change_note or "").strip() or None,
             (user or {}).get("username"), _now()))
        tid = cur.lastrowid
        if prev:
            conn.execute(
                "INSERT INTO plm_techpack_sections (techpack_id,seq,title,body) "
                "SELECT ?,seq,title,body FROM plm_techpack_sections WHERE techpack_id=?",
                (tid, prev["id"]))
            conn.execute(
                "INSERT INTO plm_techpack_specs (techpack_id,seq,pom,size,spec_value,tolerance) "
                "SELECT ?,seq,pom,size,spec_value,tolerance FROM plm_techpack_specs WHERE techpack_id=?",
                (tid, prev["id"]))
        else:
            for i, title in enumerate(DEFAULT_SECTIONS, start=1):
                conn.execute("INSERT INTO plm_techpack_sections (techpack_id,seq,title,body) "
                             "VALUES (?,?,?,?)", (tid, i, title, None))
        conn.execute("UPDATE plm_styles SET updated_at=? WHERE id=?", (_now(), style_id))
        conn.commit()
        return tid, None
    finally:
        conn.close()


def _editable(conn, techpack_id):
    """Only the newest version of a style may be edited — history is read-only."""
    r = conn.execute("SELECT style_id FROM plm_techpacks WHERE id=?", (techpack_id,)).fetchone()
    if not r:
        return False, "techpack_not_found"
    top = _latest(conn, r["style_id"])
    if not top or top["id"] != techpack_id:
        return False, "version_is_frozen"
    return True, None


def _next_seq(conn, table, techpack_id):
    return (conn.execute(f"SELECT COALESCE(MAX(seq),0) AS c FROM {table} WHERE techpack_id=?",
                         (techpack_id,)).fetchone()["c"] or 0) + 1


def add_section(techpack_id, title, body):
    conn = get_db()
    try:
        ok, err = _editable(conn, techpack_id)
        if not ok:
            return False, err
        if not (title or "").strip():
            return False, "title_required"
        conn.execute("INSERT INTO plm_techpack_sections (techpack_id,seq,title,body) VALUES (?,?,?,?)",
                     (techpack_id, _next_seq(conn, "plm_techpack_sections", techpack_id),
                      title.strip(), (body or "").strip() or None))
        conn.commit()
        return True, None
    finally:
        conn.close()


def add_spec(techpack_id, data):
    conn = get_db()
    try:
        ok, err = _editable(conn, techpack_id)
        if not ok:
            return False, err
        pom = (data.get("pom") or "").strip()
        if not pom:
            return False, "pom_required"
        tol = _qty(data.get("tolerance"), 0.0)
        if tol is None or tol < 0:       # a tolerance is a +/- band; negative is a typo
            return False, "bad_tolerance"
        # A garment is never -5 cm across the chest, and never 0 cm either: the form
        # field is optional, so a blank must be REFUSED rather than booked as 0. Spec
        # lines are add-only and every later version snapshots them, so a 0 cm target
        # would follow the style forever and the cutting room reads it as a real spec.
        val = _qty(data.get("spec_value"))
        if val is None or val <= 0:
            return False, "bad_spec_value"
        size = (data.get("size") or "").strip()
        # INVARIANT: one row per (point of measure, size). Two rows for the same POM
        # and size are two different targets for the same measurement — QC cannot
        # know which one to pass, and the snapshot copies the contradiction into
        # every future version. A re-posted form is the usual cause.
        if conn.execute("SELECT id FROM plm_techpack_specs WHERE techpack_id=? "
                        "AND LOWER(pom)=LOWER(?) AND LOWER(COALESCE(size,''))=LOWER(?)",
                        (techpack_id, pom, size)).fetchone():
            return False, "duplicate_spec"
        conn.execute(
            "INSERT INTO plm_techpack_specs (techpack_id,seq,pom,size,spec_value,tolerance) "
            "VALUES (?,?,?,?,?,?)",
            (techpack_id, _next_seq(conn, "plm_techpack_specs", techpack_id), pom,
             size or None, val, tol))
        conn.commit()
        return True, None
    finally:
        conn.close()


def get_techpack(techpack_id):
    """A single version, read-only, with its style header."""
    conn = get_db()
    try:
        tp = conn.execute("SELECT * FROM plm_techpacks WHERE id=?", (techpack_id,)).fetchone()
        if not tp:
            return None
        tp = dict(tp)
        st = conn.execute("SELECT * FROM plm_styles WHERE id=?", (tp["style_id"],)).fetchone()
        top = _latest(conn, tp["style_id"])
        return {
            "style": dict(st) if st else {},
            "techpack": tp,
            "is_latest": bool(top) and top["id"] == tp["id"],
            "sections": [dict(r) for r in conn.execute(
                "SELECT * FROM plm_techpack_sections WHERE techpack_id=? ORDER BY seq, id",
                (techpack_id,)).fetchall()],
            "specs": [dict(r) for r in conn.execute(
                "SELECT * FROM plm_techpack_specs WHERE techpack_id=? ORDER BY seq, id",
                (techpack_id,)).fetchall()],
        }
    finally:
        conn.close()


# --- style BOM master -----------------------------------------------------
def add_bom_line(style_id, data, user):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM plm_styles WHERE id=?", (style_id,)).fetchone():
            return False, "style_not_found"
        material = (data.get("material") or "").strip()
        if not material:
            return False, "material_required"
        cons = _qty(data.get("consumption"))
        # A line that consumes nothing is a typo; 1e9 per unit is a fat finger, and
        # anything larger overflows to inf once wastage is applied.
        if cons is None or cons <= 0 or cons > 1e9:
            return False, "bad_consumption"
        wast = _qty(data.get("wastage_pct"), 0.0)   # blank means 0%, garbage does not
        if wast is None or wast < 0 or wast > 100:
            return False, "bad_wastage"         # wastage is a percentage of the net figure
        # kind and unit come from <select>s, so anything else is a crafted request.
        # REFUSE it: silently falling back to 'm' would change the unit a purchase is
        # placed in (a "tonnes" line quietly booked as metres), and kind drives the
        # fabric/trim grouping every downstream reader relies on.
        kind = (data.get("kind") or "fabric").strip().lower()
        if kind not in BOM_KINDS:
            return False, "bad_kind"
        unit = (data.get("unit") or "m").strip().lower()
        if unit not in BOM_UNITS:
            return False, "bad_unit"
        placement = (data.get("placement") or "").strip()
        colour = (data.get("colour") or "").strip()
        # DOUBLE-SUBMIT GUARD: the same material in the same placement and colour
        # twice is a re-posted form, not a second material — and every per-order
        # BOM seeded from this style would then buy that quantity twice. Lines are
        # add-only (no delete), so the duplicate would be permanent.
        if conn.execute(
                "SELECT id FROM plm_bom WHERE style_id=? AND LOWER(material)=LOWER(?) "
                "AND LOWER(COALESCE(placement,''))=LOWER(?) "
                "AND LOWER(COALESCE(colour,''))=LOWER(?)",
                (style_id, material, placement, colour)).fetchone():
            return False, "duplicate_line"
        conn.execute(
            "INSERT INTO plm_bom (style_id,material,kind,placement,consumption,unit,wastage_pct,"
            "supplier,colour,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (style_id, material, kind, placement or None, cons,
             unit, wast, data.get("supplier"), colour or None, _now()))
        conn.execute("UPDATE plm_styles SET updated_at=? WHERE id=?", (_now(), style_id))
        conn.commit()
        return True, None
    finally:
        conn.close()


def bom_for_order(style_ref, order_qty=1):
    """LINKAGE HOOK — seed a per-ORDER BOM (app/costing owns those) from this
    style's master BOM. Returns one dict per line with gross_per_unit and
    total_qty = gross_per_unit * order_qty. Unknown style / bad qty -> []; this
    is a read helper and must never raise into a caller's transaction."""
    ref = (style_ref or "").strip()
    if not ref:
        return []
    qty = max(_qty(order_qty, 0.0) or 0.0, 0.0)   # garbage/nan/negative -> 0, never NaN totals
    conn = get_db()
    try:
        s = conn.execute("SELECT id FROM plm_styles WHERE LOWER(style_ref)=LOWER(?)",
                         (ref,)).fetchone()
        if not s:
            return []
        out = []
        for r in conn.execute("SELECT * FROM plm_bom WHERE style_id=? ORDER BY id",
                              (s["id"],)).fetchall():
            g = gross_consumption(r["consumption"], r["wastage_pct"])
            tot = g * qty
            out.append({
                "material": r["material"], "kind": r["kind"], "placement": r["placement"],
                "unit": r["unit"], "supplier": r["supplier"], "colour": r["colour"],
                "consumption": _num(r["consumption"]), "wastage_pct": _num(r["wastage_pct"]),
                # gross*qty can overflow to inf at absurd inputs. inf is not a
                # quantity: hand it to costing and every total downstream becomes
                # inf/nan silently. Refuse it here, exactly as the inputs are.
                # 6dp, not 4: a trim at 0.00012 kg/unit rounds to 0.0001 at 4dp —
                # a 17% error on a figure another module multiplies by the order qty.
                "gross_per_unit": round(g, 6),
                "total_qty": round(tot, 4) if isfinite(tot) else 0.0,
            })
        return out
    except Exception:
        return []
    finally:
        conn.close()


# --- sample rounds --------------------------------------------------------
def record_sample(style_id, data, user):
    """Record a sample round. round_no auto-increments per stage and revision_no
    counts how many times that stage already came back rejected/revise — both are
    stored so the record stays true even after later rounds. Returns (ok, err)."""
    stage = (data.get("stage") or "").strip()
    if stage not in SAMPLE_STAGES:
        return False, "bad_stage"
    verdict = (data.get("verdict") or "pending").strip()
    if verdict not in SAMPLE_VERDICT:
        return False, "bad_verdict"
    sent, ok = _iso_date(data.get("sent_date"))
    if not ok:
        return False, "bad_date"
    conn = get_db()
    try:
        st = conn.execute("SELECT style_ref,name,status FROM plm_styles WHERE id=?",
                          (style_id,)).fetchone()
        if not st:
            return False, "style_not_found"
        rnd = (conn.execute("SELECT COALESCE(MAX(round_no),0) AS c FROM plm_samples "
                            "WHERE style_id=? AND stage=?", (style_id, stage)).fetchone()["c"] or 0) + 1
        rev = conn.execute("SELECT COUNT(*) AS c FROM plm_samples WHERE style_id=? AND stage=? "
                           "AND verdict IN ('rejected','revise')", (style_id, stage)).fetchone()["c"]
        now = _now()
        conn.execute(
            "INSERT INTO plm_samples (style_id,stage,round_no,revision_no,sent_date,comments,"
            "verdict,decided_at,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (style_id, stage, rnd, rev, sent, data.get("comments"),
             verdict, (now if verdict != "pending" else None), (user or {}).get("username"), now))
        if verdict == "rejected":
            _bell(conn, "warning", f"Sample rejected: {st['style_ref']} {stage.upper()}",
                  f"{stage.upper()} round {rnd} rejected — {(data.get('comments') or 'no comment')[:100]}",
                  f"/plm/styles/{style_id}")
        # Sending the first sample IS the move into sampling — no one should have
        # to remember to flip the status by hand. Later statuses are deliberate.
        new_status = "sampling" if st["status"] == "development" else st["status"]
        conn.execute("UPDATE plm_styles SET status=?, updated_at=? WHERE id=?",
                     (new_status, now, style_id))
        conn.commit()
        return True, None
    finally:
        conn.close()


def set_verdict(sample_id, verdict, comments, user):
    """The buyer's answer usually arrives after the sample was sent. Returns (ok, err)."""
    if verdict not in SAMPLE_VERDICT:
        return False, "bad_verdict"
    conn = get_db()
    try:
        s = conn.execute("SELECT sp.*, st.style_ref FROM plm_samples sp "
                         "JOIN plm_styles st ON st.id=sp.style_id WHERE sp.id=?",
                         (sample_id,)).fetchone()
        if not s:
            return False, "sample_not_found"
        conn.execute("UPDATE plm_samples SET verdict=?, comments=COALESCE(?,comments), decided_at=? "
                     "WHERE id=?",
                     (verdict, (comments or "").strip() or None,
                      (_now() if verdict != "pending" else None), sample_id))
        if verdict == "rejected" and s["verdict"] != "rejected":   # alert on transition only
            _bell(conn, "warning", f"Sample rejected: {s['style_ref']} {s['stage'].upper()}",
                  f"{s['stage'].upper()} round {s['round_no']} rejected — "
                  f"{(comments or s['comments'] or 'no comment')[:100]}",
                  f"/plm/styles/{s['style_id']}")
        conn.commit()
        return True, None
    finally:
        conn.close()


# --- dashboard ------------------------------------------------------------
def dashboard():
    conn = get_db()
    try:
        def one(sql, args=()):
            return conn.execute(sql, args).fetchone()["c"]
        by_status = {s: 0 for s in STYLE_STATUS}
        for r in conn.execute("SELECT status, COUNT(*) AS c FROM plm_styles GROUP BY status").fetchall():
            by_status[r["status"]] = r["c"]
        d = {
            "styles_total": one("SELECT COUNT(*) c FROM plm_styles"),
            "by_status": by_status,
            "styles_approved": by_status.get("approved", 0) + by_status.get("in_production", 0),
            "samples_pending": one("SELECT COUNT(*) c FROM plm_samples WHERE verdict='pending'"),
            "samples_rejected": one("SELECT COUNT(*) c FROM plm_samples WHERE verdict IN ('rejected','revise')"),
            "techpack_versions": one("SELECT COUNT(*) c FROM plm_techpacks"),
        }
        # Styles still blocked from bulk — computed with the SAME latest-round rule
        # as the release gate (_pp_approved), so the KPI and the gate can never
        # disagree. Rows arrive oldest-first per style, so the last one wins.
        pp_ok = {}
        for r in conn.execute("SELECT style_id, verdict FROM plm_samples WHERE stage=? "
                              "ORDER BY style_id, COALESCE(round_no,0), id", (PP_STAGE,)).fetchall():
            pp_ok[r["style_id"]] = (r["verdict"] == "approved")
        d["awaiting_pp"] = sum(1 for s in conn.execute(
            "SELECT id FROM plm_styles WHERE status IN ('development','sampling')").fetchall()
            if not pp_ok.get(s["id"]))
        d["open_samples"] = [dict(r) for r in conn.execute(
            "SELECT sp.*, st.style_ref, st.name AS style_name, st.buyer FROM plm_samples sp "
            "JOIN plm_styles st ON st.id=sp.style_id WHERE sp.verdict IN ('pending','rejected','revise') "
            # COALESCE: sent_date is optional, and PostgreSQL sorts NULLs FIRST on
            # DESC (SQLite last) — undated rounds would otherwise fill this top-10
            # in production and push the genuinely recent ones off the dashboard.
            "ORDER BY COALESCE(sp.sent_date,'') DESC, sp.id DESC LIMIT 10").fetchall()]
        d["recent_styles"] = [dict(r) for r in conn.execute(
            "SELECT * FROM plm_styles ORDER BY COALESCE(updated_at, created_at, '') DESC, "
            "id DESC LIMIT 8").fetchall()]
        return d
    finally:
        conn.close()


# --- dataset export -------------------------------------------------------
def _t(v):
    """CSV/JSON cell for a text column: never None, never a Python object."""
    return "" if v is None else str(v)


def _q(v, dp=3):
    """Quantity cell — a number, never a string, so a report can sum and sort it.

    dp=6 for consumption: a trim at 0.00012 kg/unit disappears entirely at 3dp,
    which is why the screens and bom_for_order() also carry 6 (see get_style)."""
    return round(_num(v), dp)


# 20000 rows: line-level tables grow with styles x lines, and a CSV a merchandiser
# opens in Excel is not the place to stream a whole database. A factory's style
# master is far below this; the cap only exists so a runaway export cannot happen.
_ROW_CAP = 20000


def export_dataset(key):
    """key -> (headers, rows) for CSV/JSON export. (None, None) if unknown.

    Keys: styles | techpack-versions | measurement-specs | bom | samples | summary.
    """
    if key == "styles":
        # Reuses the styles page's own query — the register, unfiltered, in the
        # same order the screen shows. One row per style, so no cap is needed.
        return (["Style Ref", "Name", "Buyer", "Season", "Category", "Fabric",
                 "Description", "Status", "Designer", "Merchandiser",
                 "Created By", "Created At", "Updated At"],
                [[_t(s["style_ref"]), _t(s["name"]), _t(s["buyer"]), _t(s["season"]),
                  _t(s["category"]), _t(s["fabric"]), _t(s["description"]),
                  _t(s["status"]), _t(s["designer"]), _t(s["merchandiser"]),
                  _t(s["created_by"]), _t(s["created_at"]), _t(s["updated_at"])]
                 for s in list_styles()])

    if key == "summary":
        d = dashboard()
        rows = [["Styles total", d["styles_total"]]]
        rows += [[s.replace("_", " ").capitalize(), d["by_status"].get(s, 0)]
                 for s in STYLE_STATUS]
        rows += [["Approved for bulk", d["styles_approved"]],
                 ["Samples pending", d["samples_pending"]],
                 ["Samples rejected or to revise", d["samples_rejected"]],
                 ["Awaiting PP approval", d["awaiting_pp"]],
                 ["Tech-pack versions", d["techpack_versions"]]]
        return (["Metric", "Value"], rows)

    conn = get_db()
    try:
        if key == "techpack-versions":
            rows = conn.execute(
                "SELECT s.style_ref, s.name, t.version, t.change_note, t.published_by, "
                "t.published_at, "
                "(SELECT COUNT(*) FROM plm_techpack_sections x WHERE x.techpack_id=t.id) secs, "
                "(SELECT COUNT(*) FROM plm_techpack_specs x WHERE x.techpack_id=t.id) specs, "
                "(SELECT MAX(version) FROM plm_techpacks m WHERE m.style_id=t.style_id) top "
                "FROM plm_techpacks t JOIN plm_styles s ON s.id=t.style_id "
                "ORDER BY s.style_ref, t.version LIMIT ?", (_ROW_CAP,)).fetchall()
            return (["Style Ref", "Style Name", "Version", "Change Note", "Published By",
                     "Published At", "Sections", "Measurements", "Is Latest"],
                    [[_t(r["style_ref"]), _t(r["name"]), r["version"], _t(r["change_note"]),
                      _t(r["published_by"]), _t(r["published_at"]), r["secs"], r["specs"],
                      "yes" if r["version"] == r["top"] else "no"] for r in rows])

        if key == "measurement-specs":
            # The LATEST version only: an older version's numbers are history, and a
            # QC table holding two targets for one point of measure is unusable.
            # tech-pack-versions above shows what history exists.
            rows = conn.execute(
                "SELECT s.style_ref, s.name, t.version, p.pom, p.size, p.spec_value, p.tolerance "
                "FROM plm_techpack_specs p JOIN plm_techpacks t ON t.id=p.techpack_id "
                "JOIN plm_styles s ON s.id=t.style_id "
                "WHERE t.version=(SELECT MAX(version) FROM plm_techpacks m "
                "                 WHERE m.style_id=t.style_id) "
                "ORDER BY s.style_ref, p.seq, p.id LIMIT ?", (_ROW_CAP,)).fetchall()
            return (["Style Ref", "Style Name", "Version", "Point of Measure", "Size",
                     "Spec (cm)", "Tolerance +/- (cm)"],
                    [[_t(r["style_ref"]), _t(r["name"]), r["version"], _t(r["pom"]),
                      _t(r["size"]), _q(r["spec_value"]), _q(r["tolerance"])] for r in rows])

        if key == "bom":
            rows = conn.execute(
                "SELECT s.style_ref, s.name, b.kind, b.material, b.placement, b.colour, "
                "b.supplier, b.consumption, b.unit, b.wastage_pct FROM plm_bom b "
                "JOIN plm_styles s ON s.id=b.style_id ORDER BY s.style_ref, "
                "CASE b.kind WHEN 'fabric' THEN 0 WHEN 'trim' THEN 1 ELSE 2 END, b.id "
                "LIMIT ?", (_ROW_CAP,)).fetchall()
            # Gross, not net — buying net is how a cut room runs short (see
            # gross_consumption). Both figures ship so the wastage is auditable.
            return (["Style Ref", "Style Name", "Kind", "Material", "Placement", "Colour",
                     "Supplier", "Net per Unit", "Unit", "Wastage %", "Gross per Unit"],
                    [[_t(r["style_ref"]), _t(r["name"]), _t(r["kind"]), _t(r["material"]),
                      _t(r["placement"]), _t(r["colour"]), _t(r["supplier"]),
                      _q(r["consumption"], 6), _t(r["unit"]), _q(r["wastage_pct"]),
                      _q(gross_consumption(r["consumption"], r["wastage_pct"]), 6)]
                     for r in rows])

        if key == "samples":
            rows = conn.execute(
                # COALESCE on the counts: both columns are nullable, and a round
                # number must stay a NUMBER in the JSON so a report can sort on it.
                "SELECT s.style_ref, s.name, s.buyer, sp.stage, COALESCE(sp.round_no,0) rnd, "
                "COALESCE(sp.revision_no,0) rev, "
                "sp.sent_date, sp.verdict, sp.decided_at, sp.comments, sp.created_by "
                "FROM plm_samples sp JOIN plm_styles s ON s.id=sp.style_id "
                "ORDER BY s.style_ref, sp.stage, COALESCE(sp.round_no,0), sp.id "
                "LIMIT ?", (_ROW_CAP,)).fetchall()
            return (["Style Ref", "Style Name", "Buyer", "Stage", "Round", "Revisions",
                     "Sent Date", "Verdict", "Decided At", "Buyer Comments", "Recorded By"],
                    [[_t(r["style_ref"]), _t(r["name"]), _t(r["buyer"]), _t(r["stage"]),
                      r["rnd"], r["rev"], _t(r["sent_date"]),
                      _t(r["verdict"]), _t(r["decided_at"]), _t(r["comments"]),
                      _t(r["created_by"])] for r in rows])
    finally:
        conn.close()
    return (None, None)
