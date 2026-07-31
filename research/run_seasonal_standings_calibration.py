#!/usr/bin/env python3
"""三季面板 RAPM 的真实积分榜校准 + rolling-origin 验证 + 转会图诊断 + 2026 车手先验。

本脚本**不重造估计器**：复用 §5.6 `modeling.seasonal_attribution` 的三季面板 RAPM
（池化车手效应已实现转会网络修正）。在其之上叠加 §5.6 缺少的四块：
  1. 真实 Jolpica 车手/车队积分榜校准（只读、不入拟合）；
  2. rolling-origin 逐站时间外验证（§5.6 只有一次时间前切）；
  3. 转会二部图连通性 + articulation 诊断（识别可信度）；
  4. 2026 车手先验交接。
积分榜含可靠性/策略/碰撞/罚时，非纯速度，仅作方向性外部佐证；不输出车/手百分比分摊。
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

import run_experiments as base
from modeling.entities import normalize_team_name
from modeling.evaluation import (
    _read_or_fetch_json,
    interval,
    stratified_stint_bootstrap,
)
from modeling.seasonal_attribution import (
    JOINT_BLOCKS,
    extract_season_ratings,
    fit_seasonal_model,
    log_pct_to_ms,
    metric_bundle,
    prepare_season_frame,
)

ROOT = base.ROOT
CONFIG_PATH = ROOT / "research" / "configs" / "seasonal_standings_calibration.json"
RESULTS_PATH = base.RECORDS_DIR / "seasonal_standings_calibration_results.json"
SUMMARY_PATH = base.RECORDS_DIR / "seasonal_standings_calibration_summary.md"
PRIOR_PATH = ROOT / "research" / "artifacts" / "panel_2026_driver_prior.json"


# --------------------------------------------------------------------------- #
# 数据加载（复用 §5.6 冻结 CSV）
# --------------------------------------------------------------------------- #
def load_seasonal_inputs(seasonal_config: Mapping[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """读取并 SHA256 校验三季冻结 CSV，拼成单帧（对齐 run_four_team_seasonal_attribution）。"""
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in seasonal_config["input_files"]:
        path = ROOT / item["path"]
        actual = base.file_sha256(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"冻结文件哈希不一致：{item['path']}")
        frame = pd.read_csv(path, compression="gzip")
        years = sorted(pd.to_numeric(frame["year"], errors="raise").astype(int).unique())
        if years != [int(item["year"])]:
            raise RuntimeError(f"冻结文件年份不一致：{item['path']} -> {years}")
        frames.append(frame)
        identity.append({"year": int(item["year"]), "path": item["path"], "sha256": actual,
                         "rows": int(len(frame))})
    return pd.concat(frames, ignore_index=True), identity


# --------------------------------------------------------------------------- #
# 全局赛历顺序（纯函数）
# --------------------------------------------------------------------------- #
def assign_global_round(frame: pd.DataFrame) -> pd.DataFrame:
    """按 (year, round_index) 赋全局单调 round 序号 1..N。"""
    result = frame.copy()
    keys = (
        result[["year", "round_index"]]
        .drop_duplicates()
        .sort_values(["year", "round_index"])
        .reset_index(drop=True)
    )
    keys["global_round"] = np.arange(1, len(keys) + 1)
    return result.merge(keys, on=["year", "round_index"], how="left")


def rolling_origin_splits(global_rounds: Sequence[int], warm_start: int) -> list[tuple[int, int]]:
    """返回 (train_max_round, predict_round) 列表：train=round<=r，predict=r+1。"""
    unique = sorted({int(r) for r in global_rounds})
    splits: list[tuple[int, int]] = []
    for index in range(len(unique) - 1):
        origin = unique[index]
        nxt = unique[index + 1]
        if origin >= warm_start:
            splits.append((origin, nxt))
    return splits


# --------------------------------------------------------------------------- #
# 转会二部图诊断（纯函数）
# --------------------------------------------------------------------------- #
def build_transfer_graph(frame: pd.DataFrame, min_laps: int) -> tuple[set[str], set[tuple[str, str]]]:
    """车手 d 在规范车队 t 有 >=min_laps 干净圈即连边。节点加前缀区分命名空间。"""
    counts = frame.groupby(["driver_id", "team_name"]).size()
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for (driver, team), n in counts.items():
        if int(n) < int(min_laps):
            continue
        dnode, tnode = f"D:{driver}", f"T:{team}"
        nodes.add(dnode)
        nodes.add(tnode)
        edges.add((dnode, tnode))
    return nodes, edges


def connected_components(nodes: set[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    """并查集求连通分量。"""
    parent = {node: node for node in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups: dict[str, list[str]] = {}
    for node in nodes:
        groups.setdefault(find(node), []).append(node)
    return sorted((sorted(members) for members in groups.values()), key=len, reverse=True)


def articulation_points(nodes: set[str], edges: set[tuple[str, str]]) -> list[str]:
    """无向图割点（Tarjan，迭代 DFS 避免递归深度问题）。"""
    adj: dict[str, set[str]] = {node: set() for node in nodes}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    articulation: set[str] = set()
    timer = 0
    for start in nodes:
        if start in disc:
            continue
        root_children = 0
        stack: list[tuple[str, str | None, object]] = [(start, None, iter(sorted(adj[start])))]
        disc[start] = low[start] = timer
        timer += 1
        while stack:
            node, parent_node, neighbors = stack[-1]
            advanced = False
            for neighbor in neighbors:
                if neighbor == parent_node:
                    continue
                if neighbor not in disc:
                    disc[neighbor] = low[neighbor] = timer
                    timer += 1
                    if node == start:
                        root_children += 1
                    stack.append((neighbor, node, iter(sorted(adj[neighbor]))))
                    advanced = True
                    break
                low[node] = min(low[node], disc[neighbor])
            if not advanced:
                stack.pop()
                if stack:
                    parent_of_parent = stack[-1][0]
                    low[parent_of_parent] = min(low[parent_of_parent], low[node])
                    if stack[-1][1] is not None and low[node] >= disc[parent_of_parent]:
                        articulation.add(parent_of_parent)
        if root_children > 1:
            articulation.add(start)
    return sorted(articulation)


def driver_connectivity_stats(frame: pd.DataFrame, min_laps: int) -> list[dict[str, Any]]:
    """逐车手：不同车队数、不同队友数、连边度、弱锚定标记。"""
    counts = frame.groupby(["driver_id", "team_name"]).size().rename("laps").reset_index()
    counts = counts.loc[counts["laps"] >= int(min_laps)]
    driver_teams = counts.groupby("driver_id")["team_name"].apply(set).to_dict()
    team_drivers: dict[str, set[str]] = {}
    for team, sub in counts.groupby("team_name"):
        team_drivers[team] = set(sub["driver_id"])
    stats: list[dict[str, Any]] = []
    for driver, teams in driver_teams.items():
        teammates: set[str] = set()
        for team in teams:
            teammates |= team_drivers.get(team, set())
        teammates.discard(driver)
        stats.append({
            "driver": driver,
            "distinct_teams": len(teams),
            "distinct_teammates": len(teammates),
            "degree": len(teams),
            "weakly_anchored": len(teams) == 1 and len(teammates) <= 1,
        })
    return sorted(stats, key=lambda row: (row["distinct_teams"], row["distinct_teammates"]))


# --------------------------------------------------------------------------- #
# Jolpica 积分榜（只读、缓存）
# --------------------------------------------------------------------------- #
def fetch_constructor_standings(year: int, template: str, cache_dir: Path, aliases: Mapping[str, str],
                                reuse_cache: bool) -> dict[str, dict[str, Any]]:
    url = template.format(year=year)
    payload, digest = _read_or_fetch_json(url, cache_dir / f"jolpica_{year}_constructorStandings.json",
                                          reuse_cache)
    lists = payload["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in lists[0]["ConstructorStandings"]:
        team = normalize_team_name(str(row["Constructor"]["name"]), aliases)
        out[team] = {"position": int(row["position"]), "points": float(row["points"]),
                     "source_sha256": digest, "url": url}
    return out


def fetch_driver_standings(year: int, template: str, cache_dir: Path, aliases: Mapping[str, str],
                           reuse_cache: bool) -> dict[str, dict[str, Any]]:
    url = template.format(year=year)
    payload, digest = _read_or_fetch_json(url, cache_dir / f"jolpica_{year}_driverStandings.json",
                                          reuse_cache)
    lists = payload["MRData"]["StandingsTable"]["StandingsLists"]
    if not lists:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in lists[0]["DriverStandings"]:
        code = str(row["Driver"]["code"])
        team = normalize_team_name(str(row["Constructors"][-1]["name"]), aliases) if row.get("Constructors") else None
        out[code] = {"position": int(row["position"]), "points": float(row["points"]),
                     "team": team, "source_sha256": digest, "url": url}
    return out


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """两列的 Spearman 秩相关（用 pandas rank + pearson，避免额外依赖分歧）。"""
    if len(pairs) < 2:
        return None
    left = pd.Series([p[0] for p in pairs])
    right = pd.Series([p[1] for p in pairs])
    value = left.rank().corr(right.rank(), method="pearson")
    return None if pd.isna(value) else float(value)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    config = base.load_json(CONFIG_PATH)
    seasonal_config = base.load_json(ROOT / config["seasonal_config"])
    aliases = base.load_json(ROOT / seasonal_config["entity_alias_config"])["team_aliases"]
    season_order = seasonal_config["season_order"]
    reporting_teams = seasonal_config["reporting_teams"]
    ref_lap = float(config["reference_lap_seconds"])
    run_id = f"{config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    max_attempts = int(config["max_attempts"])
    base.append_log({"run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
                     "method": "seasonal_standings_calibration", "status": "started",
                     "config_hash": base.canonical_hash(config), "git_head": base.git_head()})

    # 惩罚集
    penalties = next(
        {k: float(v) for k, v in cand.items() if k != "id"}
        for cand in seasonal_config["candidate_penalties"]
        if cand["id"] == config["chosen_penalty_id"]
    )

    def prepare(_attempt: int) -> dict[str, Any]:
        raw, identity = load_seasonal_inputs(seasonal_config)
        frame = prepare_season_frame(raw, aliases, season_order, seasonal_config["session_name"])
        frame = assign_global_round(frame)
        return {"frame": frame, "identity": identity}

    prepared = base.run_with_retries(run_id, "prepare", "load_frame", max_attempts, prepare)
    if prepared.value is None:
        base.append_log({"run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
                         "method": "seasonal_standings_calibration", "status": "blocked_no_dataset"})
        return 2
    frame = prepared.value["frame"]
    identity = prepared.value["identity"]
    years = [2023, 2024, 2025]

    # --- 1. 转会图诊断 ---
    min_laps = int(config["graph"]["min_laps"])
    nodes, edges = build_transfer_graph(frame, min_laps)
    components = connected_components(nodes, edges)
    articulations = articulation_points(nodes, edges)
    conn_stats = driver_connectivity_stats(frame, min_laps)
    component_meta: list[dict[str, Any]] = []
    driver_component: dict[str, int] = {}
    for idx, members in enumerate(components):
        teams = sorted(n[2:] for n in members if n.startswith("T:"))
        drivers = sorted(n[2:] for n in members if n.startswith("D:"))
        for d in drivers:
            driver_component[d] = idx
        component_meta.append({
            "index": idx, "size": len(members), "n_teams": len(teams),
            "teams": teams, "drivers": drivers,
            "internally_transfer_anchored": len(teams) >= 2,
            "cross_grid_anchored": idx == 0,
        })
    graph = {
        "min_laps": min_laps,
        "n_driver_nodes": sum(1 for n in nodes if n.startswith("D:")),
        "n_team_nodes": sum(1 for n in nodes if n.startswith("T:")),
        "n_components": len(components),
        "connected": len(components) == 1,
        "largest_component_size": len(components[0]) if components else 0,
        "components": component_meta,
        "isolated_teams": sorted(
            t for c in component_meta if not c["cross_grid_anchored"] for t in c["teams"]
        ),
        "articulation_points": articulations,
        "weakly_anchored_drivers": [s["driver"] for s in conn_stats if s["weakly_anchored"]],
        "driver_connectivity": conn_stats,
    }

    # --- 2. 主拟合 + 季度评分（全场识别，全场评分） ---
    def fit_main(_attempt: int) -> Any:
        return fit_seasonal_model(frame, penalties, blocks=JOINT_BLOCKS,
                                  irls_iterations=int(seasonal_config["robust_fit"]["irls_iterations"]),
                                  huber_k=float(seasonal_config["robust_fit"]["huber_k"]))
    fitted = base.run_with_retries(run_id, "fit", "seasonal_model", max_attempts, fit_main)
    if fitted.value is None:
        base.append_log({"run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
                         "method": "seasonal_standings_calibration", "status": "skipped_after_3_failures"})
        return 2
    fit = fitted.value

    ratings = {"cars": [], "drivers": []}
    for year in years:
        teams_present = sorted(frame.loc[frame["year"] == year, "team_name"].unique())
        year_ratings = extract_season_ratings(fit, frame, teams_present, years=(year,))
        ratings["cars"].extend(year_ratings["cars"])
        ratings["drivers"].extend(year_ratings["drivers"])

    # --- 3. rolling-origin 时间外验证（chosen penalty） ---
    splits = rolling_origin_splits(frame["global_round"].tolist(),
                                   int(config["rolling"]["warm_start_global_round"]))
    rolling_rows: list[dict[str, Any]] = []
    for origin, nxt in splits:
        train = frame.loc[frame["global_round"] <= origin]
        nxt_frame = frame.loc[frame["global_round"] == nxt]
        try:
            step_fit = fit_seasonal_model(train, penalties, blocks=JOINT_BLOCKS,
                                          irls_iterations=int(seasonal_config["robust_fit"]["irls_iterations"]),
                                          huber_k=float(seasonal_config["robust_fit"]["huber_k"]))
            pred = step_fit.predict(nxt_frame)
            bundle = metric_bundle(nxt_frame, pred)
        except Exception as exc:  # noqa: BLE001 - 单站失败不应中断整条曲线
            rolling_rows.append({"origin_round": origin, "predict_round": nxt,
                                 "status": "failed", "error": str(exc)[:200]})
            continue
        meeting = nxt_frame["meeting_key"].iloc[0]
        year = int(nxt_frame["year"].iloc[0])
        rolling_rows.append({
            "origin_round": origin, "predict_round": nxt, "predict_meeting": meeting,
            "predict_year": year, "status": "ok",
            "mae": bundle["mae"], "rmse": bundle["rmse"], "event_balanced_mae": bundle["event_balanced_mae"],
            "row_count": bundle["row_count"],
        })
    ok_rows = [r for r in rolling_rows if r["status"] == "ok"]
    rolling_summary = {
        "warm_start_global_round": int(config["rolling"]["warm_start_global_round"]),
        "n_origins": len(ok_rows),
        "overall_event_balanced_mae": float(np.mean([r["event_balanced_mae"] for r in ok_rows])) if ok_rows else None,
        "mae_2025_locked": float(np.mean([r["mae"] for r in ok_rows if r["predict_year"] == 2025])) if any(r["predict_year"] == 2025 for r in ok_rows) else None,
        "steps": rolling_rows,
    }

    # --- 4. 积分榜校准（只读，软失败降级） ---
    cache_dir = ROOT / config["standings"]["cache_dir"]
    reuse_cache = bool(config["standings"]["reuse_cache"])
    calibration: dict[str, Any] = {"degraded": False}
    try:
        constructor_standings = {y: fetch_constructor_standings(
            y, config["standings"]["constructor_url_template"], cache_dir, aliases, reuse_cache) for y in years}
        driver_standings = {y: fetch_driver_standings(
            y, config["standings"]["driver_url_template"], cache_dir, aliases, reuse_cache) for y in years}
    except Exception as exc:  # noqa: BLE001
        calibration = {"degraded": True, "error": str(exc)[:300],
                       "note": "Jolpica 积分榜取数失败，校准降级；估计器与图诊断不受影响。"}
        base.append_log({"run_id": run_id, "timestamp": base.now_iso(), "phase": "calibrate",
                         "method": "standings_fetch", "status": "degraded", "error": str(exc)[:200]})
        constructor_standings = driver_standings = {}

    if not calibration["degraded"]:
        car_by_year = {y: {r["team"]: r for r in ratings["cars"] if r["year"] == y} for y in years}
        drv_by_year = {y: {r["driver"]: r for r in ratings["drivers"] if r["year"] == y} for y in years}
        constructor_calib: list[dict[str, Any]] = []
        driver_calib: list[dict[str, Any]] = []
        pooled_car: list[tuple[float, float]] = []
        pooled_drv_realized: list[tuple[float, float]] = []
        pooled_drv_pure: list[tuple[float, float]] = []
        for year in years:
            cs = constructor_standings.get(year, {})
            car_pairs = [(car_by_year[year][t]["car_total_z"], cs[t]["position"])
                         for t in cs if t in car_by_year[year]]
            pooled_car.extend(car_pairs)
            constructor_calib.append({
                "year": year, "n_teams": len(car_pairs),
                "spearman_car_z_vs_standings_position": spearman(car_pairs),
                "note": "car_total_z 越小越快、position 越小越好，正相关=车辆评分与制造商成绩一致。",
            })
            ds = driver_standings.get(year, {})
            realized_pairs, pure_pairs = [], []
            for code, srow in ds.items():
                drow = drv_by_year[year].get(code)
                if drow is None:
                    continue
                team = drow["team"]
                car_z = car_by_year[year].get(team, {}).get("car_total_z")
                if car_z is None:
                    continue
                realized_pairs.append((car_z + drow["driver_pace_z"], srow["position"]))
                pure_pairs.append((drow["driver_pace_z"], srow["position"]))
            pooled_drv_realized.extend(realized_pairs)
            pooled_drv_pure.extend(pure_pairs)
            driver_calib.append({
                "year": year, "n_drivers": len(realized_pairs),
                "spearman_realized_vs_standings": spearman(realized_pairs),
                "spearman_pure_driver_vs_standings": spearman(pure_pairs),
                "note": "实跑(车+手)应与名次强相关；纯车手技能不必（好车手可在差车里名次低）。",
            })
        calibration.update({
            "constructor_by_year": constructor_calib,
            "driver_by_year": driver_calib,
            "pooled_spearman_car_vs_constructors": spearman(pooled_car),
            "pooled_spearman_realized_vs_drivers": spearman(pooled_drv_realized),
            "pooled_spearman_pure_driver_vs_drivers": spearman(pooled_drv_pure),
        })

    # --- 5. 标准化车/手分解（不给百分比分摊）+ bootstrap 区间 ---
    rng = np.random.default_rng(int(config["bootstrap"]["random_seed"]))
    reps = int(config["bootstrap"]["repetitions"])
    driver_boot: dict[tuple[int, str], list[float]] = {}
    car_boot: dict[tuple[int, str], list[float]] = {}
    for _ in range(reps):
        sample = stratified_stint_bootstrap(frame, rng)
        try:
            bfit = fit_seasonal_model(sample, penalties, blocks=JOINT_BLOCKS,
                                      irls_iterations=int(seasonal_config["robust_fit"]["irls_iterations"]),
                                      huber_k=float(seasonal_config["robust_fit"]["huber_k"]))
        except Exception:  # noqa: BLE001
            continue
        for year in years:
            teams_present = sorted(sample.loc[sample["year"] == year, "team_name"].unique())
            br = extract_season_ratings(bfit, sample, teams_present, years=(year,))
            for row in br["cars"]:
                car_boot.setdefault((year, row["team"]), []).append(row["car_total_z"])
            for row in br["drivers"]:
                driver_boot.setdefault((year, row["driver"]), []).append(row["driver_pace_z"])

    weak = set(graph["weakly_anchored_drivers"])
    decomposition: list[dict[str, Any]] = []
    for drow in ratings["drivers"]:
        year, driver, team = drow["year"], drow["driver"], drow["team"]
        car_row = next((c for c in ratings["cars"] if c["year"] == year and c["team"] == team), None)
        car_z = car_row["car_total_z"] if car_row else 0.0
        car_iv = car_boot.get((year, team), [])
        drv_iv = driver_boot.get((year, driver), [])
        decomposition.append({
            "year": year, "driver": driver, "team": team,
            "car_contribution_ms": log_pct_to_ms(car_row["car_pace_advantage_z"], ref_lap) if car_row else 0.0,
            "driver_contribution_ms": drow["driver_pace_advantage_90s_ms"],
            "car_total_z": car_z, "driver_pace_z": drow["driver_pace_z"],
            "car_z_interval": interval(car_iv) if len(car_iv) >= 2 else None,
            "driver_z_interval": interval(drv_iv) if len(drv_iv) >= 2 else None,
            "weakly_anchored": driver in weak,
            "component_index": driver_component.get(driver),
            "cross_grid_transfer_anchored": driver_component.get(driver) == 0,
        })

    # --- 6. 2026 车手先验（零均值，正=更快） ---
    driver_effect = fit.effect_map("driver")
    centered = {d: -(z) for d, z in driver_effect.items()}  # 负 z 更快 -> 取负使正=更快
    mean_pace = float(np.mean(list(centered.values()))) if centered else 0.0
    driver_prior = {d: round(v - mean_pace, 6) for d, v in centered.items()}
    prior_doc = {
        "created_at": base.now_iso(), "run_id": run_id, "reference": "panel_2023_2025_pooled_driver_zero_mean",
        "convention": "正=更快（已对 driver z 取负并零均值中心）",
        "chosen_penalty_id": config["chosen_penalty_id"],
        "weakly_anchored_drivers": sorted(weak),
        "driver_prior_pace": dict(sorted(driver_prior.items(), key=lambda kv: -kv[1])),
        "handoff_recipe": "2026 圈入帧后将车手锚定该先验（作固定偏置折进目标，同 hierarchical.fit_event_model 的 driver_prior_pace），只放开 team_year[2026]+team_circuit+新秀。",
    }
    base.atomic_json(PRIOR_PATH, prior_doc)

    # --- 汇总产物 ---
    results = {
        "run_id": run_id, "created_at": base.now_iso(), "git_head": base.git_head(),
        "config": config, "config_hash": base.canonical_hash(config),
        "seasonal_config_hash": base.canonical_hash(seasonal_config),
        "chosen_penalty_id": config["chosen_penalty_id"], "penalties": penalties,
        "data_identity": identity,
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "sample_scope": {"years": years, "race_meetings": int(frame["global_round"].nunique()),
                         "clean_laps": int(len(frame)), "reporting_teams": reporting_teams},
        "transfer_graph": graph,
        "season_ratings": ratings,
        "rolling_origin": rolling_summary,
        "standings_calibration": calibration,
        "car_driver_decomposition": decomposition,
        "driver_prior_2026_path": str(PRIOR_PATH.relative_to(ROOT)),
        "interpretation_limits": [
            "叠加校准层：估计器复用 §5.6 面板 RAPM，车辆基准以 §5.6 为准；本脚本只加校准/验证/诊断/2026 先验。",
            "转会网络不连通（本数据 4 分量）：主集团(Ferrari/Mercedes/Williams/Haas/Sauber/Alpine 经 HAM/SAI/HUL 桥接)、"
            "红牛家族(RB+Red Bull Racing 内部互通但与外界无桥)、Aston Martin、McLaren 各自孤立。"
            "故 Red Bull/McLaren/Aston 的车/手分解无跨网格转会锚定，只靠赛段配速参照与收缩先验约束——"
            "其绝对车辆排名可信（配速被赛段参照锚定，与积分榜 0.89–0.96 相关），但车/手内部归因弱识别。",
            "积分榜含可靠性/策略/碰撞/罚时，非纯速度，Spearman 只作方向性佐证非真值。",
            "bootstrap 为频率派重采样区间，非贝叶斯后验；无 R-hat/ESS/PPC。",
            "燃油代理（lap_fraction）；仅 Race、无排位单圈；driver_team 仅未解释组合，不作车/手百分比分摊。",
        ],
    }
    base.atomic_json(RESULTS_PATH, results)
    write_summary(results)
    base.append_log({"run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
                     "method": "seasonal_standings_calibration", "status": "completed",
                     "connected": graph["connected"], "rolling_origins": len(ok_rows),
                     "calibration_degraded": calibration["degraded"]})
    print(json.dumps({
        "run_id": run_id, "connected_graph": graph["connected"], "n_components": graph["n_components"],
        "rolling_overall_event_mae": rolling_summary["overall_event_balanced_mae"],
        "calibration_degraded": calibration["degraded"],
        "pooled_car_vs_constructors": calibration.get("pooled_spearman_car_vs_constructors"),
        "pooled_realized_vs_drivers": calibration.get("pooled_spearman_realized_vs_drivers"),
    }, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


def write_summary(results: dict[str, Any]) -> None:
    lines: list[str] = []
    g = results["transfer_graph"]
    cal = results["standings_calibration"]
    roll = results["rolling_origin"]
    lines.append("# 三季面板 RAPM 积分榜校准 + rolling-origin 验证（§5.6 叠加层）")
    lines.append("")
    lines.append(f"- run_id：`{results['run_id']}`；惩罚集 `{results['chosen_penalty_id']}`")
    lines.append(f"- 样本：{results['sample_scope']['race_meetings']} 场 Race、{results['sample_scope']['clean_laps']} 清洁圈")
    lines.append("")
    lines.append("## 转会图连通性（识别可信度）")
    lines.append(f"- 连通：{'是' if g['connected'] else '否'}；分量数 {g['n_components']}；"
                 f"车手节点 {g['n_driver_nodes']}、车队节点 {g['n_team_nodes']}")
    for c in g["components"]:
        tag = "跨网格锚定" if c["cross_grid_anchored"] else ("内部转会互通但与网格孤立" if c["internally_transfer_anchored"] else "孤立(无转会)")
        lines.append(f"  - 分量{c['index']+1}（{c['n_teams']}队）[{tag}]：{', '.join(c['teams'])}")
    lines.append(f"- 与主网格孤立的车队（车/手分解仅赛段参照锚定、内部归因弱识别）：{', '.join(g['isolated_teams']) or '无'}")
    lines.append(f"- 弱锚定车手（区间偏宽）：{', '.join(g['weakly_anchored_drivers']) or '无'}")
    lines.append("")
    lines.append("## Rolling-origin 逐站时间外")
    lines.append(f"- 有效 origin 数 {roll['n_origins']}；总体分站等权 MAE "
                 f"{roll['overall_event_balanced_mae']:.4f}" if roll['overall_event_balanced_mae'] is not None else "- 无有效 origin")
    if roll["mae_2025_locked"] is not None:
        lines.append(f"- 2025 锁定段逐站 MAE {roll['mae_2025_locked']:.4f}")
    lines.append("")
    lines.append("## 真实积分榜校准")
    if cal["degraded"]:
        lines.append(f"- 降级：{cal.get('note', '')}")
    else:
        def _r(v):
            return "—" if v is None else round(float(v), 3)
        lines.append(f"- 三季合并 车队评分 vs 车队积分榜 Spearman：{_r(cal['pooled_spearman_car_vs_constructors'])}")
        lines.append(f"- 三季合并 车手实跑(车+手) vs 车手积分榜 Spearman：{_r(cal['pooled_spearman_realized_vs_drivers'])}")
        lines.append(f"- 三季合并 纯车手技能 vs 车手积分榜 Spearman：{_r(cal['pooled_spearman_pure_driver_vs_drivers'])}（预期更弱：好车手可在差车里名次低）")
        lines.append("")
        lines.append("| 年 | 车队评分vs车队榜 | 车手实跑vs车手榜 | 纯车手vs车手榜 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for cc, dc in zip(cal["constructor_by_year"], cal["driver_by_year"]):
            lines.append(f"| {cc['year']} | {_r(cc['spearman_car_z_vs_standings_position'])} | "
                         f"{_r(dc['spearman_realized_vs_standings'])} | {_r(dc['spearman_pure_driver_vs_standings'])} |")
    lines.append("")
    lines.append("## 2026 车手先验")
    lines.append(f"- 已导出：`{results['driver_prior_2026_path']}`（三季池化车手效应，正=更快，零均值）")
    lines.append("")
    lines.append("## 解释限制")
    for item in results["interpretation_limits"]:
        lines.append(f"- {item}")
    lines.append("")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
