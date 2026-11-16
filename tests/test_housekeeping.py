import pytest

from app.models import HousekeepingTask, Room

from .conftest import login
from .test_hotel import API, book, hdr, hdr_pw, hotel  # noqa: F401  (hotel is a fixture)


def user_id(client, email):
    return login(client, email).get_json()["user"]["user_id"]


def make_task(client, hotel, **extra):
    payload = {"room_id": hotel["r101"]["room_id"], "task_type": "cleaning", **extra}
    r = client.post(f"{API}/housekeeping/tasks", json=payload, headers=hotel["h"])
    assert r.status_code == 201, r.get_json()
    return r.get_json()["response"]


def tasks(client, hotel, qs=""):
    return client.get(f"{API}/housekeeping/tasks{qs}", headers=hotel["h"]).get_json()["response"]


def act(client, hotel, task, action, headers=None, **body):
    return client.post(f"{API}/housekeeping/tasks/{task['task_id']}/{action}", json=body,
                       headers=headers or hotel["h"])


# --- room status -> task side effect ----------------------------------------
def test_checkout_queues_checkout_clean_task(client, hotel):
    h = hotel["h"]
    rid = book(client, hotel).get_json()["response"]["reservation_id"]
    client.post(f"{API}/reservations/{rid}/check-in", headers=h)
    assert tasks(client, hotel) == []
    client.post(f"{API}/reservations/{rid}/check-out", json={"force": True}, headers=h)
    [t] = tasks(client, hotel)
    assert (t["task_type"], t["status"], t["priority"], t["estimated_duration"]) == \
        ("checkout_clean", "pending", "medium", 45)
    assert t["notes"] == "Post-checkout cleaning required" and t["room_number"] == "101"


def test_patching_room_to_cleaning_queues_one_task(client, hotel):
    h, room = hotel["h"], hotel["r101"]["room_id"]
    patch = lambda status: client.patch(f"{API}/rooms/{room}", json={"status": status}, headers=h)
    assert patch("maintenance").status_code == 200 and tasks(client, hotel) == []
    patch("cleaning")
    patch("cleaning")  # no transition, no duplicate
    assert len(tasks(client, hotel)) == 1
    patch("available")
    patch("cleaning")  # the earlier task is still open, so still no duplicate
    assert len(tasks(client, hotel)) == 1


def test_housekeeping_can_flag_room_and_queue_task(client, hotel):
    hk = hdr(client, "housekeeping@grandhotel.com")
    r = client.patch(f"{API}/rooms/{hotel['r101']['room_id']}", json={"status": "cleaning"}, headers=hk)
    assert r.status_code == 200
    [t] = tasks(client, hotel)
    assert t["created_by"] == user_id(client, "housekeeping@grandhotel.com")


def test_completing_cleaning_releases_the_room(client, hotel):
    client.patch(f"{API}/rooms/{hotel['r101']['room_id']}", json={"status": "cleaning"}, headers=hotel["h"])
    [t] = tasks(client, hotel)
    assert act(client, hotel, t, "start").status_code == 200
    done = act(client, hotel, t, "complete").get_json()["response"]
    assert done["status"] == "completed" and done["completed_at"] and done["actual_duration"] >= 1
    room = client.get(f"{API}/rooms/{hotel['r101']['room_id']}", headers=hotel["h"]).get_json()["response"]
    assert room["status"] == "available" and room["last_cleaned"]


def test_room_stays_cleaning_while_other_cleaning_open(client, hotel):
    client.patch(f"{API}/rooms/{hotel['r101']['room_id']}", json={"status": "cleaning"}, headers=hotel["h"])
    make_task(client, hotel, task_type="deep_clean")
    first = next(t for t in tasks(client, hotel) if t["task_type"] == "checkout_clean")
    act(client, hotel, first, "complete")
    room = client.get(f"{API}/rooms/{hotel['r101']['room_id']}", headers=hotel["h"]).get_json()["response"]
    assert room["status"] == "cleaning"


def test_inspection_does_not_release_room(client, hotel):
    client.patch(f"{API}/rooms/{hotel['r101']['room_id']}", json={"status": "cleaning"}, headers=hotel["h"])
    insp = make_task(client, hotel, task_type="inspection")
    act(client, hotel, insp, "complete")
    room = client.get(f"{API}/rooms/{hotel['r101']['room_id']}", headers=hotel["h"]).get_json()["response"]
    assert room["status"] == "cleaning"


