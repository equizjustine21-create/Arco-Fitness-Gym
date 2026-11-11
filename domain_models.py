"""Domain entities and business rules for Arco Fitness."""

import re
from datetime import date, timedelta

from werkzeug.security import check_password_hash


class Entity:
    """Base class for persisted domain entities."""

    def __init__(self, entity_id=None):
        self.__id = entity_id

    @property
    def id(self):
        return self.__id


class User(Entity):
    """An authenticated gym admin or member account."""

    def __init__(self, username, role, password_hash, entity_id=None, email=None):
        super().__init__(entity_id)
        self.__username = username
        self.__role = role
        self.__password_hash = password_hash
        self.__email = email

    @classmethod
    def from_record(cls, record):
        return cls(
            username=record["username"],
            role=record["role"],
            password_hash=record["password_hash"],
            entity_id=record.get("id"),
            email=record.get("email"),
        )

    @property
    def username(self):
        return self.__username

    @property
    def role(self):
        return self.__role

    @property
    def email(self):
        return self.__email

    @property
    def is_admin(self):
        return self.__role == "admin"

    def verify_password(self, password):
        return check_password_hash(self.__password_hash, password)


class Member(Entity):
    """A gym member and the contact/status rules for their record."""

    EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"
    VALID_STATUSES = frozenset(("Active", "Inactive"))

    def __init__(
        self,
        full_name,
        email,
        phone,
        membership_type="",
        status="Active",
        user_id=None,
        entity_id=None,
        current_plan=None,
    ):
        super().__init__(entity_id)
        self.__full_name = full_name
        self.__email = email
        self.__phone = phone
        self.__membership_type = membership_type
        self.__status = status
        self.__user_id = user_id
        self.__current_plan = current_plan

    @classmethod
    def from_record(cls, record):
        return cls(
            full_name=record["full_name"],
            email=record["email"],
            phone=record["phone"],
            membership_type=record.get("membership_type", ""),
            status=record.get("status", "Active"),
            user_id=record.get("user_id"),
            entity_id=record.get("id"),
            current_plan=record.get("current_plan"),
        )

    @classmethod
    def from_form(cls, form, existing=None):
        return cls(
            full_name=form.get("full_name", "").strip(),
            email=form.get("email", "").strip(),
            phone=form.get("phone", "").strip(),
            membership_type=existing.membership_type if existing else "",
            status=form.get("status", "Active"),
            user_id=existing.user_id if existing else None,
            entity_id=existing.id if existing else None,
            current_plan=existing.current_plan if existing else None,
        )

    @property
    def full_name(self):
        return self.__full_name

    @property
    def email(self):
        return self.__email

    @property
    def normalized_email(self):
        return self.__email.lower()

    @property
    def phone(self):
        return self.__phone

    @property
    def membership_type(self):
        return self.__membership_type

    @property
    def status(self):
        return self.__status

    @property
    def user_id(self):
        return self.__user_id

    @property
    def current_plan(self):
        return self.__current_plan

    @property
    def is_active(self):
        return str(self.__status).lower() == "active"

    def validation_error(self, include_status=False):
        if (
            not self.__full_name
            or len(self.__email) > 254
            or not re.fullmatch(self.EMAIL_PATTERN, self.__email)
            or not self.__phone
        ):
            return "Enter a name, valid email address, and phone number."
        if include_status and self.__status not in self.VALID_STATUSES:
            return "Select a valid member status."
        return None


class MembershipPlan(Entity):
    """A purchasable membership plan and its date calculation."""

    def __init__(
        self,
        name,
        duration_days,
        price,
        description="",
        active=True,
        entity_id=None,
    ):
        super().__init__(entity_id)
        self.__name = name
        self.__duration_days = int(duration_days)
        self.__price = price
        self.__description = description
        self.__active = bool(active)

    @classmethod
    def from_record(cls, record):
        return cls(
            name=record["name"],
            duration_days=record["duration_days"],
            price=record["price"],
            description=record.get("description", ""),
            active=record.get("active", True),
            entity_id=record.get("id"),
        )

    @property
    def name(self):
        return self.__name

    @property
    def duration_days(self):
        return self.__duration_days

    @property
    def price(self):
        return self.__price

    @property
    def description(self):
        return self.__description

    @property
    def active(self):
        return self.__active

    def end_date(self, start_date):
        if self.__duration_days < 1:
            raise ValueError("Membership plan duration must be positive.")
        if not isinstance(start_date, date):
            raise TypeError("Membership start date must be a date.")
        return start_date + timedelta(days=self.__duration_days - 1)


class Membership(Entity):
    """A member's plan period and its display status at a point in time."""

    def __init__(self, start_date, end_date, status, entity_id=None):
        super().__init__(entity_id)
        self.__start_date = date.fromisoformat(str(start_date))
        self.__end_date = date.fromisoformat(str(end_date))
        self.__status = status

    @classmethod
    def from_record(cls, record):
        return cls(
            start_date=record["start_date"],
            end_date=record["end_date"],
            status=record["status"],
            entity_id=record.get("id"),
        )

    @property
    def start_date(self):
        return self.__start_date

    @property
    def end_date(self):
        return self.__end_date

    @property
    def status(self):
        return self.__status

    def display_status(self, current_date):
        if str(self.__status).lower() == "pending":
            return "Pending"
        if str(self.__status).lower() == "active" and self.__end_date >= current_date:
            return "Active"
        return "Expired"

    def status_note(self, current_date):
        display_status = self.display_status(current_date)
        if display_status == "Pending":
            if self.__start_date > current_date:
                return f"Starts in {(self.__start_date - current_date).days} days"
            return "Payment pending"
        if display_status == "Active":
            days_remaining = (self.__end_date - current_date).days
            return (
                f"{days_remaining} days left"
                if days_remaining != 1
                else "1 day left"
            )
        return f"Expired {self.__end_date.isoformat()}"
