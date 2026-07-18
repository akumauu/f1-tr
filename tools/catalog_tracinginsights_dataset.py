#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ingest_f1_to_postgres import load_database_url


REPOSITORY_URL = "https://github.com/TracingInsights-Archive/{year}.git"
TELEMETRY_REQUIRED_FIELDS = {
    "time",
    "speed",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "drs",
    "x",
    "y",
    "z",
}
TELEMETRY_NAME = re.compile(r"^(?P<lap>\d+)_tel\.json$")
SESSION_ALIASES = {"Sprint Shootout": "Sprint Qualifying"}
SESSION_METADATA = {
    "corners.json",
    "drivers.json",
    "rcm.json",
    "session_laptimes.json",
    "weather.json",
}


@dataclass(frozen=True)
class CatalogRow:
    year: int
    repository_url: str
    repository_commit: str
    relative_path: str
    category: str
    meeting_name: str | None
    session_name: str | None
    driver_acronym: str | None
    lap_number: int | None
    file_size_bytes: int
    sha256: str
    json_valid: bool | None
    sample_count: int | None
    telemetry_fields: list[str] | None
    telemetry_duration: float | None
    validation_status: str
    validation_error: str | None

    def as_tuple(self) -> tuple[Any, ...]:
        return (
            self.year,
            self.repository_url,
            self.repository_commit,
            self.relative_path,
            self.category,
            self.meeting_name,
            self.session_name,
            self.driver_acronym,
            self.lap_number,
            self.file_size_bytes,
            self.sha256,
            self.json_valid,
            self.sample_count,
            json.dumps(self.telemetry_fields, ensure_ascii=False)
            if self.telemetry_fields is not None
            else None,
            self.telemetry_duration,
            self.validation_status,
            self.validation_error,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="登记并校验完整 TracingInsights 数据包。")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--commit", help="归档快照必须显式提供来源 Git commit")
    parser.add_argument("--database-url", default=load_database_url())
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    import psycopg

    root = Path(args.root).resolve()
    if (root / ".git").exists():
        commit = args.commit or git_output(root, "rev-parse", "HEAD")
    elif args.commit:
        commit = args.commit
    else:
        raise SystemExit(f"非 Git 归档必须提供 --commit：{root}")
    repository_url = REPOSITORY_URL.format(year=args.year)
    files = sorted(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    started_at = datetime.now(timezone.utc)

    with psycopg.connect(args.database_url) as conn:
        conn.execute(
            """
            INSERT INTO tracing_dataset_imports
              (year,repository_url,repository_commit,root_path,started_at,status)
            VALUES (%s,%s,%s,%s,%s,'running')
            ON CONFLICT (year,repository_commit) DO UPDATE SET
              root_path=EXCLUDED.root_path,
              started_at=EXCLUDED.started_at,
              completed_at=NULL,
              status='running',
              total_files=0,
              telemetry_files=0,
              valid_telemetry_files=0,
              invalid_files=0,
              total_bytes=0,
              notes=NULL
            """,
            (args.year, repository_url, commit, str(root), started_at),
        )
        conn.execute("DELETE FROM tracing_dataset_files WHERE year=%s", (args.year,))
        conn.commit()

        totals = {"files": 0, "telemetry": 0, "valid_telemetry": 0, "invalid": 0, "bytes": 0}
        try:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                pending: list[CatalogRow] = []
                for row in pool.map(
                    lambda path: inspect_file(path, root, args.year, repository_url, commit),
                    files,
                ):
                    pending.append(row)
                    totals["files"] += 1
                    totals["bytes"] += row.file_size_bytes
                    totals["telemetry"] += row.category == "telemetry_lap"
                    totals["valid_telemetry"] += (
                        row.category == "telemetry_lap" and row.validation_status == "valid"
                    )
                    totals["invalid"] += row.validation_status == "invalid"
                    if len(pending) >= args.batch_size:
                        insert_rows(conn, pending)
                        pending.clear()
                        print(
                            f"year={args.year} indexed={totals['files']}/{len(files)} "
                            f"telemetry={totals['telemetry']} invalid={totals['invalid']}",
                            flush=True,
                        )
                if pending:
                    insert_rows(conn, pending)
                conn.execute(
                    """
                    UPDATE tracing_dataset_imports SET
                      completed_at=NOW(), status='completed', total_files=%s,
                      telemetry_files=%s, valid_telemetry_files=%s,
                      invalid_files=%s, total_bytes=%s
                    WHERE year=%s AND repository_commit=%s
                    """,
                    (
                        totals["files"],
                        totals["telemetry"],
                        totals["valid_telemetry"],
                        totals["invalid"],
                        totals["bytes"],
                        args.year,
                        commit,
                    ),
                )
                conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.execute(
                """
                UPDATE tracing_dataset_imports
                SET completed_at=NOW(), status='failed', notes=%s
                WHERE year=%s AND repository_commit=%s
                """,
                (f"{type(exc).__name__}: {exc}", args.year, commit),
            )
            conn.commit()
            raise

    print(json.dumps({"year": args.year, "commit": commit, **totals}, ensure_ascii=False))
    return 0


def git_output(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def inspect_file(
    path: Path,
    root: Path,
    year: int,
    repository_url: str,
    commit: str,
) -> CatalogRow:
    relative = path.relative_to(root).as_posix()
    category, meeting, session, driver, lap = classify_path(relative)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    json_valid: bool | None = None
    sample_count: int | None = None
    fields: list[str] | None = None
    duration: float | None = None
    status = "not_applicable"
    error: str | None = None
    if category in {"telemetry_lap", "driver_laptimes", "session_metadata"}:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            json_valid = True
            status = "valid"
            if category == "telemetry_lap":
                sample_count, fields, duration = validate_telemetry(payload)
            elif category == "driver_laptimes":
                validate_parallel_arrays(payload)
        except Exception as exc:
            json_valid = False
            status = "invalid"
            error = f"{type(exc).__name__}: {exc}"[:2000]

    return CatalogRow(
        year,
        repository_url,
        commit,
        relative,
        category,
        meeting,
        session,
        driver,
        lap,
        path.stat().st_size,
        digest.hexdigest(),
        json_valid,
        sample_count,
        fields,
        duration,
        status,
        error,
    )


def classify_path(relative_path: str) -> tuple[str, str | None, str | None, str | None, int | None]:
    parts = relative_path.split("/")
    if len(parts) >= 4:
        match = TELEMETRY_NAME.match(parts[3])
        meeting = parts[0]
        session = SESSION_ALIASES.get(parts[1], parts[1])
        driver = parts[2].upper()
        if match:
            return "telemetry_lap", meeting, session, driver, int(match.group("lap"))
        if parts[3] == "laptimes.json":
            return "driver_laptimes", meeting, session, driver, None
    if len(parts) == 3 and parts[2] in SESSION_METADATA:
        return (
            "session_metadata",
            parts[0],
            SESSION_ALIASES.get(parts[1], parts[1]),
            None,
            None,
        )
    if parts[0].lower().startswith("cache") or "cache" in parts[0].lower():
        return "cache", None, None, None, None
    return "repository_support", None, None, None, None


def validate_telemetry(payload: Any) -> tuple[int, list[str], float | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("tel"), dict):
        raise ValueError("缺少 tel 对象")
    telemetry = payload["tel"]
    missing = sorted(TELEMETRY_REQUIRED_FIELDS - telemetry.keys())
    if missing:
        raise ValueError(f"缺少字段：{','.join(missing)}")
    times = telemetry["time"]
    if not isinstance(times, list) or not times:
        raise ValueError("time 为空或不是数组")
    length = len(times)
    unequal = sorted(
        key
        for key, value in telemetry.items()
        if isinstance(value, list) and len(value) != length
    )
    if unequal:
        raise ValueError(f"数组长度不一致：{','.join(unequal)}")
    numeric_times = [float(value) for value in times if isinstance(value, (int, float))]
    if len(numeric_times) != length:
        raise ValueError("time 包含非数值")
    if any(current < previous for previous, current in zip(numeric_times, numeric_times[1:])):
        raise ValueError("time 非单调递增")
    duration = numeric_times[-1] - numeric_times[0] if len(numeric_times) > 1 else 0.0
    return length, sorted(telemetry), duration


def validate_parallel_arrays(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("圈时索引不是对象")
    lengths = {len(value) for value in payload.values() if isinstance(value, list)}
    if len(lengths) > 1:
        raise ValueError(f"圈时索引数组长度不一致：{sorted(lengths)}")


def insert_rows(conn: Any, rows: Iterable[CatalogRow]) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            """
        INSERT INTO tracing_dataset_files
          (year,repository_url,repository_commit,relative_path,category,
           meeting_name,session_name,driver_acronym,lap_number,file_size_bytes,
           sha256,json_valid,sample_count,telemetry_fields,telemetry_duration,
           validation_status,validation_error,indexed_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,NOW())
        ON CONFLICT (year,relative_path) DO UPDATE SET
          repository_url=EXCLUDED.repository_url,
          repository_commit=EXCLUDED.repository_commit,
          category=EXCLUDED.category,
          meeting_name=EXCLUDED.meeting_name,
          session_name=EXCLUDED.session_name,
          driver_acronym=EXCLUDED.driver_acronym,
          lap_number=EXCLUDED.lap_number,
          file_size_bytes=EXCLUDED.file_size_bytes,
          sha256=EXCLUDED.sha256,
          json_valid=EXCLUDED.json_valid,
          sample_count=EXCLUDED.sample_count,
          telemetry_fields=EXCLUDED.telemetry_fields,
          telemetry_duration=EXCLUDED.telemetry_duration,
          validation_status=EXCLUDED.validation_status,
          validation_error=EXCLUDED.validation_error,
          indexed_at=NOW()
            """,
            [row.as_tuple() for row in rows],
        )
    conn.commit()


if __name__ == "__main__":
    raise SystemExit(main())
