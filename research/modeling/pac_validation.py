"""PAC 的独立车手级时间外验证与分层不确定性工具。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import erf, log, sqrt
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from modeling.seasonal_attribution import (
    BLOCK_COLUMNS,
    CONTEXT_BLOCKS,
    NUMERIC_FEATURES,
    fit_seasonal_model,
)


PROHIBITED_TARGET_FIELDS = frozenset(
    {
        "actual_points",
        "championship_points",
        "finish_position",
        "finishing_position",
        "classification_proxy",
        "classification_proxy_rank",
        "championship_position",
        "podiums",
        "wins",
    }
)


@dataclass(frozen=True)
class OriginPrediction:
    origin_index: int
    year: int
    meeting_name: str
    driver: str
    team: str
    target_z: float
    prediction_z: float
    negative_control_z: float
    predictive_sigma_z: float
    train_driver_events: int


def past_only_transfer_graph(frame: pd.DataFrame) -> dict[str, Any]:
    """按 driver↔team-year 设计列重建某个 origin 可见的识别图。"""

    adjacency: dict[str, set[str]] = {}
    for row in (
        frame[["driver_id", "year", "team_name"]].drop_duplicates().itertuples(index=False)
    ):
        driver = f"D:{row.driver_id}"
        team_year = f"TY:{int(row.year)}|{row.team_name}"
        adjacency.setdefault(driver, set()).add(team_year)
        adjacency.setdefault(team_year, set()).add(driver)
    components: list[list[str]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        stack = [root]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency.get(node, ()))
        unseen -= component
        components.append(sorted(component))
    components.sort(key=lambda values: (-len(values), values))
    driver_component = {
        node[2:]: index
        for index, component in enumerate(components)
        for node in component
        if node.startswith("D:")
    }
    weak_drivers = sorted(
        driver
        for driver, index in driver_component.items()
        if sum(node.startswith("D:") for node in components[index]) < 3
    )
    return {
        "node_definition": "driver_bipartite_team_year",
        "n_components": len(components),
        "connected": len(components) == 1,
        "components": components,
        "driver_component": driver_component,
        "weakly_anchored_drivers": weak_drivers,
    }


def audit_joint_identity_collinearity(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """指出旧 JOINT_BLOCKS 中由实际设计决定的完全重复身份列。"""

    rows: list[dict[str, Any]] = []
    for driver, group in frame.groupby("driver_id"):
        teams = sorted(group["team_name"].astype(str).unique())
        years = sorted(pd.to_numeric(group["year"], errors="raise").astype(int).unique())
        duplicate_blocks: list[str] = []
        if len(teams) == 1:
            duplicate_blocks.append("driver==driver_team")
        if len(years) == 1:
            duplicate_blocks.append("driver==driver_year")
        rows.append(
            {
                "driver": str(driver),
                "distinct_teams": len(teams),
                "distinct_seasons": len(years),
                "exact_duplicate_identity_columns": duplicate_blocks,
                "joint_driver_base_uniquely_identified": not duplicate_blocks,
            }
        )
    return rows


def audit_target_fields(columns: Sequence[str]) -> list[str]:
    """阻止积分、名次或 classification proxy 混入 PAC 标签。"""

    return sorted(
        {
            str(column)
            for column in columns
            if str(column).strip().lower() in PROHIBITED_TARGET_FIELDS
        }
    )


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _event_targets(
    frame: pd.DataFrame,
    residual: np.ndarray,
    *,
    minimum_laps: int,
    minimum_drivers: int,
) -> pd.DataFrame:
    work = frame[
        ["year", "round_index", "meeting_name", "meeting_key", "driver_id", "team_name"]
    ].copy()
    work["conditional_residual_z"] = np.asarray(residual, dtype=float)
    rows = (
        work.groupby(
            [
                "year",
                "round_index",
                "meeting_name",
                "meeting_key",
                "team_name",
                "driver_id",
            ],
            as_index=False,
        )
        .agg(
            conditional_residual_z=("conditional_residual_z", "median"),
            clean_laps=("conditional_residual_z", "size"),
        )
    )
    rows = rows.loc[rows["clean_laps"] >= int(minimum_laps)].copy()
    team_counts = rows.groupby(["meeting_key", "team_name"])["driver_id"].transform(
        "nunique"
    )
    rows = rows.loc[team_counts >= int(minimum_drivers)].copy()
    team_center = rows.groupby(["meeting_key", "team_name"])[
        "conditional_residual_z"
    ].transform("mean")
    rows["target_z"] = -(rows["conditional_residual_z"] - team_center)
    return rows.reset_index(drop=True)


def _driver_scores(
    target_rows: pd.DataFrame, shrinkage: float
) -> tuple[dict[str, float], dict[str, int]]:
    """用 pair-margin incidence matrix 的 ridge Laplacian 解估计单一身份量。"""

    drivers = sorted(target_rows["driver_id"].astype(str).unique())
    driver_index = {driver: index for index, driver in enumerate(drivers)}
    design: list[np.ndarray] = []
    margins: list[float] = []
    counts = (
        target_rows.groupby("driver_id")["meeting_key"].nunique().astype(int).to_dict()
    )
    for _, group in target_rows.groupby(["meeting_key", "team_name"]):
        rows = list(group.itertuples(index=False))
        for index, first in enumerate(rows):
            for second in rows[index + 1 :]:
                row = np.zeros(len(drivers), dtype=float)
                row[driver_index[str(first.driver_id)]] = 1.0
                row[driver_index[str(second.driver_id)]] = -1.0
                design.append(row)
                margins.append(float(first.target_z - second.target_z))
    if not design:
        return {}, {str(key): int(value) for key, value in counts.items()}
    matrix = np.vstack(design)
    target = np.asarray(margins, dtype=float)
    coefficients = np.linalg.solve(
        matrix.T @ matrix + float(shrinkage) * np.eye(len(drivers)),
        matrix.T @ target,
    )
    scores = {
        driver: float(coefficients[index])
        for driver, index in driver_index.items()
    }
    counts = {str(key): int(value) for key, value in counts.items()}
    return scores, counts


def _rotated_control(scores: Mapping[str, float]) -> dict[str, float]:
    drivers = sorted(scores)
    if len(drivers) < 2:
        return {driver: 0.0 for driver in drivers}
    return {
        driver: float(scores[drivers[(index + 1) % len(drivers)]])
        for index, driver in enumerate(drivers)
    }


def _teammate_edges(target_rows: pd.DataFrame) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for _, group in target_rows.groupby(["meeting_key", "team_name"]):
        drivers = sorted(group["driver_id"].astype(str).unique())
        for index, first in enumerate(drivers):
            for second in drivers[index + 1 :]:
                edges.add((first, second))
    return edges


def _connected_by_teammates(
    first: str, second: str, edges: set[tuple[str, str]]
) -> bool:
    adjacency: dict[str, set[str]] = {}
    for left, right in edges:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    stack = [first]
    visited: set[str] = set()
    while stack:
        node = stack.pop()
        if node == second:
            return True
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adjacency.get(node, ()))
    return False


def rolling_origin_predictions(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """逐完整分站前推；每个赛况头和车手历史量都只看该 origin 之前。"""

    target_contract = config["target_contract"]
    rolling = config["rolling_origin"]
    event_order = (
        frame[["year", "round_index", "meeting_name", "meeting_key"]]
        .drop_duplicates()
        .sort_values(["year", "round_index"])
        .reset_index(drop=True)
    )
    minimum_events = int(rolling["minimum_completed_events"])
    predictions: list[dict[str, Any]] = []
    origin_rankings: list[dict[str, Any]] = []
    prior_oos_pair_residuals: list[float] = []
    for origin in range(minimum_events, len(event_order)):
        train_keys = set(event_order.iloc[:origin]["meeting_key"].astype(str))
        future_key = str(event_order.iloc[origin]["meeting_key"])
        train = frame.loc[frame["meeting_key"].isin(train_keys)].copy()
        future = frame.loc[frame["meeting_key"] == future_key].copy()
        graph = past_only_transfer_graph(train)
        fit = fit_seasonal_model(
            train,
            penalties={},
            blocks=CONTEXT_BLOCKS,
            irls_iterations=int(rolling["irls_iterations"]),
            huber_k=float(rolling["huber_k"]),
        )
        train_targets = _event_targets(
            train,
            train["target_log_pct"].to_numpy(dtype=float) - fit.predict(train),
            minimum_laps=int(target_contract["minimum_clean_laps_per_driver_event"]),
            minimum_drivers=int(target_contract["minimum_drivers_per_team_event"]),
        )
        future_targets = _event_targets(
            future,
            future["target_log_pct"].to_numpy(dtype=float) - fit.predict(future),
            minimum_laps=int(target_contract["minimum_clean_laps_per_driver_event"]),
            minimum_drivers=int(target_contract["minimum_drivers_per_team_event"]),
        )
        scores, counts = _driver_scores(
            train_targets, float(rolling["driver_event_shrinkage"])
        )
        negative = _rotated_control(scores)
        teammate_edges = _teammate_edges(train_targets)
        calibration_residuals = list(prior_oos_pair_residuals)
        sigma = (
            max(
                float(rolling["probability_scale_floor_z"]),
                float(np.sqrt(np.mean(np.square(calibration_residuals)))),
            )
            if len(calibration_residuals) >= 20
            else float("nan")
        )
        origin_rankings.append(
            {
                "origin_index": origin,
                "meeting_key": future_key,
                "scores": scores,
                "transfer_graph": graph,
                "teammate_edges": sorted(teammate_edges),
                "actual_design_manifest": {
                    "numeric_fields": list(NUMERIC_FEATURES),
                    "categorical_design_fields": [
                        BLOCK_COLUMNS[block] for block in fit.blocks
                    ],
                    "encoded_features": [
                        f"{block}={category}"
                        for block in fit.blocks
                        for category in fit.encoders[block].categories_[0].astype(str)
                    ],
                    "target_field": "target_log_pct",
                    "filter_and_group_fields": [
                        "session_name",
                        "year",
                        "round_index",
                        "meeting_key",
                        "team_name",
                        "driver_id",
                        "stint_number",
                    ],
                },
                "prior_oos_pair_residuals": calibration_residuals,
            }
        )
        for row in future_targets.itertuples(index=False):
            driver = str(row.driver_id)
            predictions.append(
                OriginPrediction(
                    origin_index=origin,
                    year=int(row.year),
                    meeting_name=str(row.meeting_name),
                    driver=driver,
                    team=str(row.team_name),
                    target_z=float(row.target_z),
                    prediction_z=float(scores.get(driver, 0.0)),
                    negative_control_z=float(negative.get(driver, 0.0)),
                    predictive_sigma_z=sigma,
                    train_driver_events=int(counts.get(driver, 0)),
                ).__dict__
            )
        for _, group in future_targets.groupby(["meeting_key", "team_name"]):
            rows = list(group.itertuples(index=False))
            for index, first in enumerate(rows):
                for second in rows[index + 1 :]:
                    target_gap = float(first.target_z - second.target_z)
                    prediction_gap = float(
                        scores.get(str(first.driver_id), 0.0)
                        - scores.get(str(second.driver_id), 0.0)
                    )
                    prior_oos_pair_residuals.append(target_gap - prediction_gap)
    return pd.DataFrame(predictions), origin_rankings


def _safe_spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    value = float(spearmanr(left, right).statistic)
    return value if np.isfinite(value) else None


def validation_metrics(
    predictions: pd.DataFrame,
    origin_rankings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """汇总车手级未来表现、成对概率、排序稳定性与预测区间覆盖。"""

    if predictions.empty:
        raise ValueError("PAC rolling-origin 没有生成可验证的未来车手事件。")
    pair_rows: list[dict[str, float]] = []
    origin_audit = {
        int(row["origin_index"]): row for row in origin_rankings
    }
    for _, team_event in predictions.groupby(
        ["origin_index", "year", "meeting_name", "team"]
    ):
        rows = list(team_event.itertuples(index=False))
        for index, first in enumerate(rows):
            for second in rows[index + 1 :]:
                outcome = float(first.target_z > second.target_z)
                audit = origin_audit[int(first.origin_index)]
                calibration = np.asarray(
                    audit["prior_oos_pair_residuals"], dtype=float
                )
                if len(calibration) < 20:
                    continue
                prediction_gap = float(
                    first.prediction_z - second.prediction_z
                )
                negative_gap = float(
                    first.negative_control_z - second.negative_control_z
                )
                probability = float(
                    (np.sum(prediction_gap + calibration > 0) + 0.5)
                    / (len(calibration) + 1.0)
                )
                negative_probability = float(
                    (np.sum(negative_gap + calibration > 0) + 0.5)
                    / (len(calibration) + 1.0)
                )
                q025, q10, q90, q975 = np.quantile(
                    calibration, [0.025, 0.10, 0.90, 0.975]
                )
                edge = tuple(sorted((str(first.driver), str(second.driver))))
                seen_edges = {tuple(values) for values in audit["teammate_edges"]}
                if edge in seen_edges:
                    edge_status = "repeated_pair"
                elif (
                    first.train_driver_events > 0
                    and second.train_driver_events > 0
                    and _connected_by_teammates(edge[0], edge[1], seen_edges)
                ):
                    edge_status = "novel_edge_identifiable"
                else:
                    edge_status = "novel_edge_not_identifiable"
                pair_rows.append(
                    {
                        "year": int(first.year),
                        "outcome": outcome,
                        "probability": probability,
                        "negative_probability": negative_probability,
                        "target_gap_z": float(first.target_z - second.target_z),
                        "prediction_gap_z": prediction_gap,
                        "negative_gap_z": negative_gap,
                        "interval80_low": prediction_gap + float(q10),
                        "interval80_high": prediction_gap + float(q90),
                        "interval95_low": prediction_gap + float(q025),
                        "interval95_high": prediction_gap + float(q975),
                        "edge_status": edge_status,
                    }
                )
    pairs = pd.DataFrame(pair_rows)
    novel = pairs.loc[pairs["edge_status"] == "novel_edge_identifiable"]
    novel_not_identifiable = pairs.loc[
        pairs["edge_status"] == "novel_edge_not_identifiable"
    ]
    repeated = pairs.loc[pairs["edge_status"] == "repeated_pair"]
    # 该相关只比较相邻 origin 的过去期估计，且逐个过去期图分量计算；
    # 它是稳定性诊断，不是对未来跨队速度的 PAC 验收标签。
    stability: list[float] = []
    for previous, current in zip(origin_rankings, origin_rankings[1:]):
        previous_components = previous["transfer_graph"]["driver_component"]
        current_components = current["transfer_graph"]["driver_component"]
        common = sorted(set(previous["scores"]) & set(current["scores"]))
        groups: dict[tuple[int, int], list[str]] = {}
        for driver in common:
            if driver in previous_components and driver in current_components:
                groups.setdefault(
                    (previous_components[driver], current_components[driver]), []
                ).append(driver)
        for drivers in groups.values():
            value = _safe_spearman(
                [previous["scores"][driver] for driver in drivers],
                [current["scores"][driver] for driver in drivers],
            )
            if value is not None:
                stability.append(value)

    error = pairs["target_gap_z"] - pairs["prediction_gap_z"]
    neutral_error = pairs["target_gap_z"]
    negative_error = pairs["target_gap_z"] - pairs["negative_gap_z"]
    brier = float(np.mean(np.square(pairs["probability"] - pairs["outcome"])))
    neutral_brier = 0.25
    negative_brier = float(
        np.mean(np.square(pairs["negative_probability"] - pairs["outcome"]))
    )
    log_score = float(
        -np.mean(
            pairs["outcome"] * np.log(pairs["probability"])
            + (1.0 - pairs["outcome"]) * np.log(1.0 - pairs["probability"])
        )
    )
    mae = float(np.mean(np.abs(error)))
    neutral_mae = float(np.mean(np.abs(neutral_error)))
    negative_mae = float(np.mean(np.abs(negative_error)))
    target_gap = pairs["target_gap_z"].to_numpy(dtype=float)
    predicted_positive = pairs["probability"] >= 0.5
    actual_positive = pairs["outcome"].astype(bool)
    sensitivity = float(predicted_positive[actual_positive].mean())
    specificity = float((~predicted_positive[~actual_positive]).mean())
    season_direction = {
        str(int(year)): float(
            np.mean(
                (group["probability"] >= 0.5)
                == group["outcome"].astype(bool)
            )
        )
        for year, group in pairs.groupby("year")
    }
    return {
        "origins": int(predictions["origin_index"].nunique()),
        "event_clusters": int(
            predictions[["year", "meeting_name"]].drop_duplicates().shape[0]
        ),
        "teams": int(predictions["team"].nunique()),
        "origins_with_past_only_graph_audit": int(len(origin_rankings)),
        "future_driver_events": int(len(predictions)),
        "pairwise_comparisons": int(len(pairs)),
        "repeated_pair_comparisons": int(len(repeated)),
        "identifiable_novel_edge_comparisons": int(len(novel)),
        "not_identifiable_novel_edge_comparisons": int(
            len(novel_not_identifiable)
        ),
        "novel_edge_brier": (
            float(np.mean(np.square(novel["probability"] - novel["outcome"])))
            if len(novel)
            else None
        ),
        "novel_edge_direction_accuracy": (
            float(
                np.mean(
                    (novel["probability"] >= 0.5)
                    == novel["outcome"].astype(bool)
                )
            )
            if len(novel)
            else None
        ),
        "pairwise_direction_accuracy": float(
            np.mean((pairs["probability"] >= 0.5) == pairs["outcome"].astype(bool))
        ),
        "pairwise_balanced_accuracy": (sensitivity + specificity) / 2.0,
        "each_season_direction_accuracy": season_direction,
        "pair_margin_spearman": _safe_spearman(
            pairs["prediction_gap_z"].tolist(), pairs["target_gap_z"].tolist()
        ),
        "event_block_bootstrap_95_low": {
            "balanced_accuracy": None,
            "pair_margin_spearman": None,
            "brier_skill_vs_best_baseline": None,
        },
        "pairwise_brier": brier,
        "neutral_brier": neutral_brier,
        "brier_improvement_vs_neutral": neutral_brier - brier,
        "negative_control_brier": negative_brier,
        "brier_margin_vs_negative_control": negative_brier - brier,
        "pairwise_log_score": log_score,
        "neutral_log_score": log(2.0),
        "pair_gap_mae_z": mae,
        "neutral_pair_gap_mae_z": neutral_mae,
        "pair_gap_mae_improvement_fraction": (
            (neutral_mae - mae) / neutral_mae if neutral_mae else None
        ),
        "negative_control_pair_gap_mae_z": negative_mae,
        "pair_gap_mae_improvement_vs_negative_control": (
            (negative_mae - mae) / negative_mae if negative_mae else None
        ),
        "component_conditional_cross_origin_rank_stability": (
            float(np.median(stability)) if stability else None
        ),
        "pair_gap_predictive_interval_coverage": {
            "80": float(
                np.mean(
                    (target_gap >= pairs["interval80_low"])
                    & (target_gap <= pairs["interval80_high"])
                )
            ),
            "95": float(
                np.mean(
                    (target_gap >= pairs["interval95_low"])
                    & (target_gap <= pairs["interval95_high"])
                )
            ),
        },
        "predictive_distribution_calibration": "prior_origin_oos_pair_gap_residual_empirical_conformal",
        "rank_boundary": "无 future 跨队 rank 标签；稳定性仅在相邻 origin 的过去期识别图分量内计算且不参与通过判定。",
        "wis_improvement_vs_best_baseline": None,
        "benchmark_suite": {
            "neutral_zero_gap": "TESTED",
            "rolling_climatology": "NOT_TESTED",
            "ewma": "NOT_TESTED",
            "bradley_terry": "TESTED_AS_PRIMARY_NOT_INDEPENDENT_BASELINE",
            "novel_edge": "TESTED",
        },
        "negative_control_suite": {
            "odd_even_lap_pseudo_pair": "NOT_TESTED",
            "label_permutation": "NOT_TESTED",
            "reference_shift_invariance": "NOT_TESTED",
            "semi_synthetic_known_effect_coverage": "NOT_TESTED",
        },
    }


def evaluate_publication_gates(
    metrics: Mapping[str, Any],
    transfer_graph: Mapping[str, Any],
    config: Mapping[str, Any],
    pooled_drivers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """严格按预注册阈值判定；任一硬门槛失败，PAC 必须保持 null。"""

    limits = config["publication_gates"]
    weak = set(map(str, transfer_graph.get("weakly_anchored_drivers", [])))
    card_drivers = {
        str(row["driver"])
        for row in pooled_drivers
        if bool(row.get("eligible_for_card"))
    }
    checks = {
        "minimum_event_clusters": metrics["event_clusters"]
        >= limits["minimum_event_clusters"],
        "minimum_pairwise_comparisons": metrics["pairwise_comparisons"]
        >= limits["minimum_pairwise_comparisons"],
        "minimum_teams": metrics["teams"] >= limits["minimum_teams"],
        "minimum_pairwise_balanced_accuracy": metrics[
            "pairwise_balanced_accuracy"
        ]
        >= limits["minimum_pairwise_balanced_accuracy"],
        "minimum_block_bootstrap_balanced_accuracy_95_low": (
            metrics["event_block_bootstrap_95_low"]["balanced_accuracy"]
            is not None
            and metrics["event_block_bootstrap_95_low"]["balanced_accuracy"]
            > limits["minimum_block_bootstrap_balanced_accuracy_95_low"]
        ),
        "minimum_pair_margin_spearman": metrics["pair_margin_spearman"]
        is not None
        and metrics["pair_margin_spearman"]
        >= limits["minimum_pair_margin_spearman"],
        "minimum_block_bootstrap_pair_margin_spearman_95_low": (
            metrics["event_block_bootstrap_95_low"]["pair_margin_spearman"]
            is not None
            and metrics["event_block_bootstrap_95_low"]["pair_margin_spearman"]
            > limits["minimum_block_bootstrap_pair_margin_spearman_95_low"]
        ),
        "minimum_each_season_direction_accuracy": bool(
            metrics["each_season_direction_accuracy"]
        )
        and min(metrics["each_season_direction_accuracy"].values())
        >= limits["minimum_each_season_direction_accuracy"],
        "minimum_brier_skill_vs_best_baseline": False,
        "minimum_block_bootstrap_brier_skill_95_low": False,
        "minimum_log_loss_improvement_vs_best_baseline": False,
        "coverage_80": limits["coverage_80_low"]
        <= metrics["pair_gap_predictive_interval_coverage"]["80"]
        <= limits["coverage_80_high"],
        "coverage_95": limits["coverage_95_low"]
        <= metrics["pair_gap_predictive_interval_coverage"]["95"]
        <= limits["coverage_95_high"],
        "minimum_wis_improvement_vs_best_baseline": metrics[
            "wis_improvement_vs_best_baseline"
        ]
        is not None
        and metrics["wis_improvement_vs_best_baseline"]
        >= limits["minimum_wis_improvement_vs_best_baseline"],
        "minimum_origins_with_past_only_graph_audit": metrics[
            "origins_with_past_only_graph_audit"
        ]
        >= limits["minimum_origins_with_past_only_graph_audit"],
        "minimum_post_freeze_confirmatory_event_clusters": metrics.get(
            "post_freeze_confirmatory_origins", 0
        )
        >= limits["minimum_post_freeze_confirmatory_event_clusters"],
        "minimum_post_freeze_confirmatory_pair_events": metrics.get(
            "post_freeze_confirmatory_pair_events", 0
        )
        >= limits["minimum_post_freeze_confirmatory_pair_events"],
        "minimum_identifiable_novel_edges": metrics[
            "identifiable_novel_edge_comparisons"
        ]
        >= limits["minimum_identifiable_novel_edges"],
        "maximum_novel_edge_brier": metrics["novel_edge_brier"] is not None
        and metrics["novel_edge_brier"] <= limits["maximum_novel_edge_brier"],
        "minimum_novel_edge_direction_accuracy": metrics[
            "novel_edge_direction_accuracy"
        ]
        is not None
        and metrics["novel_edge_direction_accuracy"]
        >= limits["minimum_novel_edge_direction_accuracy"],
        "actual_design_manifest_leakage_free": (
            not limits["require_actual_design_manifest_leakage_free"]
            or not metrics.get("actual_design_prohibited_field_hits")
        ),
        "frozen_0_100_mapping_validation": (
            not limits["require_frozen_0_100_mapping_validation"]
            or bool(metrics.get("frozen_0_100_mapping_validated", False))
        ),
        "complete_benchmark_suite": (
            not limits["require_complete_benchmark_suite"]
            or all(
                status == "TESTED"
                for status in metrics["benchmark_suite"].values()
            )
        ),
        "complete_negative_control_suite": (
            not limits["require_complete_negative_control_suite"]
            or all(
                status == "TESTED"
                for status in metrics["negative_control_suite"].values()
            )
        ),
        "connected_transfer_graph": (
            not limits["require_connected_transfer_graph_for_global_pac"]
            or bool(transfer_graph.get("connected"))
        ),
        "no_weakly_anchored_published_drivers": (
            not limits["require_no_weakly_anchored_published_drivers"]
            or not (card_drivers & weak)
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_gates": [key for key, passed in checks.items() if not passed],
        "thresholds": dict(limits),
    }


def pooled_driver_intervals(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """以 event→driver→stint 重采样生成分量内中心化的 driver_base_z 区间。"""

    rolling = config["rolling_origin"]
    interval_config = config["intervals"]
    target_contract = config["target_contract"]
    fit = fit_seasonal_model(
        frame,
        penalties={},
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(rolling["irls_iterations"]),
        huber_k=float(rolling["huber_k"]),
    )
    work = frame[
        [
            "meeting_key",
            "year",
            "team_name",
            "driver_id",
            "stint_number",
            "target_log_pct",
        ]
    ].copy()
    work["conditional_residual_z"] = (
        work["target_log_pct"].to_numpy(dtype=float) - fit.predict(frame)
    )
    stint = (
        work.groupby(
            ["meeting_key", "year", "team_name", "driver_id", "stint_number"],
            as_index=False,
        )
        .agg(
            conditional_residual_z=("conditional_residual_z", "median"),
            clean_laps=("conditional_residual_z", "size"),
        )
    )
    minimum_laps = int(target_contract["minimum_clean_laps_per_driver_event"])
    event_laps = (
        stint.groupby(["meeting_key", "year", "team_name", "driver_id"])["clean_laps"]
        .sum()
        .rename("event_clean_laps")
        .reset_index()
    )
    stint = stint.merge(
        event_laps,
        on=["meeting_key", "year", "team_name", "driver_id"],
        how="left",
    )
    stint = stint.loc[stint["event_clean_laps"] >= minimum_laps].copy()
    event_keys = sorted(stint["meeting_key"].unique())
    repetitions = int(interval_config["pooled_bootstrap_repetitions"])
    rng = np.random.default_rng(int(interval_config["pooled_bootstrap_seed"]))
    draws: dict[str, list[float]] = {}
    event_counts = (
        stint[["meeting_key", "driver_id"]]
        .drop_duplicates()
        .groupby("driver_id")
        .size()
        .to_dict()
    )
    for _ in range(repetitions):
        selected_events = rng.choice(event_keys, size=len(event_keys), replace=True)
        sampled_rows: list[dict[str, Any]] = []
        for sampled_event_index, event_key in enumerate(selected_events):
            event = stint.loc[stint["meeting_key"] == event_key]
            for (year, team, driver), group in event.groupby(
                ["year", "team_name", "driver_id"]
            ):
                values = group["conditional_residual_z"].to_numpy(dtype=float)
                sampled = rng.choice(values, size=len(values), replace=True)
                sampled_rows.append(
                    {
                        "sampled_event_index": sampled_event_index,
                        "year": int(year),
                        "team_name": str(team),
                        "driver_id": str(driver),
                        "conditional_residual_z": float(np.median(sampled)),
                    }
                )
        sampled_frame = pd.DataFrame(sampled_rows)
        if sampled_frame.empty:
            continue
        counts = sampled_frame.groupby(
            ["sampled_event_index", "team_name"]
        )["driver_id"].transform("nunique")
        sampled_frame = sampled_frame.loc[counts >= 2].copy()
        centers = sampled_frame.groupby(["sampled_event_index", "team_name"])[
            "conditional_residual_z"
        ].transform("mean")
        sampled_frame["target_z"] = -(
            sampled_frame["conditional_residual_z"] - centers
        )
        sampled_frame["meeting_key"] = sampled_frame[
            "sampled_event_index"
        ].astype(str)
        sampled_scores, _ = _driver_scores(
            sampled_frame, float(rolling["driver_event_shrinkage"])
        )
        for driver, score in sampled_scores.items():
            draws.setdefault(driver, []).append(score)

    nominal = (
        stint.groupby(["meeting_key", "year", "team_name", "driver_id"], as_index=False)
        .agg(conditional_residual_z=("conditional_residual_z", "median"))
    )
    nominal_counts = nominal.groupby(["meeting_key", "team_name"])[
        "driver_id"
    ].transform("nunique")
    nominal = nominal.loc[nominal_counts >= 2].copy()
    nominal["target_z"] = -(
        nominal["conditional_residual_z"]
        - nominal.groupby(["meeting_key", "team_name"])[
            "conditional_residual_z"
        ].transform("mean")
    )
    identity = config["individual_identification"]
    shrinkage_grid = [
        float(value)
        for value in config["rolling_origin"][
            "driver_event_shrinkage_sensitivity_grid"
        ]
    ]
    result: list[dict[str, Any]] = []
    minimum_events = int(interval_config["minimum_driver_events_for_card"])
    for driver in sorted(draws):
        values = np.asarray(draws[driver], dtype=float)
        driver_rows = nominal.loc[nominal["driver_id"] == driver]
        distinct_teams = int(driver_rows["team_name"].nunique())
        distinct_seasons = int(driver_rows["year"].nunique())
        n_events = int(event_counts.get(driver, 0))
        grid_maps = [
            _driver_scores(nominal, penalty)[0] for penalty in shrinkage_grid
        ]
        grid_scores = [mapping.get(driver, 0.0) for mapping in grid_maps]
        shrinkage_range = float(max(grid_scores) - min(grid_scores))
        full_score = _driver_scores(
            nominal, float(rolling["driver_event_shrinkage"])
        )[0].get(driver, 0.0)
        leave_scores = []
        for team in sorted(driver_rows["team_name"].unique()):
            reduced = nominal.loc[
                ~(
                    (nominal["driver_id"] == driver)
                    & (nominal["team_name"] == team)
                )
            ]
            leave_scores.append(
                _driver_scores(
                    reduced, float(rolling["driver_event_shrinkage"])
                )[0].get(driver, 0.0)
            )
        leave_team_shift = (
            float(np.max(np.abs(np.asarray(leave_scores) - full_score)))
            if len(leave_scores) > 1
            else None
        )
        identification_passed = bool(
            distinct_teams >= int(identity["minimum_distinct_teams"])
            and distinct_seasons >= int(identity["minimum_distinct_seasons"])
            and shrinkage_range <= float(identity["maximum_shrinkage_grid_range_z"])
            and leave_team_shift is not None
            and leave_team_shift
            <= float(identity["maximum_leave_team_out_shift_z"])
        )
        result.append(
            {
                "driver": driver,
                "driver_events": n_events,
                "distinct_teams": distinct_teams,
                "distinct_seasons": distinct_seasons,
                "conditional_pair_latent_z": float(np.median(values)),
                "ci80_low": float(np.quantile(values, 0.10)),
                "ci80_high": float(np.quantile(values, 0.90)),
                "ci95_low": float(np.quantile(values, 0.025)),
                "ci95_high": float(np.quantile(values, 0.975)),
                "bootstrap_draws": int(len(values)),
                "shrinkage_grid_scores_z": dict(zip(map(str, shrinkage_grid), grid_scores)),
                "shrinkage_grid_range_z": shrinkage_range,
                "leave_team_out_max_shift_z": leave_team_shift,
                "individual_identification_passed": identification_passed,
                "eligible_for_card": n_events >= minimum_events
                and identification_passed,
                "global_pac": None,
                "identification_scope": "within_transfer_component_centered_only",
            }
        )
    return result


def pair_incidence_graph(target_rows: pd.DataFrame) -> dict[str, Any]:
    """只用实际合格 driver-event 形成的 pair incidence 构造发布识别图。"""

    edges = _teammate_edges(target_rows)
    adjacency: dict[str, set[str]] = {}
    for left, right in edges:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    components: list[list[str]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        stack = [root]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency.get(node, ()))
        unseen -= component
        components.append(sorted(component))
    components.sort(key=lambda values: (-len(values), values))
    mapping = {
        driver: index
        for index, component in enumerate(components)
        for driver in component
    }
    return {
        "node_definition": "qualified_driver_event_pair_incidence",
        "edges": [list(edge) for edge in sorted(edges)],
        "n_edges": len(edges),
        "n_components": len(components),
        "connected": len(components) == 1 and bool(components),
        "components": components,
        "driver_component": mapping,
    }


def _purge_direct_edge(
    target_rows: pd.DataFrame, edge: tuple[str, str]
) -> pd.DataFrame:
    purge_keys: set[tuple[str, str]] = set()
    for key, group in target_rows.groupby(["meeting_key", "team_name"]):
        drivers = set(group["driver_id"].astype(str))
        if set(edge) <= drivers:
            purge_keys.add((str(key[0]), str(key[1])))
    mask = [
        (str(meeting), str(team)) not in purge_keys
        for meeting, team in zip(target_rows["meeting_key"], target_rows["team_name"])
    ]
    return target_rows.loc[mask].copy()


def _cluster_rank_interval(
    residual_by_event: Mapping[str, Sequence[float]], level: float
) -> tuple[float, float] | None:
    """每个事件只贡献一个中位残差，并采用 (n+1) 有限样本秩修正。"""

    values = np.sort(
        np.asarray(
            [float(np.median(rows)) for rows in residual_by_event.values() if rows],
            dtype=float,
        )
    )
    n = len(values)
    if n < 2:
        return None
    alpha = (1.0 - float(level)) / 2.0
    low_rank = max(1, int(np.floor((n + 1) * alpha)))
    high_rank = min(n, int(np.ceil((n + 1) * (1.0 - alpha))))
    return float(values[low_rank - 1]), float(values[high_rank - 1])


def strict_simultaneous_split_conformal_radius(
    event_max_scores: Sequence[float], alpha: float
) -> float:
    """标准事件簇 simultaneous split conformal 半径；k>m 时不可用。"""

    values = np.sort(np.asarray(event_max_scores, dtype=float))
    m = len(values)
    if m == 0:
        return float("inf")
    k = int(np.ceil((m + 1) * (1.0 - float(alpha))))
    if k > m:
        return float("inf")
    return float(values[k - 1])


def identified_pair_gap(
    scores: Mapping[str, float],
    graph: Mapping[str, Any],
    first: str,
    second: str,
) -> tuple[str, float | None]:
    components = graph.get("driver_component", {})
    if (
        first not in components
        or second not in components
        or components[first] != components[second]
    ):
        return "not_identifiable", None
    return "identified", float(scores.get(first, 0.0) - scores.get(second, 0.0))


def rolling_origin_pair_predictions_v2(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """v2：事件簇校准、自然新边与 leave-direct-edge-out 分离。"""

    target_contract = config["target_contract"]
    rolling = config["rolling_origin"]
    event_order = (
        frame[["year", "round_index", "meeting_name", "meeting_key"]]
        .drop_duplicates()
        .sort_values(["year", "round_index"])
        .reset_index(drop=True)
    )
    calibration: dict[str, list[float]] = {}
    pair_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    minimum_clusters = int(rolling["minimum_calibration_event_clusters"])
    for origin in range(int(rolling["minimum_completed_events"]), len(event_order)):
        train_keys = set(event_order.iloc[:origin]["meeting_key"].astype(str))
        future_key = str(event_order.iloc[origin]["meeting_key"])
        train = frame.loc[frame["meeting_key"].isin(train_keys)].copy()
        future = frame.loc[frame["meeting_key"] == future_key].copy()
        fit = fit_seasonal_model(
            train,
            penalties={},
            blocks=CONTEXT_BLOCKS,
            irls_iterations=int(rolling["irls_iterations"]),
            huber_k=float(rolling["huber_k"]),
        )
        train_targets = _event_targets(
            train,
            train["target_log_pct"].to_numpy(dtype=float) - fit.predict(train),
            minimum_laps=int(target_contract["minimum_clean_laps_per_driver_event"]),
            minimum_drivers=int(target_contract["minimum_drivers_per_team_event"]),
        )
        future_targets = _event_targets(
            future,
            future["target_log_pct"].to_numpy(dtype=float) - fit.predict(future),
            minimum_laps=int(target_contract["minimum_clean_laps_per_driver_event"]),
            minimum_drivers=int(target_contract["minimum_drivers_per_team_event"]),
        )
        lam = float(rolling["driver_event_shrinkage"])
        scores, counts = _driver_scores(train_targets, lam)
        incidence = pair_incidence_graph(train_targets)
        context_graph = past_only_transfer_graph(train)
        calibration_values = np.asarray(
            [float(np.median(rows)) for rows in calibration.values() if rows],
            dtype=float,
        )
        interval80 = _cluster_rank_interval(calibration, 0.80)
        interval95 = _cluster_rank_interval(calibration, 0.95)
        ready = len(calibration_values) >= minimum_clusters
        current_residuals: list[float] = []
        for _, group in future_targets.groupby(["meeting_key", "team_name"]):
            values = list(group.itertuples(index=False))
            for index, first in enumerate(values):
                for second in values[index + 1 :]:
                    edge = tuple(sorted((str(first.driver_id), str(second.driver_id))))
                    target_gap = float(first.target_z - second.target_z)
                    seen = edge in {tuple(item) for item in incidence["edges"]}
                    if seen:
                        purged = _purge_direct_edge(train_targets, edge)
                        purged_graph = pair_incidence_graph(purged)
                        purged_scores, _ = _driver_scores(purged, lam)
                        identification_status, prediction_gap = identified_pair_gap(
                            purged_scores,
                            purged_graph,
                            str(first.driver_id),
                            str(second.driver_id),
                        )
                        identifiable = identification_status == "identified"
                        edge_class = (
                            "pair_estimator_direct_edge_purge_identifiable"
                            if identifiable
                            else "pair_estimator_direct_edge_purge_not_identifiable"
                        )
                    else:
                        identification_status, prediction_gap = identified_pair_gap(
                            scores,
                            incidence,
                            str(first.driver_id),
                            str(second.driver_id),
                        )
                        identifiable = identification_status == "identified"
                        edge_class = (
                            "natural_novel_edge_identifiable"
                            if identifiable
                            else "natural_novel_edge_not_identifiable"
                        )
                    standard_gap = float(
                        scores.get(str(first.driver_id), 0.0)
                        - scores.get(str(second.driver_id), 0.0)
                    )
                    current_residuals.append(target_gap - standard_gap)
                    probability = None
                    bounds80 = bounds95 = None
                    if ready and identifiable and prediction_gap is not None:
                        probability = float(
                            (np.sum(prediction_gap + calibration_values > 0) + 0.5)
                            / (len(calibration_values) + 1.0)
                        )
                        bounds80 = [
                            prediction_gap + interval80[0],
                            prediction_gap + interval80[1],
                        ]
                        bounds95 = [
                            prediction_gap + interval95[0],
                            prediction_gap + interval95[1],
                        ]
                    pair_rows.append(
                        {
                            "origin_index": origin,
                            "year": int(first.year),
                            "meeting_key": future_key,
                            "meeting_name": str(first.meeting_name),
                            "team": str(first.team_name),
                            "driver_first": str(first.driver_id),
                            "driver_second": str(second.driver_id),
                            "canonical_edge": "|".join(edge),
                            "target_gap_z": target_gap,
                            "prediction_gap_z": prediction_gap,
                            "probability_first_faster": probability,
                            "interval80": bounds80,
                            "interval95": bounds95,
                            "edge_class": edge_class,
                            "identification_status": identification_status,
                            "calibration_event_clusters": len(calibration_values),
                            "scored": probability is not None,
                        }
                    )
        calibration[future_key] = current_residuals
        manifest = {
            "context_head": {
                "numeric_fields": list(NUMERIC_FEATURES),
                "categorical_fields": [BLOCK_COLUMNS[b] for b in fit.blocks],
                "encoded_features": [
                    f"{block}={category}"
                    for block in fit.blocks
                    for category in fit.encoders[block].categories_[0].astype(str)
                ],
                "target": "target_log_pct",
                "weighting": "meeting_driver_equal_weight_plus_huber_irls",
                "irls_iterations": int(rolling["irls_iterations"]),
                "huber_k": float(rolling["huber_k"]),
                "ridge_alpha": 1.0,
            },
            "driver_event_estimand": {
                "statistic": "median_context_residual_z_per_qualified_driver_event",
                "minimum_clean_laps": int(
                    target_contract["minimum_clean_laps_per_driver_event"]
                ),
                "reducer_id": "driver_event_context_residual_median_v2",
                "team_event_centering": "arithmetic_mean_across_qualified_drivers",
            },
            "pair_incidence": {
                "fields": ["meeting_key", "team_name", "driver_id", "target_z"],
                "lambda": lam,
                "weight": "one_per_qualified_pair_event",
            },
            "calibration": {
                "cluster_field": "meeting_key",
                "cluster_statistic": "median_oos_pair_gap_residual",
                "method": "event_clustered_rolling_empirical_predictive_interval",
                "strict_split_conformal": "NOT_TESTED",
                "minimum_event_clusters": minimum_clusters,
            },
            "filters": [
                "session_name==Race",
                "target_log_pct_not_null",
                "minimum_clean_laps_per_driver_event",
                "minimum_two_qualified_drivers_per_team_event",
            ],
        }
        audits.append(
            {
                "origin_index": origin,
                "meeting_key": future_key,
                "qualified_driver_events": int(len(train_targets)),
                "pair_incidence_graph": incidence,
                "team_year_context_graph": context_graph,
                "actual_design_manifest": manifest,
            }
        )
    return pd.DataFrame(pair_rows), audits


def validation_metrics_v2(
    pair_predictions: pd.DataFrame, audits: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    scored = pair_predictions.loc[pair_predictions["scored"]].copy()
    eligible = pair_predictions.loc[
        pair_predictions["identification_status"] == "identified"
    ].copy()
    transferable = scored.loc[
        scored["edge_class"].isin(
            [
                "pair_estimator_direct_edge_purge_identifiable",
                "natural_novel_edge_identifiable",
            ]
        )
    ].copy()
    if scored.empty:
        return {
            "quality_status": "NOT_EVALUABLE",
            "attempted_origins": int(pair_predictions["origin_index"].nunique()),
            "eligible_origins": int(eligible["origin_index"].nunique()),
            "scored_origins": 0,
            "candidate_pair_events": int(len(pair_predictions)),
            "eligible_pair_events": int(len(eligible)),
            "scored_pair_events": 0,
            "event_clusters": 0,
            "driver_events": 0,
            "teams": 0,
            "evaluable_transfer_edges": 0,
        }
    outcome = (scored["target_gap_z"] > 0).astype(float)
    probability = scored["probability_first_faster"].astype(float).clip(1e-12, 1 - 1e-12)
    direction = (probability >= 0.5) == outcome.astype(bool)
    outcome_classes = int(outcome.nunique())
    if outcome_classes == 2:
        positive = outcome.astype(bool)
        balanced_accuracy = float(
            (
                ((probability[positive] >= 0.5).mean())
                + ((probability[~positive] < 0.5).mean())
            )
            / 2.0
        )
    else:
        balanced_accuracy = None
    transfer_outcome = (transferable["target_gap_z"] > 0).astype(float)
    transfer_probability = transferable["probability_first_faster"].astype(float).clip(
        1e-12, 1 - 1e-12
    )
    pair_graph = audits[-1]["pair_incidence_graph"] if audits else {}
    return {
        "quality_status": "EVALUABLE",
        "attempted_origins": int(pair_predictions["origin_index"].nunique()),
        "eligible_origins": int(eligible["origin_index"].nunique()),
        "scored_origins": int(scored["origin_index"].nunique()),
        "candidate_pair_events": int(len(pair_predictions)),
        "eligible_pair_events": int(len(eligible)),
        "scored_pair_events": int(len(scored)),
        "event_clusters": int(scored["meeting_key"].nunique()),
        "driver_events": int(
            scored[
                ["meeting_key", "team", "driver_first"]
            ].drop_duplicates().shape[0]
            + scored[
                ["meeting_key", "team", "driver_second"]
            ].drop_duplicates().shape[0]
        ),
        "teams": int(scored["team"].nunique()),
        "direction_accuracy": float(direction.mean()),
        "balanced_accuracy": balanced_accuracy,
        "brier": float(np.mean(np.square(probability - outcome))),
        "log_score": float(
            -np.mean(
                outcome * np.log(probability)
                + (1.0 - outcome) * np.log(1.0 - probability)
            )
        ),
        "pair_margin_spearman": _safe_spearman(
            scored["prediction_gap_z"].astype(float).tolist(),
            scored["target_gap_z"].astype(float).tolist(),
        ),
        "coverage_80": float(
            np.mean(
                [
                    row.interval80[0] <= row.target_gap_z <= row.interval80[1]
                    for row in scored.itertuples(index=False)
                ]
            )
        ),
        "coverage_95": float(
            np.mean(
                [
                    row.interval95[0] <= row.target_gap_z <= row.interval95[1]
                    for row in scored.itertuples(index=False)
                ]
            )
        ),
        "evaluable_transfer_edges": int(len(transferable)),
        "transfer_unique_events": int(transferable["meeting_key"].nunique()),
        "transfer_unique_edges": int(transferable["canonical_edge"].nunique()),
        "transfer_outcome_classes": int(transfer_outcome.nunique()),
        "natural_novel_edges": int(
            (scored["edge_class"] == "natural_novel_edge_identifiable").sum()
        ),
        "pair_estimator_direct_edge_purge_edges": int(
            (
                scored["edge_class"]
                == "pair_estimator_direct_edge_purge_identifiable"
            ).sum()
        ),
        "transfer_brier": (
            float(
                np.mean(
                    np.square(transfer_probability - transfer_outcome)
                )
            )
            if len(transferable)
            else None
        ),
        "transfer_log_score": (
            float(
                -np.mean(
                    transfer_outcome * np.log(transfer_probability)
                    + (1.0 - transfer_outcome)
                    * np.log(1.0 - transfer_probability)
                )
            )
            if len(transferable)
            else None
        ),
        "publication_pair_graph": pair_graph,
        "team_year_graph_role": "context_diagnostic_only",
        "event_block_ci": "NOT_TESTED",
        "strict_event_cluster_simultaneous_split_conformal": "NOT_TESTED",
        "full_pipeline_direct_edge_purge": "NOT_TESTED",
        "purge_effective_resistance": "NOT_TESTED",
        "event_equal_paired_baseline_lcb": "NOT_TESTED",
        "skip_reasons": {
            "calibration_not_ready": int(
                (
                    (pair_predictions["identification_status"] == "identified")
                    & ~pair_predictions["scored"]
                ).sum()
            ),
            "not_identifiable": int(
                (pair_predictions["identification_status"] == "not_identifiable").sum()
            ),
        },
        "benchmark_suite": {
            "neutral_zero_gap": "TESTED",
            "rolling_climatology": "NOT_TESTED",
            "ewma": "NOT_TESTED",
            "independent_bradley_terry": "NOT_TESTED",
        },
    }


def pooled_driver_intervals_v2(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """v2：nominal 与 bootstrap 均复算同一 driver-event median 和 Laplacian。"""

    rolling = config["rolling_origin"]
    target_contract = config["target_contract"]
    interval = config["intervals"]
    fit = fit_seasonal_model(
        frame,
        penalties={},
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(rolling["irls_iterations"]),
        huber_k=float(rolling["huber_k"]),
    )
    residual = frame["target_log_pct"].to_numpy(dtype=float) - fit.predict(frame)
    nominal = _event_targets(
        frame,
        residual,
        minimum_laps=int(target_contract["minimum_clean_laps_per_driver_event"]),
        minimum_drivers=int(target_contract["minimum_drivers_per_team_event"]),
    )
    lam = float(rolling["driver_event_shrinkage"])
    full_scores, counts = _driver_scores(nominal, lam)
    lap = frame[
        ["meeting_key", "year", "team_name", "driver_id", "stint_number"]
    ].copy()
    lap["conditional_residual_z"] = residual
    event_keys = sorted(lap["meeting_key"].astype(str).unique())
    rng = np.random.default_rng(int(interval["pooled_bootstrap_seed"]))
    draws: dict[str, list[float]] = {driver: [] for driver in full_scores}
    for _ in range(int(interval["pooled_bootstrap_repetitions"])):
        sampled_rows: list[dict[str, Any]] = []
        for sampled_index, event_key in enumerate(
            rng.choice(event_keys, size=len(event_keys), replace=True)
        ):
            event = lap.loc[lap["meeting_key"] == event_key]
            for (year, team, driver), driver_group in event.groupby(
                ["year", "team_name", "driver_id"]
            ):
                stints = list(driver_group["stint_number"].unique())
                sampled_laps: list[float] = []
                for stint in rng.choice(stints, size=len(stints), replace=True):
                    values = driver_group.loc[
                        driver_group["stint_number"] == stint,
                        "conditional_residual_z",
                    ].to_numpy(dtype=float)
                    sampled_laps.extend(
                        rng.choice(values, size=len(values), replace=True).tolist()
                    )
                if len(sampled_laps) >= int(
                    target_contract["minimum_clean_laps_per_driver_event"]
                ):
                    sampled_rows.append(
                        {
                            "meeting_key": f"B{sampled_index}",
                            "year": int(year),
                            "team_name": str(team),
                            "driver_id": str(driver),
                            "conditional_residual_z": float(
                                np.median(sampled_laps)
                            ),
                        }
                    )
        sampled = pd.DataFrame(sampled_rows)
        if sampled.empty:
            continue
        qualified = sampled.groupby(["meeting_key", "team_name"])[
            "driver_id"
        ].transform("nunique")
        sampled = sampled.loc[qualified >= 2].copy()
        sampled["target_z"] = -(
            sampled["conditional_residual_z"]
            - sampled.groupby(["meeting_key", "team_name"])[
                "conditional_residual_z"
            ].transform("mean")
        )
        scores, _ = _driver_scores(sampled, lam)
        for driver, value in scores.items():
            if driver in draws:
                draws[driver].append(value)
    result: list[dict[str, Any]] = []
    for driver in sorted(full_scores):
        values = np.asarray(draws[driver], dtype=float)
        driver_rows = nominal.loc[nominal["driver_id"] == driver]
        subgate = bool(
            len(values) > 0
            and counts.get(driver, 0)
            >= int(interval["minimum_driver_events_for_interval"])
        )
        result.append(
            {
                "driver": driver,
                "qualified_driver_events": int(counts.get(driver, 0)),
                "conditional_pair_latent_z": float(full_scores[driver]),
                "ci80_low": float(np.quantile(values, 0.10)),
                "ci80_high": float(np.quantile(values, 0.90)),
                "ci95_low": float(np.quantile(values, 0.025)),
                "ci95_high": float(np.quantile(values, 0.975)),
                "bootstrap_draws": int(len(values)),
                "distinct_teams": int(driver_rows["team_name"].nunique()),
                "distinct_seasons": int(driver_rows["year"].nunique()),
                "individual_identification_subgate_passed": subgate,
                "final_eligibility": False,
                "boundary": "conditional_pair_latent_z_component_conditional_not_PAC",
            }
        )
    return result
