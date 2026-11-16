"""Shared helpers for tenant-scoped CRUD modules: field parsing and ownership lookups."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import request

from ..extensions import db
from . import ApiError, clean_email


# --- field validators: take a raw JSON value, return the cleaned value or raise ValueError ---
def v_str(max_len=255):
    def check(v):
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip()
        if len(v) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        return v
    return check


def v_int(min_value=None, max_value=None):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be an integer")
        if min_value is not None and v < min_value:
            raise ValueError(f"must be >= {min_value}")
        if max_value is not None and v > max_value:
            raise ValueError(f"must be <= {max_value}")
        return v
    return check


def v_money(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError("must be a number")
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("must be a number")
    if d < 0 or d >= Decimal("1e10"):
        raise ValueError("must be between 0 and 9999999999.99")
    return d


def v_enum(*values):
    def check(v):
        if v not in values:
            raise ValueError("must be one of: " + ", ".join(values))
        return v
    return check


def v_bool(v):
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def v_str_list(v):
    if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
        raise ValueError("must be a list of strings")
    return [i.strip() for i in v]


def v_object(v):
    if not isinstance(v, dict):
        raise ValueError("must be an object")
    return v


def v_date(v):
    if isinstance(v, str):
        try:
            # Accept YYYY-MM-DD or a full ISO datetime (the date part is used).
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            pass
    raise ValueError("must be a date (YYYY-MM-DD)")


def v_datetime(v):
    """ISO 8601 -> naive UTC datetime (the storage convention)."""
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            return dt if dt.tzinfo is None else dt.astimezone(timezone.utc).replace(tzinfo=None)
    raise ValueError("must be an ISO 8601 datetime")


def v_email(v):
    cleaned = clean_email(v)
    if not cleaned:
        raise ValueError("must be a valid email address")
    return cleaned


def parse(data, spec, partial=False):
    """Validate `data` against `spec` = {field: (validator, required)}.

    Unknown keys are ignored (so client-sent tenant_id etc. can never reach a model).
    Returns {field: cleaned_value} for the fields present. A null/empty value clears an
    optional field and is rejected for a required one.
    """
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    out, missing = {}, []
    for field, (validator, required) in spec.items():
        if field not in data:
            if required and not partial:
                missing.append(field)
            continue
        raw = data[field]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if required:
                missing.append(field)
            else:
                out[field] = None
            continue
        try:
            out[field] = validator(raw)
        except ValueError as e:
            raise ApiError(f"{field}: {e}")
    if missing:
        raise ApiError("Missing required field(s): " + ", ".join(missing))
    return out


def apply(obj, values):
    for k, v in values.items():
        setattr(obj, k, v)


# --- tenant-scoped lookups ---
def get_owned(model, pk, tenant_id, label, status=404):
    """Fetch a row by primary key, only if it belongs to `tenant_id`."""
    obj = db.session.get(model, pk) if isinstance(pk, str) else None
    if obj is None or obj.tenant_id != tenant_id:
        raise ApiError(f"{label} not found", status)
    return obj


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    return data
