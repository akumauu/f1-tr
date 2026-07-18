-- ============================================================
-- OpenF1 endpoint 适用性函数
-- 避免对已知不适用的 session/endpoint 反复请求并制造错误快照。
-- ============================================================
CREATE OR REPLACE FUNCTION is_endpoint_applicable(session_name TEXT, endpoint TEXT)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN endpoint = 'intervals' THEN session_name IN ('Race', 'Sprint')
        WHEN endpoint = 'starting_grid' THEN session_name IN ('Qualifying', 'Sprint Qualifying')
        ELSE TRUE
    END
$$;
