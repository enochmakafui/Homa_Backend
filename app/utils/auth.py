from functools import wraps

from flask import current_app
from flask_jwt_extended import get_jwt, verify_jwt_in_request

from . import ApiError


def current_claims():
    """JWT claims, or {} when there is no token (only possible when AUTH_ENFORCED is off)."""
    try:
        return get_jwt() or {}
    except RuntimeError:
        return {}


def roles_required(*roles):
    """Require a valid access token whose role is in `roles`.

    With AUTH_ENFORCED=false a request with no token is let through (dev mode, see config).
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            enforced = current_app.config["AUTH_ENFORCED"]
            verify_jwt_in_request(optional=not enforced)
            claims = current_claims()
            if claims and claims.get("type") != "access":
                raise ApiError("Access token required", 401)
            if claims and roles and claims.get("role") not in roles:
                raise ApiError("You do not have permission to perform this action", 403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def current_tenant_id(explicit=None):
    """Tenant the request is scoped to.

    Always the JWT's tenant_id for tenant users. Only super_admin (no tenant in the
    token) may pass one explicitly. Never trust client input for anyone else.
    """
    claims = current_claims()
    if claims.get("tenant_id"):
        return claims["tenant_id"]
    return explicit


def resolve_tenant_id():
    """Tenant for a tenant-scoped request.

    Tenant users: the JWT's tenant_id (client input is ignored). super_admin, or a
    token-less dev request (AUTH_ENFORCED=false), must name one via `?tenant_id=` or
    a `tenant_id` JSON field.
    """
    from flask import request

    from ..extensions import db
    from ..models import Tenant

    tid = current_claims().get("tenant_id")
    if tid:
        return tid
    explicit = request.args.get("tenant_id")
    if not explicit:
        data = request.get_json(silent=True)
        explicit = data.get("tenant_id") if isinstance(data, dict) else None
    if not isinstance(explicit, str) or not explicit:
        raise ApiError("tenant_id is required when acting without a tenant account", 400)
    if not db.session.get(Tenant, explicit):
        raise ApiError("Tenant not found", 404)
    return explicit
