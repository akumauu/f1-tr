"""FDataAnalysis 参考图表的真实遥测方法等价 pilot。

所有图表字段都从 2025 Abu Dhabi Race 的真实逐点遥测计算。赛道距离上的弯道和
直道使用可复核的速度局部极小值/距离窗 proxy，不把它们命名成 FIA 官方弯道、
下压力或引擎功率。
"""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from reference_analysis_lab.f1pace_pilot import build_lap_table


AXIS_BINS = 200
FULL_THROTTLE_THRESHOLD = 95.0
CORNER_WINDOW = 0.0125
CORNER_BRAKE_LOOKBACK = 0.025
TARGET_CORNER_COUNT = 16


def _number(value: Any, digits: int = 6) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    rounded = round(number, digits)
    return int(rounded) if rounded.is_integer() else rounded


def linear_slope(x_values: Iterable[float], y_values: Iterable[float]) -> float | None:
    """计算描述性一阶斜率；样本不足或 x 不变时返回 None。"""

    pairs = [
        (float(x), float(y))
        for x, y in zip(x_values, y_values)
        if _number(x) is not None and _number(y) is not None
    ]
    if len(pairs) < 3:
        return None
    x = np.asarray([pair[0] for pair in pairs], dtype=float)
    y = np.asarray([pair[1] for pair in pairs], dtype=float)
    centered = x - x.mean()
    denominator = float((centered * centered).sum())
    if denominator <= 0.0:
        return None
    return float((centered * (y - y.mean())).sum() / denominator)


def _time_weighted_points(points: pd.DataFrame) -> pd.DataFrame:
    frame = points.copy()
    frame["time"] = pd.to_numeric(frame["time"], errors="coerce")
    frame["lap_duration"] = pd.to_numeric(frame["lap_duration"], errors="coerce")
    frame["rel_distance"] = pd.to_numeric(frame["rel_distance"], errors="coerce")
    frame = frame.sort_values(["driver_acronym", "lap", "time", "sample_index"])
    group_keys = ["driver_acronym", "lap"]
    next_time = frame.groupby(group_keys, sort=False)["time"].shift(-1)
    interval = next_time - frame["time"]
    positive = interval.where(interval > 0.0)
    fallback = positive.groupby(
        [frame[key] for key in group_keys], sort=False
    ).transform("median")
    tail = frame["lap_duration"] - frame["time"]
    frame["time_weight_s"] = interval.where(interval > 0.0, tail.where(tail > 0.0, fallback))
    frame["time_weight_s"] = frame["time_weight_s"].fillna(0.05).clip(lower=0.001, upper=2.0)
    frame["rel_distance"] = frame["rel_distance"].clip(lower=0.0, upper=0.999999)
    frame["axis_bin"] = (frame["rel_distance"] * AXIS_BINS).astype(int).clip(0, AXIS_BINS - 1)
    frame["bin_center"] = (frame["axis_bin"] + 0.5) / AXIS_BINS
    frame["full_throttle_flag"] = (pd.to_numeric(frame["throttle"], errors="coerce") >= FULL_THROTTLE_THRESHOLD).astype(float)
    frame["brake_flag"] = (pd.to_numeric(frame["brake"], errors="coerce") > 0).astype(float)
    frame["drs_flag"] = (pd.to_numeric(frame["drs"], errors="coerce") > 0).astype(float)
    return frame


