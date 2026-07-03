"""
TC Platform — BI & Analytics engine (Phase 6).

Turns any uploaded CSV/Excel file (or a live system dataset) into an instant,
interactive dashboard: KPIs, charts, plain-language insights, anomaly flags and
a simple forecast — all computed in pure Python (standard library + openpyxl),
so it runs fully offline on any machine with no heavy dependencies.

Modules
-------
reader      read CSV / Excel -> (columns, rows)
profiler    columns+rows -> per-column profile (type, stats) + data-quality
analyze     aggregation engine (chart data, cross-filter, outliers, forecast)
recommender profile -> a proposed dashboard (KPIs + charts)
insights    profile+data -> plain-language insights (EN / AR / TR)
query       "top 10 x by y" smart-search string -> a chart spec
store       persistence for datasets / dashboards / alerts / digests
"""
