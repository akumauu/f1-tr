"""公开遥测的距离域解释、可比圈匹配与统计发布门控。

本模块只把公开采样转换为可复核的描述性差异。速度、制动和油门代理不能
单独识别车辆状态、车手意图或具体能量存量，因此输出始终保留统计解释边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment


TELEMETRY_CLAIM_BOUNDARIES = (
    "速度或时间差只表示公开输入条件下的统计差异。",
    "不得把遥测差异直接解释为车辆物理故障。",
    "不得由公开遥测推断电池荷电状态或具体能量存量。",
    "弯道与直道标签是赛道位置代理，不是驾驶意图标签。",
)

DEFAULT_STRATEGY_PROXY_FEATURES = frozenset(
    {
        "strategy_proxy",
        "energy_deployment_proxy",
        "lift_coast_proxy",
        "traffic_management_proxy",
    }
)

_DEFAULT_GROUP_CANDIDATES = (
    "year",
    "meeting",
    "session",
    "team",
    "driver_acronym",
    "driver_number",
    "lap",
)


def _required(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{missing}")


def _group_columns(frame: pd.DataFrame, supplied: Sequence[str] | None) -> list[str]:
    if supplied is not None:
        _required(frame, supplied, "分组输入")
        return list(supplied)
    return [column for column in _DEFAULT_GROUP_CANDIDATES if column in frame.columns]


def _group_iterator(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> Iterable[tuple[tuple[Any, ...], pd.DataFrame]]:
    if not columns:
        yield (), frame
        return
    for key, group in frame.groupby(
        list(columns), dropna=False, observed=True, sort=True
    ):
        yield key if isinstance(key, tuple) else (key,), group


def dt_from_ds_v(
    distance_delta_m: Any,
    speed_kmh: Any,
) -> np.ndarray:
    """按 ``dt = ds / v`` 计算区段时间，速度输入单位为 km/h。"""

    distance = np.asarray(distance_delta_m, dtype=float)
    speed = np.asarray(speed_kmh, dtype=float)
    distance, speed = np.broadcast_arrays(distance, speed)
    if np.any(np.isfinite(distance) & (distance < 0.0)):
        raise ValueError("距离增量不得为负数")
    valid = np.isfinite(distance) & np.isfinite(speed) & (speed > 0.0)
    result = np.full(distance.shape, np.nan, dtype=float)
    result[valid] = distance[valid] / (speed[valid] / 3.6)
    return result


def resample_distance(
    samples: pd.DataFrame,
    *,
    group_columns: Sequence[str] | None = None,
    distance_column: str = "distance",
    speed_column: str = "speed",
    step_m: float = 10.0,
    continuous_columns: Sequence[str] | None = None,
    carry_columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """把一圈或多圈采样线性插值到共同的等距离网格。

    重复距离点先按数值列取均值；不向原始覆盖范围外外推。每个区段用两端
    速度均值作为 ``v``，再严格按 ``dt = ds / v`` 积分。
    """

    if not np.isfinite(step_m) or step_m <= 0.0:
        raise ValueError("step_m 必须是正数")
    _required(samples, {distance_column, speed_column}, "距离重采样输入")
    groups = _group_columns(samples, group_columns)

    if continuous_columns is None:
        continuous = [
            column
            for column in samples.columns
            if column not in {*groups, distance_column}
            and pd.api.types.is_numeric_dtype(samples[column])
        ]
    else:
        _required(samples, continuous_columns, "连续遥测输入")
        continuous = list(dict.fromkeys(continuous_columns))
    if speed_column not in continuous:
        continuous.insert(0, speed_column)
    carry = list(carry_columns or ())
    _required(samples, carry, "重采样携带输入")

    pieces: list[pd.DataFrame] = []
    for key, raw_group in _group_iterator(samples, groups):
        numeric = raw_group[[distance_column, *continuous]].copy()
        for column in numeric.columns:
            numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
        numeric = numeric.loc[np.isfinite(numeric[distance_column])]
        if numeric.empty:
            continue
        collapsed = (
            numeric.groupby(distance_column, observed=True, sort=True)[continuous]
            .mean()
            .reset_index()
        )
        speed_valid = collapsed[[distance_column, speed_column]].dropna()
        if speed_valid[distance_column].nunique() < 2:
            continue

        lower = float(collapsed[distance_column].min())
        upper = float(collapsed[distance_column].max())
        grid_start = np.ceil(lower / step_m) * step_m
        grid_end = np.floor(upper / step_m) * step_m
        if grid_end < grid_start:
            continue
        grid = np.arange(grid_start, grid_end + step_m * 0.5, step_m, dtype=float)
        output = pd.DataFrame({distance_column: grid})
        for column in continuous:
            valid = collapsed[[distance_column, column]].dropna()
            if valid.empty:
                output[column] = np.nan
            elif len(valid) == 1:
                output[column] = float(valid[column].iloc[0])
            else:
                output[column] = np.interp(
                    grid,
                    valid[distance_column].to_numpy(dtype=float),
                    valid[column].to_numpy(dtype=float),
                )

        for column, value in zip(groups, key):
            output[column] = value
        for column in carry:
            values = raw_group[column].dropna()
            output[column] = pd.NA if values.empty else values.iloc[0]

        distance_delta = output[distance_column].diff().fillna(0.0).to_numpy(dtype=float)
        speed = pd.to_numeric(output[speed_column], errors="coerce").to_numpy(dtype=float)
        segment_speed = np.r_[speed[0], (speed[:-1] + speed[1:]) / 2.0]
        segment_dt = dt_from_ds_v(distance_delta, segment_speed)
        segment_dt[0] = 0.0
        output["distance_step_m"] = distance_delta
        output["segment_speed_kmh"] = segment_speed
        output["segment_dt_s"] = segment_dt
        output["segment_time_valid"] = np.isfinite(segment_dt)
        output["elapsed_time_s"] = np.cumsum(np.where(np.isfinite(segment_dt), segment_dt, 0.0))
        pieces.append(output)

    if not pieces:
        return pd.DataFrame(
            columns=[
                *groups,
                distance_column,
                *continuous,
                "distance_step_m",
                "segment_speed_kmh",
                "segment_dt_s",
                "segment_time_valid",
                "elapsed_time_s",
            ]
        )
    ordered = [*groups, distance_column]
    return pd.concat(pieces, ignore_index=True).sort_values(ordered, kind="stable").reset_index(
        drop=True
    )


def integrate_delta_time(
    samples: pd.DataFrame,
    *,
    distance_column: str = "distance",
    speed_column: str = "speed",
    require_complete: bool = True,
) -> float:
    """对单圈距离序列执行 ``dt = ds / v`` 积分。"""

    _required(samples, {distance_column, speed_column}, "时间积分输入")
    work = samples[[distance_column, speed_column]].copy()
    work[distance_column] = pd.to_numeric(work[distance_column], errors="coerce")
    work[speed_column] = pd.to_numeric(work[speed_column], errors="coerce")
    work = work.dropna(subset=[distance_column]).sort_values(distance_column, kind="stable")
    work = work.groupby(distance_column, observed=True, sort=True)[speed_column].mean().reset_index()
    if len(work) < 2:
        raise ValueError("时间积分至少需要两个不同距离点")
    distance = work[distance_column].to_numpy(dtype=float)
    speed = work[speed_column].to_numpy(dtype=float)
    delta = np.diff(distance)
    segment_speed = (speed[:-1] + speed[1:]) / 2.0
    segment_dt = dt_from_ds_v(delta, segment_speed)
    if require_complete and not np.isfinite(segment_dt).all():
        raise ValueError("时间积分存在非正速度或缺失速度区段")
    return float(np.nansum(segment_dt))


# 为调用方保留一个直接表达公式的别名。
integrate_dt_ds_over_v = integrate_delta_time


@dataclass(frozen=True)
class TrackPhaseConfig:
    """赛道相位图的冻结阈值。"""

    corner_speed_ratio: float = 0.76
    straight_speed_ratio: float = 0.90
    brake_threshold: float = 0.10
    straight_throttle_threshold: float = 80.0
    minimum_phase_length_m: float = 20.0
    minimum_reference_laps: int = 1

    def __post_init__(self) -> None:
        if not 0.0 <= self.corner_speed_ratio < self.straight_speed_ratio <= 1.0:
            raise ValueError("弯道/直道速度比例阈值无效")
        if self.minimum_phase_length_m < 0.0:
            raise ValueError("最短相位长度不得为负数")
        if int(self.minimum_reference_laps) < 1:
            raise ValueError("minimum_reference_laps 必须至少为 1")


def _smooth_short_runs(labels: np.ndarray, minimum_bins: int) -> np.ndarray:
    """只合并被同类相位包围的短毛刺，避免主观扩张弯道范围。"""

    result = labels.astype(object).copy()
    if minimum_bins <= 1 or len(result) < 3:
        return result
    changed = True
    while changed:
        changed = False
        starts = np.r_[0, np.flatnonzero(result[1:] != result[:-1]) + 1]
        ends = np.r_[starts[1:], len(result)]
        for index, (start, end) in enumerate(zip(starts, ends)):
            if end - start >= minimum_bins or index == 0 or index == len(starts) - 1:
                continue
            left = result[starts[index - 1]]
            right = result[starts[index + 1]]
            if left == right:
                result[start:end] = left
                changed = True
        if not changed:
            break
    return result


@dataclass(frozen=True)
class TrackPhaseMap:
    """固定在距离轴上的弯道、直道和过渡区映射。"""

    frame: pd.DataFrame
    distance_column: str = "distance"
    phase_column: str = "track_phase"

    def __post_init__(self) -> None:
        required = {
            self.distance_column,
            self.phase_column,
            "track_segment_id",
            "reference_speed_kmh",
        }
        _required(self.frame, required, "TrackPhaseMap")
        normalized = self.frame.copy().sort_values(self.distance_column, kind="stable")
        if normalized[self.distance_column].duplicated().any():
            raise ValueError("TrackPhaseMap 距离网格不得重复")
        allowed = {"corner", "straight", "transition"}
        unknown = set(normalized[self.phase_column].dropna().astype(str)) - allowed
        if unknown:
            raise ValueError(f"TrackPhaseMap 包含未知相位：{sorted(unknown)}")
        object.__setattr__(self, "frame", normalized.reset_index(drop=True))

    @classmethod
    def from_reference_laps(
        cls,
        resampled_laps: pd.DataFrame,
        *,
        distance_column: str = "distance",
        speed_column: str = "speed",
        throttle_column: str = "throttle",
        brake_column: str = "brake",
        config: TrackPhaseConfig | None = None,
        anchor_distances: Sequence[float] | None = None,
    ) -> "TrackPhaseMap":
        """以多圈逐点中位速度建立固定相位图，不用待解释圈重定义赛道。

        可选 ``anchor_distances`` 用公开弯角距离的相邻中点进一步切分长相位，
        避免连续组合弯被合成一个巨型赛段；锚点只切段，不改相位标签。
        """

        cfg = config or TrackPhaseConfig()
        _required(resampled_laps, {distance_column, speed_column}, "相位图参考圈")
        reference_groups = _group_columns(resampled_laps, None)
        reference_laps = (
            int(resampled_laps[reference_groups].drop_duplicates().shape[0])
            if reference_groups
            else int(not resampled_laps.empty)
        )
        if reference_laps < int(cfg.minimum_reference_laps):
            raise ValueError(
                f"相位图参考圈不足：{reference_laps} < {int(cfg.minimum_reference_laps)}"
            )
        columns = [distance_column, speed_column]
        if throttle_column in resampled_laps:
            columns.append(throttle_column)
        if brake_column in resampled_laps:
            columns.append(brake_column)
        work = resampled_laps[columns].copy()
        for column in columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
        work = work.dropna(subset=[distance_column, speed_column])
        if work.empty:
            raise ValueError("相位图参考圈没有有效速度")

        aggregations: dict[str, Any] = {speed_column: "median"}
        if throttle_column in work:
            aggregations[throttle_column] = "median"
        if brake_column in work:
            aggregations[brake_column] = lambda values: float(values.quantile(0.75))
        reference = work.groupby(distance_column, observed=True, sort=True).agg(aggregations).reset_index()
        speed_ceiling = float(reference[speed_column].quantile(0.95))
        if not np.isfinite(speed_ceiling) or speed_ceiling <= 0.0:
            raise ValueError("相位图参考速度上界无效")
        ratio = (reference[speed_column] / speed_ceiling).clip(lower=0.0, upper=1.5)
        braking = (
            reference[brake_column].ge(cfg.brake_threshold)
            if brake_column in reference
            else pd.Series(False, index=reference.index)
        )
        throttle_ok = (
            reference[throttle_column].ge(cfg.straight_throttle_threshold)
            if throttle_column in reference
            else pd.Series(True, index=reference.index)
        )
        corner = ratio.le(cfg.corner_speed_ratio) | braking
        straight = ~corner & ratio.ge(cfg.straight_speed_ratio) & throttle_ok
        labels = np.select([corner, straight], ["corner", "straight"], default="transition")

        distances = reference[distance_column].to_numpy(dtype=float)
        positive_steps = np.diff(distances)
        positive_steps = positive_steps[positive_steps > 0.0]
        typical_step = float(np.median(positive_steps)) if len(positive_steps) else 1.0
        minimum_bins = max(1, int(np.ceil(cfg.minimum_phase_length_m / typical_step)))
        labels = _smooth_short_runs(np.asarray(labels, dtype=object), minimum_bins)
        segment_starts = np.r_[True, labels[1:] != labels[:-1]]
        if anchor_distances:
            anchors = np.asarray(anchor_distances, dtype=float)
            anchors = np.unique(anchors[np.isfinite(anchors)])
            anchors = anchors[(anchors >= distances[0]) & (anchors <= distances[-1])]
            if len(anchors) >= 2:
                midpoints = (anchors[:-1] + anchors[1:]) / 2.0
                indexes = np.searchsorted(distances, midpoints, side="left")
                indexes = indexes[(indexes > 0) & (indexes < len(distances))]
                segment_starts[indexes] = True
        segment_id = np.cumsum(segment_starts)
        phase_frame = pd.DataFrame(
            {
                distance_column: distances,
                "track_phase": labels,
                "track_segment_id": segment_id.astype(int),
                "reference_speed_kmh": reference[speed_column].to_numpy(dtype=float),
                "reference_speed_ratio": ratio.to_numpy(dtype=float),
            }
        )
        return cls(phase_frame, distance_column=distance_column)

    def annotate(
        self,
        samples: pd.DataFrame,
        *,
        tolerance_m: float | None = None,
    ) -> pd.DataFrame:
        """按最近距离点给采样附加冻结相位；超出容差的点保持未知。"""

        _required(samples, {self.distance_column}, "相位标注输入")
        map_distance = self.frame[self.distance_column].to_numpy(dtype=float)
        if len(map_distance) == 0:
            raise ValueError("TrackPhaseMap 为空")
        steps = np.diff(map_distance)
        steps = steps[steps > 0.0]
        default_tolerance = float(np.median(steps) * 0.51) if len(steps) else 1e-9
        tolerance = default_tolerance if tolerance_m is None else float(tolerance_m)
        if tolerance < 0.0:
            raise ValueError("相位标注容差不得为负数")

        output = samples.copy()
        sample_distance = pd.to_numeric(output[self.distance_column], errors="coerce").to_numpy(
            dtype=float
        )
        right = np.searchsorted(map_distance, sample_distance, side="left")
        right = np.clip(right, 0, len(map_distance) - 1)
        left = np.clip(right - 1, 0, len(map_distance) - 1)
        choose_left = np.abs(sample_distance - map_distance[left]) <= np.abs(
            sample_distance - map_distance[right]
        )
        nearest = np.where(choose_left, left, right)
        separation = np.abs(sample_distance - map_distance[nearest])
        valid = np.isfinite(sample_distance) & (separation <= tolerance)
        phase_values = np.full(len(output), None, dtype=object)
        segment_values = np.full(len(output), np.nan, dtype=float)
        reference_speed = np.full(len(output), np.nan, dtype=float)
        reference_ratio = np.full(len(output), np.nan, dtype=float)
        phase_values[valid] = self.frame[self.phase_column].to_numpy(dtype=object)[nearest[valid]]
        segment_values[valid] = self.frame["track_segment_id"].to_numpy(dtype=float)[nearest[valid]]
        reference_speed[valid] = self.frame["reference_speed_kmh"].to_numpy(dtype=float)[nearest[valid]]
        if "reference_speed_ratio" in self.frame:
            reference_ratio[valid] = self.frame["reference_speed_ratio"].to_numpy(dtype=float)[
                nearest[valid]
            ]
        output[self.phase_column] = phase_values
        output["track_segment_id"] = pd.array(segment_values, dtype="Int64")
        output["phase_reference_speed_kmh"] = reference_speed
        output["phase_reference_speed_ratio"] = reference_ratio
        return output


def extract_corner_straight_features(
    resampled_laps: pd.DataFrame,
    phase_map: TrackPhaseMap,
    *,
    group_columns: Sequence[str] | None = None,
    speed_column: str = "speed",
) -> pd.DataFrame:
    """从冻结相位图提取逐圈弯道/直道时间与速度描述特征。"""

    distance_column = phase_map.distance_column
    _required(resampled_laps, {distance_column, speed_column}, "相位特征输入")
    groups = _group_columns(resampled_laps, group_columns)
    annotated = phase_map.annotate(resampled_laps)
    rows: list[dict[str, Any]] = []
    for key, raw_group in _group_iterator(annotated, groups):
        group = raw_group.sort_values(distance_column, kind="stable").copy()
        distance = pd.to_numeric(group[distance_column], errors="coerce").to_numpy(dtype=float)
        speed = pd.to_numeric(group[speed_column], errors="coerce").to_numpy(dtype=float)
        delta = np.r_[0.0, np.diff(distance)]
        segment_speed = np.r_[speed[0], (speed[:-1] + speed[1:]) / 2.0]
        segment_dt = dt_from_ds_v(delta, segment_speed)
        segment_dt[0] = 0.0
        group = group.assign(_segment_dt_s=segment_dt)

        row: dict[str, Any] = dict(zip(groups, key))
        valid_time = np.isfinite(segment_dt)
        total_time = float(np.nansum(segment_dt))
        known_phase = group[phase_map.phase_column].notna().to_numpy() & valid_time
        known_time = float(np.nansum(segment_dt[known_phase]))
        row["lap_time_ds_over_v_s"] = total_time
        full_map_span = float(
            phase_map.frame[distance_column].max()
            - phase_map.frame[distance_column].min()
        )
        known_distances = pd.to_numeric(
            group.loc[group[phase_map.phase_column].notna(), distance_column],
            errors="coerce",
        ).dropna()
        observed_span = (
            float(known_distances.max() - known_distances.min())
            if len(known_distances)
            else 0.0
        )
        distance_coverage = (
            float(np.clip(observed_span / full_map_span, 0.0, 1.0))
            if full_map_span > 0.0
            else np.nan
        )
        observed_time_coverage = (
            known_time / total_time if total_time > 0.0 else np.nan
        )
        row["phase_distance_coverage"] = distance_coverage
        row["phase_time_coverage"] = (
            float(min(observed_time_coverage, distance_coverage))
            if np.isfinite(observed_time_coverage)
            and np.isfinite(distance_coverage)
            else np.nan
        )
        for phase in ("corner", "straight", "transition"):
            mask = group[phase_map.phase_column].eq(phase).to_numpy()
            row[f"{phase}_time_s"] = float(np.nansum(segment_dt[mask]))

        corner = group.loc[group[phase_map.phase_column].eq("corner")]
        straight = group.loc[group[phase_map.phase_column].eq("straight")]
        row["corner_min_speed_kmh"] = (
            float(pd.to_numeric(corner[speed_column], errors="coerce").min())
            if not corner.empty
            else np.nan
        )
        row["corner_mean_speed_kmh"] = (
            float(pd.to_numeric(corner[speed_column], errors="coerce").mean())
            if not corner.empty
            else np.nan
        )
        row["straight_mean_speed_kmh"] = (
            float(pd.to_numeric(straight[speed_column], errors="coerce").mean())
            if not straight.empty
            else np.nan
        )
        row["straight_max_speed_kmh"] = (
            float(pd.to_numeric(straight[speed_column], errors="coerce").max())
            if not straight.empty
            else np.nan
        )
        row["corner_segments"] = int(corner["track_segment_id"].nunique())
        row["straight_segments"] = int(straight["track_segment_id"].nunique())
        if corner.empty:
            row["corner_entry_speed_kmh"] = np.nan
            row["corner_exit_speed_kmh"] = np.nan
        else:
            corner_segments = corner.groupby("track_segment_id", observed=True, sort=True)
            entry = corner_segments[speed_column].first()
            exit_speed = corner_segments[speed_column].last()
            row["corner_entry_speed_kmh"] = float(pd.to_numeric(entry, errors="coerce").mean())
            row["corner_exit_speed_kmh"] = float(
                pd.to_numeric(exit_speed, errors="coerce").mean()
            )
        row["interpretation_scope"] = "descriptive_public_telemetry_proxy"
        rows.append(row)
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class ComparableLapConfig:
    """可比圈阻断、容差和唯一匹配规则。"""

    exact_columns: tuple[str, ...] = (
        "year",
        "meeting",
        "session",
        "compound",
        "track_status",
    )
    numeric_tolerances: Mapping[str, float] = field(
        default_factory=lambda: {
            "tyre_age": 2.0,
            "tyre_life": 2.0,
            "lap_fraction": 0.08,
            "track_temp": 5.0,
            "air_temp": 3.0,
        }
    )
    driver_column: str = "driver_acronym"
    require_different_driver: bool = True
    one_to_one: bool = True

    def __post_init__(self) -> None:
        invalid = {
            key: value
            for key, value in self.numeric_tolerances.items()
            if not np.isfinite(float(value)) or float(value) <= 0.0
        }
        if invalid:
            raise ValueError(f"可比圈数值容差必须为正数：{invalid}")


def match_comparable_laps(
    target_laps: pd.DataFrame,
    candidate_laps: pd.DataFrame | None = None,
    *,
    config: ComparableLapConfig | None = None,
) -> pd.DataFrame:
    """在精确情境阻断后按归一化输入距离执行确定性一对一匹配。"""

    cfg = config or ComparableLapConfig()
    reference = target_laps if candidate_laps is None else candidate_laps
    same_pool = candidate_laps is None
    target = target_laps.reset_index(drop=False).rename(columns={"index": "_source_index"})
    candidate = reference.reset_index(drop=False).rename(columns={"index": "_source_index"})
    _required(target, cfg.exact_columns, "目标可比圈精确阻断")
    _required(candidate, cfg.exact_columns, "候选可比圈精确阻断")
    _required(target, cfg.numeric_tolerances, "目标可比圈卡钳")
    _required(candidate, cfg.numeric_tolerances, "候选可比圈卡钳")
    exact = list(cfg.exact_columns)
    numeric = list(cfg.numeric_tolerances)

    edges: list[dict[str, Any]] = []
    for target_position, target_row in target.iterrows():
        mask = pd.Series(True, index=candidate.index)
        for column in exact:
            value = target_row[column]
            if pd.isna(value):
                mask &= False
            else:
                mask &= candidate[column].notna() & candidate[column].eq(value)
        if same_pool:
            mask &= candidate.index != target_position
        if (
            cfg.require_different_driver
            and cfg.driver_column in target
            and cfg.driver_column in candidate
        ):
            driver = target_row[cfg.driver_column]
            mask &= candidate[cfg.driver_column].notna() & candidate[cfg.driver_column].ne(driver)

        score = pd.Series(0.0, index=candidate.index, dtype=float)
        deltas: dict[str, pd.Series] = {}
        for column in numeric:
            target_value = pd.to_numeric(pd.Series([target_row[column]]), errors="coerce").iloc[0]
            candidate_value = pd.to_numeric(candidate[column], errors="coerce")
            delta = float(target_value) - candidate_value if pd.notna(target_value) else np.nan
            tolerance = float(cfg.numeric_tolerances[column])
            valid = np.isfinite(delta) & np.less_equal(np.abs(delta), tolerance)
            mask &= valid
            score += np.abs(delta).fillna(np.inf) / tolerance
            deltas[column] = delta

        for reference_position in candidate.index[mask]:
            edge: dict[str, Any] = {
                "target_position": int(target_position),
                "reference_position": int(reference_position),
                "match_distance": float(score.loc[reference_position]),
            }
            for column in numeric:
                edge[f"delta_{column}"] = float(deltas[column].loc[reference_position])
            edges.append(edge)

    edges.sort(
        key=lambda row: (
            row["match_distance"],
            row["target_position"],
            row["reference_position"],
        )
    )
    if not cfg.one_to_one:
        selected_by_target: dict[int, dict[str, Any]] = {}
        for edge in edges:
            selected_by_target.setdefault(edge["target_position"], edge)
        selected = list(selected_by_target.values())
    elif same_pool:
        # 同池配对需要一般图匹配；当前生产调用是双车目标池/候选池分离。
        # 保留确定性贪心并显式标注，避免把该分支误称为全局最优。
        selected = []
        used_targets: set[int] = set()
        used_references: set[int] = set()
        for edge in edges:
            target_position = edge["target_position"]
            reference_position = edge["reference_position"]
            if (
                target_position in used_targets
                or reference_position in used_references
                or target_position in used_references
                or reference_position in used_targets
            ):
                continue
            selected.append(edge)
            used_targets.add(target_position)
            used_references.add(reference_position)
    else:
        # 最大化匹配数后最小化总归一化卡钳距离。旧版逐边贪心会在两个目标
        # 竞争同一候选时产生次优配对，继而放大平衡告警或制造伪异常。
        n_target = len(target)
        n_reference = len(candidate)
        maximum_edge_cost = max(
            (float(edge["match_distance"]) for edge in edges),
            default=0.0,
        )
        unmatched_cost = max(1.0, maximum_edge_cost + 1.0)
        forbidden_cost = unmatched_cost * (n_target + n_reference + 2.0)
        size = n_target + n_reference
        cost = np.full((size, size), forbidden_cost, dtype=float)
        edge_lookup: dict[tuple[int, int], dict[str, Any]] = {}
        for edge in edges:
            key = (edge["target_position"], edge["reference_position"])
            cost[key] = float(edge["match_distance"])
            edge_lookup[key] = edge
        for position in range(n_target):
            cost[position, n_reference + position] = unmatched_cost
        for position in range(n_reference):
            cost[n_target + position, position] = unmatched_cost
        cost[n_target:, n_reference:] = 0.0
        row_indexes, column_indexes = linear_sum_assignment(cost)
        selected = [
            edge_lookup[(int(row), int(column))]
            for row, column in zip(row_indexes, column_indexes)
            if row < n_target and column < n_reference and (int(row), int(column)) in edge_lookup
        ]

    rows: list[dict[str, Any]] = []
    for edge in sorted(selected, key=lambda row: row["target_position"]):
        target_row = target.iloc[edge["target_position"]]
        reference_row = candidate.iloc[edge["reference_position"]]
        row = dict(edge)
        row["target_source_index"] = target_row["_source_index"]
        row["reference_source_index"] = reference_row["_source_index"]
        for column in target_laps.columns:
            row[f"target_{column}"] = target_row[column]
        for column in reference.columns:
            row[f"reference_{column}"] = reference_row[column]
        row["match_quality"] = 1.0 / (1.0 + float(edge["match_distance"]))
        row["match_rule"] = (
            "exact_context_then_global_minimum_normalized_input_distance"
            if cfg.one_to_one and not same_pool
            else "exact_context_then_deterministic_normalized_input_distance"
        )
        rows.append(row)
    if not rows:
        return pd.DataFrame(
            columns=[
                "target_position",
                "reference_position",
                "target_source_index",
                "reference_source_index",
                "match_distance",
                "match_quality",
                "match_rule",
            ]
        )
    return pd.DataFrame(rows)


def resolve_strategy_mode(year: Any, requested_mode: str = "auto") -> str:
    """2026 及以后强制策略中性；旧规则期可使用已声明的公开代理。"""

    aliases = {
        "auto": "auto",
        "neutral": "neutral",
        "neutral_strategy": "neutral",
        "observed": "observed_proxy",
        "observed_proxy": "observed_proxy",
    }
    normalized = aliases.get(str(requested_mode).strip().lower())
    if normalized is None:
        raise ValueError("strategy_mode 只允许 auto、neutral 或 observed_proxy")
    try:
        season = int(year)
    except (TypeError, ValueError) as exc:
        raise ValueError("year 必须可转换为整数") from exc
    if season >= 2026:
        return "neutral"
    return "observed_proxy" if normalized == "auto" else normalized


strategy_mode_for_season = resolve_strategy_mode


def _input_delta(frame: pd.DataFrame, feature: str) -> pd.Series:
    direct = f"delta_{feature}"
    if direct in frame:
        return pd.to_numeric(frame[direct], errors="coerce")
    target = f"target_{feature}"
    reference = f"reference_{feature}"
    if target in frame and reference in frame:
        return pd.to_numeric(frame[target], errors="coerce") - pd.to_numeric(
            frame[reference], errors="coerce"
        )
    if feature in frame:
        return pd.to_numeric(frame[feature], errors="coerce")
    raise ValueError(f"缺少输入差异字段：{feature}")


def compute_gap_baselines(
    matched_laps: pd.DataFrame,
    *,
    target_time_column: str,
    internal_reference_column: str,
    external_reference_column: str,
    adjustment_coefficients: Mapping[str, float] | None = None,
    season_column: str = "target_year",
    strategy_mode: str = "auto",
    strategy_proxy_features: Iterable[str] = DEFAULT_STRATEGY_PROXY_FEATURES,
) -> pd.DataFrame:
    """计算内部、外部和输入调整后三条透明基线。

    ``external_gap_s`` 是目标圈相对独立可比圈的直接差；
    ``input_adjusted_gap_s`` 再扣除预先给定系数对应的输入差。系数必须由调用方
    在训练段冻结，本函数不会在待解释圈上拟合。2026 会忽略策略代理系数。
    """

    _required(
        matched_laps,
        {
            target_time_column,
            internal_reference_column,
            external_reference_column,
            season_column,
        },
        "三基线输入",
    )
    output = matched_laps.copy()
    target_time = pd.to_numeric(output[target_time_column], errors="coerce")
    internal = pd.to_numeric(output[internal_reference_column], errors="coerce")
    external = pd.to_numeric(output[external_reference_column], errors="coerce")
    modes = output[season_column].map(lambda value: resolve_strategy_mode(value, strategy_mode))
    strategy_features = {str(value) for value in strategy_proxy_features}
    coefficients = dict(adjustment_coefficients or {})
    adjustment = pd.Series(0.0, index=output.index, dtype=float)
    used: list[list[str]] = [[] for _ in range(len(output))]
    ignored_strategy = pd.Series(False, index=output.index, dtype=bool)
    for feature, raw_coefficient in coefficients.items():
        coefficient = float(raw_coefficient)
        if not np.isfinite(coefficient):
            raise ValueError(f"输入调整系数必须有限：{feature}")
        delta = _input_delta(output, str(feature))
        applies = pd.Series(True, index=output.index)
        if str(feature) in strategy_features:
            applies = modes.ne("neutral")
            ignored_strategy |= ~applies
        contribution = coefficient * delta
        adjustment = adjustment.add(contribution.where(applies, 0.0), fill_value=np.nan)
        for position, applies_here in enumerate(applies.to_numpy(dtype=bool)):
            if applies_here:
                used[position].append(str(feature))

    output["internal_baseline_prediction_s"] = internal
    output["external_baseline_prediction_s"] = external
    output["input_adjusted_baseline_prediction_s"] = external + adjustment
    output["internal_gap_s"] = target_time - internal
    output["external_gap_s"] = target_time - external
    output["input_adjustment_s"] = adjustment
    output["input_adjusted_gap_s"] = target_time - (external + adjustment)
    output["strategy_mode"] = modes
    output["strategy_proxy_ignored"] = ignored_strategy
    output["adjustment_features_used"] = [",".join(values) for values in used]
    output["baseline_set"] = "internal|external|input_adjusted"
    output["interpretation_scope"] = "matched_statistical_gap_not_physical_diagnosis"
    return output


def summarize_gap_baselines(gaps: pd.DataFrame) -> pd.DataFrame:
    """用样本量、绝对差和中位偏差并列汇总三条基线。"""

    columns = {
        "internal": "internal_gap_s",
        "external": "external_gap_s",
        "input_adjusted": "input_adjusted_gap_s",
    }
    _required(gaps, columns.values(), "三基线汇总输入")
    rows: list[dict[str, Any]] = []
    for baseline, column in columns.items():
        values = pd.to_numeric(gaps[column], errors="coerce").dropna()
        rows.append(
            {
                "baseline": baseline,
                "n": int(len(values)),
                "mae_s": float(values.abs().mean()) if len(values) else np.nan,
                "median_gap_s": float(values.median()) if len(values) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def benjamini_hochberg(
    p_values: Sequence[float] | pd.Series,
    *,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """计算 Benjamini-Hochberg q 值并控制发现率。"""

    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha 必须位于 (0, 1)")
    values = pd.Series(p_values, copy=True, dtype=float)
    finite = values.notna()
    if ((values[finite] < 0.0) | (values[finite] > 1.0)).any():
        raise ValueError("p 值必须位于 [0, 1]")
    q_values = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values[finite]
    if len(valid):
        order = np.argsort(valid.to_numpy(dtype=float), kind="stable")
        sorted_p = valid.to_numpy(dtype=float)[order]
        ranks = np.arange(1, len(sorted_p) + 1, dtype=float)
        adjusted = sorted_p * len(sorted_p) / ranks
        adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
        adjusted = np.clip(adjusted, 0.0, 1.0)
        original_positions = valid.index.to_numpy()[order]
        q_values.loc[original_positions] = adjusted
    return pd.DataFrame(
        {
            "p_value": values,
            "q_value": q_values,
            "fdr_rejected": q_values.le(alpha).fillna(False),
        },
        index=values.index,
    )


bh_fdr = benjamini_hochberg


def apply_persistent_fdr_gate(
    effects: pd.DataFrame,
    *,
    entity_columns: Sequence[str],
    event_order_column: str,
    p_value_column: str = "p_value",
    effect_column: str = "effect",
    alpha: float = 0.05,
    minimum_consecutive_events: int = 3,
    minimum_absolute_effect: float = 0.0,
    fdr_group_columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """先做 BH/FDR，再要求同方向信号在连续事件中持续出现。

    默认在每个事件内对并列假设控制 FDR。门控只使用当前及过去事件；前两次
    即使显著也仍为探索性，避免回看未来后补发布资格。
    """

    if minimum_consecutive_events < 1:
        raise ValueError("minimum_consecutive_events 至少为 1")
    if minimum_absolute_effect < 0.0:
        raise ValueError("minimum_absolute_effect 不得为负数")
    required = {
        *entity_columns,
        event_order_column,
        p_value_column,
        effect_column,
    }
    _required(effects, required, "持续门控输入")
    work = effects.reset_index(drop=False).rename(columns={"index": "_source_index"}).copy()
    work[p_value_column] = pd.to_numeric(work[p_value_column], errors="coerce")
    work[effect_column] = pd.to_numeric(work[effect_column], errors="coerce")
    work["q_value"] = np.nan
    work["fdr_rejected"] = False

    correction_groups = (
        [event_order_column] if fdr_group_columns is None else list(fdr_group_columns)
    )
    _required(work, correction_groups, "FDR 分组输入")
    if correction_groups:
        iterator = work.groupby(
            correction_groups, dropna=False, observed=True, sort=False
        ).groups.values()
    else:
        iterator = [work.index]
    for positions in iterator:
        positions = list(positions)
        corrected = benjamini_hochberg(work.loc[positions, p_value_column], alpha=alpha)
        work.loc[positions, "q_value"] = corrected["q_value"].to_numpy(dtype=float)
        work.loc[positions, "fdr_rejected"] = corrected["fdr_rejected"].to_numpy(dtype=bool)

    event_values = (
        work[[event_order_column]]
        .drop_duplicates()
        .sort_values(event_order_column, kind="stable")[event_order_column]
        .tolist()
    )
    event_rank = {value: rank for rank, value in enumerate(event_values)}
    work["_event_rank"] = work[event_order_column].map(event_rank)
    work["effect_direction"] = np.sign(work[effect_column].fillna(0.0)).astype(int)
    work["persistence_run_length"] = 0
    work["persistent_gate_passed"] = False

    entity_groups: Any
    if entity_columns:
        entity_groups = work.groupby(
            list(entity_columns), dropna=False, observed=True, sort=False
        ).groups.values()
    else:
        entity_groups = [work.index]
    for positions in entity_groups:
        ordered = work.loc[list(positions)].sort_values("_event_rank", kind="stable")
        run = 0
        previous_rank: int | None = None
        previous_direction = 0
        for position, row in ordered.iterrows():
            direction = int(row["effect_direction"])
            rank = int(row["_event_rank"])
            eligible = bool(row["fdr_rejected"]) and abs(float(row[effect_column])) >= float(
                minimum_absolute_effect
            ) and direction != 0
            consecutive = previous_rank is not None and rank == previous_rank + 1
            if eligible:
                run = run + 1 if consecutive and direction == previous_direction else 1
            else:
                run = 0
            work.at[position, "persistence_run_length"] = run
            work.at[position, "persistent_gate_passed"] = (
                run >= minimum_consecutive_events
            )
            previous_rank = rank
            previous_direction = direction if eligible else 0

    work["publication_status"] = np.where(
        work["persistent_gate_passed"], "persistent_signal", "exploratory_only"
    )
    work["interpretation_scope"] = "fdr_controlled_persistent_association"
    return (
        work.sort_index()
        .drop(columns=["_event_rank"])
        .rename(columns={"_source_index": "source_index"})
    )


persistent_signal_gate = apply_persistent_fdr_gate
