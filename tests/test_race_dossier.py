"""逐场 Race Dossier 的全场门槛、策略代理与审计反例测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.race_dossier import (  # noqa: E402
    _condition_balance,
    _delete_block_fit_sensitivity,
    _descriptive_confirmed_curve_refit,
    annotate_race_laps,
    build_pre_race_tyre_envelope,
    build_race_dossier,
    build_season_audit,
    build_stint_dossiers,
    validate_race_dossier_config,
)


def _config() -> dict:
    return {
        "scope": {
            "session": "Race",
            "reporting_teams": ["Team A", "Team B"],
        },
        "coverage_gate": {
            "minimum_race_laps": 12,
            "minimum_reporting_teams": 2,
            "minimum_drivers_per_team": 2,
            "minimum_clean_laps_per_team": 8,
            "minimum_valid_stints_per_team": 2,
            "minimum_modelable_lap_fraction": 0.45,
        },
        "lap_filter": {
            "dry_compounds": ["SOFT", "MEDIUM", "HARD"],
            "minimum_distance_coverage": 0.95,
            "minimum_samples_per_lap": 20,
            "dirty_air_gap_seconds": 2.5,
            "clean_air_gap_seconds": 4.0,
            "slow_lap_mad": 3.5,
        },
        "stint_gate": {
            "minimum_observed_laps": 6,
            "minimum_modelable_laps": 4,
            "minimum_modelable_fraction": 0.50,
            "minimum_tyre_age_span": 3.0,
            "stint_validity_independent_of_baseline": True,
            "minimum_baseline_laps_for_cumulative": 3,
            "minimum_baseline_fraction_for_cumulative": 0.50,
            "warmup_laps": 2,
            "late_stint_fraction": 0.25,
        },
        "baseline": {
            "minimum_laps_per_cell": 4,
            "minimum_teams_per_cell": 2,
            "tyre_age_bin_laps": 2,
            "lap_fraction_bin": 0.10,
        },
        "strategy_envelope": {
            "minimum_valid_stints_per_compound": 2,
            "minimum_drivers_per_stop_pattern": 2,
        },
        "materiality": {
            "pit_window_seconds": 2.0,
            "require_adjacent_gap_proxy": True,
        },
        "publication_contract": {
            "analysis_unit": "complete_race",
            "qualifying_race_independent": True,
            "publish_pac": False,
            "publish_ovr": False,
            "classification_or_points_used_for_fit": False,
            "claim_real_fuel_load": False,
            "claim_pirelli_internal_simulation": False,
        },
        "bootstrap": {"repetitions": 100, "seed": 7},
    }


def _v11_config() -> dict:
    config = _config()
    config["method_version"] = "v11"
    config["simulation_proxies"] = {
        "fuel": {
            "enabled": True,
            "model": "linear_remaining_mass_sensitivity_v1",
            "initial_fuel_kg": {"low": 90.0, "base": 100.0, "high": 110.0},
            "lap_time_s_per_kg": {"low": 0.02, "base": 0.03, "high": 0.04},
            "burn_rule": "linear_by_scheduled_race_fraction",
        },
        "track_evolution": {
            "enabled": True,
            "model": "robust_additive_field_median_v1",
            "minimum_laps_per_bin": 8,
            "minimum_teams_per_bin": 2,
            "minimum_drivers_per_bin": 4,
            "maximum_abs_correction_s": 3.0,
        },
        "traffic": {
            "enabled": True,
            "model": "empirical_gap_band_fixed_effects_v1",
            "minimum_clean_laps": 8,
            "minimum_laps_per_state": 8,
            "minimum_drivers_per_state": 4,
            "shrinkage_laps": 0,
            "maximum_penalty_s": 3.0,
        },
    }
    config["module_publication_gate"] = {
        "minimum_publishable_teams": 2,
        "team": {
            "minimum_drivers": 2,
            "minimum_pace_adjusted_laps": 8,
            "minimum_valid_stints": 2,
            "minimum_baseline_laps": 4,
        },
        "driver_pair": {
            "minimum_drivers": 2,
            "minimum_pace_adjusted_laps_per_driver": 4,
            "minimum_valid_stints_per_driver": 1,
        },
    }
    return config


def _v12_config() -> dict:
    config = _v11_config()
    config["method_version"] = "v12"
    config["stint_curve_models"] = {
        "enabled": True,
        "candidates": [
            "constant_median",
            "linear_theil_sen",
            "log_theil_sen",
            "quadratic_least_squares",
            "kalman_local_linear",
        ],
        "minimum_train_laps": 4,
        "minimum_validation_laps": 2,
        "minimum_mae_improvement_s": 0.005,
        "maximum_prediction_deviation_s": 5.0,
        "kalman": {
            "level_process_variance": 0.0025,
            "slope_process_variance": 0.0001,
            "measurement_sigma_floor_s": 0.03,
        },
    }
    return config


def _v13_config() -> dict:
    config = _v12_config()
    config["method_version"] = "v13"
    config["stint_curve_models"].update(
        {
            "validation_protocol": "ordered_selection_confirmation_v1",
            "selection_fraction": 0.5,
            "minimum_selection_points": 2,
            "minimum_confirmation_points": 2,
            "minimum_confirmation_improvement_s": 0.01,
        }
    )
    config["pit_loss_proxy"] = {
        "enabled": True,
        "model": "local_adjacent_pace_pit_cycle_v1",
        "minimum_adjacent_pace_laps_each_side": 2,
        "minimum_valid_cycles_per_event": 2,
        "minimum_historical_events": 2,
        "maximum_cycle_loss_s": 60.0,
    }
    return config


def _v14_config() -> dict:
    config = _v13_config()
    config["method_version"] = "v14"
    config["stint_curve_models"][
        "execution_scope"
    ] = "valid_stints_only"
    return config


def _v15_config() -> dict:
    config = _v14_config()
    config["method_version"] = "v15"
    config["inclusive_robust_model"] = {
        "enabled": True,
        "model": "crossfit_huber_additive_v1",
        "crossfit_folds": 2,
        "ridge_penalty": 0.5,
        "huber_delta": 1.5,
        "max_iterations": 12,
        "convergence_tolerance": 1e-6,
        "residual_scale_floor_s": 0.05,
        "hard_minimum_distance_coverage": 0.50,
        "hard_minimum_samples_per_lap": 10,
        "recommended_distance_coverage": 0.95,
        "recommended_samples_per_lap": 20,
        "minimum_analysis_weight": 0.001,
        "minimum_robust_weight": 0.02,
        "minimum_candidate_laps": 40,
        "minimum_holdout_laps": 30,
        "minimum_teams": 2,
        "minimum_drivers": 4,
        "minimum_oof_mae_improvement_s": 0.01,
        "maximum_oof_mae_worsening_s": 0.0,
        "maximum_oof_mae_s": 5.0,
        "maximum_abs_nuisance_adjustment_s": 60.0,
        "maximum_saturated_adjustment_fraction": 0.02,
        "measurement_weights": {
            "inaccurate": 0.65,
            "deleted": 0.50,
            "non_green": 0.45,
            "pit_boundary": 0.25,
            "unknown_gap": 0.70,
            "dirty_air": 0.90,
            "intermediate_gap": 0.95,
            "non_dry_compound": 0.80,
        },
        "cleaning_residual_source": "out_of_fold_only",
        "claim": "crossfit_soft_weight_conditional_proxy_not_causal_truth",
        "strict_confirmation_layer": True,
    }
    config["stint_gate"].update(
        {
            "minimum_effective_laps": 3.0,
            "minimum_effective_fraction": 0.30,
            "minimum_candidate_fraction": 0.50,
            "minimum_effective_baseline_laps_for_cumulative": 2.0,
            "strict_confirmation_minimum_modelable_laps": 4,
            "strict_confirmation_minimum_modelable_fraction": 0.50,
        }
    )
    config["baseline"]["minimum_effective_laps_per_cell"] = 2.0
    config["stint_curve_models"][
        "execution_scope"
    ] = "strict_confirmed_stints_only"
    config["publication_contract"].update(
        {
            "inclusive_model_is_causal_truth": False,
            "iterative_cleaning_uses_in_sample_residuals": False,
        }
    )
    return config


def _v16_config() -> dict:
    config = _v15_config()
    config["method_version"] = "v16"
    config["stint_reference_pace"] = {
        "enabled": True,
        "model": "weighted_theil_sen_within_stint_reference_v1",
        "reference_tyre_age": "within_stint_weighted_median",
        "allow_reference_age_extrapolation": False,
        "maximum_direct_comparison_tyre_age_gap_laps": 2.0,
        "fuel_sensitivity": (
            "low_base_high_scenarios_with_non_fuel_nuisance_held_fixed"
        ),
        "claim": (
            "fuel_and_observed_condition_adjusted_tyre_age_"
            "conditional_proxy"
        ),
    }
    config["boundaries"] = {
        "representative_stint_pace_is_an_observed_lap": False,
        "representative_stint_pace_removes_tyre_age_effect": False,
    }
    return config


def _v17_config() -> dict:
    config = _v16_config()
    config["method_version"] = "v17"
    config["stint_curve_evidence"] = {
        "enabled": True,
        "schema_version": "race-dossier-stint-curve-evidence-v17",
        "primary_fit": (
            "oof_reliability_weighted_theil_sen_linear_projection_v1"
        ),
        "minimum_fit_points": 6,
        "prediction_grid_points": 17,
        "observed_range_probability": 0.8,
        "delete_block_laps": 3,
        "minimum_delete_block_refits": 5,
        "stability_interval_probability": 0.8,
        "minimum_kish_effective_laps_for_range": 8.0,
        "comparison_gate": {
            "maximum_reference_tyre_age_gap_laps": 2.0,
            "maximum_lap_fraction_difference": 0.2,
            "maximum_traffic_total_variation_distance": 0.35,
            "maximum_non_green_fraction_difference": 0.2,
            "maximum_pit_boundary_fraction_difference": 0.2,
            "maximum_low_quality_fraction_difference": 0.2,
            "nuisance_scale_floor_s": 0.1,
            "minimum_profile_coverage": 0.8,
            "minimum_common_support_span_laps": 5.0,
            "minimum_support_overlap_ratio": 0.5,
            "minimum_kish_effective_laps": 8.0,
            "maximum_direct_distance": 0.65,
            "maximum_warning_distance": 0.9,
        },
    }
    config["boundaries"].update(
        {
            "stability_interval_is_confidence_interval": False,
            "observed_range_is_prediction_interval": False,
            "fuel_scenario_is_statistical_interval": False,
            "browser_refits_stint_models": False,
        }
    )
    return config


def _race_laps(laps: int = 12) -> pd.DataFrame:
    rows: list[dict] = []
    drivers = [
        ("A1", "Team A", 0.18),
        ("A2", "Team A", 0.28),
        ("B1", "Team B", -0.18),
        ("B2", "Team B", -0.08),
    ]
    for driver, team, team_delta in drivers:
        for lap in range(1, laps + 1):
            stint = 1 if lap <= laps // 2 else 2
            stint_lap = lap if stint == 1 else lap - laps // 2
            gap_s = 1.5 if driver == "A2" and lap in {5, 6} else 5.0
            rows.append(
                {
                    "year": 2025,
                    "meeting_name": "Synthetic Grand Prix",
                    "driver_id": driver,
                    "team_name": team,
                    "lap_number": lap,
                    "lap_duration": 90.0
                    + team_delta
                    + 0.06 * stint_lap
                    + (0.8 if gap_s < 2.5 else 0.0),
                    "compound": "MEDIUM" if stint == 1 else "HARD",
                    "stint_number": stint,
                    "tyre_age": stint_lap,
                    "position": 1 if team == "Team B" else 3,
                    "driver_ahead_number": None if driver == "B1" else "99",
                    "is_accurate": True,
                    "deleted": False,
                    "all_green": lap != 8,
                    "track_status_codes": "1" if lap != 8 else "4",
                    "distance_metres": 5000.0,
                    "event_distance_reference_metres": 5000.0,
                    "distance_coverage_ratio": 1.0,
                    "median_speed_kph": 200.0,
                    "median_gap_to_ahead_m": gap_s * (200.0 / 3.6),
                    "telemetry_samples": 100,
                }
            )
    return pd.DataFrame(rows)


def _qualifying() -> pd.DataFrame:
    rows = []
    for driver, team, seconds in (
        ("A1", "Team A", 79.8),
        ("A2", "Team A", 80.0),
        ("B1", "Team B", 80.2),
        ("B2", "Team B", 80.3),
    ):
        rows.append(
            {
                "year": 2025,
                "meeting_name": "Synthetic Grand Prix",
                "driver_id": driver,
                "team_name": team,
                "lap_duration": seconds,
                "push_rank": 1,
            }
        )
    return pd.DataFrame(rows)


def test_config_structurally_keeps_pac_ovr_null_and_sessions_separate():
    validate_race_dossier_config(_config())
    bad = _config()
    bad["publication_contract"]["publish_pac"] = True
    with pytest.raises(ValueError, match="PAC"):
        validate_race_dossier_config(bad)
    bad = _config()
    bad["publication_contract"]["qualifying_race_independent"] = False
    with pytest.raises(ValueError, match="排位"):
        validate_race_dossier_config(bad)


def test_v17_curve_evidence_protocol_rejects_interval_and_balance_drift():
    config = _v17_config()
    validate_race_dossier_config(config)

    bad = _v17_config()
    bad["stint_curve_evidence"]["delete_block_laps"] = 2
    with pytest.raises(ValueError, match="连续三圈"):
        validate_race_dossier_config(bad)

    bad = _v17_config()
    bad["stint_curve_evidence"]["comparison_gate"][
        "maximum_traffic_total_variation_distance"
    ] = 0.5
    with pytest.raises(ValueError, match="可比条件门"):
        validate_race_dossier_config(bad)

    bad = _v17_config()
    bad["boundaries"]["observed_range_is_prediction_interval"] = True
    with pytest.raises(ValueError, match="observed_range"):
        validate_race_dossier_config(bad)

    bad = _v17_config()
    bad["stint_curve_evidence"]["observed_range_probability"] = 0.75
    with pytest.raises(ValueError, match="观测经验范围"):
        validate_race_dossier_config(bad)

    bad = _v17_config()
    bad["stint_curve_evidence"]["stability_interval_probability"] = 0.90
    with pytest.raises(ValueError, match="稳定性范围"):
        validate_race_dossier_config(bad)


def test_missing_gap_with_known_car_ahead_is_not_clean_air():
    laps = _race_laps()
    mask = (laps.driver_id == "A1") & (laps.lap_number == 3)
    laps.loc[mask, "median_gap_to_ahead_m"] = np.nan
    annotated = annotate_race_laps(laps, _config())
    row = annotated.loc[mask].iloc[0]
    assert row.traffic_state == "unknown_gap"
    assert not row.modelable_lap


def test_few_fast_laps_cannot_publish_a_complete_race_conclusion():
    report = build_race_dossier(
        _race_laps(laps=3),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    assert not report["publication_gate"]["passed"]
    assert report["race_analysis"]["published_vehicle_ranking"] is None
    assert report["event_conclusion"] is None
    assert report["ratings"]["PAC"] is None
    assert report["ratings"]["OVR"] is None


def test_ledger_marks_both_pit_edges_and_separates_non_green_and_traffic():
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    counts = report["event_ledger"]["lap_category_counts"]
    assert counts["pit_in_boundary_proxy"] > 0
    assert counts["pit_out_boundary_proxy"] > 0
    assert counts["non_green"] > 0
    assert counts["traffic_dirty_air"] > 0
    assert "accident_or_damage" in report["event_ledger"]["not_identified_categories"]
    assert "penalty" in report["event_ledger"]["not_identified_categories"]


def test_complete_stints_report_physical_shape_and_signed_cumulative_delta():
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    valid = [row for row in report["stint_dossiers"] if row["status"] == "valid"]
    assert valid
    row = valid[0]
    assert row["initial_pace_s"] is not None
    assert row["stable_pace_s"] is not None
    assert row["variability_mad_s"] is not None
    assert row["degradation_s_per_tyre_lap"] is not None
    assert "cumulative_delta_to_reasonable_baseline_s" in row
    assert row["major_factor_claimed"] is False
    assert "contribution_percent" not in row


def test_stint_primary_pace_is_one_fuel_adjusted_tyre_age_projection():
    rows = []
    for stint_number, base_pace, lap_offset in (
        (1, 90.0, 0),
        (2, 90.5, 8),
    ):
        for tyre_age in range(1, 9):
            fuel_effect = 3.0 - 0.30 * (tyre_age - 1)
            corrected_pace = base_pace + 0.10 * tyre_age
            rows.append(
                {
                    "year": 2025,
                    "meeting_name": "Synthetic GP",
                    "driver_id": "A1",
                    "team_name": "Team A",
                    "stint_number": stint_number,
                    "compound": "MEDIUM",
                    "lap_number": lap_offset + tyre_age,
                    "tyre_age": float(tyre_age),
                    "lap_duration": corrected_pace + fuel_effect,
                    "pace_adjusted_lap_duration_s": corrected_pace,
                    "pace_eligible_lap": True,
                    "modelable_lap": True,
                    "strict_pace_eligible_lap": True,
                    "baseline_available": False,
                    "gap_seconds_proxy": 5.0,
                    "primary_disposition": "model_eligible",
                    "fuel_corrected_low_lap_duration_s": corrected_pace
                    + 0.20,
                    "fuel_corrected_high_lap_duration_s": corrected_pace
                    - 0.20,
                    "track_evolution_proxy_s": 0.0,
                    "traffic_penalty_proxy_s": 0.0,
                }
            )

    dossiers = build_stint_dossiers(pd.DataFrame(rows), _config())

    assert len(dossiers) == 2
    assert [row["representative_tyre_age_laps"] for row in dossiers] == [
        4.0,
        4.0,
    ]
    assert [
        row["representative_tyre_age_pace_s"] for row in dossiers
    ] == pytest.approx([90.4, 90.9])
    assert all(
        row["representative_tyre_age_pace_status"]
        == "identified_conditional_proxy"
        for row in dossiers
    )
    assert all(
        row["degradation_s_per_tyre_lap"] == pytest.approx(0.10)
        for row in dossiers
    )
    assert all(
        row["raw_net_slope_s_per_tyre_lap"] == pytest.approx(-0.20)
        for row in dossiers
    )
    assert dossiers[0][
        "representative_tyre_age_pace_fuel_sensitivity_interval_s"
    ] == pytest.approx([90.2, 90.6])
    assert (
        dossiers[1]["representative_tyre_age_pace_s"]
        - dossiers[0]["representative_tyre_age_pace_s"]
    ) == pytest.approx(0.5)


def test_v17_sidecar_publishes_points_equation_ranges_and_named_scenarios():
    report, sidecar = build_race_dossier(
        _race_laps(laps=24),
        _qualifying(),
        _v17_config(),
        classification_proxy=None,
        return_curve_sidecar=True,
    )

    assert report["schema_version"] == "race-dossier-v17"
    assert report["stint_comparison_contract"][
        "maximum_direct_condition_distance"
    ] == pytest.approx(0.65)
    assert sidecar is not None
    assert sidecar["schema_version"] == (
        "race-dossier-stint-curve-evidence-v17"
    )
    assert sidecar["report_id"] == report["report_id"]
    assert sidecar["coverage"]["points"] == len(_race_laps(laps=24))
    assert sidecar["coverage"]["stints"] == len(report["stint_dossiers"])
    report_by_key = {
        (
            row["team"],
            row["driver"],
            row["stint_number"],
            row["compound"],
        ): row
        for row in report["stint_dossiers"]
    }
    for evidence in sidecar["stints"]:
        dossier = report_by_key[
            (
                evidence["team"],
                evidence["driver"],
                evidence["stint_number"],
                evidence["compound"],
            )
        ]
        fit = evidence["primary_fit"]
        equation = fit["equation"]
        assert fit["status"] == "IDENTIFIED"
        assert equation["reference_pace_s"] == pytest.approx(
            dossier["representative_tyre_age_pace_s"]
        )
        assert equation["slope_s_per_tyre_lap"] == pytest.approx(
            dossier["degradation_s_per_tyre_lap"]
        )
        anchor = equation["reference_tyre_age_laps"]
        anchor_row = min(
            fit["prediction_grid"],
            key=lambda row: abs(row["tyre_age_laps"] - anchor),
        )
        expected = (
            equation["reference_pace_s"]
            + equation["slope_s_per_tyre_lap"]
            * (anchor_row["tyre_age_laps"] - anchor)
        )
        assert anchor_row["pace_s"] == pytest.approx(expected)
        assert set(fit["fuel_scenarios"]) == {"low", "base", "high"}
        assert fit["confidence_interval"]["status"] == "NOT_TESTED"
        assert fit["new_lap_prediction_interval"]["status"] == "NOT_TESTED"
        assert len(evidence["points"]) == dossier["observed_laps"]
        assert sum(
            point["analysis_weight"]
            for point in evidence["points"]
            if point["used_for_primary_fit"]
        ) == pytest.approx(dossier["effective_weight_laps"])
        assert all(
            {
                "raw_lap_duration_s",
                "adjusted_pace_s",
                "observed_context_flags",
                "inclusive_hard_exclusion_reasons",
            }
            <= set(point)
            for point in evidence["points"]
        )
    comparable_pairs = [
        row
        for row in sidecar["pairwise_comparisons"]
        if row["status"] == "comparable"
    ]
    assert comparable_pairs
    assert all(
        row["left_minus_right_pace_s"] is not None
        and row["direct_comparison_left_minus_right_pace_s"] is not None
        and row[
            "audit_only_conditional_model_left_minus_right_pace_s"
        ]
        is None
        for row in comparable_pairs
    )


def test_v17_stability_deletes_only_strictly_consecutive_lap_blocks():
    usable = pd.DataFrame(
        {
            "lap_number": [1, 2, 4, 5, 6, 8, 9, 10, 11, 12],
            "tyre_age": np.arange(1.0, 11.0),
            "pace": 90.0 + np.arange(1.0, 11.0) * 0.05,
            "_analysis_weight": np.ones(10),
        }
    )
    settings = {
        "delete_block_laps": 3,
        "minimum_delete_block_refits": 1,
        "stability_interval_probability": 0.8,
        "prediction_grid_points": 9,
        "minimum_kish_effective_laps_for_range": 8.0,
        "minimum_fit_points": 6,
    }

    result = _delete_block_fit_sensitivity(
        usable,
        pace_column="pace",
        reference_age=5.5,
        support=[1.0, 10.0],
        settings=settings,
    )

    assert result["status"] == "ESTIMATED"
    assert result["candidate_contiguous_blocks"] == 4
    assert result["skipped_non_contiguous_blocks"] == 4
    assert result["refits"] == 4


def test_v17_pair_gate_recomputes_kish_ess_inside_common_support():
    laps = _race_laps(laps=24)
    shifted = laps["team_name"].eq("Team B") & laps["stint_number"].eq(1)
    laps.loc[shifted, "tyre_age"] = laps.loc[shifted, "tyre_age"] + 6
    _, sidecar = build_race_dossier(
        laps,
        _qualifying(),
        _v17_config(),
        classification_proxy=None,
        return_curve_sidecar=True,
    )
    assert sidecar is not None
    evidence = {
        (row["team"], row["driver"], row["stint_number"]): row
        for row in sidecar["stints"]
    }
    left = evidence[("Team A", "A1", 1)]
    right = evidence[("Team B", "B1", 1)]
    assert left["sample_audit"]["kish_effective_laps"] >= 8.0
    assert right["sample_audit"]["kish_effective_laps"] >= 8.0
    pair_key = "~".join(sorted([left["stint_key"], right["stint_key"]]))
    pair = next(
        row
        for row in sidecar["pairwise_comparisons"]
        if row["pair_key"] == pair_key
    )
    common = pair["common_support_sample_audit"]
    assert common["left"]["kish_effective_laps"] < 8.0
    assert common["right"]["kish_effective_laps"] < 8.0
    assert "left_kish_effective_laps_below_range_gate" in pair["gate_failures"]
    assert "right_kish_effective_laps_below_range_gate" in pair["gate_failures"]


def test_v17_confirmed_shape_grid_stays_inside_strict_refit_support():
    strict = pd.DataFrame(
        {
            "tyre_age": np.arange(4.0, 11.0),
            "pace": 90.0 + np.arange(4.0, 11.0) * 0.03,
        }
    )
    result = _descriptive_confirmed_curve_refit(
        strict,
        pace_column="pace",
        curve_audit={
            "status": "validated_confirmatory_proxy",
            "selected_model": "linear_theil_sen",
        },
        support=[1.0, 12.0],
        curve_settings={"maximum_prediction_deviation_s": 5.0},
        evidence_settings={
            "minimum_fit_points": 6,
            "prediction_grid_points": 9,
        },
    )

    assert result["status"] == "AVAILABLE"
    assert result["support_tyre_age_laps"] == [4.0, 10.0]
    assert min(
        row["tyre_age_laps"] for row in result["prediction_grid"]
    ) == pytest.approx(4.0)
    assert max(
        row["tyre_age_laps"] for row in result["prediction_grid"]
    ) == pytest.approx(10.0)


def test_v17_condition_balance_uses_worse_raw_nuisance_difference():
    config = _v17_config()["stint_curve_evidence"]["comparison_gate"]
    common = {
        "context_coverage": 1.0,
        "lap_fraction_median": {"raw": 0.5, "weighted": 0.5},
        "traffic_state_distribution": {
            "raw": {"clean_air": 1.0},
            "weighted": {"clean_air": 1.0},
        },
        "non_green_fraction": {"raw": 0.0, "weighted": 0.0},
        "pit_boundary_fraction": {"raw": 0.0, "weighted": 0.0},
        "low_quality_fraction": {"raw": 0.0, "weighted": 0.0},
    }
    left = {
        **common,
        "nuisance_adjustment_median_s": {
            "raw": 0.0,
            "weighted": 0.0,
            "same_event_compound_robust_scale_s": 0.1,
        },
    }
    right = {
        **common,
        "nuisance_adjustment_median_s": {
            "raw": 0.2,
            "weighted": 0.0,
            "same_event_compound_robust_scale_s": 0.1,
        },
    }

    result = _condition_balance(left, right, config)

    assert result["status"] == "not_comparable"
    nuisance = result["components"]["nuisance_adjustment"]
    assert nuisance["absolute_difference"] == pytest.approx(0.2)
    assert nuisance["normalized_distance"] == pytest.approx(2.0)


def test_v17_traffic_separation_blocks_direct_comparison():
    laps = _race_laps(laps=24)
    team_b = laps["team_name"].eq("Team B")
    laps.loc[team_b, "driver_ahead_number"] = "99"
    laps.loc[team_b, "median_gap_to_ahead_m"] = 1.2 * (200.0 / 3.6)
    report, sidecar = build_race_dossier(
        laps,
        _qualifying(),
        _v17_config(),
        classification_proxy=None,
        return_curve_sidecar=True,
    )
    assert report["ratings"] == {"PAC": None, "OVR": None}
    assert sidecar is not None
    evidence = {
        (
            row["team"],
            row["driver"],
            row["stint_number"],
        ): row
        for row in sidecar["stints"]
    }
    left = evidence[("Team A", "A1", 1)]
    right = evidence[("Team B", "B1", 1)]
    pair_key = "~".join(sorted([left["stint_key"], right["stint_key"]]))
    pair = next(
        row
        for row in sidecar["pairwise_comparisons"]
        if row["pair_key"] == pair_key
    )
    assert pair["status"] == "not_comparable"
    assert pair["comparison_tyre_age_laps"] is not None
    assert pair["left_anchor"]["point_estimate_s"] is not None
    assert pair["right_anchor"]["point_estimate_s"] is not None
    assert pair["left_minus_right_pace_s"] is None
    assert pair["direct_comparison_left_minus_right_pace_s"] is None
    assert pair[
        "audit_only_conditional_model_left_minus_right_pace_s"
    ] == pytest.approx(
        pair["left_anchor"]["point_estimate_s"]
        - pair["right_anchor"]["point_estimate_s"]
    )
    assert (
        pair["left_anchor"]["roles"]["weighted_empirical_central_80_s"]
        == (
            "frozen_fit_at_anchor_plus_weighted_residual_central_range_"
            "not_prediction_interval"
        )
    )
    assert pair["observable_condition_balance"]["status"] == "not_comparable"
    traffic = pair["observable_condition_balance"]["components"][
        "traffic_tvd"
    ]
    assert traffic["absolute_difference"] > 0.35
    assert "observable_condition_balance_failed" in pair["gate_failures"]


def test_sparse_cross_team_baseline_does_not_invalidate_intrinsic_stint_shape():
    config = _config()
    config["baseline"]["minimum_teams_per_cell"] = 99
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        config,
        classification_proxy=None,
    )
    valid = [row for row in report["stint_dossiers"] if row["status"] == "valid"]
    assert valid
    for row in valid:
        assert row["initial_pace_s"] is not None
        assert row["cumulative_delta_to_reasonable_baseline_s"] is None
        assert row["cumulative_baseline_status"] == "not_identifiable"
        assert row["cumulative_baseline_gate_failures"]


def test_qualifying_and_race_rankings_are_independent_views():
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    assert report["qualifying_analysis"]["team_order"][0]["team"] == "Team A"
    assert report["race_analysis"]["descriptive_team_order"][0]["team"] == "Team B"
    assert report["qualifying_analysis"]["absolute_time_merged_with_race"] is False


def test_tyre_windows_are_observed_data_proxies_not_internal_simulation():
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    envelope = report["tyre_strategy_envelope"]
    assert envelope["label"] == "observed_data_strategy_proxy"
    assert envelope["real_fuel_load_identified"] is False
    assert envelope["pirelli_internal_simulation"] is False
    assert envelope["compounds"]


def test_decomposition_closes_in_seconds_without_pseudo_precise_percentages():
    report = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy=None,
    )
    rows = report["vehicle_driver_decomposition"]
    assert rows
    for row in rows:
        assert row["closure_error_s"] == pytest.approx(0.0, abs=1e-9)
        assert set(row["components"]) == {
            "vehicle_common_baseline_s",
            "driver_realization_deviation_s",
            "strategy_track_position_proxy_s",
            "unexplained_residual_s",
        }
        assert "percent" not in str(row).lower()


def test_season_audit_keeps_actual_proxy_out_of_fit_and_points_null():
    first = build_race_dossier(
        _race_laps(),
        _qualifying(),
        _config(),
        classification_proxy={"Team A": 2, "Team B": 1},
    )
    second = build_race_dossier(
        _race_laps().assign(meeting_name="Synthetic Two"),
        _qualifying().assign(meeting_name="Synthetic Two"),
        _config(),
        classification_proxy={"Team A": 1, "Team B": 2},
    )
    audit = build_season_audit([first, second], _config())
    assert audit["actual_result_source"] == "classification_proxy_not_fia_final"
    assert audit["actual_results_used_for_fit"] is False
    assert audit["expected_points_interval"] is None
    assert audit["PAC"] is None
    assert audit["OVR"] is None


def test_pre_race_tyre_envelope_hard_filters_future_events():
    history = []
    for event_order in range(1, 7):
        for driver in ("A1", "A2"):
            history.append(
                {
                    "event_order": event_order,
                    "year": 2025,
                    "meeting_name": f"Event {event_order}",
                    "driver": driver,
                    "status": "valid",
                    "compound": "MEDIUM",
                    "observed_laps": 20 + event_order,
                    "degradation_s_per_tyre_lap": 0.05,
                }
            )
            history.append(
                {
                    "event_order": event_order,
                    "year": 2025,
                    "meeting_name": f"Event {event_order}",
                    "driver": driver,
                    "status": "valid",
                    "compound": "HARD",
                    "observed_laps": 30 + event_order,
                    "degradation_s_per_tyre_lap": 0.03,
                }
            )
    config = _config()
    config["strategy_envelope"]["minimum_historical_stints_per_compound"] = 4
    config["strategy_envelope"]["minimum_historical_events_per_compound"] = 2
    baseline = build_pre_race_tyre_envelope(
        history,
        target_event_order=5,
        target_race_laps=50,
        target_event_id="2025|Target",
        config=config,
    )
    future_changed = [
        {
            **row,
            "observed_laps": 200,
            "degradation_s_per_tyre_lap": 9.0,
        }
        if row["event_order"] >= 5
        else row
        for row in history
    ]
    repeated = build_pre_race_tyre_envelope(
        future_changed,
        target_event_order=5,
        target_race_laps=50,
        target_event_id="2025|Target",
        config=config,
    )
    assert repeated == baseline
    assert baseline["future_results_used"] is False


def test_pre_race_tyre_envelope_rejects_result_labels():
    config = _config()
    with pytest.raises(ValueError, match="赛果字段"):
        build_pre_race_tyre_envelope(
            [
                {
                    "event_order": 1,
                    "status": "valid",
                    "compound": "HARD",
                    "observed_laps": 30,
                    "classification_proxy": 1,
                }
            ],
            target_event_order=2,
            target_race_laps=50,
            target_event_id="2025|Target",
            config=config,
        )


def test_v11_linear_fuel_proxy_recovers_constant_underlying_pace():
    laps = _race_laps(laps=20)
    config = _v11_config()
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    race_fraction = laps["lap_number"] / 20.0
    fuel_effect = 100.0 * (1.0 - race_fraction) * 0.03
    laps["lap_duration"] = 90.0 + fuel_effect

    annotated = annotate_race_laps(laps, config)
    driver = annotated.loc[
        annotated["driver_id"].eq("B1") & annotated["modelable_lap"]
    ]
    raw_slope = np.polyfit(driver["lap_number"], driver["lap_duration"], 1)[0]
    corrected_slope = np.polyfit(
        driver["lap_number"], driver["fuel_corrected_lap_duration_s"], 1
    )[0]

    assert raw_slope < -0.10
    assert corrected_slope == pytest.approx(0.0, abs=1e-9)
    assert annotated["fuel_load_identified"].eq(False).all()
    assert (
        annotated["fuel_correction_low_s"]
        <= annotated["fuel_correction_base_s"]
    ).all()
    assert (
        annotated["fuel_correction_base_s"]
        <= annotated["fuel_correction_high_s"]
    ).all()


def test_v11_empirical_traffic_penalty_recovers_known_gap_band_effect():
    laps = _race_laps(laps=20)
    config = _v11_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    dirty = laps["lap_number"].mod(2).eq(0)
    laps["median_gap_to_ahead_m"] = np.where(
        dirty,
        1.5 * (200.0 / 3.6),
        5.0 * (200.0 / 3.6),
    )
    laps["lap_duration"] = 90.0 + np.where(dirty, 0.8, 0.0)
    laps["all_green"] = True
    laps["track_status_codes"] = "1"

    annotated = annotate_race_laps(laps, config)
    estimated = annotated.loc[
        annotated["traffic_state"].eq("dirty_air"),
        "traffic_penalty_proxy_s",
    ].dropna()
    clean_adjusted = annotated.loc[
        annotated["traffic_state"].eq("clean_air"),
        "pace_adjusted_lap_duration_s",
    ]
    dirty_adjusted = annotated.loc[
        annotated["traffic_state"].eq("dirty_air"),
        "pace_adjusted_lap_duration_s",
    ]

    assert not estimated.empty
    assert float(estimated.median()) == pytest.approx(0.8, abs=0.05)
    assert float(dirty_adjusted.median() - clean_adjusted.median()) == pytest.approx(
        0.0, abs=0.05
    )
    eligible_dirty = annotated.loc[
        annotated["traffic_state"].eq("dirty_air")
        & ~annotated["pit_boundary_proxy"],
        "pace_eligible_lap",
    ]
    assert eligible_dirty.all()
    assert not annotated.loc[
        annotated["traffic_state"].eq("dirty_air")
        & annotated["pit_boundary_proxy"],
        "pace_eligible_lap",
    ].any()


def test_v11_track_evolution_proxy_removes_known_field_common_shift():
    laps = _race_laps(laps=20)
    config = _v11_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["tyre_age"] = 1.0
    laps["compound"] = "HARD"
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0 + 1.2 * (laps["lap_number"] / 20.0)

    annotated = annotate_race_laps(laps, config)
    driver = annotated.loc[annotated["driver_id"].eq("B1")].sort_values(
        "lap_number"
    )
    raw_shift = float(
        driver.tail(4)["lap_duration"].median()
        - driver.head(4)["lap_duration"].median()
    )
    adjusted_shift = float(
        driver.tail(4)["pace_adjusted_lap_duration_s"].median()
        - driver.head(4)["pace_adjusted_lap_duration_s"].median()
    )

    assert raw_shift > 0.8
    assert abs(adjusted_shift) < 0.20
    assert driver["track_evolution_available"].all()


def test_v11_one_weak_team_does_not_block_other_team_modules():
    base = _race_laps()
    team_c = base.loc[base["team_name"].eq("Team B")].copy()
    team_c["team_name"] = "Team C"
    team_c["driver_id"] = team_c["driver_id"].map({"B1": "C1", "B2": "C2"})
    team_c["lap_duration"] = team_c["lap_duration"] + 0.12
    laps = pd.concat([base, team_c], ignore_index=True)
    weak = laps["team_name"].eq("Team A")
    laps.loc[weak, "median_gap_to_ahead_m"] = np.nan
    laps.loc[weak, "driver_ahead_number"] = "99"

    qualifying = pd.concat(
        [
            _qualifying(),
            pd.DataFrame(
                [
                    {
                        "year": 2025,
                        "meeting_name": "Synthetic Grand Prix",
                        "driver_id": "C1",
                        "team_name": "Team C",
                        "lap_duration": 80.4,
                        "push_rank": 1,
                    },
                    {
                        "year": 2025,
                        "meeting_name": "Synthetic Grand Prix",
                        "driver_id": "C2",
                        "team_name": "Team C",
                        "lap_duration": 80.5,
                        "push_rank": 1,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    config = _v11_config()
    config["scope"]["reporting_teams"] = ["Team A", "Team B", "Team C"]
    config["coverage_gate"]["minimum_reporting_teams"] = 3

    report = build_race_dossier(
        laps,
        qualifying,
        config,
        classification_proxy=None,
    )
    team_gates = {
        row["team"]: row
        for row in report["publication_gate"]["module_gates"]["teams"]
    }

    assert not report["publication_gate"]["passed"]
    assert not team_gates["Team A"]["passed"]
    assert team_gates["Team B"]["passed"]
    assert team_gates["Team C"]["passed"]
    assert report["publication_gate"]["partial_team_conclusion_allowed"]
    assert {
        row["team"] for row in report["race_analysis"]["published_vehicle_ranking"]
    } == {"Team B", "Team C"}
    assert report["race_analysis"]["ranking_scope_complete"] is False
    assert report["event_conclusion"]["scope"] == "partial_team_modules"


def test_v12_track_proxy_requires_cross_driver_holdout_improvement():
    laps = _race_laps(laps=20)
    config = _v12_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    track = config["simulation_proxies"]["track_evolution"]
    track.update(
        {
            "minimum_laps_per_bin": 4,
            "minimum_teams_per_bin": 1,
            "minimum_drivers_per_bin": 2,
            "require_holdout_non_worsening": True,
            "minimum_holdout_laps": 20,
            "minimum_holdout_mae_improvement_s": 0.05,
            "maximum_holdout_mae_worsening_s": 0.0,
        }
    )
    laps["tyre_age"] = 1.0
    laps["compound"] = "HARD"
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0 + 1.2 * (laps["lap_number"] / 20.0)

    annotated = annotate_race_laps(laps, config)

    assert annotated["track_validation_status"].eq(
        "accepted_non_worsening"
    ).all()
    assert annotated["track_evolution_available"].all()
    assert float(
        annotated["track_validation_mae_after_s"].median()
    ) < float(annotated["track_validation_mae_before_s"].median())


def test_v12_track_proxy_rejects_driver_specific_opposite_trends():
    laps = _race_laps(laps=20)
    config = _v12_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    track = config["simulation_proxies"]["track_evolution"]
    track.update(
        {
            "minimum_laps_per_bin": 4,
            "minimum_teams_per_bin": 1,
            "minimum_drivers_per_bin": 2,
            "require_holdout_non_worsening": True,
            "minimum_holdout_laps": 20,
            "minimum_holdout_mae_improvement_s": 0.05,
            "maximum_holdout_mae_worsening_s": 0.0,
        }
    )
    laps["tyre_age"] = 1.0
    laps["compound"] = "HARD"
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    direction = laps["driver_id"].map(
        {"A1": 1.0, "A2": -1.0, "B1": -1.0, "B2": 1.0}
    )
    laps["lap_duration"] = (
        90.0 + direction * 1.2 * (laps["lap_number"] / 20.0)
    )

    annotated = annotate_race_laps(laps, config)

    assert annotated["track_validation_status"].eq(
        "rejected_or_insufficient_holdout"
    ).all()
    assert not annotated["track_evolution_available"].any()
    assert annotated["track_evolution_proxy_s"].eq(0.0).all()


def test_v12_stint_curve_tournament_recovers_predictive_degradation():
    laps = _race_laps(laps=20)
    config = _v12_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0 + 0.10 * laps["tyre_age"]

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )
    validated = [
        row["tyre_curve_model_audit"]
        for row in report["stint_dossiers"]
        if row["status"] == "valid"
        and row["tyre_curve_model_audit"]["status"]
        == "validated_predictive_proxy"
    ]

    assert validated
    for audit in validated:
        assert audit["selected_model"] != "constant_median"
        assert (
            audit["rolling_origin_mae_s"]["selected"]
            < audit["rolling_origin_mae_s"]["constant_median"]
        )
        assert audit["strategy_curve_eligible"]
        assert set(audit["candidate_mae_s"]) == set(
            config["stint_curve_models"]["candidates"]
        )
    assert any(
        row["status"] == "usable_length_and_curve_proxy"
        and row["degradation_proxy_s_per_lap"] is not None
        for row in report["tyre_strategy_envelope"]["compounds"]
    )


def test_v12_stint_curve_tournament_rejects_flat_non_improving_signal():
    laps = _race_laps(laps=20)
    config = _v12_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )
    audits = [
        row["tyre_curve_model_audit"]
        for row in report["stint_dossiers"]
        if row["status"] == "valid"
    ]

    assert audits
    assert all(
        row["status"]
        in {
            "audit_only_no_predictive_gain",
            "audit_only_insufficient_validation_laps",
        }
        for row in audits
    )
    assert not any(
        row["status"] == "validated_predictive_proxy" for row in audits
    )
    assert not any(row["strategy_curve_eligible"] for row in audits)
    assert all(
        row["degradation_proxy_s_per_lap"] is None
        for row in report["tyre_strategy_envelope"]["compounds"]
        if row["status"] == "usable_length_only"
    )


def test_v13_curve_selection_requires_later_ordered_confirmation():
    laps = _race_laps(laps=24)
    config = _v13_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0 + 0.10 * laps["tyre_age"]

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )
    confirmed = [
        row["tyre_curve_model_audit"]
        for row in report["stint_dossiers"]
        if row["status"] == "valid"
        and row["tyre_curve_model_audit"]["status"]
        == "validated_confirmatory_proxy"
    ]

    assert confirmed
    for audit in confirmed:
        assert audit["validation_protocol"] == (
            "ordered_selection_confirmation_v1"
        )
        assert audit["selection_audit"]["points"] >= 2
        assert audit["confirmation_audit"]["points"] >= 2
        assert audit["confirmation_audit"]["improvement_s"] >= 0.01


def test_v13_curve_selection_rejects_early_trend_that_reverses_late():
    laps = _race_laps(laps=24)
    config = _v13_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    age = laps["tyre_age"]
    laps["lap_duration"] = 90.0 + np.where(
        age <= 7,
        0.15 * age,
        1.05 - 0.20 * (age - 7),
    )

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )
    audits = [
        row["tyre_curve_model_audit"]
        for row in report["stint_dossiers"]
        if row["status"] == "valid"
    ]

    assert audits
    assert not any(
        row["status"] == "validated_confirmatory_proxy"
        and row["strategy_curve_eligible"]
        for row in audits
    )


def test_v13_pit_cycle_proxy_recovers_staggered_local_time_loss():
    laps = _race_laps(laps=20)
    config = _v13_config()
    config["simulation_proxies"]["fuel"]["enabled"] = False
    config["simulation_proxies"]["track_evolution"]["enabled"] = False
    config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    stop_laps = {"A1": 8, "A2": 9, "B1": 10, "B2": 11}
    for driver, stop_lap in stop_laps.items():
        driver_mask = laps["driver_id"].eq(driver)
        second = driver_mask & laps["lap_number"].gt(stop_lap)
        laps.loc[driver_mask, "stint_number"] = 1
        laps.loc[second, "stint_number"] = 2
        laps.loc[driver_mask, "compound"] = "MEDIUM"
        laps.loc[second, "compound"] = "HARD"
        laps.loc[driver_mask, "tyre_age"] = laps.loc[
            driver_mask, "lap_number"
        ]
        laps.loc[second, "tyre_age"] = (
            laps.loc[second, "lap_number"] - stop_lap
        )
        boundary = driver_mask & laps["lap_number"].isin(
            [stop_lap, stop_lap + 1]
        )
        laps.loc[
            boundary & laps["lap_number"].eq(stop_lap), "lap_duration"
        ] += 12.0
        laps.loc[
            boundary & laps["lap_number"].eq(stop_lap + 1), "lap_duration"
        ] += 8.0

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )
    pit = report["pit_cycle_loss_proxy"]

    assert pit["status"] == "usable_historical_proxy"
    assert pit["valid_cycles"] == 4
    assert pit["cycle_loss_s"]["median"] == pytest.approx(20.0, abs=0.40)
    assert pit["stationary_pit_time_identified"] is False


def test_v13_pre_race_strategy_cost_adds_degradation_and_pit_components_only():
    config = _v13_config()
    config["strategy_envelope"]["minimum_historical_stints_per_compound"] = 4
    config["strategy_envelope"]["minimum_historical_events_per_compound"] = 2
    config["stint_curve_models"][
        "minimum_historical_curve_models_per_compound"
    ] = 4
    history = []
    for event_order in range(1, 5):
        for compound, length, slope in (
            ("MEDIUM", 20, 0.05),
            ("HARD", 30, 0.03),
        ):
            for driver in ("A1", "A2"):
                history.append(
                    {
                        "event_order": event_order,
                        "year": 2025,
                        "meeting_name": f"Event {event_order}",
                        "driver": driver,
                        "status": "valid",
                        "compound": compound,
                        "observed_laps": length,
                        "degradation_s_per_tyre_lap": slope,
                        "tyre_curve_model_audit": {
                            "strategy_curve_eligible": True
                        },
                        "event_pit_cycle_loss_proxy_s": 20.0,
                    }
                )

    envelope = build_pre_race_tyre_envelope(
        history,
        target_event_order=5,
        target_race_laps=50,
        target_event_id="2025|Target",
        config=config,
    )
    modeled = [
        row
        for row in envelope["one_stop_options"]
        if row["identified_time_components_proxy"] is not None
    ]

    assert envelope["pit_cycle_loss_proxy"]["status"] == (
        "usable_historical_proxy"
    )
    assert modeled
    assert all(
        row["identified_time_components_proxy"][
            "strategy_total_time_rank_identified"
        ]
        is False
        for row in modeled
    )
    assert envelope["optimal_strategy"] is None


def test_v14_skips_curve_tournament_for_failed_stints_and_preserves_valid_results():
    laps = _race_laps(laps=24)
    v13 = _v13_config()
    v14 = _v14_config()
    for config in (v13, v14):
        config["simulation_proxies"]["fuel"]["enabled"] = False
        config["simulation_proxies"]["track_evolution"]["enabled"] = False
        config["simulation_proxies"]["traffic"]["enabled"] = False
    laps["all_green"] = True
    laps["track_status_codes"] = "1"
    laps["lap_duration"] = 90.0 + 0.10 * laps["tyre_age"]
    short = laps["driver_id"].eq("A1") & laps["lap_number"].gt(4)
    laps.loc[short, "is_accurate"] = False

    report_v13 = build_race_dossier(
        laps,
        _qualifying(),
        v13,
        classification_proxy=None,
    )
    report_v14 = build_race_dossier(
        laps,
        _qualifying(),
        v14,
        classification_proxy=None,
    )
    valid_v13 = {
        (
            row["driver"],
            row["stint_number"],
            row["compound"],
        ): row["tyre_curve_model_audit"]
        for row in report_v13["stint_dossiers"]
        if row["status"] == "valid"
    }
    valid_v14 = {
        (
            row["driver"],
            row["stint_number"],
            row["compound"],
        ): row["tyre_curve_model_audit"]
        for row in report_v14["stint_dossiers"]
        if row["status"] == "valid"
    }
    skipped = [
        row["tyre_curve_model_audit"]
        for row in report_v14["stint_dossiers"]
        if row["status"] != "valid"
    ]

    assert valid_v14 == valid_v13
    assert skipped
    assert all(
        row["status"] == "not_run_stint_gate_failed" for row in skipped
    )
    assert all(row["candidate_mae_s"] == {} for row in skipped)


def test_v15_uses_broad_field_then_soft_weights_outliers_without_deleting_them():
    config = _v15_config()
    laps = _race_laps(laps=18)
    for proxy in config["simulation_proxies"].values():
        proxy["enabled"] = False

    # 构造严格干净圈极少、但条件信息仍可读的全场。非绿旗的固定损失由
    # 全量条件模型学习；单个极慢圈只能被样本外残差降权，不能事后删除。
    non_green = laps["lap_number"].mod(3).eq(0)
    laps.loc[non_green, "all_green"] = False
    laps.loc[non_green, "track_status_codes"] = "4"
    laps.loc[non_green, "lap_duration"] += 4.0
    dirty = laps["driver_id"].eq("A2") & laps["lap_number"].between(4, 14)
    laps.loc[dirty, "median_gap_to_ahead_m"] = 1.5 * (200.0 / 3.6)
    laps.loc[dirty, "lap_duration"] += 1.2
    outlier = laps["driver_id"].eq("A1") & laps["lap_number"].eq(10)
    laps.loc[outlier, "is_accurate"] = False
    laps.loc[outlier, "deleted"] = True
    laps.loc[outlier, "lap_duration"] += 25.0

    annotated = annotate_race_laps(laps, config)
    strict_laps = int(annotated["strict_pace_eligible_lap"].sum())
    inclusive_laps = int(annotated["inclusive_model_candidate"].sum())
    outlier_weight = float(
        annotated.loc[outlier.to_numpy(), "inclusive_model_weight"].iloc[0]
    )
    ordinary_weight = float(
        annotated.loc[
            annotated["driver_id"].eq("A1")
            & annotated["lap_number"].eq(11),
            "inclusive_model_weight",
        ].iloc[0]
    )

    assert inclusive_laps >= len(laps) - 1
    assert inclusive_laps > strict_laps
    assert 0.0 < outlier_weight < ordinary_weight
    assert annotated.loc[
        outlier.to_numpy(), "inclusive_pace_eligible_lap"
    ].all()
    assert annotated["pace_eligible_lap"].equals(
        annotated["inclusive_pace_eligible_lap"]
    )
    assert (
        annotated["inclusive_model_validation_status"].iloc[0]
        == "accepted_crossfit_proxy"
    )

    team_pace = (
        annotated.loc[
            annotated["inclusive_pace_eligible_lap"],
            ["team_name", "inclusive_adjusted_lap_duration_s"],
        ]
        .groupby("team_name")["inclusive_adjusted_lap_duration_s"]
        .median()
    )
    assert team_pace["Team B"] < team_pace["Team A"]


def test_v15_hard_excludes_only_physically_unusable_lap_and_is_row_order_stable():
    config = _v15_config()
    laps = _race_laps(laps=18)
    for proxy in config["simulation_proxies"].values():
        proxy["enabled"] = False
    unusable = laps["driver_id"].eq("B2") & laps["lap_number"].eq(7)
    laps.loc[unusable, "lap_duration"] = 0.0
    laps.loc[unusable, "distance_coverage_ratio"] = 0.20
    laps.loc[unusable, "telemetry_samples"] = 2

    forward = annotate_race_laps(laps, config)
    reverse = annotate_race_laps(
        laps.sample(frac=1.0, random_state=91).reset_index(drop=True),
        config,
    )
    keys = ["driver_id", "lap_number"]
    columns = keys + [
        "inclusive_model_candidate",
        "inclusive_model_weight",
        "inclusive_oof_prediction_s",
        "inclusive_adjusted_lap_duration_s",
    ]
    forward = forward[columns].sort_values(keys).reset_index(drop=True)
    reverse = reverse[columns].sort_values(keys).reset_index(drop=True)

    assert not forward.loc[
        forward["driver_id"].eq("B2") & forward["lap_number"].eq(7),
        "inclusive_model_candidate",
    ].iloc[0]
    pd.testing.assert_frame_equal(
        forward,
        reverse,
        check_exact=False,
        rtol=1e-10,
        atol=1e-10,
    )


def test_v15_rejects_in_sample_cleaning_and_missing_strict_confirmation():
    validate_race_dossier_config(_v15_config())
    bad = _v15_config()
    bad["inclusive_robust_model"][
        "cleaning_residual_source"
    ] = "in_sample"
    with pytest.raises(ValueError, match="样本外"):
        validate_race_dossier_config(bad)

    bad = _v15_config()
    bad["inclusive_robust_model"]["strict_confirmation_layer"] = False
    with pytest.raises(ValueError, match="严格复核"):
        validate_race_dossier_config(bad)


def test_v15_rejects_predictive_model_when_nuisance_adjustment_saturates():
    config = _v15_config()
    config["inclusive_robust_model"][
        "maximum_abs_nuisance_adjustment_s"
    ] = 10.0
    config["inclusive_robust_model"]["maximum_oof_mae_s"] = 100.0
    laps = _race_laps(laps=18)
    for proxy in config["simulation_proxies"].values():
        proxy["enabled"] = False
    extreme = laps["lap_number"].mod(3).eq(0)
    laps.loc[extreme, "all_green"] = False
    laps.loc[extreme, "track_status_codes"] = "4"
    laps.loc[extreme, "lap_duration"] += 100.0

    annotated = annotate_race_laps(laps, config)

    assert annotated["inclusive_model_oof_mae_s"].iloc[0] < (
        annotated["inclusive_reference_oof_mae_s"].iloc[0]
    )
    assert annotated["inclusive_adjustment_saturation_fraction"].iloc[0] > (
        config["inclusive_robust_model"][
            "maximum_saturated_adjustment_fraction"
        ]
    )
    assert annotated["inclusive_model_validation_status"].iloc[0] == (
        "rejected_adjustment_sanity_gate"
    )


def test_v15_dossier_publishes_inclusive_audit_and_keeps_strict_layer_separate():
    config = _v15_config()
    laps = _race_laps(laps=18)
    for proxy in config["simulation_proxies"].values():
        proxy["enabled"] = False
    non_green = laps["lap_number"].mod(3).eq(0)
    laps.loc[non_green, "all_green"] = False
    laps.loc[non_green, "track_status_codes"] = "4"
    laps.loc[non_green, "lap_duration"] += 4.0

    report = build_race_dossier(
        laps,
        _qualifying(),
        config,
        classification_proxy=None,
    )

    audit = report["inclusive_robust_model_audit"]
    assert audit["status"] == "accepted_crossfit_proxy"
    assert audit["candidate_laps"] > audit["strict_confirmation_laps"]
    assert audit["adjustment_distributions_s"][
        "total_nuisance"
    ]["median"] is not None
    assert audit["adjustment_distributions_s"][
        "absolute_oof_residual"
    ]["p95"] is not None
    assert report["event_ledger"]["primary_disposition_role"] == (
        "strict_confirmation_ledger_with_v15_soft_weight_actions"
    )
    assert report["ratings"] == {"PAC": None, "OVR": None}
    assert report["stint_dossiers"]
    assert all(
        "effective_weight_laps" in row
        and "strict_confirmation" in row
        for row in report["stint_dossiers"]
    )
    assert all(
        "contribution_percentages" not in row
        for row in report["vehicle_driver_decomposition"]
    )
