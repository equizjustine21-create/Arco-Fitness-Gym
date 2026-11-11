-- Back up gymarco_db before running this one-time migration in phpMyAdmin.
-- Select gymarco_db in phpMyAdmin before executing these statements.
-- paid_at is stored as a DATETIME in Asia/Manila local time.

-- A visit is not income until staff confirms that its fee was collected.
-- Existing visits intentionally remain Pending; verify them before marking paid.
ALTER TABLE walkin_visits
    ADD COLUMN fee_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
    ADD COLUMN fee_paid_at DATETIME NULL,
    ADD COLUMN fee_paid_by INT UNSIGNED NULL,
    ADD KEY idx_walkin_visits_date_fee_status (visit_date, fee_status),
    ADD CONSTRAINT fk_walkin_visits_fee_paid_by
        FOREIGN KEY (fee_paid_by) REFERENCES users (id);

ALTER TABLE payments
    ADD COLUMN paid_at DATETIME NULL AFTER payment_date,
    ADD COLUMN receipt_no VARCHAR(32) NULL AFTER reference_number,
    ADD COLUMN voided_by INT UNSIGNED NULL AFTER receipt_no,
    ADD COLUMN voided_at DATETIME NULL AFTER voided_by,
    ADD COLUMN void_reason VARCHAR(500) NULL AFTER voided_at;

-- Normalize known legacy values without converting pending/rejected rows to income.
UPDATE payments
SET payment_status = 'Confirmed'
WHERE payment_status IS NULL OR TRIM(payment_status) = ''
   OR LOWER(TRIM(payment_status)) = 'paid';

UPDATE payments
SET payment_status = CASE LOWER(TRIM(payment_status))
    WHEN 'confirmed' THEN 'Confirmed'
    WHEN 'pending' THEN 'Pending'
    WHEN 'rejected' THEN 'Rejected'
    WHEN 'voided' THEN 'Voided'
    ELSE payment_status
END;

-- Existing app records use ISO dates; preserve their Manila calendar day.
UPDATE payments
SET paid_at = STR_TO_DATE(LEFT(payment_date, 10), '%Y-%m-%d')
WHERE paid_at IS NULL
  AND payment_date REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}';

-- Stop and manually correct any rows returned here before continuing.
SELECT id, payment_date
FROM payments
WHERE paid_at IS NULL;

-- This append-only table guarantees receipt sequence numbers are never reused.
CREATE TABLE payment_receipt_sequences (
    sequence_no BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    payment_id INT UNSIGNED NOT NULL,
    PRIMARY KEY (sequence_no),
    UNIQUE KEY uq_payment_receipt_payment (payment_id),
    CONSTRAINT fk_payment_receipt_sequence_payment
        FOREIGN KEY (payment_id) REFERENCES payments (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Assign existing receipts in payment date order, with id as a stable tie-breaker.
INSERT IGNORE INTO payment_receipt_sequences (payment_id)
SELECT id
FROM payments
ORDER BY paid_at ASC, id ASC;

UPDATE payments p
JOIN payment_receipt_sequences s ON s.payment_id = p.id
SET p.receipt_no = CONCAT('R-', LPAD(s.sequence_no, 4, '0'))
WHERE p.receipt_no IS NULL OR p.receipt_no = '';

ALTER TABLE payments
    MODIFY COLUMN paid_at DATETIME NOT NULL,
    MODIFY COLUMN receipt_no VARCHAR(32) NOT NULL,
    ADD UNIQUE KEY uq_payments_receipt_no (receipt_no),
    ADD KEY idx_payments_paid_status_method (paid_at, payment_status, method),
    ADD CONSTRAINT fk_payments_voided_by
        FOREIGN KEY (voided_by) REFERENCES users (id);
