"""Settings routes for the Arco Fitness application."""

import app as _app
from blueprints import settings_bp
from app import (
    DEFAULT_SETTINGS,
    DatabaseError,
    DatabaseIntegrityError,
    GCASH_QR_MAX_BYTES,
    GCASH_QR_UPLOAD_DIR,
    HOME_IMAGE_MAX_BYTES,
    HOME_UPLOAD_DIR,
    PROFILE_IMAGE_MAX_BYTES,
    PROFILE_UPLOAD_DIR,
    Response,
    StringIO,
    ZoneInfo,
    ZoneInfoNotFoundError,
    _database_backup,
    _gcash_qr_url,
    _home_image_url,
    _image_extension,
    _money_value,
    _profile_image_url,
    _record_backup,
    _safe_csv_value,
    _save_setting_values,
    _settings_redirect,
    _valid_date,
    _verify_admin_password,
    abort,
    admin_required,
    check_password_hash,
    csv,
    current_app,
    flash,
    g,
    generate_password_hash,
    os,
    re,
    redirect,
    request,
    secrets,
    secure_filename,
    send_file,
    session,
    time,
    urlsplit,
    url_for,
)

__all__ = ['change_password', 'download_backup', 'export_settings_csv', 'home_page', 'profile', 'reset_all_gym_data', 'reset_attendance_for_date', 'reset_settings_defaults', 'send_test_email', 'settings', 'upload_gcash_qr']


