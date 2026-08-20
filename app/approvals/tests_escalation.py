# -*- coding: utf-8 -*-
"""
SoD escalation self-test — throwaway database.
    python app/approvals/tests_escalation.py

The owner's case, reproduced first: the Warehouse Manager raises a purchase
request, the warehouse rung is the first rung, and he is the ONLY person holding
a warehouse role. He may not approve his own request (correct) — so the rung
escalates ONE LEVEL UP the org chart and the Factory Manager signs it. He still
cannot.

Then: the regression that matters most (a SECOND storekeeper exists -> nothing
changes at all), a superior who is also the requester, a cyclic chain an admin
configured, the terminal case (the CEO raising a CEO-level request -> refused at
submit + proc_admin notified), self-approval at every depth, the dual-role rule
on an escalated ladder, byte-identical ladder truncation / pricing gate / stage
order for a requester who is not an approver, the settings editor (proc_admin
saves, proc_view-only gets a raw 403 and writes nothing, no self-superiority),
create_and_seed x3, and every page in en / ar / tr with every data-i18n key
resolving in all three dictionaries.
"""
import io
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="esc_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def mkuser(conn, username, role):
    from werkzeug.security import generate_password_hash
    conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                 "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                 (username, generate_password_hash("x"), username.title(), role))
    conn.commit()
    return dict(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())


def steps_of(conn, pr_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]


def ladder_shape(conn, pr_id):
    """The ladder as (seq, stage) pairs — what must stay byte-identical."""
    return [(s["seq"], s["stage"]) for s in steps_of(conn, pr_id)]


