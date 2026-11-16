"""Housekeeping tasks and maintenance requests, with their assign/start/complete workflows."""
from flask import Blueprint, request
from sqlalchemy import case

from ..extensions import db
from ..models import (
    MAINTENANCE_CATEGORIES, OPEN_MAINTENANCE_STATUSES, OPEN_TASK_STATUSES, PRIORITIES, TASK_TYPES,
    ChecklistItem, HousekeepingTask, MaintenanceRequest, Room, User, utcnow,
)
from ..services.housekeeping import release_room_if_clean
from ..utils import ApiError, ok
from ..utils.audit import audit
from ..utils.auth import current_claims, resolve_tenant_id, roles_required
from ..utils.resource import (
    apply, body, get_owned, parse, v_bool, v_datetime, v_enum, v_int, v_money, v_str, v_str_list,
)
from ._crud import register_crud

bp = Blueprint("housekeeping", __name__)

ALL_TENANT = ("super_admin", "tenant_admin", "front_office", "reservations",
              "housekeeping", "fb_manager", "accounting", "hr")
HK = ("super_admin", "tenant_admin", "housekeeping")
MAINT_READ = HK + ("front_office",)
ASSIGNABLE_ROLES = ("housekeeping", "tenant_admin")


def _actor_id():
    return current_claims().get("sub")


def _actor_email():
    return current_claims().get("email")


def _guard_worker(item):
    """Housekeeping staff may only act on work that is theirs or unassigned."""
    claims = current_claims()
    if claims.get("role") == "housekeeping" and item.assigned_to not in (None, claims.get("sub")):
        raise ApiError("This work is assigned to someone else", 403)


def _assignee(tenant_id, user_id):
    user = db.session.get(User, user_id) if isinstance(user_id, str) else None
    if not user or user.tenant_id != tenant_id or not user.is_active or user.role not in ASSIGNABLE_ROLES:
        raise ApiError("assigned_to must be an active housekeeping or admin user of this tenant", 400)
    return user


def _assign_to_self_if_unassigned(item):
    claims = current_claims()
    if item.assigned_to is None and claims.get("role") == "housekeeping":
        item.assigned_to = claims["sub"]
        item.assigned_at = utcnow()


def _minutes_since(start):
    return max(1, round((utcnow() - start).total_seconds() / 60)) if start else None


# =========================== housekeeping tasks ===========================
TASK_SPEC = {
    "room_id": (v_str(36), True), "task_type": (v_enum(*TASK_TYPES), False),
    "priority": (v_enum(*PRIORITIES), False), "estimated_duration": (v_int(1, 24 * 60), False),
    "notes": (v_str(5000), False), "scheduled_at": (v_datetime, False),
}
PRIORITY_RANK = case(
    {"urgent": 0, "high": 1, "medium": 2}, value=HousekeepingTask.priority, else_=3)


def _task(task_id):
    return get_owned(HousekeepingTask, task_id, resolve_tenant_id(), "Task")


def _open_task(task):
    if task.status not in OPEN_TASK_STATUSES:
        raise ApiError(f"Task is already {task.status}", 409)


def _checklist_from(data):
    raw = data.get("checklist_items", [])
    if not isinstance(raw, list) or len(raw) > 50:
        raise ApiError("checklist_items must be a list of at most 50 items")
    out = []
    for item in raw:
        text = item.get("description") if isinstance(item, dict) else item
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 500:
            raise ApiError("Each checklist item needs a description (max 500 characters)")
        out.append(text.strip())
    return out


@bp.get("/housekeeping/tasks")
@roles_required(*HK)
def list_tasks():
    q = HousekeepingTask.query.filter_by(tenant_id=resolve_tenant_id())
    for param in ("status", "room_id", "assigned_to", "priority", "task_type"):
        if request.args.get(param):
            q = q.filter(getattr(HousekeepingTask, param) == request.args[param])
    if request.args.get("mine") in ("1", "true"):
        q = q.filter(HousekeepingTask.assigned_to == _actor_id())
    q = q.order_by(PRIORITY_RANK, HousekeepingTask.scheduled_at, HousekeepingTask.created_at)
    return ok([t.to_dict() for t in q.all()])


@bp.post("/housekeeping/tasks")
@roles_required(*HK)
def create_task():
    tid = resolve_tenant_id()
    data = body()
    v = parse(data, TASK_SPEC)
    get_owned(Room, v["room_id"], tid, "Room", status=400)
    task = HousekeepingTask(tenant_id=tid, created_by=_actor_id(), **v)
    for pos, text in enumerate(_checklist_from(data)):
        task.checklist_items.append(ChecklistItem(position=pos, description=text))
    db.session.add(task)
    audit("housekeeping_task_created", "housekeeping_task", f"{task.task_type} for room {task.room_id}",
          user=_actor_email(), commit=False)
    db.session.commit()
    return ok(task.to_dict(), 201)


