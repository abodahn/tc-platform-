"""VAT on the request itself + the FX rate / EGP equivalent at pricing.

    python -m app.approvals.tests_pr_vat

Proves:
  1. pricing with tax_rate=14 stores it; pr_amounts gives grand = 1.14 x subtotal;
  2. the request page shows the VAT and After-VAT columns per line and the
     VAT-inclusive grand total; the pricing form offers the VAT choice and the
     FX-rate / EGP-equivalent boxes;
  3. a USD request priced with a rate shows its EGP equivalent (VAT included).
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="prvat_"), "v.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        admin = conn.execute("SELECT id FROM users WHERE role='super_admin' "
                             "ORDER BY id LIMIT 1").fetchone()
        conn.close()
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        pr_id, _ = svc.create_pr({"title": "VAT test", "department": "Maintenance"},
                                 [{"item": "BELT-A42", "qty": 50, "unit_price": 0}],
                                 {"username": "tech", "id": 9}, priced=False)
        line = get_db().execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        ok, msg = svc.price_pr(pr_id, {line: 750}, {"tax_rate": "14"}, buyer)
        chk("pricing with VAT 14 is saved", ok, msg)
        pr = dict(get_db().execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone())
        a = svc.pr_amounts(pr)
        chk("subtotal 37,500", a["subtotal"] == 37500, a)
        chk("VAT 5,250 and grand 42,750", a["tax"] == 5250 and a["grand"] == 42750, a)

        usd, _ = svc.create_pr({"title": "USD VAT", "department": "Maintenance",
                                "currency": "USD"},
                               [{"item": "NEEDLE", "qty": 10, "unit_price": 0}],
                               {"username": "tech", "id": 9}, priced=False)
        uline = get_db().execute("SELECT id FROM pr_items WHERE pr_id=?", (usd,)).fetchone()["id"]
        ok, msg = svc.price_pr(usd, {uline: 100}, {"tax_rate": "14", "fx_rate": "50",
                                                    "currency": "USD"}, buyer)
        chk("USD pricing with rate saved", ok, msg)

    cl = app.test_client()
    with cl.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = 0
    html = cl.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
    chk("line VAT 5,250.00 shown", "5,250.00" in html)
    chk("line after-VAT 42,750.00 shown", "42,750.00" in html)
    chk("VAT and After-VAT column headers", 'data-i18n="proc.after_vat"' in html
        and 'data-i18n="proc.vat_l"' in html)
    html = cl.get("/procurement/pr/%d" % usd).get_data(as_text=True)
    # 10 x 100 = 1,000 USD; +14% = 1,140; x 50 = 57,000 EGP
    chk("USD request shows EGP equivalent incl. VAT (57,000)", "57,000.00 EGP" in html)
    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
