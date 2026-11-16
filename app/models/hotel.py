"""Tenant-scoped property, guest and reservation tables (build-order step 5)."""
from decimal import Decimal

from ..extensions import db
from . import Timestamps, iso, new_id

PROPERTY_TYPES = ("hotel", "resort", "apartment", "house", "villa", "condo")
ROOM_CATEGORIES = ("standard", "deluxe", "suite", "presidential", "family", "accessible")
ROOM_STATUSES = ("available", "occupied", "cleaning", "maintenance", "reserved", "out_of_order")
RESERVATION_STATUSES = ("confirmed", "checked_in", "checked_out", "cancelled", "no_show")
# Statuses that hold a room for their date range.
BLOCKING_STATUSES = ("confirmed", "checked_in")


def money(v):
    return float(v) if v is not None else None


class Property(Timestamps, db.Model):
    __tablename__ = "properties"

    property_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(500), nullable=False)
    property_type = db.Column(db.String(20), nullable=False, default="hotel")
    floors = db.Column(db.Integer, nullable=False, default=1)
    total_rooms = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text)
    amenities = db.Column(db.JSON, nullable=False, default=list)
    images = db.Column(db.JSON, nullable=False, default=list)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def to_dict(self):
        return {
            "property_id": self.property_id, "name": self.name, "address": self.address,
            "property_type": self.property_type, "floors": self.floors,
            "total_rooms": self.total_rooms, "description": self.description,
            "amenities": self.amenities, "images": self.images, "is_active": self.is_active,
            "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
        }


class RoomType(Timestamps, db.Model):
    __tablename__ = "room_types"

    type_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(20), nullable=False, default="standard")
    base_price = db.Column(db.Numeric(12, 2), nullable=False)
    max_occupancy = db.Column(db.Integer, nullable=False, default=2)
    bed_count = db.Column(db.Integer, nullable=False, default=1)
    bed_type = db.Column(db.String(50))
    size = db.Column(db.Integer)  # sq ft
    amenities = db.Column(db.JSON, nullable=False, default=list)
    description = db.Column(db.Text)
    images = db.Column(db.JSON, nullable=False, default=list)

    def to_dict(self):
        return {
            "type_id": self.type_id, "name": self.name, "type": self.type,
            "base_price": money(self.base_price), "max_occupancy": self.max_occupancy,
            "bed_count": self.bed_count, "bed_type": self.bed_type, "size": self.size,
            "amenities": self.amenities, "description": self.description, "images": self.images,
        }


class Room(Timestamps, db.Model):
    __tablename__ = "rooms"
    __table_args__ = (db.UniqueConstraint("property_id", "number"),)

    room_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    property_id = db.Column(db.String(36), db.ForeignKey("properties.property_id"), nullable=False, index=True)
    type_id = db.Column(db.String(36), db.ForeignKey("room_types.type_id"), nullable=False, index=True)
    number = db.Column(db.String(20), nullable=False)
    floor = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="available")
    current_guest_id = db.Column(db.String(36), db.ForeignKey("guests.guest_id"))
    last_cleaned = db.Column(db.DateTime)
    last_maintenance = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    room_type = db.relationship("RoomType", lazy="joined")

    def to_dict(self):
        return {
            "room_id": self.room_id, "property_id": self.property_id, "type_id": self.type_id,
            "type_name": self.room_type.name if self.room_type else None,
            "number": self.number, "floor": self.floor, "status": self.status,
            "current_guest_id": self.current_guest_id,
            "last_cleaned": iso(self.last_cleaned), "last_maintenance": iso(self.last_maintenance),
            "notes": self.notes, "is_active": self.is_active,
        }


class FloorLayout(Timestamps, db.Model):
    __tablename__ = "floor_layouts"
    __table_args__ = (db.UniqueConstraint("property_id", "floor"),)

    layout_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    property_id = db.Column(db.String(36), db.ForeignKey("properties.property_id"), nullable=False, index=True)
    floor = db.Column(db.Integer, nullable=False)
    layout = db.Column(db.JSON, nullable=False, default=dict)

    def to_dict(self):
        return {"layout_id": self.layout_id, "property_id": self.property_id,
                "floor": self.floor, "layout": self.layout}


class Guest(Timestamps, db.Model):
    __tablename__ = "guests"

    guest_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), index=True)
    phone = db.Column(db.String(50))
    address = db.Column(db.String(500))
    id_number = db.Column(db.String(100))
    nationality = db.Column(db.String(100))
    date_of_birth = db.Column(db.Date)
    vip_status = db.Column(db.Boolean, nullable=False, default=False)
    preferences = db.Column(db.JSON, nullable=False, default=list)
    notes = db.Column(db.Text)

    def to_dict(self):
        return {
            "guest_id": self.guest_id, "first_name": self.first_name, "last_name": self.last_name,
            "email": self.email, "phone": self.phone, "address": self.address,
            "id_number": self.id_number, "nationality": self.nationality,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "vip_status": self.vip_status, "preferences": self.preferences, "notes": self.notes,
            "created_at": iso(self.created_at),
        }


class Reservation(Timestamps, db.Model):
    __tablename__ = "reservations"

    reservation_id = db.Column(db.String(36), primary_key=True, default=new_id)
    tenant_id = db.Column(db.String(36), db.ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    guest_id = db.Column(db.String(36), db.ForeignKey("guests.guest_id"), nullable=False, index=True)
    room_id = db.Column(db.String(36), db.ForeignKey("rooms.room_id"), nullable=False, index=True)
    check_in_date = db.Column(db.Date, nullable=False, index=True)
    check_out_date = db.Column(db.Date, nullable=False)
    adults = db.Column(db.Integer, nullable=False, default=1)
    children = db.Column(db.Integer, nullable=False, default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))
    paid_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0"))
    status = db.Column(db.String(20), nullable=False, default="confirmed")
    special_requests = db.Column(db.Text)

    guest = db.relationship("Guest", lazy="joined")
    room = db.relationship("Room", lazy="joined")

    @property
    def balance(self):
        return (self.total_amount or 0) - (self.paid_amount or 0)

    def to_dict(self):
        return {
            "reservation_id": self.reservation_id, "guest_id": self.guest_id, "room_id": self.room_id,
            "guest_name": f"{self.guest.first_name} {self.guest.last_name}" if self.guest else None,
            "room_number": self.room.number if self.room else None,
            "check_in_date": self.check_in_date.isoformat(),
            "check_out_date": self.check_out_date.isoformat(),
            "adults": self.adults, "children": self.children,
            "total_amount": money(self.total_amount), "paid_amount": money(self.paid_amount),
            "balance": money(self.balance), "status": self.status,
            "special_requests": self.special_requests,
            "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
        }
