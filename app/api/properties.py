"""Properties, room types, rooms and floor layouts. Reads: every tenant role. Writes: tenant_admin."""
from flask import Blueprint

from ..models import (
    PROPERTY_TYPES, ROOM_CATEGORIES, ROOM_STATUSES, FloorLayout, Guest, Property, Reservation,
    Room, RoomType,
)
from ..utils import ApiError
from ..utils.auth import current_claims
from ..utils.resource import (
    get_owned, v_bool, v_datetime, v_enum, v_int, v_money, v_object, v_str, v_str_list,
)
from ._crud import register_crud

bp = Blueprint("properties", __name__)

ALL_TENANT = ("super_admin", "tenant_admin", "front_office", "reservations",
              "housekeeping", "fb_manager", "accounting", "hr")
ADMIN = ("super_admin", "tenant_admin")


# --- properties -----------------------------------------------------------
register_crud(
    bp, "/properties", Property, pk="property_id", label="Property",
    spec={
        "name": (v_str(200), True), "address": (v_str(500), True),
        "property_type": (v_enum(*PROPERTY_TYPES), False), "floors": (v_int(1, 200), False),
        "total_rooms": (v_int(0), False), "description": (v_str(5000), False),
        "amenities": (v_str_list, False), "images": (v_str_list, False), "is_active": (v_bool, False),
    },
    read_roles=ALL_TENANT, write_roles=ADMIN, order=[Property.name],
    filters={"is_active": lambda s: s.lower() in ("1", "true")},
    in_use=lambda p: "Property still has rooms; remove or move them first"
    if Room.query.filter_by(property_id=p.property_id).first() else None,
)

# --- room types -----------------------------------------------------------
register_crud(
    bp, "/room-types", RoomType, pk="type_id", label="Room type",
    spec={
        "name": (v_str(200), True), "type": (v_enum(*ROOM_CATEGORIES), False),
        "base_price": (v_money, True), "max_occupancy": (v_int(1, 50), False),
        "bed_count": (v_int(1, 20), False), "bed_type": (v_str(50), False),
        "size": (v_int(0), False), "amenities": (v_str_list, False),
        "description": (v_str(5000), False), "images": (v_str_list, False),
    },
    read_roles=ALL_TENANT, write_roles=ADMIN, order=[RoomType.name],
    filters={"type": str},
    in_use=lambda t: "Room type is assigned to rooms" if Room.query.filter_by(type_id=t.type_id).first() else None,
)


# --- rooms ----------------------------------------------------------------
# Front office and housekeeping may update operational fields; structural ones need an admin.
OPERATIONAL_ROOM_FIELDS = {"status", "notes", "last_cleaned", "last_maintenance", "current_guest_id"}


def _room_refs(tenant_id, values, obj):
    """Every referenced id must belong to the caller's tenant (400 otherwise)."""
    for field, model, label in (("property_id", Property, "Property"),
                                ("type_id", RoomType, "Room type"),
                                ("current_guest_id", Guest, "Guest")):
        if values.get(field):
            get_owned(model, values[field], tenant_id, label, status=400)


def _room_patch_guard(values):
    role = current_claims().get("role")
    if role in (None, "super_admin", "tenant_admin"):
        return
    if set(values) - OPERATIONAL_ROOM_FIELDS:
        raise ApiError("Only an administrator can change a room's structure", 403)


register_crud(
    bp, "/rooms", Room, pk="room_id", label="Room",
    spec={
        "property_id": (v_str(36), True), "type_id": (v_str(36), True), "number": (v_str(20), True),
        "floor": (v_int(0, 200), False), "status": (v_enum(*ROOM_STATUSES), False),
        "current_guest_id": (v_str(36), False), "last_cleaned": (v_datetime, False),
        "last_maintenance": (v_datetime, False), "notes": (v_str(5000), False),
        "is_active": (v_bool, False),
    },
    read_roles=ALL_TENANT, write_roles=ADMIN,
    patch_roles=("super_admin", "tenant_admin", "front_office", "housekeeping"),
    patch_guard=_room_patch_guard, before_save=_room_refs,
    order=[Room.property_id, Room.floor, Room.number],
    filters={"property_id": str, "type_id": str, "status": str, "floor": int},
    in_use=lambda r: "Room has reservations; deactivate it instead"
    if Reservation.query.filter_by(room_id=r.room_id).first() else None,
)


# --- floor layouts --------------------------------------------------------
register_crud(
    bp, "/floor-layouts", FloorLayout, pk="layout_id", label="Floor layout",
    spec={"property_id": (v_str(36), True), "floor": (v_int(0, 200), True), "layout": (v_object, True)},
    read_roles=ALL_TENANT, write_roles=ADMIN, order=[FloorLayout.property_id, FloorLayout.floor],
    filters={"property_id": str, "floor": int},
    before_save=lambda tid, values, obj: _room_refs(tid, {"property_id": values.get("property_id")}, obj),
)
