#!/usr/bin/env python3
"""运行 2023–2025 四队单场—stint—圈级反事实解释 v4。"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import run_experiments as base
from modeling.bayesian_counterfactual import (
    build_context_adjusted_aggregates,
    posterior_diagnostics,
    sample_posterior,
)
from modeling.event_stint_attribution import (
    build_event_reports,
    build_stint_features,
    enrich_qualifying_laps,
    enrich_race_laps,
    validate_event_stint_config,
)
from modeling.seasonal_attribution import (
    CONTEXT_BLOCKS,
    fit_seasonal_model,
    local_live_timing_reference,
    prepare_season_frame,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "four_team_2023_2025_event_stint_v4.json"
)


def _selected_penalties(parent: Mapping[str, Any]) -> dict[str, float]:
    selected = next(
        row for row in parent["candidate_penalties"] if row["id"] == "balanced_v1"
    )
    return {key: float(value) for key, value in selected.items() if key != "id"}


def _load_race_inputs(parent: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in parent["input_files"]:
        path = ROOT / item["path"]
        actual_hash = base.file_sha256(path)
        if actual_hash != item["sha256"]:
            raise RuntimeError(f"冻结 Race 文件哈希不一致：{item['path']}")
        frame = pd.read_csv(path, compression="gzip")
        frames.append(frame)
        identity.append(
            {
                "year": int(item["year"]),
                "path": item["path"],
                "sha256": actual_hash,
                "rows_before_race_filter": int(len(frame)),
            }
        )
    return pd.concat(frames, ignore_index=True), identity


def _load_qualifying(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = config["qualifying_freeze"]
    manifest_path = ROOT / settings["manifest"]
    if not manifest_path.is_file():
        raise FileNotFoundError(
            "缺少排位冻结 manifest；先运行 python research/build_qualifying_frozen_data.py"
        )
    manifest = base.load_json(manifest_path)
    path = ROOT / manifest["file"]["path"]
    actual_hash = base.file_sha256(path)
    if actual_hash != manifest["file"]["sha256"]:
        raise RuntimeError("排位冻结文件哈希与 manifest 不一致。")
    return pd.read_csv(path, compression="gzip"), manifest


def _posterior_settings(
    identity_config: Mapping[str, Any], quick: bool
) -> dict[str, Any]:
    settings = copy.deepcopy(identity_config["posterior"])
    if quick:
        settings.update({"chains": 2, "iterations": 300, "warmup": 150, "thin": 3})
    settings["random_seed"] = int(settings["random_seed"]) + 9001
    return settings


def _write_deterministic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0
        ) as gzip_handle:
            frame.to_csv(
                gzip_handle, index=False, encoding="utf-8", lineterminator="\n"
            )
    temporary.replace(path)


def _lap_detail_columns() -> list[str]:
    return [
        "year",
        "meeting_name",
        "circuit_name",
        "session_name",
        "driver_id",
        "team_name",
        "lap_number",
        "stint_number",
        "compound",
        "tyre_age",
        "lap_duration",
        "session_reference",
        "target_log_pct",
        "context_prediction_z",
        "posterior_intercept_z",
        "car_state_z",
        "driver_state_z",
        "interaction_state_z",
        "identity_expected_z",
        "expected_target_z",
        "expected_lap_seconds",
        "model_residual_z",
        "model_residual_ms",
        "driver_neutral_vehicle_z",
        "driver_event_frontier_residual_ms",
        "execution_loss_ms",
        "residual_percentile_within_driver_event",
        "source_commit",
    ]


def _write_summary(result: Mapping[str, Any], path: Path) -> None:
    diagnostics = result["posterior_diagnostics"]
    lines = [
        "# 2023–2025 四队单场—stint—圈级反事实解释 v4",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        f"- Race 清洗圈：{result['coverage']['race_clean_laps']}；四队圈级解释：{result['coverage']['four_team_lap_rows']}；stint：{result['coverage']['stints']}；分站：{result['coverage']['events']}。",
        f"- Qualifying accurate 推圈：{result['coverage']['qualifying_accurate_laps']}；覆盖 {result['coverage']['qualifying_events']} 场。",
        f"- 身份骨架后验：{diagnostics['chains']} 链 × {diagnostics['draws_per_chain']}；最大尺度 R-hat={diagnostics['maximum_scale_rhat']:.3f}。",
        "- 周末车辆上沿只融合排位/正赛的相对优势，不融合绝对圈时；公开数据无法验证两辆车的具体 setup、升级和损伤完全相同。",
        "",
        "## 2025 每站周末车辆统计上沿",
        "",
        "| 分站 | 上沿第一 | 第二 | 第三 | 第四 | 分类代理一致性 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for event in result["events"]:
        if int(event["year"]) != 2025:
            continue
        cars = sorted(event["cars"], key=lambda row: row["weekend_ceiling_rank"])
        alignments = [row["result_alignment"] for row in cars]
        compatible = sum(value == "compatible_with_clean_pace" for value in alignments)
        lines.append(
            f"| {event['meeting_name']} | {cars[0]['team']} | {cars[1]['team']} | "
            f"{cars[2]['team']} | {cars[3]['team']} | {compatible}/4 配速相容 |"
        )
    lines.extend(
        [
            "",
            "## 输出如何使用",
            "",
            "- `events[].cars`：每站纯车辆后验、Race/Qualifying 条件前沿、周末上沿排名、区间和分类代理一致性。",
            "- `events[].drivers`：每站纯车手后验、实际干净圈排名、模型组合排名、执行损失和赛果相容性。",
            "- `stints[]`：每个车手每个 stint 的实跑/期望圈速、执行损失、稳定性和相对上下文的超额衰退斜率。",
            f"- 圈级可追溯文件：`{result['lap_detail']['path']}`；其 SHA-256 为 `{result['lap_detail']['sha256']}`。",
            "- 这是 2025 已查看后的回顾性描述扩展，不把同场解释性能冒充赛前预测准确率。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quick", action="store_true", help="仅烟雾测试，不写正式产物")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = base.load_json(args.config)
    validate_event_stint_config(config)
    if not config.get("offline_only"):
        raise RuntimeError("本入口只允许离线配置。")
    parent = base.load_json(ROOT / config["parent_config"])
    identity_config = base.load_json(ROOT / config["identity_model_config"])
    aliases = base.load_json(ROOT / parent["entity_alias_config"])["team_aliases"]

    raw_race, race_identity = _load_race_inputs(parent)
    race = prepare_season_frame(
        raw_race, aliases, parent["season_order"], parent["session_name"]
    )
    penalties = _selected_penalties(parent)
    aggregate, race_context_fit = build_context_adjusted_aggregates(
        race,
        penalties,
        parent["robust_fit"],
        identity_config["aggregation"],
    )
    posterior = sample_posterior(
        aggregate, _posterior_settings(identity_config, args.quick)
    )
    race_context_prediction = race_context_fit.predict(race)
    race_detail = enrich_race_laps(
        race,
        race_context_prediction,
        posterior,
        float(config["frontier"]["race_ceiling_quantile"]),
    )
    race_detail = race_detail.loc[
        race_detail["team_name"].isin(config["reporting_teams"])
    ].reset_index(drop=True)

    raw_qualifying, qualifying_manifest = _load_qualifying(config)
    qualifying = prepare_season_frame(
        raw_qualifying, aliases, parent["season_order"], "Qualifying"
    )
    qualifying_context_fit = fit_seasonal_model(
        qualifying,
        penalties,
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(parent["robust_fit"]["irls_iterations"]),
        huber_k=float(parent["robust_fit"]["huber_k"]),
    )
    qualifying_detail = enrich_qualifying_laps(
        qualifying, qualifying_context_fit.predict(qualifying), posterior
    )
    qualifying_detail = qualifying_detail.loc[
        qualifying_detail["team_name"].isin(config["reporting_teams"])
    ].reset_index(drop=True)

    references = [
        local_live_timing_reference(
            year,
            ROOT / parent["local_result_reference"]["root_template"].format(year=year),
            aliases,
            sessions=("Race",),
        )
        for year in (2023, 2024, 2025)
    ]
    events = build_event_reports(
        race_detail,
        qualifying_detail,
        posterior,
        config["reporting_teams"],
        config,
        references,
    )
    stints = build_stint_features(race_detail, config)

    outputs = config["outputs"]
    lap_path = ROOT / outputs["lap_detail"]
    lap_frame = race_detail[_lap_detail_columns()].copy()
    if not args.quick:
        _write_deterministic_csv(lap_frame, lap_path)
        lap_hash = base.file_sha256(lap_path)
    else:
        lap_hash = base.canonical_hash(
            lap_frame.head(100).where(pd.notna(lap_frame.head(100)), None).to_dict("records")
        )
    diagnostics = posterior_diagnostics(posterior)
    result = {
        "run_id": f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "methodological_status": config["validation_status"],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "sampler": "v3_custom_conjugate_hierarchical_gibbs_identity_backbone",
        },
        "network_calls": [],
        "coverage": {
            "race_clean_laps": int(len(race)),
            "driver_event_rows": int(len(aggregate)),
            "four_team_lap_rows": int(len(race_detail)),
            "qualifying_accurate_laps": int(len(qualifying_detail)),
            "qualifying_events": int(qualifying_detail["meeting_key"].nunique()),
            "events": int(len(events)),
            "stints": int(len(stints)),
            "race_input_identity": race_identity,
            "qualifying_manifest": qualifying_manifest,
        },
        "posterior_diagnostics": diagnostics,
        "events": events,
        "stints": stints,
        "lap_detail": {
            "path": outputs["lap_detail"],
            "sha256": lap_hash,
            "rows": int(len(lap_frame)),
            "columns": _lap_detail_columns(),
            "written": not args.quick,
        },
        "identification_contract": {
            "identity_backbone": config["identity_model_config"],
            "race_frontier": "driver-neutral within-event quantile with driver-balanced bootstrap",
            "qualifying_frontier": "accurate push laps; Race driver priors; independent context head",
            "weekend_ceiling": "weighted relative-session frontier; no absolute Qualifying/Race time merge",
            "result_explanation": "classification proxy consistency check, not V_result causal attribution",
            "boundaries": config["boundaries"],
        },
    }
    if not args.quick:
        manifest_path = ROOT / outputs["lap_detail_manifest"]
        base.atomic_json(
            manifest_path,
            {
                "created_at": result["created_at"],
                "run_id": result["run_id"],
                "config_hash": result["config_hash"],
                "source_race_files": race_identity,
                "source_qualifying_manifest_sha256": base.file_sha256(
                    ROOT / config["qualifying_freeze"]["manifest"]
                ),
                "file": result["lap_detail"],
            },
        )
        result_path = ROOT / outputs["result"]
        summary_path = ROOT / outputs["summary"]
        base.atomic_json(result_path, result)
        _write_summary(result, summary_path)
        base.append_log(
            {
                "run_id": result["run_id"],
                "timestamp": base.now_iso(),
                "phase": "run",
                "method": "four_team_event_stint_attribution_2023_2025_v4",
                "status": "completed",
                "network_calls": 0,
            }
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
