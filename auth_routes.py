"""Auth routes for the Arco Fitness application."""

from flask import make_response

import app as _app
from blueprints import auth_bp
from domain_models import User
from app import (
    DatabaseError,
    DatabaseIntegrityError,
    _home_image_url,
    flash,
    generate_password_hash,
    re,
    redirect,
    request,
    session,
    url_for,
)
from otp_service import (
    OTP_MAX_ATTEMPTS,
    issue_otp,
    send_otp_email,
    verify_otp as validate_otp,
)

__all__ = [
    "home",
    "login",
    "logout",
    "register",
    "resend_otp",
    "verify_otp",
]


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(
            url_for("portal.portal_dashboard")
            if session.get("role") == "member" else url_for("dashboard.dashboard")
        )

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = _app.get_db()
        limiter = _app.auth_attempt_limiter()
        bucket_keys = limiter.bucket_keys(
            "login", request.remote_addr, username
        )
        if limiter.is_limited(db, bucket_keys):
            flash("Invalid username or password. Please wait before trying again.", "error")
            return _app.render_template("login.html")

        user_record = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        user = User.from_record(user_record) if user_record else None
        if user and user.is_admin and user.verify_password(password):
            limiter.clear_account_bucket(db, bucket_keys)
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            session.permanent = True
            return redirect(url_for("dashboard.dashboard"))
        limiter.record_failure(db, bucket_keys)
        flash("Invalid username or password.", "error")

    return _app.render_template("login.html")


@auth_bp.route("/")
def home():
    values = _app.setting_values()
    editor_preview = (
        request.args.get("editor_preview") == "1"
        and session.get("role") == "admin"
    )
    response = make_response(
        _app.render_template(
            "home.html",
            home_settings=values,
            editor_preview=editor_preview,
            home_images={
                "hero": _home_image_url(values["home_hero_image"]),
                "cardio": _home_image_url(values["home_facility_cardio_image"]),
                "weights": _home_image_url(values["home_facility_weights_image"]),
                "machines": _home_image_url(values["home_facility_machines_image"]),
            },
        )
    )
    if editor_preview:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _otp_redirect(purpose):
    return redirect(
        url_for("portal.forgot_password")
        if purpose == "password_reset"
        else url_for("auth.register")
    )


def _masked_email(email):
    local, separator, domain = email.partition("@")
    if not separator:
        return ""
    return f"{local[:1]}{'*' * min(max(len(local) - 1, 2), 8)}@{domain}"


