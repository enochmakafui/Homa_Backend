import uuid
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from ..extensions import db

ROLES = (
    "super_admin", "tenant_admin", "front_office", "reservations",
    "housekeeping", "fb_manager", "accounting", "hr",
)
ACTIVE = 1
SUSPENDED = 2

_hasher = PasswordHasher()


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    """Serialise a naive-UTC datetime as ISO 8601 with a Z suffix."""
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


class Timestamps:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Tenant(Timestamps, db.Model):
    __tablename__ = "tenants"

    tenant_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_name = db.Column(db.String(200), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    phone = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(500), nullable=False)
    account_status = db.Column(db.String(20), nullable=False, default="Active")  # Active|Suspended|Pending
    plan = db.Column(db.String(20), nullable=False, default="starter")
    date_created = db.Column(db.DateTime, default=utcnow, nullable=False)

    def to_dict(self):
        return {
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "account_status": self.account_status,
            "plan": self.plan,
            "date_created": iso(self.date_created),
        }


class User(Timestamps, db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=True, index=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(255), nullable=False)
    account_status_id = db.Column(db.Integer, nullable=False, default=ACTIVE)
    last_login_at = db.Column(db.DateTime)
    date_created = db.Column(db.DateTime, default=utcnow, nullable=False)

    tenant = db.relationship("Tenant", lazy="joined")
    user_roles = db.relationship("UserRole", cascade="all, delete-orphan", lazy="joined",
                                 order_by="UserRole.id")

    @property
    def role(self):
        return self.user_roles[0].role if self.user_roles else None

    @property
    def is_active(self):
        return self.account_status_id == ACTIVE

    def set_password(self, password: str):
        self.password_hash = _hasher.hash(password)

    def check_password(self, password: str) -> bool:
        try:
            return _hasher.verify(self.password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def to_dict(self):
        """Shape of an item in GET /user/admins."""
        return {
            "user_id": self.user_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant.tenant_name if self.tenant else None,
            "account_status_id": self.account_status_id,
            "last_login_at": iso(self.last_login_at),
            "date_created": iso(self.date_created),
            "role": self.role,
            "user_roles": [{"role": r.role} for r in self.user_roles],
        }

    def to_login_dict(self):
        return {
            "user_id": self.user_id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant.tenant_name if self.tenant else None,
            "role": self.role,
        }


class UserRole(db.Model):
    __tablename__ = "user_roles"
    __table_args__ = (db.UniqueConstraint("user_id", "role"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey("users.user_id"), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.String(36), primary_key=True, default=new_id)
    timestamp = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    user = db.Column(db.String(255))  # actor email, or attempted email on failed login
    action = db.Column(db.String(100), nullable=False)
    resource = db.Column(db.String(100))
    details = db.Column(db.Text)
    severity = db.Column(db.String(20), nullable=False, default="info")  # info|warning|error|critical
    tenant = db.Column(db.String(200))
    ip_address = db.Column(db.String(64))


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    jti = db.Column(db.String(64), primary_key=True)
    revoked_at = db.Column(db.DateTime, default=utcnow, nullable=False)


# Domain tables live in their own modules; imported last because they use the helpers above.
from .hotel import (  # noqa: E402,F401
    BLOCKING_STATUSES, PROPERTY_TYPES, RESERVATION_STATUSES, ROOM_CATEGORIES, ROOM_STATUSES,
    FloorLayout, Guest, Property, Reservation, Room, RoomType,
)
from .housekeeping import (  # noqa: E402,F401
    CLEANING_TASK_TYPES, MAINTENANCE_CATEGORIES, OPEN_MAINTENANCE_STATUSES, OPEN_TASK_STATUSES,
    PRIORITIES, TASK_TYPES, ChecklistItem, HousekeepingTask, MaintenanceRequest,
)
