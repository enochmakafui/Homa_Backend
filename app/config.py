import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

DEV_SECRET = "dev-secret-change-me"


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def database_url(raw: str) -> str:
    """Normalise a DATABASE_URL for SQLAlchemy + psycopg 3.

    Supabase and Render hand out `postgres://` / `postgresql://` URLs; SQLAlchemy needs the
    `+psycopg` driver suffix. Remote Postgres hosts also get `sslmode=require` (Supabase
    rejects unencrypted connections) unless the URL already says otherwise.
    """
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            raw = "postgresql+psycopg://" + raw[len(prefix):]
            break
    if raw.startswith("postgresql+psycopg://") and "sslmode=" not in raw:
        host_part = raw.split("@")[-1]
        if not host_part.startswith(("localhost", "127.0.0.1")):
            raw += ("&" if "?" in raw else "?") + "sslmode=require"
    return raw


def _engine_options(url: str) -> dict:
    if not url.startswith("postgresql"):
        return {}
    return {
        "pool_pre_ping": True,   # drop connections the pooler/Supabase closed while idle
        "pool_recycle": 300,
        "pool_size": 5,          # keep well under Supabase's pooler client limits
        "max_overflow": 5,
        # Required for Supabase's transaction pooler (pgbouncer), harmless elsewhere.
        "connect_args": {"prepare_threshold": None},
    }


def _origins(raw: str):
    parts = [o.strip() for o in raw.split(",") if o.strip()]
    return "*" if parts == ["*"] or not parts else parts


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET)
    CORS_ORIGINS = _origins(os.getenv("CORS_ORIGINS", "*"))

    SQLALCHEMY_DATABASE_URI = database_url(os.getenv("DATABASE_URL", "sqlite:///homa.db"))
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("SECRET_KEY", DEV_SECRET))
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "30")))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "14")))

    # The frontend does not send Authorization headers yet. While False, the LIVE
    # endpoints work without a token (a valid token, if sent, is still honoured).
    AUTH_ENFORCED = _flag("AUTH_ENFORCED", "true")

    # No mailer exists yet, so POST /user returns the generated temporary password.
    EXPOSE_TEMP_PASSWORD = _flag("EXPOSE_TEMP_PASSWORD", "true")

    APP_VERSION = "0.1.0"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTH_ENFORCED = True
    EXPOSE_TEMP_PASSWORD = True
    JWT_SECRET_KEY = "test-secret-key-that-is-long-enough-for-hs256"


def check_production_config(config) -> None:
    """Refuse to boot in production with development defaults."""
    if config.get("APP_ENV") != "production":
        return
    problems = []
    for key in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = config.get(key) or ""
        if value == DEV_SECRET or len(value) < 32:
            problems.append(f"{key} must be set to a random value of at least 32 characters")
    if config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        problems.append("DATABASE_URL must point at Postgres (SQLite is not persistent on Render)")
    if not config.get("AUTH_ENFORCED"):
        problems.append("AUTH_ENFORCED must be true")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
