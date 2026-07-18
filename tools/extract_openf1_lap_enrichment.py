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
    parser = argparse.ArgumentParser(description="Extract OpenF1 lap enrichment fields from JSONB snapshots.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    with psycopg.connect(args.database_url) as conn:
        snapshots = conn.execute(
            """
            SELECT DISTINCT ON (session_key)
                session_key, payload, fetched_at
            FROM f1_api_snapshots
            WHERE provider = 'openf1'
              AND endpoint = 'laps'
              AND status = 'ok'
              AND year = %s
              AND session_key IS NOT NULL
            ORDER BY session_key, fetched_at DESC, id DESC
            """,
            (args.year,),
        ).fetchall()

        count = 0
        for session_key, payload, fetched_at in snapshots:
            rows = payload if isinstance(payload, list) else json.loads(payload)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                driver_number = to_int(row.get("driver_number"))
                lap_number = to_int(row.get("lap_number"))
                if driver_number is None or lap_number is None:
                    continue
                upsert_lap_enrichment(conn, int(session_key), driver_number, lap_number, row, fetched_at)
                count += 1
        conn.commit()

    print(f"Extracted lap enrichment rows: {count}")
    return 0


def upsert_lap_enrichment(
    conn: Any,
    session_key: int,
    driver_number: int,
    lap_number: int,
    row: dict[str, Any],
    fetched_at: Any,
) -> None:
    conn.execute(
        """
        INSERT INTO lap_enrichment
            (session_key, driver_number, lap_number, source_provider, source_endpoint,
             i1_speed, i2_speed, st_speed,
             segments_sector_1, segments_sector_2, segments_sector_3,
             raw_payload, fetched_at)
        VALUES
            (%s, %s, %s, 'openf1', 'laps',
             %s, %s, %s,
             %s::jsonb, %s::jsonb, %s::jsonb,
             %s::jsonb, %s)
        ON CONFLICT (session_key, driver_number, lap_number, source_provider) DO UPDATE SET
            i1_speed = EXCLUDED.i1_speed,
            i2_speed = EXCLUDED.i2_speed,
            st_speed = EXCLUDED.st_speed,
            segments_sector_1 = EXCLUDED.segments_sector_1,
            segments_sector_2 = EXCLUDED.segments_sector_2,
            segments_sector_3 = EXCLUDED.segments_sector_3,
            raw_payload = EXCLUDED.raw_payload,
            fetched_at = EXCLUDED.fetched_at,
            updated_at = NOW()
        """,
        (
            session_key,
            driver_number,
            lap_number,
            number_or_none(row.get("i1_speed")),
            number_or_none(row.get("i2_speed")),
            number_or_none(row.get("st_speed")),
            json.dumps(json_array_or_empty(row.get("segments_sector_1")), ensure_ascii=False),
            json.dumps(json_array_or_empty(row.get("segments_sector_2")), ensure_ascii=False),
            json.dumps(json_array_or_empty(row.get("segments_sector_3")), ensure_ascii=False),
            json.dumps(row, ensure_ascii=False),
            fetched_at,
        ),
    )


def json_array_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
