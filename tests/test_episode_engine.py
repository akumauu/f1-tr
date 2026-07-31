"""比赛 episode 引擎的机会、边界和聚合测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.episode_engine import (  # noqa: E402
    EpisodeThresholds,
    build_lap_states,
    detect_episodes,
    opportunity_summary,
)


def _samples() -> pd.DataFrame:
    rows = []
    drivers = [(1, "AAA", "Team A"), (2, "BBB", "Team B")]
    for lap in range(1, 7):
        for number, driver, team in drivers:
            for index, distance in enumerate(np.linspace(0, 1000, 21)):
                ahead = None
                gap = np.nan
                if driver == "AAA" and lap == 2:
                    ahead, gap = "2", 35.0
                rows.append(
                    {
                        "year": 2025,
                        "meeting": "Synthetic",
                        "session": "Race",
                        "team": team,
                        "driver_acronym": driver,
                        "driver_number": number,
                        "lap": lap,
                        "sample_index": index,
                        "distance": distance,
                        "speed": 180.0,
                        "driver_ahead": ahead,
                        "distance_to_driver_ahead": gap,
                        "track_status": "4" if lap == 4 else "1",
                        "lap_duration": 90.0 + (8.0 if driver == "AAA" and lap == 6 else 0.0),
                        "compound": "MEDIUM",
                        "stint": 1,
                        "tyre_life": lap,
                        "position": (2 if lap < 3 else 1) if driver == "AAA" else (1 if lap < 3 else 2),
                        "is_accurate": True,
                        "deleted": False,
                    }
                )
    return pd.DataFrame(rows)


def test_lap_states_build_attack_and_inverse_defense_opportunities():
    laps = build_lap_states(_samples())
    aaa2 = laps[(laps.driver_acronym == "AAA") & (laps.lap == 2)].iloc[0]
    bbb2 = laps[(laps.driver_acronym == "BBB") & (laps.lap == 2)].iloc[0]
    assert aaa2.attack_opportunity
    assert aaa2.driver_ahead_acronym == "BBB"
    assert bbb2.defense_opportunity
    assert bbb2.attacker == "AAA"


def test_non_green_lap_is_not_clean_and_green_transition_is_restart_proxy():
    laps = build_lap_states(_samples())
    assert not laps[(laps.driver_acronym == "AAA") & (laps.lap == 4)].iloc[0].is_clean
    episodes = detect_episodes(laps)
    assert any(e.episode_type == "restart_window" and e.lap_start == 5 for e in episodes)


def test_attack_success_is_proxy_and_opportunities_not_skill_zero():
    laps = build_lap_states(_samples())
    episodes = detect_episodes(laps)
    attack = [e for e in episodes if e.driver == "AAA" and e.episode_type == "attack_opportunity"]
    assert attack
    assert any(e.observed_outcome == "position_gain_proxy" for e in attack)
    assert all(e.directness_level == "proxy" for e in attack)
    summary = opportunity_summary(episodes)
    assert int(summary.loc[(summary.driver == "AAA") & (summary.episode_type == "attack_opportunity"), "opportunities"].iloc[0]) == 1
    start = summary.loc[
        (summary.driver == "AAA") & (summary.episode_type == "race_start_window")
    ].iloc[0]
    assert int(start.evaluable_outcomes) == 0
    assert pd.isna(start.observed_successes)


def test_missing_required_columns_are_rejected():
    try:
        build_lap_states(pd.DataFrame({"lap": [1]}), EpisodeThresholds())
    except ValueError as exc:
        assert "缺少字段" in str(exc)
    else:
        raise AssertionError("缺失字段必须拒绝")


def test_known_car_ahead_with_missing_gap_is_unknown_not_clean_air():
    samples = _samples()
    mask = (samples["driver_acronym"] == "AAA") & (samples["lap"] == 3)
    samples.loc[mask, "driver_ahead"] = "2"
    samples.loc[mask, "distance_to_driver_ahead"] = np.nan
    laps = build_lap_states(samples)
    row = laps[(laps.driver_acronym == "AAA") & (laps.lap == 3)].iloc[0]
    assert row.driver_ahead_acronym == "BBB"
    assert row.traffic_state == "unknown_gap"
    assert not row.attack_opportunity


def test_pit_boundary_marks_both_pit_in_and_pit_out_laps():
    samples = _samples()
    samples.loc[samples["lap"] >= 4, "stint"] = 2
    laps = build_lap_states(samples)
    aaa = laps[laps.driver_acronym == "AAA"].set_index("lap")
    assert aaa.loc[3, "pit_in_boundary_proxy"]
    assert aaa.loc[4, "pit_out_boundary_proxy"]
    assert aaa.loc[3, "pit_boundary_proxy"]
    assert aaa.loc[4, "pit_boundary_proxy"]
