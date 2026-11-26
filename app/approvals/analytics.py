# -*- coding: utf-8 -*-
"""
Procurement Analytics workspace — filters, KPIs, charts and the report builder.

ONE DEFINITION OF SPEND. "Spend" everywhere in this module (and in the CSV
exports services.export_dataset serves) is the COMMITTED value of a request:

    (total - budget_released) x (1 + tax_rate)      -> gross, VAT INCLUDED
    x fx_rate when the request is not in EGP        -> EGP
    only for approved-or-later requests (COMMITTED, the set budget_status uses)

which is exactly services.egp_commitment() on the released-reduced total, the
figure the budget check, the DOAM routing and reports.py "Committed (EGP)" use.
Split by supplier, a request's value is shared across its (non-cancelled)
purchase orders in proportion to each order's grand total — the same rule the
old services._committed_rows() applied — so a split requisition's spend lands
on the suppliers that actually received the orders. `share_rows()` is the only
place that rule lives.

SAFE SQL. Every dimension, measure and aggregation a user can pick is a key
looked up in the whitelists below; nothing a user types is ever concatenated
into SQL — filter VALUES are always bound parameters. Every figure is computed
with GROUP BY over three fact sets (CTEs), never one query per row:

    S  one row per (request, supplier share): value, cycle, quotes, delivery
    A  S spread over the request's lines (category / item), value allocated by
       line weight inside that supplier's share, so S and A always add up to
       the same total
    L  one row per request line: quantity and unit price (EGP, ex-VAT)

Works on SQLite and PostgreSQL: the only dialect-specific piece is the day
difference, which _days() writes for each.
"""
import json
import math
import re
import threading
from contextlib import contextmanager
from datetime import date

from app.db import get_db, _is_pg

# The same set as services._SPEND_STATUSES / reports._COMMITTED /
# budget_status (tests_analytics asserts they stay identical).
COMMITTED = ("approved", "po_issued", "in_transit", "partially_received", "received", "closed")
OPEN = ("approved", "po_issued", "in_transit", "partially_received")
DELIVERY_OPEN = ("po_issued", "in_transit", "partially_received")
STATUSES = ("draft", "pending", "approved", "rejected", "po_issued", "in_transit",
            "partially_received", "received", "closed", "cancelled")
UNASSIGNED = "(unassigned)"


def _lit(values):
    """Module constants only (never user input) as an SQL IN-list."""
    return ",".join("'%s'" % v for v in values)


_COMMITTED_SQL, _OPEN_SQL, _DELIV_SQL = _lit(COMMITTED), _lit(OPEN), _lit(DELIVERY_OPEN)

# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------
PERIODS = ("m1", "m3", "m6", "m12", "ytd", "all", "custom")
DEFAULT_PERIOD = "m12"
FILTER_KEYS = ("dept", "vendor", "cat", "cc", "status", "cur")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CUR = re.compile(r"^[A-Z]{3}$")


def _clean(v, n=120):
    return str(v or "").strip()[:n]


def parse_filters(args):
    """The global filter bar from a query-string mapping, cleaned. Unknown
    values fall back to "no filter", never to an error."""
    g = (lambda k: args.get(k, "")) if hasattr(args, "get") else (lambda k: "")
    period = g("period") if g("period") in PERIODS else DEFAULT_PERIOD
    f = {"period": period,
         "from": g("from") if _ISO.match(g("from") or "") else "",
         "to": g("to") if _ISO.match(g("to") or "") else ""}
    for k in FILTER_KEYS:
        f[k] = _clean(g(k))
    if f["status"] not in STATUSES:
        f["status"] = ""
    f["cur"] = f["cur"].upper() if _CUR.match(f["cur"].upper()) else ""
    if period == "custom" and not (f["from"] or f["to"]):
        f["period"] = "all"
    if f["period"] != "custom":
        f["from"] = f["to"] = ""
    return f


def filter_args(f):
    """The non-default filters as query-string arguments (shareable URL)."""
    out = {k: f.get(k) for k in ("from", "to") + FILTER_KEYS if f.get(k)}
    if f.get("period", DEFAULT_PERIOD) != DEFAULT_PERIOD:
        out["period"] = f["period"]
    return out


def _month_start(d, back):
    m = d.month - back
    y, m = d.year + (m - 1) // 12, (m - 1) % 12 + 1
    return date(y, m, 1)


def date_range(f, today=None):
    """(from, to) ISO strings for the filter's period; '' = open-ended."""
    t = today or date.today()
    p = f.get("period", DEFAULT_PERIOD)
    if p == "custom":
        return f.get("from") or "", f.get("to") or ""
    if p == "all":
        return "", ""
    if p == "ytd":
        return date(t.year, 1, 1).isoformat(), t.isoformat()
    back = {"m1": 0, "m3": 2, "m6": 5, "m12": 11}[p]
    return _month_start(t, back).isoformat(), t.isoformat()


# --------------------------------------------------------------------------
# SQL fragments (module-authored; user input never reaches these strings)
# --------------------------------------------------------------------------
# A request_date that is not ISO (typed by hand) falls back to created_at, so the
# month / quarter CASTs below never see '5/11/2026' (PostgreSQL would refuse it).
_DATE = ("(CASE WHEN SUBSTR(COALESCE(p.request_date, ''), 1, 10) LIKE '____-__-__' "
         "THEN SUBSTR(p.request_date, 1, 10) ELSE SUBSTR(p.created_at, 1, 10) END)")
_FX = ("(CASE WHEN UPPER(TRIM(COALESCE(NULLIF(p.currency, ''), 'EGP'))) = 'EGP' THEN 1 "
       "WHEN COALESCE(p.fx_rate, 0) > 0 THEN p.fx_rate ELSE 1 END)")
# services.egp_commitment() on (total - budget_released): gross, EGP.
_VALUE = ("(CASE WHEN p.status IN (%s) THEN (COALESCE(p.total, 0) - COALESCE(p.budget_released, 0))"
          " * (1 + COALESCE(p.tax_rate, 0) / 100.0) * %s ELSE 0 END)" % (_COMMITTED_SQL, _FX))
_PR_COLS = (
    "p.id AS pr_id, "
    "COALESCE(TRIM(p.department), '') AS department, "
    "COALESCE(TRIM(p.cost_center), '') AS cost_centre, "
    "COALESCE(p.status, '') AS status, "
    "COALESCE(NULLIF(TRIM(p.requester_name), ''), p.requester, '') AS requester, "
    "UPPER(TRIM(COALESCE(NULLIF(p.currency, ''), 'EGP'))) AS currency, "
    "SUBSTR(%(d)s, 1, 7) AS month, "
    "SUBSTR(%(d)s, 1, 4) || '-Q' || CAST((CAST(SUBSTR(%(d)s, 6, 2) AS INTEGER) + 2) / 3 AS TEXT) AS quarter, "
    "LOWER(COALESCE(NULLIF(TRIM(p.expenditure_kind), ''), 'opex')) AS kind" % {"d": _DATE})

# Every non-cancelled purchase order is one share; a request with none is one
# share under its header supplier (services._committed_rows, in SQL). P is the
# CTE of the filtered request ids, pushed into every sub-select.
_SHARES = (
    "SELECT o.pr_id AS pr_id, COALESCE(NULLIF(TRIM(o.vendor), ''), '%(u)s') AS supplier, "
    "'po' || CAST(o.id AS TEXT) AS po_key, o.issued_at AS issued_at, "
    "CASE WHEN t.g > 0 THEN COALESCE(o.grand, 0) / t.g ELSE 1.0 / t.n END AS frac "
    "FROM pr_purchase_orders o JOIN (SELECT pr_id, SUM(COALESCE(grand, 0)) AS g, COUNT(*) AS n "
    "FROM pr_purchase_orders WHERE COALESCE(status, 'issued') <> 'cancelled' "
    "AND pr_id IN (SELECT id FROM P) GROUP BY pr_id) t "
    "ON t.pr_id = o.pr_id WHERE COALESCE(o.status, 'issued') <> 'cancelled' "
    "AND o.pr_id IN (SELECT id FROM P) "
    "UNION ALL "
    "SELECT r.id, COALESCE(NULLIF(TRIM(r.vendor), ''), '%(u)s'), "
    "CASE WHEN COALESCE(TRIM(r.po_no), '') <> '' THEN 'pr' || CAST(r.id AS TEXT) END, NULL, 1.0 "
    "FROM pr_requests r WHERE r.id IN (SELECT id FROM P) AND NOT EXISTS (SELECT 1 FROM pr_purchase_orders o2 "
    "WHERE o2.pr_id = r.id AND COALESCE(o2.status, 'issued') <> 'cancelled')" % {"u": UNASSIGNED})

# A line's supplier, the way spend sees it: the order's vendor when the request
# has exactly one live purchase order, else the line's own vendor, else the
# header's. Needs `LEFT JOIN _PO1 po1 ON po1.pr_id = <request id>`.
_PO1 = ("(SELECT pr_id, MIN(COALESCE(NULLIF(TRIM(vendor), ''), '%s')) AS v, COUNT(*) AS n "
        "FROM pr_purchase_orders WHERE COALESCE(status, 'issued') <> 'cancelled' GROUP BY pr_id)" % UNASSIGNED)


def _lsup(head):
    return ("(CASE WHEN po1.n = 1 THEN po1.v ELSE COALESCE(NULLIF(TRIM(i.vendor), ''), "
            "NULLIF(TRIM(%s.vendor), ''), '%s') END)" % (head, UNASSIGNED))


# A quotation's real cost (services.quote_after_vat), in EGP. A foreign quote
# with no usable rate (NULL / <= 0 / the unset 1) has no EGP cost: NULL, so it
# is never called the lowest (services._valid_fx).
_QA = ("COALESCE(q.amount, 0) * (CASE WHEN COALESCE(q.vat_applies, 0) = 1 "
       "THEN 1 + COALESCE(q.vat_rate, 0.14) ELSE 1 END) * "
       "(CASE WHEN UPPER(TRIM(COALESCE(NULLIF(q.currency, ''), 'EGP'))) = 'EGP' THEN 1 "
       "WHEN COALESCE(q.fx_rate, 0) > 0 AND q.fx_rate <> 1 THEN q.fx_rate ELSE NULL END)")
