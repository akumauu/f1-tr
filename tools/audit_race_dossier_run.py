#!/usr/bin/env python3
"""审计追加式 Race Dossier run 的哈希、发布合同与代理支持度。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"路径越出项目：{value}")
    return path


def audit(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(ROOT):
        raise ValueError("manifest 必须位于项目目录内")
    manifest = _load(manifest_path)
    run_id = str(manifest["run_id"])
    config_path = _project_path(
        manifest["input_identities"]["config"]["path"]
    )
    config = _load(config_path)
    config_snapshot = manifest_path.parent / "config.snapshot.json"

    hash_errors: list[str] = []
    identity_checks: list[dict[str, Any]] = []

    def verify(
        path: Path,
        *,
        expected_sha256: str,
        expected_bytes: int | None,
        role: str,
    ) -> None:
        actual_sha256 = _sha256(path)
        actual_bytes = path.stat().st_size
        passed = bool(
            actual_sha256 == expected_sha256
            and (
                expected_bytes is None
                or actual_bytes == int(expected_bytes)
            )
        )
        identity_checks.append(
            {
                "role": role,
                "path": path.relative_to(ROOT).as_posix(),
                "passed": passed,
            }
        )
        if not passed:
            hash_errors.append(path.relative_to(ROOT).as_posix())

    verify(
        config_path,
        expected_sha256=manifest["input_identities"]["config"]["sha256"],
        expected_bytes=manifest["input_identities"]["config"]["bytes"],
        role="config",
    )
    snapshot = _load(config_snapshot)
    config_snapshot_matches = snapshot == config

    reports: list[dict[str, Any]] = []
    for row in manifest["events"]:
        path = _project_path(row["path"])
        verify(
            path,
            expected_sha256=row["sha256"],
            expected_bytes=row["bytes"],
            role="event_report",
        )
        reports.append(_load(path))
    for row in manifest["season_audits"]:
        path = _project_path(row["path"])
        verify(
            path,
            expected_sha256=row["sha256"],
            expected_bytes=row["bytes"],
            role="season_audit",
        )

    artifact_manifest_path = _project_path(manifest["artifact_manifest"])
    artifact_manifest = _load(artifact_manifest_path)
    artifact_file = _project_path(artifact_manifest["file"]["path"])
    verify(
        artifact_file,
        expected_sha256=artifact_manifest["file"]["sha256"],
        expected_bytes=artifact_manifest["file"]["bytes"],
        role="lap_universe",
    )

    frontend_dir = (
        _project_path(config["outputs"]["frontend_base"]) / f"run={run_id}"
    )
    frontend_manifest_path = frontend_dir / "manifest.json"
    frontend_manifest = _load(frontend_manifest_path)
    for row in frontend_manifest["reports"]:
        path = frontend_dir / row["path"]
        verify(
            path,
            expected_sha256=row["export_sha256"],
            expected_bytes=None,
            role="frontend_report",
        )

    team_pass: Counter[str] = Counter()
    traffic_supported: Counter[str] = Counter()
    traffic_accepted_events: Counter[str] = Counter()
    traffic_improvements: defaultdict[str, list[float]] = defaultdict(list)
    full_events: list[str] = []
    partial_events: list[dict[str, Any]] = []
    contract_errors: list[str] = []
    clean_laps = 0
    pace_laps = 0
    baseline_laps = 0
    valid_stints = 0
    cumulative_identified = 0
    track_available = 0
    track_direct = 0
    fuel_ranges: list[list[float | None]] = []
    legacy_passed = 0
    track_validation_status: Counter[str] = Counter()
    curve_status: Counter[str] = Counter()
    selected_curve_models: Counter[str] = Counter()
    strategy_curve_eligible = 0
    pit_event_status: Counter[str] = Counter()
    valid_pit_cycles = 0
    strategy_options_with_cost_components = 0

    for report in reports:
        event = f"{report['scope']['year']}|{report['scope']['meeting']}"
        gate = report["publication_gate"]
        if gate["passed"]:
            full_events.append(event)
        elif gate.get("partial_team_conclusion_allowed"):
            partial_events.append(
                {
                    "event": event,
                    "teams": list(gate["publishable_teams"]),
                }
            )
        if gate.get("legacy_clean_four_team_gate", {}).get("passed"):
            legacy_passed += 1
        for row in gate.get("module_gates", {}).get("teams", []):
            if row["passed"]:
                team_pass[str(row["team"])] += 1

        coverage = report["coverage"]
        clean_laps += int(coverage["modelable_reporting_laps"])
        pace_laps += int(coverage["pace_adjusted_reporting_laps"])
        baseline_laps += int(coverage["baseline_available_reporting_laps"])
        valid_stints += sum(
            row["status"] == "valid" for row in report["stint_dossiers"]
        )
        cumulative_identified += sum(
            row["cumulative_baseline_status"] == "identified_proxy"
            for row in report["stint_dossiers"]
        )

        proxy = report["simulation_proxy_audit"]
        track_available += int(
            proxy["track_evolution"]["available_laps"]
        )
        track_direct += int(
            proxy["track_evolution"]["direct_support_laps"]
        )
        track_validation_status[
            str(
                proxy["track_evolution"]
                .get("holdout_validation", {})
                .get("status", "not_tested")
            )
        ] += 1
        fuel_ranges.append(proxy["fuel"]["base_correction_range_s"])
        for state in proxy["traffic"]["states"]:
            name = str(state["state"])
            traffic_supported[name] += int(state["supported_laps"])
            validation = state["holdout_validation"]
            if validation["status"] == "accepted_non_worsening":
                traffic_accepted_events[name] += 1
                before = validation["mae_before_s"]
                after = validation["mae_after_s"]
                if before is not None and after is not None:
                    traffic_improvements[name].append(
                        float(before) - float(after)
                    )

        if (
            report["ratings"]["PAC"] is not None
            or report["ratings"]["OVR"] is not None
        ):
            contract_errors.append(f"{event}:PAC_or_OVR_nonnull")
        if report["pre_race_tyre_envelope"]["future_results_used"]:
            contract_errors.append(f"{event}:future_tyre_leak")
        if report["result_impact_audit"]["major_factor_claims"]:
            contract_errors.append(f"{event}:unsupported_major_factor")
        if (
            not gate["passed"]
            and not gate.get("partial_team_conclusion_allowed", False)
            and report["event_conclusion"] is not None
        ):
            contract_errors.append(f"{event}:conclusion_without_gate")
        if (
            gate.get("partial_team_conclusion_allowed", False)
            and not gate["passed"]
            and report["event_conclusion"].get("scope")
            != "partial_team_modules"
        ):
            contract_errors.append(f"{event}:partial_scope_mismatch")
        pre_race = report["pre_race_tyre_envelope"]
        if pre_race.get("optimal_strategy") is not None:
            contract_errors.append(f"{event}:unsupported_optimal_strategy")
        pit = report.get("pit_cycle_loss_proxy")
        if pit:
            pit_event_status[str(pit["status"])] += 1
            valid_pit_cycles += int(pit.get("valid_cycles", 0))
            if pit.get("stationary_pit_time_identified"):
                contract_errors.append(
                    f"{event}:stationary_pit_time_claimed"
                )
        for stint in report["stint_dossiers"]:
            audit = stint.get("tyre_curve_model_audit")
            if not audit:
                continue
            curve_status[str(audit["status"])] += 1
            if audit.get("selected_model"):
                selected_curve_models[str(audit["selected_model"])] += 1
            strategy_curve_eligible += int(
                bool(audit.get("strategy_curve_eligible"))
            )
        strategy_options_with_cost_components += sum(
            row.get("identified_time_components_proxy") is not None
            for key in ("one_stop_options", "two_stop_options")
            for row in pre_race.get(key, [])
        )

    finite_fuel_low = [
        float(row[0]) for row in fuel_ranges if row[0] is not None
    ]
    finite_fuel_high = [
        float(row[1]) for row in fuel_ranges if row[1] is not None
    ]
    expected_coverage = {
        "events": len(reports),
        "publication_gate_passed": len(full_events),
        "partial_team_modules_publishable": len(partial_events),
        "audit_only": len(reports) - len(full_events) - len(partial_events),
        "seasons": len(manifest["season_audits"]),
    }
    coverage_matches = manifest["coverage"] == expected_coverage
    overall_passed = bool(
        not hash_errors
        and not contract_errors
        and config_snapshot_matches
        and coverage_matches
        and frontend_manifest["run_id"] == run_id
        and len(frontend_manifest["reports"]) == len(reports)
    )
    return {
        "passed": overall_passed,
        "run_id": run_id,
        "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
        "manifest_sha256": _sha256(manifest_path),
        "methodological_status": manifest["methodological_status"],
        "coverage": manifest["coverage"],
        "coverage_matches_recomputed": coverage_matches,
        "config_snapshot_matches": config_snapshot_matches,
        "hash_error_count": len(hash_errors),
        "hash_errors": hash_errors,
        "contract_error_count": len(contract_errors),
        "contract_errors": contract_errors,
        "identity_checks": {
            "total": len(identity_checks),
            "passed": sum(row["passed"] for row in identity_checks),
        },
        "full_events": full_events,
        "partial_events": partial_events,
        "legacy_clean_gate_passed": legacy_passed,
        "team_module_pass_counts": dict(team_pass),
        "lap_support": {
            "clean_modelable_laps": clean_laps,
            "conditional_proxy_eligible_laps": pace_laps,
            "eligible_lap_change": pace_laps - clean_laps,
            "baseline_available_laps": baseline_laps,
        },
        "stints": {
            "valid": valid_stints,
            "cumulative_baseline_identified": cumulative_identified,
        },
        "traffic": {
            "supported_laps": dict(traffic_supported),
            "accepted_event_counts": dict(traffic_accepted_events),
            "holdout_improvement_median_s": {
                key: float(sorted(values)[len(values) // 2])
                for key, values in traffic_improvements.items()
                if values
            },
        },
        "track_evolution": {
            "available_laps": track_available,
            "direct_support_laps": track_direct,
            "event_validation_status_counts": dict(
                track_validation_status
            ),
        },
        "fuel": {
            "base_correction_global_range_s": [
                min(finite_fuel_low),
                max(finite_fuel_high),
            ],
            "real_fuel_load_identified": False,
        },
        "ratings": {"PAC": None, "OVR": None},
        "stint_curve_models": {
            "status_counts": dict(curve_status),
            "selected_model_counts": dict(selected_curve_models),
            "strategy_curve_eligible": strategy_curve_eligible,
        },
        "pit_cycle_loss": {
            "event_status_counts": dict(pit_event_status),
            "valid_cycles": valid_pit_cycles,
            "strategy_options_with_identified_cost_components": (
                strategy_options_with_cost_components
            ),
            "stationary_pit_time_identified": False,
            "optimal_strategy": None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = audit(args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
