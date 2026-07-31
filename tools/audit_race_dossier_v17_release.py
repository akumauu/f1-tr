#!/usr/bin/env python3
"""审计 Race Dossier v17 正式 run 的身份、覆盖和曲线证据闭合。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_experiments as base  # noqa: E402


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是 object：{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_path(value: str, label: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise FileNotFoundError(f"{label} 不存在或越出项目：{path}")
    return path


def _verify_file(
    value: str,
    expected_hash: str,
    label: str,
) -> Path:
    path = _project_path(value, label)
    actual = _sha256(path)
    if actual != expected_hash:
        raise RuntimeError(
            f"{label} SHA-256 不一致：expected={expected_hash} actual={actual}"
        )
    return path


def _audit_stint(row: Mapping[str, Any], minimum_refits: int) -> None:
    fit = row["primary_fit"]
    support = fit.get("support_tyre_age_laps")
    if fit.get("status") == "IDENTIFIED":
        if not isinstance(support, list) or len(support) != 2:
            raise RuntimeError("IDENTIFIED Stint 缺少主拟合支持域")
        for point in row.get("points", []):
            age = point.get("tyre_age_laps")
            fitted = point.get("fitted_primary_pace_s")
            if (
                fitted is not None
                and (
                    age is None
                    or float(age) < float(support[0])
                    or float(age) > float(support[1])
                )
            ):
                raise RuntimeError("逐圈主拟合值越出发布支持域")
            nuisance = (
                point.get("nuisance_adjustment_s") or {}
            ).get("total")
            scenarios = point.get(
                "adjusted_pace_by_fuel_scenario_s", {}
            )
            if nuisance is None and any(
                value is not None for value in scenarios.values()
            ):
                raise RuntimeError("缺失 nuisance 时发布了燃油情景圈时")

    stability = fit.get("stability_interval") or {}
    band = stability.get("prediction_band") or []
    band_support = stability.get(
        "prediction_band_support_tyre_age_laps"
    )
    if band:
        if not isinstance(band_support, list) or len(band_support) != 2:
            raise RuntimeError("稳定性带非空但缺少支持域")
        for point in band:
            age = float(point["tyre_age_laps"])
            if (
                age < float(band_support[0])
                or age > float(band_support[1])
                or int(point["supporting_refits"]) < minimum_refits
            ):
                raise RuntimeError("稳定性带越界或 supporting_refits 不足")

    confirmed = row.get("confirmed_shape") or {}
    confirmed_grid = confirmed.get("prediction_grid") or []
    if confirmed.get("status") == "AVAILABLE":
        confirmed_support = confirmed.get("support_tyre_age_laps")
        if (
            not isinstance(confirmed_support, list)
            or len(confirmed_support) != 2
            or any(
                float(point["tyre_age_laps"])
                < float(confirmed_support[0])
                or float(point["tyre_age_laps"])
                > float(confirmed_support[1])
                for point in confirmed_grid
            )
        ):
            raise RuntimeError("确认后描述性曲线越出 strict 支持域")


def _audit_pair(row: Mapping[str, Any]) -> None:
    status = row.get("status")
    direct = row.get("direct_comparison_left_minus_right_pace_s")
    legacy_direct = row.get("left_minus_right_pace_s")
    audit_only = row.get(
        "audit_only_conditional_model_left_minus_right_pace_s"
    )
    if status == "comparable":
        if direct is None or legacy_direct is None or audit_only is not None:
            raise RuntimeError("可比配对的正式秒差字段未闭合")
    elif direct is not None or legacy_direct is not None:
        raise RuntimeError("不可比配对泄漏正式秒差")
    if not isinstance(row.get("common_support_sample_audit"), Mapping):
        raise RuntimeError("配对缺少共同支持 Kish ESS")
    if not isinstance(row.get("common_support_condition_profiles"), Mapping):
        raise RuntimeError("配对缺少共同支持条件画像")


def audit_release(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(ROOT):
        raise ValueError("manifest 必须位于项目内")
    manifest = _json(manifest_path)
    if manifest.get("schema_version") != "race-dossier-run-manifest-v17":
        raise ValueError("只接受 race-dossier-run-manifest-v17")
    run_id = str(manifest["run_id"])
    events = manifest.get("events")
    if not isinstance(events, list) or len(events) != 70:
        raise RuntimeError("v17 正式 run 必须恰有 70 场")

    snapshot_path = manifest_path.parent / "config.snapshot.json"
    config = _json(snapshot_path)
    if base.canonical_hash(config) != manifest["config_hash"]:
        raise RuntimeError("config.snapshot 与 manifest config_hash 不一致")
    evidence_settings = config["stint_curve_evidence"]
    minimum_refits = int(evidence_settings["minimum_delete_block_refits"])
    aggregate = {
        "events": 0,
        "sidecars": 0,
        "points": 0,
        "fit_points": 0,
        "range_supported_stints": 0,
        "stability_estimated_stints": 0,
        "pairwise_comparisons": 0,
        "directly_comparable_stint_pairs": 0,
        "balance_warning_pairs": 0,
        "not_comparable_pairs": 0,
    }
    coverage_keys = {
        "points": "points",
        "fit_points": "fit_points",
        "range_supported_stints": "range_supported_stints",
        "stability_estimated_stints": "stability_estimated_stints",
        "pairwise_comparisons": "pairwise_comparisons",
        "directly_comparable_pairs": "directly_comparable_stint_pairs",
        "balance_warning_pairs": "balance_warning_pairs",
        "not_comparable_pairs": "not_comparable_pairs",
    }
    for event in events:
        report_path = _verify_file(
            str(event["path"]),
            str(event["sha256"]),
            "event report",
        )
        report = _json(report_path)
        if (
            report.get("schema_version") != "race-dossier-v17"
            or report.get("run_id") != run_id
            or report.get("ratings") != {"PAC": None, "OVR": None}
        ):
            raise RuntimeError("主报告 schema/run/ratings 契约不一致")
        curve_ref = report.get("stint_curve_evidence")
        event_curve_ref = event.get("stint_curve_evidence")
        if not isinstance(curve_ref, Mapping) or not isinstance(
            event_curve_ref, Mapping
        ):
            raise RuntimeError("主报告或 event manifest 缺少 curve sidecar")
        curve_path = _verify_file(
            str(curve_ref["path"]),
            str(curve_ref["sha256"]),
            "curve sidecar",
        )
        sidecar = _json(curve_path)
        if (
            sidecar.get("schema_version")
            != "race-dossier-stint-curve-evidence-v17"
            or sidecar.get("report_id") != report.get("report_id")
            or sidecar.get("run_id") != run_id
            or sidecar["contract"].get("browser_refits_models") is not False
            or sidecar["contract"].get("support_extrapolation_allowed")
            is not False
        ):
            raise RuntimeError("curve sidecar 身份或禁止外推契约不一致")
        expected_teams = set(config["scope"]["reporting_teams"])
        if {
            str(row["team"]) for row in sidecar.get("stints", [])
        } - expected_teams:
            raise RuntimeError("sidecar 发布了 reporting teams 之外的 Stint")
        for stint in sidecar.get("stints", []):
            _audit_stint(stint, minimum_refits)
        for pair in sidecar.get("pairwise_comparisons", []):
            _audit_pair(pair)
        aggregate["events"] += 1
        aggregate["sidecars"] += 1
        for sidecar_key, aggregate_key in coverage_keys.items():
            value = int(sidecar["coverage"][sidecar_key])
            if int(curve_ref[sidecar_key]) != value:
                raise RuntimeError(f"主报告 sidecar 计数不一致：{sidecar_key}")
            aggregate[aggregate_key] += value

    manifest_coverage = manifest["coverage"]
    expected_aggregate = {
        "events": int(manifest_coverage["events"]),
        "sidecars": int(manifest_coverage["stint_curve_sidecars"]),
        "points": int(manifest_coverage["stint_curve_points"]),
        "fit_points": int(manifest_coverage["stint_curve_fit_points"]),
        "range_supported_stints": int(
            manifest_coverage["stint_curve_range_supported_stints"]
        ),
        "stability_estimated_stints": int(
            manifest_coverage["stint_curve_stability_estimated_stints"]
        ),
        "pairwise_comparisons": int(
            manifest_coverage["stint_curve_pairwise_comparisons"]
        ),
        "directly_comparable_stint_pairs": int(
            manifest_coverage["directly_comparable_stint_pairs"]
        ),
        "balance_warning_pairs": int(
            manifest_coverage["stint_curve_balance_warning_pairs"]
        ),
        "not_comparable_pairs": int(
            manifest_coverage["stint_curve_not_comparable_pairs"]
        ),
    }
    if aggregate != expected_aggregate:
        raise RuntimeError("sidecar 汇总计数与 run manifest 不闭合")

    for season in manifest.get("season_audits", []):
        _verify_file(
            str(season["path"]),
            str(season["sha256"]),
            "season audit",
        )
    artifact_manifest_path = _project_path(
        str(manifest["artifact_manifest"]), "artifact manifest"
    )
    artifact_manifest = _json(artifact_manifest_path)
    _verify_file(
        str(artifact_manifest["file"]["path"]),
        str(artifact_manifest["file"]["sha256"]),
        "race lap universe",
    )
    for identity in manifest["input_identities"].get(
        "implementation", []
    ):
        _verify_file(
            str(identity["path"]),
            str(identity["sha256"]),
            "implementation identity",
        )

    frontend_root = (
        ROOT
        / config["outputs"]["frontend_base"]
        / f"run={run_id}"
    ).resolve()
    frontend_manifest = _json(frontend_root / "manifest.json")
    frontend_rows = frontend_manifest.get("reports")
    if not isinstance(frontend_rows, list) or len(frontend_rows) != 70:
        raise RuntimeError("v17 frontend run 必须恰有 70 份报告")
    for row in frontend_rows:
        _verify_file(
            (frontend_root / str(row["path"])).relative_to(ROOT).as_posix(),
            str(row["export_sha256"]),
            "frontend report",
        )
        curve = row.get("stint_curve_evidence")
        curve_path = frontend_root / str(curve["frontend_path"])
        if (
            not curve_path.is_file()
            or _sha256(curve_path) != curve["frontend_sha256"]
        ):
            raise RuntimeError("frontend curve sidecar SHA-256 不一致")

    return {
        "status": "PASS",
        "run_id": run_id,
        "manifest_sha256": _sha256(manifest_path),
        "coverage": aggregate,
        "publication_gate_passed": int(
            manifest_coverage["publication_gate_passed"]
        ),
        "partial_team_modules_publishable": int(
            manifest_coverage["partial_team_modules_publishable"]
        ),
        "audit_only": int(manifest_coverage["audit_only"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            audit_release(args.manifest),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
