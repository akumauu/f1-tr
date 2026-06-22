-- ============================================================
-- 视图 1: 圈速 Delta（与前一圈的差值）
-- ============================================================
CREATE OR REPLACE VIEW v_lap_deltas AS
SELECT
    l.session_key,
    l.driver_number,
    d.name_acronym,
    d.team_name,
    l.lap_number,
    l.lap_duration,
    s.compound,
    s.stint_number,
    (l.lap_duration - LAG(l.lap_duration)
        OVER (PARTITION BY l.session_key, l.driver_number
              ORDER BY l.lap_number)
    ) AS delta_to_prev,
    l.lap_duration - FIRST_VALUE(l.lap_duration)
        OVER (PARTITION BY l.session_key, l.driver_number, s.stint_number
              ORDER BY l.lap_number) AS delta_to_stint_start
FROM laps l
JOIN drivers d ON d.session_key = l.session_key AND d.driver_number = l.driver_number
LEFT JOIN stints s ON s.session_key = l.session_key
    AND s.driver_number = l.driver_number
    AND l.lap_number BETWEEN s.lap_start AND s.lap_end
WHERE l.lap_duration IS NOT NULL
  AND l.is_pit_out_lap = FALSE;

-- ============================================================
-- 视图 2: 轮胎衰退率（线性回归斜率）
-- ============================================================
CREATE OR REPLACE VIEW v_tire_degradation AS
SELECT
    l.session_key,
    l.driver_number,
    d.name_acronym,
    d.team_name,
    s.stint_number,
    s.compound,
    s.lap_start,
    s.lap_end,
    (s.lap_end - s.lap_start + 1) AS stint_length,
    REGR_SLOPE(l.lap_duration, l.lap_number)     AS deg_rate_sec_per_lap,
    REGR_INTERCEPT(l.lap_duration, l.lap_number) AS base_pace,
    REGR_R2(l.lap_duration, l.lap_number)        AS r_squared,
    AVG(l.lap_duration)                           AS avg_lap_time,
    MIN(l.lap_duration)                           AS best_lap_time
FROM laps l
JOIN stints s ON s.session_key = l.session_key
    AND s.driver_number = l.driver_number
    AND l.lap_number BETWEEN s.lap_start AND s.lap_end
JOIN drivers d ON d.session_key = l.session_key AND d.driver_number = l.driver_number
WHERE l.lap_duration IS NOT NULL
  AND l.is_pit_out_lap = FALSE
  AND (s.lap_end - s.lap_start) >= 3
GROUP BY l.session_key, l.driver_number, d.name_acronym, d.team_name,
         s.stint_number, s.compound, s.lap_start, s.lap_end;

-- ============================================================
-- 视图 3: TR 与圈数关联
-- ============================================================
CREATE OR REPLACE VIEW v_radio_with_context AS
SELECT
    tr.id,
    tr.session_key,
    tr.driver_number,
    d.name_acronym,
    d.team_name,
    tr.date AS radio_time,
    tr.recording_url,
    tr.transcript_en,
    tr.transcript_zh,
    tr.intent,
    tr.sentiment,
    tr.key_entities,
    tr.translation_status,
    (SELECT l.lap_number FROM laps l
     WHERE l.session_key = tr.session_key
       AND l.driver_number = tr.driver_number
       AND l.date_start <= tr.date
     ORDER BY l.date_start DESC LIMIT 1
    ) AS approx_lap_number
FROM team_radio tr
JOIN drivers d ON d.session_key = tr.session_key AND d.driver_number = tr.driver_number;
