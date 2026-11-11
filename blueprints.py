from flask import Blueprint


auth_bp = Blueprint("auth", __name__)
portal_bp = Blueprint("portal", __name__)
dashboard_bp = Blueprint("dashboard", __name__)
settings_bp = Blueprint("settings", __name__)
members_bp = Blueprint("members", __name__)
attendance_bp = Blueprint("attendance", __name__)
walkin_bp = Blueprint("walkin", __name__)
memberships_bp = Blueprint("memberships", __name__)
payments_bp = Blueprint("payments", __name__)
reports_bp = Blueprint("reports", __name__)
