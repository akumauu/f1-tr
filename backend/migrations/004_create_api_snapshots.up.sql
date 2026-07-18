-- ============================================================
-- API 原始响应归档
-- ============================================================
CREATE TABLE IF NOT EXISTS f1_api_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    provider      TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    year          SMALLINT,
    meeting_key   INTEGER,
    session_key   INTEGER,
    query         JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload       JSONB NOT NULL,
    record_count  INTEGER NOT NULL DEFAULT 0,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_f1_api_snapshots_lookup
ON f1_api_snapshots (provider, endpoint, session_key, fetched_at DESC);

CREATE INDEX IF NOT EXISTS idx_f1_api_snapshots_payload_gin
ON f1_api_snapshots USING GIN (payload);
