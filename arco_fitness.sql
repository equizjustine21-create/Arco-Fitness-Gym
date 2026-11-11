CREATE DATABASE IF NOT EXISTS gymarco_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE gymarco_db;

CREATE TABLE IF NOT EXISTS users (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    username VARCHAR(191) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    email VARCHAR(254) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_users_username (username),
    UNIQUE KEY idx_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS members (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(254) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    membership_type VARCHAR(100) NOT NULL DEFAULT '',
    status VARCHAR(50) NOT NULL DEFAULT 'Active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id INT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_members_email (email),
    UNIQUE KEY idx_members_user_id (user_id),
    CONSTRAINT fk_members_user
        FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS password_resets (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    member_id INT UNSIGNED NOT NULL,
    token_hash CHAR(64) NOT NULL,
    expires_at DATETIME NOT NULL,
    used TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_password_resets_token_hash (token_hash),
    KEY idx_password_resets_member_created (member_id, created_at),
    CONSTRAINT fk_password_resets_member
        FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS email_otps (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    email VARCHAR(254) NOT NULL,
    purpose VARCHAR(20) NOT NULL,
    code_hash CHAR(64) NOT NULL,
    expires_at DATETIME NOT NULL,
    attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
    verified_at DATETIME NULL,
    used TINYINT(1) NOT NULL DEFAULT 0,
    member_id INT UNSIGNED NULL,
    username VARCHAR(191) NULL,
    password_hash VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_email_otps_email_purpose_created (email, purpose, created_at),
    KEY idx_email_otps_expires_at (expires_at),
    CONSTRAINT fk_email_otps_member
        FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    bucket_key CHAR(64) NOT NULL,
    window_started_at DATETIME NOT NULL,
    attempt_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    blocked_until DATETIME NULL,
    PRIMARY KEY (bucket_key),
    KEY idx_auth_rate_limits_window_started_at (window_started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS email_log (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    to_email VARCHAR(254) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    status ENUM('sent', 'failed') NOT NULL,
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_email_log_created_at (created_at),
    KEY idx_email_log_recipient_subject_date (to_email, subject, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_notification_reads (
    admin_user_id INT UNSIGNED NOT NULL,
    notification_key VARCHAR(64) NOT NULL,
    read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (admin_user_id, notification_key),
    CONSTRAINT fk_notification_reads_admin
        FOREIGN KEY (admin_user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS member_notification_reads (
    member_user_id INT UNSIGNED NOT NULL,
    notification_key VARCHAR(64) NOT NULL,
    read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (member_user_id, notification_key),
    CONSTRAINT fk_member_notification_reads_user
        FOREIGN KEY (member_user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS attendance (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    member_id INT UNSIGNED NOT NULL,
    checkin_date VARCHAR(10) NOT NULL,
    checkin_time VARCHAR(20) NOT NULL,
    checkin_at VARCHAR(40) NULL,
    checkout_at VARCHAR(40) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_attendance_member_date (member_id, checkin_date),
    KEY idx_attendance_checkin_date (checkin_date),
    CONSTRAINT fk_attendance_member
        FOREIGN KEY (member_id) REFERENCES members (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS walkin_visits (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    full_name VARCHAR(255) NOT NULL,
    visit_date VARCHAR(10) NOT NULL,
    checkin_time VARCHAR(20) NOT NULL,
    checkout_time VARCHAR(20) NULL,
    checkin_at VARCHAR(40) NULL,
    checkout_at VARCHAR(40) NULL,
    fee DECIMAL(10, 2) NOT NULL DEFAULT 50.00,
    fee_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
    fee_paid_at DATETIME NULL,
    fee_paid_by INT UNSIGNED NULL,
    PRIMARY KEY (id),
    KEY idx_walkin_visits_date (visit_date),
    KEY idx_walkin_visits_date_fee_status (visit_date, fee_status),
    CONSTRAINT fk_walkin_visits_fee_paid_by
        FOREIGN KEY (fee_paid_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS membership_plans (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(191) NOT NULL,
    duration_days INT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    active TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    UNIQUE KEY idx_membership_plans_name (name),
    CONSTRAINT chk_membership_plans_duration CHECK (duration_days > 0),
    CONSTRAINT chk_membership_plans_price CHECK (price >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS member_memberships (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    member_id INT UNSIGNED NOT NULL,
    plan_id INT UNSIGNED NOT NULL,
    start_date VARCHAR(10) NOT NULL,
    end_date VARCHAR(10) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'Active',
    PRIMARY KEY (id),
    KEY idx_member_memberships_member_dates (member_id, start_date, end_date),
    KEY idx_member_memberships_status_dates (status, end_date),
    CONSTRAINT fk_member_memberships_member
        FOREIGN KEY (member_id) REFERENCES members (id),
    CONSTRAINT fk_member_memberships_plan
        FOREIGN KEY (plan_id) REFERENCES membership_plans (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payments (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    member_id INT UNSIGNED NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    payment_date VARCHAR(40) NOT NULL,
    paid_at DATETIME NULL,
    method VARCHAR(100) NOT NULL,
    note VARCHAR(1000) NOT NULL DEFAULT '',
    payment_status VARCHAR(20) NOT NULL DEFAULT 'Confirmed',
    reference_number VARCHAR(100) NULL,
    checkout_token VARCHAR(64) NULL,
    receipt_no VARCHAR(32) NULL,
    voided_by INT UNSIGNED NULL,
    voided_at DATETIME NULL,
    void_reason VARCHAR(500) NULL,
    membership_id INT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY idx_payments_checkout_token (checkout_token),
    UNIQUE KEY uq_payments_receipt_no (receipt_no),
    KEY idx_payments_member_date (member_id, payment_date),
    KEY idx_payments_status_date (payment_status, payment_date),
    KEY idx_payments_paid_status_method (paid_at, payment_status, method),
    CONSTRAINT chk_payments_amount CHECK (amount >= 0),
    CONSTRAINT fk_payments_member
        FOREIGN KEY (member_id) REFERENCES members (id),
    CONSTRAINT fk_payments_membership
        FOREIGN KEY (membership_id) REFERENCES member_memberships (id),
    CONSTRAINT fk_payments_voided_by
        FOREIGN KEY (voided_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payment_receipt_sequences (
    sequence_no BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    payment_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (sequence_no),
    UNIQUE KEY uq_payment_receipt_payment (payment_id),
    CONSTRAINT fk_payment_receipt_sequence_payment
        FOREIGN KEY (payment_id) REFERENCES payments (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_settings (
    `key` VARCHAR(191) NOT NULL,
    `value` TEXT NOT NULL,
    PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO app_settings (`key`, `value`) VALUES
    ('gym_name', 'ARCO FITNESS'),
    ('contact_number', ''),
    ('gym_email', ''),
    ('gym_address', ''),
    ('opening_time', '06:00'),
    ('closing_time', '22:00'),
    ('operating_days', 'Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday'),
    ('walk_in_fee', '50'),
    ('monthly_fee', '750'),
    ('time_format', '12'),
    ('timezone', 'Asia/Manila'),
    ('desktop_sidebar', 'expanded'),
    ('mobile_sidebar', 'closed'),
    ('membership_reminders', '1'),
    ('checkout_warnings', '1'),
    ('last_backup', ''),
    ('gcash_qr_filename', '');

INSERT IGNORE INTO membership_plans (name, duration_days, price, description)
VALUES ('Monthly Membership', 30, 750.00, '30-day gym membership');