# --- task CRUD / workflow ------------------------------------------------------
def test_create_task_with_checklist_and_validation(client, hotel):
    t = make_task(client, hotel, priority="high",
                  checklist_items=["Change linens", {"description": "Restock minibar"}])
    assert [i["description"] for i in t["checklist_items"]] == ["Change linens", "Restock minibar"]
    assert t["priority"] == "high" and t["status"] == "pending"
    h = hotel["h"]
    url = f"{API}/housekeeping/tasks"
    assert client.post(url, json={"room_id": "nope"}, headers=h).status_code == 400
    assert client.post(url, json={"room_id": hotel["r101"]["room_id"], "priority": "asap"}, headers=h).status_code == 400
    assert client.post(url, json={"room_id": hotel["r101"]["room_id"], "checklist_items": [""]}, headers=h).status_code == 400
    assert client.post(url, json={"room_id": hotel["r101"]["room_id"], "checklist_items": "x"}, headers=h).status_code == 400
    assert client.post(url, json={"room_id": hotel["r101"]["room_id"], "estimated_duration": 0}, headers=h).status_code == 400


def test_checklist_gates_completion(client, hotel):
    t = make_task(client, hotel, checklist_items=["a", "b"])
    blocked = act(client, hotel, t, "complete")
    assert blocked.status_code == 409 and "2 checklist" in blocked.get_json()["message"]
    for item in t["checklist_items"]:
        r = client.patch(f"{API}/housekeeping/tasks/{t['task_id']}/checklist/{item['item_id']}",
                         json={"is_completed": True}, headers=hotel["h"])
        assert r.status_code == 200
    assert all(i["completed_at"] for i in r.get_json()["response"]["checklist_items"])
    assert act(client, hotel, t, "complete", actual_duration=30).get_json()["response"]["actual_duration"] == 30


def test_force_completes_with_open_items(client, hotel):
    t = make_task(client, hotel, checklist_items=["a"])
    assert act(client, hotel, t, "complete", force=True).status_code == 200


def test_add_and_untick_checklist_items(client, hotel):
    t = make_task(client, hotel)
    url = f"{API}/housekeeping/tasks/{t['task_id']}/checklist"
    added = client.post(url, json={"description": "Wipe mirrors"}, headers=hotel["h"]).get_json()["response"]
    item = added["checklist_items"][0]
    client.patch(f"{url}/{item['item_id']}", json={"is_completed": True}, headers=hotel["h"])
    r = client.patch(f"{url}/{item['item_id']}", json={"is_completed": False}, headers=hotel["h"])
    assert r.get_json()["response"]["checklist_items"][0]["completed_at"] is None
    assert client.patch(f"{url}/missing", json={"is_completed": True}, headers=hotel["h"]).status_code == 404


def test_state_machine(client, hotel):
    t = make_task(client, hotel)
    assert act(client, hotel, t, "start").status_code == 200
    assert act(client, hotel, t, "start").status_code == 409
    assert act(client, hotel, t, "complete").status_code == 200
    for action in ("complete", "cancel", "assign"):
        assert act(client, hotel, t, action, assigned_to="x").status_code == 409
    assert client.patch(f"{API}/housekeeping/tasks/{t['task_id']}", json={"notes": "x"},
                        headers=hotel["h"]).status_code == 409


def test_cancel_and_delete_rules(client, hotel):
    h = hotel["h"]
    t = make_task(client, hotel)
    started = make_task(client, hotel)
    act(client, hotel, started, "start")
    assert client.delete(f"{API}/housekeeping/tasks/{started['task_id']}", headers=h).status_code == 409
    assert act(client, hotel, t, "cancel").get_json()["response"]["status"] == "cancelled"
    assert client.delete(f"{API}/housekeeping/tasks/{t['task_id']}", headers=h).status_code == 200


def test_patch_task_fields(client, hotel):
    t = make_task(client, hotel)
    r = client.patch(f"{API}/housekeeping/tasks/{t['task_id']}", headers=hotel["h"],
                     json={"priority": "urgent", "room_id": hotel["r102"]["room_id"],
                           "status": "completed"})  # status is ignored here
    body = r.get_json()["response"]
    assert body["priority"] == "urgent" and body["room_number"] == "102" and body["status"] == "pending"


