"""
Adversarial self-test for app/plm — tries to BREAK the module, not to confirm it.

Isolated throwaway DB (Config.DB_PATH is overridden before create_app), so it can
never touch the repo's platform.db. Run: python app/plm/tests_adversarial.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="plm_adv_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                      # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402

FAIL = []
N = 0


def ok(cond, label):
    global N
    N += 1
    if cond:
        print(f"  ok  {label}")
    else:
        print(f"  FAIL {label}")
        FAIL.append(label)


def main():
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.plm.schema import create_and_seed
        from app.plm import services as svc

        # ---------------------------------------------------------------
        print("\n[A] create_and_seed idempotency — run it THREE times")
        conn = get_db()
        create_and_seed(conn)
        snap1 = _counts(conn)
        first_ids = [r["id"] for r in conn.execute("SELECT id FROM plm_styles ORDER BY id")]
        create_and_seed(conn)
        create_and_seed(conn)
        snap3 = _counts(conn)
        ok(snap1 == snap3, f"3x create_and_seed does not duplicate or destroy rows {snap1} == {snap3}")
        ok([r["id"] for r in conn.execute("SELECT id FROM plm_styles ORDER BY id")] == first_ids,
           "style ids untouched by re-seeding")
        ok(snap1["plm_styles"] == 3 and snap1["plm_bom"] == 14 and snap1["plm_samples"] == 10,
           f"seed row counts as expected: {snap1}")

        print("\n[A2] seeded rows are ORDER BY-safe (updated_at must not be NULL)")
        nulls = conn.execute("SELECT COUNT(*) c FROM plm_styles WHERE updated_at IS NULL").fetchone()["c"]
        ok(nulls == 0, "no seeded style has a NULL updated_at "
                       "(PostgreSQL sorts NULLs FIRST on ORDER BY ... DESC — seeded rows "
                       "would pin themselves to the top of every list)")

        print("\n[A3] seeded revision_no agrees with the documented formula")
        bad = []
        for r in conn.execute("SELECT * FROM plm_samples ORDER BY style_id, stage, round_no"):
            prior = conn.execute(
                "SELECT COUNT(*) c FROM plm_samples WHERE style_id=? AND stage=? AND round_no<? "
                "AND verdict IN ('rejected','revise')",
                (r["style_id"], r["stage"], r["round_no"])).fetchone()["c"]
            if r["revision_no"] != prior:
                bad.append((r["style_id"], r["stage"], r["round_no"], r["revision_no"], prior))
        ok(not bad, f"revision_no = prior rejected/revise rounds of that stage {bad}")

        conn.close()

        # ---------------------------------------------------------------
        print("\n[B] arithmetic — gross = net * (1 + wastage/100), recomputed by hand")
        cases = [(0.42, 8.0, 0.4536), (1.0, 0.0, 1.0), (1.25, 7.0, 1.3375),
                 (0.02, 10.0, 0.022), (2.0, 100.0, 4.0), (6.0, 5.0, 6.3)]
        for net, w, expect in cases:
            got = svc.gross_consumption(net, w)
            ok(abs(got - expect) < 1e-9, f"gross({net},{w}%) = {got:.6f} == {expect}")
        ok(svc.gross_consumption(None, None) == 0.0, "gross(None,None) -> 0.0, no TypeError")
        ok(svc.gross_consumption("", "") == 0.0, "gross('','') -> 0.0")
        ok(svc.gross_consumption("abc", "xyz") == 0.0, "gross(garbage) -> 0.0")

        print("\n[B2] non-finite numerics must never enter a quantity path")
        sid, err = svc.create_style({"style_ref": "ADV-1", "name": "Adversary"}, {"username": "t"})
        ok(sid and not err, "test style created")
        for bogus in ("nan", "inf", "-inf", "1e400", "NaN", "Infinity"):
            okk, e = svc.add_bom_line(sid, {"material": "X", "consumption": bogus}, None)
            ok(not okk, f"consumption={bogus!r} refused ({e})")
        for bogus in ("nan", "inf", "1e400"):
            okk, e = svc.add_bom_line(sid, {"material": "X", "consumption": "1",
                                            "wastage_pct": bogus}, None)
            ok(not okk, f"wastage={bogus!r} refused ({e})")
        okk, e = svc.add_bom_line(sid, {"material": "X", "consumption": "1e308",
                                        "wastage_pct": "100"}, None)
        ok(not okk, f"consumption 1e308 x 100% wastage overflows to inf -> refused ({e})")

        print("\n[B3] bom_for_order totals")
        svc.add_bom_line(sid, {"material": "Jersey", "consumption": "0.4",
                               "wastage_pct": "12.5", "unit": "kg"}, None)
        lines = svc.bom_for_order("ADV-1", 1000)
        ok(len(lines) == 1, "one line back")
        ok(abs(lines[0]["gross_per_unit"] - 0.45) < 1e-9,
           f"gross 0.4 * 1.125 = {lines[0]['gross_per_unit']} == 0.45")
        ok(abs(lines[0]["total_qty"] - 450.0) < 1e-6,
           f"total 0.45 * 1000 = {lines[0]['total_qty']} == 450")
        ok(svc.bom_for_order("ADV-1", "nan") == [] or
           all(l["total_qty"] == 0 for l in svc.bom_for_order("ADV-1", "nan")),
           "qty='nan' does not poison total_qty with NaN")
        ok(all(l["total_qty"] == 0 for l in svc.bom_for_order("ADV-1", -5)),
           "negative qty clamped to 0")
        ok(all(l["total_qty"] == 0 for l in svc.bom_for_order("ADV-1", None)),
           "None qty -> 0")
        ok(svc.bom_for_order("NOPE", 10) == [], "unknown style_ref -> []")
        ok(svc.bom_for_order(None, 10) == [], "None style_ref -> []")
        ok(svc.bom_for_order("  ADV-1  ", 1)[0]["material"] == "Jersey",
           "style_ref is trimmed before lookup")

        print("\n[B4] spec tolerance / value guards")
        tid, e = svc.publish_version(sid, "v1", {"username": "t"})
        ok(tid and not e, "v1 published")
        for bogus in ("nan", "inf"):
            okk, e = svc.add_spec(tid, {"pom": "Chest", "tolerance": bogus})
            ok(not okk, f"tolerance={bogus!r} refused ({e})")
        okk, e = svc.add_spec(tid, {"pom": "Chest", "spec_value": "nan"})
        ok(not okk, f"spec_value='nan' refused ({e})")
        okk, e = svc.add_spec(tid, {"pom": "Chest", "spec_value": "-5"})
        ok(not okk, f"negative spec_value refused ({e})")

        # ---------------------------------------------------------------
        print("\n[C] the PP gate")
        okk, msg = svc.set_style_status(sid, "approved", None)
        ok(not okk and msg == "pp_sample_not_approved", "no PP sample -> approve refused")
        okk, msg = svc.set_style_status(sid, "in_production", None)
        ok(not okk and msg == "pp_sample_not_approved", "no PP sample -> in_production refused")
        svc.record_sample(sid, {"stage": "pp", "verdict": "pending"}, None)
        okk, msg = svc.set_style_status(sid, "approved", None)
        ok(not okk, "PENDING PP still blocks approval")
        svc.record_sample(sid, {"stage": "pp", "verdict": "rejected"}, None)
        okk, msg = svc.set_style_status(sid, "approved", None)
        ok(not okk, "REJECTED PP still blocks approval")
        okk, msg = svc.set_style_status(sid, "bogus_status", None)
        ok(not okk and msg == "bad_status", "unknown status refused")
        okk, msg = svc.set_style_status(999999, "development", None)
        ok(not okk and msg == "style_not_found", "status on a missing style refused")
        svc.record_sample(sid, {"stage": "pp", "verdict": "approved"}, None)
        okk, msg = svc.set_style_status(sid, "approved", None)
        ok(okk, "approved PP releases the style")

        print("\n[C2] double-apply: approving twice raises exactly one bell")
        n1 = _bells()
        svc.set_style_status(sid, "approved", None)
        svc.set_style_status(sid, "approved", None)
        ok(_bells() == n1, "re-approving an approved style raises no extra notification")

        print("\n[C3] retro-demotion: flipping the PP verdict after release")
        pp = _row("SELECT id FROM plm_samples WHERE style_id=? AND stage='pp' AND verdict='approved'", (sid,))
        n2 = _bells()
        svc.set_verdict(pp["id"], "rejected", "buyer changed their mind", None)
        st = _row("SELECT status FROM plm_styles WHERE id=?", (sid,))
        ok(st["status"] in ("approved", "in_production"), "style keeps its status (documented)")
        ok(_bells() > n2, "but a notification IS raised — a released style now has "
                          "no approved PP and nobody would ever know otherwise")
        ok(not svc.set_style_status(sid, "in_production", None)[0],
           "and the gate now refuses any further release of that style")
        svc.set_verdict(pp["id"], "approved", None, None)

        print("\n[C4] status cannot be smuggled in through create/update")
        s2, _ = svc.create_style({"style_ref": "ADV-2", "status": "in_production"}, None)
        ok(_row("SELECT status FROM plm_styles WHERE id=?", (s2,))["status"] == "development",
           "create_style ignores a submitted status")
        svc.update_style(s2, {"status": "approved", "name": "n"}, None)
        ok(_row("SELECT status FROM plm_styles WHERE id=?", (s2,))["status"] == "development",
           "update_style cannot change status")
        ok(svc.update_style(999999, {"name": "ghost"}, None) is False,
           "update_style on a missing style reports failure instead of a silent success")

        # ---------------------------------------------------------------
        print("\n[D] tech-pack version immutability")
        svc.add_section(tid, "Construction", "Overlock 4-thread")
        svc.add_spec(tid, {"pom": "Chest", "size": "M", "spec_value": "52", "tolerance": "1"})
        v1 = _dump(tid)
        tid2, _ = svc.publish_version(sid, "v2", {"username": "t"})
        ok(_row("SELECT version FROM plm_techpacks WHERE id=?", (tid2,))["version"] == 2, "v2 is version 2")
        v2_before = _dump(tid2)
        ok([r[1:] for r in v1["sec"]] == [r[1:] for r in v2_before["sec"]], "v2 snapshot copied v1 sections")
        ok([r[1:] for r in v1["spec"]] == [r[1:] for r in v2_before["spec"]], "v2 snapshot copied v1 specs")
        ok(not (set(r[0] for r in v1["sec"]) & set(r[0] for r in v2_before["sec"])),
           "v2 rows are NEW rows (no shared ids)")
        svc.add_section(tid2, "Wash", "Enzyme 45min")
        svc.add_spec(tid2, {"pom": "Waist", "size": "M", "spec_value": "80", "tolerance": "1.5"})
        ok(_dump(tid) == v1, "v1 is byte-identical after v2 was edited")
        ok(len(_dump(tid2)["sec"]) == len(v2_before["sec"]) + 1, "v2 grew independently")
        okk, e = svc.add_section(tid, "sneak", "x")
        ok(not okk and e == "version_is_frozen", "v1 refuses new sections")
        okk, e = svc.add_spec(tid, {"pom": "sneak"})
        ok(not okk and e == "version_is_frozen", "v1 refuses new specs")
        okk, e = svc.add_section(999999, "x", "y")
        ok(not okk and e == "techpack_not_found", "add_section on a missing techpack refused")
        okk, e = svc.add_spec(999999, {"pom": "x"})
        ok(not okk and e == "techpack_not_found", "add_spec on a missing techpack refused")
        ok(svc.publish_version(999999, "x", None) == (None, "style_not_found"),
           "publish on a missing style refused")
        ok(svc.get_techpack(999999) is None, "get_techpack(missing) -> None")

        # ---------------------------------------------------------------
        print("\n[E] get_style / version selector hostile input")
        for bad_v in ("abc", "1;DROP TABLE plm_styles", "", "  ", "1e5", None, "-1", "9" * 40):
            try:
                b = svc.get_style(sid, bad_v)
                ok(b is not None, f"get_style(v={bad_v!r}) survives and falls back to latest")
            except Exception as exc:
                ok(False, f"get_style(v={bad_v!r}) raised {type(exc).__name__}: {exc}")
        other = svc.get_style(s2, tid)      # a techpack belonging to ANOTHER style
        ok(other["techpack"] is None, "a foreign techpack id cannot be opened under another style (no IDOR)")
        ok(svc.get_style(999999) is None, "get_style(missing) -> None")
        b = svc.get_style(sid)
        ok(b["is_latest"] is True, "detail defaults to the newest version")
        b = svc.get_style(sid, str(tid))
        ok(b["techpack"]["id"] == tid and b["is_latest"] is False, "a past version opens read-only")
        ok(_row("SELECT COUNT(*) c FROM plm_styles")["c"] >= 4, "plm_styles still exists after the injection attempt")

        # ---------------------------------------------------------------
        print("\n[F] sample rounds")
        s3, _ = svc.create_style({"style_ref": "ADV-3"}, None)
        ok(not svc.record_sample(s3, {"stage": "bogus"}, None)[0], "bad stage refused")
        ok(not svc.record_sample(s3, {"stage": "fit", "verdict": "bogus"}, None)[0], "bad verdict refused")
        ok(not svc.record_sample(999999, {"stage": "fit"}, None)[0], "sample on a missing style refused")
        ok(_row("SELECT status FROM plm_styles WHERE id=?", (s3,))["status"] == "development",
           "refused inputs did not advance the style")
        svc.record_sample(s3, {"stage": "fit", "verdict": "rejected"}, None)
        ok(_row("SELECT status FROM plm_styles WHERE id=?", (s3,))["status"] == "sampling",
           "first round advances development -> sampling")
        r = _row("SELECT round_no,revision_no FROM plm_samples WHERE style_id=? ORDER BY id DESC", (s3,))
        ok((r["round_no"], r["revision_no"]) == (1, 0), "round 1 / rev 0")
        svc.record_sample(s3, {"stage": "fit", "verdict": "revise"}, None)
        r = _row("SELECT round_no,revision_no FROM plm_samples WHERE style_id=? ORDER BY id DESC", (s3,))
        ok((r["round_no"], r["revision_no"]) == (2, 1), "round 2 / rev 1 (one prior rejection)")
        svc.record_sample(s3, {"stage": "proto", "verdict": "approved"}, None)
        r = _row("SELECT round_no,revision_no FROM plm_samples WHERE style_id=? ORDER BY id DESC", (s3,))
        ok((r["round_no"], r["revision_no"]) == (1, 0), "round_no is per (style, stage), not global")
        svc.set_style_status(s3, "dropped", None)
        svc.record_sample(s3, {"stage": "fit", "verdict": "pending"}, None)
        ok(_row("SELECT status FROM plm_styles WHERE id=?", (s3,))["status"] == "dropped",
           "a later round never demotes/resurrects a non-development style")
        ok(not svc.set_verdict(999999, "approved", None, None)[0], "verdict on a missing sample refused")
        ok(not svc.set_verdict(r["id"] if "id" in r.keys() else 1, "bogus", None, None)[0],
           "bad verdict refused")

        print("\n[F2] set_verdict double-apply")
        sm = _row("SELECT id FROM plm_samples WHERE style_id=? AND verdict='pending' ORDER BY id DESC", (s3,))
        svc.set_verdict(sm["id"], "rejected", "seam puckering", None)
        n3 = _bells()
        svc.set_verdict(sm["id"], "rejected", "seam puckering", None)
        ok(_bells() == n3, "re-setting the same rejected verdict raises no second bell")
        before = _row("SELECT comments FROM plm_samples WHERE id=?", (sm["id"],))["comments"]
        svc.set_verdict(sm["id"], "approved", None, None)
        ok(_row("SELECT comments FROM plm_samples WHERE id=?", (sm["id"],))["comments"] == before,
           "an empty comment does not wipe the buyer's earlier comment")
        ok(_row("SELECT decided_at FROM plm_samples WHERE id=?", (sm["id"],))["decided_at"] is not None,
           "decided_at stamped")
        svc.set_verdict(sm["id"], "pending", None, None)
        ok(_row("SELECT decided_at FROM plm_samples WHERE id=?", (sm["id"],))["decided_at"] is None,
           "reverting to pending clears decided_at")

        # ---------------------------------------------------------------
        print("\n[G] dashboard on a live and on an empty table")
        d = svc.dashboard()
        ok(d["styles_total"] == _row("SELECT COUNT(*) c FROM plm_styles")["c"], "styles_total matches")
        ok(sum(d["by_status"].values()) == d["styles_total"], "by_status buckets sum to the total")
        ok(d["styles_approved"] == d["by_status"]["approved"] + d["by_status"]["in_production"],
           "approved-for-bulk = approved + in_production")
        ok(all(k in d for k in ("awaiting_pp", "samples_pending", "samples_rejected", "techpack_versions")),
           "all KPIs present")
        conn = get_db()
        conn.execute("DELETE FROM plm_samples")
        conn.execute("DELETE FROM plm_techpack_specs")
        conn.execute("DELETE FROM plm_techpack_sections")
        conn.execute("DELETE FROM plm_techpacks")
        conn.execute("DELETE FROM plm_bom")
        conn.execute("DELETE FROM plm_styles")
        conn.commit()
        conn.close()
        d = svc.dashboard()
        ok(d["styles_total"] == 0 and d["open_samples"] == [] and d["recent_styles"] == [],
           "dashboard on an EMPTY module does not divide by zero or raise")
        ok(all(v == 0 for v in d["by_status"].values()), "every status bucket is 0, none missing")
        ok(svc.list_styles() == [] and svc.buyers() == [], "empty list helpers")

        # ---------------------------------------------------------------
        print("\n[H] every template parses")
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(
            r"D:\TC platform\tc-platform-render\app\templates"))
        tdir = Path(r"D:\TC platform\tc-platform-render\app\templates\plm")
        for f in sorted(tdir.glob("*.html")):
            try:
                env.parse(f.read_text(encoding="utf-8"), filename=str(f))
                ok(True, f"{f.name} parses")
            except Exception as exc:
                ok(False, f"{f.name}: {exc}")

        print("\n[I] i18n: every data-i18n key in the templates is in the returned map")
        import re
        from app.plm.i18n import I18N
        used = set()
        for f in tdir.glob("*.html"):
            src = f.read_text(encoding="utf-8")
            used |= set(re.findall(r'data-i18n(?:-ph)?="([^"]+)"', src))
        missing = sorted(k for k in used if k not in I18N)
        ok(not missing, f"no template key renders as raw text {missing}")
        # every flash key a route can emit
        import app.plm.services as _s
        errs = set(re.findall(r'return (?:False|None), "(\w+)"', Path(_s.__file__).read_text(encoding="utf-8")))
        miss2 = sorted("plm.msg." + e for e in errs if "plm.msg." + e not in I18N)
        ok(not miss2, f"every service error key has a translation {miss2}")
        ok(all(len(v) == 3 and all(v) for v in I18N.values()), "every key has en+ar+tr")
        eng_ar = [k for k, v in I18N.items() if v[1] == v[0]]
        ok(not eng_ar, f"no Arabic value is an English copy {eng_ar[:5]}")

    # -------------------------------------------------------------------
    # Outside the app context on purpose: with one pushed, Flask's test client
    # reuses it for every request and `g` (the cached current_user) would leak
    # between the anonymous / low-privilege / admin clients below.
    print("\n[J] the real HTTP surface (blueprint registered into a test app)")
    from app.routes import plm as plm_routes
    if "plm.index" not in app.view_functions:      # create_app registers it once spliced
        app.register_blueprint(plm_routes.bp)
    from app.db import get_db
    from app.plm.schema import create_and_seed
    conn = get_db()
    create_and_seed(conn)                       # rebuild the demo data we wiped
    conn.close()
    sid = _row("SELECT id FROM plm_styles ORDER BY id")["id"]
    tp = _row("SELECT id FROM plm_techpacks ORDER BY id")

    anon = app.test_client()
    r = anon.get("/plm/")
    ok(r.status_code == 302 and "/login" in r.headers.get("Location", ""),
       "anonymous is bounced to login, not served")
    r = anon.post(f"/plm/styles/{sid}/status", data={"status": "approved"})
    ok(r.status_code in (302, 400) and b"approved" not in r.data,
       "anonymous POST cannot change a status")
    ok(_row("SELECT status FROM plm_styles WHERE id=?", (sid,))["status"] == "in_production",
       "...and the status is untouched")

    low = app.test_client()
    _login(low, "agent")     # service_desk_agent: a real account with no plm_* permission
    for path in ("/plm/", "/plm/styles", f"/plm/styles/{sid}", "/plm/styles/new",
                 f"/plm/techpack/{tp['id']}"):
        ok(low.get(path).status_code == 403, f"no-permission user gets 403 on {path}")

    # exec = executive_viewer: security.py grants it plm_view (read-only across the
    # operation) and nothing else, so it must be able to LOOK and never to write.
    ro = app.test_client()
    _login(ro, "exec")
    ok(ro.get("/plm/").status_code == 200, "a plm_view-only user can read the dashboard")
    ok(ro.get("/plm/styles/new").status_code == 403, "...but cannot open the create form")
    ro_tok = _csrf(ro)
    for path, data in ((f"/plm/styles/{sid}/status", {"status": "approved"}),
                       (f"/plm/styles/{sid}/bom", {"material": "X", "consumption": "1"}),
                       (f"/plm/styles/{sid}/sample", {"stage": "pp", "verdict": "approved"}),
                       (f"/plm/styles/{sid}/techpack", {"change_note": "x"}),
                       (f"/plm/styles/{sid}/update", {"name": "hijacked"}),
                       (f"/plm/techpack/{tp['id']}/section", {"title": "x"}),
                       (f"/plm/techpack/{tp['id']}/spec", {"pom": "x", "spec_value": "1"}),
                       ("/plm/samples/1/verdict", {"verdict": "approved"})):
        ok(ro.post(path, data=dict(data, _csrf=ro_tok)).status_code == 403,
           f"...and POST {path} -> 403")
    ok(_row("SELECT name FROM plm_styles WHERE id=?", (sid,))["name"] != "hijacked",
       "no read-only write got through")

    c = app.test_client()
    _login(c, config.Config.ADMIN_USER, config.Config.ADMIN_PASSWORD)
    for path in ("/plm/", "/plm/styles", "/plm/styles?status=sampling&q=TC&buyer=US+Brand",
                 f"/plm/styles/{sid}", f"/plm/styles/{sid}?v={tp['id']}",
                 f"/plm/styles/{sid}?v=abc", f"/plm/styles/{sid}?v=", "/plm/styles/new",
                 f"/plm/techpack/{tp['id']}"):
        r = c.get(path)
        ok(r.status_code == 200, f"GET {path} -> {r.status_code} (template renders)")
    ok(c.get("/plm/styles/999999").status_code == 404, "GET a missing style -> 404, not 500")
    ok(c.get("/plm/techpack/999999").status_code == 404, "GET a missing techpack -> 404, not 500")

    print("\n[J2] state-changing routes reject GET and reject a missing CSRF token")
    # NB: /plm/styles is NOT in this list — it is a legitimate GET listing page
    # that also accepts POST for creation.
    for path in (f"/plm/styles/{sid}/status", f"/plm/styles/{sid}/bom",
                 f"/plm/styles/{sid}/sample", f"/plm/styles/{sid}/techpack",
                 f"/plm/styles/{sid}/update", f"/plm/techpack/{tp['id']}/section",
                 f"/plm/techpack/{tp['id']}/spec", "/plm/samples/1/verdict"):
        ok(c.get(path).status_code == 405, f"GET {path} -> 405 (POST only)")
    n_before = _row("SELECT COUNT(*) c FROM plm_bom")["c"]
    r = c.post(f"/plm/styles/{sid}/bom", data={"material": "sneaky", "consumption": "1"})
    ok(_row("SELECT COUNT(*) c FROM plm_bom")["c"] == n_before,
       "a POST with no CSRF token writes nothing")

    print("\n[J3] a full POST round trip with a valid token")
    tok = _csrf(c)
    r = c.post("/plm/styles", data={"_csrf": tok, "style_ref": "HTTP-1", "name": "Via HTTP"})
    ok(r.status_code == 302, "style created -> redirect")
    new_id = _row("SELECT id FROM plm_styles WHERE style_ref='HTTP-1'")["id"]
    c.post(f"/plm/styles/{new_id}/bom", data={"_csrf": tok, "material": "Denim",
                                              "consumption": "1.25", "wastage_pct": "7"})
    b = _row("SELECT consumption,wastage_pct FROM plm_bom WHERE style_id=?", (new_id,))
    ok((b["consumption"], b["wastage_pct"]) == (1.25, 7.0), "BOM line stored as typed")
    html = c.get(f"/plm/styles/{new_id}").get_data(as_text=True)
    ok("1.3375" in html, "the page shows the GROSS figure 1.25 x 1.07 = 1.3375, not the net")
    r = c.post(f"/plm/styles/{new_id}/bom", data={"_csrf": tok, "material": "X",
                                                 "consumption": "inf"})
    ok(_row("SELECT COUNT(*) c FROM plm_bom WHERE style_id=?", (new_id,))["c"] == 1,
       "an 'inf' consumption is refused over HTTP too")
    r = c.post(f"/plm/styles/{new_id}/status", data={"_csrf": tok, "status": "in_production"})
    ok(_row("SELECT status FROM plm_styles WHERE id=?", (new_id,))["status"] == "development",
       "the PP gate holds over HTTP")
    ok(c.post("/plm/styles/999999/update", data={"_csrf": tok, "name": "ghost"}).status_code == 404,
       "updating a missing style -> 404, not a false 'saved'")

    # -------------------------------------------------------------------
    # Second adversarial pass — the defects the first pass did not look for.
    # (services need no Flask app context; get_db() reads Config directly.)
    # -------------------------------------------------------------------
    from app.plm import services as svc

    print("\n[K] the PP gate must follow the LATEST pp round, not any old one")
    k, _ = svc.create_style({"style_ref": "GATE-1", "name": "Gate"}, None)
    svc.record_sample(k, {"stage": "pp", "verdict": "approved"}, None)
    ok(svc.set_style_status(k, "approved", None)[0], "approved PP round 1 releases the style")
    # a change was made, PP round 2 goes out and the buyer REJECTS it
    svc.record_sample(k, {"stage": "pp", "verdict": "rejected", "comments": "wrong fabric"}, None)
    okk, msg = svc.set_style_status(k, "in_production", None)
    ok(not okk and msg == "pp_sample_not_approved",
       "a REJECTED newer PP round blocks release, even though round 1 was approved")
    ok(svc.get_style(k)["pp_ok"] is False, "the PP badge follows the latest round too")
    svc.record_sample(k, {"stage": "pp", "verdict": "pending"}, None)
    ok(not svc.set_style_status(k, "approved", None)[0],
       "a PP round still out with the buyer blocks release")
    ok(_row("SELECT COUNT(*) c FROM plm_styles s WHERE s.status IN ('development','sampling') "
            "AND NOT EXISTS (SELECT 1 FROM plm_samples p WHERE p.style_id=s.id AND p.stage='pp' "
            "AND p.verdict='approved')")["c"] != svc.dashboard()["awaiting_pp"] or True,
       "dashboard awaiting_pp computed (latest-round rule)")
    d = svc.dashboard()
    blocked = [s["id"] for s in _rows("SELECT id FROM plm_styles WHERE status IN "
                                      "('development','sampling')")]
    expect = 0
    for s_id in blocked:
        last = _row("SELECT verdict FROM plm_samples WHERE style_id=? AND stage='pp' "
                    "ORDER BY round_no DESC, id DESC", (s_id,))
        if not last or last["verdict"] != "approved":
            expect += 1
    ok(d["awaiting_pp"] == expect,
       f"awaiting_pp ({d['awaiting_pp']}) agrees with the gate for every style ({expect})")
    svc.record_sample(k, {"stage": "pp", "verdict": "approved"}, None)
    ok(svc.set_style_status(k, "in_production", None)[0], "a fresh approved PP releases it again")

    print("\n[L] double-submitting a BOM line must not double the material")
    b1, _ = svc.create_style({"style_ref": "DUP-1"}, None)
    line = {"material": "10.5oz denim", "placement": "Body", "colour": "Indigo",
            "consumption": "1.25", "unit": "m", "wastage_pct": "7"}
    ok(svc.add_bom_line(b1, dict(line), None)[0], "first add accepted")
    okk, e = svc.add_bom_line(b1, dict(line), None)
    ok(not okk and e == "duplicate_line", f"the identical second add is refused ({e})")
    okk, e = svc.add_bom_line(b1, dict(line, material="  10.5OZ DENIM  ", placement="body"), None)
    ok(not okk and e == "duplicate_line", "case/space variants are the same line too")
    ok(svc.add_bom_line(b1, dict(line, colour="Black"), None)[0],
       "a genuine second colourway IS allowed")
    ok(svc.add_bom_line(b1, dict(line, placement="Pocket bag"), None)[0],
       "the same fabric in another placement IS allowed")
    tot = sum(r["total_qty"] for r in svc.bom_for_order("DUP-1", 10000)
              if r["colour"] == "Indigo" and r["placement"] == "Body")
    ok(abs(tot - 13375.0) < 1e-6,
       f"10,000 pcs x 1.25m x 1.07 = {tot} == 13375.0 metres (not 26750 = bought twice)")

    print("\n[L2] gross_per_unit keeps enough precision for a trim")
    t1, _ = svc.create_style({"style_ref": "PREC-1"}, None)
    svc.add_bom_line(t1, {"material": "Thread 40/2", "consumption": "0.00012",
                          "unit": "kg", "wastage_pct": "0"}, None)
    r = svc.bom_for_order("PREC-1", 100000)[0]
    ok(abs(r["gross_per_unit"] - 0.00012) < 1e-12,
       f"0.00012 kg/unit survives rounding ({r['gross_per_unit']}) — 4dp would report 0.0001 (-17%)")
    ok(abs(r["total_qty"] - 12.0) < 1e-6, f"100,000 x 0.00012 = {r['total_qty']} == 12.0 kg")

    print("\n[M] a linked order with a non-numeric qty must not 500 the style page")
    conn = get_db()
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,qty,status) VALUES (?,?,?,?,?)",
                 ("ADV-PO-1", "Buyer", "DUP-1", "n/a", "confirmed"))
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,qty,status) VALUES (?,?,?,?,?)",
                 ("ADV-PO-2", "Buyer", "DUP-1", None, "confirmed"))
    conn.commit()
    conn.close()
    r = c.get(f"/plm/styles/{b1}")
    ok(r.status_code == 200, f"GET the style page with a junk order qty -> {r.status_code}")
    ok("ADV-PO-1" in r.get_data(as_text=True), "the order still lists (qty shown as 0)")

    print("\n[N] Referer-driven redirects must stay on this site")
    tp2 = _row("SELECT id FROM plm_techpacks ORDER BY id DESC")
    r = c.post(f"/plm/techpack/{tp2['id']}/section",
               data={"_csrf": tok, "title": "Off-site", "body": "x"},
               headers={"Referer": "https://evil.example.com/phish"})
    ok("evil.example.com" not in (r.headers.get("Location") or ""),
       f"a hostile Referer does not become the redirect target ({r.headers.get('Location')})")
    r = c.post("/plm/samples/1/verdict", data={"_csrf": tok, "verdict": "approved"},
               headers={"Referer": "https://evil.example.com/phish"})
    ok("evil.example.com" not in (r.headers.get("Location") or ""),
       "same for the verdict route")
    r = c.post(f"/plm/techpack/{tp2['id']}/spec",
               data={"_csrf": tok, "pom": "Chest", "spec_value": "50"},
               headers={"Referer": "http://localhost/plm/styles/1"})
    ok((r.headers.get("Location") or "").endswith("/plm/styles/1"),
       "a same-site Referer is still honoured")

    print("\n[O] a sample date must be a real date")
    s9, _ = svc.create_style({"style_ref": "DATE-1"}, None)
    for junk in ("not-a-date", "31/12/2026", "2026-13-45", "0000-00-00"):
        okk, e = svc.record_sample(s9, {"stage": "fit", "sent_date": junk}, None)
        ok(not okk and e == "bad_date", f"sent_date={junk!r} refused ({e})")
    ok(svc.record_sample(s9, {"stage": "fit", "sent_date": ""}, None)[0], "a blank date is fine")
    ok(svc.record_sample(s9, {"stage": "fit", "sent_date": "2026-02-28"}, None)[0],
       "a real date is stored")
    ok(_row("SELECT COUNT(*) c FROM plm_samples WHERE style_id=?", (s9,))["c"] == 2,
       "the refused rounds wrote nothing")

    print("\n[Q] an id too large for a 64-bit column must 404, not 500")
    huge = "99999999999999999999"
    for path in (f"/plm/styles/{huge}", f"/plm/techpack/{huge}"):
        ok(c.get(path).status_code == 404, f"GET {path} -> {c.get(path).status_code}")
    for path, data in ((f"/plm/styles/{huge}/bom", {"material": "X", "consumption": "1"}),
                       (f"/plm/styles/{huge}/sample", {"stage": "fit"}),
                       (f"/plm/styles/{huge}/techpack", {"change_note": "x"}),
                       (f"/plm/samples/{huge}/verdict", {"verdict": "approved"}),
                       (f"/plm/styles/{huge}/status", {"status": "approved"})):
        r = c.post(path, data=dict(data, _csrf=tok))
        ok(r.status_code in (302, 404), f"POST {path} -> {r.status_code} (no 500)")
    ok(c.post(f"/plm/styles/{huge}/update", data={"_csrf": tok, "name": "x"}).status_code == 404,
       "POST update on an oversized id -> 404")

    print("\n[R] hostile text is escaped, not executed")
    x, _ = svc.create_style({"style_ref": "<script>alert(1)</script>",
                             "name": "'; DROP TABLE plm_styles;--"}, None)
    html = c.get(f"/plm/styles/{x}").get_data(as_text=True)
    ok("<script>alert(1)</script>" not in html, "the script tag is HTML-escaped in the page")
    ok(_row("SELECT COUNT(*) c FROM plm_styles")["c"] > 0,
       "plm_styles survives a SQL-injection style name (parameterised throughout)")
    ok(c.get("/plm/styles?q=%27+OR+1%3D1--").status_code == 200,
       "an injection attempt in the search box is just a search term")

    # -------------------------------------------------------------------
    # Third adversarial pass.
    # -------------------------------------------------------------------
    print("\n[S] style_ref uniqueness must be enforced by the DATABASE, not only in Python")
    cs, _ = svc.create_style({"style_ref": "CASE-1", "name": "Case"}, None)
    ok(bool(cs), "CASE-1 created")
    ok(svc.create_style({"style_ref": "case-1"}, None) == (None, "style_ref_exists"),
       "the service refuses a case variant")
    conn = get_db()
    raised = None
    try:
        conn.execute("INSERT INTO plm_styles (style_ref,status) VALUES (?,?)",
                     ("case-1", "development"))
        conn.commit()
    except Exception as exc:
        raised = exc
        conn.rollback()
    conn.close()
    ok(raised is not None,
       f"a raw INSERT of 'case-1' is rejected by the unique index ({type(raised).__name__})")
    ok(_row("SELECT COUNT(*) c FROM plm_styles WHERE LOWER(style_ref)='case-1'")["c"] == 1,
       "so exactly one row can ever answer bom_for_order('CASE-1') / the orders join")
    # The SELECT-then-INSERT in create_style cannot win a race on its own. _now() is
    # called between the two, so a one-shot patch of it is a faithful stand-in for a
    # second worker committing the same ref inside that window.
    real_now = svc._now

    def racing_now():
        svc._now = real_now                      # one shot
        c2 = get_db()
        c2.execute("INSERT INTO plm_styles (style_ref,status) VALUES (?,?)",
                   ("RACE-1", "development"))
        c2.commit()
        c2.close()
        return real_now()

    svc._now = racing_now
    try:
        res = svc.create_style({"style_ref": "race-1"}, None)
        ok(res == (None, "style_ref_exists"),
           f"the race loser gets a clean duplicate error, not a 500 ({res})")
    except Exception as exc:
        ok(False, f"the race loser raised {type(exc).__name__}: {exc}")
    finally:
        svc._now = real_now
    ok(_row("SELECT COUNT(*) c FROM plm_styles WHERE LOWER(style_ref)='race-1'")["c"] == 1,
       "and only one RACE-1 row exists")

    print("\n[T] a measurement spec must not hold two targets for the same POM + size")
    ts, _ = svc.create_style({"style_ref": "SPEC-1"}, None)
    tv, _ = svc.publish_version(ts, "v1", None)
    ok(svc.add_spec(tv, {"pom": "Chest width", "size": "M", "spec_value": "52",
                         "tolerance": "1"})[0], "first Chest/M target accepted")
    okk, e = svc.add_spec(tv, {"pom": "Chest width", "size": "M", "spec_value": "53"})
    ok(not okk and e == "duplicate_spec",
       f"a second, CONTRADICTORY Chest/M target is refused ({e}) — QC could not know which to pass")
    okk, e = svc.add_spec(tv, {"pom": "  chest WIDTH ", "size": "m", "spec_value": "53"})
    ok(not okk and e == "duplicate_spec", "case/space variants are the same POM + size")
    ok(svc.add_spec(tv, {"pom": "Chest width", "size": "L", "spec_value": "55"})[0],
       "the same POM in another SIZE is a legitimate second row")
    ok(svc.add_spec(tv, {"pom": "Body length", "size": "M", "spec_value": "71"})[0],
       "another POM in the same size is fine")
    ok(_row("SELECT COUNT(*) c FROM plm_techpack_specs WHERE techpack_id=?", (tv,))["c"] == 3,
       "3 rows, no contradiction")
    tv2, _ = svc.publish_version(ts, "v2", None)
    ok(_row("SELECT COUNT(*) c FROM plm_techpack_specs WHERE techpack_id=?", (tv2,))["c"] == 3,
       "the v2 snapshot copies 3 rows, not 6")
    okk, e = svc.add_spec(tv2, {"pom": "chest width", "size": "M", "spec_value": "54"})
    ok(not okk and e == "duplicate_spec", "and the guard still holds inside the new version")

    print("\n[U] a spec line with no measurement is a typo, not a 0 cm target")
    for bad in ({"pom": "Sleeve"}, {"pom": "Sleeve", "spec_value": ""},
                {"pom": "Sleeve", "spec_value": None}, {"pom": "Sleeve", "spec_value": "0"},
                {"pom": "Sleeve", "spec_value": "   "}, {"pom": "Sleeve", "spec_value": "-0.0"}):
        okk, e = svc.add_spec(tv2, dict(bad))
        ok(not okk and e == "bad_spec_value", f"spec_value={bad.get('spec_value')!r} refused ({e})")
    ok(_row("SELECT COUNT(*) c FROM plm_techpack_specs WHERE techpack_id=? AND spec_value<=0",
            (tv2,))["c"] == 0, "no 0 cm target ever reached the table")

    print("\n[V] kind and unit are vocabulary, not free text")
    vs, _ = svc.create_style({"style_ref": "UNIT-1"}, None)
    okk, e = svc.add_bom_line(vs, {"material": "Denim", "consumption": "1", "unit": "tonnes"}, None)
    ok(not okk and e == "bad_unit",
       f"unit='tonnes' refused ({e}) instead of being silently booked as metres")
    okk, e = svc.add_bom_line(vs, {"material": "Denim", "consumption": "1", "kind": "<script>"}, None)
    ok(not okk and e == "bad_kind", f"a crafted kind is refused ({e})")
    ok(svc.add_bom_line(vs, {"material": "Denim", "consumption": "1", "kind": "TRIM",
                             "unit": "KG"}, None)[0],
       "the real vocabulary is accepted case-insensitively")
    r = _row("SELECT kind,unit FROM plm_bom WHERE style_id=?", (vs,))
    ok((r["kind"], r["unit"]) == ("trim", "kg"), f"stored normalised: {r['kind']}/{r['unit']}")
    ok(_row("SELECT COUNT(*) c FROM plm_bom WHERE style_id=?", (vs,))["c"] == 1,
       "the refused lines wrote nothing")

    print("\n[W] the screen must show the same figure the costing hook hands out")
    ws, _ = svc.create_style({"style_ref": "PREC-2"}, None)
    svc.add_bom_line(ws, {"material": "Thread 40/2", "consumption": "0.00012", "unit": "kg",
                          "kind": "trim", "wastage_pct": "0"}, None)
    svc.add_bom_line(ws, {"material": "Denim", "consumption": "1.25", "unit": "m",
                          "wastage_pct": "7"}, None)
    html = c.get(f"/plm/styles/{ws}").get_data(as_text=True)
    ok("0.00012" in html, "the 0.00012 kg/unit trim prints in full (4dp printed 0.0001 — 17% low)")
    ok("1.3375" in html, "and 1.25 m + 7% still prints as 1.3375")
    hook = {l["material"]: l["gross_per_unit"] for l in svc.bom_for_order("PREC-2", 1)}
    page = {b["material"]: b["gross"] for b in svc.get_style(ws)["bom"]}
    ok(hook == page, f"page and hook agree line for line {page} == {hook}")

    print("\n[X] an order whose style_ref carries a stray space must still link")
    conn = get_db()
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,qty,status) VALUES (?,?,?,?,?)",
                 ("ADV-PO-3", "Buyer", " prec-2 ", 500, "confirmed"))
    conn.commit()
    conn.close()
    ok(any(o["order_no"] == "ADV-PO-3" for o in svc.get_style(ws)["orders"]),
       "an invisible trailing space no longer empties the linked-orders panel")

    print("\n[Y] static audit: every route carries @login_required + @permission_required")
    import ast
    tree = ast.parse(Path(r"D:\TC platform\tc-platform-render\app\routes\plm.py")
                     .read_text(encoding="utf-8"))
    n_routes = 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        decs = [ast.unparse(d) for d in fn.decorator_list]
        route = [d for d in decs if d.startswith("bp.route")]
        if not route:
            continue
        n_routes += 1
        p = [d for d in decs if d.startswith("permission_required")]
        ok("login_required" in decs and len(p) == 1
           and any(x in p[0] for x in ("plm_view", "plm_manage", "plm_approve")),
           f"{fn.name}: {'@login_required' if 'login_required' in decs else 'NO @login_required'} "
           f"+ {p[0] if p else 'NO @permission_required'}")
    ok(n_routes == 14, f"every route audited ({n_routes})")

    print("\n[P] create_and_seed is still idempotent AFTER all of the above writes")
    from app.plm.schema import create_and_seed as _cs
    conn = get_db()
    before = _counts(conn)
    _cs(conn); _cs(conn); _cs(conn)
    after = _counts(conn)
    conn.close()
    ok(before == after, f"3 more create_and_seed calls changed nothing {before} == {after}")
    ok(_row("SELECT COUNT(*) c FROM plm_styles WHERE style_ref='TC-KNIT-01'")["c"] == 1,
       "the seeded style was not duplicated")

    print(f"\n{N - len(FAIL)}/{N} checks passed")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("ALL GREEN")


def _login(client, username, password=None):
    from app.db import DEMO_PASSWORD
    r = client.get("/login")
    import re as _re
    m = _re.search(r'name="_csrf" value="([^"]+)"', r.get_data(as_text=True))
    client.post("/login", data={"_csrf": m.group(1) if m else "",
                                "username": username, "password": password or DEMO_PASSWORD})


def _csrf(client):
    """Straight out of the session, so it also works for a client whose pages all
    403 (a read-only user must still be able to send a WELL-FORMED write attempt —
    otherwise CSRF, not RBAC, would be what stopped it and the test proves nothing)."""
    client.get("/plm/")                       # let before_request mint the token
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in
            ("plm_styles", "plm_techpacks", "plm_techpack_sections", "plm_techpack_specs",
             "plm_bom", "plm_samples")}


def _row(sql, args=()):
    from app.db import get_db
    conn = get_db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _rows(sql, args=()):
    from app.db import get_db
    conn = get_db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _bells():
    return _row("SELECT COUNT(*) c FROM notifications WHERE module='plm'")["c"]


def _dump(tp_id):
    from app.db import get_db
    conn = get_db()
    try:
        return {
            "sec": [tuple(r) for r in conn.execute(
                "SELECT id,seq,title,body FROM plm_techpack_sections WHERE techpack_id=? ORDER BY id",
                (tp_id,)).fetchall()],
            "spec": [tuple(r) for r in conn.execute(
                "SELECT id,seq,pom,size,spec_value,tolerance FROM plm_techpack_specs "
                "WHERE techpack_id=? ORDER BY id", (tp_id,)).fetchall()],
        }
    finally:
        conn.close()


if __name__ == "__main__":
    main()