# Only quotes that price EVERY line of the request are compared: a partial
# quote (pr_quote_lines rows for some lines only) is not a competing offer for
# the whole request. A quote with no line rows is a total-only quote (full).
_QUOTES = (
    "QX AS (SELECT q.pr_id AS pr_id, q.id AS id, q.is_chosen AS is_chosen, %s AS qa "
    "FROM pr_quotes q LEFT JOIN (SELECT quote_id, COUNT(DISTINCT pr_item_id) AS nl FROM pr_quote_lines "
    "WHERE quote_id IN (SELECT id FROM pr_quotes WHERE pr_id IN (SELECT id FROM P)) GROUP BY quote_id) ql "
    "ON ql.quote_id = q.id "
    "WHERE COALESCE(q.amount, 0) > 0 AND q.pr_id IN (SELECT id FROM P) "
    "AND (ql.quote_id IS NULL OR ql.nl >= (SELECT COUNT(*) FROM pr_items li WHERE li.pr_id = q.pr_id))), "
    "QS AS (SELECT x.pr_id AS pr_id, COUNT(x.qa) AS n, MIN(x.qa) AS lo, MAX(x.qa) AS hi, "
    "MAX(CASE WHEN x.is_chosen = 1 THEN x.qa END) AS chosen, "
    "MAX(CASE WHEN x.id = fq.first_id THEN x.qa END) AS firstq "
    "FROM QX x JOIN (SELECT pr_id, MIN(id) AS first_id FROM QX GROUP BY pr_id) fq "
    "ON fq.pr_id = x.pr_id GROUP BY x.pr_id)" % _QA)
_QOK = "(p.status IN (%s) AND qs.n >= 2 AND qs.chosen IS NOT NULL)" % _COMMITTED_SQL

_CAT_EXISTS = ("EXISTS (SELECT 1 FROM pr_items ci LEFT JOIN proc_items cp ON cp.id = ci.item_id "
               "WHERE ci.pr_id = p.id AND COALESCE(TRIM(cp.category_name), '') = ?)")
_LINE_CAT = "COALESCE(TRIM(pi.category_name), '')"
_LINE_ITEM = "COALESCE(NULLIF(TRIM(pi.name), ''), NULLIF(TRIM(i.item), ''), '')"
_LINE_W = "(COALESCE(i.qty, 0) * COALESCE(NULLIF(i.net_price, 0), i.unit_price, 0))"


def _days(a, b):
    """Days from timestamp text `a` to `b` (NULL when either is missing)."""
    if _is_pg():
        return ("(EXTRACT(EPOCH FROM (CAST(NULLIF(SUBSTR(%s, 1, 19), '') AS timestamp) - "
                "CAST(NULLIF(SUBSTR(%s, 1, 19), '') AS timestamp))) / 86400.0)" % (b, a))
    return ("(julianday(NULLIF(SUBSTR(%s, 1, 19), '')) - julianday(NULLIF(SUBSTR(%s, 1, 19), '')))"
            % (b, a))


def _pr_where(f, rng, with_cat):
    w, a = ["COALESCE(p.is_active, 1) = 1"], []
    lo, hi = rng
    if lo:
        w.append(_DATE + " >= ?")
        a.append(lo)
    if hi:
        w.append(_DATE + " <= ?")
        a.append(hi)
    for key, col in (("dept", "COALESCE(TRIM(p.department), '')"),
                     ("cc", "COALESCE(TRIM(p.cost_center), '')"),
                     ("status", "p.status"),
                     ("cur", "UPPER(TRIM(COALESCE(NULLIF(p.currency, ''), 'EGP')))")):
        if f.get(key):
            w.append(col + " = ?")
            a.append(f[key])
    if with_cat and f.get("cat"):
        w.append(_CAT_EXISTS)
        a.append(f["cat"])
    return " AND ".join(w), a


# The P look-ups inside _SHARES / _QUOTES. With no filter beyond is_active, P
# is every request and the look-ups only cost time, so they are dropped.
_P_SCOPES = (" AND pr_id IN (SELECT id FROM P)", " AND o.pr_id IN (SELECT id FROM P)",
             "r.id IN (SELECT id FROM P) AND ", " AND q.pr_id IN (SELECT id FROM P)",
             "WHERE quote_id IN (SELECT id FROM pr_quotes WHERE pr_id IN (SELECT id FROM P)) ")


def _cte_s(f, rng, today):
    where, a = _pr_where(f, rng, with_cat=True)
    shares, quotes = _SHARES, _QUOTES
    if not a:
        for frag in _P_SCOPES:
            shares, quotes = shares.replace(frag, ""), quotes.replace(frag, "")
    sql = (
        "P AS (SELECT p.id AS id FROM pr_requests p WHERE %(where)s), %(quotes)s, "
        "S AS (SELECT %(cols)s, sh.supplier AS supplier, sh.po_key AS po_key, sh.frac AS frac, "
        "%(v)s * sh.frac AS v, "
        "CASE WHEN p.status IN (%(open)s) THEN %(v)s * sh.frac ELSE 0 END AS v_open, "
        "%(cyc)s AS cyc, "
        "CASE WHEN %(qok)s THEN qs.firstq * sh.frac END AS q_first, "
        "CASE WHEN %(qok)s THEN qs.hi * sh.frac END AS q_hi, "
        "CASE WHEN %(qok)s THEN qs.lo * sh.frac END AS q_lo, "
        "CASE WHEN %(qok)s THEN qs.chosen * sh.frac END AS q_chosen, "
        "%(lead)s AS lead_d, "
        "CASE WHEN COALESCE(p.received_at, '') <> '' AND COALESCE(p.req_del_date, '') <> '' THEN "
        "(CASE WHEN SUBSTR(p.received_at, 1, 10) <= SUBSTR(p.req_del_date, 1, 10) THEN 1.0 ELSE 0.0 END) "
        "END AS ontime, "
        "CASE WHEN p.status IN (%(deliv)s) AND COALESCE(p.req_del_date, '') <> '' "
        "AND SUBSTR(p.req_del_date, 1, 10) < ? THEN 1 ELSE 0 END AS overdue "
        "FROM pr_requests p JOIN (%(shares)s) sh ON sh.pr_id = p.id "
        "LEFT JOIN QS qs ON qs.pr_id = p.id WHERE p.id IN (SELECT id FROM P))"
    ) % {"cols": _PR_COLS, "v": _VALUE, "open": _OPEN_SQL, "deliv": _DELIV_SQL,
         "cyc": _days("p.submitted_at", "p.approved_at"), "qok": _QOK,
         "lead": _days("COALESCE(sh.issued_at, p.po_sent_at)", "p.received_at"),
         "shares": shares, "quotes": quotes, "where": where}
    return sql, a + [today]


def _cte_a():
    """S spread over its request's lines. A share whose supplier names lines of
    its own is spread over those lines; one that does not (a single order under
    the header supplier) over all of the request's lines; a request with no
    lines keeps its whole share in one row. Either way the rows of a share add
    up to exactly that share."""
    lsup = "COALESCE(NULLIF(TRIM(i.vendor), ''), NULLIF(TRIM(r.vendor), ''), '%s')" % UNASSIGNED
    return (
        "LN AS (SELECT i.pr_id AS pr_id, %(lsup)s AS lsup, %(w)s AS w, %(cat)s AS category, "
        "%(item)s AS item FROM pr_items i JOIN pr_requests r ON r.id = i.pr_id "
        "LEFT JOIN proc_items pi ON pi.id = i.item_id WHERE i.pr_id IN (SELECT pr_id FROM S)), "
        "LW AS (SELECT pr_id, lsup, SUM(w) AS ws, COUNT(*) AS ns FROM LN GROUP BY pr_id, lsup), "
        "LP AS (SELECT pr_id, SUM(w) AS wp, COUNT(*) AS np FROM LN GROUP BY pr_id), "
        "A AS (SELECT S.*, COALESCE(LN.category, '') AS category, COALESCE(LN.item, '') AS item, "
        "S.v * (CASE WHEN LN.pr_id IS NULL THEN 1.0 "
        "WHEN m.pr_id IS NOT NULL THEN (CASE WHEN m.ws > 0 THEN LN.w / m.ws ELSE 1.0 / m.ns END) "
        "ELSE (CASE WHEN LP.wp > 0 THEN LN.w / LP.wp ELSE 1.0 / LP.np END) END) AS va "
        "FROM S LEFT JOIN LW m ON m.pr_id = S.pr_id AND m.lsup = S.supplier "
        "LEFT JOIN LN ON LN.pr_id = S.pr_id AND (LN.lsup = S.supplier OR m.pr_id IS NULL) "
        "LEFT JOIN LP ON LP.pr_id = S.pr_id)"
    ) % {"lsup": lsup, "w": _LINE_W, "cat": _LINE_CAT, "item": _LINE_ITEM}


def _cte_l(f, rng):
    where, a = _pr_where(f, rng, with_cat=False)
    sql = ("L AS (SELECT %s, %s AS supplier, %s AS category, %s AS item, COALESCE(i.qty, 0) AS qty, "
           "COALESCE(NULLIF(i.net_price, 0), i.unit_price, 0) * %s AS price, "
           "COALESCE(CAST(i.item_id AS TEXT), '') AS item_ref, COALESCE(TRIM(i.unit), '') AS unit "
           "FROM pr_items i JOIN pr_requests p ON p.id = i.pr_id LEFT JOIN %s po1 ON po1.pr_id = p.id "
           "LEFT JOIN proc_items pi ON pi.id = i.item_id WHERE %s)"
           % (_PR_COLS, _lsup("p"), _LINE_CAT, _LINE_ITEM, _FX, _PO1, where))
    return sql, a


# The dashboard runs a dozen statements over the same S. facts() builds S once
# into a TEMP table on the page's connection; every S / A statement on that
# connection with the same filters then reads the table instead of rebuilding S.
_MAT = threading.local()
_MAT_TABLE = "an_facts_s"


def _fkey(f, rng, today):
    return (tuple((k, f.get(k) or "") for k in FILTER_KEYS), tuple(rng), today)


@contextmanager
def facts(f, conn, today=None):
    today_s = (today or date.today()).isoformat()
    rng = date_range(f, today)
    sql, a = _cte_s(f, rng, today_s)
    prev = getattr(_MAT, "v", None)
    try:
        conn.execute("DROP TABLE IF EXISTS " + _MAT_TABLE)
        conn.execute("CREATE TEMP TABLE %s AS WITH %s SELECT * FROM S" % (_MAT_TABLE, sql), a)
        _MAT.v = (conn, _fkey(f, rng, today_s))
    except Exception as exc:                                      # noqa: BLE001
        # No TEMP privilege (or similar): every statement builds S inline, as before.
        import logging
        logging.getLogger(__name__).warning("analytics facts table not built: %s", exc)
    try:
        yield
    finally:
        _MAT.v = prev
        try:
            conn.execute("DROP TABLE IF EXISTS " + _MAT_TABLE)
        except Exception:                                         # noqa: BLE001
            pass                    # a temp table dies with its connection anyway


def _with(fact, f, rng, today, conn=None):
    return _with_many({fact}, f, rng, today, conn)


