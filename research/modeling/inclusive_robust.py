"""全量候选圈的样本外稳健软权重模型。

本模块只把圈时物理不可读、圈距严重不完整或逐点样本极少的记录硬排除。
其余交通、非绿旗、进出站、质量标记和湿地配方均进入同一条件模型，并以
观测质量先验权重与样本外残差 Huber 权重连续降权。模型是公开遥测条件
代理，不是因果模型、真实燃油/胎态估计或车队内部模拟。
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


def _finite_arrays(
    values: Sequence[float] | pd.Series,
    weights: Sequence[float] | pd.Series,
) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(values, dtype=float)
    weight = np.asarray(weights, dtype=float)
    finite = (
        np.isfinite(array)
        & np.isfinite(weight)
        & (weight > 0.0)
    )
    return array[finite], weight[finite]


def weighted_median(
    values: Sequence[float] | pd.Series,
    weights: Sequence[float] | pd.Series,
) -> float | None:
    """返回确定性的加权中位数；零权重和非有限值不参与。"""

    array, weight = _finite_arrays(values, weights)
    if not len(array):
        return None
    order = np.argsort(array, kind="stable")
    array = array[order]
    weight = weight[order]
    threshold = 0.5 * float(weight.sum())
    index = int(np.searchsorted(np.cumsum(weight), threshold, side="left"))
    return float(array[min(index, len(array) - 1)])


def weighted_mad(
    values: Sequence[float] | pd.Series,
    weights: Sequence[float] | pd.Series,
) -> tuple[float | None, float | None]:
    """返回加权中位数及 1.4826 缩放的加权 MAD。"""

    array, weight = _finite_arrays(values, weights)
    if not len(array):
        return None, None
    center = weighted_median(array, weight)
    if center is None:
        return None, None
    deviation = weighted_median(np.abs(array - center), weight)
    return center, (
        float(1.4826 * deviation) if deviation is not None else None
    )


def weighted_theil_sen_slope(
    x_values: Sequence[float] | pd.Series,
    y_values: Sequence[float] | pd.Series,
    weights: Sequence[float] | pd.Series,
) -> float | None:
    """以两圈权重乘积作为两两斜率权重的稳健 Stint 斜率。"""

    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    weight = np.asarray(weights, dtype=float)
    finite = (
        np.isfinite(x)
        & np.isfinite(y)
        & np.isfinite(weight)
        & (weight > 0.0)
    )
    x = x[finite]
    y = y[finite]
    weight = weight[finite]
    if len(x) < 2 or np.unique(x).size < 2:
        return None
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    weight = weight[order]
    delta_x = x[np.newaxis, :] - x[:, np.newaxis]
    delta_y = y[np.newaxis, :] - y[:, np.newaxis]
    pair_weight = weight[np.newaxis, :] * weight[:, np.newaxis]
    usable = np.triu(np.ones(delta_x.shape, dtype=bool), k=1)
    usable &= delta_x != 0.0
    usable &= pair_weight > 0.0
    if not usable.any():
        return None
    return weighted_median(
        delta_y[usable] / delta_x[usable],
        pair_weight[usable],
    )


def kish_effective_sample_size(
    weights: Sequence[float] | pd.Series,
) -> float:
    """Kish 有效样本量，仅描述权重集中度，不替代物理圈数。"""

    weight = np.asarray(weights, dtype=float)
    weight = weight[np.isfinite(weight) & (weight > 0.0)]
    if not len(weight):
        return 0.0
    denominator = float(np.square(weight).sum())
    return (
        float(np.square(weight.sum()) / denominator)
        if denominator > 0.0
        else 0.0
    )


def _numeric(
    frame: pd.DataFrame,
    column: str,
    default: float = np.nan,
) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _boolean(
    frame: pd.DataFrame,
    column: str,
    default: bool = False,
) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=bool)
    return frame[column].astype("boolean").fillna(default).astype(bool)


def _stable_fold(row: Any, folds: int) -> int:
    token = (
        f"{getattr(row, 'year', '')}|"
        f"{getattr(row, 'meeting_name', '')}|"
        f"{getattr(row, 'driver_id', '')}|"
        f"{getattr(row, 'lap_number', '')}"
    ).encode("utf-8")
    digest = hashlib.sha256(token).digest()
    return int.from_bytes(digest[:8], "big") % folds


def _one_hot_columns(
    frame: pd.DataFrame,
    column: str,
    prefix: str,
) -> tuple[list[np.ndarray], list[str], list[str]]:
    values = frame[column].astype("string").fillna("<MISSING>")
    categories = sorted(values.unique().tolist())
    arrays: list[np.ndarray] = []
    names: list[str] = []
    groups: list[str] = []
    for category in categories[1:]:
        arrays.append(values.eq(category).to_numpy(dtype=float))
        names.append(f"{prefix}={category}")
        groups.append("performance")
    return arrays, names, groups


def _design_matrix(
    frame: pd.DataFrame,
    settings: Mapping[str, Any],
) -> tuple[np.ndarray, list[str], list[str]]:
    """构造固定尺度加性设计；不使用结果、名次或慢圈标签作特征。"""

    arrays: list[np.ndarray] = [np.ones(len(frame), dtype=float)]
    names = ["intercept"]
    groups = ["intercept"]

    for column, prefix in (
        ("team_name", "team"),
        ("driver_id", "driver"),
        ("compound", "compound"),
    ):
        encoded, encoded_names, encoded_groups = _one_hot_columns(
            frame, column, prefix
        )
        arrays.extend(encoded)
        names.extend(encoded_names)
        groups.extend(encoded_groups)

    tyre_age = _numeric(frame, "tyre_age", 0.0)
    tyre_missing = tyre_age.isna()
    tyre_age = tyre_age.fillna(0.0).clip(lower=0.0, upper=80.0)
    arrays.extend(
        [
            (tyre_age / 20.0).to_numpy(dtype=float),
            (np.log1p(tyre_age) / np.log(41.0)).to_numpy(dtype=float),
        ]
    )
    names.extend(["tyre_age_scaled", "tyre_age_log_scaled"])
    groups.extend(["performance", "performance"])

    lap_fraction = _numeric(frame, "lap_fraction", 0.5).fillna(0.5)
    stage = lap_fraction.clip(lower=0.0, upper=1.0) - 0.5
    gap = _numeric(frame, "gap_seconds_proxy")
    clean_gap = float(settings["clean_gap_seconds"])
    pressure = ((clean_gap - gap) / clean_gap).clip(lower=0.0, upper=2.0)
    pressure = pressure.fillna(0.0)
    traffic = frame["traffic_state"].astype("string").fillna("unknown_gap")
    distance = _numeric(frame, "distance_coverage_ratio", 0.0).fillna(0.0)
    samples = _numeric(frame, "telemetry_samples", 0.0).fillna(0.0)
    recommended_distance = float(settings["recommended_distance_coverage"])
    recommended_samples = float(settings["recommended_samples_per_lap"])

    nuisance = [
        (stage.to_numpy(dtype=float), "stage_linear", "stage"),
        (
            np.square(stage.to_numpy(dtype=float)),
            "stage_quadratic",
            "stage",
        ),
        (pressure.to_numpy(dtype=float), "traffic_pressure", "traffic"),
        (
            traffic.eq("dirty_air").to_numpy(dtype=float),
            "traffic_dirty_air",
            "traffic",
        ),
        (
            traffic.eq("intermediate_gap").to_numpy(dtype=float),
            "traffic_intermediate",
            "traffic",
        ),
        (
            traffic.eq("unknown_gap").to_numpy(dtype=float),
            "traffic_unknown",
            "traffic",
        ),
        (
            (~_boolean(frame, "all_green", False)).to_numpy(dtype=float),
            "non_green",
            "event_quality",
        ),
        (
            _boolean(frame, "pit_boundary_proxy", False).to_numpy(dtype=float),
            "pit_boundary",
            "pit",
        ),
        (
            (~_boolean(frame, "is_accurate", False)).to_numpy(dtype=float),
            "inaccurate",
            "event_quality",
        ),
        (
            _boolean(frame, "deleted", False).to_numpy(dtype=float),
            "deleted",
            "event_quality",
        ),
        (
            tyre_missing.to_numpy(dtype=float),
            "tyre_age_missing",
            "event_quality",
        ),
        (
            (
                (recommended_distance - distance)
                / max(recommended_distance, 1e-9)
            )
            .clip(lower=0.0, upper=1.0)
            .to_numpy(dtype=float),
            "distance_deficit",
            "event_quality",
        ),
        (
            (
                (recommended_samples - samples)
                / max(recommended_samples, 1.0)
            )
            .clip(lower=0.0, upper=1.0)
            .to_numpy(dtype=float),
            "sample_deficit",
            "event_quality",
        ),
    ]
    for values, name, group in nuisance:
        arrays.append(values)
        names.append(name)
        groups.append(group)
    return np.column_stack(arrays), names, groups


def _weighted_ridge(
    design: np.ndarray,
    target: np.ndarray,
    weights: np.ndarray,
    ridge_penalty: float,
) -> np.ndarray:
    root_weight = np.sqrt(np.clip(weights, 0.0, None))
    weighted_design = design * root_weight[:, np.newaxis]
    weighted_target = target * root_weight
    penalty = np.eye(design.shape[1], dtype=float) * ridge_penalty
    penalty[0, 0] = 0.0
    system = weighted_design.T @ weighted_design + penalty
    rhs = weighted_design.T @ weighted_target
    try:
        return np.linalg.solve(system, rhs)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(system, rhs, rcond=None)[0]


def _huber_weights(
    residual: np.ndarray,
    base_weights: np.ndarray,
    settings: Mapping[str, Any],
) -> tuple[np.ndarray, float]:
    center, scale = weighted_mad(residual, base_weights)
    center = float(center or 0.0)
    scale = max(
        float(scale or 0.0),
        float(settings["residual_scale_floor_s"]),
    )
    threshold = float(settings["huber_delta"]) * scale
    magnitude = np.abs(residual - center)
    robust = np.ones(len(residual), dtype=float)
    large = magnitude > threshold
    robust[large] = threshold / magnitude[large]
    robust = np.maximum(
        robust, float(settings["minimum_robust_weight"])
    )
    return robust, scale


def _fit_huber_ridge(
    design: np.ndarray,
    target: np.ndarray,
    base_weights: np.ndarray,
    settings: Mapping[str, Any],
) -> tuple[np.ndarray, int]:
    coefficients = _weighted_ridge(
        design,
        target,
        base_weights,
        float(settings["ridge_penalty"]),
    )
    iterations = 0
    for iterations in range(1, int(settings["max_iterations"]) + 1):
        residual = target - design @ coefficients
        robust, _ = _huber_weights(residual, base_weights, settings)
        updated = _weighted_ridge(
            design,
            target,
            base_weights * robust,
            float(settings["ridge_penalty"]),
        )
        if float(np.max(np.abs(updated - coefficients))) <= float(
            settings["convergence_tolerance"]
        ):
            coefficients = updated
            break
        coefficients = updated
    return coefficients, iterations


def _reference_prediction(
    train: pd.DataFrame,
    train_target: np.ndarray,
    train_weights: np.ndarray,
    validation: pd.DataFrame,
) -> np.ndarray:
    """只用训练折的车手中位数，依次回退到车队和全场。"""

    work = train[["driver_id", "team_name"]].copy()
    work["_target"] = train_target
    work["_weight"] = train_weights
    global_center = weighted_median(train_target, train_weights)
    global_center = float(global_center or np.nanmedian(train_target))
    driver_center: dict[str, float] = {}
    team_center: dict[str, float] = {}
    for driver, group in work.groupby("driver_id", observed=True, sort=True):
        center = weighted_median(group["_target"], group["_weight"])
        if center is not None:
            driver_center[str(driver)] = center
    for team, group in work.groupby("team_name", observed=True, sort=True):
        center = weighted_median(group["_target"], group["_weight"])
        if center is not None:
            team_center[str(team)] = center
    prediction = []
    for row in validation[["driver_id", "team_name"]].itertuples(index=False):
        prediction.append(
            driver_center.get(
                str(row.driver_id),
                team_center.get(str(row.team_name), global_center),
            )
        )
    return np.asarray(prediction, dtype=float)


def _weighted_mae(
    observed: np.ndarray,
    predicted: np.ndarray,
    weights: np.ndarray,
) -> float | None:
    finite = (
        np.isfinite(observed)
        & np.isfinite(predicted)
        & np.isfinite(weights)
        & (weights > 0.0)
    )
    if not finite.any():
        return None
    return float(
        np.average(
            np.abs(observed[finite] - predicted[finite]),
            weights=weights[finite],
        )
    )


def _measurement_weights(
    frame: pd.DataFrame,
    candidate: pd.Series,
    settings: Mapping[str, Any],
    lap_filter: Mapping[str, Any],
) -> np.ndarray:
    weights = np.ones(len(frame), dtype=float)
    multipliers = settings["measurement_weights"]

    def apply(mask: pd.Series, key: str) -> None:
        weights[mask.to_numpy(dtype=bool)] *= float(multipliers[key])

    apply(~_boolean(frame, "is_accurate", False), "inaccurate")
    apply(_boolean(frame, "deleted", False), "deleted")
    apply(~_boolean(frame, "all_green", False), "non_green")
    apply(_boolean(frame, "pit_boundary_proxy", False), "pit_boundary")
    traffic = frame["traffic_state"].astype("string").fillna("unknown_gap")
    apply(traffic.eq("unknown_gap"), "unknown_gap")
    apply(traffic.eq("dirty_air"), "dirty_air")
    apply(traffic.eq("intermediate_gap"), "intermediate_gap")
    dry = frame["compound"].astype("string").str.upper().isin(
        [str(value).upper() for value in lap_filter["dry_compounds"]]
    )
    apply(~dry, "non_dry_compound")

    distance = _numeric(frame, "distance_coverage_ratio", 0.0).fillna(0.0)
    samples = _numeric(frame, "telemetry_samples", 0.0).fillna(0.0)
    distance_factor = (
        distance / float(settings["recommended_distance_coverage"])
    ).clip(lower=0.10, upper=1.0)
    sample_factor = (
        samples / float(settings["recommended_samples_per_lap"])
    ).clip(lower=0.10, upper=1.0)
    weights *= distance_factor.to_numpy(dtype=float)
    weights *= sample_factor.to_numpy(dtype=float)
    weights[~candidate.to_numpy(dtype=bool)] = 0.0
    return weights


def apply_inclusive_robust_model(
    frame: pd.DataFrame,
    settings: Mapping[str, Any],
    lap_filter: Mapping[str, Any],
) -> pd.DataFrame:
    """用全场候选圈拟合两折样本外稳健条件模型并返回连续权重。"""

    settings = dict(settings)
    settings.setdefault(
        "clean_gap_seconds", float(lap_filter["clean_air_gap_seconds"])
    )
    output = frame.copy()
    raw_target = _numeric(output, "lap_duration")
    fuel_target = _numeric(output, "fuel_corrected_lap_duration_s")
    target = fuel_target.where(
        np.isfinite(fuel_target) & fuel_target.gt(0.0),
        raw_target,
    )
    distance = _numeric(output, "distance_coverage_ratio")
    samples = _numeric(output, "telemetry_samples")
    lap_number = _numeric(output, "lap_number")
    candidate = (
        np.isfinite(target)
        & target.gt(0.0)
        & np.isfinite(lap_number)
        & distance.ge(float(settings["hard_minimum_distance_coverage"]))
        & samples.ge(int(settings["hard_minimum_samples_per_lap"]))
    )
    output["inclusive_model_target_s"] = target
    output["inclusive_model_candidate"] = candidate.astype(bool)

    exclusion_reasons: list[list[str]] = []
    for index in output.index:
        reasons: list[str] = []
        if not np.isfinite(target.loc[index]) or target.loc[index] <= 0.0:
            reasons.append("missing_or_invalid_lap_time")
        if not np.isfinite(lap_number.loc[index]):
            reasons.append("missing_lap_number")
        if (
            not np.isfinite(distance.loc[index])
            or distance.loc[index]
            < float(settings["hard_minimum_distance_coverage"])
        ):
            reasons.append("physically_incomplete_distance")
        if (
            not np.isfinite(samples.loc[index])
            or samples.loc[index]
            < int(settings["hard_minimum_samples_per_lap"])
        ):
            reasons.append("physically_insufficient_samples")
        exclusion_reasons.append(reasons)
    output["inclusive_hard_exclusion_reasons"] = exclusion_reasons

    base_weight = _measurement_weights(
        output, candidate, settings, lap_filter
    )
    output["inclusive_measurement_weight"] = base_weight
    output["inclusive_oof_prediction_s"] = np.nan
    output["inclusive_oof_reference_prediction_s"] = np.nan
    output["inclusive_oof_residual_s"] = np.nan
    output["inclusive_robust_residual_weight"] = 0.0
    output["inclusive_model_weight"] = 0.0
    output["inclusive_model_prediction_s"] = np.nan
    output["inclusive_nuisance_adjustment_s"] = np.nan
    output["inclusive_stage_evolution_proxy_s"] = np.nan
    output["inclusive_traffic_effect_proxy_s"] = np.nan
    output["inclusive_pit_effect_proxy_s"] = np.nan
    output["inclusive_event_quality_effect_proxy_s"] = np.nan
    output["inclusive_adjusted_lap_duration_s"] = np.nan
    output["inclusive_adjustment_saturation_fraction"] = 0.0

    positions = np.flatnonzero(candidate.to_numpy(dtype=bool))
    status = "insufficient_support"
    mae_model = None
    mae_reference = None
    improvement = None
    residual_scale = None
    max_iterations_used = 0
    folds = int(settings["crossfit_folds"])
    if len(positions):
        work = output.iloc[positions].copy()
        design, _, feature_groups = _design_matrix(work, settings)
        observed = target.iloc[positions].to_numpy(dtype=float)
        measurement = base_weight[positions]
        fold_id = np.asarray(
            [
                _stable_fold(row, folds)
                for row in work.itertuples(index=False)
            ],
            dtype=int,
        )
        oof = np.full(len(work), np.nan, dtype=float)
        reference = np.full(len(work), np.nan, dtype=float)
        fold_complete = True
        for fold in range(folds):
            validation_mask = fold_id == fold
            train_mask = ~validation_mask
            if not validation_mask.any() or train_mask.sum() < 4:
                fold_complete = False
                continue
            coefficients, iterations = _fit_huber_ridge(
                design[train_mask],
                observed[train_mask],
                measurement[train_mask],
                settings,
            )
            max_iterations_used = max(max_iterations_used, iterations)
            oof[validation_mask] = design[validation_mask] @ coefficients
            reference[validation_mask] = _reference_prediction(
                work.loc[train_mask],
                observed[train_mask],
                measurement[train_mask],
                work.loc[validation_mask],
            )
        valid_oof = np.isfinite(oof) & np.isfinite(reference)
        mae_model = _weighted_mae(
            observed[valid_oof],
            oof[valid_oof],
            measurement[valid_oof],
        )
        mae_reference = _weighted_mae(
            observed[valid_oof],
            reference[valid_oof],
            measurement[valid_oof],
        )
        if mae_model is not None and mae_reference is not None:
            improvement = float(mae_reference - mae_model)
        support_ok = bool(
            len(work) >= int(settings["minimum_candidate_laps"])
            and int(valid_oof.sum()) >= int(settings["minimum_holdout_laps"])
            and work["team_name"].nunique()
            >= int(settings["minimum_teams"])
            and work["driver_id"].nunique()
            >= int(settings["minimum_drivers"])
            and fold_complete
        )
        prediction_ok = bool(
            improvement is not None
            and improvement
            >= float(settings["minimum_oof_mae_improvement_s"])
            and mae_model is not None
            and mae_reference is not None
            and mae_model <= float(settings["maximum_oof_mae_s"])
            and mae_model
            <= mae_reference
            + float(settings["maximum_oof_mae_worsening_s"])
        )
        status = (
            "accepted_crossfit_proxy"
            if support_ok and prediction_ok
            else (
                "rejected_prediction_gate"
                if support_ok
                else "insufficient_support"
            )
        )

        residual = observed - oof
        robust = np.full(len(work), 1.0, dtype=float)
        if valid_oof.any():
            valid_robust, residual_scale = _huber_weights(
                residual[valid_oof],
                measurement[valid_oof],
                settings,
            )
            robust[valid_oof] = valid_robust
        analysis_weight = measurement * robust
        coefficients = _weighted_ridge(
            design,
            observed,
            analysis_weight,
            float(settings["ridge_penalty"]),
        )
        full_prediction = design @ coefficients
        groups = np.asarray(feature_groups, dtype=object)

        def contribution(group: str) -> np.ndarray:
            indices = np.flatnonzero(groups == group)
            if not len(indices):
                return np.zeros(len(work), dtype=float)
            return design[:, indices] @ coefficients[indices]

        stage_effect = contribution("stage")
        traffic_effect = contribution("traffic")
        pit_effect = contribution("pit")
        quality_effect = contribution("event_quality")
        raw_nuisance = (
            stage_effect + traffic_effect + pit_effect + quality_effect
        )
        maximum_adjustment = float(
            settings["maximum_abs_nuisance_adjustment_s"]
        )
        saturation_fraction = float(
            np.mean(np.abs(raw_nuisance) >= maximum_adjustment)
        )
        if (
            status == "accepted_crossfit_proxy"
            and saturation_fraction
            > float(settings["maximum_saturated_adjustment_fraction"])
        ):
            status = "rejected_adjustment_sanity_gate"
        nuisance = np.clip(
            raw_nuisance,
            -maximum_adjustment,
            maximum_adjustment,
        )
        adjusted = observed - nuisance
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_oof_prediction_s"),
        ] = oof
        output.iloc[
            positions,
            output.columns.get_loc(
                "inclusive_oof_reference_prediction_s"
            ),
        ] = reference
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_oof_residual_s"),
        ] = residual
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_robust_residual_weight"),
        ] = robust
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_model_weight"),
        ] = analysis_weight
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_model_prediction_s"),
        ] = full_prediction
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_nuisance_adjustment_s"),
        ] = nuisance
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_stage_evolution_proxy_s"),
        ] = stage_effect
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_traffic_effect_proxy_s"),
        ] = traffic_effect
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_pit_effect_proxy_s"),
        ] = pit_effect
        output.iloc[
            positions,
            output.columns.get_loc(
                "inclusive_event_quality_effect_proxy_s"
            ),
        ] = quality_effect
        output.iloc[
            positions,
            output.columns.get_loc("inclusive_adjusted_lap_duration_s"),
        ] = adjusted
        output["inclusive_adjustment_saturation_fraction"] = (
            saturation_fraction
        )

    output["inclusive_pace_eligible_lap"] = (
        output["inclusive_model_candidate"]
        & output["inclusive_model_weight"].ge(
            float(settings["minimum_analysis_weight"])
        )
    )
    output["inclusive_model_validation_status"] = status
    output["inclusive_model_oof_mae_s"] = mae_model
    output["inclusive_reference_oof_mae_s"] = mae_reference
    output["inclusive_oof_mae_improvement_s"] = improvement
    output["inclusive_oof_residual_scale_s"] = residual_scale
    output["inclusive_model_irls_iterations"] = max_iterations_used
    output["inclusive_model_target_basis"] = (
        "fuel_corrected_where_available_else_raw_lap_time"
    )
    return output


def build_inclusive_robust_audit(
    frame: pd.DataFrame,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """汇总全量候选、连续权重、样本外预测和严格复核的可审计证据。"""

    if "inclusive_model_candidate" not in frame:
        return {
            "status": "not_enabled",
            "causal_truth_identified": False,
        }
    candidate = frame["inclusive_model_candidate"].fillna(False).astype(bool)
    weight = _numeric(frame, "inclusive_model_weight", 0.0).fillna(0.0)
    strict = _boolean(frame, "strict_pace_eligible_lap", False)
    status_mode = frame["inclusive_model_validation_status"].mode()
    status = (
        str(status_mode.iloc[0])
        if not status_mode.empty
        else "insufficient_support"
    )
    hard_reason_counts: dict[str, int] = {}
    for reasons in frame["inclusive_hard_exclusion_reasons"]:
        for reason in reasons:
            hard_reason_counts[str(reason)] = (
                hard_reason_counts.get(str(reason), 0) + 1
            )
    condition_masks = {
        "strict_clean_confirmation": strict,
        "non_green": ~_boolean(frame, "all_green", False),
        "pit_boundary": _boolean(frame, "pit_boundary_proxy", False),
        "inaccurate_or_deleted": (
            ~_boolean(frame, "is_accurate", False)
            | _boolean(frame, "deleted", False)
        ),
        "dirty_air": frame["traffic_state"].astype("string").eq(
            "dirty_air"
        ),
        "unknown_gap": frame["traffic_state"].astype("string").eq(
            "unknown_gap"
        ),
    }
    condition_support = []
    for name, mask in condition_masks.items():
        condition_support.append(
            {
                "condition": name,
                "observed_laps": int(mask.sum()),
                "candidate_laps": int((mask & candidate).sum()),
                "effective_weight_mass": float(weight.loc[mask].sum()),
            }
        )
    candidate_weights = weight.loc[candidate].to_numpy(dtype=float)
    finite_weights = candidate_weights[
        np.isfinite(candidate_weights) & (candidate_weights > 0.0)
    ]

    def distribution(column: str, *, absolute: bool = False) -> dict[str, Any]:
        values = _numeric(frame.loc[candidate], column).to_numpy(dtype=float)
        values = values[np.isfinite(values)]
        if absolute:
            values = np.abs(values)
        if not len(values):
            return {
                "minimum": None,
                "p05": None,
                "median": None,
                "p95": None,
                "maximum": None,
            }
        return {
            "minimum": float(np.min(values)),
            "p05": float(np.quantile(values, 0.05)),
            "median": float(np.quantile(values, 0.50)),
            "p95": float(np.quantile(values, 0.95)),
            "maximum": float(np.max(values)),
        }

    first = frame.iloc[0] if len(frame) else pd.Series(dtype=object)
    return {
        "status": status,
        "model": str(settings["model"]),
        "observed_laps": int(len(frame)),
        "candidate_laps": int(candidate.sum()),
        "candidate_fraction": (
            float(candidate.mean()) if len(frame) else 0.0
        ),
        "strict_confirmation_laps": int(strict.sum()),
        "strict_confirmation_fraction_of_candidates": (
            float(strict.sum() / candidate.sum())
            if candidate.sum()
            else 0.0
        ),
        "effective_weight_mass": float(weight.sum()),
        "kish_effective_laps": kish_effective_sample_size(weight),
        "weight_quantiles": (
            {
                "p05": float(np.quantile(finite_weights, 0.05)),
                "p50": float(np.quantile(finite_weights, 0.50)),
                "p95": float(np.quantile(finite_weights, 0.95)),
            }
            if len(finite_weights)
            else {"p05": None, "p50": None, "p95": None}
        ),
        "hard_excluded_laps": int((~candidate).sum()),
        "hard_exclusion_reason_counts": hard_reason_counts,
        "condition_support": condition_support,
        "adjustment_distributions_s": {
            "total_nuisance": distribution(
                "inclusive_nuisance_adjustment_s"
            ),
            "stage": distribution(
                "inclusive_stage_evolution_proxy_s"
            ),
            "traffic": distribution(
                "inclusive_traffic_effect_proxy_s"
            ),
            "pit": distribution("inclusive_pit_effect_proxy_s"),
            "event_quality": distribution(
                "inclusive_event_quality_effect_proxy_s"
            ),
            "absolute_oof_residual": distribution(
                "inclusive_oof_residual_s", absolute=True
            ),
        },
        "crossfit_validation": {
            "folds": int(settings["crossfit_folds"]),
            "model_oof_mae_s": (
                float(first["inclusive_model_oof_mae_s"])
                if len(frame)
                and pd.notna(first["inclusive_model_oof_mae_s"])
                else None
            ),
            "reference_oof_mae_s": (
                float(first["inclusive_reference_oof_mae_s"])
                if len(frame)
                and pd.notna(first["inclusive_reference_oof_mae_s"])
                else None
            ),
            "mae_improvement_s": (
                float(first["inclusive_oof_mae_improvement_s"])
                if len(frame)
                and pd.notna(first["inclusive_oof_mae_improvement_s"])
                else None
            ),
            "cleaning_residual_source": str(
                settings["cleaning_residual_source"]
            ),
            "reference": "training_fold_driver_median_then_team_then_field",
        },
        "adjustment_sanity_gate": {
            "maximum_abs_adjustment_s": float(
                settings["maximum_abs_nuisance_adjustment_s"]
            ),
            "maximum_saturated_fraction": float(
                settings["maximum_saturated_adjustment_fraction"]
            ),
            "observed_saturated_fraction": (
                float(first["inclusive_adjustment_saturation_fraction"])
                if len(frame)
                else 0.0
            ),
            "passed": status != "rejected_adjustment_sanity_gate",
        },
        "publication_allowed": status == "accepted_crossfit_proxy",
        "strict_subset_role": "independent_confirmation_not_primary_training_set",
        "boundaries": {
            "causal_truth_identified": False,
            "real_fuel_load_identified": False,
            "physical_tyre_state_identified": False,
            "in_sample_residual_used_for_selection": False,
            "soft_weight_is_observation_reliability_not_event_probability": True,
        },
    }
