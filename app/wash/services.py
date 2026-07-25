"""
Wash / finishing recipe library — services.

The whole module exists so a shade is repeatable: steps and chemicals belong to
a recipe VERSION, a batch records the version it ran, and a version can only go
to bulk once its lab dip is approved.
"""
import math
from datetime import date, datetime, timezone

from app.db import get_db
from .constants import (VERSION_STATUS, LABDIP_VERDICT, BATCH_STATUS, WASH_TYPES,
                        OPERATIONS, AMBIENT_C,
                        REF_WATER_L_PER_KG, REF_HEAT_LK_PER_KG, REF_CHEM_G_PER_KG,
                        IMPACT_LOW_MAX, IMPACT_MED_MAX,
                        DEV_TIME_PCT, DEV_TEMP_C, DEV_LOAD_PCT)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _f(v, d=0.0):
    """Coerce a form value to float. Blank / None / garbage -> d, never raises.

    inf and nan count as garbage. float('inf') and float('1e400') both parse, and
    a step can never be deleted — so one 'inf' typed into Minutes would make that
    version's cycle time infinite FOREVER, and a nan would silently disagree with
    the floored aggregates (max(0, nan) is 0, but the row still prints 'nan')."""
    try:
        s = str(v).strip()
        if s in ("", "None"):
            return d
        x = float(s)
    except (TypeError, ValueError):
        return d
    return x if math.isfinite(x) else d


# A row id lives in an INTEGER column (32-bit on PostgreSQL), so anything past
# this is not "a big id", it is not an id at all. Binding it would raise
# OverflowError on SQLite / "integer out of range" on PostgreSQL — an uncaught
# 500 instead of a clean "not found".
MAX_ID = 2147483647

# Nothing physical in a laundry approaches this. Beyond it the value is a typo
# or an attack, and since a step can never be deleted one such row would poison
# that version's totals forever.
MAX_QTY = 1e9


def _i(v, d=None):
    # Routed through _f so 'inf' cannot reach int(), which raises OverflowError —
    # an uncaught 500 rather than a clean 'not_found'.
    x = _f(v, None)
    if x is None or abs(x) > MAX_ID:
        return d
    return int(x)


def _pick(v, allowed, default):
    """An enum field must come from its list. The <select> in the template is a
    convenience, not a guarantee: a hand-rolled POST bypasses it."""
    s = str(v or "").strip()
    return s if s in allowed else default


def _nn(v, d=0.0):
    """A physical quantity can never be negative. Floored here so one bad stored
    row (a '-100' load typed before the write guards existed) can never CANCEL a
    good row out of an aggregate and under-report water, chemical or cycle."""
    return max(0.0, _f(v, d))


def _bad_qty(data, fields):
    """Reason one of these physical-quantity form fields is unusable, else None.
    Negative is nonsense (it would SUBTRACT from a water/chemical total); absurdly
    large is a typo that would never be removable again."""
    for f in fields:
        x = _f(data.get(f))
        if x < 0:
            return "negative_value"
        if x > MAX_QTY:
            return "value_out_of_range"
    return None


def _bell(conn, severity, title, message, link="/wash"):
    """Surface an alert on the platform notification bell."""
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "wash", title, message, link, _now()))


# --- derived recipe maths -------------------------------------------------
def totals(steps):
    """Derived cycle maths for a version's steps. Invariants:

      bath_l    = step.load_kg x step.liquor_ratio        (0 -> dry step, no bath)
      cycle_min = SUM(step.minutes)
      water_l   = SUM(bath_l). With one constant load and ratio this is exactly
                  load_kg x liquor_ratio x number_of_baths -- the shop-floor formula.
      chem_g    = SUM(gpl x bath_l  +  owg_pct/100 x load_kg x 1000)
                  i.e. g/L doses the BATH, %OWG doses the dry GOODS.
      heat_lk   = SUM(heated_mass x max(0, temp_c - AMBIENT_C)) in litre-kelvin,
                  where heated_mass is the bath litres, or the goods kg on a dry
                  step (so a long hot tumble is not free). Energy proxy only.
      load_kg   = the largest load_kg of ANY step (a dry step's load is still the
                  goods mass), taken as the version's nominal machine load.
      max_temp_c= peak BATH temperature; a dryer's air temperature is not a bath
                  parameter an operator records against, so it is excluded.
    """
    t = {"steps": len(steps), "baths": 0, "cycle_min": 0.0, "water_l": 0.0,
         "chem_g": 0.0, "heat_lk": 0.0, "load_kg": 0.0, "max_temp_c": 0.0}
    for s in steps:
        load = _nn(s.get("load_kg"))
        mins = _nn(s.get("minutes"))
        temp = _nn(s.get("temp_c"))
        bath = load * _nn(s.get("liquor_ratio"))
        t["cycle_min"] += mins
        t["water_l"] += bath
        if bath > 0:
            t["baths"] += 1
            t["max_temp_c"] = max(t["max_temp_c"], temp)
        t["load_kg"] = max(t["load_kg"], load)
        t["heat_lk"] += (bath if bath > 0 else load) * max(0.0, temp - AMBIENT_C)
        for c in (s.get("chemicals") or []):
            t["chem_g"] += _nn(c.get("gpl")) * bath + _nn(c.get("owg_pct")) / 100.0 * load * 1000.0
    for k in ("cycle_min", "water_l", "chem_g", "heat_lk", "load_kg", "max_temp_c"):
        t[k] = round(t[k], 2)
    return t