def _with_many(facts_needed, f, rng, today, conn=None):
    """One WITH clause defining every fact set in `facts_needed` (S, A, L)."""
    parts, a = [], []
    if facts_needed & {"S", "A"}:
        m = getattr(_MAT, "v", None)
        if conn is not None and m and m[0] is conn and m[1] == _fkey(f, rng, today):
            parts.append("S AS (SELECT * FROM %s)" % _MAT_TABLE)
        else:
            sql, a = _cte_s(f, rng, today)
            parts.append(sql)
        if "A" in facts_needed:
            parts.append(_cte_a())
    if "L" in facts_needed:
        sql, la = _cte_l(f, rng)
        parts.append(sql)
        a = a + la
    return "WITH " + ", ".join(parts) + " ", a


def _fact_where(fact, f, extra=()):
    w, a = list(extra), []
    if f.get("vendor"):
        w.append("f.supplier = ?")
        a.append(f["vendor"])
    if f.get("cat") and fact in ("A", "L"):
        w.append("f.category = ?")
        a.append(f["cat"])
    return (" WHERE " + " AND ".join(w)) if w else "", a


# --------------------------------------------------------------------------
# the builder's whitelists
# --------------------------------------------------------------------------
DIMS = ("supplier", "department", "category", "item", "month", "quarter", "status",
        "requester", "cost_centre", "currency", "kind")
LINE_DIMS = {"category", "item"}
TIME_DIMS = {"month", "quarter"}
# measure -> (money?, allowed aggregations, fact rule)
MEASURES = {
    "spend": (True, ("sum", "avg", "max", "min"), "share_or_line"),
    "requests": (False, ("count",), "share_or_line"),
    "pos": (False, ("count",), "share"),
    "qty": (False, ("sum", "avg", "max", "min"), "line"),
    "price": (True, ("avg",), "line"),
    "cycle": (False, ("avg", "max", "min"), "share"),
    "savings": (True, ("sum", "avg", "max", "min"), "share"),
}
CHARTS = ("bar", "hbar", "line", "pie", "doughnut", "table")
SORTS = ("auto", "value_desc", "value_asc", "label_asc", "label_desc")
MAX_TOP = 100
MAX_SERIES = 8
_AGG_SQL = {"sum": "SUM(x.v)", "avg": "AVG(x.v)", "max": "MAX(x.v)", "min": "MIN(x.v)"}
_ADDITIVE = {"spend", "qty", "savings"}


def money_measures():
    return {k for k, (money, _a, _r) in MEASURES.items() if money}


def parse_config(args, money=True):
    """A builder definition from query args (or a saved dict). Returns
    (cfg, error_key). Everything is checked against the whitelists above."""
    g = (lambda k, d="": (args.get(k) if args.get(k) is not None else d))
    dims = [d for d in (_clean(g("d1", "supplier"), 30), _clean(g("d2"), 30)) if d]
    if not dims or any(d not in DIMS for d in dims):
        return None, "bad_dim"
    if len(dims) == 2 and dims[0] == dims[1]:
        dims = dims[:1]
    measure = _clean(g("m", "spend" if money else "requests"), 20)
    if measure not in MEASURES:
        return None, "bad_measure"
    is_money, aggs, rule = MEASURES[measure]
    if is_money and not money:
        return None, "money_hidden"
    if rule == "share" and LINE_DIMS & set(dims):
        return None, "needs_request_dim"
    agg = _clean(g("agg"), 10) or aggs[0]
    if agg not in aggs:
        agg = aggs[0]
    chart = _clean(g("chart"), 10) or "bar"
    if chart not in CHARTS:
        chart = "bar"
    sort = _clean(g("sort"), 12) or "auto"
    if sort not in SORTS:
        sort = "auto"
    try:
        top = int(g("top", "10") or 10)
    except (TypeError, ValueError):
        top = 10
    top = max(1, min(MAX_TOP, top))
    return {"d1": dims[0], "d2": dims[1] if len(dims) > 1 else "", "m": measure,
            "agg": agg, "chart": chart, "sort": sort, "top": top}, None


def config_args(cfg):
    return {k: cfg[k] for k in ("d1", "d2", "m", "agg", "chart", "sort", "top") if cfg.get(k)}


def _fact_for(measure, dims, f):
    rule = MEASURES[measure][2]
    lineish = bool(LINE_DIMS & set(dims))
    if rule == "line":
        return "L"
    if rule == "share":
        return "S"
    if measure == "spend":
        return "A" if (lineish or f.get("cat")) else "S"
    return "L" if lineish else "S"          # requests


def query(cfg, f, today=None, conn=None):
    """Run a builder definition. Returns raw grouped rows
    [(d1, d2 or None, value, n)] — one aggregate statement, no per-row work."""
    return query_many([cfg], f, today, conn)[0]


def query_many(cfgs, f, today=None, conn=None):
    """Several builder definitions over the same filters in ONE statement
    (UNION ALL, one round trip): a list of query() results, in order."""
    today_s = (today or date.today()).isoformat()
    rng = date_range(f, today)
    bodies, b_args, facts_needed = [], [], set()
    for qn, cfg in enumerate(cfgs):
        fact, body, args = _query_body(cfg, f)
        facts_needed.add(fact)
        d1 = "w.d1" if cfg.get("d2") else "NULL"
        bodies.append("SELECT %d AS qn, w.d0 AS d0, %s AS d1, w.val AS val, w.n AS n FROM (%s) w"
                      % (qn, d1, body))
        b_args += args
    w_sql, w_args = _with_many(facts_needed, f, rng, today_s, conn)
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(w_sql + " UNION ALL ".join(bodies), w_args + b_args).fetchall()
    finally:
        if own:
            conn.close()
    out = [[] for _ in cfgs]
    for r in rows:
        two = bool(cfgs[int(r["qn"])].get("d2"))
        out[int(r["qn"])].append((r["d0"] if r["d0"] is not None else "",
                                  (r["d1"] if r["d1"] is not None else "") if two else None,
                                  round(float(r["val"]), 2) if r["val"] is not None else None,
                                  int(r["n"] or 0)))
    return out


def _query_body(cfg, f):
    """(fact, SELECT ... over that fact, args) for one builder definition."""
    dims = [d for d in (cfg["d1"], cfg.get("d2")) if d]
    measure, agg = cfg["m"], cfg["agg"]
    fact = _fact_for(measure, dims, f)
    extra = ["f.status IN (%s)" % _COMMITTED_SQL] if measure in ("spend", "qty", "price", "savings") else []
    where, f_args = _fact_where(fact, f, extra)
    dsel = ", ".join("f.%s AS d%d" % (d, i) for i, d in enumerate(dims))
    dgrp = ", ".join("f.%s" % d for d in dims)
    xs = ", ".join("x.d%d" % i for i in range(len(dims)))
    per_pr = {"spend": "SUM(f.va)" if fact == "A" else "SUM(f.v)",
              "qty": "SUM(f.qty)", "cycle": "MAX(f.cyc)",
              "savings": "SUM(f.q_first - f.q_chosen)"}
    if measure in per_pr:
        sql = ("SELECT %s, %s AS val, COUNT(x.v) AS n FROM (SELECT %s, f.pr_id AS pr_id, %s AS v "
               "FROM %s f%s GROUP BY %s, f.pr_id) x GROUP BY %s"
               % (xs, _AGG_SQL[agg], dsel, per_pr[measure], fact, where, dgrp, xs))
    elif measure == "price":
        sql = ("SELECT %s, SUM(CASE WHEN f.price > 0 AND f.qty > 0 THEN f.price * f.qty ELSE 0 END) / "
               "NULLIF(SUM(CASE WHEN f.price > 0 AND f.qty > 0 THEN f.qty ELSE 0 END), 0) AS val, "
               "COUNT(DISTINCT f.pr_id) AS n FROM L f%s GROUP BY %s" % (dsel, where, dgrp))
    else:
        key = "f.po_key" if measure == "pos" else "f.pr_id"
        sql = ("SELECT %s, COUNT(DISTINCT %s) AS val, COUNT(DISTINCT f.pr_id) AS n FROM %s f%s "
               "GROUP BY %s" % (dsel, key, fact, where, dgrp))
    return fact, sql, f_args


def _ym(k):
    """(year, month) of a month / quarter label or an ISO date, else None."""
    try:
        k = str(k)
        m = int(k[-1]) * 3 - 2 if k[5:6] == "Q" else int(k[5:7])
        return (int(k[:4]), m) if 1 <= m <= 12 else None
    except (TypeError, ValueError):
        return None


