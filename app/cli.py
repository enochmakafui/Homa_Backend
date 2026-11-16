import os

import click

from .extensions import db
from .models import Tenant, User, UserRole

DEMO_TENANT = {
    "tenant_name": "Grand Palace Hotel",
    "email": "contact@grandhotel.com",
    "phone": "+1-555-0100",
    "address": "1 Palace Avenue, Metropolis",
    "plan": "professional",
}

# (email, first, last, role, belongs_to_tenant)
SEED_USERS = [
    ("platform@homa.io", "System", "Administrator", "super_admin", False),
    ("admin@grandhotel.com", "Victoria", "Sterling", "tenant_admin", True),
    ("frontdesk@grandhotel.com", "James", "Mitchell", "front_office", True),
    ("reservations@grandhotel.com", "Sarah", "Johnson", "reservations", True),
    ("housekeeping@grandhotel.com", "Maria", "Rodriguez", "housekeeping", True),
    ("restaurant@grandhotel.com", "Chef Antoine", "Dubois", "fb_manager", True),
    ("accounting@grandhotel.com", "Robert", "Chen", "accounting", True),
    ("hr@grandhotel.com", "Lisa", "Thompson", "hr", True),
]


def seed_demo_data(password: str):
    """Idempotent: creates only what is missing. Returns the number of users created."""
    tenant = Tenant.query.filter_by(tenant_name=DEMO_TENANT["tenant_name"]).first()
    if not tenant:
        tenant = Tenant(**DEMO_TENANT)
        db.session.add(tenant)
        db.session.flush()

    created = 0
    for email, first, last, role, in_tenant in SEED_USERS:
        if User.query.filter_by(email=email).first():
            continue
        user = User(email=email, first_name=first, last_name=last, phone="+1-555-0000",
                    tenant_id=tenant.tenant_id if in_tenant else None)
        user.set_password(password)
        user.user_roles.append(UserRole(role=role))
        db.session.add(user)
        created += 1
    db.session.commit()
    return created


def bootstrap_admin(email, password, first_name="Platform", last_name="Admin"):
    """Create the first super admin. Returns 'created' or 'exists'; never resets a password."""
    from .utils import clean_email

    cleaned = clean_email(email)
    if not cleaned:
        raise click.ClickException("BOOTSTRAP_ADMIN_EMAIL is not a valid email address")
    if len(password) < 12:
        raise click.ClickException("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
    if User.query.filter_by(email=cleaned).first():
        return "exists"
    user = User(email=cleaned, first_name=first_name, last_name=last_name, phone="")
    user.set_password(password)
    user.user_roles.append(UserRole(role="super_admin"))
    db.session.add(user)
    db.session.commit()
    return "created"


def register_cli(app):
    @app.cli.command("bootstrap-admin")
    def bootstrap_admin_cmd():
        """Create the first super admin from BOOTSTRAP_ADMIN_* env vars (idempotent, for deploys)."""
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL")
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
        if not email or not password:
            click.echo("BOOTSTRAP_ADMIN_EMAIL/PASSWORD not set; skipping.")
            return
        result = bootstrap_admin(email, password, os.getenv("BOOTSTRAP_ADMIN_FIRST_NAME", "Platform"),
                                 os.getenv("BOOTSTRAP_ADMIN_LAST_NAME", "Admin"))
        click.echo(f"Super admin {result}.")

    @app.cli.command("seed")
    @click.option("--password", default=lambda: os.getenv("SEED_PASSWORD", "ChangeMe123!"),
                  show_default=False, help="Password for all seeded users (env SEED_PASSWORD).")
    def seed(password):
        """Create the demo tenant and the 8 demo users (idempotent)."""
        n = seed_demo_data(password)
        click.echo(f"Seeded {n} new user(s). Demo tenant: {DEMO_TENANT['tenant_name']}.")
