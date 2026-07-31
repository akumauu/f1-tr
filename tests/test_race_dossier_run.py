"""Race Dossier v8 追加式 runner 的路径、身份与发布合同测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "tools"))

import run_race_dossiers_v8 as runner  # noqa: E402
import run_race_dossiers_v16 as runner_v16  # noqa: E402
import run_race_dossiers_v17 as runner_v17  # noqa: E402
from audit_low_resource_runtime import audit_runtime  # noqa: E402
from modeling.race_dossier import validate_race_dossier_config  # noqa: E402


def _config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v8.json"
        ).read_text(encoding="utf-8")
    )


def _v9_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v9.json"
        ).read_text(encoding="utf-8")
    )


def _v10_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v10.json"
        ).read_text(encoding="utf-8")
    )


def _v11_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v11.json"
        ).read_text(encoding="utf-8")
    )


def _v12_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v12.json"
        ).read_text(encoding="utf-8")
    )


def _v13_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v13.json"
        ).read_text(encoding="utf-8")
    )


def _v14_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v14.json"
        ).read_text(encoding="utf-8")
    )


def _v15_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "race_dossier_2023_2025_v15.json"
        ).read_text(encoding="utf-8")
    )


def _v16_config() -> dict:
    return runner_v16.load_resolved_config(
        ROOT
        / "research"
        / "configs"
        / "race_dossier_2023_2025_v16.json"
    )


def _v17_config() -> dict:
    return runner_v17.load_resolved_config(
        ROOT
        / "research"
        / "configs"
        / "race_dossier_2023_2025_v17.json"
    )


def test_v8_config_freezes_complete_race_and_null_ratings():
    config = _config()
    validate_race_dossier_config(config)
    contract = config["publication_contract"]
    assert contract["analysis_unit"] == "complete_race"
    assert contract["qualifying_race_independent"]
    assert not contract["publish_pac"]
    assert not contract["publish_ovr"]
    assert not contract["classification_or_points_used_for_fit"]
    runtime = config["runtime"]
    assert runtime["execution_policy"] == "foreground_serial_low_resource"
    assert runtime["duckdb_threads"] == 1
    assert runtime["file_batch_size"] == 1
    assert runtime["duckdb_memory_limit"] == "768MB"


def test_v9_changes_gate_semantics_not_frozen_numeric_thresholds():
    v8 = _config()
    v9 = _v9_config()
    validate_race_dossier_config(v9)
    assert v9["method_version"] == "v9"
    for key in (
        "minimum_observed_laps",
        "minimum_modelable_laps",
        "minimum_modelable_fraction",
        "minimum_tyre_age_span",
    ):
        assert v9["stint_gate"][key] == v8["stint_gate"][key]
    assert not v8["stint_gate"]["stint_validity_independent_of_baseline"]
    assert v9["stint_gate"]["stint_validity_independent_of_baseline"]
    assert v9["inputs"]["lap_universe_manifest"].startswith(
        "research/artifacts/race_dossier_v8/run="
    )
    assert v9["boundaries"]["v8_failure_used_to_lower_thresholds"] is False


def test_v10_changes_only_historical_stint_admission_from_v9():
    v9 = _v9_config()
    v10 = _v10_config()
    validate_race_dossier_config(v10)
    assert v10["method_version"] == "v10"
    assert v10["coverage_gate"] == v9["coverage_gate"]
    assert v10["lap_filter"] == v9["lap_filter"]
    assert v10["stint_gate"] == v9["stint_gate"]
    assert v10["baseline"] == v9["baseline"]
    assert v10["strategy_envelope"]["history_admission"] == (
        "individually_valid_stint_and_event_ledger_closure"
    )


def test_v10_history_admission_uses_valid_stints_without_whole_event_release():
    report = {
        "publication_gate": {"passed": False},
        "event_ledger": {
            "primary_disposition_closure": {"passed": True},
        },
        "stint_dossiers": [
            {"driver": "A", "status": "valid"},
            {"driver": "B", "status": "audit_only"},
        ],
    }
    admitted = runner._historical_stints_for_next_event(
        report,
        event_order=202501,
        config=_v10_config(),
    )
    assert admitted == [
        {"driver": "A", "status": "valid", "event_order": 202501},
    ]


def test_v15_failed_inclusive_model_cannot_contaminate_future_strategy_history():
    report = {
        "publication_gate": {"passed": False},
        "event_ledger": {
            "primary_disposition_closure": {"passed": True},
        },
        "inclusive_robust_model_audit": {
            "status": "rejected_prediction_gate",
        },
        "stint_dossiers": [{"driver": "A", "status": "valid"}],
    }
    assert (
        runner._historical_stints_for_next_event(
            report,
            event_order=202501,
            config=_v15_config(),
        )
        == []
    )


def test_v11_adds_validated_proxies_without_lowering_v10_numeric_gates():
    v10 = _v10_config()
    v11 = _v11_config()
    validate_race_dossier_config(v11)
    assert v11["method_version"] == "v11"
    for section in ("coverage_gate", "lap_filter", "stint_gate", "baseline"):
        assert v11[section] == v10[section]
    assert v11["simulation_proxies"]["fuel"]["claim"] == (
        "analyst_sensitivity_proxy_not_actual_fuel"
    )
    assert v11["simulation_proxies"]["traffic"][
        "require_holdout_non_worsening"
    ]
    assert v11["module_publication_gate"]["minimum_publishable_teams"] == 2
    assert v11["boundaries"]["v10_failure_used_to_lower_thresholds"] is False
    assert v11["boundaries"]["fuel_proxy_is_actual_fuel"] is False


def test_v12_adds_holdout_and_curve_tournament_without_lowering_v11_gates():
    v11 = _v11_config()
    v12 = _v12_config()
    validate_race_dossier_config(v12)
    assert v12["method_version"] == "v12"
    for section in (
        "coverage_gate",
        "module_publication_gate",
        "lap_filter",
        "stint_gate",
        "baseline",
    ):
        assert v12[section] == v11[section]
    assert v12["simulation_proxies"]["track_evolution"][
        "require_holdout_non_worsening"
    ]
    assert set(v12["stint_curve_models"]["candidates"]) == {
        "constant_median",
        "linear_theil_sen",
        "log_theil_sen",
        "quadratic_least_squares",
        "kalman_local_linear",
    }
    assert v12["boundaries"]["local_linear_kalman_is_bayesian_posterior"] is False
    assert v12["boundaries"]["v11_release_rate_used_to_lower_thresholds"] is False


def test_v13_adds_ordered_confirmation_and_pit_cost_without_lowering_v12_gates():
    v12 = _v12_config()
    v13 = _v13_config()
    validate_race_dossier_config(v13)
    assert v13["method_version"] == "v13"
    for section in (
        "coverage_gate",
        "module_publication_gate",
        "lap_filter",
        "stint_gate",
        "baseline",
        "simulation_proxies",
    ):
        assert v13[section] == v12[section]
    assert v13["stint_curve_models"]["validation_protocol"] == (
        "ordered_selection_confirmation_v1"
    )
    assert v13["pit_loss_proxy"]["model"] == (
        "local_adjacent_pace_pit_cycle_v1"
    )
    assert v13["boundaries"]["identified_cost_components_imply_optimal_strategy"] is False
    assert v13["boundaries"]["pit_cycle_proxy_is_stationary_pit_stop_time"] is False


def test_v14_changes_execution_scope_not_v13_scientific_thresholds():
    v13 = _v13_config()
    v14 = _v14_config()
    validate_race_dossier_config(v14)
    assert v14["method_version"] == "v14"
    for section in (
        "coverage_gate",
        "module_publication_gate",
        "lap_filter",
        "stint_gate",
        "baseline",
        "simulation_proxies",
        "pit_loss_proxy",
        "strategy_envelope",
    ):
        assert v14[section] == v13[section]
    curve_v13 = dict(v13["stint_curve_models"])
    curve_v14 = dict(v14["stint_curve_models"])
    assert curve_v14.pop("execution_scope") == "valid_stints_only"
    assert curve_v14 == curve_v13
    assert v14["boundaries"]["v13_runtime_used_to_change_scientific_thresholds"] is False


def test_v15_uses_all_readable_laps_with_oof_soft_weights_and_strict_confirmation():
    v14 = _v14_config()
    v15 = _v15_config()
    validate_race_dossier_config(v15)
    assert v15["method_version"] == "v15"
    assert v15["lap_filter"] == v14["lap_filter"]
    inclusive = v15["inclusive_robust_model"]
    assert inclusive["model"] == "crossfit_huber_additive_v1"
    assert inclusive["cleaning_residual_source"] == "out_of_fold_only"
    assert inclusive["strict_confirmation_layer"]
    assert inclusive["hard_minimum_distance_coverage"] < (
        v15["lap_filter"]["minimum_distance_coverage"]
    )
    assert inclusive["hard_minimum_samples_per_lap"] < (
        v15["lap_filter"]["minimum_samples_per_lap"]
    )
    assert v15["stint_curve_models"]["execution_scope"] == (
        "strict_confirmed_stints_only"
    )
    assert not v15["publication_contract"]["inclusive_model_is_causal_truth"]
    assert not v15["publication_contract"][
        "iterative_cleaning_uses_in_sample_residuals"
    ]
    assert not v15["boundaries"][
        "historical_v1_to_v14_outputs_may_be_overwritten"
    ]


def test_v16_publishes_one_in_support_reference_pace_per_stint():
    v16 = _v16_config()
    validate_race_dossier_config(v16)

    assert v16["method_version"] == "v16"
    assert v16["resolved_base_config"].endswith(
        "race_dossier_2023_2025_v15.json"
    )
    reference = v16["stint_reference_pace"]
    assert reference["model"] == (
        "weighted_theil_sen_within_stint_reference_v1"
    )
    assert reference["reference_tyre_age"] == (
        "within_stint_weighted_median"
    )
    assert not reference["allow_reference_age_extrapolation"]
    assert reference["maximum_direct_comparison_tyre_age_gap_laps"] == 2.0
    assert not v16["boundaries"][
        "representative_stint_pace_is_an_observed_lap"
    ]
    assert not v16["boundaries"][
        "representative_stint_pace_removes_tyre_age_effect"
    ]
    assert v16["runtime"]["duckdb_threads"] == 1
    assert v16["runtime"]["project_cache"].startswith(".runtime-cache/")
    assert v16["outputs"]["records_base"].endswith("race_dossier_v16")


def test_v16_rejects_reference_tyre_age_extrapolation():
    v16 = _v16_config()
    v16["stint_reference_pace"]["allow_reference_age_extrapolation"] = True

    with pytest.raises(ValueError, match="外推"):
        validate_race_dossier_config(v16)


def test_v17_inherits_v16_scientific_gates_and_adds_curve_sidecar_contract():
    v16 = _v16_config()
    v17 = _v17_config()
    validate_race_dossier_config(v17)

    assert v17["method_version"] == "v17"
    assert v17["resolved_base_config"].endswith(
        "race_dossier_2023_2025_v16.json"
    )
    for section in (
        "coverage_gate",
        "module_publication_gate",
        "lap_filter",
        "stint_gate",
        "baseline",
        "simulation_proxies",
        "inclusive_robust_model",
        "stint_reference_pace",
        "pit_loss_proxy",
        "strategy_envelope",
    ):
        assert v17[section] == v16[section]

    evidence = v17["stint_curve_evidence"]
    assert evidence["enabled"]
    assert evidence["schema_version"] == (
        "race-dossier-stint-curve-evidence-v17"
    )
    assert evidence["primary_fit"] == (
        "oof_reliability_weighted_theil_sen_linear_projection_v1"
    )
    assert evidence["prediction_grid_points"] == 33
    assert evidence["comparison_gate"][
        "maximum_reference_tyre_age_gap_laps"
    ] == 2.0
    assert evidence["comparison_gate"][
        "maximum_direct_distance"
    ] == 0.65
    assert not v17["boundaries"][
        "stability_interval_is_confidence_interval"
    ]
    assert not v17["boundaries"]["observed_range_is_prediction_interval"]
    assert not v17["boundaries"]["browser_refits_stint_models"]
    assert v17["runtime"]["project_cache"] == (
        ".runtime-cache/race-dossier-v17"
    )
    assert v17["outputs"] == {
        "records_base": "research/records/race_dossier_v17",
        "artifacts_base": "research/artifacts/race_dossier_v17",
        "frontend_base": "frontend/public/data/race-dossier/v17",
    }

    accepted_ids = {
        row["id"] for row in v17["model_registry"]["accepted_for_v17"]
    }
    assert {
        "single_stint_reference_pace",
        "stint_curve_evidence_sidecar",
        "observable_condition_balance_gate",
    }.issubset(accepted_ids)


def test_all_runtime_and_output_paths_are_project_relative_on_d_drive():
    for config in (
        _config(),
        _v11_config(),
        _v12_config(),
        _v13_config(),
        _v14_config(),
        _v15_config(),
        _v16_config(),
        _v17_config(),
    ):
        values = [
            config["runtime"]["project_cache"],
            *config["outputs"].values(),
        ]
        for value in values:
            resolved = runner._project_path(value, "test")
            assert resolved.drive.upper() == ROOT.drive.upper() == "D:"
            assert resolved.is_relative_to(ROOT)
    with pytest.raises(ValueError, match="相对路径"):
        runner._project_path(r"C:\temp\race-dossier", "bad")


def test_low_resource_entry_uses_direct_win32_priority_without_wmi_queries():
    runner_source = (
        ROOT / "research" / "run_race_dossiers_v8.py"
    ).read_text(encoding="utf-8")
    wrapper_source = (
        ROOT / "tools" / "run_race_dossier_v15_low_resource.ps1"
    ).read_text(encoding="utf-8")
    combined = f"{runner_source}\n{wrapper_source}".lower()
    assert "setpriorityclass" in combined
    for forbidden in (
        "get-ciminstance",
        "get-wmiobject",
        "win32_process",
        "wmic.exe",
    ):
        assert forbidden not in combined
    with pytest.raises(ValueError, match="仓库外"):
        runner._project_path("../outside", "bad")


def test_v15_operational_entry_passes_static_low_resource_audit():
    audit = audit_runtime()
    assert audit["passed"]
    assert audit["forbidden_wmi_query_hits"] == []
    assert audit["foreground_serial_without_child_process"]
    assert audit["project_local_d_drive_paths"]


def test_v16_operational_entry_passes_static_low_resource_audit():
    audit = audit_runtime("v16")
    assert audit["passed"]
    assert audit["forbidden_wmi_query_hits"] == []
    assert audit["foreground_serial_without_child_process"]
    assert audit["project_local_d_drive_paths"]


def test_v17_operational_entry_passes_static_low_resource_audit():
    audit = audit_runtime("v17")
    assert audit["passed"]
    assert audit["forbidden_wmi_query_hits"] == []
    assert audit["foreground_serial_without_child_process"]
    assert audit["project_local_d_drive_paths"]


def test_append_only_writer_refuses_existing_target(tmp_path):
    path = tmp_path / "report.json"
    runner._write_json_new(path, {"version": 1})
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        runner._write_json_new(path, {"version": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}


def test_legacy_v7_identity_mismatches_are_recorded_not_overwritten():
    audit = runner._legacy_v7_audit()
    assert audit["role"] == "audit_only_not_v8_input"
    assert audit["v7_outputs_immutable"]
    assert audit["four_event_batch"]["identity_matches"] is False


def test_telemetry_identity_reuses_frozen_manifest_hashes():
    config = _config()
    files, sources = runner._source_identity(config)
    summary = sources[-1]
    assert len(files) == 70
    assert summary["selected_file_count"] == 70
    assert summary["identity_mode"] == (
        "frozen_manifest_sha256_with_current_size_verification"
    )
    assert len(summary["selected_file_set_sha256"]) == 64
    for season in sources[:-1]:
        assert len(season["manifest_identity"]["sha256"]) == 64
        assert season["partitions"]
        assert all(
            len(row["manifest_declared_sha256"]) == 64
            for row in season["partitions"]
        )
