#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

from ingest_f1_to_postgres import (
    load_database_url,
    store_snapshot,
    upsert_drivers,
    upsert_laps,
    upsert_stints,
)


BASE_URL = "https://backend.f1insightshub.com"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill small FastF1-derived bundles from F1InsightsHub.")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--session-key", type=int, required=True)
    parser.add_argument("--fastf1-key", required=True, help="Example: 2025|Azerbaijan Grand Prix|Qualifying")
    parser.add_argument("--meeting-key", type=int)
    parser.add_argument("--sleep", type=float, default=0.75)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Missing dependency: run `python -m pip install -r requirements.txt`.") from exc

    summary = fetch_session_summary(args.fastf1_key, args.timeout)
    drivers = [
        str(row.get("driver_number"))
        for row in summary.get("driver_summaries", [])
        if row.get("driver_number") is not None
    ]
    if not drivers:
        raise SystemExit("No drivers returned by F1InsightsHub session_summary.")

    raw_bundles: list[dict[str, Any]] = []
    normalized_drivers: list[dict[str, Any]] = []
    normalized_laps: list[dict[str, Any]] = []
    normalized_stints: list[dict[str, Any]] = []

    for index, driver_number in enumerate(drivers, start=1):
        print(f"Fetching F1InsightsHub bundle {index}/{len(drivers)}: driver {driver_number}", flush=True)
        bundle = fetch_driver_bundle(args.fastf1_key, driver_number, args.year, args.timeout)
        raw_bundles.append(bundle)
        normalized_drivers.extend(normalize_drivers(bundle, args.session_key))
        normalized_laps.extend(normalize_laps(bundle, args.session_key))
        normalized_stints.extend(normalize_stints(bundle, args.session_key, driver_number))
        if args.sleep > 0:
            time.sleep(args.sleep)

    query = {"session_key": args.fastf1_key, "year": args.year}

    with psycopg.connect(args.database_url) as conn:
        store_snapshot(
            conn,
            "f1insightshub",
            "session_summary",
            args.year,
            args.meeting_key,
            args.session_key,
            query,
            summary.get("driver_summaries", []),
            "ok" if summary.get("driver_summaries") else "empty",
            None,
        )
        store_snapshot(
            conn,
            "f1insightshub",
            "analysis_driver_bundle",
            args.year,
            args.meeting_key,
            args.session_key,
            query,
            raw_bundles,
            "ok" if raw_bundles else "empty",
            None,
        )
        upsert_drivers(conn, normalized_drivers)
        upsert_laps(conn, normalized_laps)
        upsert_stints(conn, normalized_stints)
        conn.commit()

    print("Backfill complete:")
    print(f"  drivers: {len(normalized_drivers)}")
    print(f"  laps: {len(normalized_laps)}")
    print(f"  stints: {len(normalized_stints)}")
    return 0


def fetch_session_summary(fastf1_key: str, timeout: int) -> dict[str, Any]:
    key = urllib.parse.quote(fastf1_key, safe="")
    return get_json(f"{BASE_URL}/fastf1/session_summary?session_key={key}", timeout)


def fetch_driver_bundle(fastf1_key: str, driver_number: str, year: int, timeout: int) -> dict[str, Any]:
    key = urllib.parse.quote(fastf1_key, safe="")
    driver = urllib.parse.quote(str(driver_number), safe="")
    return get_json(f"{BASE_URL}/analysis/driver_bundle?session_key={key}&driver_number={driver}&year={year}", timeout)


def get_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "F1TR-data-backfill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return data


def normalize_drivers(bundle: dict[str, Any], session_key: int) -> list[dict[str, Any]]:
    driver = bundle.get("driver") or {}
    driver_number = driver.get("driver_number")
    if driver_number is None:
        return []
    return [
        {
            "session_key": session_key,
            "driver_number": driver_number,
            "full_name": driver.get("full_name"),
            "name_acronym": driver.get("name_acronym"),
            "team_name": driver.get("team_name"),
            "team_colour": driver.get("team_colour"),
            "headshot_url": "",
            "country_code": "",
        }
    ]


def normalize_laps(bundle: dict[str, Any], session_key: int) -> list[dict[str, Any]]:
    rows = []
    for lap in bundle.get("laps") or []:
        rows.append(
            {
                "session_key": session_key,
                "driver_number": lap.get("driver_number"),
                "lap_number": lap.get("lap_number"),
                "lap_duration": lap.get("lap_duration"),
                "duration_sector_1": lap.get("duration_sector_1"),
                "duration_sector_2": lap.get("duration_sector_2"),
                "duration_sector_3": lap.get("duration_sector_3"),
                "is_pit_out_lap": False,
                "date_start": lap.get("date_start"),
            }
        )
    return rows


def normalize_stints(bundle: dict[str, Any], session_key: int, driver_number: str) -> list[dict[str, Any]]:
    rows = []
    for stint in bundle.get("stints") or []:
        stint_number = stint.get("stint_number")
        if isinstance(stint_number, int) and stint_number == 0:
            stint_number = 1
        rows.append(
            {
                "session_key": session_key,
                "driver_number": driver_number,
                "stint_number": stint_number,
                "compound": stint.get("compound"),
                "tyre_age_at_start": stint.get("tyre_age_at_start"),
                "lap_start": stint.get("lap_start"),
                "lap_end": stint.get("lap_end"),
            }
        )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
