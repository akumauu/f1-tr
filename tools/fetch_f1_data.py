#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch OpenF1 endpoint data and FastF1 session data into local raw files."
    )
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--meeting-key", type=int)
    parser.add_argument("--session-key", type=int)
    parser.add_argument("--fastf1-event", default="Barcelona")
    parser.add_argument("--fastf1-session", default="R")
    parser.add_argument("--openf1-base-url", default=OPENF1_BASE_URL)
    parser.add_argument("--source", choices=("all", "openf1", "fastf1"), default="all")
    parser.add_argument("--openf1-endpoints", default=",".join(OPENF1_SESSION_ENDPOINTS))
    parser.add_argument("--out", default="data/raw/f1-api")
    parser.add_argument("--cache", default=".fastf1-cache")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--request-sleep", type=float, default=1.0)
    parser.add_argument("--strict-openf1", action="store_true")
    parser.add_argument("--fastf1-telemetry", action="store_true")
    parser.add_argument("--csv", action="store_true", help="Also write CSV files for flat records.")
    args = parser.parse_args()

    out_dir = Path(args.out)
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "year": args.year,
        "meeting_key": args.meeting_key,
        "session_key": args.session_key,
        "sources": {},
    }

    if args.source in ("all", "openf1"):
        client = OpenF1Client(args.openf1_base_url, args.timeout, args.retries)
        manifest["sources"]["openf1"] = fetch_openf1(
            client=client,
            out_dir=out_dir / "openf1",
            year=args.year,
            meeting_key=args.meeting_key,
            session_key=args.session_key,
            endpoints=split_csv(args.openf1_endpoints),
            write_csv=args.csv,
            request_sleep=args.request_sleep,
            strict=args.strict_openf1,
        )

    if args.source in ("all", "fastf1"):
        manifest["sources"]["fastf1"] = fetch_fastf1(
            out_dir=out_dir / "fastf1",
            year=args.year,
            event=args.fastf1_event,
            session_name=args.fastf1_session,
            cache_dir=args.cache,
            write_csv=args.csv,
            telemetry=args.fastf1_telemetry,
        )

    write_json(out_dir / "manifest.json", manifest)
    print(f"Wrote F1 API data manifest: {out_dir / 'manifest.json'}")
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
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"OpenF1 GET failed after {self.retries} attempts: {url}: {last_error}")


def fetch_openf1(
    client: OpenF1Client,
    out_dir: Path,
    year: int,
    meeting_key: int | None,
    session_key: int | None,
    endpoints: list[str],
    write_csv: bool,
    request_sleep: float,
    strict: bool,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"base_url": client.base_url, "files": {}, "record_counts": {}}

    meetings = client.get("meetings", year=year, meeting_key=meeting_key)
    summary["files"]["meetings"] = write_records(out_dir / "meetings.json", meetings, write_csv)
    summary["record_counts"]["meetings"] = len(meetings)

    if meeting_key is None and meetings:
        meeting_key = to_int(meetings[0].get("meeting_key"))

    sessions = client.get("sessions", meeting_key=meeting_key, session_key=session_key)
    summary["files"]["sessions"] = write_records(out_dir / "sessions.json", sessions, write_csv)
    summary["record_counts"]["sessions"] = len(sessions)

    if session_key is None and sessions:
        session_key = to_int(sessions[0].get("session_key"))

    if session_key is None:
        return summary

    session_dir = out_dir / f"sessions/{session_key}"
    for endpoint in endpoints:
        endpoint = endpoint.strip()
        if not endpoint:
            continue
        try:
            rows = client.get(endpoint, session_key=session_key)
        except Exception as exc:
            if strict:
                raise
            print(f"[WARN] OpenF1 {endpoint} session {session_key} unavailable: {exc}")
            rows = []
        file_key = endpoint.replace("/", "_")
        summary["files"][file_key] = write_records(session_dir / f"{file_key}.json", rows, write_csv)
        summary["record_counts"][file_key] = len(rows)
        if request_sleep > 0:
            time.sleep(request_sleep)
    return summary


def fetch_fastf1(
    out_dir: Path,
    year: int,
    event: str,
    session_name: str,
    cache_dir: str,
    write_csv: bool,
    telemetry: bool,
) -> dict[str, Any]:
    try:
        import fastf1
    except ImportError as exc:
        raise RuntimeError("FastF1 is not installed. Run: python -m pip install -r requirements.txt") from exc

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

    summary: dict[str, Any] = {
        "year": year,
        "event": event,
        "session": session_name,
        "files": {},
        "record_counts": {},
    }
    for name, rows in data.items():
        summary["files"][name] = write_records(out_dir / f"{name}.json", rows, write_csv)
        summary["record_counts"][name] = len(rows)
    return summary


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
    records: list[dict[str, Any]] = []
    for _, row in value.iterrows():
        records.append({str(key): json_safe(row.get(key)) for key in value.columns})
    return records


def safe_session_records(session: Any, attr: str) -> list[dict[str, Any]]:
    try:
        return dataframe_to_records(getattr(session, attr, None))
    except Exception as exc:
        print(f"[WARN] FastF1 {attr} unavailable: {exc}")
        return []


def write_records(path: Path, rows: list[dict[str, Any]], write_csv_file: bool) -> dict[str, Any]:
    write_json(path, rows)
    result = {"json": str(path), "records": len(rows)}
    if write_csv_file and rows:
        csv_path = path.with_suffix(".csv")
        write_csv(csv_path, rows)
        result["csv"] = str(csv_path)
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row.keys()})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_safe(row.get(key)) for key in fieldnames})


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


def csv_safe(value: Any) -> Any:
    value = json_safe(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
