import pytest

from app.models import AuditLog

from .conftest import PASSWORD, login

API = "/api/v1.0"


def hdr(client, email):
    return {"Authorization": f"Bearer {login(client, email).get_json()['access_token']}"}


@pytest.fixture()
def hotel(client, tenant_headers):
    """A property, a room type ($100, sleeps 2), two rooms and a guest, created via the API."""
    def post(path, payload):
        r = client.post(f"{API}{path}", json=payload, headers=tenant_headers)
        assert r.status_code == 201, r.get_json()
        return r.get_json()["response"]

    prop = post("/properties", {"name": "Main Building", "address": "1 Palace Ave", "floors": 3})
    rtype = post("/room-types", {"name": "Deluxe King", "type": "deluxe", "base_price": 100,
                                 "max_occupancy": 2})
    r101 = post("/rooms", {"property_id": prop["property_id"], "type_id": rtype["type_id"],
                           "number": "101", "floor": 1})
    r102 = post("/rooms", {"property_id": prop["property_id"], "type_id": rtype["type_id"],
                           "number": "102", "floor": 1})
    guest = post("/guests", {"first_name": "Ada", "last_name": "Lovelace", "email": "ADA@Example.com",
                             "phone": "+44-1"})
    return {"prop": prop, "type": rtype, "r101": r101, "r102": r102, "guest": guest, "h": tenant_headers}


def book(client, hotel, room="r101", cin="2026-12-01", cout="2026-12-04", **extra):
    return client.post(f"{API}/reservations", headers=hotel["h"], json={
        "guest_id": hotel["guest"]["guest_id"], "room_id": hotel[room]["room_id"],
        "check_in_date": cin, "check_out_date": cout, **extra})


# --- properties / room types / rooms ---------------------------------------
def test_property_crud_and_defaults(client, hotel):
    p = hotel["prop"]
    assert p["property_type"] == "hotel" and p["is_active"] is True and p["amenities"] == []
    r = client.patch(f"{API}/properties/{p['property_id']}", json={"description": "Nice"}, headers=hotel["h"])
    assert r.get_json()["response"]["description"] == "Nice"
    assert client.get(f"{API}/properties/{p['property_id']}", headers=hotel["h"]).status_code == 200
    assert len(client.get(f"{API}/properties", headers=hotel["h"]).get_json()["response"]) == 1


def test_validation_errors(client, tenant_headers):
    h = tenant_headers
    assert client.post(f"{API}/properties", json={"name": "X"}, headers=h).status_code == 400
    r = client.post(f"{API}/properties", json={"name": "X", "address": "Y", "property_type": "castle"}, headers=h)
    assert r.status_code == 400 and "property_type" in r.get_json()["message"]
    r = client.post(f"{API}/room-types", json={"name": "A", "base_price": -5}, headers=h)
    assert r.status_code == 400 and "base_price" in r.get_json()["message"]
    r = client.post(f"{API}/room-types", json={"name": "A", "base_price": "abc"}, headers=h)
    assert r.status_code == 400
    r = client.post(f"{API}/properties", json={"name": "X", "address": "Y", "floors": True}, headers=h)
    assert r.status_code == 400
    r = client.post(f"{API}/properties", json={"name": "X", "address": "Y", "floors": None}, headers=h)
    assert r.status_code == 400  # NOT NULL column, not a 500


def test_room_number_unique_per_property(client, hotel):
    r = client.post(f"{API}/rooms", headers=hotel["h"], json={
        "property_id": hotel["prop"]["property_id"], "type_id": hotel["type"]["type_id"], "number": "101"})
    assert r.status_code == 409


def test_room_requires_own_tenant_references(client, hotel):
    r = client.post(f"{API}/rooms", headers=hotel["h"], json={
        "property_id": "nope", "type_id": hotel["type"]["type_id"], "number": "9"})
    assert r.status_code == 400


def test_room_filters(client, hotel):
    client.patch(f"{API}/rooms/{hotel['r102']['room_id']}", json={"status": "maintenance"}, headers=hotel["h"])
    got = client.get(f"{API}/rooms?status=maintenance&floor=1", headers=hotel["h"]).get_json()["response"]
    assert [r["number"] for r in got] == ["102"]
    assert client.get(f"{API}/rooms?floor=abc", headers=hotel["h"]).status_code == 400


