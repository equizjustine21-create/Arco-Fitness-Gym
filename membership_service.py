"""Membership checkout operations that coordinate domain rules and persistence."""

from datetime import date, timedelta

from domain_models import MembershipPlan


class MembershipService:
    def __init__(self, database, current_date_provider):
        self.__database = database
        self.__current_date_provider = current_date_provider

    def checkout_details(self, member_id, plan_id, requested_start, lock=False):
        member_lock = " FOR UPDATE" if lock else ""
        member = self.__database.execute(
            "SELECT id, full_name FROM members WHERE id = ? AND lower(status) = 'active'"
            + member_lock,
            (member_id,),
        ).fetchone()
        plan_record = self.__database.execute(
            """SELECT id, name, duration_days, price, description
               FROM membership_plans WHERE id = ? AND active = 1""",
            (plan_id,),
        ).fetchone()
        if member is None or plan_record is None:
            return None, "Select an active member and an available plan."
        if requested_start is None:
            return None, "Choose a valid membership start date."

        today = self.__current_date_provider()
        if requested_start < today:
            return None, "The membership start date cannot be in the past."

        active_membership = self.__database.execute(
            """SELECT end_date FROM member_memberships
               WHERE member_id = ? AND lower(status) = 'active' AND end_date >= ?
               ORDER BY end_date DESC, id DESC LIMIT 1""",
            (member_id, today.isoformat()),
        ).fetchone()
        adjusted = False
        active_end = None
        start = requested_start
        if active_membership:
            active_end = date.fromisoformat(str(active_membership["end_date"]))
            if start <= active_end:
                start = active_end + timedelta(days=1)
                adjusted = True

        plan = MembershipPlan.from_record(plan_record)
        end = plan.end_date(start)
        overlapping = self.__database.execute(
            """SELECT id FROM member_memberships
               WHERE member_id = ? AND lower(status) IN ('active', 'pending')
                 AND start_date <= ? AND end_date >= ?
               LIMIT 1""",
            (member_id, end.isoformat(), start.isoformat()),
        ).fetchone()
        if overlapping:
            return None, "This member already has a pending membership during those dates."
        return {
            "member": member,
            "plan": plan_record,
            "member_id": member["id"],
            "plan_id": plan_record["id"],
            "full_name": member["full_name"],
            "plan_name": plan.name,
            "duration_days": plan.duration_days,
            "price": plan.price,
            "description": plan.description,
            "start_date": start,
            "end_date": end,
            "active_end": active_end,
            "date_adjusted": adjusted,
        }, None
