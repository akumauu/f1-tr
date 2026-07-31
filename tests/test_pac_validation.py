from __future__ import annotations

import sys
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.pac_validation import (  # noqa: E402
    _driver_scores,
    _cluster_rank_interval,
    _purge_direct_edge,
    audit_joint_identity_collinearity,
    audit_target_fields,
    evaluate_publication_gates,
    past_only_transfer_graph,
    pair_incidence_graph,
    identified_pair_gap,
    strict_simultaneous_split_conformal_radius,
    validation_metrics,
)


def test_pac_target_rejects_result_and_classification_fields() -> None:
    assert audit_target_fields(
        ["target_log_pct", "classification_proxy", "finish_position"]
    ) == ["classification_proxy", "finish_position"]


def test_origin_graph_uses_team_year_and_cannot_see_future_transfer() -> None:
    past = pd.DataFrame(
        [
            {"year": 2023, "team_name": "A", "driver_id": "X"},
            {"year": 2023, "team_name": "A", "driver_id": "Y"},
            {"year": 2023, "team_name": "B", "driver_id": "Z"},
            {"year": 2023, "team_name": "B", "driver_id": "W"},
        ]
    )
    future_transfer = pd.concat(
        [
            past,
            pd.DataFrame(
                [
                    {"year": 2024, "team_name": "B", "driver_id": "X"},
                    {"year": 2024, "team_name": "B", "driver_id": "Z"},
                ]
            ),
        ],
        ignore_index=True,
    )
    past_graph = past_only_transfer_graph(past)
    future_graph = past_only_transfer_graph(future_transfer)
    assert past_graph["node_definition"] == "driver_bipartite_team_year"
    assert past_graph["n_components"] == 2
    assert future_graph["n_components"] == 1


def test_joint_identity_audit_flags_exact_single_team_and_single_year_columns() -> None:
    frame = pd.DataFrame(
        [
            {"year": 2023, "team_name": "A", "driver_id": "ONE"},
            {"year": 2023, "team_name": "A", "driver_id": "ONE"},
            {"year": 2023, "team_name": "A", "driver_id": "MOVE"},
            {"year": 2024, "team_name": "B", "driver_id": "MOVE"},
        ]
    )
    audit = {row["driver"]: row for row in audit_joint_identity_collinearity(frame)}
    assert audit["ONE"]["exact_duplicate_identity_columns"] == [
        "driver==driver_team",
        "driver==driver_year",
    ]
    assert audit["ONE"]["joint_driver_base_uniquely_identified"] is False
    assert audit["MOVE"]["joint_driver_base_uniquely_identified"] is True


def test_validation_uses_pair_gap_and_rank_is_non_gating_diagnostic() -> None:
    predictions = pd.DataFrame(
        [
            {
                "origin_index": 1,
                "year": 2024,
                "meeting_name": "E1",
                "driver": "A",
                "team": "T",
                "target_z": 0.2,
                "prediction_z": 0.1,
                "negative_control_z": -0.1,
                "predictive_sigma_z": 0.3,
                "train_driver_events": 4,
            },
            {
                "origin_index": 1,
                "year": 2024,
                "meeting_name": "E1",
                "driver": "B",
                "team": "T",
                "target_z": -0.2,
                "prediction_z": -0.1,
                "negative_control_z": 0.1,
                "predictive_sigma_z": 0.3,
                "train_driver_events": 4,
            },
        ]
    )
    metrics = validation_metrics(
        predictions,
        [
            {
                "origin_index": 1,
                "scores": {"A": 0.1, "B": -0.1},
                    "transfer_graph": {
                        "driver_component": {"A": 0, "B": 0}
                    },
                    "teammate_edges": [["A", "B"]],
                    "prior_oos_pair_residuals": [0.1, -0.1] * 10,
                }
        ],
    )
    assert metrics["pairwise_comparisons"] == 1
    assert "pair_gap_mae_z" in metrics
    assert "pooled_future_rank_spearman" not in metrics
    assert "不参与通过判定" in metrics["rank_boundary"]