@settings_bp.route("/settings/home-page", methods=["GET", "POST"])
@admin_required
def home_page():
    db = _app.get_db()
    if request.method == "POST":
        values = {
            "home_hero_line1": request.form.get("home_hero_line1", "").strip(),
            "home_hero_line2": request.form.get("home_hero_line2", "").strip(),
            "home_hero_description": request.form.get("home_hero_description", "").strip(),
            "home_hero_note": request.form.get("home_hero_note", "").strip(),
            "home_opening_hours": request.form.get("home_opening_hours", "").strip(),
            "home_active_members": request.form.get("home_active_members", "").strip(),
            "home_facility_cardio_title": request.form.get("home_facility_cardio_title", "").strip(),
            "home_facility_weights_title": request.form.get("home_facility_weights_title", "").strip(),
            "home_facility_machines_title": request.form.get("home_facility_machines_title", "").strip(),
            "gym_address": request.form.get("gym_address", "").strip(),
            "contact_number": request.form.get("contact_number", "").strip(),
            "gym_email": (
                request.form.get("gym_email", "").strip()
                if "gym_email" in request.form
                else _app.setting_values()["gym_email"]
            ),
            "home_facebook_url": request.form.get("home_facebook_url", "").strip(),
        }
        max_lengths = {
            "home_hero_line1": 60,
            "home_hero_line2": 60,
            "home_hero_description": 300,
            "home_hero_note": 120,
            "home_opening_hours": 80,
            "home_active_members": 40,
            "home_facility_cardio_title": 60,
            "home_facility_weights_title": 60,
            "home_facility_machines_title": 60,
            "gym_address": 250,
            "contact_number": 40,
            "gym_email": 254,
            "home_facebook_url": 500,
        }
        invalid = [
            label for key, label in (
                ("home_hero_line1", "First headline line"),
                ("home_hero_line2", "Second headline line"),
                ("home_hero_description", "Hero description"),
                ("home_hero_note", "Hero note"),
                ("home_opening_hours", "Opening hours"),
                ("home_active_members", "Active member text"),
                ("home_facility_cardio_title", "Cardio facility title"),
                ("home_facility_weights_title", "Free weights facility title"),
                ("home_facility_machines_title", "Machines facility title"),
            )
            if not values[key] or len(values[key]) > max_lengths[key]
        ]
        invalid.extend(
            label for key, label in (
                ("gym_address", "Gym address"),
                ("contact_number", "Contact number"),
                ("gym_email", "Email address"),
                ("home_facebook_url", "Facebook URL"),
            )
            if len(values[key]) > max_lengths[key]
        )
        email = values["gym_email"]
        if email and not re.fullmatch(
                r"[^@\s&?#:/;=]+@[^@\s&?#:/;=]+\.[^@\s&?#:/;=]+", email):
            invalid.append("Email address")

        facebook_url = values["home_facebook_url"]
        try:
            parsed_facebook_url = urlsplit(facebook_url)
            valid_facebook_url = (
                parsed_facebook_url.scheme in {"http", "https"}
                and bool(parsed_facebook_url.hostname)
                and parsed_facebook_url.username is None
                and parsed_facebook_url.password is None
            )
        except ValueError:
            valid_facebook_url = False
        if facebook_url and not valid_facebook_url:
            invalid.append("Facebook URL (must start with http:// or https://)")

        upload_fields = {
            "home_hero_image": "hero_image",
            "home_facility_cardio_image": "cardio_image",
            "home_facility_weights_image": "weights_image",
            "home_facility_machines_image": "machines_image",
        }
        uploads = {}
        for setting_key, field_name in upload_fields.items():
            image = request.files.get(field_name)
            if image is None or not image.filename:
                continue
            data = image.stream.read(HOME_IMAGE_MAX_BYTES + 1)
            extension = _image_extension(data)
            if len(data) > HOME_IMAGE_MAX_BYTES:
                invalid.append(f"{field_name.replace('_', ' ').title()} (maximum 5 MB)")
            elif extension is None:
                invalid.append(f"{field_name.replace('_', ' ').title()} (use a JPG, PNG, or WebP image)")
            else:
                uploads[setting_key] = (
                    f"{secrets.token_hex(16)}{extension}",
                    data,
                )

        if invalid:
            flash("Please correct these fields: " + "; ".join(invalid) + ".", "error")
            return redirect(url_for("settings.home_page"))

        current_values = _app.setting_values()
        remove_images = set()
        for setting_key, field_name in upload_fields.items():
            if request.form.get(f"remove_{field_name}") == "1":
                values[setting_key] = ""
                remove_images.add(current_values[setting_key])
            if setting_key in uploads:
                values[setting_key] = uploads[setting_key][0]
                remove_images.add(current_values[setting_key])

        created_files = []
        try:
            os.makedirs(HOME_UPLOAD_DIR, exist_ok=True)
            for filename, data in uploads.values():
                image_path = os.path.join(HOME_UPLOAD_DIR, filename)
                with open(image_path, "xb") as image_file:
                    image_file.write(data)
                created_files.append(image_path)
        except OSError:
            for image_path in created_files:
                try:
                    os.remove(image_path)
                except OSError:
                    current_app.logger.exception("Could not remove an incomplete landing-page upload")
            current_app.logger.exception("Landing-page image upload failed")
            flash("The image could not be saved. Check the static/uploads folder permissions.", "error")
            return redirect(url_for("settings.home_page"))

        try:
            _save_setting_values(db, values)
            db.commit()
        except DatabaseError:
            db.rollback()
            for image_path in created_files:
                try:
                    os.remove(image_path)
                except OSError:
                    current_app.logger.exception("Could not clean up a landing-page upload after save failure")
            current_app.logger.exception("Landing-page settings could not be saved")
            flash("The landing-page changes could not be saved. Please try again.", "error")
            return redirect(url_for("settings.home_page"))

        for filename in remove_images:
            if _home_image_url(filename) is None:
                continue
            old_path = os.path.join(HOME_UPLOAD_DIR, filename)
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except OSError:
                current_app.logger.exception("Could not remove an old landing-page image")
        g._settings_values = {**current_values, **values}
        flash("Landing page updated.", "success")
        return redirect(url_for("settings.home_page"))

    values = _app.setting_values()
    return _app.render_template(
        "home_page_settings.html",
        home_settings=values,
        home_images={
            "hero": _home_image_url(values["home_hero_image"]),
            "cardio": _home_image_url(values["home_facility_cardio_image"]),
            "weights": _home_image_url(values["home_facility_weights_image"]),
            "machines": _home_image_url(values["home_facility_machines_image"]),
        },
        current_year=_app.philippine_now().year,
    )


