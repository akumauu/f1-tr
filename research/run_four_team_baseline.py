#!/usr/bin/env python3
"""运行 2025 最均衡单站的四队车辆—车手圈级归因原型。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import sklearn

import run_experiments as base
from modeling.entities import normalize_team_entities
from modeling.evaluation import (
    audit_balanced_meetings,
    compare_team_gaps,
    fetch_openf1_race,
    interval,
    openf1_team_pace_proxy,
    stratified_stint_bootstrap,
)
from modeling.hierarchical import (
    context_standardized_laps,
    fit_event_model,
    fit_historical_driver_prior,
    log_pct_delta_to_ms,
    sensitivity_grid,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "four_team_2025_baseline_v1.json"
COMBINED_OUTPUT = ROOT / "research" / "records" / "four_team_attribution_hungary_2025.json"
CAR_OUTPUT = ROOT / "research" / "records" / "four_team_car_baseline_2025.json"
DRIVER_OUTPUT = ROOT / "research" / "records" / "four_team_driver_extraction_2025.json"
VALUE_OUTPUT = ROOT / "research" / "records" / "four_team_value_attribution_2025.json"
EXTERNAL_OUTPUT = ROOT / "research" / "records" / "four_team_external_validation_2025_hungary.json"
SUMMARY_OUTPUT = ROOT / "research" / "records" / "four_team_attribution_hungary_2025.md"


def load_manifest_frame(manifest_path: Path, role: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = base.load_json(manifest_path)
    spec = manifest["files"][role]
    path = ROOT / spec["path"]
    actual_hash = base.file_sha256(path)
    if actual_hash != spec["sha256"]:
        raise RuntimeError(f"冻结文件哈希不一致: {path}")
    return pd.read_csv(path, compression="gzip"), manifest


def proxy_interval(estimate: float, values: Sequence[float], unit: str) -> dict[str, Any]:
    return {
        "estimate": float(estimate),
        "interval_95": interval(values),
        "unit": unit,
        "method": "stratified_driver_stint_bootstrap_not_bayesian_posterior",
    }


def _robust_scale(values: pd.Series) -> float:
    median = float(values.median())
    return float(1.4826 * (values - median).abs().median())


def _driver_metrics(
    subset: pd.DataFrame,
    team_ceiling: float,
    team_floor: float,
    reference_lap_seconds: float,
    ceiling_quantile: float,
    baseline_quantile: float,
) -> dict[str, float]:
    values = subset["driver_on_car_lap_z"]
    best = float(values.quantile(ceiling_quantile))
    typical = float(values.quantile(baseline_quantile))
    width = max(float(team_floor - team_ceiling), 1e-9)
    ceiling_extraction = 100.0 * (team_floor - best) / width
    typical_extraction = 100.0 * (team_floor - typical) / width
    scale_z = _robust_scale(values)
    return {
        "ceiling_extraction_percentile": float(ceiling_extraction),
        "typical_execution_loss_ms": log_pct_delta_to_ms(
            typical - team_ceiling, reference_lap_seconds
        ),
        "extraction_percentile": float(typical_extraction),
        "consistency_scale_ms": log_pct_delta_to_ms(scale_z, reference_lap_seconds),
        "negative_tail_probability": float((values > team_floor).mean()),
        "best_quantile_z": best,
        "typical_quantile_z": typical,
    }


def _bootstrap_models(
    target: pd.DataFrame,
    driver_priors: Mapping[str, float],
    reporting_teams: Sequence[str],
    team_penalty: float,
    driver_penalty: float,
    repetitions: int,
    seed: int,
    reference_lap_seconds: float,
    ceiling_quantile: float,
    baseline_quantile: float,
    floor_quantile: float,
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for _ in range(repetitions):
        sample = stratified_stint_bootstrap(target, rng)
        fit = fit_event_model(sample, driver_priors, team_penalty, driver_penalty)
        adjusted = context_standardized_laps(sample, fit)
        team_values: dict[str, dict[str, float]] = {}
        for team in reporting_teams:
            values = adjusted.loc[adjusted["team_name"] == team, "vehicle_lap_z"]
            team_values[team] = {
                "ceiling": float(values.quantile(ceiling_quantile)),
                "baseline": float(values.quantile(baseline_quantile)),
                "floor": float(values.quantile(floor_quantile)),
                "team_effect_z": float(fit.team_effect_z[team]),
            }
        fastest_baseline = min(value["baseline"] for value in team_values.values())
        drivers: dict[str, dict[str, float]] = {}
        for driver, subset in adjusted[
            adjusted["team_name"].isin(reporting_teams)
        ].groupby("driver_id"):
            team = str(subset["team_name"].iloc[0])
            metrics = _driver_metrics(
                subset,
                team_values[team]["ceiling"],
                team_values[team]["floor"],
                reference_lap_seconds,
                ceiling_quantile,
                baseline_quantile,
            )
            metrics["driver_event_update_z"] = float(fit.driver_update_z[str(driver)])
            metrics["unexplained_pace_contribution"] = -float(
                subset["model_residual_z"].median()
            )
            drivers[str(driver)] = metrics
        rows.append(
            {
                "teams": {
                    team: {
                        **value,
                        "gap_to_fastest_z": float(value["baseline"] - fastest_baseline),
                    }
                    for team, value in team_values.items()
                },
                "drivers": drivers,
            }
        )
    return rows


def _build_car_outputs(
    adjusted: pd.DataFrame,
    fit: Any,
    reporting_teams: Sequence[str],
    bootstrap: Sequence[Mapping[str, Any]],
    reference_lap_seconds: float,
    ceiling_quantile: float,
    baseline_quantile: float,
    floor_quantile: float,
) -> list[dict[str, Any]]:
    estimates: dict[str, dict[str, float]] = {}
    for team in reporting_teams:
        subset = adjusted[adjusted["team_name"] == team]
        values = subset["vehicle_lap_z"]
        estimates[team] = {
            "ceiling": float(values.quantile(ceiling_quantile)),
            "baseline": float(values.quantile(baseline_quantile)),
            "floor": float(values.quantile(floor_quantile)),
        }
    fastest = min(value["baseline"] for value in estimates.values())
    output: list[dict[str, Any]] = []
    for team in reporting_teams:
        subset = adjusted[adjusted["team_name"] == team]
        value = estimates[team]
        gap = float(value["baseline"] - fastest)
        drivers = {
            str(driver): int(count)
            for driver, count in subset.groupby("driver_id").size().items()
        }
        flags = [
            "single_race_exploratory",
            "bootstrap_interval_not_bayesian_posterior",
            "lap_level_no_segment_decomposition",
            "public_data_cannot_observe_setup_damage_or_fuel",
        ]
        output.append(
            {
                "team": team,
                "rank": 0,
                "car_ceiling_posterior": proxy_interval(
                    value["ceiling"],
                    [row["teams"][team]["ceiling"] for row in bootstrap],
                    "target_log_pct",
                ),
                "usable_baseline_posterior": proxy_interval(
                    value["baseline"],
                    [row["teams"][team]["baseline"] for row in bootstrap],
                    "target_log_pct",
                ),
                "performance_floor_posterior": proxy_interval(
                    value["floor"],
                    [row["teams"][team]["floor"] for row in bootstrap],
                    "target_log_pct",
                ),
                "drivability_width": proxy_interval(
                    value["floor"] - value["ceiling"],
                    [
                        row["teams"][team]["floor"]
                        - row["teams"][team]["ceiling"]
                        for row in bootstrap
                    ],
                    "target_log_pct",
                ),
                "relative_pace_pct": proxy_interval(
                    gap,
                    [row["teams"][team]["gap_to_fastest_z"] for row in bootstrap],
                    "log_pace_pct_deficit_to_fastest",
                ),
                "standard_90s_lap_delta_ms": proxy_interval(
                    log_pct_delta_to_ms(gap, 90.0),
                    [
                        log_pct_delta_to_ms(
                            row["teams"][team]["gap_to_fastest_z"], 90.0
                        )
                        for row in bootstrap
                    ],
                    "ms",
                ),
                "real_track_lap_delta_ms": proxy_interval(
                    log_pct_delta_to_ms(gap, reference_lap_seconds),
                    [
                        log_pct_delta_to_ms(
                            row["teams"][team]["gap_to_fastest_z"],
                            reference_lap_seconds,
                        )
                        for row in bootstrap
                    ],
                    "ms",
                ),
                "segment_type_deltas_ms": {},
                "development_state": "2025_hungary_single_race_update_from_2024_driver_prior",
                "model_team_effect_z": float(fit.team_effect_z[team]),
                "data_coverage": {
                    "clean_laps": int(len(subset)),
                    "drivers": drivers,
                    "driver_count": len(drivers),
                },
                "confidence_flags": flags,
            }
        )
    output.sort(key=lambda row: row["relative_pace_pct"]["estimate"])
    for rank, row in enumerate(output, start=1):
        row["rank"] = rank
    return output


def _build_driver_outputs(
    adjusted: pd.DataFrame,
    fit: Any,
    reporting_teams: Sequence[str],
    bootstrap: Sequence[Mapping[str, Any]],
    car_outputs: Sequence[Mapping[str, Any]],
    reference_lap_seconds: float,
    ceiling_quantile: float,
    baseline_quantile: float,
) -> list[dict[str, Any]]:
    car_by_team = {str(row["team"]): row for row in car_outputs}
    output: list[dict[str, Any]] = []
    for driver, subset in adjusted[
        adjusted["team_name"].isin(reporting_teams)
    ].groupby("driver_id"):
        driver = str(driver)
        team = str(subset["team_name"].iloc[0])
        car = car_by_team[team]
        ceiling = float(car["car_ceiling_posterior"]["estimate"])
        floor = float(car["performance_floor_posterior"]["estimate"])
        metrics = _driver_metrics(
            subset,
            ceiling,
            floor,
            reference_lap_seconds,
            ceiling_quantile,
            baseline_quantile,
        )
        boot = [row["drivers"][driver] for row in bootstrap]
        known_prior = driver in fit.driver_prior_pace
        flags = [
            "single_race_driver_event_update_not_stable_interaction",
            "lap_level_no_segment_extraction",
            "bootstrap_interval_not_bayesian_posterior",
        ]
        if not known_prior:
            flags.append("unseen_2024_driver_zero_prior")
        output.append(
            {
                "driver": driver,
                "team": team,
                "ceiling_extraction_percentile": proxy_interval(
                    metrics["ceiling_extraction_percentile"],
                    [value["ceiling_extraction_percentile"] for value in boot],
                    "envelope_position_pct_unclipped",
                ),
                "typical_execution_loss_ms": proxy_interval(
                    metrics["typical_execution_loss_ms"],
                    [value["typical_execution_loss_ms"] for value in boot],
                    "ms",
                ),
                "extraction_percentile": proxy_interval(
                    metrics["extraction_percentile"],
                    [value["extraction_percentile"] for value in boot],
                    "envelope_position_pct_unclipped",
                ),
                "consistency_scale": proxy_interval(
                    metrics["consistency_scale_ms"],
                    [value["consistency_scale_ms"] for value in boot],
                    "robust_mad_ms",
                ),
                "negative_tail_probability": proxy_interval(
                    metrics["negative_tail_probability"],
                    [value["negative_tail_probability"] for value in boot],
                    "probability",
                ),
                "segment_extraction": {},
                "adaptation_state": (
                    "known_2024_driver_prior" if known_prior else "zero_prior_new_driver"
                ),
                "historical_driver_pace_advantage_pct": float(
                    fit.driver_prior_pace.get(driver, 0.0)
                ),
                "driver_event_update_z": float(fit.driver_update_z[driver]),
                "sample_size": int(len(subset)),
                "confidence_flags": flags,
            }
        )
    return sorted(
        output,
        key=lambda row: float(row["extraction_percentile"]["estimate"]),
        reverse=True,
    )


def _build_value_outputs(
    adjusted: pd.DataFrame,
    fit: Any,
    reporting_teams: Sequence[str],
    bootstrap: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for driver, subset in adjusted[
        adjusted["team_name"].isin(reporting_teams)
    ].groupby("driver_id"):
        driver = str(driver)
        team = str(subset["team_name"].iloc[0])
        vehicle = -float(fit.team_effect_z[team])
        driver_base = float(fit.driver_prior_pace.get(driver, 0.0))
        interaction = -float(fit.driver_update_z[driver])
        unexplained = -float(subset["model_residual_z"].median())
        boot_vehicle = [-float(row["teams"][team]["team_effect_z"]) for row in bootstrap]
        boot_interaction = [
            -float(row["drivers"][driver]["driver_event_update_z"])
            for row in bootstrap
        ]
        boot_unexplained = [
            float(row["drivers"][driver]["unexplained_pace_contribution"])
            for row in bootstrap
        ]
        output.append(
            {
                "driver": driver,
                "team": team,
                "vehicle_contribution": vehicle,
                "driver_contribution": driver_base,
                "interaction_contribution": interaction,
                "strategy_operations_contribution": None,
                "reliability_incident_contribution": None,
                "unexplained_contribution": unexplained,
                "explained_pace_advantage": vehicle + driver_base + interaction,
                "value_function": "V_pace_context_standardized_target_log_pct",
                "reference_definition": "full_field_zero_mean_2025_hungary_car_and_2024_ridge_driver_prior",
                "posterior_interval": {
                    "vehicle_contribution": interval(boot_vehicle),
                    "driver_contribution": {
                        "low": driver_base,
                        "high": driver_base,
                        "note": "历史先验在本次 bootstrap 中固定",
                    },
                    "interaction_contribution": interval(boot_interaction),
                    "unexplained_contribution": interval(boot_unexplained),
                    "method": "stratified_driver_stint_bootstrap_not_bayesian_posterior",
                },
                "confidence_flags": [
                    "signed_associational_contribution_not_causal",
                    "interaction_is_single_race_driver_event_update_proxy",
                    "strategy_reliability_and_points_not_modeled",
                ],
            }
        )
    return output


def _fia_proxy(reference: Mapping[str, Any]) -> dict[str, Any]:
    frame = pd.DataFrame(reference["drivers"])
    teams = (
        frame.groupby("team")
        .agg(
            driver_count=("driver_id", "nunique"),
            pace_proxy_seconds=("fastest_lap_seconds", "mean"),
            best_classification=("classification", "min"),
        )
        .reset_index()
    )
    fastest = float(teams["pace_proxy_seconds"].min())
    teams["gap_to_fastest_ms"] = (teams["pace_proxy_seconds"] - fastest) * 1000.0
    teams = teams.sort_values("pace_proxy_seconds")
    return {
        "method": "mean_of_two_driver_official_fastest_laps",
        "teams": teams.to_dict(orient="records"),
        "drivers": reference["drivers"],
    }


def _driver_order_concordance(
    driver_outputs: Sequence[Mapping[str, Any]], fia_reference: Mapping[str, Any]
) -> dict[str, Any]:
    model = {
        str(row["driver"]): float(row["extraction_percentile"]["estimate"])
        for row in driver_outputs
    }
    fia = pd.DataFrame(fia_reference["drivers"])
    rows: list[dict[str, Any]] = []
    for team, subset in fia.groupby("team"):
        if len(subset) != 2:
            continue
        official = list(subset.sort_values("fastest_lap_seconds")["driver_id"].astype(str))
        modeled = sorted(official, key=lambda driver: model[driver], reverse=True)
        rows.append(
            {
                "team": str(team),
                "model_faster_driver": modeled[0],
                "official_fastest_lap_driver": official[0],
                "concordant": modeled[0] == official[0],
            }
        )
    return {
        "matched_teams": int(sum(bool(row["concordant"]) for row in rows)),
        "total_teams": len(rows),
        "details": rows,
        "limitation": "官方单一最快圈受燃油、轮胎、交通和策略影响，只作方向性核验。",
    }


def _external_validation(
    config: Mapping[str, Any],
    aliases: Mapping[str, str],
    reporting_teams: Sequence[str],
    car_outputs: Sequence[Mapping[str, Any]],
    driver_outputs: Sequence[Mapping[str, Any]],
    refresh_external: bool,
) -> dict[str, Any]:
    external = config["external_validation"]
    cache_dir = ROOT / str(external["cache_dir"])
    snapshot = fetch_openf1_race(
        int(external["year"]),
        str(external["country_name"]),
        str(external["session_name"]),
        cache_dir,
        reuse_cache=not refresh_external,
    )
    proxy = openf1_team_pace_proxy(
        snapshot,
        aliases,
        reporting_teams,
        {
            **config["cleaning"],
            "dry_compounds": config["cleaning"]["dry_compounds"],
        },
        float(external["fastest_fraction"]),
    )
    model_gaps = {
        str(row["team"]): float(row["real_track_lap_delta_ms"]["estimate"])
        for row in car_outputs
    }
    openf1_gaps = {
        str(row["team"]): float(row["gap_to_fastest_ms"])
        for row in proxy["teams"]
    }
    openf1_comparison = compare_team_gaps(model_gaps, openf1_gaps)

    fia_reference = base.load_json(ROOT / str(external["fia_reference"]))
    fia_proxy = _fia_proxy(fia_reference)
    fia_gaps = {
        str(row["team"]): float(row["gap_to_fastest_ms"])
        for row in fia_proxy["teams"]
    }
    fia_comparison = compare_team_gaps(model_gaps, fia_gaps)
    return {
        "status": "completed_directional_external_validation",
        "model_input_source": "TracingInsights expanded v4 frozen laps",
        "openf1": {
            "independence_note": "OpenF1 未参与模型拟合，仅用于外部圈速代理比较。",
            "session_key": snapshot["session_key"],
            "urls": snapshot["urls"],
            "response_sha256": snapshot["response_sha256"],
            "row_counts": {
                key: len(value) for key, value in snapshot["payloads"].items()
            },
            "pace_proxy": proxy,
            "comparison": openf1_comparison,
        },
        "fia": {
            "source_url": fia_reference["source_url"],
            "source_sha256": fia_reference["source_sha256"],
            "source_verified_at": fia_reference["source_verified_at"],
            "document": fia_reference["document"],
            "published_at": fia_reference["published_at"],
            "pace_proxy": fia_proxy,
            "comparison": fia_comparison,
            "driver_order_concordance": _driver_order_concordance(
                driver_outputs, fia_reference
            ),
        },
        "interpretation_limit": "排序或毫秒差一致只支持外部表面效度，不识别因果人车贡献。",
    }


def write_summary(results: Mapping[str, Any]) -> None:
    cars = results["cars"]
    drivers = results["drivers"]
    external = results["external_validation"]
    lines = [
        "# 2025 匈牙利站四队车辆—车手归因原型",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        f"- 基准选择：{results['meeting_selection']['selected']['meeting_name']}；四队 8 位车手最少 {results['meeting_selection']['selected']['minimum_driver_clean_laps']} 个有效圈，圈数 CV={results['meeting_selection']['selected']['driver_lap_count_cv']:.4f}。",
        f"- 模型范围：全场 {results['model_diagnostics']['identification_team_count']} 支车队参与识别，只发布四队结果。",
        f"- 圈级 MAE：{results['model_diagnostics']['in_sample_metrics']['mae']:.4f} target_log_pct。",
        "",
        "## 车辆基准",
        "",
        "| 排名 | 车队 | 相对最快车 | 90 秒标准圈差 | 真实赛道圈差 | 包络宽度 |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for car in cars:
        lines.append(
            "| {rank} | {team} | {gap:.4f}% | {standard:.1f} ms | {real:.1f} ms | {width:.4f} |".format(
                rank=car["rank"],
                team=car["team"],
                gap=car["relative_pace_pct"]["estimate"],
                standard=car["standard_90s_lap_delta_ms"]["estimate"],
                real=car["real_track_lap_delta_ms"]["estimate"],
                width=car["drivability_width"]["estimate"],
            )
        )
    lines.extend(
        [
            "",
            "## 车手提取（同车包络位置）",
            "",
            "| 车手 | 车队 | 常态提取位置 | 典型执行损失 | 样本 |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for driver in drivers:
        lines.append(
            f"| {driver['driver']} | {driver['team']} | {driver['extraction_percentile']['estimate']:.1f}% | {driver['typical_execution_loss_ms']['estimate']:.1f} ms | {driver['sample_size']} |"
        )
    lines.extend(
        [
            "",
            "## 外部对比",
            "",
            f"- OpenF1 最快四分之一圈中位代理：Spearman={external['openf1']['comparison']['spearman_rank_correlation']:.3f}，两两差值 MAE={external['openf1']['comparison']['pairwise_gap_mae_ms']:.1f} ms。",
            f"- FIA 官方最快圈双车均值代理：Spearman={external['fia']['comparison']['spearman_rank_correlation']:.3f}，两两差值 MAE={external['fia']['comparison']['pairwise_gap_mae_ms']:.1f} ms。",
            f"- 同队车手方向一致：{external['fia']['driver_order_concordance']['matched_teams']}/{external['fia']['driver_order_concordance']['total_teams']}。",
            "",
            "## 解释边界",
            "",
            "- 本结果是已查看 2025 数据上的探索性单站基线，不是确认性验证。",
            "- 单站车手更新与人车交互共线；当前交互贡献只是强收缩代理。",
            "- 区间来自车手→stint→圈分层 bootstrap，不是贝叶斯后验。",
            "- 尚未建模赛段类型、策略、可靠性、结果和积分价值函数。",
            "",
        ]
    )
    SUMMARY_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--refresh-external",
        action="store_true",
        help="忽略 research/cache 中的 OpenF1 快照并重新抓取",
    )
    parser.add_argument(
        "--bootstrap-repetitions",
        type=int,
        help="仅用于本地诊断；正式记录应使用配置中的固定次数",
    )
    args = parser.parse_args()

    config = base.load_json(args.config)
    run_id = f"{config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    max_attempts = int(config["max_attempts"])
    base.append_log(
        {
            "run_id": run_id,
            "timestamp": base.now_iso(),
            "phase": "run",
            "method": "four_team_single_race_attribution",
            "status": "started",
            "config_hash": base.canonical_hash(config),
            "git_head": base.git_head(),
        }
    )
    aliases = base.load_json(ROOT / config["entity_alias_config"])["team_aliases"]
    reporting_teams = [str(team) for team in config["reporting_teams"]]
    historical, historical_manifest = load_manifest_frame(
        ROOT / config["historical_manifest"], config["historical_file_role"]
    )
    target_all, target_manifest = load_manifest_frame(
        ROOT / config["target_manifest"], config["target_file_role"]
    )
    historical = normalize_team_entities(historical, aliases).reset_index(drop=True)
    target_all = normalize_team_entities(target_all, aliases).reset_index(drop=True)
    historical = historical[
        (historical["year"] == int(config["historical_year"]))
        & (historical["session_name"] == config["session_name"])
    ].reset_index(drop=True)
    target_year = target_all[target_all["year"] == int(config["target_year"])].copy()

    audit = audit_balanced_meetings(target_year, reporting_teams, config["session_name"])
    if not audit:
        raise RuntimeError("没有可用于四队样本平衡审计的比赛。")
    selected = audit[0]
    expected = str(config["meeting_selection"]["selected_meeting"])
    if selected["meeting_name"] != expected:
        raise RuntimeError(
            f"样本平衡选择漂移：配置={expected}，当前冻结数据={selected['meeting_name']}"
        )
    target = target_year[
        (target_year["meeting_name"] == expected)
        & (target_year["session_name"] == config["session_name"])
    ].reset_index(drop=True)
    if config["identification_scope"] != "all_teams":
        raise RuntimeError("当前实现要求 identification_scope=all_teams。")

    model_cfg = config["model"]

    def fit_once(_attempt: int) -> tuple[dict[str, float], Any, Any]:
        priors, pipeline = fit_historical_driver_prior(
            historical, float(config["historical_ridge_alpha"])
        )
        event_fit = fit_event_model(
            target,
            priors,
            float(model_cfg["team_penalty"]),
            float(model_cfg["driver_event_update_penalty"]),
        )
        return priors, pipeline, event_fit

    fit_attempt = base.run_with_retries(
        run_id,
        "m1_joint_lap_model",
        "historical_driver_prior_plus_single_race_car_update",
        max_attempts,
        fit_once,
    )
    if fit_attempt.value is None:
        return 2
    driver_priors, historical_pipeline, fit = fit_attempt.value
    adjusted = context_standardized_laps(target, fit)
    reference_lap_seconds = float(target["session_reference"].median())
    repetitions = (
        int(args.bootstrap_repetitions)
        if args.bootstrap_repetitions is not None
        else int(config["bootstrap"]["repetitions"])
    )
    bootstrap_attempt = base.run_with_retries(
        run_id,
        "m1_uncertainty",
        "stratified_driver_stint_bootstrap",
        max_attempts,
        lambda _attempt: _bootstrap_models(
            target,
            driver_priors,
            reporting_teams,
            float(model_cfg["team_penalty"]),
            float(model_cfg["driver_event_update_penalty"]),
            repetitions,
            int(config["bootstrap"]["random_seed"]),
            reference_lap_seconds,
            float(model_cfg["ceiling_quantile"]),
            float(model_cfg["baseline_quantile"]),
            float(model_cfg["floor_quantile"]),
        ),
    )
    if bootstrap_attempt.value is None:
        return 2
    bootstrap = bootstrap_attempt.value
    cars = _build_car_outputs(
        adjusted,
        fit,
        reporting_teams,
        bootstrap,
        reference_lap_seconds,
        float(model_cfg["ceiling_quantile"]),
        float(model_cfg["baseline_quantile"]),
        float(model_cfg["floor_quantile"]),
    )
    drivers = _build_driver_outputs(
        adjusted,
        fit,
        reporting_teams,
        bootstrap,
        cars,
        reference_lap_seconds,
        float(model_cfg["ceiling_quantile"]),
        float(model_cfg["baseline_quantile"]),
    )
    values = _build_value_outputs(adjusted, fit, reporting_teams, bootstrap)
    sensitivity = sensitivity_grid(
        target,
        driver_priors,
        reporting_teams,
        model_cfg["team_penalty_sensitivity"],
        model_cfg["driver_event_update_penalty_sensitivity"],
    )
    external_attempt = base.run_with_retries(
        run_id,
        "external_validation",
        "openf1_and_fia_directional_comparison",
        max_attempts,
        lambda _attempt: _external_validation(
            config,
            aliases,
            reporting_teams,
            cars,
            drivers,
            args.refresh_external,
        ),
    )
    if external_attempt.value is None:
        return 2
    external_validation = external_attempt.value

    target_prediction = fit.predict(target)
    result = {
        "run_id": run_id,
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
            "scikit_learn": sklearn.__version__,
        },
        "data_identity": {
            "historical_manifest_path": config["historical_manifest"],
            "historical_manifest_created_at": historical_manifest["created_at"],
            "historical_file_sha256": historical_manifest["files"][
                config["historical_file_role"]
            ]["sha256"],
            "target_manifest_path": config["target_manifest"],
            "target_manifest_created_at": target_manifest["created_at"],
            "target_file_sha256": target_manifest["files"][config["target_file_role"]][
                "sha256"
            ],
            "target_source_commit": str(target["source_commit"].iloc[0]),
        },
        "meeting_selection": {
            "method": config["meeting_selection"]["method"],
            "selected": selected,
            "ranking": audit,
        },
        "model_diagnostics": {
            "historical_rows": int(len(historical)),
            "historical_driver_count": int(historical["driver_id"].nunique()),
            "historical_feature_count": int(
                len(
                    historical_pipeline.named_steps[
                        "preprocess"
                    ].get_feature_names_out()
                )
            ),
            "target_rows": int(len(target)),
            "identification_team_count": int(target["team_name"].nunique()),
            "identification_driver_count": int(target["driver_id"].nunique()),
            "reference_lap_seconds": reference_lap_seconds,
            "in_sample_metrics": base.regression_metrics(
                target["target_log_pct"].to_numpy(dtype=float), target_prediction
            ),
            "residual_median": float(np.median(fit.train_residuals)),
            "residual_mad": float(
                np.median(np.abs(fit.train_residuals - np.median(fit.train_residuals)))
            ),
            "unseen_historical_prior_drivers": sorted(
                set(target["driver_id"].astype(str)) - set(driver_priors)
            ),
            "bootstrap_repetitions": repetitions,
            "sensitivity_grid": sensitivity,
            "unique_car_orders_in_sensitivity": sorted(
                {" > ".join(row["car_order_fast_to_slow"]) for row in sensitivity}
            ),
            "identifiability_statement": "单站车手当站更新与人车交互不可分；当前以 2024 车手先验和差异化 ridge 收缩作识别约束。",
        },
        "cars": cars,
        "drivers": drivers,
        "value_attribution": values,
        "external_validation": external_validation,
        "limitations": config["limitations"],
    }
    base.atomic_json(COMBINED_OUTPUT, result)
    shared = {
        "run_id": run_id,
        "created_at": result["created_at"],
        "config_hash": result["config_hash"],
        "methodological_status": result["methodological_status"],
        "meeting_selection": result["meeting_selection"]["selected"],
        "data_identity": result["data_identity"],
    }
    base.atomic_json(CAR_OUTPUT, {**shared, "cars": cars})
    base.atomic_json(DRIVER_OUTPUT, {**shared, "drivers": drivers})
    base.atomic_json(VALUE_OUTPUT, {**shared, "value_attribution": values})
    base.atomic_json(EXTERNAL_OUTPUT, {**shared, "external_validation": external_validation})
    write_summary(result)
    base.append_log(
        {
            "run_id": run_id,
            "timestamp": base.now_iso(),
            "phase": "run",
            "method": "four_team_single_race_attribution",
            "status": "completed",
            "bootstrap_repetitions": repetitions,
            "car_order": [row["team"] for row in cars],
            "openf1_spearman": external_validation["openf1"]["comparison"][
                "spearman_rank_correlation"
            ],
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
