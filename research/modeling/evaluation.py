"""单站样本审计、bootstrap 与外部公开数据验证。"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from modeling.entities import normalize_team_name


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def audit_balanced_meetings(
    frame: pd.DataFrame, reporting_teams: Sequence[str], session_name: str
) -> list[dict[str, Any]]:
    """按完整车手数、最少有效圈和圈数变异系数选择最均衡分站。"""

    expected_drivers = len(reporting_teams) * 2
    selected = frame[
        (frame["session_name"].astype(str) == str(session_name))
        & frame["team_name"].isin(reporting_teams)
    ]
    counts = (
        selected.groupby(["meeting_name", "team_name", "driver_id"])
        .size()
        .rename("clean_laps")
        .reset_index()
    )
    rows: list[dict[str, Any]] = []
    for meeting, subset in counts.groupby("meeting_name"):
        values = subset["clean_laps"].to_numpy(dtype=float)
        mean = float(np.mean(values))
        cv = float(np.std(values, ddof=1) / mean) if len(values) > 1 and mean else 1.0
        drivers = int(subset["driver_id"].nunique())
        teams = int(subset["team_name"].nunique())
        minimum = int(np.min(values))
        completeness = (drivers / expected_drivers) * (teams / len(reporting_teams))
        score = float(minimum * max(0.0, 1.0 - min(cv, 1.0)) * completeness)
        rows.append(
            {
                "meeting_name": str(meeting),
                "drivers": drivers,
                "teams": teams,
                "clean_laps": int(np.sum(values)),
                "minimum_driver_clean_laps": minimum,
                "maximum_driver_clean_laps": int(np.max(values)),
                "driver_lap_count_cv": cv,
                "balance_score": score,
                "complete_four_team_grid": drivers == expected_drivers
                and teams == len(reporting_teams),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            bool(row["complete_four_team_grid"]),
            float(row["balance_score"]),
            int(row["minimum_driver_clean_laps"]),
        ),
        reverse=True,
    )


def stratified_stint_bootstrap(
    frame: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """保留每位车手，按 stint 再按圈有放回抽样。"""

    samples: list[pd.DataFrame] = []
    for _, driver_frame in frame.groupby("driver_id", sort=False):
        blocks = [block for _, block in driver_frame.groupby("stint_number", sort=False)]
        selected = rng.integers(0, len(blocks), size=len(blocks))
        for block_index in selected:
            block = blocks[int(block_index)]
            positions = rng.integers(0, len(block), size=len(block))
            samples.append(block.iloc[positions])
    return pd.concat(samples, ignore_index=True)


def interval(values: Sequence[float], levels: tuple[float, float] = (0.025, 0.975)) -> dict[str, float]:
    data = np.asarray(values, dtype=float)
    low, high = np.quantile(data, levels)
    return {"low": float(low), "high": float(high)}


def _read_or_fetch_json(url: str, cache_path: Path, reuse_cache: bool) -> tuple[Any, str]:
    if reuse_cache and cache_path.exists():
        payload = cache_path.read_bytes()
    else:
        request = Request(url, headers={"User-Agent": "F1-TR-research/1.0"})
        with urlopen(request, timeout=45) as response:  # noqa: S310 - 固定公开 HTTPS API
            payload = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
        time.sleep(0.4)
    return json.loads(payload.decode("utf-8")), sha256_bytes(payload)


def fetch_openf1_race(
    year: int,
    country_name: str,
    session_name: str,
    cache_dir: Path,
    reuse_cache: bool,
    location: str | None = None,
) -> dict[str, Any]:
    """抓取 OpenF1 独立圈速快照并记录响应哈希。"""

    base = "https://api.openf1.org/v1"
    session_query = {
        "year": year,
        "country_name": country_name,
        "session_name": session_name,
    }
    if location:
        session_query["location"] = location
    session_url = f"{base}/sessions?{urlencode(session_query)}"
    sessions, session_hash = _read_or_fetch_json(
        session_url, cache_dir / "sessions.json", reuse_cache
    )
    if len(sessions) != 1:
        raise RuntimeError(f"OpenF1 session 查询应返回 1 条，实际 {len(sessions)} 条。")
    session_key = int(sessions[0]["session_key"])
    payloads: dict[str, Any] = {"sessions": sessions}
    hashes = {"sessions": session_hash}
    urls = {"sessions": session_url}
    for endpoint in ("drivers", "laps", "stints"):
        url = f"{base}/{endpoint}?{urlencode({'session_key': session_key})}"
        value, digest = _read_or_fetch_json(
            url, cache_dir / f"{endpoint}.json", reuse_cache
        )
        payloads[endpoint] = value
        hashes[endpoint] = digest
        urls[endpoint] = url
    return {
        "session_key": session_key,
        "payloads": payloads,
        "response_sha256": hashes,
        "urls": urls,
    }


def openf1_team_pace_proxy(
    snapshot: Mapping[str, Any],
    aliases: Mapping[str, str],
    reporting_teams: Sequence[str],
    cleaning: Mapping[str, Any],
    fastest_fraction: float,
) -> dict[str, Any]:
    """用每位车手最快四分之一有效圈的中位数形成外部车队配速代理。"""

    payloads = snapshot["payloads"]
    laps = pd.DataFrame(payloads["laps"])
    drivers = pd.DataFrame(payloads["drivers"])[
        ["driver_number", "name_acronym", "team_name"]
    ].drop_duplicates("driver_number")
    drivers["team_name"] = drivers["team_name"].map(
        lambda value: normalize_team_name(value, aliases)
    )
    laps = laps.merge(drivers, on="driver_number", how="left")
    stints = pd.DataFrame(payloads["stints"])
    compound: list[str] = []
    for row in laps.itertuples(index=False):
        matches = stints[
            (stints["driver_number"] == row.driver_number)
            & (stints["lap_start"] <= row.lap_number)
            & (stints["lap_end"] >= row.lap_number)
        ]
        compound.append(str(matches.iloc[0]["compound"]) if len(matches) else "UNKNOWN")
    laps["compound"] = compound
    laps["lap_duration"] = pd.to_numeric(laps["lap_duration"], errors="coerce")
    laps["lap_number"] = pd.to_numeric(laps["lap_number"], errors="coerce")
    mask = (
        laps["team_name"].isin(reporting_teams)
        & laps["lap_duration"].between(
            float(cleaning["minimum_lap_duration_seconds"]),
            float(cleaning["maximum_lap_duration_seconds"]),
        )
        & (laps["lap_number"] >= int(cleaning["minimum_lap_number"]))
        & ~laps["is_pit_out_lap"].fillna(False).astype(bool)
        & laps["compound"].isin(cleaning["dry_compounds"])
    )
    clean = laps.loc[mask].copy()
    robust_parts: list[pd.DataFrame] = []
    for _, subset in clean.groupby("name_acronym"):
        median = float(subset["lap_duration"].median())
        mad = float((subset["lap_duration"] - median).abs().median())
        threshold = float(cleaning["robust_mad_multiplier"]) * max(mad, 0.05)
        robust_parts.append(subset[(subset["lap_duration"] - median).abs() <= threshold])
    clean = pd.concat(robust_parts, ignore_index=True)

    drivers_out: list[dict[str, Any]] = []
    for (driver, team), subset in clean.groupby(["name_acronym", "team_name"]):
        count = max(3, int(math.ceil(len(subset) * float(fastest_fraction))))
        proxy = float(subset.nsmallest(count, "lap_duration")["lap_duration"].median())
        drivers_out.append(
            {
                "driver_id": str(driver),
                "team": str(team),
                "clean_laps": int(len(subset)),
                "fastest_fraction_laps": count,
                "fastest_fraction_median_seconds": proxy,
            }
        )
    driver_frame = pd.DataFrame(drivers_out)
    teams_out: list[dict[str, Any]] = []
    for team, subset in driver_frame.groupby("team"):
        teams_out.append(
            {
                "team": str(team),
                "driver_count": int(len(subset)),
                "pace_proxy_seconds": float(
                    subset["fastest_fraction_median_seconds"].mean()
                ),
            }
        )
    fastest = min(row["pace_proxy_seconds"] for row in teams_out)
    for row in teams_out:
        row["gap_to_fastest_ms"] = float(
            (float(row["pace_proxy_seconds"]) - float(fastest)) * 1000
        )
    teams_out.sort(key=lambda row: float(row["pace_proxy_seconds"]))
    return {
        "method": "mean_of_driver_fastest_fraction_medians",
        "fastest_fraction": float(fastest_fraction),
        "drivers": drivers_out,
        "teams": teams_out,
        "clean_laps": int(len(clean)),
    }


def compare_team_gaps(
    model_gap_ms: Mapping[str, float], external_gap_ms: Mapping[str, float]
) -> dict[str, Any]:
    """比较共同车队的排序与相对最快车毫秒差。"""

    teams = sorted(set(model_gap_ms) & set(external_gap_ms))
    if len(teams) < 2:
        raise ValueError("至少需要两个共同车队才能比较。")
    model = pd.Series({team: float(model_gap_ms[team]) for team in teams})
    external = pd.Series({team: float(external_gap_ms[team]) for team in teams})
    spearman = float(model.rank().corr(external.rank(), method="pearson"))
    pair_errors: list[float] = []
    for left_index, left in enumerate(teams):
        for right in teams[left_index + 1 :]:
            model_delta = model[left] - model[right]
            external_delta = external[left] - external[right]
            pair_errors.append(abs(float(model_delta - external_delta)))
    return {
        "teams": teams,
        "model_order_fast_to_slow": list(model.sort_values().index),
        "external_order_fast_to_slow": list(external.sort_values().index),
        "spearman_rank_correlation": spearman,
        "pairwise_gap_mae_ms": float(np.mean(pair_errors)),
    }
