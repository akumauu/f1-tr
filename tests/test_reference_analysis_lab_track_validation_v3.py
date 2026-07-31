"""参考分析复刻实验室 v3 分赛道验证协议与反例测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from reference_analysis_lab.track_validation_v3 import (  # noqa: E402
    DELTADATA_MODEL_FEATURES,
    F1PACE_MODEL_FEATURES,
    assign_observable_track_cluster,
    candidate_publication_decision,
    compute_f1pace_lap_features,
    evaluate_segmentation_strategy,
    observable_layout_discontinuity,
    prepare_deltadata_model_frame,
)
from run_reference_analysis_lab_track_validation_v3 import (  # noqa: E402
    candidate_train_events,
)


def _traffic_points(distances: list[float], *, lap_duration: float) -> pd.DataFrame:
    rows = []
    for index, distance in enumerate(distances):
        rows.append(
            {
                "driver_acronym": "AAA",
                "lap": 2,
                "sample_index": index,
                "time": float(index),
                "lap_duration": lap_duration,
                "speed": 36.0,
                "driver_ahead": "BBB",
                "distance_to_driver_ahead": distance,
                "position": 2,
                "stint": 1,
                "compound": "MEDIUM",
                "tyre_life": 3.0,
                "track_status": "1",
                "deleted": False,
                "is_accurate": True,
                "team": "Example",
            }
        )
    return pd.DataFrame(rows)


def test_f1pace_v3_keeps_fixed_two_second_and_strict_one_third_semantics():
    half = compute_f1pace_lap_features(
        _traffic_points([10.0, 20.0, 21.0, 100.0], lap_duration=4.0)
    ).iloc[0]
    assert half["traffic_ratio_2_00s"] == pytest.approx(0.5)
    assert bool(half["traffic_lap_2s_33pct"]) is True

    exact_third = compute_f1pace_lap_features(
        _traffic_points([10.0, 21.0, 100.0], lap_duration=3.0)
    ).iloc[0]
    assert exact_third["traffic_ratio_2_00s"] == pytest.approx(1.0 / 3.0)
    assert bool(exact_third["traffic_lap_2s_33pct"]) is False


def test_f1pace_v3_surrogate_features_do_not_contain_the_two_second_target():
    assert "traffic_ratio_2_00s" not in F1PACE_MODEL_FEATURES
    assert "traffic_ratio_1_75s" in F1PACE_MODEL_FEATURES
    assert "traffic_ratio_2_25s" in F1PACE_MODEL_FEATURES


def test_track_cluster_names_are_observable_proxies_not_physical_claims():
    cluster = assign_observable_track_cluster(210.0, 2.7)
    assert cluster == "speed_high__corner_dense"
    assert "downforce" not in cluster
    assert "power" not in cluster


def test_deltadata_v3_model_frame_uses_observables_and_fail_closed_clean_air():
    points = pd.DataFrame(
        [
            {
                "event_id": "2025-01-example",
                "driver": "AAA",
                "lap": 10,
                "tyre_age_laps": 8,
                "raw_lap_duration_s": 90.0,
                "adjusted_pace_s": 89.5,
                "traffic_ratio_2_00s": 0.10,
                "position": 2,
                "gap_seconds_proxy": 0.5,
                "analysis_weight": 0.8,
                "lap_fraction": 0.25,
                "compound": "MEDIUM",
                "all_green": True,
                "pit_boundary_proxy": False,
                "quality_ok": True,
                "used_for_primary_fit": True,
            },
            {
                "event_id": "2025-01-example",
                "driver": "BBB",
                "lap": 11,
                "tyre_age_laps": 9,
                "raw_lap_duration_s": 90.2,
                "adjusted_pace_s": 89.6,
                "traffic_ratio_2_00s": np.nan,
                "position": 3,
                "gap_seconds_proxy": 0.7,
                "analysis_weight": 0.8,
                "lap_fraction": 0.28,
                "compound": "HARD",
                "all_green": True,
                "pit_boundary_proxy": False,
                "quality_ok": True,
                "used_for_primary_fit": True,
            },
        ]
    )
    prepared = prepare_deltadata_model_frame(points)
    assert prepared.loc[0, "target_adjustment_s"] == pytest.approx(-0.5)
    assert bool(prepared.loc[0, "clean_air_candidate"]) is True
    assert bool(prepared.loc[1, "clean_air_candidate"]) is False
    assert not {
        "nuisance_adjustment_total",
        "fuel_correction_target",
        "adjusted_pace_s",
    }.intersection(DELTADATA_MODEL_FEATURES)


def _track_profile(*, peak_shift: float = 0.0, speed_flip: bool = False):
    grid = np.linspace(0.0, 1.0, 1201)
    centres = np.array([0.12, 0.31, 0.53, 0.76, 0.91]) + peak_shift
    lateral = np.zeros_like(grid)
    brake = np.zeros_like(grid)
    speed = np.full_like(grid, 280.0)
    for centre in centres:
        delta = np.minimum(np.abs(grid - centre), 1.0 - np.abs(grid - centre))
        lateral += 18.0 * np.exp(-0.5 * np.square(delta / 0.006))
        brake += 0.9 * np.exp(-0.5 * np.square(delta / 0.010))
        speed -= 120.0 * np.exp(-0.5 * np.square(delta / 0.020))
    if speed_flip:
        speed = 450.0 - speed
    return {
        "grid": grid,
        "speed_kph": speed,
        "throttle_pct": np.where(brake > 0.2, 25.0, 100.0),
        "brake_share": np.clip(brake, 0.0, 1.0),
        "gear": np.where(brake > 0.2, 4.0, 8.0),
        "drs_share": np.where(brake > 0.2, 0.0, 0.7),
        "lateral_accel_mps2": lateral,
        "longitudinal_accel_mps2": np.gradient(speed),
    }


def test_fdataanalysis_v3_strategy_preserves_endpoints_and_reports_tail_error():
    metrics = evaluate_segmentation_strategy(
        _track_profile(),
        {
            "strategy_id": "axis800_sigma002",
            "axis_intervals": 800,
            "extra_smoothing_sigma_fraction": 0.002,
        },
    )
    assert metrics["endpoint_start_error"] == 0.0
    assert metrics["endpoint_finish_error"] == 0.0
    assert metrics["peak_p90_fraction"] is not None
    assert metrics["peak_max_error_fraction"] is not None


def test_layout_discontinuity_proxy_blocks_track_model_without_calling_it_official():
    audit = observable_layout_discontinuity(
        [
            (2023, _track_profile()),
            (2024, _track_profile(speed_flip=True)),
        ],
        minimum_corner_match_share=0.7,
        maximum_corner_count_difference=3,
        minimum_speed_profile_correlation=0.8,
    )
    assert audit["status"] == "OBSERVABLE_LAYOUT_DISCONTINUITY_PROXY"
    assert audit["track_model_allowed"] is False
    assert "not_official_layout_change" in audit["interpretation"]


def test_candidate_with_better_mae_but_worse_tail_is_rejected():
    gate = {
        "minimum_test_events": 2,
        "minimum_kish_ess": 20,
        "minimum_relative_mae_improvement": 0.02,
        "maximum_relative_p90_degradation": 0.02,
        "maximum_relative_max_error_degradation": 0.05,
        "minimum_fold_improvement_share": 2 / 3,
        "maximum_coverage_loss": 0.01,
        "maximum_direction_accuracy_loss": 0.01,
    }
    universal = {
        "mae": 1.0,
        "p90_abs_error": 2.0,
        "max_abs_error": 4.0,
        "coverage": 1.0,
        "direction_accuracy": 0.9,
    }
    candidate = {
        "mae": 0.8,
        "p90_abs_error": 2.5,
        "max_abs_error": 4.0,
        "coverage": 1.0,
        "direction_accuracy": 0.9,
    }
    passed, failures = candidate_publication_decision(
        universal,
        candidate,
        fold_improvement_share=1.0,
        test_events=3,
        training_kish_ess=100,
        gate=gate,
    )
    assert passed is False
    assert "p90_degradation_above_gate" in failures


def test_cluster_split_accepts_catalog_that_already_contains_cluster_column():
    catalog = pd.DataFrame(
        [
            {
                "event_id": "2023-a",
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
            },
            {
                "event_id": "2024-b",
                "track_key": "b",
                "observable_cluster": "speed_low__corner_dense",
            },
            {
                "event_id": "2025-c",
                "track_key": "c",
                "observable_cluster": "speed_high__corner_sparse",
            },
        ]
    )
    tracks = pd.DataFrame(
        [
            {
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
                "year_count": 3,
                "track_model_allowed_by_layout_proxy": True,
            },
            {
                "track_key": "b",
                "observable_cluster": "speed_low__corner_dense",
                "year_count": 3,
                "track_model_allowed_by_layout_proxy": True,
            },
            {
                "track_key": "c",
                "observable_cluster": "speed_high__corner_sparse",
                "year_count": 3,
                "track_model_allowed_by_layout_proxy": True,
            },
        ]
    )
    events, reason = candidate_train_events(
        catalog,
        tracks,
        test_event_id="2023-a",
        model_type="cluster",
    )
    assert events == ["2024-b"]
    assert reason is None


def test_f1pace_external_reference_events_are_never_returned_for_training():
    catalog = pd.DataFrame(
        [
            {
                "event_id": "2023-a",
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
            },
            {
                "event_id": "2024-a",
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
            },
            {
                "event_id": "2025-abu",
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
            },
            {
                "event_id": "2025-qatar",
                "track_key": "qatar",
                "observable_cluster": "speed_high__corner_sparse",
            },
        ]
    )
    tracks = pd.DataFrame(
        [
            {
                "track_key": "a",
                "observable_cluster": "speed_low__corner_dense",
                "year_count": 3,
                "track_model_allowed_by_layout_proxy": True,
            },
            {
                "track_key": "qatar",
                "observable_cluster": "speed_high__corner_sparse",
                "year_count": 3,
                "track_model_allowed_by_layout_proxy": True,
            },
        ]
    )
    events, reason = candidate_train_events(
        catalog,
        tracks,
        test_event_id="2023-a",
        model_type="universal",
        excluded_event_ids=["2025-abu", "2025-qatar"],
    )
    assert events == ["2024-a"]
    assert reason is None

    track_events, track_reason = candidate_train_events(
        catalog,
        tracks,
        test_event_id="2023-a",
        model_type="track",
        excluded_event_ids=["2025-abu", "2025-qatar"],
    )
    assert track_events == []
    assert track_reason == "same_track_training_events_below_two"
