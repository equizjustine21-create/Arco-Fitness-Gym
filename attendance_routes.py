"""Attendance routes for the Arco Fitness application."""

import app as _app
from blueprints import attendance_bp
from app import (
    DatabaseIntegrityError,
    flash,
    gym_now,
    login_required,
    redirect,
    request,
    session,
    url_for,
    utc_timestamp,
)

__all__ = ['attendance']


@attendance_bp.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    db = _app.get_db()
    today = _app.philippine_date().isoformat()
    if request.method == "POST":
        action = request.form.get("action")
        now = gym_now()
        today = now.date().isoformat()
        local_time = now.strftime("%H:%M:%S")
        timestamp = utc_timestamp(now)
        if action == "walkin_checkout":
            visit_id = request.form.get("visit_id", type=int)
            result = db.execute(
                """UPDATE walkin_visits SET checkout_time = ?, checkout_at = ?
                   WHERE id = ? AND visit_date = ? AND checkout_time IS NULL""",
                (local_time, timestamp, visit_id, today),
            ) if visit_id else None
            if result is None or result.rowcount != 1:
                flash("That walk-in visit is already timed out or was not found.", "error")
            else:
                db.commit()
                flash("Walk-in time-out recorded.", "success")
                return redirect(url_for("attendance.attendance"))
        elif action == "member_checkout":
            record_id = request.form.get("record_id", type=int)
            result = db.execute(
                """UPDATE attendance SET checkout_at = ?
                   WHERE id = ? AND checkin_date = ? AND checkout_at IS NULL""",
                (timestamp, record_id, today),
            ) if record_id else None
            if result is None or result.rowcount != 1:
                flash("That member visit is already timed out or was not found.", "error")
            else:
                db.commit()
                flash("Member time-out recorded.", "success")
                return redirect(url_for("attendance.attendance"))
        else:
            member_id = request.form.get("member_id", type=int)
            member = db.execute(
                """SELECT m.id FROM members m
                   WHERE m.id = ? AND lower(m.status) = 'active'
                     AND EXISTS (
                       SELECT 1 FROM member_memberships mm
                       WHERE mm.member_id = m.id AND lower(mm.status) = 'active'
                         AND mm.start_date <= ? AND mm.end_date >= ?
                     )""",
                (member_id, today, today),
            ).fetchone() if member_id else None
            if member is None:
                flash("Select a member with an active monthly membership.", "error")
            else:
                try:
                    db.execute(
                        """INSERT INTO attendance
                           (member_id, checkin_date, checkin_time, checkin_at)
                           VALUES (?, ?, ?, ?)""",
                        (member_id, today, local_time, timestamp),
                    )
                    db.commit()
                except DatabaseIntegrityError:
                    flash("This member has already checked in today.", "error")
                else:
                    flash("Attendance recorded.", "success")
                    return redirect(url_for("attendance.attendance"))
    walkins = db.execute(
        """SELECT * FROM walkin_visits WHERE visit_date = ?
           ORDER BY checkin_time DESC, id DESC""",
        (today,),
    ).fetchall()
    membership_history = db.execute(
        """SELECT a.id, m.full_name, a.checkin_date, a.checkin_time,
                  a.checkin_at, a.checkout_at
           FROM attendance a
           JOIN members m ON m.id = a.member_id
           WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
           ORDER BY a.checkin_date DESC, a.checkin_time DESC, a.id DESC
           LIMIT 300""",
    ).fetchall()
    return _app.render_template(
        "attendance.html", username=session["username"],
        walkins=walkins, membership_history=membership_history, today=today
    )
