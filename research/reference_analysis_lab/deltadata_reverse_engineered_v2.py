"""DeltaData clean-air pace 的可审计方法等价倒推。

公开帖子只披露了 clean air、燃油修正、代表圈人工复核、尽量相同配方与
相近赛道阶段等原则，没有公开精确阈值、逐圈人工标签或拟合代码。本模块把
这些原则冻结成一套可复算实现：

1. 从逐点遥测重建同一赛道位置的物理前车时间差；
2. 把一圈至少 80% 可观测时间不在前车 2 秒内定义为 clean-air 候选；
3. 用全场 Huber 条件模型替代不可获得的逐圈人工复核；
4. 在相同条件分布上标准化车手配速，并单独进行同队可比性门控；
5. 燃油仅作为具名的线性折损情景，不声称识别真实油量。

这里的输出是 ``method-equivalent``，不能被描述为原作者源码的一比一复现。
"""

from __future__ import annotations

import math
import warnings
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import HuberRegressor

from reference_analysis_lab.f1pace_pilot import build_lap_table
from reference_analysis_lab.f1pace_reverse_engineered_v2 import (
    build_time_headway_features,
)


PUBLIC_FUEL_DISCOUNT_S_PER_LAP = 0.032
PUBLIC_CLEAN_AIR_SHARE_MIN = 0.80
PUBLIC_TRAFFIC_RATIO_MAX = 1.0 - PUBLIC_CLEAN_AIR_SHARE_MIN
HUBER_EPSILON = 1.5
HUBER_ALPHA = 0.01
MIN_DRIVER_MODEL_LAPS = 3
MIN_DRIVER_VISUAL_LAPS = 3
MIN_AUDITED_REPRESENTATIVE_LAPS = 6
MIN_AUDITED_KISH_ESS = 6.0
MIN_COMMON_TYRE_AGE_SPAN_LAPS = 5.0
MIN_PHASE_OVERLAP_RATIO = 0.50
MAX_TRAFFIC_MEAN_GAP = 0.10

FUEL_SCENARIOS_S_PER_LAP: Mapping[str, float] = {
    "public_2026": PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
    "v17_low": 0.031034,
    "v17_base": 0.051724,
    "v17_high": 0.075862,
}
TRAFFIC_SENSITIVITY_THRESHOLDS: tuple[float, ...] = (0.10, 0.20, 1.0 / 3.0)

