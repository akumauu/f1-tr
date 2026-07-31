"""F1pace 交通热图的可审计倒推模型。

本模块刻意拆成两层：

* ``audited_analysis`` 使用逐点遥测重建同一赛道位置的时间差，直接执行公开的
  2 秒阈值，不依赖参考图校准；
* ``visual_replication`` 只使用三组有物理含义的阈值比例，通过稳健 Huber 回归
  校准到公开图的颜色读数。校准参数必须在事件外验证，不能用目标事件重新拟合。

公开图只作为数值校准/验收证据，不作为产品图片资产。颜色反解的目标本身带有约
1--2 个百分点的读图误差，因此不能称为原作者的原始数值导出。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from PIL import Image
from sklearn.linear_model import HuberRegressor
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from reference_analysis_lab.f1pace_pilot import _time_weights


TRAFFIC_THRESHOLDS_S = (1.5, 1.75, 2.0, 2.25, 2.5)
PUBLIC_TRAFFIC_LAP_THRESHOLD = 1.0 / 3.0
REFERENCE_GREEN_INTERCEPT_PCT = 115.26745621
REFERENCE_GREEN_SLOPE_PCT = -0.47860114
REFERENCE_COLOR_CALIBRATION_MAE_PP = 0.473
REFERENCE_COLOR_CALIBRATION_MAX_ERROR_PP = 1.50

SURROGATE_FEATURE_COLUMNS = (
    "position",
    "missing",
    "lap_frac",
    "cur_1.5",
    "cur_1.75",
    "cur_2.0",
    "cur_2.25",
    "cur_2.5",
    "cross_1.5",
    "cross_1.75",
    "cross_2.0",
    "cross_2.25",
    "cross_2.5",
    "ahead_1.5",
    "ahead_1.75",
    "ahead_2.0",
    "ahead_2.25",
    "ahead_2.5",
)


@dataclass(frozen=True)
class DriverTrace:
    """单车在全场绝对时间/累计赛道相位上的单调插值轴。"""

    time_s: np.ndarray
    phase: np.ndarray
    inverse_phase: np.ndarray
    inverse_time_s: np.ndarray
    speed_kph: np.ndarray


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_reference_traffic_grid(
    image_path: Path,
    driver_order: Sequence[str],
    lap_count: int,
    *,
    x_start: int = 285,
    x_end: int = 2211,
    y_start: int = 148,
    row_height: int = 49,
) -> pd.DataFrame:
    """从公开热图背景色读取 driver×lap 的交通比例代理。

    几何位置来自公开图中可重复识别的网格线；颜色到比例的线性关系来自 22 个图内
    明文百分比标签。该函数不会 OCR 标签，也不会把 ``>90%`` 当作精确原始值。
    """

    pixels = np.asarray(Image.open(image_path).convert("RGB"))
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("参考图必须是 RGB 栅格图")
    if x_end > pixels.shape[1] or y_start + row_height * len(driver_order) > pixels.shape[0]:
        raise ValueError(
            f"参考图尺寸不足：shape={pixels.shape}，"
            f"需要 x_end={x_end}、rows={len(driver_order)}"
        )
    if lap_count <= 0:
        raise ValueError("lap_count 必须为正整数")

    x_edges = np.rint(np.linspace(x_start, x_end, lap_count + 1)).astype(int)
    rows: list[dict[str, Any]] = []
    for row_index, driver in enumerate(driver_order):
        y0 = y_start + row_index * row_height
        for lap_index in range(lap_count):
            x0, x1 = int(x_edges[lap_index]), int(x_edges[lap_index + 1])
            patch = pixels[y0 + 3 : y0 + 10, x0 + 3 : max(x0 + 4, x1 - 3)]
            if patch.size == 0:
                raise ValueError(f"空颜色采样块：driver={driver}, lap={lap_index + 1}")
            green = float(np.median(patch[:, :, 1]))
            target_pct = float(
                np.clip(
                    REFERENCE_GREEN_INTERCEPT_PCT
                    + REFERENCE_GREEN_SLOPE_PCT * green,
                    0.0,
                    100.0,
                )
            )
            rows.append(
                {
                    "driver": str(driver),
                    "lap": lap_index + 1,
                    "reference_green_median": green,
                    "reference_traffic_ratio": target_pct / 100.0,
                    "reference_target_role": "image_color_derived_proxy",
                }
            )
    return pd.DataFrame(rows)


def _absolute_seconds(frame: pd.DataFrame) -> np.ndarray:
    start = pd.to_datetime(frame["lap_start_raw"], errors="coerce", utc=True)
    start_s = start.astype("int64").to_numpy(dtype=float) / 1e9
    start_s[start.isna().to_numpy()] = np.nan
    within_lap = pd.to_numeric(frame["time"], errors="coerce").to_numpy(dtype=float)
    return start_s + within_lap


def _finite_numeric(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)


def _deduplicate_time(
    time_s: np.ndarray,
    phase: np.ndarray,
    speed_kph: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """保留每个时间戳的最后一个样本，满足 ``np.interp`` 的严格递增要求。"""

    order = np.argsort(time_s, kind="stable")
    time_s, phase, speed_kph = time_s[order], phase[order], speed_kph[order]
    reverse_unique = np.unique(time_s[::-1], return_index=True)[1]
    keep = np.sort(len(time_s) - 1 - reverse_unique)
    return time_s[keep], phase[keep], speed_kph[keep]


def build_driver_traces(points: pd.DataFrame) -> tuple[dict[str, DriverTrace], float]:
    """建立全车手绝对时间轴，并估计赛道一圈长度。"""

    required = {
        "driver_acronym",
        "lap",
        "time",
        "lap_start_raw",
        "rel_distance",
        "distance",
        "speed",
    }
    missing = sorted(required.difference(points.columns))
    if missing:
        raise ValueError(f"逐点遥测缺少相位重建字段：{', '.join(missing)}")

    lap_maxima = (
        points.assign(
            _distance=pd.to_numeric(points["distance"], errors="coerce"),
            _rel=pd.to_numeric(points["rel_distance"], errors="coerce"),
        )
        .loc[lambda frame: frame["_rel"].between(0.95, 1.05)]
        .groupby(["driver_acronym", "lap"], sort=False)["_distance"]
        .max()
    )
    plausible = lap_maxima.loc[lap_maxima.between(1000.0, 10000.0)]
    if plausible.empty:
        raise ValueError("无法从逐点距离估计赛道长度")
    circuit_length_m = float(plausible.median())

    traces: dict[str, DriverTrace] = {}
    for driver, group in points.groupby("driver_acronym", sort=True):
        time_s = _absolute_seconds(group)
        lap = _finite_numeric(group["lap"])
        rel = _finite_numeric(group["rel_distance"])
        speed = _finite_numeric(group["speed"])
        phase = lap - 1.0 + rel
        finite = np.isfinite(time_s) & np.isfinite(phase)
        if finite.sum() < 2:
            continue
        time_s, phase, speed = _deduplicate_time(
            time_s[finite], phase[finite], speed[finite]
        )
        phase = np.maximum.accumulate(phase)

        inverse_phase, first_indices = np.unique(phase, return_index=True)
        inverse_time = time_s[first_indices]
        if len(inverse_phase) < 2:
            continue
        traces[str(driver)] = DriverTrace(
            time_s=time_s,
            phase=phase,
            inverse_phase=inverse_phase,
            inverse_time_s=inverse_time,
            speed_kph=speed,
        )
    if len(traces) < 2:
        raise ValueError("至少需要两名车手才能重建前车时间差")
    return traces, circuit_length_m


def _interp_trace(
    trace: DriverTrace,
    query_time_s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    phase = np.interp(query_time_s, trace.time_s, trace.phase)
    speed = np.interp(query_time_s, trace.time_s, trace.speed_kph)
    outside = (query_time_s < trace.time_s[0]) | (query_time_s > trace.time_s[-1])
    phase[outside] = np.nan
    speed[outside] = np.nan
    return phase, speed


def _weighted_ratio(
    values: np.ndarray,
    weights: np.ndarray,
    threshold_s: float,
) -> float:
    observed = np.isfinite(weights) & (weights > 0)
    finite = np.isfinite(values) & observed
    # 公开口径是“一圈中处于 2 秒内的时间比例”，分母应是该圈全部可观测时间。
    # 无法重建前车的区间进入 missing 审计，而不能被静默移出分母，否则首圈/领跑
    # 圈会因只剩少量有限样本而被夸大到 100%。
    denominator = float(weights[observed].sum())
    if denominator <= 0:
        return math.nan
    return float(weights[finite & (values <= threshold_s)].sum() / denominator)


def _weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    quantile: float,
) -> float:
    finite = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not finite.any():
        return math.nan
    selected_values = values[finite]
    selected_weights = weights[finite]
    order = np.argsort(selected_values, kind="stable")
    selected_values = selected_values[order]
    selected_weights = selected_weights[order]
    cumulative = np.cumsum(selected_weights)
    target = float(np.clip(quantile, 0.0, 1.0) * cumulative[-1])
    return float(selected_values[min(np.searchsorted(cumulative, target), len(order) - 1)])


def _threshold_label(value: float) -> str:
    """保持 2 秒列名为 ``2.0``，与冻结校准表/方法卡身份一致。"""

    return f"{float(value):.2f}".rstrip("0").rstrip(".") + (
        ".0" if float(value).is_integer() else ""
    )


def build_time_headway_features(
    points: pd.DataFrame,
    *,
    thresholds_s: Iterable[float] = TRAFFIC_THRESHOLDS_S,
) -> pd.DataFrame:
    """为每个 driver×lap 计算三族逐点交通特征。

    ``cur`` 是冻结 feed 的前车距离/本车速度；``ahead`` 是同步全车场上最近物理
    前车的弧长/前车速度；``cross`` 是该物理前车上一次越过本车当前位置的时间差。
    ``cross_2.0`` 是不经过参考图拟合的审计主指标。
    """

    required = {
        "driver_acronym",
        "lap",
        "sample_index",
        "time",
        "lap_start_raw",
        "lap_duration",
        "rel_distance",
        "distance",
        "speed",
        "driver_ahead",
        "distance_to_driver_ahead",
        "position",
    }
    missing = sorted(required.difference(points.columns))
    if missing:
        raise ValueError(f"逐点遥测缺少交通倒推字段：{', '.join(missing)}")

    thresholds = tuple(float(value) for value in thresholds_s)
    traces, circuit_length_m = build_driver_traces(points)
    max_lap = max(1.0, float(pd.to_numeric(points["lap"], errors="coerce").max()))
    rows: list[dict[str, Any]] = []

    for (driver, lap), raw_group in points.groupby(
        ["driver_acronym", "lap"], sort=True
    ):
        driver = str(driver)
        if driver not in traces:
            continue
        group, weights = _time_weights(raw_group)
        if group.empty or weights.sum() <= 0:
            continue
        query_time = _absolute_seconds(group)
        target_lap = _finite_numeric(group["lap"])
        target_rel = _finite_numeric(group["rel_distance"])
        target_phase = target_lap - 1.0 + target_rel
        sample_speed = _finite_numeric(group["speed"])

        n_samples = len(group)
        candidate_drivers = [name for name in traces if name != driver]
        forward_gaps = np.full((len(candidate_drivers), n_samples), np.nan)
        candidate_phase = np.full_like(forward_gaps, np.nan)
        candidate_speed = np.full_like(forward_gaps, np.nan)
        for candidate_index, candidate in enumerate(candidate_drivers):
            other_phase, other_speed = _interp_trace(
                traces[candidate], query_time
            )
            raw_gap = np.mod(other_phase - target_phase, 1.0)
            valid = (
                np.isfinite(other_phase)
                & np.isfinite(target_phase)
                & (raw_gap > 1e-7)
                & (raw_gap < 1.0 - 1e-7)
            )
            forward_gaps[candidate_index, valid] = raw_gap[valid]
            candidate_phase[candidate_index, valid] = other_phase[valid]
            candidate_speed[candidate_index, valid] = other_speed[valid]

        any_candidate = np.isfinite(forward_gaps).any(axis=0)
        nearest_index = np.zeros(n_samples, dtype=int)
        if any_candidate.any():
            nearest_index[any_candidate] = np.nanargmin(
                forward_gaps[:, any_candidate], axis=0
            )
        sample_index = np.arange(n_samples)
        nearest_gap = np.full(n_samples, np.nan)
        nearest_phase = np.full(n_samples, np.nan)
        nearest_speed = np.full(n_samples, np.nan)
        nearest_driver = np.full(n_samples, "", dtype=object)
        nearest_gap[any_candidate] = forward_gaps[
            nearest_index[any_candidate], sample_index[any_candidate]
        ]
        nearest_phase[any_candidate] = candidate_phase[
            nearest_index[any_candidate], sample_index[any_candidate]
        ]
        nearest_speed[any_candidate] = candidate_speed[
            nearest_index[any_candidate], sample_index[any_candidate]
        ]
        for index in np.flatnonzero(any_candidate):
            nearest_driver[index] = candidate_drivers[nearest_index[index]]

        target_fraction = np.mod(target_phase, 1.0)
        crossing_phase = np.floor(nearest_phase) + target_fraction
        crossing_phase = np.where(
            crossing_phase > nearest_phase,
            crossing_phase - 1.0,
            crossing_phase,
        )
        crossing_time = np.full(n_samples, np.nan)
        for candidate in set(nearest_driver[nearest_driver != ""]):
            select = nearest_driver == candidate
            trace = traces[str(candidate)]
            valid_phase = (
                select
                & np.isfinite(crossing_phase)
                & (crossing_phase >= trace.inverse_phase[0])
                & (crossing_phase <= trace.inverse_phase[-1])
            )
            crossing_time[valid_phase] = np.interp(
                crossing_phase[valid_phase],
                trace.inverse_phase,
                trace.inverse_time_s,
            )
        crossing_headway_s = query_time - crossing_time
        crossing_headway_s[
            ~np.isfinite(crossing_headway_s) | (crossing_headway_s < 0)
        ] = np.nan

        ahead_headway_s = (
            nearest_gap * circuit_length_m
            / np.maximum(nearest_speed, 0.1)
            * 3.6
        )
        current_distance = _finite_numeric(group["distance_to_driver_ahead"])
        current_present = group["driver_ahead"].notna().to_numpy(dtype=bool)
        current_headway_s = (
            current_distance / np.maximum(sample_speed, 0.1) * 3.6
        )
        current_headway_s[
            ~np.isfinite(current_headway_s) | ~current_present
        ] = np.nan

        weighted_valid = weights > 0
        valid_weight = float(weights[weighted_valid].sum())
        missing_ratio = (
            float(weights[weighted_valid & ~np.isfinite(crossing_headway_s)].sum())
            / valid_weight
            if valid_weight > 0
            else math.nan
        )
        switches = 0
        previous = None
        for candidate in nearest_driver:
            if not candidate:
                continue
            if previous is not None and candidate != previous:
                switches += 1
            previous = candidate
        switch_ratio = switches / max(1, np.count_nonzero(nearest_driver != "") - 1)

        position = float(
            pd.to_numeric(group["position"], errors="coerce").median()
        )
        row: dict[str, Any] = {
            "driver": driver,
            "lap": int(lap),
            "position": position,
            "missing": missing_ratio,
            "lap_frac": float(lap) / max_lap,
            "switches": float(switch_ratio),
            "observed_time_s": valid_weight,
            "telemetry_samples": int(n_samples),
            "circuit_length_estimate_m": circuit_length_m,
            "audited_traffic_ratio": _weighted_ratio(
                crossing_headway_s, weights, 2.0
            ),
            "audited_definition": (
                "nearest_physical_car_same_track_location_crossing_headway_le_2s"
            ),
        }
        for prefix, values in (
            ("cur", current_headway_s),
            ("cross", crossing_headway_s),
            ("ahead", ahead_headway_s),
        ):
            for threshold in thresholds:
                row[f"{prefix}_{_threshold_label(threshold)}"] = _weighted_ratio(
                    values, weights, threshold
                )
            row[f"{prefix}_q1"] = _weighted_quantile(values, weights, 0.1)
            row[f"{prefix}_q5"] = _weighted_quantile(values, weights, 0.5)
            row[f"{prefix}_q9"] = _weighted_quantile(values, weights, 0.9)
        rows.append(row)
    return pd.DataFrame(rows)


def _prepare_surrogate_matrix(
    frame: pd.DataFrame,
    feature_columns: Sequence[str] = SURROGATE_FEATURE_COLUMNS,
    *,
    fill_values: Mapping[str, float] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    missing = sorted(set(feature_columns).difference(frame.columns))
    if missing:
        raise ValueError(f"稳健校准缺少特征：{', '.join(missing)}")
    matrix = frame.loc[:, feature_columns].apply(pd.to_numeric, errors="coerce")
    if fill_values is None:
        resolved_fill = {
            column: (
                float(matrix[column].median())
                if matrix[column].notna().any()
                else 0.0
            )
            for column in feature_columns
        }
    else:
        resolved_fill = {column: float(fill_values[column]) for column in feature_columns}
    matrix = matrix.fillna(resolved_fill)
    return matrix.to_numpy(dtype=float), resolved_fill


def fit_surrogate(
    training: pd.DataFrame,
    *,
    target_column: str = "reference_traffic_ratio",
    feature_columns: Sequence[str] = SURROGATE_FEATURE_COLUMNS,
) -> dict[str, Any]:
    """拟合并序列化稳健线性校准器，不保存不可审计的 pickle。"""

    if target_column not in training:
        raise ValueError(f"训练数据缺少目标列 {target_column}")
    target = pd.to_numeric(training[target_column], errors="coerce")
    usable = target.notna()
    if usable.sum() < len(feature_columns) + 2:
        raise ValueError("稳健校准样本不足")
    selected = training.loc[usable].reset_index(drop=True)
    y = target.loc[usable].to_numpy(dtype=float)
    x, fill_values = _prepare_surrogate_matrix(
        selected, feature_columns
    )
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)
    model = HuberRegressor(epsilon=1.5, alpha=0.1, max_iter=2000)
    model.fit(x_scaled, y)
    return {
        "model_type": "standard_scaler_plus_huber_regressor",
        "epsilon": 1.5,
        "alpha": 0.1,
        "max_iter": 2000,
        "feature_columns": list(feature_columns),
        "fill_values": fill_values,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "training_rows": int(len(selected)),
        "training_drivers": sorted(selected["driver"].astype(str).unique().tolist()),
        "training_target_role": "public_chart_image_color_derived_proxy",
    }


def predict_surrogate(frame: pd.DataFrame, model: Mapping[str, Any]) -> np.ndarray:
    feature_columns = tuple(str(value) for value in model["feature_columns"])
    x, _ = _prepare_surrogate_matrix(
        frame,
        feature_columns,
        fill_values=model["fill_values"],
    )
    mean = np.asarray(model["scaler_mean"], dtype=float)
    scale = np.asarray(model["scaler_scale"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    safe_scale = np.where(scale == 0, 1.0, scale)
    prediction = ((x - mean) / safe_scale) @ coefficients + float(model["intercept"])
    return np.clip(prediction, 0.0, 1.0)


def validation_metrics(
    target: Sequence[float],
    prediction: Sequence[float],
) -> dict[str, Any]:
    truth = np.asarray(target, dtype=float)
    estimate = np.asarray(prediction, dtype=float)
    finite = np.isfinite(truth) & np.isfinite(estimate)
    if not finite.any():
        return {
            "rows": 0,
            "mae_pp": None,
            "rmse_pp": None,
            "p90_abs_error_pp": None,
            "max_abs_error_pp": None,
            "traffic_lap_accuracy": None,
        }
    error_pp = (estimate[finite] - truth[finite]) * 100.0
    absolute = np.abs(error_pp)
    actual_class = truth[finite] > PUBLIC_TRAFFIC_LAP_THRESHOLD
    predicted_class = estimate[finite] > PUBLIC_TRAFFIC_LAP_THRESHOLD
    return {
        "rows": int(finite.sum()),
        "mae_pp": float(absolute.mean()),
        "rmse_pp": float(np.sqrt(np.mean(error_pp**2))),
        "p90_abs_error_pp": float(np.quantile(absolute, 0.9)),
        "max_abs_error_pp": float(absolute.max()),
        "bias_pp": float(error_pp.mean()),
        "traffic_lap_accuracy": float(np.mean(actual_class == predicted_class)),
        "actual_traffic_laps": int(actual_class.sum()),
        "predicted_traffic_laps": int(predicted_class.sum()),
    }


def grouped_cross_validation(
    training: pd.DataFrame,
    *,
    folds: int = 5,
) -> dict[str, Any]:
    """按整名车手留出，避免同一车手相邻圈泄漏到训练/验证两侧。"""

    drivers = training["driver"].astype(str)
    unique_drivers = drivers.nunique()
    split_count = min(int(folds), int(unique_drivers))
    if split_count < 2:
        raise ValueError("至少需要两名车手进行分组交叉验证")
    splitter = GroupKFold(n_splits=split_count)
    fold_rows: list[dict[str, Any]] = []
    for fold, (train_index, test_index) in enumerate(
        splitter.split(training, groups=drivers),
        start=1,
    ):
        train = training.iloc[train_index].reset_index(drop=True)
        test = training.iloc[test_index].reset_index(drop=True)
        model = fit_surrogate(train)
        prediction = predict_surrogate(test, model)
        metrics = validation_metrics(
            test["reference_traffic_ratio"], prediction
        )
        fold_rows.append(
            {
                "fold": fold,
                "held_out_drivers": sorted(
                    test["driver"].astype(str).unique().tolist()
                ),
                **metrics,
            }
        )
    metric_names = (
        "mae_pp",
        "rmse_pp",
        "p90_abs_error_pp",
        "traffic_lap_accuracy",
    )
    aggregate = {
        f"mean_{name}": float(np.mean([row[name] for row in fold_rows]))
        for name in metric_names
    }
    aggregate["worst_fold_mae_pp"] = float(
        max(row["mae_pp"] for row in fold_rows)
    )
    return {
        "protocol": "GroupKFold_by_whole_driver",
        "folds": fold_rows,
        "aggregate": aggregate,
    }


def merge_reference_and_features(
    reference: pd.DataFrame,
    features: pd.DataFrame,
) -> pd.DataFrame:
    """只保留真实遥测中存在的 driver×lap，并给出缺失覆盖账本。"""

    merged = reference.merge(
        features,
        on=["driver", "lap"],
        how="inner",
        validate="one_to_one",
    )
    merged.attrs["reference_cells"] = int(len(reference))
    merged.attrs["feature_cells"] = int(len(features))
    merged.attrs["matched_cells"] = int(len(merged))
    return merged