def test_assignment_rules(client, hotel):
    t = make_task(client, hotel)
    hk_id = user_id(client, "housekeeping@grandhotel.com")
    desk_id = user_id(client, "frontdesk@grandhotel.com")
    assert act(client, hotel, t, "assign", assigned_to=desk_id).status_code == 400   # wrong role
    assert act(client, hotel, t, "assign", assigned_to="ghost").status_code == 400
    assert act(client, hotel, t, "assign").status_code == 400
    r = act(client, hotel, t, "assign", assigned_to=hk_id).get_json()["response"]
    assert r["assigned_to"] == hk_id and r["assigned_to_name"] == "Maria Rodriguez" and r["assigned_at"]


def test_housekeeper_scope(client, hotel):
    hk = hdr(client, "housekeeping@grandhotel.com")
    hk_id = user_id(client, "housekeeping@grandhotel.com")
    admin_id = user_id(client, "admin@grandhotel.com")
    mine, theirs, free = (make_task(client, hotel) for _ in range(3))
    act(client, hotel, mine, "assign", assigned_to=hk_id)
    act(client, hotel, theirs, "assign", assigned_to=admin_id)
    assert act(client, hotel, theirs, "start", headers=hk).status_code == 403
    assert act(client, hotel, theirs, "complete", headers=hk).status_code == 403
    assert act(client, hotel, mine, "start", headers=hk).status_code == 200
    started = act(client, hotel, free, "start", headers=hk).get_json()["response"]
    assert started["assigned_to"] == hk_id                              # auto-claimed
    assert {t["task_id"] for t in tasks(client, hotel, "?mine=true")} == {theirs["task_id"]}  # admin's own
    mine_hk = client.get(f"{API}/housekeeping/tasks?mine=1", headers=hk).get_json()["response"]
    assert {t["task_id"] for t in mine_hk} == {mine["task_id"], free["task_id"]}


def test_list_sorted_by_priority_and_filtered(client, hotel):
    make_task(client, hotel, priority="low")
    make_task(client, hotel, priority="urgent")
    make_task(client, hotel, priority="medium", task_type="inspection")
    assert [t["priority"] for t in tasks(client, hotel)] == ["urgent", "medium", "low"]
    assert len(tasks(client, hotel, "?task_type=inspection")) == 1
    assert tasks(client, hotel, "?status=completed") == []


def test_task_permissions(client, hotel):
    desk = hdr(client, "frontdesk@grandhotel.com")
    assert client.get(f"{API}/housekeeping/tasks", headers=desk).status_code == 403
    hk = hdr(client, "housekeeping@grandhotel.com")
    t = make_task(client, hotel)
    assert client.delete(f"{API}/housekeeping/tasks/{t['task_id']}", headers=hk).status_code == 403


def test_tasks_are_tenant_isolated(client, hotel, super_headers):
    t = make_task(client, hotel)
    t2 = client.post(f"{API}/tenant", headers=super_headers, json={
        "tenant_name": "Other Inn", "email": "o@inn.com", "phone": "1", "address": "x"}).get_json()["response"]
    u = client.post(f"{API}/user", headers=super_headers, json={
        "first_name": "O", "last_name": "W", "email": "owner@inn.com", "phone": "1",
        "tenant_id": t2["tenant_id"]}).get_json()["response"]
    other = hdr_pw(client, "owner@inn.com", u["temporary_password"])
    assert client.get(f"{API}/housekeeping/tasks", headers=other).get_json()["response"] == []
    assert client.get(f"{API}/housekeeping/tasks/{t['task_id']}", headers=other).status_code == 404
    assert act(client, hotel, t, "cancel", headers=other).status_code == 404
    r = client.post(f"{API}/housekeeping/tasks", headers=other, json={"room_id": hotel["r101"]["room_id"]})
    assert r.status_code == 400


# --- maintenance requests -------------------------------------------------------
def report(client, hotel, headers=None, **extra):
    payload = {"room_id": hotel["r101"]["room_id"], "title": "Leaking tap", **extra}
    return client.post(f"{API}/maintenance-requests", json=payload, headers=headers or hotel["h"])


def test_anyone_can_report_and_status_is_server_controlled(client, hotel):
    desk = hdr(client, "frontdesk@grandhotel.com")
    r = report(client, hotel, headers=desk, category="plumbing", status="completed", priority="high")
    assert r.status_code == 201
    req = r.get_json()["response"]
    assert req["status"] == "reported" and req["category"] == "plumbing"
    assert req["reported_by_name"] == "James Mitchell" and req["room_number"] == "101"
    assert report(client, hotel, title="").status_code == 400
    assert report(client, hotel, category="magic").status_code == 400
    assert report(client, hotel, room_id="nope").status_code == 400
    assert report(client, hotel, estimated_cost=-1).status_code == 400


