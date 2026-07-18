#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA_VERSION = "tracinginsights-expanded-v4"
SESSION_ALIASES = {"Sprint Shootout": "Sprint Qualifying"}
KNOWN_TELEMETRY_FIELDS = {
    "time",
    "rpm",
    "speed",
    "gear",
    "throttle",
    "brake",
    "drs",
    "distance",
    "rel_distance",
    "DriverAhead",
    "DistanceToDriverAhead",
    "acc_x",
    "acc_y",
    "acc_z",
    "x",
    "y",
    "z",
    "dataKey",
}
REQUIRED_TELEMETRY_FIELDS = {
    "time", "speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y", "z"
}
FLOAT_FIELDS = (
    "time",
    "rpm",
    "speed",
    "throttle",
    "distance",
    "rel_distance",
    "DistanceToDriverAhead",
    "acc_x",
    "acc_y",
    "acc_z",
    "x",
    "y",
    "z",
)


PARQUET_SCHEMA = pa.schema(
    [
        ("year", pa.int16()),
        ("meeting", pa.string()),
        ("session", pa.string()),
        ("driver_acronym", pa.string()),
        ("driver_number", pa.int16()),
        ("lap", pa.int16()),
        ("sample_index", pa.int32()),
        ("time", pa.float32()),
        ("distance", pa.float32()),
        ("rel_distance", pa.float32()),
        ("speed", pa.float32()),
        ("throttle", pa.float32()),
        ("brake", pa.int16()),
        # 少数上游圈以 128 表示异常/缺失挡位，必须原值保留。
        ("gear", pa.int16()),
        ("rpm", pa.float32()),
        # 上游存在 128 等 DRS 状态位，int8 会溢出。
        ("drs", pa.int16()),
        ("x", pa.float32()),
        ("y", pa.float32()),
        ("z", pa.float32()),
        ("acc_x", pa.float32()),
        ("acc_y", pa.float32()),
        ("acc_z", pa.float32()),
        ("driver_ahead", pa.string()),
        ("distance_to_driver_ahead", pa.float32()),
        ("lap_duration", pa.float32()),
        ("compound", pa.string()),
        ("stint", pa.int16()),
        ("tyre_life", pa.float32()),
        ("fresh_tyre", pa.bool_()),
        ("team", pa.string()),
        ("position", pa.int16()),
        ("track_status", pa.string()),
        ("deleted", pa.bool_()),
        ("is_accurate", pa.bool_()),
        ("lap_start_raw", pa.string()),
        ("data_key", pa.string()),
        ("source_file", pa.string()),
        ("source_commit", pa.string()),
    ],
    metadata={
        b"schema_version": SCHEMA_VERSION.encode(),
        b"numeric_precision": b"continuous telemetry uses float32",
        b"sort_order": b"driver_number,lap,time",
        b"data_layer": b"normalized canonical analysis layer",
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description="将 TracingInsights 原始 JSON 转为规范 Parquet。")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--commit", help="归档快照必须显式提供来源 Git commit")
    parser.add_argument("--out", default="data/normalized/tracinginsights")
    parser.add_argument("--row-group-size", type=int, default=65536)
    parser.add_argument("--sessions", help="可选，逗号分隔；默认转换全部赛段。")
    parser.add_argument("--resume", action="store_true", help="校验并复用已完成分区")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if (root / ".git").exists():
        commit = args.commit or git_output(root, "rev-parse", "HEAD")
    elif args.commit:
        commit = args.commit
    else:
        parser.error("非 Git 归档必须提供 --commit")
    requested = (
        {normalize_session(value.strip()) for value in args.sessions.split(",") if value.strip()}
        if args.sessions
        else None
    )
    output_root = (
        Path(args.out).resolve()
        / f"schema={SCHEMA_VERSION}"
        / f"year={args.year}"
        / f"commit={commit[:12]}"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    global_driver_map = load_global_driver_map(root)

    partitions: list[dict[str, Any]] = []
    for meeting_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name != ".git"):
        for session_dir in sorted(path for path in meeting_dir.iterdir() if path.is_dir()):
            session = normalize_session(session_dir.name)
            if requested is not None and session not in requested:
                continue
            telemetry_files = list(session_dir.glob("*/*_tel.json"))
            if not telemetry_files:
                continue
            result = convert_partition(
                args.year,
                commit,
                meeting_dir.name,
                session,
                session_dir,
                output_root,
                args.row_group_size,
                global_driver_map,
                args.resume,
            )
            partitions.append(result)
            # Windows 控制台通常使用 GBK；日志使用 ASCII 转义，避免非 GBK 地名
            # 中断长时间转换。清单文件仍以 UTF-8 保存原始字符。
            print(json.dumps(result, ensure_ascii=True), flush=True)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "year": args.year,
        "source_root": str(root),
        "source_repository": f"https://github.com/TracingInsights-Archive/{args.year}.git",
        "source_commit": commit,
        "sort_order": ["driver_number", "lap", "time"],
        "continuous_dtype": "float32",
        "row_group_size": args.row_group_size,
        "partitions": partitions,
        "totals": {
            "partitions": len(partitions),
            "source_files": sum(item["source_files"] for item in partitions),
            "valid_source_files": sum(item["valid_source_files"] for item in partitions),
            "invalid_source_files": sum(item["invalid_source_files"] for item in partitions),
            "rows": sum(item["rows"] for item in partitions),
            "bytes": sum(item["parquet_bytes"] for item in partitions),
        },
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest_path}")
    return 0


