-- ============================================================
-- API 快照分类，便于按分析阶段调取原始数据
-- ============================================================
ALTER TABLE f1_api_snapshots
ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'uncategorized';

CREATE INDEX IF NOT EXISTS idx_f1_api_snapshots_category
ON f1_api_snapshots (category, provider, endpoint, session_key, fetched_at DESC);
