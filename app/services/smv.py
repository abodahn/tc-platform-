"""
The one place a standard minute value is resolved.

Eight columns across five modules store an SMV. They are not eight opinions of
the same number — they are three different KINDS of number, and only one kind
can be a source of truth:

  DEFINITION — the standard work content of a style/order. A SOURCE.
      sf_styles.smv        style master, keyed by style code        [smartfactory]
      sf_operations.smv    the style's operation bulletin           [smartfactory]
      pln_order_smv.smv    the planner's SMV for one order          [planning]
      cst_sheets.smv       the SMV the order was PRICED with        [costing]
      pln_ops.smv          the order's operation bulletin           [planning]

  OPERATIONAL RECORD — what one specific hour actually ran. NEVER a source.
      mes_hourly.smv       typed by the supervisor for that hour    [mes]

  HISTORICAL SNAPSHOT — deliberately frozen, never rewritten, NEVER a source.
      pln_allocations.smv  frozen when the plan was committed       [planning]
      ppl_piece_rate.smv   frozen so a payroll figure stays reproducible [people]

Canonical is therefore the DEFINITION group, most specific first:

  1 planning   pln_order_smv     order-level, set by whoever owns the schedule
  2 costing    cst_sheets        order-level, the number the order was quoted on
  3 style      sf_styles         the style master — the product definition itself
  4 bulletin   SUM(pln_ops)      the order's own operation bulletin (derived)
  5 style_ops  SUM(sf_operations) the style's operation bulletin (derived)

Order-level beats style-level because an order IS more specific than its style (a
shorter run, a simplified spec, a different buyer standard). The style master is
the defined DEFAULT for every order with no order-level figure — that defined
fallback is what "one source of truth" actually buys: today the fallback is
accidental (whichever module happens to have a row).

A stored SMV of 0 is NOT a reading of zero minutes — every consumer in this
codebase already treats smv<=0 as "not set", so a 0 row is a hole to fall
through, never an answer.

This module RESOLVES and COMPARES. It never writes, and it never rewrites a
snapshot: a committed plan and a paid incentive keep the number they were made
with, and record which source that number came from (see `source_of`).
"""
import math

# Two stored SMVs for the same order that differ by more than this are a real
# disagreement, not rounding: half a standard minute is ~4% of a basic tee, and
# it is minutes of capacity per garment across a whole order.
SMV_TOLERANCE = 0.5

# (source, i18n key, English label) in precedence order. The first with a value
# above zero wins; the rest are still returned so a disagreement can be SHOWN.
ORDER_SOURCES = (
    ("planning", "smv.src.planning", "Planning"),
    ("costing", "smv.src.costing", "Costing"),
    ("style", "smv.src.style", "Style master"),
    ("bulletin", "smv.src.bulletin", "Operation bulletin"),
    ("style_ops", "smv.src.style_ops", "Style bulletin"),
)

# One OPERATION's minutes (people's piece rate is per operation, not per garment).
OPERATION_SOURCES = (
    ("bulletin_op", "smv.src.bulletin_op", "Operation bulletin"),
    ("style_op", "smv.src.style_op", "Style operation"),
)

_LABELS = {s: (k, lbl) for s, k, lbl in ORDER_SOURCES + OPERATION_SOURCES}


