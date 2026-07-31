#!/usr/bin/env python3
"""离线生成追加式 2023–2025 Race Dossier v8 与赛季审计。"""

from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import unicodedata
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

# 在导入 NumPy/Pandas 前冻结数值库线程，避免桌面工作站被隐式 BLAS
# 线程池占满。命令仍以前台串行方式运行。
for _thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_thread_variable] = "1"

import numpy as np
import pandas as pd

import run_experiments as base
from modeling.race_by_race import telemetry_source_files
from modeling.race_dossier import (
    build_pre_race_tyre_envelope,
    build_race_dossier,
    build_season_audit,
    extract_race_lap_universe,
    validate_race_dossier_config,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "race_dossier_2023_2025_v8.json"
)


def _apply_low_resource_process_policy() -> None:
    """在 Windows 上将当前前台任务降为低于正常优先级。"""

    if os.name != "nt":
        return
    below_normal_priority_class = 0x00004000
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetPriorityClass.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    if not kernel32.SetPriorityClass(handle, below_normal_priority_class):
        raise OSError("无法设置 Race Dossier 进程为低于正常优先级")


def _project_path(value: str, label: str) -> Path:
    raw = Path(str(value))
    if raw.is_absolute():
        raise ValueError(f"{label} 必须是仓库根目录相对路径：{value}")
    resolved = (ROOT / raw).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"{label} 不能指向仓库外：{value}")
    return resolved


