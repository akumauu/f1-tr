#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPENF1_BASE_URL = "https://api.openf1.org/v1"
DEFAULT_YEAR = 2026
DEFAULT_MEETING_KEY = 1287
DEFAULT_SESSION_KEY = 11307
DEFAULT_FASTF1_EVENT = "Barcelona"
DEFAULT_FASTF1_SESSION = "R"
DEFAULT_MEETING_START = "2026-06-12T11:30:00+00:00"
DEFAULT_SESSION_START = "2026-06-14T13:00:00+00:00"
DEFAULT_SESSION_END = "2026-06-14T15:00:00+00:00"
DATA_VERSION = "catalunya-2026-v1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the latest Barcelona-Catalunya race to the static JSON contract."
    )
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--meeting-key", type=int, default=DEFAULT_MEETING_KEY)
    parser.add_argument("--session-key", type=int, default=DEFAULT_SESSION_KEY)
    parser.add_argument("--fastf1-event", default=DEFAULT_FASTF1_EVENT)
    parser.add_argument("--fastf1-session", default=DEFAULT_FASTF1_SESSION)
    parser.add_argument("--openf1-base-url", default=OPENF1_BASE_URL)
    parser.add_argument("--out", default="public/data")
    parser.add_argument("--cache", default=".fastf1-cache")
    parser.add_argument("--openf1-only", action="store_true", help="Skip FastF1 and use OpenF1 lap/stint data.")
    parser.add_argument("--strict-fastf1", action="store_true", help="Fail if FastF1 cannot be loaded.")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-positions", type=int, default=2500)
    args = parser.parse_args()

    openf1 = OpenF1Client(args.openf1_base_url, args.timeout, args.retries)
    openf1_bundle = fetch_openf1_bundle(openf1, args.meeting_key, args.session_key, args.max_positions)

    fastf1_bundle: dict[str, Any] | None = None
    if not args.openf1_only:
        try:
            fastf1_bundle = fetch_fastf1_bundle(
                year=args.year,
                event=args.fastf1_event,
                session_name=args.fastf1_session,
                cache_dir=args.cache,
            )
        except Exception as exc:
            if args.strict_fastf1:
                raise
            print(f"[WARN] FastF1 unavailable, falling back to OpenF1 lap/stint data: {exc}", file=sys.stderr)

    bundle = build_export_bundle(
        year=args.year,
        meeting_key=args.meeting_key,
        session_key=args.session_key,
        openf1_bundle=openf1_bundle,
        fastf1_bundle=fastf1_bundle,
    )
    write_static_bundle(Path(args.out), bundle)
    print(
        f"Wrote Catalunya static data to {args.out} "
        f"({len(bundle['laps'])} laps, {len(bundle['radio'])} radio messages)"
    )
    return 0


