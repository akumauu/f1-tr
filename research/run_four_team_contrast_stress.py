#!/usr/bin/env python3
"""固定匈牙利站参数，比较六场“直观一致/反直觉”正赛。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from urllib.request import Request, urlopen

import run_experiments as base
from modeling.entities import normalize_team_entities
from modeling.evaluation import (
    compare_team_gaps,
)
from modeling.hierarchical import (
    context_standardized_laps,
    fit_event_model,
    fit_historical_driver_prior,
    log_pct_delta_to_ms,
    sensitivity_grid,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "four_team_2025_contrast_stress_v1.json"
OUTPUT = ROOT / "research" / "records" / "four_team_contrast_stress_2025.json"
SUMMARY = ROOT / "research" / "records" / "four_team_contrast_stress_2025.md"


def load_manifest_frame(manifest_path: Path, role: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = base.load_json(manifest_path)
    spec = manifest["files"][role]
    path = ROOT / spec["path"]
    if base.file_sha256(path) != spec["sha256"]:
        raise RuntimeError(f"冻结文件哈希不一致: {path}")
    return pd.read_csv(path, compression="gzip"), manifest


def _team_envelopes(
    adjusted: pd.DataFrame,
    reporting_teams: Sequence[str],
    reference_lap_seconds: float,
    quantiles: Mapping[str, float],
) -> list[dict[str, Any]]:
    values: dict[str, dict[str, float]] = {}
    for team in reporting_teams:
        subset = adjusted[adjusted["team_name"] == team]
        if subset["driver_id"].nunique() != 2:
            raise RuntimeError(f"{team} 在目标站没有完整双车样本。")
        laps = subset["vehicle_lap_z"]
        values[team] = {
            "ceiling_z": float(laps.quantile(quantiles["ceiling"])),
            "baseline_z": float(laps.quantile(quantiles["baseline"])),
            "floor_z": float(laps.quantile(quantiles["floor"])),
            "clean_laps": int(len(subset)),
        }
    fastest_ceiling = min(row["ceiling_z"] for row in values.values())
    fastest_baseline = min(row["baseline_z"] for row in values.values())
    rows = []
    for team, value in values.items():
        rows.append(
            {
                "team": team,
                **value,
                "ceiling_gap_ms": log_pct_delta_to_ms(
                    value["ceiling_z"] - fastest_ceiling, reference_lap_seconds
                ),
                "baseline_gap_ms": log_pct_delta_to_ms(
                    value["baseline_z"] - fastest_baseline, reference_lap_seconds
                ),
                "envelope_width_ms": log_pct_delta_to_ms(
                    value["floor_z"] - value["ceiling_z"], reference_lap_seconds
                ),
            }
        )
    rows.sort(key=lambda row: float(row["baseline_z"]))
    for rank, row in enumerate(rows, start=1):
        row["baseline_rank"] = rank
    return rows


def _driver_contrast(adjusted: pd.DataFrame, reference_lap_seconds: float) -> list[dict[str, Any]]:
    medians = (
        adjusted.groupby(["team_name", "driver_id"])["driver_on_car_lap_z"]
        .median()
        .rename("median_z")
        .reset_index()
    )
    rows: list[dict[str, Any]] = []
    for team, subset in medians.groupby("team_name"):
        fastest = float(subset["median_z"].min())
        for row in subset.itertuples(index=False):
            rows.append(
                {
                    "team": str(team),
                    "driver": str(row.driver_id),
                    "median_on_car_z": float(row.median_z),
                    "gap_to_faster_teammate_ms": log_pct_delta_to_ms(
                        float(row.median_z) - fastest, reference_lap_seconds
                    ),
                }
            )
    return sorted(rows, key=lambda row: (row["team"], row["median_on_car_z"]))


def _podium_team_order(teams: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(team) for team in teams))


def _lap_time_seconds(value: str) -> float:
    minutes, seconds = str(value).split(":", maxsplit=1)
    return float(minutes) * 60.0 + float(seconds)


def _jolpica_result_proxy(
    race: Mapping[str, Any],
    aliases: Mapping[str, str],
    reporting_teams: Sequence[str],
    reuse_cache: bool,
) -> dict[str, Any]:
    """读取 Jolpica 官方分类，并用双车最快圈均值构造方向性代理。"""

    url = f"https://api.jolpi.ca/ergast/f1/2025/{int(race['round'])}/results.json"
    cache_path = ROOT / "research" / "cache" / "external" / f"jolpica_2025_round_{int(race['round']):02d}_results.json"
    if reuse_cache and cache_path.exists():
        payload = cache_path.read_bytes()
    else:
        with urlopen(Request(url, headers={"User-Agent": "F1-TR-research/1.0"}), timeout=30) as response:  # noqa: S310
            payload = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
    document = json.loads(payload.decode("utf-8"))
    races = document["MRData"]["RaceTable"]["Races"]
    if len(races) != 1:
        raise RuntimeError(f"Jolpica 应返回一场比赛: {race['meeting_name']}")
    results = races[0]["Results"]
    drivers: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    for result in results:
        team = str(result["Constructor"]["name"])
        team = str(aliases.get(team, team))
        driver = str(result["Driver"]["code"])
        classifications.append(
            {
                "position": int(result["position"]),
                "driver": driver,
                "team": team,
                "status": str(result["status"]),
            }
        )
        fastest = result.get("FastestLap", {}).get("Time", {}).get("time")
        if team in reporting_teams and fastest:
            drivers.append(
                {
                    "driver_id": driver,
                    "team": team,
                    "fastest_lap_seconds": _lap_time_seconds(str(fastest)),
                    "fastest_lap_rank": int(result["FastestLap"]["rank"]),
                }
            )
    driver_frame = pd.DataFrame(drivers)
    teams: list[dict[str, Any]] = []
    for team, subset in driver_frame.groupby("team"):
        teams.append(
            {
                "team": str(team),
                "driver_count": int(len(subset)),
                "pace_proxy_seconds": float(subset["fastest_lap_seconds"].mean()),
            }
        )
    if set(reporting_teams) - {row["team"] for row in teams}:
        raise RuntimeError(f"Jolpica 四队代理不完整: {race['meeting_name']}")
    fastest = min(float(row["pace_proxy_seconds"]) for row in teams)
    for row in teams:
        row["gap_to_fastest_ms"] = (float(row["pace_proxy_seconds"]) - fastest) * 1000.0
    teams.sort(key=lambda row: float(row["pace_proxy_seconds"]))
    return {
        "provider": "Jolpica Ergast-compatible API",
        "method": "mean_of_two_driver_official_fastest_laps",
        "url": url,
        "response_sha256": hashlib.sha256(payload).hexdigest(),
        "classifications": classifications,
        "drivers": drivers,
        "teams": teams,
    }


def run_race(
    race: Mapping[str, Any],
    target_year: pd.DataFrame,
    driver_priors: Mapping[str, float],
    aliases: Mapping[str, str],
    config: Mapping[str, Any],
    refresh_external: bool,
) -> dict[str, Any]:
    reporting_teams = [str(team) for team in config["reporting_teams"]]
    target = target_year[
        (target_year["meeting_name"] == race["meeting_name"])
        & (target_year["session_name"] == config["session_name"])
    ].reset_index(drop=True)
    if target.empty:
        raise RuntimeError(f"冻结数据不存在目标站: {race['meeting_name']}")

    model_cfg = config["model"]
    fit = fit_event_model(
        target,
        driver_priors,
        float(model_cfg["team_penalty"]),
        float(model_cfg["driver_event_update_penalty"]),
    )
    adjusted = context_standardized_laps(target, fit)
    reference_lap_seconds = float(target["session_reference"].median())
    cars = _team_envelopes(
        adjusted,
        reporting_teams,
        reference_lap_seconds,
        {
            "ceiling": float(model_cfg["ceiling_quantile"]),
            "baseline": float(model_cfg["baseline_quantile"]),
            "floor": float(model_cfg["floor_quantile"]),
        },
    )
    model_gaps = {str(row["team"]): float(row["baseline_gap_ms"]) for row in cars}
    sensitivity = sensitivity_grid(
        target,
        driver_priors,
        reporting_teams,
        model_cfg["team_penalty_sensitivity"],
        model_cfg["driver_event_update_penalty_sensitivity"],
    )
    orders = [" > ".join(row["car_order_fast_to_slow"]) for row in sensitivity]
    modal_order = max(set(orders), key=orders.count)

    proxy = _jolpica_result_proxy(
        race,
        aliases,
        reporting_teams,
        reuse_cache=not refresh_external,
    )
    official_top_three = proxy["classifications"][:3]
    if official_top_three[0]["driver"] != race["winner_driver"] or [
        row["team"] for row in official_top_three
    ] != list(race["podium_teams"]):
        raise RuntimeError(f"配置中的冠军/领奖台与 Jolpica 分类不一致: {race['meeting_name']}")
    external_gaps = {
        str(row["team"]): float(row["gap_to_fastest_ms"]) for row in proxy["teams"]
    }
    comparison = compare_team_gaps(model_gaps, external_gaps)
    model_fastest = str(cars[0]["team"])
    external_fastest = str(proxy["teams"][0]["team"])
    podium_order = _podium_team_order(race["podium_teams"])

    return {
        "meeting_name": race["meeting_name"],
        "category": race["category"],
        "selection_rationale": race["selection_rationale"],
        "official_result": {
            "winner_driver": race["winner_driver"],
            "winner_team": race["winner_team"],
            "podium_teams": race["podium_teams"],
            "distinct_podium_team_order": podium_order,
            "source_url": race["result_source_url"],
        },
        "sample": {
            "all_field_clean_laps": int(len(target)),
            "all_field_drivers": int(target["driver_id"].nunique()),
            "four_team_clean_laps": int(target[target["team_name"].isin(reporting_teams)].shape[0]),
            "four_team_drivers": int(target[target["team_name"].isin(reporting_teams)]["driver_id"].nunique()),
            "reference_lap_seconds": reference_lap_seconds,
        },
        "model": {
            "fastest_baseline_team": model_fastest,
            "winner_team_matches_fastest_baseline": model_fastest == race["winner_team"],
            "winner_team_baseline_rank": next(
                int(row["baseline_rank"]) for row in cars if row["team"] == race["winner_team"]
            ),
            "cars": cars,
            "drivers": _driver_contrast(
                adjusted[adjusted["team_name"].isin(reporting_teams)], reference_lap_seconds
            ),
            "in_sample_mae": float(np.mean(np.abs(fit.train_residuals))),
            "sensitivity": {
                "grid_size": len(sensitivity),
                "modal_team_effect_order": modal_order,
                "modal_order_count": orders.count(modal_order),
                "unique_orders": sorted(set(orders)),
                "details": sensitivity,
            },
        },
        "external_pace_proxy": {
            "pace_proxy": proxy,
            "comparison_to_model": comparison,
            "fastest_proxy_team": external_fastest,
            "winner_team_matches_fastest_proxy": external_fastest == race["winner_team"],
        },
    }


def write_summary(result: Mapping[str, Any]) -> None:
    lines = [
        "# 2025 六站四队归因反例压力测试",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        "- 固定参数：2024 车手先验 alpha=10；车队惩罚=10；当站车手更新惩罚=100。",
        "- 性质：按已知赛果事后挑选的解释性压力测试，不是确认性准确率。",
        "",
        "| 类别 | 分站 | 冠军车队 | 模型 P50 最快车 | 冠军车队模型排名 | 官方最快圈代理 | 模型/代理 Spearman |",
        "| --- | --- | --- | --- | ---: | --- | ---: |",
    ]
    for race in result["races"]:
        lines.append(
            "| {category} | {meeting} | {winner} | {model} | {rank} | {external} | {rho:.2f} |".format(
                category=race["category"],
                meeting=race["meeting_name"],
                winner=race["official_result"]["winner_team"],
                model=race["model"]["fastest_baseline_team"],
                rank=race["model"]["winner_team_baseline_rank"],
                external=race["external_pace_proxy"]["fastest_proxy_team"],
                rho=race["external_pace_proxy"]["comparison_to_model"]["spearman_rank_correlation"],
            )
        )
    lines.extend(
        [
            "",
            "## 聚合",
            "",
            f"- 模型 P50 最快车与冠军车队一致：{result['aggregate']['model_winner_matches']}/{result['aggregate']['race_count']}。",
            f"- 官方双车最快圈代理的最快车与冠军车队一致：{result['aggregate']['external_winner_matches']}/{result['aggregate']['race_count']}。",
            f"- 模型与官方最快圈代理平均 Spearman：{result['aggregate']['mean_model_external_spearman']:.3f}。",
            "- 这些数字用于定位反例；不能因样本按结果挑选而外推成泛化准确率。",
            "",
            "## 分层诊断",
            "",
        ]
    )
    for race in result["races"]:
        winner_team = str(race["official_result"]["winner_team"])
        winner_car = next(
            row for row in race["model"]["cars"] if row["team"] == winner_team
        )
        teammates = [
            row for row in race["model"]["drivers"] if row["team"] == winner_team
        ]
        teammate_text = "、".join(
            f"{row['driver']} +{row['gap_to_faster_teammate_ms']:.1f} ms"
            for row in teammates
        )
        lines.append(
            f"- {race['meeting_name']}：冠军车队 P50 相对最快车 {winner_car['baseline_gap_ms']:.1f} ms；同队当站中位差 {teammate_text}。"
        )
    lines.append("")
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--refresh-external", action="store_true")
    args = parser.parse_args()

    config = base.load_json(args.config)
    aliases = base.load_json(ROOT / config["entity_alias_config"])["team_aliases"]
    historical, historical_manifest = load_manifest_frame(
        ROOT / config["historical_manifest"], config["historical_file_role"]
    )
    target, target_manifest = load_manifest_frame(
        ROOT / config["target_manifest"], config["target_file_role"]
    )
    historical = normalize_team_entities(historical, aliases)
    target = normalize_team_entities(target, aliases)
    historical = historical[
        (historical["year"] == int(config["historical_year"]))
        & (historical["session_name"] == config["session_name"])
    ].reset_index(drop=True)
    target_year = target[target["year"] == int(config["target_year"])].reset_index(drop=True)
    driver_priors, _ = fit_historical_driver_prior(
        historical, float(config["historical_ridge_alpha"])
    )

    races = [
        run_race(
            race,
            target_year,
            driver_priors,
            aliases,
            config,
            args.refresh_external,
        )
        for race in config["races"]
    ]
    spearman = [
        float(race["external_pace_proxy"]["comparison_to_model"]["spearman_rank_correlation"])
        for race in races
    ]
    result = {
        "run_id": f"{config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "data_identity": {
            "historical_manifest_created_at": historical_manifest["created_at"],
            "historical_file_sha256": historical_manifest["files"][config["historical_file_role"]]["sha256"],
            "target_manifest_created_at": target_manifest["created_at"],
            "target_file_sha256": target_manifest["files"][config["target_file_role"]]["sha256"],
        },
        "races": races,
        "aggregate": {
            "race_count": len(races),
            "model_winner_matches": sum(
                bool(race["model"]["winner_team_matches_fastest_baseline"]) for race in races
            ),
            "external_winner_matches": sum(
                bool(race["external_pace_proxy"]["winner_team_matches_fastest_proxy"]) for race in races
            ),
            "mean_model_external_spearman": float(np.mean(spearman)),
        },
        "limitations": config["limitations"],
    }
    base.atomic_json(OUTPUT, result)
    write_summary(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
