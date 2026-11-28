"""
TC Platform — Item control rung: the settings page (who signs it per department
group) and the access an assignment grants.

Registered on approvals.bp (imported once at the end of app/routes/approvals.py),
so every URL lives under /procurement and every message leaves through the same
translating flash().
"""
import json

from flask import render_template, request, redirect, url_for, session

from app.auth import login_required, permission_required, current_user
from app.security import user_permissions
from app.approvals import item_control as IC
from app.routes.approvals import bp, flash, _u, _ip

# The signing actions an item controller may need even without proc_approve.
_SIGN_ENDPOINTS = {"approvals.approve", "approvals.reject", "approvals.send_back",
                   "approvals.hold", "approvals.resume", "approvals.bulk_sign"}

_SAVE_MSG = {
    "saved": "Item control saved.",
    "bad_user": "Pick an active user as the item controller.",
    "alias_taken": "One of those department names already belongs to another group.",
    "too_many_aliases": "Up to 20 extra department names per group.",
    "unknown_group": "That department group does not exist.",
    "pending_waiting": "Requests are waiting on this person at Item control. "
                       "Pick another person to take them over first.",
}


def _extra(user):
    raw = user.get("extra_perms")
    try:
        e = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except ValueError:
        e = []
    return {p for p in e if isinstance(p, str)} if isinstance(e, list) else set()


@bp.before_app_request
def _itemctl_access():
    """What the assignment itself grants, for THIS request only (never stored):

    * proc_view, so a controller whose role lacks it can open procurement — the
      requester-scope rule still limits them to their own requests and the ones
      assigned to them (services.can_view_pr);
    * proc_approve on a signing POST, only when the request(s) named are waiting
      on an item-control rung this user may sign — act_on_step re-checks every
      request on its own, so it cannot sign anything else."""
    ep = request.endpoint or ""
    if not ep or ep == "static" or ep.endswith(".static"):
        return None
    user = current_user()
    if not user or user.get("role") == "super_admin":
        return None
    perms = user_permissions(user)
    if "*" in perms:
        return None
    add = set()
    # Procurement's own pages (scoped by can_view_pr) and the dashboard / my-work /
    # search, never company-wide lists elsewhere (aging, lookup, reports).
    if ("proc_view" not in perms and request.blueprint in ("approvals", "main")
            and IC.is_controller(user.get("username"))):
        add.add("proc_view")
    if (request.method == "POST" and ep in _SIGN_ENDPOINTS
            and not perms & {"proc_approve", "proc_admin"}):
        ids = (request.form.getlist("pr_id") if ep == "approvals.bulk_sign"
               else [(request.view_args or {}).get("pr_id")])
        if IC.signable_now(user, ids):
            add.add("proc_approve")
    if add:
        user["extra_perms"] = json.dumps(sorted(_extra(user) | add))
    return None


@bp.app_template_global()
def itemctl_assignee(step):
    """Display name of the person an item-control rung waits on."""
    try:
        return IC.assignee_name((step or {}).get("assignee"))
    except Exception:                                        # noqa: BLE001
        return ""


@bp.app_template_global()
def itemctl_skip(pr_id):
    """{why, group} when this request's latest submission skipped the rung."""
    try:
        return IC.skip_info(pr_id)
    except Exception:                                        # noqa: BLE001
        return None


@bp.app_template_global()
def itemctl_preview():
    """Department names (normalised) whose new requests get the rung."""
    try:
        return IC.preview_names((current_user() or {}).get("username"))
    except Exception:                                        # noqa: BLE001
        return []


@bp.route("/item-control", methods=["GET"])
@login_required
@permission_required("proc_admin")
def itemctl_settings():
    return render_template("approvals/itemctl.html", active="proc_itemctl",
                           groups=IC.view(), users=IC.picker_users(),
                           alias_max=IC.ALIAS_MAX, bf=session.pop("itemctl_bf", None),
                           bf_reasons=IC.BF_REASONS)


@bp.route("/item-control/<group_key>", methods=["POST"])
@login_required
@permission_required("proc_admin")
def itemctl_save(group_key):
    ok, code = IC.save(group_key, request.form.get("username", ""),
                       request.form.get("aliases", ""), request.form.get("active") == "1",
                       _u(), ip=_ip())
    flash(_SAVE_MSG.get(code, _SAVE_MSG["unknown_group"]), "success" if ok else "error")
    anchor = ("#g-" + group_key) if group_key in {k for k, _, _ in IC.GROUPS} else ""
    return redirect(url_for("approvals.itemctl_settings") + anchor)


@bp.route("/item-control/<group_key>/backfill", methods=["POST"])
@login_required
@permission_required("proc_admin")
def itemctl_backfill(group_key):
    """Add this group's Item control rung to its open requests (IC.backfill)."""
    res = IC.run_backfill(group_key, _u(), ip=_ip())
    if res is None:
        flash(_SAVE_MSG["unknown_group"], "error")
        return redirect(url_for("approvals.itemctl_settings"))
    skipped = [r for r in res if r["result"] == "skipped"]
    flash("Item control added to open requests. Added / skipped: %d / %d"
          % (sum(1 for r in res if r["result"] == "added"), len(skipped)), "success")
    # the reasons, for the collapsible list on the page (capped: the session is a cookie)
    session["itemctl_bf"] = {"g": group_key, "skips": [
        [r["pr_no"] or "#%s" % r["pr_id"], r["why"]] for r in skipped[:40]]}
    return redirect(url_for("approvals.itemctl_settings") + "#g-" + group_key)
