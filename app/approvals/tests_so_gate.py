# -*- coding: utf-8 -*-
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
    # A second audit POSTed 26 unremarkable apparel lines an English-speaking
    # merchandiser would actually type; 22 of them reached 'pending' with no cost
    # object. The first sweep scored the keyword list against words drawn from
    # the keyword list, so it measured the vocabulary against itself.
    "Rib 2x1 collar cuff",
    "Nylon taffeta 210T",
    "Elastane spandex 40D",
    "Bias binding 20mm",
    "Ribbon grosgrain 15mm",
    "Piping cord 4mm",
    "Snaps and rivets brass",
    "Bra hook and eye tape",
    "Shoulder pad set",
    "Buckram stiffener",
    "Sequin panel for SS26",
    "Foam sheet 3mm bra cup",
    "Cotton sliver for spinning",
    "PU coated shell 150gsm",
    "Roll goods 60s CVC",
    "Style 4471 material buy",
    "Tissue paper for packing",
    "Silica gel sachets",
    "Barcode stickers for cartons",
    "Neck board and butterfly",
    "Collar bone and back board",
    # The exemption list became the way around the string it was tested on:
    # "Buttons 4-hole 18L" is caught, and the same order with the word "push"
    # bolted on the front was not.
    "Push button 4-hole 18L for shirts",
]

