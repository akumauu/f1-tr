#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


OPENF1_BASE_URL = "https://api.openf1.org/v1"
OPENF1_SESSION_ENDPOINTS = (
    "drivers",
    "laps",
    "stints",
    "team_radio",
    "pit",
    "position",
    "race_control",
    "intervals",
    "weather",
    "session_result",
    "starting_grid",
)

OPENF1_ENDPOINT_CATEGORIES = {
    "meetings": "season_index",
    "sessions": "season_index",
    "drivers": "participants",
    "laps": "lap_core",
    "stints": "strategy",
    "team_radio": "team_radio",
    "pit": "strategy",
    "position": "race_state",
    "car_data": "telemetry",
    "location": "telemetry",
    "race_control": "race_control",
    "intervals": "race_state",
    "weather": "weather",
    "session_result": "results",
    "starting_grid": "results",
}

FASTF1_ENDPOINT_CATEGORIES = {
    "event": "season_index",
    "results": "results",
    "laps": "lap_enrichment",
    "weather": "weather",
    "race_control": "race_control",
    "car_data": "telemetry",
    "pos_data": "telemetry",
}

SESSION_ALIASES = {
    "r": {"race"},
    "race": {"race"},
    "q": {"qualifying"},
    "qualifying": {"qualifying"},
    "s": {"sprint"},
    "sprint": {"sprint"},
    "sq": {"sprint qualifying", "sprint shootout"},
    "sprint qualifying": {"sprint qualifying"},
    "sprint shootout": {"sprint shootout"},
    "fp2": {"practice 2", "free practice 2", "fp2"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest OpenF1 and FastF1 data into PostgreSQL.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--meeting-key", type=int)
    parser.add_argument("--session-key", type=int)
    parser.add_argument("--fastf1-event", default="Barcelona")
    parser.add_argument("--fastf1-session", default="R")
    parser.add_argument("--source", choices=("all", "openf1", "fastf1"), default="all")
    parser.add_argument("--season", action="store_true", help="Ingest all selected sessions for --year.")
    parser.add_argument("--session-names", default="R,Q,SQ,Sprint", help="Comma-separated session aliases for --season.")
    parser.add_argument("--include-testing", action="store_true", help="Include pre-season testing meetings in --season.")
    parser.add_argument("--openf1-base-url", default=OPENF1_BASE_URL)
    parser.add_argument("--openf1-endpoints", default=",".join(OPENF1_SESSION_ENDPOINTS))
    parser.add_argument("--cache", default=".fastf1-cache")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--request-sleep", type=float, default=1.0)
    parser.add_argument("--openf1-workers", type=int, default=3, help="Concurrent OpenF1 endpoint requests per session.")
    parser.add_argument("--openf1-session-workers", type=int, default=2, help="Concurrent OpenF1 sessions in --season mode.")
    parser.add_argument("--skip-existing-snapshots", action="store_true", help="Skip OpenF1 session endpoints with a successful snapshot.")
    parser.add_argument("--strict-openf1", action="store_true")
    parser.add_argument("--fastf1-telemetry", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--migrations-dir", default="backend/migrations")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required. Set .env or pass --database-url.")

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    with psycopg.connect(args.database_url) as conn:
        if args.migrate:
            run_migrations(conn, Path(args.migrations_dir))

        summary: dict[str, int] = {}
        if args.source in ("all", "openf1"):
            client = OpenF1Client(args.openf1_base_url, args.timeout, args.retries)
            if args.season:
                summary.update(
                    ingest_openf1_season(
                        conn=conn,
                        client=client,
                        year=args.year,
                        endpoints=split_csv(args.openf1_endpoints),
                        session_names=split_csv(args.session_names),
                        request_sleep=args.request_sleep,
                        endpoint_workers=args.openf1_workers,
                        session_workers=args.openf1_session_workers,
                        skip_existing=args.skip_existing_snapshots,
                        strict=args.strict_openf1,
                        include_testing=args.include_testing,
                    )
                )
            else:
                summary.update(
                    ingest_openf1(
                        conn=conn,
                        client=client,
                        year=args.year,
                        meeting_key=args.meeting_key,
                        session_key=args.session_key,
                        endpoints=split_csv(args.openf1_endpoints),
                        request_sleep=args.request_sleep,
                        endpoint_workers=args.openf1_workers,
                        skip_existing=args.skip_existing_snapshots,
                        strict=args.strict_openf1,
                    )
                )

        if args.source in ("all", "fastf1"):
            if args.season:
                print("[WARN] Full-season FastF1 ingest is intentionally not automatic. Run per session to control cache size.")
                if args.source == "fastf1":
                    raise SystemExit("Use per-session FastF1 ingest, or run --source openf1 --season first.")
                conn.commit()
                print("Ingest complete:")
                for key in sorted(summary):
                    print(f"  {key}: {summary[key]}")
                return 0
            if args.session_key is None:
                raise SystemExit("FastF1 ingest requires --session-key so normalized rows can attach to a session.")
            summary.update(
                ingest_fastf1(
                    conn=conn,
                    year=args.year,
                    meeting_key=args.meeting_key,
                    session_key=args.session_key,
                    event=args.fastf1_event,
                    session_name=args.fastf1_session,
                    cache_dir=args.cache,
                    telemetry=args.fastf1_telemetry,
                )
            )

        conn.commit()

    print("Ingest complete:")
    for key in sorted(summary):
        print(f"  {key}: {summary[key]}")
    return 0


class OpenF1Client:
    def __init__(self, base_url: str, timeout: int, retries: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, retries)

    def get(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        clean = {key: value for key, value in params.items() if value is not None}
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                    if not isinstance(data, list):
                        raise RuntimeError(f"expected JSON array from {url}")
                    return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 404:
                    raise RuntimeError(f"OpenF1 endpoint unavailable: {url}: HTTP 404 Not Found")
                if exc.code == 429 and attempt + 1 < self.retries:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        sleep_for = float(retry_after) if retry_after else 20.0
                    except ValueError:
                        sleep_for = 20.0
                    time.sleep(max(sleep_for, 20.0))
                    continue
                if 400 <= exc.code < 500:
                    raise RuntimeError(f"OpenF1 GET failed: {url}: HTTP Error {exc.code}")
                if attempt + 1 < self.retries:
                    time.sleep(1.5 * (attempt + 1))
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"OpenF1 GET failed after {self.retries} attempts: {url}: {last_error}")


def ingest_openf1(
    conn: Any,
    client: OpenF1Client,
    year: int,
    meeting_key: int | None,
    session_key: int | None,
    endpoints: list[str],
    request_sleep: float,
    endpoint_workers: int,
    skip_existing: bool,
    strict: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    meetings_query = {"year": year, "meeting_key": meeting_key}
    meetings = client.get("meetings", **meetings_query)
    store_snapshot(conn, "openf1", "meetings", year, meeting_key, None, meetings_query, meetings)
    upsert_meetings(conn, meetings)
    counts["openf1.meetings"] = len(meetings)
    if meeting_key is None and meetings:
        meeting_key = to_int(meetings[0].get("meeting_key"))

    sessions_query = {"meeting_key": meeting_key, "session_key": session_key}
    sessions = client.get("sessions", **sessions_query)
    store_snapshot(conn, "openf1", "sessions", year, meeting_key, session_key, sessions_query, sessions)
    upsert_sessions(conn, sessions)
    counts["openf1.sessions"] = len(sessions)
    if session_key is None and sessions:
        session_key = to_int(sessions[0].get("session_key"))

    if session_key is None:
        return counts

    existing_successes = set()
    if skip_existing:
        existing_successes = fetch_existing_openf1_snapshots(conn, [session_key], endpoints)
    remaining_endpoints = filter_existing_endpoints(session_key, endpoints, existing_successes)
    counts["openf1.skipped_existing"] = len(endpoints) - len(remaining_endpoints)

    counts.update(
        ingest_openf1_session_endpoints(
            conn=conn,
            client=client,
            year=year,
            meeting_key=meeting_key,
            session_key=session_key,
            endpoints=remaining_endpoints,
            request_sleep=request_sleep,
            endpoint_workers=endpoint_workers,
            strict=strict,
        )
    )
    return counts


def ingest_openf1_season(
    conn: Any,
    client: OpenF1Client,
    year: int,
    endpoints: list[str],
    session_names: list[str],
    request_sleep: float,
    endpoint_workers: int,
    session_workers: int,
    skip_existing: bool,
    strict: bool,
    include_testing: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    meetings_query = {"year": year}
    meetings = client.get("meetings", **meetings_query)
    store_snapshot(conn, "openf1", "meetings", year, None, None, meetings_query, meetings)
    upsert_meetings(conn, meetings)
    counts["openf1.meetings"] = len(meetings)

    selected_sessions: list[dict[str, Any]] = []
    for meeting in meetings:
        if is_testing_meeting(meeting) and not include_testing:
            continue
        meeting_key = to_int(meeting.get("meeting_key"))
        if meeting_key is None:
            continue
        print(
            f"Fetching sessions for meeting {meeting_key} / {meeting.get('meeting_name')}",
            flush=True,
        )
        sessions_query = {"meeting_key": meeting_key}
        sessions = client.get("sessions", **sessions_query)
        store_snapshot(conn, "openf1", "sessions", year, meeting_key, None, sessions_query, sessions)
        upsert_sessions(conn, sessions)
        counts["openf1.sessions"] = counts.get("openf1.sessions", 0) + len(sessions)

        for session in filter_sessions(sessions, session_names):
            if is_future_session(session):
                continue
            session_key = to_int(session.get("session_key"))
            if session_key is None:
                continue
            selected_sessions.append(
                {
                    "meeting_key": meeting_key,
                    "session_key": session_key,
                    "session_name": session.get("session_name"),
                }
            )

    print(
        f"Selected {len(selected_sessions)} sessions for {year}; "
        f"session_workers={max(1, session_workers)}, endpoint_workers={max(1, endpoint_workers)}",
        flush=True,
    )
    counts["openf1.selected_sessions"] = len(selected_sessions)
    existing_successes: set[tuple[int, str]] = set()
    if skip_existing:
        existing_successes = fetch_existing_openf1_snapshots(
            conn,
            [session["session_key"] for session in selected_sessions],
            endpoints,
        )
        counts["openf1.existing_successful_snapshots"] = len(existing_successes)

    if max(1, session_workers) == 1:
        for index, session in enumerate(selected_sessions, start=1):
            print_season_session_progress(index, len(selected_sessions), session)
            remaining_endpoints = filter_existing_endpoints(session["session_key"], endpoints, existing_successes)
            skipped = len(endpoints) - len(remaining_endpoints)
            counts["openf1.skipped_existing"] = counts.get("openf1.skipped_existing", 0) + skipped
            if not remaining_endpoints:
                print("  All requested endpoints already have successful snapshots; skipping.", flush=True)
                continue
            session_counts = fetch_openf1_session_bundle(
                client=client,
                year=year,
                meeting_key=session["meeting_key"],
                session_key=session["session_key"],
                endpoints=remaining_endpoints,
                request_sleep=request_sleep,
                endpoint_workers=endpoint_workers,
                strict=strict,
            )
            store_openf1_session_bundle(conn, session_counts)
            merge_counts(counts, session_counts["counts"])
            conn.commit()
        return counts

    with ThreadPoolExecutor(max_workers=max(1, session_workers)) as pool:
        futures = {}
        for index, session in enumerate(selected_sessions, start=1):
            remaining_endpoints = filter_existing_endpoints(session["session_key"], endpoints, existing_successes)
            skipped = len(endpoints) - len(remaining_endpoints)
            counts["openf1.skipped_existing"] = counts.get("openf1.skipped_existing", 0) + skipped
            if not remaining_endpoints:
                continue
            futures[
                pool.submit(
                    fetch_openf1_session_bundle,
                    client,
                    year,
                    session["meeting_key"],
                    session["session_key"],
                    remaining_endpoints,
                    request_sleep,
                    endpoint_workers,
                    strict,
                )
            ] = (index, session)
        for future in as_completed(futures):
            index, session = futures[future]
            print_season_session_progress(index, len(selected_sessions), session, prefix="Storing")
            session_counts = future.result()
            store_openf1_session_bundle(conn, session_counts)
            merge_counts(counts, session_counts["counts"])
            conn.commit()
    return counts


def ingest_openf1_session_endpoints(
    conn: Any,
    client: OpenF1Client,
    year: int,
    meeting_key: int | None,
    session_key: int,
    endpoints: list[str],
    request_sleep: float,
    endpoint_workers: int,
    strict: bool,
) -> dict[str, int]:
    bundle = fetch_openf1_session_bundle(
        client=client,
        year=year,
        meeting_key=meeting_key,
        session_key=session_key,
        endpoints=endpoints,
        request_sleep=request_sleep,
        endpoint_workers=endpoint_workers,
        strict=strict,
    )
    store_openf1_session_bundle(conn, bundle)
    return bundle["counts"]


def fetch_openf1_session_bundle(
    client: OpenF1Client,
    year: int,
    meeting_key: int | None,
    session_key: int,
    endpoints: list[str],
    request_sleep: float,
    endpoint_workers: int,
    strict: bool,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    payloads: dict[str, list[dict[str, Any]]] = {}
    statuses: dict[str, str] = {}
    errors: dict[str, str | None] = {}
    workers = max(1, endpoint_workers)

    def fetch_one(index: int, endpoint: str) -> tuple[str, list[dict[str, Any]]]:
        if request_sleep > 0:
            time.sleep(index * request_sleep)
        print(f"  Fetching OpenF1 {endpoint} for session {session_key}", flush=True)
        return endpoint, client.get(endpoint, session_key=session_key)

    if workers == 1:
        iterable = []
        for index, endpoint in enumerate(endpoints):
            try:
                iterable.append((*fetch_one(index, endpoint), "ok", None))
            except Exception as exc:
                if strict:
                    raise
                print(f"[WARN] OpenF1 {endpoint} session {session_key} unavailable: {exc}", flush=True)
                iterable.append((endpoint, [], "error", str(exc)))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fetch_one, index, endpoint): endpoint
                for index, endpoint in enumerate(endpoints)
            }
            iterable = []
            for future in as_completed(futures):
                endpoint = futures[future]
                try:
                    iterable.append((*future.result(), "ok", None))
                except Exception as exc:
                    if strict:
                        raise
                    print(f"[WARN] OpenF1 {endpoint} session {session_key} unavailable: {exc}", flush=True)
                    iterable.append((endpoint, [], "error", str(exc)))

    for endpoint, rows, status, error_message in iterable:
        if status == "ok" and not rows:
            status = "empty"
        payloads[endpoint] = rows
        statuses[endpoint] = status
        errors[endpoint] = error_message
        counts[f"openf1.{endpoint}"] = len(rows)
    return {
        "year": year,
        "meeting_key": meeting_key,
        "session_key": session_key,
        "payloads": payloads,
        "statuses": statuses,
        "errors": errors,
        "counts": counts,
    }


def store_openf1_session_bundle(conn: Any, bundle: dict[str, Any]) -> None:
    year = bundle["year"]
    meeting_key = bundle["meeting_key"]
    session_key = bundle["session_key"]
    for endpoint, rows in bundle["payloads"].items():
        query = {"session_key": session_key}
        status = bundle.get("statuses", {}).get(endpoint, "ok" if rows else "empty")
        error_message = bundle.get("errors", {}).get(endpoint)
        store_snapshot(conn, "openf1", endpoint, year, meeting_key, session_key, query, rows, status, error_message)
        if status != "error":
            store_openf1_endpoint(conn, endpoint, rows)


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def print_season_session_progress(
    index: int,
    total: int,
    session: dict[str, Any],
    prefix: str = "Ingesting",
) -> None:
    print(
        f"{prefix} OpenF1 session {index}/{total}: "
        f"{session['session_key']} / {session.get('session_name')} / meeting {session['meeting_key']}",
        flush=True,
    )


def fetch_existing_openf1_snapshots(
    conn: Any,
    session_keys: list[int],
    endpoints: list[str],
) -> set[tuple[int, str]]:
    if not session_keys or not endpoints:
        return set()
    rows = conn.execute(
        """
        SELECT session_key, endpoint
        FROM f1_api_snapshots
        WHERE provider = 'openf1'
          AND session_key = ANY(%s)
          AND endpoint = ANY(%s)
          AND COALESCE(status, 'ok') IN ('ok', 'empty')
        GROUP BY session_key, endpoint
        HAVING COUNT(*) > 0
        """,
        (session_keys, endpoints),
    ).fetchall()
    return {(int(row[0]), str(row[1])) for row in rows}


def filter_existing_endpoints(
    session_key: int,
    endpoints: list[str],
    existing_successes: set[tuple[int, str]],
) -> list[str]:
    if not existing_successes:
        return endpoints
    return [endpoint for endpoint in endpoints if (session_key, endpoint) not in existing_successes]
    return counts


def ingest_fastf1(
    conn: Any,
    year: int,
    meeting_key: int | None,
    session_key: int,
    event: str,
    session_name: str,
    cache_dir: str,
    telemetry: bool,
) -> dict[str, int]:
    try:
        import fastf1
    except ImportError as exc:
        raise SystemExit("FastF1 is not installed. Run: python -m pip install -r requirements.txt") from exc

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)
    session = resolve_fastf1_session(fastf1, year, event, session_name)
    try:
        session.load(laps=True, telemetry=telemetry, weather=True, messages=True)
    except TypeError:
        session.load(telemetry=telemetry, weather=True, messages=True)

    data = {
        "event": safe_session_records(session, "event"),
        "results": safe_session_records(session, "results"),
        "laps": safe_session_records(session, "laps"),
        "weather": safe_session_records(session, "weather_data"),
        "race_control": safe_session_records(session, "race_control_messages"),
    }
    if telemetry:
        data["car_data"] = safe_session_records(session, "car_data")
        data["pos_data"] = safe_session_records(session, "pos_data")

    counts: dict[str, int] = {}
    for endpoint, rows in data.items():
        store_snapshot(
            conn,
            "fastf1",
            endpoint,
            year,
            meeting_key,
            session_key,
            {"event": event, "session": session_name},
            rows,
        )
        counts[f"fastf1.{endpoint}"] = len(rows)

    drivers = fastf1_drivers(data["results"], session_key)
    laps = fastf1_laps(data["laps"], session_key, drivers)
    stints = fastf1_stints_from_laps(laps)
    upsert_drivers(conn, drivers)
    upsert_laps(conn, laps)
    upsert_stints(conn, stints)
    counts["fastf1.normalized_drivers"] = len(drivers)
    counts["fastf1.normalized_laps"] = len(laps)
    counts["fastf1.normalized_stints"] = len(stints)
    return counts


def store_openf1_endpoint(conn: Any, endpoint: str, rows: list[dict[str, Any]]) -> None:
    if endpoint == "drivers":
        upsert_drivers(conn, [normalize_openf1_driver(row) for row in rows])
    elif endpoint == "laps":
        upsert_laps(conn, [normalize_openf1_lap(row) for row in rows])
    elif endpoint == "stints":
        upsert_stints(conn, [normalize_openf1_stint(row) for row in rows])
    elif endpoint == "team_radio":
        upsert_team_radio(conn, rows)
    elif endpoint == "pit":
        upsert_pit_stops(conn, rows)
    elif endpoint == "position":
        upsert_positions(conn, rows)
    elif endpoint == "race_control":
        upsert_race_control(conn, rows)


def store_snapshot(
    conn: Any,
    provider: str,
    endpoint: str,
    year: int | None,
    meeting_key: int | None,
    session_key: int | None,
    query: dict[str, Any],
    payload: list[dict[str, Any]],
    status: str = "ok",
    error_message: str | None = None,
) -> None:
    if status == "ok" and not payload:
        status = "empty"
    category = snapshot_category(provider, endpoint)
    conn.execute(
        """
        INSERT INTO f1_api_snapshots
            (provider, endpoint, category, year, meeting_key, session_key, query, payload, record_count, status, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
        """,
        (
            provider,
            endpoint,
            category,
            year,
            meeting_key,
            session_key,
            json.dumps(json_safe(query), ensure_ascii=False),
            json.dumps(json_safe(payload), ensure_ascii=False),
            len(payload),
            status,
            error_message,
        ),
    )


def snapshot_category(provider: str, endpoint: str) -> str:
    if provider == "openf1":
        return OPENF1_ENDPOINT_CATEGORIES.get(endpoint, "uncategorized")
    if provider == "fastf1":
        return FASTF1_ENDPOINT_CATEGORIES.get(endpoint, "uncategorized")
    return "uncategorized"


def upsert_meetings(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO meetings (meeting_key, meeting_name, location, country_name, circuit_name, year, date_start)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (meeting_key) DO UPDATE SET
        meeting_name = EXCLUDED.meeting_name,
        location = EXCLUDED.location,
        country_name = EXCLUDED.country_name,
        circuit_name = EXCLUDED.circuit_name,
        year = EXCLUDED.year,
        date_start = EXCLUDED.date_start
    """
    for row in rows:
        conn.execute(
            sql,
            (
                to_int(row.get("meeting_key")),
                row.get("meeting_name") or "",
                row.get("location"),
                row.get("country_name"),
                row.get("circuit_short_name") or row.get("circuit_name"),
                to_int(row.get("year")),
                row.get("date_start"),
            ),
        )


def upsert_sessions(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO sessions (session_key, meeting_key, session_name, session_type, date_start, date_end)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (session_key) DO UPDATE SET
        meeting_key = EXCLUDED.meeting_key,
        session_name = EXCLUDED.session_name,
        session_type = EXCLUDED.session_type,
        date_start = EXCLUDED.date_start,
        date_end = EXCLUDED.date_end
    """
    for row in rows:
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("meeting_key")),
                row.get("session_name") or "",
                row.get("session_type"),
                row.get("date_start"),
                row.get("date_end"),
            ),
        )


