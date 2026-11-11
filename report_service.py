"""Shared, Decimal-safe data queries for report pages and exports."""

from datetime import datetime, time, timedelta
from decimal import Decimal


ZERO = Decimal("0.00")
PAGE_SIZE = 25


def _money(value):
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _value(row, key, default=0):
    return row[key] if row and row.get(key) is not None else default


def _transaction_query(start, end):
    start_at = datetime.combine(start, time.min)
    end_at = datetime.combine(end + timedelta(days=1), time.min)
    return (
        """SELECT CONCAT('p-', p.id) AS record_key, p.id AS sort_id,
                  DATE_FORMAT(p.paid_at, '%%Y-%%m-%%d %%H:%%i') AS transaction_date,
                  DATE(p.paid_at) AS transaction_day,
                  COALESCE(m.full_name, 'Removed member') AS member_name,
                  CASE WHEN p.membership_id IS NULL THEN 'Other payment'
                       ELSE 'Membership' END AS transaction_type,
                  p.method AS method,
                  p.reference_number AS reference_number,
                  CASE WHEN LOWER(p.payment_status) = 'paid' THEN 'Confirmed'
                       ELSE p.payment_status END AS payment_status,
                  p.receipt_no AS receipt_no, p.amount AS amount,
                  p.note AS note, 'payment' AS source
           FROM payments p
           LEFT JOIN members m ON m.id = p.member_id
           WHERE p.paid_at >= ? AND p.paid_at < ?
           UNION ALL
           SELECT CONCAT('w-', w.id) AS record_key, w.id AS sort_id,
                  CONCAT(w.visit_date, ' ', w.checkin_time) AS transaction_date,
                  w.visit_date AS transaction_day, w.full_name AS member_name,
                  'Walk-in' AS transaction_type, 'Cash' AS method,
                  NULL AS reference_number,
                  CASE WHEN LOWER(w.fee_status) = 'paid' THEN 'Confirmed'
                       ELSE w.fee_status END AS payment_status,
                  NULL AS receipt_no, w.fee AS amount, NULL AS note,
                  'walk-in' AS source
           FROM walkin_visits w
           WHERE w.visit_date >= ? AND w.visit_date <= ?""",
        (start_at, end_at, start.isoformat(), end.isoformat()),
    )


def _filter_clause(method, status):
    clauses = ["LOWER(report_rows.payment_status) <> 'voided'"]
    parameters = []
    if method != "All":
        clauses.append("LOWER(report_rows.method) = LOWER(?)")
        parameters.append(method)
    if status != "All":
        clauses.append("LOWER(report_rows.payment_status) = LOWER(?)")
        parameters.append(status)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), tuple(parameters)


