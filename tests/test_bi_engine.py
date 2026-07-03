"""Unit tests for the BI engine (pure functions, no DB): reader, profiler,
analyze, recommender, insights, query."""
import io

from app.services.bi import analyze, insights, profiler, query, reader, recommender

COLS = ["Date", "Region", "Product", "Units", "Revenue"]


def _rows():
    rows = []
    regions = ["North", "South", "East", "West"]
    for i in range(120):
        rows.append([f"2026-{(i % 12) + 1:02d}-15", regions[i % 4],
                     ["A", "B", "C"][i % 3], float(i % 10 + 1), float((i % 10 + 1) * 100 + i)])
    rows[3][4] = None  # a missing value
    return rows


# --- reader ---
def test_reader_csv():
    buf = io.StringIO()
    buf.write("a,b\n1,x\n2,y\n")
    cols, rows = reader.read_bytes(buf.getvalue().encode(), "t.csv")
    # reader keeps CSV cells as raw strings; the profiler infers types
    assert cols == ["a", "b"] and len(rows) == 2 and rows[0][0] == "1"
    assert profiler.parse_number(rows[0][0]) == 1.0


def test_reader_rejects_unknown_type():
    try:
        reader.read_bytes(b"x", "t.pptx")
        assert False
    except reader.ReadError:
        pass


# --- profiler ---
def test_profile_types():
    prof = profiler.profile(COLS, _rows())
    types = {c["name"]: c["type"] for c in prof["columns"]}
    assert types["Date"] == "date"
    assert types["Region"] == "category"
    assert types["Units"] == "number"
    assert types["Revenue"] == "number"


def test_profile_number_stats():
    prof = profiler.profile(COLS, _rows())
    rev = profiler.find_column(prof, "Revenue")
    assert rev["sum"] > 0 and "mean" in rev and "median" in rev


def test_parse_number_variants():
    assert profiler.parse_number("1,234") == 1234.0
    assert profiler.parse_number("$50") == 50.0
    assert profiler.parse_number("(100)") == -100.0
    assert profiler.parse_number("abc") is None


def test_parse_date_variants():
    assert profiler.parse_date("2026-03-01") == "2026-03-01"
    assert profiler.parse_date("01/03/2026") in ("2026-03-01", "2026-01-03")
    assert profiler.parse_date("2026") is None  # bare year is not a date
    assert profiler.parse_date("xyz") is None


def test_data_quality_flags_missing():
    rows = _rows()
    for i in range(40):
        rows[i][1] = None  # 33% missing in Region
    prof = profiler.profile(COLS, rows)
    q = profiler.data_quality(prof, rows)
    assert q["score"] < 100
    assert any(i["column"] == "Region" and i["kind"] == "missing" for i in q["issues"])


# --- analyze ---
def test_aggregate_bar_and_filter():
    rows = _rows()
    prof = profiler.profile(COLS, rows)
    spec = {"type": "bar", "dim": "Region", "measure": "Revenue", "agg": "sum"}
    cd = analyze.chart_data(prof, COLS, rows, spec)
    assert len(cd["labels"]) == 4 and len(cd["datasets"][0]["data"]) == 4
    # filter to North -> fewer rows, KPI shrinks
    total = analyze.kpi_value(COLS, rows, "Units", "sum")
    north = analyze.kpi_value(COLS, analyze.apply_filters(COLS, rows, [{"column": "Region", "op": "=", "value": "North"}]), "Units", "sum")
    assert north < total


def test_line_has_forecast_and_anomalies():
    rows = _rows()
    rows[60][4] = 99999.0  # spike
    prof = profiler.profile(COLS, rows)
    spec = {"type": "line", "dim": "Date", "measure": "Revenue", "agg": "sum"}
    cd = analyze.chart_data(prof, COLS, rows, spec)
    assert cd["kind"] == "line"
    assert cd.get("forecast") and cd["forecast"]["points"]
    assert isinstance(cd.get("anomalies"), list)


def test_forecast_and_outliers_helpers():
    fc = analyze.forecast([1, 2, 3, 4, 5, 6])
    assert fc and len(fc["points"]) >= 1 and fc["points"][0] > 6
    out = analyze.outliers([10, 11, 10, 12, 11, 200, 10, 9])
    assert any(o["value"] == 200 for o in out)


def test_correlations():
    cols = ["x", "y"]
    rows = [[float(i), float(2 * i + 1)] for i in range(20)]
    prof = profiler.profile(cols, rows)
    corr = analyze.correlations(prof, cols, rows)
    assert corr and abs(corr[0]["r"]) > 0.9


# --- recommender ---
def test_recommender_builds_dashboard():
    prof = profiler.profile(COLS, _rows())
    dash = recommender.build_dashboard(prof, "Sales")
    assert dash["kpis"] and dash["charts"]
    types = [c["type"] for c in dash["charts"]]
    assert "line" in types  # date + measure -> trend
    assert any(t in ("bar", "doughnut") for t in types)


# --- insights ---
def test_insights_three_languages():
    prof = profiler.profile(COLS, _rows())
    for lang in ("en", "ar", "tr"):
        ins = insights.generate(prof, COLS, _rows(), lang)
        assert ins and all("text" in i and i["text"] for i in ins)
    # arabic differs from english (localised)
    en = insights.generate(prof, COLS, _rows(), "en")[0]["text"]
    ar = insights.generate(prof, COLS, _rows(), "ar")[0]["text"]
    assert en != ar


# --- query ---
def test_smart_query_shapes():
    prof = profiler.profile(COLS, _rows())
    assert query.parse(prof, "top 3 product by revenue")["type"] == "bar"
    assert query.parse(prof, "revenue over time")["type"] == "line"
    assert query.parse(prof, "average units by region")["agg"] == "avg"
    assert query.parse(prof, "distribution of revenue")["type"] == "histogram"
    assert query.parse(prof, "units vs revenue")["type"] == "scatter"
    assert query.parse(prof, "share by region")["type"] == "doughnut"
    assert query.parse(prof, "total nonsense zzz") is None


def test_count_includes_null_measure_rows():
    # a count-by-dimension chart that carries a measure must count ALL rows,
    # even where the measure cell is null (regression for the undercount bug)
    cols = ["Region", "Revenue"]
    rows = [["N", 10.0], ["N", None], ["S", 5.0]]
    prof = profiler.profile(cols, rows)
    cd = analyze.chart_data(prof, cols, rows,
                            {"type": "bar", "dim": "Region", "measure": "Revenue", "agg": "count"})
    d = dict(zip(cd["labels"], cd["datasets"][0]["data"]))
    assert d["N"] == 2


def test_insight_zero_to_large_is_not_flat():
    cols = ["Date", "Sales"]
    rows = [["2026-01-15", 0.0], ["2026-02-15", 0.0], ["2026-03-15", 5000.0], ["2026-04-15", 8000.0]]
    prof = profiler.profile(cols, rows)
    txt = " ".join(i["text"] for i in insights.generate(prof, cols, rows, "en")).lower()
    assert "stable" not in txt  # a real 0 -> large growth, not "broadly stable"