def upsert_drivers(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO drivers
        (session_key, driver_number, full_name, name_acronym, team_name, team_colour, headshot_url, country_code)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (session_key, driver_number) DO UPDATE SET
        full_name = COALESCE(NULLIF(EXCLUDED.full_name, ''), drivers.full_name),
        name_acronym = COALESCE(NULLIF(EXCLUDED.name_acronym, ''), drivers.name_acronym),
        team_name = COALESCE(NULLIF(EXCLUDED.team_name, ''), drivers.team_name),
        team_colour = COALESCE(NULLIF(EXCLUDED.team_colour, ''), drivers.team_colour),
        headshot_url = COALESCE(NULLIF(EXCLUDED.headshot_url, ''), drivers.headshot_url),
        country_code = COALESCE(NULLIF(EXCLUDED.country_code, ''), drivers.country_code)
    """
    for row in rows:
        if to_int(row.get("session_key")) is None or to_int(row.get("driver_number")) is None:
            continue
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("driver_number")),
                clean_str(row.get("full_name")),
                clean_str(row.get("name_acronym"))[:3],
                clean_str(row.get("team_name")),
                normalize_color(row.get("team_colour")),
                clean_str(row.get("headshot_url")),
                clean_str(row.get("country_code"))[:3],
            ),
        )


def upsert_laps(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO laps
        (session_key, driver_number, lap_number, lap_duration, duration_sector_1, duration_sector_2,
         duration_sector_3, is_pit_out_lap, date_start)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (session_key, driver_number, lap_number) DO UPDATE SET
        lap_duration = COALESCE(EXCLUDED.lap_duration, laps.lap_duration),
        duration_sector_1 = COALESCE(EXCLUDED.duration_sector_1, laps.duration_sector_1),
        duration_sector_2 = COALESCE(EXCLUDED.duration_sector_2, laps.duration_sector_2),
        duration_sector_3 = COALESCE(EXCLUDED.duration_sector_3, laps.duration_sector_3),
        is_pit_out_lap = EXCLUDED.is_pit_out_lap,
        date_start = COALESCE(EXCLUDED.date_start, laps.date_start)
    """
    for row in rows:
        if any(to_int(row.get(key)) is None for key in ("session_key", "driver_number", "lap_number")):
            continue
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("driver_number")),
                to_int(row.get("lap_number")),
                number_or_none(row.get("lap_duration")),
                number_or_none(row.get("duration_sector_1")),
                number_or_none(row.get("duration_sector_2")),
                number_or_none(row.get("duration_sector_3")),
                bool(row.get("is_pit_out_lap")),
                row.get("date_start"),
            ),
        )


