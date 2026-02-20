-- migrations/003_add_has_expiry_label_to_skus.sql
-- Description: Add has_expiry_label flag to skus table
-- Version: 3
-- Date: 2026-02-20

BEGIN TRANSACTION;

-- Add has_expiry_label column (0 = auto shelf-life tracking, 1 = manual label scan at receiving)
ALTER TABLE skus ADD COLUMN has_expiry_label INTEGER NOT NULL DEFAULT 0 CHECK(has_expiry_label IN (0, 1));

-- Update schema version
INSERT INTO schema_version (version, description, checksum)
VALUES (
    3,
    'Add has_expiry_label to skus for expiry tracking mode',
    'sha256:003_add_has_expiry_label_to_skus'
);

COMMIT;