def _aggregate_axis(points: pd.DataFrame, group_prefix: list[str]) -> pd.DataFrame:
    keys = group_prefix + ["axis_bin"]
    grouped = points.groupby(keys, sort=False, observed=True)
    axis = grouped.agg(
        bin_center=("bin_center", "median"),
        speed_kph=("speed", "median"),
        throttle=("throttle", "median"),
        gear=("gear", "median"),
        tyre_life=("tyre_life", "median"),
        compound=("compound", "first"),
        team=("team", "first"),
        telemetry_samples=("speed", "size"),
        time_weight_s=("time_weight_s", "sum"),
        full_throttle_weight=("full_throttle_flag", lambda values: float(values.sum())),
        brake_weight=("brake_flag", lambda values: float(values.sum())),
        drs_weight=("drs_flag", lambda values: float(values.sum())),
    ).reset_index()
    # 上述 flag 已经是 0/1，必须用原始 time_weight 重新加权，避免采样密度冒充时间占比。
    weighted = points.copy()
    for column in ("full_throttle_flag", "brake_flag", "drs_flag"):
        weighted[f"{column}_seconds"] = weighted[column] * weighted["time_weight_s"]
    weighted_sums = weighted.groupby(keys, sort=False, observed=True).agg(
        time_weight_s=("time_weight_s", "sum"),
        full_throttle_seconds=("full_throttle_flag_seconds", "sum"),
        brake_seconds=("brake_flag_seconds", "sum"),
        drs_seconds=("drs_flag_seconds", "sum"),
    ).reset_index()
    axis = axis.drop(columns=["time_weight_s", "full_throttle_weight", "brake_weight", "drs_weight"])
    axis = axis.merge(weighted_sums, on=keys, how="left", suffixes=("", "_weighted"))
    denominator = axis["time_weight_s"].replace(0, np.nan)
    axis["full_throttle_share"] = axis["full_throttle_seconds"] / denominator
    axis["brake_share"] = axis["brake_seconds"] / denominator
    axis["drs_share"] = axis["drs_seconds"] / denominator
    axis["time_weight_s"] = axis["time_weight_s"].fillna(0.0)
    return axis


def _global_axis(axis: pd.DataFrame) -> pd.DataFrame:
    grouped = axis.groupby("axis_bin", sort=True, observed=True)
    global_axis = grouped.agg(
        bin_center=("bin_center", "median"),
        speed_kph=("speed_kph", "median"),
        throttle=("throttle", "median"),
        full_throttle_share=("full_throttle_share", "mean"),
        brake_share=("brake_share", "mean"),
        drs_share=("drs_share", "mean"),
        coverage_rows=("speed_kph", "count"),
    ).reset_index()
    return global_axis.sort_values("axis_bin").reset_index(drop=True)


def detect_distance_corner_proxies(global_axis: pd.DataFrame) -> list[dict[str, Any]]:
    """用全场距离轴速度局部极小值生成具名 proxy，不冒充官方弯道锚点。"""

    frame = global_axis.dropna(subset=["speed_kph"]).sort_values("axis_bin").copy()
    if frame.empty:
        return []
    frame["speed_smooth"] = frame["speed_kph"].rolling(5, center=True, min_periods=1).median()
    values = frame["speed_smooth"].to_numpy(dtype=float)
    bins = frame["axis_bin"].to_numpy(dtype=int)
    candidates = []
    for index in range(3, len(frame) - 3):
        local = values[index - 3 : index + 4]
        if values[index] <= np.nanmin(local):
            candidates.append((float(values[index]), int(bins[index])))
    candidates.sort()
    selected: list[tuple[float, int]] = []
    for separation in (0.035, 0.025, 0.018):
        selected = []
        for speed, axis_bin in candidates:
            rel = (axis_bin + 0.5) / AXIS_BINS
            if all(abs(rel - ((other_bin + 0.5) / AXIS_BINS)) >= separation for _, other_bin in selected):
                selected.append((speed, axis_bin))
            if len(selected) >= TARGET_CORNER_COUNT:
                break
        if len(selected) >= min(12, TARGET_CORNER_COUNT):
            break
    selected = sorted(selected[:TARGET_CORNER_COUNT], key=lambda item: item[1])
    corners = []
    for number, (speed, axis_bin) in enumerate(selected, start=1):
        minimum = (axis_bin + 0.5) / AXIS_BINS
        corners.append(
            {
                "corner_id": f"C{number:02d}",
                "segment_role": "distance_axis_speed_local_minimum_proxy",
                "entry_rel_distance": _number(minimum - CORNER_WINDOW),
                "minimum_rel_distance": _number(minimum),
                "exit_rel_distance": _number(minimum + CORNER_WINDOW),
                "global_proxy_speed_kph": _number(speed),
                "anchor_definition": "smoothed median speed local minimum on 0.005 rel_distance axis",
            }
        )
    return corners


