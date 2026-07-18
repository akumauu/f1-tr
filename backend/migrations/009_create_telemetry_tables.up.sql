-- ============================================================
-- OpenF1 / FastF1 遥测规范化表
-- ============================================================
CREATE TABLE IF NOT EXISTS telemetry_car_data (
    session_key      INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number    SMALLINT NOT NULL,
    date             TIMESTAMPTZ NOT NULL,
    source_provider  TEXT NOT NULL,
    speed            REAL,
    throttle         REAL,
    brake            REAL,
    n_gear           SMALLINT,
    rpm              INTEGER,
    drs              SMALLINT,
    raw_payload      JSONB NOT NULL,
    fetched_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_key, driver_number, date, source_provider)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_car_session_driver_date
ON telemetry_car_data (session_key, driver_number, date);

CREATE TABLE IF NOT EXISTS telemetry_location (
    session_key      INTEGER NOT NULL REFERENCES sessions(session_key),
    driver_number    SMALLINT NOT NULL,
    date             TIMESTAMPTZ NOT NULL,
    source_provider  TEXT NOT NULL,
    x                REAL,
    y                REAL,
    z                REAL,
    raw_payload      JSONB NOT NULL,
    fetched_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_key, driver_number, date, source_provider)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_location_session_driver_date
ON telemetry_location (session_key, driver_number, date);
