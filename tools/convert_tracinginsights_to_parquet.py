#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


SCHEMA_V4_VERSION = "tracinginsights-expanded-v4"
SCHEMA_V5_VERSION = "tracinginsights-expanded-v5"
# 保留旧 CLI 和模块调用方的默认行为；v5 必须显式选择。
SCHEMA_VERSION = SCHEMA_V4_VERSION
LATEST_SCHEMA_VERSION = SCHEMA_V5_VERSION
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_V4_VERSION, SCHEMA_V5_VERSION)
SESSION_ALIASES = {"Sprint Shootout": "Sprint Qualifying"}
ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
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
    # 新上游若提供未经映射的状态码，v5 优先读取该字段；旧数据仍从 drs
    # 精确保留源值，并通过 drs_raw_source 说明来源。
    "drs_raw",
}
ARRAY_TELEMETRY_FIELDS = KNOWN_TELEMETRY_FIELDS - {"dataKey"}
REQUIRED_TELEMETRY_FIELDS_V4 = {
    "time", "speed", "throttle", "brake", "gear", "rpm", "drs", "x", "y", "z"
}
REQUIRED_TELEMETRY_FIELDS_V5 = {
    "time", "speed", "throttle", "brake", "gear", "rpm", "x", "y", "z"
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

KNOWN_ENUM_VALUES: dict[str, set[Any]] = {
    "brake": {0, 1},
    "gear": {-1, 0, 1, 2, 3, 4, 5, 6, 7, 8},
    # FastF1 旧编码集合。新规或上游哨兵值不会被拒绝，只会进入 unknown_counts。
    "drs_raw": {0, 1, 2, 3, 8, 9, 10, 12, 14},
    "compound": {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"},
}

AUDITED_FIELDS = (
    "time",
    "distance",
    "rel_distance",
    "speed",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "drs_raw",
    "drs_raw_source",
    "x",
    "y",
    "z",
    "acc_x",
    "acc_y",
    "acc_z",
    "driver_ahead",
    "distance_to_driver_ahead",
    "lap_duration",
    "compound",
    "stint",
    "tyre_life",
    "position",
    "track_status",
    "track_status_raw",
    "deleted",
    "is_accurate",
    "sample_time",
    "session_time",
    "lap_start_session_time",
    "sector_1_duration",
    "sector_2_duration",
    "sector_3_duration",
    "speed_i1",
    "speed_i2",
    "speed_finish_line",
    "speed_trap",
    "weather_sample_session_time",
    "air_temperature",
    "humidity",
    "pressure",
    "track_temperature",
    "wind_direction",
    "wind_speed",
    "rainfall",
    "absolute_time_source",
)

DOMAIN_COUNT_FIELDS = {
    "brake",
    "gear",
    "drs_raw",
    "drs_raw_source",
    "driver_ahead",
    "compound",
    "stint",
    "position",
    "track_status",
    "track_status_raw",
    "deleted",
    "is_accurate",
    "rainfall",
    "absolute_time_source",
}


V4_FIELDS = [
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
]

V5_FIELDS = V4_FIELDS + [
    # drs_raw 精确保留源 JSON 中 drs_raw（若存在）或 drs 的值；来源单独标注，
    # 不能据此声称它是 FIA ECU 的主动空力状态。
    # raw 状态使用 int64，避免未来上游哨兵值被 int16 截断；旧 drs 列仍保持
    # v4 的 int16 合同，超出其值域时写 null 并在 coercion_failures 中审计。
    ("drs_raw", pa.int64()),
    ("drs_raw_source", pa.string()),
    ("track_status_raw", pa.string()),
    ("lap_start_time", pa.timestamp("ns")),
    ("sample_time", pa.timestamp("ns")),
    ("absolute_time_source", pa.string()),
    ("session_time", pa.float32()),
    ("lap_start_session_time", pa.float32()),
    ("sector_1_duration", pa.float32()),
    ("sector_2_duration", pa.float32()),
    ("sector_3_duration", pa.float32()),
    ("sector_1_session_time", pa.float32()),
    ("sector_2_session_time", pa.float32()),
    ("sector_3_session_time", pa.float32()),
    ("speed_i1", pa.float32()),
    ("speed_i2", pa.float32()),
    ("speed_finish_line", pa.float32()),
    ("speed_trap", pa.float32()),
    ("pit_out_session_time", pa.float32()),
    ("pit_in_session_time", pa.float32()),
    ("weather_sample_session_time", pa.float32()),
    ("air_temperature", pa.float32()),
    ("humidity", pa.float32()),
    ("pressure", pa.float32()),
    ("rainfall", pa.bool_()),
    ("track_temperature", pa.float32()),
    ("wind_direction", pa.float32()),
    ("wind_speed", pa.float32()),
    ("deleted_reason", pa.string()),
    ("fastf1_generated", pa.bool_()),
    # 未登记的上游字段逐采样点原样转为紧凑 JSON，避免新状态码在 schema
    # 更新前被静默丢弃。无未知字段时为 null，几乎不增加 Parquet 体积。
    ("extra_telemetry_json", pa.string()),
]


def schema_metadata(version: str) -> dict[bytes, bytes]:
    metadata = {
        b"schema_version": version.encode(),
        b"numeric_precision": b"continuous telemetry uses float32",
        b"sort_order": b"driver_number,lap,time",
        b"data_layer": b"normalized canonical analysis layer",
    }
    if version == SCHEMA_V5_VERSION:
        metadata.update(
            {
                b"absolute_time_semantics": (
                    b"sample_time=timezone-unspecified lap_start_raw plus telemetry time seconds"
                ),
                b"raw_state_semantics": (
                    b"drs_raw preserves the selected source field; inspect drs_raw_source"
                ),
                b"quality_audit": b"value-domain-audit.json",
            }
        )
    return metadata


PARQUET_SCHEMA_V4 = pa.schema(
    V4_FIELDS,
    metadata=schema_metadata(SCHEMA_V4_VERSION),
)
PARQUET_SCHEMA_V5 = pa.schema(
    V5_FIELDS,
    metadata=schema_metadata(SCHEMA_V5_VERSION),
)
PARQUET_SCHEMAS = {
    SCHEMA_V4_VERSION: PARQUET_SCHEMA_V4,
    SCHEMA_V5_VERSION: PARQUET_SCHEMA_V5,
}
# 兼容此前从本模块导入 PARQUET_SCHEMA 的调用方；默认仍是 v4。
PARQUET_SCHEMA = PARQUET_SCHEMA_V4


def schema_for(version: str) -> pa.Schema:
    try:
        return PARQUET_SCHEMAS[version]
    except KeyError as exc:
        raise ValueError(f"不支持的 schema 版本：{version}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="将 TracingInsights 原始 JSON 转为规范 Parquet。")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--commit", help="归档快照必须显式提供来源 Git commit")
    parser.add_argument("--out", default="data/normalized/tracinginsights")
    parser.add_argument("--row-group-size", type=int, default=65536)
    parser.add_argument("--sessions", help="可选，逗号分隔；默认转换全部赛段。")
    parser.add_argument("--resume", action="store_true", help="校验并复用已完成分区")
    parser.add_argument(
        "--schema-version",
        choices=SUPPORTED_SCHEMA_VERSIONS,
        default=SCHEMA_VERSION,
        help=f"输出合同版本；默认保持兼容版本 {SCHEMA_VERSION}",
    )
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
    schema_version = args.schema_version
    output_root = (
        Path(args.out).resolve()
        / f"schema={schema_version}"
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
                schema_version,
            )
            partitions.append(result)
            # Windows 控制台通常使用 GBK；日志使用 ASCII 转义，避免非 GBK 地名
            # 中断长时间转换。清单文件仍以 UTF-8 保存原始字符。
            print(json.dumps(result, ensure_ascii=True), flush=True)

    manifest = {
        "schema_version": schema_version,
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
    if schema_version == SCHEMA_V5_VERSION:
        audit_payload = aggregate_partition_audits(
            partitions,
            output_root,
            year=args.year,
            source_commit=commit,
        )
        audit_path = output_root / "value-domain-audit.json"
        audit_path.write_text(
            json.dumps(audit_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest["value_domain_audit"] = {
            "path": audit_path.relative_to(output_root).as_posix(),
            "sha256": sha256_file(audit_path),
            "partitions": len(partitions),
            "rows": audit_payload["rows"],
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
    schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    target_schema = schema_for(schema_version)
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
        if not parquet.schema_arrow.equals(target_schema, check_metadata=True):
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
        result = {
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
        if schema_version == SCHEMA_V5_VERSION:
            audit_path = output_dir / "telemetry.value-domain-audit.json"
            parquet_sha256 = result["parquet_sha256"]
            if audit_path.exists():
                audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
            else:
                source_audit = empty_source_audit()
                represented_paths = set(represented)
                for _, lap, path, lap_metadata in files:
                    relative = path.relative_to(path.parents[3]).as_posix()
                    if relative not in represented_paths:
                        continue
                    collect_source_audit_from_path(
                        path,
                        lap_metadata.get(lap, {}),
                        source_audit,
                    )
                audit_payload = audit_parquet_file(
                    output_path,
                    source_audit,
                    schema_version=schema_version,
                    parquet_sha256=parquet_sha256,
                )
                audit_path.write_text(
                    json.dumps(audit_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if audit_payload.get("parquet_sha256") != parquet_sha256:
                raise ValueError(f"已有分区审计与 Parquet 哈希不匹配：{audit_path}")
            result.update(
                {
                    "value_domain_audit_path": audit_path.relative_to(output_root).as_posix(),
                    "value_domain_audit_sha256": sha256_file(audit_path),
                }
            )
        return result

    rows = 0
    skipped_files: list[dict[str, str]] = []
    source_bytes = 0
    source_digest = hashlib.sha256()
    source_audit = empty_source_audit()
    writer = pq.ParquetWriter(
        output_path,
        target_schema,
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
                    schema_version,
                    source_audit,
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

    result = {
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
    if schema_version == SCHEMA_V5_VERSION:
        audit_payload = audit_parquet_file(
            output_path,
            source_audit,
            schema_version=schema_version,
            parquet_sha256=result["parquet_sha256"],
        )
        audit_path = output_dir / "telemetry.value-domain-audit.json"
        audit_path.write_text(
            json.dumps(audit_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result.update(
            {
                "value_domain_audit_path": audit_path.relative_to(output_root).as_posix(),
                "value_domain_audit_sha256": sha256_file(audit_path),
            }
        )
    return result


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
    schema_version: str = SCHEMA_VERSION,
    source_audit: dict[str, Any] | None = None,
) -> pa.Table:
    payload = json.loads(path.read_text(encoding="utf-8"))
    telemetry = payload.get("tel")
    if not isinstance(telemetry, dict):
        raise ValueError(f"缺少 tel 对象：{path}")
    required_fields = (
        REQUIRED_TELEMETRY_FIELDS_V4
        if schema_version == SCHEMA_V4_VERSION
        else REQUIRED_TELEMETRY_FIELDS_V5
    )
    missing = sorted(required_fields - telemetry.keys())
    if missing:
        raise ValueError(f"缺少必需遥测字段 {missing}：{path}")
    unknown = sorted(set(telemetry) - KNOWN_TELEMETRY_FIELDS)
    if unknown and schema_version == SCHEMA_V4_VERSION:
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

    coercion_failures: dict[str, dict[str, Any]] = {}
    if schema_version == SCHEMA_V5_VERSION:
        wrong_shapes = sorted(
            key
            for key in ARRAY_TELEMETRY_FIELDS & telemetry.keys()
            if not isinstance(telemetry[key], list)
        )
        if wrong_shapes:
            raise ValueError(f"逐点遥测字段不是数组 {wrong_shapes}：{path}")

        as_float_array = lambda value, field: nullable_float_array(
            value, count, field, coercion_failures
        )
        as_int16_array = lambda value, field: nullable_int_array(
            value,
            count,
            field,
            coercion_failures,
            minimum=np.iinfo(np.int16).min,
            maximum=np.iinfo(np.int16).max,
        )
        as_float_scalar = lambda value, field: nullable_float_value(
            value, field, coercion_failures
        )
        as_int16_scalar = lambda value, field: nullable_int_value(
            value,
            field,
            coercion_failures,
            minimum=np.iinfo(np.int16).min,
            maximum=np.iinfo(np.int16).max,
        )
        as_bool_scalar = lambda value, field: nullable_bool_value(
            value, field, coercion_failures
        )
        lap_start_raw = raw_string(lap_meta.get("lSD"))
        track_status = raw_string(lap_meta.get("status"))
    else:
        as_float_array = lambda value, field: float_array(value, count)
        as_int16_array = lambda value, field: int_array(value, count)
        as_float_scalar = lambda value, field: to_float(value)
        as_int16_scalar = lambda value, field: to_int(value)
        as_bool_scalar = lambda value, field: to_bool(value)
        lap_start_raw = to_string(lap_meta.get("lSD"))
        track_status = to_string(lap_meta.get("status"))

    scalar = lambda value: [value] * count
    columns: dict[str, Any] = {
        "year": scalar(year),
        "meeting": scalar(meeting),
        "session": scalar(session),
        "driver_acronym": scalar(driver_acronym),
        "driver_number": scalar(driver_number),
        "lap": scalar(lap),
        "sample_index": np.arange(count, dtype=np.int32),
        "time": as_float_array(telemetry.get("time"), "tel.time"),
        "distance": as_float_array(telemetry.get("distance"), "tel.distance"),
        "rel_distance": as_float_array(telemetry.get("rel_distance"), "tel.rel_distance"),
        "speed": as_float_array(telemetry.get("speed"), "tel.speed"),
        "throttle": as_float_array(telemetry.get("throttle"), "tel.throttle"),
        "brake": as_int16_array(telemetry.get("brake"), "tel.brake"),
        "gear": as_int16_array(telemetry.get("gear"), "tel.gear"),
        "rpm": as_float_array(telemetry.get("rpm"), "tel.rpm"),
        "drs": as_int16_array(telemetry.get("drs"), "tel.drs"),
        "x": as_float_array(telemetry.get("x"), "tel.x"),
        "y": as_float_array(telemetry.get("y"), "tel.y"),
        "z": as_float_array(telemetry.get("z"), "tel.z"),
        "acc_x": as_float_array(telemetry.get("acc_x"), "tel.acc_x"),
        "acc_y": as_float_array(telemetry.get("acc_y"), "tel.acc_y"),
        "acc_z": as_float_array(telemetry.get("acc_z"), "tel.acc_z"),
        "driver_ahead": string_array(telemetry.get("DriverAhead"), count),
        "distance_to_driver_ahead": as_float_array(
            telemetry.get("DistanceToDriverAhead"), "tel.DistanceToDriverAhead"
        ),
        "lap_duration": scalar(as_float_scalar(lap_meta.get("time"), "laptimes.time")),
        "compound": scalar(to_string(lap_meta.get("compound"))),
        "stint": scalar(as_int16_scalar(lap_meta.get("stint"), "laptimes.stint")),
        "tyre_life": scalar(as_float_scalar(lap_meta.get("life"), "laptimes.life")),
        "fresh_tyre": scalar(as_bool_scalar(lap_meta.get("fresh"), "laptimes.fresh")),
        "team": scalar(to_string(lap_meta.get("team"))),
        "position": scalar(as_int16_scalar(lap_meta.get("pos"), "laptimes.pos")),
        "track_status": scalar(track_status),
        "deleted": scalar(as_bool_scalar(lap_meta.get("del"), "laptimes.del")),
        "is_accurate": scalar(as_bool_scalar(lap_meta.get("iacc"), "laptimes.iacc")),
        "lap_start_raw": scalar(lap_start_raw),
        "data_key": scalar(to_string(telemetry.get("dataKey"))),
        # 仅保存仓库内相对路径，避免本机绝对路径污染可移植数据集。
        "source_file": scalar(path.relative_to(path.parents[3]).as_posix()),
        "source_commit": scalar(commit),
    }
    timestamp_columns: dict[str, Any] = {}
    timestamp_parse_failed = False
    if schema_version == SCHEMA_V5_VERSION:
        drs_raw_source = None
        drs_raw_value: Any = None
        if "drs_raw" in telemetry:
            drs_raw_source = "tel.drs_raw"
            drs_raw_value = telemetry.get("drs_raw")
        elif "drs" in telemetry:
            drs_raw_source = "tel.drs"
            drs_raw_value = telemetry.get("drs")

        lap_start_array, sample_time_array, absolute_time_source = absolute_time_arrays(
            lap_start_raw,
            numeric_times,
        )
        timestamp_parse_failed = lap_start_raw is not None and absolute_time_source is None
        timestamp_columns = {
            "lap_start_time": lap_start_array,
            "sample_time": sample_time_array,
        }
        columns.update(
            {
                "drs_raw": nullable_int_array(
                    drs_raw_value,
                    count,
                    f"{drs_raw_source or 'tel.drs_raw'}",
                    coercion_failures,
                    minimum=np.iinfo(np.int64).min,
                    maximum=np.iinfo(np.int64).max,
                ),
                "drs_raw_source": scalar(drs_raw_source),
                "track_status_raw": scalar(raw_string(lap_meta.get("status"))),
                "absolute_time_source": scalar(absolute_time_source),
                "session_time": scalar(as_float_scalar(lap_meta.get("sesT"), "laptimes.sesT")),
                "lap_start_session_time": scalar(as_float_scalar(lap_meta.get("lST"), "laptimes.lST")),
                "sector_1_duration": scalar(as_float_scalar(lap_meta.get("s1"), "laptimes.s1")),
                "sector_2_duration": scalar(as_float_scalar(lap_meta.get("s2"), "laptimes.s2")),
                "sector_3_duration": scalar(as_float_scalar(lap_meta.get("s3"), "laptimes.s3")),
                "sector_1_session_time": scalar(as_float_scalar(lap_meta.get("s1T"), "laptimes.s1T")),
                "sector_2_session_time": scalar(as_float_scalar(lap_meta.get("s2T"), "laptimes.s2T")),
                "sector_3_session_time": scalar(as_float_scalar(lap_meta.get("s3T"), "laptimes.s3T")),
                "speed_i1": scalar(as_float_scalar(lap_meta.get("vi1"), "laptimes.vi1")),
                "speed_i2": scalar(as_float_scalar(lap_meta.get("vi2"), "laptimes.vi2")),
                "speed_finish_line": scalar(as_float_scalar(lap_meta.get("vfl"), "laptimes.vfl")),
                "speed_trap": scalar(as_float_scalar(lap_meta.get("vst"), "laptimes.vst")),
                "pit_out_session_time": scalar(as_float_scalar(lap_meta.get("pout"), "laptimes.pout")),
                "pit_in_session_time": scalar(as_float_scalar(lap_meta.get("pin"), "laptimes.pin")),
                "weather_sample_session_time": scalar(as_float_scalar(lap_meta.get("wT"), "laptimes.wT")),
                "air_temperature": scalar(as_float_scalar(lap_meta.get("wAT"), "laptimes.wAT")),
                "humidity": scalar(as_float_scalar(lap_meta.get("wH"), "laptimes.wH")),
                "pressure": scalar(as_float_scalar(lap_meta.get("wP"), "laptimes.wP")),
                "rainfall": scalar(as_bool_scalar(lap_meta.get("wR"), "laptimes.wR")),
                "track_temperature": scalar(as_float_scalar(lap_meta.get("wTT"), "laptimes.wTT")),
                "wind_direction": scalar(as_float_scalar(lap_meta.get("wWD"), "laptimes.wWD")),
                "wind_speed": scalar(as_float_scalar(lap_meta.get("wWS"), "laptimes.wWS")),
                "deleted_reason": scalar(to_string(lap_meta.get("delR"))),
                "fastf1_generated": scalar(as_bool_scalar(lap_meta.get("ff1G"), "laptimes.ff1G")),
                "extra_telemetry_json": extra_telemetry_array(telemetry, unknown, count),
            }
        )
        columns.update(timestamp_columns)

    table = pa.Table.from_pydict(columns, schema=schema_for(schema_version))
    if source_audit is not None and schema_version == SCHEMA_V5_VERSION:
        record_source_audit(
            source_audit,
            telemetry,
            count,
            unknown,
            timestamp_parse_failed=timestamp_parse_failed,
            source_file=path.relative_to(path.parents[3]).as_posix(),
            coercion_failures=coercion_failures,
        )
    return table


def parse_lap_start_ns(value: str | None) -> np.datetime64 | None:
    """解析上游绝对时间；解析失败时返回空值，绝不以本地时区补全。"""
    if value is None or not ISO_DATETIME_RE.fullmatch(value):
        return None
    try:
        if value.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", value):
            aware = datetime.fromisoformat(value.replace("Z", "+00:00"))
            normalized = aware.astimezone(timezone.utc).replace(tzinfo=None)
            parsed = np.datetime64(normalized, "ns")
        else:
            parsed = np.datetime64(value, "ns")
    except (TypeError, ValueError, OverflowError):
        return None
    return None if np.isnat(parsed) else parsed


def absolute_time_arrays(
    lap_start_raw: str | None,
    telemetry_times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, str | None]:
    """仅在源 `lSD` 可解析时生成绝对时间，并明确记录推导来源。"""
    count = len(telemetry_times)
    lap_start = parse_lap_start_ns(lap_start_raw)
    if lap_start is None:
        missing = np.full(count, np.datetime64("NaT"), dtype="datetime64[ns]")
        return missing, missing.copy(), None
    scaled = telemetry_times * 1_000_000_000.0
    int64 = np.iinfo(np.int64)
    if np.any(scaled < int64.min) or np.any(scaled > int64.max):
        missing = np.full(count, np.datetime64("NaT"), dtype="datetime64[ns]")
        return missing, missing.copy(), None
    offsets_ns = np.rint(scaled).astype(np.int64)
    lap_starts = np.full(count, lap_start, dtype="datetime64[ns]")
    sample_times = lap_start + offsets_ns.astype("timedelta64[ns]")
    if np.isnat(sample_times).any():
        missing = np.full(count, np.datetime64("NaT"), dtype="datetime64[ns]")
        return missing, missing.copy(), None
    return lap_starts, sample_times, "laptimes.lSD+tel.time"


def float32_time_quantization_tolerance_ns(value: float) -> int:
    """源 double 时间与落盘 float32 的最近舍入上界，再留 1us 时间戳转换余量。"""
    stored = np.float32(value)
    lower = np.nextafter(stored, np.float32(-np.inf))
    upper = np.nextafter(stored, np.float32(np.inf))
    adjacent_ulp = max(float(stored - lower), float(upper - stored))
    return math.ceil(adjacent_ulp * 0.5 * 1_000_000_000.0) + 1_000


def is_missing_source_value(value: Any) -> bool:
    return value is None or value == "None"


def record_coercion_failure(
    failures: dict[str, dict[str, Any]],
    field: str,
    value: Any,
    reason: str,
) -> None:
    entry = failures.setdefault(field, {"count": 0, "examples": []})
    entry["count"] += 1
    append_examples(
        entry["examples"],
        [{"reason": reason, "value": json_safe_value(value)}],
    )


def nullable_float_value(
    value: Any,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> float | None:
    """v5 浮点转换：缺失写 null，源 NaN/inf 原样保留，失败显式审计。"""
    if is_missing_source_value(value):
        return None
    number = to_float(value)
    if number is None:
        record_coercion_failure(failures, field, value, "not_numeric")
        return None
    if np.isfinite(number) and abs(number) > np.finfo(np.float32).max:
        record_coercion_failure(failures, field, value, "outside_float32_range")
        return None
    return number


def nullable_float_array(
    value: Any,
    count: int,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> list[float | None]:
    if value is None:
        return [None] * count
    if not isinstance(value, list):
        record_coercion_failure(failures, field, value, "expected_array")
        return [None] * count
    return [nullable_float_value(item, field, failures) for item in value]


def exact_int(value: Any) -> int | None:
    if is_missing_source_value(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(number) or not number.is_integer():
        return None
    return int(number)


def nullable_int_value(
    value: Any,
    field: str,
    failures: dict[str, dict[str, Any]],
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if is_missing_source_value(value):
        return None
    number = exact_int(value)
    if number is None:
        record_coercion_failure(failures, field, value, "not_finite_integer")
        return None
    if number < minimum or number > maximum:
        record_coercion_failure(failures, field, value, "outside_integer_range")
        return None
    return number


def nullable_int_array(
    value: Any,
    count: int,
    field: str,
    failures: dict[str, dict[str, Any]],
    *,
    minimum: int,
    maximum: int,
) -> list[int | None]:
    if value is None:
        return [None] * count
    if not isinstance(value, list):
        record_coercion_failure(failures, field, value, "expected_array")
        return [None] * count
    return [
        nullable_int_value(
            item,
            field,
            failures,
            minimum=minimum,
            maximum=maximum,
        )
        for item in value
    ]


def nullable_bool_value(
    value: Any,
    field: str,
    failures: dict[str, dict[str, Any]],
) -> bool | None:
    if is_missing_source_value(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    record_coercion_failure(failures, field, value, "not_boolean")
    return None


def raw_string(value: Any) -> str | None:
    """保留源标量的可逆文本表示；不把未知值归并到某个合法枚举。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(
        json_safe_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_safe_value(value: Any) -> Any:
    """将非有限浮点显式编码，保证 extra JSON 仍是严格 JSON。"""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        if np.isnan(value):
            label = "NaN"
        elif value > 0:
            label = "+Infinity"
        else:
            label = "-Infinity"
        return {"__non_finite__": label}
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return value


def extra_telemetry_array(
    telemetry: dict[str, Any],
    unknown_fields: list[str],
    count: int,
) -> list[str | None]:
    if not unknown_fields:
        return [None] * count
    result: list[str] = []
    for index in range(count):
        item = {
            field: json_safe_value(
                telemetry[field][index]
                if isinstance(telemetry[field], list)
                else telemetry[field]
            )
            for field in unknown_fields
        }
        result.append(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    return result


def empty_source_audit() -> dict[str, Any]:
    return {
        "source_files": 0,
        "source_rows": 0,
        "telemetry_fields": {},
        "unknown_telemetry_fields": {},
        "parse_failures": {},
        "coercion_failures": {},
    }


def source_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def append_examples(target: list[Any], values: Iterable[Any], limit: int = 8) -> None:
    seen = {
        json.dumps(json_safe_value(item), ensure_ascii=False, sort_keys=True)
        for item in target
    }
    for value in values:
        safe = json_safe_value(value)
        key = json.dumps(safe, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        target.append(safe)
        seen.add(key)
        if len(target) >= limit:
            break


def record_source_audit(
    audit: dict[str, Any],
    telemetry: dict[str, Any],
    count: int,
    unknown_fields: list[str],
    *,
    timestamp_parse_failed: bool,
    source_file: str,
    coercion_failures: dict[str, dict[str, Any]] | None = None,
) -> None:
    audit["source_files"] += 1
    audit["source_rows"] += count
    for field, value in telemetry.items():
        entry = audit["telemetry_fields"].setdefault(
            field,
            {"files": 0, "rows": 0, "types": set()},
        )
        entry["files"] += 1
        entry["rows"] += count
        entry["types"].add("array" if isinstance(value, list) else "scalar")

    for field in unknown_fields:
        value = telemetry[field]
        values = value if isinstance(value, list) else [value]
        entry = audit["unknown_telemetry_fields"].setdefault(
            field,
            {"files": 0, "rows": 0, "value_types": set(), "examples": []},
        )
        entry["files"] += 1
        entry["rows"] += count
        entry["value_types"].update(source_value_type(item) for item in values)
        append_examples(entry["examples"], values)

    if timestamp_parse_failed:
        entry = audit["parse_failures"].setdefault(
            "lap_start_raw",
            {"count": 0, "examples": []},
        )
        entry["count"] += 1
        append_examples(entry["examples"], [source_file])

    for field, source in (coercion_failures or {}).items():
        entry = audit["coercion_failures"].setdefault(
            field,
            {"count": 0, "examples": []},
        )
        entry["count"] += int(source.get("count", 0))
        append_examples(entry["examples"], source.get("examples", []))


def detect_v5_coercion_failures(
    telemetry: dict[str, Any],
    lap_meta: dict[str, Any],
    count: int,
) -> dict[str, dict[str, Any]]:
    """为缺失审计文件的 resume 路径重建转换失败统计。"""
    failures: dict[str, dict[str, Any]] = {}
    for field in FLOAT_FIELDS:
        nullable_float_array(telemetry.get(field), count, f"tel.{field}", failures)
    for field in ("brake", "gear", "drs"):
        nullable_int_array(
            telemetry.get(field),
            count,
            f"tel.{field}",
            failures,
            minimum=np.iinfo(np.int16).min,
            maximum=np.iinfo(np.int16).max,
        )
    raw_field = "drs_raw" if "drs_raw" in telemetry else "drs"
    nullable_int_array(
        telemetry.get(raw_field),
        count,
        f"tel.{raw_field}",
        failures,
        minimum=np.iinfo(np.int64).min,
        maximum=np.iinfo(np.int64).max,
    )
    for source_field in (
        "time", "life", "sesT", "lST", "s1", "s2", "s3", "s1T", "s2T",
        "s3T", "vi1", "vi2", "vfl", "vst", "pout", "pin", "wT", "wAT",
        "wH", "wP", "wTT", "wWD", "wWS",
    ):
        nullable_float_value(
            lap_meta.get(source_field),
            f"laptimes.{source_field}",
            failures,
        )
    for source_field in ("stint", "pos"):
        nullable_int_value(
            lap_meta.get(source_field),
            f"laptimes.{source_field}",
            failures,
            minimum=np.iinfo(np.int16).min,
            maximum=np.iinfo(np.int16).max,
        )
    for source_field in ("fresh", "del", "iacc", "wR", "ff1G"):
        nullable_bool_value(
            lap_meta.get(source_field),
            f"laptimes.{source_field}",
            failures,
        )
    return failures


def collect_source_audit_from_path(
    path: Path,
    lap_meta: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    telemetry = payload.get("tel")
    if not isinstance(telemetry, dict):
        raise ValueError(f"缺少 tel 对象：{path}")
    times = telemetry.get("time")
    if not isinstance(times, list) or not times:
        raise ValueError(f"time 为空：{path}")
    unknown = sorted(set(telemetry) - KNOWN_TELEMETRY_FIELDS)
    lap_start_raw = to_string(lap_meta.get("lSD"))
    coercion_failures = detect_v5_coercion_failures(
        telemetry,
        lap_meta,
        len(times),
    )
    record_source_audit(
        audit,
        telemetry,
        len(times),
        unknown,
        timestamp_parse_failed=(
            lap_start_raw is not None and parse_lap_start_ns(lap_start_raw) is None
        ),
        source_file=path.relative_to(path.parents[3]).as_posix(),
        coercion_failures=coercion_failures,
    )


def _new_column_audit(dtype: str) -> dict[str, Any]:
    return {
        "dtype": dtype,
        "rows": 0,
        "null_count": 0,
        "nan_count": 0,
        "positive_infinity_count": 0,
        "negative_infinity_count": 0,
        "finite_count": 0,
        "min": None,
        "max": None,
        "value_counts": {},
    }


def _update_min_max(stats: dict[str, Any], minimum: Any, maximum: Any) -> None:
    if minimum is not None and (stats["min"] is None or minimum < stats["min"]):
        stats["min"] = minimum
    if maximum is not None and (stats["max"] is None or maximum > stats["max"]):
        stats["max"] = maximum


def _domain_key(value: Any) -> str:
    return json.dumps(json_safe_value(value), ensure_ascii=False, sort_keys=True)


def _update_column_audit(stats: dict[str, Any], array: pa.Array, field: str) -> None:
    stats["rows"] += len(array)
    stats["null_count"] += array.null_count
    valid = array.filter(pc.is_valid(array)) if array.null_count else array

    if pa.types.is_floating(array.type):
        values = np.asarray(valid.to_numpy(zero_copy_only=False), dtype=np.float64)
        nan_mask = np.isnan(values)
        pos_inf_mask = np.isposinf(values)
        neg_inf_mask = np.isneginf(values)
        finite_mask = np.isfinite(values)
        stats["nan_count"] += int(nan_mask.sum())
        stats["positive_infinity_count"] += int(pos_inf_mask.sum())
        stats["negative_infinity_count"] += int(neg_inf_mask.sum())
        stats["finite_count"] += int(finite_mask.sum())
        if finite_mask.any():
            finite = values[finite_mask]
            _update_min_max(stats, float(finite.min()), float(finite.max()))
    elif pa.types.is_integer(array.type):
        values = valid.to_pylist()
        stats["finite_count"] += len(values)
        if values:
            _update_min_max(stats, min(values), max(values))
    elif pa.types.is_timestamp(array.type):
        stats["finite_count"] += len(valid)
        if len(valid):
            minimum = pc.min(valid).as_py()
            maximum = pc.max(valid).as_py()
            _update_min_max(stats, str(minimum), str(maximum))
    else:
        stats["finite_count"] += len(valid)

    if field in DOMAIN_COUNT_FIELDS and len(valid):
        for row in pc.value_counts(valid).to_pylist():
            value = json_safe_value(row["values"])
            key = _domain_key(value)
            entry = stats["value_counts"].setdefault(
                key,
                {"value": value, "count": 0},
            )
            entry["count"] += int(row["counts"])


def _finalize_column_audit(stats: dict[str, Any]) -> dict[str, Any]:
    rows = stats["rows"]
    non_null = rows - stats["null_count"]
    result = {
        key: value
        for key, value in stats.items()
        if key != "value_counts"
    }
    result["non_null_count"] = non_null
    result["coverage_ratio"] = non_null / rows if rows else 0.0
    result["nonfinite_count"] = (
        stats["nan_count"]
        + stats["positive_infinity_count"]
        + stats["negative_infinity_count"]
    )
    if stats["value_counts"]:
        result["value_counts"] = sorted(
            stats["value_counts"].values(),
            key=lambda item: (-item["count"], _domain_key(item["value"])),
        )
        result["distinct_value_count"] = len(result["value_counts"])
    return result


def finalize_source_audit(audit: dict[str, Any]) -> dict[str, Any]:
    source_files = audit["source_files"]
    source_rows = audit["source_rows"]
    telemetry_fields = {}
    for field, entry in sorted(audit["telemetry_fields"].items()):
        telemetry_fields[field] = {
            "files": entry["files"],
            "rows": entry["rows"],
            "types": sorted(entry["types"]),
            "file_coverage_ratio": entry["files"] / source_files if source_files else 0.0,
            "row_coverage_ratio": entry["rows"] / source_rows if source_rows else 0.0,
        }
    unknown_fields = {}
    for field, entry in sorted(audit["unknown_telemetry_fields"].items()):
        unknown_fields[field] = {
            "files": entry["files"],
            "rows": entry["rows"],
            "value_types": sorted(entry["value_types"]),
            "examples": entry["examples"],
            "file_coverage_ratio": entry["files"] / source_files if source_files else 0.0,
            "row_coverage_ratio": entry["rows"] / source_rows if source_rows else 0.0,
        }
    return {
        "source_files": source_files,
        "source_rows": source_rows,
        "telemetry_fields": telemetry_fields,
        "unknown_telemetry_fields": unknown_fields,
        "parse_failures": audit["parse_failures"],
        "coercion_failures": audit["coercion_failures"],
    }


def unknown_enum_report(columns: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    report: dict[str, list[dict[str, Any]]] = {}
    for field, known_values in KNOWN_ENUM_VALUES.items():
        value_counts = columns.get(field, {}).get("value_counts", [])
        unknown = [
            entry
            for entry in value_counts
            if entry["value"] not in known_values
        ]
        if unknown:
            report[field] = unknown
    return report


def audit_parquet_file(
    path: Path,
    source_audit: dict[str, Any],
    *,
    schema_version: str,
    parquet_sha256: str | None = None,
) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    available = set(parquet.schema_arrow.names)
    fields = [field for field in AUDITED_FIELDS if field in available]
    stats = {
        field: _new_column_audit(str(parquet.schema_arrow.field(field).type))
        for field in fields
    }
    for batch in parquet.iter_batches(columns=fields, batch_size=262_144):
        for field in fields:
            _update_column_audit(stats[field], batch.column(field), field)
    columns = {field: _finalize_column_audit(value) for field, value in stats.items()}
    nonfinite = {
        field: {
            "nan_count": value["nan_count"],
            "positive_infinity_count": value["positive_infinity_count"],
            "negative_infinity_count": value["negative_infinity_count"],
        }
        for field, value in columns.items()
        if pa.types.is_floating(parquet.schema_arrow.field(field).type)
    }
    return {
        "schema_version": schema_version,
        "parquet_path": path.name,
        "parquet_sha256": parquet_sha256 or sha256_file(path),
        "rows": parquet.metadata.num_rows,
        "columns": columns,
        "nonfinite": nonfinite,
        "unknown_enum_values": unknown_enum_report(columns),
        "source_coverage": finalize_source_audit(source_audit),
    }


def _merge_value_counts(
    target: dict[str, dict[str, Any]],
    values: Iterable[dict[str, Any]],
) -> None:
    for item in values:
        value = item["value"]
        key = _domain_key(value)
        entry = target.setdefault(key, {"value": value, "count": 0})
        entry["count"] += int(item["count"])


def _merge_finalized_columns(
    payloads: Iterable[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for columns in payloads:
        for field, source in columns.items():
            target = merged.setdefault(field, _new_column_audit(source["dtype"]))
            if target["dtype"] != source["dtype"]:
                raise ValueError(f"审计列类型不一致：{field}")
            for key in (
                "rows",
                "null_count",
                "nan_count",
                "positive_infinity_count",
                "negative_infinity_count",
                "finite_count",
            ):
                target[key] += int(source.get(key, 0))
            _update_min_max(target, source.get("min"), source.get("max"))
            _merge_value_counts(target["value_counts"], source.get("value_counts", []))
    return {
        field: _finalize_column_audit(stats)
        for field, stats in sorted(merged.items())
    }


def _merge_source_coverages(payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    audit = empty_source_audit()
    for payload in payloads:
        audit["source_files"] += int(payload.get("source_files", 0))
        audit["source_rows"] += int(payload.get("source_rows", 0))
        for field, source in payload.get("telemetry_fields", {}).items():
            target = audit["telemetry_fields"].setdefault(
                field,
                {"files": 0, "rows": 0, "types": set()},
            )
            target["files"] += int(source.get("files", 0))
            target["rows"] += int(source.get("rows", 0))
            target["types"].update(source.get("types", []))
        for field, source in payload.get("unknown_telemetry_fields", {}).items():
            target = audit["unknown_telemetry_fields"].setdefault(
                field,
                {"files": 0, "rows": 0, "value_types": set(), "examples": []},
            )
            target["files"] += int(source.get("files", 0))
            target["rows"] += int(source.get("rows", 0))
            target["value_types"].update(source.get("value_types", []))
            append_examples(target["examples"], source.get("examples", []))
        for field, source in payload.get("parse_failures", {}).items():
            target = audit["parse_failures"].setdefault(
                field,
                {"count": 0, "examples": []},
            )
            target["count"] += int(source.get("count", 0))
            append_examples(target["examples"], source.get("examples", []))
        for field, source in payload.get("coercion_failures", {}).items():
            target = audit["coercion_failures"].setdefault(
                field,
                {"count": 0, "examples": []},
            )
            target["count"] += int(source.get("count", 0))
            append_examples(target["examples"], source.get("examples", []))
    return finalize_source_audit(audit)


def aggregate_partition_audits(
    partitions: list[dict[str, Any]],
    output_root: Path,
    *,
    year: int,
    source_commit: str,
) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    partition_entries: list[dict[str, Any]] = []
    for partition in partitions:
        relative = partition.get("value_domain_audit_path")
        if not relative:
            raise ValueError(
                f"v5 分区缺少值域审计：{partition['session']}/{partition['meeting']}"
            )
        path = output_root / relative
        expected_sha = partition.get("value_domain_audit_sha256")
        actual_sha = sha256_file(path)
        if expected_sha != actual_sha:
            raise ValueError(f"分区审计 SHA-256 不匹配：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("rows") != partition["rows"]:
            raise ValueError(f"分区审计行数不匹配：{path}")
        payloads.append(payload)
        partition_entries.append(
            {
                "meeting": partition["meeting"],
                "session": partition["session"],
                "rows": partition["rows"],
                "path": relative,
                "sha256": actual_sha,
            }
        )

    columns = _merge_finalized_columns(payload["columns"] for payload in payloads)
    source_coverage = _merge_source_coverages(
        payload["source_coverage"] for payload in payloads
    )
    nonfinite = {
        field: {
            "nan_count": value["nan_count"],
            "positive_infinity_count": value["positive_infinity_count"],
            "negative_infinity_count": value["negative_infinity_count"],
        }
        for field, value in columns.items()
        if value["dtype"] in {"float", "double", "halffloat"}
    }
    return {
        "schema_version": SCHEMA_V5_VERSION,
        "year": year,
        "source_commit": source_commit,
        "rows": sum(int(payload["rows"]) for payload in payloads),
        "partitions": partition_entries,
        "columns": columns,
        "nonfinite": nonfinite,
        "unknown_enum_values": unknown_enum_report(columns),
        "source_coverage": source_coverage,
    }


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