def upsert_stints(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO stints (session_key, driver_number, stint_number, compound, tyre_age_at_start, lap_start, lap_end)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (session_key, driver_number, stint_number) DO UPDATE SET
        compound = COALESCE(NULLIF(EXCLUDED.compound, ''), stints.compound),
        tyre_age_at_start = COALESCE(EXCLUDED.tyre_age_at_start, stints.tyre_age_at_start),
        lap_start = COALESCE(EXCLUDED.lap_start, stints.lap_start),
        lap_end = COALESCE(EXCLUDED.lap_end, stints.lap_end)
    """
    for row in rows:
        if any(to_int(row.get(key)) is None for key in ("session_key", "driver_number", "stint_number")):
            continue
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("driver_number")),
                to_int(row.get("stint_number")),
                clean_str(row.get("compound")),
                to_int(row.get("tyre_age_at_start")),
                to_int(row.get("lap_start")),
                to_int(row.get("lap_end")),
            ),
        )


def upsert_team_radio(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO team_radio (session_key, meeting_key, driver_number, date, recording_url, translation_status)
    VALUES (%s, %s, %s, %s, %s, 'pending')
    ON CONFLICT (session_key, driver_number, date) DO UPDATE SET
        recording_url = EXCLUDED.recording_url
    """
    for row in rows:
        if not row.get("date"):
            continue
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("meeting_key")),
                to_int(row.get("driver_number")),
                row.get("date"),
                row.get("recording_url") or "",
            ),
        )