@settings_bp.route("/settings/profile", methods=["POST"])
@admin_required
def profile():
    db = _app.get_db()
    user_id = session["user_id"]
    user = db.execute(
        """SELECT id, email, full_name, phone, profile_photo
           FROM users WHERE id = ? AND role = 'admin'""",
        (user_id,),
    ).fetchone()
    if user is None:
        abort(403)

    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    remove_photo = request.form.get("remove_photo") == "1"
    image = request.files.get("profile_photo")
    image_data = None
    image_extension = None

    if not full_name or len(full_name) > 255:
        flash("Enter your full name (up to 255 characters).", "error")
        return redirect(url_for("settings.settings", section="profile"))
    if (len(email) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("settings.settings", section="profile"))
    if len(phone) > 50:
        flash("Phone number must be 50 characters or fewer.", "error")
        return redirect(url_for("settings.settings", section="profile"))

    duplicate_email = db.execute(
        """SELECT id FROM users
           WHERE lower(email) = lower(?) AND id != ? LIMIT 1""",
        (email, user_id),
    ).fetchone()
    if duplicate_email:
        flash("That email address is already in use.", "error")
        return redirect(url_for("settings.settings", section="profile"))

    if image is not None and image.filename:
        image_data = image.stream.read(PROFILE_IMAGE_MAX_BYTES + 1)
        if len(image_data) > PROFILE_IMAGE_MAX_BYTES:
            flash("Profile photo must be 5 MB or smaller.", "error")
            return redirect(url_for("settings.settings", section="profile"))
        image_extension = _image_extension(image_data)
        if image_extension is None:
            flash("Use a JPG, PNG, or WebP profile photo.", "error")
            return redirect(url_for("settings.settings", section="profile"))

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
            """UPDATE users SET full_name = ?, email = ?, phone = ?,
               profile_photo = ? WHERE id = ? AND role = 'admin'""",
            (full_name, email, phone, next_filename, user_id),
        )
        db.commit()
    except DatabaseIntegrityError:
        db.rollback()
        if new_path and os.path.isfile(new_path):
            try:
                os.remove(new_path)
            except OSError:
                current_app.logger.exception("Could not remove a profile photo after a duplicate-email failure")
        flash("That email address is already in use.", "error")
        return redirect(url_for("settings.settings", section="profile"))
    except (OSError, DatabaseError):
        db.rollback()
        if new_path and os.path.isfile(new_path):
            try:
                os.remove(new_path)
            except OSError:
                current_app.logger.exception("Could not remove a profile photo after save failure")
        current_app.logger.exception("Profile settings could not be saved")
        flash("Profile settings could not be saved. Please try again.", "error")
        return redirect(url_for("settings.settings", section="profile"))

    if old_filename and old_filename != next_filename:
        old_path = os.path.join(PROFILE_UPLOAD_DIR, old_filename)
        if re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|webp)", old_filename):
            try:
                if os.path.isfile(old_path):
                    os.remove(old_path)
            except OSError:
                current_app.logger.exception("Could not remove a replaced profile photo")

    flash("Profile saved.", "success")
    return redirect(url_for("settings.settings", section="profile"))


@settings_bp.route("/settings/password", methods=["POST"])
@admin_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    if not _verify_admin_password(current_password):
        flash("Your current password is incorrect.", "error")
    else:
        user = _app.get_db().execute(
            "SELECT password_hash FROM users WHERE id = ? AND role = 'admin'",
            (session["user_id"],),
        ).fetchone()
        if user is None:
            abort(403)
        password_error = _app._new_password_error(
            new_password,
            confirm_password,
            current_password_hash=user["password_hash"],
        )
        if password_error:
            flash(password_error, "error")
        else:
            try:
                _app.get_db().execute(
                    "UPDATE users SET password_hash = ? WHERE id = ? AND role = 'admin'",
                    (generate_password_hash(new_password), session["user_id"]),
                )
                _app.get_db().commit()
            except DatabaseError:
                _app.get_db().rollback()
                current_app.logger.exception("Administrator password could not be updated")
                flash("Password could not be updated. Please try again.", "error")
            else:
                flash("Password updated.", "success")
    return redirect(url_for("settings.settings", section="profile"))


