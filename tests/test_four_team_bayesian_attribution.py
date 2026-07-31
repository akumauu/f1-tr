from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.bayesian_counterfactual import (  # noqa: E402
    build_context_adjusted_aggregates,
    extract_counterfactual_ratings,
    gelman_rubin_rhat,
    posterior_mean_prediction,
    posterior_prediction_draws,
    sample_posterior,
)
from modeling.seasonal_attribution import prepare_season_frame  # noqa: E402


def _prepared_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    teams = {"Team A": ("A1", "A2"), "Team B": ("B1", "B2")}
    team_effect = {"Team A": -0.40, "Team B": 0.40}
    driver_effect = {"A1": -0.18, "A2": 0.18, "B1": -0.12, "B2": 0.12}
    meetings = ("Round One", "Round Two", "Round Three", "Round Four")
    rng = np.random.default_rng(42)
    for year in (2023, 2024, 2025):
        for round_index, meeting in enumerate(meetings, 1):
            for team, drivers in teams.items():
                for driver in drivers:
                    for lap in range(4, 22):
                        fraction = lap / 22.0
                        target = (
                            team_effect[team]
                            + driver_effect[driver]
                            + 0.18 * fraction
                            + 0.012 * (lap - 3)
                            + rng.normal(0.0, 0.025)
                        )
                        rows.append(
                            {
                                "year": year,
                                "meeting_name": meeting,
                                "circuit_name": meeting,
                                "session_name": "Race",
                                "driver_id": driver,
                                "team_name": team,
                                "compound": "MEDIUM",
                                "lap_fraction": fraction,
                                "lap_fraction_sq": fraction**2,
                                "tyre_age": float(lap - 3),
                                "tyre_age_sq": float((lap - 3) ** 2),
                                "target_log_pct": target,
                            }
                        )
    order = {str(year): list(meetings) for year in (2023, 2024, 2025)}
    return prepare_season_frame(pd.DataFrame(rows), {}, order)


def _aggregation() -> dict[str, float | int | str]:
    return {
        "location": "huber_all_clean_laps",
        "huber_k": 1.5,
        "iterations": 6,
        "standard_error_floor_z": 0.02,
        "maximum_relative_precision": 5.0,
    }


def _settings() -> dict[str, object]:
    return {
        "chains": 2,
        "iterations": 500,
        "warmup": 250,
        "thin": 5,
        "random_seed": 1234,
        "intercept_prior_sd_z": 2.0,
        "noise_prior": {"shape": 3.0, "scale_sd_z": 0.5},
        "variance_prior_shape": 3.0,
        "block_prior_sd_z": {
            "team_year": 0.6,
            "team_circuit": 0.15,
            "team_event": 0.10,
            "driver": 0.4,
            "driver_year": 0.10,
            "driver_team": 0.03,
        },
    }


def _aggregate() -> pd.DataFrame:
    aggregate, _ = build_context_adjusted_aggregates(
        _prepared_frame(),
        {
            "team_year": 4.0,
            "team_circuit": 24.0,
            "driver": 8.0,
            "driver_year": 30.0,
            "driver_team": 120.0,
        },
        {"irls_iterations": 3, "huber_k": 1.5},
        _aggregation(),
    )
    return aggregate


def test_context_aggregation_keeps_all_clean_laps() -> None:
    aggregate = _aggregate()
    assert len(aggregate) == 3 * 4 * 4
    assert int(aggregate["lap_count"].sum()) == len(_prepared_frame())
    assert np.isfinite(aggregate["response_z"]).all()
    assert np.isclose(float(aggregate["likelihood_weight"].mean()), 1.0)


def test_bayesian_counterfactual_recovers_car_and_driver_direction() -> None:
    aggregate = _aggregate()
    posterior = sample_posterior(aggregate, _settings())
    ratings = extract_counterfactual_ratings(
        posterior, aggregate, ["Team A", "Team B"]
    )
    cars = {
        (row["year"], row["team"]): row for row in ratings["cars"]
    }
    drivers = {
        (row["year"], row["driver"]): row for row in ratings["drivers"]
    }
    assert cars[(2025, "Team A")]["car_pace_advantage_z"]["mean"] > 0.20
    assert cars[(2025, "Team A")]["p_rank_1"] > 0.80
    assert (
        drivers[(2025, "A1")]["driver_pace_advantage_z"]["mean"]
        > drivers[(2025, "A2")]["driver_pace_advantage_z"]["mean"]
    )


def test_unseen_levels_use_zero_mean_prior_in_prediction() -> None:
    aggregate = _aggregate()
    posterior = sample_posterior(aggregate, _settings())
    unseen = aggregate.iloc[[0]].copy()
    unseen["team_year"] = "2026|Unknown"
    unseen["team_circuit"] = "Unknown|Nowhere"
    unseen["team_event"] = "2026|Nowhere|Unknown"
    unseen["driver_id"] = "NEW"
    unseen["driver_year"] = "2026|NEW"
    unseen["driver_team"] = "NEW|Unknown"
    prediction = posterior_mean_prediction(posterior, unseen)
    predictive = posterior_prediction_draws(
        posterior, unseen, include_observation_noise=True, random_seed=99
    )
    assert prediction.shape == (1,)
    assert np.isfinite(prediction[0])
    assert predictive.shape == (2, 50, 1)
    assert float(np.std(predictive)) > 0.05


def test_rhat_is_one_for_identical_stationary_chains() -> None:
    values = np.tile(np.linspace(-1.0, 1.0, 100), (4, 1))
    assert gelman_rubin_rhat(values) < 1.01
