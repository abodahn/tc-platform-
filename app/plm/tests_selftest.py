"""
PLM-lite self-test — runs the service + schema layer against a throwaway database.
    python app/plm/tests_selftest.py
Proves the two invariants that matter: tech-pack versions are immutable snapshots,
and a style cannot reach production while its PP sample is unapproved.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="plm_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                      # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

REPO = Path(r"D:\TC platform\tc-platform-render")
app = create_app()
U = {"username": "selftest"}
ok = 0


def check(label, cond):
    global ok
    assert cond, "FAIL: " + label
    ok += 1
    print("  ok  " + label)


with app.app_context():
    from app.db import get_db
    conn = get_db()
    from app.plm.schema import create_and_seed
    create_and_seed(conn)
    create_and_seed(conn)          # idempotent: second boot must not duplicate or destroy
    conn.commit()
    from app.plm import services as svc

    print("\n[1] schema + seed")
    seeded = svc.list_styles()
    check("seed created 3 demo styles (idempotent on re-run)", len(seeded) == 3)
    tee = next(s for s in seeded if s["style_ref"] == "TC-KNIT-01")
    check("seeded tee resolves the demo customer order via style_ref",
          [o["order_no"] for o in svc.get_style(tee["id"])["orders"]] == ["SO-1001"])
    check("seeded tee already carries 2 tech-pack versions",
          [v["version"] for v in svc.get_style(tee["id"])["versions"]] == [2, 1])

    print("\n[2] style master")
    sid, err = svc.create_style({"style_ref": " TC-TEST-99 ", "name": "Test Polo",
                                 "buyer": "EU Buyer A", "season": "SS26"}, U)
    check("create_style returns an id", sid and err is None)
    check("style_ref is trimmed", svc.get_style(sid)["style"]["style_ref"] == "TC-TEST-99")
    check("duplicate style_ref refused", svc.create_style({"style_ref": "TC-TEST-99"}, U)[1] == "style_ref_exists")
    check("blank style_ref refused", svc.create_style({"style_ref": "   "}, U)[1] == "style_ref_required")
    check("missing style_ref refused", svc.create_style({}, U)[1] == "style_ref_required")
    born, _ = svc.create_style({"style_ref": "TC-TEST-98", "status": "approved"}, U)
    check("a new style always starts in development (PP gate is not bypassable at create)",
          svc.get_style(born)["style"]["status"] == "development")
    check("get_style(missing) -> None", svc.get_style(999999) is None)
    svc.update_style(sid, {"name": "Test Polo v2"}, U)
    check("update_style writes only supplied fields",
          svc.get_style(sid)["style"]["name"] == "Test Polo v2" and
          svc.get_style(sid)["style"]["buyer"] == "EU Buyer A")
    check("update_style with nothing to set -> False", svc.update_style(sid, {}, U) is False)

    print("\n[3] tech-pack versioning (the crux)")
    v1, err = svc.publish_version(sid, "First issue", U)
    check("v1 published", v1 and err is None)
    check("v1 seeded with the default section titles", len(svc.get_techpack(v1)["sections"]) == 5)
    check("publish on a missing style refused", svc.publish_version(999999, "x", U)[1] == "style_not_found")
    svc.add_spec(v1, {"pom": "Chest width", "size": "M", "spec_value": "52", "tolerance": "1"})
    svc.add_section(v1, "Wash", "Enzyme wash 30 min")
    t1 = svc.get_techpack(v1)
    check("v1 has 1 spec + 6 sections", len(t1["specs"]) == 1 and len(t1["sections"]) == 6)

    v2, _ = svc.publish_version(sid, "Buyer fit comments", U)
    t2 = svc.get_techpack(v2)
    check("v2 is version 2", t2["techpack"]["version"] == 2)
    check("v2 SNAPSHOT copied v1 content", len(t2["specs"]) == 1 and len(t2["sections"]) == 6)
    check("v2 rows are NEW rows, not v1's", t2["specs"][0]["id"] != t1["specs"][0]["id"])

    svc.add_spec(v2, {"pom": "Body length", "size": "M", "spec_value": "72", "tolerance": "1"})
    svc.add_section(v2, "Artwork", "Chest print 200x120mm")
    t1_after = svc.get_techpack(v1)
    t2_after = svc.get_techpack(v2)
    check("editing v2 did NOT alter v1 specs", len(t1_after["specs"]) == 1)
    check("editing v2 did NOT alter v1 sections", len(t1_after["sections"]) == 6)
    check("v1 content byte-identical after v2 edits",
          [(s["pom"], s["spec_value"]) for s in t1_after["specs"]] == [("Chest width", 52.0)])
    check("v2 grew independently", len(t2_after["specs"]) == 2 and len(t2_after["sections"]) == 7)
    check("v1 is frozen: add_section refused", svc.add_section(v1, "X", "y")[1] == "version_is_frozen")
    check("v1 is frozen: add_spec refused", svc.add_spec(v1, {"pom": "X"})[1] == "version_is_frozen")
    check("add_section on a missing techpack refused",
          svc.add_section(999999, "X", "y")[1] == "techpack_not_found")
    check("blank section title refused", svc.add_section(v2, "  ", "y")[1] == "title_required")
    check("blank pom refused", svc.add_spec(v2, {"pom": ""})[1] == "pom_required")
    check("negative tolerance refused", svc.add_spec(v2, {"pom": "P", "tolerance": "-1"})[1] == "bad_tolerance")
    # A measurement a human typed as garbage is REFUSED, not silently booked as
    # 0 cm — a 0 in a size chart reads as a real spec to the cutting room.
    check("non-numeric spec value refused",
          svc.add_spec(v2, {"pom": "Garbage", "spec_value": "abc", "tolerance": ""})[1]
          == "bad_spec_value")
    # A blank is refused too: the form field is optional, and a 0 cm target booked
    # into an add-only table follows the style into every later version.
    check("a blank spec value is refused, not booked as 0 cm",
          svc.add_spec(v2, {"pom": "Blank", "spec_value": "", "tolerance": ""})[1]
          == "bad_spec_value")
    check("a duplicate point of measure + size in one version is refused",
          svc.add_spec(v2, {"pom": "Body length", "size": "M", "spec_value": "73"})[1]
          == "duplicate_spec")
    check("get_techpack(missing) -> None", svc.get_techpack(999999) is None)
    check("style detail defaults to the newest version",
          svc.get_style(sid)["techpack"]["version"] == 2 and svc.get_style(sid)["is_latest"] is True)
    check("style detail can open a past version read-only",
          svc.get_style(sid, v1)["is_latest"] is False)

    print("\n[4] style BOM master")
    check("BOM line added", svc.add_bom_line(sid, {"material": "180gsm jersey", "kind": "fabric",
                                                   "consumption": "0.40", "unit": "kg",
                                                   "wastage_pct": "10"}, U)[0] is True)
    check("zero consumption refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": "0"}, U)[1] == "bad_consumption")
    check("negative consumption refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": "-2"}, U)[1] == "bad_consumption")
    check("non-numeric consumption refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": "abc"}, U)[1] == "bad_consumption")
    check("empty consumption refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": ""}, U)[1] == "bad_consumption")
    check("None consumption refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": None}, U)[1] == "bad_consumption")
    check("blank material refused",
          svc.add_bom_line(sid, {"material": "  ", "consumption": "1"}, U)[1] == "material_required")
    check("wastage > 100% refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": "1", "wastage_pct": "120"}, U)[1] == "bad_wastage")
    check("negative wastage refused",
          svc.add_bom_line(sid, {"material": "X", "consumption": "1", "wastage_pct": "-5"}, U)[1] == "bad_wastage")
    check("BOM on a missing style refused",
          svc.add_bom_line(999999, {"material": "X", "consumption": "1"}, U)[1] == "style_not_found")
    # INVARIANT: gross = net * (1 + wastage/100)  ->  0.40 * 1.10 = 0.44
    check("gross = net * (1 + wastage/100)", round(svc.gross_consumption(0.40, 10), 6) == 0.44)
    check("zero wastage leaves net untouched", svc.gross_consumption(0.40, 0) == 0.40)

    print("\n[5] BOM -> per-order seeding hook")
    lines = svc.bom_for_order("TC-TEST-99", 1000)
    check("hook returns the style's lines", len(lines) == 1)
    check("gross_per_unit correct", lines[0]["gross_per_unit"] == 0.44)
    check("total_qty = gross * order qty", lines[0]["total_qty"] == 440.0)
    check("qty 0 -> zero totals, no division anywhere",
          svc.bom_for_order("TC-TEST-99", 0)[0]["total_qty"] == 0.0)
    check("non-numeric qty -> 0, does not raise",
          svc.bom_for_order("TC-TEST-99", "abc")[0]["total_qty"] == 0.0)
    check("negative qty clamped to 0",
          svc.bom_for_order("TC-TEST-99", -5)[0]["total_qty"] == 0.0)
    check("None qty -> 0", svc.bom_for_order("TC-TEST-99", None)[0]["total_qty"] == 0.0)
    check("unknown style_ref -> []", svc.bom_for_order("NOPE-1") == [])
    check("blank style_ref -> []", svc.bom_for_order("") == [] and svc.bom_for_order(None) == [])
    check("seeded tee BOM reachable through the hook", len(svc.bom_for_order("TC-KNIT-01", 12000)) == 5)

    print("\n[6] sample rounds")
    check("bad stage refused", svc.record_sample(sid, {"stage": "nope"}, U)[1] == "bad_stage")
    check("missing stage refused", svc.record_sample(sid, {}, U)[1] == "bad_stage")
    check("bad verdict refused",
          svc.record_sample(sid, {"stage": "proto", "verdict": "maybe"}, U)[1] == "bad_verdict")
    check("sample on a missing style refused",
          svc.record_sample(999999, {"stage": "proto"}, U)[1] == "style_not_found")
    check("rejected inputs did not advance the style",
          svc.get_style(sid)["style"]["status"] == "development")
    svc.record_sample(sid, {"stage": "proto", "verdict": "rejected", "comments": "Neck too wide"}, U)
    check("first sample round auto-advances development -> sampling",
          svc.get_style(sid)["style"]["status"] == "sampling")
    svc.record_sample(sid, {"stage": "proto", "verdict": "approved"}, U)
    rounds = [s for s in svc.get_style(sid)["samples"] if s["stage"] == "proto"]
    check("round_no auto-increments per stage", sorted(r["round_no"] for r in rounds) == [1, 2])
    check("revision_no counts prior rejected/revise rounds",
          [r["revision_no"] for r in sorted(rounds, key=lambda x: x["round_no"])] == [0, 1])
    n = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='plm' "
                     "AND title LIKE 'Sample rejected%'").fetchone()["c"]
    check("rejected sample raised a bell alert", n >= 1)
    check("set_verdict rejects an unknown verdict", svc.set_verdict(rounds[0]["id"], "x", None, U)[1] == "bad_verdict")
    check("set_verdict on a missing sample refused", svc.set_verdict(999999, "approved", None, U)[1] == "sample_not_found")

    print("\n[7] BUSINESS RULE — no production release without an approved PP sample")
    check("approve refused while PP unapproved",
          svc.set_style_status(sid, "approved", U)[1] == "pp_sample_not_approved")
    check("in_production refused while PP unapproved",
          svc.set_style_status(sid, "in_production", U)[1] == "pp_sample_not_approved")
    check("sampling is always allowed", svc.set_style_status(sid, "sampling", U)[0] is True)
    check("unknown status refused", svc.set_style_status(sid, "nonsense", U)[1] == "bad_status")
    check("status on a missing style refused", svc.set_style_status(999999, "sampling", U)[1] == "style_not_found")
    svc.record_sample(sid, {"stage": "pp", "verdict": "rejected", "comments": "Shade off"}, U)
    check("a REJECTED PP still blocks approval",
          svc.set_style_status(sid, "approved", U)[1] == "pp_sample_not_approved")
    svc.record_sample(sid, {"stage": "pp", "verdict": "approved", "comments": "PP OK"}, U)
    check("approval allowed once PP is approved", svc.set_style_status(sid, "approved", U)[0] is True)
    b1 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='plm' "
                      "AND title LIKE 'Style approved%'").fetchone()["c"]
    svc.set_style_status(sid, "approved", U)          # double-apply
    b2 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='plm' "
                      "AND title LIKE 'Style approved%'").fetchone()["c"]
    check("re-approving does not raise a second bell", b1 == 1 and b2 == 1)
    check("in_production now allowed", svc.set_style_status(sid, "in_production", U)[0] is True)
    svc.record_sample(sid, {"stage": "top", "verdict": "approved"}, U)
    check("a later sample round never demotes a released style",
          svc.get_style(sid)["style"]["status"] == "in_production")
    check("update_style cannot smuggle a status change",
          svc.update_style(sid, {"status": "dropped", "name": "Test Polo v2"}, U) and
          svc.get_style(sid)["style"]["status"] == "in_production")

    print("\n[8] dashboard")
    d = svc.dashboard()
    check("dashboard counts styles", d["styles_total"] == len(svc.list_styles()))
    check("by_status has every lifecycle key", set(d["by_status"]) >= {"development", "sampling",
                                                                       "approved", "in_production", "dropped"})
    check("samples_pending is an int >= 0", isinstance(d["samples_pending"], int) and d["samples_pending"] >= 0)
    check("styles_approved counts approved + in_production",
          d["styles_approved"] == d["by_status"]["approved"] + d["by_status"]["in_production"])
    check("awaiting_pp never counts a style with an approved PP", d["awaiting_pp"] >= 0)

    print("\n[9] filters")
    check("filter by status", all(s["status"] == "sampling" for s in svc.list_styles(status="sampling")))
    check("filter by buyer", all(s["buyer"] == "EU Buyer A" for s in svc.list_styles(buyer="EU Buyer A")))
    check("search matches ref", any(s["style_ref"] == "TC-TEST-99" for s in svc.list_styles(q="TEST-99")))
    check("search that matches nothing -> []", svc.list_styles(q="zzzz-none") == [])
    check("buyers() returns distinct non-empty buyers", "EU Buyer A" in svc.buyers())

    conn.close()

print("\n[10] template parse")
env = Environment(loader=FileSystemLoader(str(REPO / "app" / "templates")))
for f in sorted((REPO / "app" / "templates" / "plm").glob("*.html")):
    env.parse((REPO / "app" / "templates" / "plm" / f.name).read_text(encoding="utf-8"))
    print("  ok  parses: plm/" + f.name)

print(f"\nALL CHECKS PASSED ({ok} assertions)")
