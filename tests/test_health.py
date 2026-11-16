def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_versioned_health_pings_db(client):
    body = client.get("/api/v1.0/health").get_json()
    assert body["status"] == "ok" and body["database"] == "ok"


def test_unknown_route_is_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "message" in r.get_json()


def test_wrong_method_is_json(client):
    r = client.delete("/api/v1.0/auth/login")
    assert r.status_code == 405 and r.is_json and "message" in r.get_json()


def test_unhandled_exception_is_json(app, client):
    @app.get("/boom")
    def boom():
        raise RuntimeError("x")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = client.get("/boom")
    assert r.status_code == 500 and r.get_json() == {"message": "Internal server error"}
