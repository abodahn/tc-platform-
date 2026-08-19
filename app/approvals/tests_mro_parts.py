"""A machine part whose name contains a material word is still a machine part.

The DOAM §3.4 sales-order gate matches keywords over free text. When the keyword
list was widened to catch 46 of 46 real direct materials, it began catching
sewing-machine COMPONENTS too — "thread guide", "zipper foot", "denim needle" —
and demanded a sales order a maintenance technician has no business citing. A
cost centre did not clear it either: the request simply could not be submitted.
Measured at the time: 11 of 15 realistic spare names refused.

The fix is compound-noun adjacency, and the danger in it is over-exemption. An
earlier attempt exempted any line containing a part noun anywhere, which let a
material buy ride in appended to a genuine spare line. Both directions are
checked here, and the smuggling case is the one that matters most: it is the
only one where being wrong hands somebody a way around the control.
"""
import sys

sys.path.insert(0, r"D:\TC platform\tc-platform-beta")
from app.approvals import constants as C   # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


# --- machine parts must NOT demand a sales order --------------------------
PARTS = [
    "Thread guide", "Thread stand", "Zipper foot", "Button feeder",
    "Cotton reel holder", "Bobbin case", "Needle plate", "Fabric cutter blade",
    "Elastic tape roller", "Label applicator head", "Yarn tension spring",
    "Denim needle DBx1 90/14", "Leather belt for motor", "Wash pump seal",
    "Presser foot assembly", "Twill tape guide bracket", "Knit fabric roller arm",
]
print("machine parts — a technician must be able to raise these")
for p in PARTS:
    chk(p, C.cost_object_required([p]) is None, C.cost_object_required([p]))

# --- real materials must STILL be gated -----------------------------------
MATERIALS = [
    "Denim rolls 12oz", "Cotton twill 150gsm for style 4471", "Zippers #5 YKK",
    "Sewing thread 40/2 tex", "Buttons 4-hole 18L", "Interlining fusible",
    "Velcro hook and loop 25mm", "Care labels and hangtags",
    "Cartons and polybags", "Cotton greige 60s", "Elastic 25mm for waistband",
    "Poplin shirting fabric", "Yarn cones 30/1",
]
print("\ndirect materials — these must still demand a sales order")
for m in MATERIALS:
    chk(m, C.cost_object_required([m]) == "sales_order", C.cost_object_required([m]))

# --- the smuggling case ----------------------------------------------------
# A genuine spare line with a material buy appended to the description. This is
# the case an earlier fix broke, and the one worth being strict about: exempting
# it would hand anyone a way to buy fabric with no sales order by naming a part.
print("\nsmuggling — a material appended to a genuine spare line")
SMUGGLE = [
    "guide for the washing line feeder plus cotton twill fabric 100% CO",
    "cotton twill fabric for the guide",
    "presser foot and 200m of denim",
    "bobbin case, and 500 zippers #5",
]
for s in SMUGGLE:
    chk(s[:52], C.cost_object_required([s]) == "sales_order", C.cost_object_required([s]))

# --- the rule itself, at its edges -----------------------------------------
print("\nthe adjacency rule")
chk("a material run immediately followed by a part noun is a compound",
    C.cost_object_required(["thread guide"]) is None)
chk("one word of slack is allowed (elastic TAPE roller)",
    C.cost_object_required(["elastic tape roller"]) is None)
chk("two words of slack is NOT — that is where smuggling starts",
    C.cost_object_required(["cotton fabric for the guide"]) == "sales_order")
chk("a part noun alone, with no material word, needs no sales order",
    C.cost_object_required(["bobbin case"]) is None)
chk("a material word with no part noun after it is still a material",
    C.cost_object_required(["denim"]) == "sales_order")
chk("the gsm / style-number shape check still wins over everything",
    C.cost_object_required(["thread guide for cotton 150gsm style 4471"]) == "sales_order")

print("\n" + ("RESULT: ALL GREEN" if ok else "RESULT: FAILURES ABOVE"))
sys.exit(0 if ok else 1)
