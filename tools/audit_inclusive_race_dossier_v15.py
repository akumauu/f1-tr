#!/usr/bin/env python3
"""审计 Race Dossier v15 的全量候选、样本外门与严格复核层。"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"manifest 路径越出项目目录：{value}")
    return path


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    finite = sorted(float(value) for value in values if value is not None)
    if not finite:
        return {"minimum": None, "median": None, "maximum": None}
    return {
        "minimum": finite[0],
        "median": float(statistics.median(finite)),
        "maximum": finite[-1],
    }


def audit_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(ROOT):
        raise ValueError("manifest 必须位于项目目录内")
    manifest = _load(manifest_path)
    if manifest.get("schema_version") != "race-dossier-run-manifest-v15":
        raise ValueError("只接受 race-dossier-run-manifest-v15")

    statuses: Counter[str] = Counter()
    releases: Counter[str] = Counter()
    publication_reasons: Counter[str] = Counter()
    hard_reasons: Counter[str] = Counter()
    curve_statuses: Counter[str] = Counter()
    observed = candidate = strict = 0
    effective = kish = 0.0
    valid_stints = strict_confirmed_stints = 0
    improvements: list[float] = []
    model_mae: list[float] = []
    reference_mae: list[float] = []
    saturation: list[float] = []
    nuisance_p95: list[float] = []
    residual_p95: list[float] = []
    failed_events: list[dict[str, Any]] = []
    coherence_errors: list[str] = []

    for event in manifest["events"]:
        report = _load(_project_path(str(event["path"])))
        audit = report.get("inclusive_robust_model_audit") or {}
        status = str(audit.get("status"))
        release = str(report.get("release_status"))
        statuses[status] += 1
        releases[release] += 1
        publication_reasons.update(report["publication_gate"]["reasons"])
        hard_reasons.update(audit.get("hard_exclusion_reason_counts", {}))
        observed += int(audit.get("observed_laps", 0))
        candidate += int(audit.get("candidate_laps", 0))
        strict += int(audit.get("strict_confirmation_laps", 0))
        effective += float(audit.get("effective_weight_mass", 0.0))
        kish += float(audit.get("kish_effective_laps", 0.0))

        crossfit = audit.get("crossfit_validation", {})
        if crossfit.get("mae_improvement_s") is not None:
            improvements.append(float(crossfit["mae_improvement_s"]))
        if crossfit.get("model_oof_mae_s") is not None:
            model_mae.append(float(crossfit["model_oof_mae_s"]))
        if crossfit.get("reference_oof_mae_s") is not None:
            reference_mae.append(float(crossfit["reference_oof_mae_s"]))
        sanity = audit.get("adjustment_sanity_gate", {})
        saturation.append(float(sanity.get("observed_saturated_fraction", 0.0)))
        adjustments = audit.get("adjustment_distributions_s", {})
        total = adjustments.get("total_nuisance", {})
        residual = adjustments.get("absolute_oof_residual", {})
        if total.get("p95") is not None:
            nuisance_p95.append(float(total["p95"]))
        if residual.get("p95") is not None:
            residual_p95.append(float(residual["p95"]))

        for stint in report.get("stint_dossiers", []):
            valid_stints += stint.get("status") == "valid"
            strict_confirmed_stints += (
                stint.get("strict_confirmation", {}).get("status")
                == "confirmed"
            )
            curve = stint.get("tyre_curve_model_audit") or {}
            curve_statuses[str(curve.get("status", "missing"))] += 1

        event_id = f"{event['year']}|{event['meeting']}"
        accepted = status == "accepted_crossfit_proxy"
        if not accepted:
            failed_events.append(
                {
                    "event": event_id,
                    "model_status": status,
                    "release_status": release,
                    "oof_model_mae_s": crossfit.get("model_oof_mae_s"),
                    "oof_reference_mae_s": crossfit.get(
                        "reference_oof_mae_s"
                    ),
                    "oof_improvement_s": crossfit.get("mae_improvement_s"),
                    "saturated_adjustment_fraction": sanity.get(
                        "observed_saturated_fraction"
                    ),
                }
            )
            if release != "audit_only":
                coherence_errors.append(
                    f"{event_id}:failed_model_was_published"
                )
        if report.get("ratings") != {"PAC": None, "OVR": None}:
            coherence_errors.append(f"{event_id}:ratings_not_null")

    implementation_errors: list[str] = []
    implementation = manifest.get("input_identities", {}).get(
        "implementation", []
    )
    if not implementation:
        implementation_errors.append("implementation_identities_missing")
    for identity in implementation:
        path = _project_path(str(identity["path"]))
        if not path.is_file():
            implementation_errors.append(f"missing:{identity['path']}")
        elif _sha256(path) != identity["sha256"]:
            implementation_errors.append(f"hash_mismatch:{identity['path']}")

    expected_events = int(manifest.get("coverage", {}).get("events", 0))
    passed = bool(
        len(manifest["events"]) == expected_events == 70
        and observed > 0
        and candidate > 0
        and not coherence_errors
        and not implementation_errors
    )
    return {
        "passed": passed,
        "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
        "manifest_sha256": _sha256(manifest_path),
        "run_id": manifest["run_id"],
        "events": len(manifest["events"]),
        "model_status_counts": dict(statuses),
        "release_status_counts": dict(releases),
        "lap_support": {
            "observed_laps": observed,
            "candidate_laps": candidate,
            "candidate_fraction": candidate / observed if observed else 0.0,
            "strict_confirmation_laps": strict,
            "strict_fraction_of_candidates": (
                strict / candidate if candidate else 0.0
            ),
            "effective_weight_mass": effective,
            "effective_weight_fraction": (
                effective / candidate if candidate else 0.0
            ),
            "kish_effective_laps": kish,
        },
        "crossfit": {
            "mae_improvement_s": _distribution(improvements),
            "model_oof_mae_s": _distribution(model_mae),
            "reference_oof_mae_s": _distribution(reference_mae),
        },
        "adjustment_audit": {
            "saturated_fraction": _distribution(saturation),
            "event_p95_total_nuisance_s": _distribution(nuisance_p95),
            "event_p95_absolute_oof_residual_s": _distribution(
                residual_p95
            ),
        },
        "hard_exclusion_reason_counts": dict(hard_reasons),
        "stints": {
            "valid": valid_stints,
            "strict_confirmed": strict_confirmed_stints,
            "curve_status_counts": dict(curve_statuses),
        },
        "failed_events": failed_events,
        "publication_reason_counts": dict(publication_reasons),
        "implementation_identity_checks": {
            "total": len(implementation),
            "errors": implementation_errors,
        },
        "coherence_errors": coherence_errors,
        "boundaries": {
            "causal_truth_identified": False,
            "actual_fuel_identified": False,
            "physical_tyre_state_identified": False,
            "failed_events_removed_from_audit": False,
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    result = audit_manifest(args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
