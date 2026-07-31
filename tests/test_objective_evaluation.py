"""评分系统客观性门控测试。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.objective_evaluation import (  # noqa: E402
    audit_ability_feature_leakage,
    evaluate_objectivity,
)


def _fixtures():
    seasonal = {
        "standings_calibration": {
            "constructor_by_year": [
                {"spearman_car_z_vs_standings_position": value}
                for value in (0.85, 0.90, 0.96)
            ],
            "driver_by_year": [
                {"spearman_realized_vs_standings": value}
                for value in (0.96, 0.86, 0.91)
            ],
            "pooled_spearman_car_vs_constructors": 0.89,
            "pooled_spearman_realized_vs_drivers": 0.90,
        },
        "rolling_origin": {"n_origins": 44},
        "transfer_graph": {"connected": False, "n_components": 4},
    }
    bayesian = {
        "posterior_diagnostics": {"maximum_scale_rhat": 1.02},
        "time_forward_validation": {
            "years": {
                "2025": {
                    "zero": {"event_balanced_mae_z": 0.74},
                    "bayesian_joint": {
                        "event_balanced_mae_z": 0.60,
                        "calibrated_95_coverage": 0.94,
                    },
                }
            }
        },
    }
    race = {"projection_2026": {"observed_events": 4}}
    return seasonal, bayesian, race


def test_leakage_audit_is_exact_and_path_aware():
    assert audit_ability_feature_leakage(
        ["lap.pace", "actual_points", "labels/finish_position", "point_estimate"]
    ) == ["actual_points", "labels/finish_position"]


def test_historical_core_can_pass_while_full_card_and_2026_are_not_ready():
    seasonal, bayesian, race = _fixtures()
    coverage = {
        "PAC": "validated",
        "TYM": "prototype",
        "EXT": "validated",
        "ATK": "missing",
        "DEF": "missing",
        "AWA": "prototype",
        "STR": "missing",
        "ADP": "missing",
    }
    report = evaluate_objectivity(
        seasonal,
        bayesian,
        race,
        ability_feature_names=["lap_fraction", "tyre_age", "driver_id"],
        module_coverage=coverage,
    )
    assert report.core_historical_passed
    assert not report.full_driver_card_ready
    assert report.release_status.startswith("historical_result_accounting_passed")
    assert report.module_coverage["PAC"] == "prototype"
    pac_gate = next(g for g in report.gates if g.gate_id == "pac_driver_specific_time_forward")
    assert not pac_gate.passed
    gate = next(g for g in report.gates if g.gate_id == "2026_confirmatory_exposure")
    assert not gate.passed
    assert gate.value == 4


def test_pac_requires_driver_specific_time_forward_rank_and_interval_evidence():
    seasonal, bayesian, race = _fixtures()
    seasonal["pac_time_forward_validation"] = {
        "locked_events": 24,
        "rank_spearman": 0.76,
        "interval_coverage": 0.94,
    }
    coverage = {key: "validated" for key in ("PAC", "TYM", "EXT", "ATK", "DEF", "AWA", "STR", "ADP")}
    report = evaluate_objectivity(
        seasonal,
        bayesian,
        race,
        ability_feature_names=["pace"],
        module_coverage=coverage,
    )
    assert report.module_coverage["PAC"] == "validated"
    pac_gate = next(g for g in report.gates if g.gate_id == "pac_driver_specific_time_forward")
    assert pac_gate.passed


def test_result_label_leakage_fails_historical_gate():
    seasonal, bayesian, race = _fixtures()
    coverage = {key: "validated" for key in ("PAC", "TYM", "EXT", "ATK", "DEF", "AWA", "STR", "ADP")}
    report = evaluate_objectivity(
        seasonal,
        bayesian,
        race,
        ability_feature_names=["pace", "championship_points"],
        module_coverage=coverage,
    )
    assert not report.core_historical_passed
    assert not report.full_driver_card_ready
