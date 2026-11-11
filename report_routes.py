"""Admin report page and exports."""

import app as _app
from blueprints import reports_bp
from report_service import build_report_data
from app import (
    A4,
    BytesIO,
    DatabaseError,
    Paragraph,
    ParagraphStyle,
    Response,
    SimpleDocTemplate,
    TA_CENTER,
    Table,
    TableStyle,
    _valid_date,
    admin_required,
    colors,
    csv,
    current_app,
    escape,
    flash,
    getSampleStyleSheet,
    landscape,
    mm,
    redirect,
    request,
    send_file,
    session,
    StringIO,
    timedelta,
    url_for,
)

__all__ = ["export_report", "export_report_pdf", "reports", "void_report_payment"]


def _query_date_range():
    today = _app.philippine_date()
    raw_start = request.args.get("from") or request.args.get("start")
    raw_end = request.args.get("to") or request.args.get("end")
    start = _valid_date(raw_start or "") if raw_start else today.replace(day=1)
    end = _valid_date(raw_end or "") if raw_end else today
    valid = start is not None and end is not None and start <= end
    if not valid:
        start, end = today.replace(day=1), today
    return start, end, valid, raw_start or start.isoformat(), raw_end or end.isoformat()


def _filters():
    method = request.args.get("method", "All")
    status = request.args.get("status", "All")
    if method not in {"All", "Cash", "GCash"}:
        method = "All"
    if status not in {"All", "Confirmed", "Pending"}:
        status = "All"
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    return method, status, page


def _format_reference(payment):
    reference = payment.get("reference_number")
    if not reference:
        return "—"
    if str(payment.get("method", "")).lower() == "gcash":
        return f"••••{str(reference)[-4:]}"
    return str(reference)


def _generated_at():
    return _app.gym_now().strftime("%b %d, %Y %I:%M %p PHT")


