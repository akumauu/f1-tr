"""FDataAnalysis 公开图表语义的算法倒推与可靠方法等价实现。

本模块只使用冻结的公开逐点遥测。它复刻速度—距离、弯角入口/最低/出口、
直道速度、全油门、制动位置、胎龄变化和同队双车比较的可观察语义，但不把
这些输出直接命名为真实下压力、引擎功率、物理轮胎磨损或车辆因果强弱。

与 v1 相比，v2 的关键变化是：

* 每圈线性插值到 1,000 个统一距离区间，而不是 200 个粗粒度分箱；
* 用横向加速度峰值和制动证据确定动态弯角边界，不再使用固定 ±1.25% 窗口；
* 同时保留公开图风格的样本统计与时间加权审计统计；
* 胎龄变化在 Stint 内用 Theil--Sen，并报告具名燃油 low/base/high 情景；
* 同队双车只在配方、胎龄、比赛阶段与逐点交通条件匹配后直接比较。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from scipy.stats import theilslopes

from reference_analysis_lab.deltadata_reverse_engineered_v2 import (
    FUEL_SCENARIOS_S_PER_LAP,
)
from reference_analysis_lab.f1pace_pilot import build_lap_table


AXIS_INTERVALS = 1000
AXIS_POINTS = AXIS_INTERVALS + 1
FULL_THROTTLE_THRESHOLD = 95.0
LATERAL_ACCEL_MIN_MPS2 = 6.0
LATERAL_ACCEL_PROMINENCE_MPS2 = 8.0
CORNER_MIN_SEPARATION_FRACTION = 0.01
BRAKE_PREVALENCE_THRESHOLD = 0.25
TOP_SPEED_SAMPLE_COUNT = 15
TRAFFIC_RATIO_MAX = 0.20
MIN_DEGRADATION_LAPS = 6
MIN_DEGRADATION_TYRE_SPAN = 5.0
MIN_MATCHED_LAPS = 6
MIN_MATCHED_ESS = 6.0


def _number(value: Any, digits: int = 6) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    rounded = round(number, digits)
    return int(rounded) if rounded.is_integer() else rounded


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        output.append(
            {
                key: (
                    None
                    if value is None
                    or (
                        isinstance(value, (float, np.floating))
                        and not np.isfinite(value)
                    )
                    else value.item()
                    if isinstance(value, np.generic)
                    else value
                )
                for key, value in row.items()
            }
        )
    return output


def kish_effective_sample_size(weights: Iterable[float]) -> float:
    values = np.asarray(list(weights), dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    if not len(values):
        return 0.0
    denominator = float(np.square(values).sum())
    return float(np.square(values.sum()) / denominator) if denominator > 0 else 0.0


def weighted_mean(values: Iterable[float], weights: Iterable[float]) -> float | None:
    value_array = np.asarray(list(values), dtype=float)
    weight_array = np.asarray(list(weights), dtype=float)
    finite = (
        np.isfinite(value_array)
        & np.isfinite(weight_array)
        & (weight_array > 0)
    )
    if not finite.any():
        return None
    return float(np.average(value_array[finite], weights=weight_array[finite]))


def _finite_median(values: Iterable[float]) -> float | None:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    return float(np.median(array)) if len(array) else None


def linear_slope(x_values: Iterable[float], y_values: Iterable[float]) -> float | None:
    """描述性 OLS 斜率；只供 visual_replication 使用。"""

    x = np.asarray(list(x_values), dtype=float)
    y = np.asarray(list(y_values), dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < 3 or float(np.ptp(x)) <= 0:
        return None
    centered = x - x.mean()
    denominator = float(np.square(centered).sum())
    if denominator <= 0:
        return None
    return float(np.sum(centered * (y - y.mean())) / denominator)


def robust_theil_sen_slope(
    x_values: Iterable[float],
    y_values: Iterable[float],
) -> float | None:
    """返回 Theil--Sen 斜率；样本不足或 x 无跨度时 fail-close。"""

    x = np.asarray(list(x_values), dtype=float)
    y = np.asarray(list(y_values), dtype=float)
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if len(x) < 3 or float(np.ptp(x)) <= 0:
        return None
    return float(theilslopes(y, x, 0.95).slope)


def top_n_mean(values: Iterable[float], n: int = TOP_SPEED_SAMPLE_COUNT) -> float | None:
    """复刻公开“最高 n 个速度样本的均值”口径。"""

    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < int(n) or int(n) <= 0:
        return None
    selected = np.partition(array, len(array) - int(n))[-int(n) :]
    return float(selected.mean())


def integrate_speed_trace_seconds(
    speed_kph: Sequence[float],
    *,
    distance_m: float,
) -> float | None:
    """按 ``dt = ds / v`` 对统一距离轴速度轨迹积分。"""

    speed = np.asarray(speed_kph, dtype=float)
    if len(speed) < 2 or not np.isfinite(distance_m) or distance_m <= 0:
        return None
    if not np.isfinite(speed).all() or np.any(speed <= 1.0):
        return None
    axis_m = np.linspace(0.0, float(distance_m), len(speed))
    # 当前冻结运行时的 NumPy 尚未提供 ``trapezoid`` 别名；``trapz``
    # 使用同一梯形积分公式，结果身份由单元测试固定。
    return float(np.trapz(1.0 / (speed / 3.6), axis_m))


def _time_weights(group: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    ordered = group.sort_values(["time", "sample_index"], kind="stable").copy()
    ordered["time"] = pd.to_numeric(ordered["time"], errors="coerce")
    ordered = ordered.loc[ordered["time"].notna()].reset_index(drop=True)
    if ordered.empty:
        return ordered, np.empty(0, dtype=float)
    times = ordered["time"].to_numpy(dtype=float)
    if len(times) == 1:
        return ordered, np.zeros(1, dtype=float)
    weights = np.diff(times, append=np.nan)
    positive = np.diff(times)
    fallback = (
        float(np.median(positive[positive > 0]))
        if np.any(positive > 0)
        else 0.0
    )
    duration = float(
        pd.to_numeric(ordered["lap_duration"], errors="coerce").median()
    )
    tail = duration - times[-1] if np.isfinite(duration) else 0.0
    weights[-1] = tail if tail > 0 else fallback
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    return ordered, weights


def _nearest_interpolation(
    target: np.ndarray,
    x: np.ndarray,
    values: np.ndarray,
) -> np.ndarray:
    right = np.searchsorted(x, target, side="left")
    right = np.clip(right, 0, len(x) - 1)
    left = np.clip(right - 1, 0, len(x) - 1)
    choose_right = np.abs(x[right] - target) < np.abs(target - x[left])
    chosen = np.where(choose_right, right, left)
    return values[chosen]


@dataclass
class DistanceAxisBundle:
    grid: np.ndarray
    metadata: pd.DataFrame
    channels: dict[str, np.ndarray]
    circuit_length_m: float
    sample_interval_median_s: float


def build_distance_axis_bundle(
    points: pd.DataFrame,
    *,
    axis_intervals: int = AXIS_INTERVALS,
) -> tuple[DistanceAxisBundle, pd.DataFrame, pd.DataFrame]:
    """筛选真实有效圈并插值到统一距离轴。

    返回 ``bundle``、完整圈表和进入统一轴的真实逐点样本。未使用任何合成圈。
    """

    required = {
        "driver_acronym",
        "lap",
        "sample_index",
        "time",
        "distance",
        "rel_distance",
        "speed",
        "throttle",
        "brake",
        "gear",
        "rpm",
        "drs",
        "acc_x",
        "acc_y",
        "lap_duration",
        "compound",
        "stint",
        "tyre_life",
        "team",
        "track_status",
        "deleted",
        "is_accurate",
        "position",
    }
    missing = sorted(required.difference(points.columns))
    if missing:
        raise ValueError("FDataAnalysis v2 缺少字段：" + ", ".join(missing))

    lap_table = build_lap_table(points)
    eligible_keys = lap_table.loc[
        lap_table["pace_eligible"], ["driver_acronym", "lap"]
    ]
    valid_points = points.merge(
        eligible_keys,
        on=["driver_acronym", "lap"],
        how="inner",
        validate="many_to_one",
    )
    lap_lookup = lap_table.set_index(["driver_acronym", "lap"])
    grid = np.linspace(0.0, 1.0, int(axis_intervals) + 1)
    continuous = ("speed", "throttle", "rpm", "acc_x", "acc_y")
    discrete = ("brake", "gear", "drs")
    channel_rows: dict[str, list[np.ndarray]] = {
        name: [] for name in continuous + discrete
    }
    metadata_rows: list[dict[str, Any]] = []
    positive_intervals: list[np.ndarray] = []

    for (driver, lap), raw_group in valid_points.groupby(
        ["driver_acronym", "lap"],
        sort=True,
    ):
        group = raw_group.copy()
        group["rel_distance"] = pd.to_numeric(
            group["rel_distance"], errors="coerce"
        )
        group["distance"] = pd.to_numeric(group["distance"], errors="coerce")
        group["speed"] = pd.to_numeric(group["speed"], errors="coerce")
        finite = (
            group["rel_distance"].between(-0.001, 1.001)
            & group["rel_distance"].notna()
            & group["distance"].notna()
            & group["speed"].notna()
        )
        group = group.loc[finite].sort_values(
            ["rel_distance", "time", "sample_index"],
            kind="stable",
        )
        if len(group) < 100:
            continue
        x = group["rel_distance"].clip(0.0, 1.0).to_numpy(dtype=float)
        unique_x, unique_index = np.unique(x, return_index=True)
        if (
            len(unique_x) < 100
            or float(unique_x[0]) > 0.01
            or float(unique_x[-1]) < 0.98
        ):
            continue

        interpolated: dict[str, np.ndarray] = {}
        for name in continuous + discrete:
            values = pd.to_numeric(group[name], errors="coerce").to_numpy(
                dtype=float
            )[unique_index]
            finite_values = np.isfinite(values)
            if finite_values.sum() < 2:
                interpolated = {}
                break
            source_x = unique_x[finite_values]
            source_values = values[finite_values]
            if name in discrete:
                interpolated[name] = _nearest_interpolation(
                    grid, source_x, source_values
                )
            else:
                interpolated[name] = np.interp(
                    grid, source_x, source_values
                )
        if not interpolated:
            continue
        if np.any(interpolated["speed"] <= 1.0):
            continue

        ordered_time, weights = _time_weights(raw_group)
        if ordered_time.empty or float(weights.sum()) <= 0:
            continue
        times = pd.to_numeric(ordered_time["time"], errors="coerce").to_numpy(
            dtype=float
        )
        deltas = np.diff(times)
        positive_intervals.append(deltas[deltas > 0])
        throttle = pd.to_numeric(
            ordered_time["throttle"], errors="coerce"
        ).to_numpy(dtype=float)
        brake = pd.to_numeric(
            ordered_time["brake"], errors="coerce"
        ).to_numpy(dtype=float)
        drs = pd.to_numeric(
            ordered_time["drs"], errors="coerce"
        ).to_numpy(dtype=float)
        speed_samples = pd.to_numeric(
            ordered_time["speed"], errors="coerce"
        ).to_numpy(dtype=float)
        distance_values = pd.to_numeric(
            group["distance"], errors="coerce"
        ).to_numpy(dtype=float)
        lap_length_m = float(
            np.nanpercentile(distance_values, 99.9)
            - np.nanpercentile(distance_values, 0.1)
        )
        if not 1000.0 <= lap_length_m <= 10000.0:
            continue
        reconstructed = integrate_speed_trace_seconds(
            interpolated["speed"],
            distance_m=lap_length_m,
        )
        official_duration = float(
            pd.to_numeric(raw_group["lap_duration"], errors="coerce").median()
        )
        identity = lap_lookup.loc[(driver, lap)]
        axis_index = len(metadata_rows)
        metadata_rows.append(
            {
                "axis_index": axis_index,
                "driver": str(driver),
                "team": str(identity["team"]),
                "lap": int(lap),
                "stint": int(identity["stint"]),
                "compound": str(identity["compound"]).upper(),
                "tyre_age": float(identity["tyre_life"]),
                "position": _number(identity["position"]),
                "lap_duration_s": official_duration,
                "lap_length_m": lap_length_m,
                "telemetry_samples": int(len(ordered_time)),
                "observed_time_s": float(weights.sum()),
                "telemetry_coverage": float(
                    min(max(weights.sum() / official_duration, 0.0), 1.0)
                )
                if official_duration > 0
                else 0.0,
                "sample_full_throttle_share": float(
                    np.mean(throttle >= FULL_THROTTLE_THRESHOLD)
                ),
                "time_full_throttle_share": float(
                    weights[throttle >= FULL_THROTTLE_THRESHOLD].sum()
                    / weights.sum()
                ),
                "time_brake_share": float(
                    weights[brake > 0].sum() / weights.sum()
                ),
                "time_drs_share": float(
                    weights[drs > 0].sum() / weights.sum()
                ),
                "lap_speed_p99_kph": float(
                    np.nanquantile(speed_samples, 0.99)
                ),
                "lap_speed_max_kph": float(np.nanmax(speed_samples)),
                "lap_speed_median_kph": float(np.nanmedian(speed_samples)),
                "axis_reconstructed_time_s": reconstructed,
                "axis_time_error_s": (
                    reconstructed - official_duration
                    if reconstructed is not None
                    else np.nan
                ),
                "deleted": bool(identity["deleted"]),
                "is_accurate": bool(identity["is_accurate"]),
                "green_or_yellow": bool(identity["green_or_yellow"]),
                "pace_eligible": bool(identity["pace_eligible"]),
            }
        )
        for name, values in interpolated.items():
            channel_rows[name].append(values.astype(np.float32))

    if not metadata_rows:
        raise ValueError("没有可插值到统一距离轴的真实有效圈")
    metadata = pd.DataFrame(metadata_rows)
    channels = {
        name: np.stack(rows).astype(np.float32)
        for name, rows in channel_rows.items()
    }
    interval_values = (
        np.concatenate(positive_intervals)
        if positive_intervals
        else np.asarray([np.nan])
    )
    circuit_length_m = float(metadata["lap_length_m"].median())
    bundle = DistanceAxisBundle(
        grid=grid,
        metadata=metadata,
        channels=channels,
        circuit_length_m=circuit_length_m,
        sample_interval_median_s=float(np.nanmedian(interval_values)),
    )
    return bundle, lap_table, valid_points


def build_global_axis_profile(bundle: DistanceAxisBundle) -> dict[str, np.ndarray]:
    return {
        "grid": bundle.grid,
        "speed_kph": np.nanmedian(bundle.channels["speed"], axis=0),
        "throttle_pct": np.nanmedian(bundle.channels["throttle"], axis=0),
        "brake_share": np.nanmean(bundle.channels["brake"] > 0.5, axis=0),
        "gear": np.nanmedian(bundle.channels["gear"], axis=0),
        "drs_share": np.nanmean(bundle.channels["drs"] > 0.5, axis=0),
        "lateral_accel_mps2": np.nanmedian(
            bundle.channels["acc_y"], axis=0
        ),
        "longitudinal_accel_mps2": np.nanmedian(
            bundle.channels["acc_x"], axis=0
        ),
    }


def _corner_speed_band(minimum_speed_kph: float) -> str:
    if minimum_speed_kph < 140.0:
        return "low_speed"
    if minimum_speed_kph < 220.0:
        return "medium_speed"
    return "high_speed"


def detect_dynamic_corner_proxies(
    profile: Mapping[str, Sequence[float]],
) -> list[dict[str, Any]]:
    """用横向加速度峰值生成动态边界的具名弯角代理。

    这些 ID 是 ``P01`` 等观察代理，不是 FIA/FastF1 官方弯号。
    """

    grid = np.asarray(profile["grid"], dtype=float)
    speed = np.asarray(profile["speed_kph"], dtype=float)
    brake_share = np.asarray(profile["brake_share"], dtype=float)
    lateral = np.asarray(profile["lateral_accel_mps2"], dtype=float)
    if not (
        len(grid) == len(speed) == len(brake_share) == len(lateral)
        and len(grid) >= 101
    ):
        raise ValueError("动态弯角识别要求等长且至少 101 点的距离轴")
    intervals = len(grid) - 1
    sigma = max(1.0, intervals * 0.003)
    lateral_smooth = gaussian_filter1d(lateral, sigma=sigma, mode="wrap")
    lateral_abs = np.abs(lateral_smooth)
    speed_smooth = gaussian_filter1d(speed, sigma=sigma, mode="wrap")
    peaks, properties = find_peaks(
        lateral_abs,
        height=LATERAL_ACCEL_MIN_MPS2,
        prominence=LATERAL_ACCEL_PROMINENCE_MPS2,
        distance=max(
            3,
            int(round(intervals * CORNER_MIN_SEPARATION_FRACTION)),
        ),
    )
    if not len(peaks):
        return []

    provisional: list[dict[str, int | float]] = []
    for peak_index, height, prominence in zip(
        peaks,
        properties["peak_heights"],
        properties["prominences"],
    ):
        boundary_threshold = max(4.0, min(8.0, float(height) * 0.30))
        left = int(peak_index)
        while left > 0 and lateral_abs[left - 1] >= boundary_threshold:
            left -= 1
        right = int(peak_index)
        while right < intervals and lateral_abs[right + 1] >= boundary_threshold:
            right += 1

        brake_lookback = max(5, int(round(intervals * 0.05)))
        brake_window_start = max(0, left - brake_lookback)
        braking = np.flatnonzero(
            brake_share[brake_window_start : int(peak_index) + 1]
            >= BRAKE_PREVALENCE_THRESHOLD
        )
        if len(braking):
            left = min(left, brake_window_start + int(braking[0]))
        provisional.append(
            {
                "peak": int(peak_index),
                "left": left,
                "right": right,
                "height": float(height),
                "prominence": float(prominence),
            }
        )

    # 相邻弯角的横向载荷区可能相交；以两个峰值中点切开，避免双重计时。
    for index in range(len(provisional) - 1):
        current = provisional[index]
        following = provisional[index + 1]
        if int(current["right"]) >= int(following["left"]):
            midpoint = int(
                round((int(current["peak"]) + int(following["peak"])) / 2)
            )
            current["right"] = midpoint
            following["left"] = midpoint + 1

    corners: list[dict[str, Any]] = []
    for number, row in enumerate(provisional, start=1):
        left = max(0, int(row["left"]))
        right = min(intervals, int(row["right"]))
        peak = int(row["peak"])
        if right - left < 4:
            left = max(0, peak - 2)
            right = min(intervals, peak + 2)
        window = speed_smooth[left : right + 1]
        minimum_index = left + int(np.nanargmin(window))
        minimum_status = (
            "INTERIOR"
            if left < minimum_index < right
            else "BOUNDARY_LIMITED_HIGH_SPEED_OR_COMPLEX"
        )
        minimum_speed = float(speed_smooth[minimum_index])
        corners.append(
            {
                "corner_id": f"P{number:02d}",
                "official_corner_number": None,
                "segment_role": (
                    "observed_lateral_acceleration_peak_dynamic_window_proxy"
                ),
                "entry_index": left,
                "peak_lateral_index": peak,
                "minimum_speed_index": minimum_index,
                "exit_index": right,
                "entry_rel_distance": float(grid[left]),
                "peak_lateral_rel_distance": float(grid[peak]),
                "minimum_rel_distance": float(grid[minimum_index]),
                "exit_rel_distance": float(grid[right]),
                "global_entry_speed_kph": float(speed_smooth[left]),
                "global_minimum_speed_kph": minimum_speed,
                "global_exit_speed_kph": float(speed_smooth[right]),
                "lateral_accel_peak_abs_mps2": float(row["height"]),
                "lateral_accel_prominence_mps2": float(row["prominence"]),
                "global_brake_prevalence_max": float(
                    np.nanmax(brake_share[left : right + 1])
                ),
                "speed_band": _corner_speed_band(minimum_speed),
                "minimum_location_status": minimum_status,
                "anchor_definition": (
                    "median_signed_lateral_acceleration_abs_peak;"
                    "gaussian_sigma_0.003_lap;prominence_8mps2;"
                    "dynamic_lateral_and_brake_boundary"
                ),
            }
        )
    return corners


def _unwrapped_indices(
    start: int,
    end: int,
    intervals: int,
) -> np.ndarray:
    if end >= start:
        return np.arange(start, end + 1, dtype=int)
    return np.arange(start, end + intervals + 1, dtype=int)


def build_dynamic_straight_proxies(
    corners: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Sequence[float]],
) -> list[dict[str, Any]]:
    if not corners:
        return []
    grid = np.asarray(profile["grid"], dtype=float)
    speed = np.asarray(profile["speed_kph"], dtype=float)
    throttle = np.asarray(profile["throttle_pct"], dtype=float)
    drs_share = np.asarray(profile["drs_share"], dtype=float)
    intervals = len(grid) - 1
    minimum_span = max(5, int(round(intervals * 0.01)))
    straights: list[dict[str, Any]] = []
    for index, current in enumerate(corners):
        following = corners[(index + 1) % len(corners)]
        start = int(current["exit_index"])
        end = int(following["entry_index"])
        if index == len(corners) - 1:
            end += intervals
        if end - start < minimum_span:
            continue
        indices = np.arange(start, end + 1, dtype=int)
        modulo = indices % intervals
        full_throttle_distance_share = float(
            np.mean(throttle[modulo] >= FULL_THROTTLE_THRESHOLD)
        )
        # “直道”至少要有一半距离处于近全油门；否则只是一段弯间连接，
        # 不强行发布为直道。
        if full_throttle_distance_share < 0.50:
            continue
        straights.append(
            {
                "straight_id": f"S{len(straights) + 1:02d}",
                "from_corner": current["corner_id"],
                "to_corner": following["corner_id"],
                "start_index": start % intervals,
                "end_index": end % intervals,
                "span_intervals": int(end - start),
                "start_rel_distance": float((start % intervals) / intervals),
                "end_rel_distance": float((end % intervals) / intervals),
                "span_rel_distance": float((end - start) / intervals),
                "global_max_speed_kph": float(np.nanmax(speed[modulo])),
                "global_full_throttle_distance_share": (
                    full_throttle_distance_share
                ),
                "global_drs_distance_share": float(
                    np.mean(drs_share[modulo])
                ),
                "segment_role": (
                    "dynamic_gap_between_corner_proxies_with_"
                    "majority_full_throttle_distance"
                ),
            }
        )
    return straights


def _first_sustained(mask: np.ndarray, length: int = 2) -> int | None:
    values = np.asarray(mask, dtype=bool)
    if len(values) < length:
        return None
    convolved = np.convolve(
        values.astype(int),
        np.ones(int(length), dtype=int),
        mode="valid",
    )
    hits = np.flatnonzero(convolved >= int(length))
    return int(hits[0]) if len(hits) else None


def build_segment_lap_tables(
    bundle: DistanceAxisBundle,
    corners: Sequence[Mapping[str, Any]],
    straights: Sequence[Mapping[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """计算每圈弯角、直道与加速代理表。"""

    intervals = len(bundle.grid) - 1
    speed_all = bundle.channels["speed"]
    throttle_all = bundle.channels["throttle"]
    brake_all = bundle.channels["brake"]
    drs_all = bundle.channels["drs"]
    corner_rows: list[dict[str, Any]] = []
    straight_rows: list[dict[str, Any]] = []

    for metadata in bundle.metadata.itertuples(index=False):
        axis_index = int(metadata.axis_index)
        lap_length = float(metadata.lap_length_m)
        speed = speed_all[axis_index].astype(float)
        throttle = throttle_all[axis_index].astype(float)
        brake = brake_all[axis_index].astype(float)
        drs = drs_all[axis_index].astype(float)

        for corner in corners:
            start = int(corner["entry_index"])
            end = int(corner["exit_index"])
            indices = _unwrapped_indices(start, end, intervals)
            modulo = indices % intervals
            segment_speed = speed[modulo]
            minimum_offset = int(np.nanargmin(segment_speed))
            minimum_unwrapped = int(indices[minimum_offset])
            minimum_index = minimum_unwrapped % intervals

            lookback = max(5, int(round(intervals * 0.04)))
            brake_indices = np.arange(
                max(0, start - lookback),
                minimum_unwrapped + 1,
                dtype=int,
            )
            brake_modulo = brake_indices % intervals
            brake_hit = _first_sustained(brake[brake_modulo] > 0.5, 2)
            brake_unwrapped = (
                int(brake_indices[brake_hit])
                if brake_hit is not None
                else None
            )

            recovery_end = min(
                start + intervals,
                end + max(5, int(round(intervals * 0.04))),
            )
            recovery_indices = np.arange(
                minimum_unwrapped,
                recovery_end + 1,
                dtype=int,
            )
            recovery_modulo = recovery_indices % intervals
            recovery_hit = _first_sustained(
                throttle[recovery_modulo] >= FULL_THROTTLE_THRESHOLD,
                3,
            )
            recovery_unwrapped = (
                int(recovery_indices[recovery_hit])
                if recovery_hit is not None
                else None
            )
            segment_time = integrate_speed_trace_seconds(
                segment_speed,
                distance_m=lap_length * (len(indices) - 1) / intervals,
            )
            corner_rows.append(
                {
                    "axis_index": axis_index,
                    "driver": metadata.driver,
                    "team": metadata.team,
                    "lap": int(metadata.lap),
                    "stint": int(metadata.stint),
                    "compound": metadata.compound,
                    "tyre_age": float(metadata.tyre_age),
                    "corner_id": corner["corner_id"],
                    "speed_band": corner["speed_band"],
                    "entry_speed_kph": float(speed[start % intervals]),
                    "minimum_speed_kph": float(segment_speed[minimum_offset]),
                    "exit_speed_kph": float(speed[end % intervals]),
                    "minimum_rel_distance": float(
                        minimum_index / intervals
                    ),
                    "brake_onset_rel_distance": (
                        float((brake_unwrapped % intervals) / intervals)
                        if brake_unwrapped is not None
                        else np.nan
                    ),
                    "brake_to_minimum_m": (
                        float(
                            (minimum_unwrapped - brake_unwrapped)
                            / intervals
                            * lap_length
                        )
                        if brake_unwrapped is not None
                        else np.nan
                    ),
                    "throttle_recovery_rel_distance": (
                        float((recovery_unwrapped % intervals) / intervals)
                        if recovery_unwrapped is not None
                        else np.nan
                    ),
                    "minimum_to_throttle_recovery_m": (
                        float(
                            (recovery_unwrapped - minimum_unwrapped)
                            / intervals
                            * lap_length
                        )
                        if recovery_unwrapped is not None
                        else np.nan
                    ),
                    "segment_time_s": segment_time,
                    "segment_distance_m": float(
                        lap_length * (len(indices) - 1) / intervals
                    ),
                    "observed_brake_onset": brake_unwrapped is not None,
                    "feature_role": (
                        "distance_axis_observed_corner_proxy_not_official_anchor"
                    ),
                }
            )

        for straight in straights:
            start = int(straight["start_index"])
            span = int(straight["span_intervals"])
            indices = np.arange(start, start + span + 1, dtype=int)
            modulo = indices % intervals
            segment_speed = speed[modulo]
            segment_throttle = throttle[modulo]
            segment_drs = drs[modulo]
            relative_m = (
                np.arange(len(indices), dtype=float)
                * lap_length
                / intervals
            )
            gradient = np.gradient(segment_speed, relative_m)
            acceleration_mask = (
                (segment_speed >= 150.0)
                & (segment_speed <= 250.0)
                & (segment_throttle >= FULL_THROTTLE_THRESHOLD)
                & (gradient > 0)
            )
            acceleration_slope = (
                float(
                    np.polyfit(
                        relative_m[acceleration_mask],
                        segment_speed[acceleration_mask],
                        1,
                    )[0]
                    * 100.0
                )
                if int(acceleration_mask.sum()) >= 5
                and float(np.ptp(relative_m[acceleration_mask])) >= 20.0
                else np.nan
            )
            straight_rows.append(
                {
                    "axis_index": axis_index,
                    "driver": metadata.driver,
                    "team": metadata.team,
                    "lap": int(metadata.lap),
                    "stint": int(metadata.stint),
                    "compound": metadata.compound,
                    "tyre_age": float(metadata.tyre_age),
                    "straight_id": straight["straight_id"],
                    "max_speed_kph": float(np.nanmax(segment_speed)),
                    "p95_speed_kph": float(
                        np.nanquantile(segment_speed, 0.95)
                    ),
                    "full_throttle_distance_share": float(
                        np.mean(
                            segment_throttle >= FULL_THROTTLE_THRESHOLD
                        )
                    ),
                    "drs_distance_share": float(
                        np.mean(segment_drs > 0.5)
                    ),
                    "acceleration_150_250_kph_per_100m": (
                        acceleration_slope
                    ),
                    "segment_time_s": integrate_speed_trace_seconds(
                        segment_speed,
                        distance_m=lap_length * span / intervals,
                    ),
                    "segment_distance_m": float(
                        lap_length * span / intervals
                    ),
                    "feature_role": (
                        "observed_straight_speed_control_proxy_"
                        "not_engine_power_or_drag"
                    ),
                }
            )

    corner_frame = pd.DataFrame(corner_rows)
    straight_frame = pd.DataFrame(straight_rows)
    acceleration = (
        straight_frame.groupby("axis_index", as_index=False)
        .agg(
            acceleration_150_250_kph_per_100m=(
                "acceleration_150_250_kph_per_100m",
                _finite_median,
            ),
            straight_max_speed_kph=("max_speed_kph", "max"),
            straight_full_throttle_distance_share=(
                "full_throttle_distance_share",
                "mean",
            ),
        )
    )
    lap_metrics = bundle.metadata.merge(
        acceleration,
        on="axis_index",
        how="left",
        validate="one_to_one",
    )
    return corner_frame, straight_frame, lap_metrics


def _aggregate_corner_features(
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (corner_id, speed_band), group in frame.groupby(
        ["corner_id", "speed_band"],
        sort=True,
    ):
        rows.append(
            {
                "corner_id": str(corner_id),
                "speed_band": str(speed_band),
                "valid_laps": int(group["lap"].nunique()),
                "entry_speed_kph": _number(
                    _finite_median(group["entry_speed_kph"])
                ),
                "minimum_speed_kph": _number(
                    _finite_median(group["minimum_speed_kph"])
                ),
                "exit_speed_kph": _number(
                    _finite_median(group["exit_speed_kph"])
                ),
                "brake_onset_rel_distance": _number(
                    _finite_median(group["brake_onset_rel_distance"])
                ),
                "brake_to_minimum_m": _number(
                    _finite_median(group["brake_to_minimum_m"])
                ),
                "throttle_recovery_rel_distance": _number(
                    _finite_median(
                        group["throttle_recovery_rel_distance"]
                    )
                ),
                "minimum_to_throttle_recovery_m": _number(
                    _finite_median(
                        group["minimum_to_throttle_recovery_m"]
                    )
                ),
                "segment_time_s": _number(
                    _finite_median(group["segment_time_s"])
                ),
                "brake_observation_share": _number(
                    group["observed_brake_onset"].mean()
                ),
                "feature_role": (
                    "median_observed_corner_proxy_not_vehicle_physics"
                ),
            }
        )
    return rows


def _aggregate_straight_features(
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for straight_id, group in frame.groupby("straight_id", sort=True):
        rows.append(
            {
                "straight_id": str(straight_id),
                "valid_laps": int(group["lap"].nunique()),
                "max_speed_kph": _number(group["max_speed_kph"].median()),
                "p95_speed_kph": _number(group["p95_speed_kph"].median()),
                "full_throttle_distance_share": _number(
                    group["full_throttle_distance_share"].median()
                ),
                "drs_distance_share": _number(
                    group["drs_distance_share"].median()
                ),
                "acceleration_150_250_kph_per_100m": _number(
                    _finite_median(
                        group["acceleration_150_250_kph_per_100m"]
                    )
                ),
                "segment_time_s": _number(group["segment_time_s"].median()),
                "feature_role": (
                    "median_observed_straight_proxy_not_power_or_drag"
                ),
            }
        )
    return rows


def build_driver_profiles(
    valid_points: pd.DataFrame,
    lap_metrics: pd.DataFrame,
    corner_frame: pd.DataFrame,
    straight_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for driver, driver_laps in lap_metrics.groupby("driver", sort=True):
        driver_points = valid_points[
            valid_points["driver_acronym"].astype(str).eq(str(driver))
        ]
        driver_corners = corner_frame[
            corner_frame["driver"].astype(str).eq(str(driver))
        ]
        driver_straights = straight_frame[
            straight_frame["driver"].astype(str).eq(str(driver))
        ]
        speed = pd.to_numeric(driver_points["speed"], errors="coerce")
        top15 = top_n_mean(speed, TOP_SPEED_SAMPLE_COUNT)
        corner_band_values: dict[str, float | None] = {}
        for band in ("low_speed", "medium_speed", "high_speed"):
            values = driver_corners.loc[
                driver_corners["speed_band"].eq(band),
                "minimum_speed_kph",
            ]
            corner_band_values[band] = (
                float(values.median()) if len(values) else None
            )
        profiles.append(
            {
                "driver": str(driver),
                "team": str(driver_laps["team"].iloc[0]),
                "valid_laps": int(driver_laps["lap"].nunique()),
                "telemetry_samples": int(len(driver_points)),
                "top_speed": {
                    "faithful_top15_sample_mean_kph": _number(top15),
                    "faithful_max_sample_kph": _number(speed.max()),
                    "faithful_sample_count": TOP_SPEED_SAMPLE_COUNT,
                    "audited_median_lap_p99_kph": _number(
                        driver_laps["lap_speed_p99_kph"].median()
                    ),
                    "faithful_minus_audited_kph": _number(
                        float(top15)
                        - float(driver_laps["lap_speed_p99_kph"].median())
                    )
                    if top15 is not None
                    else None,
                    "public_formula_role": (
                        "mean_of_15_highest_speed_samples"
                    ),
                    "audited_formula_role": (
                        "median_of_per_lap_p99_speed_avoids_single_"
                        "straight_sample_density_dominance"
                    ),
                },
                "control_usage": {
                    "faithful_sample_full_throttle_share": _number(
                        (
                            pd.to_numeric(
                                driver_points["throttle"], errors="coerce"
                            )
                            >= FULL_THROTTLE_THRESHOLD
                        ).mean()
                    ),
                    "audited_time_full_throttle_share": _number(
                        driver_laps["time_full_throttle_share"].mean()
                    ),
                    "sample_minus_time_full_throttle_pp": _number(
                        100.0
                        * (
                            (
                                pd.to_numeric(
                                    driver_points["throttle"],
                                    errors="coerce",
                                )
                                >= FULL_THROTTLE_THRESHOLD
                            ).mean()
                            - driver_laps[
                                "time_full_throttle_share"
                            ].mean()
                        )
                    ),
                    "time_brake_share": _number(
                        driver_laps["time_brake_share"].mean()
                    ),
                    "time_drs_share": _number(
                        driver_laps["time_drs_share"].mean()
                    ),
                    "median_gear": _number(
                        pd.to_numeric(
                            driver_points["gear"], errors="coerce"
                        ).median()
                    ),
                    "role": (
                        "observed_control_usage_sample_and_time_weighted"
                    ),
                },
                "speed_intervals": {
                    "corner_minimum_speed_by_band_kph": {
                        key: _number(value)
                        for key, value in corner_band_values.items()
                    },
                    "straight_acceleration_150_250_kph_per_100m": _number(
                        _finite_median(
                            driver_straights[
                                "acceleration_150_250_kph_per_100m"
                            ]
                        )
                    ),
                    "role": (
                        "observed_speed_and_acceleration_proxy_"
                        "not_grip_downforce_power_or_drag"
                    ),
                },
                "visual_tyre_age_change": {
                    "raw_lap_time_s_per_tyre_lap": _number(
                        linear_slope(
                            driver_laps["tyre_age"],
                            driver_laps["lap_duration_s"],
                        )
                    ),
                    "raw_median_speed_kph_per_tyre_lap": _number(
                        linear_slope(
                            driver_laps["tyre_age"],
                            driver_laps["lap_speed_median_kph"],
                        )
                    ),
                    "tyre_age_min_laps": _number(
                        driver_laps["tyre_age"].min()
                    ),
                    "tyre_age_max_laps": _number(
                        driver_laps["tyre_age"].max()
                    ),
                    "role": (
                        "faithful_raw_observed_change_not_physical_tyre_wear"
                    ),
                },
                "corner_features": _aggregate_corner_features(
                    driver_corners
                ),
                "straight_features": _aggregate_straight_features(
                    driver_straights
                ),
            }
        )
    return profiles


def build_team_feature_profiles(
    driver_profiles: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for profile in driver_profiles:
        grouped[str(profile["team"])].append(profile)

    def average(rows: Sequence[Mapping[str, Any]], path: Sequence[str]) -> float | None:
        values: list[float] = []
        for row in rows:
            value: Any = row
            for part in path:
                value = value.get(part) if isinstance(value, Mapping) else None
            if _number(value) is not None:
                values.append(float(value))
        return float(np.mean(values)) if values else None

    team_profiles: list[dict[str, Any]] = []
    for team, rows in sorted(grouped.items()):
        team_profiles.append(
            {
                "team": team,
                "drivers": sorted(str(row["driver"]) for row in rows),
                "valid_laps": int(sum(int(row["valid_laps"]) for row in rows)),
                "observed_features": {
                    "top15_speed_kph": _number(
                        average(
                            rows,
                            (
                                "top_speed",
                                "faithful_top15_sample_mean_kph",
                            ),
                        )
                    ),
                    "robust_top_speed_kph": _number(
                        average(
                            rows,
                            (
                                "top_speed",
                                "audited_median_lap_p99_kph",
                            ),
                        )
                    ),
                    "full_throttle_time_share": _number(
                        average(
                            rows,
                            (
                                "control_usage",
                                "audited_time_full_throttle_share",
                            ),
                        )
                    ),
                    "acceleration_150_250_kph_per_100m": _number(
                        average(
                            rows,
                            (
                                "speed_intervals",
                                "straight_acceleration_150_250_kph_per_100m",
                            ),
                        )
                    ),
                    "low_speed_corner_min_kph": _number(
                        average(
                            rows,
                            (
                                "speed_intervals",
                                "corner_minimum_speed_by_band_kph",
                                "low_speed",
                            ),
                        )
                    ),
                    "medium_speed_corner_min_kph": _number(
                        average(
                            rows,
                            (
                                "speed_intervals",
                                "corner_minimum_speed_by_band_kph",
                                "medium_speed",
                            ),
                        )
                    ),
                    "high_speed_corner_min_kph": _number(
                        average(
                            rows,
                            (
                                "speed_intervals",
                                "corner_minimum_speed_by_band_kph",
                                "high_speed",
                            ),
                        )
                    ),
                },
                "profile_role": (
                    "team_aggregate_observed_output_features_"
                    "not_vehicle_causal_strength"
                ),
            }
        )

    metric_labels = {
        "top15_speed_kph": "最高 15 个速度样本均值",
        "robust_top_speed_kph": "逐圈 P99 中位数",
        "full_throttle_time_share": "时间加权全油门比例",
        "acceleration_150_250_kph_per_100m": "150–250 km/h 加速斜率代理",
        "low_speed_corner_min_kph": "低速弯最低速度代理",
        "medium_speed_corner_min_kph": "中速弯最低速度代理",
        "high_speed_corner_min_kph": "高速弯最低速度代理",
    }
    feature_ranks: list[dict[str, Any]] = []
    for metric, label in metric_labels.items():
        finite_rows = [
            row
            for row in team_profiles
            if _number(row["observed_features"].get(metric)) is not None
        ]
        ordered = sorted(
            finite_rows,
            key=lambda row: (
                -float(row["observed_features"][metric]),
                str(row["team"]),
            ),
        )
        for rank, row in enumerate(ordered, start=1):
            feature_ranks.append(
                {
                    "metric": metric,
                    "label": label,
                    "rank": rank,
                    "field_size": len(ordered),
                    "team": row["team"],
                    "value": row["observed_features"][metric],
                    "ranking_role": (
                        "single_observed_feature_order_no_total_score"
                    ),
                }
            )
    return team_profiles, feature_ranks


def attach_traffic_features(
    lap_metrics: pd.DataFrame,
    traffic_features: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "driver",
        "lap",
        "audited_traffic_ratio",
        "missing",
        "observed_time_s",
    }
    missing = sorted(required.difference(traffic_features.columns))
    if missing:
        raise ValueError("交通特征缺少字段：" + ", ".join(missing))
    selected = traffic_features[
        [
            "driver",
            "lap",
            "audited_traffic_ratio",
            "missing",
            "observed_time_s",
        ]
    ].rename(
        columns={
            "audited_traffic_ratio": "traffic_ratio",
            "missing": "traffic_missing_share",
            "observed_time_s": "traffic_observed_time_s",
        }
    )
    frame = lap_metrics.merge(
        selected,
        on=["driver", "lap"],
        how="left",
        validate="one_to_one",
    )
    maximum_lap = max(1.0, float(frame["lap"].max()))
    frame["lap_fraction"] = frame["lap"] / maximum_lap
    frame["clean_air_share"] = 1.0 - pd.to_numeric(
        frame["traffic_ratio"], errors="coerce"
    )
    frame["candidate_weight"] = (
        frame["clean_air_share"].clip(lower=0.0, upper=1.0) ** 2
        * frame["telemetry_coverage"].clip(lower=0.0, upper=1.0)
    )
    frame["audited_candidate"] = (
        frame["pace_eligible"]
        & frame["is_accurate"]
        & ~frame["deleted"]
        & frame["traffic_ratio"].notna()
        & frame["traffic_ratio"].le(TRAFFIC_RATIO_MAX)
        & frame["compound"].notna()
        & ~frame["compound"].isin({"", "NONE", "UNKNOWN", "NAN"})
        & frame["tyre_age"].notna()
    )
    return frame


def build_tyre_age_audit(
    lap_metrics: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stint_rows: list[dict[str, Any]] = []
    candidates = lap_metrics.loc[lap_metrics["audited_candidate"]].copy()
    for (driver, team, stint, compound), group in candidates.groupby(
        ["driver", "team", "stint", "compound"],
        sort=True,
    ):
        count = int(len(group))
        span = float(group["tyre_age"].max() - group["tyre_age"].min())
        ess = kish_effective_sample_size(group["candidate_weight"])
        supported = (
            count >= MIN_DEGRADATION_LAPS
            and span >= MIN_DEGRADATION_TYRE_SPAN
            and ess >= MIN_DEGRADATION_LAPS
        )
        scenario_slopes: dict[str, float | None] = {}
        for scenario, fuel_discount in FUEL_SCENARIOS_S_PER_LAP.items():
            adjusted = (
                group["lap_duration_s"].to_numpy(dtype=float)
                + float(fuel_discount)
                * (group["lap"].to_numpy(dtype=float) - 1.0)
            )
            scenario_slopes[scenario] = (
                robust_theil_sen_slope(group["tyre_age"], adjusted)
                if supported
                else None
            )
        stint_rows.append(
            {
                "driver": str(driver),
                "team": str(team),
                "stint": int(stint),
                "compound": str(compound),
                "candidate_laps": count,
                "kish_ess": ess,
                "tyre_age_span_laps": span,
                "lap_range": [
                    int(group["lap"].min()),
                    int(group["lap"].max()),
                ],
                "traffic_ratio_mean": float(group["traffic_ratio"].mean()),
                "fuel_scenario_lap_time_s_per_tyre_lap": scenario_slopes,
                "median_speed_kph_per_tyre_lap": (
                    robust_theil_sen_slope(
                        group["tyre_age"],
                        group["lap_speed_median_kph"],
                    )
                    if supported
                    else None
                ),
                "status": (
                    "SUPPORTED_CONDITIONAL_PROXY"
                    if supported
                    else "AUDIT_ONLY_INSUFFICIENT_SUPPORT"
                ),
                "role": (
                    "within_stint_clean_air_theil_sen_named_fuel_"
                    "scenario_not_physical_tyre_wear"
                ),
            }
        )

    supported_rows = [
        row
        for row in stint_rows
        if row["status"] == "SUPPORTED_CONDITIONAL_PROXY"
    ]
    by_driver: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in supported_rows:
        by_driver[str(row["driver"])].append(row)
    drivers = sorted(str(value) for value in lap_metrics["driver"].unique())
    driver_rows: list[dict[str, Any]] = []
    for driver in drivers:
        rows = by_driver.get(driver, [])
        team_values = lap_metrics.loc[
            lap_metrics["driver"].eq(driver), "team"
        ]
        team = str(team_values.iloc[0]) if len(team_values) else None
        weights = [float(row["kish_ess"]) for row in rows]
        scenario_summary: dict[str, Any] = {}
        for scenario in FUEL_SCENARIOS_S_PER_LAP:
            values = [
                row["fuel_scenario_lap_time_s_per_tyre_lap"][scenario]
                for row in rows
            ]
            finite_values = [
                float(value)
                for value in values
                if _number(value) is not None
            ]
            scenario_summary[scenario] = {
                "weighted_mean_s_per_tyre_lap": _number(
                    weighted_mean(values, weights)
                ),
                "stint_range_s_per_tyre_lap": [
                    _number(min(finite_values)),
                    _number(max(finite_values)),
                ]
                if finite_values
                else None,
            }
        speed_values = [
            row["median_speed_kph_per_tyre_lap"] for row in rows
        ]
        driver_rows.append(
            {
                "driver": driver,
                "team": team,
                "supported_stints": len(rows),
                "candidate_laps": int(
                    sum(int(row["candidate_laps"]) for row in rows)
                ),
                "aggregate_kish_ess": float(sum(weights)),
                "fuel_scenarios": scenario_summary,
                "median_speed_kph_per_tyre_lap": _number(
                    weighted_mean(speed_values, weights)
                ),
                "status": (
                    "SUPPORTED_CONDITIONAL_PROXY"
                    if rows
                    else "AUDIT_ONLY_INSUFFICIENT_SUPPORT"
                ),
                "physical_tyre_wear_identifiable": False,
                "track_evolution_separable": False,
                "role": (
                    "conditional_observed_tyre_age_change_not_"
                    "physical_degradation"
                ),
            }
        )
    return driver_rows, stint_rows


def _bootstrap_weighted_mean_ci(
    values: Sequence[float],
    weights: Sequence[float],
    *,
    seed: int,
    repetitions: int = 1000,
) -> list[float] | None:
    value_array = np.asarray(values, dtype=float)
    weight_array = np.asarray(weights, dtype=float)
    finite = (
        np.isfinite(value_array)
        & np.isfinite(weight_array)
        & (weight_array > 0)
    )
    value_array, weight_array = value_array[finite], weight_array[finite]
    if len(value_array) < 3:
        return None
    rng = np.random.default_rng(seed)
    estimates = np.empty(int(repetitions), dtype=float)
    for index in range(int(repetitions)):
        selected = rng.integers(0, len(value_array), len(value_array))
        estimates[index] = np.average(
            value_array[selected],
            weights=weight_array[selected],
        )
    return [
        float(np.quantile(estimates, 0.025)),
        float(np.quantile(estimates, 0.975)),
    ]


def _condition_assignment(
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> list[tuple[int, int, float]]:
    """在可观测条件门内做一对一最小代价匹配。"""

    if left.empty or right.empty:
        return []
    cost = np.full((len(left), len(right)), 1e6, dtype=float)
    for left_position, left_row in enumerate(left.itertuples(index=False)):
        for right_position, right_row in enumerate(
            right.itertuples(index=False)
        ):
            if str(left_row.compound) != str(right_row.compound):
                continue
            age_gap = abs(float(left_row.tyre_age) - float(right_row.tyre_age))
            phase_gap = abs(
                float(left_row.lap_fraction) - float(right_row.lap_fraction)
            )
            traffic_gap = abs(
                float(left_row.traffic_ratio)
                - float(right_row.traffic_ratio)
            )
            if age_gap > 2.0 or phase_gap > 0.12 or traffic_gap > 0.15:
                continue
            cost[left_position, right_position] = (
                age_gap / 2.0
                + phase_gap / 0.12
                + traffic_gap / 0.15
            )
    rows, columns = linear_sum_assignment(cost)
    return [
        (int(row), int(column), float(cost[row, column]))
        for row, column in zip(rows, columns)
        if cost[row, column] < 1e5
    ]


def build_condition_matched_teammates(
    lap_metrics: pd.DataFrame,
    corner_frame: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = lap_metrics.loc[lap_metrics["audited_candidate"]].copy()
    corner_lookup = corner_frame.set_index(["axis_index", "corner_id"])
    corner_ids = sorted(str(value) for value in corner_frame["corner_id"].unique())
    comparisons: list[dict[str, Any]] = []
    matched_ledger: list[dict[str, Any]] = []
    for team, team_frame in candidates.groupby("team", sort=True):
        drivers = sorted(str(value) for value in team_frame["driver"].unique())
        if len(drivers) != 2:
            continue
        left_driver, right_driver = drivers
        left = (
            team_frame.loc[team_frame["driver"].eq(left_driver)]
            .sort_values("lap")
            .reset_index(drop=True)
        )
        right = (
            team_frame.loc[team_frame["driver"].eq(right_driver)]
            .sort_values("lap")
            .reset_index(drop=True)
        )
        assignments = _condition_assignment(left, right)
        pair_values: dict[str, list[float]] = defaultdict(list)
        pair_weights: list[float] = []
        corner_values: dict[str, list[float]] = defaultdict(list)
        corner_weights: dict[str, list[float]] = defaultdict(list)
        age_gaps: list[float] = []
        phase_gaps: list[float] = []
        traffic_gaps: list[float] = []
        compounds: set[str] = set()
        for left_position, right_position, cost in assignments:
            left_row = left.iloc[left_position]
            right_row = right.iloc[right_position]
            weight = float(
                np.sqrt(
                    max(float(left_row["candidate_weight"]), 0.0)
                    * max(float(right_row["candidate_weight"]), 0.0)
                )
            )
            if weight <= 0:
                continue
            pair_weights.append(weight)
            pair_values["lap_time_delta_s"].append(
                float(left_row["lap_duration_s"])
                - float(right_row["lap_duration_s"])
            )
            pair_values["top_speed_p99_delta_kph"].append(
                float(left_row["lap_speed_p99_kph"])
                - float(right_row["lap_speed_p99_kph"])
            )
            pair_values["full_throttle_time_delta_pp"].append(
                100.0
                * (
                    float(left_row["time_full_throttle_share"])
                    - float(right_row["time_full_throttle_share"])
                )
            )
            pair_values["acceleration_150_250_delta"].append(
                float(
                    left_row["acceleration_150_250_kph_per_100m"]
                )
                - float(
                    right_row["acceleration_150_250_kph_per_100m"]
                )
                if _number(
                    left_row["acceleration_150_250_kph_per_100m"]
                )
                is not None
                and _number(
                    right_row["acceleration_150_250_kph_per_100m"]
                )
                is not None
                else np.nan
            )
            age_gap = abs(
                float(left_row["tyre_age"]) - float(right_row["tyre_age"])
            )
            phase_gap = abs(
                float(left_row["lap_fraction"])
                - float(right_row["lap_fraction"])
            )
            traffic_gap = abs(
                float(left_row["traffic_ratio"])
                - float(right_row["traffic_ratio"])
            )
            age_gaps.append(age_gap)
            phase_gaps.append(phase_gap)
            traffic_gaps.append(traffic_gap)
            compounds.add(str(left_row["compound"]))
            for corner_id in corner_ids:
                left_key = (int(left_row["axis_index"]), corner_id)
                right_key = (int(right_row["axis_index"]), corner_id)
                if (
                    left_key not in corner_lookup.index
                    or right_key not in corner_lookup.index
                ):
                    continue
                left_corner = corner_lookup.loc[left_key]
                right_corner = corner_lookup.loc[right_key]
                corner_values[corner_id].append(
                    float(left_corner["segment_time_s"])
                    - float(right_corner["segment_time_s"])
                )
                corner_weights[corner_id].append(weight)
            matched_ledger.append(
                {
                    "team": str(team),
                    "left_driver": left_driver,
                    "right_driver": right_driver,
                    "left_lap": int(left_row["lap"]),
                    "right_lap": int(right_row["lap"]),
                    "compound": str(left_row["compound"]),
                    "left_tyre_age": float(left_row["tyre_age"]),
                    "right_tyre_age": float(right_row["tyre_age"]),
                    "left_lap_fraction": float(left_row["lap_fraction"]),
                    "right_lap_fraction": float(right_row["lap_fraction"]),
                    "left_traffic_ratio": float(left_row["traffic_ratio"]),
                    "right_traffic_ratio": float(right_row["traffic_ratio"]),
                    "assignment_cost": cost,
                    "pair_weight": weight,
                }
            )

        matched_count = len(pair_weights)
        ess = kish_effective_sample_size(pair_weights)
        gate_failures: list[str] = []
        if matched_count < MIN_MATCHED_LAPS:
            gate_failures.append(
                f"matched_laps={matched_count}<{MIN_MATCHED_LAPS}"
            )
        if ess < MIN_MATCHED_ESS:
            gate_failures.append(f"kish_ess={ess:.3f}<{MIN_MATCHED_ESS}")
        if not compounds:
            gate_failures.append("no_common_compound")
        comparable = not gate_failures
        corner_deltas = [
            {
                "corner_id": corner_id,
                "left_minus_right_segment_time_s": _number(
                    weighted_mean(
                        corner_values[corner_id],
                        corner_weights[corner_id],
                    )
                ),
                "matched_laps": len(corner_values[corner_id]),
                "role": "same_team_condition_matched_segment_time_proxy",
            }
            for corner_id in corner_ids
            if corner_values[corner_id]
        ]
        lap_delta_values = pair_values.get("lap_time_delta_s", [])
        comparisons.append(
            {
                "team": str(team),
                "left_driver": left_driver,
                "right_driver": right_driver,
                "matched_laps": matched_count,
                "kish_ess": ess,
                "common_compounds": sorted(compounds),
                "condition_balance": {
                    "mean_abs_tyre_age_gap_laps": _number(
                        np.mean(age_gaps) if age_gaps else None
                    ),
                    "mean_abs_lap_fraction_gap": _number(
                        np.mean(phase_gaps) if phase_gaps else None
                    ),
                    "mean_abs_traffic_ratio_gap": _number(
                        np.mean(traffic_gaps) if traffic_gaps else None
                    ),
                },
                "left_minus_right": {
                    "lap_time_s": _number(
                        weighted_mean(lap_delta_values, pair_weights)
                    ),
                    "lap_time_bootstrap_95_ci_s": [
                        _number(value)
                        for value in (
                            _bootstrap_weighted_mean_ci(
                                lap_delta_values,
                                pair_weights,
                                seed=20260731 + len(comparisons),
                            )
                            or []
                        )
                    ]
                    or None,
                    "top_speed_p99_kph": _number(
                        weighted_mean(
                            pair_values.get("top_speed_p99_delta_kph", []),
                            pair_weights,
                        )
                    ),
                    "full_throttle_time_pp": _number(
                        weighted_mean(
                            pair_values.get(
                                "full_throttle_time_delta_pp", []
                            ),
                            pair_weights,
                        )
                    ),
                    "acceleration_150_250_kph_per_100m": _number(
                        weighted_mean(
                            pair_values.get(
                                "acceleration_150_250_delta", []
                            ),
                            pair_weights,
                        )
                    ),
                },
                "right_minus_left": {
                    "lap_time_s": _number(
                        -float(weighted_mean(lap_delta_values, pair_weights))
                    )
                    if weighted_mean(lap_delta_values, pair_weights)
                    is not None
                    else None,
                    "top_speed_p99_kph": _number(
                        -float(
                            weighted_mean(
                                pair_values.get(
                                    "top_speed_p99_delta_kph", []
                                ),
                                pair_weights,
                            )
                        )
                    )
                    if weighted_mean(
                        pair_values.get("top_speed_p99_delta_kph", []),
                        pair_weights,
                    )
                    is not None
                    else None,
                    "full_throttle_time_pp": _number(
                        -float(
                            weighted_mean(
                                pair_values.get(
                                    "full_throttle_time_delta_pp", []
                                ),
                                pair_weights,
                            )
                        )
                    )
                    if weighted_mean(
                        pair_values.get("full_throttle_time_delta_pp", []),
                        pair_weights,
                    )
                    is not None
                    else None,
                },
                "corner_segment_deltas": corner_deltas,
                "audited_status": (
                    "COMPARABLE"
                    if comparable
                    else "AUDIT_ONLY_INSUFFICIENT_SUPPORT"
                ),
                "gate_failures": gate_failures,
                "comparison_role": (
                    "same_team_condition_matched_observed_delivery_proxy_"
                    "not_driver_or_vehicle_causal_effect"
                ),
            }
        )
    return comparisons, matched_ledger


def segmentation_stability(
    profile: Mapping[str, Sequence[float]],
    *,
    axis_intervals: Sequence[int] = (800, 1000, 1200),
) -> dict[str, Any]:
    source_grid = np.asarray(profile["grid"], dtype=float)
    baseline = detect_dynamic_corner_proxies(profile)
    baseline_centres = np.asarray(
        [row["peak_lateral_rel_distance"] for row in baseline],
        dtype=float,
    )
    rows: list[dict[str, Any]] = []
    for intervals in axis_intervals:
        grid = np.linspace(0.0, 1.0, int(intervals) + 1)
        resampled = {
            key: (
                grid
                if key == "grid"
                else np.interp(grid, source_grid, np.asarray(value, dtype=float))
            )
            for key, value in profile.items()
        }
        corners = detect_dynamic_corner_proxies(resampled)
        centres = np.asarray(
            [row["peak_lateral_rel_distance"] for row in corners],
            dtype=float,
        )
        used: set[int] = set()
        drifts: list[float] = []
        for centre in baseline_centres:
            candidates = [
                (abs(float(value) - float(centre)), index)
                for index, value in enumerate(centres)
                if index not in used
            ]
            if not candidates:
                continue
            drift, selected = min(candidates)
            if drift <= 0.015:
                used.add(selected)
                drifts.append(drift)
        rows.append(
            {
                "axis_intervals": int(intervals),
                "corner_count": len(corners),
                "matched_to_baseline_within_1_5pct": len(drifts),
                "median_peak_drift_fraction": (
                    float(np.median(drifts)) if drifts else None
                ),
                "max_peak_drift_fraction": (
                    float(np.max(drifts)) if drifts else None
                ),
            }
        )
    passed = all(
        row["matched_to_baseline_within_1_5pct"]
        >= max(1, int(np.floor(len(baseline) * 0.8)))
        and abs(row["corner_count"] - len(baseline)) <= 2
        for row in rows
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "baseline_corner_count": len(baseline),
        "runs": rows,
        "gate": (
            "at_least_80pct_baseline_peaks_matched_within_0.015_lap_"
            "and_count_difference_le_2"
        ),
    }


def compact_track_profile(
    profile: Mapping[str, Sequence[float]],
    *,
    every: int = 4,
) -> list[dict[str, Any]]:
    grid = np.asarray(profile["grid"], dtype=float)
    indices = list(range(0, len(grid), max(1, int(every))))
    if indices[-1] != len(grid) - 1:
        indices.append(len(grid) - 1)
    rows: list[dict[str, Any]] = []
    for index in indices:
        rows.append(
            {
                "rel_distance": _number(grid[index], 6),
                "speed_kph": _number(profile["speed_kph"][index]),
                "throttle_pct": _number(profile["throttle_pct"][index]),
                "brake_share": _number(profile["brake_share"][index]),
                "gear": _number(profile["gear"][index]),
                "drs_share": _number(profile["drs_share"][index]),
                "lateral_accel_mps2": _number(
                    profile["lateral_accel_mps2"][index]
                ),
                "longitudinal_accel_mps2": _number(
                    profile["longitudinal_accel_mps2"][index]
                ),
            }
        )
    return rows


def build_fdataanalysis_reverse_engineered_v2(
    points: pd.DataFrame,
    *,
    traffic_features: pd.DataFrame,
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    """运行 2025 Abu Dhabi Race 的真实 FDataAnalysis v2 pilot。"""

    bundle, lap_table, valid_points = build_distance_axis_bundle(points)
    profile = build_global_axis_profile(bundle)
    corners = detect_dynamic_corner_proxies(profile)
    straights = build_dynamic_straight_proxies(corners, profile)
    corner_frame, straight_frame, lap_metrics = build_segment_lap_tables(
        bundle,
        corners,
        straights,
    )
    lap_metrics = attach_traffic_features(lap_metrics, traffic_features)
    driver_profiles = build_driver_profiles(
        valid_points,
        lap_metrics,
        corner_frame,
        straight_frame,
    )
    team_profiles, feature_ranks = build_team_feature_profiles(
        driver_profiles
    )
    tyre_age_drivers, tyre_age_stints = build_tyre_age_audit(lap_metrics)
    teammate_comparisons, matched_ledger = (
        build_condition_matched_teammates(lap_metrics, corner_frame)
    )
    stability = segmentation_stability(profile)

    reconstruction_abs = (
        lap_metrics["axis_time_error_s"].abs().dropna().to_numpy(dtype=float)
    )
    top_speed_differences = [
        abs(float(row["top_speed"]["faithful_minus_audited_kph"]))
        for row in driver_profiles
        if _number(row["top_speed"]["faithful_minus_audited_kph"])
        is not None
    ]
    throttle_differences = [
        abs(float(row["control_usage"]["sample_minus_time_full_throttle_pp"]))
        for row in driver_profiles
        if _number(
            row["control_usage"]["sample_minus_time_full_throttle_pp"]
        )
        is not None
    ]
    antisymmetry_errors: list[float] = []
    for row in teammate_comparisons:
        for metric in (
            "lap_time_s",
            "top_speed_p99_kph",
            "full_throttle_time_pp",
        ):
            left_value = row["left_minus_right"].get(metric)
            right_value = row["right_minus_left"].get(metric)
            if _number(left_value) is not None and _number(right_value) is not None:
                antisymmetry_errors.append(
                    abs(float(left_value) + float(right_value))
                )
    comparable_pairs = [
        row
        for row in teammate_comparisons
        if row["audited_status"] == "COMPARABLE"
    ]
    excluded_by_reason = {
        "first_lap": int(lap_table["first_lap"].sum()),
        "pit_boundary_lap": int(lap_table["pit_boundary_lap"].sum()),
        "non_green_or_yellow": int((~lap_table["green_or_yellow"]).sum()),
        "deleted": int(lap_table["deleted"].sum()),
        "inaccurate": int((~lap_table["is_accurate"]).sum()),
    }
    candidate_disposition = {
        "axis_laps": int(len(lap_metrics)),
        "audited_candidates": int(lap_metrics["audited_candidate"].sum()),
        "traffic_ratio_above_20pct": int(
            (
                lap_metrics["traffic_ratio"].notna()
                & lap_metrics["traffic_ratio"].gt(TRAFFIC_RATIO_MAX)
            ).sum()
        ),
        "traffic_not_reconstructable": int(
            lap_metrics["traffic_ratio"].isna().sum()
        ),
        "deleted_or_inaccurate": int(
            (
                lap_metrics["deleted"]
                | ~lap_metrics["is_accurate"]
            ).sum()
        ),
    }
    validation = {
        "status": (
            "PASS"
            if len(corners) >= 10
            and stability["status"] == "PASS"
            and float(np.mean(reconstruction_abs)) <= 0.25
            and float(np.quantile(reconstruction_abs, 0.90)) <= 0.50
            else "FAIL"
        ),
        "real_source_only": True,
        "synthetic_points": 0,
        "raw_points": int(len(points)),
        "valid_points": int(len(valid_points)),
        "axis_interpolated_laps": int(len(lap_metrics)),
        "axis_intervals": AXIS_INTERVALS,
        "corner_proxy_count": len(corners),
        "straight_proxy_count": len(straights),
        "sample_frequency": {
            "median_interval_s": float(bundle.sample_interval_median_s),
            "approx_hz": float(1.0 / bundle.sample_interval_median_s),
            "role": "public_feed_sampling_approximation",
        },
        "lap_time_reconstruction": {
            "mean_signed_error_s": float(
                lap_metrics["axis_time_error_s"].mean()
            ),
            "mae_s": float(np.mean(reconstruction_abs)),
            "p90_abs_error_s": float(
                np.quantile(reconstruction_abs, 0.90)
            ),
            "max_abs_error_s": float(np.max(reconstruction_abs)),
            "gate": "mae<=0.25s_and_p90<=0.50s",
            "status": (
                "PASS"
                if float(np.mean(reconstruction_abs)) <= 0.25
                and float(np.quantile(reconstruction_abs, 0.90)) <= 0.50
                else "FAIL"
            ),
        },
        "segmentation_stability": stability,
        "top_speed_formula_gap": {
            "driver_mae_faithful_top15_vs_audited_p99_kph": float(
                np.mean(top_speed_differences)
            ),
            "driver_max_abs_gap_kph": float(
                np.max(top_speed_differences)
            ),
            "interpretation": (
                "公开 top15 样本会集中在单条直道；逐圈 P99 中位数更稳健。"
            ),
        },
        "full_throttle_weighting_gap": {
            "driver_mae_sample_vs_time_pp": float(
                np.mean(throttle_differences)
            ),
            "driver_max_abs_gap_pp": float(
                np.max(throttle_differences)
            ),
        },
        "teammate_matching": {
            "teams": len(teammate_comparisons),
            "comparable_pairs": len(comparable_pairs),
            "matched_lap_pairs": len(matched_ledger),
            "max_antisymmetry_error": (
                float(max(antisymmetry_errors))
                if antisymmetry_errors
                else 0.0
            ),
            "status": (
                "PASS"
                if antisymmetry_errors
                and max(antisymmetry_errors) <= 1e-9
                else "FAIL"
            ),
        },
        "direct_physical_claims_allowed": False,
        "direct_total_vehicle_order_allowed": False,
    }
    report = {
        "schema_version": (
            "reference-analysis-lab-fdataanalysis-reverse-engineered-v2"
        ),
        "target_id": "fdataanalysis-reverse-engineered-v2",
        "status": (
            "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
            if validation["status"] == "PASS"
            else "AUDIT_ONLY_VALIDATION_FAILED"
        ),
        "run_id": source_identity.get("run_id"),
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_card": {
            "creator": "Formula Data Analysis / @FDataAnalysis",
            "original_profile": "https://x.com/FDataAnalysis",
            "publicly_observable_method": [
                "把速度、油门、制动、挡位等通道画在统一赛道距离轴上。",
                "比较弯角入口、最低和出口速度，以及刹车与再加速位置。",
                "公开分析曾使用直道 150–250 km/h 区间速度曲线斜率讨论加速。",
                "公开 Top Speeds 图曾同时披露最大速度和最高 15 个速度样本均值。",
                "长跑图按胎龄展示圈速变化、逐圈点、分布、标准差和 Push Laps 样本量。",
                "车辆强弱总结由多场遥测中反复观察到的速度与控制特征归纳而来。",
            ],
            "reverse_engineered_or_reimplemented": [
                "每圈线性插值到 1,000 个距离区间；离散控制通道使用最近邻。",
                "弯角代理由全场中位横向加速度绝对峰值识别，边界由横向载荷与制动证据动态确定。",
                "全油门视觉口径保留样本比例；审计伴随改用逐点时间权重。",
                "最高速度视觉口径使用最高 15 个逐点速度均值；审计伴随使用逐圈 P99 中位数。",
                "胎龄变化在单一 Stint/配方内使用 Theil–Sen，并报告具名燃油情景。",
                "同队双车使用一对一最小代价匹配，只接受同配方、胎龄差≤2、阶段差≤0.12、交通差≤0.15。",
            ],
            "visual_inference": [
                "原作者未公开精确平滑核、Push Lap 标签、弯角 anchor、跨场权重或车辆总评分公式。",
                "1,000 区间、横向加速度阈值和匹配门限是可解释的工程化实现，不声称一比一源码复现。",
                "参考图是 2026 匈牙利，而真实 pilot 是 2025 Abu Dhabi Race；不能把两者做数值 MAE。",
            ],
            "not_identifiable": [
                "真实下压力、机械抓地、引擎功率、阻力系数、SOC、动力模式和能量部署。",
                "真实燃油量、胎温、胎压、物理磨损、设定、损伤和车队指令。",
                "公开图精确选圈、人工复核、平滑/进站等价代码和赛季聚合权重。",
            ],
            "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
            "reference_boundary": (
                "只重实现公开逻辑与布局语义；不复制原图、logo、品牌字体或人物照片。"
            ),
        },
        "data_gap_audit": [
            {
                "required_field": "speed/throttle/brake/gear/DRS/rpm",
                "repository_field": "逐点公开遥测",
                "gap": "约 4 Hz 且为公开 feed",
                "substitute": "统一距离轴插值并披露圈时积分误差",
                "publication": "ALLOW_OBSERVED_FEATURES",
            },
            {
                "required_field": "corner_entry_min_exit_and_brake_position",
                "repository_field": "rel_distance/speed/brake/acc_y",
                "gap": "无冻结官方弯角 anchor",
                "substitute": "横向加速度峰值动态窗口 P01…Pn",
                "publication": "ALLOW_NAMED_PROXY_NOT_OFFICIAL_CORNERS",
            },
            {
                "required_field": "top_speed",
                "repository_field": "逐点 speed",
                "gap": "最高 15 样本受采样密度影响",
                "substitute": "视觉 top15 + 审计逐圈 P99 中位数",
                "publication": "ALLOW_WITH_FORMULA_DISCLOSURE",
            },
            {
                "required_field": "full_throttle_usage",
                "repository_field": "逐点 throttle/time",
                "gap": "样本占比不等于时间占比",
                "substitute": "视觉样本占比 + 审计时间权重",
                "publication": "ALLOW_BOTH_VIEWS",
            },
            {
                "required_field": "tyre_age_speed_and_lap_time_change",
                "repository_field": "tyre_life/stint/compound/lap_duration/speed",
                "gap": "燃油、赛道演化和物理胎态不可分离",
                "substitute": "单 Stint Theil–Sen + low/base/high 具名燃油情景",
                "publication": "CONDITIONAL_PROXY_ONLY",
            },
            {
                "required_field": "vehicle_strengths",
                "repository_field": "输出速度与控制特征",
                "gap": "输入物理量和设定不可识别",
                "substitute": "单特征排序；禁止总分和物理命名",
                "publication": "OBSERVED_PROFILE_ONLY_NO_CAUSAL_TOTAL_ORDER",
            },
        ],
        "visual_replication": {
            "axis_contract": {
                "axis_intervals": AXIS_INTERVALS,
                "axis_points": AXIS_POINTS,
                "rel_distance_range": [0.0, 1.0],
                "continuous_interpolation": "linear",
                "discrete_interpolation": "nearest",
                "scope": "2025 Abu Dhabi Race valid Stints",
                "approx_sampling_hz": validation["sample_frequency"][
                    "approx_hz"
                ],
            },
            "track_profile": compact_track_profile(profile),
            "track_segments": {
                "corner_proxies": corners,
                "straight_proxies": straights,
            },
            "driver_profiles": driver_profiles,
            "team_profiles": team_profiles,
            "single_feature_team_ranks": feature_ranks,
            "ranking_note": (
                "只允许单一观测特征排序；不同特征不相加为车辆总分。"
            ),
        },
        "audited_analysis": {
            "status": (
                "PASS_WITH_COMPARABLE_SUBSETS"
                if comparable_pairs
                else "audit_only"
            ),
            "direct_total_order_allowed": False,
            "condition_matched_teammates": teammate_comparisons,
            "tyre_age_change": tyre_age_drivers,
            "comparable_teammate_pairs": len(comparable_pairs),
            "all_teammate_pairs": len(teammate_comparisons),
            "allowed_claims": [
                "公开 feed 中观测到的速度、控制、制动位置和分段时间差。",
                "通过条件门的同队双车交付差异代理。",
                "单 Stint、具名燃油情景下的胎龄相关变化。",
            ],
            "forbidden_claims": [
                "真实下压力",
                "机械抓地",
                "引擎功率",
                "阻力系数",
                "物理轮胎磨损",
                "无条件车辆或车手总排名",
            ],
            "reason": (
                "速度/控制输出可复核，但燃油、SOC、设定、损伤和真实轮胎状态不可识别。"
            ),
        },
        "exclusion_ledger": {
            "raw_points": int(len(points)),
            "lap_universe": int(len(lap_table)),
            "pace_eligible_laps": int(lap_table["pace_eligible"].sum()),
            "axis_interpolated_laps": int(len(lap_metrics)),
            "valid_points": int(len(valid_points)),
            "excluded_by_reason_nonexclusive": excluded_by_reason,
            "audited_candidate_disposition": candidate_disposition,
            "drivers": int(lap_metrics["driver"].nunique()),
            "teams": int(lap_metrics["team"].nunique()),
        },
        "validation": validation,
        "boundaries": {
            "creator_exact_code_available": False,
            "official_corner_anchors_available": False,
            "true_fuel_identifiable": False,
            "physical_tyre_wear_identifiable": False,
            "vehicle_physics_identifiable": False,
            "same_event_reference_chart_available": False,
        },
    }
    sidecars = {
        "lap_metrics": lap_metrics,
        "corner_lap_features": corner_frame,
        "straight_lap_features": straight_frame,
        "tyre_age_stints": pd.DataFrame(tyre_age_stints),
        "matched_lap_pairs": pd.DataFrame(matched_ledger),
    }
    return report, sidecars


__all__ = [
    "AXIS_INTERVALS",
    "AXIS_POINTS",
    "DistanceAxisBundle",
    "build_condition_matched_teammates",
    "build_distance_axis_bundle",
    "build_fdataanalysis_reverse_engineered_v2",
    "detect_dynamic_corner_proxies",
    "integrate_speed_trace_seconds",
    "robust_theil_sen_slope",
    "segmentation_stability",
    "top_n_mean",
]
