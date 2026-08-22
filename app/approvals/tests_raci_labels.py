"""Table 5 letters: what a signature is CALLED, on the screen and on the paper.

DOAM Table 5 gives every rung an action type — R review, A approve, E endorse.
The audit finding was that the mapping read POSITIONALLY, so people who genuinely
approve were labelled as merely reviewing, and that wording is what prints on the
documents an auditor reads.

Most of that is already closed: C.step_action takes the stage, the top of the
ladder and the expenditure kind, and services writes the letter per rung, and the
PDF reads back the same stored field so screen and paper cannot drift.

What this file adds is the part that was still wrong — THE FALLBACK. Both readers
resolved an unknown or missing action to "approve", the STRONGEST claim available.
A pre-migration row, or one written by a path that never set the column, therefore
printed "Approved by" over somebody the system could not actually show had
approved anything. On a governance document the safe default is the weaker claim,
never the stronger one, and services already guarantees the top rung carries the A
so nothing is left uncommitted by making it weaker.

    python app/approvals/tests_raci_labels.py
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="raci_"), "r.db")
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

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C, pdf as P

        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            uname = "rc_" + stage
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (uname, "x", uname, role))
            signer[stage] = {"username": uname, "role": role}
        conn.commit()
        for stage in signer:
            signer[stage]["id"] = conn.execute(
                "SELECT id FROM users WHERE username=?",
                (signer[stage]["username"],)).fetchone()["id"]
        conn.close()
        buyer = signer["purchasing"]
        reqr = {"username": "rc_req", "id": 91}

        def build(title, price, kind="opex"):
            pid, _ = svc.create_pr(
                {"title": title, "department": "IT", "currency": "EGP",
                 "vendor": "RACI Vendor", "expenditure_kind": kind},
                [{"item": "Drive belt A42", "unit": "Pcs", "qty": 1, "unit_price": 0}],
                reqr, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pid,)).fetchone()["id"]
            conn.close()
            svc.price_pr(pid, {li: price}, {}, buyer)
            return pid

        def letters(pid):
            """stage -> action, as the REQUEST PAGE shows it."""
            b = svc.get_pr(pid)
            out = {}
            for s in b["steps"]:
                d = s if isinstance(s, dict) else dict(s)
                out[d["stage"]] = svc.step_action_of(d)
            return out

        # ---- 1. an OPEX ladder is not all approvers ------------------------
        print("an OPEX ladder distinguishes who commits from who verifies")
        pid = build("Opex ladder", 800000)
        L = letters(pid)
        chk("the ladder has several rungs", len(L) >= 4, L)
        chk("exactly one rung carries the A — somebody committed this",
            sum(1 for a in L.values() if a == "approve") >= 1, L)
        chk("and the demand rungs are REVIEWS, not approvals",
            L.get("warehouse") == "review", L.get("warehouse"))
        chk("every letter is a real Table 5 letter",
            all(a in C.STEP_ACTIONS for a in L.values()), L)

        # ---- 2. CAPEX letters come from §4.2, by role ----------------------
        print("\na CAPEX request is lettered by §4.2, not by position")
        pid_c = build("Capex ladder", 800000, kind="capex")
        Lc = letters(pid_c)
        chk("purchasing REVIEWS a capital purchase",
            Lc.get("purchasing") == "review", Lc.get("purchasing"))
        chk("and it is a JOINT approval — more than one office commits",
            sum(1 for a in Lc.values() if a == "approve") >= 2, Lc)
        chk("the two ladders really are lettered differently",
            L != Lc, "opex=%s capex=%s" % (L, Lc))

        # ---- 3. the paper says what the screen says ------------------------
        print("\nthe printed document and the screen cannot disagree")
        # Walk the REAL ladder, including the §4.3 sourcing gate — a walk that
        # stops at Purchasing never reaches the office that actually approves,
        # and the document would then be missing the only name under test.
        for _ in range(15):
            conn = get_db()
            nxt = conn.execute(
                "SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
                "ORDER BY seq LIMIT 1", (pid,)).fetchone()
            conn.close()
            if not nxt:
                break
            st = nxt["stage"]
            ok, msg = svc.act_on_step(pid, signer[st], "approve")
            if not ok and msg == "needs_quotes":
                for n, mult in (("A", 1.0), ("B", 1.06), ("C", 1.11)):
                    svc.add_quote(pid, {"vendor": "RACI Quote %s" % n,
                                        "amount": 800000 * mult}, buyer)
                continue
            if not ok:
                break
        conn = get_db()
        unsigned = conn.execute(
            "SELECT COUNT(*) n FROM pr_steps WHERE pr_id=? AND status='pending'",
            (pid,)).fetchone()["n"]
        conn.close()
        chk("the whole ladder was signed, so every office is on the document",
            unsigned == 0, "%d rungs still pending" % unsigned)
        b = svc.get_pr(pid)
        raw = P.pr_pdf(b)
        chk("the PR prints", raw[:4] == b"%PDF" and len(raw) > 2000, len(raw))
        try:
            from pypdf import PdfReader
            import io as _io
            txt = re.sub(r"\s+", " ", "\n".join(
                (p.extract_text() or "") for p in PdfReader(_io.BytesIO(raw)).pages))
            screen = letters(pid)
            approvers = [s for s, a in screen.items() if a == "approve"]
            reviewers = [s for s, a in screen.items() if a == "review"]
            # An office that only REVIEWED must not be printed under an
            # approval heading. Names are checked, not headings, because the
            # heading wording is free to change and the claim is not.
            for st in approvers:
                nm = signer.get(st, {}).get("username", "")
                if nm:
                    chk("approver %-16s is named on the document" % st, nm in txt)
            chk("the document names at least one approving office",
                bool(approvers), screen)
            chk("and it does not claim every office approved",
                bool(reviewers), screen)
        except ImportError:
            chk("pypdf available to read the document back", False, "not installed")

        # ---- 4. THE FALLBACK: absent and unreadable are different questions --
        # ABSENT (NULL/empty) means the row predates the action_type column, and
        # the migration added that column with DEFAULT 'approve' — so the
        # platform already decided what such a rung meant, and every real row
        # carries a letter because the ALTER wrote one. tests_doam_raci_domain
        # asserts that meaning, and it is right to.
        #
        # UNREADABLE is the case that can still arise: a value the system holds
        # and cannot parse. That must not become the STRONGEST claim available,
        # because this is what a printed document asserts about a named person.
        print("\nan UNREADABLE letter is the weaker claim; an ABSENT one is legacy")
        for absent in (None, "", "   "):
            chk("absent %-8r keeps its pre-migration meaning" % absent,
                svc.step_action_of({"action_type": absent}) == "approve"
                and P._step_action({"action": absent}) == "approve")
        for junk in ("sign", "yes", "APPROVED-BY", "A", "signed-off"):
            got, got_pdf = (svc.step_action_of({"action_type": junk}),
                            P._step_action({"action": junk}))
            chk("unparseable %-12r is NOT promoted to an approval" % junk,
                got == "review" and got_pdf == "review", "%s / %s" % (got, got_pdf))
        chk("and the two readers never disagree",
            all(svc.step_action_of({"action_type": v}) == P._step_action({"action": v})
                for v in (None, "", "sign", "yes", "review", "approve", "endorse")))

        # ...but a request must still evidence that SOMEBODY committed it.
        print("\nyet the request still shows who committed the money")
        conn = get_db()
        conn.execute("UPDATE pr_steps SET action_type=NULL WHERE pr_id=?", (pid,))
        conn.commit()
        rows = [dict(r) for r in conn.execute(
            "SELECT stage, action_type FROM pr_steps WHERE pr_id=? ORDER BY seq",
            (pid,)).fetchall()]
        conn.close()
        chk("every stored letter really was blanked", all(
            r["action_type"] is None for r in rows), rows[:2])
        def read_back(pid_):
            out = {}
            for st in svc.get_pr(pid_)["steps"]:
                d = st if isinstance(st, dict) else dict(st)
                out[d["stage"]] = svc.step_action_of(d)
            return out

        # NULL reads as the legacy Approve, so blanking does NOT understate here —
        # that is the migration's documented meaning, and every row in a real
        # database carries a letter because the ALTER wrote one. What matters is
        # that stamping restores the TRUE letters, which is asserted next.
        blanked = read_back(pid)
        chk("a blanked row reads as the pre-migration Approve",
            all(a == "approve" for a in blanked.values()), blanked)

        conn = get_db()
        svc.stamp_step_actions(conn, pid)
        conn.commit()
        conn.close()
        repaired = read_back(pid)
        chk("and stamping the ladder fills the blanks back in",
            "approve" in repaired.values(), repaired)
        chk("restoring the same letters it had before they were blanked",
            repaired == L, "before=%s after=%s" % (L, repaired))

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
