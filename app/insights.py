"""
TC Platform — offline AI executive insights (Daily Briefing).

One sentence, one statement, no internet. Everything here is real, platform-owned
maintenance data.

Two things this deliberately does NOT do any more:

  * it does not call maintenance.ai.risk_ranking(). That pulled every active
    machine (5,108 rows on production) out of the DB and scored them in Python
    to print one "N at risk" badge on the busiest page in the platform. Machine
    risk is actionable on /maintenance/ai, which is where you go to act on it.
  * it does not repeat what the factory pulse already shows. Spare-part stockouts
    are a pulse tile; restating them as a badge two rows lower is noise, not a
    second measurement.

Text is returned as {en, ar, tr} for data-loc-* rather than an i18n key, because
these sentences are assembled from live numbers and a key cannot carry a count.
"""
from app.db import get_db


def _loc(en, ar, tr):
    return {"en": en, "ar": ar, "tr": tr}


def _en(n, one, many):
    # English is the only one of the three that needs a plural here: Turkish does
    # not pluralise after a numeral, and the Arabic wording below reads for both.
    return "%d %s" % (n, one if n == 1 else many)


def executive_summary():
    conn = get_db()
    try:
        # One round trip. Four scalars, four different tables.
        r = conn.execute(
            "SELECT (SELECT COUNT(*) FROM mnt_machines WHERE status='stopped') AS stopped, "
            "(SELECT COUNT(*) FROM mnt_tickets WHERE priority='critical' "
            " AND status NOT IN ('closed','cancelled','rejected')) AS open_crit, "
            "(SELECT COUNT(*) FROM production_lines WHERE status IN ('down','maintenance')) "
            " AS lines_down, "
            "(SELECT COUNT(*) FROM mnt_approvals WHERE status='pending') AS pend"
        ).fetchone()
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    stopped = int(r["stopped"] or 0)
    open_crit = int(r["open_crit"] or 0)
    lines_down = int(r["lines_down"] or 0)
    pend = int(r["pend"] or 0)

    parts, tone = [], "good"
    if stopped:
        tone = "crit"
        parts.append(_loc(_en(stopped, "machine stopped", "machines stopped"),
                          "%d آلة متوقفة" % stopped,
                          "%d makine durdu" % stopped))
    if lines_down:
        tone = "crit"
        parts.append(_loc(_en(lines_down, "production line down", "production lines down"),
                          "%d خط إنتاج متوقف" % lines_down,
                          "%d üretim hattı durdu" % lines_down))
    if open_crit:
        tone = "crit" if tone == "crit" else "warn"
        parts.append(_loc(_en(open_crit, "open critical ticket", "open critical tickets"),
                          "%d بلاغ حرج مفتوح" % open_crit,
                          "%d açık kritik talep" % open_crit))
    if pend:
        tone = tone if tone != "good" else "warn"
        parts.append(_loc(_en(pend, "maintenance approval pending", "maintenance approvals pending"),
                          "%d موافقة صيانة معلقة" % pend,
                          "%d bekleyen bakım onayı" % pend))

    if not parts:
        return {"tone": "good",
                "text": _loc("Maintenance is clear: no stopped machines, no lines down, "
                             "no open critical tickets.",
                             "الصيانة سليمة: لا آلات متوقفة ولا خطوط متوقفة ولا بلاغات حرجة مفتوحة.",
                             "Bakım sorunsuz: duran makine, duran hat veya açık kritik "
                             "talep yok.")}

    lead = {"crit": ("Attention", "تنبيه", "Dikkat"),
            "warn": ("Watch", "للمتابعة", "İzlemede")}[tone]
    return {"tone": tone,
            "text": _loc("%s: %s." % (lead[0], ", ".join(p["en"] for p in parts)),
                         "%s: %s." % (lead[1], "، ".join(p["ar"] for p in parts)),
                         "%s: %s." % (lead[2], ", ".join(p["tr"] for p in parts)))}


if __name__ == "__main__":   # smallest thing that fails if the wording logic breaks
    def _fake(stopped, crit, down, pend):
        import app.insights as m
        class _R(dict):
            def __getitem__(self, k):
                return dict.__getitem__(self, k)
        row = _R(stopped=stopped, open_crit=crit, lines_down=down, pend=pend)

        class _C:
            def execute(self, *a):
                return type("X", (), {"fetchone": staticmethod(lambda: row)})()

            def close(self):
                pass
        m.get_db = lambda: _C()
        return m.executive_summary()

    ok = _fake(0, 0, 0, 0)
    assert ok["tone"] == "good" and "clear" in ok["text"]["en"], ok
    warn = _fake(0, 0, 0, 3)
    assert warn["tone"] == "warn" and "3 maintenance approvals pending" in warn["text"]["en"], warn
    one = _fake(0, 0, 0, 1)
    assert "1 maintenance approval pending" in one["text"]["en"], one
    crit = _fake(2, 1, 1, 0)
    assert crit["tone"] == "crit", crit
    assert crit["text"]["en"] == ("Attention: 2 machines stopped, 1 production line down, "
                                 "1 open critical ticket."), crit["text"]["en"]
    assert all(crit["text"][l] for l in ("en", "ar", "tr")), crit
    print("insights self-check ok")
