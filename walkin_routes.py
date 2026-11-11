"""Walkin routes for the Arco Fitness application."""

import app as _app
from blueprints import walkin_bp
from app import (
    BASE_DIR,
    DatabaseError,
    DatabaseIntegrityError,
    _gcash_qr_url,
    _verify_admin_password,
    admin_required,
    current_app,
    flash,
    format_time_12_hour,
    gym_now,
    jsonify,
    os,
    redirect,
    request,
    session,
    subprocess,
    sys,
    url_for,
    utc_timestamp,
)

__all__ = [
    'confirm_walkin_fee', 'walk_in', 'walk_in_history', 'walk_in_leave',
    'walk_in_today', 'walkin_member_history',
]


@walkin_bp.route("/walk-in", methods=["GET", "POST"])
def walk_in():
    db = _app.get_db()
    today = _app.philippine_date().isoformat()
    staff_user = db.execute(
        "SELECT 1 FROM users WHERE id = ? AND role = 'admin'",
        (session.get("user_id"),),
    ).fetchone() if session.get("user_id") else None
    staff_mode = staff_user is not None
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        action = request.form.get("walkin_action") or request.form.get("action")
        if (not full_name or len(full_name) > 120 or len(email) > 254
                or (email and ("@" not in email or email.startswith("@") or email.endswith("@")))):
            flash("Enter your name (up to 120 characters).", "error")
        elif action == "checkin":
            now = gym_now()
            today = now.date().isoformat()
            member = db.execute(
                """SELECT m.id, m.full_name FROM members m
                   WHERE lower(m.email) = ? AND lower(m.status) = 'active'
                     AND EXISTS (
                       SELECT 1 FROM member_memberships mm
                       WHERE mm.member_id = m.id AND lower(mm.status) = 'active'
                         AND mm.start_date <= ? AND mm.end_date >= ?
                     )""",
                (email, today, today),
            ).fetchone() if email else None
            if member:
                if not staff_mode:
                    flash("Member attendance must be recorded by staff at the front desk.", "error")
                    return redirect(url_for("walkin.walk_in"))
                try:
                    db.execute(
                        """INSERT INTO attendance
                           (member_id, checkin_date, checkin_time, checkin_at)
                           VALUES (?, ?, ?, ?)""",
                        (member["id"], today, now.strftime("%H:%M:%S"), utc_timestamp(now)),
                    )
                    db.commit()
                except DatabaseIntegrityError:
                    flash("This member has already checked in today.", "error")
                    return redirect(url_for("walkin.walk_in"))
                else:
                    flash(f"Member time-in recorded for {member['full_name']}.", "success")
                    return redirect(url_for("walkin.walk_in"))
            elif email:
                flash("No active membership was found for that email. Record the visit as a walk-in.", "error")
                return redirect(url_for("walkin.walk_in"))
            open_visit = db.execute(
                """SELECT id FROM walkin_visits
                   WHERE lower(full_name) = lower(?) AND visit_date = ?
                     AND checkout_time IS NULL""",
                (full_name, today),
            ).fetchone()
            if open_visit:
                flash("You already have an open visit. Please time out before checking in again.", "error")
            else:
                fee = _app.Decimal(str(_app.setting_values(db)["walk_in_fee"]))
                db.execute(
                    """INSERT INTO walkin_visits
                       (full_name, visit_date, checkin_time, checkin_at, fee)
                       VALUES (?, ?, ?, ?, ?)""",
                    (full_name, now.date().isoformat(), now.strftime("%H:%M:%S"),
                     utc_timestamp(now), fee),
                )
                db.commit()
                flash(f"Time-in recorded. Please pay ₱{fee:.2f} at the front desk.", "success")
                return redirect(url_for("walkin.walk_in"))
        elif action == "checkout":
            now = gym_now()
            today = now.date().isoformat()
            member = db.execute(
                "SELECT id FROM members WHERE lower(email) = ?",
                (email,),
            ).fetchone() if email else None
            if member:
                if not staff_mode:
                    flash("Member attendance must be recorded by staff at the front desk.", "error")
                    return redirect(url_for("walkin.walk_in"))
                result = db.execute(
                    """UPDATE attendance SET checkout_at = ?
                       WHERE member_id = ? AND checkin_date = ? AND checkout_at IS NULL""",
                    (utc_timestamp(now), member["id"], today),
                )
                if result.rowcount == 1:
                    db.commit()
                    flash("Member time-out recorded.", "success")
                    return redirect(url_for("walkin.walk_in"))
                flash("No open member check-in was found for that email today.", "error")
                return redirect(url_for("walkin.walk_in"))
            visit = db.execute(
                """SELECT id FROM walkin_visits
                   WHERE lower(full_name) = lower(?) AND visit_date = ?
                     AND checkout_time IS NULL ORDER BY id DESC LIMIT 1""",
                (full_name, today),
            ).fetchone()
            if visit is None:
                flash("No open walk-in visit was found for that name today.", "error")
            else:
                result = db.execute(
                    """UPDATE walkin_visits SET checkout_time = ?, checkout_at = ?
                       WHERE id = ? AND checkout_time IS NULL""",
                    (now.strftime("%H:%M:%S"), utc_timestamp(now), visit["id"]),
                )
                if result.rowcount != 1:
                    flash("That visit was already checked out. No second time-out was recorded.", "error")
                else:
                    db.commit()
                    flash("Time-out recorded. Thank you for visiting ARCO FITNESS.", "success")
                    return redirect(url_for("walkin.walk_in"))
        else:
            flash("Choose time-in or time-out.", "error")
    visits = db.execute(
        """SELECT id, full_name, checkin_time, checkout_time, checkin_at, checkout_at,
                  fee_status, fee_paid_at
           FROM walkin_visits WHERE visit_date = ?
           ORDER BY checkin_time DESC, id DESC""",
        (today,),
    ).fetchall() if staff_mode else []
    membership_attendance = []
    if staff_mode:
        membership_attendance = db.execute(
            """SELECT a.id, m.full_name, a.checkin_date, a.checkin_time,
                      a.checkin_at, a.checkout_at
               FROM attendance a
               JOIN members m ON m.id = a.member_id
                   WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
                     AND EXISTS (
                       SELECT 1 FROM member_memberships mm
                       WHERE mm.member_id = a.member_id
                     AND lower(mm.status) = 'active'
                     AND mm.start_date <= a.checkin_date
                     AND mm.end_date >= a.checkin_date
               )
               ORDER BY a.checkin_date DESC, a.checkin_time DESC, a.id DESC
               LIMIT 100"""
        ).fetchall()
    return _app.render_template(
        "walkin.html", today=today,
        visits=visits, staff_mode=staff_mode,
        membership_attendance=membership_attendance,
        gcash_qr_url=_gcash_qr_url(
            _app.setting_values(db).get("gcash_qr_filename", "")
        ),
    )


