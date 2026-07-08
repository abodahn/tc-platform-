"""
Natural-language assistant for the Intelligence Center.

Grounds answers in the latest AI risk scores + open alerts. Uses the OpenRouter
LLM (Garamento) when configured, and ALWAYS has a rule-based fallback so the
assistant works even with no external AI provider.
"""
from app.ai_engine.base import safe_rows


def context_block(conn):
    scores = [dict(r) for r in safe_rows(
        conn, "SELECT domain, score, level, headline FROM ai_risk_scores ORDER BY score DESC")]
    alerts = [dict(r) for r in safe_rows(
        conn, "SELECT domain, title, risk_score, severity, recommendation, responsible "
              "FROM ai_alerts WHERE status IN ('new','acknowledged','in_progress','escalated') "
              "ORDER BY risk_score DESC LIMIT 15")]
    lines = ["CURRENT AI RISK SCORES (0-100, higher = worse):"]
    for s in scores:
        lines.append(f"- {s['domain']}: {s['score']} ({s['level']}) {s.get('headline') or ''}")
    lines.append("\nTOP OPEN AI ALERTS:")
    for a in alerts:
        lines.append(f"- [{a['severity']}] {a['title']} (risk {a['risk_score']}) → {a.get('recommendation') or ''}")
    return "\n".join(lines), scores, alerts


def _rule_based(question, scores, alerts):
    q = (question or "").lower()
    if not scores and not alerts:
        return "No AI run has been computed yet. Open the Command Center and press ‘Recompute’."
    parts = []
    if any(w in q for w in ("risk", "today", "focus", "top", "highest", "week")):
        parts.append("Highest risks right now:")
        for s in scores[:5]:
            parts.append(f"• {s['domain']} — {s['score']} ({s['level']})")
    if any(w in q for w in ("alert", "issue", "problem", "sla", "payroll", "asset", "machine", "stock", "spare", "paper", "approval", "hr")):
        parts.append("\nTop open alerts:")
        for a in alerts[:6]:
            parts.append(f"• {a['title']} (risk {a['risk_score']}) → {a.get('recommendation') or ''}")
    if not parts:
        parts.append("Top open alerts:")
        for a in alerts[:6]:
            parts.append(f"• {a['title']} (risk {a['risk_score']})")
    return "\n".join(parts) if parts else "No notable risks detected."


def answer(question, conn, user=None):
    """Return {answer, scores, alerts, source}. Never raises."""
    ctx, scores, alerts = context_block(conn)
    try:
        from app.services import garamento as g
        if g.is_enabled():
            sys = ("You are the T&C AI Intelligence assistant. Answer the manager's question using "
                   "ONLY the current risk data below. Be concise, lead with the answer, then list the "
                   "specific items and the recommended action. Use the same language as the question.\n\n" + ctx)
            msgs = [{"role": "system", "content": sys},
                    {"role": "user", "content": (question or "What are the top risks today?")[:500]}]
            txt = g._complete(msgs, temperature=0.2, max_tokens=500)
            if txt:
                return {"answer": txt, "scores": scores, "alerts": alerts, "source": "ai"}
    except Exception:  # noqa: BLE001
        pass
    return {"answer": _rule_based(question, scores, alerts),
            "scores": scores, "alerts": alerts, "source": "rules"}