def upsert_pit_stops(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO pit_stops (session_key, driver_number, lap_number, pit_duration, date)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (session_key, driver_number, lap_number) DO UPDATE SET
        pit_duration = EXCLUDED.pit_duration,
        date = COALESCE(EXCLUDED.date, pit_stops.date)
    """
    for row in rows:
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                to_int(row.get("driver_number")),
                to_int(row.get("lap_number")),
                number_or_none(row.get("pit_duration")),
                row.get("date"),
            ),
        )


def upsert_positions(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO positions (session_key, driver_number, position, date)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (session_key, driver_number, date) DO UPDATE SET
        position = EXCLUDED.position
    """
    seen = set()
    for row in rows:
        key = (to_int(row.get("session_key")), to_int(row.get("driver_number")), row.get("date"))
        if key in seen or not key[2]:
            continue
        seen.add(key)
        conn.execute(sql, (key[0], key[1], to_int(row.get("position")), key[2]))


def upsert_race_control(conn: Any, rows: list[dict[str, Any]]) -> None:
    sql = """
    INSERT INTO race_control (session_key, date, category, flag, message, driver_number, lap_number)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT DO NOTHING
    """
    for row in rows:
        if not row.get("date"):
            continue
        conn.execute(
            sql,
            (
                to_int(row.get("session_key")),
                row.get("date"),
                row.get("category"),
                row.get("flag"),
                row.get("message"),
                to_int(row.get("driver_number")),
                to_int(row.get("lap_number")),
            ),
        )


def normalize_openf1_driver(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": row.get("session_key"),
        "driver_number": row.get("driver_number"),
        "full_name": row.get("full_name") or row.get("broadcast_name"),
        "name_acronym": row.get("name_acronym"),
        "team_name": row.get("team_name"),
        "team_colour": row.get("team_colour"),
        "headshot_url": row.get("headshot_url"),
        "country_code": row.get("country_code"),
    }


def normalize_openf1_lap(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": row.get("session_key"),
        "driver_number": row.get("driver_number"),
        "lap_number": row.get("lap_number"),
        "lap_duration": row.get("lap_duration"),
        "duration_sector_1": row.get("duration_sector_1"),
        "duration_sector_2": row.get("duration_sector_2"),
        "duration_sector_3": row.get("duration_sector_3"),
        "is_pit_out_lap": row.get("is_pit_out_lap"),
        "date_start": row.get("date_start"),
    }


def normalize_openf1_stint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": row.get("session_key"),
        "driver_number": row.get("driver_number"),
        "stint_number": row.get("stint_number"),
        "compound": row.get("compound"),
        "tyre_age_at_start": row.get("tyre_age_at_start"),
        "lap_start": row.get("lap_start"),
        "lap_end": row.get("lap_end"),
    }


