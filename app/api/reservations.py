"""Reservations: booking with overlap protection, plus cancel / check-in / check-out."""
from decimal import Decimal

from flask import Blueprint, request

from ..extensions import db
from ..models import BLOCKING_STATUSES, Guest, Reservation, Room
from ..services.housekeeping import queue_checkout_clean
from ..utils import ApiError, ok
from ..utils.audit import audit
from ..utils.auth import current_claims, resolve_tenant_id, roles_required
from ..utils.resource import (
    apply, body, get_owned, parse, v_date, v_int, v_money, v_str,
)

bp = Blueprint("reservations", __name__, url_prefix="/reservations")

BOOKING_ROLES = ("super_admin", "tenant_admin", "front_office", "reservations")
DESK_ROLES = ("super_admin", "tenant_admin", "front_office")
# Rooms in these states cannot receive a guest right now.
NOT_READY_FOR_CHECKIN = ("occupied", "cleaning", "maintenance", "out_of_order")

SPEC = {
    "guest_id": (v_str(36), True), "room_id": (v_str(36), True),
    "check_in_date": (v_date, True), "check_out_date": (v_date, True),
    "adults": (v_int(1, 50), False), "children": (v_int(0, 50), False),
    "total_amount": (v_money, False), "paid_amount": (v_money, False),
    "special_requests": (v_str(5000), False),
}
# Changing any of these re-checks availability and is only allowed before check-in.
STAY_FIELDS = {"room_id", "check_in_date", "check_out_date", "adults", "children"}


def _actor():
    return current_claims().get("email")


def _nights(cin, cout):
    return (cout - cin).days


def _overlaps(tenant_id, cin, cout, exclude_id=None):
    """Filter expression: blocking reservations whose dates intersect [cin, cout)."""
    q = Reservation.query.filter(
        Reservation.tenant_id == tenant_id,
        Reservation.status.in_(BLOCKING_STATUSES),
        Reservation.check_in_date < cout,
        Reservation.check_out_date > cin,
    )
    if exclude_id:
        q = q.filter(Reservation.reservation_id != exclude_id)
    return q


def _validate_stay(tenant_id, room_id, cin, cout, adults, children, exclude_id=None):
    if cout <= cin:
        raise ApiError("check_out_date must be after check_in_date")
    # Lock the room row so two concurrent bookings cannot both pass the overlap check (Postgres).
    room = (db.session.query(Room).filter_by(room_id=room_id, tenant_id=tenant_id)
            .with_for_update(of=Room).first())
    if not room:
        raise ApiError("Room not found", 400)
    if not room.is_active or room.status == "out_of_order":
        raise ApiError("Room is not available for booking", 409)
    cap = room.room_type.max_occupancy
    if adults + children > cap:
        raise ApiError(f"Room sleeps at most {cap} guest(s)")
    clash = _overlaps(tenant_id, cin, cout, exclude_id).filter(Reservation.room_id == room_id).first()
    if clash:
        raise ApiError(
            f"Room {room.number} is already booked {clash.check_in_date} to {clash.check_out_date}", 409)
    return room


def _quote(room, cin, cout):
    return (room.room_type.base_price * _nights(cin, cout)).quantize(Decimal("0.01"))


@bp.get("")
@roles_required(*BOOKING_ROLES)
def list_reservations():
    q = Reservation.query.filter_by(tenant_id=resolve_tenant_id())
    for param in ("status", "guest_id", "room_id"):
        if request.args.get(param):
            q = q.filter(getattr(Reservation, param) == request.args[param])
    # `from`/`to` select reservations whose stay intersects that window.
    for param, clause in (("from", lambda d: Reservation.check_out_date >= d),
                          ("to", lambda d: Reservation.check_in_date <= d)):
        if request.args.get(param):
            try:
                q = q.filter(clause(v_date(request.args[param])))
            except ValueError:
                raise ApiError(f"{param} must be a date (YYYY-MM-DD)")
    return ok([r.to_dict() for r in q.order_by(Reservation.check_in_date.desc()).all()])


@bp.get("/availability")
@roles_required(*BOOKING_ROLES)
def availability():
    """Rooms free for the whole of [check_in, check_out)."""
    tid = resolve_tenant_id()
    try:
        cin, cout = v_date(request.args.get("check_in")), v_date(request.args.get("check_out"))
    except ValueError:
        raise ApiError("check_in and check_out are required (YYYY-MM-DD)")
    if cout <= cin:
        raise ApiError("check_out must be after check_in")
    booked = _overlaps(tid, cin, cout).with_entities(Reservation.room_id)
    q = Room.query.filter(Room.tenant_id == tid, Room.is_active.is_(True),
                          Room.status != "out_of_order", Room.room_id.not_in(booked))
    for param in ("property_id", "type_id"):
        if request.args.get(param):
            q = q.filter(getattr(Room, param) == request.args[param])
    out = []
    for room in q.order_by(Room.floor, Room.number).all():
        t = room.room_type
        if request.args.get("guests", "").isdigit() and int(request.args["guests"]) > t.max_occupancy:
            continue
        out.append({**room.to_dict(), "base_price": float(t.base_price), "max_occupancy": t.max_occupancy,
                    "quote": float(_quote(room, cin, cout))})
    return ok(out)