NUMERIC_FEATURES: tuple[str, ...] = (
    "tyre_age",
    "tyre_age_sq",
    "lap_fraction",
    "lap_fraction_sq",
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_number(value: Any, digits: int = 8) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for key, value in record.items():
            if value is None or (
                isinstance(value, (float, np.floating)) and not math.isfinite(float(value))
            ):
                row[key] = None
            elif isinstance(value, np.generic):
                row[key] = value.item()
            else:
                row[key] = value
        rows.append(row)
    return rows


def kish_effective_sample_size(weights: Iterable[float]) -> float:
    """计算非负软权重的 Kish 有效样本量。"""

    values = np.asarray(
        [max(_finite(value) or 0.0, 0.0) for value in weights],
        dtype=float,
    )
    denominator = float(np.square(values).sum())
    if denominator <= 0.0:
        return 0.0
    return float(values.sum() ** 2 / denominator)


def weighted_mean(values: Iterable[float], weights: Iterable[float]) -> float | None:
    """计算非负权重均值；没有有效支持时 fail-close。"""

    value_array = np.asarray([_finite(value) for value in values], dtype=float)
    weight_array = np.asarray(
        [max(_finite(value) or 0.0, 0.0) for value in weights],
        dtype=float,
    )
    finite = np.isfinite(value_array) & np.isfinite(weight_array) & (weight_array > 0)
    if not finite.any() or float(weight_array[finite].sum()) <= 0.0:
        return None
    return float(np.average(value_array[finite], weights=weight_array[finite]))


def _quantile(values: Sequence[float], probability: float) -> float | None:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return None
    return float(np.quantile(array, probability))


def prepare_clean_air_laps(
    points: pd.DataFrame,
    *,
    traffic_features: pd.DataFrame | None = None,
    traffic_ratio_max: float = PUBLIC_TRAFFIC_RATIO_MAX,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """构造逐圈候选集，并给每个排除原因保留互斥账本。

    ``audited_traffic_ratio`` 来自逐点、同位置、时间权重的物理前车重建，
    不是圈级 ``median gap``。领跑圈允许高 missing，但 missing 仍保留在
    样本披露中，不能被解释成确定的 clean-air 时间。
    """

    laps = build_lap_table(points)
    features = (
        build_time_headway_features(points)
        if traffic_features is None
        else traffic_features.copy()
    )
    required_features = {
        "driver",
        "lap",
        "audited_traffic_ratio",
        "missing",
        "observed_time_s",
        "telemetry_samples",
    }
    missing_features = sorted(required_features.difference(features.columns))
    if missing_features:
        raise ValueError(
            "交通特征缺少字段：" + ", ".join(missing_features)
        )

    selected_feature_columns = [
        "driver",
        "lap",
        "audited_traffic_ratio",
        "missing",
        "observed_time_s",
        "telemetry_samples",
        "circuit_length_estimate_m",
        "audited_definition",
    ]
    available_feature_columns = [
        column for column in selected_feature_columns if column in features.columns
    ]
    frame = laps.merge(
        features[available_feature_columns],
        left_on=["driver_acronym", "lap"],
        right_on=["driver", "lap"],
        how="left",
        validate="one_to_one",
    )
    frame["driver"] = frame["driver_acronym"].astype(str)
    frame["lap_time_s"] = pd.to_numeric(frame["lap_duration"], errors="coerce")
    frame["tyre_age"] = pd.to_numeric(frame["tyre_life"], errors="coerce")
    frame["tyre_age_sq"] = frame["tyre_age"] ** 2
    maximum_lap = max(
        1.0,
        float(pd.to_numeric(frame["lap"], errors="coerce").max()),
    )
    frame["lap_fraction"] = pd.to_numeric(frame["lap"], errors="coerce") / maximum_lap
    frame["lap_fraction_sq"] = frame["lap_fraction"] ** 2
    frame["traffic_ratio"] = pd.to_numeric(
        frame["audited_traffic_ratio"], errors="coerce"
    )
    frame["clean_air_share"] = 1.0 - frame["traffic_ratio"]
    frame["telemetry_coverage"] = (
        pd.to_numeric(frame["observed_time_s"], errors="coerce")
        / frame["lap_time_s"]
    ).clip(lower=0.0, upper=1.0)
    frame["candidate_weight"] = (
        frame["clean_air_share"].clip(lower=0.0, upper=1.0) ** 2
        * frame["telemetry_coverage"].fillna(0.0)
    )

    compound_text = frame["compound"].fillna("").astype(str).str.upper()
    finite_pace = (
        frame["lap_time_s"].notna()
        & frame["lap_time_s"].between(30.0, 300.0)
        & frame["tyre_age"].notna()
    )
    clean_threshold = frame["traffic_ratio"].le(float(traffic_ratio_max))
    conditions = [
        frame["first_lap"],
        frame["pit_boundary_lap"],
        ~frame["green_or_yellow"],
        frame["deleted"].astype(bool),
        ~frame["is_accurate"].astype(bool),
        ~finite_pace,
        compound_text.isin({"", "NONE", "NAN", "UNKNOWN"}),
        frame["traffic_ratio"].isna(),
        ~clean_threshold,
    ]
    labels = [
        "first_lap",
        "pit_boundary_lap",
        "non_green_or_yellow",
        "deleted_lap",
        "inaccurate_lap",
        "missing_or_implausible_pace",
        "missing_compound",
        "traffic_not_reconstructable",
        "traffic_ratio_above_threshold",
    ]
    frame["selection_disposition"] = np.select(
        conditions,
        labels,
        default="clean_air_candidate",
    )
    frame["clean_air_candidate"] = frame["selection_disposition"].eq(
        "clean_air_candidate"
    )
    frame["compound"] = compound_text
    frame = frame.sort_values(["driver", "lap"], kind="stable").reset_index(drop=True)

    disposition_counts = Counter(frame["selection_disposition"].astype(str))
    candidates = frame.loc[frame["clean_air_candidate"]]
    ledger = {
        "all_laps": int(len(frame)),
        "clean_air_candidates": int(len(candidates)),
        "excluded_laps": int(len(frame) - len(candidates)),
        "selection_disposition_counts": dict(sorted(disposition_counts.items())),
        "traffic_ratio_max": float(traffic_ratio_max),
        "clean_air_share_min": float(1.0 - traffic_ratio_max),
        "traffic_definition": (
            "nearest_physical_car_same_track_location_crossing_headway_le_2s"
        ),
        "lap_time_weighted_traffic": True,
        "median_gap_used_as_ratio": False,
        "candidate_kish_ess": kish_effective_sample_size(
            candidates["candidate_weight"]
        ),
        "drivers": int(frame["driver"].nunique()),
        "teams": int(frame["team"].nunique()),
        "maximum_lap": int(maximum_lap),
    }
    return frame, ledger


@dataclass(frozen=True)
class DesignIdentity:
    drivers: tuple[str, ...]
    compounds: tuple[str, ...]
    numeric_fill: Mapping[str, float]
    numeric_mean: Mapping[str, float]
    numeric_scale: Mapping[str, float]
    feature_names: tuple[str, ...]


def _fit_design_identity(frame: pd.DataFrame) -> DesignIdentity:
    drivers = tuple(sorted(frame["driver"].astype(str).unique()))
    compounds = tuple(sorted(frame["compound"].astype(str).unique()))
    if len(drivers) < 2:
        raise ValueError("条件标准化至少需要两名车手")
    if not compounds:
        raise ValueError("条件标准化缺少轮胎配方")

    fill: dict[str, float] = {}
    mean: dict[str, float] = {}
    scale: dict[str, float] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        finite = values[np.isfinite(values)]
        if not len(finite):
            raise ValueError(f"条件标准化数值字段无有效值：{column}")
        fill[column] = float(np.median(finite))
        imputed = np.where(np.isfinite(values), values, fill[column])
        mean[column] = float(imputed.mean())
        standard_deviation = float(imputed.std(ddof=0))
        scale[column] = standard_deviation if standard_deviation > 1e-12 else 1.0

    feature_names = list(NUMERIC_FEATURES)
    feature_names.extend(f"driver={driver}" for driver in drivers[1:])
    feature_names.extend(f"compound={compound}" for compound in compounds[1:])
    feature_names.extend(
        f"tyre_age_x_compound={compound}" for compound in compounds[1:]
    )
    return DesignIdentity(
        drivers=drivers,
        compounds=compounds,
        numeric_fill=fill,
        numeric_mean=mean,
        numeric_scale=scale,
        feature_names=tuple(feature_names),
    )


def _design_matrix(
    frame: pd.DataFrame,
    identity: DesignIdentity,
) -> np.ndarray:
    parts: list[np.ndarray] = []
    numeric_z: dict[str, np.ndarray] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        values = np.where(
            np.isfinite(values),
            values,
            float(identity.numeric_fill[column]),
        )
        standardized = (
            values - float(identity.numeric_mean[column])
        ) / float(identity.numeric_scale[column])
        numeric_z[column] = standardized
        parts.append(standardized[:, None])

    driver = frame["driver"].astype(str).to_numpy()
    compound = frame["compound"].astype(str).to_numpy()
    for value in identity.drivers[1:]:
        parts.append((driver == value).astype(float)[:, None])
    for value in identity.compounds[1:]:
        parts.append((compound == value).astype(float)[:, None])
    tyre_age_z = numeric_z["tyre_age"]
    for value in identity.compounds[1:]:
        parts.append(((compound == value).astype(float) * tyre_age_z)[:, None])
    return np.hstack(parts)


def _serialize_identity(identity: DesignIdentity) -> dict[str, Any]:
    return {
        "drivers": list(identity.drivers),
        "compounds": list(identity.compounds),
        "numeric_features": list(NUMERIC_FEATURES),
        "numeric_fill": dict(identity.numeric_fill),
        "numeric_mean": dict(identity.numeric_mean),
        "numeric_scale": dict(identity.numeric_scale),
        "feature_names": list(identity.feature_names),
        "baseline_driver": identity.drivers[0],
        "baseline_compound": identity.compounds[0],
    }


def _deserialize_identity(payload: Mapping[str, Any]) -> DesignIdentity:
    return DesignIdentity(
        drivers=tuple(str(value) for value in payload["drivers"]),
        compounds=tuple(str(value) for value in payload["compounds"]),
        numeric_fill={
            str(key): float(value)
            for key, value in payload["numeric_fill"].items()
        },
        numeric_mean={
            str(key): float(value)
            for key, value in payload["numeric_mean"].items()
        },
        numeric_scale={
            str(key): float(value)
            for key, value in payload["numeric_scale"].items()
        },
        feature_names=tuple(str(value) for value in payload["feature_names"]),
    )


def fit_robust_condition_model(
    lap_frame: pd.DataFrame,
    *,
    fuel_discount_s_per_lap: float = PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """拟合可序列化的 Huber 条件模型并标记自动代表圈。

    Huber 的 ``outliers_`` 只用作“自动复核等价”标签。模型本身仍是鲁棒
    M-estimator，不把参考图中的 H2H 数值作为训练目标。
    """

    candidates = lap_frame.loc[lap_frame["clean_air_candidate"]].copy()
    if len(candidates) < 30:
        raise ValueError(f"clean-air 候选不足：{len(candidates)} < 30")
    counts = candidates.groupby("driver", sort=True).size()
    supported_drivers = counts.loc[counts.ge(MIN_DRIVER_MODEL_LAPS)].index
    candidates = candidates.loc[
        candidates["driver"].isin(supported_drivers)
    ].reset_index(drop=True)
    if candidates["driver"].nunique() < 2:
        raise ValueError("满足最低候选圈门槛的车手不足两名")

    identity = _fit_design_identity(candidates)
    x = _design_matrix(candidates, identity)
    y = (
        candidates["lap_time_s"].to_numpy(dtype=float)
        + float(fuel_discount_s_per_lap)
        * (candidates["lap"].to_numpy(dtype=float) - 1.0)
    )
    weights = candidates["candidate_weight"].to_numpy(dtype=float)
    minimum_rows = x.shape[1] + 5
    if len(candidates) < minimum_rows:
        raise ValueError(
            f"条件模型自由度不足：{len(candidates)} rows < {minimum_rows}"
        )

    estimator = HuberRegressor(
        epsilon=HUBER_EPSILON,
        alpha=HUBER_ALPHA,
        max_iter=5000,
        tol=1e-7,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        estimator.fit(x, y, sample_weight=weights)
    convergence_warnings = [
        str(item.message)
        for item in caught
        if issubclass(item.category, ConvergenceWarning)
    ]
    prediction = estimator.predict(x)
    residual = y - prediction
    candidates["fuel_adjusted_lap_time_s"] = y
    candidates["condition_prediction_s"] = prediction
    candidates["condition_residual_s"] = residual
    candidates["representative_lap"] = ~np.asarray(
        estimator.outliers_, dtype=bool
    )
    candidates["robust_weight_role"] = np.where(
        candidates["representative_lap"],
        "huber_inlier",
        "huber_downweighted_outlier",
    )

    representative = candidates.loc[candidates["representative_lap"]]
    model = {
        "model_type": "huber_additive_condition_standardizer",
        "epsilon": HUBER_EPSILON,
        "alpha": HUBER_ALPHA,
        "max_iter": 5000,
        "fuel_discount_s_per_lap": float(fuel_discount_s_per_lap),
        "fuel_role": "named_linear_scenario_not_true_fuel_reconstruction",
        "identity": _serialize_identity(identity),
        "coefficients": estimator.coef_.tolist(),
        "intercept": float(estimator.intercept_),
        "scale_s": float(estimator.scale_),
        "iterations": int(estimator.n_iter_),
        "convergence_warnings": convergence_warnings,
        "candidate_rows": int(len(candidates)),
        "representative_rows": int(len(representative)),
        "downweighted_outlier_rows": int(
            (~candidates["representative_lap"]).sum()
        ),
        "representative_share": float(candidates["representative_lap"].mean()),
        "candidate_weight_kish_ess": kish_effective_sample_size(weights),
        "representative_weight_kish_ess": kish_effective_sample_size(
            representative["candidate_weight"]
        ),
        "fit_metrics": {
            "all_mae_s": float(np.mean(np.abs(residual))),
            "all_rmse_s": float(np.sqrt(np.mean(np.square(residual)))),
            "representative_mae_s": float(
                np.mean(
                    np.abs(
                        representative["condition_residual_s"].to_numpy(dtype=float)
                    )
                )
            )
            if len(representative)
            else None,
            "representative_rmse_s": float(
                np.sqrt(
                    np.mean(
                        np.square(
                            representative["condition_residual_s"].to_numpy(
                                dtype=float
                            )
                        )
                    )
                )
            )
            if len(representative)
            else None,
        },
        "manual_review_claim_allowed": False,
        "selection_role": (
            "automatic_robust_representative_lap_equivalent_not_creator_manual_labels"
        ),
    }
    return model, candidates


def predict_condition_model(
    frame: pd.DataFrame,
    model: Mapping[str, Any],
) -> np.ndarray:
    """从 JSON 可序列化参数复算条件模型。"""

    identity = _deserialize_identity(model["identity"])
    unknown_drivers = sorted(
        set(frame["driver"].astype(str)).difference(identity.drivers)
    )
    unknown_compounds = sorted(
        set(frame["compound"].astype(str)).difference(identity.compounds)
    )
    if unknown_drivers:
        raise ValueError("模型未见车手：" + ", ".join(unknown_drivers))
    if unknown_compounds:
        raise ValueError("模型未见配方：" + ", ".join(unknown_compounds))
    matrix = _design_matrix(frame, identity)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    return matrix @ coefficients + float(model["intercept"])


def _driver_sample_summary(
    candidates: pd.DataFrame,
    driver: str,
) -> dict[str, Any]:
    rows = candidates.loc[candidates["driver"].eq(driver)].copy()
    representative = rows.loc[rows["representative_lap"]]
    weights = representative["candidate_weight"].to_numpy(dtype=float)
    traffic = representative["traffic_ratio"].to_numpy(dtype=float)
    missing = pd.to_numeric(
        representative["missing"], errors="coerce"
    ).to_numpy(dtype=float)
    return {
        "candidate_laps": int(len(rows)),
        "representative_laps": int(len(representative)),
        "downweighted_outliers": int((~rows["representative_lap"]).sum()),
        "kish_ess": kish_effective_sample_size(weights),
        "compounds": sorted(representative["compound"].astype(str).unique()),
        "stints": int(representative["stint"].nunique()),
        "tyre_age_range": [
            _safe_number(representative["tyre_age"].min(), 3),
            _safe_number(representative["tyre_age"].max(), 3),
        ]
        if len(representative)
        else [None, None],
        "lap_fraction_range": [
            _safe_number(representative["lap_fraction"].min(), 5),
            _safe_number(representative["lap_fraction"].max(), 5),
        ]
        if len(representative)
        else [None, None],
        "traffic_distribution": {
            "mean": _safe_number(weighted_mean(traffic, weights), 8),
            "median": _safe_number(_quantile(traffic, 0.50), 8),
            "p90": _safe_number(_quantile(traffic, 0.90), 8),
            "missing_mean": _safe_number(weighted_mean(missing, weights), 8),
        },
    }


def build_standardized_driver_scores(
    candidates: pd.DataFrame,
    model: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """在同一代表圈条件分布上计算全车手标准化配速。"""

    reference_grid = candidates.loc[candidates["representative_lap"]].copy()
    if reference_grid.empty:
        raise ValueError("没有 Huber 代表圈，不能建立公共条件分布")
    grid_weights = reference_grid["candidate_weight"].to_numpy(dtype=float)
    team_map = (
        candidates.groupby("driver", sort=True)["team"]
        .agg(lambda values: str(values.mode().iloc[0]))
        .to_dict()
    )
    rows: list[dict[str, Any]] = []
    for driver in model["identity"]["drivers"]:
        evaluation = reference_grid.copy()
        evaluation["driver"] = str(driver)
        prediction = predict_condition_model(evaluation, model)
        pace = weighted_mean(prediction, grid_weights)
        sample = _driver_sample_summary(candidates, str(driver))
        status = (
            "PUBLISHABLE_VISUAL_PROXY"
            if sample["representative_laps"] >= MIN_DRIVER_VISUAL_LAPS
            and sample["kish_ess"] >= float(MIN_DRIVER_VISUAL_LAPS)
            else "INSUFFICIENT_SAMPLE"
        )
        rows.append(
            {
                "driver": str(driver),
                "team": team_map.get(str(driver)),
                "standardized_pace_s": _safe_number(pace),
                "sample": sample,
                "status": status,
                "rank_role": (
                    "common_condition_method_equivalent_visual_proxy_not_causal_order"
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            row["standardized_pace_s"]
            if row["standardized_pace_s"] is not None
            else float("inf"),
            row["driver"],
        )
    )
    fastest = next(
        (
            float(row["standardized_pace_s"])
            for row in rows
            if row["status"] == "PUBLISHABLE_VISUAL_PROXY"
            and row["standardized_pace_s"] is not None
        ),
        None,
    )
    for index, row in enumerate(rows, start=1):
        row["visual_rank"] = index
        row["delta_to_fastest_pct"] = (
            _safe_number(
                (
                    float(row["standardized_pace_s"]) - fastest
                )
                / fastest
                * 100.0,
                6,
            )
            if fastest is not None and row["standardized_pace_s"] is not None
            else None
        )
    return rows


def append_unsupported_driver_rows(
    driver_scores: Sequence[Mapping[str, Any]],
    lap_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    """把未达到三圈模型门槛的真实车手显式补回披露表。"""

    output = [dict(row) for row in driver_scores]
    existing = {str(row["driver"]) for row in output}
    for driver, group in lap_frame.groupby("driver", sort=True):
        driver = str(driver)
        if driver in existing:
            continue
        candidates = group.loc[group["clean_air_candidate"]]
        traffic = pd.to_numeric(
            candidates["traffic_ratio"], errors="coerce"
        ).to_numpy(dtype=float)
        weights = pd.to_numeric(
            candidates["candidate_weight"], errors="coerce"
        ).to_numpy(dtype=float)
        team_values = group["team"].dropna().astype(str)
        team = str(team_values.mode().iloc[0]) if len(team_values) else None
        output.append(
            {
                "driver": driver,
                "team": team,
                "standardized_pace_s": None,
                "sample": {
                    "candidate_laps": int(len(candidates)),
                    "representative_laps": 0,
                    "downweighted_outliers": 0,
                    "kish_ess": 0.0,
                    "compounds": sorted(
                        candidates["compound"].astype(str).unique()
                    ),
                    "stints": int(candidates["stint"].nunique()),
                    "tyre_age_range": [
                        _safe_number(candidates["tyre_age"].min(), 3),
                        _safe_number(candidates["tyre_age"].max(), 3),
                    ]
                    if len(candidates)
                    else [None, None],
                    "lap_fraction_range": [
                        _safe_number(candidates["lap_fraction"].min(), 5),
                        _safe_number(candidates["lap_fraction"].max(), 5),
                    ]
                    if len(candidates)
                    else [None, None],
                    "traffic_distribution": {
                        "mean": _safe_number(
                            weighted_mean(traffic, weights), 8
                        ),
                        "median": _safe_number(_quantile(traffic, 0.50), 8),
                        "p90": _safe_number(_quantile(traffic, 0.90), 8),
                        "missing_mean": None,
                    },
                },
                "status": "INSUFFICIENT_CLEAN_AIR_SAMPLE",
                "rank_role": (
                    "explicitly_unranked_below_three_clean_air_candidate_laps"
                ),
                "visual_rank": None,
                "delta_to_fastest_pct": None,
            }
        )
    output.sort(
        key=lambda row: (
            row["standardized_pace_s"] is None,
            row["standardized_pace_s"]
            if row["standardized_pace_s"] is not None
            else float("inf"),
            row["driver"],
        )
    )
    return output


def _range_overlap(
    left: Sequence[float | None],
    right: Sequence[float | None],
) -> tuple[float, float]:
    values = [*left, *right]
    if any(value is None or not math.isfinite(float(value)) for value in values):
        return 0.0, 0.0
    left_low, left_high = float(left[0]), float(left[1])
    right_low, right_high = float(right[0]), float(right[1])
    intersection = max(
        0.0,
        min(left_high, right_high) - max(left_low, right_low),
    )
    union = max(left_high, right_high) - min(left_low, right_low)
    return intersection, intersection / union if union > 0 else 0.0


def _common_tyre_age_support(
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> tuple[list[str], float, list[dict[str, Any]]]:
    common = sorted(
        set(left["compound"].astype(str)).intersection(
            right["compound"].astype(str)
        )
    )
    details: list[dict[str, Any]] = []
    total_span = 0.0
    for compound in common:
        left_values = left.loc[left["compound"].eq(compound), "tyre_age"]
        right_values = right.loc[right["compound"].eq(compound), "tyre_age"]
        lower = max(float(left_values.min()), float(right_values.min()))
        upper = min(float(left_values.max()), float(right_values.max()))
        span = max(0.0, upper - lower)
        total_span += span
        details.append(
            {
                "compound": compound,
                "left_range": [
                    _safe_number(left_values.min(), 3),
                    _safe_number(left_values.max(), 3),
                ],
                "right_range": [
                    _safe_number(right_values.min(), 3),
                    _safe_number(right_values.max(), 3),
                ],
                "overlap_range": [
                    _safe_number(lower, 3),
                    _safe_number(upper, 3),
                ]
                if span > 0
                else None,
                "overlap_span_laps": _safe_number(span, 3),
            }
        )
    return common, total_span, details


def build_teammate_comparisons(
    candidates: pd.DataFrame,
    driver_scores: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """构造视觉 H2H 与独立的 fail-close 审计门。"""

    score_map = {
        str(row["driver"]): float(row["standardized_pace_s"])
        for row in driver_scores
        if row.get("standardized_pace_s") is not None
    }
    sample_map = {
        str(row["driver"]): dict(row["sample"])
        for row in driver_scores
    }
    driver_team = (
        candidates.groupby("driver", sort=True)["team"]
        .agg(lambda values: str(values.mode().iloc[0]))
        .reset_index()
    )
    rows: list[dict[str, Any]] = []
    for team, team_rows in driver_team.groupby("team", sort=True):
        drivers = sorted(team_rows["driver"].astype(str).unique())
        if len(drivers) != 2:
            continue
        left_driver, right_driver = drivers
        left_score = score_map.get(left_driver)
        right_score = score_map.get(right_driver)
        if left_score is None or right_score is None:
            continue
        left = candidates.loc[
            candidates["driver"].eq(left_driver)
            & candidates["representative_lap"]
        ]
        right = candidates.loc[
            candidates["driver"].eq(right_driver)
            & candidates["representative_lap"]
        ]
        left_sample = sample_map[left_driver]
        right_sample = sample_map[right_driver]
        common_compounds, tyre_span, tyre_details = _common_tyre_age_support(
            left, right
        )
        phase_span, phase_overlap = _range_overlap(
            left_sample["lap_fraction_range"],
            right_sample["lap_fraction_range"],
        )
        left_traffic = _finite(
            left_sample["traffic_distribution"]["mean"]
        )
        right_traffic = _finite(
            right_sample["traffic_distribution"]["mean"]
        )
        traffic_gap = (
            abs(left_traffic - right_traffic)
            if left_traffic is not None and right_traffic is not None
            else None
        )

        failures: list[str] = []
        if (
            left_sample["representative_laps"]
            < MIN_AUDITED_REPRESENTATIVE_LAPS
        ):
            failures.append("left_representative_laps_lt_6")
        if (
            right_sample["representative_laps"]
            < MIN_AUDITED_REPRESENTATIVE_LAPS
        ):
            failures.append("right_representative_laps_lt_6")
        if left_sample["kish_ess"] < MIN_AUDITED_KISH_ESS:
            failures.append("left_kish_ess_lt_6")
        if right_sample["kish_ess"] < MIN_AUDITED_KISH_ESS:
            failures.append("right_kish_ess_lt_6")
        if not common_compounds:
            failures.append("no_common_compound")
        if tyre_span < MIN_COMMON_TYRE_AGE_SPAN_LAPS:
            failures.append("common_tyre_age_span_lt_5")
        if phase_overlap < MIN_PHASE_OVERLAP_RATIO:
            failures.append("phase_overlap_ratio_lt_0_50")
        if traffic_gap is None:
            failures.append("traffic_balance_not_available")
        elif traffic_gap > MAX_TRAFFIC_MEAN_GAP:
            failures.append("traffic_mean_gap_gt_0_10")

        visual_delta_pct = (left_score - right_score) / right_score * 100.0
        directly_comparable = not failures
        rows.append(
            {
                "team": str(team),
                "left_driver": left_driver,
                "right_driver": right_driver,
                "visual_left_minus_right_pct": _safe_number(
                    visual_delta_pct, 6
                ),
                "visual_faster_driver": (
                    left_driver if visual_delta_pct < 0 else right_driver
                ),
                "visual_role": (
                    "method_equivalent_common_condition_h2h_lower_pct_is_faster"
                ),
                "audited_status": (
                    "COMPARABLE" if directly_comparable else "AUDIT_ONLY"
                ),
                "audited_left_minus_right_pct": (
                    _safe_number(visual_delta_pct, 6)
                    if directly_comparable
                    else None
                ),
                "gate_failures": failures,
                "left_sample": left_sample,
                "right_sample": right_sample,
                "common_support": {
                    "compounds": common_compounds,
                    "tyre_age_overlap_span_laps": _safe_number(tyre_span, 3),
                    "tyre_age_details": tyre_details,
                    "phase_overlap_span": _safe_number(phase_span, 6),
                    "phase_overlap_ratio": _safe_number(phase_overlap, 6),
                    "traffic_mean_gap": _safe_number(traffic_gap, 8),
                },
            }
        )
    return rows


def append_unmodelled_teammate_comparisons(
    comparisons: Sequence[Mapping[str, Any]],
    lap_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    """确保每个真实双车车队都有 H2H 行，即使只能 fail-close。"""

    output = [dict(row) for row in comparisons]
    represented = {str(row["team"]) for row in output}
    driver_team = (
        lap_frame.groupby("driver", sort=True)["team"]
        .agg(lambda values: str(values.mode().iloc[0]))
        .reset_index()
    )
    for team, group in driver_team.groupby("team", sort=True):
        team = str(team)
        drivers = sorted(group["driver"].astype(str).unique())
        if team in represented or len(drivers) != 2:
            continue
        left_driver, right_driver = drivers
        candidate_counts = {
            driver: int(
                lap_frame.loc[
                    lap_frame["driver"].eq(driver)
                    & lap_frame["clean_air_candidate"]
                ].shape[0]
            )
            for driver in drivers
        }
        output.append(
            {
                "team": team,
                "left_driver": left_driver,
                "right_driver": right_driver,
                "visual_left_minus_right_pct": None,
                "visual_faster_driver": None,
                "visual_role": "NOT_PUBLISHED_INSUFFICIENT_MODEL_SUPPORT",
                "audited_status": "AUDIT_ONLY",
                "audited_left_minus_right_pct": None,
                "gate_failures": [
                    "one_or_both_drivers_below_three_clean_air_candidate_laps"
                ],
                "left_sample": {
                    "candidate_laps": candidate_counts[left_driver],
                    "representative_laps": 0,
                    "kish_ess": 0.0,
                },
                "right_sample": {
                    "candidate_laps": candidate_counts[right_driver],
                    "representative_laps": 0,
                    "kish_ess": 0.0,
                },
                "common_support": {
                    "compounds": [],
                    "tyre_age_overlap_span_laps": None,
                    "tyre_age_details": [],
                    "phase_overlap_span": None,
                    "phase_overlap_ratio": None,
                    "traffic_mean_gap": None,
                },
            }
        )
    return sorted(output, key=lambda row: str(row["team"]))


def build_team_scores(
    driver_scores: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按同队两车等权均值生成视觉车队顺序，并披露组成。"""

    frame = pd.DataFrame(
        [
            {
                "driver": row["driver"],
                "team": row["team"],
                "pace": row["standardized_pace_s"],
                "status": row["status"],
            }
            for row in driver_scores
        ]
    )
    rows: list[dict[str, Any]] = []
    for team, group in frame.groupby("team", sort=True):
        valid = group.loc[
            group["pace"].notna()
            & group["status"].eq("PUBLISHABLE_VISUAL_PROXY")
        ]
        pace = float(valid["pace"].mean()) if len(valid) == 2 else None
        rows.append(
            {
                "team": str(team),
                "standardized_pace_s": _safe_number(pace),
                "drivers": valid["driver"].astype(str).tolist(),
                "driver_count": int(len(valid)),
                "status": (
                    "PUBLISHABLE_VISUAL_PROXY"
                    if len(valid) == 2
                    else "INSUFFICIENT_TWO_CAR_SUPPORT"
                ),
                "aggregation": "equal_weight_two_driver_mean",
            }
        )
    rows.sort(
        key=lambda row: (
            row["standardized_pace_s"]
            if row["standardized_pace_s"] is not None
            else float("inf"),
            row["team"],
        )
    )
    fastest = next(
        (
            float(row["standardized_pace_s"])
            for row in rows
            if row["standardized_pace_s"] is not None
        ),
        None,
    )
    for index, row in enumerate(rows, start=1):
        row["visual_rank"] = index
        row["delta_to_fastest_pct"] = (
            _safe_number(
                (
                    float(row["standardized_pace_s"]) - fastest
                )
                / fastest
                * 100.0,
                6,
            )
            if fastest is not None and row["standardized_pace_s"] is not None
            else None
        )
    return rows


def _comparison_map(
    comparisons: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    return {
        (
            str(row["team"]),
            str(row["left_driver"]),
            str(row["right_driver"]),
        ): row
        for row in comparisons
    }


def analyse_event(
    points: pd.DataFrame,
    *,
    traffic_features: pd.DataFrame | None = None,
    primary_fuel_discount_s_per_lap: float = PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
    include_lap_evidence: bool = True,
) -> dict[str, Any]:
    """运行单场 DeltaData 方法等价模型和双轴敏感性审计。"""

    lap_frame, exclusion_ledger = prepare_clean_air_laps(
        points,
        traffic_features=traffic_features,
        traffic_ratio_max=PUBLIC_TRAFFIC_RATIO_MAX,
    )
    primary_model, primary_candidates = fit_robust_condition_model(
        lap_frame,
        fuel_discount_s_per_lap=primary_fuel_discount_s_per_lap,
    )
    modelled_driver_scores = build_standardized_driver_scores(
        primary_candidates, primary_model
    )
    driver_scores = append_unsupported_driver_rows(
        modelled_driver_scores, lap_frame
    )
    team_scores = build_team_scores(driver_scores)
    comparisons = append_unmodelled_teammate_comparisons(
        build_teammate_comparisons(
            primary_candidates, modelled_driver_scores
        ),
        lap_frame,
    )

    fuel_sensitivity: dict[str, Any] = {}
    for name, rate in FUEL_SCENARIOS_S_PER_LAP.items():
        model, candidates = fit_robust_condition_model(
            lap_frame,
            fuel_discount_s_per_lap=rate,
        )
        scores = build_standardized_driver_scores(candidates, model)
        pairs = build_teammate_comparisons(candidates, scores)
        fuel_sensitivity[name] = {
            "fuel_discount_s_per_lap": rate,
            "driver_scores": {
                row["driver"]: row["standardized_pace_s"] for row in scores
            },
            "teammate_deltas_pct": [
                {
                    "team": row["team"],
                    "left_driver": row["left_driver"],
                    "right_driver": row["right_driver"],
                    "delta_pct": row["visual_left_minus_right_pct"],
                    "audited_status": row["audited_status"],
                }
                for row in pairs
            ],
            "representative_rows": model["representative_rows"],
        }

    traffic_sensitivity: dict[str, Any] = {}
    for threshold in TRAFFIC_SENSITIVITY_THRESHOLDS:
        threshold_frame, threshold_ledger = prepare_clean_air_laps(
            points,
            traffic_features=traffic_features,
            traffic_ratio_max=threshold,
        )
        try:
            model, candidates = fit_robust_condition_model(
                threshold_frame,
                fuel_discount_s_per_lap=primary_fuel_discount_s_per_lap,
            )
            scores = build_standardized_driver_scores(candidates, model)
            pairs = build_teammate_comparisons(candidates, scores)
            traffic_sensitivity[f"{threshold:.6f}"] = {
                "status": "PASS",
                "traffic_ratio_max": threshold,
                "clean_air_candidates": threshold_ledger[
                    "clean_air_candidates"
                ],
                "representative_rows": model["representative_rows"],
                "teammate_deltas_pct": [
                    {
                        "team": row["team"],
                        "left_driver": row["left_driver"],
                        "right_driver": row["right_driver"],
                        "delta_pct": row["visual_left_minus_right_pct"],
                    }
                    for row in pairs
                ],
            }
        except ValueError as error:
            traffic_sensitivity[f"{threshold:.6f}"] = {
                "status": "NOT_TESTED_INSUFFICIENT_SUPPORT",
                "traffic_ratio_max": threshold,
                "clean_air_candidates": threshold_ledger[
                    "clean_air_candidates"
                ],
                "reason": str(error),
            }

    pair_keyed = _comparison_map(comparisons)
    pair_sensitivity: list[dict[str, Any]] = []
    for key, primary_pair in pair_keyed.items():
        values: dict[str, float | None] = {}
        for scenario, payload in fuel_sensitivity.items():
            match = next(
                (
                    row
                    for row in payload["teammate_deltas_pct"]
                    if (
                        row["team"],
                        row["left_driver"],
                        row["right_driver"],
                    )
                    == key
                ),
                None,
            )
            values[scenario] = (
                _finite(match["delta_pct"]) if match is not None else None
            )
        finite_values = [value for value in values.values() if value is not None]
        signs = {int(np.sign(value)) for value in finite_values if abs(value) > 1e-12}
        pair_sensitivity.append(
            {
                "team": key[0],
                "left_driver": key[1],
                "right_driver": key[2],
                "fuel_scenario_delta_pct": values,
                "range_pct": [
                    _safe_number(min(finite_values), 6),
                    _safe_number(max(finite_values), 6),
                ]
                if finite_values
                else None,
                "direction_stable": len(signs) <= 1,
                "primary_audited_status": primary_pair["audited_status"],
            }
        )

    representative = primary_candidates.loc[
        primary_candidates["representative_lap"]
    ]
    lap_evidence_columns = [
        "driver",
        "team",
        "lap",
        "stint",
        "compound",
        "tyre_age",
        "lap_time_s",
        "traffic_ratio",
        "clean_air_share",
        "missing",
        "telemetry_coverage",
        "candidate_weight",
        "fuel_adjusted_lap_time_s",
        "condition_prediction_s",
        "condition_residual_s",
        "representative_lap",
        "robust_weight_role",
    ]
    lap_evidence = (
        _json_records(primary_candidates[lap_evidence_columns])
        if include_lap_evidence
        else []
    )
    auditable_pairs = [
        row for row in comparisons if row["audited_status"] == "COMPARABLE"
    ]
    return {
        "model": primary_model,
        "exclusion_ledger": exclusion_ledger,
        "representative_ledger": {
            "candidate_laps": int(len(primary_candidates)),
            "representative_laps": int(len(representative)),
            "downweighted_outliers": int(
                (~primary_candidates["representative_lap"]).sum()
            ),
            "representative_kish_ess": kish_effective_sample_size(
                representative["candidate_weight"]
            ),
            "drivers_in_model": int(primary_candidates["driver"].nunique()),
            "teams_in_model": int(primary_candidates["team"].nunique()),
            "manual_labels_available": False,
            "synthetic_laps": 0,
        },
        "visual_replication": {
            "driver_ranking": driver_scores,
            "team_ranking": team_scores,
            "teammate_h2h": comparisons,
            "ranking_role": (
                "method_equivalent_standardized_visual_order_not_audited_total_order"
            ),
        },
        "audited_analysis": {
            "status": "PASS_WITH_COMPARABLE_SUBSETS"
            if auditable_pairs
            else "audit_only",
            "direct_total_order_allowed": False,
            "comparable_teammate_pairs": len(auditable_pairs),
            "all_teammate_pairs": len(comparisons),
            "pairwise": comparisons,
            "reason": (
                "全场排名混合模型支持；只有通过同配方、共同胎龄、赛道阶段、"
                "交通平衡与 ESS 门控的同队配对允许直接解释。"
            ),
        },
        "sensitivity": {
            "fuel": fuel_sensitivity,
            "traffic_threshold": traffic_sensitivity,
            "pair_summary": pair_sensitivity,
        },
        "lap_evidence": lap_evidence,
        "boundaries": {
            "true_fuel_identifiable": False,
            "physical_tyre_wear_identifiable": False,
            "creator_manual_lap_labels_available": False,
            "creator_exact_code_available": False,
            "fuel_scenario_is_named_assumption": True,
            "traffic_is_observed_point_level_proxy": True,
        },
    }


def extract_h2h_delta(
    analysis: Mapping[str, Any],
    *,
    team: str,
    left_driver: str,
    right_driver: str,
) -> float | None:
    """按明确左右方向读取视觉 H2H；方向相反时自动取负。"""

    for row in analysis["visual_replication"]["teammate_h2h"]:
        if str(row["team"]) != team:
            continue
        if (
            str(row["left_driver"]) == left_driver
            and str(row["right_driver"]) == right_driver
        ):
            return _finite(row["visual_left_minus_right_pct"])
        if (
            str(row["left_driver"]) == right_driver
            and str(row["right_driver"]) == left_driver
        ):
            value = _finite(row["visual_left_minus_right_pct"])
            return -value if value is not None else None
    return None


def reference_benchmark_metrics(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """汇总图表转录值与模型值的百分比点误差。"""

    errors = np.asarray(
        [
            float(row["model_delta_pct"]) - float(row["reference_delta_pct"])
            for row in rows
            if _finite(row.get("model_delta_pct")) is not None
            and _finite(row.get("reference_delta_pct")) is not None
        ],
        dtype=float,
    )
    if not len(errors):
        return {
            "events": 0,
            "mae_pp": None,
            "rmse_pp": None,
            "max_abs_error_pp": None,
            "bias_pp": None,
            "direction_accuracy": None,
        }
    paired = [
        row
        for row in rows
        if _finite(row.get("model_delta_pct")) is not None
        and _finite(row.get("reference_delta_pct")) is not None
    ]
    direction = [
        np.sign(float(row["model_delta_pct"]))
        == np.sign(float(row["reference_delta_pct"]))
        for row in paired
    ]
    return {
        "events": int(len(errors)),
        "mae_pp": float(np.mean(np.abs(errors))),
        "rmse_pp": float(np.sqrt(np.mean(np.square(errors)))),
        "max_abs_error_pp": float(np.max(np.abs(errors))),
        "bias_pp": float(np.mean(errors)),
        "direction_accuracy": float(np.mean(direction)),
    }
