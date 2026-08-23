"""Every message this module shows a person exists in all three languages.

The module's SCREENS were translated — four thousand keys in app/static/i18n —
while its MESSAGES were not. Six submit refusals resolved through labels(); the
other ninety-odd were English sentences written inline in the routes. So an
Arabic user read an Arabic screen and an English sentence the moment anything
went wrong, which is the moment they most need to read it. Nobody noticed for
months, because nothing was failing: the messages had simply never been
translated, and nothing said so.

This file is what says so. It extracts every user-facing string from the routes
the same way the translation was gathered, and fails when one has no entry in
app/approvals/messages.py — so a message added next month cannot ship
English-only unnoticed.

It also holds the fragile part of the design. Messages are keyed by their ENGLISH
TEXT rather than by a key, which kept the routes untouched (ninety-three call
sites, changing no behaviour) at the price of an edited sentence silently
orphaning its translation. That price is only acceptable while this check is
here.

    python app/approvals/tests_messages_i18n.py
"""
import ast
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "approvals.py"


def user_facing_strings(path):
    """Every string that reaches a reader through flash() in `path`."""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = set()

    def add(s):
        s = (s or "").strip()
        if len(s) < 10 or not (s[0].isupper() or s[0].isdigit()):
            return
        if re.match(r"^[A-Z_]+$", s):
            return
        if "SELECT " in s or "UPDATE " in s or "INSERT " in s:
            return
        out.add(s)

    def strings(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            add(n.value)
        elif isinstance(n, ast.Dict):
            for v in n.values:
                strings(v)
        elif isinstance(n, ast.Call):
            for a in list(n.args) + [k.value for k in n.keywords]:
                strings(a)
            if isinstance(n.func, ast.Attribute):
                strings(n.func.value)
        elif isinstance(n, ast.BinOp):
            strings(n.left)
            strings(n.right)
        elif isinstance(n, ast.IfExp):
            strings(n.body)
            strings(n.orelse)
        elif isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.Constant):
                    add(v.value)

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "flash":
                for a in node.args:
                    strings(a)
            self.generic_visit(node)

    V().visit(tree)
    return out


def run():
    sys.path.insert(0, str(ROOT))
    from app.approvals.messages import MESSAGES, translate

    ok = [True]

    def chk(label, cond, extra=""):
        ok[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label
              + ((" | " + str(extra)) if extra else ""))

    msgs = user_facing_strings(ROUTES)
    print("the procurement routes show %d distinct messages" % len(msgs))
    chk("there are messages to check at all", len(msgs) > 50, len(msgs))

    missing = sorted(m for m in msgs if m not in MESSAGES)
    chk("every one has an Arabic and Turkish entry",
        not missing, "%d missing, e.g. %r" % (len(missing), missing[:2]) if missing else "")

    # A translation that is the English string back is not a translation. It is
    # the failure this whole file exists to catch, wearing a different hat.
    same = [m for m, row in MESSAGES.items()
            if row.get("ar", "").strip() == m.strip()
            or row.get("tr", "").strip() == m.strip()]
    chk("no 'translation' is just the English copied over",
        not same, same[:2])

    blank = [m for m, row in MESSAGES.items()
             if not (row.get("ar") or "").strip() or not (row.get("tr") or "").strip()]
    chk("none is left blank", not blank, blank[:2])

    # A dropped %s crashes the message at the moment somebody needs to read it.
    bad_ph = []
    for m, row in MESSAGES.items():
        want = len(re.findall(r"%[sdfg]", m))
        for lang in ("ar", "tr"):
            if len(re.findall(r"%[sdfg]", row.get(lang) or "")) != want:
                bad_ph.append((m[:40], lang))
    chk("every placeholder survives translation", not bad_ph, bad_ph[:3])

    print("\nand the resolver actually resolves")
    sample = next((m for m in sorted(MESSAGES) if MESSAGES[m].get("ar")), None)
    if sample:
        chk("an Arabic reader gets Arabic",
            translate(sample, "ar") == MESSAGES[sample]["ar"],
            translate(sample, "ar")[:40])
        chk("a Turkish reader gets Turkish",
            translate(sample, "tr") == MESSAGES[sample]["tr"],
            translate(sample, "tr")[:40])
        chk("an English reader gets the original unchanged",
            translate(sample, "en") == sample)
    chk("a string nobody translated passes through rather than vanishing",
        translate("Some brand new message.", "ar") == "Some brand new message.")

    print("\n" + ("RESULT: ALL GREEN" if ok[0] else "RESULT: FAILURES ABOVE"))
    return ok[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