def _report_payload(data, start, end, method, status, page):
    period_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    previous = build_report_data(
        _app.get_db(), previous_start, previous_end, paginate=False
    )
    previous_income = previous["total_income"]
    current_income = data["total_income"]
    if previous_income:
        income_change = (
            (current_income - previous_income) * 100 / previous_income
        )
    else:
        income_change = None
    chart_daily = [
        {
            "day": row["day"],
            "walkin_income": float(row["walkin_income"]),
            "membership_income": float(row["membership_income"]),
        }
        for row in data["daily"]
    ]
    return {
        "username": session["username"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "input_start": request.args.get("from", start.isoformat()),
        "input_end": request.args.get("to", end.isoformat()),
        "range_label": f"{start.isoformat()} to {end.isoformat()}",
        "today_date": _app.philippine_date().isoformat(),
        "period_days": period_days,
        "data": data,
        "previous": previous,
        "income_change": income_change,
        "chart_daily": chart_daily,
        "method_filter": method,
        "status_filter": status,
        "page": page,
        "generated_at": _generated_at(),
        "date_validation_error": False,
    }


@reports_bp.route("/reports")
@admin_required
def reports():
    start, end, valid_range, input_start, input_end = _query_date_range()
    method, status, page = _filters()
    if not valid_range:
        flash("To date must be on or after From date. Please correct the date range.", "error")
    data = build_report_data(
        _app.get_db(), start, end, method=method, status=status, page=page
    )
    context = _report_payload(data, start, end, method, status, page)
    context["input_start"] = input_start
    context["input_end"] = input_end
    context["date_validation_error"] = not valid_range
    return _app.render_template("reports.html", **context)


def _export_data():
    start = _valid_date(request.args.get("from") or request.args.get("start", ""))
    end = _valid_date(request.args.get("to") or request.args.get("end", ""))
    if start is None or end is None or start > end:
        flash("To date must be on or after From date before exporting.", "error")
        return None
    method, status, _ = _filters()
    data = build_report_data(
        _app.get_db(), start, end, method=method, status=status, paginate=False
    )
    return start, end, method, status, data


@reports_bp.route("/reports/export")
@admin_required
def export_report():
    export = _export_data()
    if export is None:
        return redirect(url_for("reports.reports"))
    start, end, method, status, data = export
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(("ARCO FITNESS REPORT",))
    writer.writerow(("Date range", start.isoformat(), "to", end.isoformat()))
    writer.writerow(("Generated on", _generated_at()))
    writer.writerow(("Method filter", method, "Status filter", status))
    writer.writerow(())
    writer.writerow(("Summary", "Amount / count"))
    writer.writerow(("Total income (PHP)", f'{data["total_income"]:.2f}'))
    writer.writerow(("Walk-in income (PHP)", f'{data["walkin_income"]:.2f}'))
    writer.writerow(("Membership income (PHP)", f'{data["membership_income"]:.2f}'))
    writer.writerow(("Payments received", data["payment_count"]))
    writer.writerow(("Confirmed membership payments", data["confirmed_payment_count"]))
    writer.writerow(("Member check-ins", data["member_checkins"]))
    writer.writerow(("Walk-in visits", data["walkin_visits"]))
    writer.writerow(("New members", data["new_members"]))
    writer.writerow(("New members with a paid plan", data["paid_members"]))
    writer.writerow(())
    writer.writerow(("Accuracy check", "Income (PHP)"))
    writer.writerow(("Total income (summary)", f'{data["total_income"]:.2f}'))
    writer.writerow(("Payment history total", f'{data["history_all_income"]:.2f}'))
    writer.writerow(("Daily breakdown total", f'{data["daily_income_total"]:.2f}'))
    writer.writerow(("Accuracy result", "All totals match" if data["accuracy_matches"] else "WARNING: totals differ"))
    writer.writerow(("Income rule", "Only Confirmed and Paid payments count as income."))
    writer.writerow(())
    writer.writerow(("Confirmed income by payment method", "Count", "Amount (PHP)", "Percent"))
    for item in data["method_breakdown"]:
        writer.writerow((item["method"], item["count"], f'{item["amount"]:.2f}', f'{item["percent"]:.1f}%'))
    writer.writerow(())
    writer.writerow(("Payment status", "Count", "Amount (PHP)"))
    for status_name in ("Confirmed", "Pending", "Rejected", "Voided"):
        item = data["payment_statuses"][status_name]
        writer.writerow((status_name, item["count"], f'{item["amount"]:.2f}'))
    writer.writerow(())
    writer.writerow((
        "Daily breakdown", "Walk-in visits", "Member check-ins",
        "Walk-in income (PHP)", "Membership income (PHP)", "Total income (PHP)",
    ))
    for row in data["daily_desc"]:
        writer.writerow((
            row["day"], row["walkin_visits"], row["member_checkins"],
            f'{row["walkin_income"]:.2f}', f'{row["membership_income"]:.2f}',
            f'{row["total_income"]:.2f}',
        ))
    writer.writerow((
        "TOTAL", data["walkin_visits"], data["member_checkins"],
        f'{data["walkin_income"]:.2f}', f'{data["membership_income"]:.2f}',
        f'{data["total_income"]:.2f}',
    ))
    writer.writerow(())
    writer.writerow((
        "Payment history", "Member", "Type", "Method", "Reference",
        "Amount (PHP)", "Status", "Receipt no.",
    ))
    for row in data["history"]:
        writer.writerow((
            _app._safe_csv_value(row["transaction_date"]),
            _app._safe_csv_value(row["member_name"]),
            _app._safe_csv_value(row["transaction_type"]),
            _app._safe_csv_value(row["method"]),
            _app._safe_csv_value(_format_reference(row)),
            f'{row["amount"]:.2f}',
            _app._safe_csv_value(row["payment_status"]),
            _app._safe_csv_value(row["receipt_no"] or "—"),
        ))
    filename = f"arco-report-{start.isoformat()}-to-{end.isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reports_bp.route("/reports/export/pdf")
@admin_required
def export_report_pdf():
    export = _export_data()
    if export is None:
        return redirect(url_for("reports.reports"))
    start, end, method, status, data = export
    generated_at = _generated_at()
    range_label = f"{start.isoformat()} to {end.isoformat()}"
    pdf_buffer = BytesIO()
    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=landscape(A4),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"ARCO FITNESS Activity Report {range_label}",
        author="ARCO FITNESS",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"],
        textColor=colors.HexColor("#252936"), alignment=TA_CENTER,
        fontSize=19, leading=23, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="ReportRange", parent=styles["Normal"],
        textColor=colors.HexColor("#686d78"), alignment=TA_CENTER,
        fontSize=9, spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        name="ReportSection", parent=styles["Heading2"],
        textColor=colors.HexColor("#252936"), fontSize=12,
        leading=15, spaceBefore=12, spaceAfter=7,
    ))
    body_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#252936")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dfe2e8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
    ]
    available_width = A4[1] - 28 * mm
    daily_data = [[
        "DATE", "WALK-IN VISITS", "MEMBER CHECK-INS", "WALK-IN INCOME",
        "MEMBERSHIP INCOME", "TOTAL INCOME",
    ]]
    daily_data.extend([
        [
            row["day"], str(row["walkin_visits"]), str(row["member_checkins"]),
            f"PHP {row['walkin_income']:,.2f}",
            f"PHP {row['membership_income']:,.2f}",
            f"PHP {row['total_income']:,.2f}",
        ]
        for row in data["daily_desc"]
    ])
    daily_data.append([
        "TOTAL", str(data["walkin_visits"]), str(data["member_checkins"]),
        f"PHP {data['walkin_income']:,.2f}",
        f"PHP {data['membership_income']:,.2f}",
        f"PHP {data['total_income']:,.2f}",
    ])
    daily_table = Table(
        daily_data, repeatRows=1,
        colWidths=[available_width * ratio for ratio in (.16, .15, .16, .17, .18, .18)],
    )
    daily_table.setStyle(TableStyle(body_style + [
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eceef2")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))

    payment_data = [[
        "DATE", "MEMBER", "TYPE", "METHOD", "REFERENCE",
        "AMOUNT", "STATUS", "RECEIPT NO.",
    ]]
    for payment in data["history"]:
        payment_data.append([
            str(payment["transaction_date"]),
            str(payment["member_name"]),
            str(payment["transaction_type"]),
            str(payment["method"]),
            _format_reference(payment),
            f"PHP {payment['amount']:,.2f}",
            str(payment["payment_status"]),
            str(payment["receipt_no"] or "—"),
        ])
    if len(payment_data) == 1:
        payment_data.append(["No matching records.", "", "", "", "", "", "", ""])
    payment_table = Table(
        payment_data, repeatRows=1,
        colWidths=[available_width * ratio for ratio in (.13, .17, .12, .10, .14, .12, .11, .11)],
    )
    payment_table.setStyle(TableStyle(body_style + [
        ("ALIGN", (5, 1), (5, -1), "RIGHT"),
    ]))
    accuracy_label = (
        "All totals match"
        if data["accuracy_matches"] else "WARNING: report totals do not match"
    )
    summary = Table([
        ["TOTAL INCOME", "WALK-IN INCOME", "MEMBERSHIP INCOME",
         "PAYMENTS RECEIVED", "MEMBER CHECK-INS", "NEW MEMBERS"],
        [
            f"PHP {data['total_income']:,.2f}",
            f"PHP {data['walkin_income']:,.2f}",
            f"PHP {data['membership_income']:,.2f}",
            str(data["payment_count"]), str(data["member_checkins"]),
            f"{data['new_members']} ({data['paid_members']} paid)",
        ],
    ], colWidths=[available_width / 6] * 6)
    summary.setStyle(TableStyle(body_style + [
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    accuracy_data = [
        ["ACCURACY CHECK", "TOTAL (PHP)"],
        ["Total income (summary)", f'{data["total_income"]:,.2f}'],
        ["Payment history total", f'{data["history_all_income"]:,.2f}'],
        ["Daily breakdown total", f'{data["daily_income_total"]:,.2f}'],
        [
            "Result",
            "All totals match" if data["accuracy_matches"] else "WARNING: totals differ",
        ],
        ["Income rule", "Only Confirmed and Paid payments count as income."],
    ]
    accuracy_table = Table(
        accuracy_data,
        colWidths=[available_width * .66, available_width * .34],
    )
    accuracy_table.setStyle(TableStyle(body_style + [
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -2), (-1, -2),
         colors.HexColor("#E3F5EA") if data["accuracy_matches"] else colors.HexColor("#fff0ef")),
    ]))
    method_data = [["METHOD", "COUNT", "AMOUNT (PHP)", "PERCENT"]]
    method_data.extend([
        [item["method"], str(item["count"]), f'{item["amount"]:,.2f}', f'{item["percent"]:.1f}%']
        for item in data["method_breakdown"]
    ] or [["No confirmed payments", "0", "0.00", "0.0%"]])
    method_table = Table(
        method_data,
        colWidths=[available_width * ratio for ratio in (.34, .18, .28, .20)],
    )
    method_table.setStyle(TableStyle(body_style + [
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    status_data = [["PAYMENT STATUS", "COUNT", "AMOUNT (PHP)"]]
    status_data.extend([
        [
            name, str(data["payment_statuses"][name]["count"]),
            f'{data["payment_statuses"][name]["amount"]:,.2f}',
        ]
        for name in ("Confirmed", "Pending", "Rejected", "Voided")
    ])
    status_table = Table(
        status_data,
        colWidths=[available_width * ratio for ratio in (.42, .22, .36)],
    )
    status_table.setStyle(TableStyle(body_style + [
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story = [
        Paragraph("ARCO FITNESS — ACTIVITY REPORT", styles["ReportTitle"]),
        Paragraph(
            f"Date range: {range_label}<br/>Generated on: {generated_at}"
            f"<br/>Filters: {method} / {status}",
            styles["ReportRange"],
        ),
        summary,
        Paragraph(escape(accuracy_label), styles["ReportSection"]),
        accuracy_table,
        Paragraph("Cash and GCash breakdown", styles["ReportSection"]),
        method_table,
        Paragraph("Payment status", styles["ReportSection"]),
        status_table,
        Paragraph("Daily breakdown", styles["ReportSection"]),
        daily_table,
        Paragraph("Payment history", styles["ReportSection"]),
        payment_table,
    ]

    def draw_page_number(canvas, _):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#686d78"))
        canvas.drawRightString(A4[1] - 14 * mm, 8 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    pdf_buffer.seek(0)
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"arco-report-{start.isoformat()}-to-{end.isoformat()}.pdf",
    )


@reports_bp.route("/reports/payments/<int:payment_id>/void", methods=["POST"])
@admin_required
def void_report_payment(payment_id):
    reason = request.form.get("reason", "").strip()
    start = _valid_date(request.form.get("from", "")) or _app.philippine_date().replace(day=1)
    end = _valid_date(request.form.get("to", "")) or _app.philippine_date()
    method = request.form.get("method", "All")
    status = request.form.get("status", "All")
    if not reason or len(reason) > 500:
        flash("Enter a void reason of 1 to 500 characters.", "error")
        return redirect(url_for(
            "reports.reports", **{"from": start.isoformat(), "to": end.isoformat(),
                                  "method": method, "status": status},
        ))

    db = _app.get_db()
    try:
        db.execute("START TRANSACTION")
        payment = db.execute(
            """SELECT payment_status FROM payments WHERE id = ? FOR UPDATE""",
            (payment_id,),
        ).fetchone()
        if payment is None or str(payment["payment_status"]).lower() not in {
            "confirmed", "paid",
        }:
            db.rollback()
            flash("Only a Confirmed or Paid payment can be voided.", "error")
        else:
            result = db.execute(
                """UPDATE payments
                   SET payment_status = 'Voided', voided_by = ?,
                       voided_at = ?, void_reason = ?
                   WHERE id = ? AND LOWER(payment_status) IN ('confirmed', 'paid')""",
                (session["user_id"], _app.philippine_now(), reason, payment_id),
            )
            if result.rowcount != 1:
                db.rollback()
                flash("Payment status changed; it was not voided.", "error")
            else:
                db.commit()
                flash(
                    "Payment voided and hidden from report history. "
                    "Its record and audit details were preserved.",
                    "success",
                )
    except DatabaseError:
        db.rollback()
        current_app.logger.exception("Report payment could not be voided")
        flash("Payment could not be voided. Please try again.", "error")
    return redirect(url_for(
        "reports.reports", **{"from": start.isoformat(), "to": end.isoformat(),
                              "method": method, "status": status},
    ))
