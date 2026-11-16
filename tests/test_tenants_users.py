from app.models import AuditLog

T = "/api/v1.0/tenant"
U = "/api/v1.0/user"

NEW_TENANT = {"tenant_name": "Hilton Group", "email": "contact@hilton.com",
              "phone": "+1-555-0123", "address": "123 Hotel Lane, City"}
NEW_USER = {"first_name": "Jane", "last_name": "Doe", "email": "jane@homa.io", "phone": "+1-555-0002"}


# --- GET /tenant -----------------------------------------------------------
def test_list_tenants_envelope(client, super_headers):
    r = client.get(T, headers=super_headers)
    assert r.status_code == 200
    items = r.get_json()["response"]
    assert [t["tenant_name"] for t in items] == ["Grand Palace Hotel"]
    t = items[0]
    assert t["account_status"] == "Active" and t["date_created"].endswith("Z")
    assert {"tenant_id", "email", "phone", "address"} <= t.keys()


def test_tenant_endpoints_require_auth(client):
    assert client.get(T).status_code == 401
    assert client.post(T, json=NEW_TENANT).status_code == 401


def test_tenant_endpoints_super_admin_only(client, tenant_headers):
    assert client.get(T, headers=tenant_headers).status_code == 403
    assert client.post(T, json=NEW_TENANT, headers=tenant_headers).status_code == 403


# --- POST /tenant ----------------------------------------------------------
def test_create_tenant(client, super_headers):
    r = client.post(T, json=NEW_TENANT, headers=super_headers)
    assert r.status_code == 201
    created = r.get_json()["response"]
    assert created["tenant_name"] == "Hilton Group" and created["account_status"] == "Active"
    names = [t["tenant_name"] for t in client.get(T, headers=super_headers).get_json()["response"]]
    assert "Hilton Group" in names
    assert AuditLog.query.filter_by(action="tenant_created").count() == 1


def test_create_tenant_validation(client, super_headers):
    r = client.post(T, json={**NEW_TENANT, "email": "not-an-email"}, headers=super_headers)
    assert r.status_code == 400 and "email" in r.get_json()["message"].lower()
    r = client.post(T, json={"tenant_name": "X"}, headers=super_headers)
    assert r.status_code == 400 and "email" in r.get_json()["message"]


def test_create_tenant_duplicate_409(client, super_headers):
    client.post(T, json=NEW_TENANT, headers=super_headers)
    r = client.post(T, json={**NEW_TENANT, "tenant_name": "hilton group", "email": "other@hilton.com"},
                    headers=super_headers)
    assert r.status_code == 409 and "message" in r.get_json()
    r = client.post(T, json={**NEW_TENANT, "tenant_name": "Another"}, headers=super_headers)
    assert r.status_code == 409


def test_patch_tenant_status(client, super_headers):
    tid = client.post(T, json=NEW_TENANT, headers=super_headers).get_json()["response"]["tenant_id"]
    r = client.patch(f"{T}/{tid}", json={"account_status": "Suspended"}, headers=super_headers)
    assert r.status_code == 200 and r.get_json()["response"]["account_status"] == "Suspended"
    assert client.patch(f"{T}/{tid}", json={"account_status": "Bogus"}, headers=super_headers).status_code == 400
    assert client.patch(f"{T}/nope", json={"plan": "starter"}, headers=super_headers).status_code == 404


# --- GET /user/admins ------------------------------------------------------
def test_list_admins(client, super_headers):
    r = client.get(f"{U}/admins", headers=super_headers)
    assert r.status_code == 200
    items = r.get_json()["response"]
    assert {u["email"] for u in items} == {"platform@homa.io", "admin@grandhotel.com"}
    tenant_admin = next(u for u in items if u["email"] == "admin@grandhotel.com")
    assert tenant_admin["user_roles"] == [{"role": "tenant_admin"}]
    assert tenant_admin["account_status_id"] == 1
    assert tenant_admin["tenant_name"] == "Grand Palace Hotel"
    assert "password_hash" not in tenant_admin


def test_admins_super_admin_only(client, tenant_headers):
    assert client.get(f"{U}/admins", headers=tenant_headers).status_code == 403


# --- POST /user ------------------------------------------------------------
def test_create_user_defaults_to_super_admin_without_tenant(client, super_headers):
    r = client.post(U, json=NEW_USER, headers=super_headers)
    assert r.status_code == 201
    u = r.get_json()["response"]
    assert u["user_roles"] == [{"role": "super_admin"}] and u["tenant_id"] is None
    assert u["temporary_password"]
    # The temporary password actually works.
    login = client.post("/api/v1.0/auth/login", json={"email": "jane@homa.io", "password": u["temporary_password"]})
    assert login.status_code == 200 and login.get_json()["user"]["role"] == "super_admin"


def test_create_user_with_tenant_defaults_to_tenant_admin(client, super_headers):
    tid = client.get(T, headers=super_headers).get_json()["response"][0]["tenant_id"]
    r = client.post(U, json={**NEW_USER, "tenant_id": tid}, headers=super_headers)
    assert r.status_code == 201 and r.get_json()["response"]["role"] == "tenant_admin"


def test_create_user_role_rules(client, super_headers):
    tid = client.get(T, headers=super_headers).get_json()["response"][0]["tenant_id"]
    assert client.post(U, json={**NEW_USER, "role": "wizard"}, headers=super_headers).status_code == 400
    assert client.post(U, json={**NEW_USER, "role": "hr"}, headers=super_headers).status_code == 400  # needs tenant
    assert client.post(U, json={**NEW_USER, "role": "super_admin", "tenant_id": tid},
                       headers=super_headers).status_code == 400
    assert client.post(U, json={**NEW_USER, "tenant_id": "missing"}, headers=super_headers).status_code == 404
    r = client.post(U, json={**NEW_USER, "role": "hr", "tenant_id": tid}, headers=super_headers)
    assert r.status_code == 201 and r.get_json()["response"]["role"] == "hr"


def test_create_user_duplicate_email_400(client, super_headers):
    client.post(U, json=NEW_USER, headers=super_headers)
    r = client.post(U, json={**NEW_USER, "email": "JANE@homa.io"}, headers=super_headers)
    assert r.status_code == 400 and "message" in r.get_json()


def test_create_user_validation(client, super_headers):
    assert client.post(U, json={**NEW_USER, "email": "bad"}, headers=super_headers).status_code == 400
    assert client.post(U, json={"first_name": "A"}, headers=super_headers).status_code == 400
    assert client.post(U, json=[1], headers=super_headers).status_code == 400


def test_create_user_audited_with_actor(client, super_headers):
    client.post(U, json=NEW_USER, headers=super_headers)
    log = AuditLog.query.filter_by(action="user_created").one()
    assert log.user == "platform@homa.io"


# --- AUTH_ENFORCED=false (dev mode while the frontend sends no header) ------
def test_unenforced_mode_allows_tokenless_requests(app, client):
    app.config["AUTH_ENFORCED"] = False
    assert client.get(T).status_code == 200
    assert client.post(U, json=NEW_USER).status_code == 201


def test_unenforced_mode_still_enforces_roles_when_token_present(app, client, tenant_headers):
    app.config["AUTH_ENFORCED"] = False
    assert client.get(T, headers=tenant_headers).status_code == 403
