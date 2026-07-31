"""2023–2025 分赛道模型对照、差距收敛与迭代验证的共用算法。

本模块只实现冻结协议中的纯计算部分。文件发现、身份校验、追加式发布和缓存管理
由 ``run_reference_analysis_lab_track_validation_v3.py`` 负责。

所有“赛道族群”均由公开遥测中的速度、弯角代理密度等可观测量定义；它们不能被
解释成真实下压力、阻力或引擎功率。模型目标也只对应冻结代理，不对应不可观测物理量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
import math
import warnings

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import HuberRegressor
from sklearn.preprocessing import StandardScaler

from reference_analysis_lab.f1pace_pilot import is_green_or_yellow
from reference_analysis_lab.fdataanalysis_reverse_engineered_v2 import (
    build_dynamic_straight_proxies,
    detect_dynamic_corner_proxies,
)


F1PACE_TRAFFIC_THRESHOLDS_SECONDS = (1.5, 1.75, 2.0, 2.25, 2.5)
F1PACE_MODEL_FEATURES = (
    "traffic_ratio_1_50s",
    "traffic_ratio_1_75s",
    "traffic_ratio_2_25s",
    "traffic_ratio_2_50s",
    "missing_time_share",
    "median_headway_s",
    "lap_fraction",
    "position_scaled",
    "median_speed_scaled",
)
DELTADATA_MODEL_FEATURES = (
    "lap_fraction",
    "lap_fraction_sq",
    "tyre_age_scaled",
    "tyre_age_sq_scaled",
    "traffic_ratio_2_00s",
    "position_scaled",
    "gap_seconds_proxy_clipped",
    "raw_lap_event_centered_s",
    "public_fuel_correction_s",
    "compound_soft",
    "compound_medium",
    "compound_hard",
    "compound_intermediate_or_wet",
    "non_green",
    "pit_boundary",
    "low_quality",
)


def finite_number(value: Any) -> float | None:
    """把任意标量收敛为有限浮点数；非有限值统一返回 ``None``。"""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def kish_effective_sample_size(weights: Iterable[float]) -> float:
    values = np.asarray(list(weights), dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    if not len(values):
        return 0.0
    denominator = float(np.square(values).sum())
    if denominator <= 0:
        return 0.0
    return float(values.sum() ** 2 / denominator)


def weighted_quantile(
    values: Sequence[float],
    weights: Sequence[float],
    probability: float,
) -> float | None:
    data = np.asarray(values, dtype=float)
    mass = np.asarray(weights, dtype=float)
    valid = np.isfinite(data) & np.isfinite(mass) & (mass > 0)
    data = data[valid]
    mass = mass[valid]
    if not len(data):
        return None
    order = np.argsort(data, kind="stable")
    data = data[order]
    mass = mass[order]
    cumulative = np.cumsum(mass)
    target = float(np.clip(probability, 0.0, 1.0)) * float(cumulative[-1])
    index = int(np.searchsorted(cumulative, target, side="left"))
    return float(data[min(index, len(data) - 1)])


def _first_finite(values: pd.Series) -> Any:
    for value in values:
        if value is not None and not pd.isna(value):
            return value
    return None


def _positive_last_interval_weights(points: pd.DataFrame) -> pd.Series:
    """复现 F1pace v1 的左区间时间权重，并对每圈最后一点延伸至圈尾。"""

    keys = ["driver_acronym", "lap"]
    next_time = points.groupby(keys, sort=False)["time"].shift(-1)
    raw_delta = next_time - points["time"]
    positive = raw_delta.where(raw_delta > 0)
    fallback = positive.groupby(
        [points["driver_acronym"], points["lap"]],
        sort=False,
    ).transform("median")
    tail = points["lap_duration"] - points["time"]
    is_last = next_time.isna()
    weights = raw_delta.copy()
    weights.loc[is_last] = tail.loc[is_last].where(
        tail.loc[is_last] > 0,
        fallback.loc[is_last],
    )
    return weights.where(np.isfinite(weights) & (weights > 0), 0.0)


def compute_f1pace_lap_features(points: pd.DataFrame) -> pd.DataFrame:
    """按固定 2 秒/33% 语义生成逐圈交通目标和不含 2 秒目标的模型特征。

    模型特征刻意不含 ``traffic_ratio_2_00s``，避免用目标本身预测目标。1.5/1.75
    与 2.25/2.5 秒仅用于检验不同采样条件下能否稳健恢复固定 2 秒定义。
    """

    required = {
        "driver_acronym",
        "lap",
        "sample_index",
        "time",
        "lap_duration",
        "speed",
        "driver_ahead",
        "distance_to_driver_ahead",
        "position",
        "stint",
        "compound",
        "tyre_life",
        "track_status",
        "deleted",
        "is_accurate",
        "team",
    }
    missing = sorted(required.difference(points.columns))
    if missing:
        raise ValueError("F1pace v3 缺少字段：" + ", ".join(missing))

    ordered = points.copy()
    for column in (
        "lap",
        "sample_index",
        "time",
        "lap_duration",
        "speed",
        "distance_to_driver_ahead",
        "position",
        "stint",
        "tyre_life",
    ):
        ordered[column] = pd.to_numeric(ordered[column], errors="coerce")
    ordered = ordered.loc[
        ordered["driver_acronym"].notna()
        & ordered["lap"].notna()
        & ordered["time"].notna()
    ].sort_values(
        ["driver_acronym", "lap", "time", "sample_index"],
        kind="stable",
    )
    ordered["interval_weight_s"] = _positive_last_interval_weights(ordered)
    speed_mps = ordered["speed"].clip(lower=0) / 3.6
    ahead_present = (
        ordered["driver_ahead"].notna()
        & ordered["driver_ahead"].astype(str).str.strip().ne("")
    )
    observed_headway = (
        ahead_present
        & ordered["distance_to_driver_ahead"].notna()
        & speed_mps.gt(0)
    )
    ordered["headway_s"] = np.where(
        observed_headway,
        ordered["distance_to_driver_ahead"] / speed_mps,
        np.nan,
    )
    ordered["missing_weight_s"] = np.where(
        observed_headway,
        0.0,
        ordered["interval_weight_s"],
    )
    for threshold in F1PACE_TRAFFIC_THRESHOLDS_SECONDS:
        suffix = f"{threshold:.2f}".replace(".", "_")
        ordered[f"traffic_weight_{suffix}s"] = np.where(
            observed_headway
            & (ordered["distance_to_driver_ahead"] <= speed_mps * threshold),
            ordered["interval_weight_s"],
            0.0,
        )

    keys = ["driver_acronym", "lap"]
    aggregation: dict[str, tuple[str, Any]] = {
        "lap_duration_s": ("lap_duration", "median"),
        "position": ("position", "median"),
        "stint": ("stint", "first"),
        "compound": ("compound", _first_finite),
        "tyre_age": ("tyre_life", "median"),
        "track_status": ("track_status", _first_finite),
        "deleted": ("deleted", "max"),
        "is_accurate": ("is_accurate", "min"),
        "team": ("team", _first_finite),
        "telemetry_samples": ("sample_index", "size"),
        "observed_time_s": ("interval_weight_s", "sum"),
        "missing_time_s": ("missing_weight_s", "sum"),
        "median_headway_s": ("headway_s", "median"),
        "median_speed_kph": ("speed", "median"),
    }
    for threshold in F1PACE_TRAFFIC_THRESHOLDS_SECONDS:
        suffix = f"{threshold:.2f}".replace(".", "_")
        aggregation[f"traffic_time_{suffix}s"] = (
            f"traffic_weight_{suffix}s",
            "sum",
        )
    laps = ordered.groupby(keys, as_index=False, sort=True).agg(**aggregation)
    laps["lap"] = laps["lap"].astype(int)
    laps["stint"] = pd.to_numeric(laps["stint"], errors="coerce").fillna(-1)
    laps["green_or_yellow"] = laps["track_status"].map(is_green_or_yellow)
    grouped = laps.sort_values(keys, kind="stable").groupby(
        "driver_acronym",
        sort=False,
    )
    previous_stint = grouped["stint"].shift(1)
    next_stint = grouped["stint"].shift(-1)
    laps["pit_boundary_lap"] = (
        (previous_stint.notna() & laps["stint"].ne(previous_stint))
        | (next_stint.notna() & laps["stint"].ne(next_stint))
    )
    max_lap = max(1, int(laps["lap"].max()))
    laps["lap_fraction"] = laps["lap"] / max_lap
    laps["telemetry_coverage_ratio"] = np.where(
        laps["lap_duration_s"] > 0,
        laps["observed_time_s"] / laps["lap_duration_s"],
        np.nan,
    )
    laps["missing_time_share"] = np.where(
        laps["observed_time_s"] > 0,
        laps["missing_time_s"] / laps["observed_time_s"],
        np.nan,
    )
    for threshold in F1PACE_TRAFFIC_THRESHOLDS_SECONDS:
        source_suffix = f"{threshold:.2f}".replace(".", "_")
        target_suffix = f"{threshold:.2f}".replace(".", "_")
        laps[f"traffic_ratio_{target_suffix}s"] = np.where(
            laps["observed_time_s"] > 0,
            laps[f"traffic_time_{source_suffix}s"] / laps["observed_time_s"],
            np.nan,
        )
    laps["traffic_lap_2s_33pct"] = (
        laps["traffic_ratio_2_00s"] > (1.0 / 3.0)
    )
    laps["pace_eligible"] = (
        laps["lap"].gt(1)
        & laps["green_or_yellow"]
        & ~laps["pit_boundary_lap"]
        & laps["lap_duration_s"].notna()
    )
    laps["position_scaled"] = (
        pd.to_numeric(laps["position"], errors="coerce").fillna(20.0) / 20.0
    )
    laps["median_speed_scaled"] = (
        pd.to_numeric(laps["median_speed_kph"], errors="coerce").fillna(0.0)
        / 250.0
    )
    return laps.reset_index(drop=True)


def assign_observable_track_cluster(
    median_speed_kph: float,
    corner_density_per_km: float,
    *,
    low_speed_upper: float = 185.0,
    medium_speed_upper: float = 205.0,
    dense_corner_threshold: float = 2.5,
) -> str:
    """使用固定可观测量分箱；名称有意不包含下压力或动力字样。"""

    if median_speed_kph < low_speed_upper:
        speed_band = "speed_low"
    elif median_speed_kph < medium_speed_upper:
        speed_band = "speed_medium"
    else:
        speed_band = "speed_high"
    density_band = (
        "corner_dense"
        if corner_density_per_km >= dense_corner_threshold
        else "corner_sparse"
    )
    return f"{speed_band}__{density_band}"


def event_equal_weights(event_ids: pd.Series) -> np.ndarray:
    counts = event_ids.astype(str).value_counts()
    raw = event_ids.astype(str).map(lambda value: 1.0 / counts[value]).to_numpy(
        dtype=float
    )
    total = float(raw.sum())
    return raw / total if total > 0 else raw


def cap_training_rows(
    frame: pd.DataFrame,
    *,
    maximum_per_event: int,
    target_column: str,
) -> pd.DataFrame:
    """按每场目标分位位置确定性抽样，避免大场次支配训练。"""

    rows: list[pd.DataFrame] = []
    for _, group in frame.groupby("event_id", sort=True):
        ordered = group.sort_values(
            [target_column, "driver", "lap"],
            kind="stable",
        )
        if len(ordered) <= maximum_per_event:
            rows.append(ordered)
            continue
        indices = np.linspace(
            0,
            len(ordered) - 1,
            int(maximum_per_event),
        ).round().astype(int)
        rows.append(ordered.iloc[np.unique(indices)])
    if not rows:
        return frame.iloc[0:0].copy()
    return pd.concat(rows, ignore_index=True)


@dataclass
class HuberFit:
    status: str
    predictions: np.ndarray
    training_rows: int
    training_events: int
    training_kish_ess: float
    coefficients: list[float]
    intercept: float | None
    scale: float | None
    scaler_mean: list[float]
    scaler_scale: list[float]
    reason: str | None = None


def fit_huber_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    target_column: str,
    sample_weight_column: str,
    epsilon: float,
    alpha: float,
    max_iter: int = 500,
) -> HuberFit:
    required = list(feature_columns) + [target_column, sample_weight_column]
    train_valid = train.loc[
        np.isfinite(train[required].to_numpy(dtype=float)).all(axis=1)
        & train[sample_weight_column].gt(0)
    ]
    test_features = test[list(feature_columns)].to_numpy(dtype=float)
    test_valid = np.isfinite(test_features).all(axis=1)
    predictions = np.full(len(test), np.nan, dtype=float)
    if len(train_valid) < max(40, len(feature_columns) * 4):
        return HuberFit(
            status="NOT_TESTED_INSUFFICIENT_TRAINING_ROWS",
            predictions=predictions,
            training_rows=int(len(train_valid)),
            training_events=int(train_valid["event_id"].nunique()),
            training_kish_ess=kish_effective_sample_size(
                train_valid[sample_weight_column]
            ),
            coefficients=[],
            intercept=None,
            scale=None,
            scaler_mean=[],
            scaler_scale=[],
            reason="training_rows_below_fixed_gate",
        )
    scaler = StandardScaler()
    x_train = scaler.fit_transform(
        train_valid[list(feature_columns)].to_numpy(dtype=float)
    )
    y_train = train_valid[target_column].to_numpy(dtype=float)
    weights = train_valid[sample_weight_column].to_numpy(dtype=float)
    model = HuberRegressor(
        epsilon=float(epsilon),
        alpha=float(alpha),
        max_iter=int(max_iter),
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            model.fit(x_train, y_train, sample_weight=weights)
    except (ValueError, FloatingPointError, ConvergenceWarning) as exc:
        return HuberFit(
            status="FAIL_MODEL_FIT",
            predictions=predictions,
            training_rows=int(len(train_valid)),
            training_events=int(train_valid["event_id"].nunique()),
            training_kish_ess=kish_effective_sample_size(weights),
            coefficients=[],
            intercept=None,
            scale=None,
            scaler_mean=scaler.mean_.astype(float).tolist(),
            scaler_scale=scaler.scale_.astype(float).tolist(),
            reason=f"{type(exc).__name__}:{exc}",
        )
    if test_valid.any():
        predictions[test_valid] = model.predict(
            scaler.transform(test_features[test_valid])
        )
    return HuberFit(
        status="PASS",
        predictions=predictions,
        training_rows=int(len(train_valid)),
        training_events=int(train_valid["event_id"].nunique()),
        training_kish_ess=kish_effective_sample_size(weights),
        coefficients=model.coef_.astype(float).tolist(),
        intercept=float(model.intercept_),
        scale=float(model.scale_),
        scaler_mean=scaler.mean_.astype(float).tolist(),
        scaler_scale=scaler.scale_.astype(float).tolist(),
    )


def prediction_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
    event_ids: Sequence[str],
    *,
    classification_threshold: float | None = None,
) -> dict[str, Any]:
    truth = np.asarray(actual, dtype=float)
    estimate = np.asarray(predicted, dtype=float)
    events = pd.Series(event_ids, dtype=str)
    target_rows = np.isfinite(truth)
    valid = target_rows & np.isfinite(estimate)
    if not valid.any():
        return {
            "status": "NOT_TESTED_NO_PREDICTIONS",
            "target_rows": int(target_rows.sum()),
            "predicted_rows": 0,
            "coverage": 0.0,
            "mae": None,
            "p90_abs_error": None,
            "max_abs_error": None,
            "bias": None,
            "direction_accuracy": None,
            "kish_ess": 0.0,
        }
    weights = event_equal_weights(events[valid])
    errors = estimate[valid] - truth[valid]
    absolute = np.abs(errors)
    direction_accuracy = None
    if classification_threshold is not None:
        direction_accuracy = float(
            np.average(
                (estimate[valid] > classification_threshold)
                == (truth[valid] > classification_threshold),
                weights=weights,
            )
        )
    return {
        "status": "PASS",
        "target_rows": int(target_rows.sum()),
        "predicted_rows": int(valid.sum()),
        "coverage": float(valid.sum() / max(1, target_rows.sum())),
        "mae": float(np.average(absolute, weights=weights)),
        "p90_abs_error": weighted_quantile(absolute, weights, 0.90),
        "max_abs_error": float(np.max(absolute)),
        "bias": float(np.average(errors, weights=weights)),
        "direction_accuracy": direction_accuracy,
        "kish_ess": kish_effective_sample_size(weights),
    }


def prepare_deltadata_model_frame(points: pd.DataFrame) -> pd.DataFrame:
    """为 DeltaData 条件修正模型生成只含可观测输入的矩阵。"""

    frame = points.copy()
    numeric = (
        "lap",
        "tyre_age_laps",
        "raw_lap_duration_s",
        "adjusted_pace_s",
        "traffic_ratio_2_00s",
        "position",
        "gap_seconds_proxy",
        "analysis_weight",
        "lap_fraction",
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["target_adjustment_s"] = (
        frame["adjusted_pace_s"] - frame["raw_lap_duration_s"]
    )
    frame["lap_fraction_sq"] = np.square(frame["lap_fraction"])
    frame["tyre_age_scaled"] = frame["tyre_age_laps"] / 50.0
    frame["tyre_age_sq_scaled"] = np.square(frame["tyre_age_scaled"])
    frame["position_scaled"] = frame["position"].fillna(20.0) / 20.0
    frame["gap_seconds_proxy_clipped"] = frame["gap_seconds_proxy"].clip(
        lower=0.0,
        upper=5.0,
    ).fillna(5.0) / 5.0
    event_median = frame.groupby("event_id", sort=False)[
        "raw_lap_duration_s"
    ].transform("median")
    frame["raw_lap_event_centered_s"] = (
        frame["raw_lap_duration_s"] - event_median
    ).clip(-20.0, 20.0)
    frame["public_fuel_correction_s"] = 0.032 * (frame["lap"] - 1.0)
    compound = frame["compound"].fillna("UNKNOWN").astype(str).str.upper()
    frame["compound_soft"] = compound.eq("SOFT").astype(float)
    frame["compound_medium"] = compound.eq("MEDIUM").astype(float)
    frame["compound_hard"] = compound.eq("HARD").astype(float)
    frame["compound_intermediate_or_wet"] = compound.isin(
        ["INTERMEDIATE", "WET"]
    ).astype(float)
    frame["non_green"] = (~frame["all_green"].fillna(False)).astype(float)
    frame["pit_boundary"] = frame["pit_boundary_proxy"].fillna(False).astype(
        float
    )
    frame["low_quality"] = (~frame["quality_ok"].fillna(False)).astype(float)
    base_weight = frame["analysis_weight"].fillna(0.0).clip(lower=0.0)
    frame["model_weight"] = (
        base_weight
        * np.square(
            1.0 - frame["traffic_ratio_2_00s"].fillna(1.0).clip(0.0, 1.0)
        )
    )
    frame["clean_air_candidate"] = (
        frame["traffic_ratio_2_00s"].le(0.20)
        & frame["traffic_ratio_2_00s"].notna()
        & frame["used_for_primary_fit"].fillna(False)
        & frame["quality_ok"].fillna(False)
        & frame["all_green"].fillna(False)
        & ~frame["pit_boundary_proxy"].fillna(False)
        & frame["lap"].gt(1)
        & frame["target_adjustment_s"].notna()
        & frame["model_weight"].gt(0)
    )
    return frame


def resample_track_profile(
    profile: Mapping[str, Sequence[float]],
    *,
    axis_intervals: int,
    extra_smoothing_sigma_fraction: float = 0.0,
) -> dict[str, np.ndarray]:
    source_grid = np.asarray(profile["grid"], dtype=float)
    grid = np.linspace(0.0, 1.0, int(axis_intervals) + 1)
    result: dict[str, np.ndarray] = {"grid": grid}
    for key, values in profile.items():
        if key == "grid":
            continue
        source = np.asarray(values, dtype=float)
        interpolated = np.interp(grid, source_grid, source)
        if extra_smoothing_sigma_fraction > 0:
            sigma = max(
                0.0,
                float(axis_intervals) * float(extra_smoothing_sigma_fraction),
            )
            interpolated = gaussian_filter1d(
                interpolated,
                sigma=sigma,
                mode="wrap",
            )
        result[key] = interpolated
    return result


def match_corner_proxies(
    reference: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    *,
    maximum_drift_fraction: float = 0.015,
) -> dict[str, Any]:
    candidate_centres = [
        float(row["peak_lateral_rel_distance"]) for row in candidate
    ]
    used: set[int] = set()
    drifts: list[float] = []
    for row in reference:
        centre = float(row["peak_lateral_rel_distance"])
        choices = [
            (abs(value - centre), index)
            for index, value in enumerate(candidate_centres)
            if index not in used
        ]
        if not choices:
            continue
        drift, selected = min(choices)
        if drift <= maximum_drift_fraction:
            used.add(selected)
            drifts.append(float(drift))
    reference_count = len(reference)
    return {
        "reference_corner_count": reference_count,
        "candidate_corner_count": len(candidate),
        "matched_corners": len(drifts),
        "corner_match_share": (
            float(len(drifts) / reference_count) if reference_count else None
        ),
        "corner_count_difference": abs(len(candidate) - reference_count),
        "peak_mae_fraction": float(np.mean(drifts)) if drifts else None,
        "peak_p90_fraction": (
            float(np.quantile(drifts, 0.90)) if drifts else None
        ),
        "peak_max_error_fraction": float(max(drifts)) if drifts else None,
        "endpoint_start_error": 0.0,
        "endpoint_finish_error": 0.0,
    }


def evaluate_segmentation_strategy(
    reference_profile: Mapping[str, Sequence[float]],
    strategy: Mapping[str, Any],
) -> dict[str, Any]:
    reference_corners = detect_dynamic_corner_proxies(reference_profile)
    candidate_profile = resample_track_profile(
        reference_profile,
        axis_intervals=int(strategy["axis_intervals"]),
        extra_smoothing_sigma_fraction=float(
            strategy.get("extra_smoothing_sigma_fraction", 0.0)
        ),
    )
    candidate_corners = detect_dynamic_corner_proxies(candidate_profile)
    candidate_straights = build_dynamic_straight_proxies(
        candidate_corners,
        candidate_profile,
    )
    metrics = match_corner_proxies(reference_corners, candidate_corners)
    reconstructed_speed = np.interp(
        np.asarray(reference_profile["grid"], dtype=float),
        candidate_profile["grid"],
        candidate_profile["speed_kph"],
    )
    reference_speed = np.asarray(reference_profile["speed_kph"], dtype=float)
    metrics.update(
        {
            "strategy_id": str(strategy["strategy_id"]),
            "axis_intervals": int(strategy["axis_intervals"]),
            "extra_smoothing_sigma_fraction": float(
                strategy.get("extra_smoothing_sigma_fraction", 0.0)
            ),
            "straight_proxy_count": len(candidate_straights),
            "speed_profile_mae_kph": float(
                np.mean(np.abs(reconstructed_speed - reference_speed))
            ),
            "status": (
                "PASS"
                if metrics["corner_match_share"] is not None
                and metrics["corner_match_share"] >= 0.8
                and metrics["corner_count_difference"] <= 2
                else "FAIL"
            ),
        }
    )
    return metrics


def select_segmentation_strategy(
    training_event_ids: Sequence[str],
    profiles: Mapping[str, Mapping[str, Sequence[float]]],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """只用外层训练事件选择策略，按失败数、尾误差、漏检和资源量排序。"""

    rows: list[dict[str, Any]] = []
    for strategy in candidates:
        event_metrics = [
            evaluate_segmentation_strategy(profiles[event_id], strategy)
            for event_id in training_event_ids
            if event_id in profiles
        ]
        if not event_metrics:
            continue
        p90_values = [
            row["peak_p90_fraction"]
            for row in event_metrics
            if row["peak_p90_fraction"] is not None
        ]
        rows.append(
            {
                "strategy_id": strategy["strategy_id"],
                "axis_intervals": int(strategy["axis_intervals"]),
                "extra_smoothing_sigma_fraction": float(
                    strategy.get("extra_smoothing_sigma_fraction", 0.0)
                ),
                "training_events": len(event_metrics),
                "failed_events": sum(
                    row["status"] != "PASS" for row in event_metrics
                ),
                "worst_peak_error_fraction": max(
                    (
                        row["peak_max_error_fraction"]
                        for row in event_metrics
                        if row["peak_max_error_fraction"] is not None
                    ),
                    default=float("inf"),
                ),
                "mean_p90_peak_error_fraction": (
                    float(np.mean(p90_values)) if p90_values else None
                ),
                "mean_corner_count_difference": float(
                    np.mean(
                        [
                            row["corner_count_difference"]
                            for row in event_metrics
                        ]
                    )
                ),
            }
        )
    if not rows:
        return None, []
    ordered = sorted(
        rows,
        key=lambda row: (
            row["failed_events"],
            row["worst_peak_error_fraction"],
            (
                row["mean_p90_peak_error_fraction"]
                if row["mean_p90_peak_error_fraction"] is not None
                else float("inf")
            ),
            row["mean_corner_count_difference"],
            row["axis_intervals"],
        ),
    )
    selected_id = ordered[0]["strategy_id"]
    selected = next(
        dict(row) for row in candidates if row["strategy_id"] == selected_id
    )
    return selected, rows


def observable_layout_discontinuity(
    event_profiles: Sequence[tuple[int, Mapping[str, Sequence[float]]]],
    *,
    minimum_corner_match_share: float,
    maximum_corner_count_difference: int,
    minimum_speed_profile_correlation: float,
) -> dict[str, Any]:
    """比较同名赛道跨年可观测轮廓；失败只表示代理不连续，不冒充官方布局结论。"""

    rows: list[dict[str, Any]] = []
    ordered = sorted(event_profiles, key=lambda item: item[0])
    for left_index in range(len(ordered)):
        for right_index in range(left_index + 1, len(ordered)):
            left_year, left_profile = ordered[left_index]
            right_year, right_profile = ordered[right_index]
            left_corners = detect_dynamic_corner_proxies(left_profile)
            right_corners = detect_dynamic_corner_proxies(right_profile)
            corner_metrics = match_corner_proxies(left_corners, right_corners)
            common_grid = np.linspace(0.0, 1.0, 1201)
            left_speed = np.interp(
                common_grid,
                np.asarray(left_profile["grid"], dtype=float),
                np.asarray(left_profile["speed_kph"], dtype=float),
            )
            right_speed = np.interp(
                common_grid,
                np.asarray(right_profile["grid"], dtype=float),
                np.asarray(right_profile["speed_kph"], dtype=float),
            )
            correlation = float(np.corrcoef(left_speed, right_speed)[0, 1])
            failed = []
            if (
                corner_metrics["corner_match_share"] is None
                or corner_metrics["corner_match_share"]
                < minimum_corner_match_share
            ):
                failed.append("corner_match_share_below_gate")
            if (
                corner_metrics["corner_count_difference"]
                > maximum_corner_count_difference
            ):
                failed.append("corner_count_difference_above_gate")
            if (
                not math.isfinite(correlation)
                or correlation < minimum_speed_profile_correlation
            ):
                failed.append("speed_profile_correlation_below_gate")
            rows.append(
                {
                    "left_year": left_year,
                    "right_year": right_year,
                    "corner_match_share": corner_metrics[
                        "corner_match_share"
                    ],
                    "corner_count_difference": corner_metrics[
                        "corner_count_difference"
                    ],
                    "speed_profile_correlation": correlation,
                    "status": "PASS" if not failed else "BOUNDARY",
                    "gate_failures": failed,
                }
            )
    return {
        "status": (
            "OBSERVABLE_LAYOUT_DISCONTINUITY_PROXY"
            if any(row["status"] == "BOUNDARY" for row in rows)
            else "OBSERVABLE_PROFILE_STABLE"
        ),
        "track_model_allowed": bool(rows)
        and all(row["status"] == "PASS" for row in rows),
        "comparisons": rows,
        "interpretation": (
            "observable_geometry_or_sampling_discontinuity_proxy_"
            "not_official_layout_change"
        ),
    }


def candidate_publication_decision(
    universal: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    fold_improvement_share: float,
    test_events: int,
    training_kish_ess: float,
    gate: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    """在同一量纲内逐门判断候选；不生成跨任务总分。"""

    failures: list[str] = []
    if test_events < int(gate["minimum_test_events"]):
        failures.append("test_events_below_gate")
    if training_kish_ess < float(gate["minimum_kish_ess"]):
        failures.append("training_kish_ess_below_gate")
    if universal.get("mae") is None or candidate.get("mae") is None:
        failures.append("mae_not_testable")
    else:
        required = float(gate["minimum_relative_mae_improvement"])
        if candidate["mae"] > universal["mae"] * (1.0 - required):
            failures.append("mae_improvement_below_gate")
    if (
        universal.get("p90_abs_error") is None
        or candidate.get("p90_abs_error") is None
    ):
        failures.append("p90_not_testable")
    elif candidate["p90_abs_error"] > universal["p90_abs_error"] * (
        1.0 + float(gate["maximum_relative_p90_degradation"])
    ):
        failures.append("p90_degradation_above_gate")
    if (
        universal.get("max_abs_error") is None
        or candidate.get("max_abs_error") is None
    ):
        failures.append("max_error_not_testable")
    elif candidate["max_abs_error"] > universal["max_abs_error"] * (
        1.0 + float(gate["maximum_relative_max_error_degradation"])
    ):
        failures.append("max_error_degradation_above_gate")
    if fold_improvement_share < float(
        gate["minimum_fold_improvement_share"]
    ):
        failures.append("fold_improvement_share_below_gate")
    if candidate.get("coverage", 0.0) < universal.get("coverage", 0.0) - float(
        gate["maximum_coverage_loss"]
    ):
        failures.append("coverage_loss_above_gate")
    universal_direction = universal.get("direction_accuracy")
    candidate_direction = candidate.get("direction_accuracy")
    if (
        universal_direction is not None
        and candidate_direction is not None
        and candidate_direction
        < universal_direction - float(gate["maximum_direction_accuracy_loss"])
    ):
        failures.append("direction_accuracy_loss_above_gate")
    return not failures, failures

