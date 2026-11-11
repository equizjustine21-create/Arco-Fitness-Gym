# Arco Fitness

Arco Fitness is a Flask web app for managing a gym’s members, memberships,
attendance, walk-in visits, payments, and reports. It includes a public gym
homepage and a member portal for viewing membership and activity details.

## Features

- **Gym homepage** with membership pricing, facilities, opening information,
  and links to registration and walk-in check-in.
- **Staff dashboard** with member and gym activity summaries.
- **Member management** for contact details, account status, and membership
  records. Admins can remove a member's personal details and login while
  retaining payments, memberships, and attendance under an anonymized record;
  removed members' history is hidden from the member, payment, and attendance
  tables.
- **Attendance and walk-ins** with check-in/check-out tracking and visit
  history.
- **Memberships and payments** with configurable plans and payment records.
- **Online GCash payments** with an administrator-managed QR code, member
  reference-number submissions that stay Pending until an admin confirms or
  rejects them.
- **Reports** with Philippine-time ranges, confirmed-income reconciliation,
  payment-method and status summaries, daily attendance/income, filtered
  payment history, audited payment voids, CSV, PDF, and print exports.
- **Walk-in fee tracking** that includes a visit fee in income only after an
  administrator marks it paid; the report retains unpaid visits as Pending.
- **Member portal** for eligible members to view their membership, attendance,
  and payment history.
- **Settings** for gym information, pricing, display preferences, home-page
  content, profile, account password, and the GCash payment QR. Staff can also
  export records and download a database backup.
- **Welcome email** after member registration when SMTP is configured. See
  [SMTP_SETUP.md](SMTP_SETUP.md) and [EMAIL_SETUP.md](EMAIL_SETUP.md).
- **Email delivery and password reset** for welcome messages, confirmed GCash
  payments, and one-time member password-reset links.

## Requirements

- Python 3.9 or newer
- MySQL 8.0+ (or a compatible MariaDB server)
- A MySQL account with permission to create the configured database and tables

Python packages are listed in [requirements.txt](requirements.txt).

## Reports database upgrade

Back up `gymarco_db` in phpMyAdmin before upgrading. If you choose to run the
manual SQL, select `gymarco_db` in phpMyAdmin and execute
[reports_migration.sql](reports_migration.sql) once before starting the updated
app. Do not run that one-time file if `flask --app app init-db` or
`python app.py` has already upgraded the database; startup migration performs
the same schema changes automatically.

The migration does not create payment rows from assigned memberships. Existing
walk-in fees are left Pending because the old schema did not record whether the
fee was collected. Verify each old visit before an administrator marks it paid.

## Application structure

The Flask application is created by `create_app()` in `app.py`. That module
contains shared configuration, database and request helpers, and the
application factory. Route handlers are grouped into feature modules:

- `auth_routes.py` and `portal_routes.py` handle staff sign-in and the member
  portal.
- `dashboard_routes.py` handles the staff dashboard.
- `settings_routes.py` handles gym settings and staff profile actions.
- `member_routes.py`, `attendance_routes.py`, and `walkin_routes.py` handle
  members, attendance, and walk-in visits.
- `membership_routes.py` and `payment_routes.py` handle plans and payments.
- `report_routes.py` handles reports and exports.

The route modules use the blueprints declared in `blueprints.py`. Pass a
configuration mapping to `create_app()` when creating an app for tests or
another environment. Route functions remain re-exported from `app.py` for
existing scripts, and its module-level `app` remains available to WSGI servers.

Core business entities live in `domain_models.py`. `User`, `Member`,
`MembershipPlan`, and `Membership` encapsulate their persisted attributes and
domain behavior; route handlers use these objects for authentication, member
validation, and membership date/status calculations. `membership_service.py`
coordinates membership checkout rules with database access, while
`auth_security.py` applies database-backed throttling to authentication and
password-reset requests.

The app uses separate HTTP-only, SameSite=Lax session cookies for staff and
member accounts (`session` and `member_session`), each with a 12-hour lifetime.
This lets staff and a member stay signed in at the same time in separate tabs;
logging out of one side does not sign out the other. Existing member sessions
may need to sign in again once after this change.
Authentication attempts are limited to five per account-and-source-address
pair and twenty per source address per 15-minute window; blocked buckets
expire after 15 minutes. Expired throttle records are cleaned up as new
failures are recorded. These controls require the current schema, so run
`flask --app app init-db` after updating the application. Password-reset
requests also use the per-address throttle and retain the existing limit of
three reset emails per member per hour.

Run the app through the factory with Flask's CLI:

```powershell
.\venv\Scripts\flask.exe --app app:create_app run
```

## Tests

Run the database-independent application, authentication-security,
domain-model, membership-service, notification, report, and pagination tests
with:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests
```

These automated tests use fake database connections for repeatable route and
business-rule checks; they do not replace integration testing with MySQL. Before
releasing a change, verify the following on a staging database:

- Apply `flask --app app init-db`, then check member registration, membership
  checkout, Cash and GCash payment confirmation/rejection, and attendance.
- Check that failed staff/member logins are throttled, the password-reset
  response does not reveal whether an account exists, and a valid account can
  sign in after the throttle expires.
- Sign in to the staff and member sides in separate tabs, then confirm that
  logging out of either side leaves the other side signed in.
- Download a database backup, restore it into a separate test database, and
  verify member, payment, and attendance records.
- Exercise the key pages at desktop and mobile widths; navigate by keyboard
  only; verify focus visibility, skip links, labels, error announcements, and
  reduced-motion behavior.

## Run locally (Windows / PowerShell)

From the project directory:

```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). Stop the development
server with **Ctrl+C** in the terminal.

