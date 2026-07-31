from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from run_four_team_contrast_stress import (  # noqa: E402
    _lap_time_seconds,
    _podium_team_order,
    _team_envelopes,
)


def test_lap_time_seconds_parses_official_result_format() -> None:
    assert _lap_time_seconds("1:20.901") == 80.901


def test_podium_team_order_keeps_first_appearance() -> None:
    assert _podium_team_order(["McLaren", "McLaren", "Mercedes"]) == [
        "McLaren",
        "Mercedes",
    ]


def test_team_envelopes_rank_lower_standardized_time_as_faster() -> None:
    rows = []
    for team, offset in (("A", 0.0), ("B", 0.5)):
        for driver in (f"{team}1", f"{team}2"):
            for lap, value in enumerate((0.0, 0.2, 0.4, 0.6, 0.8), start=1):
                rows.append(
                    {
                        "team_name": team,
                        "driver_id": driver,
                        "vehicle_lap_z": value + offset,
                        "lap": lap,
                    }
                )

    result = _team_envelopes(
        pd.DataFrame(rows),
        ["A", "B"],
        reference_lap_seconds=90.0,
        quantiles={"ceiling": 0.1, "baseline": 0.5, "floor": 0.9},
    )

    assert [row["team"] for row in result] == ["A", "B"]
    assert result[0]["baseline_gap_ms"] == 0.0
    assert result[1]["baseline_gap_ms"] > 0.0
