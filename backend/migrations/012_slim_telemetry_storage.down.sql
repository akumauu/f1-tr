CREATE INDEX IF NOT EXISTS idx_f1_api_snapshots_payload_gin
ON f1_api_snapshots USING GIN (payload);

COMMENT ON TABLE telemetry_car_data IS NULL;
COMMENT ON TABLE telemetry_location IS NULL;
