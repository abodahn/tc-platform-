"""
Procurement cycle training guide — one section per role, in cycle order.

The words live in app/static/i18n/{en,ar,tr}.json under proc.guide.*; this module
only knows the ORDER of the sections and which roles each one is for. A section's
items are discovered from the English dictionary (proc.guide.<sid>.step.1, .2, ...),
so adding a step is a JSON edit, and tests_guide.py proves all three languages
carry the same keys.

Inside a guide string, [[btn:<key>]] and [[ui:<key>]] are replaced by the label
the real screen prints for <key> (same dictionary, same key), so the guide can
never name a button the screen calls something else.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup, escape

from app.approvals import constants as C

_I18N = Path(__file__).resolve().parents[1] / "static" / "i18n"
LANGS = ("en", "ar", "tr")
CHIP = re.compile(r"\[\[(btn|ui):([a-z0-9_.]+)\]\]")

# (section id, ladder stage or None). Order = the order a request travels.
SECTIONS = [
    ("req", None), ("hrd", "hr_head"), ("wh", "warehouse"), ("ictl", "item_control"),
    ("whm", "warehouse_manager"),
    ("pur", "purchasing"), ("plant", "factory_manager"), ("ctrl", "control"),
    ("cfo", "cfo"), ("ceo", "ceo"), ("po", None), ("fin", None), ("admin", None),
]
PARTS = ("reach", "step", "cant")
# Who works the non-ladder sections: a permission, read from the user's role
# plus extra_perms, so a custom role holding the right is shown its section.
SECTION_PERM = {"req": ("proc_create",), "po": ("proc_purchasing", "proc_receive"),
                "fin": ("proc_pay",), "admin": ("proc_admin",)}
SECTION_ROLES = {"po": ("purchasing_manager", "warehouse_manager", "storekeeper"),
                 "fin": ("financial_controller", "cfo", "finance_manager",
                         "financial_director")}
# The "after approval" half of the cycle map: (node key, section it explains).
AFTER = [("po", "po"), ("recv", "po"), ("inv", "fin"), ("pay", "fin"), ("close", "po")]


@lru_cache(maxsize=None)
def _dict(lang):
    with open(_I18N / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


def t(lang, key):
    """The label for key in lang, falling back to English, then to the key."""
    return _dict(lang).get(key) or _dict("en").get(key) or key


def rich(lang, key):
    """A guide string as safe HTML, its [[btn:..]] / [[ui:..]] markers rendered as
    the real screen labels."""
    def chip(m):
        kind, k = m.group(1), m.group(2)
        return ('<span class="g-%s" data-i18n="%s">%s</span>'
                % (kind, k, escape(t(lang, k))))
    return Markup(CHIP.sub(chip, str(escape(t(lang, key)))))


def _items(prefix):
    out, n = [], 1
    while f"{prefix}.{n}" in _dict("en") or f"{prefix}.{n}.q" in _dict("en"):
        out.append(f"{prefix}.{n}")
        n += 1
    return out


def guide_keys():
    """Every proc.guide.* key the English dictionary defines (the source set)."""
    return sorted(k for k in _dict("en") if k.startswith("proc.guide."))


def chip_keys():
    """Every screen key a guide string points at."""
    return sorted({m.group(2) for k in guide_keys() for m in CHIP.finditer(_dict("en")[k])})


def _stage_label(lang, stage):
    # Same rule as the PR page: English keeps the DOAM wording the PDF prints;
    # other languages use the translated rung name the ladder shows them.
    from app.approvals import services as svc
    if lang == "en":
        return C.stage_label(stage)
    return svc.labels(lang)["stage"].get(stage) or C.stage_label(stage)


def view(user, lang):
    """Everything guide.html needs, resolved for one reader."""
    from app.approvals import services as svc
    from app.auth import user_has_permission
    lang = lang if lang in LANGS else "en"
    role_names = svc.labels(lang)["role"]
    stage_roles = svc.stage_roles_map()
    role = (user or {}).get("role")
    # An admin holds every right; marking every section "you" would mark none.
    is_admin = user_has_permission(user, "proc_admin")
    mine = set()
    sections = []
    for sid, stage in SECTIONS:
        if stage:
            roles = sorted(stage_roles.get(stage, ()))
            is_mine = role in roles
            title = _stage_label(lang, stage)
        else:
            roles = list(SECTION_ROLES.get(sid, ()))
            is_mine = (sid == "admin" if is_admin else
                       any(user_has_permission(user, p) for p in SECTION_PERM[sid]))
            title = t(lang, f"proc.guide.{sid}.title")
        if is_mine:
            mine.add(sid)
        p = f"proc.guide.{sid}"
        sections.append({
            "id": sid, "stage": stage, "title": title, "mine": is_mine,
            "seq": [s for s, _ in SECTIONS].index(sid),
            "who": ([role_names.get(r, r) for r in roles] if roles else []),
            "who_text": p + ".who" if p + ".who" in _dict("en") else None,
            "parts": {part: _items(f"{p}.{part}") for part in PARTS},
            "next": p + ".next" if p + ".next" in _dict("en") else None,
            "probs": _items(f"{p}.prob"),
        })
    after = [{"key": k, "target": s, "mine": s in mine} for k, s in AFTER]
    return {"lang": lang, "sections": sections, "mine": mine, "after": after,
            "rules": _items("proc.guide.rule"), "faq": _items("proc.guide.faq")}
