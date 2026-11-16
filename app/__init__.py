from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from .config import Config, check_production_config
from .extensions import cors, db, jwt, migrate
from .utils import ApiError

API_PREFIX = "/api/v1.0"


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    check_production_config(app.config)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})

    from . import models  # noqa: F401  (register tables for migrations)
    from .api import register_blueprints
    from .cli import register_cli

    register_blueprints(app, API_PREFIX)
    register_cli(app)
    _register_jwt_handlers()
    _register_error_handlers(app)
    return app


def _register_jwt_handlers():
    from .models import RevokedToken

    @jwt.token_in_blocklist_loader
    def is_revoked(_header, payload):
        return db.session.get(RevokedToken, payload["jti"]) is not None

    def _msg(text):
        return jsonify({"message": text}), 401

    jwt.expired_token_loader(lambda h, p: _msg("Token has expired"))
    jwt.invalid_token_loader(lambda reason: _msg("Invalid token"))
    jwt.unauthorized_loader(lambda reason: _msg("Authentication required"))
    jwt.revoked_token_loader(lambda h, p: _msg("Token has been revoked"))


def _register_error_handlers(app):
    # Every error is JSON `{"message": ...}`. The frontend calls response.json() blindly.
    @app.errorhandler(ApiError)
    def api_error(e):
        return jsonify({"message": e.message}), e.status

    @app.errorhandler(HTTPException)
    def http_error(e):
        return jsonify({"message": e.description}), e.code

    @app.errorhandler(Exception)
    def unexpected(e):
        app.logger.exception(e)
        db.session.rollback()
        return jsonify({"message": "Internal server error"}), 500