def build_straight_proxies(corners: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not corners:
        return []
    straights = []
    for index, current in enumerate(corners):
        following = corners[(index + 1) % len(corners)]
        start = float(current["exit_rel_distance"])
        end = float(following["entry_rel_distance"])
        if index == len(corners) - 1:
            end += 1.0
        if end - start < 0.015:
            continue
        straights.append(
            {
                "straight_id": f"S{len(straights) + 1:02d}",
                "from_corner": current["corner_id"],
                "to_corner": following["corner_id"],
                "start_rel_distance": _number(start % 1.0),
                "end_rel_distance": _number(end % 1.0),
                "span_rel_distance": _number(end - start),
                "segment_role": "between_proxy_corners_distance_window",
            }
        )
    return straights


def _window(frame: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    start = float(start)
    end = float(end)
    if start >= 0.0 and end <= 1.0:
        return frame[(frame["bin_center"] >= start) & (frame["bin_center"] <= end)]
    start_mod = start % 1.0
    end_mod = end % 1.0
    return frame[(frame["bin_center"] >= start_mod) | (frame["bin_center"] <= end_mod)]


def _nearest_axis_rows(frame: pd.DataFrame, target: float) -> pd.DataFrame:
    if frame.empty:
        return frame
    candidate = frame.assign(distance_to_target=(frame["bin_center"] - target).abs())
    return candidate.sort_values("distance_to_target").groupby("lap", as_index=False).first()


def _corner_feature_rows(
    driver: str,
    points: pd.DataFrame,
    axis: pd.DataFrame,
    corners: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    driver_points = points[points["driver_acronym"] == driver]
    driver_axis = axis[axis["driver_acronym"] == driver]
    for corner in corners:
        entry = float(corner["entry_rel_distance"])
        minimum = float(corner["minimum_rel_distance"])
        exit_distance = float(corner["exit_rel_distance"])
        axis_window = _window(driver_axis, entry, exit_distance)
        entry_rows = _nearest_axis_rows(axis_window, entry % 1.0)
        minimum_rows = _nearest_axis_rows(axis_window, minimum % 1.0)
        exit_rows = _nearest_axis_rows(axis_window, exit_distance % 1.0)
        lap_features = pd.DataFrame({"lap": sorted(set(entry_rows["lap"]) & set(minimum_rows["lap"]) & set(exit_rows["lap"]))})
        if not lap_features.empty:
            lap_features = lap_features.merge(entry_rows[["lap", "speed_kph"]].rename(columns={"speed_kph": "entry_speed_kph"}), on="lap")
            lap_features = lap_features.merge(minimum_rows[["lap", "speed_kph"]].rename(columns={"speed_kph": "minimum_speed_kph"}), on="lap")
            lap_features = lap_features.merge(exit_rows[["lap", "speed_kph"]].rename(columns={"speed_kph": "exit_speed_kph"}), on="lap")
        brake_points = driver_points[
            (driver_points["rel_distance"] >= max(0.0, entry - CORNER_BRAKE_LOOKBACK))
            & (driver_points["rel_distance"] <= minimum)
            & (driver_points["brake_flag"] > 0)
        ]
        brake_rows = brake_points.groupby("lap", as_index=False)["rel_distance"].min().rename(columns={"rel_distance": "brake_position_rel_distance"})
        if not lap_features.empty:
            lap_features = lap_features.merge(brake_rows, on="lap", how="left")
            lap_features["brake_to_apex_distance_fraction"] = minimum - lap_features["brake_position_rel_distance"]
        output.append(
            {
                "corner_id": corner["corner_id"],
                "entry_speed_kph": _number(lap_features["entry_speed_kph"].mean()) if not lap_features.empty else None,
                "minimum_speed_kph": _number(lap_features["minimum_speed_kph"].mean()) if not lap_features.empty else None,
                "exit_speed_kph": _number(lap_features["exit_speed_kph"].mean()) if not lap_features.empty else None,
                "brake_position_rel_distance": _number(lap_features["brake_position_rel_distance"].mean()) if not lap_features.empty else None,
                "brake_to_apex_distance_fraction": _number(lap_features["brake_to_apex_distance_fraction"].mean()) if not lap_features.empty else None,
                "valid_laps": int(len(lap_features)),
                "feature_role": "distance_axis_observed_corner_proxy",
            }
        )
    return output


def _straight_feature_rows(
    driver: str,
    axis: pd.DataFrame,
    straights: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    driver_axis = axis[axis["driver_acronym"] == driver]
    for straight in straights:
        start = float(straight["start_rel_distance"])
        end = start + float(straight["span_rel_distance"])
        window = _window(driver_axis, start, end)
        if window.empty:
            output.append({
                "straight_id": straight["straight_id"],
                "max_speed_kph": None,
                "p90_speed_kph": None,
                "full_throttle_share": None,
                "drs_share": None,
                "valid_laps": 0,
                "feature_role": "distance_axis_observed_straight_proxy",
            })
            continue
        per_lap = window.groupby("lap", as_index=False).agg(
            max_speed_kph=("speed_kph", "max"),
            p90_speed_kph=("speed_kph", lambda values: values.quantile(0.9)),
            full_throttle_share=("full_throttle_share", "mean"),
            drs_share=("drs_share", "mean"),
        )
        output.append(
            {
                "straight_id": straight["straight_id"],
                "max_speed_kph": _number(per_lap["max_speed_kph"].mean()),
                "p90_speed_kph": _number(per_lap["p90_speed_kph"].mean()),
                "full_throttle_share": _number(per_lap["full_throttle_share"].mean()),
                "drs_share": _number(per_lap["drs_share"].mean()),
                "valid_laps": int(len(per_lap)),
                "feature_role": "distance_axis_observed_straight_proxy",
            }
        )
    return output


def _lap_metrics(points: pd.DataFrame) -> pd.DataFrame:
    grouped = points.groupby(["driver_acronym", "lap"], sort=False, observed=True)
    rows = grouped.agg(
        tyre_life=("tyre_life", "median"),
        lap_duration=("lap_duration", "median"),
        median_speed_kph=("speed", "median"),
        time_weight_s=("time_weight_s", "sum"),
    ).reset_index()
    weighted = points.copy()
    for column in ("full_throttle_flag", "brake_flag", "drs_flag"):
        weighted[f"{column}_seconds"] = weighted[column] * weighted["time_weight_s"]
    usage = weighted.groupby(["driver_acronym", "lap"], sort=False, observed=True).agg(
        time_weight_s=("time_weight_s", "sum"),
        full_throttle_seconds=("full_throttle_flag_seconds", "sum"),
        brake_seconds=("brake_flag_seconds", "sum"),
        drs_seconds=("drs_flag_seconds", "sum"),
    ).reset_index()
    rows = rows.drop(columns=["time_weight_s"]).merge(usage, on=["driver_acronym", "lap"], how="left")
    denominator = rows["time_weight_s"].replace(0, np.nan)
    rows["full_throttle_share"] = rows["full_throttle_seconds"] / denominator
    rows["brake_share"] = rows["brake_seconds"] / denominator
    rows["drs_share"] = rows["drs_seconds"] / denominator
    return rows


def _driver_scalar_profile(driver: str, points: pd.DataFrame, axis: pd.DataFrame, laps: pd.DataFrame) -> dict[str, Any]:
    driver_axis = axis[axis["driver_acronym"] == driver]
    driver_laps = laps[laps["driver_acronym"] == driver]
    if driver_axis.empty:
        return {
            "driver": driver,
            "team": None,
            "valid_laps": 0,
            "distance_axis_bins": 0,
            "speed_distribution": {},
            "control_usage": {},
            "tyre_age_effects": {},
        }
    first_team = str(driver_axis["team"].dropna().iloc[0]) if driver_axis["team"].notna().any() else None
    return {
        "driver": driver,
        "team": first_team,
        "valid_laps": int(driver_laps["lap"].nunique()),
        "distance_axis_bins": int(driver_axis["axis_bin"].nunique()),
        "speed_distribution": {
            "p10_kph": _number(driver_axis["speed_kph"].quantile(0.10)),
            "p50_kph": _number(driver_axis["speed_kph"].quantile(0.50)),
            "p90_kph": _number(driver_axis["speed_kph"].quantile(0.90)),
            "min_kph": _number(driver_axis["speed_kph"].min()),
            "max_kph": _number(driver_axis["speed_kph"].max()),
            "role": "observed_speed_distribution_proxy",
        },
        "control_usage": {
            "full_throttle_share": _number(driver_laps["full_throttle_share"].mean()),
            "brake_share": _number(driver_laps["brake_share"].mean()),
            "drs_share": _number(driver_laps["drs_share"].mean()),
            "median_gear": _number(driver_axis["gear"].median()),
            "gear_p10": _number(driver_axis["gear"].quantile(0.10)),
            "gear_p90": _number(driver_axis["gear"].quantile(0.90)),
            "role": "observed_control_feature_proxy",
        },
        "tyre_age_effects": {
            "lap_time_s_per_tyre_lap": _number(linear_slope(driver_laps["tyre_life"], driver_laps["lap_duration"])),
            "median_speed_kph_per_tyre_lap": _number(linear_slope(driver_laps["tyre_life"], driver_laps["median_speed_kph"])),
            "tyre_age_min_laps": _number(driver_laps["tyre_life"].min()),
            "tyre_age_max_laps": _number(driver_laps["tyre_life"].max()),
            "role": "observed_tyre_age_change_proxy_not_physical_wear",
        },
    }


def _aggregate_profile_rows(rows: list[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key))].append(row)
    output = []
    for value, group in grouped.items():
        weights = np.asarray([max(float(row.get("valid_laps") or 0), 1.0) for row in group], dtype=float)
        def average(path: tuple[str, ...]) -> float | None:
            values = [row for row in group]
            for part in path:
                values = [item.get(part) if isinstance(item, Mapping) else None for item in values]
            numeric = [(float(item), weight) for item, weight in zip(values, weights) if _number(item) is not None]
            if not numeric:
                return None
            total = sum(weight for _, weight in numeric)
            return sum(value * weight for value, weight in numeric) / total
        output.append(
            {
                key: value,
                "drivers": sorted(str(row.get("driver")) for row in group),
                "valid_laps": int(sum(int(row.get("valid_laps") or 0) for row in group)),
                "speed_distribution": {
                    name: _number(average(("speed_distribution", name)))
                    for name in ("p10_kph", "p50_kph", "p90_kph", "min_kph", "max_kph")
                },
                "control_usage": {
                    name: _number(average(("control_usage", name)))
                    for name in ("full_throttle_share", "brake_share", "drs_share", "median_gear", "gear_p10", "gear_p90")
                },
                "tyre_age_effects": {
                    name: _number(average(("tyre_age_effects", name)))
                    for name in ("lap_time_s_per_tyre_lap", "median_speed_kph_per_tyre_lap", "tyre_age_min_laps", "tyre_age_max_laps")
                },
                "profile_role": "team_aggregate_of_real_driver_distance_axis_features_not_vehicle_causal_model",
            }
        )
    return sorted(output, key=lambda row: row.get("speed_distribution", {}).get("p50_kph") or -1, reverse=True)


def _teammate_baselines(profiles: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_team: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for profile in profiles:
        if profile.get("team"):
            by_team[str(profile["team"])].append(profile)
    output = []
    for team, rows in sorted(by_team.items()):
        if len(rows) != 2:
            continue
        left, right = rows
        for current, teammate in ((left, right), (right, left)):
            def difference(path: tuple[str, ...]) -> float | None:
                left_value: Any = current
                right_value: Any = teammate
                for part in path:
                    left_value = left_value.get(part) if isinstance(left_value, Mapping) else None
                    right_value = right_value.get(part) if isinstance(right_value, Mapping) else None
                if _number(left_value) is None or _number(right_value) is None:
                    return None
                return _number(float(left_value) - float(right_value))
            output.append(
                {
                    "team": team,
                    "driver": current.get("driver"),
                    "teammate": teammate.get("driver"),
                    "driver_minus_teammate": {
                        "median_speed_kph": difference(("speed_distribution", "p50_kph")),
                        "full_throttle_share": difference(("control_usage", "full_throttle_share")),
                        "brake_share": difference(("control_usage", "brake_share")),
                        "drs_share": difference(("control_usage", "drs_share")),
                        "lap_time_s_per_tyre_lap": difference(("tyre_age_effects", "lap_time_s_per_tyre_lap")),
                    },
                    "role": "same_team_observed_baseline_not_driver_causal_effect",
                }
            )
    return output


def build_fdataanalysis_pilot(
    points: pd.DataFrame,
    *,
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """构造 FDataAnalysis 真实 Abu Dhabi pilot。"""

    required = {
        "driver_acronym", "lap", "sample_index", "time", "rel_distance", "speed",
        "throttle", "brake", "gear", "drs", "tyre_life", "compound", "stint",
        "team", "lap_duration", "track_status", "is_accurate",
    }
    missing = sorted(required - set(points.columns))
    if missing:
        raise ValueError(f"FDataAnalysis pilot 缺少字段: {missing}")
    raw = points.copy()
    raw["speed"] = pd.to_numeric(raw["speed"], errors="coerce")
    raw["throttle"] = pd.to_numeric(raw["throttle"], errors="coerce")
    raw["brake"] = pd.to_numeric(raw["brake"], errors="coerce")
    raw["gear"] = pd.to_numeric(raw["gear"], errors="coerce")
    raw["drs"] = pd.to_numeric(raw["drs"], errors="coerce")
    raw["tyre_life"] = pd.to_numeric(raw["tyre_life"], errors="coerce")
    lap_table = build_lap_table(raw)
    valid_laps = lap_table[lap_table["pace_eligible"]][["driver_acronym", "lap"]].drop_duplicates()
    valid_points = raw.merge(valid_laps, on=["driver_acronym", "lap"], how="inner")
    weighted_points = _time_weighted_points(valid_points)
    axis = _aggregate_axis(weighted_points, ["driver_acronym", "lap"])
    # 补回车手与车队身份，避免只按 key 读取时丢失审计字段。
    identity = weighted_points[["driver_acronym", "lap", "team"]].drop_duplicates()
    axis = axis.drop(columns=["team"], errors="ignore").merge(identity, on=["driver_acronym", "lap"], how="left")
    global_axis = _global_axis(axis)
    corners = detect_distance_corner_proxies(global_axis)
    straights = build_straight_proxies(corners)
    laps = _lap_metrics(weighted_points)
    drivers = sorted(str(driver) for driver in weighted_points["driver_acronym"].dropna().unique())
    profiles = []
    for driver in drivers:
        profile = _driver_scalar_profile(driver, weighted_points, axis, laps[laps["driver_acronym"] == driver])
        profile["corner_features"] = _corner_feature_rows(driver, weighted_points, axis, corners)
        profile["straight_features"] = _straight_feature_rows(driver, axis, straights)
        profiles.append(profile)
    teams = _aggregate_profile_rows(profiles, "team")
    teammate_baselines = _teammate_baselines(profiles)
    per_driver_ledger = []
    for driver in drivers:
        point_rows = weighted_points[weighted_points["driver_acronym"] == driver]
        driver_lap_rows = laps[laps["driver_acronym"] == driver]
        per_driver_ledger.append(
            {
                "driver": driver,
                "team": profiles[drivers.index(driver)].get("team"),
                "raw_points": int(len(raw[raw["driver_acronym"] == driver])),
                "valid_laps": int(driver_lap_rows["lap"].nunique()),
                "valid_points": int(len(point_rows)),
                "axis_rows": int(len(axis[axis["driver_acronym"] == driver])),
                "compounds": sorted(str(value) for value in point_rows["compound"].dropna().unique()),
            }
        )
    exclusion_ledger = {
        "raw_points": int(len(raw)),
        "valid_laps": int(len(valid_laps)),
        "valid_points": int(len(weighted_points)),
        "axis_rows": int(len(axis)),
        "raw_drivers": int(raw["driver_acronym"].nunique()),
        "valid_drivers": int(weighted_points["driver_acronym"].nunique()),
        "removed_first_or_stint_boundary_laps": int((~lap_table["pace_eligible"] & lap_table["first_lap"]).sum() + (~lap_table["pace_eligible"] & lap_table["pit_boundary_lap"] & ~lap_table["first_lap"]).sum()),
        "non_green_or_yellow_laps": int((~lap_table["green_or_yellow"]).sum()),
        "per_driver": per_driver_ledger,
    }
    validation = {
        "status": "PASS",
        "real_source_only": True,
        "synthetic_points": 0,
        "raw_points": int(len(raw)),
        "valid_points": int(len(weighted_points)),
        "axis_bins": AXIS_BINS,
        "corner_proxy_count": len(corners),
        "straight_proxy_count": len(straights),
        "all_driver_profiles_have_control_features": all(bool(profile.get("control_usage")) for profile in profiles),
        "same_team_baseline_pairs": len(teammate_baselines),
        "distance_axis_resampling": "median_per_0.005_rel_distance_bin_with_time_weighted_control_shares",
        "direct_physical_claims_allowed": False,
    }
    return {
        "schema_version": "reference-analysis-lab-fdataanalysis-v1",
        "target_id": "fdataanalysis",
        "status": "PASS",
        "run_id": source_identity.get("run_id"),
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_card": {
            "creator": "Formula Data Analysis",
            "source_posts": [
                {
                    "url": "https://x.com/FDataAnalysis/status/2081706503862378548",
                    "name": "RACE PACE #HungarianGP",
                    "public_semantics": ["pit-equated race pace", "smoothed lap-by-lap race pace", "box/point distribution"],
                },
                {
                    "url": "https://x.com/FDataAnalysis/status/2081027167987634407",
                    "name": "LONG RUNS DATA (FP2 #HungarianGP)",
                    "public_semantics": ["tyre age", "lap time change", "long-run distribution"],
                },
            ],
            "public_method": [
                "公开图表展示 lap time 的分布/曲线、胎龄轴、车手/车队比较和样本圈数。",
                "公开图表语义覆盖速度、油门、制动、挡位/赛道位置等驾驶特征方向。",
            ],
            "visual_inference": [
                "曲线/小提琴/箱线图对逐圈遥测或选圈后的统计量做了视觉聚合。",
                "弯道入口/最低/出口与直道通常依赖统一赛道距离轴，但公开页没有冻结 anchor 文件。",
            ],
            "pilot_method": [
                "从真实逐点遥测过滤首圈、Stint 边界和非绿/黄旗圈，使用 0.005 rel_distance 轴。",
                "每个 driver×lap×axis bin 取速度/挡位中位数，控制开关使用采样时间权重。",
                "弯道是全场中位速度平滑局部极小值 proxy；直道是相邻 proxy 弯道之间的距离窗。",
                "同队双车以同队观察差值报告，作为 delivery baseline，不解释成车手因果贡献。",
            ],
            "not_identifiable": [
                "真实下压力、引擎功率、能量/SOC、动力模式、真实制动压力和物理轮胎磨损。",
                "创作者精确选圈、平滑核、弯道/直道 anchor、pit-equation 代码和车辆模型。",
                "Setup、损伤、车队指令、燃油负载和策略意图。",
            ],
            "reference_boundary": "只重绘公开语义；不复制 X 原图、logo、品牌字体或人物照片作为产品资产。",
        },
        "data_gap_audit": [
            {
                "field": "speed_throttle_brake_gear_drs",
                "current_field": "telemetry.speed/throttle/brake/gear/drs",
                "status": "AVAILABLE",
                "substitute": "distance-axis median + time-weighted control shares",
                "publication": "允许发布观测特征",
            },
            {
                "field": "corner_entry_min_exit_and_brake_position",
                "current_field": "rel_distance + speed + brake",
                "status": "AVAILABLE_PROXY",
                "substitute": "speed local-minimum corner proxies and distance windows",
                "publication": "允许发布 proxy，必须标明 anchor 非官方",
            },
            {
                "field": "straight_speed_and_full_throttle",
                "current_field": "rel_distance + speed + throttle + drs",
                "status": "AVAILABLE_PROXY",
                "substitute": "between-proxy-corner distance windows",
                "publication": "允许发布观测特征",
            },
            {
                "field": "tyre_age_effect",
                "current_field": "tyre_life + lap_duration + speed",
                "status": "AVAILABLE_PROXY",
                "substitute": "lap-time/speed slope per tyre-age lap",
                "publication": "允许发布条件变化，不称物理磨损",
            },
            {
                "field": "official_corner_anchor_and_vehicle_physics",
                "current_field": "NOT_AVAILABLE_IN_FREEZE",
                "status": "NOT_IDENTIFIABLE",
                "substitute": "named distance-axis proxy only",
                "publication": "禁止命名真实下压力/功率/物理轮胎状态",
            },
        ],
        "visual_replication": {
            "axis_contract": {
                "bins": AXIS_BINS,
                "rel_distance_range": [0.0, 1.0],
                "resampling": "median_per_driver_lap_distance_bin",
                "control_weight": "sample_interval_seconds",
                "scope": "2025 Abu Dhabi Race valid Stints",
            },
            "track_segments": {"corner_proxies": corners, "straight_proxies": straights},
            "driver_profiles": profiles,
            "team_profiles": teams,
            "teammate_baselines": teammate_baselines,
            "ranking_note": "视觉排序仅表示观测到的速度/控制特征代理，不形成车辆物理强弱或车手因果全序。",
        },
        "audited_analysis": {
            "status": "audit_only",
            "reason": "距离轴和同队双车基线可解释观测差异，但真实车辆状态、燃油、设定、损伤和动力模式不可识别。",
            "direct_ranking_allowed": False,
            "vehicle_driver_claims": "not_identifiable",
            "allowed_claims": [
                "观测速度分布、油门/制动/DRS/挡位使用和胎龄相关变化。",
                "同队双车在相同可观测距离轴上的条件差异代理。",
            ],
            "forbidden_claims": ["真实下压力", "引擎功率", "物理轮胎磨损", "无条件车辆强弱"],
        },
        "exclusion_ledger": exclusion_ledger,
        "validation": validation,
    }


__all__ = ["AXIS_BINS", "build_fdataanalysis_pilot", "detect_distance_corner_proxies", "linear_slope"]
