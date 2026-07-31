"""把采样级公开遥测转换为可审计的比赛情境 episode。

本模块只识别公开数据支持的机会和观测结果。位置变化仍可能由进站、退赛或
赛会控制造成，因此攻防结果默认是 proxy，不会自动进入长期车手能力评分。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EpisodeThresholds:
    minimum_distance_coverage: float = 0.96
    attack_gap_seconds: float = 1.2
    traffic_gap_seconds: float = 2.5
    clean_air_gap_seconds: float = 4.0
    warmup_laps: int = 2
    late_stint_fraction: float = 0.25
    negative_tail_mad: float = 3.5


@dataclass(frozen=True)
class EpisodeRecord:
    episode_type: str
    driver: str
    team: str
    lap_start: int
    lap_end: int
    opportunity: bool
    observed_outcome: str
    directness_level: str
    confidence: str
    evidence: Mapping[str, Any]
    alternative_explanations: Sequence[str]
    forbidden_claims: Sequence[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_valid(series: pd.Series) -> Any:
    valid = series.dropna()
    return None if valid.empty else valid.iloc[0]


def _mode_text(series: pd.Series) -> str | None:
    values = series.dropna().astype(str)
    values = values[~values.isin({"", "nan", "None", "<NA>"})]
    if values.empty:
        return None
    counts = values.value_counts()
    return str(counts.index[0])


def _all_green(series: pd.Series) -> bool:
    """只把明确的纯绿旗状态 ``1`` 当成 clean；组合状态不猜测。"""

    values = series.dropna().astype(str).str.strip()
    return bool(len(values) > 0 and values.eq("1").all())


def _required(frame: pd.DataFrame, names: Iterable[str]) -> None:
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise ValueError(f"episode 输入缺少字段：{missing}")


def build_lap_states(
    samples: pd.DataFrame,
    thresholds: EpisodeThresholds | None = None,
) -> pd.DataFrame:
    """从采样级数据生成逐圈状态、交通代理和 stint 阶段。"""

    cfg = thresholds or EpisodeThresholds()
    _required(
        samples,
        {
            "driver_acronym",
            "driver_number",
            "team",
            "lap",
            "distance",
            "speed",
            "driver_ahead",
            "distance_to_driver_ahead",
            "track_status",
        },
    )
    frame = samples.copy()
    optional_defaults: dict[str, Any] = {
        "session": "Race",
        "meeting": "<UNKNOWN_MEETING>",
        "year": -1,
        "lap_duration": np.nan,
        "compound": "UNKNOWN",
        "stint": np.nan,
        "tyre_life": np.nan,
        "position": np.nan,
        "is_accurate": True,
        "deleted": False,
    }
    for column, default in optional_defaults.items():
        if column not in frame:
            frame[column] = default
    frame["distance"] = pd.to_numeric(frame["distance"], errors="coerce")
    frame["speed"] = pd.to_numeric(frame["speed"], errors="coerce")
    frame["distance_to_driver_ahead"] = pd.to_numeric(
        frame["distance_to_driver_ahead"], errors="coerce"
    )
    keys = ["year", "meeting", "session", "team", "driver_acronym", "driver_number", "lap"]
    grouped = frame.groupby(keys, dropna=False, observed=True, sort=True)
    laps = grouped.agg(
        lap_duration=("lap_duration", _first_valid),
        compound=("compound", _first_valid),
        stint=("stint", _first_valid),
        tyre_life=("tyre_life", _first_valid),
        position=("position", _first_valid),
        is_accurate=("is_accurate", lambda s: bool(s.fillna(False).astype(bool).all())),
        deleted=("deleted", lambda s: bool(s.fillna(False).astype(bool).any())),
        all_green=("track_status", _all_green),
        min_distance=("distance", "min"),
        max_distance=("distance", "max"),
        median_speed_kmh=("speed", "median"),
        median_gap_m=("distance_to_driver_ahead", "median"),
        driver_ahead_number=("driver_ahead", _mode_text),
        samples=("distance", "size"),
    ).reset_index()

    scope = ["year", "meeting", "session"]
    laps["track_length_proxy_m"] = laps.groupby(scope, observed=True)["max_distance"].transform(
        "median"
    )
    span = laps["max_distance"] - laps["min_distance"].fillna(0.0)
    laps["distance_coverage"] = span / laps["track_length_proxy_m"].replace(0.0, np.nan)
    laps["is_clean"] = (
        laps["is_accurate"].fillna(False).astype(bool)
        & ~laps["deleted"].fillna(False).astype(bool)
        & laps["all_green"].fillna(False).astype(bool)
        & laps["distance_coverage"].ge(cfg.minimum_distance_coverage)
    )

    number_map = (
        laps[[*scope, "driver_number", "driver_acronym"]]
        .drop_duplicates()
        .assign(driver_ahead_number=lambda d: d["driver_number"].astype("Int64").astype(str))
        [[*scope, "driver_ahead_number", "driver_acronym"]]
        .rename(columns={"driver_acronym": "driver_ahead_acronym"})
    )
    laps["driver_ahead_number"] = (
        laps["driver_ahead_number"].astype("string").str.replace(r"\.0$", "", regex=True)
    )
    laps = laps.merge(number_map, on=[*scope, "driver_ahead_number"], how="left")
    speed_ms = laps["median_speed_kmh"].clip(lower=1.0) / 3.6
    laps["gap_seconds_proxy"] = laps["median_gap_m"] / speed_ms
    laps.loc[
        ~np.isfinite(laps["gap_seconds_proxy"]) | laps["gap_seconds_proxy"].lt(0),
        "gap_seconds_proxy",
    ] = np.nan
    laps["attack_opportunity"] = (
        laps["is_clean"]
        & laps["driver_ahead_acronym"].notna()
        & laps["gap_seconds_proxy"].le(cfg.attack_gap_seconds)
    )
    # “没有前车”与“已知有前车但距离缺失”必须分开。后者不能为了扩大样本
    # 被当作 clean air；它保留为 unknown_gap，并从严格可比圈中排除。
    known_no_car_ahead = laps["driver_ahead_acronym"].isna()
    unknown_gap = laps["driver_ahead_acronym"].notna() & laps["gap_seconds_proxy"].isna()
    laps["traffic_state"] = np.select(
        [
            unknown_gap,
            laps["gap_seconds_proxy"].le(cfg.attack_gap_seconds),
            laps["gap_seconds_proxy"].le(cfg.traffic_gap_seconds),
            laps["gap_seconds_proxy"].ge(cfg.clean_air_gap_seconds) | known_no_car_ahead,
        ],
        ["unknown_gap", "attack_window", "traffic", "clean_air"],
        default="intermediate_gap",
    )

    stint_keys = [*scope, "driver_acronym", "stint"]
    laps["stint_lap_index"] = laps.groupby(stint_keys, dropna=False, observed=True).cumcount() + 1
    laps["stint_length"] = laps.groupby(stint_keys, dropna=False, observed=True)["lap"].transform(
        "size"
    )
    late_start = np.maximum(
        cfg.warmup_laps + 1,
        np.ceil(laps["stint_length"] * (1.0 - cfg.late_stint_fraction)).astype(int),
    )
    laps["stint_phase"] = np.select(
        [
            laps["stint_lap_index"].le(cfg.warmup_laps),
            laps["stint_lap_index"].ge(late_start),
        ],
        ["warmup", "late"],
        default="steady",
    )

    driver_keys = [*scope, "driver_acronym"]
    laps = laps.sort_values([*driver_keys, "lap"], kind="stable").reset_index(drop=True)
    laps["next_position"] = laps.groupby(driver_keys, observed=True)["position"].shift(-1)
    laps["next_driver_ahead"] = laps.groupby(driver_keys, observed=True)[
        "driver_ahead_acronym"
    ].shift(-1)
    grouped_stint = laps.groupby(driver_keys, observed=True)["stint"]
    laps["previous_stint"] = grouped_stint.shift(1)
    laps["next_stint"] = grouped_stint.shift(-1)
    laps["pit_in_boundary_proxy"] = laps["next_stint"].notna() & laps["stint"].notna() & (
        laps["next_stint"] != laps["stint"]
    )
    laps["pit_out_boundary_proxy"] = (
        laps["previous_stint"].notna()
        & laps["stint"].notna()
        & (laps["previous_stint"] != laps["stint"])
    )
    laps["pit_boundary_proxy"] = (
        laps["pit_in_boundary_proxy"] | laps["pit_out_boundary_proxy"]
    )

    # 防守机会由另一辆车的前车指针反向构造。只保留同圈最近的攻击者。
    attackers = laps.loc[
        laps["attack_opportunity"],
        [*scope, "lap", "driver_acronym", "driver_ahead_acronym", "gap_seconds_proxy"],
    ].rename(
        columns={
            "driver_acronym": "attacker",
            "driver_ahead_acronym": "driver_acronym",
            "gap_seconds_proxy": "behind_gap_seconds_proxy",
        }
    )
    if not attackers.empty:
        attackers = attackers.sort_values("behind_gap_seconds_proxy").drop_duplicates(
            [*scope, "lap", "driver_acronym"], keep="first"
        )
        laps = laps.merge(attackers, on=[*scope, "lap", "driver_acronym"], how="left")
    else:
        laps["attacker"] = pd.NA
        laps["behind_gap_seconds_proxy"] = np.nan
    laps["defense_opportunity"] = laps["is_clean"] & laps["attacker"].notna()

    # 只生成“异常慢圈候选”，不把它命名为车手失误。
    clean_duration = pd.to_numeric(laps["lap_duration"], errors="coerce").where(laps["is_clean"])
    group_median = clean_duration.groupby(
        [laps[column] for column in stint_keys], dropna=False
    ).transform("median")
    absolute = (clean_duration - group_median).abs()
    group_mad = absolute.groupby([laps[column] for column in stint_keys], dropna=False).transform(
        "median"
    )
    robust_scale = (1.4826 * group_mad).replace(0.0, np.nan)
    laps["lap_time_residual_s"] = clean_duration - group_median
    laps["negative_tail_candidate"] = (
        laps["is_clean"]
        & laps["traffic_state"].isin(["clean_air", "intermediate_gap"])
        & ~laps["pit_boundary_proxy"]
        & laps["lap_time_residual_s"].gt(cfg.negative_tail_mad * robust_scale)
    ).fillna(False)
    return laps


def _outcome_for_attack(row: Any) -> str:
    if bool(row.pit_boundary_proxy) or pd.isna(row.next_position) or pd.isna(row.position):
        return "censored"
    gained = float(row.next_position) < float(row.position)
    passed_target = row.next_driver_ahead != row.driver_ahead_acronym
    return "position_gain_proxy" if gained and passed_target else "no_position_gain_observed"


def _outcome_for_defense(row: Any) -> str:
    if bool(row.pit_boundary_proxy) or pd.isna(row.next_position) or pd.isna(row.position):
        return "censored"
    return (
        "position_held_proxy"
        if float(row.next_position) <= float(row.position)
        else "position_loss_observed"
    )


def detect_episodes(
    lap_states: pd.DataFrame,
    thresholds: EpisodeThresholds | None = None,
) -> list[EpisodeRecord]:
    """把逐圈状态转换为攻防、发车/重启及负尾候选 episode。"""

    _ = thresholds or EpisodeThresholds()
    _required(
        lap_states,
        {
            "driver_acronym",
            "team",
            "lap",
            "attack_opportunity",
            "defense_opportunity",
            "negative_tail_candidate",
            "pit_boundary_proxy",
            "all_green",
        },
    )
    episodes: list[EpisodeRecord] = []
    generic_forbidden = (
        "不得由位置变化直接断言超车技巧",
        "不得把观测机会代理当成比赛控制官方判定",
    )
    keys = [column for column in ("year", "meeting", "session", "driver_acronym") if column in lap_states]
    ordered = lap_states.sort_values([*keys, "lap"], kind="stable")
    previous_green = ordered.groupby(keys, dropna=False, observed=True)["all_green"].shift(1)
    ordered = ordered.assign(previous_green=previous_green)
    for row in ordered.itertuples(index=False):
        common = {
            "driver": str(row.driver_acronym),
            "team": str(row.team),
            "lap_start": int(row.lap),
            "lap_end": int(row.lap),
        }
        if bool(row.attack_opportunity):
            episodes.append(
                EpisodeRecord(
                    episode_type="attack_opportunity",
                    opportunity=True,
                    observed_outcome=_outcome_for_attack(row),
                    directness_level="proxy",
                    confidence="medium" if not row.pit_boundary_proxy else "low",
                    evidence={
                        "target": row.driver_ahead_acronym,
                        "gap_seconds_proxy": _finite_or_none(row.gap_seconds_proxy),
                        "position": _finite_or_none(row.position),
                        "next_position": _finite_or_none(row.next_position),
                    },
                    alternative_explanations=("对手进站或退赛", "计时位置边界", "赛会控制"),
                    forbidden_claims=generic_forbidden,
                    **common,
                )
            )
        if bool(row.defense_opportunity):
            episodes.append(
                EpisodeRecord(
                    episode_type="defense_opportunity",
                    opportunity=True,
                    observed_outcome=_outcome_for_defense(row),
                    directness_level="proxy",
                    confidence="medium" if not row.pit_boundary_proxy else "low",
                    evidence={
                        "attacker": row.attacker,
                        "gap_seconds_proxy": _finite_or_none(row.behind_gap_seconds_proxy),
                        "position": _finite_or_none(row.position),
                        "next_position": _finite_or_none(row.next_position),
                    },
                    alternative_explanations=("攻击者主动放弃", "对手进站或退赛", "赛会控制"),
                    forbidden_claims=generic_forbidden,
                    **common,
                )
            )
        if bool(row.negative_tail_candidate):
            episodes.append(
                EpisodeRecord(
                    episode_type="unexplained_negative_tail_candidate",
                    opportunity=False,
                    observed_outcome="lap_time_loss_observed",
                    directness_level="derived",
                    confidence="low",
                    evidence={"lap_time_residual_s": _finite_or_none(row.lap_time_residual_s)},
                    alternative_explanations=("未观测交通", "轮胎状态", "车辆状态", "车队指令"),
                    forbidden_claims=("不得自动称为车手失误", "不得自动扣长期能力分"),
                    **common,
                )
            )

    # 发车和重启单列；发车仅代表开局 episode，不计算反应时间。
    for _, group in ordered.groupby(keys, dropna=False, observed=True, sort=False):
        group = group.sort_values("lap")
        first = group.iloc[0]
        episodes.append(
            EpisodeRecord(
                episode_type="race_start_window",
                driver=str(first["driver_acronym"]),
                team=str(first["team"]),
                lap_start=int(first["lap"]),
                lap_end=int(min(first["lap"] + 1, group["lap"].max())),
                opportunity=True,
                observed_outcome="start_window_observed",
                directness_level="proxy",
                confidence="low",
                evidence={"opening_position": _finite_or_none(first["position"])},
                alternative_explanations=("发车位缺失", "事故", "第一圈计时粒度"),
                forbidden_claims=("不得推断起步反应时间",),
            )
        )
        restart_rows = group.loc[group["previous_green"].eq(False) & group["all_green"].eq(True)]
        for restart in restart_rows.itertuples(index=False):
            episodes.append(
                EpisodeRecord(
                    episode_type="restart_window",
                    driver=str(restart.driver_acronym),
                    team=str(restart.team),
                    lap_start=int(restart.lap),
                    lap_end=int(restart.lap),
                    opportunity=True,
                    observed_outcome="green_transition_observed",
                    directness_level="derived",
                    confidence="medium",
                    evidence={"track_status_transition": "non_green_to_green"},
                    alternative_explanations=("track_status 组合编码",),
                    forbidden_claims=("不得称为官方重启时刻而不核对 race control",),
                )
            )
    return episodes


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def opportunity_summary(episodes: Sequence[EpisodeRecord]) -> pd.DataFrame:
    """按车手和 episode 类型汇总真实机会数；无机会保持 absent 而不是零能力。"""

    rows = [episode.to_dict() for episode in episodes if episode.opportunity]
    if not rows:
        return pd.DataFrame(
            columns=[
                "driver",
                "team",
                "episode_type",
                "opportunities",
                "evaluable_outcomes",
                "observed_successes",
            ]
        )
    frame = pd.DataFrame(rows)
    evaluable = frame["episode_type"].isin(["attack_opportunity", "defense_opportunity"])
    success = evaluable & frame["observed_outcome"].isin(
        ["position_gain_proxy", "position_held_proxy"]
    )
    frame = frame.assign(outcome_evaluable=evaluable, observed_success=success)
    summary = (
        frame.groupby(["driver", "team", "episode_type"], observed=True, sort=True)
        .agg(
            opportunities=("opportunity", "size"),
            evaluable_outcomes=("outcome_evaluable", "sum"),
            observed_successes=("observed_success", "sum"),
        )
        .reset_index()
    )
    summary["observed_successes"] = summary["observed_successes"].astype("Int64")
    summary.loc[summary["evaluable_outcomes"].eq(0), "observed_successes"] = pd.NA
    return summary
