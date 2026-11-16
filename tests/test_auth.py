from app.extensions import db
from app.models import AuditLog, User

from .conftest import PASSWORD, login

BASE = "/api/v1.0/auth"


def test_login_success_shape(client):
    r = login(client, "admin@grandhotel.com")
    assert r.status_code == 200
    body = r.get_json()
    assert body["access_token"] and body["refresh_token"]
    u = body["user"]
    assert u["email"] == "admin@grandhotel.com"
    assert (u["first_name"], u["last_name"]) == ("Victoria", "Sterling")
    assert u["tenant_name"] == "Grand Palace Hotel" and u["tenant_id"]
    assert u["role"] == "tenant_admin"


def test_super_admin_has_no_tenant(client):
    u = login(client, "platform@homa.io").get_json()["user"]
    assert u["tenant_id"] is None and u["role"] == "super_admin"


def test_email_is_case_insensitive(client):
    assert login(client, "PLATFORM@homa.io").status_code == 200


def test_bad_credentials_are_json_401_and_identical(client):
    wrong_pw = login(client, "platform@homa.io", "nope")
    no_user = login(client, "ghost@homa.io", "nope")
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.get_json() == no_user.get_json() == {"message": "Invalid email or password"}


def test_missing_fields_400(client):
    r = client.post(f"{BASE}/login", json={"email": "platform@homa.io"})
    assert r.status_code == 400 and "message" in r.get_json()


def test_non_json_body_400(client):
    r = client.post(f"{BASE}/login", data="x", content_type="text/plain")
    assert r.status_code == 400 and r.is_json


def test_suspended_user_blocked(app, client):
    User.query.filter_by(email="hr@grandhotel.com").one().account_status_id = 2
    db.session.commit()
    r = login(client, "hr@grandhotel.com")
    assert r.status_code == 403 and "suspended" in r.get_json()["message"]


def test_suspended_tenant_blocks_its_users(client, super_headers):
    tid = login(client, "admin@grandhotel.com").get_json()["user"]["tenant_id"]
    client.patch(f"/api/v1.0/tenant/{tid}", json={"account_status": "Suspended"}, headers=super_headers)
    assert login(client, "admin@grandhotel.com").status_code == 403


def test_login_audited_and_last_login_set(client):
    login(client, "platform@homa.io")
    login(client, "platform@homa.io", "wrong")
    actions = [a.action for a in AuditLog.query.order_by(AuditLog.timestamp).all()]
    assert "login" in actions and "login_failed" in actions
    assert User.query.filter_by(email="platform@homa.io").one().last_login_at is not None


def test_password_is_hashed(app):
    u = User.query.filter_by(email="platform@homa.io").one()
    assert u.password_hash != PASSWORD and u.password_hash.startswith("$argon2")


def test_me(client, tenant_headers):
    r = client.get(f"{BASE}/me", headers=tenant_headers)
    assert r.status_code == 200 and r.get_json()["response"]["role"] == "tenant_admin"


def test_me_requires_token(client):
    r = client.get(f"{BASE}/me")
    assert r.status_code == 401 and "message" in r.get_json()


def test_refresh_issues_working_access_token(client):
    refresh = login(client, "admin@grandhotel.com").get_json()["refresh_token"]
    r = client.post(f"{BASE}/refresh", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 200
    new = r.get_json()["access_token"]
    assert client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_access_token_cannot_refresh_and_refresh_token_cannot_access(client):
    body = login(client, "admin@grandhotel.com").get_json()
    assert client.post(f"{BASE}/refresh", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 401
    assert client.get("/api/v1.0/tenant", headers={"Authorization": f"Bearer {body['refresh_token']}"}).status_code == 401


def test_logout_revokes_tokens(client):
    body = login(client, "admin@grandhotel.com").get_json()
    h = {"Authorization": f"Bearer {body['access_token']}"}
    r = client.post(f"{BASE}/logout", headers=h, json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200
    assert client.get(f"{BASE}/me", headers=h).status_code == 401
    r = client.post(f"{BASE}/refresh", headers={"Authorization": f"Bearer {body['refresh_token']}"})
    assert r.status_code == 401
