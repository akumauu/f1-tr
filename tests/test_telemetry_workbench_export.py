"""静态分析工作台导出合同。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from export_telemetry_workbench import (  # noqa: E402
    export_reports,
    report_paths_from_race_dossier_manifest,
)


def _write_curve_report(
    tmp_path: Path,
    report_id: str = "curve-first",
) -> tuple[Path, Path, bytes]:
    sidecar = tmp_path / f"{report_id}-curve.json"
    sidecar_payload = {
        "schema_version": "race-dossier-stint-curve-evidence-v17",
        "report_id": report_id,
        "stints": [
            {
                "stint_key": "TEST|AAA|1|SOFT",
                "points": [{"tyre_age_laps": 1.0, "pace_s": 90.0}],
            }
        ],
        "pairwise_comparisons": [],
    }
    sidecar_bytes = (
        json.dumps(
            sidecar_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\r\n"
    ).encode("utf-8")
    sidecar.write_bytes(sidecar_bytes)
    sidecar_hash = hashlib.sha256(sidecar_bytes).hexdigest()

    report = tmp_path / f"{report_id}.json"
    report.write_text(
        json.dumps(
            {
                "report_id": report_id,
                "schema_version": "race-dossier-v17",
                "scope": {
                    "year": 2025,
                    "meeting": "Test GP",
                    "session": "Race",
                    "team": "Test",
                },
                "release_status": "pilot",
                "stint_curve_evidence": {
                    "status": "available",
                    "schema_version": (
                        "race-dossier-stint-curve-evidence-v17"
                    ),
                    "path": sidecar.name,
                    "sha256": sidecar_hash,
                    "bytes": len(sidecar_bytes),
                    "stints": 1,
                    "points": 1,
                    "directly_comparable_pairs": 0,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return report, sidecar, sidecar_bytes


def test_export_reports_writes_manifest_and_preserves_boundary(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "report_id": "synthetic-test",
                "scope": {
                    "year": 2026,
                    "meeting": "Test GP",
                    "session": "Race",
                    "team": "Test",
                },
                "release_status": "exploratory_only",
                "forbidden_claims": ["不得声称真实 SOC"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "public"
    manifest = export_reports([source], output)
    assert manifest["version"] == "telemetry-workbench-v2"
    assert manifest["reports"][0]["id"] == "synthetic-test"
    exported = json.loads(
        (output / manifest["reports"][0]["path"]).read_text(encoding="utf-8")
    )
    assert exported["forbidden_claims"] == ["不得声称真实 SOC"]


def test_duplicate_report_ids_are_rejected(tmp_path):
    paths = []
    for index in range(2):
        path = tmp_path / f"source-{index}.json"
        path.write_text(json.dumps({"report_id": "same"}), encoding="utf-8")
        paths.append(path)
    try:
        export_reports(paths, tmp_path / "out")
    except ValueError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复 report id 必须拒绝")


def test_append_export_preserves_existing_report_bytes(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps({"report_id": "first", "value": 1}),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps({"report_id": "second", "value": 2}),
        encoding="utf-8",
    )
    output = tmp_path / "public"
    export_reports([first], output)
    first_export = output / "reports" / "first.json"
    before = first_export.read_bytes()

    manifest = export_reports([second], output, append=True)

    assert first_export.read_bytes() == before
    assert {row["id"] for row in manifest["reports"]} == {
        "first",
        "second",
    }


def test_curve_sidecar_export_is_exact_and_publishes_hash_chain(tmp_path):
    report, _, source_bytes = _write_curve_report(tmp_path)
    output = tmp_path / "public"

    manifest = export_reports([report], output)

    row = manifest["reports"][0]
    curve = row["stint_curve_evidence"]
    exported_curve = output / curve["path"]
    expected_hash = hashlib.sha256(source_bytes).hexdigest()
    exported_report = json.loads(
        (output / row["path"]).read_text(encoding="utf-8")
    )
    assert exported_curve.read_bytes() == source_bytes
    assert curve["source_sha256"] == expected_hash
    assert curve["export_sha256"] == expected_hash
    assert curve["bytes"] == len(source_bytes)
    assert curve["stints"] == 1
    assert curve["points"] == 1
    assert exported_report["stint_curve_evidence"]["frontend_path"] == (
        curve["path"]
    )
    assert exported_report["stint_curve_evidence"]["frontend_sha256"] == (
        expected_hash
    )


def test_curve_sidecar_export_rejects_missing_or_tampered_source(tmp_path):
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    missing_report, missing_sidecar, _ = _write_curve_report(missing_root)
    missing_sidecar.unlink()
    with pytest.raises(FileNotFoundError, match="sidecar"):
        export_reports([missing_report], tmp_path / "missing-output")

    tampered_root = tmp_path / "tampered"
    tampered_root.mkdir()
    tampered_report, tampered_sidecar, _ = _write_curve_report(
        tampered_root,
        "curve-tampered",
    )
    tampered_sidecar.write_bytes(tampered_sidecar.read_bytes() + b" ")
    with pytest.raises(RuntimeError, match="SHA-256"):
        export_reports([tampered_report], tmp_path / "tampered-output")


def test_curve_sidecar_append_preserves_existing_bytes_and_rejects_tamper(
    tmp_path,
):
    first, _, source_bytes = _write_curve_report(tmp_path, "curve-first")
    second = tmp_path / "second.json"
    second.write_text(
        json.dumps({"report_id": "second", "value": 2}),
        encoding="utf-8",
    )
    third = tmp_path / "third.json"
    third.write_text(
        json.dumps({"report_id": "third", "value": 3}),
        encoding="utf-8",
    )
    output = tmp_path / "public"
    first_manifest = export_reports([first], output)
    first_row = first_manifest["reports"][0]
    first_report = output / first_row["path"]
    first_curve = output / first_row["stint_curve_evidence"]["path"]
    report_before = first_report.read_bytes()

    manifest = export_reports([second], output, append=True)

    retained_row = next(
        row for row in manifest["reports"] if row["id"] == "curve-first"
    )
    assert first_report.read_bytes() == report_before
    assert first_curve.read_bytes() == source_bytes
    assert retained_row["stint_curve_evidence"] == (
        first_row["stint_curve_evidence"]
    )

    missing_output = tmp_path / "missing-existing-public"
    missing_manifest = export_reports([first], missing_output)
    missing_row = missing_manifest["reports"][0]
    (missing_output / missing_row["stint_curve_evidence"]["path"]).unlink()
    with pytest.raises(FileNotFoundError):
        export_reports([third], missing_output, append=True)

    first_curve.write_bytes(source_bytes + b" ")
    with pytest.raises(RuntimeError, match="sidecar SHA-256"):
        export_reports([third], output, append=True)


def test_current_race_dossier_manifest_expansion_is_explicit_and_hash_checked():
    manifest = (
        ROOT
        / "research"
        / "records"
        / "race_dossier_v15"
        / "run=20260725T163219+0800-58bdc41b029f"
        / "manifest.json"
    )
    paths = report_paths_from_race_dossier_manifest(manifest)
    assert len(paths) == 70
    assert all(path.name == "race_dossier.json" for path in paths)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert all("inclusive_robust_model_audit" in report for report in reports)
    assert all(report["ratings"] == {"PAC": None, "OVR": None} for report in reports)


def test_visible_dossier_contains_race_fuel_tyre_stint_and_result_evidence():
    manifest = (
        ROOT
        / "research"
        / "records"
        / "race_dossier_v15"
        / "run=20260725T163219+0800-58bdc41b029f"
        / "manifest.json"
    )
    paths = report_paths_from_race_dossier_manifest(manifest)
    path = next(
        path
        for path in paths
        if "year=2025/round=24/meeting=abu-dhabi-grand-prix"
        in path.as_posix()
    )
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["event_conclusion"]["race_long_run_leader_proxy"]
    assert len(report["race_analysis"]["descriptive_team_order"]) == 4
    assert report["simulation_proxy_audit"]["fuel"]["enabled"] is True
    assert report["simulation_proxy_audit"]["fuel"][
        "real_fuel_load_identified"
    ] is False
    assert report["tyre_strategy_envelope"]["compounds"]
    bor_stints = [
        row for row in report["stint_dossiers"] if row["driver"] == "BOR"
    ]
    assert len(bor_stints) == 2
    assert all(
        row["cumulative_delta_to_reasonable_baseline_s"] is not None
        for row in bor_stints
    )
    assert report["vehicle_driver_decomposition"]
    assert report["result_impact_audit"]["classification_proxy_comparison"]


def test_v16_pilot_exposes_one_reference_pace_per_bor_stint():
    path = (
        ROOT
        / "research"
        / "records"
        / "race_dossier_v16"
        / "pilot=20260725T214500+0800-stint-reference"
        / "race_dossier.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    bor_stints = [
        row
        for row in report["stint_dossiers"]
        if row["driver"] == "BOR"
    ]

    assert report["schema_version"] == "race-dossier-v16"
    assert report["validation_scope"] == (
        "single_event_real_data_pilot_not_full_2023_2025_release"
    )
    assert len(bor_stints) == 2
    assert all(
        row["representative_tyre_age_pace_status"]
        == "identified_conditional_proxy"
        for row in bor_stints
    )
    assert all(
        row["representative_tyre_age_support_laps"][0]
        <= row["representative_tyre_age_laps"]
        <= row["representative_tyre_age_support_laps"][1]
        for row in bor_stints
    )
    assert [row["representative_tyre_age_laps"] for row in bor_stints] == [
        8.0,
        22.0,
    ]
    assert all(
        row["representative_tyre_age_pace_fuel_sensitivity_interval_s"]
        for row in bor_stints
    )
    assert report["ratings"] == {"PAC": None, "OVR": None}


def test_v16_pilot_and_appended_workbench_identity_are_hash_verified():
    pilot_root = (
        ROOT
        / "research"
        / "records"
        / "race_dossier_v16"
        / "pilot=20260725T214500+0800-stint-reference"
    )
    pilot_manifest = json.loads(
        (pilot_root / "manifest.json").read_text(encoding="utf-8")
    )
    event = pilot_manifest["events"][0]
    report_path = ROOT / event["path"]
    report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()

    assert not pilot_manifest["full_season_release"]
    assert pilot_manifest["pilot_events"] == 1
    assert pilot_manifest["expected_full_events"] == 70
    assert report_hash == event["sha256"]

    workbench_root = (
        ROOT / "frontend" / "public" / "data" / "telemetry-workbench"
    )
    workbench_manifest = json.loads(
        (workbench_root / "manifest.json").read_text(encoding="utf-8")
    )
    row = next(
        item
        for item in workbench_manifest["reports"]
        if item["id"]
        == "race-dossier-2025-abu-dhabi-grand-prix-v16"
    )
    exported = workbench_root / row["path"]

    report_ids = [item["id"] for item in workbench_manifest["reports"]]
    assert len(report_ids) >= 75
    assert len(report_ids) == len(set(report_ids))
    assert sum(item.endswith("-v15") for item in report_ids) == 70
    assert row["source_sha256"] == report_hash
    assert row["export_sha256"] == hashlib.sha256(
        exported.read_bytes()
    ).hexdigest()
