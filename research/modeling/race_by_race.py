"""全量逐站、赛季等权、stint 留出验证与 2026 探索性更新工具。"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd


def validate_race_by_race_config(config: Mapping[str, Any]) -> None:
    """拒绝会把同场描述伪装成预测或把 2026 小样本正式发布的配置。"""

    if bool(config["boundaries"]["absolute_qualifying_race_time_merge"]):
        raise ValueError("排位与正赛绝对圈时不能直接合并。")
    if not bool(config["boundaries"]["fastest_lap_is_event_local"]):
        raise ValueError("最快圈比较必须限定在同一分站。")
    if bool(config["boundaries"]["same_event_metrics_are_predictive_validation"]):
        raise ValueError("同场指标不能声明为预测验证。")
    stint = config["stint_validation"]
    if int(stint["minimum_clean_laps"]) < 2 * int(stint["minimum_half_laps"]):
        raise ValueError("stint 最小圈数必须容纳两个留出半段。")
    if int(stint["bootstrap_repetitions"]) < 100:
        raise ValueError("stint 斜率 bootstrap 至少需要 100 次。")
    rolling = config["rolling_rank_validation"]
    if not 0.0 < float(rolling["exponential_update_alpha"]) <= 1.0:
        raise ValueError("滚动更新 alpha 必须位于 (0, 1]。")
    if not 0.0 <= float(rolling["season_boundary_carry"]) <= 1.0:
        raise ValueError("跨赛季状态继承必须位于 [0, 1]。")
    projection = config["projection_2026"]
    if (
        int(projection["current_local_events"])
        < int(projection["minimum_events_for_confirmatory_release"])
        and bool(config["boundaries"]["2026_confirmatory_release_allowed_below_gate"])
    ):
        raise ValueError("2026 数据未过门槛时不能允许确认性发布。")


def _stable_seed(base_seed: int, *parts: object) -> int:
    token = "|".join(str(part) for part in parts).encode("utf-8")
    digest = int.from_bytes(hashlib.sha256(token).digest()[:4], "big")
    return int((int(base_seed) + digest) % (2**32 - 1))


def _median_mad(values: Sequence[float]) -> tuple[float | None, float | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return None, None
    median = float(np.median(array))
    mad = float(1.4826 * np.median(np.abs(array - median)))
    return median, mad


def _rank(values: Mapping[str, float], higher_is_better: bool) -> dict[str, int]:
    ordered = sorted(
        values,
        key=lambda key: (-(values[key]) if higher_is_better else values[key], key),
    )
    return {key: index + 1 for index, key in enumerate(ordered)}


def rank_spearman(first: Mapping[str, int], second: Mapping[str, int]) -> float | None:
    """在共同实体上计算秩相关；少于 2 个实体时返回空。"""

    keys = sorted(set(first) & set(second))
    if len(keys) < 2:
        return None
    left = pd.Series([float(first[key]) for key in keys])
    right = pd.Series([float(second[key]) for key in keys])
    value = left.corr(right, method="spearman")
    return float(value) if pd.notna(value) else None


def telemetry_source_files(
    schema_root: Path, years: Sequence[int], session: str
) -> tuple[list[Path], list[dict[str, Any]]]:
    """从规范 manifest 选择分区，不靠目录猜测数据身份。"""

    files: list[Path] = []
    sources: list[dict[str, Any]] = []
    for year in sorted(int(value) for value in years):
        manifests = sorted((schema_root / f"year={year}").glob("commit=*/manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError(f"{year} 应且仅应有一个规范 manifest，实际为 {len(manifests)}。")
        import json

        manifest_path = manifests[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        selected = [row for row in manifest["partitions"] if row["session"] == session]
        for row in selected:
            path = manifest_path.parent / row["parquet_path"]
            if not path.is_file():
                raise FileNotFoundError(path)
            files.append(path)
        sources.append(
            {
                "year": year,
                "source_commit": manifest["source_commit"],
                "manifest": manifest_path.relative_to(schema_root).as_posix(),
                "selected_partitions": len(selected),
                "selected_rows": int(sum(int(row["rows"]) for row in selected)),
            }
        )
    return files, sources


def extract_telemetry_lap_metrics(
    files: Sequence[Path], reporting_teams: Sequence[str]
) -> pd.DataFrame:
    """按逐圈距离/圈时计算采样频率无关的平均速度及质量审计列。"""

    frames: list[pd.DataFrame] = []
    connection = duckdb.connect()
    try:
        connection.execute("SET threads TO 4")
        placeholders = ",".join("?" for _ in reporting_teams)
        query = f"""
            SELECT
                CAST(year AS INTEGER) AS year,
                replace(meeting, '_', ' ') AS meeting_name,
                arg_min(driver_acronym, sample_index) AS driver_id,
                arg_min(team, sample_index) AS team_name,
                CAST(lap AS INTEGER) AS lap_number,
                CAST(arg_min(lap_duration, sample_index) AS DOUBLE) AS lap_duration,
                CAST(max(distance) - min(distance) AS DOUBLE) AS distance_metres,
                CAST(3.6 * (max(distance) - min(distance)) /
                    nullif(arg_min(lap_duration, sample_index), 0) AS DOUBLE)
                    AS average_speed_kph,
                CAST(avg(speed) AS DOUBLE) AS sample_mean_speed_kph,
                CAST(stddev_pop(speed) AS DOUBLE) AS within_lap_speed_sd_kph,
                CAST(max(speed) AS DOUBLE) AS maximum_speed_kph,
                count(*) AS telemetry_samples,
                bool_and(coalesce(is_accurate, false)) AS telemetry_accurate,
                bool_or(coalesce(deleted, false)) AS telemetry_deleted,
                arg_min(source_commit, sample_index) AS source_commit
            FROM read_parquet(?, hive_partitioning=false)
            WHERE team IN ({placeholders})
            GROUP BY year, meeting, driver_number, lap
            ORDER BY year, meeting, driver_number, lap
        """
        for path in files:
            frames.append(
                connection.execute(query, [str(path), *reporting_teams]).fetch_df()
            )
    finally:
        connection.close()
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    group = ["year", "meeting_name"]
    result["event_distance_reference_metres"] = result.groupby(group)[
        "distance_metres"
    ].transform("median")
    result["distance_coverage_ratio"] = (
        result["distance_metres"] / result["event_distance_reference_metres"]
    )
    result["speed_formula_relative_gap"] = (
        (result["average_speed_kph"] - result["sample_mean_speed_kph"]).abs()
        / result["average_speed_kph"].abs().clip(lower=1.0)
    )
    return result


def attach_telemetry_quality(
    lap_detail: pd.DataFrame,
    telemetry: pd.DataFrame,
    settings: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """只把与正式清洁圈键完全匹配的遥测指标接入模型圈级解释。"""

    keys = ["year", "meeting_name", "driver_id", "team_name", "lap_number"]
    columns = keys + [
        "distance_metres",
        "average_speed_kph",
        "sample_mean_speed_kph",
        "within_lap_speed_sd_kph",
        "maximum_speed_kph",
        "telemetry_samples",
        "telemetry_accurate",
        "telemetry_deleted",
        "event_distance_reference_metres",
        "distance_coverage_ratio",
        "speed_formula_relative_gap",
    ]
    merged = lap_detail.merge(telemetry[columns], how="left", on=keys, validate="one_to_one")
    available = merged["average_speed_kph"].notna()
    quality = (
        available
        & merged["distance_coverage_ratio"].between(
            float(settings["minimum_distance_coverage_ratio"]),
            float(settings["maximum_distance_coverage_ratio"]),
        )
        & (merged["telemetry_samples"] >= int(settings["minimum_samples_per_lap"]))
        & (
            merged["speed_formula_relative_gap"]
            <= float(settings["maximum_relative_gap_to_sample_mean"])
        )
    )
    merged["average_speed_quality_ok"] = quality
    merged.loc[~quality, "average_speed_kph"] = np.nan
    audit = {
        "model_clean_laps": int(len(merged)),
        "telemetry_key_matches": int(available.sum()),
        "telemetry_key_match_rate": float(available.mean()) if len(merged) else None,
        "average_speed_quality_ok": int(quality.sum()),
        "average_speed_quality_rate": float(quality.mean()) if len(merged) else None,
        "formula": settings["average_speed_formula"],
        "note": "平均速度按逐圈实际覆盖距离除以圈时计算；采样速度均值仅作质量审计。",
    }
    return merged, audit


def validate_stints(
    race_laps: pd.DataFrame,
    existing_stints: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[tuple[int, str], dict[str, Any]], dict[str, Any]]:
    """为每个 stint 增加早段拟合到晚段、奇偶圈和斜率 bootstrap 验证。"""

    existing = {
        (
            int(row["year"]),
            str(row["meeting_name"]),
            str(row["driver"]),
            str(row["team"]),
            int(row["stint_number"]),
            str(row["compound"]),
        ): copy.deepcopy(dict(row))
        for row in existing_stints
    }
    minimum = int(settings["minimum_clean_laps"])
    minimum_half = int(settings["minimum_half_laps"])
    minimum_span = float(settings["minimum_tyre_age_span"])
    repetitions = int(settings["bootstrap_repetitions"])
    base_seed = int(settings["bootstrap_seed"])
    interval = float(settings["slope_interval"])
    lower_q = (1.0 - interval) / 2.0
    upper_q = 1.0 - lower_q
    keys = [
        "year",
        "meeting_name",
        "driver_id",
        "team_name",
        "stint_number",
        "compound",
    ]
    output: list[dict[str, Any]] = []
    for group_key, group in race_laps.groupby(keys, observed=True, sort=True):
        year, meeting, driver, team, stint_number, compound = group_key
        lookup = (
            int(year), str(meeting), str(driver), str(team), int(stint_number), str(compound)
        )
        row = existing.get(
            lookup,
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "driver": str(driver),
                "team": str(team),
                "stint_number": int(stint_number),
                "compound": str(compound),
                "clean_laps": int(len(group)),
            },
        )
        ordered = group.sort_values(["lap_number", "tyre_age"])
        x = pd.to_numeric(ordered["tyre_age"], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(ordered["model_residual_ms"], errors="coerce").to_numpy(
            dtype=float
        )
        finite = np.isfinite(x) & np.isfinite(y)
        x = x[finite]
        y = y[finite]
        n = len(y)
        row.update(
            {
                "validation_status": "insufficient_laps",
                "early_fit_late_holdout_mae_ms": None,
                "early_fit_late_holdout_bias_ms": None,
                "odd_even_pace_delta_ms": None,
                "degradation_bootstrap_ci80_low": None,
                "degradation_bootstrap_ci80_high": None,
                "degradation_slope_positive_probability": None,
                "degradation_slope_sign_stable": None,
            }
        )
        if n >= minimum:
            odd = y[np.arange(n) % 2 == 0]
            even = y[np.arange(n) % 2 == 1]
            if len(odd) and len(even):
                row["odd_even_pace_delta_ms"] = float(
                    abs(np.median(odd) - np.median(even))
                )
            split = n // 2
            early_x, early_y = x[:split], y[:split]
            late_x, late_y = x[split:], y[split:]
            if (
                len(early_y) >= minimum_half
                and len(late_y) >= minimum_half
                and np.ptp(early_x) >= minimum_span
            ):
                slope, intercept = np.polyfit(early_x, early_y, 1)
                error = late_y - (slope * late_x + intercept)
                row["early_fit_late_holdout_mae_ms"] = float(np.mean(np.abs(error)))
                row["early_fit_late_holdout_bias_ms"] = float(np.mean(error))
                row["validation_status"] = "usable_holdout"
            elif row["odd_even_pace_delta_ms"] is not None:
                row["validation_status"] = "usable_split_only"
            if np.ptp(x) >= minimum_span:
                rng = np.random.default_rng(
                    _stable_seed(base_seed, year, meeting, driver, stint_number)
                )
                slopes: list[float] = []
                for _ in range(repetitions):
                    indices = rng.integers(0, n, size=n)
                    sample_x = x[indices]
                    if np.ptp(sample_x) < minimum_span:
                        continue
                    slopes.append(float(np.polyfit(sample_x, y[indices], 1)[0]))
                if slopes:
                    values = np.asarray(slopes)
                    low = float(np.quantile(values, lower_q))
                    high = float(np.quantile(values, upper_q))
                    row["degradation_bootstrap_ci80_low"] = low
                    row["degradation_bootstrap_ci80_high"] = high
                    row["degradation_slope_positive_probability"] = float(
                        np.mean(values > 0.0)
                    )
                    row["degradation_slope_sign_stable"] = bool(low > 0.0 or high < 0.0)
        row["validation_interpretation"] = settings["warning"]
        output.append(row)

    event_summaries: dict[tuple[int, str], dict[str, Any]] = {}
    output_frame = pd.DataFrame(output)
    for (year, meeting), group in output_frame.groupby(
        ["year", "meeting_name"], observed=True, sort=True
    ):
        holdout = pd.to_numeric(
            group["early_fit_late_holdout_mae_ms"], errors="coerce"
        ).dropna()
        split = pd.to_numeric(group["odd_even_pace_delta_ms"], errors="coerce").dropna()
        stable = group["degradation_slope_sign_stable"].dropna()
        driver_spread: list[float] = []
        for _, driver_group in group.groupby("driver", observed=True):
            usable = pd.to_numeric(
                driver_group["pace_residual_ms_p50"], errors="coerce"
            ).dropna()
            if len(usable) >= 2:
                _, spread = _median_mad(usable.to_numpy(dtype=float))
                if spread is not None:
                    driver_spread.append(spread)
        event_summaries[(int(year), str(meeting))] = {
            "stints_total": int(len(group)),
            "holdout_eligible_stints": int(len(holdout)),
            "median_early_fit_late_holdout_mae_ms": (
                float(holdout.median()) if len(holdout) else None
            ),
            "split_eligible_stints": int(len(split)),
            "median_odd_even_pace_delta_ms": float(split.median()) if len(split) else None,
            "bootstrap_slope_stable_stints": int(stable.astype(bool).sum()) if len(stable) else 0,
            "bootstrap_slope_assessed_stints": int(len(stable)),
            "median_within_driver_across_stint_mad_ms": (
                float(np.median(driver_spread)) if driver_spread else None
            ),
            "boundary": settings["warning"],
        }
    holdout_all = pd.to_numeric(
        output_frame["early_fit_late_holdout_mae_ms"], errors="coerce"
    ).dropna()
    split_all = pd.to_numeric(output_frame["odd_even_pace_delta_ms"], errors="coerce").dropna()
    stable_all = output_frame["degradation_slope_sign_stable"].dropna()
    summary = {
        "stints_total": int(len(output_frame)),
        "holdout_eligible_stints": int(len(holdout_all)),
        "median_early_fit_late_holdout_mae_ms": (
            float(holdout_all.median()) if len(holdout_all) else None
        ),
        "split_eligible_stints": int(len(split_all)),
        "median_odd_even_pace_delta_ms": (
            float(split_all.median()) if len(split_all) else None
        ),
        "bootstrap_slope_assessed_stints": int(len(stable_all)),
        "bootstrap_slope_stable_rate": (
            float(stable_all.astype(bool).mean()) if len(stable_all) else None
        ),
        "boundary": settings["warning"],
    }
    return output, event_summaries, summary


def standardize_historical_events(
    base_events: Sequence[Mapping[str, Any]],
    race_laps: pd.DataFrame,
    stints: Sequence[Mapping[str, Any]],
    stint_event_summaries: Mapping[tuple[int, str], Mapping[str, Any]],
    schedule: Mapping[str, Sequence[str]],
    classification_lookup: Mapping[tuple[int, str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把每站模型、同场最快圈、速度/稳定性和所有 stint 放入统一合同。"""

    base_lookup = {
        (int(event["year"]), str(event["meeting_name"])): event for event in base_events
    }
    stint_lookup: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in stints:
        stint_lookup.setdefault((int(row["year"]), str(row["meeting_name"])), []).append(
            dict(row)
        )
    lap_lookup = {
        (int(year), str(meeting)): group
        for (year, meeting), group in race_laps.groupby(
            ["year", "meeting_name"], observed=True, sort=False
        )
    }
    output: list[dict[str, Any]] = []
    for year_text, meetings in schedule.items():
        year = int(year_text)
        for round_number, meeting in enumerate(meetings, 1):
            key = (year, str(meeting))
            if key not in base_lookup or key not in lap_lookup:
                output.append(
                    {
                        "year": year,
                        "round": round_number,
                        "meeting_name": str(meeting),
                        "data_status": "unavailable_after_cleaning",
                        "data_limit": "没有达到统一干地、准确、绿旗清洁圈门槛；不插值、不借用别站。",
                        "cars": [],
                        "drivers": [],
                        "stints": [],
                    }
                )
                continue
            event = copy.deepcopy(dict(base_lookup[key]))
            event_laps = lap_lookup[key]
            team_fastest = event_laps.groupby("team_name")["lap_duration"].min().to_dict()
            driver_fastest = event_laps.groupby("driver_id")["lap_duration"].min().to_dict()
            team_fastest_rank = _rank(team_fastest, higher_is_better=False)
            driver_fastest_rank = _rank(driver_fastest, higher_is_better=False)
            speed = event_laps.loc[event_laps["average_speed_kph"].notna()].copy()
            team_speed = speed.groupby("team_name")["average_speed_kph"].median().to_dict()
            speed_center = float(np.mean(list(team_speed.values()))) if team_speed else np.nan
            team_model_rank = {
                str(row["team"]): int(row["weekend_ceiling_rank"])
                for row in event["cars"]
            }
            team_result_rank = {
                str(row["team"]): int(row["classification_proxy_rank_four_team"])
                for row in event["cars"]
                if row.get("classification_proxy_rank_four_team") is not None
            }
            for car in event["cars"]:
                team = str(car["team"])
                group = event_laps.loc[event_laps["team_name"] == team]
                residual_median, residual_mad = _median_mad(group["model_residual_ms"])
                car.update(
                    {
                        "same_event_fastest_clean_lap_seconds": float(team_fastest[team]),
                        "same_event_fastest_clean_lap_rank_four_team": int(
                            team_fastest_rank[team]
                        ),
                        "average_speed_kph_p50": (
                            float(team_speed[team]) if team in team_speed else None
                        ),
                        "average_speed_advantage_kph": (
                            float(team_speed[team] - speed_center)
                            if team in team_speed
                            else None
                        ),
                        "model_residual_ms_p50": residual_median,
                        "consistency_mad_ms": residual_mad,
                    }
                )
            for driver in event["drivers"]:
                driver_id = str(driver["driver"])
                group = event_laps.loc[event_laps["driver_id"] == driver_id]
                driver_speed = group["average_speed_kph"].dropna()
                driver.update(
                    {
                        "same_event_fastest_clean_lap_seconds": float(
                            driver_fastest[driver_id]
                        ),
                        "same_event_fastest_clean_lap_rank_four_team": int(
                            driver_fastest_rank[driver_id]
                        ),
                        "average_speed_kph_p50": (
                            float(driver_speed.median()) if len(driver_speed) else None
                        ),
                    }
                )
            event.update(
                {
                    "round": round_number,
                    "data_status": "available",
                    "same_event_comparison": {
                        "model_ceiling_vs_fastest_lap_spearman": rank_spearman(
                            team_model_rank, team_fastest_rank
                        ),
                        "model_ceiling_vs_classification_spearman": rank_spearman(
                            team_model_rank, team_result_rank
                        ),
                        "model_ceiling_top_team": min(team_model_rank, key=team_model_rank.get),
                        "fastest_clean_lap_team": min(
                            team_fastest_rank, key=team_fastest_rank.get
                        ),
                        "classification_proxy_top_team": (
                            min(team_result_rank, key=team_result_rank.get)
                            if team_result_rank
                            else None
                        ),
                        "overall_winner_driver_proxy": (
                            classification_lookup.get(key, {}).get("winner_driver_proxy")
                            if classification_lookup is not None
                            else None
                        ),
                        "overall_winner_team_proxy": (
                            classification_lookup.get(key, {}).get("winner_team_proxy")
                            if classification_lookup is not None
                            else None
                        ),
                        "overall_fastest_lap_driver_proxy": (
                            classification_lookup.get(key, {}).get(
                                "fastest_lap_driver_proxy"
                            )
                            if classification_lookup is not None
                            else None
                        ),
                        "overall_fastest_lap_team_proxy": (
                            classification_lookup.get(key, {}).get("fastest_lap_team_proxy")
                            if classification_lookup is not None
                            else None
                        ),
                        "overall_fastest_lap_seconds_proxy": (
                            classification_lookup.get(key, {}).get(
                                "fastest_lap_seconds_proxy"
                            )
                            if classification_lookup is not None
                            else None
                        ),
                        "boundary": "三列均限定本场；这是回顾性解释，不是赛前预测。",
                    },
                    "stint_validation": dict(stint_event_summaries.get(key, {})),
                    "stints": sorted(
                        stint_lookup.get(key, []),
                        key=lambda row: (str(row["driver"]), int(row["stint_number"])),
                    ),
                }
            )
            top_car = event["same_event_comparison"]["model_ceiling_top_team"]
            top_fastest = event["same_event_comparison"]["fastest_clean_lap_team"]
            top_result = event["same_event_comparison"]["classification_proxy_top_team"]
            event["conclusion"] = (
                f"模型周末上沿为 {top_car}；同场四队最快清洁圈为 {top_fastest}；"
                f"分类代理最高为 {top_result or '不可用'}。三者"
                + ("同向。" if top_car == top_fastest == top_result else "不完全同向，差异不能自动归因给车手或车辆。")
            )
            output.append(event)
    return output


