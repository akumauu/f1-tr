from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import build_v4_frozen_data as freeze  # noqa: E402


def config() -> dict:
    return {
        "session_names": ["Race", "Sprint"],
        "dry_compounds": ["SOFT", "MEDIUM", "HARD"],
        "cleaning": {
            "minimum_lap_number": 2,
            "minimum_lap_duration_seconds": 40.0,
            "maximum_lap_duration_seconds": 240.0,
            "robust_mad_multiplier": 6.0,
            "minimum_clean_laps_per_session": 2,
        },
    }


def raw_laps() -> pd.DataFrame:
    rows = []
    for lap in range(2, 7):
        rows.append(
            {
                "year": 2024,
                "meeting": "Test Grand Prix",
                "session": "Race",
                "driver_number": 1,
                "lap_number": lap,
                "driver_id": "TST",
                "team_name": "Test Team",
                "lap_duration": 90.0 + lap / 10,
                "compound": "MEDIUM",
                "stint_number": 1,
                "tyre_age": float(lap),
                "deleted": False,
                "is_accurate": True,
                "track_status": "1",
                "source_commit": "abc",
                "source_commit_count": 1,
                "sample_count": 100,
            }
        )
    return pd.DataFrame(rows)


def test_clean_laps_derives_model_columns() -> None:
    clean, exclusions = freeze.clean_laps(raw_laps(), config())

    assert len(clean) == 5
    assert clean["session_key"].nunique() == 1
    assert clean["is_disrupted_lap"].eq(False).all()
    assert clean["target_log_pct"].notna().all()
    assert exclusions["candidate_rows_before_robust_filter"] == 5


def test_clean_laps_rejects_inaccurate_and_non_green_laps() -> None:
    frame = raw_laps()
    frame.loc[0, "is_accurate"] = False
    frame.loc[1, "track_status"] = "12"

    clean, exclusions = freeze.clean_laps(frame, config())

    assert len(clean) == 3
    assert exclusions["inaccurate_including_pit_or_sc"] == 1
    assert exclusions["non_green_track_status"] == 1