def test_maintenance_read_and_edit_permissions(client, hotel):
    desk = hdr(client, "frontdesk@grandhotel.com")
    fb = hdr(client, "restaurant@grandhotel.com")
    req = report(client, hotel).get_json()["response"]
    url = f"{API}/maintenance-requests"
    assert client.get(url, headers=desk).status_code == 200
    assert client.get(url, headers=fb).status_code == 403
    assert client.patch(f"{url}/{req['request_id']}", json={"notes": "x"}, headers=desk).status_code == 403
    assert client.post(f"{url}/{req['request_id']}/start", headers=desk).status_code == 403
    assert client.delete(f"{url}/{req['request_id']}", headers=hotel["h"]).status_code in (404, 405)


def test_maintenance_lifecycle(client, hotel):
    h = hotel["h"]
    hk_id = user_id(client, "housekeeping@grandhotel.com")
    req = report(client, hotel, estimated_cost=50).get_json()["response"]
    base = f"{API}/maintenance-requests/{req['request_id']}"

    assert client.post(f"{base}/assign", json={"assigned_to": hk_id}, headers=h).get_json()["response"]["status"] == "assigned"
    assert client.post(f"{base}/start", headers=h).get_json()["response"]["status"] == "in_progress"
    assert client.post(f"{base}/start", headers=h).status_code == 409
    done = client.post(f"{base}/complete", json={"actual_cost": 42.5, "notes": "New washer"}, headers=h)
    body = done.get_json()["response"]
    assert body["status"] == "completed" and body["actual_cost"] == 42.5 and body["completed_at"]
    assert body["notes"] == "New washer" and body["estimated_cost"] == 50.0
    room = client.get(f"{API}/rooms/{hotel['r101']['room_id']}", headers=h).get_json()["response"]
    assert room["last_maintenance"]
    # finished work is frozen
    assert client.patch(base, json={"title": "x"}, headers=h).status_code == 409
    assert client.post(f"{base}/cancel", headers=h).status_code == 409
    assert client.post(f"{base}/assign", json={"assigned_to": hk_id}, headers=h).status_code == 409


def test_maintenance_start_claims_and_scope(client, hotel):
    hk = hdr(client, "housekeeping@grandhotel.com")
    admin_id = user_id(client, "admin@grandhotel.com")
    free = report(client, hotel).get_json()["response"]
    taken = report(client, hotel, title="Broken lamp").get_json()["response"]
    client.post(f"{API}/maintenance-requests/{taken['request_id']}/assign", json={"assigned_to": admin_id},
                headers=hotel["h"])
    assert client.post(f"{API}/maintenance-requests/{taken['request_id']}/start", headers=hk).status_code == 403
    r = client.post(f"{API}/maintenance-requests/{free['request_id']}/start", headers=hk).get_json()["response"]
    assert r["assigned_to"] == user_id(client, "housekeeping@grandhotel.com")


def test_maintenance_edit_rules_and_filters(client, hotel):
    h = hotel["h"]
    a = report(client, hotel, priority="low").get_json()["response"]
    b = report(client, hotel, title="Sparking socket", category="electrical", priority="urgent").get_json()["response"]
    listed = client.get(f"{API}/maintenance-requests", headers=h).get_json()["response"]
    assert [r["request_id"] for r in listed] == [b["request_id"], a["request_id"]]   # urgent first
    assert len(client.get(f"{API}/maintenance-requests?category=electrical", headers=h).get_json()["response"]) == 1
    url = f"{API}/maintenance-requests/{a['request_id']}"
    assert client.patch(url, json={"room_id": hotel["r102"]["room_id"], "title": "Moved"}, headers=h).status_code == 200
    client.post(f"{url}/start", headers=h)
    assert client.patch(url, json={"room_id": hotel["r101"]["room_id"]}, headers=h).status_code == 409
    assert client.patch(url, json={"title": None}, headers=h).status_code == 400
    assert client.patch(url, json={}, headers=h).status_code == 400
    assert client.post(f"{API}/maintenance-requests/{b['request_id']}/cancel", headers=h).status_code == 200
    assert Room.query.count() == 2 and HousekeepingTask.query.count() == 0
