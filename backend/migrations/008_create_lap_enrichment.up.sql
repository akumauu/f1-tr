-- ============================================================
-- 圈表增强字段：OpenF1 speed trap + mini-sector segments。
-- 原始 JSON 仍保留在 f1_api_snapshots，这里只做可查询规范化副本。
-- ============================================================
CREATE TABLE IF NOT EXISTS lap_enrichment (
    session_key        INTEGER NOT NULL,
    driver_number      SMALLINT NOT NULL,
    lap_number         SMALLINT NOT NULL,
    source_provider    TEXT NOT NULL,
    source_endpoint    TEXT NOT NULL,
    i1_speed           REAL,
    i2_speed           REAL,
    st_speed           REAL,
    segments_sector_1  JSONB NOT NULL DEFAULT '[]'::jsonb,
    segments_sector_2  JSONB NOT NULL DEFAULT '[]'::jsonb,
    segments_sector_3  JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_payload         JSONB NOT NULL,
    fetched_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_key, driver_number, lap_number, source_provider)
);

CREATE INDEX IF NOT EXISTS idx_lap_enrichment_session_lap
ON lap_enrichment (session_key, lap_number, driver_number);

CREATE INDEX IF NOT EXISTS idx_lap_enrichment_segments_gin
ON lap_enrichment USING GIN (segments_sector_1, segments_sector_2, segments_sector_3);
