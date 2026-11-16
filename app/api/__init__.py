from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from ..extensions import db


def register_blueprints(app, prefix):
    from .auth import bp as auth_bp
    from .guests import bp as guests_bp
    from .properties import bp as properties_bp
    from .reservations import bp as reservations_bp
    from .tenants import bp as tenants_bp
    from .users import bp as users_bp

    for bp in (auth_bp, tenants_bp, users_bp, properties_bp, guests_bp, reservations_bp, health_bp):
        # A url_prefix passed here replaces the blueprint's own, so join them.
        app.register_blueprint(bp, url_prefix=prefix + (bp.url_prefix or ""))
    # Unversioned liveness probe (kept from the original starter).
    app.add_url_rule("/api/health", "legacy_health", lambda: jsonify({"status": "ok"}))


health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "error"
    return jsonify({
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "version": current_app.config["APP_VERSION"],
    })