def test_in_use_rows_cannot_be_deleted(client, hotel):
    h = hotel["h"]
    assert client.delete(f"{API}/properties/{hotel['prop']['property_id']}", headers=h).status_code == 409
    assert client.delete(f"{API}/room-types/{hotel['type']['type_id']}", headers=h).status_code == 409
    book(client, hotel)
    assert client.delete(f"{API}/rooms/{hotel['r101']['room_id']}", headers=h).status_code == 409
    assert client.delete(f"{API}/guests/{hotel['guest']['guest_id']}", headers=h).status_code == 409
    assert client.delete(f"{API}/rooms/{hotel['r102']['room_id']}", headers=h).status_code == 200


def test_floor_layouts(client, hotel):
    body = {"property_id": hotel["prop"]["property_id"], "floor": 1, "layout": {"rooms": []}}
    assert client.post(f"{API}/floor-layouts", json=body, headers=hotel["h"]).status_code == 201
    assert client.post(f"{API}/floor-layouts", json=body, headers=hotel["h"]).status_code == 409
    assert client.post(f"{API}/floor-layouts", json={**body, "floor": 2, "property_id": "x"},
                       headers=hotel["h"]).status_code == 400


def test_role_permissions(client, hotel):
    desk = hdr(client, "frontdesk@grandhotel.com")
    hk = hdr(client, "housekeeping@grandhotel.com")
    room = hotel["r101"]["room_id"]
    assert client.get(f"{API}/rooms", headers=hk).status_code == 200            # everyone reads
    assert client.post(f"{API}/properties", headers=desk, json={"name": "a", "address": "b"}).status_code == 403
    # operational change OK, structural change not
    assert client.patch(f"{API}/rooms/{room}", json={"status": "cleaning"}, headers=hk).status_code == 200
    assert client.patch(f"{API}/rooms/{room}", json={"number": "999"}, headers=hk).status_code == 403
    # housekeeping cannot see guests or reservations
    assert client.get(f"{API}/guests", headers=hk).status_code == 403
    assert client.get(f"{API}/reservations", headers=hk).status_code == 403


# --- tenant isolation -------------------------------------------------------
def test_tenants_cannot_see_each_other(client, hotel, super_headers):
    t2 = client.post(f"{API}/tenant", headers=super_headers, json={
        "tenant_name": "Other Inn", "email": "o@inn.com", "phone": "1", "address": "x"}).get_json()["response"]
    u = client.post(f"{API}/user", headers=super_headers, json={
        "first_name": "O", "last_name": "Wner", "email": "owner@inn.com", "phone": "1",
        "tenant_id": t2["tenant_id"]}).get_json()["response"]
    other = hdr_pw(client, "owner@inn.com", u["temporary_password"])

    assert client.get(f"{API}/properties", headers=other).get_json()["response"] == []
    assert client.get(f"{API}/guests", headers=other).get_json()["response"] == []
    pid = hotel["prop"]["property_id"]
    assert client.get(f"{API}/properties/{pid}", headers=other).status_code == 404
    assert client.patch(f"{API}/properties/{pid}", json={"name": "Hax"}, headers=other).status_code == 404
    assert client.delete(f"{API}/guests/{hotel['guest']['guest_id']}", headers=other).status_code == 404
    # A client-supplied tenant_id must not redirect a tenant user's writes.
    r = client.post(f"{API}/properties", headers=other, json={
        "name": "Mine", "address": "x", "tenant_id": hotel["prop"]["property_id"]})
    assert r.status_code == 201
    assert len(client.get(f"{API}/properties", headers=hotel["h"]).get_json()["response"]) == 1
    # Cross-tenant references are rejected.
    r = client.post(f"{API}/reservations", headers=other, json={
        "guest_id": hotel["guest"]["guest_id"], "room_id": hotel["r101"]["room_id"],
        "check_in_date": "2026-12-01", "check_out_date": "2026-12-02"})
    assert r.status_code == 400


def hdr_pw(client, email, pw):
    return {"Authorization": f"Bearer {login(client, email, pw).get_json()['access_token']}"}


def test_super_admin_must_name_tenant(client, hotel, super_headers):
    assert client.get(f"{API}/properties", headers=super_headers).status_code == 400
    tid = hotel["prop"] and client.get(f"{API}/tenant", headers=super_headers).get_json()["response"][0]["tenant_id"]
    r = client.get(f"{API}/properties?tenant_id={tid}", headers=super_headers)
    assert r.status_code == 200 and len(r.get_json()["response"]) == 1
    assert client.get(f"{API}/properties?tenant_id=bogus", headers=super_headers).status_code == 404


