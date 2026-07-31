from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.evaluation import audit_balanced_meetings, compare_team_gaps  # noqa: E402
from modeling.hierarchical import (  # noqa: E402
    context_standardized_laps,
    fit_event_model,
    log_pct_delta_to_ms,
)


def event_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    drivers = {
        "Team A": ("A1", "A2"),
        "Team B": ("B1", "B2"),
    }
    team_effect = {"Team A": -0.4, "Team B": 0.4}
    driver_effect = {"A1": -0.1, "A2": 0.1, "B1": -0.2, "B2": 0.2}
    for team, team_drivers in drivers.items():
        for driver in team_drivers:
            for lap in range(3, 15):
                fraction = lap / 15.0
                tyre_age = float(lap - 2)
                target = (
                    team_effect[team]
                    + driver_effect[driver]
                    + 0.3 * fraction
                    + 0.01 * tyre_age
                )
                rows.append(
                    {
                        "meeting_name": "Synthetic Grand Prix",
                        "session_name": "Race",
                        "team_name": team,
                        "driver_id": driver,
                        "compound": "MEDIUM",
                        "stint_number": 1,
                        "lap_fraction": fraction,
                        "lap_fraction_sq": fraction**2,
                        "tyre_age": tyre_age,
                        "tyre_age_sq": tyre_age**2,
                        "target_log_pct": target,
                    }
                )
    return pd.DataFrame(rows)


def test_event_model_components_are_additive_and_centered() -> None:
    frame = event_frame()
    priors = {"A1": 0.1, "A2": -0.1, "B1": 0.2, "B2": -0.2}

    fit = fit_event_model(frame, priors, team_penalty=10.0, driver_update_penalty=100.0)
    components = fit.components(frame)

    additive = components[
        [
            "historical_driver_prior_z",
            "context_z",
            "team_z",
            "driver_event_update_z",
        ]
    ].sum(axis=1)
    np.testing.assert_allclose(additive, components["prediction_z"], atol=1e-12)
    assert abs(np.mean(list(fit.team_effect_z.values()))) < 1e-12
    for team, subset in frame[["team_name", "driver_id"]].drop_duplicates().groupby(
        "team_name"
    ):
        assert team in fit.team_effect_z
        updates = [fit.driver_update_z[driver] for driver in subset["driver_id"]]
        assert abs(float(np.mean(updates))) < 1e-12


def test_driver_extraction_keeps_historical_driver_ability() -> None:
    frame = event_frame()
    priors = {"A1": 0.1, "A2": -0.1, "B1": 0.2, "B2": -0.2}
    fit = fit_event_model(frame, priors, team_penalty=10.0, driver_update_penalty=100.0)

    adjusted = context_standardized_laps(frame, fit)

    expected = frame["target_log_pct"].to_numpy() - adjusted["context_z"].to_numpy()
    np.testing.assert_allclose(adjusted["driver_on_car_lap_z"], expected, atol=1e-12)
    assert not np.allclose(
        adjusted["driver_on_car_lap_z"], adjusted["driver_event_execution_lap_z"]
    )


def test_balance_audit_prefers_complete_even_race() -> None:
    rows: list[dict[str, object]] = []
    teams = ["A", "B", "C", "D"]
    for meeting, counts in (
        ("Balanced", (10, 10)),
        ("Uneven", (4, 20)),
    ):
        for team in teams:
            for driver_index, count in enumerate(counts):
                for _ in range(count):
                    rows.append(
                        {
                            "meeting_name": meeting,
                            "session_name": "Race",
                            "team_name": team,
                            "driver_id": f"{team}{driver_index}",
                        }
                    )

    audit = audit_balanced_meetings(pd.DataFrame(rows), teams, "Race")

    assert audit[0]["meeting_name"] == "Balanced"
    assert audit[0]["complete_four_team_grid"] is True
    assert audit[0]["driver_lap_count_cv"] == 0.0


def test_external_team_gap_comparison_reports_rank_and_pair_error() -> None:
    result = compare_team_gaps(
        {"A": 0.0, "B": 100.0, "C": 200.0},
        {"A": 0.0, "B": 120.0, "C": 180.0},
    )

    assert result["spearman_rank_correlation"] == 1.0
    assert result["model_order_fast_to_slow"] == ["A", "B", "C"]
    assert result["pairwise_gap_mae_ms"] > 0.0
    assert abs(log_pct_delta_to_ms(0.0, 90.0)) < 1e-12
