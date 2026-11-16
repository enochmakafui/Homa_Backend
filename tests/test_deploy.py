import pytest
from click.testing import CliRunner

from app import create_app
from app.cli import bootstrap_admin
from app.config import Config, TestConfig, _origins, database_url
from app.models import User

SUPABASE = "postgresql://postgres.abc:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"


def test_database_url_gets_driver_and_ssl():
    assert database_url(SUPABASE) == (
        "postgresql+psycopg://postgres.abc:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require")
    assert database_url(SUPABASE.replace("postgresql://", "postgres://")).startswith("postgresql+psycopg://")


def test_database_url_respects_existing_and_local():
    assert database_url(SUPABASE + "?sslmode=verify-full").endswith("?sslmode=verify-full")
    assert database_url(SUPABASE + "?application_name=x").endswith("?application_name=x&sslmode=require")
    local = database_url("postgresql://u:p@localhost:5432/db")
    assert local == "postgresql+psycopg://u:p@localhost:5432/db"
    assert database_url("sqlite:///homa.db") == "sqlite:///homa.db"


def test_cors_origin_parsing():
    assert _origins("*") == "*" and _origins("") == "*"
    assert _origins("https://a.com, https://b.com/ ") == ["https://a.com", "https://b.com/"]


class Prod(Config):
    APP_ENV = "production"
    SECRET_KEY = JWT_SECRET_KEY = "x" * 40
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg://u:p@db.example.com/postgres"
    SQLALCHEMY_ENGINE_OPTIONS = {}


def test_production_boots_with_safe_config():
    assert create_app(Prod)


@pytest.mark.parametrize("override", [
    {"SECRET_KEY": "dev-secret-change-me"},
    {"JWT_SECRET_KEY": "short"},
    {"SQLALCHEMY_DATABASE_URI": "sqlite:///homa.db"},
    {"AUTH_ENFORCED": False},
])
def test_production_refuses_unsafe_config(override):
    unsafe = type("Unsafe", (Prod,), override)
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        create_app(unsafe)


def test_bootstrap_admin_creates_once_and_never_resets(app):
    assert bootstrap_admin("Boss@Corp.com", "a-long-password-1") == "created"
    boss = User.query.filter_by(email="boss@corp.com").one()
    assert boss.role == "super_admin" and boss.tenant_id is None
    original = boss.password_hash
    assert bootstrap_admin("boss@corp.com", "another-long-pass-2") == "exists"
    assert User.query.filter_by(email="boss@corp.com").one().password_hash == original


def test_bootstrap_admin_validates(app):
    from click import ClickException
    with pytest.raises(ClickException):
        bootstrap_admin("not-an-email", "a-long-password-1")
    with pytest.raises(ClickException):
        bootstrap_admin("a@corp.com", "short")


def test_bootstrap_command_skips_without_env(app, monkeypatch):
    monkeypatch.delenv("BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    out = CliRunner().invoke(app.cli, ["bootstrap-admin"])
    assert out.exit_code == 0 and "skipping" in out.output
