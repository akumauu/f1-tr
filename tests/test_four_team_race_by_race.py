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

from modeling.race_by_race import (  # noqa: E402
    build_rolling_rank_validation,
    rank_spearman,
    validate_race_by_race_config,
    validate_stints,
)


def _config() -> dict[str, object]:
    return json.loads(
        (
            ROOT / "research" / "configs" / "four_team_race_by_race_v5.json"
        ).read_text(encoding="utf-8")
    )


def test_v5_config_separates_same_event_from_prediction() -> None:
    config = _config()
    validate_race_by_race_config(config)
    invalid = copy.deepcopy(config)
    invalid["boundaries"]["same_event_metrics_are_predictive_validation"] = True
    with pytest.raises(ValueError, match="预测验证"):
        validate_race_by_race_config(invalid)
    invalid = copy.deepcopy(config)
    invalid["projection_2026"]["current_local_events"] = 4
    invalid["projection_2026"]["minimum_events_for_confirmatory_release"] = 5
    invalid["boundaries"]["2026_confirmatory_release_allowed_below_gate"] = True
    with pytest.raises(ValueError, match="确认性"):
        validate_race_by_race_config(invalid)


def test_rank_spearman_uses_only_common_entities() -> None:
    assert np.isclose(
        rank_spearman(
            {"A": 1, "B": 2, "C": 3, "EXTRA": 4},
            {"A": 1, "B": 2, "C": 3},
        ),
        1.0,
    )
    assert rank_spearman({"A": 1}, {"A": 1}) is None


def test_stint_validation_has_true_late_holdout_and_bootstrap_interval() -> None:
    rows = []
    for index, tyre_age in enumerate(range(1, 13), start=5):
        rows.append(
            {
                "year": 2025,
                "meeting_name": "Test Grand Prix",
                "driver_id": "AAA",
                "team_name": "Team A",
                "stint_number": 1,
                "compound": "MEDIUM",
                "lap_number": index,
                "tyre_age": float(tyre_age),
                "model_residual_ms": 25.0 + 10.0 * tyre_age,
            }
        )
    existing = [
        {
            "year": 2025,
            "meeting_name": "Test Grand Prix",
            "driver": "AAA",
            "team": "Team A",
            "stint_number": 1,
            "compound": "MEDIUM",
            "clean_laps": 12,
            "pace_residual_ms_p50": 90.0,
            "consistency_mad_ms": 44.0,
            "degradation_residual_ms_per_tyre_lap": 10.0,
        }
    ]
    output, events, summary = validate_stints(
        pd.DataFrame(rows), existing, _config()["stint_validation"]
    )
    assert output[0]["validation_status"] == "usable_holdout"
    assert np.isclose(output[0]["early_fit_late_holdout_mae_ms"], 0.0, atol=1e-8)
    assert output[0]["degradation_bootstrap_ci80_low"] > 0.0
    assert output[0]["degradation_slope_sign_stable"] is True
    assert events[(2025, "Test Grand Prix")]["holdout_eligible_stints"] == 1
    assert summary["holdout_eligible_stints"] == 1


def test_rolling_validation_predicts_before_current_event_update() -> None:
    teams = ["A", "B"]
    events = []
    for round_number in range(1, 8):
        advantage_a = 1.0 if round_number <= 6 else -2.0
        ranks = {"A": 1, "B": 2} if advantage_a > 0 else {"A": 2, "B": 1}
        events.append(
            {
                "year": 2025,
                "round": round_number,
                "meeting_name": f"R{round_number}",
                "data_status": "available",
                "cars": [
                    {
                        "team": "A",
                        "weekend_ceiling_advantage_z": {"median": advantage_a},
                        "same_event_fastest_clean_lap_rank_four_team": ranks["A"],
                        "classification_proxy_rank_four_team": ranks["A"],
                    },
                    {
                        "team": "B",
                        "weekend_ceiling_advantage_z": {"median": -advantage_a},
                        "same_event_fastest_clean_lap_rank_four_team": ranks["B"],
                        "classification_proxy_rank_four_team": ranks["B"],
                    },
                ],
                "same_event_comparison": {
                    "fastest_clean_lap_team": "A" if ranks["A"] == 1 else "B",
                    "classification_proxy_top_team": "A" if ranks["A"] == 1 else "B",
                },
            }
        )
    settings = {
        "exponential_update_alpha": 0.35,
        "season_boundary_carry": 0.5,
        "minimum_prior_events": 5,
        "role": "test",
    }
    result = build_rolling_rank_validation(events, teams, settings)
    assert result["events"][0]["meeting_name"] == "R6"
    assert result["events"][1]["meeting_name"] == "R7"
    # R7 当前站发生反转，但预测必须仍由 R1-R6 的 A 优势状态产生。
    assert result["events"][1]["predicted_top_team"] == "A"
    assert result["events"][1]["fastest_clean_lap_top_team"] == "B"