@walkin_bp.route("/walk-in/<int:visit_id>/fee", methods=["POST"])
@admin_required
def confirm_walkin_fee(visit_id):
    db = _app.get_db()
    try:
        result = db.execute(
            """UPDATE walkin_visits
               SET fee_status = 'Confirmed', fee_paid_at = ?, fee_paid_by = ?
               WHERE id = ? AND LOWER(fee_status) = 'pending'""",
            (_app.philippine_now(), session["user_id"], visit_id),
        )
        if result.rowcount == 1:
            db.commit()
            flash("Walk-in fee marked as paid and included in income.", "success")
        else:
            db.rollback()
            flash("This walk-in fee is no longer pending or the visit was not found.", "error")
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("Walk-in fee could not be marked paid")
        flash("Walk-in fee could not be updated. Please try again.", "error")

    start = _app._valid_date(request.form.get("from", ""))
    end = _app._valid_date(request.form.get("to", ""))
    if start and end and start <= end:
        method = request.form.get("method", "All")
        status = request.form.get("status", "All")
        page = request.form.get("page", "1")
        return redirect(url_for(
            "reports.reports",
            **{"from": start.isoformat(), "to": end.isoformat(),
               "method": method, "status": status, "page": page},
        ))
    return redirect(url_for("walkin.walk_in"))


