-- ============================================================
-- TracingInsights 完整数据包目录
-- 原始文件保留在本地数据湖；本表登记来源提交、分类、校验和与解析状态。
-- ============================================================
CREATE TABLE IF NOT EXISTS tracing_dataset_files (
    year                SMALLINT NOT NULL,
    repository_url      TEXT NOT NULL,
    repository_commit   TEXT NOT NULL,
    relative_path       TEXT NOT NULL,
    category            TEXT NOT NULL,
    meeting_name        TEXT,
    session_name        TEXT,
    driver_acronym      TEXT,
    lap_number          INTEGER,
    file_size_bytes     BIGINT NOT NULL,
    sha256              TEXT NOT NULL,
    json_valid          BOOLEAN,
    sample_count        INTEGER,
    telemetry_fields    JSONB,
    telemetry_duration  DOUBLE PRECISION,
    validation_status   TEXT NOT NULL,
    validation_error    TEXT,
    indexed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (year, relative_path),
    CHECK (validation_status IN ('valid', 'invalid', 'not_applicable'))
);

CREATE INDEX IF NOT EXISTS idx_tracing_dataset_files_classification
ON tracing_dataset_files (year, meeting_name, session_name, driver_acronym, lap_number);

CREATE INDEX IF NOT EXISTS idx_tracing_dataset_files_status
ON tracing_dataset_files (year, category, validation_status);

CREATE TABLE IF NOT EXISTS tracing_dataset_imports (
    year                  SMALLINT NOT NULL,
    repository_url        TEXT NOT NULL,
    repository_commit     TEXT NOT NULL,
    root_path              TEXT NOT NULL,
    started_at             TIMESTAMPTZ NOT NULL,
    completed_at           TIMESTAMPTZ,
    status                 TEXT NOT NULL,
    total_files            INTEGER NOT NULL DEFAULT 0,
    telemetry_files        INTEGER NOT NULL DEFAULT 0,
    valid_telemetry_files  INTEGER NOT NULL DEFAULT 0,
    invalid_files          INTEGER NOT NULL DEFAULT 0,
    total_bytes            BIGINT NOT NULL DEFAULT 0,
    notes                  TEXT,
    PRIMARY KEY (year, repository_commit),
    CHECK (status IN ('running', 'completed', 'failed'))
);
