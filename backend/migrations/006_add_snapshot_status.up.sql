-- ============================================================
-- API 快照状态：区分成功、有意空数据和请求失败。
-- ============================================================
ALTER TABLE f1_api_snapshots
ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'ok',
ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_f1_api_snapshots_status
ON f1_api_snapshots (provider, status, endpoint, session_key, fetched_at DESC);

UPDATE f1_api_snapshots
SET status = 'legacy_zero',
    error_message = COALESCE(error_message, '历史零记录快照，需重新验证是正常空数据还是请求失败')
WHERE record_count = 0
  AND status = 'ok';
