import os
import importlib
import csv
import smtplib
import ssl
import math
import re
import secrets
import subprocess
import sys
import hashlib
import threading
from io import BytesIO, StringIO
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from xml.sax.saxutils import escape
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv(os.path.join(os.path.abspath(os.path.dirname(__file__)), ".env"))

import pymysql
from jinja2 import TemplateError
from pymysql.cursors import DictCursor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from flask import (Flask, Response, abort, current_app, flash, g, jsonify, redirect,
                   has_request_context, render_template, request, send_file, session,
                   url_for)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from blueprints import (
    attendance_bp,
    auth_bp,
    dashboard_bp,
    members_bp,
    memberships_bp,
    payments_bp,
    portal_bp,
    reports_bp,
    settings_bp,
    walkin_bp,
)
from auth_security import AuthAttemptLimiter
from domain_models import Membership, MembershipPlan
from membership_service import MembershipService
from report_service import build_report_data

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_NAME = os.environ.get("DB_NAME", "gymarco_db")
DATABASE_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DATABASE_PORT = int(os.environ.get("DB_PORT", "3306"))
DATABASE_USER = os.environ.get("DB_USER", "root")
DATABASE_PASSWORD = os.environ.get("DB_PASSWORD", "")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
HOME_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads", "home")
HOME_IMAGE_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_SETTINGS = {
    "gym_name": "ARCO FITNESS",
    "contact_number": "",
    "gym_email": "",
    "gym_address": "",
    "opening_time": "06:00",
    "closing_time": "22:00",
    "operating_days": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
    "walk_in_fee": "50",
    "monthly_fee": "750",
    "time_format": "12",
    "timezone": "Asia/Manila",
    "desktop_sidebar": "expanded",
    "mobile_sidebar": "closed",
    "membership_reminders": "1",
    "checkout_warnings": "1",
    "pending_payment_reminders": "1",
    "gcash_payment_reminders": "1",
    "expired_membership_reminders": "1",
    "new_registration_reminders": "1",
    "inactive_member_reminders": "1",
    "inactive_member_days": "30",
    "last_backup": "",
    "gcash_qr_filename": "",
    "home_hero_line1": "Build Your",
    "home_hero_line2": "Strongest Self.",
    "home_hero_description": "Track your membership, check-ins and payments in one place. Join the gym, train, and watch your progress.",
    "home_hero_note": "Your next chapter starts with one session.",
    "home_opening_hours": "Open daily",
    "home_active_members": "Growing every day",
    "home_facility_cardio_title": "Cardio zone",
    "home_facility_weights_title": "Free weights",
    "home_facility_machines_title": "Machines and strength",
    "home_facebook_url": "",
    "home_hero_image": "",
    "home_facility_cardio_image": "",
    "home_facility_weights_image": "",
    "home_facility_machines_image": "",
}


def _new_password_error(password, confirmation=None, current_password_hash=None):
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[0-9]", password):
        return "Password must include at least one number."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if confirmation is not None and password != confirmation:
        return "Passwords do not match."
    if (current_password_hash is not None
            and check_password_hash(current_password_hash, password)):
        return "Choose a new password that differs from your current password."
    return None


def setting_values(db=None):
    if db is None and hasattr(g, "_settings_values"):
        return g._settings_values
    database = db or get_db()
    values = DEFAULT_SETTINGS.copy()
    values.update({
        row["key"]: row["value"]
        for row in database.execute("SELECT `key`, `value` FROM app_settings")
    })
    if db is None:
        g._settings_values = values
    return values

def _home_image_url(filename):
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|webp)", filename or ""):
        return None
    if not os.path.isfile(os.path.join(HOME_UPLOAD_DIR, filename)):
        return None
    return url_for("static", filename=f"uploads/home/{filename}")

def _gcash_qr_url(filename):
    if not re.fullmatch(r"gcash-qr-[a-f0-9]{32}\.(?:jpg|png)", filename or ""):
        return None
    if not os.path.isfile(os.path.join(GCASH_QR_UPLOAD_DIR, filename)):
        return None
    return url_for("static", filename=f"uploads/{filename}")