# --- guests -----------------------------------------------------------------
def test_guest_email_normalised_and_search(client, hotel):
    assert hotel["guest"]["email"] == "ada@example.com"
    h = hotel["h"]
    for q in ("lovelace", "ada love", "ADA@EXAMPLE", "+44"):
        assert len(client.get(f"{API}/guests?q={q}", headers=h).get_json()["response"]) == 1, q
    assert client.get(f"{API}/guests?q=zzz", headers=h).get_json()["response"] == []
    assert client.get(f"{API}/guests?q=%25", headers=h).get_json()["response"] == []  # wildcard is literal
    r = client.post(f"{API}/guests", headers=h, json={"first_name": "A", "last_name": "B", "email": "bad"})
    assert r.status_code == 400
    r = client.patch(f"{API}/guests/{hotel['guest']['guest_id']}", headers=h, json={"vip_status": True})
    assert r.get_json()["response"]["vip_status"] is True
    assert len(client.get(f"{API}/guests?vip_status=true", headers=h).get_json()["response"]) == 1


# --- reservations -----------------------------------------------------------
def test_create_reservation_prices_the_stay(client, hotel):
    r = book(client, hotel)
    assert r.status_code == 201
    res = r.get_json()["response"]
    assert res["status"] == "confirmed" and res["total_amount"] == 300.0  # 3 nights x $100
    assert res["balance"] == 300.0 and res["guest_name"] == "Ada Lovelace" and res["room_number"] == "101"
    assert (res["adults"], res["children"]) == (1, 0)
    assert AuditLog.query.filter_by(action="reservation_created").count() == 1


def test_reservation_validation(client, hotel):
    assert book(client, hotel, cin="2026-12-04", cout="2026-12-04").status_code == 400
    assert book(client, hotel, cin="2026-12-05", cout="2026-12-04").status_code == 400
    assert book(client, hotel, cin="soon").status_code == 400
    assert book(client, hotel, adults=2, children=1).status_code == 400          # sleeps 2
    assert book(client, hotel, adults=0).status_code == 400
    assert book(client, hotel, total_amount=100, paid_amount=200).status_code == 400
    bad_guest = client.post(f"{API}/reservations", headers=hotel["h"], json={
        "guest_id": "nope", "room_id": hotel["r101"]["room_id"],
        "check_in_date": "2026-12-01", "check_out_date": "2026-12-02"})
    assert bad_guest.status_code == 400


def test_overlap_rejected_adjacent_allowed(client, hotel):
    assert book(client, hotel, cin="2026-12-01", cout="2026-12-04").status_code == 201
    assert book(client, hotel, cin="2026-12-03", cout="2026-12-06").status_code == 409   # overlaps tail
    assert book(client, hotel, cin="2026-11-29", cout="2026-12-02").status_code == 409   # overlaps head
    assert book(client, hotel, cin="2026-12-02", cout="2026-12-03").status_code == 409   # inside
    assert book(client, hotel, cin="2026-11-28", cout="2026-12-10").status_code == 409   # encloses
    assert book(client, hotel, cin="2026-12-04", cout="2026-12-06").status_code == 201   # same-day turnover
    assert book(client, hotel, room="r102", cin="2026-12-01", cout="2026-12-04").status_code == 201


def test_cancelled_booking_frees_the_room(client, hotel):
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    assert book(client, hotel).status_code == 409
    r = client.post(f"{API}/reservations/{rid}/cancel", json={"reason": "changed plans"}, headers=hotel["h"])
    assert r.status_code == 200 and r.get_json()["response"]["status"] == "cancelled"
    assert book(client, hotel).status_code == 201
    assert client.post(f"{API}/reservations/{rid}/cancel", headers=hotel["h"]).status_code == 409


def test_availability_search(client, hotel):
    book(client, hotel)
    q = "check_in=2026-12-02&check_out=2026-12-03"
    rooms = client.get(f"{API}/reservations/availability?{q}", headers=hotel["h"]).get_json()["response"]
    assert [r["number"] for r in rooms] == ["102"] and rooms[0]["quote"] == 100.0
    assert client.get(f"{API}/reservations/availability?check_in=2026-12-04&check_out=2026-12-05",
                      headers=hotel["h"]).get_json()["response"].__len__() == 2
    assert client.get(f"{API}/reservations/availability?check_in=x", headers=hotel["h"]).status_code == 400
    assert client.get(f"{API}/reservations/availability?{q}&guests=3", headers=hotel["h"]).get_json()["response"] == []