def build_retrospective_validation(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """汇总三年逐站的同场三向构念检查，明确不称预测。"""

    rows: list[dict[str, Any]] = []
    for event in events:
        comparison = event.get("same_event_comparison")
        if not comparison:
            continue
        rows.append(
            {
                "year": int(event["year"]),
                "round": int(event["round"]),
                "meeting_name": str(event["meeting_name"]),
                "model_vs_fastest_spearman": comparison[
                    "model_ceiling_vs_fastest_lap_spearman"
                ],
                "model_vs_classification_spearman": comparison[
                    "model_ceiling_vs_classification_spearman"
                ],
                "model_fastest_top_match": comparison["model_ceiling_top_team"]
                == comparison["fastest_clean_lap_team"],
                "model_classification_top_match": comparison["model_ceiling_top_team"]
                == comparison["classification_proxy_top_team"],
            }
        )
    frame = pd.DataFrame(rows)
    by_year: list[dict[str, Any]] = []
    for year, group in frame.groupby("year", sort=True):
        by_year.append(
            {
                "year": int(year),
                "events": int(len(group)),
                "model_fastest_top_match_rate": float(group["model_fastest_top_match"].mean()),
                "model_classification_top_match_rate": float(
                    group["model_classification_top_match"].mean()
                ),
                "mean_model_vs_fastest_spearman": float(
                    group["model_vs_fastest_spearman"].mean()
                ),
                "mean_model_vs_classification_spearman": float(
                    group["model_vs_classification_spearman"].mean()
                ),
            }
        )
    return {
        "role": "retrospective_same_event_construct_check_not_prediction",
        "events": rows,
        "overall": {
            "events": int(len(frame)),
            "model_fastest_top_match_rate": float(frame["model_fastest_top_match"].mean()),
            "model_classification_top_match_rate": float(
                frame["model_classification_top_match"].mean()
            ),
            "mean_model_vs_fastest_spearman": float(
                frame["model_vs_fastest_spearman"].mean()
            ),
            "mean_model_vs_classification_spearman": float(
                frame["model_vs_classification_spearman"].mean()
            ),
        },
        "by_year": by_year,
    }


def build_rolling_rank_validation(
    events: Sequence[Mapping[str, Any]],
    teams: Sequence[str],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """只用先前分站状态预测下一站排名，作为透明的无泄漏基线。"""

    alpha = float(settings["exponential_update_alpha"])
    carry = float(settings["season_boundary_carry"])
    minimum = int(settings["minimum_prior_events"])
    state = {team: 0.0 for team in teams}
    prior_events = 0
    last_year: int | None = None
    rows: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda row: (int(row["year"]), int(row["round"]))):
        if event.get("data_status") != "available":
            continue
        year = int(event["year"])
        if last_year is not None and year != last_year:
            state = {team: carry * value for team, value in state.items()}
        comparison = event["same_event_comparison"]
        if prior_events >= minimum:
            predicted = _rank(state, higher_is_better=True)
            fastest = {
                str(row["team"]): int(row["same_event_fastest_clean_lap_rank_four_team"])
                for row in event["cars"]
            }
            classification = {
                str(row["team"]): int(row["classification_proxy_rank_four_team"])
                for row in event["cars"]
                if row.get("classification_proxy_rank_four_team") is not None
            }
            rows.append(
                {
                    "year": year,
                    "round": int(event["round"]),
                    "meeting_name": str(event["meeting_name"]),
                    "predicted_top_team": min(predicted, key=predicted.get),
                    "fastest_clean_lap_top_team": comparison["fastest_clean_lap_team"],
                    "classification_proxy_top_team": comparison[
                        "classification_proxy_top_team"
                    ],
                    "prediction_vs_fastest_spearman": rank_spearman(predicted, fastest),
                    "prediction_vs_classification_spearman": rank_spearman(
                        predicted, classification
                    ),
                    "prediction_fastest_top_match": min(predicted, key=predicted.get)
                    == comparison["fastest_clean_lap_team"],
                    "prediction_classification_top_match": min(predicted, key=predicted.get)
                    == comparison["classification_proxy_top_team"],
                }
            )
        observed = {
            str(row["team"]): float(row["weekend_ceiling_advantage_z"]["median"])
            for row in event["cars"]
            if row["weekend_ceiling_advantage_z"]["median"] is not None
        }
        for team in teams:
            if team in observed:
                state[team] = (1.0 - alpha) * state[team] + alpha * observed[team]
        prior_events += 1
        last_year = year
    frame = pd.DataFrame(rows)
    by_year: list[dict[str, Any]] = []
    for year, group in frame.groupby("year", sort=True):
        by_year.append(
            {
                "year": int(year),
                "events": int(len(group)),
                "fastest_top_match_rate": float(group["prediction_fastest_top_match"].mean()),
                "classification_top_match_rate": float(
                    group["prediction_classification_top_match"].mean()
                ),
                "mean_prediction_vs_fastest_spearman": float(
                    group["prediction_vs_fastest_spearman"].mean()
                ),
                "mean_prediction_vs_classification_spearman": float(
                    group["prediction_vs_classification_spearman"].mean()
                ),
            }
        )
    return {
        "role": settings["role"],
        "uses_current_event_features": False,
        "minimum_prior_events": minimum,
        "events": rows,
        "overall": {
            "events": int(len(frame)),
            "fastest_top_match_rate": float(frame["prediction_fastest_top_match"].mean()),
            "classification_top_match_rate": float(
                frame["prediction_classification_top_match"].mean()
            ),
            "mean_prediction_vs_fastest_spearman": float(
                frame["prediction_vs_fastest_spearman"].mean()
            ),
            "mean_prediction_vs_classification_spearman": float(
                frame["prediction_vs_classification_spearman"].mean()
            ),
        },
        "by_year": by_year,
    }


def build_season_summaries(
    events: Sequence[Mapping[str, Any]], teams: Sequence[str]
) -> list[dict[str, Any]]:
    """以分站等权方式汇总赛季，不让圈数多的分站获得更高权重。"""

    output: list[dict[str, Any]] = []
    years = sorted({int(event["year"]) for event in events})
    for year in years:
        year_events = [
            event
            for event in events
            if int(event["year"]) == year and event.get("data_status") == "available"
        ]
        car_rows: list[dict[str, Any]] = []
        for team in teams:
            rows = [
                next((row for row in event["cars"] if row["team"] == team), None)
                for event in year_events
            ]
            rows = [row for row in rows if row is not None]
            if not rows:
                continue
            advantages = [
                float(row["weekend_ceiling_advantage_z"]["median"])
                for row in rows
                if row["weekend_ceiling_advantage_z"]["median"] is not None
            ]
            speeds = [
                float(row["average_speed_advantage_kph"])
                for row in rows
                if row.get("average_speed_advantage_kph") is not None
            ]
            consistency = [
                float(row["consistency_mad_ms"])
                for row in rows
                if row.get("consistency_mad_ms") is not None
            ]
            car_rows.append(
                {
                    "team": team,
                    "events": int(len(rows)),
                    "event_equal_weight_weekend_advantage_z_mean": float(np.mean(advantages)),
                    "event_equal_weight_mean_model_rank": float(
                        np.mean([row["weekend_ceiling_rank"] for row in rows])
                    ),
                    "model_rank_1_events": int(
                        sum(int(row["weekend_ceiling_rank"]) == 1 for row in rows)
                    ),
                    "fastest_clean_lap_rank_1_events": int(
                        sum(
                            int(row["same_event_fastest_clean_lap_rank_four_team"]) == 1
                            for row in rows
                        )
                    ),
                    "classification_proxy_rank_1_events": int(
                        sum(row.get("classification_proxy_rank_four_team") == 1 for row in rows)
                    ),
                    "event_equal_weight_average_speed_advantage_kph_mean": (
                        float(np.mean(speeds)) if speeds else None
                    ),
                    "event_median_consistency_mad_ms": (
                        float(np.median(consistency)) if consistency else None
                    ),
                }
            )
        car_rows.sort(key=lambda row: row["event_equal_weight_mean_model_rank"])
        driver_values: dict[str, list[Mapping[str, Any]]] = {}
        for event in year_events:
            for row in event["drivers"]:
                driver_values.setdefault(str(row["driver"]), []).append(row)
        driver_rows: list[dict[str, Any]] = []
        for driver, rows in driver_values.items():
            driver_rows.append(
                {
                    "driver": driver,
                    "team": sorted({str(row["team"]) for row in rows}),
                    "events": int(len(rows)),
                    "event_equal_weight_observed_clean_pace_rank_mean": float(
                        np.mean([row["observed_clean_pace_rank_four_team"] for row in rows])
                    ),
                    "event_equal_weight_execution_loss_ms_p50": float(
                        np.median([row["typical_execution_loss_ms"] for row in rows])
                    ),
                    "event_equal_weight_consistency_mad_ms_p50": float(
                        np.median([row["consistency_mad_ms"] for row in rows])
                    ),
                    "classification_better_events": int(
                        sum(row["result_alignment"] == "result_better_than_clean_pace" for row in rows)
                    ),
                    "classification_worse_events": int(
                        sum(row["result_alignment"] == "result_worse_than_clean_pace" for row in rows)
                    ),
                }
            )
        driver_rows.sort(key=lambda row: row["event_equal_weight_observed_clean_pace_rank_mean"])
        output.append(
            {
                "year": year,
                "scheduled_events": int(
                    sum(int(event["year"]) == year for event in events)
                ),
                "available_events": int(len(year_events)),
                "aggregation_unit": "event_equal_weight",
                "cars": car_rows,
                "drivers": driver_rows,
                "conclusion": (
                    f"按分站等权，{car_rows[0]['team']} 的平均模型上沿排名最高；"
                    "最快圈与分类仅作同场外部对照，不能替代车辆—车手分离。"
                    if car_rows
                    else "没有足够数据形成赛季结论。"
                ),
            }
        )
    return output


def build_2026_exploratory_projection(
    race_laps: pd.DataFrame,
    qualifying_laps: pd.DataFrame,
    telemetry_laps: pd.DataFrame,
    bayesian_result: Mapping[str, Any],
    classification_lookup: Mapping[tuple[int, str], Mapping[str, Any]],
    teams: Sequence[str],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """规则重置：继承车手先验，车辆/交互归零，再用 2026 前四站收缩更新。"""

    race = race_laps.loc[race_laps["team_name"].isin(teams)].copy()
    qualifying = qualifying_laps.loc[qualifying_laps["team_name"].isin(teams)].copy()
    prior_year = int(settings["historical_driver_prior_year"])
    historical = {
        str(row["driver"]): row
        for row in bayesian_result["counterfactual_ratings"]["drivers"]
        if int(row["year"]) == prior_year
    }
    prior_map = {
        driver: float(row["driver_pace_advantage_z"]["mean"])
        for driver, row in historical.items()
    }
    race["driver_prior_advantage_z"] = race["driver_id"].map(prior_map).fillna(
        float(settings["new_driver_prior_mean_z"])
    )
    qualifying["driver_prior_advantage_z"] = qualifying["driver_id"].map(
        prior_map
    ).fillna(float(settings["new_driver_prior_mean_z"]))
    race["driver_neutral_vehicle_z_2026"] = (
        race["context_adjusted_z"] + race["driver_prior_advantage_z"]
    )
    qualifying["driver_neutral_vehicle_z_2026"] = (
        qualifying["context_adjusted_z"] + qualifying["driver_prior_advantage_z"]
    )
    telemetry_keys = ["year", "meeting_name", "driver_id", "team_name", "lap_number"]
    telemetry_columns = telemetry_keys + ["average_speed_kph"]
    race = race.merge(
        telemetry_laps[telemetry_columns], how="left", on=telemetry_keys, validate="one_to_one"
    )
    events: list[dict[str, Any]] = []
    event_advantages: dict[str, list[float]] = {team: [] for team in teams}
    for (year, meeting), event in race.groupby(["year", "meeting_name"], sort=True):
        q_event = qualifying.loc[
            (qualifying["year"] == year) & (qualifying["meeting_name"] == meeting)
        ]
        raw_race: dict[str, float] = {}
        raw_q: dict[str, float] = {}
        for team in teams:
            team_event = event.loc[event["team_name"] == team]
            driver_frontiers = [
                float(group["driver_neutral_vehicle_z_2026"].quantile(0.10))
                for _, group in team_event.groupby("driver_id", observed=True)
            ]
            if driver_frontiers:
                raw_race[team] = float(np.median(driver_frontiers))
            q_team = q_event.loc[q_event["team_name"] == team]
            q_frontiers = [
                float(group["driver_neutral_vehicle_z_2026"].quantile(0.20))
                for _, group in q_team.groupby("driver_id", observed=True)
            ]
            if q_frontiers:
                raw_q[team] = float(np.median(q_frontiers))
        available = sorted(set(raw_race) & set(teams))
        race_center = float(np.mean([raw_race[team] for team in available]))
        race_advantage = {team: race_center - raw_race[team] for team in available}
        q_available = sorted(set(raw_q) & set(teams))
        q_center = float(np.mean([raw_q[team] for team in q_available])) if q_available else 0.0
        q_advantage = {team: q_center - raw_q[team] for team in q_available}
        weekend_advantage = {
            team: (
                0.65 * q_advantage[team] + 0.35 * race_advantage[team]
                if team in q_advantage
                else race_advantage[team]
            )
            for team in available
        }
        ranks = _rank(weekend_advantage, higher_is_better=True)
        fastest = event.groupby("team_name")["lap_duration"].min().to_dict()
        fastest_rank = _rank(fastest, higher_is_better=False)
        classification = classification_lookup.get((int(year), str(meeting)), {})
        result_rank = classification.get("team_rank", {})
        car_rows: list[dict[str, Any]] = []
        for team in available:
            team_event = event.loc[event["team_name"] == team]
            speeds = team_event["average_speed_kph"].dropna()
            event_advantages[team].append(float(weekend_advantage[team]))
            car_rows.append(
                {
                    "team": team,
                    "weekend_ceiling_advantage_z": float(weekend_advantage[team]),
                    "weekend_ceiling_rank": int(ranks[team]),
                    "race_ceiling_advantage_z": float(race_advantage[team]),
                    "qualifying_ceiling_advantage_z": (
                        float(q_advantage[team]) if team in q_advantage else None
                    ),
                    "same_event_fastest_clean_lap_rank_four_team": int(fastest_rank[team]),
                    "classification_proxy_rank_four_team": result_rank.get(team),
                    "average_speed_kph_p50": float(speeds.median()) if len(speeds) else None,
                    "race_clean_laps": int(len(team_event)),
                    "qualifying_accurate_laps": int(len(q_event.loc[q_event["team_name"] == team])),
                }
            )
        events.append(
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "data_status": "available_exploratory",
                "available_teams": available,
                "missing_teams_after_cleaning": [
                    team for team in teams if team not in available
                ],
                "rank_scope": "available reporting teams after uniform cleaning",
                "cars": sorted(car_rows, key=lambda row: row["weekend_ceiling_rank"]),
                "comparison_boundary": "2026 同场值是赛季初回顾性更新，不是赛季前预测。",
            }
        )
    prior_equivalent = float(settings["car_state_prior_event_equivalent"])
    team_projection: list[dict[str, Any]] = []
    time_forward_2025 = bayesian_result["time_forward_validation"]["years"]["2025"][
        "bayesian_joint"
    ]
    calibrated_width = float(
        time_forward_2025.get(
            "calibrated_80_mean_width_z",
            time_forward_2025["interval_80_mean_width_z"],
        )
    )
    predictive_half_width = calibrated_width / 2.0
    for team in teams:
        values = np.asarray(event_advantages[team], dtype=float)
        n = len(values)
        weight = n / (n + prior_equivalent) if n else 0.0
        mean = float(np.mean(values)) if n else 0.0
        rng = np.random.default_rng(_stable_seed(20260719, "2026", team))
        boot = np.asarray(
            [weight * float(np.mean(rng.choice(values, size=n, replace=True))) for _ in range(2000)]
            if n
            else [0.0]
        )
        shrunken = weight * mean
        team_projection.append(
            {
                "team": team,
                "observed_events": n,
                "raw_event_equal_weight_advantage_z": mean,
                "reset_prior_weight": float(1.0 - weight),
                "shrunken_car_state_advantage_z": shrunken,
                "bootstrap_ci80_low_z": float(np.quantile(boot, 0.10)),
                "bootstrap_ci80_high_z": float(np.quantile(boot, 0.90)),
                "predictive_band_80_low_z": float(shrunken - predictive_half_width),
                "predictive_band_80_high_z": float(shrunken + predictive_half_width),
            }
        )
    team_projection.sort(key=lambda row: row["shrunken_car_state_advantage_z"], reverse=True)
    for rank, row in enumerate(team_projection, 1):
        row["exploratory_rank"] = rank
    observed_events = len(events)
    gate = observed_events >= int(settings["minimum_events_for_confirmatory_release"])
    drivers = sorted(set(race["driver_id"]))
    driver_priors = [
        {
            "driver": driver,
            "prior_source": "2025_posterior" if driver in historical else "new_driver_zero_prior",
            "prior_advantage_z": float(prior_map.get(driver, settings["new_driver_prior_mean_z"])),
            "prior_ci80_low_z": (
                float(historical[driver]["driver_pace_advantage_z"]["ci80_low"])
                if driver in historical
                else -float(settings["new_driver_prior_sd_z"])
            ),
            "prior_ci80_high_z": (
                float(historical[driver]["driver_pace_advantage_z"]["ci80_high"])
                if driver in historical
                else float(settings["new_driver_prior_sd_z"])
            ),
        }
        for driver in drivers
    ]
    return {
        "role": settings["role"],
        "observed_events": observed_events,
        "minimum_events_for_confirmatory_release": int(
            settings["minimum_events_for_confirmatory_release"]
        ),
        "confirmatory_gate_passed": gate,
        "publication_status": "exploratory_only" if not gate else "eligible_for_recheck",
        "reset_contract": {
            "driver_prior": "继承 2025 后验；新车手使用零均值宽先验。",
            "car_state": "归零后按 prior event equivalent 强收缩更新。",
            "driver_team_interaction": "归零，不从 2025 继承。",
        },
        "events": events,
        "team_projection": team_projection,
        "predictive_band_calibration": {
            "source": "2025 locked driver-event Bayesian time-forward calibrated 80% mean width",
            "mean_width_z": calibrated_width,
            "half_width_z": predictive_half_width,
            "interpretation": "bootstrap 区间描述四站估计稳定性；预测带另加入历史时间前推误差。",
        },
        "driver_priors": driver_priors,
        "warning": "只有四站本地数据，排名区间未含完整的规则变化、升级、可靠性与未来赛道误差。",
    }
