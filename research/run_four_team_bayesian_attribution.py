#!/usr/bin/env python3
"""运行 2023–2025 四队车辆—车手分层贝叶斯反事实归因。"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import scipy

import run_experiments as base
from modeling.bayesian_counterfactual import (
    aggregate_with_context_fit,
    build_context_adjusted_aggregates,
    extract_counterfactual_ratings,
    posterior_diagnostics,
    posterior_mean_prediction,
    posterior_prediction_draws,
    sample_posterior,
)
from modeling.seasonal_attribution import (
    CONTEXT_BLOCKS,
    fit_seasonal_model,
    prepare_season_frame,
    split_time_forward,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "four_team_2023_2025_bayesian_v3.json"
RESULT_PATH = ROOT / "research" / "records" / "four_team_bayesian_attribution_2023_2025.json"
SUMMARY_PATH = ROOT / "research" / "records" / "four_team_bayesian_attribution_2023_2025.md"
RIDGE_RESULT_PATH = ROOT / "research" / "records" / "four_team_seasonal_attribution_2023_2025.json"


def _load_inputs(parent: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in parent["input_files"]:
        path = ROOT / item["path"]
        actual_hash = base.file_sha256(path)
        if actual_hash != item["sha256"]:
            raise RuntimeError(f"冻结文件哈希不一致：{item['path']}")
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


def _selected_penalties(parent: Mapping[str, Any]) -> dict[str, float]:
    target_id = "balanced_v1"
    selected = next(
        row for row in parent["candidate_penalties"] if row["id"] == target_id
    )
    return {key: float(value) for key, value in selected.items() if key != "id"}


def _settings(
    config: Mapping[str, Any],
    section: str | None = None,
    seed_offset: int = 0,
    quick: bool = False,
) -> dict[str, Any]:
    settings = copy.deepcopy(config["posterior"])
    if section is not None:
        override = config[section]
        for key in ("chains", "iterations", "warmup", "thin"):
            if key in override:
                settings[key] = override[key]
    if quick:
        settings.update({"chains": 2, "iterations": 300, "warmup": 150, "thin": 3})
    settings["random_seed"] = int(settings["random_seed"]) + seed_offset
    return settings


def _aggregate_metrics(frame: pd.DataFrame, prediction: np.ndarray) -> dict[str, Any]:
    error = np.asarray(prediction, dtype=float) - frame["response_z"].to_numpy(dtype=float)
    event_mae = (
        frame.assign(abs_error=np.abs(error))
        .groupby("meeting_key", observed=True)["abs_error"]
        .mean()
    )
    return {
        "mae_z": float(np.mean(np.abs(error))),
        "rmse_z": float(np.sqrt(np.mean(error**2))),
        "event_balanced_mae_z": float(event_mae.mean()),
        "bias_z": float(np.mean(error)),
        "driver_event_rows": int(len(frame)),
        "events": int(frame["meeting_key"].nunique()),
    }


def _time_forward_validation(
    frame: pd.DataFrame,
    parent: Mapping[str, Any],
    config: Mapping[str, Any],
    quick: bool,
) -> dict[str, Any]:
    robust = parent["robust_fit"]
    aggregation = config["aggregation"]
    penalties = _selected_penalties(parent)
    output: dict[str, Any] = {
        "unit": "driver_event_context_adjusted_z",
        "warning": "这是车手—分站聚合层的时间外复验，不能与 v2 逐圈 MAE 直接比较。",
        "years": {},
    }
    calibration_scores: dict[int, np.ndarray] = {}
    for target_year in (2024, 2025):
        train, validation = split_time_forward(frame, target_year, adaptation_races=5)
        context_fit = fit_seasonal_model(
            train,
            penalties,
            blocks=CONTEXT_BLOCKS,
            irls_iterations=int(robust["irls_iterations"]),
            huber_k=float(robust["huber_k"]),
        )
        train_aggregate = aggregate_with_context_fit(train, context_fit, aggregation)
        validation_aggregate = aggregate_with_context_fit(
            validation, context_fit, aggregation
        )
        validation_settings = _settings(
            config, "time_forward", seed_offset=target_year, quick=quick
        )
        posterior = sample_posterior(train_aggregate, validation_settings)
        prediction = posterior_mean_prediction(posterior, validation_aggregate)
        predictive = posterior_prediction_draws(
            posterior,
            validation_aggregate,
            include_observation_noise=True,
            random_seed=int(validation_settings["random_seed"]) + 77,
        ).reshape(-1, len(validation_aggregate))
        target = validation_aggregate["response_z"].to_numpy(dtype=float)
        coverage: dict[str, float] = {}
        for level in (80, 95):
            tail = (100 - level) / 200.0
            low = np.quantile(predictive, tail, axis=0)
            high = np.quantile(predictive, 1.0 - tail, axis=0)
            coverage[f"interval_{level}_coverage"] = float(
                np.mean((target >= low) & (target <= high))
            )
            coverage[f"interval_{level}_mean_width_z"] = float(np.mean(high - low))
            score = np.maximum.reduce((low - target, target - high, np.zeros_like(target)))
            if target_year == 2024:
                calibration_scores[level] = score
            elif level in calibration_scores:
                calibration = calibration_scores[level]
                nominal = level / 100.0
                finite_level = min(
                    1.0,
                    np.ceil((len(calibration) + 1) * nominal) / len(calibration),
                )
                inflation = float(
                    np.quantile(calibration, finite_level, method="higher")
                )
                calibrated_low = low - inflation
                calibrated_high = high + inflation
                coverage[f"calibrated_{level}_inflation_z"] = inflation
                coverage[f"calibrated_{level}_coverage"] = float(
                    np.mean(
                        (target >= calibrated_low) & (target <= calibrated_high)
                    )
                )
                coverage[f"calibrated_{level}_mean_width_z"] = float(
                    np.mean(calibrated_high - calibrated_low)
                )
        output["years"][str(target_year)] = {
            "role": "tuning_recheck" if target_year == 2024 else "locked_recheck",
            "adaptation_races": 5,
            "train_events": int(train_aggregate["meeting_key"].nunique()),
            "validation_events": int(validation_aggregate["meeting_key"].nunique()),
            "zero": _aggregate_metrics(
                validation_aggregate, np.zeros(len(validation_aggregate))
            ),
            "bayesian_joint": {
                **_aggregate_metrics(validation_aggregate, prediction),
                **coverage,
            },
            "diagnostics": posterior_diagnostics(posterior),
        }
    return output


def _rating_map(
    ratings: Mapping[str, Sequence[Mapping[str, Any]]], kind: str
) -> dict[tuple[int, str], Mapping[str, Any]]:
    name = "team" if kind == "cars" else "driver"
    return {(int(row["year"]), str(row[name])): row for row in ratings[kind]}


def _ridge_comparison(
    bayesian: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    if not RIDGE_RESULT_PATH.exists():
        return {"available": False, "reason": "v2 Ridge 结果文件不存在"}
    ridge_result = base.load_json(RIDGE_RESULT_PATH)
    ridge = ridge_result["season_ratings"]
    rows: dict[str, list[dict[str, Any]]] = {"cars": [], "drivers": []}
    for kind, key, ridge_field, bayes_field in (
        ("cars", "team", "car_pace_advantage_z", "car_pace_advantage_z"),
        ("drivers", "driver", "driver_pace_advantage_z", "driver_pace_advantage_z"),
    ):
        bayes_map = _rating_map(bayesian, kind)
        for ridge_row in ridge[kind]:
            identity = (int(ridge_row["year"]), str(ridge_row[key]))
            if identity not in bayes_map:
                continue
            bayes_mean = float(bayes_map[identity][bayes_field]["mean"])
            ridge_value = float(ridge_row[ridge_field])
            rows[kind].append(
                {
                    "year": identity[0],
                    key: identity[1],
                    "ridge_advantage_z": ridge_value,
                    "bayesian_advantage_z": bayes_mean,
                    "difference_z": bayes_mean - ridge_value,
                    "same_direction": bool(np.sign(bayes_mean) == np.sign(ridge_value)),
                }
            )
    return {
        "available": True,
        "ridge_run_id": ridge_result["run_id"],
        "warning": "两者共享同一冻结圈速数据和上下文定义，是方法复验，不是独立数据复验。",
        **rows,
    }


def _rank_correlation(order: Sequence[str], reference: Sequence[str]) -> float | None:
    common = [name for name in order if name in reference]
    if len(common) < 2:
        return None
    model_rank = np.asarray([order.index(name) + 1 for name in common], dtype=float)
    reference_rank = np.asarray(
        [reference.index(name) + 1 for name in common], dtype=float
    )
    return float(np.corrcoef(model_rank, reference_rank)[0, 1])


def _local_result_comparison(
    ratings: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    """复用 v2 已冻结的本地分类代理，不重新访问任何外部数据源。"""

    if not RIDGE_RESULT_PATH.exists():
        return {"available": False, "reason": "本地 v2 分类代理不存在"}
    ridge_result = base.load_json(RIDGE_RESULT_PATH)
    references = {
        int(row["year"]): row
        for row in ridge_result["external_validity"]["years"]
    }
    output: list[dict[str, Any]] = []
    for year in (2023, 2024, 2025):
        car_order = [
            str(row["team"])
            for row in ratings["cars"]
            if int(row["year"]) == year
        ]
        driver_order = [
            str(row["driver"])
            for row in ratings["drivers"]
            if int(row["year"]) == year
        ]
        reference = references[year]
        team_reference = list(reference["classification_proxy_team_order"])
        driver_reference = list(reference["classification_proxy_driver_order"])
        output.append(
            {
                "year": year,
                "bayesian_car_order": car_order,
                "classification_proxy_team_order": team_reference,
                "car_rank_spearman": _rank_correlation(car_order, team_reference),
                "bayesian_driver_order": driver_order,
                "classification_proxy_driver_order": driver_reference,
                "driver_rank_spearman": _rank_correlation(
                    driver_order, driver_reference
                ),
            }
        )
    return {
        "available": True,
        "source_run_id": ridge_result["run_id"],
        "source": "本地 Live Timing 赛道内分类/积分代理",
        "points_are_official": False,
        "uses_network": False,
        "role": "external_construct_check_only_not_training_or_truth_label",
        "warning": "分类代理混合车辆、车手、策略和可靠性；车手相关性尤其不能当纯能力真值。",
        "years": output,
    }


def _sensitivity(
    aggregate: pd.DataFrame,
    config: Mapping[str, Any],
    reference_frame: pd.DataFrame,
    quick: bool,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, name in enumerate(("interaction_tight", "interaction_wide"), 1):
        settings = _settings(
            config, "sensitivity", seed_offset=3000 + index * 101, quick=quick
        )
        settings["block_prior_sd_z"].update(config["sensitivity"][name])
        posterior = sample_posterior(aggregate, settings)
        ratings = extract_counterfactual_ratings(
            posterior,
            reference_frame,
            config["reporting_teams"],
            years=(2025,),
            reference_lap_seconds=float(config["counterfactuals"]["reference_lap_seconds"]),
        )
        output.append(
            {
                "name": name,
                "driver_team_prior_sd_z": float(
                    settings["block_prior_sd_z"]["driver_team"]
                ),
                "diagnostics": posterior_diagnostics(posterior),
                "cars_2025": ratings["cars"],
                "drivers_2025": ratings["drivers"],
            }
        )
    return output


def _influence_checks(
    aggregate: pd.DataFrame,
    config: Mapping[str, Any],
    reference_frame: pd.DataFrame,
    base_ratings: Mapping[str, Sequence[Mapping[str, Any]]],
    quick: bool,
) -> list[dict[str, Any]]:
    audit = config["influence_checks"]
    target_year = int(audit["year"])
    target_team = str(audit["team"])
    base_map = _rating_map(base_ratings, "cars")
    base_mean = float(
        base_map[(target_year, target_team)]["car_pace_advantage_z"]["mean"]
    )
    output: list[dict[str, Any]] = []
    for index, (name, excluded) in enumerate(audit["exclusions"].items(), 1):
        mask = (aggregate["year"] == target_year) & aggregate["driver_id"].isin(excluded)
        reduced = aggregate.loc[~mask].reset_index(drop=True)
        settings = _settings(
            config, "influence_checks", seed_offset=5000 + index * 103, quick=quick
        )
        posterior = sample_posterior(reduced, settings)
        ratings = extract_counterfactual_ratings(
            posterior,
            reference_frame,
            config["reporting_teams"],
            years=(target_year,),
            reference_lap_seconds=float(config["counterfactuals"]["reference_lap_seconds"]),
        )
        row = _rating_map(ratings, "cars")[(target_year, target_team)]
        reduced_mean = float(row["car_pace_advantage_z"]["mean"])
        output.append(
            {
                "name": name,
                "excluded_drivers": list(excluded),
                "excluded_driver_event_rows": int(mask.sum()),
                "base_car_advantage_z": base_mean,
                "reduced_car_advantage_z": reduced_mean,
                "shift_z": reduced_mean - base_mean,
                "reduced_ci95": [
                    row["car_pace_advantage_z"]["ci95_low"],
                    row["car_pace_advantage_z"]["ci95_high"],
                ],
                "diagnostics": posterior_diagnostics(posterior),
            }
        )
    return output


def _write_summary(result: Mapping[str, Any]) -> None:
    lines = [
        "# 2023–2025 四队车辆—车手分层贝叶斯反事实归因 v3",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        f"- 本地干地 Race 有效圈：{result['coverage']['clean_laps']}；车手—分站观测：{result['coverage']['driver_event_rows']}；网络调用：0。",
        "- 方法：上下文稳健 Ridge 只去除轮胎/赛道/圈次阶段；车辆、车手和人车组合由多链层级贝叶斯联合估计。",
        "- 正的 advantage 表示更快；90 秒毫秒值是条件反事实，不是赛果差值。",
        "",
        "## 收敛与时间外复验",
        "",
        f"- 主后验：{result['posterior_diagnostics']['chains']} 链 × {result['posterior_diagnostics']['draws_per_chain']} 留存样本；层级尺度最大 R-hat={result['posterior_diagnostics']['maximum_scale_rhat']:.3f}。",
        "",
        "| 目标年 | 聚合层零模型 MAE | 贝叶斯 MAE | 分站等权 MAE | 原始80%覆盖 | 原始95%覆盖 | 2024校准后95%覆盖 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for year, payload in result["time_forward_validation"]["years"].items():
        joint = payload["bayesian_joint"]
        calibrated_95 = joint.get("calibrated_95_coverage")
        calibrated_text = (
            f"{calibrated_95:.3f}" if calibrated_95 is not None else "—"
        )
        lines.append(
            f"| {year} | {payload['zero']['mae_z']:.4f} | {joint['mae_z']:.4f} | "
            f"{joint['event_balanced_mae_z']:.4f} | {joint['interval_80_coverage']:.3f} | "
            f"{joint['interval_95_coverage']:.3f} | "
            f"{calibrated_text} |"
        )
    lines.extend(
        [
            "",
            "## 标准车手下的纯赛车性能",
            "",
            "| 年份 | 车队 | 后验优势 z | 95%区间 | 90秒收益 | P(四队最快) | 期望排名 |",
            "| ---: | --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for row in result["counterfactual_ratings"]["cars"]:
        z = row["car_pace_advantage_z"]
        ms = row["car_pace_advantage_90s_ms"]
        lines.append(
            f"| {row['year']} | {row['team']} | {z['mean']:+.4f} | "
            f"[{z['ci95_low']:+.4f}, {z['ci95_high']:+.4f}] | "
            f"{ms['mean']:+.1f} ms | {row['p_rank_1']:.3f} | {row['expected_rank']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 标准赛车下的纯车手圈速贡献（2025）",
            "",
            "| 车手 | 车队 | 后验优势 z | 95%区间 | 90秒收益 | P(四队车手池最快) |",
            "| --- | --- | ---: | --- | ---: | ---: |",
        ]
    )
    for row in result["counterfactual_ratings"]["drivers"]:
        if row["year"] != 2025:
            continue
        z = row["driver_pace_advantage_z"]
        ms = row["driver_pace_advantage_90s_ms"]
        lines.append(
            f"| {row['driver']} | {row['team']} | {z['mean']:+.4f} | "
            f"[{z['ci95_low']:+.4f}, {z['ci95_high']:+.4f}] | "
            f"{ms['mean']:+.1f} ms | {row['p_rank_1']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 与本地赛季分类代理对照",
            "",
            "| 年份 | 贝叶斯纯车辆顺序 | 分类代理顺序 | Spearman |",
            "| ---: | --- | --- | ---: |",
        ]
    )
    for row in result["local_result_comparison"]["years"]:
        lines.append(
            f"| {row['year']} | {' > '.join(row['bayesian_car_order'])} | "
            f"{' > '.join(row['classification_proxy_team_order'])} | "
            f"{row['car_rank_spearman']:.3f} |"
        )
    lines.append(
        "分类代理不是 FIA 官方最终积分，并混合车辆、车手、策略与可靠性；仅检查方向，不参与拟合。"
    )
    lines.extend(["", "## 红牛极端队友差影响审计", ""])
    for row in result["influence_checks"]:
        lines.append(
            f"- `{row['name']}` 排除 {', '.join(row['excluded_drivers'])}："
            f"RB 2025 纯车辆优势从 {row['base_car_advantage_z']:+.4f} 变为 "
            f"{row['reduced_car_advantage_z']:+.4f}，漂移 {row['shift_z']:+.4f} z。"
        )
    lines.extend(
        [
            "",
            "## 如何解读",
            "",
            "- `car`：把车手、车手年度状态、人车适配都固定为零参考，回答“同一个标准车手驾驶时赛车有多快”。",
            "- `driver`：把赛车固定为四队等权参考并令适配为零，回答“同一辆标准赛车下车手能带来多少圈速”。",
            "- `interaction`：单列 driver-team 后验；不偷偷并入车手，也不拿队友差作全场标尺。",
            "- 这仍是观测数据的条件反事实。单一车队生涯导致的共线性只能通过更宽区间与先验敏感性暴露，不能被算法消灭。",
            "- 稳定性没有做 95% 截尾；每个车手—分站用全部清洗后圈做 Huber 聚合。车辆极限/排位上限仍需另建尾部分位模型。",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quick", action="store_true", help="仅用于开发烟雾测试")
    parser.add_argument("--quiet", action="store_true", help="完成运行但不打印完整 JSON")
    args = parser.parse_args()

    config = base.load_json(args.config)
    if not config.get("offline_only"):
        raise RuntimeError("本入口只允许离线配置。")
    parent = base.load_json(ROOT / config["parent_config"])
    raw, identity = _load_inputs(parent)
    aliases = base.load_json(ROOT / parent["entity_alias_config"])["team_aliases"]
    frame = prepare_season_frame(
        raw, aliases, parent["season_order"], parent["session_name"]
    )
    aggregate, _ = build_context_adjusted_aggregates(
        frame,
        _selected_penalties(parent),
        parent["robust_fit"],
        config["aggregation"],
    )
    posterior = sample_posterior(aggregate, _settings(config, quick=args.quick))
    ratings = extract_counterfactual_ratings(
        posterior,
        aggregate,
        config["reporting_teams"],
        reference_lap_seconds=float(config["counterfactuals"]["reference_lap_seconds"]),
    )
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
            "scipy": scipy.__version__,
            "sampler": "custom_conjugate_hierarchical_gibbs",
        },
        "network_calls": [],
        "coverage": {
            "clean_laps": int(len(frame)),
            "driver_event_rows": int(len(aggregate)),
            "events": int(aggregate["meeting_key"].nunique()),
            "years": sorted(aggregate["year"].astype(int).unique().tolist()),
            "input_identity": identity,
        },
        "observation_summary": {
            "median_laps_per_driver_event": float(aggregate["lap_count"].median()),
            "median_standard_error_z": float(aggregate["standard_error_z"].median()),
            "weight_min": float(aggregate["likelihood_weight"].min()),
            "weight_max": float(aggregate["likelihood_weight"].max()),
        },
        "posterior_diagnostics": posterior_diagnostics(posterior),
        "time_forward_validation": _time_forward_validation(
            frame, parent, config, args.quick
        ),
        "counterfactual_ratings": ratings,
        "ridge_v2_comparison": _ridge_comparison(ratings),
        "local_result_comparison": _local_result_comparison(ratings),
        "prior_sensitivity": _sensitivity(
            aggregate, config, aggregate, args.quick
        ),
        "influence_checks": _influence_checks(
            aggregate, config, aggregate, ratings, args.quick
        ),
        "identification_contract": {
            "pure_car": config["counterfactuals"]["car"],
            "pure_driver": config["counterfactuals"]["driver"],
            "interaction": config["counterfactuals"]["interaction"],
            "uses_teammate_gap_as_scale": False,
            "uses_results_as_training_label": False,
            "limitations": [
                "长期只效力单一车队时 driver 与 driver_team 部分共线。",
                "team_year 与 team_event 的分配依赖层级尺度先验，必须结合敏感性读。",
                "当前反事实针对正赛稳健平均配速，不是排位极限、稳定性或积分价值总模型。",
            ],
        },
    }
    if not args.quick:
        base.atomic_json(RESULT_PATH, result)
        _write_summary(result)
        base.append_log(
            {
                "run_id": result["run_id"],
                "timestamp": base.now_iso(),
                "phase": "run",
                "method": "four_team_bayesian_counterfactual_2023_2025_v3",
                "status": "completed",
                "network_calls": 0,
            }
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
