-- ============================================================
-- 赛事会议
-- ============================================================
CREATE TABLE IF NOT EXISTS meetings (
    meeting_key     INTEGER PRIMARY KEY,
    meeting_name    TEXT NOT NULL,
    location        TEXT,
    country_name    TEXT,
    circuit_name    TEXT,
    year            SMALLINT NOT NULL,
    date_start      TIMESTAMPTZ
);

-- ============================================================
-- 赛段（FP1/FP2/FP3/Qualifying/Sprint/Race）
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_key     INTEGER PRIMARY KEY,
    meeting_key     INTEGER NOT NULL REFERENCES meetings(meeting_key),
    session_name    TEXT NOT NULL,
    session_type    TEXT,
    date_start      TIMESTAMPTZ,
    date_end        TIMESTAMPTZ
);

-- ============================================================
-- 车手
-- ============================================================
CREATE TABLE IF NOT EXISTS drivers (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number   SMALLINT NOT NULL,
    full_name       TEXT,
    name_acronym    CHAR(3),
    team_name       TEXT,
    team_colour     CHAR(6),
    headshot_url    TEXT,
    country_code    CHAR(3),
    UNIQUE (session_key, driver_number)
);

-- ============================================================
-- 圈速数据（核心分析表）
-- ============================================================
CREATE TABLE IF NOT EXISTS laps (
    id                  SERIAL PRIMARY KEY,
    session_key         INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number       SMALLINT NOT NULL,
    lap_number          SMALLINT NOT NULL,
    lap_duration        REAL,
    duration_sector_1   REAL,
    duration_sector_2   REAL,
    duration_sector_3   REAL,
    is_pit_out_lap      BOOLEAN DEFAULT FALSE,
    date_start          TIMESTAMPTZ,
    UNIQUE (session_key, driver_number, lap_number)
);

-- ============================================================
-- 轮胎 Stint 数据
-- ============================================================
CREATE TABLE IF NOT EXISTS stints (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number   SMALLINT NOT NULL,
    stint_number    SMALLINT NOT NULL,
    compound        TEXT,
    tyre_age_at_start SMALLINT,
    lap_start       SMALLINT,
    lap_end         SMALLINT,
    UNIQUE (session_key, driver_number, stint_number)
);

-- ============================================================
-- Team Radio（核心 TR 表）
-- ============================================================
CREATE TABLE IF NOT EXISTS team_radio (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    meeting_key     INTEGER,
    driver_number   SMALLINT NOT NULL,
    date            TIMESTAMPTZ NOT NULL,
    recording_url   TEXT NOT NULL,
    transcript_en   TEXT,
    transcript_zh   TEXT,
    intent          TEXT,
    sentiment       TEXT,
    key_entities    JSONB,
    translation_status TEXT DEFAULT 'pending',
    UNIQUE (session_key, driver_number, date)
);

-- ============================================================
-- 进站数据
-- ============================================================
CREATE TABLE IF NOT EXISTS pit_stops (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number   SMALLINT NOT NULL,
    lap_number      SMALLINT NOT NULL,
    pit_duration    REAL,
    date            TIMESTAMPTZ,
    UNIQUE (session_key, driver_number, lap_number)
);

-- ============================================================
-- 位置变化
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number   SMALLINT NOT NULL,
    position        SMALLINT NOT NULL,
    date            TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_session_driver ON positions(session_key, driver_number);

-- ============================================================
-- 赛事控制消息
-- ============================================================
CREATE TABLE IF NOT EXISTS race_control (
    id              SERIAL PRIMARY KEY,
    session_key     INTEGER NOT NULL REFERENCES sessions(session_key),
    date            TIMESTAMPTZ NOT NULL,
    category        TEXT,
    flag            TEXT,
    message         TEXT,
    driver_number   SMALLINT,
    lap_number      SMALLINT
);
