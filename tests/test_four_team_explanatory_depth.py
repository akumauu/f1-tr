from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.bayesian_counterfactual import BAYESIAN_BLOCKS, sample_posterior  # noqa: E402
from modeling.explanatory_depth import (  # noqa: E402
    _half_normal_shortfall,
    build_car_ceiling,
    build_reconstruction,
    build_separation,
    context_oos_gate,
    fit_context_head,
    validate_explanatory_depth_config,
)


def _config() -> dict[str, object]:
    return json.loads(
        (
            ROOT / "research" / "configs" / "four_team_explanatory_depth_v6.json"
        ).read_text(encoding="utf-8")
    )


TEAMS = ["McLaren", "Red Bull Racing", "Ferrari", "Mercedes"]


# --------------------------------------------------------------------------- #
# 配置边界
# --------------------------------------------------------------------------- #


def test_v6_config_guards() -> None:
    config = _config()
    validate_explanatory_depth_config(config)

    bad = copy.deepcopy(config)
    bad["boundaries"]["absolute_qualifying_race_time_merge"] = True
    with pytest.raises(ValueError, match="绝对圈时"):
        validate_explanatory_depth_config(bad)

    bad = copy.deepcopy(config)
    bad["frontier"]["race_ceiling_quantile"] = 0.9
    with pytest.raises(ValueError, match="Race 前沿分位"):
        validate_explanatory_depth_config(bad)

    bad = copy.deepcopy(config)
    bad["context_head"]["candidate_features"] = ["unknown_feature"]
    with pytest.raises(ValueError, match="未知候选"):
        validate_explanatory_depth_config(bad)

    bad = copy.deepcopy(config)
    bad["separation"]["high_pair_confidence"] = 0.60
    bad["separation"]["medium_pair_confidence"] = 0.70
    with pytest.raises(ValueError, match="成对置信度阈值"):
        validate_explanatory_depth_config(bad)


# --------------------------------------------------------------------------- #
# Layer A —— 上下文头与门控
# --------------------------------------------------------------------------- #


def _synthetic_race(seed: int = 0) -> pd.DataFrame:
    """构造 dirty-air 真实拖慢圈速的合成 Race 帧（2023 + 2024 多站）。"""

    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    layout = {2023: 6, 2024: 12}
    circuits = ["CircuitA", "CircuitB", "CircuitC"]
    for year, meetings in layout.items():
        for round_index in range(1, meetings + 1):
            meeting = f"{year} R{round_index}"
            circuit = circuits[round_index % len(circuits)]
            for driver in ("D1", "D2", "D3", "D4"):
                for lap in range(1, 21):
                    dirty = 1.0 if rng.random() < 0.4 else 0.0
                    tyre_age = float(lap)
                    target = (
                        0.05 * tyre_age
                        + 1.2 * dirty
                        + rng.normal(0.0, 0.3)
                    )
                    rows.append(
                        {
                            "year": year,
                            "meeting_key": f"{year}|{meeting}",
                            "meeting_name": meeting,
                            "round_index": round_index,
                            "circuit_name": circuit,
                            "driver_id": driver,
                            "team_name": "Team" + driver[-1],
                            "compound": rng.choice(["SOFT", "MEDIUM", "HARD"]),
                            "lap_number": lap,
                            "lap_fraction": lap / 20.0,
                            "lap_fraction_sq": (lap / 20.0) ** 2,
                            "tyre_age": tyre_age,
                            "tyre_age_sq": tyre_age**2,
                            "target_log_pct": target,
                            "dirty_air": dirty,
                            "gap_deficit": dirty * 0.8,
                        }
                    )
    return pd.DataFrame(rows)


def test_context_head_fit_and_gate_prefers_traffic() -> None:
    race = _synthetic_race()
    baseline = fit_context_head(race, [])
    with_traffic = fit_context_head(race, ["traffic_dirty_air"])
    base_resid = race["target_log_pct"].to_numpy() - baseline.predict(race)
    traffic_resid = race["target_log_pct"].to_numpy() - with_traffic.predict(race)
    assert float(np.mean(np.abs(traffic_resid))) < float(np.mean(np.abs(base_resid)))

    config = _config()
    config["context_head"]["candidate_features"] = ["traffic_dirty_air"]
    config["context_head"]["oos_gate"]["selection_year"] = 2024
    config["context_head"]["oos_gate"]["locked_year"] = 2024
    selected, report = context_oos_gate(race, config)
    assert "traffic_dirty_air" in selected
    assert report["feature_trace"][0]["feature"] == "traffic_dirty_air"


# --------------------------------------------------------------------------- #
# Layer C —— 加性毫秒重建 + 名次构念
# --------------------------------------------------------------------------- #