def test_list_filters(client, hotel):
    book(client, hotel, cin="2026-12-01", cout="2026-12-03")
    book(client, hotel, cin="2026-12-10", cout="2026-12-12")
    got = lambda qs: client.get(f"{API}/reservations?{qs}", headers=hotel["h"]).get_json()["response"]
    assert len(got("")) == 2
    assert len(got("from=2026-12-05")) == 1
    assert len(got("to=2026-12-05")) == 1
    assert len(got("status=cancelled")) == 0
    assert client.get(f"{API}/reservations?from=bad", headers=hotel["h"]).status_code == 400


def test_modify_reservation(client, hotel):
    res = book(client, hotel).get_json()["response"]
    rid = res["reservation_id"]
    book(client, hotel, room="r102", cin="2026-12-01", cout="2026-12-04")
    # moving into a booked room is refused, a free window is repriced
    assert client.patch(f"{API}/reservations/{rid}", json={"room_id": hotel["r102"]["room_id"]},
                        headers=hotel["h"]).status_code == 409
    r = client.patch(f"{API}/reservations/{rid}", json={"check_out_date": "2026-12-06"}, headers=hotel["h"])
    assert r.status_code == 200 and r.get_json()["response"]["total_amount"] == 500.0
    # its own dates do not count as a clash
    assert client.patch(f"{API}/reservations/{rid}", json={"check_in_date": "2026-12-02"},
                        headers=hotel["h"]).status_code == 200
    assert client.patch(f"{API}/reservations/{rid}", json={"guest_id": "x"}, headers=hotel["h"]).status_code == 400


def test_check_in_and_out_flow(client, hotel):
    h = hotel["h"]
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    room_url = f"{API}/rooms/{hotel['r101']['room_id']}"

    assert client.post(f"{API}/reservations/{rid}/check-out", headers=h).status_code == 409  # not in yet
    r = client.post(f"{API}/reservations/{rid}/check-in", headers=h)
    assert r.status_code == 200 and r.get_json()["response"]["status"] == "checked_in"
    room = client.get(room_url, headers=h).get_json()["response"]
    assert room["status"] == "occupied" and room["current_guest_id"] == hotel["guest"]["guest_id"]
    assert client.post(f"{API}/reservations/{rid}/check-in", headers=h).status_code == 409
    assert client.post(f"{API}/reservations/{rid}/cancel", headers=h).status_code == 409
    assert client.patch(f"{API}/reservations/{rid}", json={"adults": 2}, headers=h).status_code == 409

    # unpaid balance blocks check-out unless forced
    blocked = client.post(f"{API}/reservations/{rid}/check-out", headers=h)
    assert blocked.status_code == 409 and "balance" in blocked.get_json()["message"]
    client.patch(f"{API}/reservations/{rid}", json={"paid_amount": 300}, headers=h)
    r = client.post(f"{API}/reservations/{rid}/check-out", headers=h)
    assert r.status_code == 200 and r.get_json()["response"]["status"] == "checked_out"
    room = client.get(room_url, headers=h).get_json()["response"]
    assert room["status"] == "cleaning" and room["current_guest_id"] is None


def test_forced_checkout_is_audited(client, hotel):
    h = hotel["h"]
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    client.post(f"{API}/reservations/{rid}/check-in", headers=h)
    r = client.post(f"{API}/reservations/{rid}/check-out", json={"force": True}, headers=h)
    assert r.status_code == 200
    assert AuditLog.query.filter_by(action="check_out", severity="warning").count() == 1


def test_check_in_refused_when_room_not_ready(client, hotel):
    h = hotel["h"]
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    client.patch(f"{API}/rooms/{hotel['r101']['room_id']}", json={"status": "cleaning"}, headers=h)
    assert client.post(f"{API}/reservations/{rid}/check-in", headers=h).status_code == 409


def test_reservations_role_rules(client, hotel):
    rsv = hdr(client, "reservations@grandhotel.com")
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    assert client.get(f"{API}/reservations", headers=rsv).status_code == 200
    assert client.post(f"{API}/reservations/{rid}/check-in", headers=rsv).status_code == 403
    assert client.patch(f"{API}/reservations/{rid}", json={"paid_amount": 10}, headers=rsv).status_code == 403
    assert client.patch(f"{API}/reservations/{rid}", json={"special_requests": "quiet"},
                        headers=rsv).status_code == 200


def test_unenforced_mode_needs_explicit_tenant(app, client, hotel):
    app.config["AUTH_ENFORCED"] = False
    assert client.get(f"{API}/guests").status_code == 400
    tid = client.get(f"{API}/tenant").get_json()["response"][0]["tenant_id"]
    assert client.get(f"{API}/guests?tenant_id={tid}").status_code == 200
