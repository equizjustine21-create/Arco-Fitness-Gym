"""Dashboard routes for the Arco Fitness application."""

import app as _app
from blueprints import dashboard_bp
from app import (
    abort,
    admin_required,
    login_required,
    re,
    request,
    session,
    timedelta,
)

__all__ = ['dashboard', 'mark_notification_read']


def _recent_member_status(membership, today, attention_end):
    if membership is None:
        return "Pending"
    membership_status = membership["status"].lower()
    if str(membership["end_date"]) < today or membership_status not in {"active", "pending"}:
        return "Expired"
    if membership_status == "pending" or not membership["has_confirmed_payment"]:
        return "Pending"
    if str(membership["end_date"]) <= attention_end:
        return "Expiring"
    return "Active"


@dashboard_bp.route("/admin/notifications/read", methods=["POST"])
@admin_required
def mark_notification_read():
    notification_key = request.form.get("notification_key", "")
    if not re.fullmatch(
            r"(?:expiring|unpaid|gcash_pending|expired|walkin|registration|inactive):[1-9][0-9]*",
            notification_key):
        abort(400, description="The notification identifier is invalid.")

    db = _app.get_db()
    db.execute(
        """INSERT IGNORE INTO admin_notification_reads
           (admin_user_id, notification_key) VALUES (?, ?)""",
        (session["user_id"], notification_key),
    )
    db.commit()
    return "", 204


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    db = _app.get_db()
    current_date = _app.philippine_date()
    today = current_date.isoformat()
    month_start = current_date.replace(day=1).isoformat()
    chart_start = current_date - timedelta(days=6)
    attention_end = current_date + timedelta(days=7)
    notification_settings = _app.setting_values(db)
    stats = {
        "total_members": db.execute(
            "SELECT COUNT(*) FROM members WHERE RIGHT(email, 16) <> '@deleted.invalid'"
        ).fetchone()[0],
        "active_members": db.execute(
            """SELECT COUNT(*) FROM members
               WHERE lower(status) = 'active'
                 AND RIGHT(email, 16) <> '@deleted.invalid'"""
        ).fetchone()[0],
        "today_attendance": db.execute(
            """SELECT COUNT(*) FROM attendance WHERE checkin_date = ?""", (today,)
        ).fetchone()[0] + db.execute(
            "SELECT COUNT(*) FROM walkin_visits WHERE visit_date = ?", (today,)
        ).fetchone()[0],
        "monthly_revenue": db.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM payments
               WHERE payment_date >= ? AND lower(payment_status) = 'confirmed'""",
            (month_start,),
        ).fetchone()[0],
        "recent_members": db.execute(
            """SELECT m.*,
                      COALESCE(
                        (SELECT p.name
                         FROM member_memberships mm
                         JOIN membership_plans p ON p.id = mm.plan_id
                         WHERE mm.member_id = m.id
                         ORDER BY mm.id DESC LIMIT 1),
                        NULLIF(m.membership_type, ''), 'None'
                      ) AS membership_type
               FROM members m
               WHERE RIGHT(m.email, 16) <> '@deleted.invalid'
               ORDER BY m.id DESC LIMIT 5"""
        ).fetchall(),
    }
    stats["walkins_today"] = db.execute(
        "SELECT COUNT(*) FROM walkin_visits WHERE visit_date = ?", (today,)
    ).fetchone()[0]
    daily_attendance = db.execute(
        """SELECT checkin_date, COUNT(*) AS checkins
           FROM attendance
           WHERE checkin_date BETWEEN ? AND ?
           GROUP BY checkin_date""",
        (chart_start.isoformat(), today),
    ).fetchall()
    attendance_by_date = {
        str(row["checkin_date"]): row["checkins"] for row in daily_attendance
    }
    stats["attendance_chart"] = [
        {
            "label": (chart_start + timedelta(days=offset)).strftime("%a"),
            "date": (chart_start + timedelta(days=offset)).strftime("%b %d"),
            "count": attendance_by_date.get(
                (chart_start + timedelta(days=offset)).isoformat(), 0
            ),
            "is_today": offset == 6,
        }
        for offset in range(7)
    ]

    stats.update(
        _app._admin_notification_data(db, current_date, notification_settings)
    )

    if stats["recent_members"]:
        recent_member_ids = [member["id"] for member in stats["recent_members"]]
        placeholders = ", ".join("?" for _ in recent_member_ids)
        recent_membership_rows = db.execute(
            f"""SELECT mm.member_id, mm.status, mm.start_date, mm.end_date,
                       EXISTS (
                           SELECT 1 FROM payments pay
                           WHERE pay.membership_id = mm.id
                             AND lower(pay.payment_status) IN ('confirmed', 'paid')
                       ) AS has_confirmed_payment
                FROM member_memberships mm
                WHERE mm.member_id IN ({placeholders})
                  AND mm.id = (
                      SELECT MAX(latest.id)
                      FROM member_memberships latest
                      WHERE latest.member_id = mm.member_id
                  )""",
            tuple(recent_member_ids),
        ).fetchall()
    else:
        recent_membership_rows = []
    recent_membership_by_member = {
        row["member_id"]: row for row in recent_membership_rows
    }
    stats["recent_member_statuses"] = {}
    for member in stats["recent_members"]:
        membership = recent_membership_by_member.get(member["id"])
        stats["recent_member_statuses"][member["id"]] = _recent_member_status(
            membership, today, attention_end.isoformat()
        )

    return _app.render_template(
        "dashboard.html", username=session["username"],
        dashboard_date=current_date.strftime("%A, %B") + f" {current_date.day}, {current_date.year}",
        current_year=_app.philippine_now().year, **stats
    )
