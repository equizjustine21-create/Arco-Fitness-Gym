"""Portal routes for the Arco Fitness application."""

import app as _app
from blueprints import portal_bp
from app import (
    DatabaseError,
    DatabaseIntegrityError,
    PROFILE_IMAGE_MAX_BYTES,
    PROFILE_UPLOAD_DIR,
    _gcash_qr_url,
    _image_extension,
    _pagination,
    _portal_member_context,
    _portal_page_context,
    _profile_image_url,
    _valid_checkout_token,
    abort,
    check_password_hash,
    current_app,
    date,
    flash,
    generate_password_hash,
    os,
    portal_required,
    re,
    redirect,
    request,
    secrets,
    session,
    timedelta,
    url_for,
)
from otp_service import issue_otp, send_otp_email

__all__ = [
    "forgot_password",
    "portal_attendance",
    "portal_change_password",
    "portal_dashboard",
    "portal_login",
    "portal_logout",
    "portal_membership",
    "portal_membership_history",
    "mark_member_notification_read",
    "portal_payments",
    "portal_profile",
    "reset_password",
    "reset_password_otp",
    "submit_gcash_payment",
]


@portal_bp.route("/portal/notifications/read", methods=["POST"])
@portal_required
def mark_member_notification_read():
    notification_key = request.form.get("notification_key", "")
    match = re.fullmatch(
        r"payment:([1-9][0-9]*):(pending|confirmed|paid|rejected)",
        notification_key,
    )
    if not match:
        abort(400, description="The notification identifier is invalid.")

    db = _app.get_db()
    db.execute(
        """INSERT IGNORE INTO member_notification_reads
               (member_user_id, notification_key)
           SELECT ?, CONCAT('payment:', p.id, ':', LOWER(p.payment_status))
           FROM payments p
           JOIN members m ON m.id = p.member_id
           WHERE p.id = ? AND m.user_id = ?
             AND LOWER(p.payment_status) = ?""",
        (
            session["user_id"],
            int(match.group(1)),
            session["user_id"],
            match.group(2),
        ),
    )
    db.commit()
    return "", 204