def _synthetic_lap_detail() -> pd.DataFrame:
    """构造四队八车、期望==观测的完美重建帧，并让组合序=观测序。"""

    speed = {"McLaren": -1.2, "Red Bull Racing": -0.4, "Ferrari": 0.4, "Mercedes": 1.2}
    drivers = {
        "McLaren": ["NOR", "PIA"],
        "Red Bull Racing": ["VER", "TSU"],
        "Ferrari": ["LEC", "HAM"],
        "Mercedes": ["RUS", "ANT"],
    }
    rows: list[dict[str, object]] = []
    for team, base in speed.items():
        for offset, driver in enumerate(drivers[team]):
            z = base + 0.1 * offset
            for lap in range(1, 16):
                rows.append(
                    {
                        "year": 2025,
                        "meeting_name": "Test GP",
                        "circuit_name": "Test",
                        "driver_id": driver,
                        "team_name": team,
                        "session_reference": 90.0,
                        "target_log_pct": z,
                        "expected_target_z": z,
                        "context_prediction_z": 0.0,
                        "car_state_z": base,
                        "driver_state_z": 0.1 * offset,
                        "interaction_state_z": 0.0,
                        "identity_expected_z": z,
                        "context_adjusted_z": z,
                        "execution_loss_ms": 0.0,
                        "model_residual_ms": 0.0,
                    }
                )
    return pd.DataFrame(rows)


def test_reconstruction_perfect_fit_and_rank_alignment() -> None:
    lap = _synthetic_lap_detail()
    config = _config()
    driver_order = (
        lap[["driver_id", "identity_expected_z"]]
        .drop_duplicates()
        .sort_values("identity_expected_z")
        .reset_index(drop=True)
    )
    classification = pd.DataFrame(
        {
            "year": 2025,
            "meeting_name": "Test GP",
            "driver": driver_order["driver_id"],
            "classification_proxy_rank": np.arange(1, len(driver_order) + 1),
        }
    )
    result = build_reconstruction(lap, TEAMS, config, classification)
    overall = result["reconstruction_quality_overall"]
    assert overall["mae_ms"] == pytest.approx(0.0, abs=1e-6)
    assert overall["r2"] == pytest.approx(1.0, abs=1e-6)
    rank = result["rank_construct_check"]["overall"]
    assert rank["mean_model_vs_observed_spearman"] == pytest.approx(1.0, abs=1e-9)
    # 三种序完全一致时，两类异常都不应出现。
    assert result["rank_construct_check"]["anomalies_model_vs_observed_pace"] == []
    assert result["rank_construct_check"]["anomalies_result_gap"] == []
    assert rank["mean_observed_vs_classification_proxy_spearman"] == pytest.approx(
        1.0, abs=1e-9
    )


def test_reconstruction_does_not_call_pace_model_gap_a_result_gap() -> None:
    lap = _synthetic_lap_detail()
    # 只扰乱模型身份序，观测配速序与 classification proxy 仍完全一致。
    lap.loc[lap["driver_id"] == "NOR", "identity_expected_z"] = 3.0
    observed = (
        lap[["driver_id", "context_adjusted_z"]]
        .drop_duplicates()
        .sort_values("context_adjusted_z")
        .reset_index(drop=True)
    )
    classification = pd.DataFrame(
        {
            "year": 2025,
            "meeting_name": "Test GP",
            "driver": observed["driver_id"],
            "classification_proxy_rank": np.arange(1, len(observed) + 1),
        }
    )
    result = build_reconstruction(lap, TEAMS, _config(), classification)
    check = result["rank_construct_check"]
    assert check["anomalies_model_vs_observed_pace"]
    assert check["anomalies_result_gap"] == []


# --------------------------------------------------------------------------- #
# Layer D —— 半正态校正的理论上限
# --------------------------------------------------------------------------- #


def test_half_normal_shortfall_bounded() -> None:
    assert _half_normal_shortfall(np.array([0.0, 0.0, 0.0])) == 0.0
    tight = _half_normal_shortfall(np.linspace(-0.2, 0.2, 40), cap=0.3)
    assert 0.0 <= tight <= 0.3
    # 整体离散但最快 10% 仍紧凑 → 稳健地保持小校正（正是只看最快圈的设计目的）。
    wide = _half_normal_shortfall(np.linspace(-5.0, 5.0, 200), cap=0.3)
    assert 0.0 <= wide <= 0.3
    # 最快 10% 本身高度离散 → 触发硬上限。
    heavy = np.concatenate([np.array([-10.0, -8.0, -6.0, -4.0, -2.0]), np.zeros(45)])
    assert _half_normal_shortfall(heavy, cap=0.3) == pytest.approx(0.3, abs=1e-9)


def _synthetic_race_event() -> pd.DataFrame:
    speed = {"McLaren": -1.0, "Red Bull Racing": -0.2, "Ferrari": 0.3, "Mercedes": 0.9}
    drivers = {
        "McLaren": ["NOR", "PIA"],
        "Red Bull Racing": ["VER", "TSU"],
        "Ferrari": ["LEC", "HAM"],
        "Mercedes": ["RUS", "ANT"],
    }
    rng = np.random.default_rng(3)
    rows: list[dict[str, object]] = []
    for team, base in speed.items():
        for driver in drivers[team]:
            for _ in range(20):
                rows.append(
                    {
                        "team_name": team,
                        "driver_id": driver,
                        "driver_neutral_vehicle_z": base + rng.normal(0.0, 0.2),
                    }
                )
    return pd.DataFrame(rows)


