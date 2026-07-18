#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ingest_f1_to_postgres import load_database_url, split_csv


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TARGET_SESSIONS = ("Race", "Qualifying", "Sprint", "Sprint Qualifying")
TIER1_ENDPOINTS = (
    "drivers",
    "laps",
    "stints",
    "pit",
    "intervals",
    "weather",
    "race_control",
    "session_result",
    "starting_grid",
)
TIER2_ENDPOINTS = ("car_data", "location")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 2025 Tier 1/Tier 2 data coverage in PostgreSQL.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--session-names", default=",".join(TARGET_SESSIONS))
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    with psycopg.connect(args.database_url) as conn:
        report = build_report(conn, args.year, split_csv(args.session_names))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    else:
        print_report(report)
    return 0


def build_report(conn: Any, year: int, session_names: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = {"year": year, "session_names": session_names}
    report["telemetry_storage_policy"] = {
        "canonical_source": "tracinginsights-expanded-v4 Parquet",
        "openf1_recovery_source": "f1_api_snapshots",
        "postgres_telemetry_tables": "deprecated empty rebuild cache",
    }
    report["sessions"] = fetch_rows(
        conn,
        """
        SELECT s.session_name, COUNT(*) AS sessions
        FROM sessions s
        JOIN meetings m ON m.meeting_key = s.meeting_key
        WHERE m.year = %s
          AND COALESCE(m.meeting_name, '') NOT ILIKE '%%testing%%'
          AND s.session_name = ANY(%s)
        GROUP BY s.session_name
        ORDER BY s.session_name
        """,
        (year, session_names),
    )
    report["openf1_endpoint_status"] = fetch_endpoint_status(conn, year, session_names)
    report["normalized_counts"] = fetch_rows(
        conn,
        """
        WITH target AS (
            SELECT s.session_key, s.session_name
            FROM sessions s
            JOIN meetings m ON m.meeting_key = s.meeting_key
            WHERE m.year = %s
              AND COALESCE(m.meeting_name, '') NOT ILIKE '%%testing%%'
              AND s.session_name = ANY(%s)
        )
        SELECT t.session_name,
               COUNT(*) AS sessions,
               SUM((SELECT COUNT(*) FROM laps l WHERE l.session_key = t.session_key)) AS laps,
               SUM((SELECT COUNT(*) FROM lap_enrichment le WHERE le.session_key = t.session_key)) AS lap_enrichment
        FROM target t
        GROUP BY t.session_name
        ORDER BY t.session_name
        """,
        (year, session_names),
    )
    report["remaining_gaps"] = fetch_rows(
        conn,
        """
        WITH target AS (
            SELECT s.session_key, m.meeting_name, s.session_name
            FROM sessions s
            JOIN meetings m ON m.meeting_key = s.meeting_key
            WHERE m.year = %s
              AND COALESCE(m.meeting_name, '') NOT ILIKE '%%testing%%'
              AND s.session_name = ANY(%s)
        ),
        endpoints AS (
            SELECT unnest(%s::text[]) AS endpoint
        ),
        matrix AS (
            SELECT t.meeting_name, t.session_name, t.session_key, e.endpoint
            FROM target t
            CROSS JOIN endpoints e
            WHERE is_endpoint_applicable(t.session_name, e.endpoint)
        )
        SELECT matrix.meeting_name,
               matrix.session_name,
               matrix.session_key,
               matrix.endpoint,
               COALESCE(v.status, 'missing') AS status,
               COALESCE(v.record_count, 0) AS record_count,
               v.error_message
        FROM matrix
        LEFT JOIN v_latest_api_snapshots v
          ON v.provider = 'openf1'
         AND v.session_key = matrix.session_key
         AND v.endpoint = matrix.endpoint
        WHERE v.id IS NULL OR v.status = 'error'
        ORDER BY matrix.meeting_name, matrix.session_name, matrix.endpoint
        """,
        (year, session_names, list(TIER1_ENDPOINTS + TIER2_ENDPOINTS)),
    )
    return report


def fetch_endpoint_status(conn: Any, year: int, session_names: list[str]) -> list[dict[str, Any]]:
    return fetch_rows(
        conn,
        """
        SELECT v.endpoint,
               s.session_name,
               v.status,
               COUNT(*) AS sessions,
               SUM(v.record_count) AS records
        FROM v_latest_api_snapshots v
        JOIN sessions s ON s.session_key = v.session_key
        JOIN meetings m ON m.meeting_key = s.meeting_key
        WHERE m.year = %s
          AND COALESCE(m.meeting_name, '') NOT ILIKE '%%testing%%'
          AND s.session_name = ANY(%s)
          AND v.provider = 'openf1'
          AND v.endpoint = ANY(%s)
          AND is_endpoint_applicable(s.session_name, v.endpoint)
        GROUP BY v.endpoint, s.session_name, v.status
        ORDER BY v.endpoint, s.session_name, v.status
        """,
        (year, session_names, list(TIER1_ENDPOINTS + TIER2_ENDPOINTS)),
    )


def fetch_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [desc.name for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def print_report(report: dict[str, Any]) -> None:
    print(f"2025 Tier audit: year={report['year']}")
    policy = report["telemetry_storage_policy"]
    print(
        "Telemetry storage: "
        f"canonical={policy['canonical_source']}; "
        f"OpenF1 recovery={policy['openf1_recovery_source']}; "
        f"PG={policy['postgres_telemetry_tables']}"
    )
    print("\nSessions:")
    for row in report["sessions"]:
        print(f"  {row['session_name']}: {row['sessions']}")

    print("\nOpenF1 endpoint status:")
    for row in report["openf1_endpoint_status"]:
        print(
            f"  {row['endpoint']:<14} {row['session_name']:<18} "
            f"{row['status']:<7} sessions={row['sessions']} records={row['records']}"
        )

    print("\nNormalized rows:")
    for row in report["normalized_counts"]:
        print(
            f"  {row['session_name']:<18} sessions={row['sessions']} "
            f"laps={row['laps']} lap_enrichment={row['lap_enrichment']}"
        )

    print("\nRemaining applicable gaps:")
    if not report["remaining_gaps"]:
        print("  none")
    for row in report["remaining_gaps"]:
        print(
            f"  {row['meeting_name']} / {row['session_name']} / "
            f"{row['endpoint']}: {row['status']} records={row['record_count']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
