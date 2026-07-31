"""单场—stint—圈级车辆/车手解释层与周末条件性能前沿。"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from modeling.bayesian_counterfactual import BayesianPosterior, gelman_rubin_rhat


IDENTITY_BLOCKS = (
    "team_year",
    "team_circuit",
    "team_event",
    "driver",
    "driver_year",
    "driver_team",
)


def validate_event_stint_config(config: Mapping[str, Any]) -> None:
    """在运行前拒绝会制造伪上限或不可比较输出的参数。"""

    frontier = config["frontier"]
    race_q = float(frontier["race_ceiling_quantile"])
    qualifying_q = float(frontier["qualifying_ceiling_quantile"])
    baseline_q = float(frontier["usable_baseline_quantile"])
    floor_q = float(frontier["performance_floor_quantile"])
    if not (0.0 < race_q < baseline_q < floor_q < 1.0):
        raise ValueError("Race 前沿参数必须满足 0 < ceiling < baseline < floor < 1。")
    if not 0.0 < qualifying_q < 0.5:
        raise ValueError("Qualifying 上沿分位必须位于 (0, 0.5)。")
    weights = frontier["weekend_session_weights"]
    if set(weights) != {"Qualifying", "Race"}:
        raise ValueError("周末上限权重必须且只能包含 Qualifying 与 Race。")
    if any(float(value) <= 0.0 for value in weights.values()):
        raise ValueError("周末上限权重必须为正。")
    if not np.isclose(sum(float(value) for value in weights.values()), 1.0):
        raise ValueError("周末上限权重之和必须为 1。")
    sensitivity = frontier["parameter_sensitivity"]
    for key in ("race_ceiling_quantiles", "qualifying_ceiling_quantiles"):
        values = [float(value) for value in sensitivity[key]]
        if not values or any(not 0.0 < value < 0.5 for value in values):
            raise ValueError(f"{key} 必须是 (0, 0.5) 内的非空分位数组。")
    qualifying_weights = [float(value) for value in sensitivity["qualifying_weights"]]
    if not qualifying_weights or any(not 0.0 < value < 1.0 for value in qualifying_weights):
        raise ValueError("qualifying_weights 必须是 (0, 1) 内的非空数组。")
    if int(frontier["bootstrap_repetitions"]) < 40:
        raise ValueError("前沿 bootstrap 至少需要 40 次。")
    thresholds = config["detail_thresholds"]
    for key in (
        "minimum_team_event_clean_laps",
        "minimum_driver_event_clean_laps",
        "minimum_stint_clean_laps",
        "minimum_qualifying_team_laps",
        "minimum_qualifying_drivers",
    ):
        if int(thresholds[key]) < 1:
            raise ValueError(f"{key} 必须为正整数。")
    if bool(config["boundaries"]["absolute_qualifying_race_time_merge"]):
        raise ValueError("当前数据不允许直接融合排位与正赛绝对圈时。")


def _effect_mean_map(posterior: BayesianPosterior, block: str) -> dict[str, float]:
    values = np.mean(posterior.block_combined(block), axis=0)
    return {
        label: float(values[index])
        for index, label in enumerate(posterior.data.categories[block])
    }


def _map_effect(
    frame: pd.DataFrame, posterior: BayesianPosterior, block: str, column: str
) -> np.ndarray:
    mapping = _effect_mean_map(posterior, block)
    return frame[column].astype(str).map(mapping).fillna(0.0).to_numpy(dtype=float)


def _block_draws(
    posterior: BayesianPosterior, block: str, label: str
) -> np.ndarray:
    lookup = posterior.data.category_lookup[block]
    if label not in lookup:
        return np.zeros((len(posterior.chains), posterior.draws_per_chain), dtype=float)
    index = lookup[label]
    return np.stack([chain["beta"][block][:, index] for chain in posterior.chains])


def _posterior_summary(values_by_chain: np.ndarray) -> dict[str, float]:
    values = np.asarray(values_by_chain, dtype=float).reshape(-1)
    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "ci80_low": float(np.quantile(values, 0.10)),
        "ci80_high": float(np.quantile(values, 0.90)),
        "ci95_low": float(np.quantile(values, 0.025)),
        "ci95_high": float(np.quantile(values, 0.975)),
        "rhat": gelman_rubin_rhat(np.asarray(values_by_chain, dtype=float)),
    }


def summarize_samples(values: Sequence[float]) -> dict[str, float | None]:
    """汇总 bootstrap/经验样本；没有可用样本时返回显式空值。"""

    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {
            "mean": None,
            "median": None,
            "ci80_low": None,
            "ci80_high": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    return {
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "ci80_low": float(np.quantile(array, 0.10)),
        "ci80_high": float(np.quantile(array, 0.90)),
        "ci95_low": float(np.quantile(array, 0.025)),
        "ci95_high": float(np.quantile(array, 0.975)),
    }


def advantage_to_ms(advantage_z: float, reference_lap_seconds: float) -> float:
    """把正向速度优势 z 转为正向参考圈毫秒收益。"""

    return float(
        -np.expm1(-float(advantage_z) / 100.0)
        * float(reference_lap_seconds)
        * 1000.0
    )


def delta_z_to_ms(delta_z: float, reference_lap_seconds: float) -> float:
    """把正向时间损失 z 转为正向参考圈毫秒损失。"""

    return float(
        np.expm1(float(delta_z) / 100.0)
        * float(reference_lap_seconds)
        * 1000.0
    )


def enrich_race_laps(
    frame: pd.DataFrame,
    context_prediction: np.ndarray,
    posterior: BayesianPosterior,
    race_ceiling_quantile: float,
) -> pd.DataFrame:
    """为每个清洗后 Race 圈保留可加的预测、残差与执行损失。"""

    if len(frame) != len(context_prediction):
        raise ValueError("上下文预测长度与 Race 圈数不一致。")
    work = frame.copy().reset_index(drop=True)
    work["context_prediction_z"] = np.asarray(context_prediction, dtype=float)
    work["posterior_intercept_z"] = float(
        np.mean(posterior.combined("intercept"))
    )
    work["car_state_z"] = (
        _map_effect(work, posterior, "team_year", "team_year")
        + _map_effect(work, posterior, "team_circuit", "team_circuit")
        + _map_effect(work, posterior, "team_event", "team_event")
    )
    work["driver_state_z"] = _map_effect(
        work, posterior, "driver", "driver_id"
    ) + _map_effect(work, posterior, "driver_year", "driver_year")
    work["interaction_state_z"] = _map_effect(
        work, posterior, "driver_team", "driver_team"
    )
    work["identity_expected_z"] = (
        work["posterior_intercept_z"]
        + work["car_state_z"]
        + work["driver_state_z"]
        + work["interaction_state_z"]
    )
    work["expected_target_z"] = (
        work["context_prediction_z"] + work["identity_expected_z"]
    )
    work["context_adjusted_z"] = (
        work["target_log_pct"] - work["context_prediction_z"]
    )
    work["model_residual_z"] = work["target_log_pct"] - work["expected_target_z"]
    work["expected_lap_seconds"] = work["session_reference"] * np.exp(
        work["expected_target_z"] / 100.0
    )
    work["model_residual_ms"] = (
        work["lap_duration"] - work["expected_lap_seconds"]
    ) * 1000.0
    work["driver_neutral_vehicle_z"] = (
        work["context_adjusted_z"]
        - work["posterior_intercept_z"]
        - work["driver_state_z"]
        - work["interaction_state_z"]
    )
    group = ["year", "meeting_name", "driver_id"]
    work["driver_event_frontier_residual_ms"] = work.groupby(group)[
        "model_residual_ms"
    ].transform(lambda values: values.quantile(float(race_ceiling_quantile)))
    work["execution_loss_ms"] = (
        work["model_residual_ms"] - work["driver_event_frontier_residual_ms"]
    ).clip(lower=0.0)
    work["residual_percentile_within_driver_event"] = work.groupby(group)[
        "model_residual_ms"
    ].rank(method="average", pct=True)
    return work


def enrich_qualifying_laps(
    frame: pd.DataFrame,
    context_prediction: np.ndarray,
    posterior: BayesianPosterior,
) -> pd.DataFrame:
    """用 Race 学到的车手/适配先验中和排位准确推圈，不借用 Race 车辆状态。"""

    if len(frame) != len(context_prediction):
        raise ValueError("上下文预测长度与 Qualifying 圈数不一致。")
    work = frame.copy().reset_index(drop=True)
    work["context_prediction_z"] = np.asarray(context_prediction, dtype=float)
    work["driver_state_z"] = _map_effect(
        work, posterior, "driver", "driver_id"
    ) + _map_effect(work, posterior, "driver_year", "driver_year")
    work["interaction_state_z"] = _map_effect(
        work, posterior, "driver_team", "driver_team"
    )
    work["context_adjusted_z"] = (
        work["target_log_pct"] - work["context_prediction_z"]
    )
    work["driver_neutral_vehicle_z"] = (
        work["context_adjusted_z"]
        - work["driver_state_z"]
        - work["interaction_state_z"]
    )
    return work


def _frontier_point(
    event: pd.DataFrame,
    value_column: str,
    teams: Sequence[str],
    quantile: float,
    baseline_quantile: float,
    floor_quantile: float,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for team in teams:
        subset = event.loc[event["team_name"] == team]
        driver_ceiling = [
            float(group[value_column].quantile(quantile))
            for _, group in subset.groupby("driver_id", observed=True)
        ]
        driver_baseline = [
            float(group[value_column].quantile(baseline_quantile))
            for _, group in subset.groupby("driver_id", observed=True)
        ]
        driver_floor = [
            float(group[value_column].quantile(floor_quantile))
            for _, group in subset.groupby("driver_id", observed=True)
        ]
        if not driver_ceiling:
            continue
        output[team] = {
            "strict_raw_z": float(np.min(driver_ceiling)),
            "robust_raw_z": float(np.median(driver_ceiling)),
            "baseline_raw_z": float(np.median(driver_baseline)),
            "floor_raw_z": float(np.median(driver_floor)),
            "driver_frontiers_z": driver_ceiling,
            "drivers": int(len(driver_ceiling)),
            "laps": int(len(subset)),
        }
    if output:
        robust_center = float(np.mean([row["robust_raw_z"] for row in output.values()]))
        strict_center = float(np.mean([row["strict_raw_z"] for row in output.values()]))
        for row in output.values():
            row["robust_advantage_z"] = robust_center - row["robust_raw_z"]
            row["strict_advantage_z"] = strict_center - row["strict_raw_z"]
    return output


def bootstrap_event_frontiers(
    event: pd.DataFrame,
    value_column: str,
    teams: Sequence[str],
    quantile: float,
    repetitions: int,
    seed: int,
) -> dict[str, dict[str, np.ndarray]]:
    """按车手等权、车手内重采样，估计单场车辆前沿不确定性。"""

    rng = np.random.default_rng(int(seed))
    samples = {
        team: {
            "robust_raw_z": np.full(repetitions, np.nan),
            "strict_raw_z": np.full(repetitions, np.nan),
        }
        for team in teams
    }
    values_by_team_driver: dict[str, list[np.ndarray]] = {}
    for team in teams:
        values_by_team_driver[team] = [
            group[value_column].to_numpy(dtype=float)
            for _, group in event.loc[event["team_name"] == team].groupby(
                "driver_id", observed=True
            )
            if len(group)
        ]
    for repetition in range(repetitions):
        for team, driver_arrays in values_by_team_driver.items():
            if not driver_arrays:
                continue
            frontiers: list[float] = []
            for values in driver_arrays:
                resampled = rng.choice(values, size=len(values), replace=True)
                frontiers.append(float(np.quantile(resampled, quantile)))
            samples[team]["robust_raw_z"][repetition] = float(np.median(frontiers))
            samples[team]["strict_raw_z"][repetition] = float(np.min(frontiers))
    for key in ("robust", "strict"):
        matrix = np.stack([samples[team][f"{key}_raw_z"] for team in teams])
        center = np.nanmean(matrix, axis=0)
        for team_index, team in enumerate(teams):
            samples[team][f"{key}_advantage_z"] = center - matrix[team_index]
    return samples


def _stable_seed(base_seed: int, year: int, meeting_name: str, offset: int) -> int:
    token = f"{year}|{meeting_name}|{offset}".encode("utf-8")
    digest = int.from_bytes(hashlib.sha256(token).digest()[:4], "big")
    return int((base_seed + digest) % (2**32 - 1))


def _parameter_grid_rank_stability(
    race_event: pd.DataFrame,
    qualifying_event: pd.DataFrame,
    teams: Sequence[str],
    frontier: Mapping[str, Any],
) -> tuple[int, dict[str, dict[str, float | int]]]:
    """在预注册的分位数/会话权重网格中检查单场排名边界。"""

    sensitivity = frontier["parameter_sensitivity"]
    ranks: dict[str, list[int]] = {team: [] for team in teams}
    combinations = 0
    for race_quantile in sensitivity["race_ceiling_quantiles"]:
        race = _frontier_point(
            race_event,
            "driver_neutral_vehicle_z",
            teams,
            float(race_quantile),
            float(frontier["usable_baseline_quantile"]),
            float(frontier["performance_floor_quantile"]),
        )
        for qualifying_quantile in sensitivity["qualifying_ceiling_quantiles"]:
            qualifying = _frontier_point(
                qualifying_event,
                "driver_neutral_vehicle_z",
                teams,
                float(qualifying_quantile),
                0.50,
                0.90,
            )
            for qualifying_weight in sensitivity["qualifying_weights"]:
                values: dict[str, float] = {}
                for team in teams:
                    if team in qualifying:
                        values[team] = (
                            float(qualifying_weight)
                            * float(qualifying[team]["robust_advantage_z"])
                            + (1.0 - float(qualifying_weight))
                            * float(race[team]["robust_advantage_z"])
                        )
                    else:
                        values[team] = float(race[team]["robust_advantage_z"])
                order = sorted(teams, key=lambda team: values[team], reverse=True)
                for rank, team in enumerate(order, 1):
                    ranks[team].append(rank)
                combinations += 1
    output = {
        team: {
            "rank_min": int(np.min(team_ranks)),
            "rank_max": int(np.max(team_ranks)),
            "median_rank": float(np.median(team_ranks)),
            "p_rank_1": float(np.mean(np.asarray(team_ranks) == 1)),
            "p_same_as_median_rank": float(
                np.mean(np.asarray(team_ranks) == int(round(float(np.median(team_ranks)))))
            ),
        }
        for team, team_ranks in ranks.items()
    }
    return combinations, output


def _car_posterior_features(
    posterior: BayesianPosterior,
    year: int,
    meeting_name: str,
    circuit_name: str,
    teams: Sequence[str],
) -> dict[str, dict[str, Any]]:
    raw: dict[str, np.ndarray] = {}
    for team in teams:
        raw[team] = (
            _block_draws(posterior, "team_year", f"{year}|{team}")
            + _block_draws(posterior, "team_circuit", f"{team}|{circuit_name}")
            + _block_draws(
                posterior, "team_event", f"{year}|{meeting_name}|{team}"
            )
        )
    stack = np.stack([raw[team] for team in teams])
    center = np.mean(stack, axis=0)
    output: dict[str, dict[str, Any]] = {}
    for team_index, team in enumerate(teams):
        advantage = center - raw[team]
        rank = 1 + np.sum(stack < raw[team][None, :, :], axis=0)
        output[team] = {
            "advantage_z": _posterior_summary(advantage),
            "expected_rank": float(np.mean(rank)),
            "p_rank_1": float(np.mean(rank == 1)),
        }
    return output


def _driver_posterior_features(
    posterior: BayesianPosterior,
    year: int,
    drivers: Sequence[str],
) -> dict[str, dict[str, Any]]:
    raw = {
        driver: _block_draws(posterior, "driver", driver)
        + _block_draws(posterior, "driver_year", f"{year}|{driver}")
        for driver in drivers
    }
    stack = np.stack([raw[driver] for driver in drivers])
    center = np.mean(stack, axis=0)
    output: dict[str, dict[str, Any]] = {}
    for driver_index, driver in enumerate(drivers):
        advantage = center - raw[driver]
        rank = 1 + np.sum(stack < raw[driver][None, :, :], axis=0)
        output[driver] = {
            "advantage_z": _posterior_summary(advantage),
            "expected_rank": float(np.mean(rank)),
            "p_rank_1": float(np.mean(rank == 1)),
        }
    return output


def rank_alignment(
    pace_rank: int | None, classification_rank: int | None, tolerance: int
) -> str:
    """只描述赛果与干净圈配速是否同向，不替未建模原因下结论。"""

    if pace_rank is None or classification_rank is None:
        return "unavailable"
    delta = int(classification_rank) - int(pace_rank)
    if abs(delta) <= int(tolerance):
        return "compatible_with_clean_pace"
    if delta < 0:
        return "result_better_than_clean_pace"
    return "result_worse_than_clean_pace"


def _classification_lookup(
    references: Sequence[Mapping[str, Any]], reporting_teams: Sequence[str]
) -> dict[tuple[int, str], dict[str, Any]]:
    output: dict[tuple[int, str], dict[str, Any]] = {}
    for reference in references:
        year = int(reference["year"])
        for event in reference["events"]:
            if event["session_name"] != "Race":
                continue
            drivers = [
                row for row in event.get("drivers", []) if row["team"] in reporting_teams
            ]
            drivers.sort(key=lambda row: int(row["position"]))
            driver_rank = {
                str(row["driver"]): index + 1 for index, row in enumerate(drivers)
            }
            driver_position = {
                str(row["driver"]): int(row["position"]) for row in drivers
            }
            teams = [row for row in event["teams"] if row["team"] in reporting_teams]
            teams.sort(key=lambda row: (-float(row["points_proxy"]), int(row["best_position"])))
            team_rank = {str(row["team"]): index + 1 for index, row in enumerate(teams)}
            output[(year, str(event["meeting_name"]))] = {
                "driver_rank": driver_rank,
                "driver_position": driver_position,
                "team_rank": team_rank,
                "source": reference["mode"],
            }
    return output


def build_stint_features(
    race_laps: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """把圈级解释汇总到每个车手 stint，并保留超额衰退与稳定性。"""

    minimum = int(config["detail_thresholds"]["minimum_stint_clean_laps"])
    rows: list[dict[str, Any]] = []
    group_columns = [
        "year",
        "meeting_name",
        "driver_id",
        "team_name",
        "stint_number",
        "compound",
    ]
    for keys, group in race_laps.groupby(group_columns, observed=True, sort=True):
        year, meeting, driver, team, stint, compound = keys
        residual = group["model_residual_ms"].to_numpy(dtype=float)
        median = float(np.median(residual))
        mad = float(1.4826 * np.median(np.abs(residual - median)))
        tyre_age = pd.to_numeric(group["tyre_age"], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(tyre_age) & np.isfinite(residual)
        slope: float | None = None
        if finite.sum() >= minimum and np.ptp(tyre_age[finite]) >= 2.0:
            slope = float(np.polyfit(tyre_age[finite], residual[finite], 1)[0])
        rows.append(
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "driver": str(driver),
                "team": str(team),
                "stint_number": int(stint),
                "compound": str(compound),
                "clean_laps": int(len(group)),
                "lap_start": int(group["lap_number"].min()),
                "lap_end": int(group["lap_number"].max()),
                "tyre_age_start": (
                    float(np.nanmin(tyre_age)) if np.any(np.isfinite(tyre_age)) else None
                ),
                "tyre_age_end": (
                    float(np.nanmax(tyre_age)) if np.any(np.isfinite(tyre_age)) else None
                ),
                "actual_lap_seconds_p10": float(group["lap_duration"].quantile(0.10)),
                "actual_lap_seconds_p50": float(group["lap_duration"].median()),
                "model_expected_lap_seconds_p50": float(
                    group["expected_lap_seconds"].median()
                ),
                "pace_residual_ms_p10": float(np.quantile(residual, 0.10)),
                "pace_residual_ms_p50": median,
                "typical_execution_loss_ms": float(
                    group["execution_loss_ms"].median()
                ),
                "consistency_mad_ms": mad,
                "degradation_residual_ms_per_tyre_lap": slope,
                "confidence": "usable" if len(group) >= minimum else "low_sample",
                "interpretation": (
                    "degradation_residual 是相对全场上下文轮胎曲线的超额斜率，"
                    "不是纯轮胎物理衰退。"
                ),
            }
        )
    return rows


def _confidence_grade(
    race_laps: int,
    race_drivers: int,
    qualifying_laps: int,
    qualifying_drivers: int,
    thresholds: Mapping[str, Any],
) -> tuple[str, list[str]]:
    flags: list[str] = []
    if race_laps < int(thresholds["minimum_team_event_clean_laps"]):
        flags.append("race_laps_below_minimum")
    if race_drivers < 2:
        flags.append("race_single_driver_frontier")
    if qualifying_laps < int(thresholds["minimum_qualifying_team_laps"]):
        flags.append("qualifying_laps_below_minimum")
    if qualifying_drivers < int(thresholds["minimum_qualifying_drivers"]):
        flags.append("qualifying_driver_coverage_below_minimum")
    if not flags:
        return "high", flags
    if "race_laps_below_minimum" not in flags and "race_single_driver_frontier" not in flags:
        return "medium", flags
    return "low", flags


def build_event_reports(
    race_laps: pd.DataFrame,
    qualifying_laps: pd.DataFrame,
    posterior: BayesianPosterior,
    reporting_teams: Sequence[str],
    config: Mapping[str, Any],
    classification_references: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """生成每一场大赛的车辆、车手、上限和赛果一致性合同。"""

    frontier = config["frontier"]
    thresholds = config["detail_thresholds"]
    repetitions = int(frontier["bootstrap_repetitions"])
    base_seed = int(frontier["bootstrap_seed"])
    reference_ms = float(frontier["reference_lap_seconds"])
    classification = _classification_lookup(classification_references, reporting_teams)
    events: list[dict[str, Any]] = []
    group_columns = ["year", "meeting_name"]
    for (year, meeting), event in race_laps.groupby(group_columns, observed=True, sort=True):
        year = int(year)
        meeting = str(meeting)
        event = event.loc[event["team_name"].isin(reporting_teams)].copy()
        if event.empty:
            continue
        q_event = qualifying_laps.loc[
            (qualifying_laps["year"] == year)
            & (qualifying_laps["meeting_name"] == meeting)
            & qualifying_laps["team_name"].isin(reporting_teams)
        ].copy()
        teams = [team for team in reporting_teams if team in set(event["team_name"])]
        circuit = str(event["circuit_name"].iloc[0])
        lap_reference = float(event["session_reference"].median())
        race_point = _frontier_point(
            event,
            "driver_neutral_vehicle_z",
            teams,
            float(frontier["race_ceiling_quantile"]),
            float(frontier["usable_baseline_quantile"]),
            float(frontier["performance_floor_quantile"]),
        )
        race_boot = bootstrap_event_frontiers(
            event,
            "driver_neutral_vehicle_z",
            teams,
            float(frontier["race_ceiling_quantile"]),
            repetitions,
            _stable_seed(base_seed, year, meeting, 1),
        )
        q_point = _frontier_point(
            q_event,
            "driver_neutral_vehicle_z",
            teams,
            float(frontier["qualifying_ceiling_quantile"]),
            0.50,
            0.90,
        )
        if q_event.empty:
            q_boot = {
                team: {
                    "robust_advantage_z": np.full(repetitions, np.nan),
                    "strict_advantage_z": np.full(repetitions, np.nan),
                }
                for team in teams
            }
        else:
            q_boot = bootstrap_event_frontiers(
                q_event,
                "driver_neutral_vehicle_z",
                teams,
                float(frontier["qualifying_ceiling_quantile"]),
                repetitions,
                _stable_seed(base_seed, year, meeting, 2),
            )
        posterior_car = _car_posterior_features(
            posterior, year, meeting, circuit, teams
        )
        parameter_combinations, parameter_stability = _parameter_grid_rank_stability(
            event, q_event, teams, frontier
        )
        weekend_samples: dict[str, np.ndarray] = {}
        weekend_strict_samples: dict[str, np.ndarray] = {}
        car_rows: list[dict[str, Any]] = []
        weights = frontier["weekend_session_weights"]
        for team in teams:
            available: list[tuple[float, np.ndarray]] = [
                (float(weights["Race"]), race_boot[team]["robust_advantage_z"])
            ]
            strict_available: list[tuple[float, np.ndarray]] = [
                (float(weights["Race"]), race_boot[team]["strict_advantage_z"])
            ]
            if team in q_point:
                available.append(
                    (
                        float(weights["Qualifying"]),
                        q_boot[team]["robust_advantage_z"],
                    )
                )
                strict_available.append(
                    (
                        float(weights["Qualifying"]),
                        q_boot[team]["strict_advantage_z"],
                    )
                )
            weight_sum = sum(weight for weight, _ in available)
            weekend = sum(weight * values for weight, values in available) / weight_sum
            weekend_samples[team] = weekend
            strict_weight_sum = sum(weight for weight, _ in strict_available)
            weekend_strict_samples[team] = (
                sum(weight * values for weight, values in strict_available)
                / strict_weight_sum
            )
        weekend_stack = np.stack([weekend_samples[team] for team in teams])
        weekend_ranks = {
            team: 1
            + int(
                np.sum(
                    [
                        float(np.nanmedian(weekend_samples[other]))
                        > float(np.nanmedian(weekend_samples[team]))
                        for other in teams
                    ]
                )
            )
            for team in teams
        }
        for team_index, team in enumerate(teams):
            race_row = race_point[team]
            q_row = q_point.get(team)
            pace_rank = weekend_ranks[team]
            result_rank = classification.get((year, meeting), {}).get(
                "team_rank", {}
            ).get(team)
            grade, flags = _confidence_grade(
                int(race_row["laps"]),
                int(race_row["drivers"]),
                int(q_row["laps"]) if q_row else 0,
                int(q_row["drivers"]) if q_row else 0,
                thresholds,
            )
            weekend_summary = summarize_samples(weekend_samples[team])
            weekend_strict_summary = summarize_samples(weekend_strict_samples[team])
            weekend_median = weekend_summary["median"]
            car_rows.append(
                {
                    "team": team,
                    "weekend_ceiling_rank": pace_rank,
                    "weekend_ceiling_advantage_z": weekend_summary,
                    "weekend_ceiling_advantage_90s_ms": (
                        advantage_to_ms(float(weekend_median), reference_ms)
                        if weekend_median is not None
                        else None
                    ),
                    "p_weekend_ceiling_rank_1": float(
                        np.mean(
                            weekend_samples[team]
                            >= np.nanmax(weekend_stack, axis=0)
                        )
                    ),
                    "weekend_theoretical_upper_proxy_advantage_z": weekend_strict_summary,
                    "upper_proxy_minus_robust_ceiling_z": (
                        float(weekend_strict_summary["median"])
                        - float(weekend_summary["median"])
                        if weekend_strict_summary["median"] is not None
                        and weekend_summary["median"] is not None
                        else None
                    ),
                    "race_ceiling_advantage_z": summarize_samples(
                        race_boot[team]["robust_advantage_z"]
                    ),
                    "race_strict_best_driver_advantage_z": summarize_samples(
                        race_boot[team]["strict_advantage_z"]
                    ),
                    "qualifying_ceiling_advantage_z": (
                        summarize_samples(q_boot[team]["robust_advantage_z"])
                        if q_row
                        else None
                    ),
                    "qualifying_strict_best_driver_advantage_z": (
                        summarize_samples(q_boot[team]["strict_advantage_z"])
                        if q_row
                        else None
                    ),
                    "race_usable_baseline_raw_z": float(race_row["baseline_raw_z"]),
                    "race_performance_floor_raw_z": float(race_row["floor_raw_z"]),
                    "race_drivability_width_90s_ms": delta_z_to_ms(
                        float(race_row["floor_raw_z"] - race_row["strict_raw_z"]),
                        reference_ms,
                    ),
                    "posterior_pure_car_advantage_z": posterior_car[team][
                        "advantage_z"
                    ],
                    "posterior_pure_car_p_rank_1": posterior_car[team]["p_rank_1"],
                    "race_clean_laps": int(race_row["laps"]),
                    "race_drivers": int(race_row["drivers"]),
                    "qualifying_accurate_laps": int(q_row["laps"]) if q_row else 0,
                    "qualifying_drivers": int(q_row["drivers"]) if q_row else 0,
                    "classification_proxy_rank_four_team": result_rank,
                    "result_alignment": rank_alignment(
                        pace_rank,
                        int(result_rank) if result_rank is not None else None,
                        int(thresholds["rank_tolerance"]),
                    ),
                    "confidence": grade,
                    "confidence_flags": flags,
                    "parameter_grid_rank_stability": parameter_stability[team],
                }
            )
        car_rows.sort(key=lambda row: int(row["weekend_ceiling_rank"]))

        driver_groups = {
            str(driver): group
            for driver, group in event.groupby("driver_id", observed=True)
        }
        drivers = sorted(driver_groups)
        driver_posterior = _driver_posterior_features(posterior, year, drivers)
        observed_order = sorted(
            drivers,
            key=lambda driver: float(driver_groups[driver]["context_adjusted_z"].median()),
        )
        expected_order = sorted(
            drivers,
            key=lambda driver: float(driver_groups[driver]["identity_expected_z"].median()),
        )
        observed_rank = {driver: index + 1 for index, driver in enumerate(observed_order)}
        expected_rank = {driver: index + 1 for index, driver in enumerate(expected_order)}
        classification_event = classification.get((year, meeting), {})
        driver_rows: list[dict[str, Any]] = []
        for driver in drivers:
            group = driver_groups[driver]
            team = str(group["team_name"].iloc[0])
            driver_frontier = float(
                group["driver_neutral_vehicle_z"].quantile(
                    float(frontier["race_ceiling_quantile"])
                )
            )
            team_frontier = float(race_point[team]["strict_raw_z"])
            result_rank = classification_event.get("driver_rank", {}).get(driver)
            clean_laps = int(len(group))
            confidence = (
                "usable"
                if clean_laps >= int(thresholds["minimum_driver_event_clean_laps"])
                else "low_sample"
            )
            driver_rows.append(
                {
                    "driver": driver,
                    "team": team,
                    "clean_laps": clean_laps,
                    "stints": int(group["stint_number"].nunique()),
                    "posterior_pure_driver_advantage_z": driver_posterior[driver][
                        "advantage_z"
                    ],
                    "posterior_pure_driver_p_rank_1": driver_posterior[driver][
                        "p_rank_1"
                    ],
                    "model_expected_combination_rank_four_team": int(
                        expected_rank[driver]
                    ),
                    "observed_clean_pace_rank_four_team": int(observed_rank[driver]),
                    "classification_proxy_rank_four_team": result_rank,
                    "classification_proxy_position_overall": classification_event.get(
                        "driver_position", {}
                    ).get(driver),
                    "result_alignment": rank_alignment(
                        int(observed_rank[driver]),
                        int(result_rank) if result_rank is not None else None,
                        int(thresholds["rank_tolerance"]),
                    ),
                    "context_adjusted_pace_z_p10": float(
                        group["context_adjusted_z"].quantile(0.10)
                    ),
                    "context_adjusted_pace_z_p50": float(
                        group["context_adjusted_z"].median()
                    ),
                    "model_residual_ms_p50": float(group["model_residual_ms"].median()),
                    "typical_execution_loss_ms": float(
                        group["execution_loss_ms"].median()
                    ),
                    "negative_tail_probability": float(
                        np.mean(group["residual_percentile_within_driver_event"] > 0.90)
                    ),
                    "consistency_mad_ms": float(
                        1.4826
                        * np.median(
                            np.abs(
                                group["model_residual_ms"]
                                - group["model_residual_ms"].median()
                            )
                        )
                    ),
                    "peak_gap_to_team_ceiling_90s_ms": delta_z_to_ms(
                        driver_frontier - team_frontier, reference_ms
                    ),
                    "confidence": confidence,
                }
            )
        driver_rows.sort(key=lambda row: int(row["observed_clean_pace_rank_four_team"]))
        events.append(
            {
                "year": year,
                "meeting_name": meeting,
                "circuit_name": circuit,
                "race_reference_lap_seconds": lap_reference,
                "race_clean_laps_four_team": int(len(event)),
                "qualifying_accurate_laps_four_team": int(len(q_event)),
                "setup_linkage": config["boundaries"]["setup_linkage"],
                "absolute_qualifying_race_time_merge": False,
                "frontier_parameter_grid_combinations": parameter_combinations,
                "cars": car_rows,
                "drivers": driver_rows,
                "classification_proxy_available": bool(classification_event),
                "result_explanation_boundary": (
                    "只判断干净圈配速与分类代理是否相容；策略、可靠性、交通、事故和罚则"
                    "尚未进入 V_result，排名差不得自动归给车手或车辆。"
                ),
            }
        )
    return events
