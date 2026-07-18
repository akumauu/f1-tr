#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any

from ingest_f1_to_postgres import load_database_url, number_or_none, to_int


DEFAULT_SESSIONS = (
    "Practice 1",
    "Practice 2",
    "Practice 3",
    "Qualifying",
    "Sprint Qualifying",
    "Sprint",
    "Race",
)
SESSION_ALIASES = {"Sprint Shootout": "Sprint Qualifying"}


def main() -> int:
    parser = argparse.ArgumentParser(description="将 TracingInsights 各赛段逐圈遥测导入 PostgreSQL。")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument(
        "--sessions",
        default=",".join(DEFAULT_SESSIONS),
        help="逗号分隔的目标赛段；默认导入练习、排位、冲刺和正赛。",
    )
    parser.add_argument("--missing-only", action="store_true", help="仅补导缺少 laps.date_start 的边界圈。")
    parser.add_argument(
        "--allow-deprecated-postgres-telemetry",
        action="store_true",
        help="显式允许把已由 v4 Parquet 替代的逐点遥测重新写入 PostgreSQL。",
    )
    args = parser.parse_args()
    if not args.allow_deprecated_postgres_telemetry:
        parser.error(
            "TracingInsights 逐点 PG 入库已废弃；规范源是 v4 Parquet。"
            "确需复现旧流程时显式传入 --allow-deprecated-postgres-telemetry。"
        )

    import psycopg

    root = Path(args.root)
    requested_sessions = {normalize_session(value.strip()) for value in args.sessions.split(",") if value.strip()}
    with psycopg.connect(args.database_url) as conn:
        targets = load_targets(conn, args.year)
        create_stage(conn)
        total = 0
        skipped = 0
        batch: list[tuple[Any, ...]] = []
        for meeting_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name != ".git"):
            meeting_name = normalize_name(meeting_dir.name)
            for session_dir in sorted(path for path in meeting_dir.iterdir() if path.is_dir()):
                session_name = normalize_session(session_dir.name)
                if session_name not in requested_sessions:
                    continue
                session_key = targets.get((meeting_name, session_name))
                if session_key is None:
                    skipped += sum(1 for _ in session_dir.glob("*/*_tel.json"))
                    print(
                        f"Skipping unmatched session: {args.year} / {meeting_dir.name} / {session_dir.name}",
                        flush=True,
                    )
                    continue
                driver_map = load_driver_map(conn, session_key)
                lap_starts = load_lap_starts(conn, session_key)
                print(
                    f"Importing {args.year} / {meeting_dir.name} / {session_name} / session={session_key}",
                    flush=True,
                )
                for driver_dir in sorted(path for path in session_dir.iterdir() if path.is_dir()):
                    driver_number = driver_map.get(driver_dir.name.upper())
                    if driver_number is None:
                        skipped += sum(1 for _ in driver_dir.glob("*_tel.json"))
                        continue
                    for path in sorted(driver_dir.glob("*_tel.json"), key=lap_number):
                        lap = lap_number(path)
                        start = lap_starts.get((driver_number, lap))
                        if args.missing_only and start is not None:
                            continue
                        if start is None:
                            start = derive_lap_start(path, driver_number, lap, lap_starts)
                            if start is None:
                                skipped += 1
                                continue
                        for row in file_rows(path, session_key, driver_number, start):
                            batch.append(row)
                            if len(batch) >= args.batch_size:
                                total += flush_batch(conn, batch)
                                batch.clear()
                                print(f"  imported={total}", flush=True)
        if batch:
            total += flush_batch(conn, batch)
        conn.commit()
    print(f"TracingInsights import complete: year={args.year} samples={total} skipped_files={skipped}")
    return 0


def load_targets(conn: Any, year: int) -> dict[tuple[str, str], int]:
    rows = conn.execute(
        """
        SELECT m.meeting_name, s.session_name, s.session_key
        FROM meetings m JOIN sessions s USING(meeting_key)
        WHERE m.year=%s
        """,
        (year,),
    ).fetchall()
    return {
        (normalize_name(meeting_name), normalize_session(session_name)): int(session_key)
        for meeting_name, session_name, session_key in rows
    }


def normalize_session(value: str) -> str:
    return SESSION_ALIASES.get(value, value)


def load_driver_map(conn: Any, session_key: int) -> dict[str, int]:
    return {
        str(acronym).upper(): int(number)
        for acronym, number in conn.execute(
            "SELECT name_acronym, driver_number FROM drivers WHERE session_key=%s", (session_key,)
        ).fetchall()
        if acronym
    }