def impact(t):
    """Internal environmental indicator — NOT a certified EIM score.

    Three intensity ratios per kg of goods, each expressed as a percentage of an
    internal reference load and capped at 100, then averaged with equal weight:
        water  = (water_l / load_kg) / REF_WATER_L_PER_KG
        energy = (heat_lk / load_kg) / REF_HEAT_LK_PER_KG
        chem   = (chem_g  / load_kg) / REF_CHEM_G_PER_KG
    LOWER IS BETTER. With no goods weight there is no intensity to measure, so
    the score is None rather than a division by zero.
    """
    load = _f(t.get("load_kg"))
    if load <= 0:
        return {"score": None, "band": "na", "water": 0.0, "energy": 0.0, "chem": 0.0}
    # Clamped to 0..100: a negative index would band a filthy recipe as "low".
    def ix(v, ref):
        return max(0.0, min(100.0, 100.0 * (_f(v) / load) / ref))
    w = ix(t.get("water_l"), REF_WATER_L_PER_KG)
    e = ix(t.get("heat_lk"), REF_HEAT_LK_PER_KG)
    c = ix(t.get("chem_g"), REF_CHEM_G_PER_KG)
    score = int(round((w + e + c) / 3.0))
    band = "low" if score <= IMPACT_LOW_MAX else ("medium" if score <= IMPACT_MED_MAX else "high")
    return {"score": score, "band": band,
            "water": round(w, 1), "energy": round(e, 1), "chem": round(c, 1)}


def _steps_of(conn, version_id):
    steps = [dict(r) for r in conn.execute(
        "SELECT * FROM wsh_steps WHERE version_id=? ORDER BY step_no, id", (version_id,)).fetchall()]
    for s in steps:
        # _nn, not _f: totals() floors negatives, so the per-row figures printed on
        # the sheet must floor them too or the rows would not add up to the total.
        s["bath_l"] = round(_nn(s["load_kg"]) * _nn(s["liquor_ratio"]), 2)
        s["chemicals"] = [dict(c) for c in conn.execute(
            "SELECT * FROM wsh_chemicals WHERE step_id=? ORDER BY id", (s["id"],)).fetchall()]
        for c in s["chemicals"]:
            # g/L doses the bath, %OWG doses the dry goods — see totals().
            c["qty_g"] = round(_nn(c["gpl"]) * s["bath_l"]
                               + _nn(c["owg_pct"]) / 100.0 * _nn(s["load_kg"]) * 1000.0, 1)
    return steps


def version_totals(version_id):
    """totals() for a stored version — the one entry point batches compare against."""
    conn = get_db()
    try:
        return totals(_steps_of(conn, version_id))
    finally:
        conn.close()


# --- reads ----------------------------------------------------------------
def list_recipes(wash_type=None, status=None):
    """Recipes with their version count, bulk standard and latest status.
    ponytail: one version query per recipe — fine for a few hundred recipes,
    fold into a GROUP BY if the library ever gets big."""
    conn = get_db()
    try:
        q = "SELECT * FROM wsh_recipes WHERE 1=1"
        args = []
        if wash_type:
            q += " AND wash_type=?"; args.append(wash_type)
        q += " ORDER BY code ASC, id ASC"
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        out = []
        for r in rows:
            vs = [dict(v) for v in conn.execute(
                "SELECT * FROM wsh_versions WHERE recipe_id=? ORDER BY version DESC",
                (r["id"],)).fetchall()]
            r["versions"] = len(vs)
            r["latest"] = vs[0]["version"] if vs else 0
            r["latest_status"] = vs[0]["status"] if vs else "draft"
            appr = [v for v in vs if v["status"] == "approved"]
            r["approved_version"] = appr[0]["version"] if appr else None
            if status and r["latest_status"] != status:
                continue
            out.append(r)
        return out
    finally:
        conn.close()


