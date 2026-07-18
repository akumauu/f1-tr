-- ============================================================
-- API 最新快照视图：每个 provider/endpoint/session 只保留最新一次抓取结果。
-- ============================================================
CREATE OR REPLACE VIEW v_latest_api_snapshots AS
SELECT
    id,
    provider,
    endpoint,
    category,
    year,
    meeting_key,
    session_key,
    query,
    payload,
    record_count,
    status,
    error_message,
    fetched_at
FROM (
    SELECT
        fs.*,
        ROW_NUMBER() OVER (
            PARTITION BY
                provider,
                endpoint,
                COALESCE(year, -1),
                COALESCE(meeting_key, -1),
                COALESCE(session_key, -1)
            ORDER BY fetched_at DESC, id DESC
        ) AS rn
    FROM f1_api_snapshots fs
) ranked
WHERE rn = 1;

-- ============================================================
-- OpenF1 端点审计视图：给后续完整性审计和备用源补数使用。
-- ============================================================
CREATE OR REPLACE VIEW v_openf1_endpoint_audit AS
SELECT
    s.session_key,
    s.meeting_key,
    m.meeting_name,
    m.country_name,
    s.session_name,
    s.session_type,
    s.date_start,
    snap.endpoint,
    snap.category,
    snap.status,
    snap.record_count,
    snap.error_message,
    snap.fetched_at,
    CASE
        WHEN snap.status IN ('ok', 'empty') THEN 'usable'
        WHEN snap.endpoint = 'laps' THEN 'needs_backup_source'
        WHEN snap.endpoint = 'pit' THEN 'needs_review'
        WHEN snap.status = 'error' THEN 'upstream_not_available'
        ELSE 'needs_review'
    END AS action_level
FROM v_latest_api_snapshots snap
JOIN sessions s ON s.session_key = snap.session_key
LEFT JOIN meetings m ON m.meeting_key = s.meeting_key
WHERE snap.provider = 'openf1';
