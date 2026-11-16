# Homa_Backend

Flask backend for the HoMA / Sentinel hotel-management frontend (`home_web/`).
Runs on **port 9004** (the Vite dev proxy forwards `/api` there). API prefix: `/api/v1.0`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
flask --app run db upgrade      # create tables (SQLite file in instance/ by default)
flask --app run seed            # demo tenant + 8 demo users
python run.py                   # http://localhost:9004/api/v1.0/health
pytest
```

Production: `gunicorn run:app`. Set `DATABASE_URL` for Postgres (and install a driver, e.g. `psycopg`).

## Deploying: Render (API) + Supabase (Postgres)

`render.yaml` is a Render Blueprint. On start it runs `flask db upgrade`, `flask bootstrap-admin`,
then gunicorn, so schema changes ship with each deploy.

**1. Supabase**
1. Create a project (note the database password; keep it simple or URL-encode it: `@`→`%40`, `/`→`%2F`, `#`→`%23`).
2. *Project Settings → Database → Connection string → Session pooler* (port **5432**). Render is
   IPv4-only and Supabase's *direct* host is IPv6-only, so the direct string will not connect; use the pooler.
   It looks like `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   (The transaction pooler on 6543 also works; the app already disables prepared statements for it.)
3. No tables to create by hand: the first deploy's `db upgrade` builds them.

**2. Render**
1. Push this repo to GitHub, then *New → Blueprint* and select it.
2. Fill the prompted env vars: `DATABASE_URL` (the string above, as is; the app adds the driver and
   `sslmode=require`), `CORS_ORIGINS` (your frontend origin, e.g. `https://app.example.com`),
   `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` (12+ chars). `SECRET_KEY` and `JWT_SECRET_KEY`
   are generated for you.
3. After deploy, check `https://<service>.onrender.com/api/v1.0/health` returns `"database": "ok"`,
   then log in with the bootstrap admin via `POST /api/v1.0/auth/login`.

Notes
- With `APP_ENV=production` the app refuses to start on dev secrets, SQLite, or `AUTH_ENFORCED=false`.
- Do not run `flask seed` in production: it creates demo users with a shared password. Create real
  tenants and users through the API as the super admin.
- The bootstrap admin is created once and its password is never reset by later deploys; remove
  `BOOTSTRAP_ADMIN_PASSWORD` from Render afterwards if you like.
- Free Render instances sleep when idle, so the first request after a pause is slow. Free Supabase
  projects pause after a week of inactivity.
- The frontend currently calls relative `/api/...` URLs (Vite proxy in dev). Hosted separately, it needs
  to call `https://<service>.onrender.com/api/v1.0/...` (or sit behind a rewrite to it), and it must send
  `Authorization: Bearer <access_token>` since production enforces auth.

## Seeded accounts (local dev only)

All use the password in `SEED_PASSWORD` (default `ChangeMe123!`). Change it outside local dev.

| Role | Email |
|---|---|
| super_admin | platform@homa.io |
| tenant_admin | admin@grandhotel.com |
| front_office | frontdesk@grandhotel.com |
| reservations | reservations@grandhotel.com |
| housekeeping | housekeeping@grandhotel.com |
| fb_manager | restaurant@grandhotel.com |
| accounting | accounting@grandhotel.com |
| hr | hr@grandhotel.com |

## Step 5: property, guest and reservation endpoints

All tenant-scoped (tenant comes from the JWT; super_admin passes `?tenant_id=`). Lists return
`{"response": [...]}`. Dates are `YYYY-MM-DD`.

| Endpoint | Read | Write |
|---|---|---|
| `/properties`, `/room-types`, `/floor-layouts` (CRUD) | all tenant roles | tenant_admin |
| `/rooms` (CRUD; filters `property_id,type_id,status,floor`) | all tenant roles | tenant_admin; front_office/housekeeping may PATCH `status,notes,last_cleaned,last_maintenance,current_guest_id` |
| `/guests` (CRUD; `?q=` name/email/phone, `?vip_status=`) | tenant_admin, front_office, reservations | same |
| `/reservations` (list filters `status,guest_id,room_id,from,to`), `POST`, `GET/PATCH /<id>` | same as guests | same |
| `GET /reservations/availability?check_in=&check_out=[&property_id&type_id&guests]` | same | |
| `POST /reservations/<id>/cancel` | | booking roles |
| `POST /reservations/<id>/check-in`, `/check-out` | | tenant_admin, front_office |

Rules: a room cannot hold overlapping `confirmed`/`checked_in` stays (same-day turnover is fine);
occupancy is capped by the room type; `total_amount` defaults to nights x base price. Check-in needs
a room that is not occupied/cleaning/maintenance/out_of_order. Check-out is blocked while a balance is
owed unless `{"force": true}` (audited as a warning); it sets the room to `cleaning`. Rows still
referenced (property with rooms, guest with reservations, ...) return 409 on DELETE.
`paid_amount` is editable on a reservation by front office/admin until the billing step adds payments.
The checkout-clean housekeeping task is added in step 6.

## Implemented (build-order steps 1-4)

| Endpoint | Notes |
|---|---|
| `POST /auth/login` | returns `access_token`, `refresh_token`, `user` (includes `role`) |
| `POST /auth/refresh` | send the refresh token as Bearer |
| `POST /auth/logout` | revokes the presented token (+ `refresh_token` in body if given) |
| `GET /auth/me` | |
| `GET/POST /tenant`, `GET/PATCH /tenant/<id>` | super_admin only; `{"response": ...}` envelope |
| `GET /user/admins`, `POST /user` | super_admin only |
| `GET /health` | DB ping + version |

Every error, including 404/405/500, is JSON: `{"message": "..."}`.

## Decisions to know about

- **`AUTH_ENFORCED`**: the frontend does not send `Authorization` headers yet. Default is `true`
  (secure). For local dev against the current frontend set `AUTH_ENFORCED=false`; requests without a
  token then pass, but a token, if sent, is still verified and role-checked. Preferred fix: have the
  frontend add `Authorization: Bearer <access_token>` at its 4 fetch sites.
- **`POST /user`** accepts optional `role` and `tenant_id`. With neither, the user becomes a
  `super_admin` (matches the frontend, which treats a null tenant as super admin); with only
  `tenant_id`, `tenant_admin`. A random temporary password is generated and returned as
  `temporary_password` while `EXPOSE_TEMP_PASSWORD=true` (there is no mailer yet).
- Suspended users, and users of a non-`Active` tenant, cannot log in (403).
- Failed logins return the same 401 for unknown email and wrong password.
- JWT claims: `sub` = user_id, `role`, `tenant_id`, `email`. `current_tenant_id()` in
  `app/utils/auth.py` is the tenant-scoping helper for upcoming modules.
