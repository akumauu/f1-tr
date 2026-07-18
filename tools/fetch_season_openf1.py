#!/usr/bin/env python3
"""Download a full F1 season from the OpenF1 public API into local raw JSON.

OpenF1 (https://api.openf1.org) covers 2023+ and is the single source here:
lap/sector times, speed traps, mini-sector segments, tyre stints, pit stops,
intervals, weather, race control, session results, and per-driver telemetry
(car_data + location). FastF1 is intentionally not used: its underlying F1
live-timing host is unreachable from this environment.

Layout:
  <out>/round_<NN>_<slug>/<SESSION>/<endpoint>.json
  <out>/round_<NN>_<slug>/<SESSION>/telemetry/car_data_<driver>.json
  <out>/manifest.json   (index of everything downloaded)

The script is idempotent: files that already exist and are non-empty are
skipped, so a killed run can simply be re-run to resume.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = "https://api.openf1.org/v1"

# Endpoints pulled once per session (small to medium size).
SESSION_ENDPOINTS = (
    "drivers",
    "laps",
    "stints",
    "pit",
    "intervals",
    "position",
    "weather",
    "race_control",
    "session_result",
)

# Telemetry endpoints are pulled per driver (large); races only by default.
TELEMETRY_ENDPOINTS = ("car_data", "location")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--out", default="data/raw/season-2025")
    parser.add_argument(
        "--sessions",
        default="Race,Qualifying,Sprint,Sprint Qualifying,Sprint Shootout",
        help="Comma-separated session_name values to download.",
    )
    parser.add_argument(
        "--telemetry-sessions",
        default="Race",
        help="Session names that also get per-driver car_data + location.",
    )
    parser.add_argument("--telemetry", action="store_true", help="Enable telemetry pull.")
    parser.add_argument("--only-round", type=int, help="Restrict to a single round number.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between requests.")
    args = parser.parse_args()

    client = OpenF1Client(BASE_URL, args.timeout, args.retries, args.sleep)
    out_dir = Path(args.out)
    wanted = split_csv(args.sessions)
    tele_wanted = set(split_csv(args.telemetry_sessions))

    meetings = client.get("meetings", year=args.year)
    meetings = [m for m in meetings if "Testing" not in (m.get("meeting_name") or "")]
    meetings.sort(key=lambda m: m.get("date_start") or "")
    print(f"{args.year}: {len(meetings)} race meetings")

    manifest: dict[str, Any] = {
        "generated_at": now_iso(),
        "year": args.year,
        "base_url": BASE_URL,
        "telemetry": bool(args.telemetry),
        "rounds": [],
    }

    for round_no, meeting in enumerate(meetings, start=1):
        if args.only_round and round_no != args.only_round:
            continue
        mk = to_int(meeting.get("meeting_key"))
        slug = slugify(meeting.get("meeting_name") or f"meeting-{mk}")
        round_dir = out_dir / f"round_{round_no:02d}_{slug}"
        print(f"\n[Round {round_no:02d}] {meeting.get('meeting_name')} (meeting_key={mk})")

        sessions = client.get("sessions", meeting_key=mk)
        sessions = [s for s in sessions if s.get("session_name") in wanted]
        round_entry: dict[str, Any] = {
            "round": round_no,
            "meeting_key": mk,
            "meeting_name": meeting.get("meeting_name"),
            "circuit": meeting.get("circuit_short_name"),
            "sessions": [],
        }

        for session in sorted(sessions, key=lambda s: s.get("date_start") or ""):
            sk = to_int(session.get("session_key"))
            name = session.get("session_name") or f"session-{sk}"
            session_dir = round_dir / slugify(name)
            counts = download_session(client, session_dir, sk)

            if args.telemetry and name in tele_wanted:
                drivers = read_json(session_dir / "drivers.json") or []
                nums = sorted({to_int(d.get("driver_number")) for d in drivers} - {None})
                counts["telemetry"] = download_telemetry(client, session_dir, sk, nums)

            round_entry["sessions"].append(
                {"session_key": sk, "session_name": name, "counts": counts}
            )
            print(f"  - {name:20s} " + ", ".join(f"{k}={v}" for k, v in counts.items()))

        manifest["rounds"].append(round_entry)
        write_json(out_dir / "manifest.json", manifest)

    print(f"\nDone. Manifest: {out_dir / 'manifest.json'}")
    return 0


def download_session(client: "OpenF1Client", session_dir: Path, sk: int | None) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    if sk is None:
        return counts
    for endpoint in SESSION_ENDPOINTS:
        path = session_dir / f"{endpoint}.json"
        if non_empty(path):
            counts[endpoint] = "cached"
            continue
        try:
            rows = client.get(endpoint, session_key=sk)
        except Exception as exc:  # noqa: BLE001 - record and continue
            counts[endpoint] = f"ERR:{type(exc).__name__}"
            continue
        write_json(path, rows)
        counts[endpoint] = len(rows)
    return counts


def download_telemetry(
    client: "OpenF1Client", session_dir: Path, sk: int, driver_numbers: list[int]
) -> dict[str, int]:
    tele_dir = session_dir / "telemetry"
    total = {"car_data": 0, "location": 0}
    for endpoint in TELEMETRY_ENDPOINTS:
        for num in driver_numbers:
            path = tele_dir / f"{endpoint}_{num}.json"
            if non_empty(path):
                continue
            try:
                rows = client.get(endpoint, session_key=sk, driver_number=num)
            except Exception:  # noqa: BLE001 - telemetry is large/flaky; keep going
                continue
            write_json(path, rows)
            total[endpoint] += len(rows)
    return total


class OpenF1Client:
    def __init__(self, base_url: str, timeout: int, retries: int, sleep: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, retries)
        self.sleep = sleep

    def get(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        last: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                if not isinstance(data, list):
                    raise RuntimeError(f"expected JSON array from {url}")
                if self.sleep:
                    time.sleep(self.sleep)
                return data
            except Exception as exc:  # noqa: BLE001 - retry with backoff
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"GET failed after {self.retries} tries: {url}: {last}")


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-") or "x"


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def non_empty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 2


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
