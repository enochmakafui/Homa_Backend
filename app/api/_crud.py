"""Tenant-scoped CRUD route factory shared by the hotel modules."""
from flask import request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..utils import ApiError, ok
from ..utils.audit import audit
from ..utils.auth import current_claims, resolve_tenant_id, roles_required
from ..utils.resource import apply, body, get_owned, parse


def _no_nulls(model, values):
    """A null for an optional spec field must not hit a NOT NULL column."""
    for field, value in values.items():
        if value is None and not model.__table__.columns[field].nullable:
            raise ApiError(f"{field} cannot be empty")


def register_crud(bp, path, model, *, pk, label, spec, read_roles, write_roles,
                  filters=None, order=None, before_save=None, in_use=None, patch_guard=None,
                  patch_roles=None, search=None, after_update=None, allow_delete=True):
    """Add GET list / POST / GET one / PATCH / DELETE routes for `model` under `path`.

    filters:      {query_param: caster} equality filters for the list route.
    before_save:  fn(tenant_id, values, obj_or_None) -> may validate references or raise.
    in_use:       fn(obj) -> message when the row is referenced and must not be deleted.
    patch_guard:  fn(values) -> may raise 403 for field-level permission rules.
    patch_roles:  roles allowed to PATCH (default: write_roles); pair with patch_guard.
    after_update: fn(obj, before, values) run after a PATCH is applied, in the same transaction;
                  `before` holds the old value of each changed field.
    search:       column expressions matched (case-insensitive contains) by `?q=` on the list route.
    """
    name = model.__tablename__

    def scoped(tenant_id):
        return model.query.filter_by(tenant_id=tenant_id)

    def list_view():
        tid = resolve_tenant_id()
        q = scoped(tid)
        for param, cast in (filters or {}).items():
            raw = request.args.get(param)
            if raw not in (None, ""):
                try:
                    q = q.filter(getattr(model, param) == cast(raw))
                except (TypeError, ValueError):
                    raise ApiError(f"Invalid value for {param}")
        text = (request.args.get("q") or "").strip()
        if text and search:
            q = q.filter(or_(*(expr.icontains(text, autoescape=True) for expr in search)))
        if order is not None:
            q = q.order_by(*order)
        return ok([o.to_dict() for o in q.all()])

    def create_view():
        tid = resolve_tenant_id()
        values = parse(body(), spec)
        _no_nulls(model, values)
        if before_save:
            before_save(tid, values, None)
        obj = model(tenant_id=tid, **values)
        db.session.add(obj)
        try:
            audit(f"{label.lower()}_created", name, f"Created {label}", commit=False,
                  user=current_claims().get("email"))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ApiError(f"This {label.lower()} conflicts with an existing record", 409)
        return ok(obj.to_dict(), 201)

    def get_view(item_id):
        return ok(get_owned(model, item_id, resolve_tenant_id(), label).to_dict())

    def patch_view(item_id):
        tid = resolve_tenant_id()
        obj = get_owned(model, item_id, tid, label)
        values = parse(body(), spec, partial=True)
        if not values:
            raise ApiError("No updatable fields supplied")
        _no_nulls(model, values)
        if patch_guard:
            patch_guard(values)
        if before_save:
            before_save(tid, values, obj)
        before = {k: getattr(obj, k) for k in values}
        apply(obj, values)
        if after_update:
            after_update(obj, before, values)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ApiError(f"This change conflicts with an existing {label.lower()}", 409)
        return ok(obj.to_dict())

    def delete_view(item_id):
        obj = get_owned(model, item_id, resolve_tenant_id(), label)
        reason = in_use(obj) if in_use else None
        if reason:
            raise ApiError(reason, 409)
        db.session.delete(obj)
        audit(f"{label.lower()}_deleted", name, f"Deleted {label}", commit=False,
              user=current_claims().get("email"))
        db.session.commit()
        return ok({"deleted": item_id})

    def add(suffix, fn, methods, roles):
        endpoint = f"{name}_{fn.__name__}"
        bp.add_url_rule(path + suffix, endpoint, roles_required(*roles)(fn), methods=methods)

    add("", list_view, ["GET"], read_roles)
    add("", create_view, ["POST"], write_roles)
    add("/<item_id>", get_view, ["GET"], read_roles)
    add("/<item_id>", patch_view, ["PATCH"], patch_roles or write_roles)
    if allow_delete:
        add("/<item_id>", delete_view, ["DELETE"], write_roles)