def _f(v):
    """A usable SMV, or None. Blank/garbage/negative/zero/inf are all 'not set'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _val(conn, sql, args, col="smv"):
    """One scalar SMV, or None. A module whose tables are not created yet must not
    break the resolver, and on PostgreSQL a failed statement aborts the whole
    transaction — so the rollback is mandatory, not tidiness."""
    try:
        row = conn.execute(sql, args).fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    return _f(row[col]) if row else None


def _style_ref(conn, order_id):
    try:
        row = conn.execute("SELECT style_ref FROM ord_orders WHERE id=?", (order_id,)).fetchone()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    return (row["style_ref"] or None) if row else None


def sources_for(conn, *, style_ref=None, order_id=None, operation_id=None, operation=None):
    """Every DEFINITION-level SMV that exists for this style/order/operation, in
    precedence order: [{"source","key","label","smv"}, ...]. Never raises."""
    if operation_id or operation:
        return _operation_sources(conn, order_id, operation_id, operation)

    found = {}
    if order_id:
        found["planning"] = _val(conn, "SELECT smv FROM pln_order_smv WHERE order_id=?", (order_id,))
        found["costing"] = _val(conn, "SELECT smv FROM cst_sheets WHERE order_id=?", (order_id,))
        found["bulletin"] = _val(conn, "SELECT SUM(smv) AS smv FROM pln_ops WHERE order_id=?",
                                 (order_id,))
        style_ref = style_ref or _style_ref(conn, order_id)
    if style_ref:
        found["style"] = _val(conn, "SELECT smv FROM sf_styles WHERE code=?", (style_ref,))
        found["style_ops"] = _val(
            conn, "SELECT SUM(o.smv) AS smv FROM sf_operations o JOIN sf_styles s "
                  "ON s.id=o.style_id WHERE s.code=?", (style_ref,))
    return [{"source": s, "key": k, "label": lbl, "smv": round(found[s], 4)}
            for s, k, lbl in ORDER_SOURCES if found.get(s) is not None]


def _operation_sources(conn, order_id, operation_id, operation):
    found = {}
    if operation_id:
        found["bulletin_op"] = _val(conn, "SELECT smv FROM pln_ops WHERE id=?", (operation_id,))
    elif operation and order_id:
        found["bulletin_op"] = _val(
            conn, "SELECT smv FROM pln_ops WHERE order_id=? AND LOWER(name)=LOWER(?) "
                  "ORDER BY id LIMIT 1", (order_id, str(operation)))
    if operation:
        found["style_op"] = _val(
            conn, "SELECT o.smv FROM sf_operations o WHERE LOWER(o.name)=LOWER(?) "
                  "ORDER BY o.id LIMIT 1", (str(operation),))
    return [{"source": s, "key": k, "label": lbl, "smv": round(found[s], 4)}
            for s, k, lbl in OPERATION_SOURCES if found.get(s) is not None]


def sources_for_orders(conn, order_ids):
    """Bulk `sources_for` — {order_id: [sources]}, same precedence and the same
    values, in six queries instead of five PER ORDER (a dashboard with 300 open
    orders would otherwise be 1500 round trips)."""
    ids = [int(i) for i in (order_ids or []) if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))

    def rows(sql, args=()):
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return []

    style_of = {r["id"]: r.get("style_ref") for r in
                rows(f"SELECT id, style_ref FROM ord_orders WHERE id IN ({marks})", ids)}
    per_order = {
        "planning": {r["order_id"]: r["smv"] for r in rows(
            f"SELECT order_id, smv FROM pln_order_smv WHERE order_id IN ({marks})", ids)},
        "costing": {r["order_id"]: r["smv"] for r in rows(
            f"SELECT order_id, smv FROM cst_sheets WHERE order_id IN ({marks})", ids)},
        "bulletin": {r["order_id"]: r["smv"] for r in rows(
            f"SELECT order_id, SUM(smv) AS smv FROM pln_ops WHERE order_id IN ({marks}) "
            "GROUP BY order_id", ids)},
    }
    # The style master is a short table — read it whole rather than per order.
    per_style = {
        "style": {r["code"]: r["smv"] for r in rows("SELECT code, smv FROM sf_styles")},
        "style_ops": {r["code"]: r["smv"] for r in rows(
            "SELECT s.code AS code, SUM(o.smv) AS smv FROM sf_operations o "
            "JOIN sf_styles s ON s.id=o.style_id GROUP BY s.code")},
    }
    out = {}
    for oid in ids:
        got = []
        for s, k, lbl in ORDER_SOURCES:
            raw = (per_order[s].get(oid) if s in per_order
                   else per_style[s].get(style_of.get(oid)))
            v = _f(raw)
            if v is not None:
                got.append({"source": s, "key": k, "label": lbl, "smv": round(v, 4)})
        out[oid] = got
    return out


def compare(sources, tolerance=SMV_TOLERANCE):
    """The disagreement verdict over a list of sources — the ONE definition of
    'these two stored SMVs disagree', shared by the single and bulk paths."""
    out = {"conflict": False, "spread": 0.0, "high": None, "low": None}
    if len(sources or []) < 2:
        return out
    hi = max(sources, key=lambda s: s["smv"])
    lo = min(sources, key=lambda s: s["smv"])
    spread = round(hi["smv"] - lo["smv"], 4)
    # Surfaced, never auto-resolved: which of two disagreeing SMVs is right is a
    # decision a planner makes, and silently picking one is the original bug.
    out.update({"spread": spread, "conflict": spread > tolerance, "high": hi, "low": lo})
    return out


def resolve(sources, tolerance=SMV_TOLERANCE):
    """A full smv_for()-shaped answer from an already-gathered source list."""
    out = {"smv": None, "source": None, "key": None, "label": None, "sources": sources or [],
           **compare(sources, tolerance)}
    if sources:
        top = sources[0]
        out.update({"smv": top["smv"], "source": top["source"], "key": top["key"],
                    "label": top["label"]})
    return out


def smv_for(conn, *, style_ref=None, order_id=None, operation_id=None, operation=None,
            tolerance=SMV_TOLERANCE):
    """The canonical SMV and WHERE it came from.

    Returns, always (never raises, never None):
        smv       float | None   — None means genuinely nobody has set one
        source    str   | None   — 'planning' / 'costing' / 'style' / ...
        key,label                — i18n key + English label of that source
        sources   list           — every source that has a value, precedence order
        conflict  bool           — two sources disagree by more than `tolerance`
        spread    float          — high - low across the sources (0 with < 2)
        high/low  dict | None    — the two ends of the disagreement, for display
    """
    return resolve(sources_for(conn, style_ref=style_ref, order_id=order_id,
                               operation_id=operation_id, operation=operation), tolerance)


def source_of(value, sources, tolerance=SMV_TOLERANCE):
    """Which source a SNAPSHOT's stored number came from — 'manual' when it matches
    none of them. Used to label a frozen value (a committed allocation, a paid
    incentive) without touching the value itself."""
    v = _f(value)
    if v is None:
        return None
    for s in sources or []:
        if abs(s["smv"] - v) <= tolerance:
            return s["source"]
    return "manual"


def label_for(source):
    """(i18n key, English label) for a source name — 'manual' included."""
    if source in _LABELS:
        return _LABELS[source]
    return ("smv.src.manual", "Typed in")


# Every `smv.*` key this module makes a template emit: key -> (en, ar, tr).
# It lives HERE, not in one module's map, because planning, costing and the MES
# line page all render the same disagreement tag — and app.js t() renders the KEY
# ITSELF when a translation is missing, so a gap ships "smv.differs" to the screen
# in every language. Each consuming module merges this into its own i18n map, and
# the orchestrator splices those into app/static/i18n/{en,ar,tr}.json.
I18N = {
    "smv.source": ("Source", "المصدر", "Kaynak"),
    "smv.conflict": (
        "These stored SMVs disagree — the plan, the price and the floor are using "
        "different numbers. Decide which is right.",
        "قيم الدقيقة المعيارية المخزّنة غير متطابقة — الخطة والتسعير والإنتاج تستخدم "
        "أرقامًا مختلفة. حدد الرقم الصحيح.",
        "Kayıtlı SMV değerleri birbirini tutmuyor — plan, fiyat ve saha farklı sayılar "
        "kullanıyor. Hangisinin doğru olduğuna karar verin."),
    "smv.differs": ("SMV differs", "الدقيقة المعيارية مختلفة", "SMV farklı"),
    "smv.suggest": ("No planning SMV set — the field above is pre-filled from",
                    "لا توجد دقيقة معيارية في التخطيط — تم ملء الحقل أعلاه من",
                    "Planlamada SMV yok — yukarıdaki alan şu kaynaktan dolduruldu:"),
    "smv.suggest_save": ("Save it to plan with it.", "احفظها للتخطيط بها.",
                         "Bununla planlamak için kaydedin."),
    "smv.src.planning": ("Planning", "التخطيط", "Planlama"),
    "smv.src.costing": ("Costing", "التكلفة", "Maliyetleme"),
    "smv.src.style": ("Style master", "بيانات الموديل", "Model ana kaydı"),
    "smv.src.bulletin": ("Operation bulletin", "بطاقة العمليات", "Operasyon bülteni"),
    "smv.src.style_ops": ("Style bulletin", "بطاقة عمليات الموديل", "Model bülteni"),
    "smv.src.bulletin_op": ("Operation bulletin", "بطاقة العمليات", "Operasyon bülteni"),
    "smv.src.style_op": ("Style operation", "عملية الموديل", "Model operasyonu"),
    "smv.src.manual": ("Typed in", "إدخال يدوي", "Elle girildi"),
}
