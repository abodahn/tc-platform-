"""Five request fields an admin can switch off, without losing anything.

Not every plant uses expenditure type, sales orders, forecasts, cost centres or
delivery conditions, and a field nobody fills is a field people learn to skip
past. So each can be hidden from Procurement -> Settings.

Two things this file exists to hold:

  * HIDING IS NOT DELETING. A hidden field is not rendered, so the browser posts
    nothing for it — and an edit would then write "" over whatever the request
    already carried. A sales order erased by an admin flipping a display switch
    is silent data loss, so the stored value wins over the empty post.
  * HIDING IS NOT SWITCHING A CONTROL OFF. The DOAM 3.4 cost-object gate still
    applies to a request whose sales-order box is hidden. That is deliberate and
    it is why each switch prints its own consequence next to it: an admin should
    read what it costs before flipping it, not after.

    python app/approvals/tests_optional_fields.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="optfld_"), "f.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    ok_all = [True]

    def chk(label, cond, extra=""):
        ok_all[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label
              + ((" | " + str(extra)) if extra else ""))

    from app.approvals import constants as C

    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = {"username": "admin", "role": "super_admin", "id": 1}
        s["uid"] = 1

    def token(url="/procurement/new"):
        m = re.search(r'name="_csrf" value="([^"]+)"',
                      c.get(url).get_data(as_text=True))
        return m.group(1) if m else ""

    def set_switch(key, on):
        tok = token("/procurement/workflow")
        r = c.post("/procurement/workflow/setting",
                   data={"key": key, "value": "1" if on else "0", "_csrf": tok})
        return r.status_code

    # ---- 1. every switch is real and defaults to ON ------------------------
    print("the switches exist, and default to how the form always looked")
    with app.app_context():
        from app.approvals import services as svc
        vis = svc.visible_fields()
    chk("all five fields are known", len(C.OPTIONAL_FIELDS) == 5,
        [k for k, _l, _w in C.OPTIONAL_FIELDS])
    chk("and every one defaults to VISIBLE", all(vis.values()), vis)
    for key, _label, warn in C.OPTIONAL_FIELDS:
        chk("%-26s says what hiding it costs" % key, bool(warn and warn.strip()))

    html = c.get("/procurement/new").get_data(as_text=True)
    for name in ("expenditure_kind", "so_no", "forecast_ref", "cost_center",
                 "delivery_condition"):
        chk("%-20s is on the form by default" % name, ('name="%s"' % name) in html)

    # ---- 2. the label change -----------------------------------------------
    print("\nthe box that asked for a Title asks for a Reason")
    chk("the form says Reason, not Title",
        "Reason" in html and ">Title *<" not in html)

    # ---- 3. switching one off hides it --------------------------------------
    print("\nswitching one off takes it off the form")
    chk("the setting saves", set_switch("show_sales_order", False) in (200, 302))
    html2 = c.get("/procurement/new").get_data(as_text=True)
    chk("the sales-order box is gone", 'name="so_no"' not in html2)
    chk("and the others are untouched",
        all(('name="%s"' % n) in html2 for n in
            ("expenditure_kind", "forecast_ref", "cost_center", "delivery_condition")))

    # ---- 4. THE ONE THAT MATTERS: hiding does not erase ---------------------
    print("\nhiding a field does not erase what a request already carries")
    set_switch("show_sales_order", True)
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        # The register the §3.4 gate actually reads is ord_orders.
        conn.execute("INSERT OR IGNORE INTO ord_orders (order_no, status) "
                     "VALUES ('SO-KEEP-1','open')")
        conn.commit()
        conn.close()
        pid, _no = svc.create_pr(
            {"title": "Keeps its sales order", "department": "Production",
             "currency": "EGP", "so_no": "SO-KEEP-1", "cost_center": "CC-9"},
            [{"item": "Bearing 6204", "unit": "Pcs", "qty": 2, "unit_price": 0}],
            {"username": "admin", "id": 1}, priced=False, submit=False)

    set_switch("show_sales_order", False)
    # An edit posted from a form that no longer renders the sales-order box.
    tok = token("/procurement/pr/%d/edit" % pid)
    r = c.post("/procurement/pr/%d/edit" % pid, data={
        "title": "Keeps its sales order", "department": "Production",
        "currency": "EGP", "cost_center": "CC-9",
        "item[]": "Bearing 6204", "description[]": "", "unit[]": "Pcs",
        "qty[]": "2", "current_stock[]": "0", "unit_price[]": "0",
        "item_notes[]": "", "spare_id[]": "", "item_id[]": "", "vendor[]": "",
        "_csrf": tok})
    chk("the edit was accepted", r.status_code in (200, 302), r.status_code)

    with app.app_context():
        from app.db import get_db
        conn = get_db()
        row = dict(conn.execute(
            "SELECT so_no, cost_center FROM pr_requests WHERE id=?", (pid,)).fetchone())
        conn.close()
    chk("the SALES ORDER survived an edit made while it was hidden",
        row["so_no"] == "SO-KEEP-1", repr(row["so_no"]))
    chk("and the visible field still saved normally",
        row["cost_center"] == "CC-9", repr(row["cost_center"]))

    # and it comes back when unhidden
    # The request PAGE is where the switch is meant to show, so assert there —
    # the edit form has moved on by now (submitting it left draft behind).
    hidden_page = c.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("while hidden, the request page does not show the sales order",
        "SO-KEEP-1" not in hidden_page)
    set_switch("show_sales_order", True)
    shown_page = c.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("unhiding shows it again — the value was on the record all along",
        "SO-KEEP-1" in shown_page)

    # ---- 5. the gate is NOT switched off by hiding its field ----------------
    print("\nhiding the field does not switch the control off")
    set_switch("show_sales_order", False)
    set_switch("show_forecast_ref", False)
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        ok_gate, reason = svc.cost_object_check(
            conn, pid, conn.execute("SELECT * FROM pr_requests WHERE id=?",
                                    (pid,)).fetchone())
        conn.close()
    chk("a request that HAS a sales order still passes the gate", ok_gate, reason)

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        pid2, _ = svc.create_pr(
            {"title": "No cost object at all", "department": "Production",
             "currency": "EGP"},
            [{"item": "Woven label roll", "unit": "Roll", "qty": 5, "unit_price": 0}],
            {"username": "admin", "id": 1}, priced=False, submit=False)
        conn = get_db()
        ok2, reason2 = svc.cost_object_check(
            conn, pid2, conn.execute("SELECT * FROM pr_requests WHERE id=?",
                                     (pid2,)).fetchone())
        conn.close()
    chk("and one with none is STILL refused — the gate is untouched",
        not ok2, reason2)

    for key, _l, _w in C.OPTIONAL_FIELDS:
        set_switch(key, True)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