# The same purchases, typed the way an Arabic- or Turkish-speaking requester
# types them. Every refusal message in this module was translated into both
# languages, so those requesters are expected by design — and an English-only
# keyword list refuses the English speaker and waves through the identical
# purchase in Arabic. All seven of these used to reach 'pending'.
DIRECT_MATERIALS_INTL = [
    "أقمشة قطن 100% للطلبية 4471",
    "خيوط خياطة 40/2",
    "سوستة معدنية مقاس 5",
    "Pamuklu kumaş 100% CO",
    "Dikiş ipliği 40/2",
    "Fermuar #5 YKK",
    "Etiket ve askı kartı",
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
    # near-misses on the widened list: SNAP rings, BRAKE pads, POWER cords,
    # BUTTERFLY valves and a RIB-joint plier are all MRO nouns.
    "Snap ring 25mm for the drive shaft",
    "Brake pad set for the forklift",
    "Power cord IEC C13 2m",
    "Butterfly valve 2 inch for the boiler line",
    "Rib joint pliers 250mm",
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
        ALL_DIRECT = DIRECT_MATERIALS + DIRECT_MATERIALS_INTL
        caught, missed = 0, []
        for text in ALL_DIRECT:
            _, ok, msg = submit(text)
            if not ok and msg == "cost_object_required":
                caught += 1
            else:
                missed.append((text.encode("unicode_escape").decode(), ok, msg))
        print("direct materials caught: %d/%d (%d of them AR/TR)"
              % (caught, len(ALL_DIRECT), len(DIRECT_MATERIALS_INTL)))
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
        # The spec is master data too, and a sewing-machine spare legitimately
        # carries trigger words in it.
        conn.execute("INSERT INTO mnt_spare_parts (code, name, spec, stock_qty, "
                     "is_active) VALUES (?,?,?,?,1)",
                     ("SP-TG", "Thread guide bracket",
                      "guide for the washing line feeder", 2))
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                           ("SP-TG",)).fetchone()["id"]
        conn.commit()
        conn.close()
        def bridge_pr(description=None):
            """Byte for byte what procure_bridge._raise_pr posts."""
            pr, _ = svc.create_pr(
                {"title": "Auto reorder — Thread guide bracket",
                 "request_for": "Maintenance spare-part replenishment",
                 "department": "Maintenance"},
                [{"item": "SP-TG — Thread guide bracket", "qty": 4,
                  "unit_price": 0, "spare_id": sid,
                  "description": description}], tech, submit=False, priced=False)
            return pr, svc.submit_pr(pr, tech)

        spare_pr, (ok, msg) = bridge_pr("guide for the washing line feeder")
        assert ok, "the maintenance auto-reorder was sent away for a sales order: %s" % msg

        # ...but only the master's own words are master data. The same line with
        # fabric appended to the real spec is the requester talking.
        _, (ok, msg) = bridge_pr("guide for the washing line feeder plus "
                                 "cotton twill fabric 100% CO")
        assert not ok and msg == "cost_object_required", (
            "free text appended to a master spec rode in on the spare link: "
            "%s / %s" % (ok, msg))

        # ---- ...but that link is a HIDDEN FORM FIELD, not master data --------
        # spare_id[] is a plain hidden input posted by the requester, stored with
        # no foreign key anywhere in this codebase. Setting it used to switch the
        # whole gate off: a real id, 999999 and 0007 all routed a fabric request
        # into the ladder with no sales order and no forecast.
        FABRIC = "Cotton twill fabric 100% CO for style 4471"
        conn = get_db()
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, is_active) "
                     "VALUES (?,?,?,1)", ("SP-REAL", "Needle bar", 5))
        real_sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                                ("SP-REAL",)).fetchone()["id"]
        mx = conn.execute("SELECT COALESCE(MAX(id),0) AS m "
                          "FROM mnt_spare_parts").fetchone()["m"]
        conn.commit()
        conn.close()

        BARE = "SP-REAL — Needle bar"

        def spare_submit(spare_id, item=FABRIC, description=None, title=FABRIC):
            pr, _ = svc.create_pr({"title": title, "department": "Production"},
                                  [{"item": item, "description": description,
                                    "qty": 10, "unit_price": 0,
                                    "spare_id": spare_id}],
                                  tech, submit=False, priced=False)
            return pr, svc.submit_pr(pr, tech)

        for bogus in (999999, str(mx + 7).zfill(5), mx + 1):
            pr, (ok, msg) = spare_submit(bogus)
            assert not ok and msg == "cost_object_required", (
                "spare_id=%r switched the DOAM cost-object gate off: %s / %s"
                % (bogus, ok, msg))
            conn = get_db()
            stored = conn.execute("SELECT spare_id FROM pr_items WHERE pr_id=?",
                                  (pr,)).fetchone()["spare_id"]
            conn.close()
            assert stored is None, (
                "a spare_id that is not a spare was persisted: %r" % stored)

        # ...and it does not even need devtools. The type-ahead clears spare_id
        # only when item[] is retyped, so picking any spare, leaving the item
        # text alone and putting the real intent in description[] did it from
        # the browser. Master data may beat free text; it may not carry it, in
        # ANY of the three boxes the requester owns.
        for where, kw in (("title", {"title": FABRIC, "item": BARE}),
                          ("item", {"title": BARE, "item": FABRIC}),
                          ("description", {"title": BARE, "item": BARE,
                                           "description": FABRIC})):
            pr, (ok, msg) = spare_submit(real_sid, **kw)
            assert not ok and msg == "cost_object_required", (
                "a real spare link smuggled direct material past the gate in "
                "%s[]: %s / %s" % (where, ok, msg))

        # ...while the words that came from the MASTER still get through. This
        # is the maintenance auto-reorder's own wording, spare name and all.
        _, (ok, msg) = spare_submit(real_sid, title="Auto reorder — Needle bar",
                                    item=BARE)
        assert ok, "the master data's own name was read as free text: %s" % msg

        # ---- SoD: the drafter of a forecast may not also agree it ------------
        # A forecast is now a full alternative to the sales order, and can_act()
        # grants a standing proc_admin override — so without this one holder of
        # proc_admin could invent a cost object and spend against it alone.
        padmin = {"username": "padmin", "role": "it_director", "id": 77}   # holds proc_admin
        scd = {"username": "scd2", "role": "supply_chain_director", "id": 78}
        ok, ref = svc.create_forecast({"ref": "FC-SELF"}, padmin)
        assert ok, ref
        conn = get_db()
        fid = conn.execute("SELECT id FROM proc_forecasts WHERE ref=?",
                           ("FC-SELF",)).fetchone()["id"]
        conn.close()
        ok, msg = svc.agree_forecast(fid, padmin)
        assert not ok and msg == "own_forecast", (
            "the drafter agreed their own forecast: %s / %s" % (ok, msg))
        ok, msg = svc.agree_forecast(fid, scd)
        assert ok, "a second signatory could not agree the forecast: %s" % msg

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

        # ---- an EMPTY order register is not a reason to skip the check -------
        # That is the state of a freshly deployed production database, i.e.
        # precisely when the golden thread most needs protecting, and it was
        # invisible in dev because create_and_seed seeds three demo orders. The
        # old rationale ("nothing the requester could type") died the day the
        # forecast register landed.
        conn = get_db()
        conn.execute("DELETE FROM ord_orders")
        conn.commit()
        conn.close()
        assert svc.sales_order_state("SO-1001") == "unknown"
        _, ok, msg = submit("Cotton twill fabric 100% CO", "n/a")
        assert not ok and msg == "so_unknown", (
            "with the order register empty, 'n/a' was accepted as a cost "
            "object: %s / %s" % (ok, msg))
        _, ok, msg = submit("Cotton twill fabric 100% CO")
        assert not ok and msg == "cost_object_required", (ok, msg)

        # The ONE remaining escape, asserted so it stays visible: the orders
        # module is not installed at all, so there is no register to check
        # against and refusing every direct material would be wrong.
        conn = get_db()
        conn.execute("ALTER TABLE ord_orders RENAME TO ord_orders_absent")
        conn.commit()
        conn.close()
        assert svc.sales_order_state("totally-made-up-ref") is None
        conn = get_db()
        conn.execute("ALTER TABLE ord_orders_absent RENAME TO ord_orders")
        conn.commit()
        conn.close()

        # ---- the refusal has to be readable, and name the DOAM's alternative -
        for lang in ("en", "ar", "tr"):
            ui = svc.labels(lang)["ui"]
            for key in ("cost_object_required_flash", "so_unknown_flash",
                        "so_closed_flash", "own_forecast_flash"):
                assert ui.get(key), "%s missing in %s" % (key, lang)
        from app.routes import approvals as rt
        with app.test_request_context("/procurement/new"):
            for code in ("cost_object_required", "so_unknown", "so_closed"):
                text = rt._submit_error(code)
                assert "(" + code + ")" not in text, code
                assert "forecast" in text, (
                    "the refusal must name the DOAM's alternative: %s" % code)

        print("PASS: %d/%d direct materials gated (%d AR/TR), %d/%d cost-centre "
              "purchases free, forged spare links refused, junk / closed / "
              "empty-register references refused"
              % (caught, len(ALL_DIRECT), len(DIRECT_MATERIALS_INTL),
                 clear, len(NOT_DIRECT)))
        return True


if __name__ == "__main__":
    run()
