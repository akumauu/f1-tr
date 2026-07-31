"""F1pace Race pace pilot 的真实数据计算函数。

本模块只实现公开页面可以核对的描述性口径。它不会把燃油、胎温、
真实磨损或策略意图当作观测字段，也不会把全场均值差包装成因果比较。
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


GREEN_YELLOW_STATUS_CODES = frozenset({"1", "2"})
TRAFFIC_THRESHOLD_SECONDS = 2.0
TRAFFIC_LAP_THRESHOLD = 1.0 / 3.0


def sha256_file(path: Path) -> str:
    """返回文件 SHA-256，供实验身份链使用。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _status_codes(value: Any) -> set[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return set()
    return {
        token
        for token in re.split(r"[^0-9]+", str(value))
        if token
    }


def is_green_or_yellow(value: Any) -> bool:
    """只接受全圈标记为绿旗或黄旗的圈。"""

    codes = _status_codes(value)
    return bool(codes) and codes.issubset(GREEN_YELLOW_STATUS_CODES)


def _first_finite(values: Iterable[Any]) -> Any:
    for value in values:
        if value is not None and not pd.isna(value):
            return value
    return None


def build_lap_table(points: pd.DataFrame) -> pd.DataFrame:
    """从逐点遥测聚合圈表，并冻结 F1pace 的圈筛选身份。

    当前 feed 的 `stint` 在进站后的出站圈发生切换，因此把切换前后两圈
    共同标成 `pit_boundary_lap`。该规则与公开图的均值逐项吻合；原始
    `deleted`/`is_accurate` 标记仍保留在账本中，不静默删除圈。
    """

    required = {
        "driver_acronym",
        "lap",
        "lap_duration",
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
        raise ValueError(f"逐点遥测缺少 F1pace 圈字段: {', '.join(missing)}")

    ordered = points.sort_values(
        ["driver_acronym", "lap", "sample_index"],
        kind="stable",
    )
    table = (
        ordered.groupby(["driver_acronym", "lap"], as_index=False, sort=True)
        .agg(
            lap_duration=("lap_duration", "median"),
            stint=("stint", "first"),
            compound=("compound", _first_finite),
            tyre_life=("tyre_life", "median"),
            position=("position", "median"),
            track_status=("track_status", _first_finite),
            deleted=("deleted", "max"),
            is_accurate=("is_accurate", "min"),
            team=("team", _first_finite),
            telemetry_samples=("sample_index", "size"),
        )
    )
    table["lap"] = pd.to_numeric(table["lap"], errors="coerce").astype(int)
    table["stint"] = pd.to_numeric(table["stint"], errors="coerce")
    table["stint"] = table["stint"].fillna(-1).astype(int)
    table = table.sort_values(["driver_acronym", "lap"], kind="stable")
    grouped = table.groupby("driver_acronym", sort=False)
    table["stint_previous"] = grouped["stint"].shift(1)
    table["stint_next"] = grouped["stint"].shift(-1)
    table["pit_boundary_lap"] = (
        (
            table["stint_previous"].notna()
            & table["stint"].ne(table["stint_previous"])
        )
        | (
            table["stint_next"].notna()
            & table["stint"].ne(table["stint_next"])
        )
    )
    table["green_or_yellow"] = table["track_status"].map(is_green_or_yellow)
    table["first_lap"] = table["lap"].eq(1)
    table["pace_eligible"] = (
        table["lap"].gt(1)
        & table["green_or_yellow"]
        & ~table["pit_boundary_lap"]
        & table["lap_duration"].notna()
    )
    stint_counts = table.groupby("driver_acronym")["stint"].transform(
        lambda values: int(values[values.ge(0)].nunique())
    )
    table["stint_count"] = stint_counts.astype(int)
    table["stop_count"] = table["stint_count"].sub(1).clip(lower=0)
    table["stint_jitter"] = (
        table["stint"].sub(table["stint_count"].add(1).div(2.0)).mul(0.22)
    )
    return table.reset_index(drop=True)


def _time_weights(group: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """用逐点时间构造左侧区间权重，并记录可观测时间覆盖。"""

    ordered = group.sort_values(["time", "sample_index"], kind="stable").copy()
    ordered["time"] = pd.to_numeric(ordered["time"], errors="coerce")
    ordered = ordered.loc[ordered["time"].notna()].reset_index(drop=True)
    if ordered.empty:
        return ordered, np.empty(0, dtype=float)
    times = ordered["time"].to_numpy(dtype=float)
    if len(times) == 1:
        weights = np.array([0.0], dtype=float)
    else:
        weights = np.diff(times, append=np.nan)
        lap_duration = float(pd.to_numeric(ordered["lap_duration"], errors="coerce").median())
        tail = lap_duration - times[-1] if np.isfinite(lap_duration) else 0.0
        positive_deltas = np.diff(times)
        fallback = (
            float(np.median(positive_deltas[positive_deltas > 0]))
            if np.any(positive_deltas > 0)
            else 0.0
        )
        weights[-1] = tail if tail > 0 else fallback
        weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    return ordered, weights


def traffic_lap_rows(points: pd.DataFrame, lap_table: pd.DataFrame) -> pd.DataFrame:
    """按逐点前车距离和速度计算每个 driver×lap 的交通比例。

    `distance_to_driver_ahead` 是米，`speed` 是 km/h；阈值因此是
    `speed / 3.6 * 2s`。分母是实际逐点时间权重而非圈级 median gap，
    且保留所有车手（包括回退车）。交通图不复用 Race pace 的首圈/进站
    排除，只排除非绿/黄旗圈，符合页面对交通图的独立描述。
    """

    required = {
        "driver_acronym",
        "lap",
        "time",
        "sample_index",
        "speed",
        "distance_to_driver_ahead",
        "driver_ahead",
        "lap_duration",
    }
    missing = sorted(required.difference(points.columns))
    if missing:
        raise ValueError(f"逐点遥测缺少交通比例字段: {', '.join(missing)}")

    rows: list[dict[str, Any]] = []
    for (driver, lap), group in points.groupby(
        ["driver_acronym", "lap"], sort=True
    ):
        ordered, weights = _time_weights(group)
        if ordered.empty:
            continue
        distance = pd.to_numeric(
            ordered["distance_to_driver_ahead"], errors="coerce"
        ).to_numpy(dtype=float)
        speed = pd.to_numeric(ordered["speed"], errors="coerce").to_numpy(dtype=float)
        ahead_present = ordered["driver_ahead"].notna().to_numpy(dtype=bool)
        threshold_m = np.maximum(speed, 0.0) / 3.6 * TRAFFIC_THRESHOLD_SECONDS
        in_traffic = (
            np.isfinite(distance)
            & np.isfinite(threshold_m)
            & ahead_present
            & (distance <= threshold_m)
        )
        observed_seconds = float(weights.sum())
        traffic_seconds = float(weights[in_traffic].sum())
        ratio = traffic_seconds / observed_seconds if observed_seconds > 0 else None
        lap_duration = float(
            pd.to_numeric(ordered["lap_duration"], errors="coerce").median()
        )
        rows.append(
            {
                "driver": str(driver),
                "lap": int(lap),
                "traffic_ratio": ratio,
                "traffic_lap": bool(
                    ratio is not None and ratio > TRAFFIC_LAP_THRESHOLD
                ),
                "observed_time_s": observed_seconds,
                "lap_duration_s": lap_duration,
                "telemetry_coverage_ratio": (
                    observed_seconds / lap_duration
                    if np.isfinite(lap_duration) and lap_duration > 0
                    else None
                ),
                "traffic_time_s": traffic_seconds,
                "telemetry_samples": int(len(ordered)),
                "traffic_samples": int(in_traffic.sum()),
                "distance_threshold_basis": "speed_kph_to_mps_x_2s",
            }
        )
    traffic = pd.DataFrame(rows)
    if traffic.empty:
        return traffic
    return traffic.merge(
        lap_table[
            [
                "driver_acronym",
                "lap",
                "stint",
                "compound",
                "tyre_life",
                "track_status",
                "green_or_yellow",
                "pit_boundary_lap",
            ]
        ].rename(columns={"driver_acronym": "driver"}),
        on=["driver", "lap"],
        how="left",
        validate="one_to_one",
    )


def _round(value: Any, digits: int = 6) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            key: (
                None
                if value is None or (isinstance(value, float) and math.isnan(value))
                else value.item() if isinstance(value, np.generic) else value
            )
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]


def build_pace_stats(lap_table: pd.DataFrame) -> dict[str, Any]:
    """生成 Race pace 的全车手点、均值、Q1/Q3 和 Stint 身份。"""

    eligible = lap_table.loc[lap_table["pace_eligible"]].copy()
    rows: list[dict[str, Any]] = []
    for driver, group in eligible.groupby("driver_acronym", sort=True):
        group = group.sort_values("lap", kind="stable")
        times = group["lap_duration"].to_numpy(dtype=float)
        mean = float(times.mean())
        rows.append(
            {
                "driver": str(driver),
                "team": str(group["team"].iloc[0]),
                "valid_laps": int(len(group)),
                "mean_lap_s": _round(mean),
                "q1_lap_s": _round(np.quantile(times, 0.25)),
                "median_lap_s": _round(np.quantile(times, 0.5)),
                "q3_lap_s": _round(np.quantile(times, 0.75)),
                "min_lap_s": _round(times.min()),
                "max_lap_s": _round(times.max()),
                "stops": int(group["stop_count"].iloc[0]),
                "stints": int(group["stint_count"].iloc[0]),
                "compound_counts": {
                    str(compound): int(count)
                    for compound, count in group["compound"].value_counts().items()
                },
                "tyre_age_range": [
                    _round(group["tyre_life"].min(), 2),
                    _round(group["tyre_life"].max(), 2),
                ],
                "points": [
                    {
                        "lap": int(row.lap),
                        "lap_time_s": _round(row.lap_duration),
                        "stint": int(row.stint),
                        "compound": str(row.compound),
                        "tyre_age": _round(row.tyre_life, 2),
                        "stint_jitter": _round(row.stint_jitter, 4),
                        "deleted_marker": bool(row.deleted),
                    }
                    for row in group.itertuples()
                ],
            }
        )
    rows.sort(key=lambda row: (row["mean_lap_s"], row["driver"]))
    fastest = float(rows[0]["mean_lap_s"]) if rows else None
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row["delta_to_fastest_s"] = (
            _round(float(row["mean_lap_s"]) - fastest) if fastest is not None else None
        )
    return {
        "eligible_laps": int(len(eligible)),
        "fastest_mean_s": _round(fastest),
        "all_drivers": rows,
        "top_10": rows[:10],
        "bottom_10": rows[-10:],
    }


def build_pairwise_mean_delta(pace_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """生成左侧车手减底部车手的均值差矩阵，并保留反对称语义。"""

    ordered = list(pace_rows)
    matrix: list[dict[str, Any]] = []
    means = {str(row["driver"]): float(row["mean_lap_s"]) for row in ordered}
    for left in ordered:
        values = {
            right["driver"]: _round(means[left["driver"]] - means[right["driver"]])
            for right in ordered
        }
        matrix.append({"driver": left["driver"], "values": values})
    return {
        "driver_order": [row["driver"] for row in ordered],
        "delta_definition": "left_driver_mean_minus_bottom_driver_mean_s",
        "matrix": matrix,
        "direct_ranking_allowed": False,
        "direct_ranking_reason": (
            "全局均值混合不同停站次数、配方、胎龄与交通；仅作视觉复刻。"
        ),
    }


def build_audited_companion(
    pace_rows: list[dict[str, Any]],
    eligible: pd.DataFrame,
    traffic: pd.DataFrame,
) -> dict[str, Any]:
    """生成不形成全序排名的条件审计伴随层。"""

    traffic_valid = traffic.loc[traffic["green_or_yellow"].fillna(False)].copy()
    profile_rows: list[dict[str, Any]] = []
    for row in pace_rows:
        driver = str(row["driver"])
        laps = eligible.loc[eligible["driver_acronym"].eq(driver)]
        driver_traffic = traffic_valid.loc[traffic_valid["driver"].eq(driver)]
        profile_rows.append(
            {
                "driver": driver,
                "global_mean_s": row["mean_lap_s"],
                "valid_laps": int(len(laps)),
                "stops": int(row["stops"]),
                "stints": int(row["stints"]),
                "compounds": sorted(
                    str(value)
                    for value in laps["compound"].dropna().unique().tolist()
                ),
                "tyre_age_median": _round(laps["tyre_life"].median(), 2),
                "tyre_age_range": [
                    _round(laps["tyre_life"].min(), 2),
                    _round(laps["tyre_life"].max(), 2),
                ],
                "traffic_time_ratio": (
                    _round(
                        np.average(
                            driver_traffic["traffic_ratio"].fillna(0.0),
                            weights=driver_traffic["observed_time_s"],
                        ),
                        4,
                    )
                    if not driver_traffic.empty
                    else None
                ),
                "causal_rank_status": "audit_only",
            }
        )
    pair_rows: list[dict[str, Any]] = []
    for left, right in combinations(profile_rows, 2):
        reasons: list[str] = []
        if left["stops"] != right["stops"]:
            reasons.append("pit_stop_count_differs")
        if left["compounds"] != right["compounds"]:
            reasons.append("compound_mix_differs")
        if (
            left["tyre_age_median"] is not None
            and right["tyre_age_median"] is not None
            and abs(left["tyre_age_median"] - right["tyre_age_median"]) > 2.0
        ):
            reasons.append("tyre_age_median_differs_over_2_laps")
        if (
            left["traffic_time_ratio"] is not None
            and right["traffic_time_ratio"] is not None
            and abs(left["traffic_time_ratio"] - right["traffic_time_ratio"]) > 0.2
        ):
            reasons.append("traffic_distribution_differs")
        pair_rows.append(
            {
                "left_driver": left["driver"],
                "right_driver": right["driver"],
                "global_mean_delta_s": _round(
                    float(left["global_mean_s"]) - float(right["global_mean_s"])
                ),
                "audited_delta_s": None,
                "status": "audit_only",
                "reasons": reasons or ["global_mean_not_condition_balanced"],
            }
        )
    return {
        "status": "audit_only",
        "direct_ranking_allowed": False,
        "reason": (
            "不同停站次数、轮胎配方、胎龄与交通构成使全局均值差不具因果可比性；"
            "本层只披露结构差异，不发布全序。"
        ),
        "driver_profiles": profile_rows,
        "pair_audit": pair_rows,
    }


def build_exclusion_ledger(lap_table: pd.DataFrame, points: pd.DataFrame) -> dict[str, Any]:
    """生成圈与逐点样本排除账本。"""

    all_laps = int(len(lap_table))
    first_laps = int(lap_table["first_lap"].sum())
    boundary_laps = int(
        (lap_table["pit_boundary_lap"] & ~lap_table["first_lap"]).sum()
    )
    non_green_laps = int(
        (~lap_table["green_or_yellow"] & ~lap_table["first_lap"]).sum()
    )
    eligible_laps = int(lap_table["pace_eligible"].sum())
    valid_points = points.merge(
        lap_table.loc[
            lap_table["pace_eligible"], ["driver_acronym", "lap"]
        ],
        on=["driver_acronym", "lap"],
        how="inner",
    )
    return {
        "lap_universe": all_laps,
        "pace_eligible": eligible_laps,
        "excluded": {
            "first_lap": first_laps,
            "pit_in_out_boundary_union": boundary_laps,
            "non_green_or_yellow": non_green_laps,
            "other_overlap_note": "排除原因按规则可重叠，pace_eligible 是最终交集。",
            "deleted_marker_retained_in_eligible": int(
                lap_table.loc[lap_table["pace_eligible"], "deleted"].sum()
            ),
        },
        "point_samples": {
            "race_total": int(len(points)),
            "pace_eligible_lap_samples": int(len(valid_points)),
        },
    }


def build_qualifying_proxy(qualifying: pd.DataFrame) -> dict[str, Any]:
    """独立输出排位整场最佳准确推圈代理，不伪造 Q1/Q2/Q3。"""

    required = {
        "driver_id",
        "lap_duration",
        "is_pit_in_lap",
        "is_pit_out_lap",
        "is_disrupted_lap",
        "compound",
    }
    missing = sorted(required.difference(qualifying.columns))
    if missing:
        raise ValueError(f"排位冻结集缺少字段: {', '.join(missing)}")
    work = qualifying.copy()
    work["lap_duration"] = pd.to_numeric(work["lap_duration"], errors="coerce")
    for column in ("is_pit_in_lap", "is_pit_out_lap", "is_disrupted_lap"):
        work[column] = work[column].astype(str).str.lower().eq("true")
    usable = work.loc[
        work["lap_duration"].notna()
        & ~work["is_pit_in_lap"]
        & ~work["is_pit_out_lap"]
        & ~work["is_disrupted_lap"]
    ]
    rows = []
    for driver, group in usable.groupby("driver_id", sort=True):
        best = group.loc[group["lap_duration"].idxmin()]
        rows.append(
            {
                "driver": str(driver),
                "best_accurate_lap_proxy_s": _round(best["lap_duration"]),
                "lap_number": int(best["lap_number"]),
                "compound": str(best["compound"]),
                "phase": None,
                "phase_status": "NOT_AVAILABLE_IN_CURRENT_FREEZE",
            }
        )
    rows.sort(key=lambda row: (row["best_accurate_lap_proxy_s"], row["driver"]))
    return {
        "status": "proxy_only",
        "interpretation": "整场最佳准确推圈代理；阶段不可识别",
        "q1_q2_q3_phase": "NOT_AVAILABLE_IN_CURRENT_FREEZE",
        "rows": rows,
    }


def build_f1pace_pilot(
    points: pd.DataFrame,
    qualifying: pd.DataFrame,
    *,
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """从真实 2025 Abu Dhabi Parquet 组装完整 F1pace pilot JSON。"""

    lap_table = build_lap_table(points)
    pace = build_pace_stats(lap_table)
    eligible = lap_table.loc[lap_table["pace_eligible"]].copy()
    traffic = traffic_lap_rows(points, lap_table)
    traffic_records = _records(traffic)
    valid_traffic = traffic.loc[traffic["green_or_yellow"].fillna(False)]
    traffic_summary = []
    for driver, group in valid_traffic.groupby("driver", sort=True):
        traffic_summary.append(
            {
                "driver": str(driver),
                "weighted_time_in_traffic": _round(
                    np.average(
                        group["traffic_ratio"].fillna(0.0),
                        weights=group["observed_time_s"],
                    ),
                    4,
                ),
                "traffic_laps_over_33pct": int(group["traffic_lap"].sum()),
                "traffic_laps_observed": int(len(group)),
                "status_filter": "green_or_yellow_only_for_summary",
            }
        )
    traffic_summary.sort(key=lambda row: (-row["weighted_time_in_traffic"], row["driver"]))
    pace_rows = pace["all_drivers"]
    return {
        "schema_version": "reference-analysis-f1pace-v1",
        "target": {
            "creator": "F1pace by F1bythenumbers",
            "page": "2025 Abu Dhabi GP: Race pace",
            "event": "2025 Abu Dhabi Grand Prix",
            "session": "Race",
        },
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_card": {
            "public_method": [
                "移除非绿/黄旗圈，即 VSC/SC 圈。",
                "移除首圈与进站/出站边界圈。",
                "湿胎圈在满足旗语条件时保留。",
                "按车手均值排序；箱形/分布图展示 Q1、Q3、均值和逐圈点。",
                "Stint 只用于横向 jitter；Stint 内横轴不编码数值。",
                "交通使用前车 2 秒内时长比例，一圈超过 33% 记为 in traffic，包含回退车。",
                "pairwise delta 为左侧车手均值减底部车手均值。",
            ],
            "visual_inference": [
                "公开图的颜色条使用车队/车手视觉身份，但本产品改用中性网格与官方车队色。",
                "箱形背景的四分位色带和右侧 0.250 秒 bin 采用语义等价的 CSS/Canvas 重绘。",
                "页面没有公开原始选圈代码；本 pilot 只复现公开筛选口径。",
            ],
            "not_identifiable": [
                "真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令。",
                "排位 Q1/Q2/Q3 阶段标签在当前冻结集不可用。",
                "全局均值差的因果车辆/车手贡献。",
            ],
            "license_boundary": (
                "参考图仅用于语义和布局核对；不复制 logo、品牌字体或原始图片作为产品资产。"
                "页面署名和 CC BY-NC-ND 边界保留。"
            ),
        },
        "data_gap_audit": [
            {
                "field": "race_lap_time",
                "current_field": "lap_duration",
                "status": "AVAILABLE",
                "substitute": None,
                "publication": "allowed_descriptive",
            },
            {
                "field": "green_yellow_flag_lap",
                "current_field": "track_status",
                "status": "AVAILABLE",
                "substitute": None,
                "publication": "allowed_descriptive",
            },
            {
                "field": "pit_in_out_lap",
                "current_field": "stint transition",
                "status": "AVAILABLE_PROXY",
                "substitute": "stint 切换前后两圈边界代理",
                "publication": "allowed_with_proxy_label",
            },
            {
                "field": "point_distance_to_driver_ahead",
                "current_field": "distance_to_driver_ahead + speed + time",
                "status": "AVAILABLE",
                "substitute": "逐点时间加权，不使用 lap median gap",
                "publication": "allowed_descriptive",
            },
            {
                "field": "qualifying_q1_q2_q3_phase",
                "current_field": "phase label absent in qualifying_2023_2025_v4",
                "status": "NOT_AVAILABLE_IN_CURRENT_FREEZE",
                "substitute": "整场最佳准确推圈代理",
                "publication": "proxy_only",
            },
            {
                "field": "true_fuel_or_tyre_state",
                "current_field": "none",
                "status": "NOT_IDENTIFIABLE",
                "substitute": "不发布物理量；后续目标可用具名情景",
                "publication": "not_allowed_as_observed_fact",
            },
        ],
        "visual_replication": {
            "pace": pace,
            "summarized_race_pace": [
                {
                    "driver": row["driver"],
                    "mean_lap_s": row["mean_lap_s"],
                    "delta_to_fastest_s": row["delta_to_fastest_s"],
                    "distribution": sorted(
                        point["lap_time_s"] for point in row["points"]
                    ),
                    "points": row["points"],
                }
                for row in pace_rows
            ],
            "traffic": {
                "lap_rows": traffic_records,
                "summary": traffic_summary,
                "threshold_seconds": TRAFFIC_THRESHOLD_SECONDS,
                "lap_classification_threshold": TRAFFIC_LAP_THRESHOLD,
                "includes_backmarkers": True,
            },
            "pairwise_mean_delta": build_pairwise_mean_delta(pace_rows),
        },
        "audited_analysis": build_audited_companion(
            pace_rows, eligible, traffic
        ),
        "qualifying_analysis": build_qualifying_proxy(qualifying),
        "exclusion_ledger": build_exclusion_ledger(lap_table, points),
        "boundaries": {
            "status": "audit_only_for_causal_claims",
            "visual_replication_is_not_a_ranking_truth_layer": True,
            "pairwise_global_mean_is_not_causal": True,
            "fuel_traffic_track_evolution_are_condition_proxies": True,
        },
    }