@bp.get("/housekeeping/tasks/<task_id>")
@roles_required(*HK)
def get_task(task_id):
    return ok(_task(task_id).to_dict())


@bp.patch("/housekeeping/tasks/<task_id>")
@roles_required(*HK)
def update_task(task_id):
    task = _task(task_id)
    _open_task(task)
    _guard_worker(task)
    # Status moves through the action endpoints; assignment through /assign.
    v = parse(body(), TASK_SPEC, partial=True)
    if not v:
        raise ApiError("No updatable fields supplied")
    if "room_id" in v:
        if task.status != "pending":
            raise ApiError("A task's room can only change while it is pending", 409)
        get_owned(Room, v["room_id"], task.tenant_id, "Room", status=400)
    apply(task, v)
    db.session.commit()
    return ok(task.to_dict())


@bp.delete("/housekeeping/tasks/<task_id>")
@roles_required("super_admin", "tenant_admin")
def delete_task(task_id):
    task = _task(task_id)
    if task.status not in ("pending", "cancelled"):
        raise ApiError("Only pending or cancelled tasks can be deleted; cancel it first", 409)
    db.session.delete(task)
    db.session.commit()
    return ok({"deleted": task_id})


@bp.post("/housekeeping/tasks/<task_id>/assign")
@roles_required(*HK)
def assign_task(task_id):
    task = _task(task_id)
    _open_task(task)
    user = _assignee(task.tenant_id, body().get("assigned_to"))
    task.assigned_to = user.user_id
    task.assigned_at = utcnow()
    audit("housekeeping_task_assigned", "housekeeping_task", f"to {user.email}",
          user=_actor_email(), commit=False)
    db.session.commit()
    return ok(task.to_dict())


@bp.post("/housekeeping/tasks/<task_id>/start")
@roles_required(*HK)
def start_task(task_id):
    task = _task(task_id)
    if task.status != "pending":
        raise ApiError(f"Only pending tasks can be started (this one is {task.status})", 409)
    _guard_worker(task)
    _assign_to_self_if_unassigned(task)
    task.status = "in_progress"
    task.started_at = utcnow()
    db.session.commit()
    return ok(task.to_dict())


@bp.post("/housekeeping/tasks/<task_id>/complete")
@roles_required(*HK)
def complete_task(task_id):
    task = _task(task_id)
    _open_task(task)
    _guard_worker(task)
    data = request.get_json(silent=True) or {}
    v = parse(data, {"actual_duration": (v_int(1, 24 * 60), False), "notes": (v_str(5000), False)}, partial=True)
    undone = [i for i in task.checklist_items if not i.is_completed]
    if undone and data.get("force") is not True:
        raise ApiError(f"{len(undone)} checklist item(s) are not done. Finish them, or send force=true", 409)
    _assign_to_self_if_unassigned(task)
    task.status = "completed"
    task.completed_at = utcnow()
    task.actual_duration = v.get("actual_duration") or _minutes_since(task.started_at)
    if v.get("notes"):
        task.notes = f"{task.notes}\n{v['notes']}" if task.notes else v["notes"]
    released = release_room_if_clean(task)
    audit("housekeeping_task_completed", "housekeeping_task",
          f"{task.task_type}, room {task.room.number}" + ("; room released" if released else ""),
          user=_actor_email(), commit=False)
    db.session.commit()
    return ok(task.to_dict())


@bp.post("/housekeeping/tasks/<task_id>/cancel")
@roles_required(*HK)
def cancel_task(task_id):
    task = _task(task_id)
    _open_task(task)
    _guard_worker(task)
    task.status = "cancelled"
    db.session.commit()
    return ok(task.to_dict())


@bp.post("/housekeeping/tasks/<task_id>/checklist")
@roles_required(*HK)
def add_checklist_item(task_id):
    task = _task(task_id)
    _open_task(task)
    _guard_worker(task)
    v = parse(body(), {"description": (v_str(500), True)})
    task.checklist_items.append(ChecklistItem(position=len(task.checklist_items), description=v["description"]))
    db.session.commit()
    return ok(task.to_dict(), 201)