class OpenF1Client:
    def __init__(self, base_url: str, timeout: int = 45, retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def get(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        clean_params = {k: v for k, v in params.items() if v is not None}
        query = urllib.parse.urlencode(clean_params)
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        last_error: Exception | None = None
        for attempt in range(max(1, self.retries)):
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"OpenF1 GET failed after {self.retries} attempts: {url}: {last_error}")


def fetch_openf1_bundle(
    client: OpenF1Client,
    meeting_key: int,
    session_key: int,
    max_positions: int,
) -> dict[str, list[dict[str, Any]]]:
    def optional(endpoint: str, **params: Any) -> list[dict[str, Any]]:
        try:
            return client.get(endpoint, **params)
        except Exception as exc:
            print(f"[WARN] OpenF1 {endpoint} failed: {exc}", file=sys.stderr)
            return []

    meetings = optional("meetings", meeting_key=meeting_key)
    sessions = optional("sessions", meeting_key=meeting_key)
    session = [item for item in sessions if int(item.get("session_key", 0)) == session_key]
    if not session:
        session = optional("sessions", session_key=session_key)

    positions = optional("position", session_key=session_key)
    if max_positions > 0 and len(positions) > max_positions:
        positions = sample_positions(positions, max_positions)
    drivers = optional("drivers", session_key=session_key)
    radio = optional("team_radio", session_key=session_key)
    pit_stops = optional("pit", session_key=session_key)
    race_control = optional("race_control", session_key=session_key)

    driver_numbers = collect_driver_numbers(drivers, radio, pit_stops, positions)
    laps = optional("laps", session_key=session_key)
    if not laps and driver_numbers:
        laps = fetch_by_driver(client, "laps", session_key, driver_numbers)
    elif driver_numbers:
        laps = fetch_by_driver(client, "laps", session_key, driver_numbers, existing_rows=laps)
    stints = optional("stints", session_key=session_key)
    if not stints and driver_numbers:
        stints = fetch_by_driver(client, "stints", session_key, driver_numbers)
    elif driver_numbers:
        stints = fetch_by_driver(client, "stints", session_key, driver_numbers, existing_rows=stints)

    return {
        "meetings": meetings,
        "sessions": session,
        "drivers": drivers,
        "laps": laps,
        "stints": stints,
        "radio": radio,
        "pit_stops": pit_stops,
        "positions": positions,
        "race_control": race_control,
    }


def fetch_by_driver(
    client: OpenF1Client,
    endpoint: str,
    session_key: int,
    driver_numbers: list[int],
    existing_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = list(existing_rows or [])
    existing_driver_numbers = set(collect_driver_numbers(rows))
    for driver_number in driver_numbers:
        if driver_number in existing_driver_numbers:
            continue
        try:
            rows.extend(client.get(endpoint, session_key=session_key, driver_number=driver_number))
        except Exception as exc:
            print(f"[WARN] OpenF1 {endpoint} driver {driver_number} failed: {exc}", file=sys.stderr)
    return rows


def collect_driver_numbers(*row_sets: list[dict[str, Any]]) -> list[int]:
    numbers = set()
    for rows in row_sets:
        for row in rows:
            driver_number = to_int(row.get("driver_number"))
            if driver_number is not None:
                numbers.add(driver_number)
    return sorted(numbers)


def fetch_fastf1_bundle(year: int, event: str, session_name: str, cache_dir: str) -> dict[str, Any]:
    try:
        import fastf1
    except ImportError as exc:
        raise RuntimeError("install Python dependencies with `python3 -m pip install -r requirements.txt`") from exc

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)
    session = resolve_fastf1_session(fastf1, year, event, session_name)

    try:
        session.load(laps=True, telemetry=False, weather=False, messages=False)
    except TypeError:
        session.load(telemetry=False, weather=False, messages=False)

    drivers = drivers_from_fastf1(session)
    laps = laps_from_fastf1(session, drivers)
    stints = stints_from_fastf1_laps(laps)
    return {
        "drivers": drivers,
        "laps": laps,
        "stints": stints,
        "source_event": event,
        "source_session": session_name,
    }


def resolve_fastf1_session(fastf1: Any, year: int, event: str, session_name: str) -> Any:
    try:
        return fastf1.get_session(year, event, session_name)
    except Exception:
        schedule = fastf1.get_event_schedule(year)
        event_lower = event.lower()
        for _, row in schedule.iterrows():
            haystack = " ".join(
                str(row.get(column, ""))
                for column in ("EventName", "OfficialEventName", "Location", "Country", "CircuitName")
            ).lower()
            if event_lower in haystack or "catalunya" in haystack or "barcelona" in haystack:
                return fastf1.get_session(year, int(row["RoundNumber"]), session_name)
        raise


def drivers_from_fastf1(session: Any) -> list[dict[str, Any]]:
    results = getattr(session, "results", None)
    if results is None or len(results) == 0:
        return []

    drivers: list[dict[str, Any]] = []
    for _, row in results.iterrows():
        driver_number = to_int(row.get("DriverNumber"))
        if driver_number is None:
            continue
        drivers.append(
            {
                "session_key": None,
                "driver_number": driver_number,
                "full_name": clean_str(row.get("FullName") or row.get("BroadcastName")),
                "name_acronym": clean_str(row.get("Abbreviation")),
                "team_name": clean_str(row.get("TeamName")),
                "team_colour": normalize_color(row.get("TeamColor")),
                "country_code": clean_str(row.get("CountryCode")),
            }
        )
    return drivers


def laps_from_fastf1(session: Any, drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    laps_df = getattr(session, "laps", None)
    if laps_df is None or len(laps_df) == 0:
        return []

    acronym_to_number = {
        driver.get("name_acronym"): driver.get("driver_number")
        for driver in drivers
        if driver.get("name_acronym")
    }
    laps: list[dict[str, Any]] = []
    for _, row in laps_df.iterrows():
        driver_number = to_int(row.get("DriverNumber")) or acronym_to_number.get(clean_str(row.get("Driver")))
        lap_number = to_int(row.get("LapNumber"))
        if driver_number is None or lap_number is None:
            continue

        laps.append(
            {
                "session_key": None,
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


def stints_from_fastf1_laps(laps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for lap in laps:
        stint_number = to_int(lap.get("_stint_number"))
        if stint_number is None:
            continue
        grouped[(int(lap["driver_number"]), stint_number)].append(lap)

    stints: list[dict[str, Any]] = []
    for (driver_number, stint_number), rows in sorted(grouped.items()):
        lap_numbers = [int(row["lap_number"]) for row in rows if to_int(row.get("lap_number")) is not None]
        tyre_life = [int(row["_tyre_life"]) for row in rows if to_int(row.get("_tyre_life")) is not None]
        compounds = [row.get("_compound") for row in rows if row.get("_compound")]
        if not lap_numbers:
            continue
        stints.append(
            {
                "session_key": None,
                "driver_number": driver_number,
                "stint_number": stint_number,
                "compound": most_common(compounds),
                "tyre_age_at_start": min(tyre_life) if tyre_life else None,
                "lap_start": min(lap_numbers),
                "lap_end": max(lap_numbers),
            }
        )
    return stints


def build_export_bundle(
    year: int,
    meeting_key: int,
    session_key: int,
    openf1_bundle: dict[str, list[dict[str, Any]]],
    fastf1_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    meeting = normalize_meeting(first(openf1_bundle.get("meetings")), year, meeting_key)
    session = normalize_session(first(openf1_bundle.get("sessions")), meeting_key, session_key)

    openf1_drivers = [normalize_driver(row, session_key) for row in openf1_bundle.get("drivers", [])]
    if not openf1_drivers:
        openf1_drivers = fallback_drivers(openf1_bundle, session_key)
    fastf1_drivers = []
    if fastf1_bundle:
        fastf1_drivers = [with_session_key(row, session_key) for row in fastf1_bundle.get("drivers", [])]
    drivers = merge_drivers(openf1_drivers, fastf1_drivers)

    if fastf1_bundle and fastf1_bundle.get("laps"):
        lap_source = "fastf1"
        laps = [strip_private(with_session_key(row, session_key)) for row in fastf1_bundle["laps"]]
        stints = [with_session_key(row, session_key) for row in fastf1_bundle.get("stints", [])]
    else:
        lap_source = "openf1"
        laps = [normalize_lap(row) for row in openf1_bundle.get("laps", [])]
        stints = [normalize_stint(row) for row in openf1_bundle.get("stints", [])]

    laps = sorted(laps, key=lambda row: (int(row.get("driver_number", 0)), int(row.get("lap_number", 0))))
    stints = sorted(stints, key=lambda row: (int(row.get("driver_number", 0)), int(row.get("stint_number", 0))))
    lap_deltas = compute_lap_deltas(laps, stints, drivers)
    degradation = compute_degradation(laps, stints, drivers)
    radio = normalize_radio(openf1_bundle.get("radio", []), laps, drivers)

    files = session_files(session_key)
    record_counts = {
        "summary": 1,
        "drivers": len(drivers),
        "laps": len(laps),
        "lap_deltas": len(lap_deltas),
        "stints": len(stints),
        "degradation": len(degradation),
        "radio": len(radio),
        "positions": len(openf1_bundle.get("positions", [])),
        "pit_stops": len(openf1_bundle.get("pit_stops", [])),
        "race_control": len(openf1_bundle.get("race_control", [])),
    }
    for key, count in record_counts.items():
        if key in files:
            files[key]["records"] = count
    summary = {
        "meeting": meeting,
        "session": session,
        "record_counts": record_counts,
        "files": files,
        "data_sources": {
            "meeting": "openf1",
            "session": "openf1",
            "drivers": "openf1+fastf1" if fastf1_drivers else "openf1",
            "laps": lap_source,
            "stints": lap_source,
            "team_radio": "openf1",
            "pit_stops": "openf1",
            "positions": "openf1",
            "race_control": "openf1",
        },
    }

    return {
        "meeting": meeting,
        "session": session,
        "drivers": drivers,
        "laps": laps,
        "lap_deltas": lap_deltas,
        "stints": stints,
        "degradation": degradation,
        "radio": radio,
        "positions": [normalize_position(row) for row in openf1_bundle.get("positions", [])],
        "pit_stops": [normalize_pit_stop(row) for row in openf1_bundle.get("pit_stops", [])],
        "race_control": [normalize_race_control(row) for row in openf1_bundle.get("race_control", [])],
        "summary": summary,
        "manifest": build_manifest(DATA_VERSION, meeting, session, files, record_counts),
    }


def normalize_meeting(row: dict[str, Any] | None, year: int, meeting_key: int) -> dict[str, Any]:
    row = row or {}
    return {
        "meeting_key": to_int(row.get("meeting_key")) or meeting_key,
        "meeting_name": row.get("meeting_name") or "Barcelona Grand Prix",
        "location": row.get("location") or "Barcelona",
        "country_name": row.get("country_name") or "Spain",
        "circuit_name": row.get("circuit_short_name") or row.get("circuit_name") or "Catalunya",
        "year": to_int(row.get("year")) or year,
        "date_start": row.get("date_start") or DEFAULT_MEETING_START,
    }


def normalize_session(row: dict[str, Any] | None, meeting_key: int, session_key: int) -> dict[str, Any]:
    row = row or {}
    return {
        "session_key": to_int(row.get("session_key")) or session_key,
        "meeting_key": to_int(row.get("meeting_key")) or meeting_key,
        "session_name": row.get("session_name") or "Race",
        "session_type": row.get("session_type") or "Race",
        "date_start": row.get("date_start") or DEFAULT_SESSION_START,
        "date_end": row.get("date_end") or DEFAULT_SESSION_END,
    }


def normalize_driver(row: dict[str, Any], session_key: int) -> dict[str, Any]:
    return {
        "session_key": session_key,
        "driver_number": to_int(row.get("driver_number")),
        "full_name": row.get("full_name") or row.get("broadcast_name") or "",
        "name_acronym": row.get("name_acronym") or "",
        "team_name": row.get("team_name") or "",
        "team_colour": normalize_color(row.get("team_colour")),
        "headshot_url": row.get("headshot_url") or "",
        "country_code": row.get("country_code") or "",
    }


def fallback_drivers(openf1_bundle: dict[str, list[dict[str, Any]]], session_key: int) -> list[dict[str, Any]]:
    acronyms: dict[int, str] = {}
    for row in openf1_bundle.get("radio", []):
        driver_number = to_int(row.get("driver_number"))
        if driver_number is None:
            continue
        acronym = acronym_from_recording_url(row.get("recording_url"))
        if acronym:
            acronyms[driver_number] = acronym

    numbers = collect_driver_numbers(
        openf1_bundle.get("radio", []),
        openf1_bundle.get("pit_stops", []),
        openf1_bundle.get("positions", []),
        openf1_bundle.get("laps", []),
        openf1_bundle.get("stints", []),
    )
    return [
        {
            "session_key": session_key,
            "driver_number": driver_number,
            "full_name": "",
            "name_acronym": acronyms.get(driver_number, str(driver_number)),
            "team_name": "",
            "team_colour": "",
            "headshot_url": "",
            "country_code": "",
        }
        for driver_number in numbers
    ]


def acronym_from_recording_url(value: Any) -> str:
    match = re.search(r"/([A-Z]{3})_(\d+)_", clean_str(value))
    return match.group(1) if match else ""


def normalize_lap(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": to_int(row.get("session_key")),
        "driver_number": to_int(row.get("driver_number")),
        "lap_number": to_int(row.get("lap_number")),
        "lap_duration": number_or_none(row.get("lap_duration")),
        "duration_sector_1": number_or_none(row.get("duration_sector_1")),
        "duration_sector_2": number_or_none(row.get("duration_sector_2")),
        "duration_sector_3": number_or_none(row.get("duration_sector_3")),
        "i1_speed": to_int(row.get("i1_speed")),
        "i2_speed": to_int(row.get("i2_speed")),
        "st_speed": to_int(row.get("st_speed")),
        "segments_sector_1": row.get("segments_sector_1") or [],
        "segments_sector_2": row.get("segments_sector_2") or [],
        "segments_sector_3": row.get("segments_sector_3") or [],
        "is_pit_out_lap": bool(row.get("is_pit_out_lap")),
        "date_start": row.get("date_start"),
    }


def normalize_stint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": to_int(row.get("session_key")),
        "driver_number": to_int(row.get("driver_number")),
        "stint_number": to_int(row.get("stint_number")),
        "compound": row.get("compound") or "",
        "tyre_age_at_start": to_int(row.get("tyre_age_at_start")),
        "lap_start": to_int(row.get("lap_start")),
        "lap_end": to_int(row.get("lap_end")),
    }


def normalize_radio(
    rows: list[dict[str, Any]],
    laps: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    driver_lookup = {driver.get("driver_number"): driver for driver in drivers}
    out = []
    for index, row in enumerate(sorted(rows, key=lambda item: item.get("date") or "")):
        driver_number = to_int(row.get("driver_number"))
        driver = driver_lookup.get(driver_number, {})
        out.append(
            {
                "id": index + 1,
                "session_key": to_int(row.get("session_key")),
                "driver_number": driver_number,
                "name_acronym": driver.get("name_acronym") or "",
                "team_name": driver.get("team_name") or "",
                "radio_time": row.get("date"),
                "recording_url": row.get("recording_url") or "",
                "transcript_en": "",
                "transcript_zh": "",
                "intent": "audio",
                "sentiment": "",
                "key_entities": {},
                "translation_status": "audio_only",
                "approx_lap_number": approximate_lap_number(laps, driver_number, row.get("date")),
            }
        )
    return out


def normalize_position(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": to_int(row.get("session_key")),
        "driver_number": to_int(row.get("driver_number")),
        "position": to_int(row.get("position")),
        "date": row.get("date"),
    }


def normalize_pit_stop(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": to_int(row.get("session_key")),
        "driver_number": to_int(row.get("driver_number")),
        "lap_number": to_int(row.get("lap_number")),
        "pit_duration": number_or_none(row.get("pit_duration")),
        "date": row.get("date"),
    }


def normalize_race_control(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_key": to_int(row.get("session_key")),
        "date": row.get("date"),
        "category": row.get("category") or "",
        "flag": row.get("flag") or "",
        "message": row.get("message") or "",
        "driver_number": to_int(row.get("driver_number")),
        "lap_number": to_int(row.get("lap_number")),
    }


def compute_lap_deltas(
    laps: list[dict[str, Any]],
    stints: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    driver_lookup = {driver.get("driver_number"): driver for driver in drivers}
    stint_lookup = build_stint_lookup(stints)
    out: list[dict[str, Any]] = []
    by_driver: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for lap in laps:
        if not valid_duration(lap.get("lap_duration")):
            continue
        by_driver[int(lap["driver_number"])].append(lap)

    for driver_number, rows in sorted(by_driver.items()):
        previous: float | None = None
        stint_start: dict[int, float] = {}
        for lap in sorted(rows, key=lambda item: int(item["lap_number"])):
            lap_number = int(lap["lap_number"])
            duration = float(lap["lap_duration"])
            stint = stint_for_lap(stint_lookup, driver_number, lap_number)
            stint_number = to_int(stint.get("stint_number")) if stint else None
            if stint_number is not None and stint_number not in stint_start:
                stint_start[stint_number] = duration
            driver = driver_lookup.get(driver_number, {})
            out.append(
                {
                    "session_key": lap.get("session_key"),
                    "driver_number": driver_number,
                    "name_acronym": driver.get("name_acronym") or "",
                    "team_name": driver.get("team_name") or "",
                    "lap_number": lap_number,
                    "lap_duration": duration,
                    "compound": stint.get("compound", "") if stint else "",
                    "stint_number": stint_number,
                    "delta_to_prev": round(duration - previous, 3) if previous is not None else None,
                    "delta_to_stint_start": round(duration - stint_start[stint_number], 3)
                    if stint_number is not None
                    else None,
                }
            )
            previous = duration
    return out


def compute_degradation(
    laps: list[dict[str, Any]],
    stints: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    driver_lookup = {driver.get("driver_number"): driver for driver in drivers}
    out = []
    for stint in stints:
        driver_number = to_int(stint.get("driver_number"))
        lap_start = to_int(stint.get("lap_start"))
        lap_end = to_int(stint.get("lap_end"))
        stint_number = to_int(stint.get("stint_number"))
        if driver_number is None or lap_start is None or lap_end is None or stint_number is None:
            continue
        points = [
            (int(lap["lap_number"]), float(lap["lap_duration"]))
            for lap in laps
            if to_int(lap.get("driver_number")) == driver_number
            and valid_duration(lap.get("lap_duration"))
            and not lap.get("is_pit_out_lap", False)
            and lap_start <= int(lap.get("lap_number", 0)) <= lap_end
        ]
        if len(points) < 2:
            continue
        xs, ys = zip(*points)
        slope, intercept, r_squared = linear_regression(xs, ys)
        driver = driver_lookup.get(driver_number, {})
        out.append(
            {
                "session_key": stint.get("session_key"),
                "driver_number": driver_number,
                "name_acronym": driver.get("name_acronym") or "",
                "team_name": driver.get("team_name") or "",
                "stint_number": stint_number,
                "compound": stint.get("compound") or "",
                "lap_start": lap_start,
                "lap_end": lap_end,
                "stint_length": lap_end - lap_start + 1,
                "deg_rate_sec_per_lap": round(slope, 6),
                "base_pace": round(intercept, 6),
                "r_squared": round(r_squared, 6),
                "avg_lap_time": round(statistics.mean(ys), 6),
                "best_lap_time": min(ys),
            }
        )
    return out


def build_manifest(
    version: str,
    meeting: dict[str, Any],
    session: dict[str, Any],
    files: dict[str, dict[str, Any]],
    record_counts: dict[str, int],
) -> dict[str, Any]:
    year = int(meeting["year"])
    meeting_key = int(meeting["meeting_key"])
    session_key = int(session["session_key"])
    return {
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "years": [
            {
                "year": year,
                "meetings_path": f"seasons/{year}/meetings.json",
                "meeting_keys": [meeting_key],
            }
        ],
        "meetings": [
            {
                **meeting,
                "sessions_path": f"meetings/{meeting_key}/sessions.json",
                "session_keys": [session_key],
            }
        ],
        "sessions": [
            {
                **session,
                "files": files,
                "record_counts": record_counts,
            }
        ],
        "default_session_key": session_key,
    }


def session_files(session_key: int) -> dict[str, dict[str, Any]]:
    names = {
        "summary": "summary.json",
        "drivers": "drivers.json",
        "laps": "laps.json",
        "lap_deltas": "lap-deltas.json",
        "stints": "stints.json",
        "degradation": "degradation.json",
        "radio": "radio.json",
        "positions": "positions.json",
        "pit_stops": "pit-stops.json",
        "race_control": "race-control.json",
    }
    return {key: {"path": f"sessions/{session_key}/{filename}", "records": 0} for key, filename in names.items()}


def write_static_bundle(out_dir: Path, bundle: dict[str, Any]) -> None:
    session_key = int(bundle["session"]["session_key"])
    meeting_key = int(bundle["meeting"]["meeting_key"])
    year = int(bundle["meeting"]["year"])

    write_json(out_dir / "manifest.json", bundle["manifest"])
    write_json(out_dir / "seasons" / str(year) / "meetings.json", [bundle["meeting"]])
    write_json(out_dir / "meetings" / str(meeting_key) / "sessions.json", [bundle["session"]])

    session_dir = out_dir / "sessions" / str(session_key)
    for key, filename in {
        "summary": "summary.json",
        "drivers": "drivers.json",
        "laps": "laps.json",
        "lap_deltas": "lap-deltas.json",
        "stints": "stints.json",
        "degradation": "degradation.json",
        "radio": "radio.json",
        "positions": "positions.json",
        "pit_stops": "pit-stops.json",
        "race_control": "race-control.json",
    }.items():
        write_json(session_dir / filename, bundle[key])


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sample_positions(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    by_driver: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        driver_number = to_int(row.get("driver_number"))
        if driver_number is not None:
            by_driver[driver_number].append(row)
    sampled = []
    per_driver = max(1, limit // max(1, len(by_driver)))
    for driver_rows in by_driver.values():
        step = max(1, math.ceil(len(driver_rows) / per_driver))
        sampled.extend(driver_rows[::step])
    return sorted(sampled[:limit], key=lambda row: row.get("date") or "")


def merge_drivers(openf1_drivers: list[dict[str, Any]], fastf1_drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for driver in openf1_drivers + fastf1_drivers:
        driver_number = to_int(driver.get("driver_number"))
        if driver_number is None:
            continue
        current = merged.get(driver_number, {})
        merged[driver_number] = {**driver, **{k: v for k, v in current.items() if v not in (None, "")}}
    return [merged[key] for key in sorted(merged)]


def build_stint_lookup(stints: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    lookup: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for stint in stints:
        driver_number = to_int(stint.get("driver_number"))
        if driver_number is not None:
            lookup[driver_number].append(stint)
    return lookup


def stint_for_lap(lookup: dict[int, list[dict[str, Any]]], driver_number: int, lap_number: int) -> dict[str, Any]:
    for stint in lookup.get(driver_number, []):
        lap_start = to_int(stint.get("lap_start"))
        lap_end = to_int(stint.get("lap_end"))
        if lap_start is not None and lap_end is not None and lap_start <= lap_number <= lap_end:
            return stint
    return {}


def approximate_lap_number(laps: list[dict[str, Any]], driver_number: int | None, radio_time: str | None) -> int | None:
    if driver_number is None or not radio_time:
        return None
    target = parse_datetime(radio_time)
    if target is None:
        return None
    candidates = []
    for lap in laps:
        if to_int(lap.get("driver_number")) != driver_number:
            continue
        lap_start = parse_datetime(lap.get("date_start"))
        lap_number = to_int(lap.get("lap_number"))
        if lap_start is not None and lap_number is not None and lap_start <= target:
            candidates.append((lap_start, lap_number))
    if not candidates:
        return None
    return sorted(candidates)[-1][1]


def linear_regression(xs: tuple[int, ...], ys: tuple[float, ...]) -> tuple[float, float, float]:
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = numerator / denominator if denominator else 0.0
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - ss_res / ss_tot if ss_tot else 1.0
    return slope, intercept, r_squared


def with_session_key(row: dict[str, Any], session_key: int) -> dict[str, Any]:
    out = dict(row)
    out["session_key"] = session_key
    return out


def strip_private(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def first(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return rows[0] if rows else None


def most_common(values: list[str]) -> str:
    if not values:
        return ""
    return max(set(values), key=values.count)


def normalize_color(value: Any) -> str:
    text = clean_str(value).lstrip("#")
    return text if len(text) == 6 else ""


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


def valid_duration(value: Any) -> bool:
    number = number_or_none(value)
    return number is not None and number > 0


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
        text = value.isoformat()
    else:
        text = str(value)
    return text.replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if is_missing(value):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value != value:
            return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    raise SystemExit(main())