### PyQt6 Walk-in admin prompt

When Flask is running on the same Windows computer as the Walk-in browser,
pressing **Back** from staff-mode Walk-in opens a separate PyQt6 admin password
window. The password is sent through a local process pipe and verified by Flask
against the stored admin password hash. Cancel the PyQt6 window to stay on
Walk-in. This native window appears on the computer running Flask; it is not
available when the Flask server runs on a different or headless machine.

The app connects to MySQL using the environment variables below. Running
`app.py` creates the configured database, applies `arco_fitness.sql`, and adds
its supported settings, profile, payment, and index migrations. Importing
`app.py` does not connect to MySQL; when using Flask CLI or a WSGI server, run
the database initialization command once first:

```powershell
.\venv\Scripts\flask.exe --app app init-db
```

When the database has no users, set a private initial admin password of at
least 12 characters, including an uppercase letter and a number, before
initializing:

```powershell
$env:INITIAL_ADMIN_PASSWORD = "Choose-A-Private1-Password"
.\venv\Scripts\python.exe app.py
```

The initial account name is `admin`. There is no built-in default password.
After the account is created, remove `INITIAL_ADMIN_PASSWORD` from the shell
environment.

## MySQL configuration

Set the connection variables in the same PowerShell session that starts the
app. Set `DB_PASSWORD` to the password for your MySQL account; do not commit
database credentials to source control.

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "3306"
$env:DB_NAME = "gymarco_db"
$env:DB_USER = "root"
$env:DB_PASSWORD = "your-mysql-password"
.\venv\Scripts\python.exe app.py
```

The application uses MySQL only. It does not read or import the legacy
`arco.db` file. Existing SQLite data must be explicitly exported and imported
into MySQL before switching databases.

## Configuration

Configure options using environment variables before starting Flask:

| Variable | Default | Description |
| --- | --- | --- |
| `DB_HOST` | `127.0.0.1` | MySQL server host. |
| `DB_PORT` | `3306` | MySQL server port. |
| `DB_NAME` | `gymarco_db` | MySQL database name. Letters, numbers, and underscores only. |
| `DB_USER` | `root` | MySQL username. |
| `DB_PASSWORD` | empty | MySQL password. |
| `SECRET_KEY` | generated once in `instance/secret_key` | Flask session-signing key. Set a stable, private value outside local development. The local key file is excluded from Git so sessions remain valid after a restart. |
| `SESSION_COOKIE_SECURE` | enabled when `APPLICATION_BASE_URL` uses HTTPS | Set to `true` for HTTPS deployments, especially behind a TLS-terminating reverse proxy. Keep `false` only for local HTTP development. |
| `TRUSTED_PROXY_HOPS` | `0` | Set to the exact number of trusted reverse-proxy hops so Flask can use the forwarded client address and HTTPS scheme. Leave at `0` when the app is directly exposed. |
| `MAIL_SERVER` | `smtp.gmail.com` | SMTP server hostname. |
| `MAIL_PORT` | `587` | SMTP server port; Gmail uses STARTTLS. |
| `MAIL_USERNAME` | empty | Gmail sender and SMTP login name. |
| `MAIL_PASSWORD` | empty | Gmail App Password; never commit it. |
| `APPLICATION_BASE_URL` | `http://127.0.0.1:5000` | Public origin used to create reset links. |

Email settings may be provided as environment variables or in a project-root
`.env` file. The app loads `.env` automatically; keep real SMTP credentials
private and never commit that file.

For Gmail setup, password resets, test steps, and troubleshooting, see
[EMAIL_SETUP.md](EMAIL_SETUP.md).

## Main pages

| URL | Purpose |
| --- | --- |
| `/` | Public gym homepage |
| `/register` | Eligible member account registration |
| `/portal/login` | Member sign-in |
| `/portal/dashboard` | Member account dashboard |
| `/login` | Staff sign-in |
| `/dashboard` | Staff dashboard |
| `/members` | Member records |
| `/attendance` | Member attendance and check-outs |
| `/walk-in` | Walk-in check-in and check-out |
| `/memberships` | Membership plans |
| `/payments` | Payment records |
| `/reports` | Reports and export |
| `/settings` | Gym and staff settings |
| `/settings/home-page` | Public homepage content and images |

Member registration requires a matching active gym member record, but does not
require an existing membership or payment. After registering and signing in,
the member can choose a plan and submit payment through GCash in the member
portal. Staff must add and activate the member record before the account can be
created.

## Development and deployment

Running `app.py` starts Flask with debug mode disabled. To enable it temporarily
for local troubleshooting, set `$env:FLASK_DEBUG = "1"` in PowerShell before
starting the app; never enable debug mode in production. For deployment, use a
production WSGI server, set a long-lived random `SECRET_KEY`, set
`SESSION_COOKIE_SECURE=true` when serving HTTPS (including behind a TLS
terminator), and keep database and SMTP credentials private. If using a
reverse proxy, set `TRUSTED_PROXY_HOPS` to the exact number of proxies you
control; never trust forwarded headers from arbitrary clients. Configure the
proxy to enforce HTTPS and HSTS. Back up the database regularly and
periodically test restoring a backup; the staff settings page provides a
downloadable backup.