def _periods(dim, rng, keys):
    """Every month / quarter label from the filter's start (or the first data
    period) to its end (or the last one), so a period with no buying shows."""
    seen = [x for x in (_ym(k) for k in keys) if x]
    if not seen:
        return []
    lo = _ym(rng[0]) if rng and rng[0] else min(seen)
    hi = _ym(rng[1]) if rng and rng[1] else max(seen)
    out = []
    y, m = lo or min(seen)
    while (y, m) <= (hi or max(seen)) and len(out) < 600:
        lab = "%04d-%02d" % (y, m) if dim == "month" else "%04d-Q%d" % (y, (m + 2) // 3)
        if not out or out[-1] != lab:
            out.append(lab)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def shape(cfg, rows, rng=None):
    """Sort, cut to top-N (+ 'Others' when the measure adds up) and pivot the
    rows of query() into chart-ready series. Labels stay RAW here; the caller
    translates them (label()). A month / quarter axis gets every calendar
    period of `rng` (date_range), empty ones at 0 for sums and counts."""
    measure, agg, two = cfg["m"], cfg["agg"], bool(cfg.get("d2"))
    additive = measure in _ADDITIVE and agg == "sum"
    totals = {}
    for d1, _d2, v, _n in rows:
        totals[d1] = totals.get(d1, 0.0) + (v or 0.0)
    timed = cfg["d1"] in TIME_DIMS
    fill = 0.0 if timed and agg in ("sum", "count") else None
    if timed:
        for k in _periods(cfg["d1"], rng, list(totals)):
            totals.setdefault(k, 0.0)
    sort = cfg.get("sort") or "auto"
    if sort == "auto":
        sort = "label_asc" if cfg["d1"] in TIME_DIMS else "value_desc"
    keys = list(totals)
    top = int(cfg.get("top") or 10)
    rest = []
    if cfg["d1"] in TIME_DIMS and sort.startswith("label"):
        keys = sorted(keys)[-top:]                  # the most recent N periods
        if sort == "label_desc":
            keys.reverse()
    else:
        if sort.startswith("label"):
            keys.sort(key=lambda k: str(k).lower(), reverse=sort == "label_desc")
        else:
            keys.sort(key=lambda k: totals[k], reverse=sort == "value_desc")
        if len(keys) > top:
            keys, rest = keys[:top], keys[top:]
    others = bool(rest) and additive
    if not two:
        vals = {d1: (v, n) for d1, _d2, v, n in rows}
        data = [vals.get(k, (fill, 0))[0] for k in keys]
        ns = [vals.get(k, (fill, 0))[1] for k in keys]
        if others:
            keys.append(None)
            data.append(round(sum(totals[k] for k in rest), 2))
            ns.append(sum(vals.get(k, (0, 0))[1] for k in rest))
        return {"keys": keys, "series_keys": [None], "data": [data], "n": ns, "others": others}
    stot = {}
    for d1, d2, v, _n in rows:
        if d1 in keys:
            stot[d2] = stot.get(d2, 0.0) + (v or 0.0)
    skeys = sorted(stot, key=lambda k: -stot[k])
    srest = skeys[MAX_SERIES:] if additive else []
    skeys = skeys[:MAX_SERIES]
    cell = {(d1, d2): v for d1, d2, v, _n in rows}
    data = [[cell.get((k, s), fill) for k in keys] for s in skeys]
    if srest:
        skeys.append(None)
        data.append([round(sum(cell.get((k, s)) or 0 for s in srest), 2) for k in keys])
    return {"keys": keys, "series_keys": skeys, "data": data, "n": None, "others": False}


# --------------------------------------------------------------------------
# the dashboard
# --------------------------------------------------------------------------
def _num(v, nd=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def kpis(f, today=None, conn=None):
    """The KPI strip: one statement over S (two when a category filter makes
    the value a share of the request's lines)."""
    today_s = (today or date.today()).isoformat()
    rng = date_range(f, today)
    w_sql, w_args = _with("S", f, rng, today_s, conn)
    where, f_args = _fact_where("S", f)
    wavg = ("SUM(CASE WHEN f.%(c)s IS NOT NULL THEN f.%(c)s * f.frac END) / "
            "NULLIF(SUM(CASE WHEN f.%(c)s IS NOT NULL THEN f.frac END), 0)")
    sql = (w_sql + "SELECT COUNT(DISTINCT f.pr_id) AS requests, COUNT(DISTINCT f.po_key) AS pos, "
           "SUM(f.v) AS spend, SUM(f.v_open) AS open_commit, "
           "SUM(f.q_hi - f.q_chosen) AS savings, SUM(f.q_chosen - f.q_lo) AS missed, "
           "SUM(f.q_first - f.q_chosen) AS vs_first, SUM(f.q_first) AS q_first, SUM(f.q_hi) AS q_hi, "
           "SUM(f.q_lo) AS q_lo, SUM(f.q_chosen) AS q_chosen, "
           + wavg % {"c": "cyc"} + " AS cyc, " + wavg % {"c": "lead_d"} + " AS lead_d, "
           + wavg % {"c": "ontime"} + " AS ontime, "
           "COUNT(DISTINCT CASE WHEN f.overdue = 1 THEN COALESCE(f.po_key, 'pr' || CAST(f.pr_id AS TEXT)) END) "
           "AS overdue FROM S f" + where)
    own = conn is None
    conn = conn or get_db()
    try:
        r = dict(conn.execute(sql, w_args + f_args).fetchone() or {})
        if f.get("cat"):
            aw, aa = _with("A", f, rng, today_s, conn)
            awhere, af = _fact_where("A", f)
            r2 = conn.execute(aw + "SELECT SUM(f.va) AS spend, SUM(CASE WHEN f.status IN (%s) THEN f.va "
                              "ELSE 0 END) AS open_commit FROM A f%s" % (_OPEN_SQL, awhere),
                              aa + af).fetchone()
            r["spend"], r["open_commit"] = r2["spend"], r2["open_commit"]
    finally:
        if own:
            conn.close()
    return {"requests": int(r.get("requests") or 0), "pos": int(r.get("pos") or 0),
            "spend": _num(r.get("spend")) or 0.0, "open_commit": _num(r.get("open_commit")) or 0.0,
            "savings": _num(r.get("savings")) or 0.0, "missed": _num(r.get("missed")) or 0.0,
            "vs_first": _num(r.get("vs_first")) or 0.0,
            "quotes": {"firstq": _num(r.get("q_first")) or 0.0, "hi": _num(r.get("q_hi")) or 0.0,
                       "lo": _num(r.get("q_lo")) or 0.0, "chosen": _num(r.get("q_chosen")) or 0.0},
            "cycle": _num(r.get("cyc"), 1), "lead": _num(r.get("lead_d"), 1),
            "ontime": _num((r.get("ontime") or 0) * 100, 1) if r.get("ontime") is not None else None,
            "overdue": int(r.get("overdue") or 0)}


def stage_times(f, today=None, conn=None):
    """Per approval stage: average hours from activation to signature,
    rejections and steps waiting now — slowest first (the bottleneck)."""
    today_s = (today or date.today()).isoformat()
    w_sql, w_args = _with("S", f, date_range(f, today), today_s, conn)
    where, f_args = _fact_where("S", f)
    sql = (w_sql + "SELECT st.stage AS stage, COUNT(*) AS steps, "
           "AVG(CASE WHEN st.status IN ('approved', 'rejected') THEN %s END) * 24 AS hours, "
           "SUM(CASE WHEN st.status = 'rejected' THEN 1 ELSE 0 END) AS rejected, "
           "SUM(CASE WHEN st.status = 'pending' AND COALESCE(st.activated_at, '') <> '' "
           "AND pp.status = 'pending' THEN 1 ELSE 0 END) AS waiting "
           "FROM pr_steps st JOIN pr_requests pp ON pp.id = st.pr_id "
           "WHERE st.pr_id IN (SELECT f.pr_id FROM S f%s) GROUP BY st.stage"
           % (_days("st.activated_at", "st.acted_at"), where))
    own = conn is None
    conn = conn or get_db()
    try:
        rows = [dict(r) for r in conn.execute(sql, w_args + f_args).fetchall()]
    finally:
        if own:
            conn.close()
    for r in rows:
        r["hours"] = _num(r["hours"], 1)
    rows.sort(key=lambda r: -(r["hours"] or -1))
    return rows


def savings_totals(f, today=None, conn=None):
    today_s = (today or date.today()).isoformat()
    w_sql, w_args = _with("S", f, date_range(f, today), today_s, conn)
    where, f_args = _fact_where("S", f)
    sql = (w_sql + "SELECT SUM(f.q_first) AS firstq, SUM(f.q_hi) AS hi, SUM(f.q_lo) AS lo, "
           "SUM(f.q_chosen) AS chosen, COUNT(DISTINCT CASE WHEN f.q_chosen IS NOT NULL THEN f.pr_id END) AS n "
           "FROM S f" + where)
    own = conn is None
    conn = conn or get_db()
    try:
        r = dict(conn.execute(sql, w_args + f_args).fetchone() or {})
    finally:
        if own:
            conn.close()
    return {k: _num(r.get(k)) or 0.0 for k in ("firstq", "hi", "lo", "chosen")} | {"n": int(r.get("n") or 0)}


def budget_year(f, today=None):
    t = today or date.today()
    lo, hi = date_range(f, today)
    return (hi or t.isoformat())[:4]


def budget_vs_actual(f, today=None, conn=None):
    """Annual budget vs committed spend per department, for the year the filter
    ends in. The year is the budget's grain, so the committed side always
    covers the whole year whatever the date filter says."""
    year = budget_year(f, today)
    fy = dict(f, period="custom", **{"from": year + "-01-01", "to": year + "-12-31"})
    own = conn is None
    conn = conn or get_db()
    try:
        args, sql = [year], ("SELECT COALESCE(TRIM(department), '') AS d, SUM(COALESCE(amount, 0)) AS a "
                             "FROM proc_budgets WHERE period = ?")
        if f.get("dept"):
            sql += " AND COALESCE(TRIM(department), '') = ?"
            args.append(f["dept"])
        budgets = {r["d"]: float(r["a"] or 0) for r in conn.execute(
            sql + " GROUP BY COALESCE(TRIM(department), '')", args).fetchall()}
        if f.get("vendor") or f.get("cat"):
            spent = {d1: v or 0.0 for d1, _d2, v, _n in query(
                {"d1": "department", "d2": "", "m": "spend", "agg": "sum"}, fy, today, conn)}
        else:
            # A request's supplier shares add up to the request, so without a
            # supplier / category filter its value per department is _VALUE
            # itself: the same figure as query(), with no share or quote work.
            where, a = _pr_where(fy, date_range(fy, today), with_cat=False)
            spent = {r["d"]: float(r["v"] or 0) for r in conn.execute(
                "SELECT COALESCE(TRIM(p.department), '') AS d, SUM(%s) AS v FROM pr_requests p "
                "WHERE %s GROUP BY COALESCE(TRIM(p.department), '')" % (_VALUE, where), a).fetchall()}
    finally:
        if own:
            conn.close()
    depts = sorted(set(budgets) | {d for d, v in spent.items() if v}, key=lambda d: -max(
        budgets.get(d, 0), spent.get(d, 0)))
    return year, [{"dept": d, "budget": round(budgets.get(d, 0.0), 2),
                   "actual": round(spent.get(d, 0.0), 2),
                   "pct": (round(100.0 * spent.get(d, 0.0) / budgets[d], 1) if budgets.get(d) else None)}
                  for d in depts]


def supplier_performance(f, today=None, conn=None, top=10, money=True):
    """Top suppliers with their delivery record: orders and average
    PO->receipt days from S, plus on-time, rejection and price variance from
    vendor_score (the same figures /procurement/vendors shows). Ranked by
    committed spend only for a reader who sees money (`money`); otherwise by
    orders and requests, so the order cannot leak the spend ranking."""
    from app.approvals import vendor_score
    from app.approvals.constants import vendor_key
    today_s = (today or date.today()).isoformat()
    w_sql, w_args = _with("S", f, date_range(f, today), today_s, conn)
    where, f_args = _fact_where("S", f)
    wavg = ("SUM(CASE WHEN f.%(c)s IS NOT NULL THEN f.%(c)s * f.frac END) / "
            "NULLIF(SUM(CASE WHEN f.%(c)s IS NOT NULL THEN f.frac END), 0)")
    sql = (w_sql + "SELECT f.supplier AS supplier, SUM(f.v) AS spend, COUNT(DISTINCT f.po_key) AS pos, "
           "COUNT(DISTINCT f.pr_id) AS requests, " + wavg % {"c": "lead_d"} + " AS lead_d, "
           "SUM(f.overdue) AS overdue FROM S f" + where + " GROUP BY f.supplier")
    own = conn is None
    conn = conn or get_db()
    try:
        rows = [dict(r) for r in conn.execute(sql, w_args + f_args).fetchall()]
        lo, _hi = date_range(f, today)
        t = today or date.today()
        months = 120 if not lo else max(1, (t.year - int(lo[:4])) * 12 + t.month - int(lo[5:7]) + 1)
        cards = vendor_score.scorecards(months=months, conn=conn, today=t)
    finally:
        if own:
            conn.close()
    rows = [r for r in rows if r["supplier"] != UNASSIGNED and (r["spend"] or r["pos"])]
    if money:
        rows.sort(key=lambda r: (-(r["spend"] or 0), -(r["pos"] or 0)))
    else:
        rows.sort(key=lambda r: (-(r["pos"] or 0), -(r["requests"] or 0), r["supplier"].lower()))
    out = []
    for r in rows[:top]:
        card = cards.get(vendor_key(r["supplier"])) or {}
        ot = card.get("on_time") or {}
        out.append({"supplier": r["supplier"], "spend": _num(r["spend"]) or 0.0,
                    "pos": int(r["pos"] or 0), "requests": int(r["requests"] or 0),
                    "lead": _num(r["lead_d"], 1),
                    "ontime": ot.get("pct"), "ontime_n": int(ot.get("n") or 0),
                    "overdue": int(r["overdue"] or 0),
                    "reject": (card.get("rejection") or {}).get("pct"),
                    "price_var": (card.get("price_variance") or {}).get("pct")})
    return out


def price_history(f, rise_pct=10.0, item="", today=None, conn=None, limit=25):
    """Monthly average unit price (EGP, ex-VAT, quantity-weighted) per item on
    committed requests; the alert list holds items whose latest month is more
    than `rise_pct` above the month they were bought before."""
    today_s = (today or date.today()).isoformat()
    w_sql, w_args = _with("L", f, date_range(f, today), today_s)
    where, f_args = _fact_where("L", f, ["f.status IN (%s)" % _COMMITTED_SQL, "f.price > 0",
                                         "f.qty > 0", "f.item <> ''"])
    sql = (w_sql + "SELECT f.item AS item, f.item_ref AS ref, f.unit AS unit, f.month AS month, "
           "SUM(f.price * f.qty) AS amt, SUM(f.qty) AS qty, COUNT(DISTINCT f.pr_id) AS n FROM L f" + where
           + " GROUP BY f.item_ref, f.item, f.unit, f.month")
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(sql, w_args + f_args).fetchall()
    finally:
        if own:
            conn.close()
    # A box and a piece are different prices, and two catalogue items may share
    # a name: one series per (catalogue id or name, unit), labelled with both.
    series, names, units = {}, {}, {}
    for r in rows:
        q = float(r["qty"] or 0)
        if q > 0:
            key = (r["ref"] or "", r["item"], r["unit"] or "")
            series.setdefault(key, []).append((r["month"], float(r["amt"] or 0) / q, int(r["n"] or 0)))
    labels = {}
    for key in series:
        lab = key[1] + (" (%s)" % key[2] if key[2] else "")
        labels.setdefault(lab, []).append(key)
    for lab, keys in labels.items():
        for key in keys:
            names[key] = lab if len(keys) == 1 else "%s #%s" % (lab, key[0] or "-")
            units[key] = key[2]
    alerts = []
    for key, pts in series.items():
        pts.sort()
        if len(pts) < 2 or pts[-2][1] <= 0:
            continue
        prev, last = pts[-2], pts[-1]
        rise = 100.0 * (last[1] - prev[1]) / prev[1]
        if rise > rise_pct:
            alerts.append({"item": names[key], "name": key[1], "unit": units[key],
                           "prev_month": prev[0], "prev": round(prev[1], 2),
                           "month": last[0], "last": round(last[1], 2), "rise": round(rise, 1)})
    alerts.sort(key=lambda a: -a["rise"])
    by_label = {names[k]: k for k in series}
    items = sorted(by_label, key=lambda lab: (-len(series[by_label[lab]]), lab.lower()))
    pick = item if item in by_label else (alerts[0]["item"] if alerts else (items[0] if items else ""))
    key = by_label.get(pick)
    return {"alerts": alerts[:limit], "items": items[:300], "item": pick,
            "item_name": key[1] if key else "",
            "points": [{"month": m, "price": round(p, 2), "n": n} for m, p, n in series.get(key, [])]}


def filter_options(conn=None):
    """Values for the filter bar's selects, one round trip."""
    sql = ("SELECT 'dept' AS k, COALESCE(TRIM(department), '') AS v FROM pr_requests WHERE is_active = 1 "
           "GROUP BY COALESCE(TRIM(department), '') "
           "UNION SELECT 'vendor', COALESCE(TRIM(vendor), '') FROM pr_purchase_orders GROUP BY COALESCE(TRIM(vendor), '') "
           "UNION SELECT 'vendor', COALESCE(TRIM(vendor), '') FROM pr_requests WHERE is_active = 1 "
           "GROUP BY COALESCE(TRIM(vendor), '') "
           "UNION SELECT 'cat', COALESCE(TRIM(pi.category_name), '') FROM pr_items i "
           "JOIN proc_items pi ON pi.id = i.item_id GROUP BY COALESCE(TRIM(pi.category_name), '') "
           "UNION SELECT 'cc', COALESCE(TRIM(cost_center), '') FROM pr_requests WHERE is_active = 1 "
           "GROUP BY COALESCE(TRIM(cost_center), '') "
           "UNION SELECT 'cur', UPPER(TRIM(COALESCE(NULLIF(currency, ''), 'EGP'))) FROM pr_requests "
           "WHERE is_active = 1 GROUP BY UPPER(TRIM(COALESCE(NULLIF(currency, ''), 'EGP')))")
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        if own:
            conn.close()
    out = {k: [] for k in ("dept", "vendor", "cat", "cc", "cur")}
    for r in rows:
        if r["v"] and r["v"] not in out[r["k"]]:
            out[r["k"]].append(r["v"])
    for v in out.values():
        v.sort(key=str.lower)
    return out


# --------------------------------------------------------------------------
# the one spend-by-supplier / by-department table (CSV exports use this too)
# --------------------------------------------------------------------------
def spend_rows(dim, f=None, conn=None):
    """[(label, requests, total EGP)] of committed spend per supplier or
    department, all time unless `f` says otherwise. services.export_dataset
    serves these rows, so the CSV, the dashboard and the builder can only ever
    show one number."""
    f = f or parse_filters({"period": "all"})
    rows = query({"d1": dim, "d2": "", "m": "spend", "agg": "sum"}, f, conn=conn)
    out = [[d1 or "—", n, round(v or 0.0, 2)] for d1, _d2, v, n in rows if (v or 0) != 0 or n]
    out.sort(key=lambda r: -r[2])
    return out


# --------------------------------------------------------------------------
# saved reports
# --------------------------------------------------------------------------
NAME_MAX = 80


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _report(row):
    d = dict(row)
    try:
        d["config"] = json.loads(d.get("config_json") or "{}")
    except (TypeError, ValueError):
        d["config"] = {}
    return d


def visible_reports(user, full_money):
    """The user's own reports plus the shared ones (shared = visible to the
    users who see all procurement money). Each carries `pinned` for THIS user."""
    uname = (user or {}).get("username") or ""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM proc_report_pins x WHERE x.report_id = r.id "
            "AND x.username = ?) AS pinned FROM proc_saved_reports r "
            "WHERE r.owner = ? OR (r.shared = 1 AND ? = 1) ORDER BY LOWER(r.name), r.id",
            (uname, uname, 1 if full_money else 0)).fetchall()
    finally:
        conn.close()
    return [_report(r) for r in rows]


def get_report(rid, user, full_money):
    for r in visible_reports(user, full_money):
        if r["id"] == rid:
            return r
    return None


def save_report(user, name, cfg, filters, shared=False, rid=None, full_money=False):
    """Create (rid None) or update the owner's report. Returns (ok, code, id).
    A report can be shared only by somebody who sees money (it is shared with
    exactly those people), and a money measure needs money to save at all."""
    uname = (user or {}).get("username") or ""
    name = _clean(name, NAME_MAX)
    if not name:
        return False, "name_required", None
    if cfg["m"] in money_measures() and not full_money:
        return False, "money_hidden", None
    if shared and not full_money:
        return False, "share_denied", None
    body = json.dumps({"cfg": config_args(cfg), "filters": filter_args(filters)}, sort_keys=True)
    conn = get_db()
    try:
        if rid:
            row = conn.execute("SELECT owner FROM proc_saved_reports WHERE id = ?", (rid,)).fetchone()
            if not row:
                return False, "not_found", None
            if row["owner"] != uname:
                return False, "not_owner", None
            conn.execute("UPDATE proc_saved_reports SET name = ?, config_json = ?, shared = ?, "
                         "updated_at = ? WHERE id = ?", (name, body, 1 if shared else 0, _now(), rid))
        else:
            cur = conn.execute("INSERT INTO proc_saved_reports (owner, name, config_json, shared, "
                               "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                               (uname, name, body, 1 if shared else 0, _now(), _now()))
            rid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return True, "saved", rid


def delete_report(user, rid):
    uname = (user or {}).get("username") or ""
    conn = get_db()
    try:
        row = conn.execute("SELECT owner FROM proc_saved_reports WHERE id = ?", (rid,)).fetchone()
        if not row:
            return False, "not_found"
        if row["owner"] != uname and (user or {}).get("role") != "super_admin":
            return False, "not_owner"
        conn.execute("DELETE FROM proc_report_pins WHERE report_id = ?", (rid,))
        conn.execute("DELETE FROM proc_saved_reports WHERE id = ?", (rid,))
        conn.commit()
    finally:
        conn.close()
    return True, "deleted"


def toggle_pin(user, rid, full_money):
    """Pin / unpin a report the user can see, on their own dashboard."""
    if not get_report(rid, user, full_money):
        return False, "not_found"
    uname = user["username"]
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM proc_report_pins WHERE report_id = ? AND username = ?",
                        (rid, uname)).fetchone():
            conn.execute("DELETE FROM proc_report_pins WHERE report_id = ? AND username = ?", (rid, uname))
            code = "unpinned"
        else:
            conn.execute("INSERT INTO proc_report_pins (report_id, username, created_at) VALUES (?, ?, ?)",
                         (rid, uname, _now()))
            code = "pinned"
        conn.commit()
    finally:
        conn.close()
    return True, code


# --------------------------------------------------------------------------
# labels (server-side, in the reader's language)
# --------------------------------------------------------------------------
_LANG = {"en": 0, "ar": 1, "tr": 2}


def t(key, lang="en"):
    row = I18N.get(key)
    if not row:
        return key
    return row[_LANG.get(lang or "en", 0)] or row[0]


def label(dim, raw, lang="en"):
    """A dimension value as a reader sees it."""
    if raw is None:
        return t("proc.analytics.others", lang)
    if raw == "" or raw == UNASSIGNED:
        return t("proc.analytics.none", lang)
    if dim == "status":
        return t("proc.analytics.st." + raw, lang) if ("proc.analytics.st." + raw) in I18N else raw
    if dim == "kind":
        return t("proc.analytics.kind." + raw, lang) if ("proc.analytics.kind." + raw) in I18N else raw.upper()
    return str(raw)


def stage_name(stage, lang="en"):
    key = "proc.analytics.stg." + (stage or "")
    if key in I18N:
        return t(key, lang)
    from app.approvals.constants import stage_label
    return stage_label(stage or "")


def unit_of(measure):
    return {"spend": "EGP", "savings": "EGP", "price": "EGP", "cycle": "days"}.get(measure, "")


# Every user-visible string of the workspace, EN / AR / TR. The same keys are
# in app/static/i18n/{en,ar,tr}.json (data-i18n); this copy renders text that
# is built on the server (chart titles, axis values) in the reader's language.
I18N = {
    "proc.analytics.title": ("Procurement analytics", "تحليلات المشتريات", "Satın alma analitiği"),
    "proc.analytics.sub": ("Spend, speed and supplier performance from the live requests. Filter, click any bar to see the requests behind it, or build your own report.",
                           "الإنفاق وسرعة الاعتماد وأداء الموردين من الطلبات الفعلية. فلتر، واضغط على أي عمود تشوف الطلبات اللي وراه، أو اعمل تقريرك بنفسك.",
                           "Canlı taleplerden harcama, hız ve tedarikçi performansı. Filtreleyin, arkasındaki talepleri görmek için herhangi bir çubuğa tıklayın veya kendi raporunuzu oluşturun."),
    "proc.analytics.builder": ("Report builder", "منشئ التقارير", "Rapor oluşturucu"),
    "proc.analytics.back": ("Back to procurement", "رجوع للمشتريات", "Satın almaya dön"),
    "proc.analytics.filters": ("Filters", "الفلاتر", "Filtreler"),
    "proc.analytics.apply": ("Apply", "تطبيق", "Uygula"),
    "proc.analytics.reset": ("Reset", "مسح الفلاتر", "Sıfırla"),
    "proc.analytics.close": ("Close", "إغلاق", "Kapat"),
    "proc.analytics.period": ("Period", "الفترة", "Dönem"),
    "proc.analytics.p.m1": ("This month", "الشهر ده", "Bu ay"),
    "proc.analytics.p.m3": ("Last 3 months", "آخر 3 شهور", "Son 3 ay"),
    "proc.analytics.p.m6": ("Last 6 months", "آخر 6 شهور", "Son 6 ay"),
    "proc.analytics.p.m12": ("Last 12 months", "آخر 12 شهر", "Son 12 ay"),
    "proc.analytics.p.ytd": ("This year", "السنة دي", "Bu yıl"),
    "proc.analytics.p.all": ("All time", "كل الفترات", "Tüm zamanlar"),
    "proc.analytics.p.custom": ("Custom", "فترة مخصصة", "Özel"),
    "proc.analytics.from": ("From", "من", "Başlangıç"),
    "proc.analytics.to": ("To", "إلى", "Bitiş"),
    "proc.analytics.f.dept": ("Department", "الإدارة", "Departman"),
    "proc.analytics.f.vendor": ("Supplier", "المورد", "Tedarikçi"),
    "proc.analytics.f.cat": ("Item category", "فئة الصنف", "Kalem kategorisi"),
    "proc.analytics.f.cc": ("Cost centre", "مركز التكلفة", "Maliyet merkezi"),
    "proc.analytics.f.status": ("Status", "الحالة", "Durum"),
    "proc.analytics.f.cur": ("Currency", "العملة", "Para birimi"),
    "proc.analytics.any": ("All", "الكل", "Tümü"),
    "proc.analytics.active": ("Active filters", "الفلاتر المفعّلة", "Etkin filtreler"),
    "proc.analytics.remove": ("Remove filter", "شيل الفلتر", "Filtreyi kaldır"),
    "proc.analytics.k.spend": ("Committed spend (EGP)", "الإنفاق المرتبط (ج.م)", "Taahhüt edilen harcama (EGP)"),
    "proc.analytics.k.requests": ("Requests", "الطلبات", "Talepler"),
    "proc.analytics.k.pos": ("Purchase orders", "أوامر الشراء", "Satın alma siparişleri"),
    "proc.analytics.k.cycle": ("Avg approval time (days)", "متوسط مدة الاعتماد (يوم)", "Ort. onay süresi (gün)"),
    "proc.analytics.k.lead": ("Avg PO → receipt (days)", "متوسط أمر الشراء ← الاستلام (يوم)", "Ort. sipariş → teslim (gün)"),
    "proc.analytics.k.savings": ("Savings vs the first quote (EGP)",
                                 "التوفير مقارنة بأول عرض (ج.م)",
                                 "İlk teklife göre tasarruf (EGP)"),
    "proc.analytics.k.ontime": ("Received in full on time", "اتستلم كامل في الميعاد", "Zamanında tam teslim"),
    "proc.analytics.k.open": ("Open commitments (EGP)", "الارتباطات المفتوحة (ج.م)", "Açık taahhütler (EGP)"),
    "proc.analytics.k.overdue": ("Overdue POs", "أوامر شراء متأخرة", "Geciken siparişler"),
    "proc.analytics.k.missed": ("Paid above the lowest quote", "مدفوع زيادة عن أقل عرض", "En düşük tekliften fazla ödenen"),
    "proc.analytics.c.trend": ("Spend over time", "الإنفاق على مدار الوقت", "Zaman içinde harcama"),
    "proc.analytics.c.trend_n": ("Requests over time", "الطلبات على مدار الوقت", "Zaman içinde talepler"),
    "proc.analytics.c.supplier": ("Spend by supplier", "الإنفاق حسب المورد", "Tedarikçiye göre harcama"),
    "proc.analytics.c.supplier_n": ("Requests by supplier", "الطلبات حسب المورد", "Tedarikçiye göre talepler"),
    "proc.analytics.c.dept": ("Spend by department", "الإنفاق حسب الإدارة", "Departmana göre harcama"),
    "proc.analytics.c.dept_n": ("Requests by department", "الطلبات حسب الإدارة", "Departmana göre talepler"),
    "proc.analytics.c.cat": ("Spend by item category", "الإنفاق حسب فئة الصنف", "Kalem kategorisine göre harcama"),
    "proc.analytics.c.cat_n": ("Requests by item category", "الطلبات حسب فئة الصنف", "Kalem kategorisine göre talepler"),
    "proc.analytics.c.kind": ("OPEX / CAPEX split", "تقسيم المصروفات التشغيلية / الرأسمالية", "OPEX / CAPEX dağılımı"),
    "proc.analytics.c.status": ("Requests by status", "الطلبات حسب الحالة", "Duruma göre talepler"),
    "proc.analytics.c.stages": ("Approval time per stage", "مدة الاعتماد في كل مرحلة", "Aşama başına onay süresi"),
    "proc.analytics.c.stages_sub": ("Average hours from a stage becoming active to its signature. Slowest first: that is the bottleneck.",
                                    "متوسط الساعات من وصول الطلب للمرحلة لحد التوقيع. الأبطأ الأول: دي نقطة الاختناق.",
                                    "Aşamanın etkinleşmesinden imzaya kadar ortalama saat. En yavaş başta: darboğaz odur."),
    "proc.analytics.c.savings": ("Savings from quotations", "التوفير من عروض الأسعار", "Tekliflerden tasarruf"),
    "proc.analytics.c.savings_sub": ("Requests with two or more quotations and a chosen one: what the first, highest, lowest and chosen quotes add up to (EGP, VAT included).",
                                     "الطلبات اللي عليها عرضين أو أكتر وفيه عرض متختار: مجموع أول عرض وأعلى عرض وأقل عرض والعرض المختار (ج.م شامل الضريبة).",
                                     "İki veya daha fazla teklifi ve seçilmiş teklifi olan talepler: ilk, en yüksek, en düşük ve seçilen tekliflerin toplamı (EGP, KDV dahil)."),
    "proc.analytics.q.first": ("First quote", "أول عرض", "İlk teklif"),
    "proc.analytics.q.hi": ("Highest quote", "أعلى عرض", "En yüksek teklif"),
    "proc.analytics.q.lo": ("Lowest quote", "أقل عرض", "En düşük teklif"),
    "proc.analytics.q.chosen": ("Chosen quote", "العرض المختار", "Seçilen teklif"),
    "proc.analytics.c.budget": ("Budget vs committed", "الموازنة مقابل المرتبط", "Bütçe / taahhüt"),
    "proc.analytics.c.budget_sub": ("Annual department budget against committed spend for the whole year shown.",
                                    "موازنة الإدارة السنوية مقابل الإنفاق المرتبط عن السنة كلها.",
                                    "Yıllık departman bütçesi ile gösterilen yılın tamamındaki taahhüt edilen harcama."),
    "proc.analytics.budget": ("Budget", "الموازنة", "Bütçe"),
    "proc.analytics.committed": ("Committed", "المرتبط", "Taahhüt"),
    "proc.analytics.used": ("Used", "المستخدم", "Kullanılan"),
    "proc.analytics.year": ("Year", "السنة", "Yıl"),
    "proc.analytics.c.suppliers": ("Supplier performance", "أداء الموردين", "Tedarikçi performansı"),
    "proc.analytics.c.suppliers_sub": ("Top suppliers by committed spend. On-time and rejection come from the vendor scorecard (each goods receipt against the required delivery date); price variance from invoices.",
                                       "أكبر الموردين حسب الإنفاق المرتبط. الالتزام بالميعاد ونسبة الرفض من تقييم الموردين (كل إذن استلام مقارنة بتاريخ التسليم المطلوب)؛ فرق السعر من الفواتير.",
                                       "Taahhüt edilen harcamaya göre en büyük tedarikçiler. Zamanında teslim ve ret, tedarikçi karnesinden gelir (her mal kabul, istenen teslim tarihiyle karşılaştırılır); fiyat sapması faturalardan."),
    "proc.analytics.col.supplier": ("Supplier", "المورد", "Tedarikçi"),
    "proc.analytics.col.spend": ("Spend (EGP)", "الإنفاق (ج.م)", "Harcama (EGP)"),
    "proc.analytics.col.pos": ("POs", "أوامر الشراء", "Sipariş"),
    "proc.analytics.col.lead": ("PO → receipt (days)", "أمر الشراء ← الاستلام (يوم)", "Sipariş → teslim (gün)"),
    "proc.analytics.col.ontime": ("On time", "في الميعاد", "Zamanında"),
    "proc.analytics.col.overdue": ("Overdue", "متأخر", "Geciken"),
    "proc.analytics.col.reject": ("Rejected", "مرفوض", "Reddedilen"),
    "proc.analytics.col.price_var": ("Price variance", "فرق السعر", "Fiyat sapması"),
    "proc.analytics.col.stage": ("Stage", "المرحلة", "Aşama"),
    "proc.analytics.col.hours": ("Avg hours", "متوسط الساعات", "Ort. saat"),
    "proc.analytics.col.steps": ("Steps", "الخطوات", "Adım"),
    "proc.analytics.col.rejected": ("Rejections", "مرات الرفض", "Ret"),
    "proc.analytics.col.waiting": ("Waiting now", "منتظر دلوقتي", "Şu an bekleyen"),
    "proc.analytics.col.item": ("Item", "الصنف", "Kalem"),
    "proc.analytics.col.prev": ("Before", "قبل", "Önce"),
    "proc.analytics.col.last": ("Latest", "الأخير", "Son"),
    "proc.analytics.col.rise": ("Rise", "الزيادة", "Artış"),
    "proc.analytics.col.month": ("Month", "الشهر", "Ay"),
    "proc.analytics.col.price": ("Avg unit price (EGP)", "متوسط سعر الوحدة (ج.م)", "Ort. birim fiyat (EGP)"),
    "proc.analytics.col.value": ("Value", "القيمة", "Değer"),
    "proc.analytics.col.n": ("Requests", "الطلبات", "Talep"),
    "proc.analytics.c.price": ("Price history per item", "تاريخ أسعار الأصناف", "Kalem bazında fiyat geçmişi"),
    "proc.analytics.c.price_sub": ("Quantity-weighted average unit price per month on committed requests (EGP, before VAT).",
                                   "متوسط سعر الوحدة في الشهر مرجّح بالكمية على الطلبات المعتمدة (ج.م قبل الضريبة).",
                                   "Taahhüt edilen taleplerde aylık miktar ağırlıklı ortalama birim fiyat (EGP, KDV hariç)."),
    "proc.analytics.c.alerts": ("Price rise alerts", "تنبيهات زيادة الأسعار", "Fiyat artışı uyarıları"),
    "proc.analytics.alerts_sub": ("Items whose latest month's price is more than the threshold above the month they were bought before.",
                                  "الأصناف اللي سعرها في آخر شهر زاد أكتر من النسبة المحددة عن آخر شهر اتشترت فيه قبله.",
                                  "Son ayki fiyatı, önceki alındığı aya göre eşikten fazla artan kalemler."),
    "proc.analytics.rise_label": ("Alert when the price rose more than (%)", "نبّهني لو السعر زاد أكتر من (%)", "Fiyat şu orandan fazla artarsa uyar (%)"),
    "proc.analytics.item_pick": ("Item", "الصنف", "Kalem"),
    "proc.analytics.show": ("Show", "اعرض", "Göster"),
    "proc.analytics.no_alerts": ("No item rose above the threshold in this period.", "مفيش صنف زاد عن النسبة دي في الفترة دي.", "Bu dönemde eşiği aşan kalem yok."),
    "proc.analytics.view_chart": ("Chart", "رسم بياني", "Grafik"),
    "proc.analytics.view_table": ("Table", "جدول", "Tablo"),
    "proc.analytics.export_xlsx": ("Excel", "إكسل", "Excel"),
    "proc.analytics.export_csv": ("CSV", "CSV", "CSV"),
    "proc.analytics.empty": ("No data for these filters yet.", "مفيش بيانات للفلاتر دي لسه.", "Bu filtreler için henüz veri yok."),
    "proc.analytics.drill_hint": ("Click a bar, slice or row to open the requests behind it.", "اضغط على أي عمود أو جزء أو صف عشان تفتح الطلبات اللي وراه.", "Arkasındaki talepleri açmak için bir çubuğa, dilime veya satıra tıklayın."),
    "proc.analytics.others": ("Others", "أخرى", "Diğerleri"),
    "proc.analytics.none": ("(not set)", "(غير محدد)", "(belirtilmemiş)"),
    "proc.analytics.define": ("How the figures are counted", "إزاي الأرقام بتتحسب", "Rakamlar nasıl hesaplanır"),
    "proc.analytics.def.spend": ("Spend = committed value of approved-or-later requests, in EGP, VAT included (net + tax, then × exchange rate), less any budget released on short-close. A request split across suppliers is shared by each purchase order's value. The same figure is used by the budget check, the exports and the report builder.",
                                 "الإنفاق = القيمة المرتبطة للطلبات المعتمدة وما بعدها، بالجنيه المصري، شاملة الضريبة (الصافي + الضريبة ثم × سعر الصرف)، ناقص أي موازنة اتفكت عند الإقفال الجزئي. الطلب اللي متقسم على كذا مورد بيتوزع حسب قيمة كل أمر شراء. نفس الرقم ده بيستخدمه فحص الموازنة والتصدير ومنشئ التقارير.",
                                 "Harcama = onaylanmış veya sonraki aşamadaki taleplerin taahhüt edilen değeri, EGP, KDV dahil (net + vergi, sonra × kur), kısmi kapanışta serbest bırakılan bütçe düşülerek. Birden çok tedarikçiye bölünen talep, her siparişin değerine göre paylaştırılır. Aynı rakam bütçe kontrolü, dışa aktarımlar ve rapor oluşturucu tarafından kullanılır."),
    "proc.analytics.def.cycle": ("Approval time = submitted to fully approved. PO → receipt = order issued to goods fully received. Received in full on time (the tile) = the whole request received on or before the required delivery date; the supplier table uses the vendor scorecard instead, which judges each goods receipt, partial ones included. Overdue = still on order after that date.",
                                 "مدة الاعتماد = من الإرسال لحد الاعتماد الكامل. أمر الشراء ← الاستلام = من إصدار الأمر لحد استلام البضاعة كاملة. اتستلم كامل في الميعاد (المربع) = الطلب كله اتستلم في أو قبل تاريخ التسليم المطلوب؛ أما جدول الموردين فبياخد رقم تقييم الموردين، اللي بيحكم على كل إذن استلام لوحده حتى الجزئي. متأخر = لسه تحت التوريد بعد التاريخ ده.",
                                 "Onay süresi = gönderimden tam onaya kadar. Sipariş → teslim = siparişin verilmesinden malların tamamen teslim alınmasına kadar. Zamanında tam teslim (kutucuk) = talebin tamamı istenen teslim tarihinde veya öncesinde teslim alındı; tedarikçi tablosu bunun yerine tedarikçi karnesini kullanır, kısmi olanlar dahil her mal kabulü ayrı değerlendirir. Geciken = bu tarihten sonra hâlâ siparişte."),
    "proc.analytics.def.savings": ("Savings = the first quote minus the chosen quote; the saving against the highest quote is shown beside it. Only requests with a chosen quote and two or more quotations that each price every line count (EGP, VAT included). Partial quotes and requests awarded line by line are left out.",
                                   "التوفير = أول عرض ناقص العرض المختار، وجنبه التوفير مقارنة بأعلى عرض. بيتحسب بس على الطلبات اللي فيها عرض مختار وعرضين أو أكتر كل واحد فيهم مسعّر كل البنود (ج.م شامل الضريبة). العروض الجزئية والطلبات اللي اترست بند بند مش داخلة في الحساب.",
                                   "Tasarruf = ilk teklif eksi seçilen teklif; en yüksek teklife göre tasarruf yanında gösterilir. Yalnızca seçilmiş bir teklifi ve her biri tüm kalemleri fiyatlayan iki veya daha fazla teklifi olan talepler sayılır (EGP, KDV dahil). Kısmi teklifler ve kalem kalem verilen talepler hariçtir."),
    "proc.analytics.counts_only": ("You see counts and times only. Money figures are shown to Purchasing, Finance and procurement admins.",
                                   "انت شايف الأعداد والمدد بس. الأرقام المالية بتظهر للمشتريات والمالية ومديري المشتريات.",
                                   "Yalnızca sayıları ve süreleri görüyorsunuz. Parasal rakamlar Satın Alma, Finans ve satın alma yöneticilerine gösterilir."),
    "proc.analytics.pinned": ("Pinned reports", "التقارير المثبّتة", "Sabitlenmiş raporlar"),
    "proc.analytics.open_builder": ("Open in builder", "افتح في المنشئ", "Oluşturucuda aç"),
    "proc.analytics.exports": ("Exports", "التصدير", "Dışa aktarımlar"),
    "proc.analytics.k.vs_hi": ("Vs the highest quote", "مقارنة بأعلى عرض", "En yüksek teklife göre"),
    "proc.analytics.c.suppliers_sub_n": ("Suppliers with the most purchase orders. On-time and rejection come from the vendor scorecard (each goods receipt against the required delivery date); price variance from invoices.",
                                         "الموردين اللي عليهم أكتر أوامر شراء. الالتزام بالميعاد ونسبة الرفض من تقييم الموردين (كل إذن استلام مقارنة بتاريخ التسليم المطلوب)؛ فرق السعر من الفواتير.",
                                         "En çok satın alma siparişi olan tedarikçiler. Zamanında teslim ve ret, tedarikçi karnesinden gelir (her mal kabul, istenen teslim tarihiyle karşılaştırılır); fiyat sapması faturalardan."),
    "proc.analytics.pin_own": ("Saved filters:", "الفلاتر المحفوظة:", "Kayıtlı filtreler:"),
    "proc.analytics.pin_cap": ("Only the first pinned reports are shown here. Unpin one in the report builder to make room.",
                               "بيظهر هنا أول التقارير المثبّتة بس. شيل التثبيت من تقرير في منشئ التقارير عشان تفضّي مكان.",
                               "Burada yalnızca ilk sabitlenmiş raporlar gösterilir. Yer açmak için rapor oluşturucuda birinin sabitlemesini kaldırın."),
    "proc.analytics.list.committed": ("Approved and later only (committed)",
                                      "المعتمد وما بعده بس (مرتبط)",
                                      "Yalnızca onaylanmış ve sonrası (taahhüt)"),
    "proc.analytics.list.open": ("Open commitments only", "الارتباطات المفتوحة بس", "Yalnızca açık taahhütler"),
    "proc.analytics.exports_sub": ("Live CSV reports, Excel-ready (UTF-8). All time.", "تقارير CSV مباشرة جاهزة للإكسل (UTF-8). كل الفترات.", "Canlı CSV raporları, Excel'e hazır (UTF-8). Tüm zamanlar."),
    # --- builder ---
    "proc.analytics.b.sub": ("Pick what to group by and what to measure. The result updates as you change it; save it to come back, share it or pin it to the dashboard.",
                             "اختار تجمّع بإيه وتقيس إيه. النتيجة بتتحدث وانت بتغيّر؛ احفظ التقرير عشان ترجعله أو تشاركه أو تثبّته في لوحة التحليلات.",
                             "Neye göre gruplayacağınızı ve neyi ölçeceğinizi seçin. Siz değiştirdikçe sonuç güncellenir; geri dönmek, paylaşmak veya panoya sabitlemek için kaydedin."),
    "proc.analytics.b.design": ("Design", "التصميم", "Tasarım"),
    "proc.analytics.b.d1": ("Group by", "جمّع حسب", "Grupla"),
    "proc.analytics.b.d2": ("Then by (optional)", "وبعدين حسب (اختياري)", "Sonra (isteğe bağlı)"),
    "proc.analytics.b.nothing": ("— nothing —", "— ولا حاجة —", "— hiçbiri —"),
    "proc.analytics.b.measure": ("Measure", "المقياس", "Ölçü"),
    "proc.analytics.b.agg": ("Aggregation", "طريقة الحساب", "Toplama"),
    "proc.analytics.b.chart": ("Chart type", "نوع الرسم", "Grafik türü"),
    "proc.analytics.b.sort": ("Sort", "الترتيب", "Sıralama"),
    "proc.analytics.b.top": ("Show top", "اعرض أول", "İlk kaç"),
    "proc.analytics.b.result": ("Result", "النتيجة", "Sonuç"),
    "proc.analytics.b.save": ("Save report", "احفظ التقرير", "Raporu kaydet"),
    "proc.analytics.b.save_as": ("Save as new", "احفظ كتقرير جديد", "Yeni olarak kaydet"),
    "proc.analytics.b.update": ("Update report", "حدّث التقرير", "Raporu güncelle"),
    "proc.analytics.b.name": ("Report name", "اسم التقرير", "Rapor adı"),
    "proc.analytics.b.share": ("Share with Purchasing, Finance and procurement admins", "شاركه مع المشتريات والمالية ومديري المشتريات", "Satın Alma, Finans ve satın alma yöneticileriyle paylaş"),
    "proc.analytics.b.mine": ("My reports", "تقاريري", "Raporlarım"),
    "proc.analytics.b.shared": ("Shared with me", "متشارك معايا", "Benimle paylaşılan"),
    "proc.analytics.b.no_reports": ("No saved reports yet.", "مفيش تقارير محفوظة لسه.", "Henüz kayıtlı rapor yok."),
    "proc.analytics.b.open": ("Open", "افتح", "Aç"),
    "proc.analytics.b.delete": ("Delete", "امسح", "Sil"),
    "proc.analytics.b.pin": ("Pin to dashboard", "ثبّت في اللوحة", "Panoya sabitle"),
    "proc.analytics.b.unpin": ("Unpin", "شيل التثبيت", "Sabitlemeyi kaldır"),
    "proc.analytics.b.by": ("by", "بواسطة", "oluşturan"),
    "proc.analytics.b.shared_tag": ("Shared", "متشارك", "Paylaşılan"),
    "proc.analytics.b.confirm_delete": ("Delete this saved report? This cannot be undone.", "تمسح التقرير المحفوظ ده؟ مش هينفع ترجعه.", "Bu kayıtlı rapor silinsin mi? Geri alınamaz."),
    "proc.analytics.b.loading": ("Updating…", "بيتحدث…", "Güncelleniyor…"),
    "proc.analytics.b.error": ("The report could not be run.", "التقرير ماشتغلش.", "Rapor çalıştırılamadı."),
    "proc.analytics.b.editing": ("Editing", "بتعدّل", "Düzenleniyor"),
    "proc.analytics.b.new": ("New report", "تقرير جديد", "Yeni rapor"),
    "proc.analytics.b.money_note": ("Money measures are available to Purchasing, Finance and procurement admins.", "مقاييس الفلوس متاحة للمشتريات والمالية ومديري المشتريات بس.", "Parasal ölçüler Satın Alma, Finans ve satın alma yöneticilerine açıktır."),
    "proc.analytics.err.bad_dim": ("Choose what to group by.", "اختار هتجمّع بإيه.", "Neye göre gruplanacağını seçin."),
    "proc.analytics.err.bad_measure": ("Choose a measure.", "اختار مقياس.", "Bir ölçü seçin."),
    "proc.analytics.err.money_hidden": ("This measure shows money, which your role does not see.", "المقياس ده فيه فلوس، ودورك مش بيشوفها.", "Bu ölçü parasal tutarları gösterir; rolünüz bunları görmez."),
    "proc.analytics.err.needs_request_dim": ("This measure is counted per request, so it cannot be split by item or category.", "المقياس ده بيتحسب لكل طلب، فمش ينفع يتقسم حسب الصنف أو الفئة.", "Bu ölçü talep başına hesaplanır; kaleme veya kategoriye göre bölünemez."),
    "proc.analytics.dim.supplier": ("Supplier", "المورد", "Tedarikçi"),
    "proc.analytics.dim.department": ("Department", "الإدارة", "Departman"),
    "proc.analytics.dim.category": ("Item category", "فئة الصنف", "Kalem kategorisi"),
    "proc.analytics.dim.item": ("Item", "الصنف", "Kalem"),
    "proc.analytics.dim.month": ("Month", "الشهر", "Ay"),
    "proc.analytics.dim.quarter": ("Quarter", "الربع", "Çeyrek"),
    "proc.analytics.dim.status": ("Status", "الحالة", "Durum"),
    "proc.analytics.dim.requester": ("Requester", "مقدّم الطلب", "Talep eden"),
    "proc.analytics.dim.cost_centre": ("Cost centre", "مركز التكلفة", "Maliyet merkezi"),
    "proc.analytics.dim.currency": ("Currency", "العملة", "Para birimi"),
    "proc.analytics.dim.kind": ("OPEX / CAPEX", "تشغيلي / رأسمالي", "OPEX / CAPEX"),
    "proc.analytics.m.spend": ("Spend (EGP)", "الإنفاق (ج.م)", "Harcama (EGP)"),
    "proc.analytics.m.requests": ("Number of requests", "عدد الطلبات", "Talep sayısı"),
    "proc.analytics.m.pos": ("Number of POs", "عدد أوامر الشراء", "Sipariş sayısı"),
    "proc.analytics.m.qty": ("Quantity", "الكمية", "Miktar"),
    "proc.analytics.m.price": ("Average unit price (EGP)", "متوسط سعر الوحدة (ج.م)", "Ortalama birim fiyat (EGP)"),
    "proc.analytics.m.cycle": ("Approval time (days)", "مدة الاعتماد (يوم)", "Onay süresi (gün)"),
    "proc.analytics.m.savings": ("Savings vs the first quote (EGP)",
                                 "التوفير مقارنة بأول عرض (ج.م)",
                                 "İlk teklife göre tasarruf (EGP)"),
    "proc.analytics.agg.sum": ("Total", "الإجمالي", "Toplam"),
    "proc.analytics.agg.avg": ("Average per request", "المتوسط لكل طلب", "Talep başına ortalama"),
    "proc.analytics.agg.max": ("Largest request", "أكبر طلب", "En büyük talep"),
    "proc.analytics.agg.min": ("Smallest request", "أصغر طلب", "En küçük talep"),
    "proc.analytics.agg.count": ("Count", "العدد", "Sayı"),
    "proc.analytics.ch.bar": ("Bar", "أعمدة", "Çubuk"),
    "proc.analytics.ch.hbar": ("Horizontal bar", "أعمدة أفقية", "Yatay çubuk"),
    "proc.analytics.ch.line": ("Line", "خطي", "Çizgi"),
    "proc.analytics.ch.pie": ("Pie", "دائري", "Pasta"),
    "proc.analytics.ch.doughnut": ("Doughnut", "حلقي", "Halka"),
    "proc.analytics.ch.table": ("Table only", "جدول بس", "Yalnızca tablo"),
    "proc.analytics.s.auto": ("Automatic", "تلقائي", "Otomatik"),
    "proc.analytics.s.value_desc": ("Highest first", "الأعلى الأول", "En yüksek önce"),
    "proc.analytics.s.value_asc": ("Lowest first", "الأقل الأول", "En düşük önce"),
    "proc.analytics.s.label_asc": ("A → Z / oldest first", "أ ← ي / الأقدم الأول", "A → Z / en eski önce"),
    "proc.analytics.s.label_desc": ("Z → A / newest first", "ي ← أ / الأحدث الأول", "Z → A / en yeni önce"),
    "proc.analytics.st.draft": ("Draft", "مسودة", "Taslak"),
    "proc.analytics.st.pending": ("Pending approval", "قيد الاعتماد", "Onay bekliyor"),
    "proc.analytics.st.approved": ("Approved", "معتمد", "Onaylandı"),
    "proc.analytics.st.rejected": ("Rejected", "مرفوض", "Reddedildi"),
    "proc.analytics.st.po_issued": ("On order", "تحت التوريد", "Siparişte"),
    "proc.analytics.st.in_transit": ("On the way", "في الطريق", "Yolda"),
    "proc.analytics.st.partially_received": ("Partly received", "مستلم جزئياً", "Kısmen teslim alındı"),
    "proc.analytics.st.received": ("Received", "مستلم", "Teslim alındı"),
    "proc.analytics.st.closed": ("Closed", "مقفول", "Kapandı"),
    "proc.analytics.st.cancelled": ("Cancelled", "ملغي", "İptal"),
    "proc.analytics.kind.opex": ("OPEX (operating)", "تشغيلي (OPEX)", "OPEX (işletme)"),
    "proc.analytics.kind.capex": ("CAPEX (capital)", "رأسمالي (CAPEX)", "CAPEX (yatırım)"),
    "proc.analytics.stg.requester": ("Requester", "مقدّم الطلب", "Talep eden"),
    "proc.analytics.stg.hr_head": ("HR Director", "مدير الموارد البشرية", "İK Direktörü"),
    "proc.analytics.stg.warehouse": ("Warehouse", "المخازن", "Depo"),
    "proc.analytics.stg.item_control": ("Item control", "مراقبة الأصناف", "Malzeme Kontrolü"),
    "proc.analytics.stg.warehouse_manager": ("Warehouse Manager", "مدير المخازن", "Depo Müdürü"),
    "proc.analytics.stg.purchasing": ("Procurement Manager", "مدير المشتريات", "Satın Alma Müdürü"),
    "proc.analytics.stg.factory_manager": ("Plant Director", "مدير المصنع", "Fabrika Direktörü"),
    "proc.analytics.stg.scd": ("Supply Chain Director", "مدير سلسلة الإمداد", "Tedarik Zinciri Direktörü"),
    "proc.analytics.stg.finance": ("Financial Director", "المدير المالي للإدارة", "Finans Direktörü"),
    "proc.analytics.stg.control": ("Financial Control", "الرقابة المالية", "Finansal Kontrol"),
    "proc.analytics.stg.cfo": ("CFO", "المدير المالي", "Mali İşler Direktörü"),
    "proc.analytics.stg.ceo": ("CEO", "الرئيس التنفيذي", "Genel Müdür"),
    "proc.analytics.u.days": ("days", "يوم", "gün"),
    "proc.analytics.u.hours": ("h", "س", "sa"),
}