@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    purpose = session.get("otp_purpose", "")
    challenge_id = session.get("otp_challenge_id")
    database = _app.get_db()
    challenge = None
    if challenge_id and purpose in {"registration", "password_reset"}:
        challenge = database.execute(
            """SELECT id, email, purpose, code_hash, expires_at, attempts, used,
                      verified_at, member_id, username, password_hash
               FROM email_otps WHERE id = ? AND purpose = ?""",
            (challenge_id, purpose),
        ).fetchone()

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        now = _app.datetime.now(_app.timezone.utc).replace(tzinfo=None)
        if not challenge or challenge["used"] or challenge["expires_at"] <= now:
            flash("This verification code is invalid or has expired. Request a new code.", "error")
            return _otp_redirect(purpose)

        limiter = _app.auth_attempt_limiter()
        bucket_keys = limiter.bucket_keys(
            "email-otp-verify", request.remote_addr, challenge["email"]
        )
        if limiter.is_limited(database, bucket_keys):
            flash("Too many code attempts. Please wait before trying again.", "error")
            return _otp_redirect(purpose)

        database.execute("START TRANSACTION")
        challenge = database.execute(
            """SELECT id, email, purpose, code_hash, expires_at, attempts, used,
                      verified_at, member_id, username, password_hash
               FROM email_otps WHERE id = ? AND purpose = ? FOR UPDATE""",
            (challenge_id, purpose),
        ).fetchone()
        if (
            not challenge
            or challenge["used"]
            or challenge["expires_at"] <= now
            or challenge["attempts"] >= OTP_MAX_ATTEMPTS
        ):
            database.rollback()
            flash("This verification code is invalid or has expired. Request a new code.", "error")
            return _otp_redirect(purpose)

        if not validate_otp(
            _app.current_app.config["SECRET_KEY"],
            challenge["email"],
            purpose,
            code,
            challenge["code_hash"],
        ):
            attempts = challenge["attempts"] + 1
            database.execute(
                """UPDATE email_otps SET attempts = ?,
                          used = IF(? >= ?, 1, used)
                   WHERE id = ?""",
                (attempts, attempts, OTP_MAX_ATTEMPTS, challenge_id),
            )
            if attempts >= OTP_MAX_ATTEMPTS:
                database.execute(
                    """UPDATE email_otps SET username = NULL, password_hash = NULL,
                              member_id = IF(purpose = 'registration', NULL, member_id)
                       WHERE id = ?""",
                    (challenge_id,),
                )
            database.commit()
            limiter.record_failure(database, bucket_keys)
            flash("The verification code is incorrect or expired.", "error")
            return _otp_redirect(purpose)

        if purpose == "registration":
            try:
                member = database.execute(
                    """SELECT id, email, user_id FROM members
                       WHERE id = ? AND lower(email) = ?
                         AND lower(status) = 'active'
                       FOR UPDATE""",
                    (challenge["member_id"], challenge["email"]),
                ).fetchone()
                existing_user = database.execute(
                    """SELECT id FROM users
                       WHERE username = ? OR lower(email) = ? LIMIT 1""",
                    (challenge["username"], challenge["email"]),
                ).fetchone()
                if not member or member["user_id"] is not None or existing_user:
                    database.execute(
                        """UPDATE email_otps SET used = 1, username = NULL,
                                  password_hash = NULL, member_id = NULL
                           WHERE id = ?""",
                        (challenge_id,),
                    )
                    database.commit()
                    session.pop("otp_challenge_id", None)
                    session.pop("otp_purpose", None)
                    flash(
                        "The member record or display name is no longer available. "
                        "Please start registration again or contact the gym.",
                        "error",
                    )
                    return redirect(url_for("auth.register"))

                user_id = database.execute(
                    """INSERT INTO users
                       (username, password_hash, role, email)
                       VALUES (?, ?, 'member', ?)""",
                    (
                        challenge["username"],
                        challenge["password_hash"],
                        challenge["email"],
                    ),
                ).lastrowid
                updated = database.execute(
                    "UPDATE members SET user_id = ? WHERE id = ? AND user_id IS NULL",
                    (user_id, member["id"]),
                )
                if updated.rowcount != 1:
                    raise DatabaseIntegrityError(
                        "Member account link was already claimed."
                    )
                database.execute(
                    """UPDATE email_otps SET used = 1, username = NULL,
                              password_hash = NULL, member_id = NULL
                       WHERE id = ?""",
                    (challenge_id,),
                )
                database.commit()
            except DatabaseIntegrityError:
                database.rollback()
                _app.current_app.logger.exception(
                    "Verified member account could not be created"
                )
                flash(
                    "That display name or email is already in use. Please start again.",
                    "error",
                )
                return redirect(url_for("auth.register"))
            except DatabaseError:
                database.rollback()
                _app.current_app.logger.exception(
                    "Verified member account could not be created"
                )
                flash("Your account could not be created. Please try again.", "error")
                return _otp_redirect(purpose)

            limiter.clear_account_bucket(database, bucket_keys)
            session.clear()
            session["user_id"] = user_id
            session["username"] = challenge["username"]
            session["role"] = "member"
            session["member_id"] = member["id"]
            session.permanent = True
            flash("Email verified. Your account is ready.", "success")
            return redirect(url_for("portal.portal_dashboard"))

        database.execute(
            "UPDATE email_otps SET verified_at = UTC_TIMESTAMP() WHERE id = ?",
            (challenge_id,),
        )
        database.commit()
        limiter.clear_account_bucket(database, bucket_keys)
        flash("Email verified. Choose a new password.", "success")
        return redirect(url_for("portal.reset_password_otp"))

    display_email = (
        _masked_email(challenge["email"])
        if challenge and purpose == "registration"
        else None
    )
    return _app.render_template(
        "verify_otp.html",
        purpose=purpose,
        email=display_email,
        can_resend=bool(challenge and not challenge["used"]),
    )


