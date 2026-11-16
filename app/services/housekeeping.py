"""Housekeeping rules shared by several endpoints."""
from ..extensions import db
from ..models import CLEANING_TASK_TYPES, OPEN_TASK_STATUSES, HousekeepingTask, utcnow


def queue_checkout_clean(room, created_by=None):
    """Queue the post-checkout cleaning task for a room that just went to `cleaning`.

    Mirrors the frontend's PropertyContext.updateRoom: 45 min, medium priority. Idempotent:
    returns None if the room already has an open checkout_clean task.
    """
    already = HousekeepingTask.query.filter(
        HousekeepingTask.room_id == room.room_id,
        HousekeepingTask.task_type == "checkout_clean",
        HousekeepingTask.status.in_(OPEN_TASK_STATUSES),
    ).first()
    if already:
        return None
    task = HousekeepingTask(
        tenant_id=room.tenant_id, room_id=room.room_id, task_type="checkout_clean",
        priority="medium", estimated_duration=45, notes="Post-checkout cleaning required",
        created_by=created_by,
    )
    db.session.add(task)
    return task


def release_room_if_clean(task):
    """After a cleaning task finishes, a `cleaning` room becomes `available` again,
    unless another cleaning task for that room is still open."""
    room = task.room
    if task.task_type not in CLEANING_TASK_TYPES or room.status != "cleaning":
        return False
    still_open = HousekeepingTask.query.filter(
        HousekeepingTask.room_id == room.room_id,
        HousekeepingTask.task_id != task.task_id,
        HousekeepingTask.task_type.in_(CLEANING_TASK_TYPES),
        HousekeepingTask.status.in_(OPEN_TASK_STATUSES),
    ).first()
    if still_open:
        return False
    room.status = "available"
    room.last_cleaned = utcnow()
    return True