@bp.patch("/housekeeping/tasks/<task_id>/checklist/<item_id>")
@roles_required(*HK)
def update_checklist_item(task_id, item_id):
    task = _task(task_id)
    _open_task(task)
    _guard_worker(task)
    item = next((i for i in task.checklist_items if i.item_id == item_id), None)
    if not item:
        raise ApiError("Checklist item not found", 404)
    v = parse(body(), {"is_completed": (v_bool, False), "notes": (v_str(5000), False)}, partial=True)
    if not v:
        raise ApiError("No updatable fields supplied")
    if "is_completed" in v and v["is_completed"] != item.is_completed:
        item.completed_at = utcnow() if v["is_completed"] else None
    apply(item, v)
    db.session.commit()
    return ok(task.to_dict())


# ========================== maintenance requests ==========================
MAINT_SPEC = {
    "room_id": (v_str(36), True), "title": (v_str(200), True), "description": (v_str(5000), False),
    "category": (v_enum(*MAINTENANCE_CATEGORIES), False), "priority": (v_enum(*PRIORITIES), False),
    "estimated_cost": (v_money, False), "actual_cost": (v_money, False),
    "estimated_completion": (v_datetime, False), "images": (v_str_list, False), "notes": (v_str(5000), False),
}


def _maint_before_save(tenant_id, values, obj):
    if values.get("room_id"):
        get_owned(Room, values["room_id"], tenant_id, "Room", status=400)
    if obj is None:  # create: the server decides who reported it and where it starts
        values["reported_by"] = _actor_id()
        values["status"] = "reported"
        return
    if obj.status not in OPEN_MAINTENANCE_STATUSES:
        raise ApiError(f"A {obj.status} request can no longer be edited", 409)
    if "room_id" in values and obj.status not in ("reported", "assigned"):
        raise ApiError("The room can only change before work starts", 409)
    _guard_worker(obj)


register_crud(
    bp, "/maintenance-requests", MaintenanceRequest, pk="request_id", label="Maintenance request",
    spec=MAINT_SPEC,
    read_roles=MAINT_READ,
    write_roles=ALL_TENANT,          # anyone on staff can report a problem...
    patch_roles=HK,                  # ...only housekeeping/admin can triage it
    allow_delete=False,              # cancel instead, to keep the history
    before_save=_maint_before_save,
    filters={"status": str, "room_id": str, "category": str, "priority": str, "assigned_to": str},
    order=[case({"urgent": 0, "high": 1, "medium": 2}, value=MaintenanceRequest.priority, else_=3),
           MaintenanceRequest.created_at.desc()],
)


def _request(request_id):
    return get_owned(MaintenanceRequest, request_id, resolve_tenant_id(), "Maintenance request")


def _open_request(req):
    if req.status not in OPEN_MAINTENANCE_STATUSES:
        raise ApiError(f"Request is already {req.status}", 409)
    _guard_worker(req)


@bp.post("/maintenance-requests/<request_id>/assign")
@roles_required(*HK)
def assign_request(request_id):
    req = _request(request_id)
    _open_request(req)
    user = _assignee(req.tenant_id, body().get("assigned_to"))
    req.assigned_to = user.user_id
    if req.status == "reported":
        req.status = "assigned"
    db.session.commit()
    return ok(req.to_dict())


@bp.post("/maintenance-requests/<request_id>/start")
@roles_required(*HK)
def start_request(request_id):
    req = _request(request_id)
    if req.status not in ("reported", "assigned"):
        raise ApiError(f"Only reported or assigned requests can be started (this one is {req.status})", 409)
    _guard_worker(req)
    if req.assigned_to is None and current_claims().get("role") == "housekeeping":
        req.assigned_to = _actor_id()
    req.status = "in_progress"
    db.session.commit()
    return ok(req.to_dict())


@bp.post("/maintenance-requests/<request_id>/complete")
@roles_required(*HK)
def complete_request(request_id):
    req = _request(request_id)
    _open_request(req)
    v = parse(request.get_json(silent=True) or {},
              {"actual_cost": (v_money, False), "notes": (v_str(5000), False)}, partial=True)
    req.status = "completed"
    req.completed_at = utcnow()
    if v.get("actual_cost") is not None:
        req.actual_cost = v["actual_cost"]
    if v.get("notes"):
        req.notes = f"{req.notes}\n{v['notes']}" if req.notes else v["notes"]
    req.room.last_maintenance = req.completed_at
    audit("maintenance_completed", "maintenance_request", req.title, user=_actor_email(), commit=False)
    db.session.commit()
    return ok(req.to_dict())


@bp.post("/maintenance-requests/<request_id>/cancel")
@roles_required(*HK)
def cancel_request(request_id):
    req = _request(request_id)
    _open_request(req)
    req.status = "cancelled"
    db.session.commit()
    return ok(req.to_dict())