@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():
    challenge_id = session.get("otp_challenge_id")
    purpose = session.get("otp_purpose", "")
    if not challenge_id or purpose not in {"registration", "password_reset"}:
        flash("Request a new verification code first.", "error")
        return _otp_redirect(purpose)

    database = _app.get_db()
    challenge = database.execute(
        """SELECT email, purpose, used, member_id, username, password_hash
           FROM email_otps WHERE id = ? AND purpose = ?""",
        (challenge_id, purpose),
    ).fetchone()
    if not challenge or challenge["used"]:
        flash("This verification request is no longer available. Start again.", "error")
        session.pop("otp_challenge_id", None)
        session.pop("otp_purpose", None)
        return _otp_redirect(purpose)

    limiter = _app.auth_attempt_limiter()
    bucket_keys = limiter.bucket_keys(
        "email-otp-send", request.remote_addr, challenge["email"]
    )
    if limiter.is_limited(database, bucket_keys):
        flash("Too many code requests. Please wait before requesting another.", "error")
        return redirect(url_for("auth.verify_otp"))

    try:
        new_challenge_id, code = issue_otp(
            database,
            _app.current_app.config["SECRET_KEY"],
            challenge["email"],
            purpose,
            member_id=challenge["member_id"],
            username=challenge["username"],
            password_hash=challenge["password_hash"],
        )
    except DatabaseError:
        database.rollback()
        _app.current_app.logger.exception("Verification code could not be resent")
        flash("A new code could not be sent. Please try again.", "error")
        return redirect(url_for("auth.verify_otp"))

    session["otp_challenge_id"] = new_challenge_id
    limiter.record_failure(database, bucket_keys)
    if not send_otp_email(challenge["email"], purpose, code):
        flash("The code could not be queued. Please try again later.", "error")
        return redirect(url_for("auth.verify_otp"))
    flash("A new verification code has been requested. Check your email.", "success")
    return redirect(url_for("auth.verify_otp"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(
            url_for("portal.portal_dashboard")
            if session.get("role") == "member" else url_for("dashboard.dashboard")
        )

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        error = None
        if not 3 <= len(username) <= 80:
            error = "Display name must be between 3 and 80 characters."
        elif len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            error = "Enter the email address on your gym member record."
        else:
            error = _app._new_password_error(password, confirm)

        if error is None:
            db = _app.get_db()
            matching_members = db.execute(
                """SELECT m.id, m.user_id, m.full_name
                   FROM members m
                   WHERE lower(m.email) = ? AND lower(m.status) = 'active'""",
                (email,),
            ).fetchall()
            if not matching_members:
                error = "An active member record with this email must be added by the gym before you can create an account."
            elif len(matching_members) > 1:
                error = "More than one member record matches that email. Please contact the gym."
            elif matching_members[0]["user_id"] is not None:
                error = "An account is already linked to this member. Please sign in."
            else:
                member = matching_members[0]

        if error is None:
            try:
                existing_user = db.execute(
                    """SELECT id FROM users
                       WHERE username = ? OR lower(email) = ? LIMIT 1""",
                    (username, email),
                ).fetchone()
                if existing_user:
                    error = "That user name or email is already in use."
                else:
                    limiter = _app.auth_attempt_limiter()
                    bucket_keys = limiter.bucket_keys(
                        "email-otp-send", request.remote_addr, email
                    )
                    if limiter.is_limited(db, bucket_keys):
                        error = "Too many verification code requests. Please wait before trying again."
                    else:
                        challenge_id, code = issue_otp(
                            db,
                            _app.current_app.config["SECRET_KEY"],
                            email,
                            "registration",
                            member_id=member["id"],
                            username=username,
                            password_hash=generate_password_hash(password),
                        )
                        session["otp_challenge_id"] = challenge_id
                        session["otp_purpose"] = "registration"
                        queued = send_otp_email(email, "registration", code)
                        limiter.record_failure(db, bucket_keys)
                        if not queued:
                            flash(
                                "Email could not be queued. Please check the email settings and try again.",
                                "error",
                            )
                            return redirect(url_for("auth.verify_otp"))
                        flash(
                            "Enter the 6-digit code sent to your email to finish creating your account.",
                            "success",
                        )
                        return redirect(url_for("auth.verify_otp"))
            except DatabaseIntegrityError:
                db.rollback()
                error = "That user name or email is already in use."
            except DatabaseError:
                db.rollback()
                _app.current_app.logger.exception(
                    "Registration verification code could not be created"
                )
                error = "A verification code could not be sent. Please try again."

        flash(error, "error")

    return _app.render_template("register.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.home"))
