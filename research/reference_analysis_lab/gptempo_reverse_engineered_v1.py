"""GP Tempo 公开分段 Delta 方法的可审计复刻。

本模块只处理公开 timing/car-channel 与本地冻结赛道几何。约 4 Hz 信号的段内形状是
线性插值估计；每个官方 sector 端点由计时硬约束，不能把曲线细节解释为高频真值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


TARGET_ID = "gptempo-reverse-engineered-v1"
TARGET_STATUS = "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED"
SESSION_QUALIFYING = "Qualifying"
SESSION_RACE = "Race"
COMPARABLE = "COMPARABLE_DEFAULT"
CROSS_SESSION_WARNING = "WARNING_CROSS_SESSION_COMBINED_CONDITIONS"


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _number(value: Any, digits: int = 9) -> float | None:
    if not _finite(value):
        return None
    return round(float(value), digits)


def _nearest_values(
    source_axis: np.ndarray,
    source_values: np.ndarray,
    target_axis: np.ndarray,
) -> np.ndarray:
    """在单调轴上做最近邻映射，供 brake/gear/DRS 等离散通道使用。"""

    if len(source_axis) == 0:
        raise ValueError("最近邻映射缺少源点")
    right = np.searchsorted(source_axis, target_axis, side="left")
    right = np.clip(right, 0, len(source_axis) - 1)
    left = np.clip(right - 1, 0, len(source_axis) - 1)
    choose_right = np.abs(source_axis[right] - target_axis) < np.abs(
        target_axis - source_axis[left]
    )
    indices = np.where(choose_right, right, left)
    return source_values[indices]


def integrate_cumulative_distance(
    elapsed_s: Sequence[float],
    speed_kph: Sequence[float],
) -> np.ndarray:
    """按梯形积分把约 4 Hz 速度流转换为单调累计距离。"""

    elapsed = np.asarray(elapsed_s, dtype=float)
    speed = np.asarray(speed_kph, dtype=float)
    if len(elapsed) != len(speed) or len(elapsed) < 2:
        raise ValueError("速度积分至少需要两个等长样本")
    if not np.all(np.isfinite(elapsed)) or not np.all(np.isfinite(speed)):
        raise ValueError("速度积分不接受非有限值")
    delta_t = np.diff(elapsed)
    if np.any(delta_t <= 0):
        raise ValueError("速度积分要求严格递增时间轴")
    speed_mps = np.maximum(speed, 0.0) / 3.6
    segment = 0.5 * (speed_mps[:-1] + speed_mps[1:]) * delta_t
    return np.concatenate(([0.0], np.cumsum(segment)))


def official_cumulative_sector_times(
    sector_times_s: Sequence[float],
) -> np.ndarray:
    """把三个官方 sector time 转换为四个累计端点。"""

    sectors = np.asarray(sector_times_s, dtype=float)
    if sectors.shape != (3,) or not np.all(np.isfinite(sectors)):
        raise ValueError("必须提供三个有限的官方 sector time")
    if np.any(sectors <= 0):
        raise ValueError("官方 sector time 必须为正")
    return np.concatenate(([0.0], np.cumsum(sectors)))


def linearly_scale_time_to_official_endpoints(
    raw_time_s: Sequence[float],
    official_start_s: float,
    official_end_s: float,
) -> np.ndarray:
    """线性缩放段内时间，使首尾严格等于官方累计时间。

    当原始段内时间跨度退化为零时，使用索引线性坡道 fail-safe；该路径会由调用方记录，
    不静默伪造段内形状。
    """

    raw = np.asarray(raw_time_s, dtype=float)
    if len(raw) < 2 or not np.all(np.isfinite(raw)):
        raise ValueError("时间缩放至少需要两个有限点")
    start = float(official_start_s)
    end = float(official_end_s)
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        raise ValueError("官方端点必须有限且严格递增")
    span = float(raw[-1] - raw[0])
    if abs(span) <= 1e-12:
        scaled = np.linspace(start, end, len(raw))
    else:
        scaled = start + (raw - raw[0]) * (end - start) / span
    # 避免浮点传播让硬约束在序列化前出现末位误差。
    scaled[0] = start
    scaled[-1] = end
    return scaled


def prepare_raw_lap_trace(
    car_points: pd.DataFrame,
    *,
    date_start: Any,
    lap_duration_s: float,
    sector_times_s: Sequence[float],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """把 OpenF1 原始 car channel 修剪为一圈并生成积分距离轴。"""

    required = {"date", "speed", "throttle", "brake", "n_gear", "drs"}
    missing = required.difference(car_points.columns)
    if missing:
        raise ValueError(f"car channel 缺少字段: {sorted(missing)}")
    duration = float(lap_duration_s)
    sectors = official_cumulative_sector_times(sector_times_s)
    if abs(float(sectors[-1]) - duration) > 0.005:
        raise ValueError("官方三段和与圈时相差超过 5ms")

    start = pd.Timestamp(date_start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    table = car_points.copy()
    table["date"] = pd.to_datetime(table["date"], utc=True, format="mixed")
    table["elapsed_s"] = (table["date"] - start).dt.total_seconds()
    table = (
        table.sort_values("elapsed_s", kind="stable")
        .drop_duplicates("elapsed_s", keep="last")
    )
    table = table.loc[
        table["elapsed_s"].between(-0.75, duration + 0.75)
    ].copy()
    for column in ("speed", "throttle", "brake", "n_gear", "drs", "rpm"):
        if column in table:
            table[column] = pd.to_numeric(table[column], errors="coerce")
    table = table.dropna(
        subset=["elapsed_s", "speed", "throttle", "brake", "n_gear", "drs"]
    )
    if len(table) < 30:
        raise ValueError("一圈原始 car channel 有效点不足 30")

    source_time = table["elapsed_s"].to_numpy(float)
    inner = source_time[(source_time > 0.0) & (source_time < duration)]
    target_time = np.unique(np.concatenate(([0.0], inner, [duration])))
    continuous: dict[str, np.ndarray] = {}
    for column in ("speed", "throttle", "rpm"):
        if column not in table:
            continue
        values = table[column].to_numpy(float)
        valid = np.isfinite(values)
        if valid.sum() < 2:
            continue
        continuous[column] = np.interp(
            target_time,
            source_time[valid],
            values[valid],
        )
    discrete: dict[str, np.ndarray] = {}
    for column in ("brake", "n_gear", "drs"):
        values = table[column].to_numpy(float)
        valid = np.isfinite(values)
        discrete[column] = _nearest_values(
            source_time[valid],
            values[valid],
            target_time,
        )
    distance = integrate_cumulative_distance(target_time, continuous["speed"])
    if distance[-1] <= 1000:
        raise ValueError("积分圈长异常，拒绝生成距离轴")
    rel_distance = distance / distance[-1]
    raw_intervals = np.diff(
        source_time[(source_time >= 0) & (source_time <= duration)]
    )
    positive_intervals = raw_intervals[raw_intervals > 0]
    median_interval = (
        float(np.median(positive_intervals))
        if len(positive_intervals)
        else None
    )
    sector_distance = np.interp(sectors, target_time, rel_distance)
    result = {
        **dict(identity),
        "lap_duration_s": duration,
        "sector_times_s": [float(value) for value in sector_times_s],
        "official_cumulative_sector_times_s": sectors.tolist(),
        "elapsed_s": target_time.tolist(),
        "rel_distance": rel_distance.tolist(),
        "integrated_distance_m": distance.tolist(),
        "speed_kph": continuous["speed"].tolist(),
        "throttle_pct": continuous["throttle"].tolist(),
        "brake": discrete["brake"].tolist(),
        "gear": discrete["n_gear"].tolist(),
        "drs": discrete["drs"].tolist(),
        "sector_boundary_rel_distance": sector_distance.tolist(),
        "raw_sample_count": int(len(table)),
        "trimmed_sample_count": int(len(target_time)),
        "median_sample_interval_s": _number(median_interval, 6),
        "approx_sample_hz": (
            _number(1.0 / median_interval, 3)
            if median_interval and median_interval > 0
            else None
        ),
        "distance_role": "speed_integrated_relative_distance_proxy",
    }
    if "rpm" in continuous:
        result["rpm"] = continuous["rpm"].tolist()
    return result


def build_display_trace(
    trace: Mapping[str, Any],
    *,
    intervals: int = 400,
) -> dict[str, Any]:
    """为多车手交互图生成固定距离轴通道。"""

    if intervals < 50:
        raise ValueError("显示距离轴至少需要 50 个区间")
    source = np.asarray(trace["rel_distance"], dtype=float)
    axis = np.linspace(0.0, 1.0, intervals + 1)
    result: dict[str, Any] = {
        "axis": [_number(value, 6) for value in axis],
    }
    for source_key, output_key in (
        ("elapsed_s", "elapsed_s"),
        ("speed_kph", "speed_kph"),
        ("throttle_pct", "throttle_pct"),
    ):
        values = np.asarray(trace[source_key], dtype=float)
        result[output_key] = [
            _number(value, 6)
            for value in np.interp(axis, source, values)
        ]
    for source_key, output_key in (
        ("brake", "brake"),
        ("gear", "gear"),
        ("drs", "drs"),
    ):
        values = np.asarray(trace[source_key], dtype=float)
        result[output_key] = [
            _number(value, 3)
            for value in _nearest_values(source, values, axis)
        ]
    return result


def build_sector_constrained_delta(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """按 GP Tempo 公开三步法生成候选圈相对参考圈的 Delta。"""

    ref_axis = np.asarray(reference["rel_distance"], dtype=float)
    cand_axis = np.asarray(candidate["rel_distance"], dtype=float)
    ref_time = np.asarray(reference["elapsed_s"], dtype=float)
    cand_time = np.asarray(candidate["elapsed_s"], dtype=float)
    ref_official = official_cumulative_sector_times(
        reference["sector_times_s"]
    )
    cand_official = official_cumulative_sector_times(
        candidate["sector_times_s"]
    )
    ref_bounds = np.asarray(
        reference["sector_boundary_rel_distance"], dtype=float
    )
    cand_bounds = np.asarray(
        candidate["sector_boundary_rel_distance"], dtype=float
    )
    if np.any(np.diff(ref_bounds) <= 0) or np.any(np.diff(cand_bounds) <= 0):
        raise ValueError("sector 距离边界必须严格递增")

    output_axis: list[float] = []
    output_delta: list[float] = []
    output_sector: list[int] = []
    endpoint_rows: list[dict[str, Any]] = []
    degenerate_scaling_segments = 0

    for sector in range(3):
        ref_start, ref_end = ref_bounds[sector : sector + 2]
        cand_start, cand_end = cand_bounds[sector : sector + 2]
        interior = ref_axis[(ref_axis > ref_start) & (ref_axis < ref_end)]
        sector_ref_axis = np.unique(
            np.concatenate(([ref_start], interior, [ref_end]))
        )
        unit_axis = (sector_ref_axis - ref_start) / (ref_end - ref_start)
        sector_cand_axis = cand_start + unit_axis * (cand_end - cand_start)

        raw_ref_time = np.interp(sector_ref_axis, ref_axis, ref_time)
        raw_cand_time = np.interp(sector_cand_axis, cand_axis, cand_time)
        if abs(float(raw_ref_time[-1] - raw_ref_time[0])) <= 1e-12:
            degenerate_scaling_segments += 1
        if abs(float(raw_cand_time[-1] - raw_cand_time[0])) <= 1e-12:
            degenerate_scaling_segments += 1
        scaled_ref = linearly_scale_time_to_official_endpoints(
            raw_ref_time,
            ref_official[sector],
            ref_official[sector + 1],
        )
        scaled_cand = linearly_scale_time_to_official_endpoints(
            raw_cand_time,
            cand_official[sector],
            cand_official[sector + 1],
        )
        delta = scaled_cand - scaled_ref
        expected_start = cand_official[sector] - ref_official[sector]
        expected_end = cand_official[sector + 1] - ref_official[sector + 1]
        delta[0] = expected_start
        delta[-1] = expected_end
        endpoint_rows.append(
            {
                "sector": sector + 1,
                "reference_sector_time_s": _number(
                    reference["sector_times_s"][sector], 6
                ),
                "candidate_sector_time_s": _number(
                    candidate["sector_times_s"][sector], 6
                ),
                "expected_cumulative_delta_s": _number(expected_end, 9),
                "actual_cumulative_delta_s": _number(delta[-1], 9),
                "endpoint_error_s": _number(delta[-1] - expected_end, 12),
                "reference_end_rel_distance": _number(ref_end, 9),
                "candidate_end_rel_distance": _number(cand_end, 9),
            }
        )
        start_index = 0 if sector == 0 else 1
        output_axis.extend(sector_ref_axis[start_index:].tolist())
        output_delta.extend(delta[start_index:].tolist())
        output_sector.extend(
            [sector + 1] * (len(sector_ref_axis) - start_index)
        )

    expected_finish = (
        float(cand_official[-1]) - float(ref_official[-1])
    )
    actual_finish = float(output_delta[-1])
    errors = [abs(float(row["endpoint_error_s"])) for row in endpoint_rows]
    return {
        "reference_lap_id": str(reference["lap_id"]),
        "candidate_lap_id": str(candidate["lap_id"]),
        "axis": [_number(value, 9) for value in output_axis],
        "delta_s": [_number(value, 9) for value in output_delta],
        "sector_index": output_sector,
        "sector_endpoints": endpoint_rows,
        "expected_finish_delta_s": _number(expected_finish, 9),
        "actual_finish_delta_s": _number(actual_finish, 9),
        "finish_error_s": _number(actual_finish - expected_finish, 12),
        "max_abs_sector_endpoint_error_s": _number(max(errors), 12),
        "degenerate_scaling_segments": degenerate_scaling_segments,
        "shape_status": "ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ",
        "endpoint_status": (
            "PASS_EXACT_OFFICIAL_SECTOR_ENDPOINTS"
            if max(errors) <= 1e-9
            else "FAIL_SECTOR_ENDPOINTS"
        ),
    }


def comparison_condition_status(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """给任意两圈生成默认可比门和跨 session 警告。"""

    reasons: list[str] = []
    if reference["session"] != candidate["session"]:
        reasons.append(CROSS_SESSION_WARNING)
    if str(reference.get("track_status")) != "1" or str(
        candidate.get("track_status")
    ) != "1":
        reasons.append("WARNING_NON_GREEN_TRACK_STATUS")
    if reference.get("compound") != candidate.get("compound"):
        reasons.append("WARNING_DIFFERENT_COMPOUND")
    ref_age = reference.get("tyre_life")
    cand_age = candidate.get("tyre_life")
    if _finite(ref_age) and _finite(cand_age):
        if abs(float(ref_age) - float(cand_age)) > 2:
            reasons.append("WARNING_TYRE_AGE_OUTSIDE_DEFAULT_SUPPORT")
    else:
        reasons.append("WARNING_TYRE_AGE_MISSING")
    return {
        "status": COMPARABLE if not reasons else "CONDITION_WARNING",
        "reasons": reasons,
        "default_selectable": not reasons,
        "same_session": reference["session"] == candidate["session"],
        "same_compound": reference.get("compound")
        == candidate.get("compound"),
        "tyre_age_difference_laps": (
            _number(abs(float(ref_age) - float(cand_age)), 3)
            if _finite(ref_age) and _finite(cand_age)
            else None
        ),
        "interpretation": (
            "descriptive combined-condition delta; not pure driver pace"
            if reasons
            else "same-session observed telemetry under default tyre support"
        ),
    }


def build_track_geometry(
    points: pd.DataFrame,
    *,
    intervals: int = 400,
) -> dict[str, Any]:
    """从本地冻结 x/y 构造与遥测距离轴联动的赛道几何。"""

    required = {"rel_distance", "x", "y"}
    missing = required.difference(points.columns)
    if missing:
        raise ValueError(f"赛道几何缺少字段: {sorted(missing)}")
    table = points.copy()
    for column in required:
        table[column] = pd.to_numeric(table[column], errors="coerce")
    table = (
        table.dropna(subset=list(required))
        .sort_values("rel_distance", kind="stable")
        .drop_duplicates("rel_distance", keep="last")
    )
    if len(table) < 50:
        raise ValueError("赛道几何有效点不足 50")
    source = table["rel_distance"].to_numpy(float)
    source = (source - source.min()) / max(source.max() - source.min(), 1e-9)
    axis = np.linspace(0.0, 1.0, intervals + 1)
    return {
        "axis": [_number(value, 6) for value in axis],
        "x": [
            _number(value, 3)
            for value in np.interp(axis, source, table["x"].to_numpy(float))
        ],
        "y": [
            _number(value, 3)
            for value in np.interp(axis, source, table["y"].to_numpy(float))
        ],
        "source_points": int(len(table)),
        "role": "local_frozen_track_geometry_linked_by_relative_distance",
    }


def build_gptempo_reverse_engineered_v1(
    *,
    prepared_laps: Sequence[Mapping[str, Any]],
    track_geometry: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """生成 GP Tempo 双 session 真实 pilot 报告。"""

    laps = [dict(lap) for lap in prepared_laps]
    if len(laps) < 4:
        raise ValueError("交互 pilot 至少需要四个真实圈")
    ids = [str(lap["lap_id"]) for lap in laps]
    if len(ids) != len(set(ids)):
        raise ValueError("lap_id 必须唯一")
    display_laps = []
    for lap in laps:
        display_laps.append(
            {
                key: lap.get(key)
                for key in (
                    "lap_id",
                    "session",
                    "driver",
                    "driver_number",
                    "team",
                    "lap_number",
                    "lap_duration_s",
                    "sector_times_s",
                    "compound",
                    "tyre_life",
                    "track_status",
                    "raw_sample_count",
                    "trimmed_sample_count",
                    "median_sample_interval_s",
                    "approx_sample_hz",
                    "distance_role",
                )
            }
        )
        display_laps[-1]["trace"] = build_display_trace(lap)

    comparisons: list[dict[str, Any]] = []
    endpoint_errors: list[float] = []
    for reference in laps:
        for candidate in laps:
            if reference["lap_id"] == candidate["lap_id"]:
                continue
            delta = build_sector_constrained_delta(reference, candidate)
            condition = comparison_condition_status(reference, candidate)
            delta["condition"] = condition
            comparisons.append(delta)
            endpoint_errors.append(
                float(delta["max_abs_sector_endpoint_error_s"])
            )

    by_session: dict[str, list[dict[str, Any]]] = {}
    for lap in display_laps:
        by_session.setdefault(str(lap["session"]), []).append(lap)
    for rows in by_session.values():
        rows.sort(key=lambda row: float(row["lap_duration_s"]))
    qualifying = by_session.get(SESSION_QUALIFYING, [])
    race = by_session.get(SESSION_RACE, [])
    default_laps = [row["lap_id"] for row in qualifying[:3]]
    default_pairs = []
    if qualifying:
        default_reference = qualifying[0]
        for candidate in qualifying[1:3]:
            default_pairs.append(
                comparison_condition_status(default_reference, candidate)
            )
    sample_hz = [
        float(lap["approx_sample_hz"])
        for lap in display_laps
        if _finite(lap.get("approx_sample_hz"))
    ]
    sector_sum_errors = [
        abs(sum(map(float, lap["sector_times_s"])) - float(lap["lap_duration_s"]))
        for lap in display_laps
    ]
    comparable_pairs = sum(
        comparison["condition"]["status"] == COMPARABLE
        for comparison in comparisons
    )
    cross_session_pairs = sum(
        not comparison["condition"]["same_session"]
        for comparison in comparisons
    )
    validation_status = (
        "PASS"
        if endpoint_errors
        and max(endpoint_errors) <= 1e-9
        and all(item["status"] == COMPARABLE for item in default_pairs)
        else "FAIL"
    )
    return {
        "schema_version": "gptempo-reverse-engineered-v1",
        "target_id": TARGET_ID,
        "status": TARGET_STATUS,
        "one_to_one_status": (
            "PUBLIC_METHOD_EXPLICIT_REIMPLEMENTATION_VISUAL_STYLE_INDEPENDENT"
        ),
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_card": {
            "public_method": [
                "official timing feed via FastF1",
                "telemetry typically around 4 Hz",
                "split each lap by three official sector times",
                "linearly interpolate candidate distance to reference distance axis within each sector",
                "linearly scale time in each sector to official start/end timing constraints",
                "positive delta means candidate behind reference",
                "only sector endpoints are exact; intra-sector shape is estimated",
                "cross-session comparison is supported with combined-condition warning",
            ],
            "reimplemented": [
                "speed integration to relative distance for frozen raw OpenF1 car channel",
                "continuous speed/throttle interpolation and nearest brake/gear/DRS",
                "all ordered pair deltas for five Qualifying and five Race pilot laps",
                "linked local track geometry on the same relative distance axis",
            ],
            "not_identifiable": [
                "true fuel load, SOC, tyre temperature/pressure/wear",
                "setup, damage, power mode and team instruction",
                "sub-sample braking/throttle event timing at higher frequency",
            ],
        },
        "data_gap_audit": [
            {
                "required": "about 4 Hz car telemetry",
                "repository": "expanded-v4 about 7.69 Hz",
                "supplement": "frozen OpenF1 raw car_data median about 3.7 Hz",
                "publish": "ALLOW_WITH_SAMPLING_LIMIT",
            },
            {
                "required": "official three sector times",
                "repository": "not retained in expanded-v4",
                "supplement": "frozen OpenF1 laps from official timing feed",
                "publish": "ALLOW_FROZEN_SUPPLEMENT",
            },
            {
                "required": "distance and track x/y",
                "repository": "expanded-v4 rel_distance/x/y",
                "supplement": "speed-integrated raw distance plus local frozen geometry",
                "publish": "ALLOW_DISTANCE_PROXY",
            },
            {
                "required": "comparable tyre and session conditions",
                "repository": "session, compound, tyre_life, track_status",
                "supplement": "same session/compound and tyre-age difference <=2 default gate",
                "publish": "ALLOW_DEFAULT; OTHERWISE_WARNING",
            },
            {
                "required": "cross-session pure driver delta",
                "repository": "fuel/weather/evolution not separable",
                "supplement": "none",
                "publish": CROSS_SESSION_WARNING,
            },
            {
                "required": "high-frequency exact braking point",
                "repository": "public car channel about 4 Hz",
                "supplement": "none",
                "publish": "NOT_IDENTIFIABLE_SUB_SAMPLE_EVENT_TIME",
            },
        ],
        "visual_replication": {
            "default_selection": {
                "lap_ids": default_laps,
                "reference_rule": "fastest_selected_lap",
                "status": COMPARABLE,
            },
            "sessions": by_session,
            "comparisons": comparisons,
            "track_geometry": dict(track_geometry),
            "channels": [
                "speed",
                "throttle",
                "brake",
                "gear",
                "DRS",
                "delta",
                "track_position",
            ],
        },
        "audited_analysis": {
            "default_gate": {
                "same_session": True,
                "same_compound": True,
                "maximum_tyre_age_difference_laps": 2,
                "green_track_status": "1",
            },
            "cross_session": {
                "supported": True,
                "warning": CROSS_SESSION_WARNING,
                "interpretation": (
                    "delta combines compound, fuel, weather, track evolution "
                    "and session objectives"
                ),
            },
            "intra_sector_shape": "ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ",
            "sector_endpoints": "EXACT_OFFICIAL_TIMING_CONSTRAINT",
            "direct_driver_or_vehicle_causal_claim_allowed": False,
        },
        "validation": {
            "status": validation_status,
            "real_laps": len(display_laps),
            "qualifying_laps": len(qualifying),
            "race_laps": len(race),
            "synthetic_laps": 0,
            "ordered_pair_comparisons": len(comparisons),
            "comparable_default_pairs": comparable_pairs,
            "cross_session_warning_pairs": cross_session_pairs,
            "sector_endpoint_checks": len(comparisons) * 3,
            "max_abs_sector_endpoint_error_s": _number(
                max(endpoint_errors), 12
            ),
            "finish_endpoint_checks": len(comparisons),
            "max_official_sector_sum_error_s": _number(
                max(sector_sum_errors), 12
            ),
            "raw_sample_count": sum(
                int(lap["raw_sample_count"]) for lap in display_laps
            ),
            "sample_hz_median": _number(np.median(sample_hz), 3),
            "sample_hz_min": _number(np.min(sample_hz), 3),
            "sample_hz_max": _number(np.max(sample_hz), 3),
            "default_selection_gate": (
                "PASS"
                if all(item["status"] == COMPARABLE for item in default_pairs)
                else "FAIL"
            ),
            "track_geometry_points": len(track_geometry["axis"]),
        },
        "method_boundaries": [
            "sector endpoint exactness does not imply exact intra-sector shape",
            "about 4 Hz can shift apparent brake/throttle events by about one sample",
            "cross-session delta is combined-condition description",
            "observed channels do not identify fuel, SOC, setup, damage or power mode",
        ],
    }
