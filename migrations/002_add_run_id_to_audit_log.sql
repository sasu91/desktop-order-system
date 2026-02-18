-- migrations/002_add_run_id_to_audit_log.sql
-- Description: Add run_id for batch operation traceability (FASE 7 TASK 7.4)
-- Version: 2
-- Author: Tech Lead
-- Date: 2026-02-17

BEGIN TRANSACTION;

-- Add run_id column to audit_log for batch operation grouping
ALTER TABLE audit_log ADD COLUMN run_id TEXT DEFAULT NULL;

-- Create index on run_id for efficient batch query
CREATE INDEX IF NOT EXISTS idx_audit_log_run_id ON audit_log(run_id);

-- Create index on timestamp for chronological queries
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);

-- Update schema version
INSERT INTO schema_version (version, description, checksum)
VALUES (
    2,
    'Add run_id to audit_log for batch operation traceability',
    'sha256:002_add_run_id_to_audit_log'
);

COMMIT;
