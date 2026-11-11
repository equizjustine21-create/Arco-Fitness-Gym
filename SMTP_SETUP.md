# Email setup

The app sends welcome, confirmed-payment, password-reset, and test emails through
Gmail SMTP in a background thread. See [EMAIL_SETUP.md](EMAIL_SETUP.md) for the
SQL setup, Gmail App Password steps, Windows PowerShell environment variables,
test checklist, and troubleshooting.

The app uses Python's built-in `smtplib`; it does not require Flask-Mail.
