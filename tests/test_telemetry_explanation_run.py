"""端到端报告编排中的评分与数据分区合同测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_telemetry_explanation as runner  # noqa: E402


def _config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "telemetry_explanation_2026_australia_ferrari_v7.json"
        ).read_text(encoding="utf-8")
    )


def test_partition_uses_highest_available_schema_with_identity_hash():
    path, identity = runner._resolve_partition(_config())
    assert path.is_file()
    assert identity["schema"] in {
        "tracinginsights-expanded-v4",
        "tracinginsights-expanded-v5",
    }
    assert len(identity["sha256"]) == 64
    expected_rank = 1 if identity["schema"].endswith("v5") else 2
    assert identity["schema_preference_rank"] == expected_rank


def test_pac_stays_null_until_driver_specific_time_forward_gate_passes():
    rows = runner._rating_cards(
        _config(),
        clean_laps_by_driver={"HAM": 48, "LEC": 48},
        matched_pairs=19,
        observed_episode_rows=32,
    )
    ham = next(row for row in rows if row["code"] == "HAM.PAC")
    lec = next(row for row in rows if row["code"] == "LEC.PAC")
    assert ham["rating_mean"] is None
    assert lec["rating_mean"] is None
    assert ham["maturity"] == "prototype"
    assert lec["maturity"] == "prototype"
    assert ham["rating_interval_95"] is None
    assert ham["uncertainty_note"] == "pooled_driver_base_interval_not_exported"
    event = next(row for row in rows if row["code"] == "HAM.EventDelivery")
    assert event["rating_mean"] is None
    assert event["exposure"]["independent_episodes"] is None
    assert next(row for row in rows if row["code"] == "HAM.OVR")["status"] == "insufficient"


def test_missing_objective_review_fails_closed_for_pac(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "OBJECTIVE_REVIEW", tmp_path / "missing.json")
    rows = runner._rating_cards(_config())
    pac = [row for row in rows if row["module_id"] == "PAC"]
    assert pac
    assert all(row["rating_mean"] is None for row in pac)
    assert all(row["status"] == "insufficient" for row in pac)


def test_strategy_comparison_keeps_same_label_and_feature_strength_separate():
    rows = [
        {
            "driver_acronym": "AAA",
            "status": "ok",
            "mode_label": "late_gain_mode",
            "mode_distribution": {"late_gain_mode": 8, "save_like_mode": 2},
            "evidence": {"peak_straight_speed_kmh": 320.0, "response_proxy": 0.8},
        },
        {
            "driver_acronym": "BBB",
            "status": "ok",
            "mode_label": "late_gain_mode",
            "mode_distribution": {"late_gain_mode": 9, "save_like_mode": 1},
            "evidence": {"peak_straight_speed_kmh": 318.0, "response_proxy": 0.6},
        },
    ]
    comparison = runner._compare_strategy_fingerprints(
        rows, target_driver="AAA", reference_driver="BBB"
    )
    assert comparison["same_dominant_mode"]
    assert comparison["target_minus_reference_feature_differences"][
        "peak_straight_speed_kmh"
    ] == pytest.approx(2.0)
    assert "确认" not in comparison["interpretation"]


def test_matching_balance_exposes_fraction_of_caliper():
    matches = pd.DataFrame(
        {
            "delta_tyre_life": [-2.0, -1.0],
            "delta_lap_fraction": [0.04, 0.0],
            "delta_lap_context": [4.0, 0.0],
            "target_compound": ["MEDIUM", "MEDIUM"],
            "reference_compound": ["MEDIUM", "MEDIUM"],
        }
    )
    balance = runner._matching_balance(matches, _config())
    assert balance["all_within_declared_calipers"]
    assert balance["maximum_mean_absolute_fraction_of_caliper"] == pytest.approx(0.75)


import pytest  # noqa: E402
import pandas as pd  # noqa: E402
