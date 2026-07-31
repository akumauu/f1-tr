#!/usr/bin/env python3
"""单站四大车队弯段级机理归因组件（从属于 four-team 主规范，非独立车辆基准来源）。

定位：本脚本是 `docs/four-team-car-driver-attribution-model.md` 主线下的从属机理组件，
提供 5.4 全场联合模型缺少的"差在哪个环节"这一层。直接读单站采样级遥测
（distance/speed/throttle/brake…），在距离域对齐后做：
  Stage 1 车辆能力包络（排除车手，前沿分位 + 理论最优圈拼接两种上限 + 敏感性）；
  Stage 2 车手发挥定位（排除车辆，落在同队车包络的百分比）；
  Stage 3 跨队基准比较 + 成绩归因（车/手各占多少）。

严谨定位：条件关联非因果真值；同队前沿仅部分识别车辆上限（一名车手异常出色会把其技能
误并入车辆上限），车辆基准以全场联合模型 `run_four_team_baseline.py` 为准；本组件价值在
弯段机理定位。单站=赛道特定，未跨赛道归一；不进入 2026。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

import run_experiments as base

ROOT = base.ROOT
CONFIG_PATH = ROOT / "research" / "configs" / "car_driver_envelope_spain_2025.json"
MANIFEST_PATH = ROOT / "research" / "artifacts" / "car_driver_envelope_manifest.json"
RESULTS_PATH = base.RECORDS_DIR / "car_driver_envelope_results.json"
SUMMARY_PATH = base.RECORDS_DIR / "car_driver_envelope_summary.md"
FIG_DIR = ROOT / "research" / "artifacts" / "figures"

KMH_TO_MS = 1000.0 / 3600.0


# --------------------------------------------------------------------------- #
# 路径解析与数据加载
# --------------------------------------------------------------------------- #
def resolve_parquet(config: dict[str, Any]) -> tuple[Path, str, str]:
    """按年度 manifest 解析单站 parquet 路径与其 parquet_sha256（溯源）。"""
    root = ROOT / config["parquet_root"]
    manifests = sorted((root / f"year={config['year']}").glob("commit=*/manifest.json"))
    if not manifests:
        raise FileNotFoundError(f"未找到 year={config['year']} 的 manifest.json")
    manifest_path = manifests[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "tracinginsights-expanded-v4":
        raise ValueError(f"schema_version 不符: {manifest.get('schema_version')}")
    rows = [
        row
        for row in manifest["partitions"]
        if row["session"] == config["session"]
        and row["meeting"] == config["meeting_manifest"]
    ]
    if not rows:
        raise FileNotFoundError(
            f"manifest 中无 {config['meeting_manifest']} / {config['session']}"
        )
    row = rows[0]
    parquet_path = manifest_path.parent / row["parquet_path"]
    if not parquet_path.exists():
        raise FileNotFoundError(f"解析出的 parquet 不存在: {parquet_path}")
    return parquet_path, row["parquet_sha256"], manifest["source_commit"]


def load_samples(parquet_path: Path, config: dict[str, Any]) -> pd.DataFrame:
    """duckdb 只读该单站四强 parquet，返回采样级 DataFrame。"""
    teams = list(config["teams"])
    placeholders = ",".join(["?"] * len(teams))
    connection = duckdb.connect()
    try:
        connection.execute("SET threads TO 4")
        query = f"""
            SELECT team, driver_acronym, driver_number, lap, sample_index,
                   distance, speed, throttle, brake, track_status,
                   compound, tyre_life, lap_duration, is_accurate, deleted,
                   distance_to_driver_ahead
            FROM read_parquet(?, hive_partitioning=false)
            WHERE team IN ({placeholders})
            ORDER BY driver_number, lap, sample_index
        """
        frame = connection.execute(query, [str(parquet_path), *teams]).fetch_df()
    finally:
        connection.close()
    return frame


# --------------------------------------------------------------------------- #
# 圈级清洗（纯函数）
# --------------------------------------------------------------------------- #
def lap_level_frame(samples: pd.DataFrame) -> pd.DataFrame:
    """把采样级聚合成逐圈一行，供清洗使用。"""
    groups = samples.groupby(["team", "driver_acronym", "driver_number", "lap"], dropna=False)
    lap_frame = groups.agg(
        lap_duration=("lap_duration", "first"),
        compound=("compound", "first"),
        is_accurate=("is_accurate", "first"),
        deleted=("deleted", "first"),
        tyre_life=("tyre_life", "first"),
        max_distance=("distance", "max"),
        median_gap=("distance_to_driver_ahead", "median"),
        n_samples=("sample_index", "count"),
    ).reset_index()
    all_green = (
        samples.assign(green=lambda d: d["track_status"].astype(str) == "1")
        .groupby(["driver_number", "lap"])["green"]
        .all()
        .rename("all_green")
        .reset_index()
    )
    lap_frame = lap_frame.merge(all_green, on=["driver_number", "lap"], how="left")
    return lap_frame


def select_clean_laps(lap_frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """应用清洗规则，返回带 is_clean / is_dirty_air 标记的圈级帧。"""
    clean_cfg = config["cleaning"]
    dry = set(config["dry_compounds"])
    frame = lap_frame.copy()
    base_ok = (
        frame["is_accurate"].fillna(False).astype(bool)
        & ~frame["deleted"].fillna(False).astype(bool)
        & frame["all_green"].fillna(False).astype(bool)
        & frame["compound"].isin(dry)
        & (frame["lap"] >= int(clean_cfg["minimum_lap_number"]))
        & (frame["lap_duration"] >= float(clean_cfg["minimum_lap_duration_seconds"]))
        & (frame["lap_duration"] <= float(clean_cfg["maximum_lap_duration_seconds"]))
    )
    frame["base_ok"] = base_ok
    # 逐车手 stint 内 MAD 稳健异常过滤（仅在 base_ok 上）
    keep = base_ok.copy()
    multiplier = float(clean_cfg["robust_mad_multiplier"])
    for driver, sub in frame[base_ok].groupby("driver_number"):
        durations = sub["lap_duration"].to_numpy(dtype=float)
        median = np.median(durations)
        mad = np.median(np.abs(durations - median))
        scale = mad if mad > 1e-9 else 1.0
        within = np.abs(durations - median) <= multiplier * 1.4826 * scale
        keep.loc[sub.index] = within
    frame["is_clean"] = keep
    frame["is_dirty_air"] = frame["median_gap"] < float(clean_cfg["traffic_min_median_gap_m"])
    if bool(clean_cfg.get("traffic_exclude", False)):
        frame["is_clean"] = frame["is_clean"] & ~frame["is_dirty_air"]
    return frame


# --------------------------------------------------------------------------- #
# 距离域重采样（纯函数）
# --------------------------------------------------------------------------- #
def resample_lap(distance: np.ndarray, values: np.ndarray, grid: np.ndarray,
                 discrete: bool = False) -> np.ndarray:
    """把一圈 (distance, values) 重采样到共享 grid。连续量线性插值；离散量前向保持。"""
    order = np.argsort(distance, kind="stable")
    dsorted = distance[order]
    vsorted = values[order]
    unique_mask = np.concatenate(([True], np.diff(dsorted) > 0))
    dsorted = dsorted[unique_mask]
    vsorted = vsorted[unique_mask]
    if dsorted.size < 2:
        return np.full(grid.shape, np.nan)
    if discrete:
        idx = np.searchsorted(dsorted, grid, side="right") - 1
        idx = np.clip(idx, 0, dsorted.size - 1)
        return vsorted[idx]
    return np.interp(grid, dsorted, vsorted, left=vsorted[0], right=vsorted[-1])


def build_lap_matrices(samples: pd.DataFrame, clean_laps: pd.DataFrame,
                       grid: np.ndarray) -> dict[str, dict[str, Any]]:
    """对每名车手的每个 clean 圈重采样 speed/throttle/brake，返回矩阵。"""
    clean_keys = set(
        map(tuple, clean_laps.loc[clean_laps["is_clean"], ["driver_number", "lap"]].to_numpy())
    )
    result: dict[str, dict[str, Any]] = {}
    for (driver, team), sub in samples.groupby(["driver_acronym", "team"], dropna=False):
        speed_rows, throttle_rows, brake_rows, lap_ids = [], [], [], []
        for lap, lap_samples in sub.groupby("lap"):
            if (int(lap_samples["driver_number"].iloc[0]), int(lap)) not in clean_keys:
                continue
            distance = lap_samples["distance"].to_numpy(dtype=float)
            speed = resample_lap(distance, lap_samples["speed"].to_numpy(dtype=float), grid)
            if not np.all(np.isfinite(speed)) or np.nanmin(speed) <= 0:
                continue
            speed_rows.append(speed)
            throttle_rows.append(
                resample_lap(distance, lap_samples["throttle"].to_numpy(dtype=float), grid)
            )
            brake_rows.append(
                resample_lap(distance, lap_samples["brake"].to_numpy(dtype=float), grid,
                             discrete=True)
            )
            lap_ids.append(int(lap))
        if speed_rows:
            result[driver] = {
                "team": team,
                "speed": np.vstack(speed_rows),
                "throttle": np.vstack(throttle_rows),
                "brake": np.vstack(brake_rows),
                "laps": lap_ids,
            }
    return result


# --------------------------------------------------------------------------- #
# Δt 恒等式与 mini-sector 时间（纯函数，核心）
# --------------------------------------------------------------------------- #
def integrate_lap_time(speed_kmh: np.ndarray, grid: np.ndarray) -> float:
    """由 speed(distance) 积分出圈时（秒）。dt = ds / v，段用两端速度均值。"""
    ds = np.diff(grid)
    v_ms = np.clip((speed_kmh[:-1] + speed_kmh[1:]) / 2.0, 1e-6, None) * KMH_TO_MS
    return float(np.sum(ds / v_ms))


def segment_delta_time(speed_a: np.ndarray, speed_b: np.ndarray,
                       grid: np.ndarray) -> tuple[np.ndarray, float]:
    """逐段圈时差 dt_a - dt_b，返回 (逐段差数组, 总差)。总差恒等于逐段差之和。"""
    ds = np.diff(grid)
    va = np.clip((speed_a[:-1] + speed_a[1:]) / 2.0, 1e-6, None) * KMH_TO_MS
    vb = np.clip((speed_b[:-1] + speed_b[1:]) / 2.0, 1e-6, None) * KMH_TO_MS
    per_segment = ds / va - ds / vb
    return per_segment, float(np.sum(per_segment))


def sector_ids_for_grid(grid: np.ndarray, n_sectors: int) -> np.ndarray:
    """把 grid 的 N-1 个段等距分到 n_sectors 个 mini-sector，返回逐段的 sector 索引。"""
    seg_mid = (grid[:-1] + grid[1:]) / 2.0
    span = grid[-1] - grid[0]
    ids = np.floor((seg_mid - grid[0]) / span * n_sectors).astype(int)
    return np.clip(ids, 0, n_sectors - 1)


def lap_sector_times(speed_kmh: np.ndarray, grid: np.ndarray,
                     sector_ids: np.ndarray, n_sectors: int) -> np.ndarray:
    """单圈每个 mini-sector 的用时（秒）。"""
    ds = np.diff(grid)
    v_ms = np.clip((speed_kmh[:-1] + speed_kmh[1:]) / 2.0, 1e-6, None) * KMH_TO_MS
    seg_time = ds / v_ms
    return np.bincount(sector_ids, weights=seg_time, minlength=n_sectors)


def matrix_sector_times(speed_matrix: np.ndarray, grid: np.ndarray,
                        sector_ids: np.ndarray, n_sectors: int) -> np.ndarray:
    """多圈 speed 矩阵 -> [n_laps, n_sectors] 的 sector 时间矩阵。"""
    return np.vstack([
        lap_sector_times(speed_matrix[i], grid, sector_ids, n_sectors)
        for i in range(speed_matrix.shape[0])
    ])


# --------------------------------------------------------------------------- #
# 弯型标注
# --------------------------------------------------------------------------- #
def corner_type_labels(ref_speed: np.ndarray, grid: np.ndarray, sector_ids: np.ndarray,
                       n_sectors: int, config: dict[str, Any]) -> list[str]:
    """按参照速度给每个 mini-sector 打弯型标签。"""
    bins = config["corner_speed_bins_kmh"]
    low_max = float(bins["low_max"])
    medium_max = float(bins["medium_max"])
    full_throttle = float(config["cleaning"]["full_throttle_threshold_pct"])
    ref_seg = (ref_speed[:-1] + ref_speed[1:]) / 2.0
    labels: list[str] = []
    for sector in range(n_sectors):
        mask = sector_ids == sector
        if not np.any(mask):
            labels.append("unknown")
            continue
        seg_speeds = ref_seg[mask]
        min_speed = float(np.min(seg_speeds))
        median_speed = float(np.median(seg_speeds))
        if min_speed < low_max:
            labels.append("low_speed_corner")
        elif min_speed < medium_max:
            labels.append("medium_speed_corner")
        elif median_speed >= medium_max:
            labels.append("straight_or_flat")
        else:
            labels.append("high_speed_corner")
    return labels


# --------------------------------------------------------------------------- #
# Stage 1 车辆能力包络 / Stage 2 车手发挥 / Stage 3 跨队与归因
# --------------------------------------------------------------------------- #
def car_envelope(team_driver_sectors: dict[str, np.ndarray], config: dict[str, Any]) -> dict[str, Any]:
    """给定一支队两名车手的 sector 时间矩阵，算车辆能力包络（两种上限 + 下限）。"""
    q_ceiling = float(config["ceiling_sector_quantile"])
    q_floor = float(config["floor_sector_quantile"])
    pooled = np.vstack(list(team_driver_sectors.values()))
    ceiling_frontier = np.quantile(pooled, q_ceiling, axis=0)
    floor = np.quantile(pooled, q_floor, axis=0)
    per_driver_best = {
        driver: np.quantile(mat, q_ceiling, axis=0)
        for driver, mat in team_driver_sectors.items()
    }
    ceiling_ideal = np.min(np.vstack(list(per_driver_best.values())), axis=0)
    return {
        "ceiling_frontier_p90": ceiling_frontier,
        "ceiling_ideal_lap_splice": ceiling_ideal,
        "floor": floor,
        "per_driver_best": per_driver_best,
    }


def driver_extraction(driver_sectors: np.ndarray, ceiling: np.ndarray,
                      floor: np.ndarray, best_quantile: float) -> dict[str, np.ndarray]:
    """车手每 sector 的发挥定位（100%=贴上限，0%=下限）。

    发挥%用车手自身低油最优（同 ceiling 的分位）对齐车辆上限（同为低油口径），
    以抵消燃油/胎龄状态差异；另返回中位实跑作为典型比赛节奏。
    """
    realized_best = np.quantile(driver_sectors, best_quantile, axis=0)
    realized_median = np.median(driver_sectors, axis=0)
    span = floor - ceiling
    span = np.where(np.abs(span) < 1e-9, 1e-9, span)
    pct = (floor - realized_best) / span * 100.0
    return {
        "realized_best_sector_time": realized_best,
        "realized_sector_time": realized_median,
        "extraction_pct": pct,
    }


def aggregate_by_channel(sector_values: np.ndarray, labels: list[str]) -> dict[str, float]:
    """把逐 sector 的量（时间）按弯型聚合求和。"""
    out: dict[str, float] = {}
    for label, value in zip(labels, sector_values):
        out[label] = out.get(label, 0.0) + float(value)
    return out


def _headline_from_sector_mats(driver_sector_mats: dict[str, np.ndarray],
                               driver_team: dict[str, str], teams: list[str],
                               reference: str, config: dict[str, Any]) -> dict[str, Any]:
    """由每车手 sector 时间矩阵算头部标量（上限圈时、实跑、归因），供点估计与 bootstrap 共用。"""
    q_ceiling = float(config["ceiling_sector_quantile"])
    ceiling_laptime: dict[str, float] = {}
    realized_laptime: dict[str, float] = {}
    for team in teams:
        mats = [m for d, m in driver_sector_mats.items() if driver_team[d] == team]
        pooled = np.vstack(mats)
        ceiling_laptime[team] = float(np.sum(np.quantile(pooled, q_ceiling, axis=0)))
        realized_laptime[team] = min(float(np.sum(np.median(m, axis=0))) for m in mats)
    attribution: dict[str, dict[str, float]] = {}
    for team in teams:
        if team == reference:
            continue
        realized_gap = realized_laptime[team] - realized_laptime[reference]
        ceiling_gap = ceiling_laptime[team] - ceiling_laptime[reference]
        extraction_gap = realized_gap - ceiling_gap
        denom = abs(ceiling_gap) + abs(extraction_gap)
        car_share = abs(ceiling_gap) / denom * 100.0 if denom > 1e-9 else float("nan")
        attribution[team] = {
            "realized_gap_s": realized_gap,
            "ceiling_gap_s": ceiling_gap,
            "extraction_gap_s": extraction_gap,
            "car_share_pct": car_share,
        }
    return {"ceiling_laptime": ceiling_laptime, "realized_laptime": realized_laptime,
            "attribution": attribution}


def bootstrap_headline_ci(driver_sector_mats: dict[str, np.ndarray],
                          driver_team: dict[str, str], teams: list[str],
                          reference: str, config: dict[str, Any]) -> dict[str, Any]:
    """逐圈 block bootstrap（按车手重采样其 clean 圈）给头部量的 80%/95% CI。"""
    n = int(config["bootstrap_n"])
    rng = np.random.default_rng(int(config["random_seed"]))
    ceiling_samples: dict[str, list[float]] = {t: [] for t in teams}
    car_share_samples: dict[str, list[float]] = {t: [] for t in teams if t != reference}
    ceiling_gap_samples: dict[str, list[float]] = {t: [] for t in teams if t != reference}
    for _ in range(n):
        resampled = {
            d: m[rng.integers(0, m.shape[0], m.shape[0])]
            for d, m in driver_sector_mats.items()
        }
        head = _headline_from_sector_mats(resampled, driver_team, teams, reference, config)
        for team in teams:
            ceiling_samples[team].append(head["ceiling_laptime"][team])
        for team, attr in head["attribution"].items():
            car_share_samples[team].append(attr["car_share_pct"])
            ceiling_gap_samples[team].append(attr["ceiling_gap_s"])

    def ci(values: list[float]) -> dict[str, float]:
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return {"p2_5": float("nan"), "p10": float("nan"), "p90": float("nan"),
                    "p97_5": float("nan")}
        return {
            "p2_5": float(np.percentile(arr, 2.5)),
            "p10": float(np.percentile(arr, 10.0)),
            "p90": float(np.percentile(arr, 90.0)),
            "p97_5": float(np.percentile(arr, 97.5)),
        }

    return {
        "bootstrap_n": n,
        "ceiling_laptime_ci": {t: ci(v) for t, v in ceiling_samples.items()},
        "car_share_pct_ci": {t: ci(v) for t, v in car_share_samples.items()},
        "ceiling_gap_s_ci": {t: ci(v) for t, v in ceiling_gap_samples.items()},
    }


# --------------------------------------------------------------------------- #
# 汇总与产物
# --------------------------------------------------------------------------- #
def build_results(config: dict[str, Any], provenance: dict[str, Any],
                  clean_laps: pd.DataFrame, lap_mats: dict[str, dict[str, Any]],
                  grid: np.ndarray, run_id: str) -> dict[str, Any]:
    n_sectors = int(config["mini_sectors"])
    sector_ids = sector_ids_for_grid(grid, n_sectors)
    ceiling_method = "ceiling_frontier_p90"

    # 参照速度曲线（全 8 车池化中位）与弯型标签
    all_speed = np.vstack([m["speed"] for m in lap_mats.values()])
    ref_speed = np.median(all_speed, axis=0)
    labels = corner_type_labels(ref_speed, grid, sector_ids, n_sectors, config)

    # 每车手 sector 时间矩阵
    driver_sector_mats = {
        driver: matrix_sector_times(m["speed"], grid, sector_ids, n_sectors)
        for driver, m in lap_mats.items()
    }
    driver_team = {driver: m["team"] for driver, m in lap_mats.items()}
    teams = list(dict.fromkeys(driver_team.values()))

    # Stage 1: 每队能力包络
    car_env: dict[str, Any] = {}
    for team in teams:
        team_drivers = {d: mat for d, mat in driver_sector_mats.items() if driver_team[d] == team}
        env = car_envelope(team_drivers, config)
        car_env[team] = {
            "drivers": list(team_drivers.keys()),
            "ceiling_frontier_p90_laptime": float(np.sum(env["ceiling_frontier_p90"])),
            "ceiling_ideal_lap_splice_laptime": float(np.sum(env["ceiling_ideal_lap_splice"])),
            "floor_laptime": float(np.sum(env["floor"])),
            "ceiling_by_channel": aggregate_by_channel(env["ceiling_frontier_p90"], labels),
            "ceiling_sensitivity_ideal_minus_frontier_s": float(
                np.sum(env["ceiling_ideal_lap_splice"]) - np.sum(env["ceiling_frontier_p90"])
            ),
            "_ceiling_vec": env[ceiling_method],
            "_floor_vec": env["floor"],
        }

    # Stage 2: 车手发挥定位
    q_ceiling = float(config["ceiling_sector_quantile"])
    driver_extr: dict[str, Any] = {}
    for driver, mat in driver_sector_mats.items():
        team = driver_team[driver]
        ext = driver_extraction(mat, car_env[team]["_ceiling_vec"], car_env[team]["_floor_vec"],
                                q_ceiling)
        realized_median_lap = float(np.sum(ext["realized_sector_time"]))
        realized_best_lap = float(np.sum(ext["realized_best_sector_time"]))
        ceiling_lap = float(np.sum(car_env[team]["_ceiling_vec"]))
        counts = aggregate_by_channel(np.ones(n_sectors), labels)
        pct_sum_by_channel = aggregate_by_channel(ext["extraction_pct"], labels)
        driver_extr[driver] = {
            "team": team,
            "clean_laps_used": int(mat.shape[0]),
            "median_realized_laptime": realized_median_lap,
            "best_realized_laptime": realized_best_lap,
            "peak_gap_to_car_ceiling_s": realized_best_lap - ceiling_lap,
            "overall_extraction_pct": float(
                np.mean(np.clip(ext["extraction_pct"], -50, 150))
            ),
            "extraction_pct_by_channel": {
                k: pct_sum_by_channel[k] / counts[k] for k in counts
            },
        }

    # Stage 3: 跨队比较 + 成绩归因（以 ceiling 最快队为基准）
    ranked = sorted(teams, key=lambda t: car_env[t]["ceiling_frontier_p90_laptime"])
    reference = ranked[0]
    ref_ceiling = car_env[reference]["ceiling_frontier_p90_laptime"]
    # 每队实跑代表 pace = 两车手 median realized 的较优（更快）
    team_realized = {
        team: min(
            driver_extr[d]["median_realized_laptime"]
            for d in car_env[team]["drivers"]
        )
        for team in teams
    }
    cross_team = {}
    for team in teams:
        d_ceiling = car_env[team]["ceiling_frontier_p90_laptime"] - ref_ceiling
        cross_team[team] = {
            "ceiling_gap_to_reference_s": d_ceiling,
            "ceiling_gap_by_channel_s": {
                ch: car_env[team]["ceiling_by_channel"].get(ch, 0.0)
                - car_env[reference]["ceiling_by_channel"].get(ch, 0.0)
                for ch in car_env[reference]["ceiling_by_channel"]
            },
            "realized_gap_to_reference_s": team_realized[team] - team_realized[reference],
        }

    # 成绩归因：对每个非基准队，实跑差 = 车辆上限差 + 车手发挥差
    attribution = {}
    for team in teams:
        if team == reference:
            continue
        realized_gap = team_realized[team] - team_realized[reference]
        ceiling_gap = car_env[team]["ceiling_frontier_p90_laptime"] - ref_ceiling
        extraction_gap = realized_gap - ceiling_gap
        denom = abs(ceiling_gap) + abs(extraction_gap)
        car_share = abs(ceiling_gap) / denom * 100.0 if denom > 1e-9 else float("nan")
        attribution[f"{team}_vs_{reference}"] = {
            "realized_gap_s": realized_gap,
            "car_ceiling_gap_s": ceiling_gap,
            "driver_extraction_gap_s": extraction_gap,
            "car_share_pct": car_share,
            "driver_share_pct": (100.0 - car_share) if denom > 1e-9 else float("nan"),
        }

    # 数据一致性：积分圈时 vs 实际 lap_duration
    integ = []
    for driver, m in lap_mats.items():
        for i in range(m["speed"].shape[0]):
            integ.append(integrate_lap_time(m["speed"][i], grid))
    consistency = {
        "median_integrated_laptime_s": float(np.median(integ)),
        "note": "积分圈时来自 speed(distance) 数值积分，与实际 lap_duration 存在已知小偏差。",
    }

    # 不确定性：逐圈 block bootstrap
    bootstrap = bootstrap_headline_ci(driver_sector_mats, driver_team, teams, reference, config)
    for team in teams:
        car_env[team]["ceiling_frontier_p90_laptime_ci"] = \
            bootstrap["ceiling_laptime_ci"].get(team)
    for key in list(attribution.keys()):
        team = key.split("_vs_")[0]
        attribution[key]["car_share_pct_ci"] = bootstrap["car_share_pct_ci"].get(team)
        attribution[key]["car_ceiling_gap_s_ci"] = bootstrap["ceiling_gap_s_ci"].get(team)

    # 清理内部向量
    for team in teams:
        car_env[team].pop("_ceiling_vec", None)
        car_env[team].pop("_floor_vec", None)

    return {
        "run_id": run_id,
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "data_manifest": provenance,
        "sample_scope": {
            "teams": teams,
            "drivers": {d: driver_extr[d]["clean_laps_used"] for d in driver_extr},
            "grid_points": int(config["grid_points"]),
            "mini_sectors": n_sectors,
            "corner_labels": labels,
            "reference_team": reference,
        },
        "consistency": consistency,
        "car_envelope": car_env,
        "driver_extraction": driver_extr,
        "cross_team": cross_team,
        "result_attribution": attribution,
        "interpretation_limits": [
            "从属机理组件：本脚本提供弯段级'差在哪'的 Δt 分解；车辆基准以全场联合模型 run_four_team_baseline.py 为准。",
            "条件关联非因果真值；同队前沿仅部分识别车辆上限，一名车手异常出色会把其技能误并入车辆上限（本站 Red Bull 车即被高估）。",
            "燃油与胎龄混淆：上限取自低油 P10 圈，发挥%用车手自身低油最优对齐（同口径抵消大部分燃油），"
            "但中位实跑仍含燃油/胎龄，跨队成绩归因中的发挥差含各队比赛燃油/策略差异，未做显式燃油修正。",
            "单站=赛道特定，未跨赛道归一；不能外推为季度车辆评级。",
            "直道/尾速差含设定与能量部署，不能等同车手弯中能力。",
            "车手转会锚定、全场识别网络与 2026 迁移由 four-team 主规范承担；本组件不重复。",
        ],
    }


def make_figures(results: dict[str, Any]) -> list[str]:
    """可选 matplotlib 图；不可用则返回空并不阻断。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    teams = results["sample_scope"]["teams"]
    ref = results["sample_scope"]["reference_team"]

    fig, ax = plt.subplots(figsize=(7, 4))
    gaps = [results["cross_team"][t]["ceiling_gap_to_reference_s"] for t in teams]
    realized = [results["cross_team"][t]["realized_gap_to_reference_s"] for t in teams]
    x = np.arange(len(teams))
    ax.bar(x - 0.2, gaps, 0.4, label="car ceiling gap")
    ax.bar(x + 0.2, realized, 0.4, label="realized gap")
    ax.set_xticks(x)
    ax.set_xticklabels(teams, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel(f"laptime gap vs {ref} (s)")
    ax.set_title("Car ceiling vs realized gap (more negative = faster)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = FIG_DIR / "cross_team_ceiling_vs_realized.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    saved.append(str(path.relative_to(ROOT)))
    return saved


def write_summary(results: dict[str, Any], figures: list[str]) -> None:
    lines: list[str] = []
    scope = results["sample_scope"]
    lines.append("# 单站四大车队车辆能力包络与车手发挥归因（探索性）")
    lines.append("")
    lines.append(f"- run_id：`{results['run_id']}`")
    lines.append(f"- 样本：{results['config']['year']} {results['config']['meeting_manifest']} "
                 f"{results['config']['session']}，参照队 {scope['reference_team']}")
    lines.append(f"- 车手清洁圈：" + "，".join(f"{d}={n}" for d, n in scope["drivers"].items()))
    lines.append("")
    lines.append("## Stage 1 车辆能力包络（排除车手）")
    lines.append("")
    lines.append("| 车队 | 前沿P90上限圈时(s) | 理论最优圈上限(s) | 参考下限(s) | 上限敏感性(理论-前沿,s) |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for team in scope["teams"]:
        env = results["car_envelope"][team]
        lines.append(
            f"| {team} | {env['ceiling_frontier_p90_laptime']:.3f} | "
            f"{env['ceiling_ideal_lap_splice_laptime']:.3f} | {env['floor_laptime']:.3f} | "
            f"{env['ceiling_sensitivity_ideal_minus_frontier_s']:+.3f} |"
        )
    lines.append("")
    lines.append("## Stage 2 车手发挥定位（排除车辆，100%=贴车辆上限）")
    lines.append("")
    lines.append("| 车手 | 车队 | 清洁圈 | 中位实跑(s) | 自身低油最优(s) | 峰值距上限(s) | 总体发挥% |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for driver, ext in results["driver_extraction"].items():
        lines.append(
            f"| {driver} | {ext['team']} | {ext['clean_laps_used']} | "
            f"{ext['median_realized_laptime']:.3f} | {ext['best_realized_laptime']:.3f} | "
            f"{ext['peak_gap_to_car_ceiling_s']:+.3f} | "
            f"{ext['overall_extraction_pct']:.1f} |"
        )
    lines.append("")
    lines.append("## Stage 3 成绩归因（相对参照队，实跑差 = 车辆上限差 + 车手发挥差）")
    lines.append("")
    lines.append("| 对比 | 实跑差(s) | 车辆上限差(s) | 车手发挥差(s) | 车占% | 手占% | 车占% 80%CI |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for key, attr in results["result_attribution"].items():
        ci = attr.get("car_share_pct_ci") or {}
        ci_txt = (f"[{ci['p10']:.0f}, {ci['p90']:.0f}]"
                  if ci.get("p10") == ci.get("p10") else "—")
        lines.append(
            f"| {key} | {attr['realized_gap_s']:+.3f} | {attr['car_ceiling_gap_s']:+.3f} | "
            f"{attr['driver_extraction_gap_s']:+.3f} | {attr['car_share_pct']:.1f} | "
            f"{attr['driver_share_pct']:.1f} | {ci_txt} |"
        )
    lines.append("")
    if figures:
        lines.append("## 图")
        for fig in figures:
            lines.append(f"- `{fig}`")
        lines.append("")
    lines.append("## 解释限制")
    for item in results["interpretation_limits"]:
        lines.append(f"- {item}")
    lines.append("")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    config = base.load_json(CONFIG_PATH)
    run_id = f"{config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    max_attempts = int(config["max_attempts"])
    base.append_log({
        "run_id": run_id,
        "timestamp": base.now_iso(),
        "phase": "run",
        "method": "car_driver_envelope",
        "status": "started",
        "config_hash": base.canonical_hash(config),
        "git_head": base.git_head(),
    })

    def prepare(_attempt: int) -> dict[str, Any]:
        parquet_path, parquet_sha, source_commit = resolve_parquet(config)
        samples = load_samples(parquet_path, config)
        lap_frame = lap_level_frame(samples)
        clean_laps = select_clean_laps(lap_frame, config)
        grid_points = int(config["grid_points"])
        max_dist = float(np.median(
            clean_laps.loc[clean_laps["is_clean"], "max_distance"].to_numpy(dtype=float)
        ))
        grid = np.linspace(0.0, max_dist, grid_points)
        lap_mats = build_lap_matrices(samples, clean_laps, grid)
        min_laps = int(config["cleaning"]["minimum_clean_laps_per_driver"])
        usable = {d: m for d, m in lap_mats.items() if m["speed"].shape[0] >= min_laps}
        provenance = {
            "parquet_path": str(parquet_path.relative_to(ROOT)),
            "parquet_sha256_manifest": parquet_sha,
            "parquet_sha256_recomputed": base.file_sha256(parquet_path),
            "source_commit": source_commit,
            "config_hash": base.canonical_hash(config),
            "lap_distance_grid_max_m": max_dist,
            "clean_lap_counts": {
                d: int(m["speed"].shape[0]) for d, m in usable.items()
            },
            "dirty_air_laps_flagged": int(clean_laps["is_dirty_air"].sum()),
        }
        return {"grid": grid, "lap_mats": usable, "clean_laps": clean_laps,
                "provenance": provenance}

    prepared = base.run_with_retries(run_id, "prepare", "load_resample", max_attempts, prepare)
    if prepared.value is None:
        base.append_log({
            "run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
            "method": "car_driver_envelope", "status": "blocked_no_dataset",
        })
        return 2
    data = prepared.value
    if len(data["lap_mats"]) < 2:
        base.append_log({
            "run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
            "method": "car_driver_envelope", "status": "blocked_insufficient_drivers",
            "drivers": list(data["lap_mats"].keys()),
        })
        return 2

    def analyze(_attempt: int) -> dict[str, Any]:
        return build_results(config, data["provenance"], data["clean_laps"],
                             data["lap_mats"], data["grid"], run_id)

    analyzed = base.run_with_retries(run_id, "analyze", "decompose", max_attempts, analyze)
    if analyzed.value is None:
        base.append_log({
            "run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
            "method": "car_driver_envelope", "status": "skipped_after_3_failures",
        })
        return 2
    results = analyzed.value

    manifest = {
        "created_at": base.now_iso(),
        "run_id": run_id,
        "config_hash": results["config_hash"],
        **results["data_manifest"],
    }
    base.atomic_json(MANIFEST_PATH, manifest)
    figures = make_figures(results)
    results["figures"] = figures
    base.atomic_json(RESULTS_PATH, results)
    write_summary(results, figures)

    base.append_log({
        "run_id": run_id, "timestamp": base.now_iso(), "phase": "run",
        "method": "car_driver_envelope", "status": "completed",
        "teams": results["sample_scope"]["teams"],
        "drivers_used": len(results["driver_extraction"]),
        "figures": len(figures),
    })
    print(json.dumps({
        "run_id": run_id,
        "reference_team": results["sample_scope"]["reference_team"],
        "result_attribution": results["result_attribution"],
    }, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
