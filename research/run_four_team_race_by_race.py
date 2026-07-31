#!/usr/bin/env python3
"""生成 2023–2025 全量逐站报告、stint 验证及 2026 探索性更新 v5。"""

from __future__ import annotations

import argparse
import gzip
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import build_v4_frozen_data as frozen
import run_experiments as base
from build_qualifying_frozen_data import prepare_qualifying_laps
from modeling.race_by_race import (
    attach_telemetry_quality,
    build_2026_exploratory_projection,
    build_retrospective_validation,
    build_rolling_rank_validation,
    build_season_summaries,
    extract_telemetry_lap_metrics,
    standardize_historical_events,
    telemetry_source_files,
    validate_race_by_race_config,
    validate_stints,
)
from modeling.seasonal_attribution import (
    CONTEXT_BLOCKS,
    fit_seasonal_model,
    local_live_timing_reference,
    prepare_season_frame,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "four_team_race_by_race_v5.json"


def _write_deterministic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0
        ) as handle:
            frame.to_csv(handle, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(path)


def _load_historical_inputs(
    config: Mapping[str, Any]
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    event_result = base.load_json(ROOT / config["event_stint_result"])
    lap_manifest_path = ROOT / config["event_stint_lap_manifest"]
    lap_manifest = base.load_json(lap_manifest_path)
    lap_path = ROOT / lap_manifest["file"]["path"]
    if base.file_sha256(lap_path) != lap_manifest["file"]["sha256"]:
        raise RuntimeError("v4 圈级解释文件哈希与 manifest 不一致。")
    lap_detail = pd.read_csv(lap_path, compression="gzip")
    seasonal = base.load_json(ROOT / config["seasonal_result"])
    bayesian = base.load_json(ROOT / config["bayesian_result"])
    season_config = base.load_json(ROOT / config["season_order_config"])
    return event_result, lap_detail, seasonal, bayesian, season_config


def _classification_lookup(
    references: Sequence[Mapping[str, Any]], teams: Sequence[str]
) -> dict[tuple[int, str], dict[str, Any]]:
    output: dict[tuple[int, str], dict[str, Any]] = {}
    for reference in references:
        year = int(reference["year"])
        for event in reference["events"]:
            if event["session_name"] != "Race":
                continue
            drivers = [row for row in event["drivers"] if row["team"] in teams]
            drivers.sort(key=lambda row: int(row["position"]))
            team_rows = [row for row in event["teams"] if row["team"] in teams]
            team_rows.sort(
                key=lambda row: (-float(row["points_proxy"]), int(row["best_position"]))
            )
            fastest_lap = min(
                (
                    row
                    for row in event["drivers"]
                    if row.get("fastest_lap") is not None
                ),
                key=lambda row: float(row["fastest_lap"]),
                default=None,
            )
            output[(year, str(event["meeting_name"]))] = {
                "team_rank": {
                    str(row["team"]): index + 1 for index, row in enumerate(team_rows)
                },
                "driver_rank": {
                    str(row["driver"]): index + 1 for index, row in enumerate(drivers)
                },
                "winner_driver_proxy": min(
                    event["drivers"], key=lambda row: int(row["position"])
                )["driver"],
                "winner_team_proxy": event["winner_team_proxy"],
                "fastest_lap_driver_proxy": (
                    fastest_lap["driver"] if fastest_lap is not None else None
                ),
                "fastest_lap_team_proxy": (
                    fastest_lap["team"] if fastest_lap is not None else None
                ),
                "fastest_lap_seconds_proxy": (
                    float(fastest_lap["fastest_lap"])
                    if fastest_lap is not None
                    else None
                ),
            }
    return output


def _prepare_2026(
    config: Mapping[str, Any],
    season_config: Mapping[str, Any],
    aliases: Mapping[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    schema_root = ROOT / config["telemetry"]["schema_root"]
    base_cleaning_config = base.load_json(
        ROOT / "research" / "configs" / "2024_train_2025_validate.json"
    )
    race_files, race_provenance = frozen.source_catalog(schema_root, [2026], ["Race"])
    raw_race = frozen.extract_laps(race_files)
    clean_race, race_audit = frozen.clean_laps(raw_race, base_cleaning_config)
    q_files, q_provenance = frozen.source_catalog(schema_root, [2026], ["Qualifying"])
    raw_q = frozen.extract_laps(q_files)
    q_settings = {
        "dry_compounds": ["SOFT", "MEDIUM", "HARD"],
        "minimum_lap_duration_seconds": 45.0,
        "maximum_lap_duration_seconds": 200.0,
        "minimum_accurate_laps_per_session": 20,
    }
    clean_q, q_audit = prepare_qualifying_laps(raw_q, q_settings)
    order = {"2026": list(config["projection_2026"]["season_order"])}
    race = prepare_season_frame(clean_race, aliases, order, "Race")
    qualifying = prepare_season_frame(clean_q, aliases, order, "Qualifying")
    penalties = next(
        row for row in season_config["candidate_penalties"] if row["id"] == "balanced_v1"
    )
    penalties = {key: float(value) for key, value in penalties.items() if key != "id"}
    robust = season_config["robust_fit"]
    race_fit = fit_seasonal_model(
        race,
        penalties,
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(robust["irls_iterations"]),
        huber_k=float(robust["huber_k"]),
    )
    qualifying_fit = fit_seasonal_model(
        qualifying,
        penalties,
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(robust["irls_iterations"]),
        huber_k=float(robust["huber_k"]),
    )
    race["context_adjusted_z"] = race["target_log_pct"] - race_fit.predict(race)
    qualifying["context_adjusted_z"] = (
        qualifying["target_log_pct"] - qualifying_fit.predict(qualifying)
    )
    return race, qualifying, {
        "race_provenance": race_provenance,
        "qualifying_provenance": q_provenance,
        "race_cleaning_audit": race_audit,
        "qualifying_cleaning_audit": q_audit,
        "race_clean_laps": int(len(race)),
        "qualifying_accurate_laps": int(len(qualifying)),
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return f"{float(value):.{digits}f}"


def _write_season_report(
    year: int,
    events: Sequence[Mapping[str, Any]],
    season: Mapping[str, Any],
    retrospective: Mapping[str, Any],
    rolling: Mapping[str, Any],
    path: Path,
) -> None:
    lines = [
        f"# {year} 四队逐站车辆—车手—stint 归因 v5",
        "",
        "口径：每站独立列出模型周末上沿、同场最快清洁圈、同场分类代理；三者不会跨站或跨年混用。平均速度为逐圈距离/圈时，稳定性为模型残差 MAD。",
        "",
    ]
    for event in events:
        lines.extend([f"## R{event['round']} {event['meeting_name']}", ""])
        if event.get("data_status") != "available":
            lines.extend([f"- 数据状态：`{event['data_status']}`。{event['data_limit']}", ""])
            continue
        lines.extend(
            [
                f"- 可用正赛清洁圈：{event['race_clean_laps_four_team']}；排位准确推圈：{event['qualifying_accurate_laps_four_team']}。",
                f"- 结论：{event['conclusion']}",
                f"- 全场本场代理：冠军 {event['same_event_comparison'].get('overall_winner_driver_proxy') or '—'} / {event['same_event_comparison'].get('overall_winner_team_proxy') or '—'}；最快圈 {event['same_event_comparison'].get('overall_fastest_lap_driver_proxy') or '—'} / {event['same_event_comparison'].get('overall_fastest_lap_team_proxy') or '—'}（{_fmt(event['same_event_comparison'].get('overall_fastest_lap_seconds_proxy'), 3)} s）。",
                "",
                "### 车辆",
                "",
                "| 车队 | 模型上沿名次 | 排位上沿 z | 正赛上沿 z | 同场最快圈名次 | 分类名次 | 平均速度 km/h | 相对四队 km/h | 稳定性 MAD ms | 90s 上沿收益 ms | 可信度 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in sorted(event["cars"], key=lambda item: item["weekend_ceiling_rank"]):
            q_summary = row.get("qualifying_ceiling_advantage_z")
            race_summary = row.get("race_ceiling_advantage_z")
            lines.append(
                f"| {row['team']} | {row['weekend_ceiling_rank']} | "
                f"{_fmt(q_summary.get('median') if isinstance(q_summary, dict) else None, 3)} | "
                f"{_fmt(race_summary.get('median') if isinstance(race_summary, dict) else None, 3)} | "
                f"{row['same_event_fastest_clean_lap_rank_four_team']} | "
                f"{_fmt(row.get('classification_proxy_rank_four_team'), 0)} | "
                f"{_fmt(row.get('average_speed_kph_p50'))} | "
                f"{_fmt(row.get('average_speed_advantage_kph'), 3)} | "
                f"{_fmt(row.get('consistency_mad_ms'), 1)} | "
                f"{_fmt(row.get('weekend_ceiling_advantage_90s_ms'), 1)} | "
                f"{row['confidence']} |"
            )
        lines.extend(
            [
                "",
                "### 车手",
                "",
                "| 车手 | 车队 | 清洁圈 | 实际干净配速名次 | 模型组合名次 | 同场最快圈名次 | 分类名次 | 平均速度 km/h | 执行损失 ms | 稳定性 MAD ms |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in sorted(
            event["drivers"], key=lambda item: item["observed_clean_pace_rank_four_team"]
        ):
            lines.append(
                f"| {row['driver']} | {row['team']} | {row['clean_laps']} | "
                f"{row['observed_clean_pace_rank_four_team']} | "
                f"{row['model_expected_combination_rank_four_team']} | "
                f"{row['same_event_fastest_clean_lap_rank_four_team']} | "
                f"{_fmt(row.get('classification_proxy_rank_four_team'), 0)} | "
                f"{_fmt(row.get('average_speed_kph_p50'))} | "
                f"{_fmt(row.get('typical_execution_loss_ms'), 1)} | "
                f"{_fmt(row.get('consistency_mad_ms'), 1)} |"
            )
        validation = event.get("stint_validation", {})
        lines.extend(
            [
                "",
                "### Stint 与方法验证",
                "",
                f"- 可做早段→晚段留出的 stint：{validation.get('holdout_eligible_stints', 0)}/{validation.get('stints_total', 0)}；中位晚段 MAE={_fmt(validation.get('median_early_fit_late_holdout_mae_ms'), 1)} ms。",
                f"- 可做奇偶圈复验的 stint：{validation.get('split_eligible_stints', 0)}；中位奇偶圈差={_fmt(validation.get('median_odd_even_pace_delta_ms'), 1)} ms；同车跨 stint 残差 MAD={_fmt(validation.get('median_within_driver_across_stint_mad_ms'), 1)} ms。",
                "",
                "| 车手 | Stint | 胎 | 圈数 | 中位残差 ms | 稳定性 MAD ms | 超额衰退 ms/胎龄圈 | 80% bootstrap 区间 | 晚段留出 MAE ms | 奇偶圈差 ms | 验证状态 |",
                "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
            ]
        )
        for row in event["stints"]:
            interval = (
                f"[{_fmt(row.get('degradation_bootstrap_ci80_low'), 1)}, "
                f"{_fmt(row.get('degradation_bootstrap_ci80_high'), 1)}]"
            )
            lines.append(
                f"| {row['driver']} | {row['stint_number']} | {row['compound']} | "
                f"{row['clean_laps']} | {_fmt(row.get('pace_residual_ms_p50'), 1)} | "
                f"{_fmt(row.get('consistency_mad_ms'), 1)} | "
                f"{_fmt(row.get('degradation_residual_ms_per_tyre_lap'), 1)} | "
                f"{interval} | {_fmt(row.get('early_fit_late_holdout_mae_ms'), 1)} | "
                f"{_fmt(row.get('odd_even_pace_delta_ms'), 1)} | {row['validation_status']} |"
            )
        lines.extend(
            [
                "",
                "> stint 斜率是相对全场上下文轮胎曲线的超额残差斜率；换胎、交通、损伤与策略仍可能造成跨 stint 差异。",
                "",
            ]
        )
    lines.extend(
        [
            "## 赛季等权总结",
            "",
            f"- 已分析 {season['available_events']}/{season['scheduled_events']} 站；{season['conclusion']}",
            "",
            "| 车队 | 平均模型名次 | 模型第一站数 | 最快圈第一站数 | 分类第一站数 | 平均速度相对值 km/h | 稳定性 MAD ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in season["cars"]:
        lines.append(
            f"| {row['team']} | {_fmt(row['event_equal_weight_mean_model_rank'])} | "
            f"{row['model_rank_1_events']} | {row['fastest_clean_lap_rank_1_events']} | "
            f"{row['classification_proxy_rank_1_events']} | "
            f"{_fmt(row['event_equal_weight_average_speed_advantage_kph_mean'], 3)} | "
            f"{_fmt(row['event_median_consistency_mad_ms'], 1)} |"
        )
    retro_year = next(row for row in retrospective["by_year"] if row["year"] == year)
    rolling_year = next((row for row in rolling["by_year"] if row["year"] == year), None)
    lines.extend(
        [
            "",
            "## 验证边界",
            "",
            f"- 同场回顾性：模型第一与本场最快圈第一一致率 {_fmt(100 * retro_year['model_fastest_top_match_rate'], 1)}%；与分类第一一致率 {_fmt(100 * retro_year['model_classification_top_match_rate'], 1)}%。",
            (
                f"- 无当前站特征的滚动基线：{rolling_year['events']} 站，最快圈第一命中率 {_fmt(100 * rolling_year['fastest_top_match_rate'], 1)}%，分类第一命中率 {_fmt(100 * rolling_year['classification_top_match_rate'], 1)}%。"
                if rolling_year
                else "- 无当前站特征的滚动基线：本年尚未达到五站适应门槛。"
            ),
            "- 同场回顾性与时间前推是两套指标，不能互换。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _plot_seasons(
    events: Sequence[Mapping[str, Any]], teams: Sequence[str], directory: Path
) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    colors = {
        "McLaren": "#ff8700",
        "Red Bull Racing": "#1e41ff",
        "Ferrari": "#dc0000",
        "Mercedes": "#00a19c",
    }
    paths: list[str] = []
    for year in sorted({int(row["year"]) for row in events}):
        year_events = [row for row in events if int(row["year"]) == year]
        x = np.arange(1, len(year_events) + 1)
        fig, axes = plt.subplots(2, 1, figsize=(15, 8), sharex=True, constrained_layout=True)
        for team in teams:
            speed: list[float] = []
            consistency: list[float] = []
            for event in year_events:
                row = next((item for item in event.get("cars", []) if item["team"] == team), None)
                speed.append(
                    float(row["average_speed_advantage_kph"])
                    if row and row.get("average_speed_advantage_kph") is not None
                    else np.nan
                )
                consistency.append(
                    float(row["consistency_mad_ms"])
                    if row and row.get("consistency_mad_ms") is not None
                    else np.nan
                )
            axes[0].plot(x, speed, marker="o", markersize=3, label=team, color=colors[team])
            axes[1].plot(
                x, consistency, marker="o", markersize=3, label=team, color=colors[team]
            )
        axes[0].axhline(0.0, color="#666666", linewidth=0.8)
        axes[0].set_ylabel("Median average-speed advantage (km/h)")
        axes[0].set_title(f"{year} race-by-race speed and model-residual consistency")
        axes[1].set_ylabel("Consistency MAD (ms, lower is better)")
        axes[1].set_xlabel("Round")
        axes[1].set_xticks(x)
        axes[1].grid(alpha=0.2)
        axes[0].grid(alpha=0.2)
        axes[0].legend(ncol=4, fontsize=9)
        path = directory / f"race_by_race_{year}_speed_consistency.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path.relative_to(ROOT).as_posix())
    return paths


def _plot_validation(
    retrospective: Mapping[str, Any],
    rolling: Mapping[str, Any],
    stint_summary: Mapping[str, Any],
    projection: Mapping[str, Any],
    directory: Path,
) -> list[str]:
    paths: list[str] = []
    years = [row["year"] for row in retrospective["by_year"]]
    retro = [row["model_fastest_top_match_rate"] for row in retrospective["by_year"]]
    rolling_map = {row["year"]: row for row in rolling["by_year"]}
    forward = [rolling_map.get(year, {}).get("fastest_top_match_rate", np.nan) for year in years]
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    x = np.arange(len(years))
    ax.bar(x - 0.18, retro, 0.36, label="Same-event descriptive")
    ax.bar(x + 0.18, forward, 0.36, label="Prior-events-only rolling")
    ax.set_xticks(x, [str(year) for year in years])
    ax.set_ylim(0, 1)
    ax.set_ylabel("Top-team match rate")
    ax.set_title("Descriptive and time-forward checks are reported separately")
    ax.legend()
    ax.grid(axis="y", alpha=0.2)
    path = directory / "validation_top_team_match.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path.relative_to(ROOT).as_posix())

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    axes[0].bar(
        ["Late holdout MAE", "Odd-even delta"],
        [
            stint_summary["median_early_fit_late_holdout_mae_ms"] or 0.0,
            stint_summary["median_odd_even_pace_delta_ms"] or 0.0,
        ],
        color=["#4c78a8", "#f58518"],
    )
    axes[0].set_ylabel("Median error / difference (ms)")
    axes[0].set_title("Stint reproducibility")
    stable = stint_summary["bootstrap_slope_stable_rate"] or 0.0
    axes[1].bar(["Stable sign", "Crosses zero"], [stable, 1.0 - stable])
    axes[1].set_ylim(0, 1)
    axes[1].set_ylabel("Share of assessed stints")
    axes[1].set_title("Bootstrap slope-sign evidence")
    path = directory / "stint_method_validation.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path.relative_to(ROOT).as_posix())

    teams = [row["team"] for row in projection["team_projection"]]
    values = [row["shrunken_car_state_advantage_z"] for row in projection["team_projection"]]
    low = [row["predictive_band_80_low_z"] for row in projection["team_projection"]]
    high = [row["predictive_band_80_high_z"] for row in projection["team_projection"]]
    estimate_low = [row["bootstrap_ci80_low_z"] for row in projection["team_projection"]]
    estimate_high = [row["bootstrap_ci80_high_z"] for row in projection["team_projection"]]
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    y = np.arange(len(teams))
    ax.errorbar(
        values,
        y,
        xerr=[np.asarray(values) - np.asarray(low), np.asarray(high) - np.asarray(values)],
        fmt="o",
        capsize=4,
        label="Historical-error calibrated predictive band",
    )
    ax.errorbar(
        values,
        y,
        xerr=[
            np.asarray(values) - np.asarray(estimate_low),
            np.asarray(estimate_high) - np.asarray(values),
        ],
        fmt="none",
        color="#d62728",
        linewidth=3,
        capsize=3,
        label="4-event bootstrap estimate interval",
    )
    ax.set_yticks(y, teams)
    ax.axvline(0.0, color="#666666", linewidth=0.8)
    ax.invert_yaxis()
    ax.set_xlabel("Shrunken 2026 car-state advantage z")
    ax.set_title("2026 exploratory reset update (4 events; not confirmatory)")
    ax.legend(fontsize=8, loc="lower right")
    path = directory / "2026_exploratory_reset_update.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path.relative_to(ROOT).as_posix())
    return paths


def _write_2026_report(projection: Mapping[str, Any], path: Path) -> None:
    calibration = projection["predictive_band_calibration"]
    lines = [
        "# 2026 四队规则重置探索性更新 v5",
        "",
        f"- 本地仅有 {projection['observed_events']} 站，确认性门槛为 {projection['minimum_events_for_confirmatory_release']} 站，当前状态：`{projection['publication_status']}`。",
        "- 车辆与车手—车辆交互在规则重置点归零；只继承 2025 车手后验，新车手使用零均值宽先验。",
        f"- 预测带按历史锁定时间前推误差校准：80% 平均宽度 {_fmt(calibration['mean_width_z'], 3)} z；四站 bootstrap 只表示当前估计稳定性。",
        "",
    ]
    for index, event in enumerate(projection["events"], 1):
        lines.extend(
            [
                f"## R{index} {event['meeting_name']}",
                "",
                (
                    f"- 清洁后缺失车队：{', '.join(event['missing_teams_after_cleaning'])}；本场名次只在其余可用四队范围内计算，不补值。"
                    if event["missing_teams_after_cleaning"]
                    else "- 四支报告车队均有统一清洁样本。"
                ),
                "",
                "| 车队 | 周末上沿名次 | 周末优势 z | 排位优势 z | 正赛优势 z | 同场最快圈名次 | 分类名次 | 平均速度 km/h | 正赛清洁圈 | 排位推圈 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in event["cars"]:
            lines.append(
                f"| {row['team']} | {row['weekend_ceiling_rank']} | "
                f"{_fmt(row['weekend_ceiling_advantage_z'], 3)} | "
                f"{_fmt(row['qualifying_ceiling_advantage_z'], 3)} | "
                f"{_fmt(row['race_ceiling_advantage_z'], 3)} | "
                f"{row['same_event_fastest_clean_lap_rank_four_team']} | "
                f"{_fmt(row.get('classification_proxy_rank_four_team'), 0)} | "
                f"{_fmt(row.get('average_speed_kph_p50'))} | {row['race_clean_laps']} | "
                f"{row['qualifying_accurate_laps']} |"
            )
        lines.extend(["", f"> {event['comparison_boundary']}", ""])
    lines.extend(
        [
            "## 强收缩后的当前车辆状态",
            "",
            "| 探索名次 | 车队 | 四站原始优势 z | 重置先验权重 | 收缩估计 z | 四站 bootstrap 80% | 历史误差校准预测带 80% |",
            "| ---: | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in projection["team_projection"]:
        lines.append(
            f"| {row['exploratory_rank']} | {row['team']} | "
            f"{_fmt(row['raw_event_equal_weight_advantage_z'], 3)} | "
            f"{_fmt(row['reset_prior_weight'], 3)} | "
            f"{_fmt(row['shrunken_car_state_advantage_z'], 3)} | "
            f"[{_fmt(row['bootstrap_ci80_low_z'], 3)}, {_fmt(row['bootstrap_ci80_high_z'], 3)}] | "
            f"[{_fmt(row['predictive_band_80_low_z'], 3)}, {_fmt(row['predictive_band_80_high_z'], 3)}] |"
        )
    lines.extend(["", f"> {projection['warning']}", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_master_summary(result: Mapping[str, Any], path: Path) -> None:
    retro = result["validation"]["same_event_retrospective"]["overall"]
    rolling = result["validation"]["rolling_prior_events_only"]["overall"]
    stint = result["stint_method_validation"]
    projection = result["projection_2026"]
    lines = [
        "# 2023–2026 四队逐站车辆—车手分层反事实归因 v5",
        "",
        f"- 运行 ID：`{result['run_id']}`。",
        f"- 2023–2025：计划 {result['coverage']['scheduled_events_2023_2025']} 站，形成正式逐站分析 {result['coverage']['available_events_2023_2025']} 站；缺失站保留显式空记录。",
        f"- 圈级模型与遥测键匹配率：{100 * result['telemetry_audit']['telemetry_key_match_rate']:.2f}%；平均速度质量通过率：{100 * result['telemetry_audit']['average_speed_quality_rate']:.2f}%。",
        f"- 同场回顾性模型第一/最快圈第一一致率：{100 * retro['model_fastest_top_match_rate']:.1f}%；模型第一/分类第一一致率：{100 * retro['model_classification_top_match_rate']:.1f}%。",
        f"- 只用先前分站的滚动基线共验证 {rolling['events']} 站；最快圈第一命中率 {100 * rolling['fastest_top_match_rate']:.1f}%，分类第一命中率 {100 * rolling['classification_top_match_rate']:.1f}%。",
        f"- Stint：{stint['holdout_eligible_stints']}/{stint['stints_total']} 可做早段→晚段留出；中位晚段 MAE={_fmt(stint['median_early_fit_late_holdout_mae_ms'], 1)} ms；中位奇偶圈差={_fmt(stint['median_odd_even_pace_delta_ms'], 1)} ms。",
        f"- 2026 本地只有 {projection['observed_events']} 站，确认性门槛为 {projection['minimum_events_for_confirmatory_release']} 站，因此发布状态为 `{projection['publication_status']}`。",
        "",
        "## 这版修正了什么",
        "",
        "1. 主验证不再依赖六站手选样本；所有赛历分站按相同规则进入或留下缺失原因。",
        "2. 每站同时列本场模型上沿、本场最快清洁圈和本场分类，不再跨年取最快值。",
        "3. 每个 stint 都有奇偶圈复验；样本足够时增加早段拟合→晚段留出和斜率 bootstrap 区间。",
        "4. 赛季总结按分站等权；另列无当前站特征的时间前推基线，避免把回顾性一致率冒充预测率。",
        "5. 2026 只继承车手先验，车辆与车手—车辆交互归零后强收缩更新。",
        "",
        "## 可视化",
        "",
    ]
    for figure in result["figures"]:
        lines.append(f"- `{figure}`")
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 平均速度是描述值，赛道长度与比赛情境不同，跨站比较只使用各站四队中心化后的相对值。",
            "- 排位与正赛只融合相对优势，不合并绝对圈时；公开数据无法证明两车具体调校、升级和损伤完全相同。",
            "- 分类代理不含全部赛后判罚修订；策略、可靠性、交通、安全车、事故和损伤未进入因果赛果模型。",
            "- 2026 四站 bootstrap 只描述当前点估计；图中另列历史锁定时间前推误差校准的宽预测带，仍不是完整赛季确认结论。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--rebuild-telemetry", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    config = base.load_json(args.config)
    validate_race_by_race_config(config)
    if not config.get("offline_only"):
        raise RuntimeError("本入口只允许离线运行。")
    event_result, lap_detail, seasonal, bayesian, season_config = _load_historical_inputs(
        config
    )
    aliases = base.load_json(ROOT / config["entity_alias_config"])["team_aliases"]
    teams = list(config["reporting_teams"])
    references = [
        local_live_timing_reference(
            year,
            ROOT
            / season_config["local_result_reference"]["root_template"].format(year=year),
            aliases,
            sessions=("Race",),
        )
        for year in (2023, 2024, 2025, 2026)
    ]
    classification = _classification_lookup(references, teams)
    schema_root = ROOT / config["telemetry"]["schema_root"]
    telemetry_files, telemetry_sources = telemetry_source_files(
        schema_root, config["telemetry"]["years"], config["telemetry"]["session"]
    )
    telemetry_output = ROOT / config["outputs"]["telemetry_lap_metrics"]
    telemetry_manifest_path = ROOT / config["outputs"]["telemetry_manifest"]
    if telemetry_output.is_file() and telemetry_manifest_path.is_file() and not args.rebuild_telemetry:
        telemetry_manifest = base.load_json(telemetry_manifest_path)
        if base.file_sha256(telemetry_output) != telemetry_manifest["file"]["sha256"]:
            raise RuntimeError("遥测圈级缓存哈希不一致，请使用 --rebuild-telemetry。")
        telemetry = pd.read_csv(telemetry_output, compression="gzip")
        telemetry_manifest["sources"] = telemetry_sources
        base.atomic_json(telemetry_manifest_path, telemetry_manifest)
    else:
        telemetry = extract_telemetry_lap_metrics(telemetry_files, teams)
        _write_deterministic_csv(telemetry, telemetry_output)
        telemetry_manifest = {
            "created_at": base.now_iso(),
            "query_version": "race_by_race_distance_over_lap_time_v1",
            "sources": telemetry_sources,
            "coverage": {
                "rows": int(len(telemetry)),
                "years": sorted(telemetry["year"].astype(int).unique().tolist()),
                "events": int(telemetry.groupby(["year", "meeting_name"]).ngroups),
            },
            "file": {
                "path": config["outputs"]["telemetry_lap_metrics"],
                "sha256": base.file_sha256(telemetry_output),
                "bytes": telemetry_output.stat().st_size,
            },
        }
        base.atomic_json(telemetry_manifest_path, telemetry_manifest)
    historical_telemetry = telemetry.loc[telemetry["year"].between(2023, 2025)].copy()
    lap_detail, telemetry_audit = attach_telemetry_quality(
        lap_detail, historical_telemetry, config["telemetry"]
    )
    stints, stint_event_summaries, stint_summary = validate_stints(
        lap_detail, event_result["stints"], config["stint_validation"]
    )
    events = standardize_historical_events(
        event_result["events"],
        lap_detail,
        stints,
        stint_event_summaries,
        season_config["season_order"],
        classification,
    )
    seasons = build_season_summaries(events, teams)
    retrospective = build_retrospective_validation(events)
    rolling = build_rolling_rank_validation(
        events, teams, config["rolling_rank_validation"]
    )
    race_2026, qualifying_2026, coverage_2026 = _prepare_2026(
        config, season_config, aliases
    )
    telemetry_2026 = telemetry.loc[telemetry["year"] == 2026].copy()
    settings = config["telemetry"]
    telemetry_2026["average_speed_quality_ok"] = (
        telemetry_2026["distance_coverage_ratio"].between(
            settings["minimum_distance_coverage_ratio"],
            settings["maximum_distance_coverage_ratio"],
        )
        & (telemetry_2026["telemetry_samples"] >= settings["minimum_samples_per_lap"])
        & (
            telemetry_2026["speed_formula_relative_gap"]
            <= settings["maximum_relative_gap_to_sample_mean"]
        )
    )
    telemetry_2026.loc[~telemetry_2026["average_speed_quality_ok"], "average_speed_kph"] = np.nan
    projection = build_2026_exploratory_projection(
        race_2026,
        qualifying_2026,
        telemetry_2026,
        bayesian,
        classification,
        teams,
        config["projection_2026"],
    )
    figure_directory = ROOT / config["outputs"]["figure_directory"]
    figures = _plot_seasons(events, teams, figure_directory)
    figures.extend(
        _plot_validation(
            retrospective, rolling, stint_summary, projection, figure_directory
        )
    )
    time_forward = {
        "bayesian_driver_event": bayesian["time_forward_validation"],
        "ridge_lap_level": seasonal["time_forward_validation"],
        "interpretation": "既有预注册 2024 调参与 2025 锁定测试保持不变；v5 另增逐站滚动排名基线。",
    }
    result = {
        "run_id": f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "methodological_status": config["validation_status"],
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "network_calls": [],
        "coverage": {
            "scheduled_events_2023_2025": int(len(events)),
            "available_events_2023_2025": int(
                sum(row.get("data_status") == "available" for row in events)
            ),
            "historical_lap_rows": int(len(lap_detail)),
            "historical_stints": int(len(stints)),
            "telemetry_manifest": telemetry_manifest,
            "2026": coverage_2026,
        },
        "telemetry_audit": telemetry_audit,
        "events": events,
        "season_summaries": seasons,
        "stints": stints,
        "stint_method_validation": stint_summary,
        "validation": {
            "same_event_retrospective": retrospective,
            "rolling_prior_events_only": rolling,
            "pre_registered_time_forward": time_forward,
        },
        "projection_2026": projection,
        "figures": figures,
        "deprecated_primary_validation": {
            "six_race_hand_selected_stress": True,
            "replacement": "全量赛历逐站合同 + 先前分站滚动验证",
            "note": "旧六站结果仅保留为历史压力测试，不进入 v5 主结论。",
        },
    }
    for season in seasons:
        year = int(season["year"])
        report_path = ROOT / config["outputs"]["season_report_template"].format(year=year)
        _write_season_report(
            year,
            [row for row in events if int(row["year"]) == year],
            season,
            retrospective,
            rolling,
            report_path,
        )
    report_2026_path = ROOT / config["outputs"]["season_report_template"].format(
        year=2026
    )
    _write_2026_report(projection, report_2026_path)
    result_path = ROOT / config["outputs"]["result"]
    summary_path = ROOT / config["outputs"]["master_summary"]
    base.atomic_json(result_path, result)
    _write_master_summary(result, summary_path)
    base.append_log(
        {
            "run_id": result["run_id"],
            "timestamp": base.now_iso(),
            "phase": "run",
            "method": "four_team_race_by_race_2023_2026_v5",
            "status": "completed",
            "network_calls": 0,
        }
    )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
