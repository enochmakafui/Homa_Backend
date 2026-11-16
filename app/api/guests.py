from flask import Blueprint

from ..models import Guest, Reservation
from ..utils.resource import v_bool, v_date, v_email, v_str, v_str_list
from ._crud import register_crud

bp = Blueprint("guests", __name__)

GUEST_ROLES = ("super_admin", "tenant_admin", "front_office", "reservations")

register_crud(
    bp, "/guests", Guest, pk="guest_id", label="Guest",
    spec={
        "first_name": (v_str(100), True), "last_name": (v_str(100), True),
        "email": (v_email, False), "phone": (v_str(50), False), "address": (v_str(500), False),
        "id_number": (v_str(100), False), "nationality": (v_str(100), False),
        "date_of_birth": (v_date, False), "vip_status": (v_bool, False),
        "preferences": (v_str_list, False), "notes": (v_str(5000), False),
    },
    read_roles=GUEST_ROLES, write_roles=GUEST_ROLES,
    order=[Guest.last_name, Guest.first_name],
    filters={"vip_status": lambda s: s.lower() in ("1", "true")},
    # `?q=` matches name, email or phone.
    search=[Guest.first_name + " " + Guest.last_name, Guest.email, Guest.phone],
    in_use=lambda g: "Guest has reservations" if Reservation.query.filter_by(guest_id=g.guest_id).first() else None,
)
