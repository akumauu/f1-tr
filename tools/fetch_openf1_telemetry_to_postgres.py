#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ingest_f1_to_postgres import (
    OpenF1Client,
    load_database_url,
    split_csv,
    store_snapshot,
    to_int,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch OpenF1 car_data/location telemetry snapshots into PostgreSQL.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--session-names", default="Race,Qualifying,Sprint,Sprint Qualifying")
    parser.add_argument("--endpoints", default="car_data,location")
    parser.add_argument("--session-key", type=int)
    parser.add_argument("--driver-number", type=int)
    parser.add_argument(
        "--all-drivers-per-session",
        action="store_true",
        help="每个 session 一次拉取全部车手，显著减少大批量赛季采集请求数。",
    )
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    client = OpenF1Client("https://api.openf1.org/v1", args.timeout, args.retries)
    endpoints = split_csv(args.endpoints)

    with psycopg.connect(args.database_url) as conn:
        targets = load_targets(conn, args.year, split_csv(args.session_names), args.session_key, args.driver_number)
        if args.all_drivers_per_session and args.driver_number is None:
            targets = unique_session_targets(targets)
            target_label = "sessions (all drivers per request)"
        else:
            target_label = "driver-session pairs"
        print(f"Selected telemetry targets: {len(targets)} {target_label}", flush=True)
        tasks: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for index, target in enumerate(targets, start=1):
            for endpoint in endpoints:
                if args.skip_existing and has_success_snapshot(
                    conn, endpoint, target["session_key"], target.get("driver_number")
                ):
                    counts[f"skipped.{endpoint}"] = counts.get(f"skipped.{endpoint}", 0) + 1
                    continue
                tasks.append({"index": index, "endpoint": endpoint, **target})

        if args.workers <= 1:
            for task in tasks:
                result = fetch_task(client, task)
                save_result(conn, args.year, result, counts)
                if args.sleep > 0:
                    time.sleep(args.sleep)
        else:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                future_map = {executor.submit(fetch_task, client, task): task for task in tasks}
                for completed, future in enumerate(as_completed(future_map), start=1):
                    result = future.result()
                    save_result(conn, args.year, result, counts)
                    print(
                        f"Stored {completed}/{len(tasks)}: {result['endpoint']} "
                        f"session={result['session_key']} driver={result.get('driver_number', 'ALL')} "
                        f"status={result['status']} records={len(result['payload'])}",
                        flush=True,
                    )
                    if args.sleep > 0:
                        time.sleep(args.sleep)

    print("Telemetry fetch complete:")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    return 0


def fetch_task(client: OpenF1Client, task: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(task["endpoint"])
    query = {"session_key": task["session_key"]}
    if task.get("driver_number") is not None:
        query["driver_number"] = task["driver_number"]
    print(
        f"Fetching {endpoint} {task['index']}: "
        f"session={task['session_key']} driver={task.get('driver_number', 'ALL')}",
        flush=True,
    )
    try:
        payload = client.get(endpoint, **query)
        status = "ok" if payload else "empty"
        error_message = None
    except Exception as exc:
        payload = []
        status = "error"
        error_message = str(exc)
        print(f"[WARN] {endpoint} failed for {query}: {exc}", flush=True)
    return {
        **task,
        "query": query,
        "payload": payload,
        "status": status,
        "error_message": error_message,
    }


def save_result(conn: Any, year: int, result: dict[str, Any], counts: dict[str, int]) -> None:
    store_snapshot(
        conn,
        "openf1",
        result["endpoint"],
        year,
        result["meeting_key"],
        result["session_key"],
        result["query"],
        result["payload"],
        result["status"],
        result["error_message"],
    )
    conn.commit()
    counts[f"openf1.{result['endpoint']}"] = counts.get(f"openf1.{result['endpoint']}", 0) + len(result["payload"])


def load_targets(
    conn: Any,
    year: int,
    session_names: list[str],
    session_key: int | None,
    driver_number: int | None,
) -> list[dict[str, int]]:
    rows = conn.execute(
        """
        SELECT DISTINCT s.session_key, s.meeting_key, d.driver_number
        FROM sessions s
        JOIN drivers d ON d.session_key = s.session_key
        WHERE s.date_start >= make_timestamptz(%s, 1, 1, 0, 0, 0)
          AND s.date_start < make_timestamptz(%s + 1, 1, 1, 0, 0, 0)
          AND (%s::bigint IS NULL OR s.session_key = %s::bigint)
          AND (%s::integer IS NULL OR d.driver_number = %s::integer)
          AND s.session_name = ANY(%s)
        ORDER BY s.session_key, d.driver_number
        """,
        (year, year, session_key, session_key, driver_number, driver_number, session_names),
    ).fetchall()
    return [
        {"session_key": int(row[0]), "meeting_key": int(row[1]), "driver_number": int(row[2])}
        for row in rows
        if to_int(row[0]) is not None and to_int(row[1]) is not None and to_int(row[2]) is not None
    ]


def unique_session_targets(targets: list[dict[str, int]]) -> list[dict[str, int]]:
    unique: dict[int, dict[str, int]] = {}
    for target in targets:
        unique[target["session_key"]] = {
            "session_key": target["session_key"],
            "meeting_key": target["meeting_key"],
        }
    return [unique[key] for key in sorted(unique)]


def has_success_snapshot(conn: Any, endpoint: str, session_key: int, driver_number: int | None) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM f1_api_snapshots
        WHERE provider = 'openf1'
          AND endpoint = %s
          AND session_key = %s
          AND ((%s::integer IS NULL AND NOT query ? 'driver_number') OR query->>'driver_number' = %s)
          AND status IN ('ok', 'empty')
        LIMIT 1
        """,
        (endpoint, session_key, driver_number, str(driver_number) if driver_number is not None else None),
    ).fetchone()
    return row is not None


if __name__ == "__main__":
    raise SystemExit(main())
