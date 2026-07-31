"""同队双车逐段报告的时间守恒、响应残差和异常门控测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.team_telemetry_report import (  # noqa: E402
    build_anomaly_evidence,
    build_pair_segment_differences,
    build_segment_lap_features,
    build_stint_dossiers,
    fit_field_response_baseline,
    summarize_segment_profiles,
)
from modeling.telemetry_explanation import TrackPhaseMap  # noqa: E402


def _phase_map() -> TrackPhaseMap:
    return TrackPhaseMap(
        pd.DataFrame(
            {
                "distance": [0.0, 100.0, 200.0, 300.0],
                "track_phase": ["straight", "straight", "corner", "corner"],
                "track_segment_id": [1, 1, 2, 2],
                "reference_speed_kmh": [200.0, 220.0, 150.0, 170.0],
            }
        )
    )


def test_segment_features_preserve_lap_time_sum():
    rows = []
    for driver, lap in (("AAA", 1), ("BBB", 2)):
        for index, distance in enumerate((0.0, 100.0, 200.0, 300.0)):
            rows.append(
                {
                    "year": 2026,
                    "meeting": "Synthetic",
                    "session": "Race",
                    "team": "Team",
                    "driver_acronym": driver,
                    "lap": lap,
                    "distance": distance,
                    "speed": 180.0 + index,
                    "segment_dt_s": 0.0 if index == 0 else 2.0,
                    "throttle": 100.0,
                    "brake": 0.0,
                    "gear": 7.0,
                    "rpm": 11000.0,
                    "acc_x": 0.2,
                }
            )
    features = build_segment_lap_features(pd.DataFrame(rows), _phase_map())
    totals = features.groupby(["driver_acronym", "lap"])["segment_time_s"].sum()
    assert np.allclose(totals.to_numpy(), [6.0, 6.0])
    assert set(features.track_phase) == {"straight", "corner"}


def test_field_response_baseline_and_pair_gap_are_separate():
    field_rows = []
    for lap in range(1, 21):
        throttle = 80.0 + lap / 2
        field_rows.append(
            {
                "driver_acronym": f"F{lap % 4}",
                "lap": lap,
                "track_segment_id": 1,
                "track_phase": "straight",
                "distance_start_m": 0.0,
                "distance_end_m": 300.0,
                "segment_time_s": 5.0 - throttle / 100.0,
                "entry_speed_kmh": 200.0,
                "exit_speed_kmh": 250.0,
                "minimum_speed_kmh": 200.0,
                "maximum_speed_kmh": 250.0,
                "mean_speed_kmh": 225.0,
                "mean_throttle_pct": throttle,
                "full_throttle_fraction": throttle / 100.0,
                "brake_fraction": 0.0,
                "mean_gear": 7.0,
                "mean_rpm": 11000.0,
                "mean_acc_x_proxy": 0.5,
            }
        )
    team = pd.DataFrame(
        [
            {**field_rows[0], "driver_acronym": "AAA", "lap": 10, "segment_time_s": 4.3},
            {**field_rows[0], "driver_acronym": "BBB", "lap": 11, "segment_time_s": 4.1},
        ]
    )
    scored = fit_field_response_baseline(
        pd.DataFrame(field_rows), team, minimum_rows_per_segment=12
    )
    matches = pd.DataFrame(
        {
            "target_driver_acronym": ["AAA"],
            "target_lap": [10],
            "reference_driver_acronym": ["BBB"],
            "reference_lap": [11],
            "match_quality": [1.0],
        }
    )
    pair = build_pair_segment_differences(matches, scored)
    assert np.isclose(pair.external_condition_matched_gap_s.iloc[0], 0.2)
    assert np.isfinite(pair.input_adjusted_response_gap_s.iloc[0])
    profiles = summarize_segment_profiles(
        pair, random_seed=7, force_low_confidence=True
    )
    assert profiles[0]["delta_time_ms"] == pytest.approx(200.0)
    assert "input_adjusted_response_gap_ms" in profiles[0]
    assert profiles[0]["confidence"] == "low"
    assert profiles[0]["comparability_warning"]


def test_persistent_three_baseline_step_can_trigger_but_not_name_failure():
    rows = []
    for lap in range(1, 31):
        step = 0.20 if 25 <= lap <= 27 else 0.0
        rows.append(
            {
                "target_lap": lap,
                "target_driver_acronym": "AAA",
                "reference_driver_acronym": "BBB",
                "track_segment_id": 1,
                "track_phase": "straight",
                "target_segment_time_s": 1.0 + step,
                "reference_segment_time_s": 1.0,
                "external_condition_matched_gap_s": step,
                "input_adjusted_response_gap_s": step,
                "delta_exit_speed_kmh": -20.0 if 25 <= lap <= 27 else 0.0,
                "delta_minimum_speed_kmh": -10.0 if 25 <= lap <= 27 else 0.0,
                "target_distance_start_m": 0.0,
                "target_distance_end_m": 300.0,
            }
        )
    field = pd.DataFrame(
        {
            "track_segment_id": [1] * 20,
            "segment_time_s": [1.0 + (index % 3 - 1) * 0.001 for index in range(20)],
        }
    )
    episodes, audit = build_anomaly_evidence(
        pd.DataFrame(rows),
        field,
        minimum_persistent_laps=3,
        practical_time_loss_ms=20.0,
        false_discovery_rate=0.10,
    )
    assert audit.anomaly_triggered.any()
    assert audit.loc[audit.anomaly_triggered, "kinematic_response_family_supported"].all()
    assert episodes
    assert episodes[0]["primary_class"] == "single_car_observed_response_divergence"
    assert any("不得命名具体故障" in claim for claim in episodes[0]["forbidden_claims"])


def test_stint_dossiers_never_merge_same_driver_stint_across_events():
    rows = []
    for meeting in ("Event A", "Event B"):
        for lap in range(1, 7):
            rows.append(
                {
                    "year": 2026,
                    "meeting": meeting,
                    "session": "Race",
                    "driver_acronym": "AAA",
                    "team": "Team",
                    "stint": 1,
                    "compound": "MEDIUM",
                    "lap": lap,
                    "stint_phase": "steady",
                    "is_clean": True,
                    "lap_duration": 90.0 + lap / 10,
                    "traffic_state": "clean_air",
                }
            )
    dossiers = build_stint_dossiers(pd.DataFrame(rows), [])
    assert len(dossiers) == 2
    assert {row["meeting"] for row in dossiers} == {"Event A", "Event B"}
    assert all(row["compound"] == "MEDIUM" for row in dossiers)


# pytest 仅用于 approx；放在文件尾避免遮蔽上方数据构造。
import pytest  # noqa: E402
