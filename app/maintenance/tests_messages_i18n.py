"""Every maintenance message a person reads exists in all three languages.

Fifty of the fifty-one flash() calls in the maintenance routes pass an m_* key
that app.js resolves from app/static/i18n. The other fifteen were plain English
sentences, because they carry a value — a ticket number, a report reference, an
exception name — and t() in app.js is an exact lookup, so a key with a value
stuck on the end resolves to nothing at all.

Those fifteen are translated on the server now. This file is what stops a
sixteenth from shipping English-only: it pulls every flash() literal out of the
routes and fails when one has no entry in app/maintenance/messages.py.

It also guards the mechanism itself. A message with a value must be translated
as a TEMPLATE and formatted afterwards, so an entry whose English carries %s
must carry the same %s in Arabic and Turkish, or the format call raises at the
moment somebody most needs to read the message.

    python app/maintenance/tests_messages_i18n.py
"""
import ast
import io
import json
import re
import sys
from pathlib import Path

# These print Arabic. A cp1252 console (Git Bash) raises
# UnicodeEncodeError part-way through and every check below the
# first Arabic line silently never runs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "app" / "routes" / "maintenance.py"


def flash_literals(path):
    """Every English sentence a flash() in `path` shows a reader.

    Returns the TEMPLATE where the message carries a value, because that is what
    is looked up: _msg() is applied before the % formatting.
    """
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    out = set()

    def text(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.JoinedStr):                     # f"..."
            return "".join(v.value if isinstance(v, ast.Constant) else "%s"
                           for v in n.values)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
            return text(n.left)                              # the template
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            a, b = text(n.left), text(n.right)
            return None if a is None or b is None else a + b
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None)
            if name == "_msg" and n.args:                    # _msg("...") % v
                return text(n.args[0])
        return None

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "flash" and node.args:
                for arg in ([node.args[0]] if not isinstance(node.args[0], ast.IfExp)
                            else [node.args[0].body, node.args[0].orelse]):
                    s = (text(arg) or "").strip()
                    # an m_* key is resolved in the browser, not here
                    if len(s) >= 10 and not re.fullmatch(r"m_[a-z0-9_]+", s):
                        out.add(s)
            self.generic_visit(node)

    V().visit(tree)
    return out


def run():
    sys.path.insert(0, str(ROOT))
    from app.maintenance.messages import MESSAGES, translate

    ok = [True]

    def chk(label, cond, extra=""):
        ok[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label
              + ((" | " + str(extra)) if extra else ""))

    msgs = flash_literals(ROUTES)
    print("the maintenance routes flash %d English sentences" % len(msgs))
    chk("there are messages to check at all", len(msgs) >= 14, len(msgs))

    missing = sorted(m for m in msgs if m not in MESSAGES)
    chk("every one has an Arabic and Turkish entry", not missing,
        "%d missing: %r" % (len(missing), missing[:2]) if missing else "")

    same = [m for m, r in MESSAGES.items()
            if r.get("ar", "").strip() == m.strip() or r.get("tr", "").strip() == m.strip()]
    chk("no 'translation' is just the English copied over", not same, same[:2])

    blank = [m for m, r in MESSAGES.items()
             if not (r.get("ar") or "").strip() or not (r.get("tr") or "").strip()]
    chk("none is left blank", not blank, blank[:2])

    # A dropped %s is a crash, not a typo: the route formats what comes back.
    bad = []
    for m, r in MESSAGES.items():
        want = len(re.findall(r"%[sdfg]", m))
        for lang in ("ar", "tr"):
            if len(re.findall(r"%[sdfg]", r.get(lang) or "")) != want:
                bad.append((m[:40], lang))
    chk("every placeholder survives translation", not bad, bad[:3])

    # ...and the formatting the routes actually do must not raise.
    raised = []
    for m, r in MESSAGES.items():
        n = len(re.findall(r"%[sdfg]", m))
        if n:
            for lang in ("ar", "tr"):
                try:
                    translate(m, lang) % tuple(["1"] * n)
                except Exception as exc:
                    raised.append((m[:40], lang, type(exc).__name__))
    chk("a translated message still formats", not raised, raised[:3])

    print("\nand the resolver actually resolves")
    chk("an Arabic reader gets Arabic",
        translate("Report approved.", "ar") == MESSAGES["Report approved."]["ar"])
    chk("a Turkish reader gets Turkish",
        translate("Report rejected.", "tr") == MESSAGES["Report rejected."]["tr"])
    chk("an English reader gets the original unchanged",
        translate("Report approved.", "en") == "Report approved.")
    chk("an m_* key passes through for app.js to resolve",
        translate("m_saved", "ar") == "m_saved")
    chk("an untranslated sentence still reaches the reader",
        translate("Some brand new message.", "ar") == "Some brand new message.")

    print("\nthe DOAM field labels, which is what a refusal tells you to go and fix")
    from app.maintenance.messages import FIELD_LABELS, labels
    from app.maintenance.eng_justification import REQUIRED_FIELDS

    # Asked of _missing() rather than assembled here. stock_on_hand is checked
    # apart from REQUIRED_FIELDS (0 is an answer, blank is not), so its label
    # lives only inside that function — retyping it here would let a rename ship
    # English inside an Arabic refusal with this check still green.
    from app.maintenance.eng_justification import _missing
    all_blank = {col: None for col, _lbl in REQUIRED_FIELDS}
    all_blank["stock_on_hand"] = None
    required = set(_missing(all_blank))
    chk("the labels come from the code that produces them",
        len(required) == len(REQUIRED_FIELDS) + 1, sorted(required))

    gap = sorted(required - set(FIELD_LABELS))
    chk("every mandatory field's label is translated", not gap, gap)

    dead = sorted(set(FIELD_LABELS) - required)
    chk("and none has been orphaned by a rename", not dead, dead)

    bad = [k for k, r in FIELD_LABELS.items()
           if not (r.get("ar") or "").strip() or not (r.get("tr") or "").strip()
           or r.get("ar") == k or r.get("tr") == k]
    chk("none is blank or the English copied over", not bad, bad[:2])

    joined = labels("root cause, criticality", "ar")
    chk("the list is joined with the Arabic comma, not a Latin one",
        "،" in joined and "," not in joined, joined)
    chk("an English reader still gets the English list",
        labels("root cause, criticality", "en") == "root cause, criticality")
    chk("a label nobody translated stays in the list rather than vanishing",
        "some new field" in labels("root cause, some new field", "ar"))
    chk("a list can be passed as a list, which is how the report page has it",
        labels(["root cause"], "tr") == FIELD_LABELS["root cause"]["tr"])

    print("\nand every key the screens ask app.js to resolve")
    dicts = {}
    for lang in ("en", "ar", "tr"):
        with io.open(str(ROOT / "app" / "static" / "i18n" / (lang + ".json")),
                     encoding="utf-8") as fh:
            dicts[lang] = json.load(fh)
    absent = []
    for path in sorted((ROOT / "app" / "templates" / "maintenance").glob("*.html")):
        page = io.open(str(path), encoding="utf-8").read()
        # literal keys only: a few are assembled in JS from a loop variable
        for key in set(re.findall(r'data-i18n="([^"{}+]+)"', page)):
            for lang in ("en", "ar", "tr"):
                if key not in dicts[lang]:
                    absent.append((path.name, key, lang))
    chk("every data-i18n key in a maintenance template resolves in en, ar and tr",
        not absent, "%d missing, e.g. %s" % (len(absent), absent[:2]) if absent else "")

    print("\n" + ("RESULT: ALL GREEN" if ok[0] else "RESULT: FAILURES ABOVE"))
    return ok[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