def get_recipe(recipe_id, version_id=None):
    """Recipe + all its versions + the selected version's steps, impact and history.
    Returns None when the recipe does not exist."""
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM wsh_recipes WHERE id=?", (recipe_id,)).fetchone()
        if not r:
            return None
        versions = [dict(v) for v in conn.execute(
            "SELECT * FROM wsh_versions WHERE recipe_id=? ORDER BY version DESC",
            (recipe_id,)).fetchall()]
        sel = None
        want = _i(version_id)          # ?v=abc must fall back, not raise a ValueError
        if want is not None:
            sel = next((v for v in versions if v["id"] == want), None)
        if not sel:
            sel = next((v for v in versions if v["status"] == "approved"), None) or \
                (versions[0] if versions else None)
        steps, t, imp, dips, batches = [], totals([]), impact(totals([])), [], []
        if sel:
            steps = _steps_of(conn, sel["id"])
            t = totals(steps)
            imp = impact(t)
            dips = [dict(x) for x in conn.execute(
                "SELECT * FROM wsh_labdips WHERE version_id=? ORDER BY id DESC",
                (sel["id"],)).fetchall()]
            batches = [dict(x) for x in conn.execute(
                "SELECT * FROM wsh_batches WHERE version_id=? ORDER BY id DESC LIMIT 20",
                (sel["id"],)).fetchall()]
        return {"recipe": dict(r), "versions": versions, "sel": sel, "steps": steps,
                "tot": t, "imp": imp, "dips": dips, "batches": batches}
    finally:
        conn.close()


