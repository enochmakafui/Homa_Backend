from flask import has_request_context, request

from ..extensions import db
from ..models import AuditLog


def audit(action, resource=None, details=None, severity="info", user=None, tenant=None, commit=True):
    entry = AuditLog(
        action=action, resource=resource, details=details, severity=severity,
        user=user, tenant=tenant,
        ip_address=request.remote_addr if has_request_context() else None,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry
