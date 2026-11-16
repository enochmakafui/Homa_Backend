from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token, create_refresh_token, decode_token, get_jwt, get_jwt_identity, jwt_required,
)
from flask_jwt_extended.exceptions import JWTExtendedException
from jwt import PyJWTError

from ..extensions import db
from ..models import RevokedToken, User, utcnow
from ..utils import ApiError, clean_email, ok
from ..utils.audit import audit

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _claims(user: User):
    return {"role": user.role, "tenant_id": user.tenant_id, "email": user.email}


def _tokens(user: User):
    return (
        create_access_token(identity=user.user_id, additional_claims=_claims(user)),
        create_refresh_token(identity=user.user_id, additional_claims=_claims(user)),
    )


def _fail(email, reason, status, user=None):
    audit("login_failed", "auth", reason, severity="warning", user=email,
          tenant=user.tenant.tenant_name if user and user.tenant else None)
    raise ApiError(reason, status)


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = clean_email(data.get("email"))
    password = data.get("password")
    if not email or not isinstance(password, str) or not password:
        raise ApiError("Email and password are required", 400)

    user = User.query.filter_by(email=email).first()
    # Same message for unknown email and wrong password: no account enumeration.
    if not user or not user.check_password(password):
        _fail(email, "Invalid email or password", 401, user)
    if not user.is_active:
        _fail(email, "This account has been suspended", 403, user)
    if user.tenant and user.tenant.account_status != "Active":
        _fail(email, "This organisation's account is not active", 403, user)

    user.last_login_at = utcnow()
    access, refresh = _tokens(user)
    audit("login", "auth", "User logged in", user=user.email,
          tenant=user.tenant.tenant_name if user.tenant else None)
    return jsonify({"access_token": access, "refresh_token": refresh, "user": user.to_login_dict()})


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = db.session.get(User, get_jwt_identity())
    if not user or not user.is_active:
        raise ApiError("Account is unavailable", 401)
    return jsonify({"access_token": create_access_token(identity=user.user_id,
                                                        additional_claims=_claims(user))})


@bp.post("/logout")
@jwt_required(verify_type=False)
def logout():
    """Revoke the presented token, and the refresh token too if it is sent in the body."""
    jtis = {get_jwt()["jti"]}
    body = request.get_json(silent=True) or {}
    if body.get("refresh_token"):
        try:
            jtis.add(decode_token(body["refresh_token"])["jti"])
        except (JWTExtendedException, PyJWTError):
            pass  # an already-expired refresh token needs no revoking
    for jti in jtis:
        if not db.session.get(RevokedToken, jti):
            db.session.add(RevokedToken(jti=jti))
    audit("logout", "auth", user=get_jwt().get("email"), commit=False)
    db.session.commit()
    return jsonify({"message": "Logged out"})


@bp.get("/me")
@jwt_required()
def me():
    user = db.session.get(User, get_jwt_identity())
    if not user or not user.is_active:
        raise ApiError("Account is unavailable", 401)
    return ok(user.to_login_dict())