def list_versions():
    """Selectable versions for the batch / lab-dip forms: 'WR-STN-01 v2 (approved)'."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT v.id, v.version, v.status, r.code, r.name, r.id AS recipe_id "
            "FROM wsh_versions v JOIN wsh_recipes r ON r.id=v.recipe_id "
            "ORDER BY r.code ASC, v.version DESC").fetchall()]
    finally:
        conn.close()


def list_batches(limit=200):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT b.*, r.code, r.name, v.version FROM wsh_batches b "
            "LEFT JOIN wsh_versions v ON v.id=b.version_id "
            "LEFT JOIN wsh_recipes r ON r.id=b.recipe_id "
            "ORDER BY b.id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


def list_labdips(verdict=None, limit=200):
    conn = get_db()
    try:
        q = ("SELECT d.*, r.code, r.name, v.version, v.recipe_id, b.batch_no FROM wsh_labdips d "
             "LEFT JOIN wsh_versions v ON v.id=d.version_id "
             "LEFT JOIN wsh_recipes r ON r.id=v.recipe_id "
             "LEFT JOIN wsh_batches b ON b.id=d.batch_id WHERE 1=1")
        args = []
        if verdict:
            q += " AND d.verdict=?"; args.append(verdict)
        q += (" ORDER BY CASE d.verdict WHEN 'pending' THEN 0 WHEN 'resubmit' THEN 1 "
              "ELSE 2 END, d.id DESC LIMIT ?")
        args.append(limit)
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


# --- writes ---------------------------------------------------------------
def create_recipe(data, user):
    """Create the family and its draft v1 in one go — a recipe with no version
    is unusable, so there is never a state where one exists without the other.
    Returns the new id, or None when the operator-typed code is already taken."""
    conn = get_db()
    try:
        code = (data.get("code") or "").strip() or None
        # code is UNIQUE and operator-typed: check it here or the INSERT raises an
        # IntegrityError straight out of the service and 500s the recipes page.
        if code and conn.execute("SELECT id FROM wsh_recipes WHERE code=?", (code,)).fetchone():
            return None
        cur = conn.execute(
            "INSERT INTO wsh_recipes (code,name,style_ref,wash_type,order_id,notes,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (code, (data.get("name") or "Recipe").strip(), data.get("style_ref") or None,
             _pick(data.get("wash_type"), WASH_TYPES, "rinse"), _i(data.get("order_id")),
             data.get("notes") or None, (user or {}).get("username"), _now()))
        rid = cur.lastrowid
        if not code:
            # Numbered from the row id, but a human may already have typed that exact
            # string: suffix until free (bounded — each pass tries a new string).
            auto = "WR-%04d" % rid
            n = 1
            while conn.execute("SELECT id FROM wsh_recipes WHERE code=? AND id<>?",
                               (auto, rid)).fetchone():
                auto = "WR-%04d-%d" % (rid, n)
                n += 1
            conn.execute("UPDATE wsh_recipes SET code=? WHERE id=?", (auto, rid))
        conn.execute("INSERT INTO wsh_versions (recipe_id,version,status,created_by,created_at) "
                     "VALUES (?,?,?,?,?)", (rid, 1, "draft", (user or {}).get("username"), _now()))
        conn.commit()
        return rid
    finally:
        conn.close()


def new_version(recipe_id, source_version_id, user):
    """SNAPSHOT the source version into a fresh draft version.

    Every step and chemical line is COPIED, never shared: editing the new version
    can therefore not alter a version that bulk already ran against. That is the
    whole repeatability guarantee of this module.
    Returns (new_version_id, None) or (None, reason).
    """
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM wsh_recipes WHERE id=?", (recipe_id,)).fetchone():
            return None, "recipe_not_found"
        # Resolve the source BEFORE inserting anything, and REQUIRE one. Every
        # recipe is born with a v1, so there is no legitimate "from nothing" call:
        # a blank or unparseable source is a broken form post, and letting it
        # through silently appended empty draft versions that can never be deleted.
        src_id = _i(source_version_id)
        src = conn.execute("SELECT id FROM wsh_versions WHERE id=? AND recipe_id=?",
                           (src_id, recipe_id)).fetchone() if src_id is not None else None
        if not src:
            return None, "source_not_found"
        mx = conn.execute("SELECT MAX(version) AS c FROM wsh_versions WHERE recipe_id=?",
                          (recipe_id,)).fetchone()["c"] or 0
        cur = conn.execute(
            "INSERT INTO wsh_versions (recipe_id,version,status,created_by,created_at) "
            "VALUES (?,?,?,?,?)", (recipe_id, mx + 1, "draft", (user or {}).get("username"), _now()))
        nid = cur.lastrowid
        for s in conn.execute("SELECT * FROM wsh_steps WHERE version_id=? ORDER BY step_no, id",
                              (src["id"],)).fetchall():
            scur = conn.execute(
                "INSERT INTO wsh_steps (version_id,step_no,operation,temp_c,minutes,"
                "liquor_ratio,load_kg,notes) VALUES (?,?,?,?,?,?,?,?)",
                (nid, s["step_no"], s["operation"], s["temp_c"], s["minutes"],
                 s["liquor_ratio"], s["load_kg"], s["notes"]))
            for c in conn.execute("SELECT * FROM wsh_chemicals WHERE step_id=? ORDER BY id",
                                  (s["id"],)).fetchall():
                conn.execute("INSERT INTO wsh_chemicals (step_id,name,gpl,owg_pct) "
                             "VALUES (?,?,?,?)",
                             (scur.lastrowid, c["name"], c["gpl"], c["owg_pct"]))
        conn.commit()
        return nid, None
    finally:
        conn.close()


def add_step(version_id, data, user):
    """Append a step to a DRAFT version. Approved/retired versions are frozen —
    that is what makes a recorded batch's version meaningful."""
    conn = get_db()
    try:
        v = conn.execute("SELECT * FROM wsh_versions WHERE id=?", (version_id,)).fetchone()
        if not v:
            return False, "version_not_found"
        if v["status"] != "draft":
            return False, "version_frozen"
        op = (data.get("operation") or "").strip()
        if not op:
            return False, "operation_required"
        # Refused rather than defaulted: the operation IS the step. A silent
        # fallback would put a wrong process on the shop-floor sheet.
        if op not in OPERATIONS:
            return False, "bad_operation"
        # Physical quantities are non-negative. A '-8' liquor ratio would otherwise
        # subtract from the water and chemical totals and make the recipe look green.
        bad = _bad_qty(data, ("temp_c", "minutes", "liquor_ratio", "load_kg"))
        if bad:
            return False, bad
        nxt = (conn.execute("SELECT MAX(step_no) AS c FROM wsh_steps WHERE version_id=?",
                            (version_id,)).fetchone()["c"] or 0) + 1
        conn.execute(
            "INSERT INTO wsh_steps (version_id,step_no,operation,temp_c,minutes,liquor_ratio,"
            "load_kg,notes) VALUES (?,?,?,?,?,?,?,?)",
            (version_id, nxt, op, _f(data.get("temp_c")),
             _f(data.get("minutes")), _f(data.get("liquor_ratio")), _f(data.get("load_kg")),
             data.get("notes") or None))
        conn.commit()
        return True, None
    finally:
        conn.close()


def add_chemical(step_id, data, user):
    conn = get_db()
    try:
        s = conn.execute("SELECT s.id, v.status FROM wsh_steps s "
                         "JOIN wsh_versions v ON v.id=s.version_id WHERE s.id=?",
                         (_i(step_id, 0),)).fetchone()
        if not s:
            return False, "step_not_found"
        if s["status"] != "draft":
            return False, "version_frozen"
        if not (data.get("name") or "").strip():
            return False, "name_required"
        bad = _bad_qty(data, ("gpl", "owg_pct"))
        if bad:
            return False, bad
        conn.execute("INSERT INTO wsh_chemicals (step_id,name,gpl,owg_pct) VALUES (?,?,?,?)",
                     (s["id"], data.get("name").strip(), _f(data.get("gpl")),
                      _f(data.get("owg_pct"))))
        conn.commit()
        return True, None
    finally:
        conn.close()


