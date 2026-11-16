import secrets

from flask import Blueprint, current_app, request
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import ROLES, Tenant, User, UserRole
from ..utils import ApiError, clean_email, ok, require_fields
from ..utils.audit import audit
from ..utils.auth import current_claims, roles_required

bp = Blueprint("users", __name__, url_prefix="/user")

ADMIN_ROLES = ("super_admin", "tenant_admin")


@bp.get("/admins")
@roles_required("super_admin")
def list_admins():
    users = (
        User.query.join(UserRole).filter(UserRole.role.in_(ADMIN_ROLES))
        .order_by(User.date_created.desc()).all()
    )
    return ok([u.to_dict() for u in users])


@bp.post("")
@roles_required("super_admin")
def create_user():
    """Create a user. The live form sends only first_name/last_name/email/phone.

    Optional extras: `tenant_id` and `role`. If `role` is omitted it defaults to
    `tenant_admin` when a tenant is given, otherwise `super_admin` (the platform Users
    page manages platform admins, and the frontend treats a null tenant as super_admin).
    """
    data = request.get_json(silent=True)
    f = require_fields(data, ("first_name", "last_name", "email", "phone"))
    email = clean_email(f["email"])
    if not email:
        raise ApiError("A valid email address is required")

    tenant_id = data.get("tenant_id") or None
    tenant = None
    if tenant_id:
        tenant = db.session.get(Tenant, tenant_id)
        if not tenant:
            raise ApiError("Tenant not found", 404)
    role = data.get("role") or ("tenant_admin" if tenant else "super_admin")
    if role not in ROLES:
        raise ApiError("role must be one of: " + ", ".join(ROLES))
    if role == "super_admin" and tenant:
        raise ApiError("super_admin users cannot belong to a tenant")
    if role != "super_admin" and not tenant:
        raise ApiError(f"tenant_id is required for role {role}")

    if User.query.filter_by(email=email).first():
        raise ApiError("A user with this email already exists", 400)

    temp_password = secrets.token_urlsafe(12)
    user = User(first_name=f["first_name"], last_name=f["last_name"], email=email,
                phone=f["phone"], tenant_id=tenant_id)
    user.set_password(temp_password)
    user.user_roles.append(UserRole(role=role))
    db.session.add(user)
    try:
        audit("user_created", "user", f"Created {role} {email}", user=current_claims().get("email"),
              tenant=tenant.tenant_name if tenant else None, commit=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ApiError("A user with this email already exists", 400)

    body = user.to_dict()
    if current_app.config["EXPOSE_TEMP_PASSWORD"]:
        body["temporary_password"] = temp_password  # no mailer yet: caller must relay it
    return ok(body, 201)