@portal_bp.route("/portal/login", methods=["GET", "POST"])
def portal_login():
    if session.get("role") == "member":
        return redirect(url_for("portal.portal_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = _app.get_db()
        limiter = _app.auth_attempt_limiter()
        bucket_keys = limiter.bucket_keys("login", request.remote_addr, email)
        if limiter.is_limited(db, bucket_keys):
            flash("Invalid email or password. Please wait before trying again.", "error")
            return _app.render_template("portal_login.html")

        user = db.execute(
            """SELECT u.id, u.username, u.password_hash, m.id AS member_id
               FROM users u JOIN members m ON m.user_id = u.id
               WHERE lower(u.email) = ? AND u.role = 'member'
                 AND lower(m.status) = 'active'""",
            (email,),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            limiter.clear_account_bucket(db, bucket_keys)
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "member"
            session["member_id"] = user["member_id"]
            session.permanent = True
            return redirect(url_for("portal.portal_dashboard"))
        limiter.record_failure(db, bucket_keys)
        flash("Invalid email or password.", "error")
    return _app.render_template("portal_login.html")


@portal_bp.route("/portal/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        db = _app.get_db()
        limiter = _app.auth_attempt_limiter()
        bucket_keys = limiter.bucket_keys(
            "email-otp-send", request.remote_addr, email
        )
        session.pop("otp_challenge_id", None)
        session["otp_purpose"] = "password_reset"
        limited = limiter.is_limited(db, bucket_keys)
        if not limited and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            try:
                member = db.execute(
                    """SELECT m.id, u.email
                       FROM members m
                       JOIN users u ON u.id = m.user_id
                       WHERE lower(u.email) = ? AND u.role = 'member'
                         AND lower(m.status) = 'active'
                       LIMIT 1""",
                    (email,),
                ).fetchone()
                if member:
                    challenge_id, code = issue_otp(
                        db,
                        current_app.config["SECRET_KEY"],
                        member["email"],
                        "password_reset",
                        member_id=member["id"],
                    )
                    session["otp_challenge_id"] = challenge_id
                    send_otp_email(member["email"], "password_reset", code)
            except DatabaseError:
                db.rollback()
                current_app.logger.exception(
                    "Password reset verification code could not be created"
                )
        if not limited:
            limiter.record_failure(db, bucket_keys)

        flash(
            "If that email is registered, a verification code has been sent.",
            "success",
        )
        return redirect(url_for("auth.verify_otp"))
    return _app.render_template("forgot_password.html")


@portal_bp.route("/portal/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    return redirect(url_for("portal.forgot_password"))


@portal_bp.route("/portal/reset-password-otp", methods=["GET", "POST"])
def reset_password_otp():
    challenge_id = session.get("otp_challenge_id")
    if session.get("otp_purpose") != "password_reset" or not challenge_id:
        flash("Verify the code sent to your email before resetting your password.", "error")
        return redirect(url_for("portal.forgot_password"))

    db = _app.get_db()
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        password_error = _app._new_password_error(password, confirm)
        if password_error:
            flash(password_error, "error")
            return _app.render_template("password_reset.html", otp_verified=True)

        try:
            db.execute("START TRANSACTION")
            reset = db.execute(
                """SELECT pr.id, pr.member_id, u.id AS user_id,
                          u.password_hash
                   FROM email_otps pr
                   JOIN members m ON m.id = pr.member_id
                   JOIN users u ON u.id = m.user_id
                     AND lower(u.email) = lower(pr.email)
                   WHERE pr.id = ? AND pr.purpose = 'password_reset'
                     AND pr.verified_at IS NOT NULL AND pr.used = 0
                     AND pr.expires_at > UTC_TIMESTAMP()
                     AND u.role = 'member'
                     AND lower(m.status) = 'active'
                   FOR UPDATE""",
                (challenge_id,),
            ).fetchone()
            if reset is None:
                db.rollback()
                session.pop("otp_challenge_id", None)
                session.pop("otp_purpose", None)
                flash("Your verified reset session has expired. Request a new code.", "error")
                return redirect(url_for("portal.forgot_password"))

            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(password), reset["user_id"]),
            )
            db.execute(
                "UPDATE email_otps SET used = 1 WHERE id = ?",
                (challenge_id,),
            )
            db.commit()
        except DatabaseError:
            db.rollback()
            current_app.logger.exception("Member password reset failed")
            flash("The password could not be changed. Please try again.", "error")
            return _app.render_template("password_reset.html", otp_verified=True)

        session.pop("otp_challenge_id", None)
        session.pop("otp_purpose", None)
        flash("Your password has been changed. You can now sign in.", "success")
        return redirect(url_for("portal.portal_login"))

    reset = db.execute(
        """SELECT 1 FROM email_otps
           WHERE id = ? AND purpose = 'password_reset'
             AND verified_at IS NOT NULL AND used = 0
             AND expires_at > UTC_TIMESTAMP()""",
        (challenge_id,),
    ).fetchone()
    if not reset:
        session.pop("otp_challenge_id", None)
        session.pop("otp_purpose", None)
        flash("Your verified reset session has expired. Request a new code.", "error")
        return redirect(url_for("portal.forgot_password"))
    return _app.render_template("password_reset.html", otp_verified=True)


@portal_bp.route("/portal")
@portal_bp.route("/portal/dashboard")
@portal_required
def portal_dashboard():
    context = _portal_member_context()
    if context is None:
        return redirect(url_for("portal.portal_login"))
    context["active_portal_page"] = "overview"
    return _app.render_template("client_dashboard.html", **context)


@portal_bp.route("/portal/membership")
@portal_required
def portal_membership():
    context = _portal_page_context("membership")
    if context is None:
        return redirect(url_for("portal.portal_login"))
    return _app.render_template("client_membership.html", **context)


@portal_bp.route("/portal/membership-history")
@portal_required
def portal_membership_history():
    context = _portal_page_context("membership-history")
    if context is None:
        return redirect(url_for("portal.portal_login"))
    today = _app.philippine_date().isoformat()
    db = _app.get_db()
    member_id = context["member"]["id"]
    pagination = _pagination(db.execute(
        "SELECT COUNT(*) FROM member_memberships WHERE member_id = ?",
        (member_id,),
    ).fetchone()[0])
    context["membership_history"] = _app.get_db().execute(
        """SELECT mm.id, p.name AS plan_name, p.price,
                  mm.start_date, mm.end_date, mm.status,
                  CASE
                    WHEN lower(mm.status) = 'pending' THEN 'Pending'
                    WHEN lower(mm.status) != 'active' THEN mm.status
                    WHEN mm.end_date < ? THEN 'Expired'
                    ELSE 'Active'
                  END AS display_status,
                  COALESCE(membership_payments.paid_amount, 0) AS paid_amount,
                  membership_payments.payment_date,
                  membership_payments.methods
           FROM member_memberships mm
           JOIN membership_plans p ON p.id = mm.plan_id
           LEFT JOIN (
               SELECT mm.id AS membership_id, SUM(pay.amount) AS paid_amount,
                      MAX(pay.payment_date) AS payment_date,
                      GROUP_CONCAT(DISTINCT pay.method) AS methods
               FROM member_memberships mm
               JOIN payments pay
                 ON (pay.membership_id = mm.id
                     OR (pay.membership_id IS NULL AND pay.member_id = mm.member_id
                         AND pay.payment_date BETWEEN mm.start_date AND mm.end_date))
                AND lower(pay.payment_status) = 'confirmed'
               WHERE mm.member_id = ?
               GROUP BY mm.id
           ) membership_payments ON membership_payments.membership_id = mm.id
           WHERE mm.member_id = ?
           ORDER BY mm.start_date DESC, mm.id DESC
           LIMIT ? OFFSET ?""",
        (today, member_id, member_id, pagination["page_size"], pagination["page_offset"]),
    ).fetchall()
    context.update(pagination)
    return _app.render_template("client_membership_history.html", **context)


@portal_bp.route("/portal/attendance")
@portal_required
def portal_attendance():
    context = _portal_page_context("attendance")
    if context is None:
        return redirect(url_for("portal.portal_login"))
    db = _app.get_db()
    member_id = context["member"]["id"]
    pagination = _pagination(db.execute(
        "SELECT COUNT(*) FROM attendance WHERE member_id = ?",
        (member_id,),
    ).fetchone()[0])
    context["attendance"] = db.execute(
        """SELECT checkin_date, checkin_time, checkin_at, checkout_at
           FROM attendance
           WHERE member_id = ?
           ORDER BY checkin_date DESC, checkin_time DESC, id DESC
           LIMIT ? OFFSET ?""",
        (member_id, pagination["page_size"], pagination["page_offset"]),
    ).fetchall()
    context.update(pagination)
    return _app.render_template("client_attendance.html", **context)


@portal_bp.route("/portal/payments")
@portal_required
def portal_payments():
    context = _portal_page_context("payments")
    if context is None:
        return redirect(url_for("portal.portal_login"))
    db = _app.get_db()
    member_id = context["member"]["id"]
    pagination = _pagination(db.execute(
        "SELECT COUNT(*) FROM payments WHERE member_id = ?",
        (member_id,),
    ).fetchone()[0])
    context["payments"] = db.execute(
        """SELECT amount, payment_date, method, note, payment_status,
                  reference_number
           FROM payments
           WHERE member_id = ?
           ORDER BY payment_date DESC, id DESC
           LIMIT ? OFFSET ?""",
        (member_id, pagination["page_size"], pagination["page_offset"]),
    ).fetchall()
    context.update(pagination)
    context["online_plans"] = db.execute(
        """SELECT id, name, duration_days, price
           FROM membership_plans WHERE active = 1
           ORDER BY name COLLATE NOCASE"""
    ).fetchall()
    requested_plan_id = request.args.get("plan_id", type=int)
    available_plan_ids = {plan["id"] for plan in context["online_plans"]}
    context["selected_plan_id"] = (
        requested_plan_id
        if requested_plan_id in available_plan_ids
        else None
    )
    context["renewal_mode"] = request.args.get("renew") == "1"
    context["gcash_qr_url"] = _gcash_qr_url(
        _app.setting_values(db).get("gcash_qr_filename", "")
    )
    context["checkout_token"] = secrets.token_urlsafe(32)
    return _app.render_template("client_payments.html", **context)


@portal_bp.route("/portal/payments/gcash", methods=["POST"])
@portal_required
def submit_gcash_payment():
    db = _app.get_db()
    qr_filename = _app.setting_values(db).get("gcash_qr_filename", "")
    if not _gcash_qr_url(qr_filename):
        flash("Online payment is not available yet.", "error")
        return redirect(url_for("portal.portal_payments"))

    plan_id = request.form.get("plan_id", type=int)
    reference_number = request.form.get("reference_number", "").strip()
    checkout_token = request.form.get("checkout_token", "")
    if (not plan_id or len(reference_number) < 6 or len(reference_number) > 100
            or not _valid_checkout_token(checkout_token)):
        flash("Choose a membership plan and enter a GCash reference number of 6 to 100 characters.", "error")
        return redirect(url_for("portal.portal_payments"))

    plan = db.execute(
        """SELECT id, name, duration_days, price FROM membership_plans
           WHERE id = ? AND active = 1""",
        (plan_id,),
    ).fetchone()
    member = db.execute(
        "SELECT id FROM members WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()
    if plan is None or member is None:
        flash("The selected membership plan is unavailable.", "error")
        return redirect(url_for("portal.portal_payments"))

    today = _app.philippine_date()
    try:
        db.execute("START TRANSACTION")
        db.execute(
            "SELECT id FROM members WHERE id = ? FOR UPDATE",
            (member["id"],),
        )
        pending_payment = db.execute(
            """SELECT id FROM payments
               WHERE member_id = ? AND method = 'GCash'
                 AND lower(payment_status) = 'pending'
               LIMIT 1""",
            (member["id"],),
        ).fetchone()
        if pending_payment:
            db.rollback()
            flash("You already have a pending GCash payment. Please wait for admin review.", "error")
            return redirect(url_for("portal.portal_payments"))
        duplicate_reference = db.execute(
            """SELECT id FROM payments
               WHERE method = 'GCash' AND reference_number = ?
               LIMIT 1""",
            (reference_number,),
        ).fetchone()
        if duplicate_reference:
            db.rollback()
            flash("That GCash reference number has already been submitted.", "error")
            return redirect(url_for("portal.portal_payments"))
        current_membership = db.execute(
            """SELECT end_date FROM member_memberships
               WHERE member_id = ? AND lower(status) = 'active' AND end_date >= ?
               ORDER BY end_date DESC, id DESC LIMIT 1""",
            (member["id"], today.isoformat()),
        ).fetchone()
        start_date = today
        if current_membership:
            start_date = max(
                today, date.fromisoformat(current_membership["end_date"]) + timedelta(days=1)
            )
        end_date = start_date + timedelta(days=plan["duration_days"] - 1)
        membership_result = db.execute(
            """INSERT INTO member_memberships
               (member_id, plan_id, start_date, end_date, status)
               VALUES (?, ?, ?, ?, 'Pending')""",
            (
                member["id"], plan["id"], start_date.isoformat(),
                end_date.isoformat(),
            ),
        )
        payment_timestamp = _app.philippine_now()
        payment_result = db.execute(
            """INSERT INTO payments
               (member_id, amount, payment_date, paid_at, method, note, membership_id,
                payment_status, reference_number, checkout_token)
               VALUES (?, ?, ?, ?, 'GCash', ?, ?, 'Pending', ?, ?)""",
            (
                member["id"], plan["price"], today.isoformat(), payment_timestamp,
                f"Online GCash membership payment: {plan['name']}",
                membership_result.lastrowid, reference_number, checkout_token,
            ),
        )
        _app._assign_payment_receipt(db, payment_result.lastrowid, payment_timestamp)
        db.commit()
    except DatabaseIntegrityError:
        db.rollback()
        existing_payment = db.execute(
            "SELECT id FROM payments WHERE checkout_token = ?",
            (checkout_token,),
        ).fetchone()
        if existing_payment:
            flash("This payment was already submitted.", "success")
            return redirect(url_for("portal.portal_payments"))
        current_app.logger.exception("Online GCash payment could not be submitted")
        flash("Your payment could not be submitted. Please try again.", "error")
        return redirect(url_for("portal.portal_payments"))
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("Online GCash payment could not be submitted")
        flash("Your payment could not be submitted. Please try again.", "error")
        return redirect(url_for("portal.portal_payments"))

    flash(
        "Payment submitted. Waiting for confirmation. Your membership becomes Active "
        "once the admin confirms the GCash payment.",
        "success",
    )
    return redirect(url_for("portal.portal_payments"))


@portal_bp.route("/portal/profile", methods=["GET", "POST"])
@portal_required
def portal_profile():
    db = _app.get_db()
    user_id = session["user_id"]
    member = db.execute(
        "SELECT * FROM members WHERE user_id = ?", (user_id,)
    ).fetchone()
    if member is None:
        session.clear()
        flash("Your member account is not linked to a gym record.", "error")
        return redirect(url_for("portal.portal_login"))

    user = db.execute(
        "SELECT profile_photo FROM users WHERE id = ? AND role = 'member'",
        (user_id,),
    ).fetchone()
    if user is None:
        session.clear()
        flash("Your member account could not be found. Please sign in again.", "error")
        return redirect(url_for("portal.portal_login"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        remove_photo = request.form.get("remove_photo") == "1"
        image = request.files.get("profile_photo")
        image_data = None
        image_extension = None
        error = None

        if not full_name or len(full_name) > 255:
            error = "Enter your full name (up to 255 characters)."
        elif len(email) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            error = "Enter a valid email address."
        elif not phone or len(phone) > 50:
            error = "Enter a phone number of 50 characters or fewer."
        elif db.execute(
            "SELECT 1 FROM members WHERE lower(email) = lower(?) AND id != ?",
            (email, member["id"]),
        ).fetchone():
            error = "That email address is already in use."
        elif db.execute(
            "SELECT 1 FROM users WHERE lower(email) = lower(?) AND id != ?",
            (email, user_id),
        ).fetchone():
            error = "That email address is already in use."

        if error is None and image is not None and image.filename:
            image_data = image.stream.read(PROFILE_IMAGE_MAX_BYTES + 1)
            if len(image_data) > PROFILE_IMAGE_MAX_BYTES:
                error = "Profile photo must be 5 MB or smaller."
            else:
                image_extension = _image_extension(image_data)
                if image_extension is None:
                    error = "Use a JPG, PNG, or WebP profile photo."

        if error:
            flash(error, "error")
        else:
            new_filename = (
                f"{secrets.token_hex(16)}{image_extension}"
                if image_data is not None and image_extension
                else None
            )
            old_filename = user["profile_photo"] or ""
            next_filename = (
                new_filename if new_filename
                else "" if remove_photo
                else old_filename
            )
            new_path = None
            try:
                if new_filename and image_data is not None:
                    os.makedirs(PROFILE_UPLOAD_DIR, exist_ok=True)
                    new_path = os.path.join(PROFILE_UPLOAD_DIR, new_filename)
                    with open(new_path, "xb") as image_file:
                        image_file.write(image_data)
                db.execute(
                    """UPDATE members SET full_name = ?, email = ?, phone = ?
                       WHERE id = ? AND user_id = ?""",
                    (full_name, email, phone, member["id"], user_id),
                )
                db.execute(
                    """UPDATE users SET full_name = ?, email = ?, phone = ?,
                       profile_photo = ? WHERE id = ? AND role = 'member'""",
                    (full_name, email, phone, next_filename, user_id),
                )
                db.commit()
            except DatabaseIntegrityError:
                db.rollback()
                if new_path and os.path.isfile(new_path):
                    try:
                        os.remove(new_path)
                    except OSError:
                        current_app.logger.exception(
                            "Could not remove a member profile photo after save failure"
                        )
                flash("That email address is already in use.", "error")
            except (OSError, DatabaseError):
                db.rollback()
                if new_path and os.path.isfile(new_path):
                    try:
                        os.remove(new_path)
                    except OSError:
                        current_app.logger.exception(
                            "Could not remove a member profile photo after save failure"
                        )
                current_app.logger.exception("Member profile could not be saved")
                flash("Profile could not be saved. Please try again.", "error")
            else:
                if old_filename and old_filename != next_filename and re.fullmatch(
                    r"[a-f0-9]{32}\.(?:jpg|png|webp)", old_filename
                ):
                    old_path = os.path.join(PROFILE_UPLOAD_DIR, old_filename)
                    try:
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                    except OSError:
                        current_app.logger.exception(
                            "Could not remove a replaced member profile photo"
                        )
                flash("Profile updated.", "success")
                return redirect(url_for("portal.portal_profile"))

    return _app.render_template(
        "client_profile.html", member=member,
        profile_photo_url=_profile_image_url(user["profile_photo"]),
        current_year=_app.philippine_now().year,
        active_portal_page="settings",
    )


@portal_bp.route("/portal/logout", methods=["POST"])
def portal_logout():
    session.clear()
    return redirect(url_for("auth.home"))


@portal_bp.route("/portal/password", methods=["POST"])
@portal_required
def portal_change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    db = _app.get_db()
    user = db.execute(
        "SELECT password_hash FROM users WHERE id = ? AND role = 'member'",
        (session["user_id"],),
    ).fetchone()

    if user is None:
        session.clear()
        flash("Your member account could not be found. Please sign in again.", "error")
        return redirect(url_for("portal.portal_login"))
    if not check_password_hash(user["password_hash"], current_password):
        flash("Your current password is incorrect.", "error")
    elif password_error := _app._new_password_error(
        new_password,
        confirm_password,
        current_password_hash=user["password_hash"],
    ):
        flash(password_error, "error")
    else:
        try:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ? AND role = 'member'",
                (generate_password_hash(new_password), session["user_id"]),
            )
            db.commit()
        except DatabaseError:
            db.rollback()
            current_app.logger.exception("Member password could not be updated")
            flash("Password could not be updated. Please try again.", "error")
        else:
            flash("Password updated.", "success")

    return redirect(
        url_for("portal.portal_profile", _anchor="member-password-settings")
    )
