DROP INDEX IF EXISTS idx_f1_api_snapshots_status;

ALTER TABLE f1_api_snapshots
DROP COLUMN IF EXISTS error_message,
DROP COLUMN IF EXISTS status;
