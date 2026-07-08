"""
TC Platform — Garamento assistant routes.

  POST /garamento/chat             -> conversational reply (any logged-in user)
  POST /garamento/market-research  -> live approximate price for a PR line item
  GET  /garamento/hello            -> greeting + whether the assistant is enabled

All state-changing calls are CSRF-protected (the browser sends X-CSRF-Token,
handled globally in app/csrf.py).
"""
from flask import Blueprint, request, jsonify

from app.auth import login_required, permission_required, current_user
from app.services import garamento as g

bp = Blueprint("garamento", __name__, url_prefix="/garamento")


@bp.get("/hello")
@login_required
def hello():
    return jsonify({"enabled": g.is_enabled(), "greeting": g.greeting()})


@bp.post("/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    history = data.get("messages") or []
    if not isinstance(history, list):
        history = []
    result = g.chat(history, user=current_user())
    return jsonify(result)


@bp.post("/market-research")
@permission_required("proc_view")
def market_research():
    data = request.get_json(silent=True) or {}
    result = g.market_research(
        item=data.get("item", ""),
        description=data.get("description", ""),
        unit=data.get("unit", ""),
        qty=data.get("qty", 1),
        currency=data.get("currency", "EGP"),
    )
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status


@bp.post("/market-research-bulk")
@permission_required("proc_view")
def market_research_bulk():
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        items = []
    result = g.market_research_bulk(items, currency=data.get("currency", "EGP"))
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status


@bp.post("/polish")
@login_required
def polish():
    data = request.get_json(silent=True) or {}
    result = g.polish(text=data.get("text", ""), kind=data.get("kind", "generic"))
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status


@bp.post("/draft-pr")
@permission_required("proc_create")
def draft_pr():
    from app.approvals import constants as C
    from app.approvals import services as psvc
    data = request.get_json(silent=True) or {}
    result = g.draft_pr(
        text=data.get("text", ""),
        departments=psvc.list_departments(),
        units=C.UNITS,
        currency=data.get("currency", "EGP"),
    )
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status


@bp.post("/triage")
@permission_required("maint_ticket_create")
def triage():
    data = request.get_json(silent=True) or {}
    result = g.triage_ticket(
        description=data.get("description", ""),
        machine=data.get("machine", ""),
        criticality=data.get("criticality", "medium"),
    )
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status
