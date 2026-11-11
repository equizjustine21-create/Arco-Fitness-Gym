"""Payments routes for the Arco Fitness application."""

import app as _app
from blueprints import payments_bp
from app import (
    DatabaseError,
    DatabaseIntegrityError,
    admin_required,
    current_app,
    flash,
    login_required,
    redirect,
    request,
    session,
    url_for,
)

__all__ = ['payments', 'update_gcash_payment_status']


@payments_bp.route("/payments")
@login_required
def payments():
    db = _app.get_db()
    status_filter = request.args.get("status", "All")
    method_filter = request.args.get("method", "All")
    if status_filter not in {"All", "Confirmed", "Pending", "Rejected", "Voided"}:
        status_filter = "All"
    if method_filter not in {"All", "Cash", "GCash"}:
        method_filter = "All"
    clauses = ["RIGHT(m.email, 16) <> '@deleted.invalid'"]
    parameters = []
    if status_filter != "All":
        clauses.append("LOWER(p.payment_status) = LOWER(?)")
        parameters.append(status_filter)
    if method_filter != "All":
        clauses.append("LOWER(p.method) = LOWER(?)")
        parameters.append(method_filter)
    rows = db.execute(
        f"""SELECT p.*, m.full_name
           FROM payments p JOIN members m ON m.id = p.member_id
           WHERE {' AND '.join(clauses)}
           ORDER BY p.paid_at DESC, p.id DESC LIMIT 200""",
        tuple(parameters),
    ).fetchall()
    return _app.render_template(
        "payments.html", username=session["username"], payments=rows,
        status_filter=status_filter, method_filter=method_filter,
        total=sum(
            row["amount"] for row in rows
            if row["payment_status"].lower() in {"confirmed", "paid"}
        ),
    )


@payments_bp.route("/payments/gcash/<int:payment_id>/status", methods=["POST"])
@admin_required
def update_gcash_payment_status(payment_id):
    action = request.form.get("action", "")
    status_filter = request.form.get("return_status", "All")
    method_filter = request.form.get("return_method", "All")
    if status_filter not in {"All", "Confirmed", "Pending", "Rejected", "Voided"}:
        status_filter = "All"
    if method_filter not in {"All", "Cash", "GCash"}:
        method_filter = "All"
    return_url = url_for(
        "payments.payments",
        status=status_filter,
        method=method_filter,
    )
    if action not in ("confirm", "reject"):
        flash("Choose Confirm or Reject for this GCash payment.", "error")
        return redirect(return_url)

    db = _app.get_db()
    try:
        db.execute("START TRANSACTION")
        payment = db.execute(
            """SELECT id, member_id, membership_id, payment_status
               FROM payments
               WHERE id = ? AND method = 'GCash'
               FOR UPDATE""",
            (payment_id,),
        ).fetchone()
        if payment is None or payment["payment_status"].lower() != "pending":
            db.rollback()
            flash("This GCash payment is no longer pending.", "error")
            return redirect(return_url)

        if action == "confirm":
            membership = db.execute(
                """SELECT mm.id, mm.status, p.name AS plan_name,
                          mm.start_date, mm.end_date,
                          m.full_name, m.email, pay.amount
                   FROM member_memberships mm
                   JOIN membership_plans p ON p.id = mm.plan_id
                   JOIN members m ON m.id = mm.member_id
                   JOIN payments pay ON pay.membership_id = mm.id
                     AND pay.id = ? AND pay.member_id = m.id
                   WHERE mm.id = ? AND mm.member_id = ?
                   FOR UPDATE""",
                (payment_id, payment["membership_id"], payment["member_id"]),
            ).fetchone()
            if membership is None or membership["status"].lower() != "pending":
                db.rollback()
                flash("The related membership is not awaiting confirmation.", "error")
                return redirect(return_url)
            updated_membership = db.execute(
                """UPDATE member_memberships SET status = 'Active'
                   WHERE id = ? AND lower(status) = 'pending'""",
                (membership["id"],),
            )
            if updated_membership.rowcount != 1:
                raise DatabaseIntegrityError("Membership status changed before confirmation.")
            updated_member = db.execute(
                """UPDATE members SET membership_type = ?
                   WHERE id = ?""",
                (membership["plan_name"], payment["member_id"]),
            )
            if updated_member.rowcount != 1:
                raise DatabaseIntegrityError("Member record was unavailable during confirmation.")
            new_status = "Confirmed"
        else:
            if payment["membership_id"]:
                db.execute(
                    """UPDATE member_memberships SET status = 'Rejected'
                       WHERE id = ? AND lower(status) = 'pending'""",
                    (payment["membership_id"],),
                )
            new_status = "Rejected"

        updated_payment = db.execute(
            """UPDATE payments SET payment_status = ?
               WHERE id = ? AND lower(payment_status) = 'pending'""",
            (new_status, payment_id),
        )
        if updated_payment.rowcount != 1:
            raise DatabaseIntegrityError("Payment status changed before confirmation.")
        db.commit()
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("GCash payment status could not be updated")
        flash("GCash payment status could not be updated. Please try again.", "error")
        return redirect(return_url)

    if action == "confirm":
        amount = _app.Decimal(str(membership["amount"]))
        start_date = membership["start_date"]
        end_date = membership["end_date"]
        member_name = membership["full_name"]
        payment_text = (
            f"Hello {member_name},\n\n"
            "Your GCash payment has been confirmed.\n"
            f"Plan: {membership['plan_name']}\n"
            f"Amount: PHP {amount:,.2f}\n"
            f"Membership starts: {start_date}\n"
            f"Membership expires: {end_date}\n\n"
            "Thank you,\nArco Fitness Gym"
        )
        _app.send_email(
            membership["email"],
            "Your Arco Fitness Gym payment is confirmed",
            payment_text,
            _app.render_template(
                "emails/payment_confirmed.html",
                member_name=member_name,
                plan_name=membership["plan_name"],
                amount=f"{amount:,.2f}",
                start_date=start_date,
                end_date=end_date,
            ),
        )
    flash(f"GCash payment marked {new_status.lower()}.", "success")
    return redirect(return_url)
