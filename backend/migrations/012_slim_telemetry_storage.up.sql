-- 逐点遥测不再常驻 PostgreSQL：TracingInsights 以 v4 Parquet 为准，
-- OpenF1 可从 f1_api_snapshots 显式重建。payload 没有 JSON 包含查询消费者。
DROP INDEX IF EXISTS idx_f1_api_snapshots_payload_gin;

COMMENT ON TABLE telemetry_car_data IS
'可丢弃的 OpenF1 逐点重建缓存；默认应为空，不是产品或研究真相源。';

COMMENT ON TABLE telemetry_location IS
'可丢弃的 OpenF1 逐点重建缓存；默认应为空，不是产品或研究真相源。';