def set_version_status(version_id, status, user):
    """BULK GATE: a version may only become 'approved' when it has steps AND at
    least one APPROVED lab dip of its own. Shade sign-off precedes bulk, always.
    Approving also retires any previously approved version of the same recipe —
    exactly one version is the current bulk standard at any time.
    """
    if status not in VERSION_STATUS:
        return False, "bad_status"
    conn = get_db()
    try:
        v = conn.execute("SELECT * FROM wsh_versions WHERE id=?", (version_id,)).fetchone()
        if not v:
            return False, "version_not_found"
        if v["status"] == status:
            return False, "already_" + status          # double-submit is a no-op, not a re-write
        if status == "draft" and v["status"] != "draft":
            # THE SNAPSHOT GUARANTEE. Only a draft is editable, so re-opening a
            # version that batches already recorded would silently rewrite their
            # recipe of record. A version never goes back to draft — take a new one.
            return False, "cannot_reopen"
        if status == "approved":
            if not conn.execute("SELECT COUNT(*) AS c FROM wsh_steps WHERE version_id=?",
                                (version_id,)).fetchone()["c"]:
                return False, "no_steps"
            if not conn.execute("SELECT COUNT(*) AS c FROM wsh_labdips WHERE version_id=? "
                                "AND verdict='approved'", (version_id,)).fetchone()["c"]:
                return False, "labdip_not_approved"
            conn.execute("UPDATE wsh_versions SET status='retired' WHERE recipe_id=? AND id!=? "
                         "AND status='approved'", (v["recipe_id"], version_id))
            conn.execute("UPDATE wsh_versions SET status=?, approved_by=?, approved_at=? WHERE id=?",
                         (status, (user or {}).get("username"), _now(), version_id))
        else:
            conn.execute("UPDATE wsh_versions SET status=? WHERE id=?", (status, version_id))
        conn.commit()
        return True, None
    finally:
        conn.close()


def _deviation(t, load_kg, act_minutes, act_temp_c):
    """Actuals vs the version's nominals. A nominal of 0 means there is no
    baseline to judge against, so that check is skipped — never a /0."""
    out = []
    if t["cycle_min"] > 0 and act_minutes > 0:
        pct = abs(act_minutes - t["cycle_min"]) / t["cycle_min"] * 100.0
        if pct > DEV_TIME_PCT:
            out.append("time %g vs %g min (%.0f%%)" % (act_minutes, t["cycle_min"], pct))
    if t["max_temp_c"] > 0 and act_temp_c > 0:
        d = abs(act_temp_c - t["max_temp_c"])
        if d > DEV_TEMP_C:
            out.append("temp %g vs %g C (%.0f K)" % (act_temp_c, t["max_temp_c"], d))
    if t["load_kg"] > 0 and load_kg > 0:
        pct = abs(load_kg - t["load_kg"]) / t["load_kg"] * 100.0
        if pct > DEV_LOAD_PCT:
            out.append("load %g vs %g kg (%.0f%%)" % (load_kg, t["load_kg"], pct))
    return "; ".join(out)


def create_batch(data, user):
    """Record an executed lot AGAINST A VERSION. Storing version_id (not just the
    recipe) is what lets this shade be reproduced months later once the recipe has
    moved on. Rings the bell once when the actuals fall outside tolerance.
    Returns (batch_id, None) or (None, reason)."""
    conn = get_db()
    try:
        vid = _i(data.get("version_id"))
        v = conn.execute("SELECT * FROM wsh_versions WHERE id=?", (vid or 0,)).fetchone()
        if not v:
            return None, "version_not_found"
        bad = _bad_qty(data, ("load_kg", "act_minutes", "act_temp_c", "act_water_l"))
        if bad:
            return None, bad
        load = _f(data.get("load_kg"))
        act_min = _f(data.get("act_minutes"))
        act_temp = _f(data.get("act_temp_c"))
        t = totals(_steps_of(conn, v["id"]))
        dev = _deviation(t, load, act_min, act_temp)
        cur = conn.execute(
            "INSERT INTO wsh_batches (recipe_id,version_id,order_id,machine,load_kg,operator,"
            "started_at,ended_at,act_minutes,act_temp_c,act_water_l,shade,status,deviation,"
            "deviation_alerted,notes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v["recipe_id"], v["id"], _i(data.get("order_id")), data.get("machine") or None,
             load, data.get("operator") or None, data.get("started_at") or None,
             data.get("ended_at") or None, act_min, act_temp, _f(data.get("act_water_l")),
             data.get("shade") or None, _pick(data.get("status"), BATCH_STATUS, "done"), dev or None,
             1 if dev else 0, data.get("notes") or None, (user or {}).get("username"), _now()))
        bid = cur.lastrowid
        # Number from the row id — collision-free, unlike COUNT(*)+1 after a delete.
        bno = "WB-%d-%05d" % (date.today().year, bid)
        conn.execute("UPDATE wsh_batches SET batch_no=? WHERE id=?", (bno, bid))
        if dev:
            rc = conn.execute("SELECT code FROM wsh_recipes WHERE id=?", (v["recipe_id"],)).fetchone()
            _bell(conn, "warning", "Wash batch off recipe: %s" % bno,
                  "%s v%s — %s" % ((rc["code"] if rc else "recipe"), v["version"], dev),
                  "/wash/batches")
        conn.commit()
        return bid, None
    finally:
        conn.close()


