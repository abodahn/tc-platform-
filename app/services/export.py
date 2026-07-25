"""
Shared dataset export — CSV for Excel, JSON for scanners/BI/scripts.

Every module exposes ONE function:

    export_dataset(key) -> (headers, rows) | (None, None)

and two routes built from the helpers here. That keeps the wire format identical
everywhere, so a factory can pull any dataset the same way instead of learning
eleven conventions.

CSV is written with a UTF-8 BOM (utf-8-sig) because Excel on Windows otherwise
renders Arabic and Turkish as mojibake — the platform is trilingual, so this is a
correctness requirement, not a nicety.
"""
import csv
import io

from flask import jsonify, send_file


def csv_file(headers, rows, filename):
    """Build a downloadable CSV response. `rows` is an iterable of sequences."""
    buf = io.StringIO()
    w = csv.writer(buf)
    if headers:
        w.writerow(headers)
    for r in rows or ():
        w.writerow(["" if v is None else v for v in r])
    data = buf.getvalue().encode("utf-8-sig")     # BOM: Excel + Arabic/Turkish
    return send_file(io.BytesIO(data), mimetype="text/csv",
                     as_attachment=True, download_name=filename)


def json_rows(headers, rows, key=None):
    """Same dataset as JSON: a list of objects keyed by the CSV headers.

    Machine-readable so a barcode scanner, a Power BI query or a script can read
    exactly what the CSV shows without scraping the HTML page."""
    hs = [str(h) for h in (headers or [])]
    out = []
    for r in rows or ():
        out.append({hs[i] if i < len(hs) else f"col{i}": v for i, v in enumerate(r)})
    return jsonify({"dataset": key, "count": len(out), "columns": hs, "rows": out})


def dispatch(export_dataset, key, module, fmt="csv"):
    """Serve `key` from a module's export_dataset() as csv or json.

    Returns (response, 404-ish flag) — the route decides how to abort so each
    module keeps its own error convention."""
    headers, rows = export_dataset(key)
    if headers is None:
        return None
    if fmt == "json":
        return json_rows(headers, rows, key)
    return csv_file(headers, rows, f"{module}-{key}.csv")
