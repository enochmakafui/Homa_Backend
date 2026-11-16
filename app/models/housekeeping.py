"""Housekeeping tasks and maintenance requests (build-order step 6)."""
from ..extensions import db
from . import Timestamps, iso, new_id
from .hotel import money

TASK_TYPES = ("cleaning", "inspection", "maintenance", "deep_clean", "checkout_clean")
TASK_STATUSES = ("pending", "in_progress", "completed", "cancelled")
PRIORITIES = ("low", "medium", "high", "urgent")
OPEN_TASK_STATUSES = ("pending", "in_progress")
# Completing one of these returns a room that is `cleaning` to `available`.
CLEANING_TASK_TYPES = ("cleaning", "deep_clean", "checkout_clean")

MAINTENANCE_CATEGORIES = ("plumbing", "electrical", "hvac", "furniture", "appliance", "structural", "other")
MAINTENANCE_STATUSES = ("reported", "assigned", "in_progress", "completed", "cancelled")
OPEN_MAINTENANCE_STATUSES = ("reported", "assigned", "in_progress")


def _person(user):
    return f"{user.first_name} {user.last_name}" if user else None


class HousekeepingTask(Timestamps, db.Model):
    __tablename__ = "housekeeping_tasks"

    task_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    room_id = db.Column(db.String(36), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    assigned_to = db.Column(db.String(36), db.ForeignKey("users.user_id"), index=True)
    created_by = db.Column(db.String(36), db.ForeignKey("users.user_id"))
    task_type = db.Column(db.String(20), nullable=False, default="cleaning")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    priority = db.Column(db.String(10), nullable=False, default="medium")
    estimated_duration = db.Column(db.Integer)  # minutes
    actual_duration = db.Column(db.Integer)     # minutes
    notes = db.Column(db.Text)
    assigned_at = db.Column(db.DateTime)
    scheduled_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    room = db.relationship("Room", lazy="joined")
    assignee = db.relationship("User", foreign_keys=[assigned_to], lazy="joined")
    checklist_items = db.relationship("ChecklistItem", cascade="all, delete-orphan", lazy="joined",
                                      order_by="ChecklistItem.position")

    def to_dict(self):
        return {
            "task_id": self.task_id, "room_id": self.room_id,
            "room_number": self.room.number if self.room else None,
            "assigned_to": self.assigned_to, "assigned_to_name": _person(self.assignee),
            "created_by": self.created_by, "task_type": self.task_type, "status": self.status,
            "priority": self.priority, "estimated_duration": self.estimated_duration,
            "actual_duration": self.actual_duration, "notes": self.notes,
            "assigned_at": iso(self.assigned_at), "scheduled_at": iso(self.scheduled_at),
            "started_at": iso(self.started_at), "completed_at": iso(self.completed_at),
            "created_at": iso(self.created_at),
            "checklist_items": [i.to_dict() for i in self.checklist_items],
        }


class ChecklistItem(db.Model):
    __tablename__ = "checklist_items"

    item_id = db.Column(db.String(36), primary_key=True, default=new_id)
    task_id = db.Column(db.String(36), db.ForeignKey("housekeeping_tasks.task_id"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.String(500), nullable=False)
    is_completed = db.Column(db.Boolean, nullable=False, default=False)
    completed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)

    def to_dict(self):
        return {"item_id": self.item_id, "description": self.description,
                "is_completed": self.is_completed, "completed_at": iso(self.completed_at),
                "notes": self.notes}


class MaintenanceRequest(Timestamps, db.Model):
    __tablename__ = "maintenance_requests"

    request_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    room_id = db.Column(db.String(36), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    reported_by = db.Column(db.String(36), db.ForeignKey("users.user_id"))
    assigned_to = db.Column(db.String(36), db.ForeignKey("users.user_id"), index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(20), nullable=False, default="other")
    priority = db.Column(db.String(10), nullable=False, default="medium")
    status = db.Column(db.String(20), nullable=False, default="reported", index=True)
    estimated_cost = db.Column(db.Numeric(12, 2))
    actual_cost = db.Column(db.Numeric(12, 2))
    estimated_completion = db.Column(db.DateTime)
    images = db.Column(db.JSON, nullable=False, default=list)
    notes = db.Column(db.Text)
    completed_at = db.Column(db.DateTime)

    room = db.relationship("Room", lazy="joined")
    reporter = db.relationship("User", foreign_keys=[reported_by], lazy="joined")
    assignee = db.relationship("User", foreign_keys=[assigned_to], lazy="joined")

    def to_dict(self):
        return {
            "request_id": self.request_id, "room_id": self.room_id,
            "room_number": self.room.number if self.room else None,
            "reported_by": self.reported_by, "reported_by_name": _person(self.reporter),
            "assigned_to": self.assigned_to, "assigned_to_name": _person(self.assignee),
            "title": self.title, "description": self.description, "category": self.category,
            "priority": self.priority, "status": self.status,
            "estimated_cost": money(self.estimated_cost), "actual_cost": money(self.actual_cost),
            "estimated_completion": iso(self.estimated_completion), "images": self.images,
            "notes": self.notes, "completed_at": iso(self.completed_at),
            "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
        }
