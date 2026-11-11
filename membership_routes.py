"""Memberships routes for the Arco Fitness application."""

import app as _app
from blueprints import memberships_bp
from app import (
    DatabaseError,
    DatabaseIntegrityError,
    _gcash_qr_url,
    _membership_checkout_details,
    _membership_page_context,
    _money_value,
    _valid_checkout_token,
    _valid_date,
    admin_required,
    current_app,
    flash,
    login_required,
    redirect,
    request,
    session,
    url_for,
)

__all__ = ['edit_plan', 'memberships', 'remove_membership', 'toggle_plan']


@memberships_bp.route("/memberships", methods=["GET", "POST"])
@login_required
def memberships():
    db = _app.get_db()
    show_create_form = False
    if request.method == "POST":
        action = request.form.get("action")
        if action in ("assign", "record_cash", "submit_gcash", "confirm_payment"):
            if action == "confirm_payment":
                return redirect(url_for(
                    "memberships.memberships",
                    step="payment",
                    membership_id=request.form.get("membership_id", type=int),
                ))
            member_id = request.form.get("member_id", type=int)
            plan_id = request.form.get("plan_id", type=int)
            start = _valid_date(request.form.get("start_date", ""))
            legacy_membership_id = request.form.get("membership_id", type=int)
            if action == "assign":
                details, error = _membership_checkout_details(
                    db, member_id, plan_id, start
                ) if member_id and plan_id else (None, "Select a member, plan, and valid start date.")
                if error:
                    flash(error, "error")
                    return redirect(url_for("memberships.memberships"))
                if details["date_adjusted"]:
                    flash(
                        f"{details['member']['full_name']} already has an active membership "
                        f"until {details['active_end']}. The start date was moved to the next day.",
                        "warning",
                    )
                return redirect(url_for(
                    "memberships.memberships",
                    step="payment",
                    member_id=member_id,
                    plan_id=plan_id,
                    start_date=details["start_date"].isoformat(),
                ))

            method = "Cash" if action in ("record_cash", "confirm_payment") else "GCash"
            reference_number = request.form.get("reference_number", "").strip()
            checkout_token = request.form.get("checkout_token", "")
            if not _valid_checkout_token(checkout_token):
                flash("This checkout expired. Please start the payment step again.", "error")
                return redirect(url_for("memberships.memberships"))
            if method == "GCash":
                if not _gcash_qr_url(_app.setting_values(db).get("gcash_qr_filename", "")):
                    flash(
                        "GCash is not available yet. Ask the admin to upload the GCash QR in Settings.",
                        "error",
                    )
                    return redirect(url_for("memberships.memberships"))
                if len(reference_number) < 6 or len(reference_number) > 100:
                    flash("Enter a GCash reference number of 6 to 100 characters.", "error")
                    return redirect(url_for("memberships.memberships"))

            try:
                db.execute("START TRANSACTION")
                if legacy_membership_id:
                    membership = db.execute(
                        """SELECT mm.id AS membership_id, mm.member_id, mm.plan_id,
                                  mm.start_date, mm.end_date, mm.status,
                                  m.full_name, p.name AS plan_name, p.price
                           FROM member_memberships mm
                           JOIN members m ON m.id = mm.member_id
                           JOIN membership_plans p ON p.id = mm.plan_id
                           WHERE mm.id = ? AND lower(mm.status) = 'pending'
                           FOR UPDATE""",
                        (legacy_membership_id,),
                    ).fetchone()
                    existing_payment = db.execute(
                        "SELECT id FROM payments WHERE membership_id = ? LIMIT 1",
                        (legacy_membership_id,),
                    ).fetchone() if membership else None
                    if membership is None or existing_payment:
                        db.rollback()
                        flash("This membership is no longer awaiting payment.", "error")
                        return redirect(url_for("memberships.memberships"))
                    if member_id and member_id != membership["member_id"]:
                        db.rollback()
                        flash("The selected member does not match this membership.", "error")
                        return redirect(url_for("memberships.memberships"))
                else:
                    details, error = _membership_checkout_details(
                        db, member_id, plan_id, start, lock=True
                    ) if member_id and plan_id else (None, "Select a member, plan, and valid start date.")
                    if error:
                        db.rollback()
                        flash(error, "error")
                        return redirect(url_for("memberships.memberships"))
                    membership_status = "Pending" if method == "GCash" else "Active"
                    membership_result = db.execute(
                        """INSERT INTO member_memberships
                           (member_id, plan_id, start_date, end_date, status)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            member_id, plan_id, details["start_date"].isoformat(),
                            details["end_date"].isoformat(), membership_status,
                        ),
                    )
                    membership = {
                        "membership_id": membership_result.lastrowid,
                        "member_id": member_id,
                        "start_date": details["start_date"].isoformat(),
                        "end_date": details["end_date"].isoformat(),
                        "full_name": details["member"]["full_name"],
                        "plan_name": details["plan"]["name"],
                        "price": details["plan"]["price"],
                    }

                payment_timestamp = _app.philippine_now()
                if method == "Cash":
                    payment_result = db.execute(
                        """INSERT INTO payments
                           (member_id, amount, payment_date, paid_at, method, note,
                            membership_id, payment_status, checkout_token)
                           VALUES (?, ?, ?, ?, 'Cash', ?, ?, 'Confirmed', ?)""",
                        (
                            membership["member_id"], membership["price"],
                            payment_timestamp.date().isoformat(), payment_timestamp,
                            f"Membership payment: {membership['plan_name']}",
                            membership["membership_id"], checkout_token,
                        ),
                    )
                    if legacy_membership_id:
                        db.execute(
                            """UPDATE member_memberships SET status = 'Active'
                               WHERE id = ? AND lower(status) = 'pending'""",
                            (membership["membership_id"],),
                        )
                    db.execute(
                        "UPDATE members SET membership_type = ? WHERE id = ?",
                        (membership["plan_name"], membership["member_id"]),
                    )
                else:
                    duplicate_reference = db.execute(
                        """SELECT id FROM payments
                           WHERE method = 'GCash' AND reference_number = ?
                           LIMIT 1""",
                        (reference_number,),
                    ).fetchone()
                    if duplicate_reference:
                        db.rollback()
                        flash("That GCash reference number has already been submitted.", "error")
                        return redirect(url_for("memberships.memberships"))
                    payment_result = db.execute(
                        """INSERT INTO payments
                           (member_id, amount, payment_date, paid_at, method, note,
                            membership_id, payment_status, reference_number,
                            checkout_token)
                           VALUES (?, ?, ?, ?, 'GCash', ?, ?, 'Pending', ?, ?)""",
                        (
                            membership["member_id"], membership["price"],
                            payment_timestamp.date().isoformat(), payment_timestamp,
                            f"Online GCash membership payment: {membership['plan_name']}",
                            membership["membership_id"], reference_number,
                            checkout_token,
                        ),
                    )
                    if legacy_membership_id:
                        db.execute(
                            """UPDATE member_memberships SET status = 'Pending'
                               WHERE id = ?""",
                            (membership["membership_id"],),
                        )
                payment_id = payment_result.lastrowid
                _app._assign_payment_receipt(db, payment_id, payment_timestamp)
                db.commit()
            except DatabaseIntegrityError:
                db.rollback()
                existing_payment = db.execute(
                    "SELECT id FROM payments WHERE checkout_token = ?",
                    (checkout_token,),
                ).fetchone()
                if existing_payment:
                    return redirect(url_for(
                        "memberships.memberships",
                        step="done", payment_id=existing_payment["id"],
                    ))
                current_app.logger.exception("Membership checkout payment could not be recorded")
                flash("Payment could not be recorded. Please try again.", "error")
                return redirect(url_for("memberships.memberships"))
            except DatabaseError:
                db.rollback()
                current_app.logger.exception("Membership checkout payment could not be recorded")
                flash("Payment could not be recorded. Please try again.", "error")
                return redirect(url_for("memberships.memberships"))

            if method == "Cash":
                flash("Cash payment recorded. The membership is active.", "success")
            else:
                flash(
                    "Payment submitted. Waiting for confirmation. The membership becomes "
                    "Active once the admin confirms the GCash payment.",
                    "success",
                )
            return redirect(url_for(
                "memberships.memberships", step="done", payment_id=payment_id
            ))

        if action == "add_plan":
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            duration_days = request.form.get("duration_days", type=int)
            price = _money_value(request.form.get("price", ""))
            if not name or len(name) > 80:
                flash("Enter a plan name up to 80 characters.", "error")
                show_create_form = True
            elif len(description) > 240:
                flash("Plan descriptions must be 240 characters or fewer.", "error")
                show_create_form = True
            elif duration_days is None or not 1 <= duration_days <= 3650 or price is None:
                flash("Enter a duration from 1 to 3,650 days and a valid price of zero or more.", "error")
                show_create_form = True
            else:
                try:
                    db.execute(
                        """INSERT INTO membership_plans
                           (name, duration_days, price, description)
                           VALUES (?, ?, ?, ?)""",
                        (name, duration_days, float(price), description),
                    )
                    db.commit()
                except DatabaseIntegrityError:
                    flash("A plan with that name already exists.", "error")
                    show_create_form = True
                else:
                    flash("Membership plan created.", "success")
                    return redirect(url_for("memberships.memberships"))
        else:
            flash("Unknown membership action.", "error")

    page_context = _membership_page_context(db, request.args)
    page_context.update({
        "username": session["username"],
        "show_create_form": show_create_form,
    })
    return _app.render_template("memberships.html", **page_context)


@memberships_bp.route("/memberships/<int:plan_id>/toggle", methods=["POST"])
@login_required
def toggle_plan(plan_id):
    db = _app.get_db()
    plan = db.execute(
        "SELECT name FROM membership_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if plan and plan["name"].strip().casefold() == "monthly membership":
        flash("The standard Monthly Membership cannot be deactivated.", "error")
        return redirect(url_for("memberships.memberships"))
    result = db.execute(
        "UPDATE membership_plans SET active = 1 - active WHERE id = ?",
        (plan_id,),
    )
    db.commit()
    flash("Plan status updated." if result.rowcount else "Plan not found.",
          "success" if result.rowcount else "error")
    return redirect(url_for("memberships.memberships"))


@memberships_bp.route(
    "/memberships/<int:membership_id>/remove", methods=["POST"]
)
@admin_required
def remove_membership(membership_id):
    db = _app.get_db()
    try:
        db.execute("START TRANSACTION")
        membership = db.execute(
            "SELECT id, member_id FROM member_memberships WHERE id = ? FOR UPDATE",
            (membership_id,),
        ).fetchone()
        if membership is None:
            db.rollback()
            flash("Membership record was not found.", "error")
            return redirect(url_for("memberships.memberships"))

        db.execute(
            "DELETE FROM payments WHERE membership_id = ?",
            (membership_id,),
        )
        result = db.execute(
            "DELETE FROM member_memberships WHERE id = ?",
            (membership_id,),
        )
        if result.rowcount != 1:
            raise DatabaseIntegrityError("Membership could not be removed.")
        remaining_membership = db.execute(
            """SELECT p.name FROM member_memberships mm
               JOIN membership_plans p ON p.id = mm.plan_id
               WHERE mm.member_id = ?
               ORDER BY mm.id DESC LIMIT 1""",
            (membership["member_id"],),
        ).fetchone()
        db.execute(
            "UPDATE members SET membership_type = ? WHERE id = ?",
            (
                remaining_membership["name"] if remaining_membership else "",
                membership["member_id"],
            ),
        )
        db.commit()
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("Membership and linked payments could not be removed")
        flash("Membership could not be removed. No records were deleted.", "error")
        return redirect(url_for("memberships.memberships"))

    flash("Membership and its linked payment records were removed.", "success")
    return redirect(url_for("memberships.memberships"))


@memberships_bp.route("/memberships/<int:plan_id>/edit", methods=["GET", "POST"])
@login_required
def edit_plan(plan_id):
    db = _app.get_db()
    plan = db.execute(
        "SELECT * FROM membership_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if plan is None:
        flash("Membership plan not found.", "error")
        return redirect(url_for("memberships.memberships"))
    if plan["name"].strip().casefold() == "monthly membership":
        flash("Change the standard Monthly Membership price in Settings.", "error")
        return redirect(url_for("memberships.memberships"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        duration_days = request.form.get("duration_days", type=int)
        price = _money_value(request.form.get("price", ""))
        if not name or len(name) > 80:
            flash("Enter a plan name up to 80 characters.", "error")
        elif len(description) > 240:
            flash("Plan descriptions must be 240 characters or fewer.", "error")
        elif duration_days is None or not 1 <= duration_days <= 3650 or price is None:
            flash("Enter a duration from 1 to 3,650 days and a valid price of zero or more.", "error")
        else:
            try:
                db.execute(
                    """UPDATE membership_plans
                       SET name = ?, duration_days = ?, price = ?, description = ?
                       WHERE id = ?""",
                    (name, duration_days, float(price), description, plan_id),
                )
                db.execute(
                    "UPDATE members SET membership_type = ? WHERE membership_type = ?",
                    (name, plan["name"]),
                )
                db.commit()
            except DatabaseIntegrityError:
                db.rollback()
                flash("A plan with that name already exists.", "error")
            else:
                flash("Membership plan updated.", "success")
                return redirect(url_for("memberships.memberships"))
    return _app.render_template(
        "plan_form.html", username=session["username"], plan=plan,
        monthly_fee=float(_app.setting_values(db)["monthly_fee"]),
    )