def build_report_data(db, start, end, method="All", status="All", page=1,
                      paginate=True):
    """Return all report metrics and filtered history from the same date range."""
    if start > end:
        raise ValueError("The report start date cannot be after its end date.")
    if method not in {"All", "Cash", "GCash"}:
        method = "All"
    if status not in {"All", "Confirmed", "Pending"}:
        status = "All"
    page = max(1, int(page or 1))

    start_at, end_at = datetime.combine(start, time.min), datetime.combine(
        end + timedelta(days=1), time.min
    )
    date_bounds = (start.isoformat(), end.isoformat())
    payment_bounds = (start_at, end_at)
    payment_stats = db.execute(
        """SELECT
               COALESCE(SUM(CASE WHEN LOWER(payment_status) IN ('confirmed', 'paid')
                                 THEN amount ELSE 0 END), 0) AS income,
               SUM(CASE WHEN LOWER(payment_status) IN ('confirmed', 'paid')
                        THEN 1 ELSE 0 END) AS confirmed_count
           FROM payments WHERE paid_at >= ? AND paid_at < ?""",
        payment_bounds,
    ).fetchone()
    payment_income = _money(_value(payment_stats, "income", ZERO))
    confirmed_payment_count = int(_value(payment_stats, "confirmed_count", 0) or 0)

    payment_status_rows = db.execute(
        """SELECT CASE WHEN LOWER(payment_status) = 'paid' THEN 'Confirmed'
                       ELSE payment_status END AS status,
                  COUNT(*) AS payment_count, COALESCE(SUM(amount), 0) AS amount
           FROM payments WHERE paid_at >= ? AND paid_at < ?
           GROUP BY CASE WHEN LOWER(payment_status) = 'paid' THEN 'Confirmed'
                         ELSE payment_status END""",
        payment_bounds,
    ).fetchall()
    payment_statuses = {
        "Confirmed": {"count": 0, "amount": ZERO},
        "Pending": {"count": 0, "amount": ZERO},
        "Rejected": {"count": 0, "amount": ZERO},
        "Voided": {"count": 0, "amount": ZERO},
    }
    for row in payment_status_rows:
        key = str(row["status"] or "").title()
        if key in payment_statuses:
            payment_statuses[key] = {
                "count": int(row["payment_count"] or 0),
                "amount": _money(row["amount"]),
            }

    method_rows = db.execute(
        """SELECT method, COUNT(*) AS payment_count, COALESCE(SUM(amount), 0) AS amount
           FROM payments WHERE paid_at >= ? AND paid_at < ?
             AND LOWER(payment_status) IN ('confirmed', 'paid')
           GROUP BY method ORDER BY method""",
        payment_bounds,
    ).fetchall()
    method_breakdown = [
        {
            "method": row["method"] or "Other",
            "count": int(row["payment_count"] or 0),
            "amount": _money(row["amount"]),
            "percent": (
                (_money(row["amount"]) * Decimal("100") / payment_income).quantize(
                    Decimal("0.1")
                )
                if payment_income else ZERO
            ),
        }
        for row in method_rows
    ]

    walkin_daily_rows = db.execute(
        """SELECT visit_date AS day, COUNT(*) AS visits,
                  SUM(CASE WHEN LOWER(fee_status) IN ('confirmed', 'paid')
                           THEN fee ELSE 0 END) AS income,
                  SUM(CASE WHEN LOWER(fee_status) IN ('confirmed', 'paid')
                           THEN 1 ELSE 0 END) AS paid_visits
           FROM walkin_visits WHERE visit_date BETWEEN ? AND ?
           GROUP BY visit_date""",
        date_bounds,
    ).fetchall()
    walkin_by_day = {
        row["day"]: (
            int(row["visits"] or 0),
            _money(row["income"]),
            int(row["paid_visits"] or 0),
        )
        for row in walkin_daily_rows
    }
    walkin_fee_rows = db.execute(
        """SELECT fee, COUNT(*) AS paid_visits
           FROM walkin_visits WHERE visit_date BETWEEN ? AND ?
             AND LOWER(fee_status) IN ('confirmed', 'paid')
           GROUP BY fee ORDER BY fee""",
        date_bounds,
    ).fetchall()
    walkin_fee_breakdown = [
        {"fee": _money(row["fee"]), "count": int(row["paid_visits"] or 0)}
        for row in walkin_fee_rows
    ]
    payment_daily_rows = db.execute(
        """SELECT DATE(paid_at) AS day, COALESCE(SUM(amount), 0) AS income
           FROM payments WHERE paid_at >= ? AND paid_at < ?
             AND LOWER(payment_status) IN ('confirmed', 'paid')
           GROUP BY DATE(paid_at)""",
        payment_bounds,
    ).fetchall()
    membership_by_day = {
        str(row["day"]): _money(row["income"]) for row in payment_daily_rows
    }
    attendance_rows = db.execute(
        """SELECT checkin_date AS day, COUNT(*) AS checkins
           FROM attendance WHERE checkin_date BETWEEN ? AND ?
           GROUP BY checkin_date""",
        date_bounds,
    ).fetchall()
    attendance_by_day = {
        row["day"]: int(row["checkins"] or 0) for row in attendance_rows
    }

    daily = []
    day = start
    walkin_income = ZERO
    walkin_visits = 0
    paid_walkin_count = 0
    member_checkins = 0
    while day <= end:
        key = day.isoformat()
        visits, day_walkin_income, paid_visits = walkin_by_day.get(
            key, (0, ZERO, 0)
        )
        day_membership_income = membership_by_day.get(key, ZERO)
        day_member_checkins = attendance_by_day.get(key, 0)
        daily.append({
            "day": key,
            "walkin_visits": visits,
            "member_checkins": day_member_checkins,
            "checkins": day_member_checkins,
            "walkin_income": day_walkin_income,
            "membership_income": day_membership_income,
            "total_income": day_walkin_income + day_membership_income,
        })
        walkin_visits += visits
        paid_walkin_count += paid_visits
        walkin_income += day_walkin_income
        member_checkins += day_member_checkins
        day += timedelta(days=1)

    member_rows = db.execute(
        """SELECT COUNT(*) AS member_count FROM members
           WHERE DATE(created_at) BETWEEN ? AND ?""",
        date_bounds,
    ).fetchone()
    paid_member_rows = db.execute(
        """SELECT COUNT(DISTINCT p.member_id) AS member_count
           FROM payments p
           JOIN members m ON m.id = p.member_id
           WHERE p.paid_at >= ? AND p.paid_at < ?
             AND DATE(m.created_at) BETWEEN ? AND ?
             AND p.membership_id IS NOT NULL
             AND LOWER(p.payment_status) IN ('confirmed', 'paid')""",
        payment_bounds + date_bounds,
    ).fetchone()
    pending_gcash = db.execute(
        """SELECT COUNT(*) AS payment_count, COALESCE(SUM(amount), 0) AS amount
           FROM payments WHERE paid_at >= ? AND paid_at < ?
             AND LOWER(payment_status) = 'pending'
             AND LOWER(method) = 'gcash'""",
        payment_bounds,
    ).fetchone()

    union_sql, union_parameters = _transaction_query(start, end)
    all_transactions = f"({union_sql}) AS report_rows"
    all_history_total = db.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN LOWER(report_rows.payment_status)
                                  IN ('confirmed', 'paid')
                                  THEN report_rows.amount ELSE 0 END), 0) AS income
            FROM {all_transactions}""",
        union_parameters,
    ).fetchone()
    all_history_income = _money(_value(all_history_total, "income", ZERO))

    grouped = db.execute(
        f"""SELECT report_rows.method, report_rows.payment_status,
                   COUNT(*) AS row_count
            FROM {all_transactions}
            WHERE LOWER(report_rows.payment_status) <> 'voided'
            GROUP BY report_rows.method, report_rows.payment_status""",
        union_parameters,
    ).fetchall()
    method_counts = {"All": 0, "Cash": 0, "GCash": 0}
    status_counts = {"All": 0, "Confirmed": 0, "Pending": 0}
    for row in grouped:
        row_method = str(row["method"] or "Other")
        row_status = str(row["payment_status"] or "").title()
        row_count = int(row["row_count"] or 0)
        if row_method in method_counts:
            if status == "All" or row_status == status:
                method_counts[row_method] += row_count
        if status == "All" or row_status == status:
            method_counts["All"] += row_count
        if row_status in status_counts:
            if method == "All" or row_method.lower() == method.lower():
                status_counts[row_status] += row_count
        if method == "All" or row_method.lower() == method.lower():
            status_counts["All"] += row_count

    filter_clause, filter_parameters = _filter_clause(method, status)
    history_summary = db.execute(
        f"""SELECT COUNT(*) AS row_count,
                   COALESCE(SUM(CASE WHEN LOWER(report_rows.payment_status)
                                     IN ('confirmed', 'paid')
                                     THEN report_rows.amount ELSE 0 END), 0) AS income
            FROM {all_transactions}{filter_clause}""",
        union_parameters + filter_parameters,
    ).fetchone()
    history_count = int(_value(history_summary, "row_count", 0) or 0)
    history_income = _money(_value(history_summary, "income", ZERO))
    page_count = max(1, (history_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, page_count)
    history_sql = f"""SELECT report_rows.* FROM {all_transactions}{filter_clause}
                      ORDER BY report_rows.transaction_day DESC,
                               report_rows.sort_id DESC"""
    history_parameters = union_parameters + filter_parameters
    if paginate:
        history_sql += " LIMIT ? OFFSET ?"
        history_parameters += (PAGE_SIZE, (page - 1) * PAGE_SIZE)
    history = db.execute(history_sql, history_parameters).fetchall()

    daily_income_total = sum((row["total_income"] for row in daily), ZERO)
    total_income = payment_income + walkin_income
    return {
        "start": start,
        "end": end,
        "daily": daily,
        "daily_desc": list(reversed(daily)),
        "total_income": total_income,
        "payment_income": payment_income,
        "membership_income": payment_income,
        "walkin_income": walkin_income,
        "walkin_visits": walkin_visits,
        "paid_walkin_count": paid_walkin_count,
        "walkin_fee_breakdown": walkin_fee_breakdown,
        "confirmed_payment_count": confirmed_payment_count,
        "payment_count": confirmed_payment_count + paid_walkin_count,
        "member_checkins": member_checkins,
        "new_members": int(_value(member_rows, "member_count", 0) or 0),
        "paid_members": int(_value(paid_member_rows, "member_count", 0) or 0),
        "payment_statuses": payment_statuses,
        "method_breakdown": method_breakdown,
        "pending_gcash_count": int(_value(pending_gcash, "payment_count", 0) or 0),
        "pending_gcash_amount": _money(_value(pending_gcash, "amount", ZERO)),
        "history": history,
        "history_count": history_count,
        "history_income": history_income,
        "history_all_income": all_history_income,
        "history_income_matches": history_income == total_income,
        "daily_income_total": daily_income_total,
        "accuracy_matches": (
            total_income == all_history_income == daily_income_total
        ),
        "method_counts": method_counts,
        "status_counts": status_counts,
        "method_filter": method,
        "status_filter": status,
        "page": page,
        "page_count": page_count,
        "page_size": PAGE_SIZE,
    }