@settings_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    settings_sections = {
        "overview": "Settings",
        "profile": "My profile",
        "email": "Email delivery",
        "gym": "Gym information",
        "pricing": "Membership and pricing",
        "gcash": "GCash QR",
        "attendance": "Attendance and display",
        "notifications": "Notifications and reminders",
        "data": "Backup and export",
        "danger": "Reset and maintenance",
    }
    settings_section = request.args.get("section", "overview")
    if settings_section not in settings_sections:
        abort(404)
    db = _app.get_db()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "account":
            username = request.form.get("username", "").strip()
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not _verify_admin_password(current_password):
                flash("Your current password is incorrect.", "error")
            elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", username):
                flash("Username must be 3–80 characters using letters, numbers, dots, underscores, or hyphens.", "error")
            else:
                user = db.execute(
                    "SELECT password_hash FROM users WHERE id = ? AND role = 'admin'",
                    (session["user_id"],),
                ).fetchone()
                if user is None:
                    abort(403)
                password_error = (
                    _app._new_password_error(
                        new_password,
                        confirm_password,
                        current_password_hash=user["password_hash"],
                    )
                    if new_password
                    else (
                        "The new passwords do not match."
                        if confirm_password else None
                    )
                )
                if password_error:
                    flash(password_error, "error")
                else:
                    password_hash = (
                        generate_password_hash(new_password)
                        if new_password else user["password_hash"]
                    )
                    try:
                        db.execute(
                            "UPDATE users SET username = ?, password_hash = ? WHERE id = ?",
                            (username, password_hash, session["user_id"]),
                        )
                        db.commit()
                    except DatabaseIntegrityError:
                        db.rollback()
                        flash("That username is already in use.", "error")
                    else:
                        session["username"] = username
                        flash("Account settings saved.", "success")
                        return _settings_redirect("profile")
        elif action == "gym":
            name = request.form.get("gym_name", "").strip()
            phone = request.form.get("contact_number", "").strip()
            email = request.form.get("gym_email", "").strip()
            address = request.form.get("gym_address", "").strip()
            opening = request.form.get("opening_time", "").strip()
            closing = request.form.get("closing_time", "").strip()
            days = request.form.getlist("operating_days")
            valid_days = {
                "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday",
            }
            try:
                time.fromisoformat(opening)
                time.fromisoformat(closing)
                valid_times = True
            except ValueError:
                valid_times = False
            if not name or len(name) > 100:
                flash("Enter a gym name of up to 100 characters.", "error")
            elif len(phone) > 40 or len(address) > 250:
                flash("Contact number or address is too long.", "error")
            elif email and (len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)):
                flash("Enter a valid gym email address.", "error")
            elif not valid_times:
                flash("Choose valid opening and closing times.", "error")
            elif not days or any(day not in valid_days for day in days):
                flash("Select at least one valid operating day.", "error")
            else:
                _save_setting_values(db, {
                    "gym_name": name,
                    "contact_number": phone,
                    "gym_email": email,
                    "gym_address": address,
                    "opening_time": opening,
                    "closing_time": closing,
                    "operating_days": ",".join(day for day in (
                        "Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday",
                    ) if day in days),
                })
                db.commit()
                flash("Gym information saved.", "success")
                return _settings_redirect("gym")
        elif action == "pricing":
            walkin_fee = _money_value(request.form.get("walk_in_fee", ""))
            monthly_fee = _money_value(request.form.get("monthly_fee", ""))
            if walkin_fee is None or monthly_fee is None:
                flash("Enter valid numeric prices that are zero or greater.", "error")
            else:
                _save_setting_values(db, {
                    "walk_in_fee": format(walkin_fee, "f"),
                    "monthly_fee": format(monthly_fee, "f"),
                })
                db.execute(
                    """UPDATE membership_plans SET price = ?
                       WHERE lower(name) = 'monthly membership'""",
                    (float(monthly_fee),),
                )
                db.commit()
                flash("Pricing saved. Existing payment and visit records were not changed.", "success")
                return _settings_redirect("pricing")
        elif action == "attendance":
            time_format = request.form.get("time_format", "")
            timezone_name = request.form.get("timezone", "").strip()
            desktop_sidebar = request.form.get("desktop_sidebar", "")
            mobile_sidebar = request.form.get("mobile_sidebar", "")
            try:
                ZoneInfo(timezone_name)
                valid_timezone = True
            except (ZoneInfoNotFoundError, ValueError):
                valid_timezone = False
            if time_format not in ("12", "24"):
                flash("Choose a valid time display format.", "error")
            elif not valid_timezone:
                flash("Enter a valid IANA timezone, such as Asia/Manila or America/Los_Angeles.", "error")
            elif desktop_sidebar not in ("expanded", "collapsed") or mobile_sidebar not in ("open", "closed"):
                flash("Choose valid sidebar defaults.", "error")
            else:
                _save_setting_values(db, {
                    "time_format": time_format,
                    "timezone": timezone_name,
                    "desktop_sidebar": desktop_sidebar,
                    "mobile_sidebar": mobile_sidebar,
                })
                db.commit()
                flash("Attendance and display preferences saved.", "success")
                return _settings_redirect("attendance")
        elif action == "notifications":
            inactive_member_days = request.form.get("inactive_member_days", "").strip()
            if not inactive_member_days.isdecimal() or not 7 <= int(inactive_member_days) <= 365:
                flash("Set the inactive-member period to a whole number from 7 to 365 days.", "error")
            else:
                _save_setting_values(db, {
                    "membership_reminders": "1" if request.form.get("membership_reminders") else "0",
                    "checkout_warnings": "1" if request.form.get("checkout_warnings") else "0",
                    "pending_payment_reminders": "1" if request.form.get("pending_payment_reminders") else "0",
                    "gcash_payment_reminders": "1" if request.form.get("gcash_payment_reminders") else "0",
                    "expired_membership_reminders": "1" if request.form.get("expired_membership_reminders") else "0",
                    "new_registration_reminders": "1" if request.form.get("new_registration_reminders") else "0",
                    "inactive_member_reminders": "1" if request.form.get("inactive_member_reminders") else "0",
                    "inactive_member_days": inactive_member_days,
                })
                db.commit()
                flash("In-app notification preferences saved.", "success")
                return _settings_redirect("notifications")
        else:
            flash("Choose a valid settings section.", "error")

    values = _app.setting_values(db)
    admin = db.execute(
        """SELECT username, email, full_name, phone, profile_photo, role
           FROM users WHERE id = ?""",
        (session["user_id"],),
    ).fetchone()
    profile_values = dict(admin)
    profile_values["photo_url"] = _profile_image_url(profile_values["profile_photo"])
    email_logs = db.execute(
        """SELECT to_email, subject, status, error_message, created_at
           FROM email_log ORDER BY id DESC LIMIT 50"""
    ).fetchall()
    return _app.render_template(
        "settings.html", username=admin["username"], profile=profile_values,
        settings_section=settings_section,
        settings_section_title=settings_sections[settings_section],
        settings=values,
        gcash_qr_url=_gcash_qr_url(values.get("gcash_qr_filename", "")),
        operating_days=values["operating_days"].split(","),
        last_backup=values["last_backup"],
        email_logs=email_logs,
        email_configured=_app.email_is_configured(),
        today=_app.philippine_date().isoformat(),
        current_year=_app.philippine_now().year,
    )


