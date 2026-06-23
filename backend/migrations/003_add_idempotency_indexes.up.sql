-- ============================================================
-- 幂等抓取索引
-- ============================================================
WITH ranked_positions AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY session_key, driver_number, date
            ORDER BY id
        ) AS rn
    FROM positions
)
DELETE FROM positions p
USING ranked_positions r
WHERE p.id = r.id
  AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_unique_sample
ON positions (session_key, driver_number, date);

WITH ranked_race_control AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY
                session_key,
                date,
                COALESCE(category, ''),
                COALESCE(flag, ''),
                COALESCE(message, ''),
                COALESCE(driver_number, -1),
                COALESCE(lap_number, -1)
            ORDER BY id
        ) AS rn
    FROM race_control
)
DELETE FROM race_control rc
USING ranked_race_control r
WHERE rc.id = r.id
  AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_race_control_unique_message
ON race_control (
    session_key,
    date,
    COALESCE(category, ''),
    COALESCE(flag, ''),
    COALESCE(message, ''),
    COALESCE(driver_number, -1),
    COALESCE(lap_number, -1)
);
