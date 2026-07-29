"""
Compliance & social-audit routes (native platform blueprint at /compliance).
Buyer/social/quality audits, corrective-action plans, and certificate-expiry
tracking — reusing platform auth, RBAC, CSRF, i18n, base template and the bell.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.compliance import services as svc
from app.compliance.constants import (SCHEMES, AUDIT_STATUS, FINDING_SEVERITY,
                                       FINDING_STATUS, CERT_TYPES)

bp = Blueprint("compliance", __name__, url_prefix="/compliance")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.expiry_sweep()


@bp.route("/")
@login_required
@permission_required("cmp_view")
def index():
    _sweep_throttled()
    return render_template("compliance/dashboard.html", active="compliance",
                           d=svc.dashboard(), days_left=svc.days_left)


@bp.route("/audits")
@login_required
@permission_required("cmp_view")
def audits():
    scheme = request.args.get("scheme") or None
    status = request.args.get("status") or None
    return render_template("compliance/audits.html", active="cmp_audits",
                           rows=svc.list_audits(scheme, status), schemes=SCHEMES,
                           statuses=AUDIT_STATUS, f_scheme=scheme, f_status=status,
                           days_left=svc.days_left)


@bp.route("/audits/new")
@login_required
@permission_required("cmp_manage")
def audit_new():
    return render_template("compliance/audit_form.html", active="cmp_audits",
                           schemes=SCHEMES, statuses=AUDIT_STATUS)


@bp.route("/audits", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def audit_create():
    if not (request.form.get("scheme") or "").strip():
        flash("Scheme is required.", "error")
        return redirect(url_for("compliance.audit_new"))
    aid = svc.create_audit(request.form, _u())
    flash("Audit recorded.", "success")
    return redirect(url_for("compliance.audit_detail", audit_id=aid))


@bp.route("/audits/<int:audit_id>")
@login_required
@permission_required("cmp_view")
def audit_detail(audit_id):
    bundle = svc.get_audit(audit_id)
    if not bundle:
        abort(404)
    return render_template("compliance/audit_detail.html", active="cmp_audits", **bundle,
                           statuses=AUDIT_STATUS, severities=FINDING_SEVERITY,
                           finding_statuses=FINDING_STATUS, days_left=svc.days_left)


@bp.route("/audits/<int:audit_id>/update", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def audit_update(audit_id):
    svc.update_audit(audit_id, request.form, _u())
    flash("Audit updated.", "success")
    return redirect(url_for("compliance.audit_detail", audit_id=audit_id))


@bp.route("/audits/<int:audit_id>/finding", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def finding_add(audit_id):
    if not (request.form.get("finding") or "").strip():
        flash("Finding text is required.", "error")
    else:
        svc.add_finding(audit_id, request.form, _u())
        flash("Corrective action added.", "success")
    return redirect(url_for("compliance.audit_detail", audit_id=audit_id))


@bp.route("/findings/<int:finding_id>/status", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def finding_status(finding_id):
    svc.set_finding_status(finding_id, request.form.get("status") or "open",
                           request.form.get("evidence"), _u())
    flash("Corrective action updated.", "success")
    return redirect(request.referrer or url_for("compliance.index"))


@bp.route("/certs")
@login_required
@permission_required("cmp_view")
def certs():
    return render_template("compliance/certs.html", active="cmp_certs",
                           rows=svc.list_certs(), cert_types=CERT_TYPES, days_left=svc.days_left)


@bp.route("/certs", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def cert_create():
    if not (request.form.get("name") or "").strip():
        flash("Certificate name is required.", "error")
    else:
        svc.create_cert(request.form, _u())
        flash("Certificate added.", "success")
    return redirect(url_for("compliance.certs"))


@bp.route("/certs/<int:cert_id>/renew", methods=["POST"])
@login_required
@permission_required("cmp_manage")
def cert_renew(cert_id):
    svc.renew_cert(cert_id, request.form.get("expiry_date"), _u())
    flash("Certificate renewed.", "success")
    return redirect(url_for("compliance.certs"))


# Declares this module's reports with the shared reporting engine. Import only —
# it runs no query and touches no database (see app/compliance/reports.py).
from app.compliance import reports as _reports    # noqa: E402,F401