def test_post_freeze_confirmation_is_a_hard_gate() -> None:
    metrics = {
        "origins": 60,
        "event_clusters": 60,
        "teams": 10,
        "origins_with_past_only_graph_audit": 60,
        "pairwise_comparisons": 200,
        "pairwise_brier": 0.20,
        "brier_improvement_vs_neutral": 0.05,
        "pairwise_log_score": 0.60,
        "pairwise_direction_accuracy": 0.60,
        "pairwise_balanced_accuracy": 0.60,
        "pair_margin_spearman": 0.40,
        "each_season_direction_accuracy": {"2023": 0.6},
        "event_block_bootstrap_95_low": {
            "balanced_accuracy": None,
            "pair_margin_spearman": None,
            "brier_skill_vs_best_baseline": None,
        },
        "pair_gap_mae_improvement_fraction": 0.10,
        "pair_gap_predictive_interval_coverage": {"80": 0.80, "95": 0.95},
        "brier_margin_vs_negative_control": 0.02,
        "pair_gap_mae_improvement_vs_negative_control": 0.02,
        "post_freeze_confirmatory_origins": 0,
        "identifiable_novel_edge_comparisons": 5,
        "novel_edge_brier": 0.20,
        "novel_edge_direction_accuracy": 0.80,
        "actual_design_prohibited_field_hits": [],
        "wis_improvement_vs_best_baseline": None,
        "benchmark_suite": {"neutral_zero_gap": "TESTED"},
        "negative_control_suite": {"label_permutation": "NOT_TESTED"},
        "frozen_0_100_mapping_validated": False,
    }
    limits = json.loads(
        (ROOT / "research/configs/pac_validation_2023_2025_v1.json").read_text(
            encoding="utf-8"
        )
    )["publication_gates"]
    result = evaluate_publication_gates(
        metrics,
        {"connected": True, "weakly_anchored_drivers": []},
        {"publication_gates": limits},
        [],
    )
    assert result["passed"] is False
    assert "minimum_post_freeze_confirmatory_event_clusters" in result["failed_gates"]


def test_laplacian_estimator_is_pair_symmetric_and_not_joint_blocks() -> None:
    rows = pd.DataFrame(
        [
            {
                "meeting_key": "E1",
                "team_name": "T",
                "driver_id": "A",
                "target_z": 0.3,
            },
            {
                "meeting_key": "E1",
                "team_name": "T",
                "driver_id": "B",
                "target_z": -0.3,
            },
        ]
    )
    scores, _ = _driver_scores(rows, 1.0)
    assert scores["A"] > 0
    assert scores["B"] < 0
    assert abs(scores["A"] + scores["B"]) < 1e-12


def test_team_alias_config_is_passed_as_team_aliases_mapping() -> None:
    alias_config = json.loads(
        (ROOT / "research/configs/entity_aliases.json").read_text(encoding="utf-8")
    )
    assert "team_aliases" in alias_config
    assert isinstance(alias_config["team_aliases"], dict)
    assert alias_config["team_aliases"] != alias_config


def test_pair_incidence_graph_not_team_year_context_controls_publication() -> None:
    rows = pd.DataFrame(
        [
            {"meeting_key": "E1", "team_name": "T1", "driver_id": "A"},
            {"meeting_key": "E1", "team_name": "T1", "driver_id": "B"},
            {"meeting_key": "E2", "team_name": "T2", "driver_id": "C"},
            {"meeting_key": "E2", "team_name": "T2", "driver_id": "D"},
        ]
    )
    graph = pair_incidence_graph(rows)
    assert graph["n_components"] == 2
    assert graph["connected"] is False
    assert graph["components"] == [["A", "B"], ["C", "D"]]


def test_leave_direct_edge_out_really_removes_all_pair_events() -> None:
    rows = pd.DataFrame(
        [
            {"meeting_key": "E1", "team_name": "T", "driver_id": "A", "target_z": 1.0},
            {"meeting_key": "E1", "team_name": "T", "driver_id": "B", "target_z": -1.0},
            {"meeting_key": "E2", "team_name": "T", "driver_id": "A", "target_z": 0.5},
            {"meeting_key": "E2", "team_name": "T", "driver_id": "C", "target_z": -0.5},
            {"meeting_key": "E3", "team_name": "T", "driver_id": "C", "target_z": 0.5},
            {"meeting_key": "E3", "team_name": "T", "driver_id": "B", "target_z": -0.5},
        ]
    )
    purged = _purge_direct_edge(rows, ("A", "B"))
    assert "E1" not in set(purged["meeting_key"])
    graph = pair_incidence_graph(purged)
    assert graph["driver_component"]["A"] == graph["driver_component"]["B"]


def test_cluster_rank_interval_counts_each_event_once() -> None:
    clustered = {"E1": [100.0, -100.0, 0.0], "E2": [1.0]}
    interval = _cluster_rank_interval(clustered, 0.80)
    assert interval == (0.0, 1.0)


def test_strict_cluster_conformal_small_m_is_unavailable_not_interpolated() -> None:
    assert strict_simultaneous_split_conformal_radius([0.1, 0.2], 0.05) == float(
        "inf"
    )


def test_disconnected_purge_graph_forces_null_gap_even_when_ridge_has_numbers() -> None:
    rows = pd.DataFrame(
        [
            {"meeting_key": "E1", "team_name": "T1", "driver_id": "A"},
            {"meeting_key": "E1", "team_name": "T1", "driver_id": "C"},
            {"meeting_key": "E2", "team_name": "T2", "driver_id": "B"},
            {"meeting_key": "E2", "team_name": "T2", "driver_id": "D"},
        ]
    )
    graph = pair_incidence_graph(rows)
    status, gap = identified_pair_gap(
        {"A": 0.5, "B": -0.5, "C": 0.1, "D": -0.1},
        graph,
        "A",
        "B",
    )
    assert status == "not_identifiable"
    assert gap is None