def _image_extension(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None

def gym_timezone():
    return ZoneInfo(setting_values()["timezone"])

def gym_now():
    return datetime.now(timezone.utc).astimezone(gym_timezone())

def philippine_now():
    return gym_now().replace(tzinfo=None)

def philippine_date():
    return philippine_now().date()

def utc_timestamp(value=None):
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")

def _record_email_log(application, to_email, subject, status, error_message):
    with application.app_context():
        db = _connect_mysql()
        try:
            db.execute(
                """INSERT INTO email_log
                   (to_email, subject, status, error_message, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    to_email,
                    subject,
                    status,
                    (error_message or "")[:1000],
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
            db.commit()
        finally:
            db.close()

def _deliver_email(application, to_email, subject, text_body, html_body):
    with application.app_context():
        status = "failed"
        error_message = ""
        try:
            mail_server = current_app.config["MAIL_SERVER"]
            username = current_app.config["MAIL_USERNAME"]
            password = current_app.config["MAIL_PASSWORD"]
            if not mail_server or not username or not password:
                raise RuntimeError(
                    "Email skipped: configure MAIL_SERVER, MAIL_USERNAME, and "
                    "MAIL_PASSWORD in the project .env file."
                )

            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = current_app.config["MAIL_FROM"]
            message["To"] = to_email
            message.set_content(text_body)
            if html_body:
                message.add_alternative(
                    current_app.jinja_env.get_template("emails/base.html").render(
                        content_html=html_body
                    ),
                    subtype="html",
                )

            mail_port = current_app.config["MAIL_PORT"]
            tls_context = ssl.create_default_context()
            if current_app.config["MAIL_USE_SSL"]:
                connection = smtplib.SMTP_SSL(
                    mail_server, mail_port, timeout=20, context=tls_context
                )
            else:
                connection = smtplib.SMTP(mail_server, mail_port, timeout=20)

            with connection as server:
                if not current_app.config["MAIL_USE_SSL"]:
                    server.ehlo()
                    server.starttls(context=tls_context)
                    server.ehlo()
                server.login(username, password)
                server.send_message(message)
            status = "sent"
            print(f"Email sent to {to_email}: {subject}")
        except (
            OSError,
            RuntimeError,
            smtplib.SMTPException,
            TemplateError,
            ValueError,
        ) as exc:
            error_message = str(exc)
            print(f"Email failed for {to_email} ({subject}): {error_message}")
            current_app.logger.error(
                "Email delivery failed for %s (%s): %s",
                to_email,
                subject,
                error_message,
            )

        try:
            _record_email_log(
                current_app._get_current_object(),
                to_email,
                subject,
                status,
                error_message,
            )
        except (DatabaseError, OSError, pymysql.MySQLError):
            current_app.logger.exception("Could not save the email delivery log")

def send_email(to_email, subject, text_body, html_body=None):
    application = current_app._get_current_object()
    if not email_is_configured():
        error_message = (
            "Email skipped: configure MAIL_SERVER, MAIL_USERNAME, and "
            "MAIL_PASSWORD in the project .env file."
        )
        current_app.logger.error(
            "Email was not queued because SMTP configuration is incomplete"
        )
        try:
            _record_email_log(
                application, to_email, subject, "failed", error_message
            )
        except (DatabaseError, OSError, pymysql.MySQLError):
            current_app.logger.exception("Could not save the email delivery log")
        return False

    worker = threading.Thread(
        target=_deliver_email,
        args=(application, to_email, subject, text_body, html_body),
        daemon=True,
        name="arco-email-delivery",
    )
    try:
        worker.start()
        return True
    except RuntimeError as exc:
        current_app.logger.error("Email could not be queued for %s: %s", to_email, exc)
        try:
            _record_email_log(
                application,
                to_email,
                subject,
                "failed",
                f"Email could not be queued: {exc}",
            )
        except (DatabaseError, OSError, pymysql.MySQLError):
            current_app.logger.exception("Could not save the email delivery log")
        return False

def email_is_configured():
    return bool(
        current_app.config.get("MAIL_SERVER")
        and current_app.config.get("MAIL_USERNAME")
        and current_app.config.get("MAIL_PASSWORD")
    )

def format_time_12_hour(value):
    if not value:
        return "—"
    settings = setting_values()
    formatted = str(value)
    try:
        if "T" in formatted or " " in formatted:
            parsed = datetime.fromisoformat(formatted.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(ZoneInfo(settings["timezone"]))
        else:
            parsed_time = time.fromisoformat(formatted)
            parsed = datetime.combine(date.today(), parsed_time)
        return parsed.strftime("%I:%M %p" if settings["time_format"] == "12" else "%H:%M")
    except (ValueError, ZoneInfoNotFoundError):
        return formatted

def format_gym_date(value, legacy_date=None):
    if not value:
        return legacy_date or "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return legacy_date or parsed.date().isoformat()
        return parsed.astimezone(gym_timezone()).date().isoformat()
    except (ValueError, ZoneInfoNotFoundError):
        return legacy_date or str(value)

def format_stay_duration(start_value, end_value):
    if not start_value:
        return "—"
    if not end_value:
        return "In progress"
    try:
        start_text, end_text = str(start_value), str(end_value)
        start_is_timestamp = "T" in start_text or " " in start_text
        end_is_timestamp = "T" in end_text or " " in end_text
        if start_is_timestamp and end_is_timestamp:
            start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
            seconds = int((end - start).total_seconds())
        else:
            if start_is_timestamp:
                start = datetime.fromisoformat(start_text.replace("Z", "+00:00")).astimezone(
                    gym_timezone()
                ).replace(tzinfo=None)
                end_time = time.fromisoformat(end_text)
                end = datetime.combine(start.date(), end_time)
            elif end_is_timestamp:
                end = datetime.fromisoformat(end_text.replace("Z", "+00:00")).astimezone(
                    gym_timezone()
                ).replace(tzinfo=None)
                start_time = time.fromisoformat(start_text)
                start = datetime.combine(end.date(), start_time)
            else:
                start_time = time.fromisoformat(start_text)
                end_time = time.fromisoformat(end_text)
                start = datetime.combine(date.today(), start_time)
                end = datetime.combine(date.today(), end_time)
            seconds = int((end - start).total_seconds())
            if seconds < 0:
                seconds += 24 * 60 * 60
        minutes = max(0, seconds // 60)
        hours, remainder = divmod(minutes, 60)
        return f"{hours}h {remainder}m" if hours else f"{remainder}m"
    except ValueError:
        return "—"

# ---------- database ----------
class DatabaseError(Exception):
    pass

class DatabaseIntegrityError(DatabaseError):
    pass

class DatabaseRow(dict):
    def __init__(self, values):
        row = dict(values)
        super().__init__(row)
        self._values = list(row.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)

class DatabaseCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid
        if cursor.description is None:
            cursor.close()
            self.cursor = None

    def _row(self, row):
        return DatabaseRow(row) if row is not None else None

    def fetchone(self):
        if self.cursor is None:
            return None
        row = self.cursor.fetchone()
        self.cursor.close()
        self.cursor = None
        return self._row(row)

    def fetchall(self):
        if self.cursor is None:
            return []
        rows = self.cursor.fetchall()
        self.cursor.close()
        self.cursor = None
        return [self._row(row) for row in rows]

    def __iter__(self):
        return iter(self.fetchall())

class DatabaseConnection:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def _sql(statement):
        statement = statement.replace("INSERT OR IGNORE", "INSERT IGNORE")
        statement = statement.replace("?", "%s")
        statement = statement.replace("COLLATE NOCASE", "COLLATE utf8mb4_general_ci")
        statement = statement.replace("BEGIN IMMEDIATE", "START TRANSACTION")
        return statement

    def execute(self, statement, parameters=()):
        cursor = self.connection.cursor(DictCursor)
        try:
            cursor.execute(self._sql(statement), parameters)
            return DatabaseCursor(cursor)
        except pymysql.err.IntegrityError as exc:
            cursor.close()
            raise DatabaseIntegrityError(str(exc)) from exc
        except pymysql.MySQLError as exc:
            cursor.close()
            raise DatabaseError(str(exc)) from exc

    def executemany(self, statement, parameters):
        cursor = self.connection.cursor(DictCursor)
        try:
            cursor.executemany(self._sql(statement), parameters)
            return DatabaseCursor(cursor)
        except pymysql.err.IntegrityError as exc:
            cursor.close()
            raise DatabaseIntegrityError(str(exc)) from exc
        except pymysql.MySQLError as exc:
            cursor.close()
            raise DatabaseError(str(exc)) from exc

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

def _connect_mysql(database=DATABASE_NAME):
    connection = pymysql.connect(
        host=DATABASE_HOST,
        port=DATABASE_PORT,
        user=DATABASE_USER,
        password=DATABASE_PASSWORD,
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )
    cursor = connection.cursor()
    try:
        cursor.execute("SET time_zone = '+08:00'")
    finally:
        cursor.close()
    return DatabaseConnection(connection)

def get_db():
    if "db" not in g:
        g.db = _connect_mysql()
    return g.db

def _migrate_user_profile_columns(db):
    columns = {
        row["COLUMN_NAME"]
        for row in db.execute(
            """SELECT COLUMN_NAME FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = ?""",
            ("users",),
        ).fetchall()
    }
    definitions = {
        "full_name": "VARCHAR(255) NOT NULL DEFAULT ''",
        "phone": "VARCHAR(50) NOT NULL DEFAULT ''",
        "profile_photo": "VARCHAR(255) NOT NULL DEFAULT ''",
    }

    for column, definition in definitions.items():
        if column not in columns:
            db.execute(f"ALTER TABLE users ADD COLUMN `{column}` {definition}")


def _migrate_payment_columns(db):
    columns = {
        row["COLUMN_NAME"]
        for row in db.execute(
            """SELECT COLUMN_NAME FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = ?""",
            ("payments",),
        ).fetchall()
    }
    definitions = {
        "payment_status": "VARCHAR(20) NOT NULL DEFAULT 'Confirmed'",
        "reference_number": "VARCHAR(100) NULL",
        "checkout_token": "VARCHAR(64) NULL",
        "paid_at": "DATETIME NULL",
        "receipt_no": "VARCHAR(32) NULL",
        "voided_by": "INT UNSIGNED NULL",
        "voided_at": "DATETIME NULL",
        "void_reason": "VARCHAR(500) NULL",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db.execute(f"ALTER TABLE payments ADD COLUMN `{column}` {definition}")
    db.execute(
        """UPDATE payments SET payment_status = 'Confirmed'
           WHERE LOWER(TRIM(payment_status)) = 'paid'"""
    )
    db.execute(
        """UPDATE payments SET paid_at = STR_TO_DATE(LEFT(payment_date, 10), '%%Y-%%m-%%d')
           WHERE paid_at IS NULL
             AND payment_date REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS payment_receipt_sequences (
               sequence_no BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
               payment_id INT UNSIGNED NOT NULL,
               PRIMARY KEY (sequence_no),
               UNIQUE KEY uq_payment_receipt_payment (payment_id),
               CONSTRAINT fk_payment_receipt_sequence_payment
                   FOREIGN KEY (payment_id) REFERENCES payments (id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    db.execute(
        """INSERT IGNORE INTO payment_receipt_sequences (payment_id)
           SELECT id FROM payments WHERE paid_at IS NOT NULL
           ORDER BY paid_at ASC, id ASC"""
    )
    db.execute(
        """UPDATE payments p
           JOIN payment_receipt_sequences s ON s.payment_id = p.id
           SET p.receipt_no = CONCAT('R-', LPAD(s.sequence_no, 4, '0'))
           WHERE p.receipt_no IS NULL OR p.receipt_no = ''"""
    )
    indexes = {
        row["INDEX_NAME"]
        for row in db.execute(
            """SELECT DISTINCT INDEX_NAME FROM information_schema.statistics
               WHERE table_schema = DATABASE() AND table_name = 'payments'"""
        ).fetchall()
    }
    if "idx_payments_checkout_token" not in indexes:
        db.execute(
            "CREATE UNIQUE INDEX idx_payments_checkout_token ON payments (checkout_token)"
        )
    index_definitions = {
        "attendance": {
            "idx_attendance_checkin_date": "CREATE INDEX idx_attendance_checkin_date ON attendance (checkin_date)",
        },
        "walkin_visits": {
            "idx_walkin_visits_date": "CREATE INDEX idx_walkin_visits_date ON walkin_visits (visit_date)",
        },
        "member_memberships": {
            "idx_member_memberships_member_dates": "CREATE INDEX idx_member_memberships_member_dates ON member_memberships (member_id, start_date, end_date)",
            "idx_member_memberships_status_dates": "CREATE INDEX idx_member_memberships_status_dates ON member_memberships (status, end_date)",
        },
        "payments": {
            "idx_payments_member_date": "CREATE INDEX idx_payments_member_date ON payments (member_id, payment_date)",
            "idx_payments_status_date": "CREATE INDEX idx_payments_status_date ON payments (payment_status, payment_date)",
            "idx_payments_paid_status_method": "CREATE INDEX idx_payments_paid_status_method ON payments (paid_at, payment_status, method)",
            "uq_payments_receipt_no": "CREATE UNIQUE INDEX uq_payments_receipt_no ON payments (receipt_no)",
        },
    }
    for table, definitions_by_name in index_definitions.items():
        existing_indexes = {
            row["INDEX_NAME"]
            for row in db.execute(
                """SELECT DISTINCT INDEX_NAME FROM information_schema.statistics
                   WHERE table_schema = DATABASE() AND table_name = ?""",
                (table,),
            ).fetchall()
        }
        for name, statement in definitions_by_name.items():
            if name not in existing_indexes:
                db.execute(statement)
                existing_indexes.add(name)
    constraints = {
        row["CONSTRAINT_NAME"]
        for row in db.execute(
            """SELECT CONSTRAINT_NAME FROM information_schema.key_column_usage
               WHERE constraint_schema = DATABASE() AND table_name = 'payments'
                 AND constraint_name = 'fk_payments_voided_by'"""
        ).fetchall()
    }
    if "fk_payments_voided_by" not in constraints:
        db.execute(
            """ALTER TABLE payments ADD CONSTRAINT fk_payments_voided_by
               FOREIGN KEY (voided_by) REFERENCES users (id)"""
        )

def _assign_payment_receipt(db, payment_id, paid_at=None):
    timestamp = paid_at or philippine_now()
    sequence = db.execute(
        "INSERT INTO payment_receipt_sequences (payment_id) VALUES (?)",
        (payment_id,),
    ).lastrowid
    if sequence is None:
        raise DatabaseIntegrityError("A receipt sequence was not created for the payment.")
    receipt_no = f"R-{int(sequence):04d}"
    result = db.execute(
        """UPDATE payments SET receipt_no = ?, paid_at = COALESCE(paid_at, ?)
           WHERE id = ?""",
        (receipt_no, timestamp, payment_id),
    )
    if result.rowcount != 1:
        raise DatabaseIntegrityError("The payment receipt could not be saved.")
    return receipt_no

def _migrate_walkin_fee_columns(db):
    columns = {
        row["COLUMN_NAME"]
        for row in db.execute(
            """SELECT COLUMN_NAME FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = 'walkin_visits'"""
        ).fetchall()
    }
    definitions = {
        "fee_status": "VARCHAR(20) NOT NULL DEFAULT 'Pending'",
        "fee_paid_at": "DATETIME NULL",
        "fee_paid_by": "INT UNSIGNED NULL",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db.execute(f"ALTER TABLE walkin_visits ADD COLUMN `{column}` {definition}")
    indexes = {
        row["INDEX_NAME"]
        for row in db.execute(
            """SELECT DISTINCT INDEX_NAME FROM information_schema.statistics
               WHERE table_schema = DATABASE() AND table_name = 'walkin_visits'"""
        ).fetchall()
    }
    if "idx_walkin_visits_date_fee_status" not in indexes:
        db.execute(
            """CREATE INDEX idx_walkin_visits_date_fee_status
               ON walkin_visits (visit_date, fee_status)"""
        )
    constraints = {
        row["CONSTRAINT_NAME"]
        for row in db.execute(
            """SELECT CONSTRAINT_NAME FROM information_schema.key_column_usage
               WHERE constraint_schema = DATABASE()
                 AND table_name = 'walkin_visits'
                 AND constraint_name = 'fk_walkin_visits_fee_paid_by'"""
        ).fetchall()
    }
    if "fk_walkin_visits_fee_paid_by" not in constraints:
        db.execute(
            """ALTER TABLE walkin_visits ADD CONSTRAINT fk_walkin_visits_fee_paid_by
               FOREIGN KEY (fee_paid_by) REFERENCES users (id)"""
        )

def _migrate_member_notification_reads(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS member_notification_reads (
               member_user_id INT UNSIGNED NOT NULL,
               notification_key VARCHAR(64) NOT NULL,
               read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               PRIMARY KEY (member_user_id, notification_key),
               CONSTRAINT fk_member_notification_reads_user
                   FOREIGN KEY (member_user_id) REFERENCES users (id) ON DELETE CASCADE
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
    )
    db.execute(
        """INSERT IGNORE INTO member_notification_reads
               (member_user_id, notification_key)
           SELECT m.user_id,
                  CONCAT('payment:', p.id, ':', LOWER(p.payment_status))
           FROM payments p
           JOIN members m ON m.id = p.member_id
           WHERE m.user_id IS NOT NULL
             AND LOWER(p.payment_status) IN ('confirmed', 'paid', 'rejected')"""
    )

def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def protect_post_requests():
    if request.method != "POST":
        return
    expected = session.get("_csrf_token")
    supplied = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected and request.endpoint == "members.remove_member":
        session.clear()
        flash("Your session expired. Please sign in again, then retry the action.", "error")
        return redirect(url_for("auth.login"))
    if (not expected or not supplied or not secrets.compare_digest(expected, supplied)):
        if request.endpoint == "portal.forgot_password":
            flash(
                "The form expired. Please try again.",
                "error",
            )
            return redirect(url_for("portal.forgot_password"))
        abort(400, description="The form security token is missing or expired.")


def auth_attempt_limiter():
    return current_app.extensions["auth_attempt_limiter"]


def _admin_notification_data(db, current_date, notification_settings):
    if hasattr(g, "_admin_notification_data"):
        return g._admin_notification_data

    admin_user_id = session.get("user_id")
    today = current_date.isoformat()
    attention_end = current_date + timedelta(days=7)
    inactivity_cutoff = (
        current_date - timedelta(days=int(notification_settings["inactive_member_days"]))
    ).isoformat()
    membership_reminders_enabled = notification_settings["membership_reminders"] == "1"
    checkout_warnings_enabled = notification_settings["checkout_warnings"] == "1"
    notifications = {
        "expiring_memberships": [],
        "overdue_memberships": [],
        "open_walkin_checkouts": [],
        "new_registrations": [],
        "inactive_members": [],
    }

    expiring_count = 0
    if membership_reminders_enabled:
        notifications["expiring_memberships"] = db.execute(
            """SELECT mm.id AS membership_id, m.id AS member_id,
                      m.full_name, p.name AS plan_name, mm.end_date
               FROM member_memberships mm
               JOIN members m ON m.id = mm.member_id
               JOIN membership_plans p ON p.id = mm.plan_id
               WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                 AND lower(mm.status) = 'active'
                 AND mm.end_date BETWEEN ? AND ?
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('expiring:', mm.id)
                 )
               ORDER BY mm.end_date ASC, mm.id DESC LIMIT 5""",
            (today, attention_end.isoformat(), admin_user_id),
        ).fetchall()
        expiring_count = db.execute(
            """SELECT COUNT(*) FROM member_memberships mm
               JOIN members m ON m.id = mm.member_id
               WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                 AND lower(mm.status) = 'active'
                 AND mm.end_date BETWEEN ? AND ?
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('expiring:', mm.id)
                 )""",
            (today, attention_end.isoformat(), admin_user_id),
        ).fetchone()[0]
        for item in notifications["expiring_memberships"]:
            item["days_remaining"] = (
                date.fromisoformat(str(item["end_date"])) - current_date
            ).days

    membership_alert_types = (
        (
            "pending_payment_reminders",
            "unpaid",
            """lower(mm.status) = 'pending' AND NOT EXISTS (
                   SELECT 1 FROM payments pay WHERE pay.membership_id = mm.id
                     AND lower(pay.payment_status) = 'pending'
               )""",
        ),
        (
            "gcash_payment_reminders",
            "gcash_pending",
            """lower(mm.status) = 'pending' AND EXISTS (
                   SELECT 1 FROM payments pay WHERE pay.membership_id = mm.id
                     AND lower(pay.payment_status) = 'pending'
               )""",
        ),
        (
            "expired_membership_reminders",
            "expired",
            "(lower(mm.status) = 'expired' OR "
            "(lower(mm.status) = 'active' AND mm.end_date < ?))",
        ),
    )
    overdue_count = 0
    for setting_key, attention_type, condition in membership_alert_types:
        if notification_settings[setting_key] != "1":
            continue
        query_parameters = (today,) if attention_type == "expired" else ()
        rows = db.execute(
            f"""SELECT mm.id AS membership_id, m.id AS member_id,
                       m.full_name, p.name AS plan_name, mm.end_date
                FROM member_memberships mm
                JOIN members m ON m.id = mm.member_id
                JOIN membership_plans p ON p.id = mm.plan_id
                WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                  AND {condition}
                  AND NOT EXISTS (
                      SELECT 1 FROM admin_notification_reads nr
                      WHERE nr.admin_user_id = ?
                        AND nr.notification_key = CONCAT('{attention_type}:', mm.id)
                  )
                ORDER BY mm.end_date ASC, mm.id DESC LIMIT 5""",
            (*query_parameters, admin_user_id),
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["attention_type"] = attention_type
            notifications["overdue_memberships"].append(item)
        overdue_count += db.execute(
            f"""SELECT COUNT(*) FROM member_memberships mm
                JOIN members m ON m.id = mm.member_id
                WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                  AND {condition}
                  AND NOT EXISTS (
                      SELECT 1 FROM admin_notification_reads nr
                      WHERE nr.admin_user_id = ?
                        AND nr.notification_key = CONCAT('{attention_type}:', mm.id)
                  )""",
            (*query_parameters, admin_user_id),
        ).fetchone()[0]

    if checkout_warnings_enabled:
        notifications["open_walkin_checkouts"] = db.execute(
            """SELECT walkin_visits.id AS visit_id, full_name, visit_date, checkin_time
               FROM walkin_visits
               WHERE checkout_time IS NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('walkin:', walkin_visits.id)
                 )
               ORDER BY visit_date DESC, checkin_time DESC, id DESC LIMIT 5""",
            (admin_user_id,),
        ).fetchall()
        open_walkin_count = db.execute(
            """SELECT COUNT(*) FROM walkin_visits
               WHERE checkout_time IS NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('walkin:', walkin_visits.id)
                 )""",
            (admin_user_id,),
        ).fetchone()[0]
    else:
        open_walkin_count = 0

    new_registration_count = 0
    if notification_settings["new_registration_reminders"] == "1":
        notifications["new_registrations"] = db.execute(
            """SELECT id AS member_id, full_name, created_at
               FROM members
               WHERE RIGHT(email, 16) <> '@deleted.invalid'
              AND NOT EXISTS (
                  SELECT 1 FROM admin_notification_reads nr
                  WHERE nr.admin_user_id = ?
                    AND nr.notification_key = CONCAT('registration:', members.id)
              )
              AND created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)
            ORDER BY created_at DESC, id DESC LIMIT 5""",
            (admin_user_id,),
        ).fetchall()
        new_registration_count = db.execute(
            """SELECT COUNT(*) FROM members
            WHERE RIGHT(email, 16) <> '@deleted.invalid'
              AND NOT EXISTS (
                  SELECT 1 FROM admin_notification_reads nr
                  WHERE nr.admin_user_id = ?
                    AND nr.notification_key = CONCAT('registration:', members.id)
              )
              AND created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)""",
            (admin_user_id,),
        ).fetchone()[0]

    inactive_member_count = 0
    if notification_settings["inactive_member_reminders"] == "1":
        notifications["inactive_members"] = db.execute(
            """SELECT m.id AS member_id, m.full_name,
                      MAX(a.checkin_date) AS last_checkin
               FROM members m
               LEFT JOIN attendance a ON a.member_id = m.id
               WHERE m.user_id IS NOT NULL
                 AND lower(m.status) = 'active'
                 AND RIGHT(m.email, 16) <> '@deleted.invalid'
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('inactive:', m.id)
                 )
               GROUP BY m.id, m.full_name
               HAVING MAX(a.checkin_date) IS NULL OR MAX(a.checkin_date) < ?
               ORDER BY last_checkin ASC, m.full_name ASC LIMIT 5""",
            (admin_user_id, inactivity_cutoff),
        ).fetchall()
        inactive_member_count = db.execute(
            """SELECT COUNT(*) FROM members m
               WHERE m.user_id IS NOT NULL
                 AND lower(m.status) = 'active'
                 AND RIGHT(m.email, 16) <> '@deleted.invalid'
                 AND NOT EXISTS (
                     SELECT 1 FROM admin_notification_reads nr
                     WHERE nr.admin_user_id = ?
                       AND nr.notification_key = CONCAT('inactive:', m.id)
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM attendance a
                     WHERE a.member_id = m.id AND a.checkin_date >= ?
                 )""",
            (admin_user_id, inactivity_cutoff),
        ).fetchone()[0]

    notifications.update({
        "attention_count": (
            expiring_count + overdue_count + open_walkin_count
            + new_registration_count + inactive_member_count
        ),
        "membership_reminders_enabled": membership_reminders_enabled,
        "checkout_warnings_enabled": checkout_warnings_enabled,
        "new_registration_count": new_registration_count,
        "inactive_member_count": inactive_member_count,
        "inactive_member_days": int(notification_settings["inactive_member_days"]),
    })
    g._admin_notification_data = notifications
    return notifications

def inject_shared_template_values():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    db = get_db()
    values = setting_values(db)
    current_user_profile = None
    if session.get("user_id") is not None:
        user = db.execute(
            """SELECT username, role, full_name, profile_photo
               FROM users WHERE id = ?""",
            (session["user_id"],),
        ).fetchone()
        if user:
            current_user_profile = dict(user)
            current_user_profile["photo_url"] = _profile_image_url(
                current_user_profile["profile_photo"]
            )
    shared_values = {
        "csrf_token": session["_csrf_token"],
        "settings_values": values,
        "walk_in_fee": float(values["walk_in_fee"]),
        "monthly_fee": float(values["monthly_fee"]),
        "current_user_profile": current_user_profile,
    }
    if current_user_profile and current_user_profile["role"] == "admin":
        shared_values.update(
            _admin_notification_data(db, philippine_date(), values)
        )
    return shared_values

def _local_secret_key():
    instance_dir = os.path.join(BASE_DIR, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    secret_path = os.path.join(instance_dir, "secret_key")
    try:
        with open(secret_path, "x", encoding="utf-8") as secret_file:
            secret_key = secrets.token_hex(32)
            secret_file.write(secret_key)
            return secret_key
    except FileExistsError:
        with open(secret_path, encoding="utf-8") as secret_file:
            secret_key = secret_file.read().strip()
        if not secret_key:
            raise RuntimeError(f"The Flask secret key file is empty: {secret_path}")
        return secret_key


def _environment_boolean(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be set to true or false.")


def init_db():
    if not re.fullmatch(r"[A-Za-z0-9_]+", DATABASE_NAME):
        raise ValueError("DB_NAME may contain only letters, numbers, and underscores.")
    connection = pymysql.connect(
        host=DATABASE_HOST, port=DATABASE_PORT, user=DATABASE_USER,
        password=DATABASE_PASSWORD, charset="utf8mb4",
        cursorclass=DictCursor, autocommit=False,
    )
    db = DatabaseConnection(connection)
    try:
        db.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        connection.select_db(DATABASE_NAME)
        with open(os.path.join(BASE_DIR, "arco_fitness.sql"), encoding="utf-8") as schema_file:
            for statement in schema_file.read().split(";"):
                statement = statement.strip()
                if (statement and not statement.upper().startswith(
                        ("CREATE DATABASE", "USE "))):
                    db.execute(statement)
        _migrate_user_profile_columns(db)
        _migrate_payment_columns(db)
        _migrate_walkin_fee_columns(db)
        _migrate_member_notification_reads(db)
        db.executemany(
            "INSERT IGNORE INTO app_settings (`key`, `value`) VALUES (?, ?)",
            DEFAULT_SETTINGS.items(),
        )
        db.execute(
            "DELETE FROM app_settings WHERE `key` = 'system_notifications'"
        )
        monthly_fee = setting_values(db)["monthly_fee"]
        if not db.execute(
                "SELECT 1 FROM membership_plans "
                "WHERE lower(name) = 'monthly membership'").fetchone():
            db.execute(
                """INSERT INTO membership_plans
                   (name, duration_days, price, description)
                   VALUES ('Monthly Membership', 30, ?, '30-day gym membership')""",
                (monthly_fee,),
            )
        db.execute(
            """UPDATE membership_plans SET duration_days = 30, price = ?, active = 1
               WHERE lower(name) = 'monthly membership'""",
            (monthly_fee,),
        )
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            initial_admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
            if len(initial_admin_password) < 12:
                raise RuntimeError(
                    "Set INITIAL_ADMIN_PASSWORD to a password with at least 12 "
                    "characters before initializing an empty users table."
                )
            password_error = _new_password_error(initial_admin_password)
            if password_error:
                raise RuntimeError(
                    "INITIAL_ADMIN_PASSWORD must contain at least 12 characters, "
                    "one number, and one uppercase letter."
                )
            db.execute(
                """INSERT INTO users
                   (username, password_hash, role)
                   VALUES (?, ?, ?)""",
                ("admin", generate_password_hash(initial_admin_password), "admin"),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ---------- auth ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("auth.login"))
        user = get_db().execute(
            "SELECT username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            session.clear()
            return redirect(url_for("auth.login"))
        if user["role"] != "admin":
            session["role"] = user["role"]
            return redirect(url_for("portal.portal_dashboard"))
        session["role"] = "admin"
        session["username"] = user["username"]
        return view(*args, **kwargs)

    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("auth.login"))
        user = get_db().execute(
            "SELECT username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None or user["role"] != "admin":
            session.clear()
            return redirect(url_for("auth.login"))
        session["role"] = "admin"
        session["username"] = user["username"]
        return view(*args, **kwargs)

    return wrapped

def portal_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None or session.get("role") != "member":
            return redirect(url_for("portal.portal_login"))
        member = get_db().execute(
            """SELECT 1 FROM users u
               JOIN members m ON m.user_id = u.id
               WHERE u.id = ? AND u.role = 'member'
                 AND lower(m.status) = 'active'""",
            (user_id,),
        ).fetchone()
        if member is None:
            session.clear()
            flash("Your member account is inactive or unavailable. Please contact the gym.", "error")
            return redirect(url_for("portal.portal_login"))
        return view(*args, **kwargs)

    return wrapped

def _portal_member_context():
    db = get_db()
    member = db.execute(
        "SELECT m.* FROM members m WHERE m.user_id = ?",
        (session["user_id"],),
    ).fetchone()
    if member is None:
        session.clear()
        flash("Your member account is not linked to a gym record.", "error")
        return None

    today = philippine_date().isoformat()
    membership = db.execute(
        """SELECT mm.plan_id, p.name AS plan_name, p.duration_days,
                  mm.start_date AS plan_start, mm.end_date AS plan_end,
                  mm.status AS plan_status
           FROM member_memberships mm
           JOIN membership_plans p ON p.id = mm.plan_id
           WHERE mm.member_id = ?
           ORDER BY CASE
               WHEN lower(mm.status) = 'active'
                    AND mm.start_date <= ? AND mm.end_date >= ? THEN 0
               WHEN lower(mm.status) = 'pending' THEN 1
               ELSE 2
           END, mm.id DESC
           LIMIT 1""",
        (member["id"], today, today),
    ).fetchone()
    member = dict(member)
    if membership:
        member.update(membership)
    else:
        member.update({
            "plan_id": None,
            "plan_name": None,
            "duration_days": None,
            "plan_start": None,
            "plan_end": None,
            "plan_status": None,
        })

    if member["plan_end"] and member["plan_end"] < today:
        membership_status = "Expired"
    elif member["plan_status"] and member["plan_status"].lower() == "pending":
        membership_status = "Pending"
    elif member["plan_status"] and member["plan_status"].lower() == "active":
        membership_status = "Active"
    else:
        membership_status = "No active plan"
    membership_active = bool(
        member["status"].lower() == "active"
        and member["plan_status"]
        and member["plan_status"].lower() == "active"
        and member["plan_end"]
        and member["plan_start"] <= today <= member["plan_end"]
    )
    renewal_indicator = _membership_renewal_indicator(
        member, membership_status, philippine_date()
    )
    return {
        "member": member,
        "membership_active": membership_active,
        "membership_status": membership_status,
        "renewal_indicator": renewal_indicator,
        "member_notifications": _member_notification_data(
            db, session["user_id"], member["id"]
        ),
        "has_pending_gcash": bool(db.execute(
            """SELECT 1 FROM payments
               WHERE member_id = ? AND method = 'GCash'
                 AND lower(payment_status) = 'pending'
               LIMIT 1""",
            (member["id"],),
        ).fetchone()),
        "current_year": philippine_now().year,
    }

def _membership_renewal_indicator(member, membership_status, today):
    end_date = member.get("plan_end")
    days_remaining = None
    if end_date:
        days_remaining = (date.fromisoformat(str(end_date)) - today).days

    if membership_status == "Expired":
        state = "expired"
        title = "Membership expired"
        message = "Renew now to restore access to your membership benefits."
    elif membership_status == "Pending":
        state = "pending"
        title = "Payment awaiting confirmation"
        message = "Your membership will become active after your payment is confirmed."
    elif membership_status == "Active" and days_remaining is not None and days_remaining <= 7:
        state = "expiring"
        title = "Renew soon"
        if days_remaining <= 0:
            message = "Your membership expires today. Renew now to keep your benefits."
        elif days_remaining == 1:
            message = "Your membership expires in 1 day. Renew now to avoid interruption."
        else:
            message = (
                f"Your membership expires in {days_remaining} days. "
                "Renew now to avoid interruption."
            )
    elif membership_status == "Active":
        state = "active"
        title = "Membership active"
        if days_remaining is None:
            message = "Your membership is active."
        elif days_remaining == 1:
            message = "You have 1 day remaining on your membership."
        else:
            message = f"You have {days_remaining} days remaining on your membership."
    else:
        state = "none"
        title = "Choose a membership"
        message = "Select a plan to start enjoying your membership benefits."

    duration_days = member.get("duration_days")
    if duration_days:
        total_days = max(1, int(duration_days))
    elif days_remaining is not None:
        total_days = max(1, days_remaining + 1)
    else:
        total_days = 1
    progress = (
        max(0, min(100, round(max(0, days_remaining) / total_days * 100)))
        if days_remaining is not None
        else 0
    )
    return {
        "state": state,
        "title": title,
        "message": message,
        "days_remaining": days_remaining,
        "progress": progress,
        "show_renewal_action": state in {"expired", "expiring", "none"},
    }

def _member_notification_data(db, user_id, member_id):
    rows = db.execute(
        """SELECT p.id, p.amount, p.method, p.payment_status,
                  CONCAT('payment:', p.id, ':', LOWER(p.payment_status)) AS notification_key
           FROM payments p
           LEFT JOIN member_notification_reads nr
             ON nr.member_user_id = ?
            AND nr.notification_key =
                CONCAT('payment:', p.id, ':', LOWER(p.payment_status))
           WHERE p.member_id = ?
             AND LOWER(p.payment_status) IN ('pending', 'confirmed', 'paid', 'rejected')
             AND nr.notification_key IS NULL
           ORDER BY p.id DESC
           LIMIT 10""",
        (user_id, member_id),
    ).fetchall()
    notifications = []
    for row in rows:
        status = row["payment_status"].lower()
        method = row["method"]
        amount = Decimal(str(row["amount"]))
        if status == "pending":
            title = "Payment awaiting confirmation"
            icon = "fa-hourglass-half"
            style = "warning"
        elif status == "rejected":
            title = "Payment not approved"
            icon = "fa-circle-exclamation"
            style = "danger"
        else:
            title = "Payment confirmed"
            icon = "fa-circle-check"
            style = "success"
        notifications.append({
            "key": row["notification_key"],
            "title": title,
            "message": f"{method} · PHP {amount:,.2f}",
            "icon": icon,
            "style": style,
        })
    return notifications

def _portal_page_context(active_page):
    context = _portal_member_context()
    if context is None:
        return None
    context["active_portal_page"] = active_page
    return context

def _valid_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def _pagination(total_records, page_size=50):
    total_pages = max(1, (total_records + page_size - 1) // page_size)
    page = max(1, request.args.get("page", 1, type=int) or 1)
    page = min(page, total_pages)
    return {
        "page": page,
        "page_size": page_size,
        "page_offset": (page - 1) * page_size,
        "total_pages": total_pages,
        "total_records": total_records,
    }

def _valid_checkout_token(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{32,64}", value or ""))

def _settings_redirect(section=None):
    if section is None:
        section = {
            "account": "profile",
            "gym": "gym",
            "pricing": "pricing",
            "attendance": "attendance",
            "notifications": "notifications",
        }.get(request.form.get("action"), "overview")
    if section not in {
        "overview", "profile", "email", "gym", "pricing", "gcash",
        "attendance", "notifications", "data", "danger",
    }:
        section = "overview"
    return redirect(url_for("settings.settings", section=section))

def _save_setting_values(db, values):
    db.executemany(
        """INSERT INTO app_settings (`key`, `value`) VALUES (?, ?)
           ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)""",
        [(key, str(value)) for key, value in values.items()],
    )

def _money_value(raw):
    try:
        amount = Decimal(raw.strip())
    except (AttributeError, InvalidOperation):
        return None
    if not amount.is_finite() or amount < 0 or not math.isfinite(float(amount)):
        return None
    return amount

def _safe_csv_value(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value

def _database_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_name = "arco-backup-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    ) + ".sql"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    db = None
    try:
        db = _connect_mysql()
        tables = db.execute("SHOW TABLES").fetchall()
        with open(backup_path, "w", encoding="utf-8", newline="\n") as backup:
            backup.write("SET FOREIGN_KEY_CHECKS=0;\n")
            for table_row in tables:
                table_name = table_row[0]
                create_statement = db.execute(
                    f"SHOW CREATE TABLE `{table_name}`"
                ).fetchone()[1]
                backup.write(f"\n{create_statement};\n")
                columns = db.execute(f"SELECT * FROM `{table_name}`")
                rows = columns.fetchall()
                if rows:
                    column_names = list(rows[0].keys())
                    quoted_columns = ", ".join(
                        f"`{name}`" for name in column_names
                    )
                    for row in rows:
                        escaped_values = ", ".join(
                            db.connection.escape(row[name])
                            for name in column_names
                        )
                        backup.write(
                            f"INSERT INTO `{table_name}` ({quoted_columns}) "
                            f"VALUES ({escaped_values});\n"
                        )
            backup.write("\nSET FOREIGN_KEY_CHECKS=1;\n")
        return backup_path
    except (OSError, DatabaseError):
        if os.path.exists(backup_path):
            os.unlink(backup_path)
        raise
    finally:
        if db is not None:
            db.close()

def _backfill_legacy_timestamps(db, table, date_column, checkout_column=None):
    select_columns = "id, {}, checkin_time, checkin_at".format(date_column)
    if checkout_column:
        select_columns += ", {}, checkout_at".format(checkout_column)
    else:
        select_columns += ", NULL, checkout_at"
    rows = db.execute(
        f"SELECT {select_columns} FROM {table} WHERE checkin_at IS NULL"
    ).fetchall()
    legacy_zone = ZoneInfo("Asia/Manila")
    for row in rows:
        try:
            local_start = datetime.combine(
                date.fromisoformat(row[1]),
                time.fromisoformat(row[2]),
                tzinfo=legacy_zone,
            )
            checkin_at = local_start.astimezone(timezone.utc).isoformat(timespec="seconds")
            checkout_at = None
            if row[4]:
                local_end = datetime.combine(
                    date.fromisoformat(row[1]),
                    time.fromisoformat(row[4]),
                    tzinfo=legacy_zone,
                )
                checkout_at = local_end.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            current_app.logger.warning(
                "Could not convert legacy attendance timestamp in %s row %s",
                table, row[0],
            )
            continue
        db.execute(
            f"UPDATE {table} SET checkin_at = ?, checkout_at = ? WHERE id = ?",
            (checkin_at, checkout_at, row[0]),
        )

def _record_backup(db):
    backed_up_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_setting_values(db, {"last_backup": backed_up_at})
    db.commit()
    return backed_up_at

def _verify_admin_password(password):
    user = get_db().execute(
        "SELECT password_hash FROM users WHERE id = ? AND role = 'admin'",
        (session.get("user_id"),),
    ).fetchone()
    return bool(user and check_password_hash(user["password_hash"], password))

PROFILE_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads", "profiles")
PROFILE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
GCASH_QR_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
GCASH_QR_MAX_BYTES = 2 * 1024 * 1024

def _profile_image_url(filename):
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|webp)", filename or ""):
        return None
    image_path = os.path.join(PROFILE_UPLOAD_DIR, filename)
    if not os.path.isfile(image_path):
        return None
    return url_for("static", filename=f"uploads/profiles/{filename}")

def _daily_income_rows(db, start, end):
    return build_report_data(db, start, end, paginate=False)["daily"]

def _membership_checkout_details(db, member_id, plan_id, requested_start, lock=False):
    return MembershipService(db, philippine_date).checkout_details(
        member_id, plan_id, requested_start, lock=lock
    )

def _membership_page_context(db, args):
    today = philippine_date()
    today_text = today.isoformat()
    plans = db.execute(
        "SELECT id, name, duration_days, price, description, active FROM membership_plans "
        "ORDER BY active DESC, name COLLATE NOCASE"
    ).fetchall()
    active_plans = [plan for plan in plans if plan["active"]]
    members = db.execute(
        """SELECT m.id, m.full_name, m.status,
                  (SELECT MAX(mm.end_date) FROM member_memberships mm
                   WHERE mm.member_id = m.id AND lower(mm.status) = 'active'
                     AND mm.end_date >= ?) AS active_end,
                  (SELECT mm.end_date FROM member_memberships mm
                   WHERE mm.member_id = m.id ORDER BY mm.id DESC LIMIT 1) AS latest_end,
                  (SELECT mm.status FROM member_memberships mm
                   WHERE mm.member_id = m.id ORDER BY mm.id DESC LIMIT 1) AS latest_membership_status
           FROM members m WHERE lower(m.status) = 'active'
           ORDER BY m.full_name COLLATE NOCASE""",
        (today_text,),
    ).fetchall()
    for member in members:
        if member["active_end"]:
            member["member_state"] = f"Active until {member['active_end']}"
        elif (member["latest_membership_status"]
              and str(member["latest_membership_status"]).lower() == "pending"):
            member["member_state"] = "Payment pending"
        elif member["latest_end"]:
            member["member_state"] = f"Expired {member['latest_end']}"
        else:
            member["member_state"] = "No membership yet"

    assignments = db.execute(
        """SELECT mm.id, mm.member_id, mm.start_date, mm.end_date, mm.status,
                  m.full_name, p.name AS plan_name
           FROM member_memberships mm
           JOIN members m ON m.id = mm.member_id
           JOIN membership_plans p ON p.id = mm.plan_id
           WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
           ORDER BY mm.id DESC LIMIT 200"""
    ).fetchall()
    status_counts = {"all": len(assignments), "active": 0, "pending": 0, "expired": 0}
    for assignment in assignments:
        membership = Membership.from_record(assignment)
        assignment["display_status"] = membership.display_status(today)
        status_counts[assignment["display_status"].lower()] += 1
        assignment["status_note"] = membership.status_note(today)

    step = args.get("step", "select")
    checkout = None
    payment_membership_id = args.get("membership_id", type=int) or args.get("payment", type=int)
    if step == "payment":
        if payment_membership_id:
            checkout = db.execute(
                """SELECT mm.id AS membership_id, mm.member_id, mm.plan_id,
                          mm.start_date, mm.end_date, mm.status,
                          m.full_name, p.name AS plan_name, p.duration_days,
                          p.price, p.description
                   FROM member_memberships mm
                   JOIN members m ON m.id = mm.member_id
                   JOIN membership_plans p ON p.id = mm.plan_id
                   WHERE mm.id = ? AND lower(mm.status) = 'pending'
                     AND NOT EXISTS (
                         SELECT 1 FROM payments pay WHERE pay.membership_id = mm.id
                     )""",
                (payment_membership_id,),
            ).fetchone()
            if checkout:
                checkout = dict(checkout)
                checkout["start_date"] = date.fromisoformat(str(checkout["start_date"]))
                checkout["end_date"] = date.fromisoformat(str(checkout["end_date"]))
                checkout["date_adjusted"] = False
                checkout["active_end"] = None
        else:
            member_id = args.get("member_id", type=int)
            plan_id = args.get("plan_id", type=int)
            start = _valid_date(args.get("start_date", ""))
            if member_id and plan_id:
                checkout, error = _membership_checkout_details(
                    db, member_id, plan_id, start
                )
                if error:
                    flash(error, "error")
                    step = "select"
    if checkout:
        checkout["checkout_token"] = secrets.token_urlsafe(32)
    done_payment = None
    if args.get("step") == "done":
        payment_id = args.get("payment_id", type=int)
        if payment_id:
            done_payment = db.execute(
                """SELECT pay.id, pay.amount, pay.method, pay.payment_status,
                          pay.reference_number, mm.status AS membership_status,
                          mm.start_date, mm.end_date, m.full_name, p.name AS plan_name
                   FROM payments pay
                   LEFT JOIN member_memberships mm ON mm.id = pay.membership_id
                   JOIN members m ON m.id = pay.member_id
                   LEFT JOIN membership_plans p ON p.id = mm.plan_id
                   WHERE pay.id = ?""",
                (payment_id,),
            ).fetchone()
        step = "done"
    return {
        "plans": plans,
        "active_plans": active_plans,
        "members": members,
        "assignments": assignments,
        "status_counts": status_counts,
        "today": today_text,
        "step": step,
        "checkout": checkout,
        "done_payment": done_payment,
        "selected_member_id": args.get("member_id", type=int),
        "selected_plan_id": args.get("plan_id", type=int),
        "gcash_qr_url": _gcash_qr_url(
            setting_values(db).get("gcash_qr_filename", "")
        ),
    }


class SplitSessionInterface(SecureCookieSessionInterface):
    member_session_paths = {"/register", "/verify-otp", "/resend-otp"}

    def get_cookie_name(self, app):
        if has_request_context() and (
            request.path.startswith("/portal/")
            or request.path == "/portal"
            or request.path in self.member_session_paths
        ):
            return app.config["MEMBER_SESSION_COOKIE_NAME"]
        return super().get_cookie_name(app)


def create_app(test_config=None):
    for module_name in (
        "auth_routes",
        "portal_routes",
        "dashboard_routes",
        "settings_routes",
        "member_routes",
        "attendance_routes",
        "walkin_routes",
        "membership_routes",
        "payment_routes",
        "report_routes",
    ):
        importlib.import_module(module_name)

    application = Flask(__name__)
    try:
        trusted_proxy_hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
    except ValueError as exc:
        raise ValueError("TRUSTED_PROXY_HOPS must be a non-negative integer.") from exc
    if trusted_proxy_hops < 0:
        raise ValueError("TRUSTED_PROXY_HOPS must be a non-negative integer.")
    application_base_url = os.environ.get(
        "APPLICATION_BASE_URL",
        "http://127.0.0.1:5000",
    ).rstrip("/")
    parsed_base_url = urlsplit(application_base_url)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise ValueError("APPLICATION_BASE_URL must be an http or https origin.")
    application.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _local_secret_key(),
        MAX_CONTENT_LENGTH=21 * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=_environment_boolean(
            "SESSION_COOKIE_SECURE", parsed_base_url.scheme == "https"
        ),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_REFRESH_EACH_REQUEST=False,
        MEMBER_SESSION_COOKIE_NAME="member_session",
        TRUSTED_PROXY_HOPS=trusted_proxy_hops,
        MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
        MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
        MAIL_USE_SSL=_environment_boolean("MAIL_USE_SSL", False),
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD", ""),
        MAIL_FROM=os.environ.get("MAIL_FROM") or os.environ.get("MAIL_USERNAME", ""),
        APPLICATION_BASE_URL=application_base_url,
        SERVER_NAME=parsed_base_url.netloc,
        PREFERRED_URL_SCHEME=parsed_base_url.scheme,
    )
    if test_config is not None:
        application.config.update(test_config)
    application.session_interface = SplitSessionInterface()
    if trusted_proxy_hops:
        application.wsgi_app = ProxyFix(
            application.wsgi_app,
            x_for=trusted_proxy_hops,
            x_proto=trusted_proxy_hops,
        )
    application.extensions["auth_attempt_limiter"] = AuthAttemptLimiter(
        application.config["SECRET_KEY"]
    )

    application.add_template_filter(format_time_12_hour, "time12")
    application.add_template_filter(format_gym_date, "gymdate")
    application.add_template_filter(format_stay_duration, "stay_duration")
    application.teardown_appcontext(close_db)
    application.before_request(protect_post_requests)
    application.context_processor(inject_shared_template_values)

    @application.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )
        return response

    @application.cli.command("init-db")
    def initialize_database():
        init_db()

    for blueprint in (
        auth_bp,
        portal_bp,
        dashboard_bp,
        settings_bp,
        members_bp,
        attendance_bp,
        walkin_bp,
        memberships_bp,
        payments_bp,
        reports_bp,
    ):
        application.register_blueprint(blueprint)

    return application


sys.modules.setdefault("app", sys.modules[__name__])

from auth_routes import home, login, logout, register
from portal_routes import (
    forgot_password,
    portal_attendance,
    portal_change_password,
    portal_dashboard,
    portal_login,
    portal_logout,
    portal_membership,
    portal_membership_history,
    portal_payments,
    portal_profile,
    reset_password,
    submit_gcash_payment,
)
from dashboard_routes import dashboard
from settings_routes import (
    change_password,
    download_backup,
    export_settings_csv,
    home_page,
    profile,
    reset_all_gym_data,
    reset_attendance_for_date,
    reset_settings_defaults,
    send_test_email,
    settings,
    upload_gcash_qr,
)
from member_routes import add_member, edit_member, members, remove_member, toggle_member_status
from attendance_routes import attendance
from walkin_routes import (
    walk_in,
    walk_in_leave,
    walk_in_today,
    walk_in_history,
    walkin_member_history,
)
from membership_routes import edit_plan, memberships, remove_membership, toggle_plan
from payment_routes import payments, update_gcash_payment_status
from report_routes import export_report, export_report_pdf, reports

app = create_app()

if __name__ == "__main__":
    init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"})
