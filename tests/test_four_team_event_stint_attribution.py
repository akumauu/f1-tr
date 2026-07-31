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

from modeling.event_stint_attribution import (  # noqa: E402
    bootstrap_event_frontiers,
    build_stint_features,
    rank_alignment,
    validate_event_stint_config,
)


def _config() -> dict[str, object]:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "four_team_2023_2025_event_stint_v4.json"
        ).read_text(encoding="utf-8")
    )


def test_event_stint_config_enforces_frontier_boundaries() -> None:
    config = _config()
    validate_event_stint_config(config)
    invalid = copy.deepcopy(config)
    invalid["frontier"]["race_ceiling_quantile"] = 0.60
    with pytest.raises(ValueError, match="ceiling"):
        validate_event_stint_config(invalid)
    invalid = copy.deepcopy(config)
    invalid["boundaries"]["absolute_qualifying_race_time_merge"] = True
    with pytest.raises(ValueError, match="绝对圈时"):
        validate_event_stint_config(invalid)


def test_driver_balanced_bootstrap_recovers_faster_car_direction() -> None:
    rng = np.random.default_rng(42)
    rows: list[dict[str, object]] = []
    for team, center in (("Team A", -0.4), ("Team B", 0.4)):
        for driver in (f"{team}-1", f"{team}-2"):
            for value in center + rng.normal(0.0, 0.04, 12):
                rows.append(
                    {
                        "team_name": team,
                        "driver_id": driver,
                        "driver_neutral_vehicle_z": value,
                    }
                )
    samples = bootstrap_event_frontiers(
        pd.DataFrame(rows),
        "driver_neutral_vehicle_z",
        ["Team A", "Team B"],
        quantile=0.10,
        repetitions=80,
        seed=7,
    )
    assert float(np.median(samples["Team A"]["robust_advantage_z"])) > 0.3
    assert float(np.mean(samples["Team A"]["robust_advantage_z"] > 0.0)) > 0.95


def test_stint_features_keep_execution_and_excess_degradation() -> None:
    rows: list[dict[str, object]] = []
    for lap, tyre_age in enumerate(range(2, 10), start=5):
        expected = 90.0 + 0.02 * tyre_age
        residual_ms = 30.0 + 12.0 * tyre_age
        rows.append(
            {
                "year": 2025,
                "meeting_name": "Test Grand Prix",
                "driver_id": "AAA",
                "team_name": "Team A",
                "stint_number": 2,
                "compound": "MEDIUM",
                "lap_number": lap,
                "tyre_age": float(tyre_age),
                "lap_duration": expected + residual_ms / 1000.0,
                "expected_lap_seconds": expected,
                "model_residual_ms": residual_ms,
                "execution_loss_ms": max(0.0, residual_ms - 54.0),
            }
        )
    result = build_stint_features(pd.DataFrame(rows), _config())
    assert len(result) == 1
    assert result[0]["confidence"] == "usable"
    assert result[0]["typical_execution_loss_ms"] > 0.0
    assert np.isclose(
        result[0]["degradation_residual_ms_per_tyre_lap"], 12.0, atol=1e-8
    )


def test_rank_alignment_has_explicit_noncausal_bands() -> None:
    assert rank_alignment(2, 3, 1) == "compatible_with_clean_pace"
    assert rank_alignment(4, 1, 1) == "result_better_than_clean_pace"
    assert rank_alignment(1, 4, 1) == "result_worse_than_clean_pace"
    assert rank_alignment(None, 1, 1) == "unavailable"
