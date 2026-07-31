"""表现评分、赛果价值和证据包合同测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.performance_scoring import (  # noqa: E402
    Evidence,
    Episode,
    PerformanceScore,
    RaceValue,
    SeasonProfile,
    StintDossier,
    build_overall_score,
    close_six_attribution_components,
    shapley_interaction_closure,
)


def _pace_score(metric: str = "PAC") -> PerformanceScore:
    return PerformanceScore.from_z(
        metric,
        1.0,
        (0.5, 1.5),
        exposure=20,
        prior_exposure=20,
        model_version="synthetic-v1",
        data_cutoff="2025-12-31",
    )


def test_performance_score_uses_75_plus_10_shrunk_z_with_ci_and_exposure():
    score = _pace_score()
    assert score.shrinkage_weight == pytest.approx(0.5)
    assert score.shrunk_z == pytest.approx(0.5)
    assert score.score == pytest.approx(80.0)
    assert score.score_ci == pytest.approx((77.5, 82.5))
    assert score.exposure == 20
    assert score.confidence == "medium"
    assert score.to_dict()["formula"] == "75 + 10 * shrunk_z"


def test_zero_exposure_returns_prior_mean_but_is_not_publishable():
    score = PerformanceScore.from_z("ATK", 4.0, (2.0, 6.0), exposure=0)
    assert score.score == pytest.approx(75.0)
    assert score.score_ci == pytest.approx((75.0, 75.0))
    assert score.confidence == "insufficient"
    assert not score.publishable
    assert "no_exposure" in score.confidence_flags


def test_upstream_shrunk_score_is_not_shrunk_a_second_time():
    score = PerformanceScore.from_z(
        "PAC",
        0.8,
        (0.4, 1.2),
        exposure=20,
        prior_exposure=20,
        source_already_shrunk=True,
    )
    assert score.shrinkage_weight == pytest.approx(1.0)
    assert score.shrunk_z == pytest.approx(0.8)
    assert score.score == pytest.approx(83.0)
    assert score.source_already_shrunk
    assert "upstream_shrinkage_preserved" in score.confidence_flags


def test_performance_score_rejects_invalid_interval_and_exposure():
    with pytest.raises(ValueError, match="必须包含点估计"):
        PerformanceScore.from_z("PAC", 1.0, (-1.0, 0.0), exposure=10)
    with pytest.raises(ValueError, match="不能为负"):
        PerformanceScore.from_z("PAC", 0.0, (-1.0, 1.0), exposure=-1)


def test_race_value_zero_is_50_and_raw_physical_value_is_preserved():
    neutral = RaceValue.from_physical(
        "V_pace",
        0.0,
        (-20.0, 20.0),
        unit="ms/90s_lap",
        scale_per_10_points=100.0,
        higher_is_better=False,
    )
    faster = RaceValue.from_physical(
        "V_pace",
        -100.0,
        (-120.0, -80.0),
        unit="ms/90s_lap",
        scale_per_10_points=100.0,
        higher_is_better=False,
    )
    assert neutral.score == pytest.approx(50.0)
    assert neutral.physical_value == pytest.approx(0.0)
    assert neutral.to_dict()["unit"] == "ms/90s_lap"
    assert faster.score == pytest.approx(60.0)
    assert faster.score_ci == pytest.approx((58.0, 62.0))


def test_frozen_ovr_rejects_result_fields_but_accepts_ability_features():
    score = _pace_score()
    with pytest.raises(ValueError, match="冻结场景 OVR 禁止赛果字段"):
        build_overall_score(
            {"PAC": score},
            feature_names=("telemetry.brake_point", "result.actualPoints"),
        )

    overall = build_overall_score(
        {"PAC": score, "EXT": score},
        {"PAC": 0.7, "EXT": 0.3},
        feature_names=("lap_fraction", "traffic_dirty_air"),
    )
    assert overall.metric == "OVR"
    assert overall.score == pytest.approx(80.0)
    assert "ability_modules_only" in overall.confidence_flags
    assert "frozen_result_fields_rejected" in overall.confidence_flags


def test_ovr_rejects_missing_ability_instead_of_using_prior_mean_as_zero_skill():
    missing = PerformanceScore.from_z("ATK", 0.0, (-1.0, 1.0), exposure=0)
    with pytest.raises(ValueError, match="缺失/零暴露"):
        build_overall_score({"ATK": missing}, feature_names=("attack_opportunity",))


def test_shapley_non_additive_interaction_closes_exactly():
    result = shapley_interaction_closure(0.0, 2.0, 3.0, 10.0)
    assert result["interaction"] == pytest.approx(5.0)
    assert result["vehicle_shapley"] == pytest.approx(4.5)
    assert result["driver_shapley"] == pytest.approx(5.5)
    assert result["reconstructed_total"] == pytest.approx(result["total_gain"])
    assert result["closed"] is True


def test_six_signed_contributions_keep_interaction_and_shapley_view_closed():
    result = close_six_attribution_components(
        {
            "vehicle_contribution": 2.0,
            "driver_contribution": 3.0,
            "interaction_contribution": 4.0,
            "strategy_operations_contribution": -1.0,
            "reliability_incident_contribution": -2.0,
            "unexplained_contribution": 0.5,
        }
    )
    assert result["interaction_contribution"] == pytest.approx(4.0)
    assert result["vehicle_shapley_with_half_interaction"] == pytest.approx(4.0)
    assert result["driver_shapley_with_half_interaction"] == pytest.approx(5.0)
    assert result["shapley_total"] == pytest.approx(result["signed_total"])
    assert result["closed"] is True


def test_episode_without_opportunity_is_absent_not_zero_skill():
    episode = Episode(
        episode_id="2025-HUN-VER-ATK-0",
        episode_type="attack_opportunity",
        driver="VER",
        event_id="2025-HUN",
        lap_start=12,
        lap_end=12,
        opportunity=False,
        observed_outcome="no_opportunity_observed",
        exposure=0.0,
    )
    assert not episode.scoreable
    assert episode.to_dict()["exposure"] == 0.0


def test_evidence_episode_stint_and_season_contracts_remain_separate():
    evidence = Evidence(
        evidence_id="ev-1",
        source="frozen_telemetry_v5",
        payload={"gap_seconds_proxy": 0.8},
        directness_level="proxy",
        confidence="medium",
        limitations=("公开遥测代理",),
    )
    episode = Episode(
        episode_id="ep-1",
        episode_type="attack_opportunity",
        driver="VER",
        team="Red Bull Racing",
        event_id="2025-HUN",
        lap_start=12,
        lap_end=12,
        opportunity=True,
        observed_outcome="position_gain_proxy",
        exposure=1.0,
        evidence=(evidence,),
        confidence="medium",
        forbidden_claims=("不得直接称为超车能力真值",),
    )
    pace = _pace_score()
    race_value = RaceValue.from_physical(
        "V_points",
        2.0,
        (1.0, 3.0),
        unit="expected_points",
        scale_per_10_points=5.0,
        evidence_ids=(evidence.evidence_id,),
    )
    stint = StintDossier(
        dossier_id="stint-1",
        season=2025,
        event_id="2025-HUN",
        driver="VER",
        team="Red Bull Racing",
        stint_number=1,
        lap_start=1,
        lap_end=20,
        compound="MEDIUM",
        physical_metrics={"median_lap_ms": 81234.0},
        performance_scores=(pace,),
        race_values=(race_value,),
        episodes=(episode,),
        evidence=(evidence,),
        data_coverage=0.95,
    )
    overall = build_overall_score({"PAC": pace}, feature_names=("lap_fraction",))
    profile = SeasonProfile(
        profile_id="profile-2025-VER",
        season=2025,
        driver="VER",
        scenario="frozen_2025",
        overall=overall,
        performance_scores=(pace,),
        race_values=(race_value,),
        stint_dossiers=(stint,),
        episodes=(episode,),
        evidence=(evidence,),
        ability_feature_names=("lap_fraction", "traffic_dirty_air"),
        event_exposure=24,
    )
    payload = profile.to_dict()
    assert payload["overall"]["metric"] == "OVR"
    assert payload["performance_scores"][0]["score"] == pytest.approx(80.0)
    assert payload["race_values"][0]["physical_value"] == pytest.approx(2.0)
    assert payload["stint_dossiers"][0]["episodes"][0]["evidence"][0]["evidence_id"] == "ev-1"


def test_frozen_season_profile_rejects_result_leakage():
    with pytest.raises(ValueError, match="禁止 OVR 赛果字段"):
        SeasonProfile(
            profile_id="bad-profile",
            season=2026,
            driver="AAA",
            scenario="frozen_2026",
            ability_feature_names=("pace_z", "classification_position"),
        )
