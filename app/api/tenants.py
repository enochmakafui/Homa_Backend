from flask import Blueprint, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Tenant
from ..utils import ApiError, clean_email, ok, require_fields
from ..utils.audit import audit
from ..utils.auth import current_claims, roles_required

bp = Blueprint("tenants", __name__, url_prefix="/tenant")

STATUSES = ("Active", "Suspended", "Pending")
PLANS = ("starter", "professional", "enterprise")


def _actor():
    return current_claims().get("email")


def _get(tenant_id):
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        raise ApiError("Tenant not found", 404)
    return tenant


@bp.get("")
@roles_required("super_admin")
def list_tenants():
    tenants = Tenant.query.order_by(Tenant.date_created.desc()).all()
    return ok([t.to_dict() for t in tenants])


@bp.post("")
@roles_required("super_admin")
def create_tenant():
    data = request.get_json(silent=True)
    f = require_fields(data, ("tenant_name", "email", "phone", "address"))
    email = clean_email(f["email"])
    if not email:
        raise ApiError("A valid email address is required")

    exists = Tenant.query.filter(
        (func.lower(Tenant.tenant_name) == f["tenant_name"].lower()) | (Tenant.email == email)
    ).first()
    if exists:
        raise ApiError("A tenant with this name or email already exists", 409)

    tenant = Tenant(tenant_name=f["tenant_name"], email=email, phone=f["phone"], address=f["address"])
    db.session.add(tenant)
    try:
        audit("tenant_created", "tenant", f"Created tenant {tenant.tenant_name}",
              user=_actor(), tenant=tenant.tenant_name, commit=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ApiError("A tenant with this name or email already exists", 409)
    return ok(tenant.to_dict(), 201)


@bp.get("/<tenant_id>")
@roles_required("super_admin")
def get_tenant(tenant_id):
    return ok(_get(tenant_id).to_dict())


@bp.patch("/<tenant_id>")
@roles_required("super_admin")
def update_tenant(tenant_id):
    tenant = _get(tenant_id)
    data = request.get_json(silent=True) or {}
    changes = []
    if "account_status" in data:
        if data["account_status"] not in STATUSES:
            raise ApiError("account_status must be one of: " + ", ".join(STATUSES))
        changes.append(f"status {tenant.account_status} -> {data['account_status']}")
        tenant.account_status = data["account_status"]
    if "plan" in data:
        if data["plan"] not in PLANS:
            raise ApiError("plan must be one of: " + ", ".join(PLANS))
        changes.append(f"plan {tenant.plan} -> {data['plan']}")
        tenant.plan = data["plan"]
    for field in ("phone", "address"):
        if field in data:
            value = (data[field] or "").strip() if isinstance(data[field], str) else ""
            if not value:
                raise ApiError(f"{field} cannot be empty")
            setattr(tenant, field, value)
            changes.append(field)
    if not changes:
        raise ApiError("No updatable fields supplied")
    audit("tenant_updated", "tenant", "; ".join(changes), user=_actor(),
          tenant=tenant.tenant_name, severity="warning" if "account_status" in data else "info",
          commit=False)
    db.session.commit()
    return ok(tenant.to_dict())