def _slug(value: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower() or "event"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"追加式产物禁止覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text_new(path: Path, value: str) -> None:
    if path.exists():
        raise FileExistsError(f"追加式产物禁止覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_gzip_csv_new(path: Path, frame: pd.DataFrame) -> None:
    if path.exists():
        raise FileExistsError(f"追加式产物禁止覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            compresslevel=9,
            mtime=0,
        ) as handle:
            frame.to_csv(handle, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(path)


def _identity(path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": base.file_sha256(path),
        "bytes": int(path.stat().st_size),
    }


def _stage_directory(final: Path, run_id: str) -> Path:
    if final.exists():
        raise FileExistsError(f"run_id 已存在，禁止覆盖：{final}")
    stage = final.parent / f".staging-{run_id}"
    if stage.exists():
        raise FileExistsError(f"存在同名未完成 staging，禁止覆盖：{stage}")
    stage.mkdir(parents=True, exist_ok=False)
    return stage


def _load_qualifying(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest_path = _project_path(
        config["inputs"]["qualifying_manifest"], "qualifying_manifest"
    )
    manifest = base.load_json(manifest_path)
    data_path = _project_path(manifest["file"]["path"], "qualifying_file")
    actual_hash = base.file_sha256(data_path)
    if actual_hash != manifest["file"]["sha256"]:
        raise RuntimeError("排位冻结文件 SHA-256 与 manifest 不一致")
    return pd.read_csv(data_path, compression="gzip"), {
        "manifest": _identity(manifest_path),
        "data": _identity(data_path),
        "coverage": manifest.get("coverage"),
        "boundaries": manifest.get("boundaries"),
    }


def _load_v5_index(
    config: Mapping[str, Any],
) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[str, Any]]:
    path = _project_path(
        config["inputs"]["race_by_race_v5_result"], "race_by_race_v5_result"
    )
    value = base.load_json(path)
    years = {int(year) for year in config["scope"]["years"]}
    index: dict[tuple[int, str], dict[str, Any]] = {}
    for event in value["events"]:
        year = int(event["year"])
        if year not in years:
            continue
        meeting = str(event["meeting_name"])
        index[(year, meeting)] = {
            "round": (
                int(event["round"])
                if event.get("round") is not None
                else None
            ),
            "classification_proxy": {
                str(row["team"]): int(row["classification_proxy_rank_four_team"])
                for row in event.get("cars", [])
                if row.get("classification_proxy_rank_four_team") is not None
            },
            "v5_data_status": event.get("data_status"),
        }
    return index, _identity(path)


def _source_identity(
    config: Mapping[str, Any],
) -> tuple[list[Path], list[dict[str, Any]]]:
    schema_root = _project_path(
        config["inputs"]["telemetry_schema_root"], "telemetry_schema_root"
    )
    files, sources = telemetry_source_files(
        schema_root,
        [int(year) for year in config["scope"]["years"]],
        str(config["scope"]["session"]),
    )
    selected_paths = {path.resolve() for path in files}
    declared_paths: set[Path] = set()
    declared_identities: list[str] = []
    enriched_sources: list[dict[str, Any]] = []
    for source in sources:
        manifest_path = schema_root / str(source["manifest"])
        manifest = base.load_json(manifest_path)
        selected = [
            row
            for row in manifest["partitions"]
            if row["session"] == str(config["scope"]["session"])
        ]
        partition_identities: list[dict[str, Any]] = []
        for row in selected:
            parquet_path = (manifest_path.parent / row["parquet_path"]).resolve()
            if not parquet_path.is_file():
                raise FileNotFoundError(parquet_path)
            declared_size = int(row["parquet_bytes"])
            actual_size = int(parquet_path.stat().st_size)
            if actual_size != declared_size:
                raise RuntimeError(
                    f"规范 Parquet 大小与冻结 manifest 不一致：{parquet_path}"
                )
            declared_hash = str(row["parquet_sha256"])
            relative_path = parquet_path.relative_to(ROOT).as_posix()
            declared_paths.add(parquet_path)
            declared_identities.append(
                f"{relative_path}|{declared_size}|{declared_hash}"
            )
            partition_identities.append(
                {
                    "meeting": row["meeting"],
                    "path": relative_path,
                    "bytes": declared_size,
                    "manifest_declared_sha256": declared_hash,
                }
            )
        enriched_sources.append(
            {
                **source,
                "manifest_identity": _identity(manifest_path),
                "partition_identity_mode": (
                    "frozen_manifest_sha256_with_current_size_verification"
                ),
                "partitions": partition_identities,
            }
        )
    if declared_paths != selected_paths:
        missing = sorted(str(path) for path in selected_paths - declared_paths)
        unexpected = sorted(str(path) for path in declared_paths - selected_paths)
        raise RuntimeError(
            "规范 manifest 与所选 Parquet 集合不一致："
            f"missing={missing}, unexpected={unexpected}"
        )
    file_set_hash = hashlib.sha256(
        "\n".join(sorted(declared_identities)).encode("utf-8")
    ).hexdigest()
    return files, [
        *enriched_sources,
        {
            "selected_file_count": len(files),
            "selected_file_set_sha256": file_set_hash,
            "identity_mode": (
                "frozen_manifest_sha256_with_current_size_verification"
            ),
        },
    ]


def _legacy_v7_audit() -> dict[str, Any]:
    registry_path = ROOT / "research" / "configs" / "pac_post_freeze_event_registry_v2.json"
    australia_path = (
        ROOT
        / "research"
        / "records"
        / "telemetry_explanation_2026_australia_ferrari_v7.json"
    )
    batch_config_path = (
        ROOT
        / "research"
        / "configs"
        / "telemetry_explanation_2026_ferrari_four_event_v7.json"
    )
    batch_result_path = (
        ROOT
        / "research"
        / "records"
        / "telemetry_explanation_2026_ferrari_four_event_v7.json"
    )
    audit: dict[str, Any] = {
        "role": "audit_only_not_v8_input",
        "v7_outputs_immutable": True,
    }
    if registry_path.is_file() and australia_path.is_file():
        registry = base.load_json(registry_path)
        registered = None
        for row in registry.get("events", []):
            serialized = json.dumps(row, ensure_ascii=False).lower()
            if "australia" in serialized:
                registered = (
                    row.get("report_sha256")
                    or row.get("sha256")
                    or row.get("data_sha256")
                )
                break
        actual = base.file_sha256(australia_path)
        audit["australia_report"] = {
            "registered_sha256": registered,
            "current_sha256": actual,
            "identity_matches": registered == actual if registered else None,
        }
    if batch_config_path.is_file() and batch_result_path.is_file():
        batch_config = base.load_json(batch_config_path)
        batch_result = base.load_json(batch_result_path)
        current_config_hash = base.canonical_hash(batch_config)
        recorded_config_hash = batch_result.get("config_hash")
        audit["four_event_batch"] = {
            "current_config_hash": current_config_hash,
            "recorded_result_config_hash": recorded_config_hash,
            "identity_matches": current_config_hash == recorded_config_hash,
            "current_required_events": batch_config.get(
                "confirmatory_event_threshold"
            ),
            "recorded_required_events": batch_result.get(
                "confirmatory_exposure_gate", {}
            ).get("required_events"),
        }
    return audit


def _historical_stints_for_next_event(
    report: Mapping[str, Any],
    *,
    event_order: int,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mode = str(
        config["strategy_envelope"].get(
            "history_admission",
            "whole_event_publication_only",
        )
    )
    if mode == "whole_event_publication_only":
        admitted = bool(report["publication_gate"]["passed"])
    elif mode == "individually_valid_stint_and_event_ledger_closure":
        admitted = bool(
            report["event_ledger"]["primary_disposition_closure"]["passed"]
        )
    else:
        raise ValueError(f"未知 history_admission：{mode}")
    if int(str(config.get("method_version", "v8")).removeprefix("v")) >= 15:
        admitted = bool(
            admitted
            and report.get("inclusive_robust_model_audit", {}).get("status")
            == "accepted_crossfit_proxy"
        )
    if not admitted:
        return []
    pit_cycle = report.get("pit_cycle_loss_proxy", {})
    pit_summary = pit_cycle.get("cycle_loss_s") or {}
    pit_value = pit_summary.get("median")
    event_context = (
        {"event_pit_cycle_loss_proxy_s": float(pit_value)}
        if pit_value is not None
        else {}
    )
    return [
        {
            **dict(row),
            "event_order": int(event_order),
            **event_context,
        }
        for row in report["stint_dossiers"]
        if row["status"] == "valid"
    ]


def _event_summary(
    report: Mapping[str, Any], *, method_version: str = "v8"
) -> str:
    scope = report["scope"]
    gate = report["publication_gate"]
    coverage = report["coverage"]
    q_rows = report["qualifying_analysis"]["team_order"]
    r_rows = (
        report["race_analysis"].get("published_vehicle_ranking")
        or report["race_analysis"]["descriptive_team_order"]
    )
    pace_fraction = coverage.get(
        "pace_adjusted_reporting_lap_fraction",
        coverage["modelable_reporting_lap_fraction"],
    )
    lines = [
        f"# {scope['year']} {scope['meeting']} Race Dossier {method_version}",
        "",
        f"- 发布状态：`{gate['status']}`；整场门槛：{'通过' if gate['passed'] else '未通过'}。",
        (
            f"- 全场观测圈：{coverage['observed_field_laps']}；四队观测圈："
            f"{coverage['observed_reporting_team_laps']}；条件代理可用比例："
            f"{pace_fraction:.1%}。"
        ),
        "- PAC/OVR：`null`。排位与正赛独立，不输出统一周末排名。",
        "",
        "## 独立车辆视图",
        "",
        f"- 排位潜力代理第一：{q_rows[0]['team'] if q_rows else '不可用'}。",
        f"- 正赛长距离代理第一：{r_rows[0]['team'] if r_rows else '不可用'}。",
        "",
        "## 发布门槛",
        "",
    ]
    if gate["reasons"]:
        lines.extend(f"- `{reason}`" for reason in gate["reasons"])
    else:
        lines.append("- 全场覆盖、有效 stint 和账本闭合门槛均通过。")
    if gate.get("partial_team_conclusion_allowed"):
        lines.append(
            "- 独立通过并允许部分发布的车队："
            + "、".join(gate.get("publishable_teams", []))
            + "；不得外推为四队全序。"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 轮胎长度、衰减和进站窗口均为公开数据代理，不是真实燃油或倍耐力内部模拟。",
            "- 累计秒差没有 FIA 最终相邻车辆时间差支持时，不称最终名次主要因素。",
            "- 单场保胎表现只称车手—赛车组合代理。",
        ]
    )
    return "\n".join(lines)


def _season_summary(
    report: Mapping[str, Any], year: int, *, method_version: str = "v8"
) -> str:
    q = [row for row in report["qualifying_performance_curve"] if row["year"] == year]
    race = [row for row in report["race_performance_curve"] if row["year"] == year]
    return "\n".join(
        [
            f"# {year} 赛季审计 {method_version}",
            "",
            f"- 排位曲线观测：{len(q)} 个 team-event。",
            f"- 正赛曲线观测：{len(race)} 个 team-event。",
            "- 两条曲线独立；没有统一周末排名。",
            "- 实际结果只使用 `classification_proxy` 审计，不是 FIA 最终分类。",
            "- 期望积分、期望锦标赛排名、PAC 与 OVR 均保持 `null`。",
        ]
    )


def _master_summary(
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    seasons: Sequence[Mapping[str, Any]],
    method_version: str = "v8",
) -> str:
    passed = sum(bool(row["publication_gate_passed"]) for row in events)
    partial = sum(
        row.get("release_status") == "partial_team_modules_publishable"
        for row in events
    )
    return "\n".join(
        [
            f"# Race Dossier {method_version} 运行摘要",
            "",
            f"- run_id：`{run_id}`。",
            f"- 逐场 Dossier：{len(events)}；通过整场发布门槛：{passed}。",
            f"- 允许部分车队模块发布：{partial}；部分结果不代表完整四队全序。",
            f"- 赛季审计：{len(seasons)}。",
            "- 所有输出均为追加式路径；旧 v1–v7 产物未覆盖。",
            "- PAC/OVR、期望积分与期望锦标赛排名保持 `null`。",
        ]
    )


def run(
    config: Mapping[str, Any],
    *,
    run_id: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    validate_race_dossier_config(config)
    if not bool(config.get("offline_only")):
        raise ValueError("Race Dossier runner 必须 offline_only=true")
    method_version = str(config.get("method_version", "v8"))
    config_hash = base.canonical_hash(config)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    resolved_run_id = run_id or f"{timestamp}-{config_hash[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", resolved_run_id):
        raise ValueError("run_id 只能包含字母、数字、点、下划线、加号和连字符")

    records_base = _project_path(
        config["outputs"]["records_base"], "records_base"
    )
    artifacts_base = _project_path(
        config["outputs"]["artifacts_base"], "artifacts_base"
    )
    frontend_base = _project_path(
        config["outputs"]["frontend_base"], "frontend_base"
    )
    records_final = records_base / f"run={resolved_run_id}"
    artifacts_final = artifacts_base / f"run={resolved_run_id}"
    frontend_final = frontend_base / f"run={resolved_run_id}"
    records_stage = _stage_directory(records_final, resolved_run_id)
    artifacts_stage = _stage_directory(artifacts_final, resolved_run_id)
    frontend_stage = _stage_directory(frontend_final, resolved_run_id)

    config_path = config_path.resolve()
    input_identities: dict[str, Any] = {
        "config": _identity(config_path) if config_path.is_file() else None
    }
    if int(method_version.removeprefix("v")) >= 15:
        implementation_paths = [
            ROOT / "research" / "modeling" / "race_dossier.py",
            ROOT / "research" / "modeling" / "inclusive_robust.py",
            ROOT / "research" / "run_race_dossiers_v8.py",
            ROOT
            / "research"
            / f"run_race_dossiers_{method_version}.py",
            ROOT
            / "tools"
            / f"run_race_dossier_{method_version}_low_resource.ps1",
        ]
        input_identities["implementation"] = [
            _identity(path) for path in implementation_paths if path.is_file()
        ]
    for key in (
        "event_stint_v4_result",
        "explanatory_depth_v6_result",
        "pac_v1_result",
        "pac_v2_result",
    ):
        path = _project_path(config["inputs"][key], key)
        input_identities[key] = _identity(path)

    qualifying, qualifying_identity = _load_qualifying(config)
    input_identities["qualifying"] = qualifying_identity
    v5_index, v5_identity = _load_v5_index(config)
    input_identities["race_by_race_v5_result"] = v5_identity
    cache_root = _project_path(
        config["runtime"]["project_cache"], "project_cache"
    )
    reused_manifest_value = config["inputs"].get("lap_universe_manifest")
    if reused_manifest_value:
        reused_manifest_path = _project_path(
            reused_manifest_value, "lap_universe_manifest"
        )
        reused_manifest = base.load_json(reused_manifest_path)
        reused_data_path = _project_path(
            reused_manifest["file"]["path"], "lap_universe_file"
        )
        actual_reused_hash = base.file_sha256(reused_data_path)
        if actual_reused_hash != reused_manifest["file"]["sha256"]:
            raise RuntimeError("复用的逐圈宇宙 SHA-256 与 manifest 不一致")
        lap_universe = pd.read_csv(reused_data_path, compression="gzip")
        telemetry_sources = list(reused_manifest["sources"])
        input_identities["lap_universe_reuse"] = {
            "manifest": _identity(reused_manifest_path),
            "data": _identity(reused_data_path),
            "source_run_id": reused_manifest["run_id"],
            "identity_verified": True,
        }
        input_identities["telemetry_sources"] = telemetry_sources
    else:
        telemetry_files, telemetry_sources = _source_identity(config)
        input_identities["telemetry_sources"] = telemetry_sources
        lap_universe = extract_race_lap_universe(
            telemetry_files,
            duckdb_temp_directory=cache_root / "duckdb",
            threads=int(config["runtime"]["duckdb_threads"]),
            file_batch_size=int(config["runtime"]["file_batch_size"]),
            memory_limit=str(config["runtime"]["duckdb_memory_limit"]),
            batch_cooldown_seconds=float(
                config["runtime"]["batch_cooldown_seconds"]
            ),
            progress_callback=lambda done, total: print(
                f"[lap-universe] {done}/{total} batches", flush=True
            ),
        )
    event_count = int(
        lap_universe[["year", "meeting_name"]].drop_duplicates().shape[0]
    )
    if event_count != int(config["scope"]["expected_events"]):
        raise RuntimeError(
            f"全场圈宇宙分站数 {event_count} != 配置 {config['scope']['expected_events']}"
        )
    lap_path_stage = artifacts_stage / "race_lap_universe.csv.gz"
    _write_gzip_csv_new(lap_path_stage, lap_universe)

    artifact_manifest = {
        "schema_version": f"race-lap-universe-{method_version}",
        "run_id": resolved_run_id,
        "created_at": base.now_iso(),
        "config_hash": config_hash,
        "sources": telemetry_sources,
        "coverage": {
            "rows": int(len(lap_universe)),
            "events": event_count,
            "years": sorted(
                int(value) for value in lap_universe["year"].unique()
            ),
            "drivers": int(lap_universe["driver_id"].nunique()),
        },
        "file": {
            "path": (
                artifacts_final / "race_lap_universe.csv.gz"
            ).relative_to(ROOT).as_posix(),
            "sha256": base.file_sha256(lap_path_stage),
            "bytes": int(lap_path_stage.stat().st_size),
            "columns": lap_universe.columns.tolist(),
        },
    }
    _write_json_new(artifacts_stage / "manifest.json", artifact_manifest)

    event_keys = list(
        lap_universe[["year", "meeting_name"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    fallback_order = {
        key: index + 1
        for index, key in enumerate(sorted(event_keys), start=1)
    }
    ordered_events = sorted(
        event_keys,
        key=lambda key: (
            int(key[0]),
            (
                v5_index.get((int(key[0]), str(key[1])), {}).get("round")
                or fallback_order[key]
            ),
            str(key[1]),
        ),
    )

    historical_stints: list[dict[str, Any]] = []
    dossiers: list[dict[str, Any]] = []
    event_manifest: list[dict[str, Any]] = []
    frontend_rows: list[dict[str, Any]] = []
    for fallback_index, raw_key in enumerate(ordered_events, start=1):
        year, meeting = int(raw_key[0]), str(raw_key[1])
        v5 = v5_index.get((year, meeting), {})
        round_number = int(v5.get("round") or fallback_index)
        event_order = year * 100 + round_number
        event_laps = lap_universe.loc[
            lap_universe["year"].eq(year)
            & lap_universe["meeting_name"].eq(meeting)
        ].copy()
        event_qualifying = qualifying.loc[
            qualifying["year"].eq(year)
            & qualifying["meeting_name"].eq(meeting)
        ].copy()
        report_bundle = build_race_dossier(
            event_laps,
            event_qualifying,
            config,
            classification_proxy=v5.get("classification_proxy"),
            return_curve_sidecar=bool(
                config.get("stint_curve_evidence", {}).get("enabled")
            ),
        )
        if isinstance(report_bundle, tuple):
            report, curve_sidecar = report_bundle
        else:
            report, curve_sidecar = report_bundle, None
        report["run_id"] = resolved_run_id
        report["created_at"] = base.now_iso()
        report["config_hash"] = config_hash
        report["round"] = round_number
        report["event_order"] = event_order
        report["source_identity"] = {
            "lap_universe_manifest": (
                artifacts_final / "manifest.json"
            ).relative_to(ROOT).as_posix(),
            "qualifying_manifest_sha256": qualifying_identity["manifest"][
                "sha256"
            ],
            "v5_result_sha256": v5_identity["sha256"],
        }
        report["pre_race_tyre_envelope"] = build_pre_race_tyre_envelope(
            historical_stints,
            target_event_order=event_order,
            target_race_laps=int(report["coverage"]["race_lap_span"]),
            target_event_id=f"{year}|{meeting}",
            config=config,
        )
        dossiers.append(report)
        historical_stints.extend(
            _historical_stints_for_next_event(
                report,
                event_order=event_order,
                config=config,
            )
        )

        event_slug = _slug(meeting)
        relative = (
            Path(f"year={year}")
            / f"round={round_number:02d}"
            / f"meeting={event_slug}"
        )
        curve_sidecar_manifest = None
        if curve_sidecar is not None:
            curve_sidecar.update(
                {
                    "run_id": resolved_run_id,
                    "created_at": base.now_iso(),
                    "config_hash": config_hash,
                    "source_identity": {
                        "run_lap_universe_manifest": (
                            artifacts_final / "manifest.json"
                        ).relative_to(ROOT).as_posix(),
                        "lap_universe_sha256": artifact_manifest[
                            "file"
                        ]["sha256"],
                        "input_lap_universe_reuse": input_identities.get(
                            "lap_universe_reuse"
                        ),
                        "qualifying_manifest_sha256": qualifying_identity[
                            "manifest"
                        ]["sha256"],
                        "implementation": input_identities.get(
                            "implementation", []
                        ),
                    },
                }
            )
            curve_sidecar_stage = (
                records_stage / relative / "stint_curve_evidence.json"
            )
            _write_json_new(curve_sidecar_stage, curve_sidecar)
            curve_sidecar_final = (
                records_final / relative / "stint_curve_evidence.json"
            )
            curve_sidecar_manifest = {
                "status": "available",
                "schema_version": curve_sidecar["schema_version"],
                "path": curve_sidecar_final.relative_to(ROOT).as_posix(),
                "sha256": base.file_sha256(curve_sidecar_stage),
                "bytes": int(curve_sidecar_stage.stat().st_size),
                "stints": int(curve_sidecar["coverage"]["stints"]),
                "points": int(curve_sidecar["coverage"]["points"]),
                "fit_points": int(
                    curve_sidecar["coverage"]["fit_points"]
                ),
                "range_supported_stints": int(
                    curve_sidecar["coverage"]["range_supported_stints"]
                ),
                "stability_estimated_stints": int(
                    curve_sidecar["coverage"]["stability_estimated_stints"]
                ),
                "pairwise_comparisons": int(
                    curve_sidecar["coverage"]["pairwise_comparisons"]
                ),
                "directly_comparable_pairs": int(
                    curve_sidecar["coverage"]["directly_comparable_pairs"]
                ),
                "balance_warning_pairs": int(
                    curve_sidecar["coverage"]["balance_warning_pairs"]
                ),
                "not_comparable_pairs": int(
                    curve_sidecar["coverage"]["not_comparable_pairs"]
                ),
            }
            report["stint_curve_evidence"] = dict(
                curve_sidecar_manifest
            )
        event_json_stage = records_stage / relative / "race_dossier.json"
        event_md_stage = records_stage / relative / "race_dossier.md"
        _write_json_new(event_json_stage, report)
        _write_text_new(
            event_md_stage,
            _event_summary(report, method_version=method_version),
        )
        event_json_final = records_final / relative / "race_dossier.json"
        report_hash = base.file_sha256(event_json_stage)
        event_manifest.append(
            {
                "year": year,
                "round": round_number,
                "meeting": meeting,
                "event_order": event_order,
                "path": event_json_final.relative_to(ROOT).as_posix(),
                "sha256": report_hash,
                "bytes": int(event_json_stage.stat().st_size),
                "release_status": report["release_status"],
                "publication_gate_passed": bool(
                    report["publication_gate"]["passed"]
                ),
                "stint_curve_evidence": curve_sidecar_manifest,
            }
        )

        frontend_name = f"{year}-{round_number:02d}-{event_slug}.json"
        frontend_report_stage = frontend_stage / "reports" / frontend_name
        frontend_report = deepcopy(report)
        frontend_curve_manifest = None
        if curve_sidecar is not None and curve_sidecar_manifest is not None:
            frontend_curve_name = (
                f"{year}-{round_number:02d}-{event_slug}.json"
            )
            frontend_curve_stage = (
                frontend_stage
                / "curve-evidence"
                / frontend_curve_name
            )
            _write_json_new(frontend_curve_stage, curve_sidecar)
            frontend_curve_manifest = {
                **curve_sidecar_manifest,
                "frontend_path": (
                    Path("curve-evidence") / frontend_curve_name
                ).as_posix(),
                "frontend_sha256": base.file_sha256(
                    frontend_curve_stage
                ),
            }
            frontend_report["stint_curve_evidence"] = dict(
                frontend_curve_manifest
            )
        _write_json_new(frontend_report_stage, frontend_report)
        frontend_rows.append(
            {
                "id": report["report_id"],
                "title": f"{year} · R{round_number:02d} · {meeting}",
                "path": f"reports/{frontend_name}",
                "year": year,
                "meeting": meeting,
                "session": "Race",
                "team": "Four-team dossier",
                "release_status": report["release_status"],
                "source_path": event_json_final.relative_to(ROOT).as_posix(),
                "source_sha256": report_hash,
                "export_sha256": base.file_sha256(frontend_report_stage),
                "stint_curve_evidence": frontend_curve_manifest,
            }
        )
        print(
            (
                f"[race-dossier] {fallback_index}/{len(ordered_events)} "
                f"{year} {_slug(meeting)} - {report['release_status']}"
            ),
            flush=True,
        )

    season_manifest: list[dict[str, Any]] = []
    for year in sorted(int(value) for value in config["scope"]["years"]):
        season_events = [
            dossier for dossier in dossiers if int(dossier["scope"]["year"]) == year
        ]
        audit = build_season_audit(season_events, config)
        audit.update(
            {
                "report_id": f"season_audit_{year}_{method_version}",
                "run_id": resolved_run_id,
                "created_at": base.now_iso(),
                "config_hash": config_hash,
                "scope": {
                    "year": year,
                    "meeting": "Season audit",
                    "session": "Race",
                    "team": " / ".join(config["scope"]["reporting_teams"]),
                },
                "release_status": "retrospective_season_audit",
            }
        )
        season_path_stage = (
            records_stage / f"year={year}" / "season_audit.json"
        )
        season_md_stage = records_stage / f"year={year}" / "season_audit.md"
        _write_json_new(season_path_stage, audit)
        _write_text_new(
            season_md_stage,
            _season_summary(audit, year, method_version=method_version),
        )
        season_path_final = records_final / f"year={year}" / "season_audit.json"
        season_manifest.append(
            {
                "year": year,
                "path": season_path_final.relative_to(ROOT).as_posix(),
                "sha256": base.file_sha256(season_path_stage),
                "bytes": int(season_path_stage.stat().st_size),
            }
        )

    manifest = {
        "schema_version": f"race-dossier-run-manifest-{method_version}",
        "run_id": resolved_run_id,
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config_hash": config_hash,
        "methodological_status": config["validation_status"],
        "offline_only": True,
        "network_calls": [],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "project_cache": config["runtime"]["project_cache"],
        },
        "input_identities": input_identities,
        "historical_v7_identity_audit": _legacy_v7_audit(),
        "coverage": {
            "events": len(event_manifest),
            "publication_gate_passed": sum(
                row["publication_gate_passed"] for row in event_manifest
            ),
            "partial_team_modules_publishable": sum(
                row["release_status"]
                == "partial_team_modules_publishable"
                for row in event_manifest
            ),
            "audit_only": sum(
                row["release_status"] == "audit_only"
                for row in event_manifest
            ),
            "seasons": len(season_manifest),
            "stint_curve_sidecars": sum(
                row.get("stint_curve_evidence") is not None
                for row in event_manifest
            ),
            "stint_curve_points": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "points", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_fit_points": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "fit_points", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_range_supported_stints": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "range_supported_stints", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_stability_estimated_stints": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "stability_estimated_stints", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_pairwise_comparisons": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "pairwise_comparisons", 0
                    )
                )
                for row in event_manifest
            ),
            "directly_comparable_stint_pairs": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "directly_comparable_pairs", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_balance_warning_pairs": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "balance_warning_pairs", 0
                    )
                )
                for row in event_manifest
            ),
            "stint_curve_not_comparable_pairs": sum(
                int(
                    (row.get("stint_curve_evidence") or {}).get(
                        "not_comparable_pairs", 0
                    )
                )
                for row in event_manifest
            ),
        },
        "events": event_manifest,
        "season_audits": season_manifest,
        "artifact_manifest": (
            artifacts_final / "manifest.json"
        ).relative_to(ROOT).as_posix(),
        "publication_contract": {
            **dict(config["publication_contract"]),
            "PAC": None,
            "OVR": None,
            "expected_points_interval": None,
            "expected_championship_rank_interval": None,
        },
    }
    _write_json_new(records_stage / "manifest.json", manifest)
    _write_text_new(
        records_stage / "README.md",
        _master_summary(
            run_id=resolved_run_id,
            events=event_manifest,
            seasons=season_manifest,
            method_version=method_version,
        ),
    )
    _write_json_new(records_stage / "config.snapshot.json", dict(config))
    frontend_manifest = {
        "version": f"race-dossier-{method_version}",
        "run_id": resolved_run_id,
        "generated_at": base.now_iso(),
        "source_manifest": (
            records_final / "manifest.json"
        ).relative_to(ROOT).as_posix(),
        "reports": sorted(
            frontend_rows,
            key=lambda row: (int(row["year"]), row["title"]),
        ),
    }
    _write_json_new(frontend_stage / "manifest.json", frontend_manifest)

    # 三个 stage 全部写完后才发布最终不可变目录；任何一步失败均不会覆盖旧 run。
    artifacts_stage.replace(artifacts_final)
    records_stage.replace(records_final)
    frontend_stage.replace(frontend_final)
    return {
        "run_id": resolved_run_id,
        "records": records_final.relative_to(ROOT).as_posix(),
        "artifacts": artifacts_final.relative_to(ROOT).as_posix(),
        "frontend": frontend_final.relative_to(ROOT).as_posix(),
        "coverage": manifest["coverage"],
        "manifest_sha256": base.file_sha256(records_final / "manifest.json"),
    }


def main() -> int:
    _apply_low_resource_process_policy()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-id")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    if not config_path.is_relative_to(ROOT):
        parser.error("--config 必须位于项目目录内")
    config = base.load_json(config_path)
    result = run(config, run_id=args.run_id, config_path=config_path)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
