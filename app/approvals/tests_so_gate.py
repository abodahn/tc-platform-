"""DOAM §5 / Table 12 — the sales-order gate, proved on the REAL flow.

Audit finding 3.4-b2 had two halves and this file drives both through the entry
points the web UI uses: create_pr(..., priced=False) with no prices at all (the
requester price lockout), submit_pr(), and price_pr() at the pricing gate.

    (a) NOT VALIDATED — the field is a free-text datalist, so "x", "-" or "n/a"
        satisfied "a valid client sales order reference".
    (b) TRIGGER TOO NARROW — twelve substrings of Table 12's prose; an auditor
        walked 14 of 17 real apparel direct materials past it.

Both directions are checked: direct materials must be caught, and MRO / IT /
facility purchases (which legitimately carry a cost centre) must not be.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "sogate.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


# The auditor's 17 direct materials. The first ten are the ones named in the
# finding; the last three are what the old twelve keywords already caught.
DIRECT_MATERIALS = [
    "Zippers #5 YKK for style 4471",
    "Sewing thread 40/2 tex",
    "Buttons 4-hole 18L",
    "Interlining fusible",
    "Cotton greige 60s",
    "Denim rolls 12oz",
    "Care labels + hangtags",
    "Cartons and polybags",
    "Direct materials for SO 9001",
    "Raw material purchase - production",
    "Elastic waistband tape 40mm",
    "Woven main labels and size labels",
    "Poly bags and hangers for finishing",
    "Velcro hook and loop 25mm",
    "Cotton twill fabric 100% CO",
    "Reactive dyes for the wash plant",
    "Polyester yarn 150D",
]

# Purchases that must NOT be dragged into the gate: they carry a cost centre.
# Half of these are deliberate near-misses on the new keywords — WASHERS,
# THREADED rod, PUSH BUTTONS, the PRINTER, ZIP ties, a LABEL printer.
NOT_DIRECT = [
    "Bearing 6204 for sewing line 3",
    "Toner cartridge for the HP printer",
    "Flat washers M8 stainless",
    "Push button switch 22mm for the control panel",
    "Threaded rod M10 with nuts",
    "Laptop Dell Latitude for the IT department",
    "Cleaning supplies for the washroom",
    "Air filter for the compressor",
    "Zip ties 200mm for cable management",
    "Label printer ribbon for the stores desk",
    "Fire extinguisher annual refill",
    "Diesel for the standby generator",
]


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc

        tech = {"username": "tech", "id": 9}
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def raise_pr(title, so=None):
            """A request exactly as the UI makes one: no prices anywhere."""
            header = {"title": title, "department": "Production"}
            if so is not None:
                header["so_no"] = so
            pr_id, _ = svc.create_pr(header, [{"item": title, "qty": 10,
                                               "unit_price": 0}],
                                     tech, submit=False, priced=False)
            return pr_id

        def submit(title, so=None):
            pr_id = raise_pr(title, so)
            ok, msg = svc.submit_pr(pr_id, tech)
            return pr_id, ok, msg

        # A real open order and a closed one to test the reference against.
        conn = get_db()
        live = conn.execute(
            "SELECT order_no FROM ord_orders WHERE status NOT IN ('closed','cancelled') "
            "ORDER BY id LIMIT 1").fetchone()
        assert live, "no open sales order on file — the fixture cannot run"
        LIVE_SO = live["order_no"]
        conn.execute("INSERT INTO ord_orders (order_no, buyer, qty, status, created_by) "
                     "VALUES (?,?,?,?,?)", ("SO-CLOSED-1", "Old buyer", 100, "closed", "test"))
        conn.commit()
        conn.close()

        # ---- (b) TRIGGER: every direct material must demand a sales order ----
        caught, missed = 0, []
        for text in DIRECT_MATERIALS:
            _, ok, msg = submit(text)
            if not ok and msg == "cost_object_required":
                caught += 1
            else:
                missed.append((text, ok, msg))
        print("direct materials caught: %d/%d" % (caught, len(DIRECT_MATERIALS)))
        assert not missed, "these went through with NO sales order: %s" % missed

        # ---- (4) ...and MRO / IT / facility must still get through -----------
        clear, wrongly = 0, []
        for text in NOT_DIRECT:
            _, ok, msg = submit(text)
            if ok:
                clear += 1
            else:
                wrongly.append((text, msg))
        print("MRO/IT/facility cleared: %d/%d" % (clear, len(NOT_DIRECT)))
        assert not wrongly, "the gate got greedy and blocked cost-centre spend: %s" % wrongly

        # ---- (a) VALIDATION: junk in the free-text field is not a reference --
        for junk in ("x", "-", "n/a", "N/A", "tbc", "SO-0000"):
            _, ok, msg = submit("Cotton twill fabric 100% CO", junk)
            assert not ok and msg == "so_unknown", (
                "%r satisfied the sales-order gate: %s / %s" % (junk, ok, msg))

        _, ok, msg = submit("Cotton twill fabric 100% CO", "SO-CLOSED-1")
        assert not ok and msg == "so_closed", (
            "a closed order was accepted as a cost object: %s / %s" % (ok, msg))

        # ...but a real, open order is accepted — including sloppy typing.
        for good in (LIVE_SO, "  " + LIVE_SO.lower() + " "):
            _, ok, msg = submit("Cotton twill fabric 100% CO", good)
            assert ok, "the live order %r was refused: %s" % (good, msg)

        # A blank sales order on an MRO request is untouched by any of this.
        _, ok, msg = submit("Bearing 6204 for sewing line 3", "")
        assert ok, msg

        # A line linked to a maintenance SPARE is MRO by master data, whatever it
        # is called — this is the maintenance auto-reorder path, and "thread
        # guide" is a real sewing-machine spare.
        conn = get_db()
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, is_active) "
                     "VALUES (?,?,?,1)", ("SP-TG", "Thread guide bracket", 2))
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                           ("SP-TG",)).fetchone()["id"]
        conn.commit()
        conn.close()
        spare_pr, _ = svc.create_pr(
            {"title": "Auto reorder — Thread guide bracket",
             "request_for": "Maintenance spare-part replenishment",
             "department": "Maintenance"},
            [{"item": "SP-TG — Thread guide bracket", "qty": 4, "unit_price": 0,
              "spare_id": sid}], tech, submit=False, priced=False)
        ok, msg = svc.submit_pr(spare_pr, tech)
        assert ok, "the maintenance auto-reorder was sent away for a sales order: %s" % msg

        # ---- the whole real journey: unpriced -> refused -> fixed -> priced --
        pr_id = raise_pr("Denim rolls 12oz", "n/a")
        ok, msg = svc.submit_pr(pr_id, tech)
        assert not ok and msg == "so_unknown", (ok, msg)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET so_no=? WHERE id=?", (LIVE_SO, pr_id))
        conn.commit()
        conn.close()
        ok, msg = svc.submit_pr(pr_id, tech)
        assert ok, "fixing the sales order did not unblock the request: %s" % msg
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        conn.close()
        ok, msg = svc.price_pr(pr_id, {li: 400}, {"tax_rate": 14}, buyer)
        assert ok, "pricing a properly costed request failed: %s" % msg

        # ---- the refusal has to be readable, and name the DOAM's alternative -
        for lang in ("en", "ar", "tr"):
            ui = svc.labels(lang)["ui"]
            for key in ("cost_object_required_flash", "so_unknown_flash",
                        "so_closed_flash"):
                assert ui.get(key), "%s missing in %s" % (key, lang)
        from app.routes import approvals as rt
        with app.test_request_context("/procurement/new"):
            for code in ("cost_object_required", "so_unknown", "so_closed"):
                text = rt._submit_error(code)
                assert "(" + code + ")" not in text, code
                assert "forecast" in text, (
                    "the refusal must name the DOAM's alternative: %s" % code)

        print("PASS: %d/%d direct materials gated, %d/%d cost-centre purchases free, "
              "junk and closed orders refused" % (caught, len(DIRECT_MATERIALS),
                                                  clear, len(NOT_DIRECT)))
        return True


if __name__ == "__main__":
    run()
