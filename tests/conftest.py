import pytest

from app import create_app
from app.cli import seed_demo_data
from app.config import TestConfig
from app.extensions import db

PASSWORD = "Sup3rSecret!"


@pytest.fixture(autouse=True)
def fast_hasher(monkeypatch):
    """Cheap argon2 parameters so seeding 8 users per test stays fast."""
    from argon2 import PasswordHasher

    from app import models
    monkeypatch.setattr(models, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_demo_data(PASSWORD)
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email, password=PASSWORD):
    return client.post("/api/v1.0/auth/login", json={"email": email, "password": password})


@pytest.fixture()
def super_headers(client):
    token = login(client, "platform@homa.io").get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def tenant_headers(client):
    token = login(client, "admin@grandhotel.com").get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