@settings_bp.route("/settings/test-email", methods=["POST"])
@admin_required
def send_test_email():
    if not _app.email_is_configured():
        flash(
            "Email is not configured. Add your provider's MAIL_SERVER, MAIL_USERNAME, and MAIL_PASSWORD to the project .env file, then restart Flask.",
            "error",
        )
        return redirect(url_for("settings.settings", section="email"))

    admin = _app.get_db().execute(
        "SELECT email, full_name FROM users WHERE id = ? AND role = 'admin'",
        (session["user_id"],),
    ).fetchone()
    if not admin or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", admin["email"] or ""):
        flash("Add a valid administrator email in My profile before testing email.", "error")
    else:
        queued = _app.send_email(
            admin["email"],
            "Arco Fitness Gym test email",
            (
                f"Hello {admin['full_name'] or session['username']},\n\n"
                "This test email confirms that Arco Fitness Gym can send email."
            ),
            _app.render_template(
                "emails/test_email.html",
                member_name=admin["full_name"] or session["username"],
            ),
        )
        if queued:
            flash("Test email queued. Check the email log in a few seconds.", "success")
        else:
            flash("The test email could not be queued. Check the email log.", "error")
    return redirect(url_for("settings.settings", section="email"))


@settings_bp.route("/settings/gcash-qr", methods=["POST"])
@admin_required
def upload_gcash_qr():
    image = request.files.get("gcash_qr")
    if image is None or not image.filename:
        flash("Choose a PNG or JPG GCash QR image to upload.", "error")
        return redirect(url_for("settings.settings", section="gcash"))

    original_name = secure_filename(image.filename)
    extension = os.path.splitext(original_name)[1].lower()
    if extension not in (".png", ".jpg", ".jpeg"):
        flash("Use a PNG, JPG, or JPEG image for the GCash QR.", "error")
        return redirect(url_for("settings.settings", section="gcash"))

    image_data = image.stream.read(GCASH_QR_MAX_BYTES + 1)
    if len(image_data) > GCASH_QR_MAX_BYTES:
        flash("The GCash QR image must be 2 MB or smaller.", "error")
        return redirect(url_for("settings.settings", section="gcash"))

    image_extension = _image_extension(image_data)
    extension_matches = (
        image_extension == ".png" and extension == ".png"
        or image_extension == ".jpg" and extension in (".jpg", ".jpeg")
    )
    if not extension_matches:
        flash("The selected file is not a valid PNG or JPG image.", "error")
        return redirect(url_for("settings.settings", section="gcash"))

    filename = secure_filename(f"gcash-qr-{secrets.token_hex(16)}{image_extension}")
    old_filename = _app.setting_values(_app.get_db()).get("gcash_qr_filename", "")
    new_path = os.path.join(GCASH_QR_UPLOAD_DIR, filename)
    db = _app.get_db()
    try:
        os.makedirs(GCASH_QR_UPLOAD_DIR, exist_ok=True)
        with open(new_path, "xb") as image_file:
            image_file.write(image_data)
        _save_setting_values(db, {"gcash_qr_filename": filename})
        db.commit()
    except (OSError, DatabaseError):
        db.rollback()
        if os.path.isfile(new_path):
            try:
                os.remove(new_path)
            except OSError:
                current_app.logger.exception("Could not remove a GCash QR image after save failure")
        current_app.logger.exception("GCash QR image could not be saved")
        flash("GCash QR image could not be saved. Please try again.", "error")
        return redirect(url_for("settings.settings", section="gcash"))

    if old_filename != filename and re.fullmatch(
            r"gcash-qr-[a-f0-9]{32}\.(?:jpg|png)", old_filename or ""):
        old_path = os.path.join(GCASH_QR_UPLOAD_DIR, old_filename)
        try:
            if os.path.isfile(old_path):
                os.remove(old_path)
        except OSError:
            current_app.logger.exception("Could not remove the replaced GCash QR image")

    flash("GCash QR image uploaded.", "success")
    return redirect(url_for("settings.settings", section="gcash"))


