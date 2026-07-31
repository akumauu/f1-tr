"""F1TelemetryData 赛后/排位图表包的方法等价复刻。

本模块把可直接从公开图读出的统计语义与仓库审计语义分开：

* ``visual_replication`` 使用作者图中可见的全场最快圈、最高速、圈均值、
  Stint 和 pit-lane 均值等定义；
* ``audited_analysis`` 保留外部补充源身份、圈级排除账本、采样口径差异和
  不可识别字段，不把观察图改名为车辆物理或因果排序。

OpenF1 只补充当前冻结集没有保留的 sector timing、Q 阶段、speed trap、
pit-lane/stop duration 和 tyre-set 起始年龄。逐点曲线仍以仓库冻结遥测为主。
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .f1pace_pilot import build_lap_table, build_pace_stats


TARGET_ID = "f1telemetrydata-reverse-engineered-v1"
TARGET_STATUS = "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"


# 下列数字由同场 Telegram 参考图人工转录，只用于外部数值验收，不进入拟合目标。
REFERENCE_Q_GAP_S = {
    "VER": 0.000,
    "NOR": 0.201,
    "PIA": 0.230,
    "RUS": 0.438,
    "LEC": 0.523,
    "ALO": 0.654,
    "BOR": 0.667,
    "ANT": 0.687,
    "OCO": 0.706,
    "HAD": 0.790,
    "TSU": 0.827,
    "BEA": 0.834,
    "SAI": 0.835,
    "LAW": 0.870,
    "STR": 0.890,
    "HAM": 1.187,
    "ALB": 1.209,
    "HUL": 1.243,
    "GAS": 1.261,
    "COL": 1.683,
}

REFERENCE_Q_TOP_SPEED_KPH = {
    "TSU": 333,
    "RUS": 331,
    "VER": 331,
    "ANT": 331,
    "HUL": 331,
    "ALO": 330,
    "STR": 330,
    "NOR": 329,
    "BOR": 329,
    "LAW": 329,
    "HAD": 329,
    "GAS": 329,
    "COL": 329,
    "PIA": 328,
    "LEC": 328,
    "SAI": 328,
    "OCO": 327,
    "BEA": 327,
    "ALB": 327,
    "HAM": 326,
}

REFERENCE_Q_TOP3_SECTORS_S = {
    "VER": (16.858, 35.710, 29.639),
    "NOR": (16.908, 35.890, 29.610),
    "PIA": (16.919, 35.930, 29.588),
}

REFERENCE_LAP_SECTIONS_PCT = {
    "VER": (13.8, 3.5, 22.0, 60.7),
    "NOR": (13.5, 4.6, 21.2, 60.7),
    "PIA": (13.6, 2.0, 24.5, 59.9),
    "RUS": (14.6, 2.4, 23.1, 60.0),
    "LEC": (15.5, 0.5, 27.2, 56.9),
    "ALO": (15.5, 2.9, 20.2, 61.4),
}

REFERENCE_TEAM_MAX_THROTTLE_PCT = {
    "Racing Bulls": 67.8,
    "Kick Sauber": 67.5,
    "Red Bull Racing": 67.2,
    "Aston Martin": 67.0,
    "McLaren": 66.2,
    "Mercedes": 65.8,
    "Alpine": 65.3,
    "Haas F1 Team": 64.5,
    "Ferrari": 64.3,
    "Williams": 62.5,
}

REFERENCE_RACE_MEAN_GAP_S = {
    "NOR": 0.000,
    "LEC": 0.109,
    "VER": 0.160,
    "PIA": 0.352,
    "HAM": 0.948,
    "RUS": 0.952,
    "HUL": 1.053,
    "ALB": 1.160,
    "GAS": 1.241,
    "ALO": 1.276,
    "OCO": 1.286,
    "STR": 1.344,
    "COL": 1.367,
    "BEA": 1.375,
    "TSU": 1.458,
    "ANT": 1.471,
    "SAI": 1.475,
    "LAW": 1.525,
    "BOR": 1.534,
    "HAD": 1.684,
}

REFERENCE_RACE_FASTEST_LAP_S = {
    "LEC": 86.725,
    "PIA": 86.765,
    "NOR": 86.818,
    "HUL": 87.050,
    "VER": 87.625,
    "STR": 87.626,
    "COL": 87.710,
    "GAS": 87.767,
    "ANT": 88.029,
    "HAM": 88.094,
    "ALO": 88.279,
    "ALB": 88.367,
    "TSU": 88.557,
    "RUS": 88.599,
    "OCO": 88.655,
    "LAW": 88.886,
    "BEA": 88.976,
    "SAI": 89.052,
    "BOR": 89.114,
    "HAD": 89.375,
}

REFERENCE_PIT_TEAM_MEAN_S = {
    "McLaren": 21.42,
    "Ferrari": 21.46,
    "Williams": 21.63,
    "Kick Sauber": 21.69,
    "Haas F1 Team": 22.24,
    "Alpine": 22.41,
    "Mercedes": 22.43,
    "Aston Martin": 22.46,
    "Red Bull Racing": 24.31,
    "Racing Bulls": 25.14,
}

REFERENCE_TOP5_STINTS = {
    "VER": (("MEDIUM", 1, 23, True), ("HARD", 24, 58, True)),
    "PIA": (("HARD", 1, 41, True), ("MEDIUM", 42, 58, True)),
    "NOR": (
        ("MEDIUM", 1, 16, True),
        ("HARD", 17, 40, True),
        ("HARD", 41, 58, True),
    ),
    "LEC": (
        ("MEDIUM", 1, 16, True),
        ("HARD", 17, 39, True),
        ("MEDIUM", 40, 58, False),
    ),
    "RUS": (("MEDIUM", 1, 14, True), ("HARD", 15, 58, True)),
}


def _finite(value: Any) -> bool:
    return value is not None and not pd.isna(value) and np.isfinite(float(value))


def _number(value: Any, digits: int = 6) -> float | None:
    if not _finite(value):
        return None
    return round(float(value), digits)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if value is None or (
                isinstance(value, (float, np.floating)) and math.isnan(float(value))
            ):
                clean[key] = None
            elif isinstance(value, np.generic):
                clean[key] = value.item()
            elif isinstance(value, pd.Timestamp):
                clean[key] = value.isoformat()
            else:
                clean[key] = value
        result.append(clean)
    return result


def _mean_abs_error(
    actual: Mapping[str, float | int | None],
    reference: Mapping[str, float | int],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for key, expected in reference.items():
        observed = actual.get(key)
        error = (
            float(observed) - float(expected)
            if observed is not None and _finite(observed)
            else None
        )
        rows.append(
            {
                "key": key,
                "actual": _number(observed),
                "reference": _number(expected),
                "error": _number(error),
                "absolute_error": _number(abs(error)) if error is not None else None,
            }
        )
    errors = [float(row["absolute_error"]) for row in rows if row["absolute_error"] is not None]
    return {
        "rows": rows,
        "coverage": len(errors),
        "expected": len(reference),
        "mae": _number(np.mean(errors)) if errors else None,
        "max_abs_error": _number(np.max(errors)) if errors else None,
    }


def build_phase_windows(race_control: pd.DataFrame) -> list[dict[str, Any]]:
    """由冻结 race-control 的开绿灯/方格旗构造 Q1/Q2/Q3 时间窗。"""

    required = {"date", "message", "qualifying_phase"}
    missing = required.difference(race_control.columns)
    if missing:
        raise ValueError(f"race_control 缺少字段: {sorted(missing)}")
    table = race_control.copy()
    table["date"] = pd.to_datetime(table["date"], utc=True, format="mixed")
    rows: list[dict[str, Any]] = []
    for phase in (1, 2, 3):
        group = table.loc[
            pd.to_numeric(table["qualifying_phase"], errors="coerce").eq(phase)
        ]
        starts = group.loc[
            group["message"].astype(str).str.contains(
                "GREEN LIGHT - PIT EXIT OPEN", regex=False
            ),
            "date",
        ]
        ends = group.loc[
            group["message"].astype(str).str.contains("CHEQUERED FLAG", regex=False),
            "date",
        ]
        if starts.empty or ends.empty:
            raise ValueError(f"Q{phase} 缺少冻结的起止 race-control 事件")
        rows.append(
            {
                "phase": f"Q{phase}",
                "start": starts.min(),
                "end": ends.max(),
                "source": "OpenF1 race_control qualifying_phase",
            }
        )
    return rows


def assign_qualifying_phase(
    date_start: Any,
    lap_duration_s: Any,
    windows: Sequence[Mapping[str, Any]],
) -> str | None:
    """按圈起点分配阶段；允许圈在方格旗前起步、在方格旗后完成。"""

    if date_start is None or pd.isna(date_start):
        return None
    start = pd.Timestamp(date_start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    duration = float(lap_duration_s) if _finite(lap_duration_s) else 0.0
    end = start + timedelta(seconds=max(duration, 0.0))
    for window in windows:
        left = pd.Timestamp(window["start"])
        right = pd.Timestamp(window["end"])
        if left <= start <= right or (start < left and end >= left):
            return str(window["phase"])
    return None


def control_section_shares(
    points: pd.DataFrame,
    *,
    weight_mode: str = "sample",
    full_throttle_threshold: float = 99.0,
    lift_threshold: float = 0.0,
) -> dict[str, float]:
    """将逐点控制信号互斥分为 braking/lift/partial/full。

    公开图没有披露阈值。视觉层使用约 3.7Hz 原始 car-channel、样本等权、
    ``brake>0``、``throttle<=0`` 与 ``throttle>=99``；审计层可改用时间权重。
    """

    required = {"throttle", "brake"}
    missing = required.difference(points.columns)
    if missing:
        raise ValueError(f"控制分段缺少字段: {sorted(missing)}")
    ordered = points.sort_values("date", kind="stable").copy()
    throttle = pd.to_numeric(ordered["throttle"], errors="coerce").to_numpy(float)
    brake = pd.to_numeric(ordered["brake"], errors="coerce").fillna(0).to_numpy(float) > 0
    valid = np.isfinite(throttle)
    if weight_mode == "sample":
        weights = np.ones(len(ordered), dtype=float)
    elif weight_mode == "time":
        if "date" not in ordered:
            raise ValueError("时间加权需要 date")
        dates = pd.to_datetime(
            ordered["date"], utc=True, format="mixed"
        ).astype("int64").to_numpy(dtype=float) / 1e9
        weights = np.diff(dates, append=np.nan)
        positive = weights[np.isfinite(weights) & (weights > 0)]
        fallback = float(np.median(positive)) if len(positive) else 0.0
        weights[-1] = fallback
        weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    else:
        raise ValueError("weight_mode 必须是 sample 或 time")
    weights = np.where(valid, weights, 0.0)
    denominator = float(weights.sum())
    if denominator <= 0:
        return {
            "braking_pct": 0.0,
            "lift_pct": 0.0,
            "partial_throttle_pct": 0.0,
            "full_throttle_pct": 0.0,
        }
    masks = {
        "braking_pct": brake,
        "lift_pct": (~brake) & (throttle <= lift_threshold),
        "partial_throttle_pct": (
            (~brake)
            & (throttle > lift_threshold)
            & (throttle < full_throttle_threshold)
        ),
        "full_throttle_pct": (~brake) & (throttle >= full_throttle_threshold),
    }
    return {
        key: float(100.0 * weights[mask].sum() / denominator)
        for key, mask in masks.items()
    }


def interpolate_lap_trace(
    points: pd.DataFrame,
    *,
    intervals: int = 500,
) -> dict[str, Any]:
    """将一圈插值到统一赛道相对距离轴。"""

    required = {"rel_distance", "time", "speed", "throttle", "x", "y"}
    missing = required.difference(points.columns)
    if missing:
        raise ValueError(f"圈轨迹缺少字段: {sorted(missing)}")
    frame = points.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["rel_distance", "time", "speed"])
    frame = (
        frame.sort_values(["rel_distance", "time"], kind="stable")
        .drop_duplicates("rel_distance", keep="last")
    )
    if len(frame) < 10:
        raise ValueError("圈轨迹有效点不足 10")
    source_axis = frame["rel_distance"].to_numpy(float)
    source_axis = (source_axis - source_axis.min()) / max(
        source_axis.max() - source_axis.min(), 1e-9
    )
    axis = np.linspace(0.0, 1.0, intervals + 1)
    result: dict[str, Any] = {
        "axis": [_number(value, 6) for value in axis],
        "sample_count": int(len(frame)),
    }
    for column in ("time", "speed", "throttle", "x", "y", "distance"):
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
        valid = np.isfinite(values)
        if valid.sum() < 2:
            continue
        interpolated = np.interp(axis, source_axis[valid], values[valid])
        if column == "time":
            interpolated = interpolated - interpolated[0]
        result[column] = [_number(value, 4) for value in interpolated]
    return result


def build_track_dominance(
    traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """按统一距离处的累计用时最小者生成描述性 track dominance。"""

    if len(traces) < 2:
        raise ValueError("track dominance 至少需要两圈")
    axis = np.asarray(traces[0]["axis"], dtype=float)
    matrix = []
    drivers = []
    for trace in traces:
        candidate_axis = np.asarray(trace["axis"], dtype=float)
        if not np.allclose(axis, candidate_axis):
            raise ValueError("track dominance 的距离轴不一致")
        drivers.append(str(trace["driver"]))
        matrix.append(np.asarray(trace["time"], dtype=float))
    winner_index = np.argmin(np.vstack(matrix), axis=0)
    winners = [drivers[int(index)] for index in winner_index]
    segments: list[dict[str, Any]] = []
    start = 0
    for index in range(1, len(winners) + 1):
        if index == len(winners) or winners[index] != winners[start]:
            segments.append(
                {
                    "driver": winners[start],
                    "start_fraction": _number(axis[start], 6),
                    "end_fraction": _number(axis[index - 1], 6),
                    "distance_share": _number(
                        axis[index - 1] - axis[start] + 1.0 / (len(axis) - 1),
                        6,
                    ),
                }
            )
            start = index
    shares = {
        driver: _number(np.mean(np.asarray(winners) == driver), 6)
        for driver in drivers
    }
    return {
        "definition": "minimum_cumulative_elapsed_time_at_common_distance",
        "axis": [_number(value, 6) for value in axis],
        "winners": winners,
        "segments": segments,
        "driver_distance_shares": shares,
        "causal_vehicle_dominance_allowed": False,
    }


def _trim_openf1_car_data_to_lap(
    car_data: pd.DataFrame,
    best_laps: pd.DataFrame,
) -> pd.DataFrame:
    table = car_data.copy()
    table["date"] = pd.to_datetime(table["date"], utc=True, format="mixed")
    lookup = best_laps.set_index("driver_number")[["date_start", "lap_duration"]]
    keep = np.zeros(len(table), dtype=bool)
    for driver_number, index in table.groupby("driver_number").groups.items():
        if driver_number not in lookup.index:
            continue
        row = lookup.loc[driver_number]
        start = pd.Timestamp(row["date_start"])
        end = start + timedelta(seconds=float(row["lap_duration"]))
        dates = table.loc[index, "date"]
        keep[np.asarray(index, dtype=int)] = dates.between(start, end).to_numpy()
    return table.loc[keep].reset_index(drop=True)


def _phase_labelled_qualifying_laps(
    open_laps: pd.DataFrame,
    windows: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    table = open_laps.copy()
    table["qualifying_phase"] = [
        assign_qualifying_phase(date, duration, windows)
        for date, duration in zip(table["date_start"], table["lap_duration"])
    ]
    return table


def build_qualifying_package(
    *,
    qualifying_points: pd.DataFrame,
    qualifying_freeze: pd.DataFrame,
    openf1_laps: pd.DataFrame,
    openf1_drivers: pd.DataFrame,
    openf1_results: pd.DataFrame,
    openf1_race_control: pd.DataFrame,
    openf1_car_data: pd.DataFrame,
) -> dict[str, Any]:
    """构建 7 张排位图及外部参考差异。"""

    driver_map = (
        openf1_drivers.drop_duplicates("driver_number")
        .set_index("driver_number")[["name_acronym", "team_name"]]
    )
    laps = openf1_laps.copy()
    laps["driver"] = laps["driver_number"].map(driver_map["name_acronym"])
    laps["team"] = laps["driver_number"].map(driver_map["team_name"])
    windows = build_phase_windows(openf1_race_control)
    laps = _phase_labelled_qualifying_laps(laps, windows)

    valid = laps.loc[laps["lap_duration"].notna()].copy()
    best = (
        valid.sort_values(["lap_duration", "lap_number"], kind="stable")
        .groupby("driver", as_index=False, sort=True)
        .first()
        .sort_values(["lap_duration", "driver"], kind="stable")
        .reset_index(drop=True)
    )
    fastest = float(best["lap_duration"].min())
    best["gap_to_fastest_s"] = best["lap_duration"].astype(float) - fastest
    best["rank"] = np.arange(1, len(best) + 1)

    # 当前冻结 qualifying CSV 只含准确推圈；本 pilot 用外部时间窗显式补阶段。
    qfreeze = qualifying_freeze.copy()
    qfreeze["driver_number"] = pd.to_numeric(
        qfreeze["driver_number"], errors="coerce"
    ).astype("Int64")
    qfreeze["lap_number"] = pd.to_numeric(
        qfreeze["lap_number"], errors="coerce"
    ).astype("Int64")
    compound_lookup = (
        qfreeze.dropna(subset=["driver_number", "lap_number"])
        .drop_duplicates(["driver_number", "lap_number"])
        .set_index(["driver_number", "lap_number"])["compound"]
        .to_dict()
    )
    best["compound"] = [
        compound_lookup.get((int(driver_number), int(lap_number)))
        for driver_number, lap_number in zip(
            best["driver_number"], best["lap_number"]
        )
    ]
    qfreeze = qfreeze.merge(
        laps[
            [
                "driver_number",
                "lap_number",
                "qualifying_phase",
                "date_start",
            ]
        ],
        on=["driver_number", "lap_number"],
        how="left",
        validate="many_to_one",
    )
    phase_counts = {
        str(key): int(value)
        for key, value in qfreeze["qualifying_phase"]
        .fillna("NOT_IDENTIFIED")
        .value_counts()
        .sort_index()
        .items()
    }

    fastest_keys = {
        (str(row.driver), int(row.lap_number))
        for row in best[["driver", "lap_number"]].itertuples(index=False)
    }
    qpoints = qualifying_points.loc[
        [
            (str(driver), int(lap)) in fastest_keys
            for driver, lap in zip(
                qualifying_points["driver_acronym"],
                qualifying_points["lap"],
            )
        ]
    ].copy()
    local_top_speed = (
        qpoints.groupby("driver_acronym")["speed"].max().astype(float).to_dict()
    )

    timing_rows: list[dict[str, Any]] = []
    for row in best.itertuples(index=False):
        timing_rows.append(
            {
                "rank": int(row.rank),
                "driver": str(row.driver),
                "team": str(row.team),
                "driver_number": int(row.driver_number),
                "lap": int(row.lap_number),
                "phase": row.qualifying_phase,
                "lap_time_s": _number(row.lap_duration, 3),
                "gap_to_fastest_s": _number(row.gap_to_fastest_s, 3),
                "sector_1_s": _number(row.duration_sector_1, 3),
                "sector_2_s": _number(row.duration_sector_2, 3),
                "sector_3_s": _number(row.duration_sector_3, 3),
                "i1_speed_kph": _number(row.i1_speed, 0),
                "i2_speed_kph": _number(row.i2_speed, 0),
                "speed_trap_kph": _number(row.st_speed, 0),
                "fastest_lap_max_speed_kph": _number(
                    local_top_speed.get(str(row.driver)), 0
                ),
                "compound": row.compound,
                "is_pit_out_lap": bool(row.is_pit_out_lap),
                "role": "whole_session_fastest_accurate_push_lap_with_external_phase",
            }
        )

    top_three = [str(value) for value in best.head(3)["driver"]]
    traces: list[dict[str, Any]] = []
    for driver in top_three:
        row = best.loc[best["driver"].eq(driver)].iloc[0]
        group = qpoints.loc[
            qpoints["driver_acronym"].eq(driver)
            & qpoints["lap"].eq(int(row["lap_number"]))
        ]
        trace = interpolate_lap_trace(group, intervals=500)
        trace.update(
            {
                "driver": driver,
                "team": str(row["team"]),
                "lap": int(row["lap_number"]),
                "lap_time_s": _number(row["lap_duration"], 3),
                "phase": row["qualifying_phase"],
            }
        )
        traces.append(trace)

    dominance = build_track_dominance(traces)
    # 赛道绘制沿最快圈的 x/y，颜色由共同距离上的累计用时赢家决定。
    map_trace = traces[0]
    dominance["track_x"] = map_trace.get("x", [])
    dominance["track_y"] = map_trace.get("y", [])

    sector_rows = []
    for sector_index, column in enumerate(
        ("duration_sector_1", "duration_sector_2", "duration_sector_3"),
        start=1,
    ):
        row = best.loc[best[column].notna()].sort_values(column).iloc[0]
        sector_rows.append(
            {
                "sector": sector_index,
                "driver": str(row["driver"]),
                "time_s": _number(row[column], 3),
                "source": "OpenF1_laps_frozen",
            }
        )

    # 只估计参考圈上的 sector 距离位置；不是官方几何锚点。
    ver_row = best.loc[best["driver"].eq(top_three[0])].iloc[0]
    ver_trace = next(trace for trace in traces if trace["driver"] == top_three[0])
    trace_time = np.asarray(ver_trace["time"], dtype=float)
    trace_axis = np.asarray(ver_trace["axis"], dtype=float)
    sector_1_end_time = float(ver_row["duration_sector_1"])
    sector_2_end_time = sector_1_end_time + float(ver_row["duration_sector_2"])
    sector_anchor_proxy = [
        {
            "anchor": "S1_END",
            "lap_fraction": _number(
                np.interp(sector_1_end_time, trace_time, trace_axis), 6
            ),
            "status": "ESTIMATED_FROM_OFFICIAL_TIME_AND_LOCAL_TRACE",
        },
        {
            "anchor": "S2_END",
            "lap_fraction": _number(
                np.interp(sector_2_end_time, trace_time, trace_axis), 6
            ),
            "status": "ESTIMATED_FROM_OFFICIAL_TIME_AND_LOCAL_TRACE",
        },
    ]

    car_data = _trim_openf1_car_data_to_lap(openf1_car_data, best)
    control_rows: list[dict[str, Any]] = []
    for driver, group in car_data.groupby("_f1tr_driver_acronym", sort=True):
        visual = control_section_shares(group, weight_mode="sample")
        audited = control_section_shares(group, weight_mode="time")
        team = str(
            driver_map.loc[
                int(group["driver_number"].iloc[0]), "team_name"
            ]
        )
        control_rows.append(
            {
                "driver": str(driver),
                "team": team,
                "raw_samples": int(len(group)),
                **{key: _number(value) for key, value in visual.items()},
                "audited_time_weighted": {
                    key: _number(value) for key, value in audited.items()
                },
            }
        )
    control_rows.sort(
        key=lambda row: (-float(row["full_throttle_pct"]), row["driver"])
    )
    team_throttle_rows = []
    control_frame = pd.DataFrame(control_rows)
    for team, group in control_frame.groupby("team", sort=True):
        team_throttle_rows.append(
            {
                "team": str(team),
                "drivers": sorted(group["driver"].astype(str).tolist()),
                "driver_count": int(len(group)),
                "max_throttle_sample_mean_pct": _number(
                    group["full_throttle_pct"].astype(float).mean()
                ),
            }
        )
    team_throttle_rows.sort(
        key=lambda row: (
            -float(row["max_throttle_sample_mean_pct"]),
            row["team"],
        )
    )

    q_gap_validation = _mean_abs_error(
        {
            str(row["driver"]): float(row["gap_to_fastest_s"])
            for row in timing_rows
        },
        REFERENCE_Q_GAP_S,
    )
    q_speed_validation = _mean_abs_error(
        {
            str(row["driver"]): float(row["fastest_lap_max_speed_kph"])
            for row in timing_rows
        },
        REFERENCE_Q_TOP_SPEED_KPH,
    )
    sector_actual = {
        str(row["driver"]): (
            float(row["sector_1_s"]),
            float(row["sector_2_s"]),
            float(row["sector_3_s"]),
        )
        for row in timing_rows
        if str(row["driver"]) in REFERENCE_Q_TOP3_SECTORS_S
    }
    sector_errors = []
    for driver, reference in REFERENCE_Q_TOP3_SECTORS_S.items():
        actual = sector_actual[driver]
        for index, expected in enumerate(reference):
            sector_errors.append(
                {
                    "driver": driver,
                    "sector": index + 1,
                    "actual_s": _number(actual[index], 3),
                    "reference_s": _number(expected, 3),
                    "error_s": _number(actual[index] - expected, 6),
                }
            )
    control_errors: list[float] = []
    control_comparison: list[dict[str, Any]] = []
    control_by_driver = {row["driver"]: row for row in control_rows}
    keys = (
        "braking_pct",
        "lift_pct",
        "partial_throttle_pct",
        "full_throttle_pct",
    )
    for driver, reference in REFERENCE_LAP_SECTIONS_PCT.items():
        actual = control_by_driver[driver]
        row = {"driver": driver}
        for key, expected in zip(keys, reference):
            observed = float(actual[key])
            error = observed - expected
            control_errors.append(abs(error))
            row[key] = {
                "actual": _number(observed),
                "reference": expected,
                "error": _number(error),
            }
        control_comparison.append(row)
    team_throttle_validation = _mean_abs_error(
        {
            str(row["team"]): float(row["max_throttle_sample_mean_pct"])
            for row in team_throttle_rows
        },
        REFERENCE_TEAM_MAX_THROTTLE_PCT,
    )
    return {
        "identity": {
            "session": "Qualifying",
            "primary_lap_definition": "whole_session_fastest_accurate_push_lap",
            "phase_source": "frozen_OpenF1_race_control_and_session_result",
            "phase_windows": [
                {
                    "phase": row["phase"],
                    "start": pd.Timestamp(row["start"]).isoformat(),
                    "end": pd.Timestamp(row["end"]).isoformat(),
                }
                for row in windows
            ],
            "phase_coverage": {
                "frozen_push_laps": int(len(qfreeze)),
                "identified": int(qfreeze["qualifying_phase"].notna().sum()),
                "counts": phase_counts,
                "status": (
                    "PASS"
                    if qfreeze["qualifying_phase"].notna().all()
                    else "PARTIAL"
                ),
            },
        },
        "lap_compare": {
            "drivers": top_three,
            "traces": traces,
            "fastest_sectors": sector_rows,
            "sector_anchor_proxy": sector_anchor_proxy,
            "official_geometric_sector_anchor_status": (
                "NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR"
            ),
        },
        "lap_delta": timing_rows,
        "track_dominance": dominance,
        "top_speeds": sorted(
            [
                {
                    "driver": row["driver"],
                    "team": row["team"],
                    "top_speed_kph": row["fastest_lap_max_speed_kph"],
                }
                for row in timing_rows
            ],
            key=lambda row: (-float(row["top_speed_kph"]), row["driver"]),
        ),
        "throttle_usage": team_throttle_rows,
        "lap_sections": control_rows,
        "timings": timing_rows,
        "validation": {
            "lap_delta": q_gap_validation,
            "top_speed": q_speed_validation,
            "top3_sector_rows": sector_errors,
            "top3_sector_mae_s": _number(
                np.mean([abs(row["error_s"]) for row in sector_errors])
            ),
            "top3_sector_max_abs_error_s": _number(
                np.max([abs(row["error_s"]) for row in sector_errors])
            ),
            "lap_sections": {
                "rows": control_comparison,
                "mae_pp": _number(np.mean(control_errors)),
                "max_abs_error_pp": _number(np.max(control_errors)),
                "interpretation": (
                    "精确控制分类阈值未公开；约 3.7Hz 冻结 car-channel 的"
                    "可解释互斥规则作为方法等价实现，不对参考值做逐车手调参。"
                ),
            },
            "team_throttle": team_throttle_validation,
            "car_channel_samples": int(len(car_data)),
            "car_channel_driver_coverage": int(
                car_data["_f1tr_driver_acronym"].nunique()
            ),
        },
    }


def _summarize_local_strategies(lap_table: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (driver, stint), group in lap_table.groupby(
        ["driver_acronym", "stint"], sort=True
    ):
        ordered = group.sort_values("lap", kind="stable")
        rows.append(
            {
                "driver": str(driver),
                "stint_number": int(stint),
                "compound": str(ordered["compound"].iloc[0]),
                "lap_start": int(ordered["lap"].min()),
                "lap_end": int(ordered["lap"].max()),
            }
        )
    return pd.DataFrame(rows)


def build_pit_summary(
    pit: pd.DataFrame,
    drivers: pd.DataFrame,
) -> dict[str, Any]:
    """发布 pit-lane transit 与 stationary stop 两个不同字段。"""

    mapping = (
        drivers.drop_duplicates("driver_number")
        .set_index("driver_number")[["name_acronym", "team_name"]]
    )
    table = pit.copy()
    table["driver"] = table["driver_number"].map(mapping["name_acronym"])
    table["team"] = table["driver_number"].map(mapping["team_name"])
    team_rows: list[dict[str, Any]] = []
    for team, group in table.groupby("team", sort=True):
        lane = pd.to_numeric(group["lane_duration"], errors="coerce")
        stop = pd.to_numeric(group["stop_duration"], errors="coerce")
        team_rows.append(
            {
                "team": str(team),
                "pit_lane_passes": int(lane.notna().sum()),
                "lane_duration_mean_s": _number(lane.mean(), 6),
                "lane_duration_median_s": _number(lane.median(), 6),
                "stop_duration_observed": int(stop.notna().sum()),
                "stop_duration_missing": int(stop.isna().sum()),
                "stationary_stop_mean_s": _number(stop.mean(), 6),
                "field_role": "lane_duration_not_stationary_stop",
            }
        )
    team_rows.sort(key=lambda row: float(row["lane_duration_mean_s"]))
    validation = _mean_abs_error(
        {
            str(row["team"]): float(row["lane_duration_mean_s"])
            for row in team_rows
        },
        REFERENCE_PIT_TEAM_MEAN_S,
    )
    return {
        "team_summary": team_rows,
        "stops": _records(
            table[
                [
                    "driver",
                    "team",
                    "lap_number",
                    "lane_duration",
                    "stop_duration",
                    "date",
                ]
            ].sort_values(["lap_number", "driver"])
        ),
        "coverage": {
            "lane_duration": int(table["lane_duration"].notna().sum()),
            "stationary_stop_duration": int(table["stop_duration"].notna().sum()),
            "total_pit_lane_passes": int(len(table)),
        },
        "reference_validation": validation,
    }


def build_race_package(
    *,
    race_points: pd.DataFrame,
    openf1_laps: pd.DataFrame,
    openf1_drivers: pd.DataFrame,
    openf1_pit: pd.DataFrame,
    openf1_stints: pd.DataFrame,
    f1pace_audited_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """构建 6 张正赛图与 fail-close 审计 companion。"""

    lap_table = build_lap_table(race_points)
    pace = build_pace_stats(lap_table)
    driver_map = (
        openf1_drivers.drop_duplicates("driver_number")
        .set_index("driver_number")[["name_acronym", "team_name"]]
    )
    laps = openf1_laps.copy()
    laps["driver"] = laps["driver_number"].map(driver_map["name_acronym"])
    laps["team"] = laps["driver_number"].map(driver_map["team_name"])
    fastest = (
        laps.loc[laps["lap_duration"].notna()]
        .sort_values(["lap_duration", "lap_number"], kind="stable")
        .groupby("driver", as_index=False)
        .first()
        .sort_values(["lap_duration", "driver"], kind="stable")
        .reset_index(drop=True)
    )
    fastest["rank"] = np.arange(1, len(fastest) + 1)
    race_timing_rows = [
        {
            "rank": int(row.rank),
            "driver": str(row.driver),
            "team": str(row.team),
            "lap": int(row.lap_number),
            "lap_time_s": _number(row.lap_duration, 3),
            "sector_1_s": _number(row.duration_sector_1, 3),
            "sector_2_s": _number(row.duration_sector_2, 3),
            "sector_3_s": _number(row.duration_sector_3, 3),
            "i1_speed_kph": _number(row.i1_speed, 0),
            "i2_speed_kph": _number(row.i2_speed, 0),
            "speed_trap_kph": _number(row.st_speed, 0),
        }
        for row in fastest.itertuples(index=False)
    ]

    race_gap_validation = _mean_abs_error(
        {
            str(row["driver"]): float(row["delta_to_fastest_s"])
            for row in pace["all_drivers"]
        },
        REFERENCE_RACE_MEAN_GAP_S,
    )
    race_fastest_validation = _mean_abs_error(
        {
            str(row["driver"]): float(row["lap_time_s"])
            for row in race_timing_rows
        },
        REFERENCE_RACE_FASTEST_LAP_S,
    )

    local_laps = lap_table.rename(
        columns={
            "driver_acronym": "driver",
            "lap": "lap_number",
            "lap_duration": "local_lap_duration",
        }
    )
    external = laps[
        ["driver", "lap_number", "lap_duration", "duration_sector_1",
         "duration_sector_2", "duration_sector_3"]
    ].rename(columns={"lap_duration": "openf1_lap_duration"})
    concordance = local_laps.merge(
        external,
        on=["driver", "lap_number"],
        how="inner",
        validate="one_to_one",
    )
    concordance["difference_s"] = (
        concordance["local_lap_duration"].astype(float)
        - concordance["openf1_lap_duration"].astype(float)
    )
    non_start = concordance.loc[
        concordance["lap_number"].gt(1)
        & concordance["local_lap_duration"].notna()
        & concordance["openf1_lap_duration"].notna()
    ]

    stints = openf1_stints.copy()
    stints["driver"] = stints["driver_number"].map(driver_map["name_acronym"])
    stints["team"] = stints["driver_number"].map(driver_map["team_name"])
    stints["fresh_tyre_proxy"] = (
        pd.to_numeric(stints["tyre_age_at_start"], errors="coerce")
        .fillna(1)
        .eq(0)
    )
    local_strategy = _summarize_local_strategies(lap_table)
    strategy_join = stints.merge(
        local_strategy,
        on=["driver", "stint_number"],
        how="left",
        suffixes=("_openf1", "_local"),
        validate="one_to_one",
    )
    strategy_join["boundary_match"] = (
        strategy_join["compound_openf1"].eq(strategy_join["compound_local"])
        & strategy_join["lap_start_openf1"].eq(strategy_join["lap_start_local"])
        & strategy_join["lap_end_openf1"].eq(strategy_join["lap_end_local"])
    )

    top5_reference_rows = []
    for driver, expected_stints in REFERENCE_TOP5_STINTS.items():
        observed = stints.loc[stints["driver"].eq(driver)].sort_values("stint_number")
        actual = tuple(
            (
                str(row.compound),
                int(row.lap_start),
                int(row.lap_end),
                bool(row.fresh_tyre_proxy),
            )
            for row in observed.itertuples(index=False)
        )
        top5_reference_rows.append(
            {
                "driver": driver,
                "actual": actual,
                "reference": expected_stints,
                "match": actual == expected_stints,
            }
        )

    pit = build_pit_summary(openf1_pit, openf1_drivers)
    lap_chart_drivers = ["VER", "PIA", "NOR", "LEC", "RUS"]
    pace_by_driver = {row["driver"]: row for row in pace["all_drivers"]}
    lap_chart = [
        {
            "driver": driver,
            "points": pace_by_driver[driver]["points"],
            "stints": _records(
                stints.loc[stints["driver"].eq(driver)][
                    [
                        "driver",
                        "stint_number",
                        "compound",
                        "lap_start",
                        "lap_end",
                        "tyre_age_at_start",
                        "fresh_tyre_proxy",
                    ]
                ]
            ),
            "pit_laps": [
                int(row["lap_number"])
                for row in pit["stops"]
                if row["driver"] == driver
            ],
        }
        for driver in lap_chart_drivers
    ]
    return {
        "race_pace": {
            "definition": (
                "exclude lap1, both sides of each stint transition, non-green/yellow; "
                "retain wet compounds and rank by arithmetic mean"
            ),
            "eligible_laps": pace["eligible_laps"],
            "driver_rows": pace["all_drivers"],
            "boxplot_order": [row["driver"] for row in pace["all_drivers"]],
            "audited_direct_ranking_allowed": False,
        },
        "tyre_strategies": {
            "rows": _records(
                stints[
                    [
                        "driver",
                        "team",
                        "stint_number",
                        "compound",
                        "lap_start",
                        "lap_end",
                        "tyre_age_at_start",
                        "fresh_tyre_proxy",
                    ]
                ].sort_values(["driver", "stint_number"])
            ),
            "local_boundary_concordance": {
                "rows": int(len(strategy_join)),
                "matches": int(strategy_join["boundary_match"].sum()),
                "status": (
                    "PASS"
                    if strategy_join["boundary_match"].all()
                    else "PARTIAL"
                ),
            },
            "top5_reference_transcription": top5_reference_rows,
            "physical_tyre_wear_identified": False,
        },
        "average_gap": {
            "fastest_mean_s": pace["fastest_mean_s"],
            "rows": [
                {
                    "driver": row["driver"],
                    "mean_lap_s": row["mean_lap_s"],
                    "gap_to_fastest_s": row["delta_to_fastest_s"],
                    "valid_laps": row["valid_laps"],
                }
                for row in pace["all_drivers"]
            ],
        },
        "pit_times": pit,
        "timings": race_timing_rows,
        "lap_times": {
            "drivers": lap_chart_drivers,
            "rows": lap_chart,
            "selection": "same eligible lap universe as race_pace",
        },
        "validation": {
            "average_gap": race_gap_validation,
            "fastest_laps": race_fastest_validation,
            "local_vs_openf1_lap_duration": {
                "all_joined_laps": int(len(concordance)),
                "non_start_laps": int(len(non_start)),
                "mae_s": _number(non_start["difference_s"].abs().mean()),
                "max_abs_error_s": _number(non_start["difference_s"].abs().max()),
                "within_1ms_share": _number(
                    non_start["difference_s"].abs().le(0.0011).mean()
                ),
                "lap1_excluded_reason": (
                    "start-line timing origin differs by roughly 0.21–0.50s"
                ),
            },
            "strategy_boundaries": {
                "openf1_rows": int(len(strategy_join)),
                "local_matches": int(strategy_join["boundary_match"].sum()),
                "top5_reference_matches": int(
                    sum(row["match"] for row in top5_reference_rows)
                ),
                "top5_reference_expected": len(top5_reference_rows),
            },
            "pit_team_means": pit["reference_validation"],
        },
        "audited_companion": {
            **dict(f1pace_audited_analysis),
            "global_race_pace_ranking_allowed": False,
            "pit_lane_vs_stationary_stop_separated": True,
            "real_fuel_soc_tyre_state_setup_damage_power_mode_identified": False,
        },
        "lap_table": lap_table,
    }


def build_data_gap_audit() -> list[dict[str, Any]]:
    return [
        {
            "chart": "Quali Lap Compare / Lap Delta",
            "required": "fastest accurate lap, speed, throttle, distance",
            "repository": "qualifying_v4 + expanded-v4 telemetry",
            "gap": "current global freeze has no Q phase",
            "supplement": "frozen OpenF1 race_control/session_result",
            "publish": "ALLOW_WITH_EXTERNAL_PHASE_IDENTITY",
        },
        {
            "chart": "Track Dominance",
            "required": "common distance axis, cumulative time, track x/y",
            "repository": "expanded-v4 telemetry",
            "gap": "official geometric sector anchor absent",
            "supplement": "sector-end distance estimated from official sector time",
            "publish": "ALLOW_OBSERVED_TIME_PROXY_OFFICIAL_ANCHOR_NOT_TESTED",
        },
        {
            "chart": "Top Speeds / Throttle Usage / Lap Sections",
            "required": "raw car-channel speed/throttle/brake",
            "repository": "expanded-v4 telemetry (~7.69Hz)",
            "gap": "creator thresholds and source feed version opaque",
            "supplement": "frozen OpenF1 fastest-lap car data (~3.7Hz)",
            "publish": "ALLOW_METHOD_EQUIVALENT_WITH_REFERENCE_ERROR",
        },
        {
            "chart": "Quali Timings / Race Timings",
            "required": "sector durations and timing-line speeds",
            "repository": "not retained in current telemetry freeze",
            "gap": "sector timing and speed traps",
            "supplement": "frozen OpenF1 laps",
            "publish": "ALLOW_FROZEN_SUPPLEMENT",
        },
        {
            "chart": "Race Pace / Average Gap / Lap Times",
            "required": "lap duration, stint boundary, status",
            "repository": "expanded-v4 telemetry + F1pace audited selection",
            "gap": "fuel, traffic causality and tyre state",
            "supplement": "none; descriptive visual plus audit_only companion",
            "publish": "ALLOW_DESCRIPTIVE_NO_CAUSAL_GLOBAL_RANK",
        },
        {
            "chart": "Tyre Strategies",
            "required": "compound, stint, lap boundaries, tyre age at start",
            "repository": "expanded-v4 + frozen OpenF1 stints",
            "gap": "physical wear and tyre-set identity",
            "supplement": "fresh/used proxy from tyre_age_at_start",
            "publish": "ALLOW_OBSERVED_STRATEGY_NOT_PHYSICAL_WEAR",
        },
        {
            "chart": "Pit Times",
            "required": "pit-lane transit and stationary stop duration",
            "repository": "not retained in current freeze",
            "gap": "one stationary stop missing in supplement",
            "supplement": "frozen OpenF1 pit lane_duration/stop_duration",
            "publish": "ALLOW_LANE_DURATION; STOP_DURATION_PARTIAL_NO_IMPUTATION",
        },
        {
            "chart": "Downforce Map",
            "required": "aerodynamic load/drag and setup",
            "repository": "not identifiable",
            "gap": "true downforce, drag, setup and power mode",
            "supplement": "none",
            "publish": "NOT_IDENTIFIABLE",
        },
    ]


def build_f1telemetrydata_reverse_engineered_v1(
    *,
    qualifying_points: pd.DataFrame,
    race_points: pd.DataFrame,
    qualifying_freeze: pd.DataFrame,
    openf1: Mapping[str, pd.DataFrame],
    f1pace_report: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> dict[str, Any]:
    qualifying = build_qualifying_package(
        qualifying_points=qualifying_points,
        qualifying_freeze=qualifying_freeze,
        openf1_laps=openf1["qualifying_laps"],
        openf1_drivers=openf1["qualifying_drivers"],
        openf1_results=openf1["qualifying_results"],
        openf1_race_control=openf1["qualifying_race_control"],
        openf1_car_data=openf1["qualifying_car_data"],
    )
    race = build_race_package(
        race_points=race_points,
        openf1_laps=openf1["race_laps"],
        openf1_drivers=openf1["race_drivers"],
        openf1_pit=openf1["race_pit"],
        openf1_stints=openf1["race_stints"],
        f1pace_audited_analysis=f1pace_report["audited_analysis"],
    )
    lap_table = race.pop("lap_table")
    exact_gates = {
        "q_gap_mae_le_0_0005s": (
            float(qualifying["validation"]["lap_delta"]["mae"]) <= 0.0005
        ),
        "q_top_speed_exact": (
            float(qualifying["validation"]["top_speed"]["max_abs_error"]) == 0.0
        ),
        "q_top3_sectors_exact": (
            float(qualifying["validation"]["top3_sector_max_abs_error_s"]) == 0.0
        ),
        "race_gap_mae_le_0_0005s": (
            float(race["validation"]["average_gap"]["mae"]) <= 0.0005
        ),
        "race_fastest_lap_exact": (
            float(race["validation"]["fastest_laps"]["max_abs_error"]) == 0.0
        ),
        "strategy_top5_exact": (
            int(race["validation"]["strategy_boundaries"]["top5_reference_matches"])
            == int(race["validation"]["strategy_boundaries"]["top5_reference_expected"])
        ),
    }
    validation_status = "PASS" if all(exact_gates.values()) else "FAIL"
    return {
        "schema_version": "f1telemetrydata-reverse-engineered-v1",
        "target_id": TARGET_ID,
        "status": TARGET_STATUS,
        "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_identity": {
            "public_semantics_confirmed": [
                "Quali Lap Compare",
                "Lap Delta",
                "Track Dominance",
                "Top Speeds",
                "Throttle Usage",
                "Lap Sections",
                "Quali Timings",
                "Race Pace",
                "Tyre Strategies",
                "Average Gap",
                "Pit Times",
                "Race Timings",
                "Lap Times",
            ],
            "exact_code_or_thresholds_public": False,
            "visual_replication": (
                "same-event reference semantics with frozen external sector/pit supplement"
            ),
            "audited_analysis": (
                "F1 TR full lap ledger and fail-close causal/non-identifiability boundary"
            ),
        },
        "data_gap_audit": build_data_gap_audit(),
        "visual_replication": {
            "qualifying": qualifying,
            "race": {
                key: value
                for key, value in race.items()
                if key != "audited_companion"
            },
        },
        "audited_analysis": {
            "status": "PASS_WITH_DECLARED_PARTIAL_FIELDS",
            "qualifying": {
                "phase_coverage": qualifying["identity"]["phase_coverage"],
                "official_geometric_sector_anchor": (
                    "NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR"
                ),
                "control_formula": (
                    "brake>0; lift=no_brake_and_throttle<=0; "
                    "full=no_brake_and_throttle>=99; partial=remainder"
                ),
                "control_reference_mae_pp": qualifying["validation"][
                    "lap_sections"
                ]["mae_pp"],
                "team_throttle_reference_mae_pp": qualifying["validation"][
                    "team_throttle"
                ]["mae"],
            },
            "race": race["audited_companion"],
            "pit": {
                "lane_duration_coverage": race["pit_times"]["coverage"][
                    "lane_duration"
                ],
                "stationary_stop_coverage": race["pit_times"]["coverage"][
                    "stationary_stop_duration"
                ],
                "missing_stationary_stops_not_imputed": True,
            },
            "direct_physical_vehicle_claims_allowed": False,
            "direct_global_race_pace_causal_order_allowed": False,
        },
        "exclusion_ledger": {
            "race_lap_universe": int(len(lap_table)),
            "race_pace_eligible": int(lap_table["pace_eligible"].sum()),
            "race_first_lap_excluded": int(lap_table["first_lap"].sum()),
            "race_pit_boundary_excluded": int(lap_table["pit_boundary_lap"].sum()),
            "race_non_green_yellow_excluded": int(
                (~lap_table["green_or_yellow"]).sum()
            ),
            "race_deleted_markers_retained": int(lap_table["deleted"].sum()),
            "qualifying_push_laps": int(
                qualifying["identity"]["phase_coverage"]["frozen_push_laps"]
            ),
            "qualifying_phase_identified": int(
                qualifying["identity"]["phase_coverage"]["identified"]
            ),
        },
        "validation": {
            "status": validation_status,
            "exact_reference_gates": exact_gates,
            "qualifying": qualifying["validation"],
            "race": race["validation"],
            "synthetic_points": 0,
            "sample_frequency_boundary": {
                "repository_expanded_v4_approx_hz": 7.692,
                "openf1_raw_car_channel_documented_approx_hz": 3.7,
                "effect": (
                    "control-state percentages differ with sampling, interpolation "
                    "and opaque thresholds"
                ),
            },
        },
        "method_boundaries": [
            "Track Dominance means observed cumulative-time lead at common distance, not physical car dominance.",
            "Race pace and average-gap orders mix fuel scenarios, compounds, tyre age, traffic and strategy.",
            "lane_duration is pit-lane transit; stop_duration is stationary time and is not complete.",
            "official sector times are frozen; geometric distance anchors remain NOT_TESTED.",
            "true fuel, SOC, tyre temperature/pressure/wear, setup, damage, power mode and team orders are not identifiable.",
        ],
    }
