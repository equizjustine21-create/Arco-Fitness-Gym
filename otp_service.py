"""OTP helpers for member registration and password recovery."""

import hashlib
import html
import hmac
import secrets


OTP_LIFETIME_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def generate_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_digest(secret_key, email, purpose, code):
    message = f"{email.strip().lower()}\0{purpose}\0{code}".encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_otp(
    database,
    secret_key,
    email,
    purpose,
    member_id=None,
    username=None,
    password_hash=None,
):
    code = generate_otp()
    code_hash = otp_digest(secret_key, email, purpose, code)
    database.execute("START TRANSACTION")
    database.execute(
        """UPDATE email_otps SET used = 1,
                  username = NULL, password_hash = NULL,
                  member_id = IF(purpose = 'registration', NULL, member_id)
           WHERE email = ? AND purpose = ? AND used = 0""",
        (email, purpose),
    )
    database.execute(
        """DELETE FROM email_otps
           WHERE created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)"""
    )
    result = database.execute(
        """INSERT INTO email_otps
           (email, purpose, code_hash, expires_at, attempts, verified_at, used,
            member_id, username, password_hash, created_at)
           VALUES (?, ?, ?, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 10 MINUTE),
                   0, NULL, 0, ?, ?, ?, UTC_TIMESTAMP())""",
        (email, purpose, code_hash, member_id, username, password_hash),
    )
    database.commit()
    return result.lastrowid, code


def verify_otp(secret_key, email, purpose, code, code_hash):
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    expected_hash = otp_digest(secret_key, email, purpose, code)
    return hmac.compare_digest(expected_hash, code_hash)


def send_otp_email(email, purpose, code):
    import app as gym_app

    action = "finish creating your account" if purpose == "registration" else (
        "reset your password"
    )
    return gym_app.send_email(
        email,
        "Your Arco Fitness Gym verification code",
        (
            f"Your code to {action} is {code}. "
            "It expires in 10 minutes. If you did not request it, ignore this email."
        ),
        (
            f"<p>Your code to {html.escape(action)} is:</p>"
            f"<p style=\"font-size:24px;font-weight:bold;letter-spacing:4px\">"
            f"{html.escape(code)}</p>"
            "<p>This code expires in 10 minutes. If you did not request it, "
            "ignore this email.</p>"
        ),
    )
