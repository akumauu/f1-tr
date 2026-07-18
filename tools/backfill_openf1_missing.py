#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from ingest_f1_to_postgres import (
    OPENF1_BASE_URL,
    OpenF1Client,
    fetch_openf1_session_bundle,
    load_database_url,
    split_csv,
    store_openf1_session_bundle,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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
    parser = argparse.ArgumentParser(description="Backfill only missing OpenF1 snapshots by data tier.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--session-names", default="Race,Qualifying,Sprint,Sprint Qualifying")
    parser.add_argument("--tier", choices=("tier1", "tier2", "all"), default="all")
    parser.add_argument("--endpoints", help="Comma-separated endpoint override.")
    parser.add_argument("--session-key", type=int, help="Restrict to a single session.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry latest error snapshots as well as missing snapshots.")
    parser.add_argument("--include-not-applicable", action="store_true", help="Also try endpoints known to be absent for some session types.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    endpoints = choose_endpoints(args.tier, args.endpoints)
    client = OpenF1Client(OPENF1_BASE_URL, args.timeout, args.retries)

    with psycopg.connect(args.database_url) as conn:
        targets = find_targets(
            conn,
            year=args.year,
            session_names=split_csv(args.session_names),
            endpoints=endpoints,
            session_key=args.session_key,
            retry_errors=args.retry_errors,
            include_not_applicable=args.include_not_applicable,
        )
        print(f"Backfill targets: {len(targets)} session-endpoint pairs")
        for target in targets:
            print(
                f"  {target['meeting_name']} / {target['session_name']} "
                f"session={target['session_key']} endpoint={target['endpoint']} "
                f"latest_status={target['latest_status']}"
            )
        if args.dry_run or not targets:
            return 0

        grouped: dict[int, dict[str, Any]] = {}
        for target in targets:
            session = grouped.setdefault(
                int(target["session_key"]),
                {
                    "year": args.year,
                    "meeting_key": target["meeting_key"],
                    "session_key": target["session_key"],
                    "endpoints": [],
                },
            )
            session["endpoints"].append(target["endpoint"])

        for index, session in enumerate(grouped.values(), start=1):
            print(
                f"Fetching {index}/{len(grouped)} session={session['session_key']} "
                f"endpoints={','.join(session['endpoints'])}",
                flush=True,
            )
            bundle = fetch_openf1_session_bundle(
                client=client,
                year=args.year,
                meeting_key=session["meeting_key"],
                session_key=session["session_key"],
                endpoints=session["endpoints"],
                request_sleep=args.sleep,
                endpoint_workers=args.workers,
                strict=False,
            )
            store_openf1_session_bundle(conn, bundle)
            conn.commit()
            time.sleep(max(args.sleep, 0.0))
    return 0


def choose_endpoints(tier: str, override: str | None) -> list[str]:
    if override:
        return split_csv(override)
    if tier == "tier1":
        return list(TIER1_ENDPOINTS)
    if tier == "tier2":
        return list(TIER2_ENDPOINTS)
    return list(TIER1_ENDPOINTS + TIER2_ENDPOINTS)


def find_targets(
    conn: Any,
    year: int,
    session_names: list[str],
    endpoints: list[str],
    session_key: int | None,
    retry_errors: bool,
    include_not_applicable: bool,
) -> list[dict[str, Any]]:
    status_predicate = "v.id IS NULL"
    if retry_errors:
        status_predicate = "(v.id IS NULL OR v.status = 'error')"
    applicability_predicate = "TRUE" if include_not_applicable else "is_endpoint_applicable(s.session_name, e.endpoint)"
    rows = conn.execute(
        f"""
        WITH endpoint_list AS (
            SELECT unnest(%s::text[]) AS endpoint
        )
        SELECT m.meeting_name,
               s.session_name,
               s.meeting_key,
               s.session_key,
               e.endpoint,
               COALESCE(v.status, 'missing') AS latest_status
        FROM sessions s
        JOIN meetings m ON m.meeting_key = s.meeting_key
        CROSS JOIN endpoint_list e
        LEFT JOIN v_latest_api_snapshots v
          ON v.provider = 'openf1'
         AND v.session_key = s.session_key
         AND v.endpoint = e.endpoint
        WHERE m.year = %s
          AND COALESCE(m.meeting_name, '') NOT ILIKE '%%testing%%'
          AND s.session_name = ANY(%s)
          AND (%s::integer IS NULL OR s.session_key = %s::integer)
          AND {applicability_predicate}
          AND {status_predicate}
        ORDER BY m.date_start, s.date_start, e.endpoint
        """,
        (endpoints, year, session_names, session_key, session_key),
    ).fetchall()
    columns = ["meeting_name", "session_name", "meeting_key", "session_key", "endpoint", "latest_status"]
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())
