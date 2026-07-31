"""完整比赛 Race Dossier 的覆盖审计、stint 物理量与赛季审计。

本模块把“整场观测圈宇宙”作为首要分析单元。逐圈匹配、赛道几何遥测和
历史 PAC 只能作为旁证，不能替代全场覆盖门槛。所有轮胎、交通、进站和
排名输出都是公开数据代理；真实燃油、胎温胎压、磨损量、车队内部模型及
倍耐力内部模拟均不可识别。
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd

from .inclusive_robust import (
    apply_inclusive_robust_model,
    build_inclusive_robust_audit,
    kish_effective_sample_size,
    weighted_mad,
    weighted_median,
    weighted_theil_sen_slope,
)


REQUIRED_LAP_COLUMNS = {
    "year",
    "meeting_name",
    "driver_id",
    "team_name",
    "lap_number",
    "lap_duration",
    "compound",
    "stint_number",
    "tyre_age",
    "position",
    "is_accurate",
    "deleted",
    "all_green",
    "track_status_codes",
    "distance_metres",
    "median_speed_kph",
    "median_gap_to_ahead_m",
    "telemetry_samples",
}

PRIMARY_DISPOSITIONS = (
    "model_eligible",
    "event_excluded",
    "traffic_separate",
    "pit_excluded",
    "quality_missing",
    "unknown_unaccounted",
)

UNIDENTIFIED_EVENT_CATEGORIES = (
    "accident_or_damage",
    "penalty",
    "abnormal_pit_stop",
    "defending_or_team_order",
    "reliability_state",
)


def _required(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{missing}")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _numeric_median(values: Any) -> float | None:
    series = pd.to_numeric(values, errors="coerce").dropna()
    return float(series.median()) if len(series) else None


def _median_mad(values: Sequence[float]) -> tuple[float | None, float | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return None, None
    center = float(np.median(array))
    mad = float(1.4826 * np.median(np.abs(array - center)))
    return center, mad


def _quantile(values: Sequence[float], probability: float) -> float | None:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    return float(np.quantile(array, probability)) if len(array) else None


def _theil_sen_slope(x_values: Any, y_values: Any) -> float | None:
    """返回稳健的两两中位斜率；stint 圈数很小，直接枚举可保持确定性。"""

    x = pd.to_numeric(x_values, errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(y_values, errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if len(x) < 2 or np.unique(x).size < 2:
        return None
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    delta_x = x[np.newaxis, :] - x[:, np.newaxis]
    delta_y = y[np.newaxis, :] - y[:, np.newaxis]
    usable = np.triu(np.ones(delta_x.shape, dtype=bool), k=1)
    usable &= delta_x != 0.0
    if not usable.any():
        return None
    return float(np.median(delta_y[usable] / delta_x[usable]))


def _estimate_stint_reference_pace(
    tyre_age_values: Any,
    pace_values: Any,
    weights: Any,
    *,
    reference_tyre_age_laps: float | None = None,
) -> dict[str, float | str | None]:
    """把一个 Stint 的有效圈投影为一个位于样本支持内的胎龄代表配速。

    这里的输出不是挑选某一真实圈，也不是把胎龄效应删除。先对已经完成燃油与
    可观测条件修正的圈速估计稳健胎龄斜率，再把所有有效圈投影到同一参考胎龄，
    最后取加权中位数。默认参考胎龄是本 Stint 的加权中位胎龄，因此不做外推。
    """

    tyre_age = pd.to_numeric(tyre_age_values, errors="coerce").to_numpy(
        dtype=float
    )
    pace = pd.to_numeric(pace_values, errors="coerce").to_numpy(dtype=float)
    weight = pd.to_numeric(weights, errors="coerce").to_numpy(dtype=float)
    finite = (
        np.isfinite(tyre_age)
        & np.isfinite(pace)
        & np.isfinite(weight)
        & (weight > 0.0)
    )
    tyre_age = tyre_age[finite]
    pace = pace[finite]
    weight = weight[finite]
    if not len(tyre_age):
        return {
            "status": "not_identifiable",
            "reference_tyre_age_laps": None,
            "pace_s": None,
            "slope_s_per_tyre_lap": None,
            "support_min_tyre_age_laps": None,
            "support_max_tyre_age_laps": None,
        }

    support_min = float(np.min(tyre_age))
    support_max = float(np.max(tyre_age))
    reference_age = (
        float(reference_tyre_age_laps)
        if reference_tyre_age_laps is not None
        else weighted_median(tyre_age, weight)
    )
    if (
        reference_age is None
        or reference_age < support_min
        or reference_age > support_max
    ):
        return {
            "status": "not_identifiable_reference_outside_support",
            "reference_tyre_age_laps": reference_age,
            "pace_s": None,
            "slope_s_per_tyre_lap": None,
            "support_min_tyre_age_laps": support_min,
            "support_max_tyre_age_laps": support_max,
        }

    slope = weighted_theil_sen_slope(tyre_age, pace, weight)
    if slope is None:
        return {
            "status": "level_only_tyre_age_slope_not_identified",
            "reference_tyre_age_laps": float(reference_age),
            "pace_s": weighted_median(pace, weight),
            "slope_s_per_tyre_lap": None,
            "support_min_tyre_age_laps": support_min,
            "support_max_tyre_age_laps": support_max,
        }

    projected = pace - float(slope) * (tyre_age - float(reference_age))
    return {
        "status": "identified_conditional_proxy",
        "reference_tyre_age_laps": float(reference_age),
        "pace_s": weighted_median(projected, weight),
        "slope_s_per_tyre_lap": float(slope),
        "support_min_tyre_age_laps": support_min,
        "support_max_tyre_age_laps": support_max,
    }


def _weighted_quantile(
    values: Any,
    weights: Any,
    probability: float,
) -> float | None:
    """确定性加权分位数；只用于描述范围，不冒充概率校准区间。"""

    array = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    weight = pd.to_numeric(weights, errors="coerce").to_numpy(dtype=float)
    finite = (
        np.isfinite(array)
        & np.isfinite(weight)
        & (weight > 0.0)
    )
    array = array[finite]
    weight = weight[finite]
    if not len(array):
        return None
    order = np.argsort(array, kind="stable")
    array = array[order]
    weight = weight[order]
    threshold = float(np.clip(probability, 0.0, 1.0)) * float(weight.sum())
    index = int(np.searchsorted(np.cumsum(weight), threshold, side="left"))
    return float(array[min(index, len(array) - 1)])


def _weighted_interval(
    values: Any,
    weights: Any,
    probability: float,
) -> list[float] | None:
    tail = max(0.0, min(0.5, (1.0 - float(probability)) / 2.0))
    lower = _weighted_quantile(values, weights, tail)
    upper = _weighted_quantile(values, weights, 1.0 - tail)
    return (
        [float(lower), float(upper)]
        if lower is not None and upper is not None
        else None
    )


def _stint_key(row: Mapping[str, Any]) -> str:
    """跨报告/sidecar 的稳定 Stint 主键。"""

    return "|".join(
        [
            str(int(row["year"])),
            str(row.get("meeting_name") or row.get("meeting") or ""),
            str(row.get("team") or row.get("team_name") or ""),
            str(row.get("driver") or row.get("driver_id") or ""),
            str(int(row["stint_number"])),
            str(row.get("compound") or "").upper(),
        ]
    )


def _linear_prediction_grid(
    *,
    support: Sequence[float],
    reference_age: float,
    reference_pace: float,
    slope: float,
    points: int,
) -> list[dict[str, float]]:
    minimum, maximum = float(support[0]), float(support[1])
    if maximum <= minimum:
        return []
    return [
        {
            "tyre_age_laps": float(age),
            "pace_s": float(
                reference_pace + slope * (float(age) - reference_age)
            ),
        }
        for age in np.linspace(minimum, maximum, max(2, int(points)))
    ]


def _delete_block_fit_sensitivity(
    usable: pd.DataFrame,
    *,
    pace_column: str,
    reference_age: float,
    support: Sequence[float],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """逐次删除连续圈块，量化局部序列段对线性拟合的敏感性。

    该范围是确定性的稳定性审计，不是置信区间或新圈预测区间。
    """

    block_length = int(settings["delete_block_laps"])
    minimum_refits = int(settings["minimum_delete_block_refits"])
    probability = float(settings["stability_interval_probability"])
    grid_points = int(settings["prediction_grid_points"])
    work = usable.sort_values("lap_number", kind="stable").copy()
    alternatives: list[dict[str, float]] = []
    candidate_blocks = 0
    skipped_non_contiguous_blocks = 0
    effective_size = kish_effective_sample_size(work["_analysis_weight"])
    if effective_size < float(
        settings["minimum_kish_effective_laps_for_range"]
    ):
        return {
            "status": "NOT_TESTED",
            "method": "leave_contiguous_lap_block_out_v1",
            "block_length_laps": block_length,
            "candidate_contiguous_blocks": 0,
            "skipped_non_contiguous_blocks": 0,
            "refits": 0,
            "probability": probability,
            "reference_pace_interval_s": None,
            "slope_interval_s_per_tyre_lap": None,
            "prediction_band": [],
            "prediction_band_support_tyre_age_laps": None,
            "role": "stability_sensitivity_not_confidence_or_prediction_interval",
            "reason": "kish_effective_laps_below_range_gate",
        }
    if len(work) > block_length:
        for start in range(0, len(work) - block_length + 1):
            deleted = work.iloc[start : start + block_length]
            deleted_laps = pd.to_numeric(
                deleted["lap_number"], errors="coerce"
            ).to_numpy(dtype=float)
            if (
                len(deleted_laps) != block_length
                or not np.isfinite(deleted_laps).all()
                or not np.all(np.diff(deleted_laps) == 1.0)
            ):
                skipped_non_contiguous_blocks += 1
                continue
            candidate_blocks += 1
            kept = work.drop(deleted.index)
            if len(kept) < int(settings["minimum_fit_points"]):
                continue
            fit = _estimate_stint_reference_pace(
                kept["tyre_age"],
                kept[pace_column],
                kept["_analysis_weight"],
                reference_tyre_age_laps=float(reference_age),
            )
            pace = _finite(fit["pace_s"])
            slope = _finite(fit["slope_s_per_tyre_lap"])
            if pace is None or slope is None:
                continue
            alternatives.append(
                {
                    "reference_pace_s": pace,
                    "slope_s_per_tyre_lap": slope,
                    "support_minimum_tyre_age_laps": float(
                        pd.to_numeric(
                            kept["tyre_age"], errors="coerce"
                        ).min()
                    ),
                    "support_maximum_tyre_age_laps": float(
                        pd.to_numeric(
                            kept["tyre_age"], errors="coerce"
                        ).max()
                    ),
                }
            )
    if len(alternatives) < minimum_refits:
        return {
            "status": "NOT_TESTED",
            "method": "leave_contiguous_lap_block_out_v1",
            "block_length_laps": block_length,
            "candidate_contiguous_blocks": candidate_blocks,
            "skipped_non_contiguous_blocks": (
                skipped_non_contiguous_blocks
            ),
            "refits": len(alternatives),
            "probability": probability,
            "reference_pace_interval_s": None,
            "slope_interval_s_per_tyre_lap": None,
            "prediction_band": [],
            "prediction_band_support_tyre_age_laps": None,
            "role": "stability_sensitivity_not_confidence_or_prediction_interval",
        }

    lower_probability = (1.0 - probability) / 2.0
    upper_probability = 1.0 - lower_probability
    pace_values = np.asarray(
        [row["reference_pace_s"] for row in alternatives], dtype=float
    )
    slope_values = np.asarray(
        [row["slope_s_per_tyre_lap"] for row in alternatives], dtype=float
    )
    prediction_band = []
    for age in np.linspace(
        float(support[0]),
        float(support[1]),
        max(2, grid_points),
    ):
        eligible_refits = [
            row
            for row in alternatives
            if row["support_minimum_tyre_age_laps"]
            <= float(age)
            <= row["support_maximum_tyre_age_laps"]
        ]
        if len(eligible_refits) < minimum_refits:
            continue
        predictions = np.asarray(
            [
                row["reference_pace_s"]
                + row["slope_s_per_tyre_lap"]
                * (float(age) - reference_age)
                for row in eligible_refits
            ],
            dtype=float,
        )
        prediction_band.append(
            {
                "tyre_age_laps": float(age),
                "lower_s": float(np.quantile(predictions, lower_probability)),
                "upper_s": float(np.quantile(predictions, upper_probability)),
                "supporting_refits": int(len(eligible_refits)),
            }
        )
    reference_refits = [
        row
        for row in alternatives
        if row["support_minimum_tyre_age_laps"]
        <= reference_age
        <= row["support_maximum_tyre_age_laps"]
    ]
    reference_pace_values = np.asarray(
        [row["reference_pace_s"] for row in reference_refits],
        dtype=float,
    )
    return {
        "status": "ESTIMATED",
        "method": "leave_contiguous_lap_block_out_v1",
        "block_length_laps": block_length,
        "candidate_contiguous_blocks": candidate_blocks,
        "skipped_non_contiguous_blocks": skipped_non_contiguous_blocks,
        "refits": len(alternatives),
        "probability": probability,
        "reference_pace_interval_s": (
            [
                float(
                    np.quantile(reference_pace_values, lower_probability)
                ),
                float(
                    np.quantile(reference_pace_values, upper_probability)
                ),
            ]
            if len(reference_pace_values) >= minimum_refits
            else None
        ),
        "slope_interval_s_per_tyre_lap": [
            float(np.quantile(slope_values, lower_probability)),
            float(np.quantile(slope_values, upper_probability)),
        ],
        "prediction_band": prediction_band,
        "prediction_band_support_tyre_age_laps": (
            [
                float(prediction_band[0]["tyre_age_laps"]),
                float(prediction_band[-1]["tyre_age_laps"]),
            ]
            if prediction_band
            else None
        ),
        "role": "stability_sensitivity_not_confidence_or_prediction_interval",
    }


def _descriptive_confirmed_curve_refit(
    strict_usable: pd.DataFrame,
    *,
    pace_column: str,
    curve_audit: Mapping[str, Any] | None,
    support: Sequence[float],
    curve_settings: Mapping[str, Any],
    evidence_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """确认模型家族通过后做描述性全段重拟合，不回写确认误差。"""

    selected_model = (
        str(curve_audit.get("selected_model"))
        if curve_audit and curve_audit.get("selected_model")
        else None
    )
    if (
        not curve_audit
        or curve_audit.get("status") != "validated_confirmatory_proxy"
        or not selected_model
    ):
        return {
            "status": "NOT_AVAILABLE",
            "selected_model": selected_model,
            "reason": (
                curve_audit.get("status")
                if curve_audit
                else "curve_audit_missing"
            ),
            "prediction_grid": [],
            "equation": None,
        }
    work = strict_usable.sort_values("tyre_age", kind="stable").copy()
    x = pd.to_numeric(work["tyre_age"], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(work[pace_column], errors="coerce").to_numpy(dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < int(evidence_settings["minimum_fit_points"]):
        return {
            "status": "NOT_AVAILABLE",
            "selected_model": selected_model,
            "reason": "strict_refit_points_below_gate",
            "prediction_grid": [],
            "equation": None,
        }
    descriptive_support = [
        max(float(support[0]), float(np.min(x))),
        min(float(support[1]), float(np.max(x))),
    ]
    if descriptive_support[1] <= descriptive_support[0]:
        return {
            "status": "NOT_AVAILABLE",
            "selected_model": selected_model,
            "reason": "strict_refit_support_does_not_overlap_primary_support",
            "prediction_grid": [],
            "equation": None,
        }
    grid = np.linspace(
        descriptive_support[0],
        descriptive_support[1],
        max(2, int(evidence_settings["prediction_grid_points"])),
    )
    equation: dict[str, Any] | None = None
    if selected_model == "linear_theil_sen":
        slope = _theil_sen_slope(pd.Series(x), pd.Series(y))
        if slope is not None:
            intercept = float(np.median(y - float(slope) * x))
            equation = {
                "form": "pace_s = intercept_s + slope_s_per_tyre_lap * tyre_age_laps",
                "intercept_s": intercept,
                "slope_s_per_tyre_lap": float(slope),
            }
    elif selected_model == "log_theil_sen":
        transformed = np.log1p(np.clip(x, a_min=0.0, a_max=None))
        slope = _theil_sen_slope(pd.Series(transformed), pd.Series(y))
        if slope is not None:
            intercept = float(np.median(y - float(slope) * transformed))
            equation = {
                "form": "pace_s = intercept_s + slope_s * log1p(tyre_age_laps)",
                "intercept_s": intercept,
                "slope_s": float(slope),
            }
    elif selected_model == "quadratic_least_squares":
        if len(x) >= 5 and np.unique(x).size >= 3:
            center = float(np.median(x))
            coefficients = np.polyfit(x - center, y, 2)
            equation = {
                "form": (
                    "pace_s = quadratic_s_per_lap2 * centered_age^2 + "
                    "linear_s_per_lap * centered_age + intercept_s"
                ),
                "center_tyre_age_laps": center,
                "quadratic_s_per_lap2": float(coefficients[0]),
                "linear_s_per_lap": float(coefficients[1]),
                "intercept_s": float(coefficients[2]),
            }
    predictions = [
        _curve_model_predict(
            selected_model,
            x,
            y,
            float(age),
            curve_settings,
        )
        for age in grid
    ]
    if any(value is None for value in predictions):
        return {
            "status": "NOT_AVAILABLE",
            "selected_model": selected_model,
            "reason": "descriptive_refit_failed",
            "prediction_grid": [],
            "equation": equation,
        }
    return {
        "status": "AVAILABLE",
        "selected_model": selected_model,
        "fit_role": (
            "post_confirmation_descriptive_refit_not_out_of_sample_evidence"
        ),
        "fit_points": int(len(x)),
        "support_tyre_age_laps": descriptive_support,
        "equation": equation,
        "prediction_grid": [
            {
                "tyre_age_laps": float(age),
                "pace_s": float(prediction),
            }
            for age, prediction in zip(grid, predictions)
        ],
    }


def _curve_validation_roles(
    group: pd.DataFrame,
    *,
    row: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[Any, str]:
    roles = {index: "not_in_strict_curve_pool" for index in group.index}
    strict_mask = (
        group["strict_pace_eligible_lap"].fillna(False).astype(bool)
        if "strict_pace_eligible_lap" in group
        else group["modelable_lap"].fillna(False).astype(bool)
    )
    strict = group.loc[strict_mask].sort_values("lap_number", kind="stable")
    warmup = int(config["stint_gate"]["warmup_laps"])
    for index in strict.head(warmup).index:
        roles[index] = "warmup_excluded_from_curve_tournament"
    curve_input = strict.iloc[warmup:].sort_values("tyre_age", kind="stable")
    audit = row.get("tyre_curve_model_audit") or {}
    if not len(curve_input):
        return roles
    if str(audit.get("validation_protocol")) != (
        "ordered_selection_confirmation_v1"
    ):
        for index in curve_input.index:
            roles[index] = "curve_validation_pool"
        return roles
    settings = config["stint_curve_models"]
    minimum_train = int(settings["minimum_train_laps"])
    origins = list(range(minimum_train, len(curve_input)))
    minimum_selection = int(settings["minimum_selection_points"])
    minimum_confirmation = int(settings["minimum_confirmation_points"])
    split = int(np.floor(len(origins) * float(settings["selection_fraction"])))
    split = max(minimum_selection, split)
    split = min(split, len(origins) - minimum_confirmation)
    if (
        split < minimum_selection
        or len(origins) - split < minimum_confirmation
    ):
        selection: set[int] = set()
        confirmation: set[int] = set()
    else:
        selection = set(origins[:split])
        confirmation = set(origins[split:])
    for position, index in enumerate(curve_input.index):
        if position < minimum_train:
            roles[index] = "initial_training_history"
        elif position in selection:
            roles[index] = "model_family_selection_origin"
        elif position in confirmation:
            roles[index] = "later_confirmation_origin"
        else:
            roles[index] = "curve_validation_pool"
    return roles


def _distribution(
    values: pd.Series,
    categories: Sequence[str],
    weights: pd.Series | None = None,
) -> dict[str, float]:
    if weights is None:
        denominator = float(len(values))
        return {
            category: (
                float(values.astype("string").eq(category).sum()) / denominator
                if denominator > 0.0
                else 0.0
            )
            for category in categories
        }
    numeric_weight = pd.to_numeric(weights, errors="coerce").fillna(0.0)
    denominator = float(numeric_weight.sum())
    return {
        category: (
            float(
                numeric_weight.loc[
                    values.astype("string").eq(category)
                ].sum()
            )
            / denominator
            if denominator > 0.0
            else 0.0
        )
        for category in categories
    }


def _weighted_fraction(mask: pd.Series, weights: pd.Series) -> float | None:
    numeric_weight = pd.to_numeric(weights, errors="coerce").fillna(0.0)
    denominator = float(numeric_weight.sum())
    if denominator <= 0.0:
        return None
    return float(numeric_weight.loc[mask.fillna(False).astype(bool)].sum()) / (
        denominator
    )


def _condition_profile(
    group: pd.DataFrame,
    usable: pd.DataFrame,
    *,
    nuisance_scale_s: float,
) -> dict[str, Any]:
    traffic_categories = (
        "clean_air",
        "intermediate_gap",
        "dirty_air",
        "unknown_gap",
    )
    observed = max(1, int(len(group)))
    weight = usable["_analysis_weight"]
    required_finite = (
        pd.to_numeric(group["lap_fraction"], errors="coerce").notna()
        & group["traffic_state"].notna()
        & pd.to_numeric(
            group.get("inclusive_nuisance_adjustment_s", np.nan),
            errors="coerce",
        ).notna()
    )
    raw_lap_fraction = _numeric_median(group["lap_fraction"])
    weighted_lap_fraction = weighted_median(
        usable["lap_fraction"],
        weight,
    )
    nuisance_raw = _numeric_median(
        group.get("inclusive_nuisance_adjustment_s", pd.Series(dtype=float))
    )
    nuisance_weighted = weighted_median(
        usable.get(
            "inclusive_nuisance_adjustment_s",
            pd.Series(index=usable.index, dtype=float),
        ),
        weight,
    )
    non_green = ~group["all_green"].fillna(False).astype(bool)
    pit = group["pit_boundary_proxy"].fillna(False).astype(bool)
    low_quality = ~group["quality_ok"].fillna(False).astype(bool)
    usable_non_green = ~usable["all_green"].fillna(False).astype(bool)
    usable_pit = usable["pit_boundary_proxy"].fillna(False).astype(bool)
    usable_low_quality = ~usable["quality_ok"].fillna(False).astype(bool)
    return {
        "context_coverage": float(required_finite.sum()) / observed,
        "lap_fraction_median": {
            "raw": raw_lap_fraction,
            "weighted": weighted_lap_fraction,
        },
        "traffic_state_distribution": {
            "raw": _distribution(
                group["traffic_state"],
                traffic_categories,
            ),
            "weighted": _distribution(
                usable["traffic_state"],
                traffic_categories,
                weight,
            ),
        },
        "non_green_fraction": {
            "raw": float(non_green.sum()) / observed,
            "weighted": _weighted_fraction(usable_non_green, weight),
        },
        "pit_boundary_fraction": {
            "raw": float(pit.sum()) / observed,
            "weighted": _weighted_fraction(usable_pit, weight),
        },
        "low_quality_fraction": {
            "raw": float(low_quality.sum()) / observed,
            "weighted": _weighted_fraction(usable_low_quality, weight),
        },
        "nuisance_adjustment_median_s": {
            "raw": nuisance_raw,
            "weighted": nuisance_weighted,
            "same_event_compound_robust_scale_s": float(nuisance_scale_s),
        },
        "analysis_weight_median": _numeric_median(weight),
    }


def _common_support_condition_profile(
    row: Mapping[str, Any],
    *,
    minimum_age: float,
    maximum_age: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """只在两段共同胎龄支持内重算条件画像与有效样本量。"""

    records = []
    for point in row.get("points", []):
        age = _finite(point.get("tyre_age_laps"))
        if (
            age is None
            or age < minimum_age
            or age > maximum_age
        ):
            continue
        nuisance = point.get("nuisance_adjustment_s") or {}
        records.append(
            {
                "lap_number": point.get("lap_number"),
                "lap_fraction": point.get("lap_fraction"),
                "traffic_state": point.get("traffic_state"),
                "inclusive_nuisance_adjustment_s": nuisance.get("total"),
                "all_green": bool(point.get("all_green", False)),
                "pit_boundary_proxy": bool(
                    point.get("pit_boundary_proxy", False)
                ),
                "quality_ok": bool(point.get("quality_ok", False)),
                "analysis_weight": point.get("analysis_weight"),
                "used_for_primary_fit": bool(
                    point.get("used_for_primary_fit", False)
                ),
            }
        )
    columns = [
        "lap_number",
        "lap_fraction",
        "traffic_state",
        "inclusive_nuisance_adjustment_s",
        "all_green",
        "pit_boundary_proxy",
        "quality_ok",
        "analysis_weight",
        "used_for_primary_fit",
    ]
    group = pd.DataFrame.from_records(records, columns=columns)
    weight = pd.to_numeric(
        group["analysis_weight"], errors="coerce"
    ).fillna(0.0)
    usable = group.loc[
        group["used_for_primary_fit"].fillna(False).astype(bool)
        & weight.gt(0.0)
    ].copy()
    usable["_analysis_weight"] = weight.loc[usable.index]
    nuisance_scale = float(
        row["condition_profile"]["nuisance_adjustment_median_s"].get(
            "same_event_compound_robust_scale_s", 0.1
        )
    )
    profile = _condition_profile(
        group,
        usable,
        nuisance_scale_s=nuisance_scale,
    )
    profile["scope"] = "pair_common_tyre_age_support"
    sample_audit = {
        "observed_laps": int(len(group)),
        "fit_laps": int(len(usable)),
        "effective_weight_laps": float(usable["_analysis_weight"].sum()),
        "kish_effective_laps": kish_effective_sample_size(
            usable["_analysis_weight"]
        ),
    }
    return profile, sample_audit


def _worse_absolute_difference(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> float | None:
    differences = []
    for key in ("raw", "weighted"):
        a = _finite(left.get(key))
        b = _finite(right.get(key))
        if a is not None and b is not None:
            differences.append(abs(a - b))
    return max(differences) if differences else None


def _traffic_total_variation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> float | None:
    distances = []
    for mode in ("raw", "weighted"):
        a = left.get(mode)
        b = right.get(mode)
        if not isinstance(a, Mapping) or not isinstance(b, Mapping):
            continue
        categories = sorted(set(a) | set(b))
        distances.append(
            0.5
            * sum(
                abs(float(a.get(key, 0.0)) - float(b.get(key, 0.0)))
                for key in categories
            )
        )
    return max(distances) if distances else None


def _interval_grid_value_at(
    rows: Sequence[Mapping[str, Any]],
    target_age: float,
) -> list[float] | None:
    """在线性支持域内读取冻结的稳定性带，不在消费端重新拟合。"""

    usable = [
        (
            _finite(row.get("tyre_age_laps")),
            _finite(row.get("lower_s")),
            _finite(row.get("upper_s")),
        )
        for row in rows
    ]
    usable = [
        (age, lower, upper)
        for age, lower, upper in usable
        if age is not None and lower is not None and upper is not None
    ]
    if not usable:
        return None
    usable.sort(key=lambda row: row[0])
    ages = np.asarray([row[0] for row in usable], dtype=float)
    if target_age < float(ages[0]) or target_age > float(ages[-1]):
        return None
    lower = np.asarray([row[1] for row in usable], dtype=float)
    upper = np.asarray([row[2] for row in usable], dtype=float)
    return [
        float(np.interp(target_age, ages, lower)),
        float(np.interp(target_age, ages, upper)),
    ]


def _stint_fit_value_at(
    row: Mapping[str, Any],
    target_age: float,
    *,
    fuel_scenario: str | None = None,
) -> float | None:
    fit = row["primary_fit"]
    if fuel_scenario is None:
        reference_pace = _finite(fit.get("reference_pace_s"))
        slope = _finite(fit.get("slope_s_per_tyre_lap"))
    else:
        scenario = fit.get("fuel_scenarios", {}).get(fuel_scenario, {})
        reference_pace = _finite(scenario.get("reference_pace_s"))
        slope = _finite(scenario.get("slope_s_per_tyre_lap"))
    reference_age = _finite(fit.get("reference_tyre_age_laps"))
    if (
        reference_pace is None
        or slope is None
        or reference_age is None
    ):
        return None
    return float(reference_pace + slope * (target_age - reference_age))


def _stint_anchor_ranges(
    row: Mapping[str, Any],
    target_age: float,
) -> dict[str, Any]:
    point = _stint_fit_value_at(row, target_age)
    residual_range = row["sample_audit"].get(
        "primary_fit_residual_central_range_s"
    )
    empirical = (
        [
            float(point + float(residual_range[0])),
            float(point + float(residual_range[1])),
        ]
        if point is not None
        and isinstance(residual_range, Sequence)
        and len(residual_range) == 2
        and _finite(residual_range[0]) is not None
        and _finite(residual_range[1]) is not None
        else None
    )
    stability = _interval_grid_value_at(
        row["primary_fit"]
        .get("stability_interval", {})
        .get("prediction_band", []),
        target_age,
    )
    fuel_values = {
        scenario: _stint_fit_value_at(
            row,
            target_age,
            fuel_scenario=scenario,
        )
        for scenario in ("low", "base", "high")
    }
    finite_fuel = [
        value for value in fuel_values.values() if value is not None
    ]
    return {
        "point_estimate_s": point,
        "weighted_empirical_central_80_s": empirical,
        "delete_block_stability_80_s": stability,
        "fuel_sensitivity_s": fuel_values,
        "fuel_sensitivity_envelope_s": (
            [min(finite_fuel), max(finite_fuel)]
            if finite_fuel
            else None
        ),
        "roles": {
            "weighted_empirical_central_80_s": (
                "frozen_fit_at_anchor_plus_weighted_residual_central_range_"
                "not_prediction_interval"
            ),
            "delete_block_stability_80_s": (
                "leave_contiguous_three_laps_out_sensitivity_"
                "not_confidence_interval"
            ),
            "fuel_sensitivity_envelope_s": (
                "named_fuel_correction_sensitivity_not_statistical_interval"
            ),
        },
    }


def _condition_balance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    minimum_coverage = float(settings["minimum_profile_coverage"])
    left_coverage = _finite(left.get("context_coverage"))
    right_coverage = _finite(right.get("context_coverage"))
    if (
        left_coverage is None
        or right_coverage is None
        or min(left_coverage, right_coverage) < minimum_coverage
    ):
        return {
            "status": "NOT_TESTED",
            "maximum_normalized_distance": None,
            "components": {},
            "reason": "observable_condition_profile_coverage_below_gate",
        }
    actuals = {
        "lap_fraction": _worse_absolute_difference(
            left["lap_fraction_median"],
            right["lap_fraction_median"],
        ),
        "traffic_tvd": _traffic_total_variation(
            left["traffic_state_distribution"],
            right["traffic_state_distribution"],
        ),
        "non_green": _worse_absolute_difference(
            left["non_green_fraction"],
            right["non_green_fraction"],
        ),
        "pit_boundary": _worse_absolute_difference(
            left["pit_boundary_fraction"],
            right["pit_boundary_fraction"],
        ),
        "low_quality": _worse_absolute_difference(
            left["low_quality_fraction"],
            right["low_quality_fraction"],
        ),
    }
    nuisance_scale = max(
        float(
            left["nuisance_adjustment_median_s"].get(
                "same_event_compound_robust_scale_s",
                settings["nuisance_scale_floor_s"],
            )
        ),
        float(
            right["nuisance_adjustment_median_s"].get(
                "same_event_compound_robust_scale_s",
                settings["nuisance_scale_floor_s"],
            )
        ),
        float(settings["nuisance_scale_floor_s"]),
    )
    actuals["nuisance_adjustment"] = _worse_absolute_difference(
        left["nuisance_adjustment_median_s"],
        right["nuisance_adjustment_median_s"],
    )
    calipers = {
        "lap_fraction": float(settings["maximum_lap_fraction_difference"]),
        "traffic_tvd": float(
            settings["maximum_traffic_total_variation_distance"]
        ),
        "non_green": float(settings["maximum_non_green_fraction_difference"]),
        "pit_boundary": float(
            settings["maximum_pit_boundary_fraction_difference"]
        ),
        "low_quality": float(
            settings["maximum_low_quality_fraction_difference"]
        ),
        "nuisance_adjustment": nuisance_scale,
    }
    if any(value is None for value in actuals.values()):
        return {
            "status": "NOT_TESTED",
            "maximum_normalized_distance": None,
            "components": {},
            "reason": "observable_condition_profile_field_missing",
        }
    components = {
        key: {
            "absolute_difference": float(actuals[key]),
            "caliper": float(calipers[key]),
            "normalized_distance": float(actuals[key]) / float(calipers[key]),
        }
        for key in actuals
    }
    distance = max(
        row["normalized_distance"] for row in components.values()
    )
    direct = float(settings["maximum_direct_distance"])
    warning = float(settings["maximum_warning_distance"])
    status = (
        "balanced"
        if distance <= direct
        else "warning"
        if distance <= warning
        else "not_comparable"
    )
    return {
        "status": status,
        "maximum_normalized_distance": float(distance),
        "components": components,
        "reason": None,
    }


def _pairwise_stint_comparison(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    left_support = left["primary_fit"]["support_tyre_age_laps"]
    right_support = right["primary_fit"]["support_tyre_age_laps"]
    common_minimum = max(float(left_support[0]), float(right_support[0]))
    common_maximum = min(float(left_support[1]), float(right_support[1]))
    common_span = max(0.0, common_maximum - common_minimum)
    minimum_span = min(
        float(left_support[1]) - float(left_support[0]),
        float(right_support[1]) - float(right_support[0]),
    )
    overlap_ratio = common_span / minimum_span if minimum_span > 0.0 else 0.0
    reference_gap = abs(
        float(left["primary_fit"]["reference_tyre_age_laps"])
        - float(right["primary_fit"]["reference_tyre_age_laps"])
    )
    comparison_age = (
        float(
            np.clip(
                (
                    float(left["primary_fit"]["reference_tyre_age_laps"])
                    + float(right["primary_fit"]["reference_tyre_age_laps"])
                )
                / 2.0,
                common_minimum,
                common_maximum,
            )
        )
        if common_maximum >= common_minimum
        else None
    )
    left_profile, left_common_sample = _common_support_condition_profile(
        left,
        minimum_age=common_minimum,
        maximum_age=common_maximum,
    )
    right_profile, right_common_sample = _common_support_condition_profile(
        right,
        minimum_age=common_minimum,
        maximum_age=common_maximum,
    )
    phase_gap = _worse_absolute_difference(
        left_profile["lap_fraction_median"],
        right_profile["lap_fraction_median"],
    )
    balance = _condition_balance(
        left_profile,
        right_profile,
        settings,
    )
    failures: list[str] = []
    if left["compound"] != right["compound"]:
        failures.append("compound_mismatch")
    if reference_gap > float(settings["maximum_reference_tyre_age_gap_laps"]):
        failures.append("reference_tyre_age_gap_above_gate")
    if phase_gap is None:
        failures.append("phase_balance_not_tested")
    elif phase_gap > float(settings["maximum_lap_fraction_difference"]):
        failures.append("lap_fraction_difference_above_gate")
    if common_span < float(settings["minimum_common_support_span_laps"]):
        failures.append("common_tyre_age_support_below_gate")
    if overlap_ratio < float(settings["minimum_support_overlap_ratio"]):
        failures.append("tyre_age_support_overlap_ratio_below_gate")
    if balance["status"] == "NOT_TESTED":
        failures.append("observable_condition_balance_not_tested")
    elif balance["status"] == "warning":
        failures.append("observable_condition_balance_warning")
    elif balance["status"] == "not_comparable":
        failures.append("observable_condition_balance_failed")
    if left["qualification"]["status"] != "valid":
        failures.append("left_stint_gate_failed")
    if right["qualification"]["status"] != "valid":
        failures.append("right_stint_gate_failed")
    minimum_kish = float(settings["minimum_kish_effective_laps"])
    if float(left_common_sample["kish_effective_laps"]) < minimum_kish:
        failures.append("left_kish_effective_laps_below_range_gate")
    if float(right_common_sample["kish_effective_laps"]) < minimum_kish:
        failures.append("right_kish_effective_laps_below_range_gate")
    status = (
        "comparable"
        if not failures
        else "audit_only_balance_warning"
        if failures == ["observable_condition_balance_warning"]
        else "not_comparable"
    )
    left_anchor = (
        _stint_anchor_ranges(left, comparison_age)
        if comparison_age is not None
        else None
    )
    right_anchor = (
        _stint_anchor_ranges(right, comparison_age)
        if comparison_age is not None
        else None
    )
    left_pace = (
        _finite(left_anchor.get("point_estimate_s"))
        if left_anchor
        else None
    )
    right_pace = (
        _finite(right_anchor.get("point_estimate_s"))
        if right_anchor
        else None
    )
    conditional_model_delta = (
        float(left_pace - right_pace)
        if left_pace is not None and right_pace is not None
        else None
    )
    return {
        "pair_key": "~".join(
            sorted([str(left["stint_key"]), str(right["stint_key"])])
        ),
        "left_stint_key": left["stint_key"],
        "right_stint_key": right["stint_key"],
        "compound": left["compound"],
        "status": status,
        "gate_failures": failures,
        "reference_tyre_age_gap_laps": float(reference_gap),
        "lap_fraction_median_gap": phase_gap,
        "common_tyre_age_support_laps": [
            float(common_minimum),
            float(common_maximum),
        ],
        "common_support_span_laps": float(common_span),
        "support_overlap_ratio": float(overlap_ratio),
        "common_support_sample_audit": {
            "left": left_common_sample,
            "right": right_common_sample,
        },
        "common_support_condition_profiles": {
            "left": left_profile,
            "right": right_profile,
        },
        "comparison_tyre_age_laps": comparison_age,
        "left_anchor": left_anchor,
        "right_anchor": right_anchor,
        "left_minus_right_pace_s": (
            conditional_model_delta if status == "comparable" else None
        ),
        "direct_comparison_left_minus_right_pace_s": (
            conditional_model_delta if status == "comparable" else None
        ),
        "audit_only_conditional_model_left_minus_right_pace_s": (
            conditional_model_delta if status != "comparable" else None
        ),
        "delta_role": (
            "near_condition_direct_comparison_proxy"
            if status == "comparable"
            else "condition_model_audit_only_not_direct_comparison"
        ),
        "observable_condition_balance": balance,
    }


def build_stint_curve_sidecar(
    annotated: pd.DataFrame,
    stint_dossiers: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    report_id: str,
) -> dict[str, Any] | None:
    """发布逐圈散点、冻结拟合和显式可比基准，不在浏览器重新拟合。"""

    settings = config.get("stint_curve_evidence")
    if not settings or not bool(settings.get("enabled")):
        return None
    comparison = settings["comparison_gate"]
    interval_probability = float(settings["observed_range_probability"])
    pace_column = "pace_adjusted_lap_duration_s"
    valid_weight = pd.to_numeric(
        annotated.get("analysis_weight", 1.0), errors="coerce"
    ).fillna(0.0)
    event_usable = annotated.loc[
        annotated["pace_eligible_lap"].fillna(False).astype(bool)
        & valid_weight.gt(0.0)
    ].copy()
    event_usable["_analysis_weight"] = valid_weight.loc[event_usable.index]
    compound_nuisance_scale: dict[str, float] = {}
    for compound, compound_rows in event_usable.groupby(
        "compound", observed=True, dropna=False, sort=True
    ):
        _, scale = weighted_mad(
            pd.to_numeric(
                compound_rows.get(
                    "inclusive_nuisance_adjustment_s",
                    pd.Series(index=compound_rows.index, dtype=float),
                ),
                errors="coerce",
            ),
            compound_rows["_analysis_weight"],
        )
        compound_nuisance_scale[str(compound).upper()] = max(
            float(scale or 0.0),
            float(comparison["nuisance_scale_floor_s"]),
        )

    dossier_by_key = {_stint_key(row): row for row in stint_dossiers}
    evidence_rows: list[dict[str, Any]] = []
    group_keys = [
        "year",
        "meeting_name",
        "driver_id",
        "team_name",
        "stint_number",
        "compound",
    ]
    reporting_teams = {
        str(team) for team in config["scope"]["reporting_teams"]
    }
    published_laps = annotated.loc[
        annotated["stint_number"].notna()
        & annotated["team_name"].astype(str).isin(reporting_teams)
    ].copy()
    grouped = published_laps.groupby(
        group_keys,
        observed=True,
        dropna=False,
        sort=True,
    )
    for _, raw_group in grouped:
        group = raw_group.sort_values("lap_number", kind="stable").copy()
        identity = _stint_identity(group)
        key = _stint_key(identity)
        dossier = dossier_by_key.get(key)
        if dossier is None:
            raise RuntimeError(f"Stint sidecar 无法匹配报告行：{key}")
        eligible = group["pace_eligible_lap"].fillna(False).astype(bool)
        weight = pd.to_numeric(
            group.get("analysis_weight", 1.0), errors="coerce"
        ).fillna(0.0)
        usable = group.loc[eligible & weight.gt(0.0)].copy()
        usable["_analysis_weight"] = weight.loc[usable.index]
        strict_mask = (
            group["strict_pace_eligible_lap"].fillna(False).astype(bool)
            if "strict_pace_eligible_lap" in group
            else group["modelable_lap"].fillna(False).astype(bool)
        )
        strict_usable = group.loc[strict_mask].copy()
        warmup = int(config["stint_gate"]["warmup_laps"])
        strict_curve_input = strict_usable.iloc[warmup:].copy()
        strict_curve_pace_column = (
            "strict_pace_adjusted_lap_duration_s"
            if "strict_pace_adjusted_lap_duration_s"
            in strict_curve_input
            else pace_column
        )
        reference_age = _finite(
            dossier.get("representative_tyre_age_laps")
        )
        reference_pace = _finite(
            dossier.get("representative_tyre_age_pace_s")
        )
        slope = _finite(dossier.get("degradation_s_per_tyre_lap"))
        support = dossier.get("representative_tyre_age_support_laps")
        fit_identified = bool(
            reference_age is not None
            and reference_pace is not None
            and slope is not None
            and len(usable) >= int(settings["minimum_fit_points"])
            and isinstance(support, Sequence)
            and len(support) == 2
            and _finite(support[0]) is not None
            and _finite(support[1]) is not None
            and float(support[1]) > float(support[0])
        )
        if fit_identified:
            support = [float(support[0]), float(support[1])]
            prediction_grid = _linear_prediction_grid(
                support=support,
                reference_age=float(reference_age),
                reference_pace=float(reference_pace),
                slope=float(slope),
                points=int(settings["prediction_grid_points"]),
            )
            stability = _delete_block_fit_sensitivity(
                usable,
                pace_column=pace_column,
                reference_age=float(reference_age),
                support=support,
                settings=settings,
            )
        else:
            support = [None, None]
            prediction_grid = []
            stability = {
                "status": "NOT_TESTED",
                "method": "leave_contiguous_lap_block_out_v1",
                "block_length_laps": int(settings["delete_block_laps"]),
                "candidate_contiguous_blocks": 0,
                "skipped_non_contiguous_blocks": 0,
                "refits": 0,
                "probability": float(
                    settings["stability_interval_probability"]
                ),
                "prediction_band": [],
                "prediction_band_support_tyre_age_laps": None,
                "role": (
                    "stability_sensitivity_not_confidence_or_prediction_interval"
                ),
                "reason": "primary_linear_fit_not_identified",
            }

        scenario_fits: dict[str, Any] = {}
        scenario_columns = {
            "low": "fuel_corrected_low_lap_duration_s",
            "base": "fuel_corrected_lap_duration_s",
            "high": "fuel_corrected_high_lap_duration_s",
        }
        nuisance = pd.to_numeric(
            usable.get(
                "inclusive_nuisance_adjustment_s",
                pd.Series(index=usable.index, dtype=float),
            ),
            errors="coerce",
        )
        for name, column in scenario_columns.items():
            if reference_age is None or column not in usable:
                scenario_fits[name] = {
                    "status": "NOT_IDENTIFIED",
                    "reference_pace_s": None,
                    "slope_s_per_tyre_lap": None,
                }
                continue
            scenario_values = pd.to_numeric(
                usable[column], errors="coerce"
            ) - nuisance
            fit = _estimate_stint_reference_pace(
                usable["tyre_age"],
                scenario_values,
                usable["_analysis_weight"],
                reference_tyre_age_laps=float(reference_age),
            )
            scenario_fits[name] = {
                "status": fit["status"],
                "reference_pace_s": fit["pace_s"],
                "slope_s_per_tyre_lap": fit[
                    "slope_s_per_tyre_lap"
                ],
            }

        roles = _curve_validation_roles(
            group,
            row=dossier,
            config=config,
        )
        points = []
        for index, lap in group.iterrows():
            point_weight = _finite(lap.get("analysis_weight"))
            used = bool(
                lap.get("pace_eligible_lap")
                and point_weight is not None
                and point_weight > 0.0
            )
            age = _finite(lap.get("tyre_age"))
            adjusted = _finite(lap.get(pace_column))
            fitted = (
                float(
                    reference_pace
                    + slope * (float(age) - float(reference_age))
                )
                if (
                    fit_identified
                    and age is not None
                    and float(support[0]) <= age <= float(support[1])
                )
                else None
            )
            raw = _finite(lap.get("lap_duration"))
            nuisance_value = _finite(
                lap.get("inclusive_nuisance_adjustment_s")
            )
            scenario_values = {}
            for name, column in scenario_columns.items():
                value = _finite(lap.get(column))
                scenario_values[name] = (
                    float(value - nuisance_value)
                    if value is not None and nuisance_value is not None
                    else None
                )
            hard_reasons = lap.get("inclusive_hard_exclusion_reasons")
            if not isinstance(hard_reasons, list):
                hard_reasons = [] if hard_reasons in (None, "") else [
                    str(hard_reasons)
                ]
            context_flags = lap.get("reason_codes")
            if not isinstance(context_flags, list):
                context_flags = [] if context_flags in (None, "") else [
                    str(context_flags)
                ]
            points.append(
                {
                    "lap_number": int(lap["lap_number"]),
                    "tyre_age_laps": age,
                    "raw_lap_duration_s": raw,
                    "adjusted_pace_s": adjusted,
                    "adjusted_pace_by_fuel_scenario_s": scenario_values,
                    "fitted_primary_pace_s": fitted,
                    "primary_fit_residual_s": (
                        float(adjusted - fitted)
                        if adjusted is not None and fitted is not None
                        else None
                    ),
                    "fuel_correction_s": {
                        name: _finite(lap.get(f"fuel_correction_{name}_s"))
                        for name in ("low", "base", "high")
                    },
                    "nuisance_adjustment_s": {
                        "total": nuisance_value,
                        "stage": _finite(
                            lap.get("inclusive_stage_evolution_proxy_s")
                        ),
                        "traffic": _finite(
                            lap.get("inclusive_traffic_effect_proxy_s")
                        ),
                        "pit": _finite(
                            lap.get("inclusive_pit_effect_proxy_s")
                        ),
                        "event_quality": _finite(
                            lap.get("inclusive_event_quality_effect_proxy_s")
                        ),
                    },
                    "measurement_weight": _finite(
                        lap.get("inclusive_measurement_weight")
                    ),
                    "oof_residual_weight": _finite(
                        lap.get("inclusive_robust_residual_weight")
                    ),
                    "analysis_weight": point_weight,
                    "oof_residual_s": _finite(
                        lap.get("inclusive_oof_residual_s")
                    ),
                    "model_candidate": bool(
                        lap.get("inclusive_model_candidate", False)
                    ),
                    "used_for_primary_fit": used,
                    "strict_confirmation_eligible": bool(
                        lap.get("strict_pace_eligible_lap", False)
                    ),
                    "curve_validation_role": roles[index],
                    "inclusive_hard_exclusion_reasons": hard_reasons,
                    "observed_context_flags": context_flags,
                    "primary_disposition": str(
                        lap.get("primary_disposition") or ""
                    ),
                    "analysis_disposition": str(
                        lap.get("analysis_disposition") or ""
                    ),
                    "traffic_state": str(lap.get("traffic_state") or ""),
                    "all_green": bool(lap.get("all_green", False)),
                    "pit_boundary_proxy": bool(
                        lap.get("pit_boundary_proxy", False)
                    ),
                    "quality_ok": bool(lap.get("quality_ok", False)),
                    "distance_coverage_ratio": _finite(
                        lap.get("distance_coverage_ratio")
                    ),
                    "telemetry_samples": (
                        int(lap["telemetry_samples"])
                        if _finite(lap.get("telemetry_samples")) is not None
                        else None
                    ),
                    "gap_seconds_proxy": _finite(
                        lap.get("gap_seconds_proxy")
                    ),
                    "position": _finite(lap.get("position")),
                    "lap_fraction": _finite(lap.get("lap_fraction")),
                    "baseline_available": bool(
                        lap.get("baseline_available", False)
                    ),
                    "delta_to_reasonable_baseline_s": _finite(
                        lap.get("delta_to_reasonable_baseline_s")
                    ),
                }
            )

        fitted_values = np.asarray(
            [
                point["fitted_primary_pace_s"]
                for point in points
                if point["used_for_primary_fit"]
                and point["fitted_primary_pace_s"] is not None
            ],
            dtype=float,
        )
        adjusted_values = pd.to_numeric(
            usable.get(pace_column, pd.Series(dtype=float)),
            errors="coerce",
        )
        raw_values = pd.to_numeric(
            usable.get("lap_duration", pd.Series(dtype=float)),
            errors="coerce",
        )
        weights = usable["_analysis_weight"]
        if fit_identified and len(usable):
            fitted_series = (
                float(reference_pace)
                + float(slope)
                * (
                    pd.to_numeric(usable["tyre_age"], errors="coerce")
                    - float(reference_age)
                )
            )
            residual = adjusted_values - fitted_series
            finite_residual = residual.notna() & weights.gt(0.0)
            residual_weight = weights.loc[finite_residual]
            residual_values = residual.loc[finite_residual]
            weighted_mae = (
                float(
                    np.average(
                        np.abs(residual_values),
                        weights=residual_weight,
                    )
                )
                if len(residual_values)
                else None
            )
            weighted_rmse = (
                float(
                    np.sqrt(
                        np.average(
                            np.square(residual_values),
                            weights=residual_weight,
                        )
                    )
                )
                if len(residual_values)
                else None
            )
            _, residual_mad = weighted_mad(
                residual_values,
                residual_weight,
            )
        else:
            residual = pd.Series(dtype=float)
            weighted_mae = None
            weighted_rmse = None
            residual_mad = None
        compound = str(identity["compound"]).upper()
        profile = _condition_profile(
            group,
            usable,
            nuisance_scale_s=compound_nuisance_scale.get(
                compound,
                float(comparison["nuisance_scale_floor_s"]),
            ),
        )
        primary_fit = {
            "status": (
                "IDENTIFIED"
                if fit_identified
                else "NOT_IDENTIFIED"
            ),
            "estimator": (
                "oof_reliability_weighted_theil_sen_linear_projection_v1"
            ),
            "equation": (
                {
                    "form": (
                        "pace_s = reference_pace_s + "
                        "slope_s_per_tyre_lap * "
                        "(tyre_age_laps - reference_tyre_age_laps)"
                    ),
                    "reference_tyre_age_laps": reference_age,
                    "reference_pace_s": reference_pace,
                    "slope_s_per_tyre_lap": slope,
                }
                if fit_identified
                else None
            ),
            "reference_tyre_age_laps": reference_age,
            "reference_pace_s": reference_pace,
            "slope_s_per_tyre_lap": slope,
            "support_tyre_age_laps": support,
            "prediction_grid": prediction_grid,
            "stability_interval": stability,
            "fuel_scenarios": scenario_fits,
            "confidence_interval": {
                "status": "NOT_TESTED",
                "reason": (
                    "未冻结可校准的 Stint 内中心置信区间协议；"
                    "稳定性范围不得改名为置信区间。"
                ),
            },
            "new_lap_prediction_interval": {
                "status": "NOT_TESTED",
                "reason": (
                    "未冻结独立校准样本；观测中间范围不得改名为预测区间。"
                ),
            },
        }
        effective_size = kish_effective_sample_size(weights)
        range_supported = effective_size >= float(
            settings["minimum_kish_effective_laps_for_range"]
        )
        evidence_rows.append(
            {
                "stint_key": key,
                **identity,
                "lap_range": [
                    int(group["lap_number"].min()),
                    int(group["lap_number"].max()),
                ],
                "phase_midpoint_fraction": _numeric_median(
                    group["lap_fraction"]
                ),
                "qualification": {
                    "status": dossier["status"],
                    "gate_failures": list(dossier["gate_failures"]),
                    "strict_confirmation": dossier[
                        "strict_confirmation"
                    ],
                },
                "sample_audit": {
                    "observed_laps": int(len(group)),
                    "fit_laps": int(len(usable)),
                    "strict_confirmation_laps": int(len(strict_usable)),
                    "effective_weight_laps": float(weights.sum()),
                    "kish_effective_laps": effective_size,
                    "unique_tyre_ages": int(
                        pd.to_numeric(
                            usable["tyre_age"], errors="coerce"
                        ).nunique()
                    ),
                    "observed_range_status": (
                        "ESTIMATED"
                        if range_supported
                        else "INSUFFICIENT_SUPPORT"
                    ),
                    "raw_lap_time_central_range_s": (
                        _weighted_interval(
                            raw_values,
                            weights,
                            interval_probability,
                        )
                        if range_supported
                        else None
                    ),
                    "adjusted_pace_central_range_s": (
                        _weighted_interval(
                            adjusted_values,
                            weights,
                            interval_probability,
                        )
                        if range_supported
                        else None
                    ),
                    "primary_fit_residual_central_range_s": (
                        _weighted_interval(
                            residual,
                            weights,
                            interval_probability,
                        )
                        if len(residual) and range_supported
                        else None
                    ),
                    "primary_fit_weighted_mae_s": weighted_mae,
                    "primary_fit_weighted_rmse_s": weighted_rmse,
                    "primary_fit_residual_mad_s": residual_mad,
                    "observed_range_probability": interval_probability,
                    "fitted_points": int(len(fitted_values)),
                },
                "condition_profile": profile,
                "primary_fit": primary_fit,
                "confirmed_shape": _descriptive_confirmed_curve_refit(
                    strict_curve_input,
                    pace_column=strict_curve_pace_column,
                    curve_audit=dossier.get("tyre_curve_model_audit"),
                    support=support,
                    curve_settings=config["stint_curve_models"],
                    evidence_settings=settings,
                )
                if fit_identified
                else {
                    "status": "NOT_AVAILABLE",
                    "selected_model": None,
                    "reason": "primary_fit_not_identified",
                    "prediction_grid": [],
                    "equation": None,
                },
                "points": points,
            }
        )

    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(evidence_rows):
        for right in evidence_rows[left_index + 1 :]:
            if left["compound"] != right["compound"]:
                continue
            if (
                left["primary_fit"]["status"] != "IDENTIFIED"
                or right["primary_fit"]["status"] != "IDENTIFIED"
            ):
                continue
            pairs.append(
                _pairwise_stint_comparison(
                    left,
                    right,
                    comparison,
                )
            )
    return {
        "schema_version": str(settings["schema_version"]),
        "report_id": report_id,
        "status": "available",
        "contract": {
            "x": "tyre_age_laps",
            "y": "pace_adjusted_lap_duration_s",
            "weight": "analysis_weight",
            "primary_fit": (
                "oof_reliability_weighted_theil_sen_linear_projection_v1"
            ),
            "browser_refits_models": False,
            "support_extrapolation_allowed": False,
            "observed_range_role": (
                "weighted_empirical_central_range_not_prediction_interval"
            ),
            "stability_interval_role": (
                "leave_block_out_sensitivity_not_confidence_interval"
            ),
            "fuel_scenario_role": (
                "named_low_base_high_sensitivity_not_statistical_interval"
            ),
            "comparison_gate": dict(comparison),
        },
        "coverage": {
            "scope": "configured_reporting_teams",
            "reporting_teams": sorted(reporting_teams),
            "stints": len(evidence_rows),
            "valid_stints": sum(
                row["qualification"]["status"] == "valid"
                for row in evidence_rows
            ),
            "points": sum(len(row["points"]) for row in evidence_rows),
            "fit_points": sum(
                row["sample_audit"]["fit_laps"] for row in evidence_rows
            ),
            "range_supported_stints": sum(
                row["sample_audit"]["observed_range_status"] == "ESTIMATED"
                for row in evidence_rows
            ),
            "stability_estimated_stints": sum(
                row["primary_fit"]["stability_interval"].get("status")
                == "ESTIMATED"
                for row in evidence_rows
            ),
            "pairwise_comparisons": len(pairs),
            "directly_comparable_pairs": sum(
                row["status"] == "comparable" for row in pairs
            ),
            "balance_warning_pairs": sum(
                row["status"] == "audit_only_balance_warning"
                for row in pairs
            ),
            "not_comparable_pairs": sum(
                row["status"] == "not_comparable" for row in pairs
            ),
        },
        "stints": evidence_rows,
        "pairwise_comparisons": pairs,
        "boundaries": {
            "real_fuel_load_identified": False,
            "physical_tyre_wear_identified": False,
            "weather_and_track_temperature_identified": False,
            "confidence_interval_available": False,
            "new_lap_prediction_interval_available": False,
        },
    }


def _kalman_local_linear_forecast(
    x_values: np.ndarray,
    y_values: np.ndarray,
    target_x: float,
    settings: Mapping[str, Any],
) -> float | None:
    """低资源局部线性高斯状态空间一步/多步预测；不是 Bayesian Stan 拟合。"""

    finite = np.isfinite(x_values) & np.isfinite(y_values)
    x = np.asarray(x_values[finite], dtype=float)
    y = np.asarray(y_values[finite], dtype=float)
    if len(x) < 3 or np.unique(x).size < 2:
        return None
    order = np.argsort(x, kind="stable")
    x = x[order]
    y = y[order]
    initial_slope = _theil_sen_slope(
        pd.Series(x[: min(4, len(x))]),
        pd.Series(y[: min(4, len(y))]),
    )
    state = np.asarray(
        [float(y[0]), float(initial_slope or 0.0)], dtype=float
    )
    covariance = np.diag([1.0, 0.1]).astype(float)
    _, robust_sigma = _median_mad(y)
    sigma_floor = float(settings["measurement_sigma_floor_s"])
    measurement_variance = max(
        float((robust_sigma or sigma_floor) ** 2),
        sigma_floor**2,
    )
    level_q = float(settings["level_process_variance"])
    slope_q = float(settings["slope_process_variance"])
    last_x = float(x[0])
    for index in range(1, len(x)):
        delta = max(float(x[index] - last_x), 1e-6)
        transition = np.asarray([[1.0, delta], [0.0, 1.0]])
        process = np.diag([level_q * delta, slope_q * delta])
        state = transition @ state
        covariance = transition @ covariance @ transition.T + process
        innovation = float(y[index] - state[0])
        innovation_variance = float(
            covariance[0, 0] + measurement_variance
        )
        gain = covariance[:, 0] / innovation_variance
        state = state + gain * innovation
        covariance = covariance - np.outer(gain, covariance[0, :])
        last_x = float(x[index])
    forecast_delta = float(target_x - last_x)
    return float(state[0] + forecast_delta * state[1])


def _curve_model_predict(
    model: str,
    x_values: np.ndarray,
    y_values: np.ndarray,
    target_x: float,
    settings: Mapping[str, Any],
) -> float | None:
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    x = np.asarray(x_values[finite], dtype=float)
    y = np.asarray(y_values[finite], dtype=float)
    if not len(x):
        return None
    if model == "constant_median":
        prediction = float(np.median(y))
    elif model == "linear_theil_sen":
        slope = _theil_sen_slope(pd.Series(x), pd.Series(y))
        if slope is None:
            return None
        intercept = float(np.median(y - slope * x))
        prediction = float(intercept + slope * target_x)
    elif model == "log_theil_sen":
        transformed = np.log1p(np.clip(x, a_min=0.0, a_max=None))
        target = float(np.log1p(max(target_x, 0.0)))
        slope = _theil_sen_slope(
            pd.Series(transformed), pd.Series(y)
        )
        if slope is None:
            return None
        intercept = float(np.median(y - slope * transformed))
        prediction = float(intercept + slope * target)
    elif model == "quadratic_least_squares":
        if len(x) < 5 or np.unique(x).size < 3:
            return None
        center = float(np.median(x))
        coefficients = np.polyfit(x - center, y, 2)
        prediction = float(np.polyval(coefficients, target_x - center))
    elif model == "kalman_local_linear":
        prediction = _kalman_local_linear_forecast(
            x,
            y,
            target_x,
            settings["kalman"],
        )
        if prediction is None:
            return None
    else:
        raise ValueError(f"未知 stint 曲线候选：{model}")
    maximum = float(settings["maximum_prediction_deviation_s"])
    center_y = float(np.median(y))
    return float(np.clip(prediction, center_y - maximum, center_y + maximum))


def _evaluate_stint_curve_models(
    frame: pd.DataFrame,
    *,
    pace_column: str,
    settings: Mapping[str, Any],
    fuel_sensitivity_interval: Sequence[float] | None,
) -> dict[str, Any]:
    """用滚动原点误差决定曲线模型是否比常数基线有实际价值。"""

    candidates = [str(value) for value in settings["candidates"]]
    minimum_train = int(settings["minimum_train_laps"])
    minimum_validation = int(settings["minimum_validation_laps"])
    work = frame.sort_values("tyre_age", kind="stable")
    x = pd.to_numeric(work["tyre_age"], errors="coerce").to_numpy(
        dtype=float
    )
    y = pd.to_numeric(work[pace_column], errors="coerce").to_numpy(
        dtype=float
    )
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    error_records: dict[str, list[tuple[int, float]]] = {
        model: [] for model in candidates
    }
    if len(x) >= minimum_train + 1:
        for index in range(minimum_train, len(x)):
            for model in candidates:
                prediction = _curve_model_predict(
                    model,
                    x[:index],
                    y[:index],
                    float(x[index]),
                    settings,
                )
                if prediction is not None:
                    error_records[model].append(
                        (index, abs(float(y[index]) - prediction))
                    )
    candidate_mae: dict[str, float | None] = {
        model: (
            float(np.median([value for _, value in records]))
            if len(records) >= minimum_validation
            else None
        )
        for model, records in error_records.items()
    }
    constant_mae = candidate_mae.get("constant_median")
    protocol = str(
        settings.get("validation_protocol", "rolling_origin_selection_only_v1")
    )
    selection_audit: dict[str, Any] | None = None
    confirmation_audit: dict[str, Any] | None = None
    if protocol == "ordered_selection_confirmation_v1":
        constant_records = dict(error_records["constant_median"])
        indices = sorted(constant_records)
        minimum_selection = int(settings["minimum_selection_points"])
        minimum_confirmation = int(settings["minimum_confirmation_points"])
        split = int(
            np.floor(
                len(indices) * float(settings["selection_fraction"])
            )
        )
        split = max(minimum_selection, split)
        split = min(split, len(indices) - minimum_confirmation)
        if split < minimum_selection or len(indices) - split < minimum_confirmation:
            selection_indices: list[int] = []
            confirmation_indices: list[int] = []
        else:
            selection_indices = indices[:split]
            confirmation_indices = indices[split:]
        selection_mae: dict[str, float | None] = {}
        for model, records in error_records.items():
            lookup = dict(records)
            values = [
                lookup[index]
                for index in selection_indices
                if index in lookup
            ]
            selection_mae[model] = (
                float(np.median(values))
                if len(values) >= minimum_selection
                else None
            )
        selection_available = {
            model: value
            for model, value in selection_mae.items()
            if value is not None
        }
        selected_model = (
            min(
                selection_available,
                key=lambda model: (selection_available[model], model),
            )
            if selection_available
            else None
        )
        selected_lookup = (
            dict(error_records[selected_model])
            if selected_model is not None
            else {}
        )
        common_confirmation = [
            index
            for index in confirmation_indices
            if index in selected_lookup and index in constant_records
        ]
        selected_confirmation_values = [
            selected_lookup[index] for index in common_confirmation
        ]
        constant_confirmation_values = [
            constant_records[index] for index in common_confirmation
        ]
        selected_mae = (
            float(np.median(selected_confirmation_values))
            if len(selected_confirmation_values) >= minimum_confirmation
            else None
        )
        confirmation_constant_mae = (
            float(np.median(constant_confirmation_values))
            if len(constant_confirmation_values) >= minimum_confirmation
            else None
        )
        improvement = (
            float(confirmation_constant_mae - selected_mae)
            if confirmation_constant_mae is not None
            and selected_mae is not None
            else None
        )
        validated = bool(
            selected_model is not None
            and selected_model != "constant_median"
            and improvement is not None
            and improvement
            >= float(settings["minimum_confirmation_improvement_s"])
        )
        if (
            not selection_available
            or selected_mae is None
            or confirmation_constant_mae is None
        ):
            status = "audit_only_insufficient_confirmation_laps"
        elif validated:
            status = "validated_confirmatory_proxy"
        else:
            status = "audit_only_confirmation_no_gain"
        selection_audit = {
            "points": len(selection_indices),
            "candidate_mae_s": selection_mae,
        }
        confirmation_audit = {
            "points": len(common_confirmation),
            "constant_median_mae_s": confirmation_constant_mae,
            "selected_model_mae_s": selected_mae,
            "improvement_s": improvement,
        }
    else:
        available = {
            model: value
            for model, value in candidate_mae.items()
            if value is not None
        }
        selected_model = (
            min(available, key=lambda model: (available[model], model))
            if available
            else None
        )
        selected_mae = (
            float(available[selected_model])
            if selected_model is not None
            else None
        )
        improvement = (
            float(constant_mae - selected_mae)
            if constant_mae is not None and selected_mae is not None
            else None
        )
        validated = bool(
            selected_model is not None
            and selected_model != "constant_median"
            and improvement is not None
            and improvement
            >= float(settings["minimum_mae_improvement_s"])
        )
        if not available or constant_mae is None:
            status = "audit_only_insufficient_validation_laps"
        elif validated:
            status = "validated_predictive_proxy"
        else:
            status = "audit_only_no_predictive_gain"
    direction_robust_positive = bool(
        fuel_sensitivity_interval is not None
        and len(fuel_sensitivity_interval) == 2
        and float(fuel_sensitivity_interval[0]) > 0.0
    )
    return {
        "status": status,
        "selected_model": selected_model,
        "rolling_origin_points": max(
            (len(records) for records in error_records.values()), default=0
        ),
        "validation_protocol": protocol,
        "candidate_mae_s": candidate_mae,
        "rolling_origin_mae_s": {
            "constant_median": constant_mae,
            "selected": selected_mae,
            "improvement": improvement,
        },
        "selection_audit": selection_audit,
        "confirmation_audit": confirmation_audit,
        "fuel_sensitivity_direction_robust_positive": (
            direction_robust_positive
        ),
        "strategy_curve_eligible": bool(
            validated and direction_robust_positive
        ),
        "kalman_model_boundary": (
            "local_linear_gaussian_filter_not_bayesian_stan_posterior"
        ),
    }


def _stable_seed(base: int, *parts: object) -> int:
    token = "|".join(str(part) for part in parts).encode("utf-8")
    digest = int.from_bytes(hashlib.sha256(token).digest()[:4], "big")
    return int((int(base) + digest) % (2**32 - 1))


def validate_race_dossier_config(config: Mapping[str, Any]) -> None:
    """冻结关键边界；拒绝重新启用统一评分或 Q/R 混合。"""

    required_sections = {
        "scope",
        "coverage_gate",
        "lap_filter",
        "stint_gate",
        "baseline",
        "strategy_envelope",
        "materiality",
        "publication_contract",
        "bootstrap",
    }
    missing = sorted(required_sections - set(config))
    if missing:
        raise ValueError(f"Race Dossier 配置缺少段：{missing}")
    contract = config["publication_contract"]
    if contract.get("analysis_unit") != "complete_race":
        raise ValueError("Race Dossier 的 analysis_unit 必须是 complete_race")
    if not bool(contract.get("qualifying_race_independent")):
        raise ValueError("排位与正赛必须独立分析，禁止统一周末评分")
    if bool(contract.get("publish_pac")):
        raise ValueError("本轮 PAC 必须保持 null")
    if bool(contract.get("publish_ovr")):
        raise ValueError("本轮 OVR 必须保持 null")
    if bool(contract.get("classification_or_points_used_for_fit")):
        raise ValueError("年终名次或积分只能审计，不能进入拟合")
    if bool(contract.get("claim_real_fuel_load")):
        raise ValueError("公开数据不可声称真实燃油量")
    if bool(contract.get("claim_pirelli_internal_simulation")):
        raise ValueError("公开数据代理不能冒充倍耐力内部模拟")
    if int(config["bootstrap"]["repetitions"]) < 100:
        raise ValueError("bootstrap repetitions 至少为 100")
    if not config["scope"].get("reporting_teams"):
        raise ValueError("reporting_teams 不能为空")
    method_version = str(config.get("method_version", "v8"))
    if not method_version.startswith("v") or not method_version[1:].isdigit():
        raise ValueError("method_version 必须采用 vN 格式")
    if int(method_version[1:]) >= 11:
        if "simulation_proxies" not in config:
            raise ValueError("v11+ 配置必须声明 simulation_proxies")
        if "module_publication_gate" not in config:
            raise ValueError("v11+ 配置必须声明 module_publication_gate")
        proxies = config["simulation_proxies"]
        required_proxies = {"fuel", "track_evolution", "traffic"}
        missing_proxies = sorted(required_proxies - set(proxies))
        if missing_proxies:
            raise ValueError(f"simulation_proxies 缺少段：{missing_proxies}")
        fuel = proxies["fuel"]
        if fuel.get("model") != "linear_remaining_mass_sensitivity_v1":
            raise ValueError("v11 燃油代理模型名称未冻结")
        if fuel.get("burn_rule") != "linear_by_scheduled_race_fraction":
            raise ValueError("v11 只接受按计划比赛圈比例线性耗油的敏感性代理")
        for key in ("initial_fuel_kg", "lap_time_s_per_kg"):
            values = fuel[key]
            low = float(values["low"])
            base_value = float(values["base"])
            high = float(values["high"])
            if not 0.0 <= low <= base_value <= high:
                raise ValueError(f"{key} 必须满足 0 <= low <= base <= high")
        track = proxies["track_evolution"]
        if track.get("model") != "robust_additive_field_median_v1":
            raise ValueError("v11 赛道演化代理模型名称未冻结")
        traffic = proxies["traffic"]
        if traffic.get("model") != "empirical_gap_band_fixed_effects_v1":
            raise ValueError("v11 交通代理模型名称未冻结")
        if float(traffic["maximum_penalty_s"]) <= 0.0:
            raise ValueError("traffic maximum_penalty_s 必须大于 0")
    if int(method_version[1:]) >= 12:
        if "stint_curve_models" not in config:
            raise ValueError("v12+ 配置必须声明 stint_curve_models")
        curve = config["stint_curve_models"]
        candidates = set(curve.get("candidates", []))
        required_candidates = {
            "constant_median",
            "linear_theil_sen",
            "log_theil_sen",
            "quadratic_least_squares",
            "kalman_local_linear",
        }
        if candidates != required_candidates:
            raise ValueError(
                "v12 stint 曲线候选必须冻结为常数、线性、对数、二次和局部线性状态空间"
            )
        if int(curve["minimum_train_laps"]) < 4:
            raise ValueError("stint 曲线 minimum_train_laps 至少为 4")
        if int(curve["minimum_validation_laps"]) < 2:
            raise ValueError("stint 曲线 minimum_validation_laps 至少为 2")
    if int(method_version[1:]) >= 13:
        curve = config["stint_curve_models"]
        if curve.get("validation_protocol") != (
            "ordered_selection_confirmation_v1"
        ):
            raise ValueError("v13+ 必须使用有序选择/确认协议")
        if int(curve["minimum_selection_points"]) < 2:
            raise ValueError("曲线选择段至少需要 2 个滚动原点")
        if int(curve["minimum_confirmation_points"]) < 2:
            raise ValueError("曲线确认段至少需要 2 个滚动原点")
        if not 0.25 <= float(curve["selection_fraction"]) <= 0.75:
            raise ValueError("selection_fraction 必须位于 0.25 到 0.75")
        if "pit_loss_proxy" not in config:
            raise ValueError("v13+ 配置必须声明 pit_loss_proxy")
        pit = config["pit_loss_proxy"]
        if pit.get("model") != "local_adjacent_pace_pit_cycle_v1":
            raise ValueError("v13 进站周期损失代理模型名称未冻结")
        if int(pit["minimum_adjacent_pace_laps_each_side"]) < 2:
            raise ValueError("进站周期局部基线每侧至少需要 2 圈")
    version_number = int(method_version[1:])
    if version_number == 14:
        if config["stint_curve_models"].get("execution_scope") != (
            "valid_stints_only"
        ):
            raise ValueError("v14 曲线竞赛只能对已通过物理门的 Stint 执行")
    if version_number >= 15:
        if "inclusive_robust_model" not in config:
            raise ValueError("v15+ 必须声明全量稳健模型 inclusive_robust_model")
        inclusive = config["inclusive_robust_model"]
        if not bool(inclusive.get("enabled")):
            raise ValueError("v15 全量稳健模型不得关闭")
        if inclusive.get("model") != "crossfit_huber_additive_v1":
            raise ValueError("v15 全量稳健模型名称未冻结")
        if int(inclusive.get("crossfit_folds", 0)) < 2:
            raise ValueError("v15 样本外软清洗至少需要两折")
        if inclusive.get("cleaning_residual_source") != "out_of_fold_only":
            raise ValueError("v15 清洗权重只能来自样本外残差")
        if not bool(inclusive.get("strict_confirmation_layer")):
            raise ValueError("v15 必须保留严格复核层")
        if config["stint_curve_models"].get("execution_scope") != (
            "strict_confirmed_stints_only"
        ):
            raise ValueError("v15 曲线竞赛只能对严格复核通过的 Stint 执行")
        if contract.get("inclusive_model_is_causal_truth") is not False:
            raise ValueError("v15 必须明确全量条件模型不是因果真值")
        if (
            contract.get("iterative_cleaning_uses_in_sample_residuals")
            is not False
        ):
            raise ValueError("v15 禁止使用样本内残差迭代清洗")
        if float(inclusive["hard_minimum_distance_coverage"]) <= 0.0:
            raise ValueError("v15 硬距离门必须大于 0")
        if int(inclusive["hard_minimum_samples_per_lap"]) < 1:
            raise ValueError("v15 硬样本门必须至少为 1")
        if float(inclusive["minimum_analysis_weight"]) <= 0.0:
            raise ValueError("v15 最小分析权重必须大于 0")
        if not 0.0 < float(inclusive["minimum_robust_weight"]) <= 1.0:
            raise ValueError("v15 最小稳健权重必须位于 0 到 1")
        if float(inclusive["maximum_oof_mae_s"]) <= 0.0:
            raise ValueError("v15 样本外 MAE 绝对上限必须大于 0")
        if not 0.0 <= float(
            inclusive["maximum_saturated_adjustment_fraction"]
        ) <= 1.0:
            raise ValueError("v15 修正饱和比例上限必须位于 0 到 1")
        for key, value in inclusive["measurement_weights"].items():
            if not 0.0 < float(value) <= 1.0:
                raise ValueError(f"v15 观测权重 {key} 必须位于 0 到 1")
        required_stint_keys = {
            "minimum_effective_laps",
            "minimum_effective_fraction",
            "minimum_candidate_fraction",
            "minimum_effective_baseline_laps_for_cumulative",
            "strict_confirmation_minimum_modelable_laps",
            "strict_confirmation_minimum_modelable_fraction",
        }
        missing_stint_keys = sorted(
            required_stint_keys - set(config["stint_gate"])
        )
        if missing_stint_keys:
            raise ValueError(
                f"v15 stint_gate 缺少加权/严格复核门：{missing_stint_keys}"
            )
        if "minimum_effective_laps_per_cell" not in config["baseline"]:
            raise ValueError("v15 baseline 必须声明最小有效权重圈数")
    if version_number >= 16:
        if "stint_reference_pace" not in config:
            raise ValueError("v16+ 必须声明单一 Stint 胎龄代表配速协议")
        reference = config["stint_reference_pace"]
        if reference.get("model") != (
            "weighted_theil_sen_within_stint_reference_v1"
        ):
            raise ValueError("v16 Stint 代表配速模型名称未冻结")
        if reference.get("reference_tyre_age") != (
            "within_stint_weighted_median"
        ):
            raise ValueError("v16 参考胎龄必须位于本 Stint 样本内")
        if bool(reference.get("allow_reference_age_extrapolation")):
            raise ValueError("v16 禁止为补齐代表配速而外推胎龄")
        if float(
            reference["maximum_direct_comparison_tyre_age_gap_laps"]
        ) <= 0.0:
            raise ValueError("v16 直接比较胎龄差门槛必须大于 0")
        if reference.get("claim") != (
            "fuel_and_observed_condition_adjusted_tyre_age_"
            "conditional_proxy"
        ):
            raise ValueError("v16 必须把代表配速声明为条件数据代理")
        boundaries = config.get("boundaries", {})
        if boundaries.get(
            "representative_stint_pace_is_an_observed_lap"
        ) is not False:
            raise ValueError("v16 必须声明代表配速不是挑选的一条实测圈")
        if boundaries.get(
            "representative_stint_pace_removes_tyre_age_effect"
        ) is not False:
            raise ValueError("v16 必须保留而非删除胎龄效应")
    if version_number >= 17:
        evidence = config.get("stint_curve_evidence")
        if not evidence or not bool(evidence.get("enabled")):
            raise ValueError("v17+ 必须启用逐圈 Stint 曲线证据 sidecar")
        if evidence.get("schema_version") != (
            "race-dossier-stint-curve-evidence-v17"
        ):
            raise ValueError("v17 sidecar schema_version 未冻结")
        if evidence.get("primary_fit") != (
            "oof_reliability_weighted_theil_sen_linear_projection_v1"
        ):
            raise ValueError("v17 主回归必须复用加权 Theil-Sen 条件代理")
        if int(evidence["delete_block_laps"]) != 3:
            raise ValueError("v17 稳定性审计必须删除连续三圈")
        if not np.isclose(
            float(evidence["observed_range_probability"]), 0.80
        ):
            raise ValueError("v17 观测经验范围必须冻结为中间 80%")
        if not np.isclose(
            float(evidence["stability_interval_probability"]), 0.80
        ):
            raise ValueError("v17 稳定性范围必须冻结为中间 80%")
        if int(evidence["minimum_kish_effective_laps_for_range"]) < 8:
            raise ValueError("v17 配速范围至少要求 Kish ESS 8")
        comparison = evidence.get("comparison_gate", {})
        frozen_values = {
            "maximum_reference_tyre_age_gap_laps": 2.0,
            "maximum_lap_fraction_difference": 0.20,
            "maximum_traffic_total_variation_distance": 0.35,
            "maximum_non_green_fraction_difference": 0.20,
            "maximum_pit_boundary_fraction_difference": 0.20,
            "maximum_low_quality_fraction_difference": 0.20,
            "nuisance_scale_floor_s": 0.10,
            "minimum_profile_coverage": 0.80,
            "minimum_common_support_span_laps": 5.0,
            "minimum_support_overlap_ratio": 0.50,
            "minimum_kish_effective_laps": 8.0,
            "maximum_direct_distance": 0.65,
            "maximum_warning_distance": 0.90,
        }
        for key, expected in frozen_values.items():
            if key not in comparison or not np.isclose(
                float(comparison[key]), expected
            ):
                raise ValueError(f"v17 可比条件门 {key} 必须冻结为 {expected}")
        boundaries = config.get("boundaries", {})
        for key in (
            "stability_interval_is_confidence_interval",
            "observed_range_is_prediction_interval",
            "fuel_scenario_is_statistical_interval",
            "browser_refits_stint_models",
        ):
            if boundaries.get(key) is not False:
                raise ValueError(f"v17 必须明确 {key}=false")


def extract_race_lap_universe(
    files: Sequence[Path],
    *,
    duckdb_temp_directory: Path,
    threads: int = 4,
    file_batch_size: int = 4,
    memory_limit: str = "2GB",
    batch_cooldown_seconds: float = 0.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """从规范逐点 Parquet 聚合完整观测圈宇宙。

    DuckDB 临时目录必须由调用方显式传入项目内 D 盘路径，避免隐式使用
    系统盘。函数不执行网络访问。
    """

    if not files:
        return pd.DataFrame(columns=sorted(REQUIRED_LAP_COLUMNS))
    temp_directory = duckdb_temp_directory.resolve()
    temp_directory.mkdir(parents=True, exist_ok=True)
    batch_size = int(file_batch_size)
    if batch_size < 1:
        raise ValueError("file_batch_size 必须至少为 1")
    normalized_memory_limit = str(memory_limit).strip().upper()
    if not normalized_memory_limit.endswith(("MB", "GB")):
        raise ValueError("memory_limit 必须使用 MB 或 GB 单位")
    try:
        memory_value = int(normalized_memory_limit[:-2])
    except ValueError as exc:
        raise ValueError("memory_limit 必须是正整数加 MB 或 GB 单位") from exc
    if memory_value < 1:
        raise ValueError("memory_limit 必须大于 0")
    cooldown = float(batch_cooldown_seconds)
    if cooldown < 0.0 or cooldown > 5.0:
        raise ValueError("batch_cooldown_seconds 必须位于 0 到 5 秒")
    frames: list[pd.DataFrame] = []
    safe_temp = temp_directory.as_posix().replace("'", "''")
    query = """
            SELECT
                CAST(year AS INTEGER) AS year,
                replace(meeting, '_', ' ') AS meeting_name,
                arg_min(driver_acronym, sample_index) AS driver_id,
                arg_min(team, sample_index) AS team_name,
                CAST(lap AS INTEGER) AS lap_number,
                CAST(arg_min(lap_duration, sample_index) AS DOUBLE) AS lap_duration,
                arg_min(compound, sample_index) AS compound,
                CAST(arg_min(stint, sample_index) AS DOUBLE) AS stint_number,
                CAST(arg_min(tyre_life, sample_index) AS DOUBLE) AS tyre_age,
                CAST(arg_min(position, sample_index) AS DOUBLE) AS position,
                CAST(mode(driver_ahead) AS VARCHAR) AS driver_ahead_number,
                bool_and(coalesce(is_accurate, false)) AS is_accurate,
                bool_or(coalesce(deleted, false)) AS deleted,
                bool_and(trim(CAST(track_status AS VARCHAR)) = '1') AS all_green,
                string_agg(
                    DISTINCT trim(CAST(track_status AS VARCHAR)),
                    '|' ORDER BY trim(CAST(track_status AS VARCHAR))
                ) AS track_status_codes,
                CAST(max(distance) - min(distance) AS DOUBLE) AS distance_metres,
                CAST(median(speed) AS DOUBLE) AS median_speed_kph,
                CAST(median(distance_to_driver_ahead) AS DOUBLE)
                    AS median_gap_to_ahead_m,
                count(*) AS telemetry_samples,
                arg_min(source_commit, sample_index) AS source_commit
            FROM read_parquet(?, hive_partitioning=false)
            GROUP BY year, meeting, driver_number, lap
            ORDER BY year, meeting, driver_id, lap_number
        """
    # 每批使用独立连接，确保 DuckDB 缓冲池在批次间释放；分区键包含
    # year/meeting，因此各批聚合后可以直接拼接，不会把同一场比赛拆开。
    total_batches = (len(files) + batch_size - 1) // batch_size
    for batch_index, start in enumerate(range(0, len(files), batch_size), start=1):
        batch = files[start : start + batch_size]
        connection = duckdb.connect()
        try:
            connection.execute(f"SET threads TO {max(1, int(threads))}")
            connection.execute(f"SET temp_directory='{safe_temp}'")
            connection.execute(f"SET memory_limit='{memory_value}{normalized_memory_limit[-2:]}'")
            frames.append(
                connection.execute(
                    query, [[str(path.resolve()) for path in batch]]
                ).fetch_df()
            )
        finally:
            connection.close()
        if progress_callback is not None:
            progress_callback(batch_index, total_batches)
        if cooldown:
            time.sleep(cooldown)
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    event_keys = ["year", "meeting_name"]
    result["event_distance_reference_metres"] = result.groupby(
        event_keys, observed=True
    )["distance_metres"].transform("median")
    result["distance_coverage_ratio"] = (
        result["distance_metres"]
        / result["event_distance_reference_metres"].replace(0.0, np.nan)
    )
    return result


def _traffic_state(frame: pd.DataFrame, settings: Mapping[str, Any]) -> pd.Series:
    speed_ms = pd.to_numeric(frame["median_speed_kph"], errors="coerce").clip(lower=1.0) / 3.6
    gap_m = pd.to_numeric(frame["median_gap_to_ahead_m"], errors="coerce")
    gap_s = gap_m / speed_ms
    frame["gap_seconds_proxy"] = gap_s.where(np.isfinite(gap_s) & gap_s.ge(0.0))
    if "driver_ahead_number" in frame:
        ahead = frame["driver_ahead_number"].astype("string").str.strip()
        known_ahead = ahead.notna() & ~ahead.isin({"", "nan", "None", "<NA>"})
    else:
        # 没有前车指针时，缺失距离不能按 clean air 处理。
        known_ahead = pd.Series(True, index=frame.index)
    unknown_gap = known_ahead & frame["gap_seconds_proxy"].isna()
    no_ahead = ~known_ahead
    return pd.Series(
        np.select(
            [
                unknown_gap,
                frame["gap_seconds_proxy"].le(
                    float(settings["dirty_air_gap_seconds"])
                ),
                frame["gap_seconds_proxy"].ge(
                    float(settings["clean_air_gap_seconds"])
                )
                | no_ahead,
            ],
            ["unknown_gap", "dirty_air", "clean_air"],
            default="intermediate_gap",
        ),
        index=frame.index,
        dtype="string",
    )


def _mark_pit_boundaries(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["year", "meeting_name", "driver_id"]
    output = frame.sort_values([*keys, "lap_number"], kind="stable").copy()
    grouped = output.groupby(keys, observed=True, dropna=False)["stint_number"]
    previous_stint = grouped.shift(1)
    next_stint = grouped.shift(-1)
    output["pit_in_boundary_proxy"] = (
        output["stint_number"].notna()
        & next_stint.notna()
        & output["stint_number"].ne(next_stint)
    )
    output["pit_out_boundary_proxy"] = (
        output["stint_number"].notna()
        & previous_stint.notna()
        & output["stint_number"].ne(previous_stint)
    )
    output["pit_boundary_proxy"] = (
        output["pit_in_boundary_proxy"] | output["pit_out_boundary_proxy"]
    )
    return output


def _mark_slow_tail(frame: pd.DataFrame, settings: Mapping[str, Any]) -> pd.Series:
    candidate = pd.Series(False, index=frame.index)
    keys = [
        "year",
        "meeting_name",
        "driver_id",
        "stint_number",
        "compound",
    ]
    base = frame.loc[frame["preliminary_modelable"]].copy()
    for _, group in base.groupby(keys, observed=True, dropna=False, sort=False):
        if len(group) < 5:
            continue
        x = pd.to_numeric(group["tyre_age"], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(group["lap_duration"], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(x) & np.isfinite(y)
        if valid.sum() < 5 or np.unique(x[valid]).size < 2:
            continue
        slope, intercept = np.polyfit(x[valid], y[valid], 1)
        residual = y - (intercept + slope * x)
        center, scale = _median_mad(residual[valid])
        if center is None or scale is None or scale <= 0.0:
            continue
        triggered = residual > center + float(settings["slow_lap_mad"]) * scale
        candidate.loc[group.index] = triggered
    return candidate


def _apply_fuel_mass_proxy(
    frame: pd.DataFrame, settings: Mapping[str, Any]
) -> pd.DataFrame:
    """应用公开线性燃油质量敏感性；输出始终明确不识别真实油量。"""

    output = frame.copy()
    lap_time = pd.to_numeric(output["lap_duration"], errors="coerce")
    race_fraction = pd.to_numeric(output["lap_fraction"], errors="coerce").clip(
        lower=0.0, upper=1.0
    )
    enabled = bool(settings.get("enabled", False))
    if enabled:
        initial = settings["initial_fuel_kg"]
        sensitivity = settings["lap_time_s_per_kg"]
        remaining_fraction = 1.0 - race_fraction
        low = (
            remaining_fraction
            * float(initial["low"])
            * float(sensitivity["low"])
        )
        base = (
            remaining_fraction
            * float(initial["base"])
            * float(sensitivity["base"])
        )
        high = (
            remaining_fraction
            * float(initial["high"])
            * float(sensitivity["high"])
        )
        output["fuel_remaining_mass_proxy_kg"] = (
            remaining_fraction * float(initial["base"])
        )
        output["fuel_proxy_model"] = str(settings["model"])
    else:
        low = pd.Series(0.0, index=output.index)
        base = pd.Series(0.0, index=output.index)
        high = pd.Series(0.0, index=output.index)
        output["fuel_remaining_mass_proxy_kg"] = np.nan
        output["fuel_proxy_model"] = "disabled_identity"
    output["fuel_correction_low_s"] = low
    output["fuel_correction_base_s"] = base
    output["fuel_correction_high_s"] = high
    output["fuel_corrected_low_lap_duration_s"] = lap_time - low
    output["fuel_corrected_lap_duration_s"] = lap_time - base
    output["fuel_corrected_high_lap_duration_s"] = lap_time - high
    output["fuel_proxy_available"] = lap_time.notna() & race_fraction.notna()
    output["fuel_proxy_applied"] = enabled
    output["fuel_load_identified"] = False
    return output


def _cross_validate_track_evolution(
    candidate: pd.DataFrame,
    *,
    settings: Mapping[str, Any],
    fraction_width: float,
) -> dict[str, Any]:
    """按车手两折留出验证公共赛道阶段曲线是否可迁移。"""

    drivers = sorted(candidate["driver_id"].dropna().astype(str).unique())
    if len(drivers) < 4:
        return {
            "accepted": False,
            "holdout_laps": 0,
            "mae_before_s": None,
            "mae_after_s": None,
        }
    driver_fold = {
        driver: index % 2 for index, driver in enumerate(drivers)
    }
    before_errors: list[float] = []
    after_errors: list[float] = []
    cell_keys = ["compound", "_track_age_bin"]
    minimum_laps = max(2, int(settings["minimum_laps_per_bin"]) // 2)
    minimum_teams = max(
        1, int(settings["minimum_teams_per_bin"]) // 2
    )
    minimum_drivers = max(
        2, int(settings["minimum_drivers_per_bin"]) // 2
    )
    for fold in (0, 1):
        train_drivers = {
            driver for driver, value in driver_fold.items() if value != fold
        }
        test_drivers = {
            driver for driver, value in driver_fold.items() if value == fold
        }
        train = candidate.loc[
            candidate["driver_id"].astype(str).isin(train_drivers)
        ].copy()
        test = candidate.loc[
            candidate["driver_id"].astype(str).isin(test_drivers)
        ].copy()
        if train.empty or test.empty:
            continue
        train["_cv_driver_center"] = train.groupby(
            "driver_id", observed=True
        )["_track_y"].transform("median")
        train["_cv_centered"] = (
            train["_track_y"] - train["_cv_driver_center"]
        )
        cell_centers = (
            train.groupby(
                cell_keys, observed=True, dropna=False
            )["_cv_centered"]
            .median()
            .rename("_cv_cell_center")
            .reset_index()
        )
        train = train.merge(
            cell_centers,
            on=cell_keys,
            how="left",
            validate="many_to_one",
        )
        train["_cv_residual"] = (
            train["_cv_centered"] - train["_cv_cell_center"]
        )
        stage = (
            train.groupby(
                "track_lap_fraction_bin",
                observed=True,
                dropna=False,
            )
            .agg(
                effect=("_cv_residual", "median"),
                laps=("_cv_residual", "size"),
                teams=("team_name", "nunique"),
                drivers=("driver_id", "nunique"),
            )
            .reset_index()
        )
        direct = stage.loc[
            stage["laps"].ge(minimum_laps)
            & stage["teams"].ge(minimum_teams)
            & stage["drivers"].ge(minimum_drivers)
            & pd.to_numeric(stage["effect"], errors="coerce").notna()
        ].sort_values("track_lap_fraction_bin", kind="stable")
        if len(direct) < 2:
            continue
        x = (
            pd.to_numeric(
                direct["track_lap_fraction_bin"], errors="coerce"
            ).to_numpy(dtype=float)
            + fraction_width / 2.0
        )
        effect = pd.to_numeric(
            direct["effect"], errors="coerce"
        ).to_numpy(dtype=float)
        effect = effect - float(np.median(effect))
        effect = np.clip(
            effect,
            -float(settings["maximum_abs_correction_s"]),
            float(settings["maximum_abs_correction_s"]),
        )

        test["_cv_driver_center"] = test.groupby(
            "driver_id", observed=True
        )["_track_y"].transform("median")
        test["_cv_centered"] = (
            test["_track_y"] - test["_cv_driver_center"]
        )
        test = test.merge(
            cell_centers,
            on=cell_keys,
            how="left",
            validate="many_to_one",
        )
        test = test.loc[test["_cv_cell_center"].notna()].copy()
        if test.empty:
            continue
        test["_cv_residual"] = (
            test["_cv_centered"] - test["_cv_cell_center"]
        )
        target = (
            pd.to_numeric(
                test["track_lap_fraction_bin"], errors="coerce"
            ).to_numpy(dtype=float)
            + fraction_width / 2.0
        )
        finite = np.isfinite(target)
        if not finite.any():
            continue
        residual = pd.to_numeric(
            test["_cv_residual"], errors="coerce"
        ).to_numpy(dtype=float)
        finite &= np.isfinite(residual)
        if not finite.any():
            continue
        predicted = np.interp(target[finite], x, effect)
        anchor = _numeric_median(train["_cv_residual"])
        if anchor is None:
            anchor = 0.0
        before_errors.extend(
            np.abs(residual[finite] - anchor).tolist()
        )
        after_errors.extend(
            np.abs(residual[finite] - anchor - predicted).tolist()
        )
    holdout_laps = len(before_errors)
    before_mae = (
        float(np.median(before_errors)) if before_errors else None
    )
    after_mae = float(np.median(after_errors)) if after_errors else None
    minimum_holdout = int(settings.get("minimum_holdout_laps", 40))
    minimum_improvement = float(
        settings.get("minimum_holdout_mae_improvement_s", 0.0)
    )
    tolerance = float(
        settings.get("maximum_holdout_mae_worsening_s", 0.0)
    )
    accepted = bool(
        holdout_laps >= minimum_holdout
        and before_mae is not None
        and after_mae is not None
        and after_mae <= before_mae + tolerance
        and before_mae - after_mae >= minimum_improvement
    )
    return {
        "accepted": accepted,
        "holdout_laps": holdout_laps,
        "mae_before_s": before_mae,
        "mae_after_s": after_mae,
    }


def _apply_track_evolution_proxy(
    frame: pd.DataFrame,
    settings: Mapping[str, Any],
    baseline_settings: Mapping[str, Any],
) -> pd.DataFrame:
    """以车手固定中心和配方/胎龄格消除后残差估计公共赛道阶段项。"""

    output = frame.copy()
    fraction_width = float(baseline_settings["lap_fraction_bin"])
    age_width = float(baseline_settings["tyre_age_bin_laps"])
    output["track_lap_fraction_bin"] = (
        np.floor(
            pd.to_numeric(output["lap_fraction"], errors="coerce")
            / fraction_width
        )
        * fraction_width
    ).clip(upper=max(0.0, 1.0 - fraction_width))
    output["track_evolution_proxy_s"] = 0.0
    output["track_evolution_available"] = False
    output["track_evolution_direct_support"] = False
    output["track_evolution_support_laps"] = 0
    output["track_evolution_support_teams"] = 0
    output["track_evolution_support_drivers"] = 0
    output["track_validation_status"] = (
        "not_required"
        if not bool(settings.get("require_holdout_non_worsening", False))
        else "not_tested"
    )
    output["track_validation_holdout_laps"] = 0
    output["track_validation_mae_before_s"] = np.nan
    output["track_validation_mae_after_s"] = np.nan
    output["track_evolution_proxy_model"] = (
        str(settings["model"]) if bool(settings.get("enabled", False))
        else "disabled_identity"
    )
    if not bool(settings.get("enabled", False)):
        output["track_adjusted_lap_duration_s"] = output[
            "fuel_corrected_lap_duration_s"
        ]
        return output

    event_keys = ["year", "meeting_name"]
    for _, event in output.groupby(event_keys, observed=True, sort=False):
        candidate = event.loc[
            event["modelable_lap"] & event["fuel_proxy_available"]
        ].copy()
        if candidate.empty:
            continue
        candidate["_track_y"] = pd.to_numeric(
            candidate["fuel_corrected_lap_duration_s"], errors="coerce"
        )
        candidate = candidate.loc[candidate["_track_y"].notna()]
        if candidate.empty:
            continue
        candidate["_driver_center"] = candidate.groupby(
            "driver_id", observed=True
        )["_track_y"].transform("median")
        candidate["_driver_centered"] = (
            candidate["_track_y"] - candidate["_driver_center"]
        )
        candidate["_track_age_bin"] = (
            np.floor(
                pd.to_numeric(candidate["tyre_age"], errors="coerce")
                / age_width
            )
            * age_width
        )
        if bool(settings.get("require_holdout_non_worsening", False)):
            validation = _cross_validate_track_evolution(
                candidate,
                settings=settings,
                fraction_width=fraction_width,
            )
            output.loc[
                event.index, "track_validation_status"
            ] = (
                "accepted_non_worsening"
                if validation["accepted"]
                else "rejected_or_insufficient_holdout"
            )
            output.loc[
                event.index, "track_validation_holdout_laps"
            ] = int(validation["holdout_laps"])
            output.loc[
                event.index, "track_validation_mae_before_s"
            ] = validation["mae_before_s"]
            output.loc[
                event.index, "track_validation_mae_after_s"
            ] = validation["mae_after_s"]
            if not validation["accepted"]:
                continue
        candidate["_compound_age_center"] = candidate.groupby(
            ["compound", "_track_age_bin"],
            observed=True,
            dropna=False,
        )["_driver_centered"].transform("median")
        candidate["_track_residual"] = (
            candidate["_driver_centered"]
            - candidate["_compound_age_center"]
        )
        stage = (
            candidate.groupby(
                "track_lap_fraction_bin",
                observed=True,
                dropna=False,
            )
            .agg(
                effect=("_track_residual", "median"),
                laps=("_track_residual", "size"),
                teams=("team_name", "nunique"),
                drivers=("driver_id", "nunique"),
            )
            .reset_index()
        )
        stage["direct"] = (
            stage["laps"].ge(int(settings["minimum_laps_per_bin"]))
            & stage["teams"].ge(int(settings["minimum_teams_per_bin"]))
            & stage["drivers"].ge(int(settings["minimum_drivers_per_bin"]))
            & pd.to_numeric(stage["effect"], errors="coerce").notna()
        )
        direct = stage.loc[stage["direct"]].sort_values(
            "track_lap_fraction_bin", kind="stable"
        )
        if len(direct) < 2:
            continue
        x = (
            pd.to_numeric(
                direct["track_lap_fraction_bin"], errors="coerce"
            ).to_numpy(dtype=float)
            + fraction_width / 2.0
        )
        effect = pd.to_numeric(direct["effect"], errors="coerce").to_numpy(
            dtype=float
        )
        effect = effect - float(np.median(effect))
        maximum = float(settings["maximum_abs_correction_s"])
        effect = np.clip(effect, -maximum, maximum)
        target = (
            pd.to_numeric(
                event["track_lap_fraction_bin"], errors="coerce"
            ).to_numpy(dtype=float)
            + fraction_width / 2.0
        )
        finite_target = np.isfinite(target)
        interpolated = np.zeros(len(event), dtype=float)
        interpolated[finite_target] = np.interp(
            target[finite_target], x, effect
        )
        output.loc[event.index, "track_evolution_proxy_s"] = interpolated
        output.loc[event.index[finite_target], "track_evolution_available"] = True

        direct_effect = dict(
            zip(
                direct["track_lap_fraction_bin"].astype(float),
                effect,
                strict=True,
            )
        )
        support_laps = dict(
            zip(
                direct["track_lap_fraction_bin"].astype(float),
                direct["laps"].astype(int),
                strict=True,
            )
        )
        support_teams = dict(
            zip(
                direct["track_lap_fraction_bin"].astype(float),
                direct["teams"].astype(int),
                strict=True,
            )
        )
        support_drivers = dict(
            zip(
                direct["track_lap_fraction_bin"].astype(float),
                direct["drivers"].astype(int),
                strict=True,
            )
        )
        bins = pd.to_numeric(
            event["track_lap_fraction_bin"], errors="coerce"
        )
        direct_mask = bins.isin(direct_effect)
        direct_index = event.index[direct_mask]
        output.loc[
            direct_index, "track_evolution_direct_support"
        ] = True
        output.loc[direct_index, "track_evolution_support_laps"] = (
            bins.loc[direct_mask].map(support_laps).astype(int).to_numpy()
        )
        output.loc[direct_index, "track_evolution_support_teams"] = (
            bins.loc[direct_mask].map(support_teams).astype(int).to_numpy()
        )
        output.loc[direct_index, "track_evolution_support_drivers"] = (
            bins.loc[direct_mask].map(support_drivers).astype(int).to_numpy()
        )
    output["track_adjusted_lap_duration_s"] = (
        pd.to_numeric(
            output["fuel_corrected_lap_duration_s"], errors="coerce"
        )
        - pd.to_numeric(output["track_evolution_proxy_s"], errors="coerce")
    )
    return output


def _cross_validate_traffic_state(
    candidate: pd.DataFrame,
    *,
    state: str,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """两折交叉拟合 gap-band 惩罚，并只以留出圈判断是否非劣。"""

    work = candidate.copy()
    work["_traffic_cv_fold"] = (
        work.groupby("traffic_state", observed=True).cumcount() % 2
    )
    before_errors: list[float] = []
    after_errors: list[float] = []
    estimated_penalties: list[float] = []
    minimum_clean = max(4, int(settings["minimum_clean_laps"]) // 2)
    minimum_state = max(4, int(settings["minimum_laps_per_state"]) // 2)
    minimum_drivers = max(
        2, int(settings["minimum_drivers_per_state"]) // 2
    )
    for fold in (0, 1):
        train = work.loc[work["_traffic_cv_fold"].ne(fold)]
        test = work.loc[
            work["_traffic_cv_fold"].eq(fold)
            & work["traffic_state"].eq(state)
        ]
        clean_train = train.loc[train["traffic_state"].eq("clean_air")]
        state_train = train.loc[train["traffic_state"].eq(state)]
        if (
            len(clean_train) < minimum_clean
            or len(state_train) < minimum_state
            or state_train["driver_id"].nunique() < minimum_drivers
            or test.empty
        ):
            continue
        clean_anchor = _numeric_median(clean_train["_traffic_residual"])
        state_center = _numeric_median(state_train["_traffic_residual"])
        if clean_anchor is None or state_center is None:
            continue
        raw_penalty = max(0.0, float(state_center - clean_anchor))
        raw_penalty = min(
            raw_penalty, float(settings["maximum_penalty_s"])
        )
        shrinkage_laps = max(0, int(settings["shrinkage_laps"]))
        shrinkage = len(state_train) / (
            len(state_train) + shrinkage_laps
        )
        penalty = float(raw_penalty * shrinkage)
        values = pd.to_numeric(
            test["_traffic_residual"], errors="coerce"
        ).dropna().to_numpy(dtype=float)
        before_errors.extend(np.abs(values - clean_anchor).tolist())
        after_errors.extend(
            np.abs(values - clean_anchor - penalty).tolist()
        )
        estimated_penalties.append(penalty)
    holdout_laps = len(before_errors)
    before_mae = (
        float(np.median(before_errors)) if before_errors else None
    )
    after_mae = float(np.median(after_errors)) if after_errors else None
    minimum_holdout = int(
        settings.get(
            "minimum_holdout_laps",
            max(4, int(settings["minimum_laps_per_state"])),
        )
    )
    tolerance = float(settings.get("maximum_holdout_mae_worsening_s", 0.0))
    minimum_improvement = float(
        settings.get("minimum_holdout_mae_improvement_s", 0.0)
    )
    accepted = bool(
        holdout_laps >= minimum_holdout
        and before_mae is not None
        and after_mae is not None
        and after_mae <= before_mae + tolerance
        and before_mae - after_mae >= minimum_improvement
    )
    return {
        "state": state,
        "accepted": accepted,
        "holdout_laps": holdout_laps,
        "mae_before_s": before_mae,
        "mae_after_s": after_mae,
        "median_crossfit_penalty_s": (
            float(np.median(estimated_penalties))
            if estimated_penalties
            else None
        ),
    }


def _apply_traffic_penalty_proxy(
    frame: pd.DataFrame,
    settings: Mapping[str, Any],
    baseline_settings: Mapping[str, Any],
    lap_filter_settings: Mapping[str, Any],
) -> pd.DataFrame:
    """估计同场 gap-band 条件秒差；这是相关性代理，不是空气动力因果量。"""

    output = frame.copy()
    output["traffic_penalty_proxy_s"] = np.nan
    output["traffic_penalty_raw_s"] = np.nan
    output["traffic_penalty_available"] = False
    output["traffic_penalty_support_laps"] = 0
    output["traffic_penalty_support_drivers"] = 0
    output["traffic_validation_status"] = "not_tested"
    output["traffic_validation_holdout_laps"] = 0
    output["traffic_validation_mae_before_s"] = np.nan
    output["traffic_validation_mae_after_s"] = np.nan
    output["traffic_model_residual_s"] = np.nan
    output["traffic_penalty_proxy_model"] = (
        str(settings["model"]) if bool(settings.get("enabled", False))
        else "disabled_clean_air_only"
    )
    base_candidate = (
        output["quality_ok"]
        & output["all_green"].fillna(False).astype(bool)
        & ~output["pit_boundary_proxy"]
        & output["traffic_state"].ne("unknown_gap")
        & pd.to_numeric(
            output["track_adjusted_lap_duration_s"], errors="coerce"
        ).notna()
    )
    clean = base_candidate & output["traffic_state"].eq("clean_air")
    output.loc[clean, "traffic_penalty_proxy_s"] = 0.0
    output.loc[clean, "traffic_penalty_raw_s"] = 0.0
    output.loc[clean, "traffic_penalty_available"] = True
    if bool(settings.get("enabled", False)):
        age_width = float(baseline_settings["tyre_age_bin_laps"])
        event_keys = ["year", "meeting_name"]
        for _, event in output.groupby(event_keys, observed=True, sort=False):
            candidate = event.loc[base_candidate.loc[event.index]].copy()
            if candidate.empty:
                continue
            candidate["_traffic_y"] = pd.to_numeric(
                candidate["track_adjusted_lap_duration_s"], errors="coerce"
            )
            candidate["_driver_center"] = candidate.groupby(
                "driver_id", observed=True
            )["_traffic_y"].transform("median")
            candidate["_driver_centered"] = (
                candidate["_traffic_y"] - candidate["_driver_center"]
            )
            candidate["_traffic_age_bin"] = (
                np.floor(
                    pd.to_numeric(candidate["tyre_age"], errors="coerce")
                    / age_width
                )
                * age_width
            )
            candidate["_cell_center"] = candidate.groupby(
                ["compound", "_traffic_age_bin", "track_lap_fraction_bin"],
                observed=True,
                dropna=False,
            )["_driver_centered"].transform("median")
            candidate["_traffic_residual"] = (
                candidate["_driver_centered"] - candidate["_cell_center"]
            )
            output.loc[
                candidate.index, "traffic_model_residual_s"
            ] = candidate["_traffic_residual"]
            clean_rows = candidate.loc[
                candidate["traffic_state"].eq("clean_air")
            ]
            if len(clean_rows) < int(settings["minimum_clean_laps"]):
                continue
            clean_anchor = _numeric_median(clean_rows["_traffic_residual"])
            if clean_anchor is None:
                continue
            for state in ("intermediate_gap", "dirty_air"):
                validation = _cross_validate_traffic_state(
                    candidate, state=state, settings=settings
                )
                state_event_index = event.index[
                    event["traffic_state"].eq(state)
                ]
                output.loc[
                    state_event_index, "traffic_validation_status"
                ] = (
                    "accepted_non_worsening"
                    if validation["accepted"]
                    else "rejected_or_insufficient_holdout"
                )
                output.loc[
                    state_event_index,
                    "traffic_validation_holdout_laps",
                ] = int(validation["holdout_laps"])
                output.loc[
                    state_event_index,
                    "traffic_validation_mae_before_s",
                ] = validation["mae_before_s"]
                output.loc[
                    state_event_index,
                    "traffic_validation_mae_after_s",
                ] = validation["mae_after_s"]
                state_rows = candidate.loc[
                    candidate["traffic_state"].eq(state)
                ]
                support_laps = int(len(state_rows))
                support_drivers = int(state_rows["driver_id"].nunique())
                if (
                    support_laps < int(settings["minimum_laps_per_state"])
                    or support_drivers
                    < int(settings["minimum_drivers_per_state"])
                    or (
                        bool(
                            settings.get(
                                "require_holdout_non_worsening", True
                            )
                        )
                        and not validation["accepted"]
                    )
                ):
                    continue
                state_center = _numeric_median(
                    state_rows["_traffic_residual"]
                )
                if state_center is None:
                    continue
                raw_penalty = max(0.0, float(state_center - clean_anchor))
                raw_penalty = min(
                    raw_penalty, float(settings["maximum_penalty_s"])
                )
                shrinkage_laps = max(0, int(settings["shrinkage_laps"]))
                shrinkage = support_laps / (support_laps + shrinkage_laps)
                penalty = float(raw_penalty * shrinkage)
                index = state_rows.index
                output.loc[index, "traffic_penalty_raw_s"] = raw_penalty
                output.loc[index, "traffic_penalty_proxy_s"] = penalty
                output.loc[index, "traffic_penalty_available"] = True
                output.loc[
                    index, "traffic_penalty_support_laps"
                ] = support_laps
                output.loc[
                    index, "traffic_penalty_support_drivers"
                ] = support_drivers
    output["pace_adjusted_lap_duration_s"] = np.where(
        output["traffic_penalty_available"],
        pd.to_numeric(
            output["track_adjusted_lap_duration_s"], errors="coerce"
        )
        - pd.to_numeric(
            output["traffic_penalty_proxy_s"], errors="coerce"
        ),
        np.nan,
    )
    preliminary = (
        base_candidate
        & output["traffic_penalty_available"]
        & pd.to_numeric(
            output["pace_adjusted_lap_duration_s"], errors="coerce"
        ).notna()
        & ~output["anomalous_slow_proxy"]
    )
    slow_input = output.copy()
    slow_input["preliminary_modelable"] = preliminary
    slow_input["lap_duration"] = output["pace_adjusted_lap_duration_s"]
    output["pace_anomalous_slow_proxy"] = _mark_slow_tail(
        slow_input, lap_filter_settings
    )
    output["pace_eligible_lap"] = (
        preliminary & ~output["pace_anomalous_slow_proxy"]
    )
    output["analysis_disposition"] = np.select(
        [
            output["pace_eligible_lap"],
            output["traffic_state"].eq("unknown_gap"),
            ~output["traffic_penalty_available"] & base_candidate,
        ],
        [
            "pace_proxy_eligible",
            "traffic_gap_unknown",
            "traffic_model_insufficient",
        ],
        default=output["primary_disposition"].astype("string"),
    )
    return output


def _attach_scientific_simulation_proxies(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    """按燃油、赛道演化、交通的固定顺序构造可消融条件代理。"""

    proxies = config["simulation_proxies"]
    output = _apply_fuel_mass_proxy(frame, proxies["fuel"])
    output = _apply_track_evolution_proxy(
        output, proxies["track_evolution"], config["baseline"]
    )
    return _apply_traffic_penalty_proxy(
        output,
        proxies["traffic"],
        config["baseline"],
        config["lap_filter"],
    )


def _attach_reasonable_baseline(
    frame: pd.DataFrame, settings: Mapping[str, Any]
) -> pd.DataFrame:
    output = frame.copy()
    age_width = float(settings["tyre_age_bin_laps"])
    fraction_width = float(settings["lap_fraction_bin"])
    output["tyre_age_bin"] = (
        np.floor(pd.to_numeric(output["tyre_age"], errors="coerce") / age_width)
        * age_width
    )
    output["lap_fraction_bin"] = (
        np.floor(pd.to_numeric(output["lap_fraction"], errors="coerce") / fraction_width)
        * fraction_width
    ).clip(upper=max(0.0, 1.0 - fraction_width))
    cell_keys = [
        "year",
        "meeting_name",
        "compound",
        "tyre_age_bin",
        "lap_fraction_bin",
    ]
    eligibility_column = (
        "pace_eligible_lap"
        if "pace_eligible_lap" in output
        else "modelable_lap"
    )
    value_column = (
        "pace_adjusted_lap_duration_s"
        if "pace_adjusted_lap_duration_s" in output
        else "lap_duration"
    )
    output["baseline_lap_time_basis"] = value_column
    eligible = output.loc[output[eligibility_column]].copy()
    if eligible.empty:
        output["reasonable_baseline_s"] = np.nan
        output["baseline_support_laps"] = 0
        output["baseline_effective_laps"] = 0.0
        output["baseline_kish_effective_laps"] = 0.0
        output["baseline_support_teams"] = 0
        output["baseline_available"] = False
        output["delta_to_reasonable_baseline_s"] = np.nan
        return output
    weighted_mode = "analysis_weight" in eligible
    if weighted_mode:
        eligible["_baseline_weight"] = pd.to_numeric(
            eligible["analysis_weight"], errors="coerce"
        ).fillna(0.0)
        cell_rows: list[dict[str, Any]] = []
        for cell_key, group in eligible.groupby(
            cell_keys, observed=True, dropna=False, sort=True
        ):
            values = pd.to_numeric(group[value_column], errors="coerce")
            weights = pd.to_numeric(
                group["_baseline_weight"], errors="coerce"
            ).fillna(0.0)
            finite = values.notna() & weights.gt(0.0)
            row = {
                key: value for key, value in zip(cell_keys, cell_key)
            }
            row.update(
                {
                    "reasonable_baseline_s": weighted_median(
                        values.loc[finite], weights.loc[finite]
                    ),
                    "baseline_support_laps": int(finite.sum()),
                    "baseline_effective_laps": float(
                        weights.loc[finite].sum()
                    ),
                    "baseline_kish_effective_laps": (
                        kish_effective_sample_size(weights.loc[finite])
                    ),
                    "baseline_support_teams": int(
                        group.loc[finite, "team_name"].nunique()
                    ),
                }
            )
            cell_rows.append(row)
        cells = pd.DataFrame(cell_rows)
    else:
        cells = (
            eligible.groupby(cell_keys, observed=True, dropna=False)
            .agg(
                reasonable_baseline_s=(value_column, "median"),
                baseline_support_laps=(value_column, "size"),
                baseline_support_teams=("team_name", "nunique"),
            )
            .reset_index()
        )
        cells["baseline_effective_laps"] = cells[
            "baseline_support_laps"
        ].astype(float)
        cells["baseline_kish_effective_laps"] = cells[
            "baseline_support_laps"
        ].astype(float)
    cells["baseline_available"] = (
        cells["baseline_support_laps"].ge(int(settings["minimum_laps_per_cell"]))
        & cells["baseline_support_teams"].ge(int(settings["minimum_teams_per_cell"]))
    )
    if weighted_mode:
        cells["baseline_available"] &= cells[
            "baseline_effective_laps"
        ].ge(float(settings["minimum_effective_laps_per_cell"]))
    cells.loc[~cells["baseline_available"], "reasonable_baseline_s"] = np.nan
    output = output.merge(cells, on=cell_keys, how="left", validate="many_to_one")
    output["baseline_support_laps"] = (
        output["baseline_support_laps"].fillna(0).astype(int)
    )
    output["baseline_support_teams"] = (
        output["baseline_support_teams"].fillna(0).astype(int)
    )
    output["baseline_effective_laps"] = pd.to_numeric(
        output["baseline_effective_laps"], errors="coerce"
    ).fillna(0.0)
    output["baseline_kish_effective_laps"] = pd.to_numeric(
        output["baseline_kish_effective_laps"], errors="coerce"
    ).fillna(0.0)
    output["baseline_available"] = (
        output["baseline_available"].astype("boolean").fillna(False).astype(bool)
    )
    output["delta_to_reasonable_baseline_s"] = (
        pd.to_numeric(output[value_column], errors="coerce")
        - pd.to_numeric(output["reasonable_baseline_s"], errors="coerce")
    )
    return output


def annotate_race_laps(
    laps: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    """为所有观测圈分配互斥处置、重叠原因及条件基线。"""

    validate_race_dossier_config(config)
    _required(laps, REQUIRED_LAP_COLUMNS, "RaceLapUniverse")
    output = laps.copy()
    numeric = [
        "year",
        "lap_number",
        "lap_duration",
        "stint_number",
        "tyre_age",
        "position",
        "distance_metres",
        "median_speed_kph",
        "median_gap_to_ahead_m",
        "telemetry_samples",
    ]
    for column in numeric:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    event_keys = ["year", "meeting_name"]
    if "event_distance_reference_metres" not in output:
        output["event_distance_reference_metres"] = output.groupby(
            event_keys, observed=True
        )["distance_metres"].transform("median")
    if "distance_coverage_ratio" not in output:
        output["distance_coverage_ratio"] = (
            output["distance_metres"]
            / pd.to_numeric(
                output["event_distance_reference_metres"], errors="coerce"
            ).replace(0.0, np.nan)
        )
    settings = config["lap_filter"]
    output["traffic_state"] = _traffic_state(output, settings)
    output = _mark_pit_boundaries(output)
    event_max = output.groupby(event_keys, observed=True)["lap_number"].transform("max")
    output["lap_fraction"] = output["lap_number"] / event_max.replace(0.0, np.nan)
    dry = output["compound"].astype("string").str.upper().isin(
        [str(value).upper() for value in settings["dry_compounds"]]
    )
    quality = (
        output["is_accurate"].fillna(False).astype(bool)
        & ~output["deleted"].fillna(False).astype(bool)
        & output["lap_duration"].gt(0.0)
        & output["distance_coverage_ratio"].ge(
            float(settings["minimum_distance_coverage"])
        )
        & output["telemetry_samples"].ge(int(settings["minimum_samples_per_lap"]))
        & dry
    )
    output["quality_ok"] = quality
    output["preliminary_modelable"] = (
        quality
        & output["all_green"].fillna(False).astype(bool)
        & ~output["pit_boundary_proxy"]
        & output["traffic_state"].eq("clean_air")
    )
    output["anomalous_slow_proxy"] = _mark_slow_tail(output, settings)
    output["modelable_lap"] = (
        output["preliminary_modelable"] & ~output["anomalous_slow_proxy"]
    )

    disposition = np.full(len(output), "quality_missing", dtype=object)
    disposition[quality.to_numpy()] = "unknown_unaccounted"
    disposition[
        (
            quality
            & ~output["all_green"].fillna(False).astype(bool)
        ).to_numpy()
    ] = "event_excluded"
    disposition[
        (
            quality
            & output["all_green"].fillna(False).astype(bool)
            & output["pit_boundary_proxy"]
        ).to_numpy()
    ] = "pit_excluded"
    disposition[
        (
            quality
            & output["all_green"].fillna(False).astype(bool)
            & ~output["pit_boundary_proxy"]
            & output["traffic_state"].isin(["dirty_air", "intermediate_gap"])
        ).to_numpy()
    ] = "traffic_separate"
    disposition[output["modelable_lap"].to_numpy()] = "model_eligible"
    output["primary_disposition"] = pd.Categorical(
        disposition, categories=PRIMARY_DISPOSITIONS
    )

    reason_codes: list[list[str]] = []
    for row in output.itertuples(index=False):
        reasons: list[str] = []
        if not bool(row.is_accurate):
            reasons.append("inaccurate")
        if bool(row.deleted):
            reasons.append("deleted")
        if _finite(row.lap_duration) is None or float(row.lap_duration) <= 0.0:
            reasons.append("missing_or_invalid_lap_time")
        if _finite(row.distance_coverage_ratio) is None or float(
            row.distance_coverage_ratio
        ) < float(settings["minimum_distance_coverage"]):
            reasons.append("incomplete_distance")
        if _finite(row.telemetry_samples) is None or float(
            row.telemetry_samples
        ) < int(settings["minimum_samples_per_lap"]):
            reasons.append("insufficient_samples")
        if str(row.compound).upper() not in {
            str(value).upper() for value in settings["dry_compounds"]
        }:
            reasons.append("non_dry_compound")
        if not bool(row.all_green):
            reasons.append("non_green")
        if bool(row.pit_in_boundary_proxy):
            reasons.append("pit_in_boundary_proxy")
        if bool(row.pit_out_boundary_proxy):
            reasons.append("pit_out_boundary_proxy")
        if str(row.traffic_state) == "dirty_air":
            reasons.append("traffic_dirty_air")
        elif str(row.traffic_state) == "intermediate_gap":
            reasons.append("traffic_intermediate")
        elif str(row.traffic_state) == "unknown_gap":
            reasons.append("traffic_gap_unknown")
        if bool(row.anomalous_slow_proxy):
            reasons.append("unexplained_slow_lap_proxy")
        reason_codes.append(reasons or ["model_eligible"])
    output["reason_codes"] = reason_codes
    if "simulation_proxies" in config:
        output = _attach_scientific_simulation_proxies(output, config)
    if "inclusive_robust_model" in config:
        # v11-v14 的严格干净圈结论保留为独立复核证据，不能被 v15
        # 全量主模型覆盖或改写。
        output["strict_pace_eligible_lap"] = output[
            "pace_eligible_lap"
        ].copy()
        output["strict_pace_adjusted_lap_duration_s"] = output[
            "pace_adjusted_lap_duration_s"
        ].copy()
        output = apply_inclusive_robust_model(
            output,
            config["inclusive_robust_model"],
            config["lap_filter"],
        )
        output["pace_eligible_lap"] = output[
            "inclusive_pace_eligible_lap"
        ].copy()
        output["pace_adjusted_lap_duration_s"] = output[
            "inclusive_adjusted_lap_duration_s"
        ].copy()
        output["analysis_weight"] = output[
            "inclusive_model_weight"
        ].copy()
    output = _attach_reasonable_baseline(output, config["baseline"])
    return output.sort_values(
        ["year", "meeting_name", "driver_id", "lap_number"], kind="stable"
    ).reset_index(drop=True)


def _lap_ranges(values: Sequence[int]) -> list[list[int]]:
    ordered = sorted({int(value) for value in values})
    if not ordered:
        return []
    ranges: list[list[int]] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append([start, previous])
        start = previous = value
    ranges.append([start, previous])
    return ranges


def _ledger_entries(frame: pd.DataFrame) -> list[dict[str, Any]]:
    inclusive_mode = "inclusive_model_candidate" in frame
    masks = {
        "non_green": ~frame["all_green"].fillna(False).astype(bool),
        "pit_in_boundary_proxy": frame["pit_in_boundary_proxy"],
        "pit_out_boundary_proxy": frame["pit_out_boundary_proxy"],
        "traffic_dirty_air": frame["traffic_state"].eq("dirty_air"),
        "traffic_gap_unknown": frame["traffic_state"].eq("unknown_gap"),
        "inaccurate_or_deleted": (
            ~frame["is_accurate"].fillna(False).astype(bool)
            | frame["deleted"].fillna(False).astype(bool)
        ),
        "incomplete_distance": frame["distance_coverage_ratio"].lt(0.95),
        "unexplained_slow_lap_proxy": frame["anomalous_slow_proxy"],
    }
    actions = {
        "non_green": "exclude",
        "pit_in_boundary_proxy": "exclude",
        "pit_out_boundary_proxy": "exclude",
        "traffic_dirty_air": "separate",
        "traffic_gap_unknown": "censor",
        "inaccurate_or_deleted": "censor",
        "incomplete_distance": "censor",
        "unexplained_slow_lap_proxy": "separate",
    }
    inclusive_actions = {
        "non_green": "condition_and_soft_weight",
        "pit_in_boundary_proxy": "condition_and_soft_weight",
        "pit_out_boundary_proxy": "condition_and_soft_weight",
        "traffic_dirty_air": "condition_and_soft_weight",
        "traffic_gap_unknown": "condition_and_soft_weight",
        "inaccurate_or_deleted": "condition_and_soft_weight",
        "incomplete_distance": (
            "soft_weight_or_hard_exclude_below_physical_floor"
        ),
        "unexplained_slow_lap_proxy": (
            "out_of_fold_residual_soft_weight_only"
        ),
    }
    rows: list[dict[str, Any]] = []
    for event_type, mask in masks.items():
        affected = frame.loc[mask]
        for driver, group in affected.groupby("driver_id", observed=True, sort=True):
            rows.append(
                {
                    "event_type": event_type,
                    "driver": str(driver),
                    "lap_ranges": _lap_ranges(group["lap_number"].tolist()),
                    "affected_laps": int(len(group)),
                    "analysis_action": (
                        inclusive_actions[event_type]
                        if inclusive_mode
                        else actions[event_type]
                    ),
                    "strict_confirmation_action": actions[event_type],
                    "source": "public_telemetry_lap_proxy",
                    "directness": "observed_or_derived_proxy",
                    "confidence": (
                        "medium"
                        if event_type
                        in {
                            "non_green",
                            "pit_in_boundary_proxy",
                            "pit_out_boundary_proxy",
                        }
                        else "low"
                    ),
                }
            )
    return rows


def build_event_ledger(frame: pd.DataFrame) -> dict[str, Any]:
    """生成重叠事件证据与互斥圈处置闭合账本。"""

    disposition_counts = {
        key: int(frame["primary_disposition"].eq(key).sum())
        for key in PRIMARY_DISPOSITIONS
    }
    lap_category_counts = {
        "non_green": int((~frame["all_green"].fillna(False).astype(bool)).sum()),
        "pit_in_boundary_proxy": int(frame["pit_in_boundary_proxy"].sum()),
        "pit_out_boundary_proxy": int(frame["pit_out_boundary_proxy"].sum()),
        "traffic_dirty_air": int(frame["traffic_state"].eq("dirty_air").sum()),
        "traffic_gap_unknown": int(frame["traffic_state"].eq("unknown_gap").sum()),
        "inaccurate_or_deleted": int(
            (
                ~frame["is_accurate"].fillna(False).astype(bool)
                | frame["deleted"].fillna(False).astype(bool)
            ).sum()
        ),
        "incomplete_distance": int(frame["distance_coverage_ratio"].lt(0.95).sum()),
        "unexplained_slow_lap_proxy": int(frame["anomalous_slow_proxy"].sum()),
    }
    observed = int(len(frame))
    accounted = int(sum(disposition_counts.values()))
    return {
        "observed_laps": observed,
        "primary_disposition_role": (
            "strict_confirmation_ledger_with_v15_soft_weight_actions"
            if "inclusive_model_candidate" in frame
            else "primary_analysis_ledger"
        ),
        "primary_disposition_counts": disposition_counts,
        "primary_disposition_closure": {
            "accounted_laps": accounted,
            "unaccounted_laps": observed - accounted,
            "passed": accounted == observed,
        },
        "lap_category_counts": lap_category_counts,
        "entries": _ledger_entries(frame),
        "not_identified_categories": list(UNIDENTIFIED_EVENT_CATEGORIES),
        "track_status_limit": (
            "track_status 组合编码只支持 non-green 排除，未核对 race-control "
            "来源时不得细分为官方 SC、VSC 或黄旗。"
        ),
    }


def _stint_identity(group: pd.DataFrame) -> dict[str, Any]:
    first = group.iloc[0]
    return {
        "year": int(first["year"]),
        "meeting_name": str(first["meeting_name"]),
        "driver": str(first["driver_id"]),
        "team": str(first["team_name"]),
        "stint_number": int(first["stint_number"]),
        "compound": str(first["compound"]),
    }


def build_stint_dossiers(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """分析完整 stint；累计秒差只对有条件基线的模型圈求和。"""

    settings = config["stint_gate"]
    materiality = config["materiality"]
    keys = [
        "year",
        "meeting_name",
        "driver_id",
        "team_name",
        "stint_number",
        "compound",
    ]
    valid_frame = frame.loc[frame["stint_number"].notna()].copy()
    rows: list[dict[str, Any]] = []
    for _, group in valid_frame.groupby(
        keys, observed=True, dropna=False, sort=True
    ):
        group = group.sort_values("lap_number", kind="stable")
        identity = _stint_identity(group)
        observed_laps = int(group["lap_number"].nunique())
        expected_span = int(group["lap_number"].max() - group["lap_number"].min() + 1)
        continuity = observed_laps / expected_span if expected_span else 0.0
        # Stint 自身的物理形态只要求该圈通过事件、质量和交通门槛；
        # 相对车辆基线是否可得是第二道独立的可识别门，不能反向让 Stint 失效。
        independent_stint_gate = bool(
            settings.get("stint_validity_independent_of_baseline", False)
        )
        eligibility_column = (
            "pace_eligible_lap"
            if "pace_eligible_lap" in group
            else "modelable_lap"
        )
        pace_column = (
            "pace_adjusted_lap_duration_s"
            if "pace_adjusted_lap_duration_s" in group
            else "lap_duration"
        )
        method_version_number = int(
            str(config.get("method_version", "v8")).removeprefix("v")
        )
        weighted_mode = (
            "analysis_weight" in group and method_version_number >= 15
        )
        usable_mask = group[eligibility_column]
        if not independent_stint_gate:
            usable_mask = usable_mask & group["baseline_available"]
        usable = group.loc[usable_mask].copy()
        if weighted_mode:
            usable["_analysis_weight"] = pd.to_numeric(
                usable["analysis_weight"], errors="coerce"
            ).fillna(0.0)
            usable = usable.loc[usable["_analysis_weight"].gt(0.0)]
        else:
            usable["_analysis_weight"] = 1.0
        baseline_usable = usable.loc[usable["baseline_available"]].copy()
        usable_laps = int(len(usable))
        usable_fraction = usable_laps / observed_laps if observed_laps else 0.0
        effective_laps = float(usable["_analysis_weight"].sum())
        effective_fraction = (
            effective_laps / observed_laps if observed_laps else 0.0
        )
        baseline_laps = int(len(baseline_usable))
        baseline_effective_laps = float(
            baseline_usable["_analysis_weight"].sum()
        )
        baseline_fraction = (
            baseline_laps / usable_laps if usable_laps else 0.0
        )
        tyre_scope = usable if weighted_mode and len(usable) else group
        tyre_values = pd.to_numeric(
            tyre_scope["tyre_age"], errors="coerce"
        ).dropna()
        tyre_span = (
            float(tyre_values.max() - tyre_values.min()) if len(tyre_values) else 0.0
        )
        reasons: list[str] = []
        if observed_laps < int(settings["minimum_observed_laps"]):
            reasons.append("observed_laps_below_gate")
        if weighted_mode:
            if usable_fraction < float(settings["minimum_candidate_fraction"]):
                reasons.append("candidate_fraction_below_gate")
            if effective_laps < float(settings["minimum_effective_laps"]):
                reasons.append("effective_laps_below_gate")
            if effective_fraction < float(
                settings["minimum_effective_fraction"]
            ):
                reasons.append("effective_fraction_below_gate")
        else:
            if usable_laps < int(settings["minimum_modelable_laps"]):
                reasons.append("modelable_laps_below_gate")
            if usable_fraction < float(settings["minimum_modelable_fraction"]):
                reasons.append("modelable_fraction_below_gate")
        if tyre_span < float(settings["minimum_tyre_age_span"]):
            reasons.append("tyre_age_span_below_gate")
        if continuity < 0.90:
            reasons.append("internal_lap_continuity_below_0_90")
        status = "valid" if not reasons else "audit_only"

        strict_mask = (
            group["strict_pace_eligible_lap"].fillna(False).astype(bool)
            if "strict_pace_eligible_lap" in group
            else group["modelable_lap"].fillna(False).astype(bool)
        )
        strict_usable = group.loc[strict_mask].copy()
        strict_laps = int(len(strict_usable))
        strict_fraction = (
            strict_laps / observed_laps if observed_laps else 0.0
        )
        strict_reasons: list[str] = []
        if observed_laps < int(settings["minimum_observed_laps"]):
            strict_reasons.append("observed_laps_below_gate")
        if strict_laps < int(
            settings.get(
                "strict_confirmation_minimum_modelable_laps",
                settings["minimum_modelable_laps"],
            )
        ):
            strict_reasons.append("strict_modelable_laps_below_gate")
        if strict_fraction < float(
            settings.get(
                "strict_confirmation_minimum_modelable_fraction",
                settings["minimum_modelable_fraction"],
            )
        ):
            strict_reasons.append("strict_modelable_fraction_below_gate")
        strict_tyre = pd.to_numeric(
            strict_usable["tyre_age"], errors="coerce"
        ).dropna()
        strict_tyre_span = (
            float(strict_tyre.max() - strict_tyre.min())
            if len(strict_tyre)
            else 0.0
        )
        if strict_tyre_span < float(settings["minimum_tyre_age_span"]):
            strict_reasons.append("strict_tyre_age_span_below_gate")
        if continuity < 0.90:
            strict_reasons.append("internal_lap_continuity_below_0_90")
        strict_status = (
            "confirmed" if not strict_reasons else "not_confirmed"
        )

        warmup_laps = int(settings["warmup_laps"])
        late_count = max(
            1, int(np.ceil(max(usable_laps, 1) * float(settings["late_stint_fraction"])))
        )
        initial = usable.head(warmup_laps)
        late = usable.tail(late_count)
        stable = usable.iloc[warmup_laps : max(warmup_laps, usable_laps - late_count)]
        if stable.empty:
            stable = usable
        if weighted_mode:
            initial_pace = weighted_median(
                pd.to_numeric(initial[pace_column], errors="coerce"),
                initial["_analysis_weight"],
            )
            stable_pace = weighted_median(
                pd.to_numeric(stable[pace_column], errors="coerce"),
                stable["_analysis_weight"],
            )
            late_pace = weighted_median(
                pd.to_numeric(late[pace_column], errors="coerce"),
                late["_analysis_weight"],
            )
            _, variability = weighted_mad(
                pd.to_numeric(usable[pace_column], errors="coerce"),
                usable["_analysis_weight"],
            )
            slope = weighted_theil_sen_slope(
                usable["tyre_age"],
                usable[pace_column],
                usable["_analysis_weight"],
            )
            raw_slope = weighted_theil_sen_slope(
                usable["tyre_age"],
                usable["lap_duration"],
                usable["_analysis_weight"],
            )
        else:
            initial_pace = _numeric_median(initial[pace_column])
            stable_pace = _numeric_median(stable[pace_column])
            late_pace = _numeric_median(late[pace_column])
            _, variability = _median_mad(
                pd.to_numeric(usable[pace_column], errors="coerce").to_numpy()
            )
            slope = _theil_sen_slope(
                usable["tyre_age"], usable[pace_column]
            )
            raw_slope = _theil_sen_slope(
                usable["tyre_age"], usable["lap_duration"]
            )
        reference_pace = _estimate_stint_reference_pace(
            usable["tyre_age"],
            usable[pace_column],
            usable["_analysis_weight"],
        )
        slope = reference_pace["slope_s_per_tyre_lap"]
        fuel_sensitivity_interval = None
        reference_pace_fuel_sensitivity_interval = None
        reference_pace_fuel_sensitivity_basis = None
        if {
            "fuel_corrected_low_lap_duration_s",
            "fuel_corrected_high_lap_duration_s",
        }.issubset(usable.columns):
            if "inclusive_nuisance_adjustment_s" in usable:
                common_adjustments = pd.to_numeric(
                    usable["inclusive_nuisance_adjustment_s"],
                    errors="coerce",
                ).fillna(0.0)
                reference_pace_fuel_sensitivity_basis = (
                    "low_high_fuel_scenarios_with_inclusive_non_fuel_"
                    "nuisance_held_fixed"
                )
            else:
                common_adjustments = (
                    pd.to_numeric(
                        usable.get("track_evolution_proxy_s", 0.0),
                        errors="coerce",
                    ).fillna(0.0)
                    + pd.to_numeric(
                        usable.get("traffic_penalty_proxy_s", 0.0),
                        errors="coerce",
                    ).fillna(0.0)
                )
                reference_pace_fuel_sensitivity_basis = (
                    "low_high_fuel_scenarios_with_track_and_traffic_"
                    "adjustments_held_fixed"
                )
            low_values = (
                pd.to_numeric(
                    usable["fuel_corrected_low_lap_duration_s"],
                    errors="coerce",
                )
                - common_adjustments
            )
            high_values = (
                pd.to_numeric(
                    usable["fuel_corrected_high_lap_duration_s"],
                    errors="coerce",
                )
                - common_adjustments
            )
            if weighted_mode:
                low_slope = weighted_theil_sen_slope(
                    usable["tyre_age"],
                    low_values,
                    usable["_analysis_weight"],
                )
                high_slope = weighted_theil_sen_slope(
                    usable["tyre_age"],
                    high_values,
                    usable["_analysis_weight"],
                )
            else:
                low_slope = _theil_sen_slope(
                    usable["tyre_age"], low_values
                )
                high_slope = _theil_sen_slope(
                    usable["tyre_age"], high_values
                )
            if low_slope is not None and high_slope is not None:
                fuel_sensitivity_interval = [
                    float(min(low_slope, high_slope)),
                    float(max(low_slope, high_slope)),
                ]
            reference_age = reference_pace["reference_tyre_age_laps"]
            if reference_age is not None:
                low_reference = _estimate_stint_reference_pace(
                    usable["tyre_age"],
                    low_values,
                    usable["_analysis_weight"],
                    reference_tyre_age_laps=float(reference_age),
                )
                high_reference = _estimate_stint_reference_pace(
                    usable["tyre_age"],
                    high_values,
                    usable["_analysis_weight"],
                    reference_tyre_age_laps=float(reference_age),
                )
                low_pace = low_reference["pace_s"]
                high_pace = high_reference["pace_s"]
                if low_pace is not None and high_pace is not None:
                    reference_pace_fuel_sensitivity_interval = [
                        float(min(low_pace, high_pace)),
                        float(max(low_pace, high_pace)),
                    ]
        curve_audit = None
        curve_settings = config.get("stint_curve_models")
        curve_scope = (
            str(curve_settings.get("execution_scope"))
            if curve_settings
            else ""
        )
        curve_fuel_sensitivity_interval = fuel_sensitivity_interval
        if (
            curve_scope == "strict_confirmed_stints_only"
            and {
                "fuel_corrected_low_lap_duration_s",
                "fuel_corrected_high_lap_duration_s",
                "track_evolution_proxy_s",
                "traffic_penalty_proxy_s",
            }.issubset(strict_usable.columns)
        ):
            strict_common_adjustments = (
                pd.to_numeric(
                    strict_usable["track_evolution_proxy_s"],
                    errors="coerce",
                ).fillna(0.0)
                + pd.to_numeric(
                    strict_usable["traffic_penalty_proxy_s"],
                    errors="coerce",
                ).fillna(0.0)
            )
            strict_low_slope = _theil_sen_slope(
                strict_usable["tyre_age"],
                pd.to_numeric(
                    strict_usable[
                        "fuel_corrected_low_lap_duration_s"
                    ],
                    errors="coerce",
                )
                - strict_common_adjustments,
            )
            strict_high_slope = _theil_sen_slope(
                strict_usable["tyre_age"],
                pd.to_numeric(
                    strict_usable[
                        "fuel_corrected_high_lap_duration_s"
                    ],
                    errors="coerce",
                )
                - strict_common_adjustments,
            )
            curve_fuel_sensitivity_interval = (
                [
                    float(min(strict_low_slope, strict_high_slope)),
                    float(max(strict_low_slope, strict_high_slope)),
                ]
                if strict_low_slope is not None
                and strict_high_slope is not None
                else None
            )
        curve_allowed = bool(
            curve_settings
            and (
                (
                    curve_scope == "strict_confirmed_stints_only"
                    and strict_status == "confirmed"
                )
                or (
                    curve_scope != "strict_confirmed_stints_only"
                    and (
                        status == "valid"
                        or curve_scope != "valid_stints_only"
                    )
                )
            )
        )
        if curve_allowed and curve_settings:
            if curve_scope == "strict_confirmed_stints_only":
                curve_input = strict_usable.iloc[warmup_laps:].copy()
                curve_pace_column = (
                    "strict_pace_adjusted_lap_duration_s"
                    if "strict_pace_adjusted_lap_duration_s"
                    in curve_input
                    else pace_column
                )
            else:
                curve_input = usable.iloc[warmup_laps:].copy()
                curve_pace_column = pace_column
            curve_audit = _evaluate_stint_curve_models(
                curve_input,
                pace_column=curve_pace_column,
                settings=curve_settings,
                fuel_sensitivity_interval=curve_fuel_sensitivity_interval,
            )
        elif curve_settings:
            not_run_status = (
                "not_run_strict_confirmation_failed"
                if curve_scope == "strict_confirmed_stints_only"
                else "not_run_stint_gate_failed"
            )
            curve_audit = {
                "status": not_run_status,
                "selected_model": None,
                "rolling_origin_points": 0,
                "validation_protocol": str(
                    curve_settings.get(
                        "validation_protocol",
                        "rolling_origin_selection_only_v1",
                    )
                ),
                "candidate_mae_s": {},
                "rolling_origin_mae_s": {
                    "constant_median": None,
                    "selected": None,
                    "improvement": None,
                },
                "selection_audit": None,
                "confirmation_audit": None,
                "fuel_sensitivity_direction_robust_positive": False,
                "strategy_curve_eligible": False,
                "kalman_model_boundary": (
                    (
                        "not_run_because_strict_confirmation_failed"
                        if curve_scope == "strict_confirmed_stints_only"
                        else "not_run_because_stint_physical_gate_failed"
                    )
                ),
            }
        late_drop = (
            float(late_pace - stable_pace)
            if late_pace is not None and stable_pace is not None
            else None
        )
        baseline_reasons: list[str] = []
        if baseline_laps < int(
            settings.get("minimum_baseline_laps_for_cumulative", 1)
        ):
            baseline_reasons.append("baseline_laps_below_cumulative_gate")
        if baseline_fraction < float(
            settings.get("minimum_baseline_fraction_for_cumulative", 0.0)
        ):
            baseline_reasons.append("baseline_fraction_below_cumulative_gate")
        if weighted_mode and baseline_effective_laps < float(
            settings["minimum_effective_baseline_laps_for_cumulative"]
        ):
            baseline_reasons.append(
                "baseline_effective_laps_below_cumulative_gate"
            )
        if baseline_laps and not baseline_reasons:
            baseline_delta = pd.to_numeric(
                baseline_usable["delta_to_reasonable_baseline_s"],
                errors="coerce",
            )
            if weighted_mode:
                baseline_weight = baseline_usable["_analysis_weight"]
                finite_baseline = (
                    baseline_delta.notna() & baseline_weight.gt(0.0)
                )
                cumulative = (
                    float(
                        np.average(
                            baseline_delta.loc[finite_baseline],
                            weights=baseline_weight.loc[finite_baseline],
                        )
                        * int(finite_baseline.sum())
                    )
                    if finite_baseline.any()
                    else None
                )
                cumulative_method = (
                    "oof_reliability_weighted_mean_per_lap_times_observed_laps"
                )
            else:
                cumulative = float(baseline_delta.sum())
                cumulative_method = "raw_sum_of_conditionally_comparable_laps"
        else:
            cumulative = None
            cumulative_method = None
        adjacent_gap = _numeric_median(group["gap_seconds_proxy"])
        time_material = (
            cumulative is not None
            and abs(cumulative) >= float(materiality["pit_window_seconds"])
        )
        position_proxy = bool(
            time_material
            and adjacent_gap is not None
            and abs(float(cumulative)) >= adjacent_gap
        )
        rows.append(
            {
                **identity,
                "lap_start": int(group["lap_number"].min()),
                "lap_end": int(group["lap_number"].max()),
                "observed_laps": observed_laps,
                "modelable_laps": usable_laps,
                "clean_modelable_laps": int(group["modelable_lap"].sum()),
                "pace_adjusted_laps": usable_laps,
                "modelable_fraction": float(usable_fraction),
                "effective_weight_laps": effective_laps,
                "effective_weight_fraction": effective_fraction,
                "kish_effective_laps": kish_effective_sample_size(
                    usable["_analysis_weight"]
                ),
                "internal_lap_continuity": float(continuity),
                "tyre_age_span": tyre_span,
                "status": status,
                "gate_failures": reasons,
                "strict_confirmation": {
                    "status": strict_status,
                    "modelable_laps": strict_laps,
                    "modelable_fraction": strict_fraction,
                    "tyre_age_span": strict_tyre_span,
                    "gate_failures": strict_reasons,
                    "role": (
                        "independent_confirmation_not_primary_training_set"
                    ),
                },
                "initial_pace_s": initial_pace,
                "stable_pace_s": stable_pace,
                "variability_mad_s": variability,
                "representative_tyre_age_pace_s": reference_pace["pace_s"],
                "representative_tyre_age_laps": reference_pace[
                    "reference_tyre_age_laps"
                ],
                "representative_tyre_age_support_laps": [
                    reference_pace["support_min_tyre_age_laps"],
                    reference_pace["support_max_tyre_age_laps"],
                ],
                "representative_tyre_age_pace_status": reference_pace[
                    "status"
                ],
                "representative_tyre_age_pace_estimator": (
                    "oof_reliability_weighted_theil_sen_projection_"
                    "at_within_stint_weighted_median_tyre_age"
                    if weighted_mode
                    else (
                        "theil_sen_projection_at_within_stint_"
                        "median_tyre_age"
                    )
                ),
                "representative_tyre_age_pace_basis": (
                    "fuel_corrected_and_observed_condition_adjusted_"
                    "pace_with_tyre_age_retained"
                ),
                "representative_tyre_age_pace_fuel_sensitivity_interval_s": (
                    reference_pace_fuel_sensitivity_interval
                ),
                "representative_tyre_age_pace_fuel_sensitivity_basis": (
                    reference_pace_fuel_sensitivity_basis
                ),
                "representative_tyre_age_direct_comparison_rule": (
                    "same_compound_and_near_reference_tyre_age_and_"
                    "near_track_conditions_only"
                ),
                "degradation_s_per_tyre_lap": slope,
                "raw_net_slope_s_per_tyre_lap": raw_slope,
                "fuel_sensitivity_degradation_interval_s_per_tyre_lap": (
                    fuel_sensitivity_interval
                ),
                "tyre_curve_model_audit": curve_audit,
                "late_drop_s": late_drop,
                "cumulative_delta_to_reasonable_baseline_s": cumulative,
                "cumulative_delta_estimator": cumulative_method,
                "baseline_laps": baseline_laps,
                "baseline_effective_weight_laps": baseline_effective_laps,
                "baseline_coverage": float(baseline_fraction),
                "cumulative_baseline_status": (
                    "identified_proxy"
                    if not baseline_reasons
                    else "not_identifiable"
                ),
                "cumulative_baseline_gate_failures": baseline_reasons,
                "adjacent_gap_proxy_s": adjacent_gap,
                "materiality": {
                    "physical_time_material": bool(time_material),
                    "could_affect_track_position_proxy": position_proxy,
                    "final_position_change_identified": False,
                    "reason": (
                        "缺少 FIA 最终相邻车辆计时差和完整反事实赛道位置。"
                    ),
                },
                "major_factor_claimed": False,
                "event_exposure_counts": {
                    key: int(value)
                    for key, value in group["primary_disposition"]
                    .astype("string")
                    .value_counts()
                    .items()
                },
                "pace_basis": pace_column,
                "degradation_estimator": (
                    (
                        "oof_reliability_weighted_theil_sen_pairwise_median"
                        if weighted_mode
                        else (
                            "theil_sen_pairwise_median"
                            if pace_column != "lap_duration"
                            else "theil_sen_pairwise_median_raw"
                        )
                    )
                ),
                "interpretation": (
                    "衰减与累计秒差是同配方、相近胎龄和比赛阶段的条件代理；"
                    "v15 主层使用全量候选圈的样本外软权重，严格干净圈只作"
                    "独立复核；仍不是纯轮胎物理退化或真实胎态。"
                    if weighted_mode
                    else (
                        "衰减与累计秒差是同配方、相近胎龄和比赛阶段的条件代理；"
                        "v11 可扣除燃油、公共赛道演化和可估交通项，但仍不是纯轮胎"
                        "物理退化或真实胎态。"
                    )
                ),
            }
        )
    return rows


def build_pit_cycle_loss_proxy(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> dict[str, Any]:
    """用进站前后局部可用圈估计完整 pit-in/pit-out 周期损失。"""

    if "pit_loss_proxy" not in config:
        return {
            "status": "not_enabled",
            "valid_cycles": 0,
            "cycle_loss_s": None,
            "stationary_pit_time_identified": False,
        }
    settings = config["pit_loss_proxy"]
    minimum_side = int(
        settings["minimum_adjacent_pace_laps_each_side"]
    )
    maximum = float(settings["maximum_cycle_loss_s"])
    pace_column = (
        "pace_adjusted_lap_duration_s"
        if "pace_adjusted_lap_duration_s" in frame
        else "lap_duration"
    )
    actual_column = (
        "track_adjusted_lap_duration_s"
        if "track_adjusted_lap_duration_s" in frame
        else "lap_duration"
    )
    eligibility_column = (
        "pace_eligible_lap"
        if "pace_eligible_lap" in frame
        else "modelable_lap"
    )
    cycles: list[dict[str, Any]] = []
    rejected = Counter()
    event_keys = ["year", "meeting_name", "driver_id"]
    for (_, _, driver), group in frame.groupby(
        event_keys, observed=True, sort=True
    ):
        group = group.sort_values("lap_number", kind="stable")
        pit_in_rows = group.loc[group["pit_in_boundary_proxy"]]
        for _, pit_in in pit_in_rows.iterrows():
            later = group.loc[
                group["lap_number"].gt(pit_in["lap_number"])
                & group["pit_out_boundary_proxy"]
            ].head(1)
            if later.empty:
                rejected["missing_pit_out_boundary"] += 1
                continue
            pit_out = later.iloc[0]
            if int(pit_out["lap_number"]) - int(pit_in["lap_number"]) > 2:
                rejected["pit_boundaries_not_adjacent"] += 1
                continue
            if not bool(pit_in["all_green"]) or not bool(
                pit_out["all_green"]
            ):
                rejected["non_green_boundary"] += 1
                continue
            pre = group.loc[
                group["lap_number"].lt(pit_in["lap_number"])
                & group[eligibility_column]
            ].tail(minimum_side)
            post = group.loc[
                group["lap_number"].gt(pit_out["lap_number"])
                & group[eligibility_column]
            ].head(minimum_side)
            if len(pre) < minimum_side or len(post) < minimum_side:
                rejected["insufficient_local_pace_support"] += 1
                continue
            expected_in = _numeric_median(pre[pace_column])
            expected_out = _numeric_median(post[pace_column])
            actual_in = _finite(pit_in[actual_column])
            actual_out = _finite(pit_out[actual_column])
            if None in (expected_in, expected_out, actual_in, actual_out):
                rejected["missing_lap_time_component"] += 1
                continue
            in_loss = float(actual_in - expected_in)
            out_loss = float(actual_out - expected_out)
            total_loss = float(in_loss + out_loss)
            if not 0.0 < total_loss <= maximum:
                rejected["cycle_loss_outside_gate"] += 1
                continue
            cycles.append(
                {
                    "driver": str(driver),
                    "pit_in_lap": int(pit_in["lap_number"]),
                    "pit_out_lap": int(pit_out["lap_number"]),
                    "pit_in_local_excess_s": in_loss,
                    "pit_out_local_excess_s": out_loss,
                    "cycle_loss_proxy_s": total_loss,
                    "pre_support_laps": int(len(pre)),
                    "post_support_laps": int(len(post)),
                }
            )
    values = [row["cycle_loss_proxy_s"] for row in cycles]
    enough = len(cycles) >= int(settings["minimum_valid_cycles_per_event"])
    return {
        "status": (
            "usable_historical_proxy" if enough else "insufficient_cycles"
        ),
        "model": str(settings["model"]),
        "valid_cycles": len(cycles),
        "cycle_loss_s": (
            {
                "median": _quantile(values, 0.50),
                "interval_80": [
                    _quantile(values, 0.10),
                    _quantile(values, 0.90),
                ],
            }
            if enough
            else None
        ),
        "cycles": cycles,
        "rejected_cycle_counts": dict(rejected),
        "stationary_pit_time_identified": False,
        "interpretation": (
            "完整 pit-in/pit-out 圈相对两侧局部条件配速的周期损失代理；"
            "混合了限速区、停车、进出站路径和出站暖胎，不是静止换胎时间。"
        ),
    }


def _strategy_windows(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> dict[str, Any]:
    minimum = int(
        config["strategy_envelope"]["minimum_drivers_per_stop_pattern"]
    )
    patterns: dict[int, list[list[int]]] = {}
    keys = ["year", "meeting_name", "driver_id"]
    for _, driver in frame.groupby(keys, observed=True, sort=True):
        stints = (
            driver.loc[driver["stint_number"].notna()]
            .groupby("stint_number", observed=True)
            .agg(lap_end=("lap_number", "max"))
            .sort_index()
        )
        stops = max(0, len(stints) - 1)
        if stops in {1, 2}:
            patterns.setdefault(stops, []).append(
                [int(value) for value in stints["lap_end"].iloc[:-1]]
            )

    def interval_for(stop_count: int, stop_index: int) -> list[int] | None:
        rows = patterns.get(stop_count, [])
        if len(rows) < minimum:
            return None
        values = [row[stop_index] for row in rows if len(row) > stop_index]
        if len(values) < minimum:
            return None
        return [
            int(np.floor(np.quantile(values, 0.25))),
            int(np.ceil(np.quantile(values, 0.75))),
        ]

    return {
        "one_stop_window_laps": interval_for(1, 0),
        "two_stop_window_laps": [
            interval_for(2, 0),
            interval_for(2, 1),
        ],
        "observed_driver_patterns": {
            "one_stop": len(patterns.get(1, [])),
            "two_stop": len(patterns.get(2, [])),
        },
    }


def build_tyre_strategy_envelope(
    frame: pd.DataFrame,
    stint_dossiers: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """由本场观测 stint 生成赛后策略包络代理，不伪装成赛前内部模型。"""

    minimum = int(
        config["strategy_envelope"]["minimum_valid_stints_per_compound"]
    )
    valid = [row for row in stint_dossiers if row["status"] == "valid"]
    curve_mode = "stint_curve_models" in config
    minimum_curves = int(
        config.get("stint_curve_models", {}).get(
            "minimum_strategy_curves_per_compound", minimum
        )
    )
    compounds: list[dict[str, Any]] = []
    for compound in sorted({str(row["compound"]) for row in valid}):
        rows = [row for row in valid if str(row["compound"]) == compound]
        curve_rows = (
            [
                row
                for row in rows
                if row.get("tyre_curve_model_audit")
                and row["tyre_curve_model_audit"].get(
                    "strategy_curve_eligible", False
                )
            ]
            if curve_mode
            else rows
        )
        lengths = [int(row["observed_laps"]) for row in rows]
        slopes = [
            float(row["degradation_s_per_tyre_lap"])
            for row in curve_rows
            if row.get("degradation_s_per_tyre_lap") is not None
        ]
        enough = len(rows) >= minimum
        enough_curves = len(curve_rows) >= minimum_curves
        compounds.append(
            {
                "compound": compound,
                "valid_stints": len(rows),
                "predictively_validated_curves": len(curve_rows),
                "status": (
                    (
                        "usable_length_and_curve_proxy"
                        if enough_curves
                        else "usable_length_only"
                    )
                    if enough
                    else "insufficient_stints"
                )
                if curve_mode
                else ("usable_proxy" if enough else "insufficient_stints"),
                "reasonable_stint_length_laps": (
                    [
                        int(np.floor(np.quantile(lengths, 0.25))),
                        int(np.ceil(np.quantile(lengths, 0.75))),
                    ]
                    if enough
                    else None
                ),
                "observed_length_range_laps": (
                    [int(min(lengths)), int(max(lengths))] if lengths else None
                ),
                "degradation_proxy_s_per_lap": (
                    {
                        "median": _quantile(slopes, 0.50),
                        "interval_80": [
                            _quantile(slopes, 0.10),
                            _quantile(slopes, 0.90),
                        ],
                    }
                    if enough and enough_curves and slopes
                    else None
                ),
            }
        )
    windows = _strategy_windows(frame, config)
    return {
        "label": "observed_data_strategy_proxy",
        "timing_role": "post_race_audit_not_pre_race_forecast",
        "real_fuel_load_identified": False,
        "pirelli_internal_simulation": False,
        "compounds": compounds,
        **windows,
        "limitations": [
            "合理 stint 长度来自本场公开观测分布，不是轮胎物理寿命。",
            "一停/两停窗口来自实际换胎圈分布，不含真实燃油、胎温胎压或车队内部模拟。",
            "v12 衰减曲线只有滚动原点优于常数基线且燃油敏感性方向稳定时才进入策略代理。",
            "SC/VSC、异常慢停和损伤未被可靠识别时不得用于主要策略归因。",
        ],
    }


def build_pre_race_tyre_envelope(
    historical_stints: Sequence[Mapping[str, Any]],
    *,
    target_event_order: int,
    target_race_laps: int,
    target_event_id: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """只用目标分站之前的有效 stint 构建赛前可见策略代理。

    该函数按 ``event_order < target_event_order`` 硬过滤未来数据。调用方即使
    误传未来分站，也不会改变目标包络。
    """

    prohibited = {
        "actual_points",
        "championship_points",
        "finish_position",
        "classification_proxy",
        "winner",
    }
    for row in historical_stints:
        leakage = sorted(prohibited & set(row))
        if leakage:
            raise ValueError(f"赛前轮胎包络禁止赛果字段：{leakage}")
    visible = [
        dict(row)
        for row in historical_stints
        if int(row.get("event_order", 10**9)) < int(target_event_order)
        and row.get("status") == "valid"
    ]
    settings = config["strategy_envelope"]
    minimum_stints = int(
        settings.get("minimum_historical_stints_per_compound", 12)
    )
    minimum_events = int(
        settings.get("minimum_historical_events_per_compound", 4)
    )
    curve_mode = "stint_curve_models" in config
    minimum_curves = int(
        config.get("stint_curve_models", {}).get(
            "minimum_historical_curve_models_per_compound",
            minimum_stints,
        )
    )
    compounds: list[dict[str, Any]] = []
    bounds: dict[str, tuple[int, int]] = {}
    for compound in sorted({str(row["compound"]) for row in visible}):
        rows = [row for row in visible if str(row["compound"]) == compound]
        event_count = len(
            {
                (row.get("year"), row.get("meeting_name"))
                for row in rows
            }
        )
        lengths = np.asarray(
            [float(row["observed_laps"]) for row in rows], dtype=float
        )
        curve_rows = (
            [
                row
                for row in rows
                if row.get("tyre_curve_model_audit")
                and row["tyre_curve_model_audit"].get(
                    "strategy_curve_eligible", False
                )
            ]
            if curve_mode
            else rows
        )
        slopes = np.asarray(
            [
                float(row["degradation_s_per_tyre_lap"])
                for row in curve_rows
                if row.get("degradation_s_per_tyre_lap") is not None
            ],
            dtype=float,
        )
        enough = len(rows) >= minimum_stints and event_count >= minimum_events
        enough_curves = len(curve_rows) >= minimum_curves
        interval = None
        if enough:
            interval = [
                max(1, int(np.floor(np.quantile(lengths, 0.10)))),
                max(1, int(np.ceil(np.quantile(lengths, 0.90)))),
            ]
            bounds[compound] = (interval[0], interval[1])
        compounds.append(
            {
                "compound": compound,
                "historical_stints": len(rows),
                "historical_events": event_count,
                "predictively_validated_curves": len(curve_rows),
                "status": (
                    (
                        "usable_pre_race_length_and_curve_proxy"
                        if enough_curves
                        else "usable_pre_race_length_only"
                    )
                    if enough
                    else "insufficient_history"
                )
                if curve_mode
                else (
                    "usable_pre_race_proxy"
                    if enough
                    else "insufficient_history"
                ),
                "reasonable_stint_length_laps": interval,
                "degradation_proxy_s_per_lap": (
                    {
                        "median": float(np.median(slopes)) if len(slopes) else None,
                        "interval_80": (
                            [
                                float(np.quantile(slopes, 0.10)),
                                float(np.quantile(slopes, 0.90)),
                            ]
                            if len(slopes)
                            else None
                        ),
                    }
                    if enough and enough_curves
                    else None
                ),
            }
        )

    one_stop: list[dict[str, Any]] = []
    compound_names = sorted(bounds)
    for first in compound_names:
        for second in compound_names:
            if first == second:
                continue
            first_low, first_high = bounds[first]
            second_low, second_high = bounds[second]
            lower = max(first_low, target_race_laps - second_high)
            upper = min(first_high, target_race_laps - second_low)
            if lower <= upper:
                one_stop.append(
                    {
                        "sequence": [first, second],
                        "pit_window_laps": [int(lower), int(upper)],
                    }
                )

    two_stop: list[dict[str, Any]] = []
    for first in compound_names:
        for second in compound_names:
            for third in compound_names:
                if len({first, second, third}) < 2:
                    continue
                first_low, first_high = bounds[first]
                second_low, second_high = bounds[second]
                third_low, third_high = bounds[third]
                total_low = first_low + second_low + third_low
                total_high = first_high + second_high + third_high
                if not total_low <= target_race_laps <= total_high:
                    continue
                first_window = [
                    max(first_low, target_race_laps - second_high - third_high),
                    min(first_high, target_race_laps - second_low - third_low),
                ]
                second_window = [
                    max(
                        first_window[0] + second_low,
                        target_race_laps - third_high,
                    ),
                    min(
                        first_window[1] + second_high,
                        target_race_laps - third_low,
                    ),
                ]
                if (
                    first_window[0] <= first_window[1]
                    and second_window[0] <= second_window[1]
                ):
                    two_stop.append(
                        {
                            "sequence": [first, second, third],
                            "first_pit_window_laps": [
                                int(first_window[0]),
                                int(first_window[1]),
                            ],
                            "second_pit_window_laps": [
                                int(second_window[0]),
                                int(second_window[1]),
                            ],
                        }
                    )

    pit_by_event: dict[tuple[Any, Any, Any], float] = {}
    for row in visible:
        pit_value = _finite(row.get("event_pit_cycle_loss_proxy_s"))
        if pit_value is None:
            continue
        key = (
            row.get("event_order"),
            row.get("year"),
            row.get("meeting_name"),
        )
        pit_by_event[key] = pit_value
    pit_values = list(pit_by_event.values())
    pit_settings = config.get("pit_loss_proxy", {})
    pit_enough = bool(
        pit_settings
        and len(pit_values)
        >= int(pit_settings.get("minimum_historical_events", 1))
    )
    pit_proxy = {
        "status": (
            "usable_historical_proxy"
            if pit_enough
            else (
                "insufficient_history"
                if pit_settings
                else "not_enabled"
            )
        ),
        "historical_events": len(pit_values),
        "cycle_loss_s": (
            {
                "median": _quantile(pit_values, 0.50),
                "interval_80": [
                    _quantile(pit_values, 0.10),
                    _quantile(pit_values, 0.90),
                ],
            }
            if pit_enough
            else None
        ),
        "stationary_pit_time_identified": False,
    }
    compound_degradation = {
        str(row["compound"]): row["degradation_proxy_s_per_lap"]
        for row in compounds
        if row.get("degradation_proxy_s_per_lap") is not None
    }

    def points(interval: Sequence[int]) -> list[int]:
        lower, upper = int(interval[0]), int(interval[1])
        return sorted({lower, int((lower + upper) // 2), upper})

    def identified_components(
        sequence: Sequence[str],
        length_scenarios: Sequence[Sequence[int]],
    ) -> dict[str, Any] | None:
        if not pit_enough or any(
            compound not in compound_degradation for compound in sequence
        ):
            return None
        degradation_rows: list[tuple[float, float, float]] = []
        for lengths in length_scenarios:
            if len(lengths) != len(sequence) or any(
                int(length) < 1 for length in lengths
            ):
                continue
            lower_cost = median_cost = upper_cost = 0.0
            usable = True
            for compound, length in zip(sequence, lengths, strict=True):
                model = compound_degradation[compound]
                interval = model.get("interval_80")
                median_slope = _finite(model.get("median"))
                if (
                    median_slope is None
                    or not interval
                    or _finite(interval[0]) is None
                    or _finite(interval[1]) is None
                ):
                    usable = False
                    break
                factor = int(length) * (int(length) - 1) / 2.0
                lower_cost += float(interval[0]) * factor
                median_cost += median_slope * factor
                upper_cost += float(interval[1]) * factor
            if usable:
                degradation_rows.append(
                    (lower_cost, median_cost, upper_cost)
                )
        if not degradation_rows:
            return None
        pit = pit_proxy["cycle_loss_s"]
        stops = len(sequence) - 1
        pit_low = float(pit["interval_80"][0]) * stops
        pit_median = float(pit["median"]) * stops
        pit_high = float(pit["interval_80"][1]) * stops
        degradation_medians = [row[1] for row in degradation_rows]
        degradation_low = min(row[0] for row in degradation_rows)
        degradation_high = max(row[2] for row in degradation_rows)
        degradation_median = float(np.median(degradation_medians))
        return {
            "model": "free_track_additive_degradation_plus_pit_cycle_v1",
            "candidate_length_scenarios": len(degradation_rows),
            "tyre_degradation_proxy_s": {
                "median": degradation_median,
                "sensitivity_range": [
                    degradation_low,
                    degradation_high,
                ],
            },
            "pit_cycle_loss_proxy_s": {
                "median": pit_median,
                "interval_80": [pit_low, pit_high],
            },
            "combined_identified_components_s": {
                "median": degradation_median + pit_median,
                "sensitivity_range": [
                    degradation_low + pit_low,
                    degradation_high + pit_high,
                ],
            },
            "compound_base_pace_offset_identified": False,
            "strategy_total_time_rank_identified": False,
            "limitations": [
                "不含各配方新胎基础速度差、暖胎、交通、SC/VSC 与赛道位置反事实。",
                "因此只能比较已识别成本分量，不能据此宣称最优策略。",
            ],
        }

    for row in one_stop:
        scenarios = [
            [first_length, target_race_laps - first_length]
            for first_length in points(row["pit_window_laps"])
        ]
        row["identified_time_components_proxy"] = identified_components(
            row["sequence"], scenarios
        )
    for row in two_stop:
        scenarios = []
        for first_stop in points(row["first_pit_window_laps"]):
            for second_stop in points(row["second_pit_window_laps"]):
                if first_stop < second_stop < target_race_laps:
                    scenarios.append(
                        [
                            first_stop,
                            second_stop - first_stop,
                            target_race_laps - second_stop,
                        ]
                    )
        row["identified_time_components_proxy"] = identified_components(
            row["sequence"], scenarios
        )
    return {
        "label": "historical_pre_race_data_proxy",
        "target_event_id": str(target_event_id),
        "target_event_order": int(target_event_order),
        "history_cutoff_rule": "event_order_strictly_less_than_target",
        "visible_valid_stints": len(visible),
        "compounds": compounds,
        "one_stop_options": one_stop,
        "two_stop_options": two_stop,
        "pit_cycle_loss_proxy": pit_proxy,
        "optimal_strategy": None,
        "strategy_total_time_rank_identified": False,
        "real_fuel_load_identified": False,
        "pirelli_internal_simulation": False,
        "future_results_used": False,
        "limitations": [
            "仅使用目标分站前的公开历史 stint，不能代表实时天气、赛道演化或车队内部模型。",
            "进站窗口由历史长度包络覆盖赛程推导，未估计真实燃油、胎温胎压和磨损量。",
            "v12 历史衰减率另需通过滚动原点预测增益和燃油敏感性方向门，长度包络与衰减曲线门互不替代。",
            "v13 只累加经门控的衰减成本与历史进站周期损失；配方基础速度差不可识别，因此不发布最优策略。",
        ],
    }


def _bootstrap_team_order(
    values: pd.DataFrame,
    *,
    value_column: str,
    lower_is_better: bool,
    repetitions: int,
    seed: int,
) -> list[dict[str, Any]]:
    teams = sorted(values["team_name"].dropna().astype(str).unique())
    weighted_mode = "analysis_weight" in values
    point: dict[str, float] = {}
    for team in teams:
        team_rows = values.loc[values["team_name"].eq(team)]
        team_values = pd.to_numeric(
            team_rows[value_column], errors="coerce"
        )
        if not team_values.notna().any():
            continue
        if weighted_mode:
            team_weights = pd.to_numeric(
                team_rows["analysis_weight"], errors="coerce"
            ).fillna(0.0)
            center = weighted_median(team_values, team_weights)
            if center is None:
                continue
            point[team] = center
        else:
            point[team] = float(team_values.median())
    ordered = sorted(
        point,
        key=lambda team: (
            point[team] if lower_is_better else -point[team],
            team,
        ),
    )
    rank_draws: dict[str, list[int]] = {team: [] for team in point}
    rng = np.random.default_rng(seed)
    for _ in range(repetitions):
        draw: dict[str, float] = {}
        for team in point:
            team_rows = values.loc[values["team_name"].eq(team)].copy()
            team_rows["_bootstrap_value"] = pd.to_numeric(
                team_rows[value_column], errors="coerce"
            )
            team_rows = team_rows.loc[
                team_rows["_bootstrap_value"].notna()
            ]
            team_values = team_rows["_bootstrap_value"].to_numpy(dtype=float)
            probabilities = None
            if weighted_mode:
                team_weights = pd.to_numeric(
                    team_rows["analysis_weight"], errors="coerce"
                ).fillna(0.0).to_numpy(dtype=float)
                if team_weights.sum() > 0.0:
                    probabilities = team_weights / team_weights.sum()
            sampled = rng.choice(
                team_values,
                size=len(team_values),
                replace=True,
                p=probabilities,
            )
            draw[team] = float(np.median(sampled))
        draw_order = sorted(
            draw,
            key=lambda team: (
                draw[team] if lower_is_better else -draw[team],
                team,
            ),
        )
        for rank, team in enumerate(draw_order, start=1):
            rank_draws[team].append(rank)
    rows = []
    for rank, team in enumerate(ordered, start=1):
        draws = rank_draws[team]
        rows.append(
            {
                "team": team,
                "rank": rank,
                "value": point[team],
                "rank_interval_80": [
                    int(np.floor(np.quantile(draws, 0.10))),
                    int(np.ceil(np.quantile(draws, 0.90))),
                ],
                "p_rank_1": float(np.mean(np.asarray(draws) == 1)),
            }
        )
    return rows


def _qualifying_analysis(
    qualifying: pd.DataFrame,
    config: Mapping[str, Any],
    event_id: tuple[int, str],
) -> dict[str, Any]:
    if qualifying.empty:
        return {
            "status": "missing",
            "absolute_time_merged_with_race": False,
            "team_order": [],
            "published_vehicle_ranking": None,
        }
    required = {
        "year",
        "meeting_name",
        "driver_id",
        "team_name",
        "lap_duration",
    }
    _required(qualifying, required, "Qualifying")
    work = qualifying.loc[
        qualifying["year"].eq(event_id[0])
        & qualifying["meeting_name"].eq(event_id[1])
        & qualifying["team_name"].isin(config["scope"]["reporting_teams"])
    ].copy()
    work["lap_duration"] = pd.to_numeric(work["lap_duration"], errors="coerce")
    work = work.loc[work["lap_duration"].gt(0.0)]
    driver_best = (
        work.groupby(["team_name", "driver_id"], observed=True)["lap_duration"]
        .min()
        .reset_index()
    )
    order = _bootstrap_team_order(
        driver_best,
        value_column="lap_duration",
        lower_is_better=True,
        repetitions=int(config["bootstrap"]["repetitions"]),
        seed=_stable_seed(int(config["bootstrap"]["seed"]), *event_id, "qualifying"),
    )
    return {
        "status": "descriptive_vehicle_potential_proxy" if order else "missing",
        "absolute_time_merged_with_race": False,
        "team_order": order,
        "published_vehicle_ranking": (
            [{"team": row["team"], "rank": row["rank"]} for row in order]
            if len(order) >= int(config["coverage_gate"]["minimum_reporting_teams"])
            else None
        ),
        "interpretation": (
            "以两位车手各自最佳准确推圈的车队中位数描述排位潜力；"
            "不与正赛绝对圈时加权融合。"
        ),
    }


def _race_analysis(
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    publication_gate: Mapping[str, Any],
    event_id: tuple[int, str],
) -> dict[str, Any]:
    eligibility_column = (
        "pace_eligible_lap"
        if "pace_eligible_lap" in frame
        else "modelable_lap"
    )
    reporting = frame.loc[
        frame["team_name"].isin(config["scope"]["reporting_teams"])
        & frame[eligibility_column]
        & frame["baseline_available"]
    ].copy()
    order = _bootstrap_team_order(
        reporting,
        value_column="delta_to_reasonable_baseline_s",
        lower_is_better=True,
        repetitions=int(config["bootstrap"]["repetitions"]),
        seed=_stable_seed(int(config["bootstrap"]["seed"]), *event_id, "race"),
    )
    publishable_teams = list(
        publication_gate.get("publishable_teams")
        or (
            config["scope"]["reporting_teams"]
            if publication_gate["passed"]
            else []
        )
    )
    publishable = reporting.loc[
        reporting["team_name"].isin(publishable_teams)
    ]
    published_order = (
        _bootstrap_team_order(
            publishable,
            value_column="delta_to_reasonable_baseline_s",
            lower_is_better=True,
            repetitions=int(config["bootstrap"]["repetitions"]),
            seed=_stable_seed(
                int(config["bootstrap"]["seed"]),
                *event_id,
                "race_publishable_modules",
            ),
        )
        if len(publishable_teams) >= 2
        else []
    )
    partial_allowed = bool(
        publication_gate.get("partial_team_conclusion_allowed", False)
    )
    publication_allowed = bool(publication_gate["passed"] or partial_allowed)
    return {
        "status": (
            "publishable_retrospective_proxy"
            if publication_gate["passed"]
            else (
                "partial_team_modules_proxy"
                if partial_allowed
                else "audit_only"
            )
        ),
        "descriptive_team_order": order,
        "published_vehicle_ranking": (
            [
                {
                    "team": row["team"],
                    "rank": row["rank"],
                    "rank_interval_80": row["rank_interval_80"],
                }
                for row in published_order
            ]
            if publication_allowed
            else None
        ),
        "ranking_scope_teams": publishable_teams,
        "ranking_scope_complete": bool(publication_gate["passed"]),
        "pace_basis": (
            (
                "inclusive_crossfit_soft_weight_conditional_proxy"
                if "inclusive_model_candidate" in frame
                else "fuel_track_traffic_adjusted_conditional_proxy"
            )
            if eligibility_column == "pace_eligible_lap"
            else "clean_air_observed_lap_time"
        ),
        "ranking_uncertainty": (
            "reliability_weighted_lap_bootstrap_rank_interval"
            if "analysis_weight" in reporting
            else "bootstrap rank interval over complete-event modelable laps"
        ),
        "interpretation": (
            "正赛次序来自同配方、相近胎龄与比赛阶段条件池的长距离基础配速代理；"
            "部分发布只覆盖独立通过车队模块的车队，不代表四队完整全序。"
        ),
    }


def build_vehicle_driver_decomposition(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """在可建模/交通圈范围内做秒数加法闭合，不生成贡献百分比。"""

    proxy_mode = "pace_eligible_lap" in frame
    inclusive_mode = "inclusive_model_candidate" in frame
    if proxy_mode:
        usable_scope = frame["pace_eligible_lap"] & frame["baseline_available"]
    else:
        usable_scope = (
            frame["quality_ok"]
            & frame["all_green"].fillna(False).astype(bool)
            & ~frame["pit_boundary_proxy"]
            & frame["baseline_available"]
            & frame["traffic_state"].isin(
                ["clean_air", "dirty_air", "intermediate_gap"]
            )
        )
    work = frame.loc[
        usable_scope & frame["team_name"].isin(config["scope"]["reporting_teams"])
    ].copy()
    rows: list[dict[str, Any]] = []
    event_keys = ["year", "meeting_name", "team_name"]
    for (year, meeting, team), team_group in work.groupby(
        event_keys, observed=True, sort=True
    ):
        clean_team = (
            team_group
            if proxy_mode
            else team_group.loc[team_group["traffic_state"].eq("clean_air")]
        )
        if inclusive_mode:
            team_common = weighted_median(
                pd.to_numeric(
                    clean_team["delta_to_reasonable_baseline_s"],
                    errors="coerce",
                ),
                pd.to_numeric(
                    clean_team["analysis_weight"], errors="coerce"
                ).fillna(0.0),
            )
        else:
            team_common = _finite(
                pd.to_numeric(
                    clean_team["delta_to_reasonable_baseline_s"],
                    errors="coerce",
                ).median()
            )
        if team_common is None:
            continue
        for driver, group in team_group.groupby("driver_id", observed=True, sort=True):
            clean_driver = (
                group
                if proxy_mode
                else group.loc[group["traffic_state"].eq("clean_air")]
            )
            if inclusive_mode:
                driver_center = weighted_median(
                    pd.to_numeric(
                        clean_driver["delta_to_reasonable_baseline_s"],
                        errors="coerce",
                    ),
                    pd.to_numeric(
                        clean_driver["analysis_weight"], errors="coerce"
                    ).fillna(0.0),
                )
            else:
                driver_center = _finite(
                    pd.to_numeric(
                        clean_driver["delta_to_reasonable_baseline_s"],
                        errors="coerce",
                    ).median()
                )
            if driver_center is None:
                driver_center = team_common
            adjusted_delta = pd.to_numeric(
                group["delta_to_reasonable_baseline_s"], errors="coerce"
            )
            n = int(len(group))
            if inclusive_mode:
                group_weight = pd.to_numeric(
                    group["analysis_weight"], errors="coerce"
                ).fillna(0.0)
                track_position_loss = (
                    pd.to_numeric(
                        group["inclusive_traffic_effect_proxy_s"],
                        errors="coerce",
                    ).fillna(0.0)
                    + pd.to_numeric(
                        group["inclusive_pit_effect_proxy_s"],
                        errors="coerce",
                    ).fillna(0.0)
                )
                adjusted_center = weighted_median(
                    adjusted_delta, group_weight
                )
                strategy_center = weighted_median(
                    track_position_loss, group_weight
                )
                adjusted_total = float((adjusted_center or 0.0) * n)
                strategy = float((strategy_center or 0.0) * n)
                observed = adjusted_total + strategy
            else:
                traffic_loss = (
                    pd.to_numeric(
                        group["traffic_penalty_proxy_s"], errors="coerce"
                    ).fillna(0.0)
                    if proxy_mode
                    else pd.Series(0.0, index=group.index)
                )
                observed = float((adjusted_delta + traffic_loss).sum())
            vehicle = float(team_common * n)
            driver_component = float((driver_center - team_common) * n)
            if inclusive_mode:
                pass
            elif proxy_mode:
                strategy = float(traffic_loss.sum())
            else:
                traffic = group.loc[
                    group["traffic_state"].isin(
                        ["dirty_air", "intermediate_gap"]
                    )
                ]
                strategy = float(
                    (
                        pd.to_numeric(
                            traffic["delta_to_reasonable_baseline_s"],
                            errors="coerce",
                        )
                        - driver_center
                    ).sum()
                )
            unexplained = float(observed - vehicle - driver_component - strategy)
            closure = float(
                observed - (vehicle + driver_component + strategy + unexplained)
            )
            rows.append(
                {
                    "year": int(year),
                    "meeting_name": str(meeting),
                    "team": str(team),
                    "driver": str(driver),
                    "scope": (
                        (
                            "inclusive_crossfit_soft_weight_laps_with_baseline"
                            if inclusive_mode
                            else "fuel_track_traffic_adjusted_laps_with_baseline"
                        )
                        if proxy_mode
                        else "green_quality_laps_with_baseline_including_traffic_proxy"
                    ),
                    "observed_accounted_delta_s": observed,
                    "components": {
                        "vehicle_common_baseline_s": vehicle,
                        "driver_realization_deviation_s": driver_component,
                        "strategy_track_position_proxy_s": strategy,
                        "unexplained_residual_s": unexplained,
                    },
                    "closure_error_s": closure,
                    "vehicle_parameter_shared_by_team_drivers": True,
                    "strategy_identification": (
                        (
                            "inclusive_model_traffic_and_pit_condition_proxy_not_causal_loss"
                            if inclusive_mode
                            else "empirical_traffic_penalty_proxy_only_pit_and_full_counterfactual_not_identified"
                        )
                        if proxy_mode
                        else "traffic_excess_proxy_only_pit_and_full_counterfactual_not_identified"
                    ),
                    "driver_interpretation": (
                        "当场车手—赛车组合兑现偏差；不是长期纯车手能力。"
                    ),
                }
            )
    return rows


def _simulation_proxy_audit(
    frame: pd.DataFrame, config: Mapping[str, Any]
) -> dict[str, Any]:
    """汇总条件模拟的支持度、敏感性和不可识别边界。"""

    if "simulation_proxies" not in config:
        return {
            "status": "not_enabled",
            "real_fuel_load_identified": False,
            "causal_traffic_effect_identified": False,
            "physical_track_state_identified": False,
        }
    proxies = config["simulation_proxies"]
    fuel_base = pd.to_numeric(
        frame["fuel_correction_base_s"], errors="coerce"
    )
    fuel_low = pd.to_numeric(
        frame["fuel_correction_low_s"], errors="coerce"
    )
    fuel_high = pd.to_numeric(
        frame["fuel_correction_high_s"], errors="coerce"
    )
    track_effect = pd.to_numeric(
        frame["track_evolution_proxy_s"], errors="coerce"
    )
    traffic_rows: list[dict[str, Any]] = []
    for state in ("clean_air", "intermediate_gap", "dirty_air"):
        state_rows = frame.loc[frame["traffic_state"].eq(state)]
        supported = state_rows.loc[state_rows["traffic_penalty_available"]]
        traffic_rows.append(
            {
                "state": state,
                "observed_laps": int(len(state_rows)),
                "supported_laps": int(len(supported)),
                "penalty_proxy_s": _numeric_median(
                    supported["traffic_penalty_proxy_s"]
                ),
                "support_drivers": int(
                    supported["driver_id"].nunique()
                    if len(supported)
                    else 0
                ),
                "holdout_validation": {
                    "status": (
                        str(
                            state_rows[
                                "traffic_validation_status"
                            ].mode().iloc[0]
                        )
                        if len(state_rows)
                        and not state_rows[
                            "traffic_validation_status"
                        ].mode().empty
                        else "not_tested"
                    ),
                    "holdout_laps": int(
                        pd.to_numeric(
                            state_rows[
                                "traffic_validation_holdout_laps"
                            ],
                            errors="coerce",
                        ).max()
                        if len(state_rows)
                        else 0
                    ),
                    "mae_before_s": _numeric_median(
                        state_rows[
                            "traffic_validation_mae_before_s"
                        ]
                    ),
                    "mae_after_s": _numeric_median(
                        state_rows[
                            "traffic_validation_mae_after_s"
                        ]
                    ),
                },
            }
        )
    clean_laps = int(frame["modelable_lap"].sum())
    pace_laps = int(frame["pace_eligible_lap"].sum())
    return {
        "status": "conditional_simulation_proxy",
        "model_registry": dict(config.get("model_registry", {})),
        "fuel": {
            "model": str(proxies["fuel"]["model"]),
            "enabled": bool(proxies["fuel"].get("enabled", False)),
            "burn_rule": str(proxies["fuel"]["burn_rule"]),
            "assumptions": {
                "initial_fuel_kg": dict(
                    proxies["fuel"]["initial_fuel_kg"]
                ),
                "lap_time_s_per_kg": dict(
                    proxies["fuel"]["lap_time_s_per_kg"]
                ),
            },
            "base_correction_range_s": [
                _finite(fuel_base.min()),
                _finite(fuel_base.max()),
            ],
            "sensitivity_correction_range_s": [
                _finite(fuel_low.min()),
                _finite(fuel_high.max()),
            ],
            "real_fuel_load_identified": False,
        },
        "track_evolution": {
            "model": str(proxies["track_evolution"]["model"]),
            "enabled": bool(
                proxies["track_evolution"].get("enabled", False)
            ),
            "available_laps": int(
                frame["track_evolution_available"].sum()
            ),
            "direct_support_laps": int(
                frame["track_evolution_direct_support"].sum()
            ),
            "correction_range_s": [
                _finite(track_effect.min()),
                _finite(track_effect.max()),
            ],
            "holdout_validation": {
                "status": (
                    str(frame["track_validation_status"].mode().iloc[0])
                    if "track_validation_status" in frame
                    and not frame["track_validation_status"].mode().empty
                    else "not_tested"
                ),
                "holdout_laps": int(
                    pd.to_numeric(
                        frame.get(
                            "track_validation_holdout_laps",
                            pd.Series(0, index=frame.index),
                        ),
                        errors="coerce",
                    ).max()
                ),
                "mae_before_s": _numeric_median(
                    frame.get(
                        "track_validation_mae_before_s",
                        pd.Series(np.nan, index=frame.index),
                    )
                ),
                "mae_after_s": _numeric_median(
                    frame.get(
                        "track_validation_mae_after_s",
                        pd.Series(np.nan, index=frame.index),
                    )
                ),
            },
            "physical_track_state_identified": False,
        },
        "traffic": {
            "model": str(proxies["traffic"]["model"]),
            "enabled": bool(proxies["traffic"].get("enabled", False)),
            "states": traffic_rows,
            "causal_aerodynamic_loss_identified": False,
        },
        "ablation": {
            "clean_air_modelable_laps": clean_laps,
            "conditional_proxy_eligible_laps": pace_laps,
            "eligible_lap_change": pace_laps - clean_laps,
        },
        "validation_boundary": (
            "合成恢复测试验证实现；真实比赛只报告支持度、敏感性和消融，"
            "没有真实油量、真实胎态或风洞真值可作直接监督。"
        ),
    }


def _coverage_audit(
    frame: pd.DataFrame,
    stint_dossiers: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    gate = config["coverage_gate"]
    inclusive_mode = (
        int(
            str(config.get("method_version", "v8")).removeprefix("v")
        )
        >= 15
        and "inclusive_model_candidate" in frame
    )
    reporting = frame.loc[
        frame["team_name"].isin(config["scope"]["reporting_teams"])
    ].copy()
    driver_expected = (
        reporting.groupby(["year", "meeting_name", "driver_id"], observed=True)[
            "lap_number"
        ]
        .max()
        .sum()
    )
    continuity = (
        float(len(reporting) / driver_expected)
        if driver_expected and np.isfinite(driver_expected)
        else 0.0
    )
    disposition_total = int(
        sum(
            int(reporting["primary_disposition"].eq(value).sum())
            for value in PRIMARY_DISPOSITIONS
        )
    )
    valid_stints = [row for row in stint_dossiers if row["status"] == "valid"]
    team_metrics: list[dict[str, Any]] = []
    reasons: list[str] = []
    for team in config["scope"]["reporting_teams"]:
        team_laps = reporting.loc[reporting["team_name"].eq(team)]
        drivers = int(team_laps["driver_id"].nunique())
        clean_laps = int(team_laps["modelable_lap"].sum())
        pace_laps = int(
            team_laps[
                "pace_eligible_lap"
                if "pace_eligible_lap" in team_laps
                else "modelable_lap"
            ].sum()
        )
        effective_pace_laps = float(
            pd.to_numeric(
                team_laps.get(
                    "analysis_weight",
                    pd.Series(1.0, index=team_laps.index),
                ),
                errors="coerce",
            )
            .where(
                team_laps[
                    "pace_eligible_lap"
                    if "pace_eligible_lap" in team_laps
                    else "modelable_lap"
                ],
                0.0,
            )
            .fillna(0.0)
            .sum()
        )
        baseline_laps = int(
            (
                team_laps[
                    "pace_eligible_lap"
                    if "pace_eligible_lap" in team_laps
                    else "modelable_lap"
                ]
                & team_laps["baseline_available"]
            ).sum()
        )
        team_valid_stints = sum(row["team"] == team for row in valid_stints)
        team_metrics.append(
            {
                "team": team,
                "drivers": drivers,
                "modelable_laps": clean_laps,
                "pace_adjusted_laps": pace_laps,
                "effective_pace_laps": effective_pace_laps,
                "baseline_laps": baseline_laps,
                "valid_stints": int(team_valid_stints),
            }
        )
        if drivers < int(gate["minimum_drivers_per_team"]):
            reasons.append(f"{team}:drivers_below_gate")
        if clean_laps < int(gate["minimum_clean_laps_per_team"]):
            reasons.append(f"{team}:modelable_laps_below_gate")
        if team_valid_stints < int(gate["minimum_valid_stints_per_team"]):
            reasons.append(f"{team}:valid_stints_below_gate")
    event_laps = int(frame["lap_number"].max()) if len(frame) else 0
    primary_eligibility = (
        "pace_eligible_lap"
        if inclusive_mode
        else "modelable_lap"
    )
    observed_teams = int(
        reporting.loc[reporting[primary_eligibility], "team_name"].nunique()
    )
    modelable_fraction = (
        float(reporting["modelable_lap"].mean()) if len(reporting) else 0.0
    )
    if event_laps < int(gate["minimum_race_laps"]):
        reasons.append("race_lap_span_below_gate")
    if observed_teams < int(gate["minimum_reporting_teams"]):
        reasons.append("reporting_team_coverage_below_gate")
    if modelable_fraction < float(gate["minimum_modelable_lap_fraction"]):
        reasons.append("modelable_lap_fraction_below_gate")
    if disposition_total != len(reporting):
        reasons.append("lap_disposition_does_not_close")
    coverage = {
        "analysis_unit": "complete_race",
        "observed_field_laps": int(len(frame)),
        "observed_reporting_team_laps": int(len(reporting)),
        "race_lap_span": event_laps,
        "reporting_driver_lap_continuity_proxy": continuity,
        "modelable_reporting_laps": int(reporting["modelable_lap"].sum()),
        "modelable_reporting_lap_fraction": modelable_fraction,
        "pace_adjusted_reporting_laps": int(
            reporting[
                "pace_eligible_lap"
                if "pace_eligible_lap" in reporting
                else "modelable_lap"
            ].sum()
        ),
        "pace_adjusted_reporting_lap_fraction": float(
            reporting[
                "pace_eligible_lap"
                if "pace_eligible_lap" in reporting
                else "modelable_lap"
            ].mean()
            if len(reporting)
            else 0.0
        ),
        "effective_pace_reporting_laps": float(
            pd.to_numeric(
                reporting.get(
                    "analysis_weight",
                    pd.Series(1.0, index=reporting.index),
                ),
                errors="coerce",
            )
            .where(
                reporting[
                    "pace_eligible_lap"
                    if "pace_eligible_lap" in reporting
                    else "modelable_lap"
                ],
                0.0,
            )
            .fillna(0.0)
            .sum()
        ),
        "strict_confirmation_reporting_laps": int(
            reporting.get(
                "strict_pace_eligible_lap",
                reporting["modelable_lap"],
            )
            .fillna(False)
            .astype(bool)
            .sum()
        ),
        "baseline_available_reporting_laps": int(
            reporting["baseline_available"].sum()
        ),
        "stint_assignment_coverage": float(
            reporting["stint_number"].notna().mean() if len(reporting) else 0.0
        ),
        "track_state_coverage": float(
            reporting["track_status_codes"].notna().mean() if len(reporting) else 0.0
        ),
        "position_gap_state_coverage": float(
            reporting["traffic_state"].ne("unknown_gap").mean()
            if len(reporting)
            else 0.0
        ),
        "teams": team_metrics,
        "lap_disposition_closure": {
            "accounted_laps": disposition_total,
            "observed_laps": int(len(reporting)),
            "passed": disposition_total == len(reporting),
        },
        "inclusive_model_status": (
            str(reporting["inclusive_model_validation_status"].mode().iloc[0])
            if inclusive_mode
            and not reporting[
                "inclusive_model_validation_status"
            ].mode().empty
            else "not_enabled"
        ),
    }
    publication = {
        "passed": not reasons,
        "status": "publishable_retrospective_dossier" if not reasons else "audit_only",
        "reasons": reasons,
        "whole_race_conclusion_allowed": not reasons,
    }
    if "module_publication_gate" in config:
        module = config["module_publication_gate"]
        team_settings = module["team"]
        pair_settings = module["driver_pair"]
        pace_column = (
            "pace_eligible_lap"
            if "pace_eligible_lap" in reporting
            else "modelable_lap"
        )
        team_modules: list[dict[str, Any]] = []
        pair_modules: list[dict[str, Any]] = []
        valid_by_driver: dict[tuple[str, str], int] = {}
        for row in valid_stints:
            key = (str(row["team"]), str(row["driver"]))
            valid_by_driver[key] = valid_by_driver.get(key, 0) + 1
        for team in config["scope"]["reporting_teams"]:
            team_laps = reporting.loc[reporting["team_name"].eq(team)]
            drivers = sorted(team_laps["driver_id"].dropna().astype(str).unique())
            pace_laps = int(team_laps[pace_column].sum())
            team_effective_pace = float(
                pd.to_numeric(
                    team_laps.get(
                        "analysis_weight",
                        pd.Series(1.0, index=team_laps.index),
                    ),
                    errors="coerce",
                )
                .where(team_laps[pace_column], 0.0)
                .fillna(0.0)
                .sum()
            )
            baseline_laps = int(
                (team_laps[pace_column] & team_laps["baseline_available"]).sum()
            )
            team_valid_stints = sum(
                str(row["team"]) == str(team) for row in valid_stints
            )
            team_reasons: list[str] = []
            if len(drivers) < int(team_settings["minimum_drivers"]):
                team_reasons.append("drivers_below_gate")
            if pace_laps < int(
                team_settings["minimum_pace_adjusted_laps"]
            ):
                team_reasons.append("pace_adjusted_laps_below_gate")
            if team_effective_pace < float(
                team_settings.get("minimum_effective_pace_laps", 0.0)
            ):
                team_reasons.append("effective_pace_laps_below_gate")
            if team_valid_stints < int(
                team_settings["minimum_valid_stints"]
            ):
                team_reasons.append("valid_stints_below_gate")
            if baseline_laps < int(team_settings["minimum_baseline_laps"]):
                team_reasons.append("baseline_laps_below_gate")
            team_modules.append(
                {
                    "team": str(team),
                    "passed": not team_reasons,
                    "reasons": team_reasons,
                    "drivers": len(drivers),
                    "pace_adjusted_laps": pace_laps,
                    "effective_pace_laps": team_effective_pace,
                    "baseline_laps": baseline_laps,
                    "valid_stints": int(team_valid_stints),
                }
            )

            driver_rows: list[dict[str, Any]] = []
            pair_reasons: list[str] = []
            if len(drivers) < int(pair_settings["minimum_drivers"]):
                pair_reasons.append("drivers_below_gate")
            for driver in drivers:
                driver_laps = team_laps.loc[
                    team_laps["driver_id"].astype(str).eq(driver)
                ]
                driver_pace_laps = int(driver_laps[pace_column].sum())
                driver_effective_pace = float(
                    pd.to_numeric(
                        driver_laps.get(
                            "analysis_weight",
                            pd.Series(1.0, index=driver_laps.index),
                        ),
                        errors="coerce",
                    )
                    .where(driver_laps[pace_column], 0.0)
                    .fillna(0.0)
                    .sum()
                )
                driver_valid_stints = int(
                    valid_by_driver.get((str(team), driver), 0)
                )
                driver_rows.append(
                    {
                        "driver": driver,
                        "pace_adjusted_laps": driver_pace_laps,
                        "effective_pace_laps": driver_effective_pace,
                        "valid_stints": driver_valid_stints,
                    }
                )
                if driver_pace_laps < int(
                    pair_settings["minimum_pace_adjusted_laps_per_driver"]
                ):
                    pair_reasons.append(f"{driver}:pace_laps_below_gate")
                if driver_effective_pace < float(
                    pair_settings.get(
                        "minimum_effective_pace_laps_per_driver", 0.0
                    )
                ):
                    pair_reasons.append(
                        f"{driver}:effective_pace_laps_below_gate"
                    )
                if driver_valid_stints < int(
                    pair_settings["minimum_valid_stints_per_driver"]
                ):
                    pair_reasons.append(f"{driver}:valid_stints_below_gate")
            pair_modules.append(
                {
                    "team": str(team),
                    "passed": not pair_reasons,
                    "reasons": pair_reasons,
                    "drivers": driver_rows,
                    "interpretation": (
                        "只允许同队条件对照；通过不等于识别纯车手能力。"
                    ),
                }
            )

        context_reasons: list[str] = []
        observed_reporting_teams = int(reporting["team_name"].nunique())
        if event_laps < int(gate["minimum_race_laps"]):
            context_reasons.append("race_lap_span_below_gate")
        if observed_reporting_teams < int(gate["minimum_reporting_teams"]):
            context_reasons.append("reporting_team_observation_below_gate")
        if disposition_total != len(reporting):
            context_reasons.append("lap_disposition_does_not_close")
        if inclusive_mode and coverage["inclusive_model_status"] != (
            "accepted_crossfit_proxy"
        ):
            context_reasons.append("inclusive_robust_model_not_accepted")
        pace_fraction = coverage["pace_adjusted_reporting_lap_fraction"]
        full_reasons = list(context_reasons)
        if pace_fraction < float(gate["minimum_modelable_lap_fraction"]):
            full_reasons.append("pace_adjusted_lap_fraction_below_gate")
        full_reasons.extend(
            f"{row['team']}:{reason}"
            for row in team_modules
            for reason in row["reasons"]
        )
        publishable_teams = [
            str(row["team"]) for row in team_modules if row["passed"]
        ]
        context_passed = not context_reasons
        full_passed = not full_reasons
        partial_allowed = bool(
            context_passed
            and len(publishable_teams)
            >= int(module["minimum_publishable_teams"])
        )
        publication = {
            "passed": full_passed,
            "status": (
                "publishable_retrospective_dossier"
                if full_passed
                else (
                    "partial_team_modules_publishable"
                    if partial_allowed
                    else "audit_only"
                )
            ),
            "reasons": full_reasons,
            "whole_race_conclusion_allowed": full_passed,
            "partial_team_conclusion_allowed": partial_allowed,
            "publishable_teams": publishable_teams,
            "module_gates": {
                "whole_race_context": {
                    "passed": context_passed,
                    "reasons": context_reasons,
                    "analysis_unit": "complete_race",
                },
                "teams": team_modules,
                "driver_pairs": pair_modules,
                "full_reporting_field": {
                    "passed": full_passed,
                    "reasons": full_reasons,
                },
            },
            "legacy_clean_four_team_gate": {
                "passed": not reasons,
                "reasons": reasons,
                "thresholds_unchanged": True,
            },
        }
    return coverage, publication


def build_race_dossier(
    laps: pd.DataFrame,
    qualifying: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    classification_proxy: Mapping[str, int] | None,
    return_curve_sidecar: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any] | None]:
    """生成单场 Dossier；输入包含多场时拒绝，防止局部串场。"""

    annotated = annotate_race_laps(laps, config)
    event_ids = annotated[["year", "meeting_name"]].drop_duplicates()
    if len(event_ids) != 1:
        raise ValueError("build_race_dossier 每次必须且只能接收一场完整比赛")
    event_id = (
        int(event_ids.iloc[0]["year"]),
        str(event_ids.iloc[0]["meeting_name"]),
    )
    stints = build_stint_dossiers(annotated, config)
    coverage, publication = _coverage_audit(annotated, stints, config)
    ledger = build_event_ledger(annotated)
    qualifying_analysis = _qualifying_analysis(qualifying, config, event_id)
    race_analysis = _race_analysis(
        annotated, config, publication, event_id
    )
    tyre = build_tyre_strategy_envelope(annotated, stints, config)
    pit_cycle = build_pit_cycle_loss_proxy(annotated, config)
    decomposition = build_vehicle_driver_decomposition(annotated, config)
    proxy_rows = []
    if classification_proxy:
        race_ranks = {
            row["team"]: int(row["rank"])
            for row in race_analysis["descriptive_team_order"]
        }
        for team, actual_rank in sorted(classification_proxy.items()):
            proxy_rows.append(
                {
                    "team": str(team),
                    "performance_proxy_rank": race_ranks.get(str(team)),
                    "classification_proxy_rank": int(actual_rank),
                    "rank_difference": (
                        int(actual_rank) - int(race_ranks[str(team)])
                        if str(team) in race_ranks
                        else None
                    ),
                }
            )
    event_conclusion = None
    conclusion_allowed = bool(
        publication["passed"]
        or publication.get("partial_team_conclusion_allowed", False)
    )
    if conclusion_allowed:
        published_race_order = (
            race_analysis.get("published_vehicle_ranking") or []
        )
        event_conclusion = {
            "scope": (
                "complete_reporting_field"
                if publication["passed"]
                else "partial_team_modules"
            ),
            "included_teams": race_analysis.get("ranking_scope_teams", []),
            "qualifying_potential_leader_proxy": (
                qualifying_analysis["team_order"][0]["team"]
                if qualifying_analysis["team_order"]
                else None
            ),
            "race_long_run_leader_proxy": (
                published_race_order[0]["team"]
                if published_race_order
                else None
            ),
            "unified_weekend_rank": None,
            "note": (
                "排位与正赛独立发布，不生成统一周末全序；partial 结论"
                "不外推到未通过车队模块。"
            ),
        }
    method_version = str(config.get("method_version", "v8"))
    report_id = f"race_dossier_{event_id[0]}_{event_id[1]}_{method_version}"
    report = {
        "report_id": report_id,
        "scope": {
            "year": event_id[0],
            "meeting": event_id[1],
            "session": "Race",
            "team": " / ".join(config["scope"]["reporting_teams"]),
        },
        "schema_version": f"race-dossier-{method_version}",
        "release_status": publication["status"],
        "analysis_unit": "complete_race",
        "coverage": coverage,
        "publication_gate": publication,
        "event_ledger": ledger,
        "simulation_proxy_audit": _simulation_proxy_audit(
            annotated, config
        ),
        "inclusive_robust_model_audit": (
            build_inclusive_robust_audit(
                annotated, config["inclusive_robust_model"]
            )
            if "inclusive_robust_model" in config
            else {
                "status": "not_enabled",
                "causal_truth_identified": False,
            }
        ),
        "qualifying_analysis": qualifying_analysis,
        "race_analysis": race_analysis,
        "tyre_strategy_envelope": tyre,
        "pit_cycle_loss_proxy": pit_cycle,
        "stint_dossiers": stints,
        "vehicle_driver_decomposition": decomposition,
        "result_impact_audit": {
            "major_factor_claims": [],
            "classification_proxy_comparison": proxy_rows,
            "fia_final_classification_used": False,
            "expected_points_interval": None,
            "reason": (
                "没有冻结且时间外校准的全场位置/积分反事实模拟；"
                "classification_proxy 仅用于赛后构念审计。"
            ),
        },
        "ratings": {"PAC": None, "OVR": None},
        "event_conclusion": event_conclusion,
        "boundaries": {
            "data_proxy": True,
            "unidentified": [
                "真实燃油量",
                "真实胎温胎压与磨损量",
                "车辆损伤与具体故障",
                "车队指令与驾驶意图",
                "倍耐力或车队内部策略模拟",
            ],
            "forbidden_claims": [
                "由少量圈直接形成整场结论",
                "把公开数据包络称为倍耐力内部模拟",
                "把单场保胎差异直接归因为纯车手能力",
                "把 classification_proxy 称为 FIA 最终分类",
                "输出伪精确贡献百分比",
                "把样本外软权重解释为事件发生概率或因果真值",
                "用样本内残差循环删除不支持既有结论的圈",
            ],
        },
    }
    if "stint_curve_evidence" in config:
        comparison = config["stint_curve_evidence"]["comparison_gate"]
        report["stint_comparison_contract"] = {
            "schema_version": "stint-comparison-v17",
            "source": "stint_curve_evidence_sidecar",
            "same_compound_required": True,
            "valid_stint_required": True,
            "maximum_reference_tyre_age_gap_laps": float(
                comparison["maximum_reference_tyre_age_gap_laps"]
            ),
            "maximum_lap_fraction_difference": float(
                comparison["maximum_lap_fraction_difference"]
            ),
            "minimum_common_support_span_laps": float(
                comparison["minimum_common_support_span_laps"]
            ),
            "minimum_support_overlap_ratio": float(
                comparison["minimum_support_overlap_ratio"]
            ),
            "maximum_direct_condition_distance": float(
                comparison["maximum_direct_distance"]
            ),
            "maximum_warning_condition_distance": float(
                comparison["maximum_warning_distance"]
            ),
            "weather_and_track_temperature_identified": False,
        }
    if return_curve_sidecar:
        return report, build_stint_curve_sidecar(
            annotated,
            stints,
            config,
            report_id=report_id,
        )
    return report


def build_season_audit(
    dossiers: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> dict[str, Any]:
    """按分站等权形成赛季曲线；实际结果只进入审计残差。"""

    validate_race_dossier_config(config)
    qualifying_curve: list[dict[str, Any]] = []
    race_curve: list[dict[str, Any]] = []
    driver_curve: list[dict[str, Any]] = []
    vehicle_curve: list[dict[str, Any]] = []
    result_audit: list[dict[str, Any]] = []
    for dossier in dossiers:
        year = int(dossier["scope"]["year"])
        meeting = str(dossier["scope"]["meeting"])
        for row in dossier["qualifying_analysis"].get("team_order", []):
            qualifying_curve.append(
                {
                    "year": year,
                    "meeting": meeting,
                    "team": row["team"],
                    "rank": row["rank"],
                    "rank_interval_80": row["rank_interval_80"],
                    "value_seconds": row["value"],
                }
            )
        for row in dossier["race_analysis"].get("descriptive_team_order", []):
            race_curve.append(
                {
                    "year": year,
                    "meeting": meeting,
                    "team": row["team"],
                    "rank": row["rank"],
                    "rank_interval_80": row["rank_interval_80"],
                    "delta_to_reasonable_baseline_s_per_lap": row["value"],
                }
            )
        for row in dossier.get("vehicle_driver_decomposition", []):
            components = row["components"]
            driver_curve.append(
                {
                    "year": year,
                    "meeting": meeting,
                    "driver": row["driver"],
                    "team": row["team"],
                    "driver_realization_deviation_s": components[
                        "driver_realization_deviation_s"
                    ],
                }
            )
            vehicle_curve.append(
                {
                    "year": year,
                    "meeting": meeting,
                    "team": row["team"],
                    "driver": row["driver"],
                    "vehicle_common_baseline_s": components[
                        "vehicle_common_baseline_s"
                    ],
                }
            )
        result_audit.extend(
            {
                "year": year,
                "meeting": meeting,
                **row,
            }
            for row in dossier["result_impact_audit"].get(
                "classification_proxy_comparison", []
            )
        )
    method_version = str(config.get("method_version", "v8"))
    return {
        "schema_version": f"season-audit-{method_version}",
        "analysis_unit": "event_equal_weight",
        "qualifying_performance_curve": qualifying_curve,
        "race_performance_curve": race_curve,
        "driver_realization_curve": driver_curve,
        "vehicle_realization_curve": vehicle_curve,
        "performance_vs_actual_audit": result_audit,
        "actual_result_source": (
            "classification_proxy_not_fia_final" if result_audit else "not_available"
        ),
        "actual_results_used_for_fit": False,
        "expected_points_interval": None,
        "expected_championship_rank_interval": None,
        "PAC": None,
        "OVR": None,
        "limitations": [
            "当前没有冻结且时间外校准的积分/排名概率映射，相关区间保持 null。",
            "车辆排名区间重叠时只保留分站排名范围，不发布伪精确赛季全序。",
            "年终 classification_proxy 只审计逐场模型，不反向修改参数或门槛。",
        ],
    }