def create_labdip(data, user):
    conn = get_db()
    try:
        vid = _i(data.get("version_id"))
        if not conn.execute("SELECT id FROM wsh_versions WHERE id=?", (vid or 0,)).fetchone():
            return None, "version_not_found"
        bid = _i(data.get("batch_id"))
        if bid is not None:
            b = conn.execute("SELECT version_id FROM wsh_batches WHERE id=?", (bid,)).fetchone()
            if not b:
                return None, "batch_not_found"
            # A lab dip is physical evidence cut FROM a lot, and the bulk gate
            # counts approved dips. A lot that ran a different version is evidence
            # for THAT version — accepting it here would let one version's shade
            # sign-off unlock another version for bulk.
            if b["version_id"] != vid:
                return None, "batch_version_mismatch"
        cur = conn.execute(
            "INSERT INTO wsh_labdips (version_id,batch_id,reference,verdict,notes,created_by,"
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (vid, bid, (data.get("reference") or "").strip() or None,
             "pending", data.get("notes") or None, (user or {}).get("username"), _now()))
        conn.commit()
        return cur.lastrowid, None
    finally:
        conn.close()


def set_labdip_verdict(dip_id, verdict, notes, user):
    """Record the shade verdict. A rejection rings the bell — someone has to
    re-lab-dip before bulk can be approved."""
    if verdict not in LABDIP_VERDICT:
        return False, "bad_verdict"
    conn = get_db()
    try:
        d = conn.execute("SELECT * FROM wsh_labdips WHERE id=?", (dip_id,)).fetchone()
        if not d:
            return False, "labdip_not_found"
        if d["verdict"] == verdict:
            # A refresh-repost must not restamp the sign-off with a different
            # approver's name — the shade decision is an accountability record.
            return False, "already_" + verdict
        conn.execute("UPDATE wsh_labdips SET verdict=?, approver=?, verdict_date=?, "
                     "notes=COALESCE(?,notes) WHERE id=?",
                     (verdict, (user or {}).get("username"), str(date.today()),
                      (notes or None), dip_id))
        if verdict in ("rejected", "resubmit"):     # the verdict changed — see the guard above
            v = conn.execute("SELECT v.version, v.status, r.code FROM wsh_versions v "
                             "JOIN wsh_recipes r ON r.id=v.recipe_id WHERE v.id=?",
                             (d["version_id"],)).fetchone()
            # INVARIANT: an approved version has at least one approved lab dip — that
            # is exactly what the bulk gate enforces. Revoking the LAST approved dip
            # of a version that is already the live bulk standard breaks it after the
            # fact: the version stays approved (batches reference it, and retiring it
            # behind the operator's back is not this function's call), so bulk keeps
            # running on a shade the lab just rejected. That is a critical alert, not
            # a warning, and the message must not claim bulk is merely "blocked".
            left = conn.execute("SELECT COUNT(*) AS c FROM wsh_labdips WHERE version_id=? "
                                "AND verdict='approved'", (d["version_id"],)).fetchone()["c"]
            live = v and v["status"] == "approved" and left == 0
            if live:
                _bell(conn, "critical",
                      "Bulk shade revoked: %s" % (d["reference"] or "lab dip"),
                      "%s v%s is STILL the approved bulk standard but no longer has any "
                      "approved lab dip (%s). Stop or re-dip this shade."
                      % (v["code"], v["version"], verdict), "/wash/labdips")
            else:
                _bell(conn, "warning", "Lab dip %s: %s" % (verdict, d["reference"] or "shade"),
                      "%s v%s shade %s — the version cannot go to bulk until a dip is approved."
                      % ((v["code"] if v else "recipe"), (v["version"] if v else "?"), verdict),
                      "/wash/labdips")
        conn.commit()
        return True, None
    finally:
        conn.close()


