#!/usr/bin/env python3
"""运行 2023–2025 全赛季四队车辆—车手鲁棒归因与时间外回测。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import scipy
import sklearn
from scipy.stats import spearmanr

import run_experiments as base
from modeling.seasonal_attribution import (
    CONTEXT_BLOCKS,
    DESCRIPTIVE_BLOCKS,
    JOINT_BLOCKS,
    TEAM_BLOCKS,
    extract_season_ratings,
    fit_seasonal_model,
    local_live_timing_reference,
    metric_bundle,
    prepare_season_frame,
    split_time_forward,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "four_team_2023_2025_seasonal_v2.json"
)
RESULT_PATH = ROOT / "research" / "records" / "four_team_seasonal_attribution_2023_2025.json"
SUMMARY_PATH = ROOT / "research" / "records" / "four_team_seasonal_attribution_2023_2025.md"


def _load_inputs(config: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in config["input_files"]:
        path = ROOT / item["path"]
        actual_hash = base.file_sha256(path)
        if actual_hash != item["sha256"]:
            raise RuntimeError(f"冻结文件哈希不一致：{item['path']}")
        frame = pd.read_csv(path, compression="gzip")
        years = sorted(pd.to_numeric(frame["year"], errors="raise").astype(int).unique())
        if years != [int(item["year"])]:
            raise RuntimeError(f"冻结文件年份与配置不一致：{item['path']} -> {years}")
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


def _penalties(candidate: Mapping[str, Any], config: Mapping[str, Any], descriptive: bool) -> dict[str, float]:
    result = {
        key: float(value)
        for key, value in candidate.items()
        if key != "id"
    }
    if descriptive:
        result.update(
            {
                key: float(value)
                for key, value in config["descriptive_event_penalties"].items()
            }
        )
    return result


def _fit_options(config: Mapping[str, Any]) -> dict[str, Any]:
    robust = config["robust_fit"]
    return {
        "irls_iterations": int(robust["irls_iterations"]),
        "huber_k": float(robust["huber_k"]),
    }


def _evaluate_model(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    penalties: Mapping[str, float],
    blocks: Sequence[str],
    config: Mapping[str, Any],
    interval_residuals: np.ndarray | None = None,
) -> tuple[dict[str, float], Any, np.ndarray]:
    fit = fit_seasonal_model(
        train, penalties, blocks=blocks, **_fit_options(config)
    )
    prediction = fit.predict(validation)
    metrics = metric_bundle(validation, prediction, interval_residuals)
    return metrics, fit, prediction


def _time_forward_validation(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    protocol = config["time_forward"]
    adaptation = int(protocol["adaptation_races"])
    tuning_year = int(protocol["tuning_year"])
    test_year = int(protocol["locked_test_year"])
    tune_train, tune_validation = split_time_forward(frame, tuning_year, adaptation)

    candidates: list[dict[str, Any]] = []
    for candidate in config["candidate_penalties"]:
        penalties = _penalties(candidate, config, descriptive=False)
        metrics, _, _ = _evaluate_model(
            tune_train, tune_validation, penalties, JOINT_BLOCKS, config
        )
        candidates.append(
            {
                "id": candidate["id"],
                "penalties": penalties,
                "tuning_2024": metrics,
            }
        )
    best_score = min(
        row["tuning_2024"]["event_balanced_mae"] for row in candidates
    )
    tolerance = float(protocol["practical_equivalence_tolerance"])
    equivalent_ids = {
        row["id"]
        for row in candidates
        if row["tuning_2024"]["event_balanced_mae"] <= best_score + tolerance
    }
    selected_id = next(
        candidate["id"]
        for candidate in config["candidate_penalties"]
        if candidate["id"] in equivalent_ids
    )
    selected = next(
        candidate
        for candidate in config["candidate_penalties"]
        if candidate["id"] == selected_id
    )

    evaluations: dict[str, Any] = {}
    calibration_residuals: dict[str, np.ndarray] = {}
    for year in (tuning_year, test_year):
        train, validation = split_time_forward(frame, year, adaptation)
        selected_penalties = _penalties(selected, config, descriptive=False)
        target = validation["target_log_pct"].to_numpy(dtype=float)
        zero_prediction = np.zeros(len(validation), dtype=float)
        zero = metric_bundle(
            validation,
            zero_prediction,
            calibration_residuals.get("zero") if year == test_year else None,
        )
        context_metrics, _, context_prediction = _evaluate_model(
            train,
            validation,
            selected_penalties,
            CONTEXT_BLOCKS,
            config,
            calibration_residuals.get("context") if year == test_year else None,
        )
        team_metrics, _, team_prediction = _evaluate_model(
            train,
            validation,
            selected_penalties,
            TEAM_BLOCKS,
            config,
            calibration_residuals.get("team_only") if year == test_year else None,
        )
        joint_metrics, _, joint_prediction = _evaluate_model(
            train,
            validation,
            selected_penalties,
            JOINT_BLOCKS,
            config,
            calibration_residuals.get("joint_robust") if year == test_year else None,
        )
        evaluations[str(year)] = {
            "role": "tuning" if year == tuning_year else "locked_test",
            "adaptation_races": adaptation,
            "train_rows": int(len(train)),
            "validation_rows": int(len(validation)),
            "validation_meetings": sorted(validation["meeting_name"].unique()),
            "models": {
                "zero": zero,
                "context": context_metrics,
                "team_only": team_metrics,
                "joint_robust": joint_metrics,
            },
            "joint_mae_delta_vs_context": float(
                joint_metrics["mae"] - context_metrics["mae"]
            ),
            "joint_event_mae_delta_vs_team_only": float(
                joint_metrics["event_balanced_mae"]
                - team_metrics["event_balanced_mae"]
            ),
        }
        if year == tuning_year:
            calibration_residuals = {
                "zero": target - zero_prediction,
                "context": target - context_prediction,
                "team_only": target - team_prediction,
                "joint_robust": target - joint_prediction,
            }

    return (
        {
            "selection_uses_only": "2024_after_first_5_races",
            "locked_test": "2025_after_first_5_races",
            "selected_candidate": selected_id,
            "selection_rule": {
                "metric": protocol["selection_metric"],
                "best_score": float(best_score),
                "practical_equivalence_tolerance": tolerance,
                "equivalent_candidates": sorted(equivalent_ids),
                "tie_break": protocol["tie_break"],
            },
            "candidate_tuning": candidates,
            "evaluations": evaluations,
        },
        selected,
    )


def _attach_bootstrap_intervals(
    ratings: dict[str, list[dict[str, Any]]],
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    bootstrap_cfg = config["bootstrap"]
    repetitions = int(bootstrap_cfg["repetitions"])
    rng = np.random.default_rng(int(bootstrap_cfg["random_seed"]))
    car_samples: dict[tuple[int, str], list[float]] = {}
    driver_samples: dict[tuple[int, str], list[float]] = {}
    by_year = {
        int(year): {
            meeting: subset
            for meeting, subset in year_frame.groupby("meeting_key", sort=False)
        }
        for year, year_frame in frame.groupby("year", sort=True)
    }
    penalties = _penalties(selected, config, descriptive=True)
    for _ in range(repetitions):
        pieces: list[pd.DataFrame] = []
        for meetings in by_year.values():
            keys = list(meetings)
            sampled = rng.choice(keys, size=len(keys), replace=True)
            pieces.extend(meetings[str(key)] for key in sampled)
        sample = pd.concat(pieces, ignore_index=True)
        fit = fit_seasonal_model(
            sample, penalties, blocks=DESCRIPTIVE_BLOCKS, **_fit_options(config)
        )
        extracted = extract_season_ratings(
            fit, sample, config["reporting_teams"]
        )
        for row in extracted["cars"]:
            car_samples.setdefault((row["year"], row["team"]), []).append(
                row["car_pace_advantage_z"]
            )
        for row in extracted["drivers"]:
            driver_samples.setdefault((row["year"], row["driver"]), []).append(
                row["driver_pace_advantage_z"]
            )

    def add_interval(row: dict[str, Any], samples: list[float]) -> None:
        if not samples:
            return
        row["pace_advantage_z_ci80"] = [
            float(value) for value in np.quantile(samples, [0.10, 0.90])
        ]
        row["pace_advantage_z_ci95"] = [
            float(value) for value in np.quantile(samples, [0.025, 0.975])
        ]

    for row in ratings["cars"]:
        add_interval(row, car_samples.get((row["year"], row["team"]), []))
    for row in ratings["drivers"]:
        add_interval(row, driver_samples.get((row["year"], row["driver"]), []))
    return {
        "repetitions": repetitions,
        "unit": bootstrap_cfg["unit"],
        "intervals_are": "meeting_cluster_bootstrap_not_bayesian_posterior",
    }


def _penalty_sensitivity(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for candidate in config["candidate_penalties"]:
        fit = fit_seasonal_model(
            frame,
            _penalties(candidate, config, descriptive=True),
            blocks=DESCRIPTIVE_BLOCKS,
            **_fit_options(config),
        )
        ratings = extract_season_ratings(fit, frame, config["reporting_teams"])
        orders = {
            str(year): [
                row["team"]
                for row in ratings["cars"]
                if row["year"] == year
            ]
            for year in (2023, 2024, 2025)
        }
        output.append({"id": candidate["id"], "car_orders": orders})
    return output


def _rank_correlation(model_order: Sequence[str], reference_order: Sequence[str]) -> float | None:
    common = [entity for entity in model_order if entity in reference_order]
    if len(common) < 3:
        return None
    model_rank = {entity: index for index, entity in enumerate(model_order)}
    reference_rank = {entity: index for index, entity in enumerate(reference_order)}
    value = spearmanr(
        [model_rank[entity] for entity in common],
        [reference_rank[entity] for entity in common],
    ).statistic
    return float(value) if np.isfinite(value) else None


def _external_validity(
    ratings: dict[str, list[dict[str, Any]]],
    references: list[dict[str, Any]],
    reporting_teams: Sequence[str],
    final_fit: Any,
    frame: pd.DataFrame,
) -> dict[str, Any]:
    years: list[dict[str, Any]] = []
    for reference in references:
        year = int(reference["year"])
        car_order = [
            row["team"] for row in ratings["cars"] if row["year"] == year
        ]
        team_reference = [
            row["team"]
            for row in reference["teams"]
            if row["team"] in reporting_teams
        ]
        driver_order = [
            row["driver"] for row in ratings["drivers"] if row["year"] == year
        ]
        active_drivers = set(driver_order)
        driver_reference = [
            row["driver"]
            for row in reference["drivers"]
            if row["driver"] in active_drivers
        ]
        years.append(
            {
                "year": year,
                "model_car_order": car_order,
                "classification_proxy_team_order": team_reference,
                "car_vs_team_rank_spearman": _rank_correlation(
                    car_order, team_reference
                ),
                "model_driver_order": driver_order,
                "classification_proxy_driver_order": driver_reference,
                "driver_vs_points_rank_spearman": _rank_correlation(
                    driver_order, driver_reference
                ),
            }
        )
    effects = {
        block: final_fit.effect_map(block)
        for block in ("team_year", "team_circuit", "team_event")
    }
    event_rows: list[dict[str, Any]] = []
    reference_by_year_meeting = {
        (int(reference["year"]), event["meeting_name"]): event
        for reference in references
        for event in reference["events"]
        if event["session_name"] == "Race"
    }
    for (year, meeting), event_frame in frame.groupby(
        ["year", "meeting_name"], sort=True
    ):
        reference_event = reference_by_year_meeting.get((int(year), str(meeting)))
        if reference_event is None:
            continue
        circuit = str(event_frame["circuit_name"].iloc[0])
        model_values: dict[str, float] = {}
        for team in reporting_teams:
            if not bool((event_frame["team_name"] == team).any()):
                continue
            model_values[team] = float(
                effects["team_year"].get(f"{int(year)}|{team}", 0.0)
                + effects["team_circuit"].get(f"{team}|{circuit}", 0.0)
                + effects["team_event"].get(
                    f"{int(year)}|{meeting}|{team}", 0.0
                )
            )
        model_order = sorted(model_values, key=model_values.get)
        reference_order = [
            row["team"]
            for row in reference_event["teams"]
            if row["team"] in reporting_teams
        ]
        if not model_order or not reference_order:
            continue
        event_rows.append(
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "model_car_order": model_order,
                "classification_proxy_order": reference_order,
                "best_four_team_match": model_order[0] == reference_order[0],
                "rank_spearman": _rank_correlation(model_order, reference_order),
            }
        )
    event_correlations = [
        row["rank_spearman"]
        for row in event_rows
        if row["rank_spearman"] is not None
    ]
    event_summary = {
        "status": "retrospective_same_event_construct_check",
        "event_count": len(event_rows),
        "best_four_team_match_count": int(
            sum(row["best_four_team_match"] for row in event_rows)
        ),
        "best_four_team_match_rate": float(
            np.mean([row["best_four_team_match"] for row in event_rows])
        ) if event_rows else None,
        "mean_rank_spearman": float(np.mean(event_correlations))
        if event_correlations
        else None,
        "events": event_rows,
        "warning": "team_event 使用同一分站圈速拟合，这是赛后构念检查，不是赛前预测准确率。",
    }
    return {
        "role": "external_construct_check_only_not_tuning",
        "years": years,
        "race_level": event_summary,
        "warning": "车手配速能力不应与含车辆、可靠性和策略的积分排名高度等同；相关性只作构念边界诊断。",
    }


def _extreme_pair_audit(
    frame: pd.DataFrame,
    final_fit: Any,
    ratings: dict[str, list[dict[str, Any]]],
    reporting_teams: Sequence[str],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    context_fit = fit_seasonal_model(
        frame,
        _penalties(config["candidate_penalties"][0], config, descriptive=False),
        blocks=CONTEXT_BLOCKS,
        **_fit_options(config),
    )
    work = frame.copy()
    work["context_residual_z"] = (
        work["target_log_pct"].to_numpy(dtype=float) - context_fit.predict(work)
    )
    work["robust_weight"] = final_fit.robust_weights
    output: list[dict[str, Any]] = []
    for (year, team), subset in work.loc[
        work["team_name"].isin(reporting_teams)
    ].groupby(["year", "team_name"]):
        raw_driver = (
            subset.groupby("driver_id")["context_residual_z"].median().sort_values()
        )
        driver_rows = [
            row
            for row in ratings["drivers"]
            if row["year"] == int(year) and row["team"] == team
        ]
        driver_values = [row["driver_pace_z"] for row in driver_rows]
        output.append(
            {
                "year": int(year),
                "team": str(team),
                "drivers": list(raw_driver.index.astype(str)),
                "raw_context_adjusted_teammate_span_z": float(
                    raw_driver.max() - raw_driver.min()
                ) if len(raw_driver) > 1 else None,
                "model_driver_skill_span_z": float(max(driver_values) - min(driver_values))
                if len(driver_values) > 1 else None,
                "robust_downweighted_row_fraction": float(
                    np.mean(subset["robust_weight"] < 0.999999)
                ),
                "normalization_uses_teammate_span": False,
            }
        )
    return output


def _coverage(frame: pd.DataFrame, identity: list[dict[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    by_year = []
    for year, subset in frame.groupby("year", sort=True):
        expected = list(config["season_order"][str(int(year))])
        observed = sorted(subset["meeting_name"].astype(str).unique())
        by_year.append(
            {
                "year": int(year),
                "race_rows": int(len(subset)),
                "expected_races": len(expected),
                "observed_races_with_clean_laps": len(observed),
                "missing_clean_races": [meeting for meeting in expected if meeting not in observed],
                "drivers": int(subset["driver_id"].nunique()),
                "teams": int(subset["team_name"].nunique()),
            }
        )
    return {"input_identity": identity, "race_total_rows": int(len(frame)), "by_year": by_year}


def _prototype_comparison() -> list[dict[str, str]]:
    return [
        {
            "artifact": "four_team_contrast_stress_2025.md",
            "status": "diagnostic_only_not_model",
            "difference": "6 站按赛果事后挑选，只比较队友中位差和最快车代理；不得给出泛化结论。",
        },
        {
            "artifact": "four_team_attribution_hungary_2025.md",
            "status": "single_event_m0_m1",
            "difference": "有历史车手 ridge 先验和单站分层更新，但惩罚未经过时间外选择，车手当站更新与交互仍共线。",
        },
        {
            "artifact": "car_driver_envelope_summary.md",
            "status": "descriptive_mechanism_component",
            "difference": "Spain 逐点 mini-sector 前沿解释差距发生在哪里；只用四队同队前沿，不能独立识别人车贡献。",
        },
        {
            "artifact": "four_team_seasonal_attribution_2023_2025.md",
            "status": "seasonal_quantitative_v2",
            "difference": "70 场覆盖、全场连接网络、2024 选参、2025 锁定时间外测试、分块收缩与鲁棒权重；面向 2026 只迁移车手先验。",
        },
    ]


def write_summary(result: Mapping[str, Any]) -> None:
    validation = result["time_forward_validation"]
    selected = validation["selected_candidate"]
    locked = validation["evaluations"]["2025"]
    lines = [
        "# 2023–2025 四队全赛季车辆—车手鲁棒归因 v2",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        f"- 本地有效 Race 圈：{result['coverage']['race_total_rows']}；不调用网络。",
        f"- 2024 时间外选参：`{selected}`；2025 锁定测试不参与选参。",
        "- 赛果/积分来自本地 Live Timing 分类代理，仅作外部效度检查，不是 FIA 官方最终积分。",
        "",
        "## 时间外回测",
        "",
        "| 年份 | 角色 | 模型 | MAE | 分站等权 MAE | 80% 覆盖 | 95% 覆盖 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for year, payload in validation["evaluations"].items():
        for name, metrics in payload["models"].items():
            lines.append(
                f"| {year} | {payload['role']} | {name} | {metrics['mae']:.4f} | "
                f"{metrics['event_balanced_mae']:.4f} | "
                f"{metrics.get('interval_80_coverage', float('nan')):.3f} | "
                f"{metrics.get('interval_95_coverage', float('nan')):.3f} |"
            )
    lines.extend(
        [
            "",
            "## 四队车辆赛季评分",
            "",
            "正的 `car_pace_advantage_z` 表示相对当年四队均值更快；毫秒列为 90 秒标准圈相对最快车差。",
            "",
            "| 年份 | 排名 | 车队 | 配速优势 z | 相对最快车 | 95% 区间 |",
            "| ---: | ---: | --- | ---: | ---: | --- |",
        ]
    )
    for row in result["season_ratings"]["cars"]:
        interval = row.get("pace_advantage_z_ci95", [float("nan"), float("nan")])
        lines.append(
            f"| {row['year']} | {row['rank']} | {row['team']} | "
            f"{row['car_pace_advantage_z']:+.4f} | {row['delta_to_fastest_90s_ms']:.1f} ms | "
            f"[{interval[0]:+.4f}, {interval[1]:+.4f}] |"
        )
    lines.extend(
        [
            "",
            "## 与本地赛季分类代理对比",
            "",
            "| 年份 | 模型车辆顺序 | 分类代理顺序 | Spearman |",
            "| ---: | --- | --- | ---: |",
        ]
    )
    for row in result["external_validity"]["years"]:
        lines.append(
            f"| {row['year']} | {' > '.join(row['model_car_order'])} | "
            f"{' > '.join(row['classification_proxy_team_order'])} | "
            f"{row['car_vs_team_rank_spearman']:.3f} |"
        )
    race_level = result["external_validity"]["race_level"]
    lines.extend(
        [
            "",
            f"- {race_level['event_count']} 场有干地有效圈的赛后车辆状态与当场四队最佳分类一致："
            f"{race_level['best_four_team_match_count']}/{race_level['event_count']} "
            f"({race_level['best_four_team_match_rate']:.1%})；逐站平均 Spearman="
            f"{race_level['mean_rank_spearman']:.3f}。这是同场赛后构念检查，不是赛前准确率。",
        ]
    )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            f"- 2025 锁定集 joint_robust MAE={locked['models']['joint_robust']['mae']:.4f}，"
            f"相对 context 变化 {locked['joint_mae_delta_vs_context']:+.4f}；是否采用必须以这个时间外结果为准。",
            "- 不以队友差最大值归一化，不输出‘车辆/车手百分比’；VER–队友极端差只影响其自身残差和车手层，不定义全场尺度。",
            "- 分站 bootstrap 是事件簇抽样区间，不是贝叶斯后验；2023–2025 都已被查看，仍属于回顾性证据。",
            "- 2026 只迁移车手基础能力和不确定性；车辆、能量、空气动力和人车交互必须重置。",
            "- Spain 逐点原型继续负责‘差在哪个赛段’，本 v2 负责‘跨赛季如何量化分开’，两者互补而非互相复制。",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--bootstrap-repetitions", type=int)
    args = parser.parse_args()

    config = base.load_json(args.config)
    if not config.get("offline_only"):
        raise RuntimeError("本入口只允许离线配置。")
    if args.bootstrap_repetitions is not None:
        config["bootstrap"]["repetitions"] = int(args.bootstrap_repetitions)
    run_id = f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}"

    raw, identity = _load_inputs(config)
    alias_payload = base.load_json(ROOT / config["entity_alias_config"])
    aliases = alias_payload["team_aliases"]
    frame = prepare_season_frame(
        raw, aliases, config["season_order"], config["session_name"]
    )
    coverage = _coverage(frame, identity, config)

    time_forward, selected = _time_forward_validation(frame, config)
    final_fit = fit_seasonal_model(
        frame,
        _penalties(selected, config, descriptive=True),
        blocks=DESCRIPTIVE_BLOCKS,
        **_fit_options(config),
    )
    ratings = extract_season_ratings(
        final_fit, frame, config["reporting_teams"]
    )
    bootstrap = _attach_bootstrap_intervals(ratings, frame, config, selected)
    sensitivity = _penalty_sensitivity(frame, config)

    references = [
        local_live_timing_reference(
            year,
            ROOT / config["local_result_reference"]["root_template"].format(year=year),
            aliases,
            config["local_result_reference"]["sessions"],
        )
        for year in (2023, 2024, 2025)
    ]
    external_validity = _external_validity(
        ratings, references, config["reporting_teams"], final_fit, frame
    )
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
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "network_calls": [],
        "coverage": coverage,
        "time_forward_validation": time_forward,
        "season_ratings": ratings,
        "bootstrap": bootstrap,
        "penalty_sensitivity": sensitivity,
        "extreme_teammate_gap_audit": _extreme_pair_audit(
            frame, final_fit, ratings, config["reporting_teams"], config
        ),
        "local_result_references": references,
        "external_validity": external_validity,
        "prototype_comparison": _prototype_comparison(),
        "2026_transfer_contract": {
            "transfer": [
                "driver_base_z",
                "driver consistency / robust residual scale",
                "measurement and cleaning protocol",
                "time-forward adaptation protocol",
            ],
            "reset": [
                "team_year",
                "team_event",
                "team_circuit response",
                "driver_team interaction",
                "energy and aerodynamic regime effects",
            ],
            "first_live_gate": "至少 5 站适配后才与 2024/2025 伪新赛季误差带比较。",
        },
        "limitations": [
            "当前主头只建模 Race 干地有效圈；Qualifying、Sprint、湿地、策略和可靠性尚未进入同一价值函数。",
            "本地分类代理不含赛后 DSQ、罚时和 FIA 最终修订，积分数不能视为官方积分。",
            "分块 ridge 的归因仍依赖收缩先验；通过时间外选参、惩罚敏感性和 bootstrap 报告不确定性，而非宣称唯一真值。",
            "逐点 Spain 原型的赛段机理尚未扩展到 70 场，因此本结果回答跨赛季量化，不回答每站具体弯段物理原因。",
        ],
    }
    base.atomic_json(RESULT_PATH, result)
    write_summary(result)
    base.append_log(
        {
            "run_id": run_id,
            "timestamp": base.now_iso(),
            "phase": "run",
            "method": "four_team_seasonal_attribution_2023_2025_v2",
            "status": "completed",
            "selected_candidate": time_forward["selected_candidate"],
            "locked_2025_mae": time_forward["evaluations"]["2025"]["models"]["joint_robust"]["mae"],
            "network_calls": 0,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