@walkin_bp.route("/walk-in/leave", methods=["POST"])
@admin_required
def walk_in_leave():
    password = request.form.get("admin_password")
    if password is None:
        command = [
            sys.executable,
            os.path.join(BASE_DIR, "walkin_admin_dialog.py"),
        ]
        options = {
            "capture_output": True,
            "timeout": 180,
            "check": False,
            "encoding": "utf-8",
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            prompt = subprocess.run(command, **options)
        except (OSError, subprocess.TimeoutExpired):
            current_app.logger.exception("Could not open the Walk-in admin password window")
            flash("The admin password window could not be opened.", "error")
            return redirect(url_for("walkin.walk_in"))
        if prompt.returncode == 2:
            return redirect(url_for("walkin.walk_in"))
        if prompt.returncode != 0:
            current_app.logger.error(
                "Walk-in admin password window exited with code %s: %s",
                prompt.returncode,
                prompt.stderr,
            )
            flash("The admin password window could not be opened.", "error")
            return redirect(url_for("walkin.walk_in"))
        password = prompt.stdout

    if not _verify_admin_password(password):
        flash("Incorrect admin password. You are still on Walk-in attendance.", "error")
        return redirect(url_for("walkin.walk_in"))
    return redirect(url_for("dashboard.dashboard"))


@walkin_bp.route("/walk-in/member-history")
@admin_required
def walkin_member_history():
    db = _app.get_db()
    records = db.execute(
        """SELECT m.full_name, a.checkin_date, a.checkin_time,
                  a.checkin_at, a.checkout_at
           FROM attendance a
           JOIN members m ON m.id = a.member_id
           WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
             AND EXISTS (
               SELECT 1 FROM member_memberships mm
               WHERE mm.member_id = a.member_id
                 AND lower(mm.status) = 'active'
                 AND mm.start_date <= a.checkin_date
                 AND mm.end_date >= a.checkin_date
           )
           ORDER BY a.checkin_date DESC, a.checkin_time DESC, a.id DESC LIMIT 300""",
    ).fetchall()
    return _app.render_template(
        "member_attendance_history.html", records=records,
        current_year=_app.philippine_now().year,
    )


@walkin_bp.route("/walk-in/today.json")
@admin_required
def walk_in_today():
    today = _app.philippine_date().isoformat()
    visits = _app.get_db().execute(
        """SELECT id, full_name, checkin_time, checkout_time, checkin_at, checkout_at,
                  fee_status, fee_paid_at
           FROM walkin_visits WHERE visit_date = ?
           ORDER BY checkin_time DESC, id DESC""",
        (today,),
    ).fetchall()
    return jsonify([
        {
            "id": visit["id"],
            "full_name": visit["full_name"],
            "checkin_time": visit["checkin_time"],
            "checkout_time": visit["checkout_time"],
            "checkin_display": format_time_12_hour(
                visit["checkin_at"] or visit["checkin_time"]
            ),
            "checkout_display": format_time_12_hour(
                visit["checkout_at"] or visit["checkout_time"]
            ) if visit["checkout_at"] or visit["checkout_time"] else "—",
            "fee_status": visit["fee_status"],
            "status": "Checked Out" if visit["checkout_time"] else "Currently In Gym",
        }
        for visit in visits
    ])


@walkin_bp.route("/walk-in/history")
def walk_in_history():
    full_name = request.args.get("full_name", "").strip()
    visits = []
    if full_name:
        visits = _app.get_db().execute(
            """SELECT full_name, visit_date, checkin_time, checkout_time,
                      checkin_at, checkout_at
               FROM walkin_visits
               WHERE lower(full_name) = lower(?)
               ORDER BY visit_date DESC, checkin_time DESC, id DESC""",
            (full_name,),
        ).fetchall()
    return _app.render_template(
        "walkin_history.html", full_name=full_name, visits=visits
    )
