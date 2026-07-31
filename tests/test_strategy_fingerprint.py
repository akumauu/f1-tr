from __future__ import annotations

import numpy as np
import pandas as pd

from research.modeling.strategy_fingerprint import (
    FORBIDDEN_CLAIMS,
    MODE_LABELS,
    StraightFeatureConfig,
    build_strategy_mode_fingerprints,
    derive_strategy_lap_features,
    fit_strategy_mode_fingerprints,
)


FEATURES = (
    "early_accel_mps2",
    "late_accel_mps2",
    "full_throttle_response_mps2",
    "lift_coast_fraction",
    "full_throttle_fraction",
    "high_rpm_dwell_fraction",
)


def _mode_frame() -> pd.DataFrame:
    centers = {
        "E": (3.0, -1.0, 0.2, 0.1, 0.70, 0.40),
        "L": (-1.0, 3.0, 0.3, 0.1, 0.75, 0.50),
        "S": (0.1, 0.1, -1.0, 3.0, 0.20, 0.10),
        "H": (0.5, 0.5, 3.0, -1.0, 3.00, 3.00),
    }
    rows: list[dict[str, object]] = []
    for driver, center in centers.items():
        for lap in range(1, 5):
            row: dict[str, object] = {
                "year": 2026,
                "meeting": "Synthetic GP",
                "session": "Race",
                "driver_acronym": driver,
                "lap": lap,
                "feature_status": "ok",
            }
            for index, column in enumerate(FEATURES):
                row[column] = center[index] + (lap - 2.5) * 0.015
            rows.append(row)
    return pd.DataFrame(rows)


def test_derive_strategy_lap_features_from_annotated_straights() -> None:
    rows: list[dict[str, object]] = []
    for segment, start in ((1, 0.0), (3, 200.0)):
        for offset in np.linspace(0.0, 100.0, 11):
            rows.append(
                {
                    "year": 2026,
                    "meeting": "Synthetic GP",
                    "session": "Race",
                    "team": "Example",
                    "driver_acronym": "AAA",
                    "driver_number": "1",
                    "lap": 10,
                    "distance": start + offset,
                    "speed": 180.0 + 0.55 * offset,
                    "phase_reference_speed_kmh": 178.0 + 0.50 * offset,
                    "track_phase": "straight",
                    "track_segment_id": segment,
                    "throttle": 100.0 if offset < 90.0 else 15.0,
                    "brake": False,
                    "gear": 6 if offset < 50.0 else 7,
                    "rpm": 9_500.0 + 18.0 * offset,
                }
            )
    features = derive_strategy_lap_features(
        pd.DataFrame(rows),
        config=StraightFeatureConfig(
            minimum_samples=20,
            minimum_straight_segments=2,
            minimum_straight_distance_m=150.0,
        ),
    )

    assert len(features) == 1
    assert features.loc[0, "feature_status"] == "ok"
    assert features.loc[0, "straight_segments"] == 2
    assert features.loc[0, "straight_distance_m"] == 200.0
    assert np.isfinite(features.loc[0, "early_accel_mps2"])
    assert np.isfinite(features.loc[0, "late_speed_residual_kmh"])
    assert 0.0 < features.loc[0, "lift_coast_fraction"] < 1.0


def test_kmeans_modes_are_neutral_and_deterministic_under_row_shuffle() -> None:
    frame = _mode_frame()
    first = fit_strategy_mode_fingerprints(
        frame,
        feature_columns=FEATURES,
        n_modes=4,
        minimum_rows=8,
    )
    second = fit_strategy_mode_fingerprints(
        frame.sample(frac=1.0, random_state=91),
        feature_columns=FEATURES,
        n_modes=4,
        minimum_rows=8,
    )

    assert first.status == "ok"
    assert set(first.assignments["mode_label"]).issubset(MODE_LABELS)
    assert set(first.assignments["mode_label"]) == {
        "early_gain_mode",
        "late_gain_mode",
        "save_like_mode",
        "high_response_mode",
    }
    columns = ["driver_acronym", "lap", "mode_label"]
    left = first.assignments[columns].sort_values(columns[:2]).reset_index(drop=True)
    right = second.assignments[columns].sort_values(columns[:2]).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
    assert first.forbidden_claims == FORBIDDEN_CLAIMS


def test_public_wrapper_returns_group_exposure_evidence_and_boundaries() -> None:
    rows = build_strategy_mode_fingerprints(
        _mode_frame(),
        feature_columns=FEATURES,
        n_modes=4,
        group_columns=("driver_acronym",),
        minimum_rows=8,
    )

    assert len(rows) == 4
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["mode_label"] in MODE_LABELS for row in rows)
    assert all(row["exposure"]["rows"] == 4 for row in rows)
    assert all(set(row["evidence"]) == set(FEATURES) for row in rows)
    assert all(row["forbidden_claims"] == list(FORBIDDEN_CLAIMS) for row in rows)
    assert all("SOC" not in row["interpretation"] for row in rows)


def test_insufficient_and_not_comparable_are_explicit() -> None:
    insufficient = fit_strategy_mode_fingerprints(
        _mode_frame().head(4),
        feature_columns=FEATURES,
        minimum_rows=8,
    )
    missing = fit_strategy_mode_fingerprints(
        _mode_frame(),
        feature_columns=(*FEATURES, "unknown_proxy"),
        minimum_rows=8,
    )
    leaked = fit_strategy_mode_fingerprints(
        _mode_frame().assign(points=0),
        feature_columns=(*FEATURES, "points"),
        minimum_rows=8,
    )

    assert insufficient.status == "insufficient"
    assert insufficient.assignments.empty
    assert missing.status == "not_comparable"
    assert leaked.status == "not_comparable"
    assert "结果标签" in leaked.reason


def test_degenerate_features_do_not_force_a_mode_story() -> None:
    frame = _mode_frame().head(10).copy()
    for column in FEATURES:
        frame[column] = 1.0
    result = fit_strategy_mode_fingerprints(
        frame,
        feature_columns=FEATURES,
        minimum_rows=8,
    )

    assert result.status == "insufficient"
    assert result.assignments.empty
    assert result.context_audit[0]["reason"] == "feature_coverage_or_variation_insufficient"


def test_deterministic_fallback_works_without_sklearn() -> None:
    result = fit_strategy_mode_fingerprints(
        _mode_frame(),
        feature_columns=FEATURES,
        n_modes=4,
        minimum_rows=8,
        prefer_sklearn=False,
    )

    assert result.status == "ok"
    assert result.method == "deterministic_fallback_kmeans"
    assert set(result.assignments["mode_label"]).issubset(MODE_LABELS)
    assert result.assignments["confidence_score"].between(0.0, 0.74).all()


def test_result_columns_present_but_not_selected_do_not_change_modes() -> None:
    base = fit_strategy_mode_fingerprints(
        _mode_frame(), feature_columns=FEATURES, minimum_rows=8
    )
    contaminated_frame = _mode_frame().assign(points=np.arange(16), finish_position=1)
    audited = fit_strategy_mode_fingerprints(
        contaminated_frame, feature_columns=FEATURES, minimum_rows=8
    )

    assert audited.status == "ok"
    assert "points" not in audited.feature_columns
    base_modes = base.assignments.sort_values(["driver_acronym", "lap"])["mode_label"].tolist()
    audited_modes = audited.assignments.sort_values(["driver_acronym", "lap"])["mode_label"].tolist()
    assert base_modes == audited_modes