def load_lap_starts(conn: Any, session_key: int) -> dict[tuple[int, int], Any]:
    return {
        (int(driver), int(lap)): start
        for driver, lap, start in conn.execute(
            "SELECT driver_number,lap_number,date_start FROM laps WHERE session_key=%s AND date_start IS NOT NULL",
            (session_key,),
        ).fetchall()
    }


def file_rows(path: Path, session_key: int, driver_number: int, start: Any):
    payload = json.loads(path.read_text(encoding="utf-8")).get("tel", {})
    times = payload.get("time") or []
    columns = ("speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y", "z")
    for index, value in enumerate(times):
        seconds = number_or_none(value)
        if seconds is None:
            continue
        values = {key: at(payload.get(key), index) for key in columns}
        raw = {"time": seconds, **values, "source_file": str(path)}
        yield (
            session_key,
            driver_number,
            start + timedelta(seconds=seconds),
            real_or_none(values["speed"]),
            real_or_none(values["throttle"]),
            real_or_none(values["brake"]),
            to_int(values["gear"]),
            to_int(values["rpm"]),
            to_int(values["drs"]),
            real_or_none(values["x"]),
            real_or_none(values["y"]),
            real_or_none(values["z"]),
            json.dumps(raw, ensure_ascii=False),
        )


def create_stage(conn: Any) -> None:
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tracing_telemetry_stage (
          session_key integer, driver_number smallint, date timestamptz,
          speed real, throttle real, brake real, n_gear smallint, rpm integer, drs smallint,
          x real, y real, z real, raw_payload jsonb
        ) ON COMMIT PRESERVE ROWS
        """
    )


def flush_batch(conn: Any, rows: list[tuple[Any, ...]]) -> int:
    conn.execute("TRUNCATE tracing_telemetry_stage")
    with conn.cursor().copy(
        "COPY tracing_telemetry_stage (session_key,driver_number,date,speed,throttle,brake,n_gear,rpm,drs,x,y,z,raw_payload) FROM STDIN"
    ) as copy:
        for row in rows:
            copy.write_row(row)
    conn.execute(
        """
        INSERT INTO telemetry_car_data
          (session_key,driver_number,date,source_provider,speed,throttle,brake,n_gear,rpm,drs,raw_payload,fetched_at)
        SELECT session_key,driver_number,date,'tracinginsights',speed,throttle,brake,n_gear,rpm,drs,raw_payload,NOW()
        FROM tracing_telemetry_stage ON CONFLICT DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO telemetry_location
          (session_key,driver_number,date,source_provider,x,y,z,raw_payload,fetched_at)
        SELECT session_key,driver_number,date,'tracinginsights',x,y,z,raw_payload,NOW()
        FROM tracing_telemetry_stage ON CONFLICT DO NOTHING
        """
    )
    conn.commit()
    return len(rows)


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip().replace(" grand prix", "")


def lap_number(path: Path) -> int:
    try:
        return int(path.name.split("_", 1)[0])
    except ValueError:
        return 10**9


def at(value: Any, index: int) -> Any:
    return value[index] if isinstance(value, list) and index < len(value) else None


def derive_lap_start(path: Path, driver_number: int, lap: int, lap_starts: dict[tuple[int, int], Any]):
    """用相邻圈锚点和遥测持续时间补齐首圈/末圈绝对时间。"""
    duration = telemetry_duration(path)
    next_start = lap_starts.get((driver_number, lap + 1))
    if next_start is not None and duration is not None:
        return next_start - timedelta(seconds=duration)
    previous_start = lap_starts.get((driver_number, lap - 1))
    previous_path = path.with_name(f"{lap - 1}_tel.json")
    previous_duration = telemetry_duration(previous_path) if previous_start is not None else None
    if previous_start is not None and previous_duration is not None:
        return previous_start + timedelta(seconds=previous_duration)
    return None


def telemetry_duration(path: Path) -> float | None:
    if not path.exists():
        return None
    times = json.loads(path.read_text(encoding="utf-8")).get("tel", {}).get("time") or []
    values = [number_or_none(value) for value in times]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def real_or_none(value: Any) -> float | None:
    """适配 PostgreSQL REAL；传感器插值产生的极小值按数值零处理。"""
    number = number_or_none(value)
    if number is not None and 0 < abs(number) < 1.17549435e-38:
        return 0.0
    return number


if __name__ == "__main__":
    raise SystemExit(main())