def convert_partition(
    year: int,
    commit: str,
    meeting: str,
    session: str,
    session_dir: Path,
    output_root: Path,
    row_group_size: int,
    global_driver_map: dict[str, int],
    resume: bool,
) -> dict[str, Any]:
    driver_map = load_driver_map(session_dir / "drivers.json")
    output_dir = output_root / f"session={partition_value(session)}" / f"meeting={partition_value(meeting)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "telemetry.parquet"
    files: list[tuple[int, int, Path, dict[int, dict[str, Any]]]] = []
    for driver_dir in (path for path in session_dir.iterdir() if path.is_dir()):
        number = driver_map.get(driver_dir.name.upper())
        if number is None:
            number = load_driver_number_from_laptimes(driver_dir / "laptimes.json")
        if number is None:
            number = global_driver_map.get(driver_dir.name.upper())
        if number is None:
            raise ValueError(f"无法确定车手编号：{driver_dir}")
        lap_metadata = load_lap_metadata(driver_dir / "laptimes.json")
        for path in driver_dir.glob("*_tel.json"):
            files.append((number, lap_number(path), path, lap_metadata))
    files.sort(key=lambda item: (item[0], item[1], item[2].as_posix()))

    if output_path.exists():
        if not resume:
            raise FileExistsError(f"规范分区已存在，拒绝覆盖：{output_path}")
        parquet = pq.ParquetFile(output_path)
        if parquet.schema_arrow != PARQUET_SCHEMA:
            raise ValueError(f"已有分区 schema 不匹配：{output_path}")
        # 直接通过 ParquetFile 读取单文件，避免 PyArrow 从父目录
        # year=... 推断 Hive 分区列并与文件内 int16 year 冲突。
        represented = set(
            parquet.read(columns=["source_file"])["source_file"].unique().to_pylist()
        )
        missing_files = [
            path for _, _, path, _ in files
            if path.relative_to(path.parents[3]).as_posix() not in represented
        ]
        return {
            "meeting": meeting,
            "session": session,
            "source_files": len(files),
            "valid_source_files": len(files) - len(missing_files),
            "invalid_source_files": len(missing_files),
            "skipped_files": [
                {
                    "source_file": path.relative_to(path.parents[3]).as_posix(),
                    "reason": "已有 Parquet 未包含该源文件；以目录表 validation_error 为准",
                }
                for path in missing_files
            ],
            "source_bytes": sum(path.stat().st_size for _, _, path, _ in files),
            "source_listing_sha256": source_listing_digest(files, session_dir),
            "rows": parquet.metadata.num_rows,
            "parquet_path": output_path.relative_to(output_root).as_posix(),
            "parquet_bytes": output_path.stat().st_size,
            "parquet_sha256": sha256_file(output_path),
            "resumed": True,
        }

    rows = 0
    skipped_files: list[dict[str, str]] = []
    source_bytes = 0
    source_digest = hashlib.sha256()
    writer = pq.ParquetWriter(
        output_path,
        PARQUET_SCHEMA,
        compression="zstd",
        compression_level=6,
        use_dictionary=True,
        write_statistics=True,
    )
    try:
        for number, lap, path, lap_metadata in files:
            source_bytes += path.stat().st_size
            source_digest.update(path.relative_to(session_dir).as_posix().encode())
            source_digest.update(str(path.stat().st_size).encode())
            try:
                table = telemetry_table(
                    year,
                    meeting,
                    session,
                    number,
                    path.parent.name.upper(),
                    lap,
                    path,
                    commit,
                    lap_metadata.get(lap, {}),
                )
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError, pa.ArrowInvalid) as exc:
                skipped_files.append(
                    {
                        "source_file": path.relative_to(path.parents[3]).as_posix(),
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            writer.write_table(table, row_group_size=row_group_size)
            rows += table.num_rows
    finally:
        writer.close()

    return {
        "meeting": meeting,
        "session": session,
        "source_files": len(files),
        "valid_source_files": len(files) - len(skipped_files),
        "invalid_source_files": len(skipped_files),
        "skipped_files": skipped_files,
        "source_bytes": source_bytes,
        "source_listing_sha256": source_digest.hexdigest(),
        "rows": rows,
        "parquet_path": output_path.relative_to(output_root).as_posix(),
        "parquet_bytes": output_path.stat().st_size,
        "parquet_sha256": sha256_file(output_path),
    }


def telemetry_table(
    year: int,
    meeting: str,
    session: str,
    driver_number: int,
    driver_acronym: str,
    lap: int,
    path: Path,
    commit: str,
    lap_meta: dict[str, Any],
) -> pa.Table:
    payload = json.loads(path.read_text(encoding="utf-8"))
    telemetry = payload.get("tel")
    if not isinstance(telemetry, dict):
        raise ValueError(f"缺少 tel 对象：{path}")
    missing = sorted(REQUIRED_TELEMETRY_FIELDS - telemetry.keys())
    if missing:
        raise ValueError(f"缺少必需遥测字段 {missing}：{path}")
    unknown = sorted(set(telemetry) - KNOWN_TELEMETRY_FIELDS)
    if unknown:
        raise ValueError(f"发现未登记遥测字段 {unknown}：{path}")
    times = telemetry.get("time")
    if not isinstance(times, list) or not times:
        raise ValueError(f"time 为空：{path}")
    count = len(times)
    numeric_times = np.asarray(times, dtype=np.float64)
    if not np.isfinite(numeric_times).all():
        raise ValueError(f"time 包含非有限数值：{path}")
    if np.any(np.diff(numeric_times) < 0):
        raise ValueError(f"time 非单调递增：{path}")
    for key, value in telemetry.items():
        if isinstance(value, list) and len(value) != count:
            raise ValueError(f"数组长度不一致 {key}：{path}")

    scalar = lambda value: [value] * count
    columns: dict[str, Any] = {
        "year": scalar(year),
        "meeting": scalar(meeting),
        "session": scalar(session),
        "driver_acronym": scalar(driver_acronym),
        "driver_number": scalar(driver_number),
        "lap": scalar(lap),
        "sample_index": np.arange(count, dtype=np.int32),
        "time": float_array(telemetry.get("time"), count),
        "distance": float_array(telemetry.get("distance"), count),
        "rel_distance": float_array(telemetry.get("rel_distance"), count),
        "speed": float_array(telemetry.get("speed"), count),
        "throttle": float_array(telemetry.get("throttle"), count),
        "brake": int_array(telemetry.get("brake"), count),
        "gear": int_array(telemetry.get("gear"), count),
        "rpm": float_array(telemetry.get("rpm"), count),
        "drs": int_array(telemetry.get("drs"), count),
        "x": float_array(telemetry.get("x"), count),
        "y": float_array(telemetry.get("y"), count),
        "z": float_array(telemetry.get("z"), count),
        "acc_x": float_array(telemetry.get("acc_x"), count),
        "acc_y": float_array(telemetry.get("acc_y"), count),
        "acc_z": float_array(telemetry.get("acc_z"), count),
        "driver_ahead": string_array(telemetry.get("DriverAhead"), count),
        "distance_to_driver_ahead": float_array(telemetry.get("DistanceToDriverAhead"), count),
        "lap_duration": scalar(to_float(lap_meta.get("time"))),
        "compound": scalar(to_string(lap_meta.get("compound"))),
        "stint": scalar(to_int(lap_meta.get("stint"))),
        "tyre_life": scalar(to_float(lap_meta.get("life"))),
        "fresh_tyre": scalar(to_bool(lap_meta.get("fresh"))),
        "team": scalar(to_string(lap_meta.get("team"))),
        "position": scalar(to_int(lap_meta.get("pos"))),
        "track_status": scalar(to_string(lap_meta.get("status"))),
        "deleted": scalar(to_bool(lap_meta.get("del"))),
        "is_accurate": scalar(to_bool(lap_meta.get("iacc"))),
        "lap_start_raw": scalar(to_string(lap_meta.get("lSD"))),
        "data_key": scalar(to_string(telemetry.get("dataKey"))),
        # 仅保存仓库内相对路径，避免本机绝对路径污染可移植数据集。
        "source_file": scalar(path.relative_to(path.parents[3]).as_posix()),
        "source_commit": scalar(commit),
    }
    return pa.Table.from_pydict(columns, schema=PARQUET_SCHEMA)


def load_driver_map(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, int] = {}
    for row in payload.get("drivers", []):
        acronym = row.get("driver")
        number = to_int(row.get("dn"))
        if acronym and number is not None:
            result[str(acronym).upper()] = number
    return result


def load_global_driver_map(root: Path) -> dict[str, int]:
    """仅采用全季所有 drivers.json 中一致且唯一的车号映射。"""
    candidates: dict[str, set[int]] = {}
    for path in root.rglob("drivers.json"):
        for acronym, number in load_driver_map(path).items():
            candidates.setdefault(acronym, set()).add(number)
    return {
        acronym: next(iter(numbers))
        for acronym, numbers in candidates.items()
        if len(numbers) == 1
    }


def load_driver_number_from_laptimes(path: Path) -> int | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for value in payload.get("dNum", []):
        number = to_int(value)
        if number is not None:
            return number
    return None


def load_lap_metadata(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    laps = payload.get("lap", [])
    result: dict[int, dict[str, Any]] = {}
    for index, value in enumerate(laps):
        lap = to_int(value)
        if lap is None:
            continue
        result[lap] = {
            key: items[index] if isinstance(items, list) and index < len(items) else None
            for key, items in payload.items()
        }
    return result


def float_array(value: Any, count: int) -> np.ndarray:
    source = value if isinstance(value, list) else [None] * count
    return np.asarray([np.nan if to_float(item) is None else to_float(item) for item in source], dtype=np.float32)


def int_array(value: Any, count: int) -> list[int | None]:
    source = value if isinstance(value, list) else [None] * count
    return [to_int(item) for item in source]


def string_array(value: Any, count: int) -> list[str | None]:
    source = value if isinstance(value, list) else [None] * count
    return [to_string(item) for item in source]


def to_float(value: Any) -> float | None:
    if value is None or value == "None":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def to_bool(value: Any) -> bool | None:
    if value is None or value == "None":
        return None
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


def to_string(value: Any) -> str | None:
    if value is None or value == "None":
        return None
    return str(value)


def lap_number(path: Path) -> int:
    match = re.match(r"(\d+)_tel\.json$", path.name)
    if not match:
        raise ValueError(f"无法解析圈号：{path}")
    return int(match.group(1))


def normalize_session(value: str) -> str:
    return SESSION_ALIASES.get(value, value)


def partition_value(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def git_output(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_listing_digest(
    files: list[tuple[int, int, Path, dict[int, dict[str, Any]]]], session_dir: Path
) -> str:
    digest = hashlib.sha256()
    for _, _, path, _ in files:
        digest.update(path.relative_to(session_dir).as_posix().encode())
        digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