@bp.post("")
@roles_required(*BOOKING_ROLES)
def create_reservation():
    tid = resolve_tenant_id()
    v = parse(body(), SPEC)
    get_owned(Guest, v["guest_id"], tid, "Guest", status=400)
    v.setdefault("adults", 1)
    v.setdefault("children", 0)
    room = _validate_stay(tid, v["room_id"], v["check_in_date"], v["check_out_date"], v["adults"], v["children"])
    if v.get("total_amount") is None:
        v["total_amount"] = _quote(room, v["check_in_date"], v["check_out_date"])
    if v.get("paid_amount") is not None and v["paid_amount"] > v["total_amount"]:
        raise ApiError("paid_amount cannot exceed total_amount")
    res = Reservation(tenant_id=tid, status="confirmed", **v)
    db.session.add(res)
    audit("reservation_created", "reservation", f"Room {room.number} {v['check_in_date']} to {v['check_out_date']}",
          user=_actor(), commit=False)
    db.session.commit()
    return ok(res.to_dict(), 201)


def _get(reservation_id):
    return get_owned(Reservation, reservation_id, resolve_tenant_id(), "Reservation")


@bp.get("/<reservation_id>")
@roles_required(*BOOKING_ROLES)
def get_reservation(reservation_id):
    return ok(_get(reservation_id).to_dict())


@bp.patch("/<reservation_id>")
@roles_required(*BOOKING_ROLES)
def update_reservation(reservation_id):
    res = _get(reservation_id)
    # guest_id is immutable: the guest folio hangs off it.
    v = parse(body(), {k: s for k, s in SPEC.items() if k != "guest_id"}, partial=True)
    if not v:
        raise ApiError("No updatable fields supplied")
    # Interim: payments become their own resource in the billing step. Until then the
    # desk may record the paid amount here.
    if "paid_amount" in v and current_claims().get("role") not in (None, *DESK_ROLES):
        raise ApiError("Only front office or an administrator can record payments", 403)
    if STAY_FIELDS & v.keys():
        if res.status != "confirmed":
            raise ApiError("Stay details can only be changed before check-in", 409)
        merged = {f: v.get(f, getattr(res, f)) for f in STAY_FIELDS}
        room = _validate_stay(res.tenant_id, merged["room_id"], merged["check_in_date"],
                              merged["check_out_date"], merged["adults"], merged["children"],
                              exclude_id=res.reservation_id)
        if "total_amount" not in v and {"room_id", "check_in_date", "check_out_date"} & v.keys():
            v["total_amount"] = _quote(room, merged["check_in_date"], merged["check_out_date"])
    total = v.get("total_amount", res.total_amount)
    if v.get("paid_amount", res.paid_amount) > total:
        raise ApiError("paid_amount cannot exceed total_amount")
    apply(res, v)
    audit("reservation_updated", "reservation", ", ".join(sorted(v)), user=_actor(), commit=False)
    db.session.commit()
    return ok(res.to_dict())


@bp.post("/<reservation_id>/cancel")
@roles_required(*BOOKING_ROLES)
def cancel(reservation_id):
    res = _get(reservation_id)
    if res.status != "confirmed":
        raise ApiError(f"A {res.status.replace('_', ' ')} reservation cannot be cancelled", 409)
    reason = (request.get_json(silent=True) or {}).get("reason")
    res.status = "cancelled"
    audit("reservation_cancelled", "reservation", reason if isinstance(reason, str) else None,
          user=_actor(), severity="warning", commit=False)
    db.session.commit()
    return ok(res.to_dict())


@bp.post("/<reservation_id>/check-in")
@roles_required(*DESK_ROLES)
def check_in(reservation_id):
    res = _get(reservation_id)
    if res.status != "confirmed":
        raise ApiError(f"Only confirmed reservations can be checked in (this one is {res.status})", 409)
    room = res.room
    if not room.is_active or room.status in NOT_READY_FOR_CHECKIN:
        raise ApiError(f"Room {room.number} is not ready ({room.status}); it cannot be checked into", 409)
    res.status = "checked_in"
    room.status = "occupied"
    room.current_guest_id = res.guest_id
    audit("check_in", "reservation", f"Room {room.number}", user=_actor(), commit=False)
    db.session.commit()
    return ok(res.to_dict())


@bp.post("/<reservation_id>/check-out")
@roles_required(*DESK_ROLES)
def check_out(reservation_id):
    res = _get(reservation_id)
    if res.status != "checked_in":
        raise ApiError(f"Only checked-in reservations can be checked out (this one is {res.status})", 409)
    force = (request.get_json(silent=True) or {}).get("force") is True
    if res.balance > 0 and not force:
        raise ApiError(f"Outstanding balance of {res.balance:.2f}. Settle it, or send force=true to override", 409)
    room = res.room
    res.status = "checked_out"
    room.status = "cleaning"
    room.current_guest_id = None
    queue_checkout_clean(room, created_by=current_claims().get("sub"))
    audit("check_out", "reservation", f"Room {room.number}" + (f"; forced with balance {res.balance:.2f}" if res.balance > 0 else ""),
          user=_actor(), severity="warning" if res.balance > 0 else "info", commit=False)
    db.session.commit()
    return ok(res.to_dict())