@settings_bp.route("/settings/backup", methods=["POST"])
@admin_required
def download_backup():
    try:
        backup_path = _database_backup()
        _record_backup(_app.get_db())
    except (OSError, DatabaseError):
        current_app.logger.exception("Database backup failed")
        flash("The database backup could not be created. Please try again.", "error")
        return _settings_redirect("data")
    return send_file(
        backup_path, mimetype="application/sql",
        as_attachment=True,
        download_name=os.path.basename(backup_path),
    )


@settings_bp.route("/settings/export/<record_type>.csv")
@admin_required
def export_settings_csv(record_type):
    db = _app.get_db()
    output = StringIO(newline="")
    writer = csv.writer(output)
    try:
        if record_type == "attendance":
            writer.writerow((
                "Date", "Name", "Visit type",
                "Time in (UTC when available)",
                "Time out (UTC when available)", "Status",
            ))
            member_rows = db.execute(
                """SELECT a.checkin_date AS visit_date, m.full_name,
                          a.checkin_time, a.checkin_at, a.checkout_at
                   FROM attendance a JOIN members m ON m.id = a.member_id
                   WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                   ORDER BY a.checkin_date, a.id"""
            ).fetchall()
            walkin_rows = db.execute(
                """SELECT visit_date, full_name, checkin_time, checkout_time,
                          checkin_at, checkout_at
                   FROM walkin_visits ORDER BY visit_date, id"""
            ).fetchall()
            for kind, rows in (("Monthly member", member_rows), ("Walk-in", walkin_rows)):
                for row in rows:
                    writer.writerow(tuple(_safe_csv_value(value) for value in (
                        row["visit_date"], row["full_name"], kind,
                        row["checkin_at"] or row["checkin_time"] or "",
                        row["checkout_at"] or (
                            row["checkout_time"] if "checkout_time" in row.keys() else ""
                        ) or "",
                        "Checked Out" if row["checkout_at"] else "Currently In Gym",
                    )))
            filename = "arco-attendance.csv"
        elif record_type == "payments":
            writer.writerow(("Date", "Member", "Amount (PHP)", "Method", "Note"))
            payment_rows = db.execute(
                """SELECT p.payment_date, m.full_name, p.amount, p.method, p.note
                   FROM payments p JOIN members m ON m.id = p.member_id
                   WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                   ORDER BY p.payment_date, p.id"""
            ).fetchall()
            writer.writerows(
                tuple(_safe_csv_value(value) for value in row)
                for row in payment_rows
            )
            filename = "arco-payments.csv"
        else:
            abort(404)
    except DatabaseError:
        current_app.logger.exception("CSV export failed")
        flash("The requested CSV export could not be created.", "error")
        return _settings_redirect("danger")
    return Response(
        output.getvalue(), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@settings_bp.route("/settings/reset-defaults", methods=["POST"])
@admin_required
def reset_settings_defaults():
    if request.form.get("confirmation") != "RESET SETTINGS":
        flash("Type RESET SETTINGS to confirm the settings reset.", "error")
        return _settings_redirect("danger")
    db = _app.get_db()
    defaults = {
        key: value for key, value in DEFAULT_SETTINGS.items()
        if key != "last_backup"
    }
    _save_setting_values(db, defaults)
    db.execute(
        """UPDATE membership_plans SET price = ?
           WHERE lower(name) = 'monthly membership'""",
        (float(defaults["monthly_fee"]),),
    )
    db.commit()
    flash("Settings restored to defaults. Historical records were kept.", "success")
    return _settings_redirect("danger")


@settings_bp.route("/settings/reset-attendance", methods=["POST"])
@admin_required
def reset_attendance_for_date():
    selected_date = _valid_date(request.form.get("attendance_date", ""))
    if selected_date is None:
        flash("Choose a valid date for the attendance reset.", "error")
        return _settings_redirect("danger")
    if not _verify_admin_password(request.form.get("admin_password", "")):
        flash("The administrator password is incorrect; no attendance was changed.", "error")
        return _settings_redirect("danger")
    if request.form.get("confirmation") != "RESET ATTENDANCE":
        flash("Type RESET ATTENDANCE to confirm; no attendance was changed.", "error")
        return _settings_redirect("danger")
    db = _app.get_db()
    try:
        _database_backup()
        _record_backup(db)
        db.execute("BEGIN IMMEDIATE")
        member_count = db.execute(
            "DELETE FROM attendance WHERE checkin_date = ?",
            (selected_date.isoformat(),),
        ).rowcount
        walkin_count = db.execute(
            "DELETE FROM walkin_visits WHERE visit_date = ?",
            (selected_date.isoformat(),),
        ).rowcount
        db.commit()
    except (OSError, DatabaseError):
        db.rollback()
        current_app.logger.exception("Attendance reset failed")
        flash("A backup or attendance reset failed. No reset was completed.", "error")
        return _settings_redirect("danger")
    flash(
        f"Attendance for {selected_date.isoformat()} was reset "
        f"({member_count + walkin_count} visit records removed).",
        "success",
    )
    return _settings_redirect("danger")


@settings_bp.route("/settings/reset-all", methods=["POST"])
@admin_required
def reset_all_gym_data():
    if not _verify_admin_password(request.form.get("admin_password", "")):
        flash("The administrator password is incorrect; no data was changed.", "error")
        return _settings_redirect("danger")
    if request.form.get("confirmation") != "RESET ALL GYM DATA":
        flash("Type RESET ALL GYM DATA exactly; no data was changed.", "error")
        return _settings_redirect("danger")
    db = _app.get_db()
    monthly_fee = float(_app.setting_values(db)["monthly_fee"])
    try:
        _database_backup()
        _record_backup(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM attendance")
        db.execute("DELETE FROM walkin_visits")
        db.execute("DELETE FROM payments")
        db.execute("DELETE FROM member_memberships")
        db.execute("DELETE FROM members")
        db.execute("DELETE FROM users WHERE role = 'member'")
        db.execute("DELETE FROM membership_plans")
        db.execute(
            """INSERT INTO membership_plans
               (name, duration_days, price, description, active)
               VALUES ('Monthly Membership', 30, ?,
                       '30-day gym membership', 1)""",
            (monthly_fee,),
        )
        db.commit()
    except (OSError, DatabaseError):
        db.rollback()
        current_app.logger.exception("Full gym data reset failed")
        flash("A backup or data reset failed. No reset was completed.", "error")
        return _settings_redirect("danger")
    flash(
        "All gym records were reset. Admin accounts, settings, and the database schema were preserved.",
        "success",
    )
    return _settings_redirect("danger")
