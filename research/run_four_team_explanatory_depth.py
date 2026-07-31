#!/usr/bin/env python3
"""运行 2023–2025 四队 v6 可解释深度层：更强上下文 + 分离度 + 毫秒重建 + 理论上限。"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import build_v4_frozen_data as frozen
import run_experiments as base
from modeling.bayesian_counterfactual import (
    aggregate_with_context_fit,
    posterior_diagnostics,
    sample_posterior,
)
from modeling.event_stint_attribution import (
    enrich_qualifying_laps,
    enrich_race_laps,
)
from modeling.explanatory_depth import (
    attach_traffic,
    build_car_ceiling,
    build_reconstruction,
    build_separation,
    context_oos_gate,
    extract_traffic_lap_metrics,
    fit_context_head,
    validate_explanatory_depth_config,
)
from modeling.race_by_race import telemetry_source_files
from modeling.seasonal_attribution import prepare_season_frame


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "four_team_explanatory_depth_v6.json"


def _load_race_inputs(parent: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in parent["input_files"]:
        path = ROOT / item["path"]
        actual = base.file_sha256(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"冻结 Race 文件哈希不一致：{item['path']}")
        frame = pd.read_csv(path, compression="gzip")
        frames.append(frame)
        identity.append(
            {"year": int(item["year"]), "path": item["path"], "sha256": actual, "rows": int(len(frame))}
        )
    return pd.concat(frames, ignore_index=True), identity


def _load_qualifying(event_config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = event_config["qualifying_freeze"]
    manifest_path = ROOT / settings["manifest"]
    if not manifest_path.is_file():
        raise FileNotFoundError("缺少排位冻结 manifest；先运行 build_qualifying_frozen_data.py。")
    manifest = base.load_json(manifest_path)
    path = ROOT / manifest["file"]["path"]
    if base.file_sha256(path) != manifest["file"]["sha256"]:
        raise RuntimeError("排位冻结文件哈希与 manifest 不一致。")
    return pd.read_csv(path, compression="gzip"), manifest


def _load_classification_reference(
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """只抽取 v5 的 classification proxy；它不是 FIA 最终分类，也不作为训练标签。"""

    relative_path = config["reconstruction"]["classification_reference"]
    path = ROOT / relative_path
    payload = base.load_json(path)
    rows: list[dict[str, Any]] = []
    allowed_years = {int(year) for year in config["telemetry"]["years"]}
    for event in payload.get("events", []):
        year = int(event["year"])
        if year not in allowed_years:
            continue
        for driver in event.get("drivers", []):
            rank = driver.get("classification_proxy_rank_four_team")
            if rank is None:
                continue
            rows.append(
                {
                    "year": year,
                    "meeting_name": str(event["meeting_name"]),
                    "driver": str(driver["driver"]),
                    "classification_proxy_rank": int(rank),
                }
            )
    return pd.DataFrame(rows), {
        "path": relative_path,
        "sha256": base.file_sha256(path),
        "rows": int(len(rows)),
        "role": config["reconstruction"]["classification_reference_role"],
    }


def _load_or_build_traffic(
    config: Mapping[str, Any], rebuild: bool
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = ROOT / config["outputs"]["traffic_lap_metrics"]
    manifest_path = ROOT / config["outputs"]["traffic_manifest"]
    if output.is_file() and manifest_path.is_file() and not rebuild:
        manifest = base.load_json(manifest_path)
        if base.file_sha256(output) != manifest["file"]["sha256"]:
            raise RuntimeError("traffic 缓存哈希不一致，请使用 --rebuild-traffic。")
        return pd.read_csv(output, compression="gzip"), manifest
    schema_root = ROOT / config["telemetry"]["schema_root"]
    files, sources = telemetry_source_files(
        schema_root, config["telemetry"]["years"], config["telemetry"]["session"]
    )
    traffic = extract_traffic_lap_metrics(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    import gzip

    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0) as handle:
            traffic.to_csv(handle, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(output)
    manifest = {
        "created_at": base.now_iso(),
        "query_version": "explanatory_depth_gap_to_ahead_median_v1",
        "sources": sources,
        "coverage": {
            "rows": int(len(traffic)),
            "years": sorted(traffic["year"].astype(int).unique().tolist()) if len(traffic) else [],
        },
        "file": {
            "path": config["outputs"]["traffic_lap_metrics"],
            "sha256": base.file_sha256(output),
            "bytes": output.stat().st_size,
        },
    }
    base.atomic_json(manifest_path, manifest)
    return traffic, manifest


def _posterior_settings(identity_config: Mapping[str, Any], quick: bool) -> dict[str, Any]:
    settings = copy.deepcopy(identity_config["posterior"])
    if quick:
        settings.update({"chains": 2, "iterations": 300, "warmup": 150, "thin": 3})
    settings["random_seed"] = int(settings["random_seed"]) + 6006
    return settings


def _selected_penalties(parent: Mapping[str, Any]) -> dict[str, float]:
    selected = next(row for row in parent["candidate_penalties"] if row["id"] == "balanced_v1")
    return {key: float(value) for key, value in selected.items() if key != "id"}


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return f"{float(value):.{digits}f}"


def _write_summary(result: Mapping[str, Any], path: Path) -> None:
    gate = result["context_gate"]
    recon = result["reconstruction"]["reconstruction_quality_overall"]
    rank = result["reconstruction"]["rank_construct_check"]["overall"]
    lines = [
        "# 2023–2025 四队 v6 可解释深度层",
        "",
        f"- 运行 ID：`{result['run_id']}`；四队圈级：{result['coverage']['four_team_lap_rows']}；分站：{result['coverage']['events']}。",
        f"- 身份骨架后验：{result['posterior_diagnostics']['chains']} 链 × {result['posterior_diagnostics']['draws_per_chain']}；最大尺度 R-hat={result['posterior_diagnostics']['maximum_scale_rhat']:.3f}。",
        f"- Layer A 上下文门控（仅 2024 选特征）：基线 MAE={_fmt(gate['baseline_selection_year_mae'])} → 选中 {gate['selected_features']} MAE={_fmt(gate['selected_selection_year_mae'])}；2025 锁定基线/选中={_fmt(gate['locked_year_mae']['baseline'])}/{_fmt(gate['locked_year_mae']['selected'])}。",
        f"- Layer C 圈速重建：driver-event R²={_fmt(recon['r2'])}，MAE={_fmt(recon['mae_ms'], 1)} ms；模型组合序 vs 观测干净圈序平均 Spearman={_fmt(rank['mean_model_vs_observed_spearman'])}。",
        f"- Layer C 配速模型偏差（模型组合序≠观测干净圈序）：{len(result['reconstruction']['rank_construct_check']['anomalies_model_vs_observed_pace'])} 例；这是配速构念检查，不再误称赛果异常。",
        f"- Layer C 配速≠classification proxy：平均 Spearman={_fmt(rank['mean_observed_vs_classification_proxy_spearman'])}，排名差≥2 的 driver-event={len(result['reconstruction']['rank_construct_check']['anomalies_pace_vs_classification_proxy'])} 例；只标记待查原因，不作因果归因。",
        "",
        "## 每站分离度与理论上限（2025）",
        "",
        "| 分站 | 方差比 | 成对置信度 | 样本门控 | 分离度等级 | 上限第一 | 上限优势 90s ms |",
        "| --- | ---: | ---: | --- | --- | --- | ---: |",
    ]
    for event in result["events"]:
        if int(event["year"]) != 2025:
            continue
        sep = event["separation"]
        ceilings = event["car_ceiling"]
        top = None
        top_ms = None
        for team, row in ceilings.items():
            median = row["weekend_theoretical_ceiling_advantage_z"].get("median")
            if median is not None and (top is None or median > top[1]):
                top = (team, median)
                top_ms = row.get("weekend_theoretical_ceiling_90s_ms")
        lines.append(
            f"| {event['meeting_name']} | {_fmt(sep['separation_index_between_within'])} | "
            f"{_fmt(sep['mean_pairwise_ordering_confidence'])} | "
            f"{'pass' if sep['coverage_sufficient'] else 'fail'} | {sep['separation_grade']} | "
            f"{top[0] if top else '—'} | {_fmt(top_ms, 1)} |"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 更明显来自更干净的上下文与显式信号/噪声，未改动 v3 先验强度。",
            "- 理论上限是统计前沿+最快 10% 圈执行损失校正，不是物理极限；校正 bootstrap 已进入 CI，排位/正赛只融合相对优势。",
            "- 配速模型构念与 classification proxy 赛果构念分开报告；proxy 不是 FIA 最终分类，策略/可靠/交通/事故未进入因果赛果。",
            "- 这是 2025 已查看后的回顾性解释扩展，不冒充赛前预测。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rank_text(order: Mapping[str, Any]) -> str:
    if not order:
        return "—"
    return " > ".join(
        str(driver) for driver, _ in sorted(order.items(), key=lambda item: int(item[1]))
    )


def _top_ceiling(event: Mapping[str, Any]) -> tuple[str | None, float | None]:
    candidates: list[tuple[str, float]] = []
    for team, row in event["car_ceiling"].items():
        median = row["weekend_theoretical_ceiling_advantage_z"].get("median")
        if median is not None:
            candidates.append((str(team), float(median)))
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1])


def _write_season_report(result: Mapping[str, Any], year: int, path: Path) -> None:
    """输出每站可审计特征表；不把“明显”压缩成单一总评分。"""

    events = [event for event in result["events"] if int(event["year"]) == int(year)]
    rank_rows = {
        (int(row["year"]), str(row["meeting_name"])): row
        for row in result["reconstruction"]["rank_construct_check"]["events"]
    }
    gate = result["context_gate"]
    traffic = result["traffic_audit"]
    lines = [
        f"# {year} 四队逐站可解释深度报告（v6）",
        "",
        f"- 性质：`{result['methodological_status']}`；共 {len(events)} 站。",
        f"- Layer A：traffic 覆盖 {_fmt(traffic['usable_gap_rate'] * 100, 1)}%；特征只按 {gate['selection_year']} 时间外选择，{gate['locked_year']} 仅锁定报告。",
        "- 方向约定：理论上限 advantage_z / 90s ms 越大越快；CI 是统计不确定性，不是物理极限范围。",
        "",
        "## 分站总览",
        "",
        "| 分站 | R 圈 | traffic覆盖 | dirty-air | 方差比 | 成对置信度 | 门控 | 等级 | 理论上限第一 | 90s ms | 模型↔配速 ρ | 配速↔赛果proxy ρ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for event in events:
        sep = event["separation"]
        rank = rank_rows.get((int(year), str(event["meeting_name"])), {})
        top_team, top_z = _top_ceiling(event)
        top_ms = None
        if top_team is not None:
            top_ms = event["car_ceiling"][top_team].get(
                "weekend_theoretical_ceiling_90s_ms"
            )
        lines.append(
            f"| {event['meeting_name']} | {event['race_clean_laps_four_team']} | "
            f"{_fmt(event['traffic_usable_rate'] * 100, 1)}% | "
            f"{_fmt(event['dirty_air_lap_share'] * 100, 1)}% | "
            f"{_fmt(sep['separation_index_between_within'])} | "
            f"{_fmt(sep['mean_pairwise_ordering_confidence'])} | "
            f"{'pass' if sep['coverage_sufficient'] else 'fail'} | "
            f"{sep['separation_grade']} | {top_team or '—'} | "
            f"{_fmt(top_ms, 1)} | {_fmt(rank.get('model_vs_observed_spearman'))} | "
            f"{_fmt(rank.get('observed_vs_classification_proxy_spearman'))} |"
        )

    lines.extend(
        [
            "",
            "## 三种排序（从快到慢）",
            "",
            "| 分站 | 模型车手组合序 | 观测干净配速序 | classification proxy 序 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for event in events:
        rank = rank_rows.get((int(year), str(event["meeting_name"])), {})
        lines.append(
            f"| {event['meeting_name']} | {_rank_text(rank.get('model_combination_order', {}))} | "
            f"{_rank_text(rank.get('observed_clean_pace_order', {}))} | "
            f"{_rank_text(rank.get('classification_proxy_order', {}))} |"
        )

    lines.extend(
        [
            "",
            "## 每队理论上限",
            "",
            "| 分站 | 车队 | 融合 advantage_z p50 | CI80 | Q p50 | R p50 | 执行校正 R p50 |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for event in events:
        for team, ceiling in event["car_ceiling"].items():
            fused = ceiling["weekend_theoretical_ceiling_advantage_z"]
            qualifying = ceiling.get("qualifying_ceiling_advantage_z") or {}
            race = ceiling["race_ceiling_advantage_z"]
            correction = ceiling["race_half_normal_shortfall_uncertainty_z"]
            lines.append(
                f"| {event['meeting_name']} | {team} | {_fmt(fused.get('median'))} | "
                f"[{_fmt(fused.get('ci80_low'))}, {_fmt(fused.get('ci80_high'))}] | "
                f"{_fmt(qualifying.get('median'))} | {_fmt(race.get('median'))} | "
                f"{_fmt(correction.get('median'))} |"
            )

    anomalies = [
        row
        for row in result["reconstruction"]["rank_construct_check"][
            "anomalies_pace_vs_classification_proxy"
        ]
        if int(row["year"]) == int(year)
    ]
    lines.extend(
        [
            "",
            "## 配速与赛果代理不一致（排名差 ≥ 2）",
            "",
            "| 分站 | 车手 | 车队 | 配速排名 | classification proxy | 差值 |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for row in anomalies:
        lines.append(
            f"| {row['meeting_name']} | {row['driver']} | {row['team']} | "
            f"{row['observed_clean_pace_rank']} | {row['classification_proxy_rank']} | "
            f"{row['rank_gap']:+d} |"
        )
    if not anomalies:
        lines.append("| — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- classification proxy 来自 v5 本地代理分类，不等同于经赛后处罚修订的 FIA 最终分类。",
            "- 配速与赛果代理不一致只是一张待查清单；不能从差值直接断言策略、可靠性、事故或处罚中的哪一项造成结果。",
            "- 同调教指 parc-fermé 周末族代理，不表示已观测到两位车手采用完全相同设置。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quick", action="store_true", help="短采样冒烟，不写正式产物")
    parser.add_argument("--rebuild-traffic", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = base.load_json(args.config)
    validate_explanatory_depth_config(config)
    parent = base.load_json(ROOT / config["parent_config"])
    identity_config = base.load_json(ROOT / config["identity_model_config"])
    event_config = base.load_json(ROOT / config["event_stint_config"])
    aliases = base.load_json(ROOT / parent["entity_alias_config"])["team_aliases"]
    teams = list(config["reporting_teams"])

    raw_race, race_identity = _load_race_inputs(parent)
    race = prepare_season_frame(raw_race, aliases, parent["season_order"], parent["session_name"])
    traffic, traffic_manifest = _load_or_build_traffic(config, args.rebuild_traffic)
    race, traffic_audit = attach_traffic(race, traffic, config["telemetry"])

    selected_features, gate_report = context_oos_gate(race, config)
    richer_head = fit_context_head(
        race, selected_features, float(config["context_head"]["ridge_alpha"])
    )

    aggregate = aggregate_with_context_fit(race, richer_head, identity_config["aggregation"])
    posterior_settings = _posterior_settings(identity_config, args.quick)
    posterior = sample_posterior(aggregate, posterior_settings)
    diagnostics = posterior_diagnostics(posterior)

    race_detail = enrich_race_laps(
        race,
        richer_head.predict(race),
        posterior,
        float(config["frontier"]["race_ceiling_quantile"]),
    )
    race_detail = race_detail.loc[race_detail["team_name"].isin(teams)].reset_index(drop=True)

    raw_qualifying, qualifying_manifest = _load_qualifying(event_config)
    qualifying = prepare_season_frame(raw_qualifying, aliases, parent["season_order"], "Qualifying")
    qualifying_head = fit_context_head(
        qualifying,
        [feature for feature in selected_features if feature != "traffic_dirty_air"],
        float(config["context_head"]["ridge_alpha"]),
    )
    qualifying_detail = enrich_qualifying_laps(
        qualifying, qualifying_head.predict(qualifying), posterior
    )
    qualifying_detail = qualifying_detail.loc[
        qualifying_detail["team_name"].isin(teams)
    ].reset_index(drop=True)

    classification_reference, classification_identity = _load_classification_reference(config)
    reconstruction = build_reconstruction(
        race_detail, teams, config, classification_reference
    )

    events: list[dict[str, Any]] = []
    for (year, meeting), event_laps in race_detail.groupby(
        ["year", "meeting_name"], observed=True, sort=True
    ):
        year = int(year)
        meeting = str(meeting)
        present = [team for team in teams if team in set(event_laps["team_name"])]
        if len(present) < 2:
            continue
        circuit = str(event_laps["circuit_name"].iloc[0])
        q_event = qualifying_detail.loc[
            (qualifying_detail["year"] == year)
            & (qualifying_detail["meeting_name"] == meeting)
            & qualifying_detail["team_name"].isin(present)
        ].copy()
        separation = build_separation(
            event_laps, posterior, year, meeting, circuit, present, config["separation"]
        )
        car_ceiling = build_car_ceiling(
            event_laps,
            q_event,
            present,
            config["frontier"],
            year=year,
            meeting_name=meeting,
        )
        events.append(
            {
                "year": year,
                "meeting_name": meeting,
                "circuit_name": circuit,
                "reporting_teams_present": present,
                "race_clean_laps_four_team": int(len(event_laps)),
                "qualifying_accurate_laps_four_team": int(len(q_event)),
                "traffic_usable_rate": float(event_laps["traffic_available"].mean()),
                "dirty_air_lap_share": float(event_laps["dirty_air"].mean()),
                "separation": separation,
                "car_ceiling": car_ceiling,
                "boundary": "分离度与理论上限均为回顾性描述；策略/可靠/交通/事故未建模。",
            }
        )

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
            "sampler": "v3_conjugate_gibbs_reused_on_richer_context_aggregates",
        },
        "network_calls": [],
        "coverage": {
            "race_clean_laps": int(len(race)),
            "driver_event_rows": int(len(aggregate)),
            "four_team_lap_rows": int(len(race_detail)),
            "qualifying_accurate_laps": int(len(qualifying_detail)),
            "events": int(len(events)),
            "race_input_identity": race_identity,
            "qualifying_manifest_sha256": base.file_sha256(
                ROOT / event_config["qualifying_freeze"]["manifest"]
            ),
            "traffic_manifest": traffic_manifest,
            "classification_reference": classification_identity,
        },
        "posterior_diagnostics": diagnostics,
        "context_gate": gate_report,
        "traffic_audit": traffic_audit,
        "selected_context_features": selected_features,
        "events": events,
        "reconstruction": reconstruction,
        "identification_contract": {
            "identity_backbone": config["identity_model_config"],
            "identity_config_sha256": base.file_sha256(
                ROOT / config["identity_model_config"]
            ),
            "identity_prior_strength_unchanged": {
                "variance_prior_shape": identity_config["posterior"][
                    "variance_prior_shape"
                ],
                "block_prior_sd_z": identity_config["posterior"][
                    "block_prior_sd_z"
                ],
                "noise_prior": identity_config["posterior"]["noise_prior"],
                "intercept_prior_sd_z": identity_config["posterior"][
                    "intercept_prior_sd_z"
                ],
                "sampling_only_seed_offset": int(posterior_settings["random_seed"])
                - int(identity_config["posterior"]["random_seed"]),
            },
            "context_head": "identity-free richer nuisance head gated on 2024 time-forward only",
            "ceiling": "driver-neutral frontier with half-normal shortfall; relative advantages, no absolute merge",
            "separation": "between/within variance ratio + reused Gibbs posterior order probability",
            "boundaries": config["boundaries"],
        },
    }

    if not args.quick:
        result_path = ROOT / config["outputs"]["result"]
        summary_path = ROOT / config["outputs"]["summary"]
        base.atomic_json(result_path, result)
        _write_summary(result, summary_path)
        for year in config["telemetry"]["years"]:
            season_path = ROOT / config["outputs"]["season_report_template"].format(
                year=int(year)
            )
            _write_season_report(result, int(year), season_path)
        base.append_log(
            {
                "run_id": result["run_id"],
                "timestamp": base.now_iso(),
                "phase": "run",
                "method": "four_team_explanatory_depth_2023_2025_v6",
                "status": "completed",
                "network_calls": 0,
            }
        )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
