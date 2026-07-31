#!/usr/bin/env python3
"""生成同队双车遥测差异、Episode、策略指纹与评分边界报告。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd

import run_experiments as base
from modeling.episode_engine import (
    EpisodeThresholds,
    build_lap_states,
    detect_episodes,
    opportunity_summary,
)
from modeling.team_telemetry_report import (
    LAP_KEYS,
    build_anomaly_evidence,
    build_pair_segment_differences,
    build_segment_lap_features,
    build_stint_dossiers,
    fit_field_response_baseline,
    summarize_segment_profiles,
)
from modeling.telemetry_explanation import (
    ComparableLapConfig,
    TELEMETRY_CLAIM_BOUNDARIES,
    TrackPhaseConfig,
    TrackPhaseMap,
    extract_corner_straight_features,
    match_comparable_laps,
    resample_distance,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT
    / "research"
    / "configs"
    / "telemetry_explanation_2026_australia_ferrari_v7.json"
)
OBJECTIVE_REVIEW = ROOT / "research" / "records" / "objective_rating_review_2023_2026_v1.json"
SEASONAL_RESULT = ROOT / "research" / "records" / "seasonal_standings_calibration_results.json"

SAMPLE_COLUMNS = (
    "year",
    "meeting",
    "session",
    "team",
    "driver_acronym",
    "driver_number",
    "lap",
    "sample_index",
    "time",
    "distance",
    "speed",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "drs",
    "drs_raw",
    "drs_raw_source",
    "x",
    "y",
    "z",
    "acc_x",
    "acc_y",
    "acc_z",
    "driver_ahead",
    "distance_to_driver_ahead",
    "lap_duration",
    "compound",
    "stint",
    "tyre_life",
    "fresh_tyre",
    "position",
    "track_status",
    "track_status_raw",
    "deleted",
    "is_accurate",
    "lap_start_raw",
    "lap_start_time",
    "sample_time",
)


def _resolve_partition(config: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    scope = config["scope"]
    session_partition = str(scope["session"]).replace(" ", "_")
    meeting_partition = str(scope["meeting_partition"])
    candidates: list[tuple[int, Path, Path]] = []
    preferences = list(config["telemetry"]["schema_roots_preference"])
    for priority, relative in enumerate(preferences):
        schema_root = ROOT / relative
        year_root = schema_root / f"year={int(scope['year'])}"
        if not year_root.is_dir():
            continue
        pattern = (
            f"commit=*/session={session_partition}/meeting={meeting_partition}/telemetry.parquet"
        )
        for path in year_root.glob(pattern):
            candidates.append((priority, schema_root, path))
    if not candidates:
        raise FileNotFoundError(
            f"没有找到 {scope['year']} {scope['meeting']} {scope['session']} 的规范遥测分区"
        )
    candidates.sort(key=lambda item: (item[0], -item[2].stat().st_mtime_ns, str(item[2])))
    priority, schema_root, path = candidates[0]
    same_priority = [item for item in candidates if item[0] == priority]
    identity = {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": base.file_sha256(path),
        "bytes": int(path.stat().st_size),
        "schema": schema_root.name.removeprefix("schema="),
        "schema_preference_rank": int(priority + 1),
        "same_schema_candidates": int(len(same_priority)),
        "commit_partition": next(
            (part.removeprefix("commit=") for part in path.parts if part.startswith("commit=")),
            None,
        ),
    }
    return path, identity


def _load_samples(path: Path) -> tuple[pd.DataFrame, list[str]]:
    connection = duckdb.connect()
    try:
        sql_path = path.as_posix().replace("'", "''")
        description = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{sql_path}')"
        ).fetchdf()
        available = description["column_name"].astype(str).tolist()
        selected = [column for column in SAMPLE_COLUMNS if column in available]
        required = {
            "year",
            "meeting",
            "session",
            "team",
            "driver_acronym",
            "driver_number",
            "lap",
            "distance",
            "speed",
            "throttle",
            "brake",
            "gear",
            "rpm",
            "driver_ahead",
            "distance_to_driver_ahead",
            "lap_duration",
            "compound",
            "stint",
            "tyre_life",
            "position",
            "track_status",
            "deleted",
            "is_accurate",
        }
        missing = sorted(required - set(selected))
        if missing:
            raise ValueError(f"遥测分区缺少端到端解释必需字段：{missing}")
        quoted = ", ".join(f'"{column}"' for column in selected)
        frame = connection.execute(
            f"SELECT {quoted} FROM read_parquet('{sql_path}')"
        ).fetchdf()
    finally:
        connection.close()
    return frame, available


def _episode_thresholds(config: Mapping[str, Any]) -> EpisodeThresholds:
    episode = config["episode"]
    return EpisodeThresholds(
        minimum_distance_coverage=float(config["telemetry"]["minimum_distance_coverage"]),
        attack_gap_seconds=float(episode["attack_gap_seconds"]),
        traffic_gap_seconds=float(episode["traffic_gap_seconds"]),
        clean_air_gap_seconds=float(episode["clean_air_gap_seconds"]),
        warmup_laps=int(episode["warmup_laps"]),
        late_stint_fraction=float(episode["late_stint_fraction"]),
        negative_tail_mad=float(episode["negative_tail_mad"]),
    )


def _prepare_lap_states(samples: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    laps = build_lap_states(samples, _episode_thresholds(config))
    session_max = laps.groupby(
        ["year", "meeting", "session"], observed=True
    )["lap"].transform("max")
    laps["lap_fraction"] = pd.to_numeric(laps["lap"], errors="coerce") / session_max
    laps["lap_context"] = pd.to_numeric(laps["lap"], errors="coerce")
    laps["track_status"] = np.where(laps["all_green"], "1", "non_green")
    return laps


def _eligible_laps(laps: pd.DataFrame, *, minimum_samples: int) -> pd.DataFrame:
    duration = pd.to_numeric(laps["lap_duration"], errors="coerce")
    return laps.loc[
        laps["is_clean"]
        & laps["samples"].ge(minimum_samples)
        & duration.notna()
        & duration.gt(0)
        & ~laps["pit_boundary_proxy"]
    ].copy()


def _reference_lap_keys(
    eligible: pd.DataFrame,
    *,
    target_team: str,
    maximum_laps_per_driver: int,
    exclude_target_team: bool,
) -> pd.DataFrame:
    pool = eligible.copy()
    if exclude_target_team:
        pool = pool.loc[pool["team"].ne(target_team)]
    preferred = pool.loc[
        pool["traffic_state"].isin(["clean_air", "intermediate_gap"])
        & pool["stint_phase"].eq("steady")
    ].copy()
    if preferred.empty:
        preferred = pool
    duration = pd.to_numeric(preferred["lap_duration"], errors="coerce")
    median = duration.groupby(preferred["driver_acronym"], observed=True).transform("median")
    preferred["_reference_distance"] = (duration - median).abs()
    return (
        preferred.sort_values(
            ["driver_acronym", "_reference_distance", "lap"], kind="stable"
        )
        .groupby("driver_acronym", observed=True, sort=False)
        .head(int(maximum_laps_per_driver))
        [["driver_acronym", "lap"]]
        .drop_duplicates()
    )


def _select_samples(samples: pd.DataFrame, keys: pd.DataFrame) -> pd.DataFrame:
    return samples.merge(keys.drop_duplicates(), on=["driver_acronym", "lap"], how="inner")


def _resample(samples: pd.DataFrame, step_m: float) -> pd.DataFrame:
    continuous = [
        column
        for column in (
            "speed",
            "throttle",
            "brake",
            "gear",
            "rpm",
            "acc_x",
            "acc_y",
            "acc_z",
            "x",
            "y",
            "z",
        )
        if column in samples.columns
    ]
    return resample_distance(
        samples,
        group_columns=LAP_KEYS,
        step_m=float(step_m),
        continuous_columns=continuous,
    )


def _match_team_laps(
    eligible: pd.DataFrame,
    config: Mapping[str, Any],
) -> pd.DataFrame:
    scope = config["scope"]
    matching = config["matching"]
    target = eligible.loc[
        eligible["team"].eq(scope["team"])
        & eligible["driver_acronym"].eq(scope["target_driver"])
    ].copy()
    reference = eligible.loc[
        eligible["team"].eq(scope["team"])
        & eligible["driver_acronym"].eq(scope["reference_driver"])
    ].copy()
    exact = ["year", "meeting", "session", "compound", "track_status"]
    if bool(matching.get("traffic_state_exact", True)):
        exact.append("traffic_state")
    exact.append("stint_phase")
    return match_comparable_laps(
        target,
        reference,
        config=ComparableLapConfig(
            exact_columns=tuple(exact),
            numeric_tolerances={
                "tyre_life": float(matching["tyre_life_caliper"]),
                "lap_fraction": float(matching["lap_fraction_caliper"]),
                "lap_context": float(matching["time_window_laps"]),
            },
            one_to_one=True,
        ),
    )


def _matching_balance(
    matches: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """汇报匹配后协变量差异；不把“落在卡钳内”偷换成完全平衡。"""

    tolerances = {
        "tyre_life": float(config["matching"]["tyre_life_caliper"]),
        "lap_fraction": float(config["matching"]["lap_fraction_caliper"]),
        "lap_context": float(config["matching"]["time_window_laps"]),
    }
    numeric: dict[str, Any] = {}
    for feature, tolerance in tolerances.items():
        column = f"delta_{feature}"
        values = (
            pd.to_numeric(matches[column], errors="coerce").dropna()
            if column in matches
            else pd.Series(dtype=float)
        )
        numeric[feature] = {
            "n": int(len(values)),
            "mean_delta": float(values.mean()) if len(values) else None,
            "median_delta": float(values.median()) if len(values) else None,
            "mean_absolute_delta": float(values.abs().mean()) if len(values) else None,
            "maximum_absolute_delta": float(values.abs().max()) if len(values) else None,
            "caliper": tolerance,
            "mean_absolute_fraction_of_caliper": (
                float(values.abs().mean() / tolerance) if len(values) else None
            ),
        }
    exact_columns = [
        column
        for column in ("compound", "track_status", "traffic_state", "stint_phase")
        if f"target_{column}" in matches and f"reference_{column}" in matches
    ]
    exact_mismatches = {
        column: int(
            matches[f"target_{column}"].astype("string").ne(
                matches[f"reference_{column}"].astype("string")
            ).sum()
        )
        for column in exact_columns
    }
    return {
        "numeric": numeric,
        "exact_mismatches": exact_mismatches,
        "all_within_declared_calipers": bool(
            all(
                row["maximum_absolute_delta"] is None
                or row["maximum_absolute_delta"] <= row["caliper"] + 1e-12
                for row in numeric.values()
            )
        ),
        "all_exact_blocks_equal": bool(all(value == 0 for value in exact_mismatches.values())),
        "maximum_mean_absolute_fraction_of_caliper": max(
            (
                float(row["mean_absolute_fraction_of_caliper"])
                for row in numeric.values()
                if row["mean_absolute_fraction_of_caliper"] is not None
            ),
            default=None,
        ),
    }


def _load_corners(config: Mapping[str, Any]) -> dict[str, Any] | None:
    path = ROOT / config["telemetry"]["raw_corners"]
    if not path.is_file():
        return None
    value = base.load_json(path)
    return value if isinstance(value, dict) else None


def _strategy_feature_rows(segment_features: pd.DataFrame) -> pd.DataFrame:
    """构造每圈前后直道收益交换、滑行和高响应代理。"""

    straight = segment_features.loc[segment_features["track_phase"].eq("straight")].copy()
    if straight.empty:
        return pd.DataFrame()
    straight["segment_order"] = straight.groupby(
        [*LAP_KEYS], observed=True
    )["distance_start_m"].rank(method="dense")
    straight["segment_count"] = straight.groupby(
        [*LAP_KEYS], observed=True
    )["track_segment_id"].transform("nunique")
    straight["straight_fraction"] = straight["segment_order"] / straight[
        "segment_count"
    ].clip(lower=1)
    straight["early_speed"] = straight["mean_speed_kmh"].where(
        straight["straight_fraction"].le(0.5)
    )
    straight["late_speed"] = straight["mean_speed_kmh"].where(
        straight["straight_fraction"].gt(0.5)
    )
    rows = (
        straight.groupby(list(LAP_KEYS), observed=True, sort=True)
        .agg(
            early_straight_speed_kmh=("early_speed", "mean"),
            late_straight_speed_kmh=("late_speed", "mean"),
            peak_straight_speed_kmh=("maximum_speed_kmh", "max"),
            full_throttle_fraction=("full_throttle_fraction", "mean"),
            mean_straight_rpm=("mean_rpm", "mean"),
            mean_straight_gear=("mean_gear", "mean"),
            response_proxy=("mean_acc_x_proxy", "mean"),
            straight_segments=("track_segment_id", "nunique"),
        )
        .reset_index()
    )
    rows["early_late_exchange_kmh"] = (
        rows["early_straight_speed_kmh"] - rows["late_straight_speed_kmh"]
    )
    rows["save_like_proxy"] = 1.0 - rows["full_throttle_fraction"]
    return rows


def _build_strategy_fingerprints(
    segment_features: pd.DataFrame,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    features = _strategy_feature_rows(segment_features)
    if features.empty:
        return []
    try:
        from modeling.strategy_fingerprint import build_strategy_mode_fingerprints
    except ImportError:
        return [
            {
                "mode_label": "not_comparable",
                "confidence": "insufficient",
                "interpretation": "策略指纹模块不可用。",
                "alternative_explanations": ["模块尚未安装"],
            }
        ]
    settings = config["strategy_modes"]
    result = build_strategy_mode_fingerprints(
        features,
        feature_columns=(
            "early_straight_speed_kmh",
            "late_straight_speed_kmh",
            "peak_straight_speed_kmh",
            "save_like_proxy",
            "mean_straight_rpm",
            "response_proxy",
        ),
        n_modes=int(settings["n_modes"]),
        random_seed=int(settings["random_seed"]),
        minimum_rows=max(6, int(settings["minimum_straight_samples"])),
        prefer_sklearn=bool(settings.get("prefer_sklearn", True)),
    )
    if isinstance(result, pd.DataFrame):
        return result.to_dict(orient="records")
    return list(result)


def _compare_strategy_fingerprints(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_driver: str,
    reference_driver: str,
) -> dict[str, Any]:
    """比较中性模式分布与公开特征，不把聚类标签解释为真实开关状态。"""

    target = next(
        (row for row in rows if row.get("driver_acronym") == target_driver), None
    )
    reference = next(
        (row for row in rows if row.get("driver_acronym") == reference_driver), None
    )
    if not target or not reference or target.get("status") != "ok" or reference.get("status") != "ok":
        return {
            "status": "not_comparable",
            "reason": "两位车手没有同时形成稳定的中性模式指纹。",
        }
    target_distribution = target.get("mode_distribution", {})
    reference_distribution = reference.get("mode_distribution", {})
    labels = sorted(set(target_distribution) | set(reference_distribution))
    target_total = max(1, sum(int(target_distribution.get(label, 0)) for label in labels))
    reference_total = max(1, sum(int(reference_distribution.get(label, 0)) for label in labels))
    total_variation = 0.5 * sum(
        abs(
            int(target_distribution.get(label, 0)) / target_total
            - int(reference_distribution.get(label, 0)) / reference_total
        )
        for label in labels
    )
    target_evidence = target.get("evidence", {})
    reference_evidence = reference.get("evidence", {})
    differences: dict[str, float] = {}
    for feature in sorted(set(target_evidence) & set(reference_evidence)):
        try:
            differences[feature] = float(target_evidence[feature]) - float(
                reference_evidence[feature]
            )
        except (TypeError, ValueError):
            continue
    same = target.get("mode_label") == reference.get("mode_label")
    return {
        "status": "comparable",
        "target_driver": target_driver,
        "reference_driver": reference_driver,
        "target_mode_label": target.get("mode_label"),
        "reference_mode_label": reference.get("mode_label"),
        "same_dominant_mode": bool(same),
        "mode_distribution_total_variation": float(total_variation),
        "target_minus_reference_feature_differences": differences,
        "interpretation": (
            "两车主导中性模式相同；差异主要体现在同类模式内的公开特征强度。"
            if same
            else "两车主导中性模式分布不同，但不能据此确认能量或主动空力状态。"
        ),
        "directness_level": "public_telemetry_proxy_not_direct_state",
        "alternative_explanations": [
            "出弯初速与尾流",
            "燃油、轮胎和比赛目标",
            "驾驶操作、车辆设定或单车状态",
            "公开采样及插值误差",
        ],
    }


def _rating_cards(
    config: Mapping[str, Any],
    *,
    clean_laps_by_driver: Mapping[str, int] | None = None,
    matched_pairs: int = 0,
    observed_episode_rows: int = 0,
) -> list[dict[str, Any]]:
    """只发布已通过车手级时间前推门控的 PAC；其余模块保持空值。"""

    drivers = [config["scope"]["target_driver"], config["scope"]["reference_driver"]]
    module_status = {
        # 审查文件缺失时必须 fail closed，不能把“没有反证”当成已验证。
        "PAC": "missing",
        "TYM": "prototype",
        "EXT": "prototype",
        "ATK": "missing",
        "DEF": "missing",
        "AWA": "missing",
        "STR": "missing",
        "ADP": "missing",
        "OVR": "insufficient",
    }
    if OBJECTIVE_REVIEW.is_file():
        review = base.load_json(OBJECTIVE_REVIEW)
        module_status.update(review.get("evaluation", {}).get("module_coverage", {}))
    rows: list[dict[str, Any]] = []
    seasonal = base.load_json(SEASONAL_RESULT) if SEASONAL_RESULT.is_file() else None
    ratings = seasonal.get("season_ratings", {}).get("drivers", []) if seasonal else []
    # driver_base_z 已经是跨季层级收缩后的长期能力节点。每个车手只计一次，
    # 不再按圈数二次收缩，也不把年度 driver_pace_z 区间伪装成 pooled 区间。
    pooled_by_driver = {
        str(row["driver"]): float(row["driver_base_z"])
        for row in ratings
        if int(row.get("year", -1)) in {2023, 2024, 2025}
        and row.get("driver_base_z") is not None
    }
    reference_values = np.asarray(
        [-value for value in pooled_by_driver.values()], dtype=float
    )
    center = float(np.mean(reference_values)) if len(reference_values) else np.nan
    spread = float(np.std(reference_values, ddof=0)) if len(reference_values) > 1 else np.nan
    season_gate = None
    if OBJECTIVE_REVIEW.is_file():
        review = base.load_json(OBJECTIVE_REVIEW)
        season_gate = next(
            (
                gate
                for gate in review.get("evaluation", {}).get("gates", [])
                if gate.get("gate_id") == "2026_confirmatory_exposure"
            ),
            None,
        )
    for driver in drivers:
        if (
            module_status.get("PAC") == "validated"
            and driver in pooled_by_driver
            and np.isfinite(spread)
            and spread > 0
        ):
            advantage = -float(pooled_by_driver[driver])
            standardized = (advantage - center) / spread
            score = float(np.clip(75.0 + 10.0 * standardized, 0.0, 100.0))
            historical_exposure = int(
                sum(
                    int(row.get("sample_count", 0))
                    for row in ratings
                    if row.get("driver") == driver
                    and int(row.get("year", -1)) in {2023, 2024, 2025}
                )
            )
            rows.append(
                {
                    "code": f"{driver}.PAC",
                    "driver": driver,
                    "module_id": "PAC",
                    "rating_mean": score,
                    "rating_interval_95": None,
                    "confidence_grade": "interval_not_exported",
                    "status": "validated_historical_prior_not_2026_event_delivery",
                    "source": "2023-2025 frozen clean-pace model",
                    "exposure": historical_exposure,
                    "reference_distribution": {
                        "drivers": int(len(reference_values)),
                        "center": center,
                        "population_sd": spread,
                        "source_run_id": seasonal.get("run_id") if seasonal else None,
                        "source_config_hash": seasonal.get("config_hash") if seasonal else None,
                    },
                    "uncertainty_note": "pooled_driver_base_interval_not_exported",
                }
            )
        else:
            rows.append(
                {
                    "code": f"{driver}.PAC",
                    "driver": driver,
                    "module_id": "PAC",
                    "status": "insufficient",
                    "maturity": module_status.get("PAC", "missing"),
                    "rating_mean": None,
                    "rating_interval_95": None,
                    "reason": (
                        "联合圈速时间前推尚未证明池化 PAC 的车手级排序稳定性与区间覆盖。"
                    ),
                    "uncertainty_note": "pooled_driver_base_interval_not_exported",
                }
            )
        for module in ("TYM", "EXT", "ATK", "DEF", "AWA", "STR", "ADP", "OVR"):
            rows.append(
                {
                    "code": f"{driver}.{module}",
                    "driver": driver,
                    "module_id": module,
                    "status": "insufficient",
                    "maturity": module_status.get(module, "missing"),
                    "reason": (
                        "能力模块尚未通过冻结时间外验证，不能由本场赛果或同队差反推。"
                    ),
                }
            )
        rows.append(
            {
                "code": f"{driver}.EventDelivery",
                "driver": driver,
                "module_id": "EventDelivery",
                "status": "prototype_not_publishable",
                "rating_mean": None,
                "physical_delta_ms_per_lap": None,
                "physical_delta_ci95": None,
                "exposure": {
                    "season_events_observed": season_gate.get("value") if season_gate else None,
                    "season_events_required": season_gate.get("threshold") if season_gate else None,
                    "clean_laps": int((clean_laps_by_driver or {}).get(driver, 0)),
                    "comparable_pairs": int(matched_pairs),
                    "observed_episode_rows": int(observed_episode_rows),
                    "independent_episodes": None,
                },
                "reason": "2026 era 尚无冻结且验证过的全场 Event Delivery 参考分布。",
            }
        )
    return rows


def _finite(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def _report_summary(report: Mapping[str, Any]) -> str:
    scope = report["scope"]
    comparison = report["team_difference"]["comparability"]
    lines = [
        f"# {scope['year']} {scope['meeting']} {scope['team']} 双车遥测解释",
        "",
        f"- 对比方向：`{scope['target_driver']} - {scope['reference_driver']}`；正时间差表示目标车手更慢。",
        f"- 可比圈：{comparison['matched_pairs']}；状态：`{comparison['status']}`。",
        f"- 数据合同：`{report['data_identity']['schema']}`；发布状态：`{report['release_status']}`。",
        f"- 通过完整异常门控的 Episode：{len(report['team_difference']['anomaly_episodes'])}。",
        "",
        "## 主要赛段来源",
        "",
        "| 赛段 | 直接差 ms | 输入调整响应差 ms | 可比圈 | 置信 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    profiles = sorted(
        report["team_difference"]["segment_profiles"],
        key=lambda row: abs(float(row.get("delta_time_ms") or 0.0)),
        reverse=True,
    )[:12]
    for row in profiles:
        adjusted = row.get("input_adjusted_response_gap_ms")
        lines.append(
            f"| {row['label']} | {row['delta_time_ms']:.1f} | "
            f"{adjusted:.1f} | {row['matched_pairs']} | {row['confidence']} |"
            if adjusted is not None
            else f"| {row['label']} | {row['delta_time_ms']:.1f} | — | {row['matched_pairs']} | {row['confidence']} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 直接时间差保留车手操作、策略和未观测状态；输入调整残差也只是诊断证据，不是因果效应。",
            "- 2026 策略只使用中性模式标签，不推断 SOC、ERS-K 实际功率、主动翼位置或 Overtake 状态。",
            "- 当前只有四场 2026 数据，赛季确认性门槛为八场；本报告不能生成 2026 赛季 OVR。",
            "",
        ]
    )
    return "\n".join(lines)


def run(config: Mapping[str, Any]) -> dict[str, Any]:
    path, data_identity = _resolve_partition(config)
    samples, available_columns = _load_samples(path)
    laps = _prepare_lap_states(samples, config)
    eligible = _eligible_laps(
        laps,
        minimum_samples=int(config["telemetry"]["minimum_samples_per_lap"]),
    )
    scope = config["scope"]
    matches = _match_team_laps(eligible, config)
    minimum_pairs = int(config["matching"]["minimum_matched_pairs"])
    match_balance = _matching_balance(matches, config)
    maximum_balance_fraction = match_balance["maximum_mean_absolute_fraction_of_caliper"]
    warning_fraction = float(
        config["matching"].get("balance_warning_fraction_of_caliper", 0.65)
    )
    reject_fraction = float(
        config["matching"].get("balance_reject_fraction_of_caliper", 0.90)
    )
    if (
        len(matches) < minimum_pairs
        or not match_balance["all_within_declared_calipers"]
        or not match_balance["all_exact_blocks_equal"]
        or (
            maximum_balance_fraction is not None
            and maximum_balance_fraction > reject_fraction
        )
    ):
        comparability_status = "not_comparable"
    elif maximum_balance_fraction is not None and maximum_balance_fraction > warning_fraction:
        comparability_status = "comparable_with_balance_warning"
    else:
        comparability_status = "comparable"

    field_settings = config["field_reference"]
    reference_keys = _reference_lap_keys(
        eligible,
        target_team=str(scope["team"]),
        maximum_laps_per_driver=int(field_settings["maximum_laps_per_driver"]),
        exclude_target_team=bool(field_settings["exclude_target_team"]),
    )
    team_eligible_keys = eligible.loc[
        eligible["team"].eq(scope["team"])
        & eligible["driver_acronym"].isin(
            [scope["target_driver"], scope["reference_driver"]]
        ),
        ["driver_acronym", "lap"],
    ]
    if len(matches):
        target_keys = pd.concat(
            [
                matches[["target_driver_acronym", "target_lap"]].rename(
                    columns={
                        "target_driver_acronym": "driver_acronym",
                        "target_lap": "lap",
                    }
                ),
                matches[["reference_driver_acronym", "reference_lap"]].rename(
                    columns={
                        "reference_driver_acronym": "driver_acronym",
                        "reference_lap": "lap",
                    }
                ),
            ],
            ignore_index=True,
        ).drop_duplicates()
    else:
        target_keys = (
            team_eligible_keys.sort_values(["driver_acronym", "lap"], kind="stable")
            .groupby("driver_acronym", observed=True, sort=False)
            .head(max(3, int(config["strategy_modes"]["minimum_straight_samples"])))
        )
    step_m = float(config["telemetry"]["grid_step_m"])
    field_resampled = _resample(_select_samples(samples, reference_keys), step_m)
    team_resampled = _resample(_select_samples(samples, target_keys), step_m)
    corners = _load_corners(config)
    phase_map = TrackPhaseMap.from_reference_laps(
        field_resampled,
        config=TrackPhaseConfig(
            minimum_phase_length_m=max(20.0, step_m * 3),
        ),
        anchor_distances=(corners or {}).get("Distance"),
    )
    field_segments = build_segment_lap_features(field_resampled, phase_map)
    team_segments = build_segment_lap_features(team_resampled, phase_map)
    scored_team_segments = fit_field_response_baseline(
        field_segments,
        team_segments,
        alpha=float(field_settings["ridge_alpha"]),
        minimum_rows_per_segment=int(field_settings["minimum_rows_per_segment"]),
    )
    pair_segments = (
        build_pair_segment_differences(matches, scored_team_segments)
        if comparability_status != "not_comparable"
        else pd.DataFrame()
    )
    profiles = summarize_segment_profiles(
        pair_segments,
        corners=corners,
        random_seed=int(config["strategy_modes"]["random_seed"]),
        force_low_confidence=comparability_status != "comparable",
    )
    anomaly = config["anomaly"]
    if comparability_status == "comparable":
        anomaly_episodes, anomaly_audit = build_anomaly_evidence(
            pair_segments,
            field_segments,
            minimum_persistent_laps=int(anomaly["minimum_persistent_laps"]),
            minimum_independent_feature_families=int(
                anomaly["minimum_independent_feature_families"]
            ),
            practical_time_loss_ms=float(anomaly["practical_time_loss_ms"]),
            false_discovery_rate=float(anomaly["false_discovery_rate"]),
            corners=corners,
        )
    else:
        anomaly_episodes, anomaly_audit = [], pd.DataFrame()
    episode_records = detect_episodes(laps, _episode_thresholds(config))
    team_episode_records = [
        episode for episode in episode_records if episode.team == scope["team"]
    ]
    episode_summary = opportunity_summary(team_episode_records)
    team_laps = laps.loc[laps["team"].eq(scope["team"])]
    stint_dossiers = build_stint_dossiers(
        team_laps,
        [episode.to_dict() for episode in team_episode_records],
    )
    phase_features = extract_corner_straight_features(team_resampled, phase_map)
    phase_annotation_coverage = float(phase_features["phase_time_coverage"].mean())
    analysis_distance_coverage = float(
        pd.to_numeric(
            eligible.loc[
                eligible["team"].eq(scope["team"])
                & eligible["driver_acronym"].isin(
                    [scope["target_driver"], scope["reference_driver"]]
                ),
                "distance_coverage",
            ],
            errors="coerce",
        ).clip(lower=0.0, upper=1.0).median()
    )
    strategy_modes = _build_strategy_fingerprints(team_segments, config)
    strategy_comparison = _compare_strategy_fingerprints(
        strategy_modes,
        target_driver=str(scope["target_driver"]),
        reference_driver=str(scope["reference_driver"]),
    )

    drs_values = (
        sorted(pd.to_numeric(samples["drs"], errors="coerce").dropna().unique().tolist())
        if "drs" in samples
        else []
    )
    has_raw_state = "drs_raw" in samples and samples["drs_raw"].notna().any()
    drs_raw_values = (
        sorted(pd.to_numeric(samples["drs_raw"], errors="coerce").dropna().unique().tolist())
        if "drs_raw" in samples
        else []
    )
    drs_raw_sources = (
        sorted(samples["drs_raw_source"].dropna().astype(str).unique().tolist())
        if "drs_raw_source" in samples
        else []
    )
    objective = base.load_json(OBJECTIVE_REVIEW) if OBJECTIVE_REVIEW.is_file() else {}
    objective_evaluation = objective.get("evaluation", {})
    season_2026_gate = next(
        (
            gate
            for gate in objective_evaluation.get("gates", [])
            if gate.get("gate_id") == "2026_confirmatory_exposure"
        ),
        None,
    )
    direct_lap_delta = (
        pair_segments.groupby("pair_id", observed=True)[
            "external_condition_matched_gap_s"
        ].sum()
        if not pair_segments.empty
        else pd.Series(dtype=float)
    )
    direct_lap_interval = (
        [
            float(direct_lap_delta.quantile(0.10)),
            float(direct_lap_delta.quantile(0.90)),
        ]
        if len(direct_lap_delta)
        else [None, None]
    )
    response_available = (
        float(scored_team_segments["field_response_residual_s"].notna().mean())
        if len(scored_team_segments)
        else 0.0
    )
    report = {
        "report_id": config["experiment_name"],
        "run_id": f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "scope": dict(scope),
        "release_status": "exploratory_2026_rules_reset_insufficient_season_exposure",
        "methodological_status": config["validation_status"],
        "config_hash": base.canonical_hash(config),
        "data_identity": data_identity,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "duckdb": duckdb.__version__,
        },
        "network_calls": [],
        "data_quality": {
            "sample_rows": int(len(samples)),
            "drivers": int(samples["driver_acronym"].nunique()),
            "driver_laps": int(len(laps)),
            "eligible_clean_laps": int(len(eligible)),
            "team_clean_laps": int(len(team_eligible_keys)),
            "team_analysis_laps": int(len(target_keys)),
            "field_reference_laps": int(len(reference_keys)),
            "distance_grid_step_m": step_m,
            "segment_coverage": analysis_distance_coverage,
            "phase_annotation_coverage": phase_annotation_coverage,
            "response_baseline_coverage": response_available,
            "available_columns": available_columns,
            "drs_observed_values": drs_values,
            "drs_raw_observed_values": drs_raw_values,
            "drs_raw_sources": drs_raw_sources,
            "raw_state_preserved": bool(has_raw_state),
            "raw_state_informative": bool(len(drs_raw_values) > 1),
            "absolute_time_preserved": bool(
                "sample_time" in samples and samples["sample_time"].notna().any()
            ),
            "limitations": [
                "当前规范分区回退到 v4；历史二值 DRS 映射可能已丢失 2026 新状态。"
                if data_identity["schema"].endswith("v4")
                else "v5 保留原始状态码，但原始字段语义仍需逐场审计。",
                "当前归档的 drs_raw 仍全部为 0，且来源为上游 tel.drs；v5 只能保留收到的值，不能恢复上游入库前已丢失的新状态。"
                if has_raw_state and drs_raw_values == [0]
                else "原始状态值域必须逐场对照上游采集合同后再解释。",
                "acc_x/y/z 是速度与插值位置的派生低频代理，不是原始 IMU。",
                "公开数据没有直接 SOC、ERS-K 实际功率、主动翼位置和 Overtake 按钮状态。",
            ],
        },
        "team_difference": {
            "orientation": f"{scope['target_driver']} - {scope['reference_driver']}",
            "comparability": {
                "status": comparability_status,
                "matched_pairs": int(len(matches)),
                "minimum_required": minimum_pairs,
                "median_match_quality": (
                    float(matches["match_quality"].median()) if len(matches) else None
                ),
                "target_eligible_laps": int(
                    eligible["driver_acronym"].eq(scope["target_driver"]).sum()
                ),
                "reference_eligible_laps": int(
                    eligible["driver_acronym"].eq(scope["reference_driver"]).sum()
                ),
                "segment_coverage": analysis_distance_coverage,
                "phase_annotation_coverage": phase_annotation_coverage,
                "rule": "same session/compound/traffic/stint phase + tyre/lap calipers",
                "covariate_balance": match_balance,
                "balance_warning_threshold": warning_fraction,
                "balance_reject_threshold": reject_fraction,
            },
            "segment_profiles": profiles,
            "anomaly_episodes": anomaly_episodes,
            "anomaly_audit": {
                "hypotheses": int(len(anomaly_audit)),
                "fdr_rejections": int(anomaly_audit.get("fdr_rejected", pd.Series(dtype=bool)).sum()),
                "persistent_passes": int(
                    anomaly_audit.get("persistent_gate_passed", pd.Series(dtype=bool)).sum()
                ),
                "final_triggers": int(
                    anomaly_audit.get("anomaly_triggered", pd.Series(dtype=bool)).sum()
                ),
                "three_baselines": ["self", "teammate", "field"],
                "minimum_feature_families": int(
                    anomaly["minimum_independent_feature_families"]
                ),
                "fdr_alpha": float(anomaly["false_discovery_rate"]),
                "balance_gate_passed": comparability_status == "comparable",
            },
        },
        "strategy_mode_fingerprints": strategy_modes,
        "strategy_mode_comparison": strategy_comparison,
        "episode_summary": episode_summary.to_dict(orient="records"),
        "episodes": [episode.to_dict() for episode in team_episode_records],
        "stint_dossiers": stint_dossiers,
        "race_value_ledger": {
            "status": "physical_time_only_expected_points_not_identified",
            "target_minus_reference_median_s_per_comparable_lap": (
                float(direct_lap_delta.median()) if len(direct_lap_delta) else None
            ),
            "interval_80_s_per_comparable_lap": direct_lap_interval,
            "delta_expected_points": None,
            "reason": "单队匹配遥测不足以构造全场反事实位置与积分概率。",
        },
        "ratings": _rating_cards(
            config,
            clean_laps_by_driver={
                str(driver): int(count)
                for driver, count in team_eligible_keys.groupby("driver_acronym", observed=True).size().items()
            },
            matched_pairs=int(len(matches)),
            observed_episode_rows=int(len(team_episode_records)),
        ),
        "rating_release_gate": {
            "historical_core_passed": objective_evaluation.get("core_historical_passed"),
            "full_driver_card_ready": objective_evaluation.get("full_driver_card_ready"),
            "season_2026_confirmatory": season_2026_gate,
            "ability_ovr_published": False,
            "event_delivery_published": False,
            "race_value_score_published": False,
        },
        "boundaries": dict(config["boundaries"]),
        "claim_contract": {
            "module_boundaries": list(TELEMETRY_CLAIM_BOUNDARIES),
            "external_condition_matched_gap": (
                "控制可观测外部条件后仍保留操作、策略与未观测状态的直接差。"
            ),
            "input_adjusted_response_gap": (
                "再控制入段速度、油门、制动、挡位和 RPM 后的事件内统计残差；不是因果效应。"
            ),
        },
    }
    return _finite(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    config = base.load_json(args.config)
    report = run(config)
    if not args.no_write:
        output = ROOT / config["outputs"]["result"]
        summary = ROOT / config["outputs"]["summary"]
        base.atomic_json(output, report)
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(_report_summary(report), encoding="utf-8")
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