# --- dashboard ------------------------------------------------------------
def dashboard():
    conn = get_db()
    try:
        def one(sql, args=()):
            r = conn.execute(sql, args).fetchone()
            return (r["c"] if r and r["c"] is not None else 0)
        today = str(date.today())
        d = {
            "recipes": one("SELECT COUNT(*) AS c FROM wsh_recipes"),
            "approved": one("SELECT COUNT(DISTINCT recipe_id) AS c FROM wsh_versions "
                            "WHERE status='approved'"),
            "versions": one("SELECT COUNT(*) AS c FROM wsh_versions"),
            "batches_today": one("SELECT COUNT(*) AS c FROM wsh_batches WHERE "
                                 "COALESCE(started_at, created_at) LIKE ?", (today + "%",)),
            "batches": one("SELECT COUNT(*) AS c FROM wsh_batches"),
            "dips_pending": one("SELECT COUNT(*) AS c FROM wsh_labdips WHERE verdict IN "
                                "('pending','resubmit')"),
            "dips_rejected": one("SELECT COUNT(*) AS c FROM wsh_labdips WHERE verdict='rejected'"),
            "deviations": one("SELECT COUNT(*) AS c FROM wsh_batches WHERE deviation IS NOT NULL"),
        }
        # Average cycle time over APPROVED versions only — the standards actually in use.
        # The CASE mirrors _nn(): totals() floors a negative or NULL minutes to 0, so a
        # bare SUM(s.minutes) here would print a KPI that contradicts every recipe sheet
        # (one -1000 row turned a 100 min average into -400).
        avg = conn.execute(
            "SELECT AVG(m) AS c FROM (SELECT SUM(CASE WHEN s.minutes > 0 THEN s.minutes "
            "ELSE 0 END) AS m FROM wsh_steps s "
            "JOIN wsh_versions v ON v.id=s.version_id WHERE v.status='approved' "
            "GROUP BY s.version_id) x").fetchone()
        d["avg_cycle_min"] = round(_f(avg["c"] if avg else 0), 0)
        d["recent"] = [dict(r) for r in conn.execute(
            "SELECT b.*, r.code, v.version FROM wsh_batches b "
            "LEFT JOIN wsh_versions v ON v.id=b.version_id "
            "LEFT JOIN wsh_recipes r ON r.id=b.recipe_id ORDER BY b.id DESC LIMIT 8").fetchall()]
        d["pending_dips"] = [dict(r) for r in conn.execute(
            "SELECT d.*, r.code, v.version, v.recipe_id FROM wsh_labdips d "
            "LEFT JOIN wsh_versions v ON v.id=d.version_id "
            "LEFT JOIN wsh_recipes r ON r.id=v.recipe_id "
            "WHERE d.verdict IN ('pending','resubmit','rejected') ORDER BY d.id DESC LIMIT 8"
        ).fetchall()]
        return d
    finally:
        conn.close()


# --- exports --------------------------------------------------------------
def _c(v):
    """A cell is text or a number, never None. Blank must read as blank in Excel,
    and as "" in JSON where a null looks like a broken column."""
    return "" if v is None else v


