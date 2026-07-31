from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.seasonal_attribution import (  # noqa: E402
    DESCRIPTIVE_BLOCKS,
    extract_season_ratings,
    fit_seasonal_model,
    local_live_timing_reference,
    meeting_driver_weights,
    prepare_season_frame,
    split_time_forward,
)


def synthetic_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    teams = {"Team A": ("A1", "A2"), "Team B": ("B1", "B2")}
    team_effect = {"Team A": -0.35, "Team B": 0.35}
    driver_effect = {"A1": -0.10, "A2": 0.10, "B1": -0.15, "B2": 0.15}
    meetings = ("Round One", "Round Two", "Round Three")
    for year in (2023, 2024, 2025):
        for round_index, meeting in enumerate(meetings, 1):
            for team, drivers in teams.items():
                for driver in drivers:
                    for lap in range(3, 15):
                        fraction = lap / 15.0
                        target = (
                            team_effect[team]
                            + driver_effect[driver]
                            + 0.22 * fraction
                            + 0.01 * (lap - 2)
                            + 0.02 * round_index
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
                                "tyre_age": float(lap - 2),
                                "tyre_age_sq": float((lap - 2) ** 2),
                                "target_log_pct": target,
                            }
                        )
    return pd.DataFrame(rows)


def prepared() -> pd.DataFrame:
    order = {
        str(year): ["Round One", "Round Two", "Round Three"]
        for year in (2023, 2024, 2025)
    }
    return prepare_season_frame(synthetic_frame(), {}, order)


def penalties() -> dict[str, float]:
    return {
        "team_year": 4.0,
        "team_circuit": 20.0,
        "driver": 8.0,
        "driver_year": 30.0,
        "driver_team": 120.0,
        "team_event": 12.0,
        "driver_event": 180.0,
    }


def test_meeting_driver_weights_equalize_total_weight() -> None:
    frame = prepared()
    frame = pd.concat([frame, frame.iloc[:10]], ignore_index=True)
    weights = meeting_driver_weights(frame)
    totals = (
        frame.assign(weight=weights)
        .groupby(["meeting_key", "driver_id"])["weight"]
        .sum()
    )
    assert float(totals.max() - totals.min()) < 1e-10


def test_components_are_additive_and_car_order_is_recovered() -> None:
    frame = prepared()
    fit = fit_seasonal_model(frame, penalties(), blocks=DESCRIPTIVE_BLOCKS)
    components = fit.components(frame)
    np.testing.assert_allclose(
        components.drop(columns="prediction_z").sum(axis=1),
        components["prediction_z"],
        atol=1e-10,
    )
    ratings = extract_season_ratings(fit, frame, ["Team A", "Team B"])
    for year in (2023, 2024, 2025):
        year_rows = [row for row in ratings["cars"] if row["year"] == year]
        assert [row["team"] for row in year_rows] == ["Team A", "Team B"]


def test_huber_irls_downweights_extreme_lap() -> None:
    frame = prepared()
    extreme = frame.iloc[[0]].copy()
    extreme["target_log_pct"] = 50.0
    contaminated = pd.concat([frame, extreme], ignore_index=True)
    fit = fit_seasonal_model(contaminated, penalties(), blocks=DESCRIPTIVE_BLOCKS)
    assert fit.robust_weights[-1] < 0.1
    assert np.isfinite(fit.predict(contaminated)).all()


def test_time_forward_split_uses_only_initial_target_rounds() -> None:
    train, validation = split_time_forward(prepared(), 2025, adaptation_races=1)
    assert train["year"].max() == 2025
    assert train.loc[train["year"] == 2025, "round_index"].max() == 1
    assert validation["round_index"].min() == 2


def test_local_reference_is_offline_proxy(tmp_path: Path) -> None:
    race = tmp_path / "Synthetic Grand Prix" / "Race"
    for position, (driver, team) in enumerate(
        (("A1", "Team A"), ("B1", "Team B"), ("A2", "Team A"), ("B2", "Team B")),
        1,
    ):
        directory = race / driver
        directory.mkdir(parents=True)
        payload = {
            "lap": [1, 2, 3],
            "pos": [position, position, position],
            "team": [team, team, team],
            "time": [90.0 + position, 89.0 + position, 88.0 + position],
        }
        (directory / "laptimes.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    # 参考构造器要求至少 10 位车手，补足不计分实体。
    for position in range(5, 11):
        directory = race / f"X{position}"
        directory.mkdir(parents=True)
        (directory / "laptimes.json").write_text(
            json.dumps(
                {
                    "lap": [1, 2, 3],
                    "pos": [position] * 3,
                    "team": [f"Team {position}"] * 3,
                    "time": [100.0 + position] * 3,
                }
            ),
            encoding="utf-8",
        )

    reference = local_live_timing_reference(2025, tmp_path, {})

    assert reference["points_are_official"] is False
    assert reference["event_count"] == 1
    assert reference["teams"][0]["team"] == "Team A"