def test_car_ceiling_ranks_faster_team_highest() -> None:
    event = _synthetic_race_event()
    config = _config()
    ceilings = build_car_ceiling(event, pd.DataFrame(columns=event.columns), TEAMS, config["frontier"])
    medians = {
        team: ceilings[team]["weekend_theoretical_ceiling_advantage_z"]["median"]
        for team in TEAMS
    }
    assert max(medians, key=medians.get) == "McLaren"
    assert medians["McLaren"] > medians["Mercedes"]
    assert ceilings["McLaren"]["ceiling_uncertainty_includes_shortfall_bootstrap"]
    assert (
        ceilings["McLaren"]["race_half_normal_shortfall_uncertainty_z"]["median"]
        is not None
    )


# --------------------------------------------------------------------------- #
# Layer B —— 分离度（含真实 Gibbs 后验）
# --------------------------------------------------------------------------- #


def _tiny_posterior(base_speed: dict[str, float]):
    circuits = {"M1": "C1", "M2": "C2"}
    drivers = {
        "McLaren": ["NOR", "PIA"],
        "Red Bull Racing": ["VER", "TSU"],
        "Ferrari": ["LEC", "HAM"],
        "Mercedes": ["RUS", "ANT"],
    }
    rng = np.random.default_rng(11)
    rows: list[dict[str, object]] = []
    for meeting, circuit in circuits.items():
        for team, base in base_speed.items():
            for driver in drivers[team]:
                rows.append(
                    {
                        "team_year": f"2025|{team}",
                        "team_circuit": f"{team}|{circuit}",
                        "team_event": f"2025|{meeting}|{team}",
                        "driver_id": driver,
                        "driver_year": f"2025|{driver}",
                        "driver_team": f"{driver}|{team}",
                        "response_z": base + rng.normal(0.0, 0.05),
                        "likelihood_weight": 1.0,
                    }
                )
    aggregate = pd.DataFrame(rows)
    settings = {
        "chains": 2,
        "iterations": 600,
        "warmup": 300,
        "thin": 2,
        "variance_prior_shape": 3.0,
        "block_prior_sd_z": {block: 0.6 for block in BAYESIAN_BLOCKS},
        "noise_prior": {"shape": 3.0, "scale_sd_z": 0.2},
        "intercept_prior_sd_z": 1.0,
        "random_seed": 5,
    }
    return sample_posterior(aggregate, settings)


def _event_frame(base_speed: dict[str, float]) -> pd.DataFrame:
    drivers = {
        "McLaren": ["NOR", "PIA"],
        "Red Bull Racing": ["VER", "TSU"],
        "Ferrari": ["LEC", "HAM"],
        "Mercedes": ["RUS", "ANT"],
    }
    rng = np.random.default_rng(7)
    rows: list[dict[str, object]] = []
    for team, base in base_speed.items():
        for driver in drivers[team]:
            for _ in range(18):
                rows.append(
                    {
                        "team_name": team,
                        "driver_id": driver,
                        "driver_neutral_vehicle_z": base + rng.normal(0.0, 0.2),
                    }
                )
    return pd.DataFrame(rows)


def test_separation_strong_case_ranks_and_grades() -> None:
    base_speed = {"McLaren": -0.9, "Red Bull Racing": -0.3, "Ferrari": 0.3, "Mercedes": 0.9}
    posterior = _tiny_posterior(base_speed)
    event = _event_frame(base_speed)
    config = _config()
    result = build_separation(event, posterior, 2025, "M1", "C1", TEAMS, config["separation"])
    p1 = result["posterior_top_team_p_rank_1"]
    assert max(p1, key=p1.get) == "McLaren"
    assert result["mean_pairwise_ordering_confidence"] > 0.7
    assert result["separation_grade"] in {"medium", "high"}
    assert result["coverage_sufficient"]
    assert all(
        row["p_faster_than_slower"] >= 0.5 for row in result["separated_pairs"]
    )
    assert all(
        row["faster_team"] != row["slower_team"] for row in result["separated_pairs"]
    )


def test_separation_null_case_is_low() -> None:
    base_speed = {team: 0.0 for team in TEAMS}
    posterior = _tiny_posterior(base_speed)
    event = _event_frame(base_speed)
    config = _config()
    result = build_separation(event, posterior, 2025, "M1", "C1", TEAMS, config["separation"])
    assert result["separation_grade"] == "low"


def test_separation_insufficient_team_laps_forces_low() -> None:
    base_speed = {"McLaren": -0.9, "Red Bull Racing": -0.3, "Ferrari": 0.3, "Mercedes": 0.9}
    posterior = _tiny_posterior(base_speed)
    event = _event_frame(base_speed)
    settings = copy.deepcopy(_config()["separation"])
    settings["minimum_team_event_clean_laps"] = 100
    result = build_separation(event, posterior, 2025, "M1", "C1", TEAMS, settings)
    assert not result["coverage_sufficient"]
    assert result["separation_grade"] == "low"
    assert result["grade_components"]["sample_coverage"] == "fail"
