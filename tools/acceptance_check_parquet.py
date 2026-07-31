#!/usr/bin/env python3
"""TracingInsights 规范 Parquet 验收：兼容 v4，并校验 v5 保真与审计合同。"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from convert_tracinginsights_to_parquet import (
    AUDITED_FIELDS,
    SCHEMA_V4_VERSION,
    SCHEMA_V5_VERSION,
    aggregate_partition_audits,
    float32_time_quantization_tolerance_ns as float32_time_tolerance_ns,
    schema_for,
    sha256_file,
    unknown_enum_report,
)


DEFAULT_ROOT = Path("data/normalized/tracinginsights") / f"schema={SCHEMA_V4_VERSION}"
DEFAULT_REPORT = Path("data/logs/parquet-acceptance-report.json")
EXPECTED_SORT = ["driver_number", "lap", "time"]
FLOAT32_COLUMNS = ("time", "speed", "throttle", "rpm", "x", "y", "z", "acc_x")
V5_RAW_COLUMNS = (
    "drs_raw",
    "drs_raw_source",
    "track_status_raw",
    "lap_start_raw",
    "lap_start_time",
    "sample_time",
    "absolute_time_source",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def add_error(report: dict[str, Any], message: str) -> None:
    report["errors"].append(message)


def check_sorted(path: Path) -> dict[str, Any]:
    previous: tuple[int, int, float] | None = None
    rows = 0
    for batch in pq.ParquetFile(path).iter_batches(columns=EXPECTED_SORT, batch_size=262_144):
        drivers = batch.column("driver_number").to_pylist()
        laps = batch.column("lap").to_pylist()
        times = batch.column("time").to_pylist()
        for current in zip(drivers, laps, times):
            if previous is not None and current < previous:
                return {"ok": False, "rows_checked": rows, "previous": previous, "current": current}
            previous = current
            rows += 1
    return {"ok": True, "rows_checked": rows}


def check_column_audit(
    audit: dict[str, Any],
    schema: pa.Schema,
    label: str,
    report: dict[str, Any],
) -> None:
    rows = int(audit.get("rows", -1))
    columns = audit.get("columns", {})
    expected = {name for name in AUDITED_FIELDS if name in schema.names}
    missing = sorted(expected - columns.keys())
    if missing:
        add_error(report, f"{label}: 审计缺列 {missing}")
    for field, stats in columns.items():
        if int(stats.get("rows", -1)) != rows:
            add_error(report, f"{label}: {field} 审计行数不一致")
            continue
        nulls = int(stats.get("null_count", 0))
        finite = int(stats.get("finite_count", 0))
        nonfinite = sum(
            int(stats.get(key, 0))
            for key in ("nan_count", "positive_infinity_count", "negative_infinity_count")
        )
        field_type = schema.field(field).type
        accounted = nulls + finite + (nonfinite if pa.types.is_floating(field_type) else 0)
        if accounted != rows:
            add_error(report, f"{label}: {field} null/finite/nonfinite 未对账")
    expected_unknown = unknown_enum_report(columns)
    if audit.get("unknown_enum_values", {}) != expected_unknown:
        add_error(report, f"{label}: unknown_enum_values 与值计数不一致")
    if expected_unknown:
        report["warnings"].append({"partition": label, "unknown_enum_values": expected_unknown})


def check_v5_sample_semantics(
    path: Path,
    label: str,
    report: dict[str, Any],
    row_limit: int | None = None,
) -> None:
    """逐行校验 v5 状态/绝对时间语义。

    默认必须覆盖整个分区；``row_limit`` 只供单元测试显式限流，生产验收不得
    把固定前缀抽查表述为全量语义验收。
    """
    columns = [
        "drs", "drs_raw", "drs_raw_source", "track_status", "track_status_raw",
        "lap_start_raw", "lap_start_time", "sample_time", "absolute_time_source", "time",
    ]
    checked = 0
    for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=4096):
        values = {name: batch.column(name).to_pylist() for name in columns}
        lap_start_ns = batch.column("lap_start_time").cast(pa.int64()).to_pylist()
        sample_time_ns = batch.column("sample_time").cast(pa.int64()).to_pylist()
        for index in range(batch.num_rows):
            source = values["drs_raw_source"][index]
            raw = values["drs_raw"][index]
            normalized = values["drs"][index]
            if source is None and raw is not None:
                add_error(report, f"{label}: 无 raw 来源却存在 drs_raw")
                return
            if source not in {None, "tel.drs", "tel.drs_raw"}:
                add_error(report, f"{label}: 未知 drs_raw_source={source!r}")
                return
            if source == "tel.drs" and normalized is not None and raw != normalized:
                add_error(report, f"{label}: tel.drs 未原值保留")
                return
            if values["track_status"][index] != values["track_status_raw"][index]:
                add_error(report, f"{label}: track_status_raw 与源状态不一致")
                return

            absolute_source = values["absolute_time_source"][index]
            lap_start = lap_start_ns[index]
            sample_time = sample_time_ns[index]
            if absolute_source is None:
                if lap_start is not None or sample_time is not None:
                    add_error(report, f"{label}: 无绝对时间来源却写入时间戳")
                    return
            elif absolute_source == "laptimes.lSD+tel.time":
                if values["lap_start_raw"][index] is None or lap_start is None or sample_time is None:
                    add_error(report, f"{label}: 绝对时间来源链不完整")
                    return
                stored_time = float(values["time"][index])
                expected_offset_ns = round(stored_time * 1_000_000_000.0)
                observed_offset_ns = sample_time - lap_start
                tolerance_ns = float32_time_tolerance_ns(stored_time)
                if abs(observed_offset_ns - expected_offset_ns) > tolerance_ns:
                    add_error(
                        report,
                        f"{label}: sample_time 推导不一致 "
                        f"(误差={abs(observed_offset_ns - expected_offset_ns)}ns, "
                        f"float32容差={tolerance_ns}ns)",
                    )
                    return
            else:
                add_error(report, f"{label}: 未知 absolute_time_source={absolute_source!r}")
                return
            checked += 1
            if row_limit is not None and checked >= row_limit:
                report["v5_semantic_checks"].append({"file": label, "rows_checked": checked, "ok": True})
                return
    report["v5_semantic_checks"].append({"file": label, "rows_checked": checked, "ok": True})


def check_v5_year_audit(
    commit_dir: Path,
    manifest: dict[str, Any],
    report: dict[str, Any],
) -> None:
    label = f"{manifest.get('year')}/{commit_dir.name}"
    root_entry = manifest.get("value_domain_audit")
    if not isinstance(root_entry, dict):
        add_error(report, f"{label}: manifest 缺少 value_domain_audit")
        return
    root_path = commit_dir / str(root_entry.get("path", ""))
    if not root_path.is_file():
        add_error(report, f"{label}: 年度审计文件不存在")
        return
    if sha256_file(root_path) != root_entry.get("sha256"):
        add_error(report, f"{label}: 年度审计 SHA-256 不匹配")
        return
    try:
        recomputed = aggregate_partition_audits(
            manifest["partitions"],
            commit_dir,
            year=int(manifest["year"]),
            source_commit=str(manifest["source_commit"]),
        )
        stored = json.loads(root_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        add_error(report, f"{label}: 无法重算年度审计：{exc}")
        return
    if stored != recomputed:
        add_error(report, f"{label}: 年度审计与分区审计聚合不一致")
    if int(root_entry.get("rows", -1)) != int(manifest["totals"]["rows"]):
        add_error(report, f"{label}: 年度审计行数与 manifest 不一致")


def acceptance_check(
    root: Path,
    *,
    sample_partitions: int = 3,
    seed: int = 20260717,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "root": str(root.resolve()),
        "years": {},
        "schema_checks": [],
        "sort_checks": [],
        "sha_checks": [],
        "v5_semantic_checks": [],
        "warnings": [],
        "errors": [],
    }
    manifests = sorted(root.glob("year=*/commit=*/manifest.json"))
    if not manifests:
        add_error(report, f"未找到 manifest：{root}")
        report["verdict"] = "FAIL"
        return report

    rng = random.Random(seed)
    for manifest_path in manifests:
        commit_dir = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            add_error(report, f"无法读取 {manifest_path}: {exc}")
            continue
        schema_version = manifest.get("schema_version")
        if schema_version not in {SCHEMA_V4_VERSION, SCHEMA_V5_VERSION}:
            add_error(report, f"{manifest_path}: 不支持的 schema_version={schema_version!r}")
            continue
        expected_schema = schema_for(schema_version)
        year = str(manifest.get("year"))
        entries = manifest.get("partitions", [])
        disk_files = sorted(commit_dir.rglob("telemetry.parquet"))
        disk_rel = {path.relative_to(commit_dir).as_posix() for path in disk_files}
        manifest_rel = {str(item.get("parquet_path")) for item in entries}
        actual_rows = sum(pq.ParquetFile(path).metadata.num_rows for path in disk_files)
        actual_bytes = sum(path.stat().st_size for path in disk_files)
        totals = manifest.get("totals", {})
        if disk_rel != manifest_rel:
            add_error(report, f"{year}: manifest 与磁盘分区集合不一致")
        if int(totals.get("partitions", -1)) != len(disk_files):
            add_error(report, f"{year}: 分区总数不一致")
        if int(totals.get("rows", -1)) != actual_rows:
            add_error(report, f"{year}: 行数不一致")
        if int(totals.get("bytes", -1)) != actual_bytes:
            add_error(report, f"{year}: 字节数不一致")
        report["years"][year] = {
            "schema_version": schema_version,
            "partitions": len(disk_files),
            "rows": actual_rows,
            "bytes": actual_bytes,
        }

        if schema_version == SCHEMA_V5_VERSION:
            check_v5_year_audit(commit_dir, manifest, report)

        by_path = {str(item["parquet_path"]): item for item in entries}
        selected = disk_files
        if sample_partitions > 0 and len(selected) > sample_partitions:
            selected = rng.sample(selected, sample_partitions)
        for path in selected:
            rel = path.relative_to(commit_dir).as_posix()
            label = f"{year}/{rel}"
            entry = by_path.get(rel)
            if entry is None:
                continue
            digest = file_sha256(path)
            sha_ok = digest == entry.get("parquet_sha256")
            report["sha_checks"].append({"file": label, "ok": sha_ok})
            if not sha_ok:
                add_error(report, f"{label}: Parquet SHA-256 不匹配")
            parquet = pq.ParquetFile(path)
            schema_ok = parquet.schema_arrow.equals(expected_schema, check_metadata=True)
            float32_ok = all(parquet.schema_arrow.field(name).type == pa.float32() for name in FLOAT32_COLUMNS)
            raw_columns_ok = schema_version != SCHEMA_V5_VERSION or all(
                name in parquet.schema_arrow.names for name in V5_RAW_COLUMNS
            )
            report["schema_checks"].append(
                {"file": label, "schema_ok": schema_ok, "float32_ok": float32_ok, "raw_columns_ok": raw_columns_ok}
            )
            if not schema_ok or not float32_ok or not raw_columns_ok:
                add_error(report, f"{label}: schema 合同不匹配")
            sort_result = check_sorted(path)
            sort_result["file"] = label
            report["sort_checks"].append(sort_result)
            if not sort_result["ok"]:
                add_error(report, f"{label}: 排序违规")
            if schema_version == SCHEMA_V5_VERSION:
                audit_path = commit_dir / str(entry.get("value_domain_audit_path", ""))
                try:
                    audit = json.loads(audit_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    add_error(report, f"{label}: 无法读取分区审计：{exc}")
                    continue
                if sha256_file(audit_path) != entry.get("value_domain_audit_sha256"):
                    add_error(report, f"{label}: 分区审计 SHA-256 不匹配")
                if audit.get("parquet_sha256") != digest or int(audit.get("rows", -1)) != parquet.metadata.num_rows:
                    add_error(report, f"{label}: 分区审计未绑定当前 Parquet")
                check_column_audit(audit, parquet.schema_arrow, label, report)
                check_v5_sample_semantics(path, label, report)

    report["verdict"] = "PASS" if not report["errors"] else "FAIL"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--sample-partitions", type=int, default=3, help="每年抽查数；0 表示全部")
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    report = acceptance_check(
        args.root.resolve(),
        sample_partitions=args.sample_partitions,
        seed=args.seed,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(
        f"验收结论: {report['verdict']}；年份={len(report['years'])}；"
        f"错误={len(report['errors'])}；警告={len(report['warnings'])}"
    )
    for error in report["errors"]:
        print(f"  !! {error}")
    print(f"报告已写入 {args.report}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