def export_dataset(key):
    """key -> (headers, rows) for the shared CSV/JSON exporters, else (None, None).

    Keys: recipes | recipe-steps | batches | lab-dips | version-impact.
    Built on this module's own read functions and on totals()/impact() wherever they
    already return what the export needs — a downloaded file that disagrees with the
    page it came from is worse than no export at all.
    Figures keep the module's own rounding (2dp totals, 1dp doses) for the same reason.
    """
    if key == "recipes":
        return (["Code", "Name", "Wash Type", "Style Ref", "Versions", "Bulk Standard",
                 "Latest Version", "Latest Status", "Notes", "Created By", "Created At"],
                [[_c(r["code"]), _c(r["name"]), _c(r["wash_type"]), _c(r["style_ref"]),
                  r["versions"], _c(r["approved_version"]), r["latest"], r["latest_status"],
                  _c(r["notes"]), _c(r["created_by"]), _c(r["created_at"])]
                 for r in list_recipes()])

    if key == "batches":
        # 5000, not the page's 200: this is the traceability register, and a silently
        # truncated one answers the wrong question in an audit or a claim.
        return (["Batch No", "Recipe Code", "Recipe", "Version", "Machine", "Operator",
                 "Load kg", "Started At", "Ended At", "Actual Minutes", "Actual Temp C",
                 "Actual Water L", "Shade", "Status", "Deviation", "Recorded By"],
                [[_c(b["batch_no"]), _c(b["code"]), _c(b["name"]), _c(b["version"]),
                  _c(b["machine"]), _c(b["operator"]), _c(b["load_kg"]),
                  _c(b["started_at"]), _c(b["ended_at"]), _c(b["act_minutes"]),
                  _c(b["act_temp_c"]), _c(b["act_water_l"]), _c(b["shade"]),
                  _c(b["status"]), _c(b["deviation"]), _c(b["created_by"])]
                 for b in list_batches(5000)])

    if key == "lab-dips":
        return (["Reference", "Recipe Code", "Recipe", "Version", "Batch No", "Verdict",
                 "Approver", "Verdict Date", "Notes", "Logged By", "Logged At"],
                [[_c(d["reference"]), _c(d["code"]), _c(d["name"]), _c(d["version"]),
                  _c(d["batch_no"]), _c(d["verdict"]), _c(d["approver"]),
                  _c(d["verdict_date"]), _c(d["notes"]), _c(d["created_by"]),
                  _c(d["created_at"])]
                 for d in list_labdips(None, 5000)])

    if key not in ("recipe-steps", "version-impact"):
        return None, None

    conn = get_db()
    try:
        if key == "recipe-steps":
            # One flat row per dosing line — that is the sheet the floor reads. LEFT
            # JOIN so a dry step with no chemicals still appears in its cycle order.
            # Flat SQL rather than _steps_of() per version: this walks the whole
            # library, and the per-version reader would be a query per step.
            rows = conn.execute(
                "SELECT r.code, r.name, v.version, v.status, s.step_no, s.operation, "
                "s.temp_c, s.minutes, s.liquor_ratio, s.load_kg, s.notes, "
                "c.name AS chem, c.gpl, c.owg_pct FROM wsh_steps s "
                "JOIN wsh_versions v ON v.id=s.version_id "
                "JOIN wsh_recipes r ON r.id=v.recipe_id "
                "LEFT JOIN wsh_chemicals c ON c.step_id=s.id "
                "ORDER BY r.code, v.version, s.step_no, s.id, c.id LIMIT 20000").fetchall()
            out = []
            for s in rows:
                # _nn and the same rounding as _steps_of(): the export has to add up to
                # the printed sheet, not to an unfloored re-derivation of it.
                bath = round(_nn(s["load_kg"]) * _nn(s["liquor_ratio"]), 2)
                dose = round(_nn(s["gpl"]) * bath
                             + _nn(s["owg_pct"]) / 100.0 * _nn(s["load_kg"]) * 1000.0,
                             1) if s["chem"] else ""
                out.append([_c(s["code"]), _c(s["name"]), _c(s["version"]), _c(s["status"]),
                            _c(s["step_no"]), _c(s["operation"]), _c(s["temp_c"]),
                            _c(s["minutes"]), _c(s["liquor_ratio"]), _c(s["load_kg"]),
                            bath, _c(s["chem"]), _c(s["gpl"]), _c(s["owg_pct"]), dose,
                            _c(s["notes"])])
            return (["Code", "Recipe", "Version", "Version Status", "Step No", "Operation",
                     "Temp C", "Minutes", "Liquor Ratio", "Load kg", "Bath L", "Chemical",
                     "g/L", "% OWG", "Dose g", "Step Notes"], out)

        # version-impact: the per-version cycle + intensity roll-up the recipe sheet already
        # shows, one row per version so the library can be ranked in a spreadsheet.
        vers = conn.execute(
            "SELECT v.id, v.version, v.status, v.approved_by, v.approved_at, r.code, r.name "
            "FROM wsh_versions v JOIN wsh_recipes r ON r.id=v.recipe_id "
            "ORDER BY r.code, v.version LIMIT 2000").fetchall()
        nb = {r["version_id"]: r["c"] for r in conn.execute(
            "SELECT version_id, COUNT(*) AS c FROM wsh_batches GROUP BY version_id").fetchall()}
        nd = {r["version_id"]: r["c"] for r in conn.execute(
            "SELECT version_id, COUNT(*) AS c FROM wsh_labdips WHERE verdict='approved' "
            "GROUP BY version_id").fetchall()}
        out = []
        for v in vers:
            # ponytail: _steps_of() per version (a query per step for its chemicals) —
            # same shape list_recipes() already accepts, and totals()/impact() are the
            # only place this maths is allowed to live. Fold into two flat queries if
            # the library ever passes a few hundred versions.
            t = totals(_steps_of(conn, v["id"]))
            im = impact(t)
            out.append([_c(v["code"]), _c(v["name"]), v["version"], _c(v["status"]),
                        t["steps"], t["baths"], t["cycle_min"], t["water_l"], t["chem_g"],
                        t["heat_lk"], t["load_kg"], t["max_temp_c"],
                        im["water"], im["energy"], im["chem"], _c(im["score"]), im["band"],
                        nb.get(v["id"], 0), nd.get(v["id"], 0),
                        _c(v["approved_by"]), _c(v["approved_at"])])
        return (["Code", "Recipe", "Version", "Status", "Steps", "Baths", "Cycle Minutes",
                 "Water L", "Chemical g", "Heat LK", "Load kg", "Peak Bath Temp C",
                 "Water Index", "Energy Index", "Chemical Index", "Impact Score",
                 "Impact Band", "Batches", "Approved Lab Dips", "Approved By",
                 "Approved At"], out)
    finally:
        conn.close()
