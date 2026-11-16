from email_validator import EmailNotValidError, validate_email
from flask import jsonify


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def ok(payload, status: int = 200):
    """Wrap in the `{"response": ...}` envelope used across the API."""
    return jsonify({"response": payload}), status


def clean_email(value):
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except (EmailNotValidError, TypeError, AttributeError):
        return None


def require_fields(data, fields):
    """Return stripped values for `fields`, or raise a 400 naming what is missing."""
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    out, missing = {}, []
    for f in fields:
        v = data.get(f)
        v = v.strip() if isinstance(v, str) else v
        if not v:
            missing.append(f)
        out[f] = v
    if missing:
        raise ApiError("Missing required field(s): " + ", ".join(missing))
    return out