def fastf1_drivers(rows: list[dict[str, Any]], session_key: int) -> list[dict[str, Any]]:
    drivers = []
    for row in rows:
        driver_number = to_int(row.get("DriverNumber"))
        if driver_number is None:
            continue
        drivers.append(
            {
                "session_key": session_key,
                "driver_number": driver_number,
                "full_name": row.get("FullName") or row.get("BroadcastName"),
                "name_acronym": row.get("Abbreviation"),
                "team_name": row.get("TeamName"),
                "team_colour": row.get("TeamColor"),
                "headshot_url": "",
                "country_code": row.get("CountryCode"),
            }
        )
    return drivers


def fastf1_laps(
    rows: list[dict[str, Any]],
    session_key: int,
    drivers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    acronym_to_number = {
        driver.get("name_acronym"): driver.get("driver_number")
        for driver in drivers
        if driver.get("name_acronym")
    }
    laps = []
    for row in rows:
        driver_number = to_int(row.get("DriverNumber")) or acronym_to_number.get(row.get("Driver"))
        lap_number = to_int(row.get("LapNumber"))
        if driver_number is None or lap_number is None:
            continue
        laps.append(
            {
                "session_key": session_key,
                "driver_number": driver_number,
                "lap_number": lap_number,
                "lap_duration": duration_seconds(row.get("LapTime")),
                "duration_sector_1": duration_seconds(row.get("Sector1Time")),
                "duration_sector_2": duration_seconds(row.get("Sector2Time")),
                "duration_sector_3": duration_seconds(row.get("Sector3Time")),
                "is_pit_out_lap": not is_missing(row.get("PitOutTime")),
                "date_start": datetime_to_iso(row.get("LapStartDate")),
                "_stint_number": to_int(row.get("Stint")),
                "_compound": clean_str(row.get("Compound")),
                "_tyre_life": to_int(row.get("TyreLife")),
            }
        )
    return laps


def fastf1_stints_from_laps(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for lap in laps:
        stint_number = to_int(lap.get("_stint_number"))
        driver_number = to_int(lap.get("driver_number"))
        if stint_number is not None and driver_number is not None:
            grouped[(driver_number, stint_number)].append(lap)

    stints = []
    for (driver_number, stint_number), rows in sorted(grouped.items()):
        lap_numbers = [to_int(row.get("lap_number")) for row in rows]
        lap_numbers = [value for value in lap_numbers if value is not None]
        tyre_life = [to_int(row.get("_tyre_life")) for row in rows]
        tyre_life = [value for value in tyre_life if value is not None]
        compounds = [clean_str(row.get("_compound")) for row in rows if clean_str(row.get("_compound"))]
        if not lap_numbers:
            continue
        stints.append(
            {
                "session_key": rows[0]["session_key"],
                "driver_number": driver_number,
                "stint_number": stint_number,
                "compound": most_common(compounds),
                "tyre_age_at_start": min(tyre_life) if tyre_life else None,
                "lap_start": min(lap_numbers),
                "lap_end": max(lap_numbers),
            }
        )
    return stints


def resolve_fastf1_session(fastf1: Any, year: int, event: str, session_name: str) -> Any:
    try:
        return fastf1.get_session(year, event, session_name)
    except Exception:
        schedule = fastf1.get_event_schedule(year)
        needle = event.lower()
        for _, row in schedule.iterrows():
            haystack = " ".join(
                str(row.get(column, ""))
                for column in ("EventName", "OfficialEventName", "Location", "Country", "CircuitName")
            ).lower()
            if needle in haystack:
                return fastf1.get_session(year, int(row["RoundNumber"]), session_name)
        raise


def dataframe_to_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_frame"):
        value = value.to_frame().T
    if not hasattr(value, "iterrows"):
        return [json_safe(value)]
    return [{str(key): json_safe(row.get(key)) for key in value.columns} for _, row in value.iterrows()]


def safe_session_records(session: Any, attr: str) -> list[dict[str, Any]]:
    try:
        return dataframe_to_records(getattr(session, attr, None))
    except Exception as exc:
        print(f"[WARN] FastF1 {attr} unavailable: {exc}")
        return []


def run_migrations(conn: Any, migrations_dir: Path) -> None:
    for path in sorted(migrations_dir.glob("*.up.sql")):
        sql = path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
        print(f"Applied migration: {path}")


def filter_sessions(rows: list[dict[str, Any]], requested_names: list[str]) -> list[dict[str, Any]]:
    wanted = expand_session_names(requested_names)
    out = []
    for row in rows:
        session_name = normalize_session_name(row.get("session_name"))
        session_type = normalize_session_name(row.get("session_type"))
        if session_name in wanted or session_type in wanted:
            out.append(row)
    return out


def is_testing_meeting(row: dict[str, Any]) -> bool:
    text = " ".join(
        clean_str(row.get(key))
        for key in ("meeting_name", "meeting_official_name", "location")
    ).lower()
    return "testing" in text or "pre-season" in text or "pre season" in text


def is_future_session(row: dict[str, Any]) -> bool:
    """避免把当前赛季尚未举行的赛程当成已完成比赛入库。"""
    value = clean_str(row.get("date_end") or row.get("date_start"))
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > datetime.now(timezone.utc)
    except ValueError:
        return False


def expand_session_names(values: list[str]) -> set[str]:
    wanted: set[str] = set()
    for value in values:
        normalized = normalize_session_name(value)
        aliases = SESSION_ALIASES.get(normalized)
        if aliases:
            wanted.update(aliases)
        elif normalized:
            wanted.add(normalized)
    return wanted


def normalize_session_name(value: Any) -> str:
    return " ".join(clean_str(value).lower().replace("_", " ").replace("-", " ").split())


def load_database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return "postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable"


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def clean_str(value: Any) -> str:
    return "" if is_missing(value) else str(value)


def to_int(value: Any) -> int | None:
    if is_missing(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def number_or_none(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def duration_seconds(value: Any) -> float | None:
    if is_missing(value):
        return None
    if hasattr(value, "total_seconds"):
        return round(float(value.total_seconds()), 6)
    return number_or_none(value)


def datetime_to_iso(value: Any) -> str | None:
    if is_missing(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def normalize_color(value: Any) -> str:
    text = clean_str(value).lstrip("#")
    return text if len(text) == 6 else ""


def most_common(values: list[str]) -> str:
    return max(set(values), key=values.count) if values else ""


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
