#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ingest_f1_to_postgres import load_database_url, number_or_none, to_int


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract OpenF1 telemetry snapshots into normalized tables.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument(
        "--materialize-postgres-cache",
        action="store_true",
        help="显式从 f1_api_snapshots 重建可丢弃的 PG 逐点缓存。",
    )
    args = parser.parse_args()
    if not args.materialize_postgres_cache:
        parser.error(
            "PG 逐点遥测仅是可重建缓存，默认禁止物化；"
            "确需临时 SQL 分析时传入 --materialize-postgres-cache。"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    with psycopg.connect(args.database_url) as conn:
        car_count = extract_endpoint(conn, args.year, "car_data", args.batch_size)
        location_count = extract_endpoint(conn, args.year, "location", args.batch_size)
        conn.commit()

    print("Extracted telemetry rows:")
    print(f"  car_data: {car_count}")
    print(f"  location: {location_count}")
    return 0


def extract_endpoint(conn: Any, year: int, endpoint: str, batch_size: int) -> int:
    snapshots = conn.execute(
        """
        WITH aggregate_snapshots AS (
            SELECT DISTINCT ON (session_key)
                session_key, 'ALL'::text AS requested_driver_number, payload, fetched_at
            FROM f1_api_snapshots
            WHERE provider = 'openf1' AND endpoint = %s AND status = 'ok' AND year = %s
              AND session_key IS NOT NULL AND NOT query ? 'driver_number'
            ORDER BY session_key, fetched_at DESC, id DESC
        ), driver_snapshots AS (
            SELECT DISTINCT ON (session_key, query->>'driver_number')
                session_key, query->>'driver_number' AS requested_driver_number, payload, fetched_at
            FROM f1_api_snapshots s
            WHERE provider = 'openf1' AND endpoint = %s AND status = 'ok' AND year = %s
              AND session_key IS NOT NULL AND query ? 'driver_number'
              AND NOT EXISTS (SELECT 1 FROM aggregate_snapshots a WHERE a.session_key = s.session_key)
            ORDER BY session_key, query->>'driver_number', fetched_at DESC, id DESC
        )
        SELECT * FROM aggregate_snapshots
        UNION ALL
        SELECT * FROM driver_snapshots
        ORDER BY session_key, requested_driver_number
        """,
        (endpoint, year, endpoint, year),
    ).fetchall()

    count = 0
    for session_key, requested_driver_number, payload, fetched_at in snapshots:
        rows = payload if isinstance(payload, list) else json.loads(payload)
        batch: list[tuple[Any, ...]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            driver_number = to_int(row.get("driver_number"))
            date = row.get("date")
            if driver_number is None or not date:
                continue
            if endpoint == "car_data":
                batch.append(car_data_params(int(session_key), driver_number, date, row, fetched_at))
            elif endpoint == "location":
                batch.append(location_params(int(session_key), driver_number, date, row, fetched_at))
            if len(batch) >= batch_size:
                write_batch(conn, endpoint, batch)
                count += len(batch)
                batch.clear()
        if batch:
            write_batch(conn, endpoint, batch)
            count += len(batch)
        conn.commit()
        print(
            f"  {endpoint} session={session_key} driver={requested_driver_number}: "
            f"{len(rows)} source rows processed",
            flush=True,
        )
    return count


def write_batch(conn: Any, endpoint: str, batch: list[tuple[Any, ...]]) -> None:
    sql = CAR_DATA_SQL if endpoint == "car_data" else LOCATION_SQL
    with conn.cursor() as cur:
        cur.executemany(sql, batch)


CAR_DATA_SQL = """
INSERT INTO telemetry_car_data
    (session_key, driver_number, date, source_provider,
     speed, throttle, brake, n_gear, rpm, drs,
     raw_payload, fetched_at)
VALUES
    (%s, %s, %s, 'openf1',
     %s, %s, %s, %s, %s, %s,
     %s::jsonb, %s)
ON CONFLICT (session_key, driver_number, date, source_provider) DO UPDATE SET
    speed = EXCLUDED.speed,
    throttle = EXCLUDED.throttle,
    brake = EXCLUDED.brake,
    n_gear = EXCLUDED.n_gear,
    rpm = EXCLUDED.rpm,
    drs = EXCLUDED.drs,
    raw_payload = EXCLUDED.raw_payload,
    fetched_at = EXCLUDED.fetched_at,
    updated_at = NOW()
"""


LOCATION_SQL = """
INSERT INTO telemetry_location
    (session_key, driver_number, date, source_provider,
     x, y, z, raw_payload, fetched_at)
VALUES
    (%s, %s, %s, 'openf1',
     %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (session_key, driver_number, date, source_provider) DO UPDATE SET
    x = EXCLUDED.x,
    y = EXCLUDED.y,
    z = EXCLUDED.z,
    raw_payload = EXCLUDED.raw_payload,
    fetched_at = EXCLUDED.fetched_at,
    updated_at = NOW()
"""


def car_data_params(
    session_key: int,
    driver_number: int,
    date: str,
    row: dict[str, Any],
    fetched_at: Any,
) -> tuple[Any, ...]:
    return (
        session_key,
        driver_number,
        date,
        number_or_none(row.get("speed")),
        number_or_none(row.get("throttle")),
        number_or_none(row.get("brake")),
        to_int(row.get("n_gear")),
        to_int(row.get("rpm")),
        to_int(row.get("drs")),
        json.dumps(row, ensure_ascii=False),
        fetched_at,
    )


def location_params(
    session_key: int,
    driver_number: int,
    date: str,
    row: dict[str, Any],
    fetched_at: Any,
) -> tuple[Any, ...]:
    return (
        session_key,
        driver_number,
        date,
        number_or_none(row.get("x")),
        number_or_none(row.get("y")),
        number_or_none(row.get("z")),
        json.dumps(row, ensure_ascii=False),
        fetched_at,
    )


if __name__ == "__main__":
    raise SystemExit(main())
