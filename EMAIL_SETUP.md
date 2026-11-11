# Email and password-reset setup

The app uses Python's built-in `smtplib` with your provider's SMTP service. No extra mail
library is required. Registration and password recovery require a six-digit
email code. Use SMTP settings from an actual email provider.
[SMTP7 is a WordPress plugin](https://wordpress.org/plugins/wp-mail-smtp-mailer/);
it does not provide an SMTP server or account for this Flask app.

## 1. Database setup

For an existing database, the normal `python app.py` startup applies the
`email_otps` table in [arco_fitness.sql](arco_fitness.sql). If the app is
deployed without running `init_db`, run the `email_otps` table definition from
[email_setup.sql](email_setup.sql) against `gymarco_db`. The same setup script
also creates the legacy `password_resets` and `email_log` tables and adds the
unique index on `members.email`. Check for duplicate member email
addresses before running it:

```sql
SELECT LOWER(email) AS normalized_email, COUNT(*) AS duplicate_count
FROM members
GROUP BY LOWER(email)
HAVING COUNT(*) > 1;
```

Resolve any duplicates carefully before adding the unique index. Do not delete
or merge member data without checking related memberships and payments.

For a new database, `init_db` applies [arco_fitness.sql](arco_fitness.sql),
which includes the OTP and email log tables. Existing member accounts remain
usable; new member accounts are not created until their email code is verified.

## 2. Configure your email provider

Get the SMTP server, port, encryption mode, username, password, and allowed
sender address from your email provider. If you use Gmail, create an App
Password after enabling 2-Step Verification. Do not use your normal Gmail
password. If you use SMTP7 in WordPress, copy the actual mail provider settings
configured in that plugin; the plugin itself cannot send mail for Flask.

For Gmail, use `MAIL_SERVER=smtp.gmail.com`, `MAIL_PORT=587`, and
`MAIL_USE_SSL=false` (STARTTLS). Set `MAIL_USERNAME` to the Gmail account
address and `MAIL_PASSWORD` to that account's Google App Password.

In PowerShell, from the project folder, create `.env` if needed and edit it:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Replace the example host and fill in the values supplied by your provider:

```dotenv
MAIL_SERVER=smtp.your-provider.example
MAIL_PORT=587
MAIL_USE_SSL=false
MAIL_USERNAME=your-smtp-username
MAIL_PASSWORD=your-smtp-password
MAIL_FROM=your-verified-sender@example.com
APPLICATION_BASE_URL=http://127.0.0.1:5000
```

Port `587` normally uses STARTTLS, so set `MAIL_USE_SSL=false`. Port `465`
normally uses implicit SSL, so set `MAIL_USE_SSL=true`. Use the mode specified
by your provider. The `MAIL_FROM` address must be allowed by that provider.

Save `.env`, stop Flask with **Ctrl+C**, then restart it so the settings load:

```powershell
.\venv\Scripts\python.exe app.py
```

The project `.env` is excluded from Git. Do not paste it into chat or commit it.

## 3. Test registration and password recovery

1. Sign in as an admin and open **Settings → Email delivery**.
2. Use **Send test email to my profile email**. Wait a few seconds, refresh the
   page, and check the Email log for `Sent` or the SMTP error.
3. During registration, enter the email address on an active member record.
   The account is created only after the emailed six-digit code is verified.
4. Open **Member sign in → Forgot password?**, enter the email on a registered
   member account, verify the emailed six-digit code, and choose a new
   password. The message on screen does not reveal whether an email is
   registered.

Codes expire after 10 minutes and allow at most five guesses. New codes
invalidate older ones. Only an HMAC hash of each code is stored. Code sending
and verification are rate-limited by email and IP address.

## Troubleshooting

| Error | What to check |
| --- | --- |
| `535` or login rejected | Confirm `MAIL_USERNAME` is the Gmail account that created the App Password. Use the App Password, not your normal Gmail password. |
| Connection timed out | Check internet access and that outbound SMTP port 587 is allowed. |
| Email log says `failed` | Read the Details column in **Settings → Email delivery** for the reported SMTP error. |
| Email not in inbox | Check Spam/Junk and confirm the recipient address. |
| Password reset table error | Run [email_setup.sql](email_setup.sql) against `gymarco_db`. |
