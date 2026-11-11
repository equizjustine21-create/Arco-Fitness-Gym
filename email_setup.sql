USE gymarco_db;

SELECT LOWER(email) AS normalized_email, COUNT(*) AS duplicate_count
FROM members
GROUP BY LOWER(email)
HAVING COUNT(*) > 1;

ALTER TABLE members
    ADD UNIQUE KEY idx_members_email (email);

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