# ------------------------------------------------------------------ i18n scan
class Extract(HTMLParser):
    """Every data-i18n key on a rendered page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for at in ("data-i18n", "data-i18n-ph", "data-i18n-title"):
            if a.get(at):
                self.keys.add(a[at])


ARABIC = re.compile(r"[؀-ۿ]")

with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from app.approvals import constants as C
    from app.approvals import i18n_text as T
    from app.approvals import pdf as pdfgen

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    # The development seed ships one demo user per role, which is exactly the
    # "somebody else can sign" case. Park them so the owner's understaffed
    # situation can be reproduced; `admin` (super_admin) stays active.
    SEEDED = ("store", "warehouse", "factory", "purchasing", "finance", "cfo", "ceo")
    conn.execute("UPDATE users SET is_active=0 WHERE username IN (%s)"
                 % ",".join("?" * len(SEEDED)), SEEDED)
    conn.commit()

    def activate(*names, on=1):
        conn.execute("UPDATE users SET is_active=? WHERE username IN (%s)"
                     % ",".join("?" * len(names)), (on,) + names)
        conn.commit()

    print("\n--- 0. the chain is seeded as data, editable, additive ----------")
    chain = svc.escalation_map(conn)
    ok("proc_escalations seeded with the owner's default chain",
       all(chain.get(k) == v for k, v in C.DEFAULT_ESCALATION.items()))
    ok("storekeeper -> warehouse_manager -> factory_manager -> ceo",
       chain["storekeeper"] == "warehouse_manager"
       and chain["warehouse_manager"] == "factory_manager"
       and chain["factory_manager"] == "ceo")
    ok("purchasing_manager -> cfo, finance_user -> finance_manager -> cfo, cfo -> ceo",
       chain["purchasing_manager"] == "cfo" and chain["finance_user"] == "finance_manager"
       and chain["finance_manager"] == "cfo" and chain["cfo"] == "ceo")
    ok("ceo has nobody above it", (chain.get("ceo") or "") == "")
    ok("pr_steps carries esc_role + esc_from (and the SLA columns are untouched)",
       {"esc_role", "esc_from", "escalated", "escalated_at", "escalated2_at"}
       <= {r[1] for r in conn.execute("PRAGMA table_info(pr_steps)").fetchall()})

    # =====================================================================
    print("\n--- 1. THE OWNER'S CASE: sole warehouse_manager raises a PR ----")
    wm = mkuser(conn, "esc_wm", "warehouse_manager")
    fm = mkuser(conn, "esc_fm", "factory_manager")
    # The factory manager is the SOLE holder of his own role, and the factory rung
    # is on every ladder — so he cannot take the warehouse rung as well (the
    # dual-role rule would then refuse him his own rung, and the request would be
    # stuck one rung further down instead of at the first one). Every real company
    # has somebody above him; here that is the CEO.
    ceo = mkuser(conn, "esc_ceo", "ceo")
    # The buyer. DOAM §4.1 makes Purchasing a DEMAND rung, and a rung with no
    # holder at all is left alone by the climb — so without him nothing could
    # walk this ladder past rung 2, and "the deadlock is gone, not moved" would
    # be untestable.
    pm = mkuser(conn, "esc_pm", "purchasing_manager")
    ok("esc_wm is the ONLY holder of a warehouse-stage role",
       svc.role_holders(conn, C.STAGE_ROLES["warehouse"]) == {"esc_wm"})
    ok("esc_fm is the ONLY holder of the factory-stage role",
       svc.role_holders(conn, C.STAGE_ROLES["factory_manager"]) == {"esc_fm"})

    # PRICED past §4.1's tier-1 ceiling on purpose: the Plant Director joins the
    # ladder only above 10,000, and the invariant below is that the climb must not
    # hand the warehouse rung to the ONE person the factory rung of this same
    # request depends on. At zero he is not on the ladder and there is nothing to
    # collide with.
    pr1, no1 = svc.create_pr({"title": "Owner case", "department": "Production",
                              "currency": "EGP"},
                             [{"item": "Pallet truck", "qty": 1, "unit_price": 20000}],
                             wm, submit=True)
    ok("the PR was accepted and is pending (not stuck as a draft)",
       conn.execute("SELECT status FROM pr_requests WHERE id=?",
                    (pr1,)).fetchone()["status"] == "pending")
    st1 = steps_of(conn, pr1)
    wh = st1[0]
    ok("the first rung is still the warehouse STAGE (stage order untouched)",
       wh["stage"] == "warehouse" and wh["seq"] == 1)
    ok("the warehouse rung escalated (esc_role=%r)" % wh["esc_role"],
       bool(wh["esc_role"]))
    ok("...and records what it escalated FROM",
       set((wh["esc_from"] or "").split(",")) == set(C.STAGE_ROLES["warehouse"]))
    ok("no OTHER rung was escalated (purchasing has no holders at all)",
       [s["stage"] for s in st1 if s["esc_from"]] == ["warehouse"])
    # THE INVARIANT the first cut of this feature missed: the escalated rung must
    # not be handed to the only person who can sign another rung of the SAME
    # ladder. esc_fm is exactly that person, so the climb must pass him.
    ok("the escalation did NOT land on the sole factory manager (would deadlock "
       "rung 2 with dual_role)", "factory_manager" not in svc.step_roles(wh))
    ok("it climbed one more level, to the CEO (%r)" % wh["esc_role"],
       svc.step_roles(wh) == {"ceo"})
    ok("the CEO may now sign the warehouse rung", svc.can_act_step(ceo, wh))
    ok("the sole factory manager may NOT (he is needed on his own rung)",
       not svc.can_act_step(fm, wh))
    ok("the requester still may NOT (can_act_step is not a loophole)",
       not svc.can_act_step(wm, wh))
    ok("the escalated rung is in the CEO's queue",
       pr1 in [p["id"] for p in svc.my_queue(ceo)])
    ok("and NOT in the requester's queue", pr1 not in [p["id"] for p in svc.my_queue(wm)])
    okk, msg = svc.act_on_step(pr1, wm, "approve")
    ok("act_on_step refuses the requester on the escalated rung (%s)" % msg,
       not okk and msg in ("self_approval", "forbidden"))
    okk, msg = svc.act_on_step(pr1, ceo, "approve")
    ok("the CEO's signature is accepted and the PR advances (%s)" % msg,
       okk and msg == "advanced")
    # Rung 2 is Purchasing under §4.1, and §4.3's lowest sourcing band still wants
    # one quotation on file before the buyer signs.
    svc.add_quote(pr1, {"vendor": "Owner Case Vendor", "amount": 20000}, pm)
    okk, msg = svc.act_on_step(pr1, pm, "approve")
    ok("the buyer signs the purchasing rung (%s)" % msg, okk and msg == "advanced")
    # ...and the rung the escalation used to steal is still signable by its own
    # owner. This is the end-to-end proof the deadlock is gone, not moved.
    okk, msg = svc.act_on_step(pr1, fm, "approve")
    ok("the factory manager then signs his OWN rung — no dual_role deadlock (%s)"
       % msg, okk and msg == "advanced")
    ok("the escalation is on the audit trail",
       any(e["action"] == "escalated_stage" for e in svc.get_pr(pr1)["events"]))

    print("\n--- 1b. nobody above is free either -> refuse at SUBMIT ---------")
    # Same understaffing, but with no CEO: warehouse -> factory_manager is blocked
    # (he is needed on rung 2) and the chain ends there. The request must be
    # REFUSED, never half-signed and parked.
    activate("esc_ceo", on=0)
    pr1b, _ = svc.create_pr({"title": "No free superior", "department": "Production",
                             "currency": "EGP"},
                            [{"item": "Trolley", "qty": 1, "unit_price": 20000}],
                            wm, submit=False)
    okk, msg = svc.submit_pr(pr1b, wm)
    # POLICY: the rung is PARKED, the submit is NOT refused. Refusing stopped
    # senior staff raising any request at all on a one-holder-per-role chart (and
    # with no active cfo/ceo user), which is worse than one rung awaiting an admin.
    # The security guarantee is unchanged and asserted below: the requester still
    # cannot sign the parked rung, so this is not a weaker contract — availability
    # improved while self-approval remains impossible.
    ok("submit SUCCEEDS — the requester is not blocked from working (%s)" % msg,
       okk)
    ok("the full ladder was written, no rung skipped",
       conn.execute("SELECT COUNT(*) c FROM pr_steps WHERE pr_id=?",
                    (pr1b,)).fetchone()["c"] == len(C.build_ladder(20000)))
    ok("the request is circulating, not stranded as a draft",
       conn.execute("SELECT status FROM pr_requests WHERE id=?",
                    (pr1b,)).fetchone()["status"] == "pending")
    ok("the unresolvable rung is PARKED (esc_role='')",
       conn.execute("SELECT COUNT(*) c FROM pr_steps WHERE pr_id=? AND esc_role=''",
                    (pr1b,)).fetchone()["c"] >= 1)
    # THE assertion that must never weaken: parking must not become self-approval.
    ok("the REQUESTER still cannot sign the parked rung",
       svc.act_on_step(pr1b, wm, "approve", comment="mine")[1] == "self_approval")
    ok("an admin CAN clear the parked rung",
       svc.act_on_step(pr1b, admin, "approve", comment="unblock")[0] is True)
    ok("proc_admin was notified so a human can delegate or override",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE title=?",
                    ("Approval rung needs a signer",)).fetchone()["c"] > 0)
    activate("esc_ceo")

    print("\n--- 1c. a SECOND factory manager -> no over-climbing ------------")
    # With two factory managers, one signs the escalated warehouse rung and the
    # other his own: the escalation must stop at factory_manager, not climb to the
    # CEO. This is the guard against the fix over-firing.
    fm2 = mkuser(conn, "esc_fm2", "factory_manager")
    pr1c, _ = svc.create_pr({"title": "Two factory managers", "department": "Production",
                             "currency": "EGP"},
                            [{"item": "Fan", "qty": 1, "unit_price": 20000}], wm,
                            submit=True)
    st1c = steps_of(conn, pr1c)
    ok("the warehouse rung escalated to factory_manager, one level only (%r)"
       % st1c[0]["esc_role"], st1c[0]["esc_role"] == "factory_manager")
    okk, msg = svc.act_on_step(pr1c, fm, "approve")
    ok("factory manager #1 signs the escalated warehouse rung (%s)" % msg, okk)
    svc.add_quote(pr1c, {"vendor": "Fan Vendor", "amount": 20000}, pm)
    svc.act_on_step(pr1c, pm, "approve")            # rung 2: Purchasing (§4.1)
    okk, msg = svc.act_on_step(pr1c, fm, "approve")
    ok("...#1 is refused the factory rung he is eligible for: dual_role (%s)" % msg,
       not okk and msg == "dual_role")
    okk, msg = svc.act_on_step(pr1c, fm2, "approve")
    ok("factory manager #2 signs it instead (%s)" % msg, okk and msg == "advanced")
    activate("esc_fm2", on=0)

    print("\n--- 2. REGRESSION: a SECOND storekeeper -> nothing changes -----")
    store2 = mkuser(conn, "esc_store2", "storekeeper")
    pr2, _ = svc.create_pr({"title": "Second holder", "department": "Production",
                            "currency": "EGP"},
                           [{"item": "Gloves", "qty": 10, "unit_price": 0}], wm, submit=True)
    st2 = steps_of(conn, pr2)
    ok("NO escalation anywhere: every esc_role / esc_from is NULL",
       all(s["esc_role"] is None and s["esc_from"] is None for s in st2))
    ok("the warehouse rung is signed by the stage's own roles",
       svc.step_roles(st2[0]) == set(C.STAGE_ROLES["warehouse"]))
    ok("the colleague can sign it", svc.can_act_step(store2, st2[0]))
    okk, msg = svc.act_on_step(pr2, wm, "approve")
    ok("the requester is refused exactly as before (%s)" % msg,
       not okk and msg == "self_approval")
    okk, msg = svc.act_on_step(pr2, store2, "approve")
    ok("the second storekeeper signs -> 'advanced' (today's behaviour) (%s)" % msg,
       okk and msg == "advanced")

    print("\n--- 3. the superior IS the requester -> climb past them --------")
    activate("esc_store2", on=0)
    cfo = mkuser(conn, "esc_cfo", "cfo")
    ceo = mkuser(conn, "esc_ceo", "ceo")
    found, why = svc.resolve_escalation(conn, "warehouse", "esc_wm")
    ok("warehouse: requester=esc_wm -> escalated to {factory_manager} (%s)" % why,
       why == "escalated" and found == {"factory_manager"})
    # Now make the level above the requester as well: esc_cfo goes on leave and
    # delegates the CFO authority to esc_fm, so the ONLY person eligible for the
    # cfo level is esc_fm — who is the requester. The climb must pass him.
    conn.execute("UPDATE proc_escalations SET superior_role='cfo' "
                 "WHERE role_key='factory_manager'")
    conn.commit()
    svc.add_delegation("esc_cfo", "esc_fm", None, None, "leave cover", admin)
    activate("esc_cfo", on=0)
    ok("the cfo level's only eligible person is now the requester himself",
       svc.role_holders(conn, {"cfo"}) == {"esc_fm"})
    found, why = svc.resolve_escalation(conn, "factory_manager", "esc_fm")
    ok("factory_manager raised by its only holder, superior also him -> {ceo} (%s)"
       % why, why == "escalated" and found == {"ceo"})
    ok("the climb never resolved to the level the requester covers",
       found is not None and "cfo" not in found)
    svc.revoke_delegation(conn.execute(
        "SELECT id FROM proc_delegations WHERE to_user='esc_fm'").fetchone()["id"])
    activate("esc_cfo")
    conn.execute("UPDATE proc_escalations SET superior_role='ceo' "
                 "WHERE role_key='factory_manager'")
    conn.commit()

    print("\n--- 4. a CYCLIC chain an admin configured terminates -----------")
    conn.execute("UPDATE proc_escalations SET superior_role='warehouse_manager' "
                 "WHERE role_key='factory_manager'")
    conn.execute("UPDATE proc_escalations SET superior_role='factory_manager' "
                 "WHERE role_key='warehouse_manager'")
    conn.execute("UPDATE proc_escalations SET superior_role='warehouse_manager' "
                 "WHERE role_key='storekeeper'")
    conn.commit()
    activate("esc_fm", "esc_cfo", "esc_ceo", on=0)
    import threading
    box = {}

    def _run():
        box["r"] = svc.resolve_escalation(get_db(), "warehouse", "esc_wm")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(20)
    ok("resolve_escalation returned (no hang) on an A->B->A chain", not t.is_alive())
    ok("the cycle is treated as 'nobody above' (%r)" % (box.get("r"),),
       box.get("r") == (set(), "no_superior"))
    # restore the seeded chain
    for k, v in C.DEFAULT_ESCALATION.items():
        conn.execute("UPDATE proc_escalations SET superior_role=? WHERE role_key=?", (v, k))
    conn.commit()
    activate("esc_fm", "esc_cfo", "esc_ceo")

    print("\n--- 5. TERMINAL: the CEO raises a CEO-level request ------------")
    # §4.1 tier 5 (2,000,001 - 5,000,000) is the first band that puts the Managing
    # Director on the ladder at all; 250,000 was the paper form's CEO threshold and
    # under the DOAM stops at the Financial Director, so there would be no CEO rung
    # to strand.
    pr5, no5 = svc.create_pr({"title": "CEO purchase", "department": "Production",
                              "currency": "EGP"},
                             [{"item": "Line upgrade", "qty": 1, "unit_price": 2500000}],
                             ceo, submit=True)
    row5 = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr5,)).fetchone()
    # POLICY: nobody sits above the CEO, so his own rung is PARKED for an admin —
    # the request still circulates. Refusing it outright meant the CEO could not
    # raise a request at all, which is not a defensible governance rule.
    ok("the CEO's 2 500 000 request circulates instead of being stranded",
       row5["status"] == "pending")
    ok("the full value ladder was written, nothing skipped",
       conn.execute("SELECT COUNT(*) c FROM pr_steps WHERE pr_id=?",
                    (pr5,)).fetchone()["c"] == len(C.build_ladder(2500000)))
    ok("the CEO's own rung is parked (esc_role='') for an admin to clear",
       conn.execute("SELECT COUNT(*) c FROM pr_steps WHERE pr_id=? AND esc_role=''",
                    (pr5,)).fetchone()["c"] >= 1)
    # He is refused 'forbidden' rather than 'self_approval' here because the first
    # PENDING rung is Warehouse, which he may not sign anyway — the point is only
    # that parking never hands him a signature on his own request.
    _ceo_try = svc.act_on_step(pr5, ceo, "approve", comment="mine")
    ok("and the CEO still cannot sign his own request (%s)" % _ceo_try[1],
       _ceo_try[0] is False and _ceo_try[1] in ("self_approval", "forbidden"))
    ev5 = [e for e in svc.get_pr(pr5)["events"] if e["action"] == "escalation_parked"]
    ok("the parking is audited with a human-readable reason",
       ev5 and "only eligible signer" in (ev5[0]["detail"] or ""))
    notes = conn.execute(
        "SELECT target_user FROM notifications WHERE title=? ORDER BY id DESC",
        ("Approval rung needs a signer",)).fetchall()
    ok("proc_admin was notified via the bell (%d row(s))" % len(notes), len(notes) > 0)
    ok("the notified users are the platform / procurement admins",
       {r["target_user"] for r in notes} <= set(svc._proc_admins(conn)))
    ok("...and the admin IS among them",
       admin["username"] in {r["target_user"] for r in notes})
    okk, msg = svc.act_on_step(pr5, admin, "approve")
    ok("an admin CAN move the parked request along (%s)" % msg, okk)
    # a delegation is the documented way out: delegate the CEO's authority and the
    # parked rung becomes signable by a real human instead of only by an admin.
    svc.add_delegation("esc_ceo", "esc_fm", None, None, "cover", admin)
    _ceo_step = [s for s in steps_of(conn, pr5) if s["stage"] == "ceo"][0]
    _deleg_user = dict(conn.execute("SELECT * FROM users WHERE username=?",
                                    ("esc_fm",)).fetchone())
    ok("after delegating the CEO's authority, the delegate may sign the parked rung",
       svc.can_act_step(_deleg_user, _ceo_step) is True)
    ok("the parked ceo rung is still in the ladder, not dropped",
       any(s["stage"] == "ceo" for s in steps_of(conn, pr5)))
    svc.revoke_delegation(conn.execute(
        "SELECT id FROM proc_delegations WHERE to_user='esc_fm'").fetchone()["id"])

    print("\n--- 6. self-approval is impossible at any depth ----------------")
    pr6, _ = svc.create_pr({"title": "Depth", "department": "Production", "currency": "EGP"},
                           [{"item": "Belt", "qty": 1, "unit_price": 0}], wm, submit=True)
    s6 = steps_of(conn, pr6)[0]
    ok("the rung escalated (esc_role=%r)" % s6["esc_role"], bool(s6["esc_role"]))
    ok("the requester is not in the escalated rung's eligible list",
       "esc_wm" not in svc.eligible_approvers(conn, "warehouse",
                                              roles=svc.step_roles(s6)))
    okk, msg = svc.act_on_step(pr6, wm, "approve")
    ok("act_on_step refuses him (%s)" % msg,
       not okk and msg in ("self_approval", "forbidden"))
    ok("the rung is still pending, unsigned",
       steps_of(conn, pr6)[0]["status"] == "pending")

    print("\n--- 7. the dual-role rule still fires on an escalated ladder ---")
    # The escalated signer takes the warehouse rung; a delegation then makes him
    # eligible for the factory rung as well — which the dual-role rule must still
    # refuse. (The ladder builder deliberately avoids handing out an escalation
    # that WOULD collide; a delegation added afterwards can still create one, and
    # that is what this asserts.)
    signer = next(u for u in (ceo, cfo, fm) if svc.can_act_step(u, s6))
    okk, msg = svc.act_on_step(pr6, signer, "approve")
    ok("the escalated signer %s signs the warehouse rung (%s)" % (signer["username"], msg),
       okk)
    # Rung 2 is Purchasing, so the delegation that creates the collision is the
    # BUYER's — delegating the escalated signer's own authority to himself would
    # grant nothing and the assertion below would pass on native eligibility.
    svc.add_delegation(pm["username"], signer["username"], None, None, "cover", admin)
    ok("the delegation makes him eligible for the NEXT rung too",
       svc.can_act_step(signer, steps_of(conn, pr6)[1]))
    okk, msg = svc.act_on_step(pr6, signer, "approve")
    ok("the same person is refused on the NEXT rung: dual_role (%s)" % msg,
       not okk and msg == "dual_role")
    svc.revoke_delegation(conn.execute(
        "SELECT id FROM proc_delegations WHERE to_user=? ORDER BY id DESC",
        (signer["username"],)).fetchone()["id"])

    print("\n--- 8. the ladder itself is untouched for a normal requester ---")
    normal = mkuser(conn, "esc_normal", "normal_user")
    shapes = {}
    for total in (0, 9999, 10000, 24999, 25000, 48000, 99999, 100000, 5_000_000):
        # A DISTINCT item per request. DOAM 3.4 aggregates same-department
        # purchases of the SAME item inside 30 days, so nine identical "X" lines
        # routed on their running total instead of their own value — which is the
        # anti-splitting control working, not a ladder this section is measuring.
        prx, _ = svc.create_pr({"title": "Shape %d" % total, "department": "Production",
                                "currency": "EGP"},
                               [{"item": "X %d" % total, "qty": 1, "unit_price": total}],
                               normal, submit=True)
        shapes[total] = ladder_shape(conn, prx)
        expect = [(i, s) for i, s in enumerate(C.build_ladder(total), start=1)]
        ok("total %-9d: ladder == build_ladder(total) %r" % (total, [s for _, s in shapes[total]]),
           shapes[total] == expect)
        ok("total %-9d: no rung escalated for a requester who is not an approver" % total,
           all(s["esc_role"] is None and s["esc_from"] is None
               for s in steps_of(conn, prx)))
    # pricing gate + value-ladder reconciliation, unchanged
    prg, _ = svc.create_pr({"title": "Gate", "department": "Production", "currency": "EGP"},
                           [{"item": "Pump", "qty": 1, "unit_price": 0}], normal, submit=True)
    ok("an unpriced PR routes the demand stages only",
       [s for _, s in ladder_shape(conn, prg)] == C.DEMAND_STAGES)
    # One person per rung: SOD_ADMIN_EXEMPT is False, so one account cannot sign
    # two rungs of the same request and a single admin can no longer walk a ladder.
    okk, msg = svc.act_on_step(prg, wm, "approve")              # warehouse
    okk, msg = svc.act_on_step(prg, pm, "approve")              # purchasing, unpriced
    ok("PRICING GATE still blocks the purchasing stage while unpriced (%s)" % msg,
       not okk and msg == "needs_pricing")
    it = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (prg,)).fetchone()["id"]
    # §4.1 tier 4, so the CFO is already on the value ladder and Table 4's
    # unbudgeted L1 rung adds nothing — the shape here is build_ladder() exactly.
    svc.price_pr(prg, {it: 600000.0}, {"tax_rate": 0}, admin)
    ok("pricing reconciles the value ladder to build_ladder(600000) exactly",
       [s for _, s in ladder_shape(conn, prg)] == C.build_ladder(600000))
    ok("the appended value rungs were not escalated (normal requester)",
       all(s["esc_role"] is None for s in steps_of(conn, prg)))
    okk, msg = svc.act_on_step(prg, admin, "approve")
    ok("RFQ GATE still blocks purchasing at 600 000 with no quotes (%s)" % msg,
       not okk and msg == "needs_quotes")

    print("\n--- 8b. a value rung that joins AFTER pricing gets the same rule -")
    # The reconcile path can deadlock identically: the sole finance manager raises
    # an unpriced request, pricing appends Finance + CFO, and finance_manager ->
    # cfo would hand the finance rung to the only CFO — who is then refused his own
    # cfo rung. The climb must pass him, exactly as at submit time.
    fmg = mkuser(conn, "esc_fmg", "finance_manager")
    ok("esc_fmg is the only holder of a finance-stage role",
       svc.role_holders(conn, C.STAGE_ROLES["finance"]) == {"esc_fmg"})
    prr, _ = svc.create_pr({"title": "Priced later", "department": "Production",
                            "currency": "EGP"},
                           [{"item": "Cutter", "qty": 1, "unit_price": 0}], fmg, submit=True)
    ok("it submitted with the demand ladder only",
       [s for _, s in ladder_shape(conn, prr)] == C.DEMAND_STAGES)
    ok("the demand rungs were NOT escalated (others hold those roles)",
       all(s["esc_from"] is None for s in steps_of(conn, prr)))
    svc.act_on_step(prr, wm, "approve")                      # warehouse
    itr = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (prr,)).fetchone()["id"]
    # §4.1 tier 4: Finance AND the CFO both join here, which is what this case
    # needs — the sole CFO must be left free for his own rung.
    svc.price_pr(prr, {itr: 600000.0}, {"tax_rate": 0}, admin)
    ok("pricing appended the value rungs build_ladder(600000) requires",
       [s for _, s in ladder_shape(conn, prr)] == C.build_ladder(600000))
    fin = next(s for s in steps_of(conn, prr) if s["stage"] == "finance")
    cfo_step = next(s for s in steps_of(conn, prr) if s["stage"] == "cfo")
    ok("the finance rung escalated (%r)" % fin["esc_role"], bool(fin["esc_role"]))
    ok("...NOT onto the only CFO, who is needed on the cfo rung",
       "cfo" not in svc.step_roles(fin))
    ok("...it climbed to the CEO instead (%r)" % fin["esc_role"],
       svc.step_roles(fin) == {"ceo"})
    ok("the CEO can sign the escalated finance rung", svc.can_act_step(ceo, fin))
    ok("the requester cannot", not svc.can_act_step(fmg, fin))
    ok("and the CFO is left free for his OWN rung", svc.can_act_step(cfo, cfo_step))
    ok("the cfo rung itself was not escalated", cfo_step["esc_from"] is None)
    ok("the reconcile escalation is audited",
       any(e["action"] == "escalated_stage"
           and C.stage_label("finance") in (e["detail"] or "")
           for e in svc.get_pr(prr)["events"]))
    # the _busy hand-off itself: somebody who already signed a rung of this request
    # can never sign another (dual_role), so the climb must skip them too.
    found, why = svc.resolve_escalation(conn, "finance", "esc_fmg")
    ok("with nobody busy, finance escalates one level to {cfo} (%s)" % why,
       why == "escalated" and found == {"cfo"})
    found, why = svc.resolve_escalation(conn, "finance", "esc_fmg", _busy={"esc_cfo"})
    ok("with the CFO already spoken for, the same climb reaches {ceo} (%s)" % why,
       why == "escalated" and found == {"ceo"})
    activate("esc_fmg", on=0)

    print("\n--- 9. the settings editor -------------------------------------")
    okk, msg = svc.set_escalation("storekeeper", "storekeeper", admin)
    ok("a role cannot be its own superior (%s)" % msg,
       not okk and msg == "self_superior")
    ok("...and nothing was written",
       svc.escalation_map(conn)["storekeeper"] == "warehouse_manager")
    okk, msg = svc.set_escalation("no_such_role", "cfo", admin)
    ok("an unknown role is refused (%s)" % msg, not okk and msg == "unknown_role")
    okk, msg = svc.set_escalation("storekeeper", "no_such_role", admin)
    ok("an unknown superior is refused (%s)" % msg, not okk and msg == "unknown_role")
    okk, msg = svc.set_escalation("factory_manager", "cfo", admin)
    ok("the owner's flagged edit factory_manager -> cfo saves", okk)
    ok("...and the resolver consumes it immediately",
       svc.escalation_map(conn)["factory_manager"] == "cfo")
    ok("the edit is on the governance change log",
       any(r["action"] == "wf_escalation" for r in svc.governance_log(50)))
    okk, msg = svc.set_escalation("ceo", "", admin)
    ok("a blank superior is legitimate ('nobody above')", okk)
    svc.set_escalation("factory_manager", "ceo", admin)
    ok("escalation_rows() labels every row and lists what it signs",
       all(r["label"] and isinstance(r["signs"], list)
           for r in svc.escalation_rows("en")))

    print("\n--- 10. create_and_seed x3: idempotent, owner edits survive ----")
    svc.set_escalation("purchasing_manager", "ceo", admin)      # an owner edit
    before = conn.execute("SELECT COUNT(*) c FROM proc_escalations").fetchone()["c"]
    for _ in range(3):
        create_and_seed(conn)
        conn.commit()
    after = conn.execute("SELECT COUNT(*) c FROM proc_escalations").fetchone()["c"]
    ok("no duplicate rows after three more create_and_seed runs (%d)" % after,
       before == after)
    ok("the owner's edited superior survived every re-seed (not reset to cfo)",
       svc.escalation_map(conn)["purchasing_manager"] == "ceo")
    ok("a blanked superior stays blank across re-seeds",
       (svc.escalation_map(conn).get("ceo") or "") == "")
    svc.set_escalation("purchasing_manager", "cfo", admin)
    svc.set_escalation("ceo", "", admin)

    print("\n--- 11. the PDFs carry the deviation ---------------------------")
    b1 = svc.get_pr(pr1)
    lines = pdfgen._esc_lines(b1["steps"])
    esc1 = next(s for s in b1["steps"] if s.get("esc_from"))
    ok("the PR PDF prints one escalation line naming both ends of the deviation",
       len(lines) == 1 and "escalated from" in lines[0]
       and pdfgen._role_names(esc1["esc_from"]) in lines[0]
       and pdfgen._role_names(esc1["esc_role"]) in lines[0])
    try:
        raw = pdfgen.pr_pdf(b1)
        ok("pr_pdf still renders (%d bytes)" % len(raw), len(raw) > 1000)
        raw = pdfgen.po_pdf(b1)
        ok("po_pdf still renders with the deviation block (%d bytes)" % len(raw),
           len(raw) > 1000)
    except ImportError:
        ok("reportlab not installed — PDF render skipped", True)
        ok("reportlab not installed — PDF render skipped", True)
    ok("a PR with no escalation prints no deviation line",
       pdfgen._esc_lines(svc.get_pr(pr2)["steps"]) == [])

    print("\n--- 12. trilingual prose for the new rule ---------------------")
    row = dict(conn.execute("SELECT body, body_ar, body_tr FROM proc_doc "
                            "WHERE section='sod_escalation'").fetchone())
    ok("the sod_escalation block is seeded in all three languages",
       (row["body"] or "").strip() and (row["body_ar"] or "").strip()
       and (row["body_tr"] or "").strip())
    ok("the Arabic body carries Arabic script", ARABIC.search(row["body_ar"] or ""))
    ok("the Turkish body differs from the English", row["body_tr"] != row["body"])
    ok("the seeded text is exactly i18n_text.py",
       row["body_ar"] == T.DOC_AR["sod_escalation"]
       and row["body_tr"] == T.DOC_TR["sod_escalation"])
    for lg in ("en", "ar", "tr"):
        v = svc.workflow_view("Production", lg)
        ok("[%s] workflow_view carries the escalation prose + the live chain" % lg,
           v["escalation"]["body"].strip() and len(v["escalation"]["chain"]) >= 8)
        ok("[%s] the prose is in %s, not the English default" % (lg, lg),
           (v["escalation"]["body"] == C.DOC_SECTIONS["sod_escalation"]) == (lg == "en"))
        ok("[%s] every UI label the new sections need is present" % lg,
           all(svc.labels(lg)["ui"].get(k) for k in
               ("esc_title", "esc_sub", "esc_role", "esc_superior", "esc_none",
                "esc_signs", "esc_save", "esc_tag", "esc_from_to", "esc_stuck",
                "esc_note", "esc_blocked_flash")))
    ok("workflow_view STILL reports 6 stages / 6 gates / 9 statuses / 4 knobs",
       len(svc.workflow_view("Production")["stages"]) == len(C.LADDER)
       and len(svc.workflow_view("Production")["gates"]) == 6
       and len(svc.workflow_view("Production")["statuses"]) == len(C.PR_STATUSES)
       and len(svc.workflow_view("Production")["knobs"]) == 4)
    viewer = mkuser(conn, "esc_view", "executive_viewer")
    conn.close()
# =======================================================================
# Everything below runs inside ONE app context so the test client shares it;
# g["user"] is popped before every request because auth.current_user() caches the
# user (and their lang_pref) per context.
DICTS = {lg: json.load(io.open(str(REPO / "app" / "static" / "i18n" / (lg + ".json")),
                               encoding="utf-8")) for lg in ("en", "ar", "tr")}

with app.app_context():
    from flask import g
    from app.db import get_db
    from app.approvals import services as svc

    conn = get_db()
    vid = conn.execute("SELECT id FROM users WHERE username='esc_view'").fetchone()["id"]
    pr_page = conn.execute(
        "SELECT id FROM pr_requests WHERE title='Owner case'").fetchone()["id"]
    PAGES = ["/procurement/workflow", "/procurement/settings",
             "/procurement/pr/%d" % pr_page]

    def set_lang(lang):
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
        conn.commit()

    def fetch(cl, path):
        g.pop("user", None)          # drop the per-context current_user cache
        return cl.get(path)

    print("\n--- 13. pages render 200 in en / ar / tr, keys all resolve ------")
    cl = app.test_client()
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    for lg in ("en", "ar", "tr"):
        set_lang(lg)
        ui = svc.labels(lg)["ui"]
        for path in PAGES:
            r = fetch(cl, path)
            ok("GET %s [%s] -> 200 (raw)" % (path, lg), r.status_code == 200)
            html = r.get_data(as_text=True)
            p = Extract()
            p.feed(html)
            missing = sorted(k for k in p.keys if k not in DICTS[lg])
            ok("%s [%s]: all %d data-i18n keys exist in %s.json%s"
               % (path, lg, len(p.keys), lg,
                  ("  << " + ", ".join(missing[:5])) if missing else ""),
               not missing)
            if path.endswith("/workflow"):
                ok("workflow [%s] documents the escalation rule in %s" % (lg, lg),
                   ui["esc_title"] in html
                   and svc.workflow_view(None, lg)["escalation"]["body"][:60] in html)
                ok("workflow [%s] lists the chain as configured" % lg,
                   ui["esc_superior"] in html and "warehouse_manager" in html)
            if path.endswith("/settings"):
                ok("settings [%s] renders the chain editor in %s" % (lg, lg),
                   ui["esc_title"] in html and 'name="sup_storekeeper"' in html
                   and ui["esc_none"] in html)
            if "/pr/" in path:
                ok("PR page [%s] shows the escalation tag in %s" % (lg, lg),
                   ui["esc_tag"] in html)
                ok("PR page [%s] explains WHY the rung moved, in %s" % (lg, lg),
                   ui["esc_note"] in html)
                # both ends of the deviation, read off the step itself rather than
                # hardcoded — the climb may legitimately land higher than one level
                esc = conn.execute("SELECT esc_role, esc_from FROM pr_steps "
                                   "WHERE pr_id=? AND esc_from IS NOT NULL",
                                   (pr_page,)).fetchone()
                ok("PR page [%s] names the from/to roles in %s" % (lg, lg),
                   svc.role_names(esc["esc_role"], lg) in html
                   and svc.role_names(esc["esc_from"], lg) in html)
            ok("%s [%s] leaks no raw UI key" % (path, lg),
               "esc_from_to" not in html and "esc_blocked_flash" not in html)
        if lg == "ar":
            ok("[ar] the escalation prose really is Arabic script",
               ARABIC.search(svc.workflow_view(None, "ar")["escalation"]["body"]))

    set_lang("en")
    g.pop("user", None)
    r = cl.post("/procurement/settings/escalation",
                data={"_csrf": "tok", "department": "Production",
                      "role_key": "storekeeper", "sup_storekeeper": "cfo"})
    ok("proc_admin POST /procurement/settings/escalation -> 302 (got %d)" % r.status_code,
       r.status_code == 302)
    ok("the admin's save is in force", svc.escalation_map(conn)["storekeeper"] == "cfo")
    g.pop("user", None)
    r = cl.post("/procurement/settings/escalation",
                data={"_csrf": "tok", "role_key": "cfo", "sup_cfo": "cfo"})
    ok("a self-superior POST is refused with a message, not a write (302)",
       r.status_code == 302 and svc.escalation_map(conn)["cfo"] == "ceo")

    print("\n--- 14. a proc_view-only user is refused, and writes nothing ----")
    cl2 = app.test_client()
    with cl2.session_transaction() as s:
        s["uid"] = vid
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    g.pop("user", None)
    r = cl2.post("/procurement/settings/escalation",
                 data={"_csrf": "tok", "role_key": "storekeeper",
                       "sup_storekeeper": "normal_user"})
    ok("POST as a proc_view-only viewer -> 403 raw (got %d)" % r.status_code,
       r.status_code == 403)
    g.pop("user", None)
    r = cl2.get("/procurement/settings")
    ok("GET /procurement/settings as a viewer -> 403 raw (got %d)" % r.status_code,
       r.status_code == 403)
    ok("the refused viewer POST changed nothing",
       svc.escalation_map(conn)["storekeeper"] == "cfo")

    admin_row = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    svc.set_escalation("storekeeper", "warehouse_manager", admin_row)
    ok("chain restored to the seeded default",
       svc.escalation_map(conn)["storekeeper"] == "warehouse_manager")
    conn.close()

print("\n%d checks, %d passed, %d failed"
      % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
print("ALL GREEN" if all(PASS) else "FAILURES ABOVE")
sys.exit(0 if all(PASS) else 1)
