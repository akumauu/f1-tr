#!/usr/bin/env python3
"""运行预注册的 2023–2025 PAC 车手级时间外验证。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import scipy
import sklearn

import run_experiments as base
from modeling.pac_validation import (
    audit_joint_identity_collinearity,
    audit_target_fields,
    evaluate_publication_gates,
    past_only_transfer_graph,
    pooled_driver_intervals,
    pooled_driver_intervals_v2,
    rolling_origin_predictions,
    rolling_origin_pair_predictions_v2,
    validation_metrics,
    validation_metrics_v2,
)
from modeling.seasonal_attribution import prepare_season_frame


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "pac_validation_2023_2025_v1.json"


def _load_verified_json(path: Path, expected_hash: str) -> dict[str, Any]:
    actual = base.file_sha256(path)
    if actual != expected_hash:
        raise RuntimeError(f"冻结依赖哈希不一致：{path} -> {actual}")
    return base.load_json(path)


def _load_frame(
    parent: Mapping[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    identity: list[dict[str, Any]] = []
    for item in parent["input_files"]:
        path = ROOT / item["path"]
        actual = base.file_sha256(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"冻结圈数据哈希不一致：{item['path']}")
        frame = pd.read_csv(path, compression="gzip")
        frames.append(frame)
        identity.append(
            {
                "year": int(item["year"]),
                "path": item["path"],
                "sha256": actual,
                "rows": int(len(frame)),
            }
        )
    raw = pd.concat(frames, ignore_index=True)
    alias_config = base.load_json(ROOT / parent["entity_alias_config"])
    aliases = alias_config["team_aliases"]
    return (
        prepare_season_frame(
            raw, aliases, parent["season_order"], parent["session_name"]
        ),
        identity,
    )


def _summary(result: Mapping[str, Any]) -> str:
    metrics = result["validation_metrics"]
    gate = result["publication_gate"]
    status = "通过" if gate["passed"] else "未通过"
    failed = "、".join(gate["failed_gates"]) or "无"
    return "\n".join(
        [
            "# PAC 车手级时间外验证（2023–2025）",
            "",
            f"- 结论：**{status}**；PAC 发布值保持 `null`：`{not gate['passed']}`。",
            f"- 完整分站 rolling-origin：{metrics['origins']}；未来 driver-event：{metrics['future_driver_events']}。",
            f"- Pairwise direction：{metrics['pairwise_direction_accuracy']:.3f}；Brier：{metrics['pairwise_brier']:.3f}；log score：{metrics['pairwise_log_score']:.3f}。",
            f"- Pair-gap MAE：{metrics['pair_gap_mae_z']:.3f} z；相对零基线改善：{metrics['pair_gap_mae_improvement_fraction']:.1%}。",
            f"- 分量内跨 origin 排序稳定性（仅诊断）：{metrics['component_conditional_cross_origin_rank_stability']:.3f}。",
            f"- Pair-gap 80%/95% 预测区间覆盖：{metrics['pair_gap_predictive_interval_coverage']['80']:.3f} / {metrics['pair_gap_predictive_interval_coverage']['95']:.3f}。",
            f"- transfer graph 分量：{result['identification']['n_components']}；全局连通：{result['identification']['connected']}。",
            f"- 冻结后确认性比赛集群：{metrics['post_freeze_confirmatory_origins']} / {result['temporal_boundary']['minimum_post_freeze_confirmatory_event_clusters']}；2026 冻结前四站不计入。",
            f"- 失败门槛：{failed}。",
            "",
            "边界：验证目标只来自未来同队 pair-gap 的条件干净配速；未使用积分、完赛名次或 classification proxy。覆盖率只属于 held-out pair-gap predictive distribution，不是 pooled latent CI coverage。分层区间属于独立的 `conditional_pair_latent_z` 原型，与旧 `pooled_driver_base_z` 和旧车手卡断开；未验证冻结 0–100 映射前 PAC 恒为 null。",
            "",
        ]
    )


def run(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    config = base.load_json(config_path)
    config_hash = base.file_sha256(config_path)
    parent_path = ROOT / config["parent_config"]
    parent = _load_verified_json(parent_path, config["parent_config_sha256"])
    frame, input_identity = _load_frame(parent)
    if int(config.get("protocol_version", 1)) == 2:
        return _run_v2(config_path, config, config_hash, frame, input_identity)
    transfer_graph = past_only_transfer_graph(frame)
    leakage = audit_target_fields(
        [
            "target_log_pct",
            *config["rolling_origin"]["context_numeric"],
            *config["rolling_origin"]["context_blocks"],
        ]
    )
    if leakage:
        raise RuntimeError(f"PAC 标签或赛况头命中禁用赛果字段：{leakage}")
    predictions, rankings = rolling_origin_predictions(frame, config)
    metrics = validation_metrics(predictions, rankings)
    metrics["post_freeze_confirmatory_origins"] = 0
    metrics["post_freeze_confirmatory_pair_events"] = 0
    metrics["frozen_0_100_mapping_validated"] = False
    metrics["transfer_diversity_subgate"] = "NOT_TESTED"
    actual_design_manifests = [
        {
            "origin_index": int(row["origin_index"]),
            "meeting_key": row["meeting_key"],
            **row["actual_design_manifest"],
            "manifest_hash": base.canonical_hash(row["actual_design_manifest"]),
        }
        for row in rankings
    ]
    actual_fields = sorted(
        {
            field
            for manifest in actual_design_manifests
            for field in (
                manifest["numeric_fields"]
                + manifest["categorical_design_fields"]
                + [manifest["target_field"]]
                + manifest["filter_and_group_fields"]
            )
        }
    )
    actual_design_hits = audit_target_fields(actual_fields)
    metrics["actual_design_prohibited_field_hits"] = actual_design_hits
    pooled = pooled_driver_intervals(frame, config)
    gate = evaluate_publication_gates(metrics, transfer_graph, config, pooled)
    weak = set(map(str, transfer_graph.get("weakly_anchored_drivers", [])))
    for row in pooled:
        row["weakly_anchored"] = row["driver"] in weak
    result = {
        "run_id": f"{config['experiment_name']}-{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_path": config_path.relative_to(ROOT).as_posix(),
        "config_hash": config_hash,
        "config_validation_status": config["validation_status"],
        "protocol_hash": config_hash,
        "protocol_frozen_at": config["protocol_frozen_at"],
        "temporal_boundary": config["temporal_boundary"],
        "data_identity": input_identity,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
        },
        "target_contract": {
            **config["target_contract"],
            "fields_used": [
                "target_log_pct",
                "lap_fraction",
                "lap_fraction_sq",
                "tyre_age",
                "tyre_age_sq",
                "compound",
                "circuit_name",
                "driver_id",
                "team_name",
                "meeting_key",
                "stint_number",
            ],
            "prohibited_field_hits": leakage,
        },
        "actual_design_audit": {
            "fields": actual_fields,
            "prohibited_field_hits": actual_design_hits,
            "origin_manifests": actual_design_manifests,
            "combined_manifest_hash": base.canonical_hash(
                actual_design_manifests
            ),
            "audit_source": "actual_fitted_design_export_not_config_feature_claim",
        },
        "rolling_origin_contract": {
            "training_visibility": "strictly_prior_complete_events_only",
            "horizon_events": 1,
            "minimum_completed_events": config["rolling_origin"][
                "minimum_completed_events"
            ],
            "joint_lap_mae_is_acceptance_target": False,
            "transfer_graph_visibility": "rebuilt_at_each_origin_from_prior_events_only",
            "transfer_graph_team_node": "team_year",
            "future_cross_team_rank_is_acceptance_target": False,
        },
        "validation_metrics": metrics,
        "baselines": config["baselines"],
        "identification": {
            "connected": bool(transfer_graph["connected"]),
            "n_components": int(transfer_graph["n_components"]),
            "components": transfer_graph["components"],
            "weakly_anchored_drivers": transfer_graph[
                "weakly_anchored_drivers"
            ],
            "boundary": "断连分量之间的绝对 driver_base_z 不可识别；长期单队且仅一位稳定队友者不得获得全局 PAC。",
        },
        "joint_identity_collinearity_audit": audit_joint_identity_collinearity(
            frame
        ),
        "estimator_identity_contract": {
            "estimator_id": "conditional_pair_laplacian_v1",
            "point_estimator": "ridge_laplacian_on_past_within_team_pair_margins",
            "interval_estimator": "same_ridge_laplacian_under_event_driver_stint_bootstrap",
            "predictive_distribution": "empirical_conformal_from_strictly_prior_origin_pair_gap_residuals",
            "legacy_joint_blocks_reused": False,
            "legacy_pooled_driver_base_z_binding": False,
            "legacy_driver_card_binding": False,
            "frozen_0_100_mapping_validated": False,
        },
        "conditional_pair_latent_intervals": pooled,
        "publication_gate": gate,
        "pac_status": "conditional_pair_prototype_keep_null",
        "pac": None,
    }
    output = ROOT / config["outputs"]["result"]
    summary = ROOT / config["outputs"]["summary"]
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary.write_text(_summary(result), encoding="utf-8")
    return result


def _run_v2(
    config_path: Path,
    config: Mapping[str, Any],
    config_hash: str,
    frame: pd.DataFrame,
    input_identity: list[dict[str, Any]],
) -> dict[str, Any]:
    registry_path = ROOT / config["post_freeze_event_registry"]
    registry = base.load_json(registry_path)
    frozen_at = pd.Timestamp(config["protocol_frozen_at"])
    registered = []
    for row in registry["post_freeze_confirmatory_events"]:
        observed_at = pd.Timestamp(row["artifact_created_at"])
        if observed_at > frozen_at:
            registered.append(row)
    pairs, audits = rolling_origin_pair_predictions_v2(frame, config)
    metrics = validation_metrics_v2(pairs, audits)
    metrics["post_freeze_confirmatory_event_clusters"] = len(registered)
    metrics["post_freeze_confirmatory_pair_events"] = int(
        sum(int(row.get("qualified_pair_events", 0)) for row in registered)
    )
    metrics["frozen_0_100_mapping_validated"] = False
    intervals = pooled_driver_intervals_v2(frame, config)
    limits = config["publication_gates"]
    graph = metrics.get("publication_pair_graph", {})
    checks = {
        "minimum_scored_event_clusters": metrics.get("event_clusters", 0)
        >= limits["minimum_scored_event_clusters"],
        "minimum_scored_pair_events": metrics.get("scored_pair_events", 0)
        >= limits["minimum_scored_pair_events"],
        "minimum_teams": metrics.get("teams", 0) >= limits["minimum_teams"],
        "minimum_evaluable_transfer_edges": metrics.get(
            "evaluable_transfer_edges", 0
        )
        >= limits["minimum_evaluable_transfer_edges"],
        "transfer_unique_event_edge_and_two_outcome_diversity": (
            metrics.get("transfer_diversity_subgate") == "TESTED"
        ),
        "strict_event_cluster_simultaneous_split_conformal": (
            not limits["require_strict_event_cluster_simultaneous_split_conformal"]
            or metrics.get("strict_event_cluster_simultaneous_split_conformal")
            == "TESTED"
        ),
        "full_pipeline_direct_edge_purge": (
            not limits["require_full_pipeline_direct_edge_purge"]
            or metrics.get("full_pipeline_direct_edge_purge") == "TESTED"
        ),
        "transfer_subset_event_block_ci": (
            not limits["require_transfer_subset_event_block_ci"]
            or metrics.get("event_block_ci") != "NOT_TESTED"
        ),
        "event_equal_paired_baseline_lcb": (
            not limits["require_event_equal_paired_baseline_lcb"]
            or metrics.get("event_equal_paired_baseline_lcb") == "TESTED"
        ),
        "transfer_brier_skill_vs_best_baseline": False,
        "transfer_log_score_skill_vs_best_baseline": False,
        "complete_benchmark_suite": (
            not limits["require_complete_benchmark_suite"]
            or all(
                status == "TESTED"
                for status in metrics.get("benchmark_suite", {}).values()
            )
        ),
        "connected_pair_incidence_graph": (
            not limits["require_connected_pair_incidence_graph"]
            or bool(graph.get("connected"))
        ),
        "frozen_0_100_mapping_validation": (
            not limits["require_frozen_0_100_mapping_validation"]
            or metrics["frozen_0_100_mapping_validated"]
        ),
        "minimum_post_freeze_confirmatory_event_clusters": metrics[
            "post_freeze_confirmatory_event_clusters"
        ]
        >= limits["minimum_post_freeze_confirmatory_event_clusters"],
        "minimum_post_freeze_confirmatory_pair_events": metrics[
            "post_freeze_confirmatory_pair_events"
        ]
        >= limits["minimum_post_freeze_confirmatory_pair_events"],
    }
    publication_passed = all(checks.values())
    for row in intervals:
        row["model_publication_gate"] = publication_passed
        row["driver_evidence_gate"] = row[
            "individual_identification_subgate_passed"
        ]
        row["final_eligibility"] = bool(
            row["model_publication_gate"] and row["driver_evidence_gate"]
        )
    manifests = [
        {
            "origin_index": row["origin_index"],
            "meeting_key": row["meeting_key"],
            **row["actual_design_manifest"],
            "manifest_hash": base.canonical_hash(row["actual_design_manifest"]),
        }
        for row in audits
    ]
    actual_fields = sorted(
        {
            field
            for manifest in manifests
            for field in (
                manifest["context_head"]["numeric_fields"]
                + manifest["context_head"]["categorical_fields"]
                + [manifest["context_head"]["target"]]
                + manifest["pair_incidence"]["fields"]
                + [manifest["calibration"]["cluster_field"]]
            )
        }
    )
    result = {
        "run_id": f"{config['experiment_name']}-{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_path": config_path.relative_to(ROOT).as_posix(),
        "config_hash": config_hash,
        "protocol_hash": config_hash,
        "protocol_frozen_at": config["protocol_frozen_at"],
        "v1_status": "superseded_for_method_audit",
        "data_identity": input_identity,
        "lap_evidence_contract": {
            "lap_evidence_hash": base.canonical_hash(input_identity),
            "reducer_id": "driver_event_context_residual_median_v2",
            "shared_by_nominal_and_bootstrap": True,
            "interval_conditioning": "conditional_on_rolling_context_heads",
            "context_heads_refit_inside_bootstrap": False,
        },
        "post_freeze_event_registry": {
            "path": config["post_freeze_event_registry"],
            "sha256": base.file_sha256(registry_path),
            "registered_confirmatory_events": len(registered),
            "counting_rule": registry["counting_rule"],
        },
        "validation_metrics": metrics,
        "pair_predictions": pairs.to_dict(orient="records"),
        "actual_design_audit": {
            "fields": actual_fields,
            "prohibited_field_hits": audit_target_fields(actual_fields),
            "origin_manifests": manifests,
            "combined_manifest_hash": base.canonical_hash(manifests),
        },
        "identification": {
            "publication_graph": graph,
            "publication_graph_role": "qualified_pair_incidence_only",
            "team_year_graph_role": "context_diagnostic_only",
        },
        "conditional_pair_latent_intervals": intervals,
        "publication_gate": {
            "passed": publication_passed,
            "quality_status": (
                "FAIL" if metrics.get("quality_status") == "EVALUABLE" else "NOT_EVALUABLE"
            ),
            "checks": checks,
            "failed_gates": [key for key, value in checks.items() if not value],
            "thresholds": limits,
        },
        "estimator_identity_contract": {
            "estimator_id": "conditional_pair_laplacian_v2",
            "driver_event_estimand": "median_context_residual_z_per_qualified_driver_event",
            "point_export": "nominal_full_score_not_bootstrap_median",
            "bootstrap": "event_then_driver_then_stint_then_lap_then_same_driver_event_statistic_and_laplacian",
            "predictive_interval": "event_clustered_rolling_empirical_predictive_interval_conditional_on_rolling_context_heads_not_split_conformal",
            "purge_scope": "pair_estimator_direct_edge_purge_not_full_pipeline",
            "legacy_pac_binding": False,
        },
        "pac_status": "v2_retrospective_prototype_keep_null",
        "pac": None,
        "ovr": None,
    }
    output = ROOT / config["outputs"]["result"]
    summary = ROOT / config["outputs"]["summary"]
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary.write_text(
        "\n".join(
            [
                "# Conditional pair prototype v2",
                "",
                f"- protocol hash: `{config_hash}`",
                f"- publication: `FAIL`; PAC/OVR: `null`",
                f"- scored event clusters/pairs: {metrics.get('event_clusters', 0)} / {metrics.get('scored_pair_events', 0)}",
                f"- evaluable purged or natural novel edges: {metrics.get('evaluable_transfer_edges', 0)}",
                f"- failed gates: {', '.join(result['publication_gate']['failed_gates'])}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    path = args.config if args.config.is_absolute() else ROOT / args.config
    result = run(path)
    print(json.dumps({"pac_status": result["pac_status"], "failed_gates": result["publication_gate"]["failed_gates"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
