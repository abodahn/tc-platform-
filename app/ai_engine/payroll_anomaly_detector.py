"""Payroll intelligence — old-vs-new sheet comparison + anomaly detection."""
from app.ai_engine.base import finding, safe_rows, zscore_anomalies


def predict(conn):
    out = []
    new = [dict(r) for r in safe_rows(conn, "SELECT * FROM payroll_rows WHERE sheet_version='new'")]
    old = {r["employee_code"]: r for r in
           [dict(x) for x in safe_rows(conn, "SELECT * FROM payroll_rows WHERE sheet_version='old'")]}
    if not new:
        return out

    nets = [r.get("net") or 0 for r in new]
    z_by_index = {i: zz for i, _, zz in zscore_anomalies(nets, threshold=2.5)}

    seen = {}
    for i, r in enumerate(new):
        code = r.get("employee_code")
        if code in seen:
            out.append(finding(
                "payroll", f"Duplicate employee row · {code}", 66,
                entity_type="payroll", entity_ref=code,
                impact="Risk of double payment.",
                recommendation="Remove the duplicate row before approving the sheet.",
                responsible="Finance / Payroll",
                explanation="Employee code appears more than once in the new sheet.", kind="duplicate"))
        seen[code] = True

        net = r.get("net") or 0
        risk = 0.0
        why = []
        if net <= 0:
            risk += 55; why.append("net pay is zero or negative")
        if i in z_by_index:
            risk += 40; why.append(f"net is a statistical outlier (z={z_by_index[i]})")
        o = old.get(code)
        if o:
            onet = o.get("net") or 0
            if onet and abs(net - onet) / max(onet, 1) >= 0.25:
                risk += 35; why.append(f"net changed {((net - onet) / onet * 100):+.0f}% vs last month")
            oded = o.get("deductions") or 0
            if oded > 0 and (r.get("deductions") or 0) > oded * 1.5:
                risk += 20; why.append("deductions jumped more than 50%")
        else:
            risk += 20; why.append("no matching row in last month's sheet")
        if risk >= 41:
            out.append(finding(
                "payroll", f"Payroll anomaly · {code} {r.get('name')}", risk,
                entity_type="payroll", entity_ref=code,
                impact="Wrong pay, employee disputes, or a compliance issue.",
                recommendation="Verify the row and the underlying formula before final approval.",
                responsible="Finance / Payroll", explanation="; ".join(why), kind="payroll_anomaly"))

    new_codes = {r.get("employee_code") for r in new}
    for code, o in old.items():
        if code not in new_codes:
            out.append(finding(
                "payroll", f"Employee missing from new sheet · {code}", 60,
                entity_type="payroll", entity_ref=code,
                impact="The employee may be left unpaid this period.",
                recommendation="Confirm whether the employee left or was omitted by mistake.",
                responsible="Finance / Payroll",
                explanation="Present in last month's sheet, absent this month.", kind="missing"))
    return out
