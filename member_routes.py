"""Members routes for the Arco Fitness application."""

import app as _app
from blueprints import members_bp
from domain_models import Member
from app import (
    DatabaseError,
    DatabaseIntegrityError,
    admin_required,
    current_app,
    flash,
    login_required,
    redirect,
    request,
    secrets,
    session,
    url_for,
)

__all__ = ['add_member', 'edit_member', 'members', 'remove_member', 'toggle_member_status']


@members_bp.route("/members")
@login_required
def members():
    search = request.args.get("q", "").strip()
    query = """SELECT m.*,
                      (SELECT p.name
                       FROM member_memberships mm
                       JOIN membership_plans p ON p.id = mm.plan_id
                       WHERE mm.member_id = m.id
                       ORDER BY mm.id DESC LIMIT 1) AS current_plan
               FROM members m
               WHERE RIGHT(m.email, 16) <> '@deleted.invalid'"""
    parameters = ()
    if search:
        query += " AND (m.full_name LIKE ? OR m.email LIKE ? OR m.phone LIKE ?)"
        pattern = f"%{search}%"
        parameters = (pattern, pattern, pattern)
    query += " ORDER BY m.full_name COLLATE NOCASE"
    rows = _app.get_db().execute(query, parameters).fetchall()
    return _app.render_template(
        "members.html",
        username=session["username"],
        members=[Member.from_record(row) for row in rows],
        search=search,
    )


@members_bp.route("/members/add", methods=["GET", "POST"])
@login_required
def add_member():
    if request.method == "POST":
        member = Member.from_form(request.form)
        error = member.validation_error()
        if error:
            flash(error, "error")
        elif _app.get_db().execute(
            "SELECT 1 FROM members WHERE lower(email) = lower(?)",
            (member.normalized_email,),
        ).fetchone():
            flash("A member with that email address already exists.", "error")
        else:
            db = _app.get_db()
            try:
                db.execute(
                    """INSERT INTO members
                       (full_name, email, phone, membership_type, status)
                       VALUES (?, ?, ?, '', 'Active')""",
                    (member.full_name, member.normalized_email, member.phone),
                )
                db.commit()
            except DatabaseIntegrityError:
                flash("Could not save this member. Check the details and try again.", "error")
            else:
                flash("Member added successfully.", "success")
                return redirect(url_for("members.members"))
    return _app.render_template(
        "member_form.html", username=session["username"], member=None
    )


@members_bp.route("/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
def edit_member(member_id):
    db = _app.get_db()
    member_record = db.execute(
        "SELECT * FROM members WHERE id = ?", (member_id,)
    ).fetchone()
    if member_record is None:
        flash("Member not found.", "error")
        return redirect(url_for("members.members"))
    member = Member.from_record(member_record)
    if request.method == "POST":
        updated_member = Member.from_form(request.form, existing=member)
        error = updated_member.validation_error(include_status=True)
        if error:
            flash(error, "error")
        elif db.execute(
            "SELECT 1 FROM members WHERE lower(email) = lower(?) AND id != ?",
            (updated_member.normalized_email, member_id),
        ).fetchone():
            flash("A member with that email address already exists.", "error")
        else:
            db.execute(
                """UPDATE members SET full_name = ?, email = ?, phone = ?, status = ?
                   WHERE id = ?""",
                (
                    updated_member.full_name,
                    updated_member.normalized_email,
                    updated_member.phone,
                    updated_member.status,
                    member_id,
                ),
            )
            if updated_member.user_id is not None:
                try:
                    db.execute(
                        "UPDATE users SET email = ? WHERE id = ?",
                        (updated_member.normalized_email, updated_member.user_id),
                    )
                except DatabaseIntegrityError:
                    db.rollback()
                    flash("A member account already uses that email address.", "error")
                    return _app.render_template(
                        "member_form.html",
                        username=session["username"],
                        member=updated_member,
                    )
            db.commit()
            flash("Member details updated.", "success")
            return redirect(url_for("members.members"))
    return _app.render_template(
        "member_form.html", username=session["username"], member=member
    )


@members_bp.route("/members/<int:member_id>/status", methods=["POST"])
@login_required
def toggle_member_status(member_id):
    db = _app.get_db()
    result = db.execute(
        """UPDATE members
           SET status = CASE WHEN lower(status) = 'active' THEN 'Inactive' ELSE 'Active' END
           WHERE id = ?""",
        (member_id,),
    )
    db.commit()
    flash("Member status updated." if result.rowcount else "Member not found.",
          "success" if result.rowcount else "error")
    return redirect(url_for("members.members"))


@members_bp.route("/members/<int:member_id>/remove", methods=["POST"])
@admin_required
def remove_member(member_id):
    db = _app.get_db()
    try:
        db.execute("START TRANSACTION")
        member = db.execute(
            "SELECT id, user_id FROM members WHERE id = ? FOR UPDATE",
            (member_id,),
        ).fetchone()
        if member is None:
            db.rollback()
            flash("Member not found.", "error")
            return redirect(url_for("members.members"))

        reset_table = db.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema = DATABASE() AND table_name = ?""",
            ("password_resets",),
        ).fetchone()
        if reset_table:
            db.execute(
                "DELETE FROM password_resets WHERE member_id = ?",
                (member_id,),
            )

        anonymized_email = (
            f"removed-{member_id}-{secrets.token_hex(8)}@deleted.invalid"
        )
        db.execute(
            """UPDATE members
               SET full_name = ?, email = ?, phone = '', membership_type = '',
                   status = 'Inactive', user_id = NULL
               WHERE id = ?""",
            (f"Removed Member #{member_id}", anonymized_email, member_id),
        )
        if member["user_id"] is not None:
            db.execute(
                "DELETE FROM users WHERE id = ? AND role = 'member'",
                (member["user_id"],),
            )
        db.commit()
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("Member and linked records could not be removed")
        flash("Member could not be removed. No records were deleted.", "error")
        return redirect(url_for("members.members"))

    flash(
        "Member personal details and login were removed. Their anonymized membership, payment, and attendance history is retained in the database but hidden from member history tables.",
        "success",
    )
    return redirect(url_for("members.members"))
