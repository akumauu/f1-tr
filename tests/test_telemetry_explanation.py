"""距离域遥测解释、匹配基线和统计门控测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.telemetry_explanation import (  # noqa: E402
    ComparableLapConfig,
    TELEMETRY_CLAIM_BOUNDARIES,
    TrackPhaseConfig,
    TrackPhaseMap,
    apply_persistent_fdr_gate,
    benjamini_hochberg,
    compute_gap_baselines,
    extract_corner_straight_features,
    integrate_delta_time,
    match_comparable_laps,
    resample_distance,
    resolve_strategy_mode,
    summarize_gap_baselines,
)


def test_distance_resampling_and_dt_ds_over_v_are_unit_consistent():
    raw = pd.DataFrame(
        {
            "driver_acronym": ["AAA", "AAA", "AAA"],
            "lap": [1, 1, 1],
            "distance": [0.0, 60.0, 100.0],
            "speed": [36.0, 36.0, 36.0],
            "throttle": [100.0, 100.0, 100.0],
        }
    )
    sampled = resample_distance(raw, step_m=20.0)
    assert sampled["distance"].tolist() == [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]
    assert np.isclose(sampled["elapsed_time_s"].iloc[-1], 10.0)
    assert np.isclose(integrate_delta_time(sampled), 10.0)


def test_track_phase_map_and_corner_straight_features_use_frozen_distance_map():
    rows = []
    for lap in (1, 2):
        for distance, speed, throttle, brake in (
            (0.0, 280.0, 100.0, 0.0),
            (50.0, 280.0, 100.0, 0.0),
            (100.0, 120.0, 40.0, 1.0),
            (150.0, 120.0, 50.0, 0.2),
            (200.0, 280.0, 100.0, 0.0),
        ):
            rows.append(
                {
                    "driver_acronym": "AAA",
                    "lap": lap,
                    "distance": distance,
                    "speed": speed,
                    "throttle": throttle,
                    "brake": brake,
                }
            )
    reference = pd.DataFrame(rows)
    phase_map = TrackPhaseMap.from_reference_laps(
        reference,
        config=TrackPhaseConfig(minimum_phase_length_m=0.0),
    )
    phase_by_distance = phase_map.frame.set_index("distance")["track_phase"].to_dict()
    assert phase_by_distance[0.0] == "straight"
    assert phase_by_distance[100.0] == "corner"
    features = extract_corner_straight_features(reference, phase_map)
    assert len(features) == 2
    assert (features["corner_segments"] == 1).all()
    assert (features["straight_max_speed_kmh"] == 280.0).all()
    assert (features["corner_min_speed_kmh"] == 120.0).all()
    assert (features["phase_time_coverage"] == 1.0).all()


def test_corner_distance_anchors_split_long_phase_without_changing_phase_labels():
    reference = pd.DataFrame(
        {
            "driver_acronym": ["AAA"] * 7,
            "lap": [1] * 7,
            "distance": np.arange(0.0, 700.0, 100.0),
            "speed": [120.0] * 7,
            "throttle": [30.0] * 7,
            "brake": [1.0] * 7,
        }
    )
    phase_map = TrackPhaseMap.from_reference_laps(
        reference,
        config=TrackPhaseConfig(minimum_phase_length_m=0.0),
        anchor_distances=[100.0, 300.0, 500.0],
    )
    assert phase_map.frame["track_phase"].eq("corner").all()
    assert phase_map.frame["track_segment_id"].nunique() == 3


def test_track_phase_map_rejects_too_few_reference_laps_when_gate_is_enabled():
    reference = pd.DataFrame(
        {
            "driver_acronym": ["AAA"] * 4,
            "lap": [1] * 4,
            "distance": [0.0, 100.0, 200.0, 300.0],
            "speed": [250.0, 150.0, 150.0, 250.0],
        }
    )
    with pytest.raises(ValueError, match="参考圈"):
        TrackPhaseMap.from_reference_laps(
            reference,
            config=TrackPhaseConfig(
                minimum_phase_length_m=0.0,
                minimum_reference_laps=2,
            ),
        )


def test_track_phase_coverage_uses_full_frozen_map_not_only_local_fragment():
    phase_map = TrackPhaseMap(
        pd.DataFrame(
            {
                "distance": [0.0, 100.0, 200.0, 300.0, 400.0],
                "track_phase": ["straight", "straight", "corner", "corner", "straight"],
                "track_segment_id": [1, 1, 2, 2, 3],
                "reference_speed_kmh": [250.0, 250.0, 150.0, 150.0, 250.0],
            }
        )
    )
    fragment = pd.DataFrame(
        {
            "driver_acronym": ["AAA", "AAA", "AAA"],
            "lap": [1, 1, 1],
            "distance": [100.0, 200.0, 300.0],
            "speed": [250.0, 150.0, 150.0],
        }
    )
    features = extract_corner_straight_features(fragment, phase_map)
    assert features.loc[0, "phase_time_coverage"] < 1.0
    assert features.loc[0, "phase_distance_coverage"] == pytest.approx(0.5)


def test_comparable_lap_matching_blocks_context_and_minimizes_input_distance():
    targets = pd.DataFrame(
        [
            {
                "year": 2025,
                "meeting": "Synthetic",
                "session": "Race",
                "compound": "MEDIUM",
                "track_status": "green",
                "driver_acronym": "AAA",
                "lap": 10,
                "tyre_age": 5.0,
                "lap_fraction": 0.50,
                "lap_time_s": 90.0,
            }
        ]
    )
    candidates = pd.DataFrame(
        [
            {
                "year": 2025,
                "meeting": "Synthetic",
                "session": "Race",
                "compound": "MEDIUM",
                "track_status": "green",
                "driver_acronym": "BBB",
                "lap": 9,
                "tyre_age": 5.5,
                "lap_fraction": 0.51,
                "lap_time_s": 89.8,
            },
            {
                "year": 2025,
                "meeting": "Synthetic",
                "session": "Race",
                "compound": "SOFT",
                "track_status": "green",
                "driver_acronym": "CCC",
                "lap": 11,
                "tyre_age": 5.0,
                "lap_fraction": 0.50,
                "lap_time_s": 88.0,
            },
        ]
    )
    config = ComparableLapConfig(
        numeric_tolerances={"tyre_age": 2.0, "lap_fraction": 0.05}
    )
    matched = match_comparable_laps(targets, candidates, config=config)
    assert len(matched) == 1
    assert matched.iloc[0]["reference_driver_acronym"] == "BBB"
    assert np.isclose(matched.iloc[0]["delta_tyre_age"], -0.5)
    assert matched.iloc[0]["match_quality"] > 0.68


def test_comparable_lap_matching_is_global_not_greedy_and_requires_declared_fields():
    targets = pd.DataFrame(
        {
            "year": [2026, 2026],
            "meeting": ["Synthetic", "Synthetic"],
            "session": ["Race", "Race"],
            "compound": ["MEDIUM", "MEDIUM"],
            "track_status": ["green", "green"],
            "driver_acronym": ["AAA", "AAA"],
            "tyre_age": [0.0, 0.11],
        }
    )
    candidates = pd.DataFrame(
        {
            "year": [2026, 2026],
            "meeting": ["Synthetic", "Synthetic"],
            "session": ["Race", "Race"],
            "compound": ["MEDIUM", "MEDIUM"],
            "track_status": ["green", "green"],
            "driver_acronym": ["BBB", "BBB"],
            "tyre_age": [0.10, -0.20],
        }
    )
    config = ComparableLapConfig(numeric_tolerances={"tyre_age": 1.0})
    matched = match_comparable_laps(targets, candidates, config=config)
    pairs = {
        (int(row.target_position), int(row.reference_position))
        for row in matched.itertuples()
    }
    # 贪心会先选 (1, 0)，迫使 (0, 1)，总距离 0.21；全局最优为 0.19。
    assert pairs == {(0, 1), (1, 0)}
    assert matched["match_rule"].eq(
        "exact_context_then_global_minimum_normalized_input_distance"
    ).all()

    with pytest.raises(ValueError, match="精确阻断"):
        match_comparable_laps(
            targets.drop(columns="track_status"),
            candidates,
            config=config,
        )


def test_three_baselines_keep_external_and_input_adjusted_gaps_separate():
    pairs = pd.DataFrame(
        {
            "year": [2025, 2026],
            "target_time": [91.0, 91.0],
            "internal_reference": [90.5, 90.5],
            "external_reference": [90.0, 90.0],
            "delta_tyre_age": [2.0, 2.0],
            "delta_strategy_proxy": [1.0, 1.0],
        }
    )
    result = compute_gap_baselines(
        pairs,
        target_time_column="target_time",
        internal_reference_column="internal_reference",
        external_reference_column="external_reference",
        adjustment_coefficients={"tyre_age": 0.2, "strategy_proxy": 0.3},
        season_column="year",
    )
    assert result["internal_gap_s"].tolist() == [0.5, 0.5]
    assert result["external_gap_s"].tolist() == [1.0, 1.0]
    assert np.isclose(result.loc[0, "input_adjusted_gap_s"], 0.3)
    assert np.isclose(result.loc[1, "input_adjusted_gap_s"], 0.6)
    assert result.loc[1, "strategy_mode"] == "neutral"
    assert result.loc[1, "strategy_proxy_ignored"]
    summary = summarize_gap_baselines(result)
    assert summary["baseline"].tolist() == ["internal", "external", "input_adjusted"]


def test_bh_fdr_and_persistence_gate_require_same_direction_consecutive_events():
    corrected = benjamini_hochberg([0.01, 0.02, 0.20], alpha=0.05)
    assert corrected["fdr_rejected"].tolist() == [True, True, False]
    assert np.allclose(corrected["q_value"], [0.03, 0.03, 0.20])

    effects = pd.DataFrame(
        {
            "event": [1, 2, 3, 4, 1, 2, 3],
            "feature": ["corner"] * 4 + ["straight"] * 3,
            "effect": [0.2, 0.3, 0.4, -0.5, 0.2, -0.3, -0.4],
            "p_value": [0.01] * 7,
        }
    )
    gated = apply_persistent_fdr_gate(
        effects,
        entity_columns=["feature"],
        event_order_column="event",
        minimum_consecutive_events=3,
    )
    corner = gated[gated["feature"] == "corner"].sort_values("event")
    straight = gated[gated["feature"] == "straight"].sort_values("event")
    assert corner["persistent_gate_passed"].tolist() == [False, False, True, False]
    assert not straight["persistent_gate_passed"].any()
    assert corner.iloc[2]["publication_status"] == "persistent_signal"


def test_2026_is_always_strategy_neutral_and_claim_boundaries_are_explicit():
    assert resolve_strategy_mode(2025, "auto") == "observed_proxy"
    assert resolve_strategy_mode(2026, "observed_proxy") == "neutral"
    joined = "".join(TELEMETRY_CLAIM_BOUNDARIES)
    assert "物理故障" in joined
    assert "荷电状态" in joined
