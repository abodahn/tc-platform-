"""Command Center: the pure logic behind the front page.

Deliberately no client/DB fixture — these are the branches that would silently
render a wrong sentence, and they are all pure functions. Rendering itself is
covered by the smoke suite.
"""
from datetime import date, timedelta

from app.routes.main import _PULSE_RANK, _ar_count, _freshness, _greeting, _today, _verdict


def _tiles(*tones):
    return [{"rank": _PULSE_RANK[t]} for t in tones]


def test_verdict_counts_crit_and_warn_only():
    v = _verdict(_tiles("crit", "crit", "warn", "info", "good"))
    assert v["en"] == "3 of 5 indicators need action."
    # Turkish puts the total first — the reason these are whole sentences per
    # language instead of a Jinja concatenation.
    assert v["tr"] == "5 göstergeden 3 tanesi aksiyon gerektiriyor."
    assert v["ar"].startswith("3 من 5")


def test_verdict_all_clear_and_no_tiles():
    assert _verdict(_tiles("good", "good"))["en"] == "All 2 indicators are on track."
    assert _verdict([]) is None          # nothing measured -> no sentence at all


def test_freshness_names_the_day_the_numbers_came_from():
    assert _freshness(None) is None
    assert _freshness("not-a-date") is None
    today = _today()
    assert _freshness(today.isoformat())["en"].endswith("today")
    assert _freshness((today - timedelta(days=1)).isoformat())["en"].endswith("yesterday")
    old = today - timedelta(days=13)
    line = _freshness(old.isoformat() + " 00:00:00")
    assert line["en"] == "Production last reported %s · 13 days ago" % old.isoformat()
    assert all(line[lang] for lang in ("en", "ar", "tr"))


def test_arabic_counted_nouns_agree():
    # Arabic has three forms where English has two: dual, plural for 3-10, and
    # accusative singular for 11+. One form for all of them reads as broken Arabic.
    assert _ar_count(2, "يومين", "أيام", "يومًا") == "يومين"
    assert _ar_count(5, "يومين", "أيام", "يومًا") == "5 أيام"
    assert _ar_count(13, "يومين", "أيام", "يومًا") == "13 يومًا"
    assert _freshness((_today() - timedelta(days=5)).isoformat())["ar"].endswith("قبل 5 أيام")


def test_greeting_without_a_name_has_no_dangling_comma():
    g = _greeting(None)                       # "Good morning." not "Good morning, ."
    assert ", ." not in g["en"] and "، ." not in g["ar"] and ", ." not in g["tr"]
    assert all(g[lang].endswith(".") for lang in ("en", "ar", "tr"))
    assert _greeting({"username": "ahmed"})["en"].endswith(", ahmed.")
