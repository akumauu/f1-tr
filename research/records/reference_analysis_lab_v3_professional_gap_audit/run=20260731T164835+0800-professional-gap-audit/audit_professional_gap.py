#!/usr/bin/env python3
"""F1 TR v3 与五个公开专业分析目标的追加式差距审计。

本脚本只读取冻结产物与源码，不修改任何 v1/v2/v3、Race Dossier v14-v17
产物。所有输出均写入脚本所在的独立追加目录。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable


SCRIPT_VERSION = "professional-gap-audit-v1.0.0"
RUN_ID = "20260731T164835+0800-professional-gap-audit"
ALLOWED_STATUS = {"PASS", "FAIL", "NOT_TESTED", "UNKNOWN"}
ALLOWED_POSITION = {"LEADING", "PARITY", "BEHIND", "UNKNOWN"}
AUDIT_FILENAMES = (
    "audit_matrix.csv",
    "audit_matrix.json",
    "problem_register.csv",
    "problem_register.json",
    "improvement_backlog.csv",
    "improvement_backlog.json",
    "independent_recalculation.json",
    "improvement_roadmap.md",
    "audit_report.md",
)


HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[4]
V3_ROOT = (
    REPO_ROOT
    / "research/records/reference_analysis_lab_v3"
    / "run=20260731T235900+0800-track-validation-v3"
)
V3_MANIFEST = V3_ROOT / "manifest.json"
V3_BROWSER_INDEX = (
    REPO_ROOT
    / "research/records/reference_analysis_lab_v3_browser_acceptance/manifest.json"
)
V2_INDEX = REPO_ROOT / "frontend/public/data/reference-analysis-lab/v2/manifest.json"
V17_MANIFEST = (
    REPO_ROOT
    / "research/records/race_dossier_v17"
    / "run=20260726T124625+0800-ce93134a40b8/manifest.json"
)
CONFIG = (
    REPO_ROOT
    / "research/configs/reference_analysis_lab_track_validation_2023_2025_v3.json"
)
F1PACE_V1_VALIDATION = (
    REPO_ROOT
    / "research/records/reference_analysis_lab_v1/f1pace"
    / "run=20260730T154800+0800-f1pace-abu-dhabi-v1/validation.json"
)
GENERATOR_SOURCE = REPO_ROOT / "research/run_reference_analysis_lab_track_validation_v3.py"
GENERATOR_LIBRARY = (
    REPO_ROOT / "research/reference_analysis_lab/track_validation_v3.py"
)
FRONTEND_SOURCE = REPO_ROOT / "frontend/src/track-validation-v3.js"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def resolve_repo_path(value: str) -> Path:
    return REPO_ROOT / Path(value.replace("/", str(Path("/"))))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"不能写入空 CSV：{path}")
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if list(row) != fields:
                raise ValueError(f"CSV 字段顺序不一致：{path}")
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False)
                        if isinstance(value, (list, dict))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def pct(value: float, digits: int = 3) -> str:
    return f"{value * 100:.{digits}f}%"


def num(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def verify_hash(path: Path, expected: str, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} 缺失：{path}")
    actual = sha256_file(path)
    if actual != expected:
        raise AssertionError(
            f"{label} 哈希不一致：expected={expected}, actual={actual}, path={path}"
        )
    return {
        "label": label,
        "path": repo_relative(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "status": "PASS",
    }


def verify_v3_hash_graph() -> list[dict[str, Any]]:
    manifest = load_json(V3_MANIFEST)
    checks: list[dict[str, Any]] = []
    checks.append(
        verify_hash(
            resolve_repo_path(manifest["config"]["path"]),
            manifest["config"]["sha256"],
            "v3 config",
        )
    )
    for key in ("report", "model_registry", "event_catalog", "observable_track_registry"):
        checks.append(
            verify_hash(
                resolve_repo_path(manifest[key]["path"]),
                manifest[key]["sha256"],
                f"v3 {key}",
            )
        )
    checks.append(
        verify_hash(
            resolve_repo_path(manifest["source_identities"]["v17_manifest"]["path"]),
            manifest["source_identities"]["v17_manifest"]["sha256"],
            "v17 manifest routed by v3",
        )
    )
    for item in manifest["source_identities"]["year_manifests"]:
        checks.append(
            verify_hash(
                resolve_repo_path(item["path"]),
                item["sha256"],
                f"{item['year']} normalized manifest",
            )
        )
    for target in manifest["target_manifests"]:
        target_manifest_path = resolve_repo_path(target["manifest"])
        checks.append(
            verify_hash(
                target_manifest_path,
                target["manifest_sha256"],
                f"{target['target_id']} target manifest",
            )
        )
        target_manifest = load_json(target_manifest_path)
        for entry in target_manifest["files"]:
            checks.append(
                verify_hash(
                    resolve_repo_path(entry["path"]),
                    entry["sha256"],
                    f"{target['target_id']}:{Path(entry['path']).name}",
                )
            )
    return checks


def v2_report_paths_and_hash_checks() -> tuple[dict[str, Path], list[dict[str, Any]]]:
    index = load_json(V2_INDEX)
    reports: dict[str, Path] = {}
    checks: list[dict[str, Any]] = []
    for target in index["targets"]:
        manifest_path = REPO_ROOT / "frontend/public" / target["manifest"]
        manifest = load_json(manifest_path)
        report_entry = manifest["report"]
        if isinstance(report_entry, str):
            report_path = manifest_path.parent / report_entry
            expected = manifest["report_sha256"]
        else:
            report_path = REPO_ROOT / "frontend/public" / report_entry["path"]
            expected = report_entry["sha256"]
        checks.append(
            verify_hash(
                report_path,
                expected,
                f"v2 report:{target['target_id']}",
            )
        )
        if expected != target["report_sha256"]:
            raise AssertionError(
                f"v2 index/report hash 路由不一致：{target['target_id']}"
            )
        reports[target["target_id"]] = report_path
    return reports, checks


def resolve_identity_path(value: str, base_dir: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    normalized = value.replace("\\", "/")
    if normalized.startswith(
        (
            ".runtime-cache/",
            "research/",
            "frontend/",
            "data/",
            "tests/",
            "tools/",
        )
    ):
        return REPO_ROOT / path
    if base_dir is not None:
        return base_dir / path
    return REPO_ROOT / path


def verify_v3_acceptance_graph() -> dict[str, Any]:
    index = load_json(V3_BROWSER_INDEX)
    checks: list[dict[str, Any]] = []
    run_summaries: list[dict[str, Any]] = []
    for run in index["runs"]:
        manifest_path = resolve_repo_path(run["manifest"])
        checks.append(
            verify_hash(
                manifest_path,
                run["manifest_sha256"],
                f"v3 browser acceptance:{run['acceptance_id']}",
            )
        )
        manifest = load_json(manifest_path)
        checks.append(
            verify_hash(
                resolve_repo_path(manifest["source_run_manifest"]["path"]),
                manifest["source_run_manifest"]["sha256"],
                f"v3 acceptance source:{run['acceptance_id']}",
            )
        )
        checks.append(
            verify_hash(
                resolve_repo_path(manifest["browser_acceptance"]["path"]),
                manifest["browser_acceptance"]["sha256"],
                f"v3 browser result:{run['acceptance_id']}",
            )
        )
        screenshot_count = 0
        exact_mobile_widths: list[int] = []
        for target in manifest["target_manifests"]:
            target_manifest_path = resolve_repo_path(target["path"])
            checks.append(
                verify_hash(
                    target_manifest_path,
                    target["sha256"],
                    (
                        f"v3 acceptance target:{run['acceptance_id']}:"
                        f"{target['target_id']}"
                    ),
                )
            )
            target_manifest = load_json(target_manifest_path)
            for screenshot in target_manifest.get("screenshots", []):
                screenshot_path = resolve_identity_path(screenshot["path"])
                checks.append(
                    verify_hash(
                        screenshot_path,
                        screenshot["sha256"],
                        (
                            f"v3 screenshot:{run['acceptance_id']}:"
                            f"{target['target_id']}:{screenshot['viewport']}"
                        ),
                    )
                )
                screenshot_count += 1
                if screenshot["viewport"] == "mobile-390":
                    exact_mobile_widths.append(
                        int(screenshot.get("css_inner_width", screenshot["width"]))
                    )
        if screenshot_count != int(manifest["screenshot_count"]):
            raise AssertionError(
                f"{run['acceptance_id']} screenshot_count 不一致"
            )
        run_summaries.append(
            {
                "acceptance_id": run["acceptance_id"],
                "index_status": run["status"],
                "manifest_status": manifest["status"],
                "screenshot_count": screenshot_count,
                "mobile_css_widths": exact_mobile_widths,
                "index_reason": run.get("reason"),
            }
        )
    latest = next(
        row
        for row in run_summaries
        if row["acceptance_id"] == index["latest_acceptance_id"]
    )
    if latest["index_status"] != "PASS":
        raise AssertionError("v3 latest browser acceptance 不是 PASS")
    if latest["mobile_css_widths"] != [390, 390, 390, 390, 390]:
        raise AssertionError("v3 latest mobile CSS width 不是全目标 390")
    return {
        "index_sha256": sha256_file(V3_BROWSER_INDEX),
        "latest_acceptance_id": index["latest_acceptance_id"],
        "latest_status": latest["index_status"],
        "latest_screenshot_count": latest["screenshot_count"],
        "latest_exact_mobile_width": 390,
        "prior_failure_preserved": any(
            row["index_status"].startswith("FAILED") for row in run_summaries
        ),
        "runs": run_summaries,
        "hash_checks": checks,
    }


def verify_v2_acceptance_graph() -> dict[str, Any]:
    root = REPO_ROOT / "research/records/reference_analysis_lab_v2"
    checks: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    acceptance_manifests = sorted(root.glob("*/run=*/acceptance_manifest.json"))
    for manifest_path in acceptance_manifests:
        manifest = load_json(manifest_path)
        if manifest["status"] != "PASS":
            raise AssertionError(f"v2 acceptance 不是 PASS：{manifest_path}")
        if isinstance(manifest.get("base_manifest"), dict):
            checks.append(
                verify_hash(
                    resolve_identity_path(
                        manifest["base_manifest"]["path"], manifest_path.parent
                    ),
                    manifest["base_manifest"]["sha256"],
                    f"v2 acceptance base:{manifest['run_id']}",
                )
            )
        elif manifest.get("base_manifest_sha256"):
            checks.append(
                verify_hash(
                    manifest_path.parent / "manifest.json",
                    manifest["base_manifest_sha256"],
                    f"v2 acceptance base:{manifest['run_id']}",
                )
            )
        if isinstance(manifest.get("report"), dict):
            checks.append(
                verify_hash(
                    resolve_identity_path(
                        manifest["report"]["path"], manifest_path.parent
                    ),
                    manifest["report"]["sha256"],
                    f"v2 acceptance report:{manifest['run_id']}",
                )
            )
        elif manifest.get("report_sha256"):
            base_manifest = load_json(manifest_path.parent / "manifest.json")
            report_name = base_manifest["report"]
            if isinstance(report_name, dict):
                report_path = resolve_identity_path(
                    report_name["path"], manifest_path.parent
                )
            else:
                report_path = manifest_path.parent / report_name
            checks.append(
                verify_hash(
                    report_path,
                    manifest["report_sha256"],
                    f"v2 acceptance report:{manifest['run_id']}",
                )
            )
        browser = manifest["browser_acceptance"]
        browser_path = resolve_identity_path(browser["path"], manifest_path.parent)
        checks.append(
            verify_hash(
                browser_path,
                browser["sha256"],
                f"v2 browser result:{manifest['run_id']}",
            )
        )
        screenshot_checks = 0
        for screenshot in manifest.get("screenshots", {}).values():
            checks.append(
                verify_hash(
                    resolve_identity_path(screenshot["path"], manifest_path.parent),
                    screenshot["sha256"],
                    f"v2 screenshot:{manifest['run_id']}:{screenshot_checks + 1}",
                )
            )
            screenshot_checks += 1
        summaries.append(
            {
                "run_id": manifest["run_id"],
                "status": manifest["status"],
                "acceptance_manifest": repo_relative(manifest_path),
                "screenshots_verified": screenshot_checks,
            }
        )

    f1pace_dir = next(root.glob("f1pace/run=*"))
    f1pace_browser_path = f1pace_dir / "browser_acceptance.json"
    f1pace_browser = load_json(f1pace_browser_path)
    if f1pace_browser["status"] != "PASS":
        raise AssertionError("F1pace v2 browser acceptance 不是 PASS")
    checks.append(
        verify_hash(
            f1pace_dir / "manifest.json",
            f1pace_browser["base_manifest_sha256"],
            "v2 acceptance base:F1pace",
        )
    )
    screenshot_checks = 0
    for viewport in ("desktop", "mobile"):
        screenshot = f1pace_browser[viewport]
        checks.append(
            verify_hash(
                resolve_identity_path(screenshot["screenshot_cache_path"]),
                screenshot["sha256"],
                f"v2 screenshot:F1pace:{viewport}",
            )
        )
        screenshot_checks += 1
    summaries.append(
        {
            "run_id": f1pace_browser["run_id"],
            "status": f1pace_browser["status"],
            "acceptance_manifest": None,
            "browser_acceptance": repo_relative(f1pace_browser_path),
            "screenshots_verified": screenshot_checks,
            "limitation": "F1pace v2 只有 browser_acceptance.json，无独立 acceptance_manifest.json。",
        }
    )
    return {
        "targets": len(summaries),
        "all_status_pass": all(row["status"] == "PASS" for row in summaries),
        "summaries": summaries,
        "hash_checks": checks,
    }


def select_model_rows(target: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    target_root = V3_ROOT / "targets" / target
    registry = load_json(target_root / "model_registry.json")["rows"]
    selected = {row["track_key"]: row["selected_model_type"] for row in registry}
    comparison = read_csv(target_root / "model_comparison.csv")
    rows: list[dict[str, Any]] = []
    for row in comparison:
        if selected.get(row["track_key"]) != row["model_type"]:
            continue
        rows.append(
            {
                **row,
                "mae": parse_float(row.get("mae")),
                "p90_abs_error": parse_float(row.get("p90_abs_error")),
                "max_abs_error": parse_float(row.get("max_abs_error")),
                "coverage": parse_float(row.get("coverage")),
                "direction_accuracy": parse_float(row.get("direction_accuracy")),
            }
        )
    if len(rows) != 24:
        raise AssertionError(f"{target} selected rows 应为 24，实际 {len(rows)}")
    return rows, selected


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for key in ("mae", "p90_abs_error", "max_abs_error", "coverage"):
        values = [row[key] for row in rows if row[key] is not None]
        metrics[key] = {
            "min": min(values),
            "mean": fmean(values),
            "max": max(values),
        }
    directions = [
        row["direction_accuracy"]
        for row in rows
        if row["direction_accuracy"] is not None
    ]
    metrics["direction_accuracy"] = {
        "min": min(directions),
        "mean": fmean(directions),
        "max": max(directions),
    }
    metrics["worst_p90"] = sorted(
        (
            {
                "track_key": row["track_key"],
                "meeting": row["meeting"],
                "model_type": row["model_type"],
                "p90_abs_error": row["p90_abs_error"],
            }
            for row in rows
        ),
        key=lambda row: row["p90_abs_error"],
        reverse=True,
    )[:5]
    metrics["worst_max"] = sorted(
        (
            {
                "track_key": row["track_key"],
                "meeting": row["meeting"],
                "model_type": row["model_type"],
                "max_abs_error": row["max_abs_error"],
            }
            for row in rows
        ),
        key=lambda row: row["max_abs_error"],
        reverse=True,
    )[:5]
    return metrics


def selected_model_counts(target: str) -> dict[str, int]:
    rows = load_json(V3_ROOT / "targets" / target / "model_registry.json")["rows"]
    return dict(Counter(row["selected_model_type"] for row in rows))


def ledger_sums(target: str) -> dict[str, int | bool]:
    rows = read_csv(V3_ROOT / "targets" / target / "sample_exclusion_ledger.csv")
    result: dict[str, int | bool] = {}
    for key in rows[0]:
        if key in {"track_key", "ledger_closed"}:
            continue
        try:
            result[key] = sum(int(row[key]) for row in rows)
        except ValueError:
            continue
    if "ledger_closed" in rows[0]:
        result["all_ledgers_closed"] = all(
            row["ledger_closed"].lower() == "true" for row in rows
        )
    return result


def top_prediction_errors(
    target: str,
    selected: dict[str, str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    path = V3_ROOT / "targets" / target / "event_predictions.csv"
    worst: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if selected.get(row["track_key"]) != row["model_type"]:
                continue
            actual = float(row["actual"])
            predicted = float(row["predicted"])
            item = {
                "event_id": row["event_id"],
                "track_key": row["track_key"],
                "meeting": row["meeting"],
                "driver": row["driver"],
                "lap": int(float(row["lap"])),
                "compound": row["compound"],
                "model_type": row["model_type"],
                "actual": actual,
                "predicted": predicted,
                "abs_error": abs(predicted - actual),
            }
            if len(worst) < limit:
                worst.append(item)
                worst.sort(key=lambda value: value["abs_error"])
            elif item["abs_error"] > worst[0]["abs_error"]:
                worst[0] = item
                worst.sort(key=lambda value: value["abs_error"])
    return sorted(worst, key=lambda value: value["abs_error"], reverse=True)


def raw_race_coverage(v3_manifest: dict[str, Any]) -> dict[str, Any]:
    by_year: dict[str, Any] = {}
    total_rows = 0
    total_events = 0
    invalid_files = 0
    skipped_files = 0
    for entry in v3_manifest["source_identities"]["year_manifests"]:
        manifest = load_json(resolve_repo_path(entry["path"]))
        race = [row for row in manifest["partitions"] if row["session"] == "Race"]
        rows = sum(int(row["rows"]) for row in race)
        invalid = sum(int(row["invalid_source_files"]) for row in race)
        skipped = sum(len(row["skipped_files"]) for row in race)
        by_year[str(entry["year"])] = {
            "events": len(race),
            "rows": rows,
            "invalid_source_files": invalid,
            "skipped_source_files": skipped,
        }
        total_rows += rows
        total_events += len(race)
        invalid_files += invalid
        skipped_files += skipped
    return {
        "by_year": by_year,
        "events": total_events,
        "raw_points": total_rows,
        "invalid_source_files": invalid_files,
        "skipped_source_files": skipped_files,
    }


def sampling_audit() -> dict[str, Any]:
    root = REPO_ROOT / ".runtime-cache/reference-analysis-lab-v3/events"
    files = sorted(root.glob("*/segments.json"))
    intervals: list[float] = []
    representative_laps: list[int] = []
    identities: list[str] = []
    for path in files:
        data = load_json(path)
        intervals.append(float(data["observables"]["sample_interval_median_s"]))
        representative_laps.append(int(data["observables"]["representative_laps"]))
        identities.append(data["event_id"])
    if len(files) != 70:
        raise AssertionError(f"segments cache 应有 70 个事件，实际 {len(files)}")
    return {
        "role": "runtime_cache_read_only_recalculation_not_formal_v3_manifest_evidence",
        "events": len(files),
        "unique_event_ids": len(set(identities)),
        "sample_interval_median_s": {
            "min": min(intervals),
            "median": median(intervals),
            "max": max(intervals),
        },
        "approx_hz": {
            "min": 1.0 / max(intervals),
            "median": 1.0 / median(intervals),
            "max": 1.0 / min(intervals),
        },
        "representative_laps": {
            "min": min(representative_laps),
            "median": median(representative_laps),
            "max": max(representative_laps),
        },
    }


def source_line_evidence() -> dict[str, Any]:
    generator = GENERATOR_SOURCE.read_text(encoding="utf-8").splitlines()
    frontend = FRONTEND_SOURCE.read_text(encoding="utf-8").splitlines()
    config = load_json(CONFIG)
    v3 = load_json(V3_MANIFEST)
    frozen = datetime.fromisoformat(config["frozen_at"])
    created = datetime.fromisoformat(v3["created_at"])
    return {
        "selection_reuses_outer_fold_results": {
            "path": repo_relative(GENERATOR_SOURCE),
            "selection_lines": "1078-1126",
            "reporting_lines": "1153-1185",
            "verified_tokens": [
                "candidate_publication_decision" in "\n".join(generator[1077:1126]),
                "comparison_rows.append" in "\n".join(generator[1152:1185]),
            ],
            "untouched_final_confirmation_fold": False,
        },
        "universal_auto_publication": {
            "path": repo_relative(GENERATOR_SOURCE),
            "lines": "1153-1160",
            "verified": "gate_passed = True" in "\n".join(generator[1152:1160]),
            "absolute_task_quality_ceiling": False,
        },
        "cache_identity": {
            "path": repo_relative(GENERATOR_SOURCE),
            "lines": "351-370",
            "contains_generator_sha": "generator_sha" in "\n".join(generator[350:370]),
            "contains_config_sha": "config_sha" in "\n".join(generator[350:370]),
        },
        "reference_informed_flags": {
            "path": repo_relative(GENERATOR_SOURCE),
            "lines": "1149-1150;1945-1946",
            "f1pace_and_deltadata_literal_false": (
                '"reference_informed": False' in "\n".join(generator[1130:1151])
            ),
            "fdata_literal_false": (
                '"reference_informed": False' in "\n".join(generator[1926:1947])
            ),
        },
        "q_phase_status_logic": {
            "path": repo_relative(GENERATOR_SOURCE),
            "lines": "2053-2066",
            "abu_reference_branch_precedes_q_phase_branch": (
                generator[2056].strip() == "if is_public_reference:"
                and "elif task ==" in generator[2058]
            ),
        },
        "frontend_layer_behavior": {
            "path": repo_relative(FRONTEND_SOURCE),
            "lines": "375-404;548-560;629-658",
            "comparison_uses_layer": any(
                "state.layer" in line for line in frontend[374:404]
            ),
            "coverage_uses_layer": any(
                "state.layer" in line for line in frontend[403:540]
            ),
            "method_cards_use_layer": any(
                "state.layer" in line for line in frontend[547:560]
            ),
            "overclaim_text_present": any(
                "完全留出的年份上稳定改善" in line
                for line in frontend[330:351]
            ),
        },
        "time_identity": {
            "config_frozen_at": config["frozen_at"],
            "manifest_created_at": v3["created_at"],
            "frozen_after_manifest_creation_seconds": (
                frozen.astimezone(timezone.utc) - created.astimezone(timezone.utc)
            ).total_seconds(),
            "chronological_freeze_proven": frozen <= created.astimezone(frozen.tzinfo),
        },
    }


def v17_summary() -> dict[str, Any]:
    data = load_json(V17_MANIFEST)
    coverage = data.get("coverage", {})
    return {
        "run_id": data["run_id"],
        "manifest_sha256": sha256_file(V17_MANIFEST),
        "coverage": coverage,
        "status": data.get("status"),
    }


def build_recalculation() -> tuple[dict[str, Any], dict[str, Path]]:
    v3_manifest = load_json(V3_MANIFEST)
    reports, v2_checks = v2_report_paths_and_hash_checks()
    v3_checks = verify_v3_hash_graph()
    v3_acceptance = verify_v3_acceptance_graph()
    v2_acceptance = verify_v2_acceptance_graph()
    f1_rows, f1_selected = select_model_rows("f1pace")
    delta_rows, delta_selected = select_model_rows("deltadata")
    fdata_rows, _ = select_model_rows("fdataanalysis")

    f1_v2 = load_json(reports["f1pace-reverse-engineered-v2"])
    delta_v2 = load_json(reports["deltadata-reverse-engineered-v2"])
    fdata_v2 = load_json(reports["fdataanalysis-reverse-engineered-v2"])
    telemetry_v2 = load_json(
        reports["f1telemetrydata-reverse-engineered-v1"]
    )
    gp_v2 = load_json(reports["gptempo-reverse-engineered-v1"])
    f1_v1 = load_json(F1PACE_V1_VALIDATION)

    telemetry_comparison = {
        row["chart_task"]: row
        for row in read_csv(
            V3_ROOT / "targets/f1telemetrydata/model_comparison.csv"
        )
    }
    abu_reference_rows = [
        row
        for row in read_csv(
            V3_ROOT / "targets/f1telemetrydata/chart_reference_matrix.csv"
        )
        if row["event_id"] == "2025-24-abu-dhabi-grand-prix"
    ]
    q_reference_row = next(
        row
        for row in abu_reference_rows
        if row["chart_task"] == "qualifying_q1_q2_q3_phase"
    )
    q_comparison = telemetry_comparison["qualifying_q1_q2_q3_phase"]

    delta_selected_summary = metric_summary(delta_rows)
    delta_selected_summary["counts_above_threshold"] = {
        "mae_gt_1s": sum(row["mae"] > 1 for row in delta_rows),
        "mae_gt_2s": sum(row["mae"] > 2 for row in delta_rows),
        "p90_gt_1s": sum(row["p90_abs_error"] > 1 for row in delta_rows),
        "p90_gt_5s": sum(row["p90_abs_error"] > 5 for row in delta_rows),
        "max_gt_1s": sum(row["max_abs_error"] > 1 for row in delta_rows),
        "max_gt_5s": sum(row["max_abs_error"] > 5 for row in delta_rows),
    }
    source_audit = source_line_evidence()
    if not all(
        source_audit["selection_reuses_outer_fold_results"]["verified_tokens"]
    ):
        raise AssertionError("未能在冻结生成器中复核模型选择/报告复用证据")
    if source_audit["frontend_layer_behavior"]["comparison_uses_layer"]:
        raise AssertionError("前端比较表已经按 layer 分流，审计结论需要更新")

    recalculation = {
        "schema_version": "reference-analysis-lab-v3-professional-gap-recalculation-v1",
        "run_id": RUN_ID,
        "audit_scope": {
            "v3_run_id": v3_manifest["run_id"],
            "platforms": [
                "F1pace",
                "DeltaData",
                "FDataAnalysis",
                "F1TelemetryData",
                "GP Tempo",
            ],
            "offline_only": True,
        },
        "hash_graph": {
            "v3_checks": v3_checks,
            "v2_report_checks": v2_checks,
            "v3_acceptance_checks": v3_acceptance["hash_checks"],
            "v2_acceptance_checks": v2_acceptance["hash_checks"],
            "checks": (
                len(v3_checks)
                + len(v2_checks)
                + len(v3_acceptance["hash_checks"])
                + len(v2_acceptance["hash_checks"])
            ),
            "failures": 0,
        },
        "acceptance_audit": {
            "v3": {
                key: value
                for key, value in v3_acceptance.items()
                if key != "hash_checks"
            },
            "v2": {
                key: value
                for key, value in v2_acceptance.items()
                if key != "hash_checks"
            },
        },
        "raw_race_coverage": raw_race_coverage(v3_manifest),
        "selected_model_counts": {
            target: selected_model_counts(target)
            for target in (
                "f1pace",
                "deltadata",
                "fdataanalysis",
                "f1telemetrydata",
                "gptempo",
            )
        },
        "f1pace": {
            "selected_model_metrics": metric_summary(f1_rows),
            "sample_ledger": ledger_sums("f1pace"),
            "worst_selected_cells": top_prediction_errors(
                "f1pace", f1_selected
            ),
            "v1_same_event_race_pace": f1_v1,
            "v2_qatar_external_validation": f1_v2["calibration"][
                "external_validation"
            ],
            "v2_color_reader_error": f1_v2["calibration"]["color_extraction"],
            "external_zero_refit": f1_v2["calibration"]["external_zero_refit"],
        },
        "deltadata": {
            "selected_model_metrics": delta_selected_summary,
            "selected_track_metrics": [
                {
                    "track_key": row["track_key"],
                    "meeting": row["meeting"],
                    "model_type": row["model_type"],
                    "mae": row["mae"],
                    "p90_abs_error": row["p90_abs_error"],
                    "max_abs_error": row["max_abs_error"],
                    "direction_accuracy": row["direction_accuracy"],
                    "coverage": row["coverage"],
                }
                for row in delta_rows
            ],
            "sample_ledger": ledger_sums("deltadata"),
            "worst_selected_cells": top_prediction_errors(
                "deltadata", delta_selected
            ),
            "v2_reference_benchmark": delta_v2["reference_benchmark"],
            "public_fuel_discount_s_per_lap": delta_v2["method_card"][
                "publicly_stated_method"
            ][1],
            "manual_review_is_public_capability": any(
                "人工复核" in item
                for item in delta_v2["method_card"]["publicly_stated_method"]
            ),
            "clean_air_threshold_is_f1tr_engineering_choice": any(
                "不是原作者公开数字" in item
                for item in delta_v2["method_card"]["visual_inference"]
            ),
        },
        "fdataanalysis": {
            "selected_model_metrics": metric_summary(fdata_rows),
            "reference_image_events": [
                image["name"]
                for image in fdata_v2["reference_identity"]["images"]
            ],
            "reference_image_statuses": [
                image["status"]
                for image in fdata_v2["reference_identity"]["images"]
            ],
            "same_event_numeric_truth_available": False,
            "validation": fdata_v2["validation"],
            "sampling": sampling_audit(),
        },
        "f1telemetrydata": {
            "v3_model_comparison": telemetry_comparison,
            "v2_validation": telemetry_v2["validation"],
            "abu_q_phase_reference_matrix_row": q_reference_row,
            "abu_q_phase_model_comparison_row": q_comparison,
            "q_phase_status_contradiction": (
                q_reference_row["status"] == "REFERENCE_INFORMED_SAME_EVENT"
                and q_comparison["status"].startswith("NOT_TESTED")
            ),
            "events_without_public_reference": sum(
                row["public_same_event_chart_reference"].lower() == "false"
                for row in read_csv(
                    V3_ROOT / "targets/f1telemetrydata/coverage_matrix.csv"
                )
            ),
        },
        "gptempo": {
            "v2_validation": gp_v2["validation"],
            "v3_model_comparison": read_csv(
                V3_ROOT / "targets/gptempo/model_comparison.csv"
            ),
            "v3_coverage": read_csv(
                V3_ROOT / "targets/gptempo/coverage_matrix.csv"
            ),
            "endpoint_constraint_role": (
                "construction_invariant_not_independent_segment_truth"
            ),
        },
        "source_and_product_audit": source_audit,
        "v17": v17_summary(),
        "boundaries": {
            "non_identifiable": [
                "真实燃油量",
                "SOC",
                "胎温/胎压/物理磨损",
                "赛车设定",
                "损伤",
                "动力模式",
                "车队指令",
            ],
            "classification_proxy_is_fia_final_classification": False,
            "pac": None,
            "ovr": None,
            "cross_task_total_score": None,
        },
    }

    if recalculation["raw_race_coverage"]["events"] != 70:
        raise AssertionError("Race 事件覆盖不是 70")
    if recalculation["raw_race_coverage"]["raw_points"] != 55_248_206:
        raise AssertionError("Race raw points 不是 55,248,206")
    if recalculation["selected_model_counts"]["deltadata"] != {
        "cluster": 5,
        "track": 6,
        "universal": 13,
    }:
        raise AssertionError("DeltaData 选择计数与冻结基线不一致")
    if not recalculation["f1telemetrydata"]["q_phase_status_contradiction"]:
        raise AssertionError("预期的 Q phase 状态矛盾没有被重现")
    return recalculation, reports


def matrix_row(
    row_id: str,
    platform: str,
    target: str,
    subtask: str,
    reference_capability: str,
    f1tr_evidence: str,
    same_scale_numeric_gap: str,
    status: str,
    confidence: str,
    severity: str,
    evidence_path: str,
    evidence_locator: str,
    limitations: str,
    relative_position: str,
    comparison_scope: str,
    unit: str,
    metric: str,
    gap_value: float | None,
    reference_sample_size: int | None,
    finding_category: str,
    counts_toward_advantage_rate: bool = False,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUS:
        raise ValueError(status)
    if relative_position not in ALLOWED_POSITION:
        raise ValueError(relative_position)
    if status in {"UNKNOWN", "NOT_TESTED"} and relative_position != "UNKNOWN":
        raise ValueError(f"{row_id}: 未知/未测试不能计为领先、持平或落后")
    if counts_toward_advantage_rate and relative_position != "LEADING":
        raise ValueError(f"{row_id}: 只有领先项可进入优势率")
    return {
        "row_id": row_id,
        "platform": platform,
        "target": target,
        "subtask": subtask,
        "reference_capability": reference_capability,
        "f1tr_evidence": f1tr_evidence,
        "same_scale_numeric_gap": same_scale_numeric_gap,
        "status": status,
        "confidence": confidence,
        "severity": severity,
        "evidence_path": evidence_path,
        "evidence_locator": evidence_locator,
        "limitations": limitations,
        "relative_position": relative_position,
        "comparison_scope": comparison_scope,
        "unit": unit,
        "metric": metric,
        "gap_value": gap_value,
        "reference_sample_size": reference_sample_size,
        "finding_category": finding_category,
        "counts_toward_advantage_rate": counts_toward_advantage_rate,
    }


def build_matrix(
    recalculation: dict[str, Any], reports: dict[str, Path]
) -> list[dict[str, Any]]:
    f1_v2_path = repo_relative(reports["f1pace-reverse-engineered-v2"])
    delta_v2_path = repo_relative(reports["deltadata-reverse-engineered-v2"])
    fdata_v2_path = repo_relative(reports["fdataanalysis-reverse-engineered-v2"])
    telemetry_v2_path = repo_relative(
        reports["f1telemetrydata-reverse-engineered-v1"]
    )
    gp_v2_path = repo_relative(reports["gptempo-reverse-engineered-v1"])
    delta_australia = next(
        row
        for row in recalculation["deltadata"]["selected_track_metrics"]
        if row["track_key"] == "australian-grand-prix"
    )
    rows: list[dict[str, Any]] = []

    def add(*args: Any, **kwargs: Any) -> None:
        rows.append(matrix_row(*args, **kwargs))

    # F1pace：五个同量纲/同方法语义持平项，一个已证实数值差距，其余未知。
    add(
        "F1P-01", "F1pace", "race_pace", "2025 Abu Dhabi 车手平均圈速",
        "公开同场表给出 20 名车手平均圈速。",
        "20/20 行在 0.001 s 冻结容差内，最大绝对差 0.000491 s。",
        "max_abs_gap=0.000491 s; tolerance=0.001 s", "PASS", "high", "P2",
        repo_relative(F1PACE_V1_VALIDATION), "$.mean_abs_difference_max_s",
        "这是表值复算一致性，不证明清洗、燃油或因果方法与平台相同。",
        "PARITY", "same_event_same_task_same_unit", "seconds",
        "max_abs_error", 0.000491, 20, "VERIFIED_SAME_SCALE",
    )
    add(
        "F1P-02", "F1pace", "traffic_ratio", "2025 Qatar 外部零重拟合交通比例",
        "逐车手逐圈颜色网格给出交通比例代理真值。",
        "1,067 个同场单元：MAE 2.238343 pp，P90 6.847727 pp，max 72.308835 pp。",
        "MAE=2.238343 pp; P90=6.847727 pp; max=72.308835 pp",
        "FAIL", "high", "P1", f1_v2_path,
        "$.calibration.external_validation",
        "参考真值来自图片颜色读数；颜色读数自身 MAE 0.473 pp、max 1.5 pp，仍不能解释 72.3 pp 尾部。",
        "BEHIND", "same_event_same_task_same_unit", "percentage_points",
        "mae_pp", 2.2383428745207405, 1067, "VERIFIED_SAME_SCALE",
    )
    add(
        "F1P-03", "F1pace", "traffic_classification", "2025 Qatar 33% 交通圈阈值分类",
        "公开图以 33% 交通占比解释交通圈。",
        "外部零重拟合分类准确率 99.250234%，不一致率 0.749766%。",
        "classification_disagreement=0.749766 percentage points",
        "PASS", "high", "P2", f1_v2_path,
        "$.calibration.external_validation.traffic_lap_accuracy",
        "高分类准确率会掩盖连续交通比例的长尾；不能替代比例 MAE/P90/max。",
        "PARITY", "same_event_same_task_same_unit", "percentage_points",
        "classification_disagreement", 0.74976569821931, 1067,
        "VERIFIED_SAME_SCALE",
    )
    add(
        "F1P-04", "F1pace", "traffic_semantics", "物理前车 2 秒交通窗口",
        "公开说明采用 2 秒固定交通语义。",
        "F1 TR audited_analysis 使用同位置 crossing headway <=2s。",
        "N/A — exact disclosed method constant", "PASS", "high", "P2",
        f1_v2_path, "$.audited_analysis.definition",
        "同一阈值不等于逐点前车配对、缺失处理或积分方法完全相同。",
        "PARITY", "same_method_invariant", "seconds", "traffic_window", 0.0,
        None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "F1P-05", "F1pace", "traffic_semantics", "33% 交通圈判定",
        "公开图的 traffic lap 语义为一圈超过 33%。",
        "F1 TR 同阈值计算 traffic_laps_over_33pct。",
        "N/A — exact disclosed threshold", "PASS", "high", "P2",
        f1_v2_path, "$.visual_replication.*_driver_summary",
        "阈值语义持平不表示连续比例数值持平。",
        "PARITY", "same_method_invariant", "ratio_fraction", "lap_threshold",
        0.0, None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "F1P-06", "F1pace", "pairwise_delta", "同场 pairwise delta 反对称",
        "两车差值矩阵应满足 delta(A,B)=-delta(B,A)。",
        "70 个事件的 max_antisymmetry_error_s 全部为 0。",
        "max_antisymmetry_error=0 s", "PASS", "high", "P2",
        repo_relative(V3_ROOT / "targets/f1pace/pairwise_invariants.csv"),
        "rows=70; column=max_antisymmetry_error_s",
        "这是结构不变量，不是对 F1pace 私有 pairwise 数值的外部检验。",
        "PARITY", "same_method_invariant", "seconds",
        "max_antisymmetry_error", 0.0, 70, "VERIFIED_METHOD_MATCH",
    )
    add(
        "F1P-07", "F1pace", "algorithm_identity", "平台完整交通算法、积分和缺失规则",
        "公开资料未披露完整实现。",
        "本地实现是可解释物理重建，one_to_one_status=SKIPPED_OPAQUE_METHOD。",
        "UNKNOWN", "UNKNOWN", "high", "P1", f1_v2_path, "$.one_to_one_status",
        "闭源，无法判定算法等价或完整产品能力。",
        "UNKNOWN", "opaque_product_scope", "", "", None, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "F1P-08", "F1pace", "external_isolation", "外部参考不得进入训练",
        "平台自身训练资料不可见。",
        "2025 Qatar/Abu Dhabi 在 v3 F1pace split 中作为 frozen_training_exclusions。",
        "NOT_COMPARABLE — protocol evidence only", "PASS", "high", "P2",
        repo_relative(V3_ROOT / "targets/f1pace/split_registry.csv"),
        "column=frozen_training_exclusions",
        "这是 F1 TR 方法学强项，不能据此推断平台准确率或平台没有隔离。",
        "UNKNOWN", "f1tr_internal_protocol_only", "", "", None, 2,
        "METHOD_ONLY",
    )
    add(
        "F1P-09", "F1pace", "coverage", "跨赛季公开产品覆盖",
        "本地冻结只覆盖两个同场参考事件，不能代表平台全部历史页面。",
        "v3 内部代理覆盖 70 events/24 tracks、77,720 driver laps。",
        "UNKNOWN", "UNKNOWN", "medium", "P2",
        repo_relative(V3_ROOT / "targets/f1pace/coverage_matrix.csv"),
        "rows=24; columns=driver_laps,eligible_driver_laps",
        "内部代理覆盖广度不能与未完整抓取的平台产品覆盖比较。",
        "UNKNOWN", "different_evidence_universe", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )
    add(
        "F1P-10", "F1pace", "v3_internal_tail", "v3 交通代理最坏尾部",
        "没有同单元平台真值。",
        "内部代理最坏为 2024 Japan SAI lap 2，绝对误差 0.523868 ratio。",
        "NOT_COMPARABLE_INTERNAL_PROXY", "FAIL", "high", "P1",
        repo_relative(V3_ROOT / "targets/f1pace/event_predictions.csv"),
        "event=2024 Japanese GP; driver=SAI; lap=2; selected=universal",
        "只能证明当前内部重建存在尾部，不得换算为对 F1pace 的数值差距。",
        "UNKNOWN", "f1tr_internal_proxy_only", "ratio_fraction",
        "max_abs_error", 0.523868, 1, "INTERNAL_ONLY",
    )
    add(
        "F1P-11", "F1pace", "product_scope", "完整交互、更新频率和解释工作流",
        "只冻结了有限页面/图片与方法卡。",
        "F1 TR 有可审计矩阵和账本，但当前 layer 开关未真正分流数据。",
        "UNKNOWN", "UNKNOWN", "medium", "P1", repo_relative(FRONTEND_SOURCE),
        "lines 375-404;548-560",
        "不能用局部截图推断双方完整产品完成度。",
        "UNKNOWN", "opaque_product_scope", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )

    # DeltaData。
    add(
        "DD-01", "DeltaData", "clean_air_pace", "2026 同场 Mercedes clean-air H2H 数值",
        "冻结图含 11 个公开 H2H 数值，其中 3 个有本地同场数据。",
        "3 行 reference-informed benchmark：MAE 0.09134 pp，max 0.111173 pp。",
        "MAE=0.091340 pp; max=0.111173 pp", "FAIL", "high", "P1",
        delta_v2_path, "$.reference_benchmark.metrics",
        "不是盲测；3 行中仅 1 行通过可比门，另外 2 行为 audit_only。",
        "BEHIND", "same_event_same_task_same_unit", "percentage_points",
        "mae_pp", 0.09134, 3, "VERIFIED_SAME_SCALE",
    )
    add(
        "DD-02", "DeltaData", "clean_air_direction", "同场 Mercedes H2H 方向",
        "公开图给出快慢方向。",
        "3/3 方向一致。",
        "direction_accuracy=100%; disagreement=0 percentage points",
        "PASS", "medium", "P2", delta_v2_path,
        "$.reference_benchmark.metrics.direction_accuracy",
        "小样本且 reference-informed，不能外推赛季方向准确率。",
        "PARITY", "same_event_same_task_same_unit", "percentage_points",
        "direction_disagreement", 0.0, 3, "VERIFIED_SAME_SCALE",
    )
    add(
        "DD-03", "DeltaData", "fuel_scenario", "公开具名燃油情景 0.032 s/lap",
        "公开方法文字给出 2026 燃油折损 0.032 s/圈。",
        "F1 TR v2 方法等价层使用相同固定情景。",
        "parameter_gap=0 s/lap", "PASS", "high", "P2", delta_v2_path,
        "$.method_card.publicly_stated_method[1]",
        "这是情景参数，不是真实逐车燃油；不能写成已识别燃油效应。",
        "PARITY", "same_method_invariant", "seconds_per_lap",
        "fuel_scenario_parameter_gap", 0.0, None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "DD-04", "DeltaData", "real_fuel", "真实逐车燃油量与燃油效应",
        "平台是否使用额外私有燃油信息未公开。",
        "公开 telemetry 不含真实燃油量，F1 TR 只能发布具名情景。",
        "NOT_TESTED", "NOT_TESTED", "high", "P0", delta_v2_path,
        "$.method_card.not_identifiable",
        "真实燃油不可识别；不得把 0.032 s/lap 情景称为测量值。",
        "UNKNOWN", "non_identifiable", "", "", None, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "DD-05", "DeltaData", "degradation", "轮胎衰减标准化",
        "公开方法称会标准化 Stint/衰减，但精确公式不公开。",
        "F1 TR 有胎龄/配方代理和 degradation_direction.csv。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(V3_ROOT / "targets/deltadata/degradation_direction.csv"),
        "all rows; no public same-event degradation truth",
        "真实胎况/磨损不可识别，且没有同事件同量纲冻结真值。",
        "UNKNOWN", "no_same_scale_truth", "", "", None, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "DD-06", "DeltaData", "teammate_comparison", "同队共同支持 H2H",
        "公开产品以同队 clear-air H2H 为主要输出。",
        "F1 TR v2 对 10 对同队车手逐对门控，4 对可比、6 对 audit_only。",
        "N/A — capability and gate match", "PASS", "medium", "P2",
        delta_v2_path, "$.audited_analysis.comparable_teammate_pairs",
        "可比门持平不等于 4 对的数值都已被外部真值验证。",
        "PARITY", "same_method_invariant", "pairs",
        "comparable_pair_gate", 0.0, 10, "VERIFIED_METHOD_MATCH",
    )
    add(
        "DD-07", "DeltaData", "comparability_gate", "不可比队/车手不强行全序",
        "公开能力聚焦同队与代表圈，跨队精确因果不可从截图确认。",
        "F1 TR direct_total_order_allowed=false，并输出 gate_failures。",
        "N/A — fail-close semantic match", "PASS", "medium", "P2",
        delta_v2_path, "$.audited_analysis.direct_total_order_allowed",
        "这是审计边界持平；不能推断 DeltaData 完整产品是否有同等账本。",
        "PARITY", "same_method_invariant", "boolean",
        "direct_total_order_allowed", 0.0, None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "DD-08", "DeltaData", "manual_review", "逐圈人工代表性复核",
        "公开方法明确称逐圈人工复核。",
        "F1 TR 用 Huber 自动降权代替不可得的人工标签。",
        "N/A — verified capability gap", "FAIL", "high", "P1",
        delta_v2_path, "$.method_card.publicly_stated_method[2]",
        "已证实功能差距；人工复核对准确率的净收益仍未知。",
        "BEHIND", "same_capability_disclosed", "capability",
        "manual_lap_review", 1.0, None, "VERIFIED_CAPABILITY_GAP",
    )
    add(
        "DD-09", "DeltaData", "track_model_selection", "分赛道/族群模型发布确认",
        "平台训练/发布流程闭源。",
        "11 个专用模型由同一 outer fold 结果选择并报告，没有 untouched final test。",
        "NOT_COMPARABLE_INTERNAL_PROTOCOL", "FAIL", "high", "P0",
        repo_relative(GENERATOR_SOURCE), "lines 1078-1185",
        "这是 F1 TR 发布门缺陷，无法与闭源平台量化比较。",
        "UNKNOWN", "f1tr_internal_protocol_only", "", "", None, 11,
        "INTERNAL_ONLY",
    )
    add(
        "DD-10", "DeltaData", "absolute_tail_gate", "澳大利亚 selected cluster 尾部",
        "没有同单元 DeltaData 真值。",
        (
            f"selected cluster：MAE {delta_australia['mae']:.6f} s，"
            f"P90 {delta_australia['p90_abs_error']:.6f} s，"
            f"max {delta_australia['max_abs_error']:.6f} s，仍被发布。"
        ),
        "NOT_COMPARABLE_INTERNAL_PROXY", "FAIL", "high", "P0",
        repo_relative(V3_ROOT / "targets/deltadata/model_comparison.csv"),
        "track_key=australian-grand-prix; model_type=cluster",
        "证明相对门不能阻止绝对失真；不是对 DeltaData 的 11.168 s 差距。",
        "UNKNOWN", "f1tr_internal_proxy_only", "seconds",
        "max_abs_error", delta_australia["max_abs_error"], 355, "INTERNAL_ONLY",
    )
    add(
        "DD-11", "DeltaData", "opaque_parameters", "clean-air 阈值、赛季权重与人工标签",
        "精确公式、阈值、标签和赛季权重未公开。",
        "80% clean-air 是 F1 TR 工程选择，非原作者公开数字。",
        "UNKNOWN", "UNKNOWN", "high", "P1", delta_v2_path,
        "$.method_card.visual_inference[0]",
        "不能宣称一对一复刻或计算精确算法差距。",
        "UNKNOWN", "opaque_product_scope", "", "", None, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "DD-12", "DeltaData", "product_scope", "公开平台完整赛季覆盖与交互",
        "本地只冻结有限方法帖与图表。",
        "F1 TR 内部覆盖 70 events/24 tracks，但外部同场数值只有 3 行。",
        "UNKNOWN", "UNKNOWN", "high", "P1", delta_v2_path,
        "$.reference_benchmark",
        "不能把内部 70 场覆盖换算为对平台产品覆盖的领先。",
        "UNKNOWN", "different_evidence_universe", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )

    # FDataAnalysis。
    add(
        "FDA-01", "FDataAnalysis", "distance_axis", "标准化赛道距离轴",
        "公开图按赛道距离展示 telemetry/pace 特征。",
        "F1 TR 使用 rel_distance 0..1 和 1,200 点轴。",
        "N/A — disclosed visual method capability", "PASS", "medium", "P2",
        fdata_v2_path, "$.visual_replication",
        "轴语义持平；没有同场逐点真值验证对齐误差。",
        "PARITY", "same_method_invariant", "lap_fraction",
        "axis_endpoint_gap", 0.0, None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "FDA-02", "FDataAnalysis", "corner_straight_segments", "弯道/直道分段",
        "公开分析按赛道位置解释弯道、直道与控制特征。",
        "F1 TR 输出 corners/straights observable proxies 和边界。",
        "N/A — capability match", "PASS", "medium", "P2",
        repo_relative(V3_ROOT / "targets/fdataanalysis/segmentation_events.csv"),
        "rows by event/segment",
        "F1 TR 是观测代理，不是 FIA 官方弯角编号。",
        "PARITY", "same_method_invariant", "capability",
        "segment_capability", 0.0, None, "VERIFIED_METHOD_MATCH",
    )
    add(
        "FDA-03", "FDataAnalysis", "control_features", "速度、油门、制动等控制特征",
        "公开图具有速度/控制特征分析能力。",
        "F1 TR 冻结 speed/throttle/brake/DRS 及距离占比。",
        "N/A — observable channel capability", "PASS", "medium", "P2",
        fdata_v2_path, "$.method_identity",
        "采样、插值、阈值和展示平滑未同场校验。",
        "PARITY", "same_method_invariant", "capability",
        "observable_channel_capability", 0.0, None,
        "VERIFIED_METHOD_MATCH",
    )
    add(
        "FDA-04", "FDataAnalysis", "track_dominance_numeric", "同场赛道主导区段数值",
        "冻结参考图片来自 2026 Hungary。",
        "F1 TR pilot/v3 对照事件是 2025 Abu Dhabi/2023-2025 内部代理。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1", fdata_v2_path,
        "$.reference_identity.images[*].status",
        "事件/赛季/任务不同，严禁做数值差值。",
        "UNKNOWN", "no_same_event_truth", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )
    add(
        "FDA-05", "FDataAnalysis", "speed_trace_numeric", "同场逐点速度/时间差",
        "没有冻结同事件逐点公开数值真值。",
        "v3 MAE 是对 F1 TR 自身 1,200 轴的重采样一致性。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(V3_ROOT / "targets/fdataanalysis/model_comparison.csv"),
        "unit=lap_fraction; target=self profile",
        "0.00032 lap-fraction 不能称为相对 FDataAnalysis 的误差。",
        "UNKNOWN", "f1tr_internal_proxy_only", "lap_fraction",
        "internal_resampling_mae", 0.000319779, 24, "INTERNAL_ONLY",
    )
    add(
        "FDA-06", "FDataAnalysis", "weighting_and_smoothing", "采样/时间加权与平滑策略",
        "公开图片无法恢复精确权重和滤波参数。",
        "F1 TR 已量化 sample vs time weighting gap 0.308727 pp。",
        "UNKNOWN", "UNKNOWN", "medium", "P1", fdata_v2_path,
        "$.validation",
        "这是敏感性提示，不是平台误差。",
        "UNKNOWN", "opaque_product_scope", "percentage_points",
        "internal_weighting_sensitivity", 0.308727, None, "INTERNAL_ONLY",
    )
    add(
        "FDA-07", "FDataAnalysis", "lap_time_reconstruction", "速度积分重建圈时",
        "没有同场平台数值真值。",
        "2025 Abu Dhabi 内部重建 MAE 0.154728 s、P90 0.304797 s、max 0.836559 s。",
        "NOT_COMPARABLE_INTERNAL_PROXY", "FAIL", "medium", "P1",
        fdata_v2_path, "$.validation",
        "内部物理一致性，不得计为对平台落后或持平。",
        "UNKNOWN", "f1tr_internal_proxy_only", "seconds",
        "lap_time_reconstruction_mae", 0.154728, None, "INTERNAL_ONLY",
    )
    add(
        "FDA-08", "FDataAnalysis", "cross_year_stability", "2023-2025 赛道结构稳定性",
        "平台跨年稳定性算法与覆盖未冻结。",
        "F1 TR 23/24 赛道 proxy stable，Dutch 标为 discontinuity。",
        "NOT_COMPARABLE — method evidence only", "PASS", "medium", "P2",
        repo_relative(V3_ROOT / "targets/fdataanalysis/layout_stability.csv"),
        "rows=24; layout_proxy_status",
        "可作为暂定方法学优势，但不能推断平台没有类似处理。",
        "UNKNOWN", "f1tr_internal_protocol_only", "tracks",
        "stable_tracks", 23.0, 24, "METHOD_ONLY",
    )
    add(
        "FDA-09", "FDataAnalysis", "layout_change_boundary", "布局变化禁止 track model",
        "平台布局变化边界未公开。",
        "Dutch observable discontinuity 会禁止 track-specific 发布。",
        "NOT_COMPARABLE — fail-close method", "PASS", "medium", "P2",
        repo_relative(V3_ROOT / "targets/fdataanalysis/coverage_matrix.csv"),
        "track_key=dutch-grand-prix; layout_proxy_status",
        "是观测几何/采样不连续代理，不是已证实赛道改造。",
        "UNKNOWN", "f1tr_internal_protocol_only", "boolean",
        "layout_gate", 1.0, 1, "METHOD_ONLY",
    )
    add(
        "FDA-10", "FDataAnalysis", "product_scope", "完整图表、赛季和解释能力",
        "只冻结两个不同事件图片和方法证据。",
        "F1 TR 有 70 场内部 profile，但无同场外部逐点真值。",
        "UNKNOWN", "UNKNOWN", "high", "P1", fdata_v2_path,
        "$.reference_identity.images",
        "不能从两张图推断平台完整产品，也不能报复刻率。",
        "UNKNOWN", "opaque_product_scope", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )

    # F1TelemetryData：同场数值任务可直接计量。
    telemetry_rows = [
        (
            "FTD-01", "qualifying_lap_delta", "排位圈速差", "0 s",
            "PASS", "PARITY", 0.0, "seconds", "mae", 20,
            "$.validation.qualifying.lap_delta",
        ),
        (
            "FTD-02", "qualifying_control_sections", "排位控制区段占比",
            "MAE=2.639122 pp; max=6.157325 pp", "FAIL", "BEHIND",
            2.639122, "percentage_points", "mae_pp", 24,
            "$.validation.qualifying.lap_sections",
        ),
        (
            "FTD-03", "qualifying_team_throttle", "车队全油门占比",
            "MAE=1.195668 pp; max=3.084442 pp", "FAIL", "BEHIND",
            1.195668, "percentage_points", "mae_pp", 10,
            "$.validation.qualifying.team_throttle",
        ),
        (
            "FTD-04", "race_average_gap", "正赛平均差距",
            "MAE=0.000216 s; max=0.000471 s", "PASS", "PARITY",
            0.000216, "seconds", "mae", 20,
            "$.validation.race.average_gap",
        ),
        (
            "FTD-05", "race_fastest_lap", "正赛最快圈",
            "MAE=0 s; max=0 s", "PASS", "PARITY", 0.0, "seconds",
            "mae", 20, "$.validation.race.fastest_laps",
        ),
        (
            "FTD-06", "pit_lane_duration", "维修区通道时长",
            "MAE=0.065808 s; max=0.363 s", "FAIL", "BEHIND",
            0.065808, "seconds", "mae", 10,
            "$.validation.race.pit_team_means",
        ),
        (
            "FTD-08", "qualifying_top_speed", "排位最高速",
            "MAE=0 km/h; max=0 km/h", "PASS", "PARITY", 0.0,
            "kilometres_per_hour", "mae", 20,
            "$.validation.qualifying.top_speed",
        ),
        (
            "FTD-09", "qualifying_top3_sectors", "前三名官方 sector",
            "MAE=0 s; max=0 s", "PASS", "PARITY", 0.0, "seconds",
            "mae", 9, "$.validation.qualifying.top3_sector_mae_s",
        ),
        (
            "FTD-10", "strategy_boundaries", "前五名配方/Stint 边界",
            "5/5 exact; mismatch=0 rows", "PASS", "PARITY", 0.0,
            "rows", "mismatch_rows", 5,
            "$.validation.race.strategy_boundaries",
        ),
    ]
    for (
        row_id,
        task,
        label,
        gap,
        status,
        position,
        value,
        unit,
        metric,
        n,
        locator,
    ) in telemetry_rows:
        limitation = (
            "同场同任务同量纲；但参考来自公开图表转录和冻结 OpenF1 补充，"
            "不代表平台完整算法。"
        )
        if task == "qualifying_control_sections":
            limitation = (
                "公开控制阈值和采样处理不透明；约 3.7Hz 与本仓扩展采样差异会影响占比。"
            )
        if task == "pit_lane_duration":
            limitation = (
                "lane_duration 是维修区通道时长，不是静止换胎 stop_duration。"
            )
        add(
            row_id, "F1TelemetryData", task, label,
            f"冻结 2025 Abu Dhabi 公开同场图表包含{label}。",
            f"F1 TR v2/v3 同场对照：{gap}。",
            gap, status, "high", "P1" if status == "FAIL" else "P2",
            telemetry_v2_path, locator, limitation, position,
            "same_event_same_task_same_unit", unit, metric, value, n,
            "VERIFIED_SAME_SCALE",
        )
    add(
        "FTD-07", "F1TelemetryData", "qualifying_q1_q2_q3_phase",
        "Q1/Q2/Q3 阶段身份与图表",
        "平台公开图有阶段语义，但本地冻结没有逐阶段数值真值。",
        "v3 model_comparison 正确为 NOT_TESTED；chart_reference_matrix 却把 Abu 行标为 REFERENCE_INFORMED。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(
            V3_ROOT / "targets/f1telemetrydata/chart_reference_matrix.csv"
        ),
        "event=2025-24-abu-dhabi-grand-prix; task=qualifying_q1_q2_q3_phase",
        "状态矛盾必须修复；缺真值前不能报数值准确。",
        "UNKNOWN", "no_same_scale_truth", "", "", None, None,
        "INTERNAL_ONLY",
    )
    add(
        "FTD-11", "F1TelemetryData", "sampling_frequency",
        "控制通道采样频率与阈值处理",
        "平台完整采样/插值/阈值未公开。",
        "冻结 raw car channel 约 3.7Hz，本仓 expanded-v4 约 7.692Hz。",
        "UNKNOWN", "UNKNOWN", "high", "P1", telemetry_v2_path,
        "$.validation.sample_frequency_boundary",
        "频率不同可解释部分控制占比误差，但无法量化因果贡献。",
        "UNKNOWN", "opaque_product_scope", "hertz",
        "sampling_frequency_difference", 3.992, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "FTD-12", "F1TelemetryData", "registry_granularity",
        "七个图表任务独立发布身份",
        "平台完整内部 registry 不可见。",
        "v3 有 7 个 model_comparison task，却只有每赛道 1 个通用 registry 行。",
        "NOT_COMPARABLE_INTERNAL_PROTOCOL", "FAIL", "high", "P1",
        repo_relative(V3_ROOT / "targets/f1telemetrydata/model_registry.json"),
        "$.rows length=24; comparison tasks=7",
        "会把不同任务的 PASS/NOT_TESTED 状态折叠成单一赛道状态。",
        "UNKNOWN", "f1tr_internal_protocol_only", "tasks",
        "unregistered_task_identities", 6.0, 7, "INTERNAL_ONLY",
    )
    add(
        "FTD-13", "F1TelemetryData", "cross_event_coverage",
        "跨赛季同场公开图表覆盖",
        "本地冻结只有 2025 Abu Dhabi 同场参考。",
        "其余 69/70 事件对每个图表任务均无公开同场参考。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(V3_ROOT / "targets/f1telemetrydata/coverage_matrix.csv"),
        "public_same_event_chart_reference=False rows=69",
        "不能用一个事件外推 2023-2025 稳定性。",
        "UNKNOWN", "no_same_scale_truth", "events",
        "events_without_reference", 69.0, 70, "UNKNOWN_PRODUCT_SCOPE",
    )
    add(
        "FTD-14", "F1TelemetryData", "product_scope",
        "完整公开图表包、更新节奏和分析深度",
        "本地冻结 13 张 2025 Abu Dhabi 参考图。",
        "已覆盖若干数值任务，但平台完整产品能力不可从单场图包推断。",
        "UNKNOWN", "UNKNOWN", "medium", "P2", telemetry_v2_path,
        "$.reference_identity.chart_count",
        "局部数值持平不能外推完整产品持平。",
        "UNKNOWN", "opaque_product_scope", "", "", None, 13,
        "UNKNOWN_PRODUCT_SCOPE",
    )

    # GP Tempo。
    add(
        "GPT-01", "GP Tempo", "sector_endpoints",
        "三个官方 sector 端点时间约束",
        "公开方法以官方 sector timing 锚定累计 delta。",
        "90 有序对 × 3 sector=270 端点，max error=0 s。",
        "max_abs_sector_endpoint_error=0 s", "PASS", "high", "P2",
        gp_v2_path, "$.validation.sector_endpoint_checks",
        "这是构造约束必然成立，只验证端点实现，不验证段内曲线。",
        "PARITY", "same_method_invariant", "seconds",
        "max_abs_sector_endpoint_error", 0.0, 270,
        "VERIFIED_METHOD_MATCH",
    )
    add(
        "GPT-02", "GP Tempo", "finish_endpoint",
        "终点累计 delta 等于官方 sector 和",
        "公开方法要求终点闭合。",
        "90 个终点检查，max official sector sum error=0 s。",
        "max_finish_error=0 s", "PASS", "high", "P2", gp_v2_path,
        "$.validation.finish_endpoint_checks",
        "仍是构造不变量，不是逐点独立真值。",
        "PARITY", "same_method_invariant", "seconds",
        "max_finish_error", 0.0, 90, "VERIFIED_METHOD_MATCH",
    )
    add(
        "GPT-03", "GP Tempo", "segment_shape",
        "sector 内逐点 delta 曲线",
        "公开页面没有冻结逐点数值真值。",
        "v3 segment_mae=null，正确标记 NOT_TESTED。",
        "NOT_TESTED", "NOT_TESTED", "high", "P0",
        repo_relative(V3_ROOT / "targets/gptempo/model_comparison.csv"),
        "row model_type=universal; segment_mae_status",
        "严禁由端点零误差推导段内 MAE 或准确率。",
        "UNKNOWN", "no_same_scale_truth", "", "", None, None,
        "UNKNOWN_CLOSED_METHOD",
    )
    add(
        "GPT-04", "GP Tempo", "sampling_rate",
        "约 4Hz 原始 car channel",
        "公开方法基于约 4Hz 数据。",
        "本地 raw sample median=4.167Hz，min=4.149Hz，max=4.167Hz。",
        "median_rate_gap_to_4Hz=0.167 Hz", "PASS", "medium", "P2",
        gp_v2_path, "$.validation.sample_hz_median",
        "“约 4Hz”不是精确平台真值，故仅判方法量级持平。",
        "PARITY", "same_method_invariant", "hertz",
        "sampling_rate_gap_to_nominal", 0.167, 10,
        "VERIFIED_METHOD_MATCH",
    )
    add(
        "GPT-05", "GP Tempo", "interpolation_axis",
        "统一赛道轴与 sector 内插值",
        "公开三步法要求 sector 内累计 delta 构造。",
        "F1 TR 使用 401 点轴、linear_continuous_nearest_discrete、smoothing=none。",
        "N/A — explicit method reimplementation", "PASS", "medium", "P2",
        repo_relative(V3_ROOT / "targets/gptempo/model_comparison.csv"),
        "row model_type=universal",
        "端点方法匹配不证明平台真实前端也用完全相同的 401 点或离散插值。",
        "PARITY", "same_method_invariant", "points",
        "published_axis_points", 401.0, 1, "VERIFIED_METHOD_MATCH",
    )
    add(
        "GPT-06", "GP Tempo", "sensitivity",
        "采样/插值/平滑对段内稳定性的影响",
        "平台精确前端处理未完整公开。",
        "v3 只发布单一 401 点、线性、无平滑方案，没有敏感性带。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(V3_ROOT / "targets/gptempo/model_comparison.csv"),
        "sampling_points/interpolation/smoothing columns",
        "段内局部领先/落后可能随处理选择变化。",
        "UNKNOWN", "no_same_scale_truth", "", "", None, None,
        "INTERNAL_ONLY",
    )
    add(
        "GPT-07", "GP Tempo", "official_input_coverage",
        "多事件官方 sector 与 car channel 冻结",
        "平台产品覆盖不可由一个冻结页面推断。",
        "v3 仅 1/70 事件有冻结官方 sector+car input，69/70 NOT_TESTED。",
        "NOT_TESTED", "NOT_TESTED", "high", "P1",
        repo_relative(V3_ROOT / "targets/gptempo/coverage_matrix.csv"),
        "official_sector_times_and_car_channel_available=True rows=1",
        "内部几何覆盖 70 场不等于官方输入覆盖 70 场。",
        "UNKNOWN", "no_same_scale_truth", "events",
        "missing_official_input_events", 69.0, 70,
        "UNKNOWN_PRODUCT_SCOPE",
    )
    add(
        "GPT-08", "GP Tempo", "cross_session",
        "跨 session 比较边界",
        "公开平台可能允许跨 session 浏览。",
        "F1 TR 对跨 session 明示 compound/fuel/weather/track evolution 混杂警告。",
        "NOT_COMPARABLE — method boundary only", "PASS", "medium", "P2",
        gp_v2_path, "$.audited_analysis.cross_session",
        "是暂定方法学优势，不能推断平台没有告警或 F1 TR 数值更准。",
        "UNKNOWN", "f1tr_internal_protocol_only", "", "", None, 50,
        "METHOD_ONLY",
    )
    add(
        "GPT-09", "GP Tempo", "causal_boundary",
        "车手/车辆因果归因边界",
        "平台完整解释规则未冻结。",
        "F1 TR direct_driver_or_vehicle_causal_claim_allowed=false。",
        "NOT_COMPARABLE — fail-close method", "PASS", "medium", "P2",
        gp_v2_path,
        "$.audited_analysis.direct_driver_or_vehicle_causal_claim_allowed",
        "透明边界不能偷换为预测准确率优势。",
        "UNKNOWN", "f1tr_internal_protocol_only", "", "", None, None,
        "METHOD_ONLY",
    )
    add(
        "GPT-10", "GP Tempo", "product_scope",
        "完整交互、赛历、会话和比较能力",
        "本地只冻结 About、脚本标识与一个事件。",
        "F1 TR 只可确认一个事件的方法复刻与端点不变量。",
        "UNKNOWN", "UNKNOWN", "high", "P1", gp_v2_path,
        "$.reference_identity",
        "不能推断完整产品领先、持平或落后。",
        "UNKNOWN", "opaque_product_scope", "", "", None, None,
        "UNKNOWN_PRODUCT_SCOPE",
    )

    seen = set()
    for row in rows:
        if row["row_id"] in seen:
            raise AssertionError(f"重复 matrix row_id：{row['row_id']}")
        seen.add(row["row_id"])
        path = resolve_repo_path(row["evidence_path"])
        if not path.exists():
            raise FileNotFoundError(f"{row['row_id']} 证据路径不存在：{path}")
        if (
            row["same_scale_numeric_gap"]
            not in {
                "UNKNOWN",
                "NOT_TESTED",
                "NOT_COMPARABLE_INTERNAL_PROXY",
                "NOT_COMPARABLE_INTERNAL_PROTOCOL",
                "NOT_COMPARABLE — protocol evidence only",
                "NOT_COMPARABLE — method evidence only",
                "NOT_COMPARABLE — fail-close method",
                "NOT_COMPARABLE — method boundary only",
            }
            and not row["same_scale_numeric_gap"].startswith("N/A")
            and row["comparison_scope"]
            not in {
                "same_event_same_task_same_unit",
                "same_method_invariant",
            }
        ):
            raise AssertionError(
                f"{row['row_id']} 数值差距不是同事件同任务同量纲："
                f"{row['comparison_scope']}"
            )
    return rows


def problem(
    problem_id: str,
    top_risk_rank: int | None,
    platform: str,
    target: str,
    scope: str,
    field_track_year_task_metric_or_gate: str,
    evidence: str,
    root_cause: str,
    user_impact: str,
    scientific_risk: str,
    priority: str,
    proposed_fix: str,
    prerequisites: str,
    effort: str,
    acceptance_test: str,
    fallback: str,
    evidence_path: str,
    evidence_locator: str,
) -> dict[str, Any]:
    return {
        "problem_id": problem_id,
        "top_risk_rank": top_risk_rank,
        "platform": platform,
        "target": target,
        "scope": scope,
        "field_track_year_task_metric_or_gate": field_track_year_task_metric_or_gate,
        "evidence": evidence,
        "root_cause": root_cause,
        "user_impact": user_impact,
        "scientific_risk": scientific_risk,
        "priority": priority,
        "proposed_fix": proposed_fix,
        "prerequisites": prerequisites,
        "effort": effort,
        "acceptance_test": acceptance_test,
        "fallback": fallback,
        "evidence_path": evidence_path,
        "evidence_locator": evidence_locator,
    }


def build_problem_register(recalculation: dict[str, Any]) -> list[dict[str, Any]]:
    p = problem
    delta_australia = next(
        row
        for row in recalculation["deltadata"]["selected_track_metrics"]
        if row["track_key"] == "australian-grand-prix"
    )
    rows = [
        p(
            "P-001", 1, "ALL", "A-C model selection", "validation protocol",
            "track/cluster publication gate",
            "生成器 1078-1126 用全部 outer held-out event 指标选择模型，1153-1185 又报告同一批指标。",
            "把 outer folds 同时当模型选择验证集和最终测试集；没有嵌套验证或时间上后置的 confirmation fold。",
            "用户会把 11 个 DeltaData 专用模型理解为经过独立最终测试。",
            "选择偏差；最终 MAE/P90/max 不再是无偏发布证据。",
            "P0",
            "预注册模型族和阈值；按时间/事件建立 train→selection-validation→untouched-final-test 三层身份，最终折禁止参与任何模型或超参数选择。",
            "冻结事件时间顺序、最少 final-test 事件数和回退规则。",
            "L（5-8 人日）",
            "测试读取 split_registry，断言每个发布模型至少有 2 个未被任何选择逻辑读取的 final-test events；篡改 final-test 标签不得改变 selected_model_type；最终指标单独发布。",
            "在 final-test 完成前把 11 个专用模型降为 audit_only，产品回退 universal。",
            repo_relative(GENERATOR_SOURCE), "lines 1078-1185",
        ),
        p(
            "P-002", 2, "DeltaData", "deltadata", "publication quality gate",
            "australian-grand-prix/selected cluster/max_abs_error",
            (
                f"澳大利亚 selected cluster MAE={delta_australia['mae']:.6f}s、"
                f"P90={delta_australia['p90_abs_error']:.6f}s、"
                f"max={delta_australia['max_abs_error']:.6f}s 仍 "
                "publication_gate_passed=True；universal 自动通过。"
            ),
            "发布门只约束相对 universal 改善和尾部退化，没有任务绝对误差上限。",
            "产品可显示“通过”但单圈调整误差达到 11 秒。",
            "相对较差基线的改善被误称为足够准确。",
            "P0",
            "在 selection-validation 上预注册绝对门，并仅在 untouched final test 上确认；Delta 调整试行门：MAE<=0.25s、P90<=0.50s、max<=1.50s，阈值变更必须在 final test 前冻结。",
            "先完成 P-001；明确调整秒的产品容忍度。",
            "M（3-5 人日）",
            "澳大利亚当前模型必须 FAIL/audit_only；任何 universal 也必须通过相同绝对门；测试覆盖阈值边界。",
            "不满足绝对门时不发布模型选择，只展示账本和 audit_only 内部诊断。",
            repo_relative(V3_ROOT / "targets/deltadata/model_comparison.csv"),
            "track_key=australian-grand-prix; model_type=cluster",
        ),
        p(
            "P-003", 3, "F1pace|DeltaData|FDataAnalysis", "A-C", "target identity",
            "v3 target vs public-platform truth",
            "A 预测本地 2s 交通代理；B 预测 v17 adjusted_pace_s-raw_lap_duration_s；C 对 F1 TR 自身 1,200 轴重采样。",
            "把内部代理稳定性实验放在专业平台目标名下，但缺少逐点平台冻结真值。",
            "用户可能把 0.0087 ratio、0.319s、0.00032 lap fraction 误读成对平台误差。",
            "构念效度失配；内部拟合好不等于平台复刻准确。",
            "P0",
            "机器可读地拆分 target_identity={internal_proxy,public_reference_truth}；报告标题和 UI 明示 INTERNAL_PROXY；只有 public_reference_truth 行可称平台 gap/convergence。",
            "确定每目标可冻结的同场真值与许可边界。",
            "M（3-5 人日）",
            "schema 测试禁止 internal_proxy 指标出现在 same_scale_numeric_gap 或平台准确率摘要；所有 A-C 当前 v3 指标显示“内部稳定性”。",
            "无外部真值时维持 UNKNOWN/NOT_TESTED，不发布差距收敛结论。",
            repo_relative(V3_ROOT / "track_validation_v3.json"),
            "$.targets and $.title",
        ),
        p(
            "P-004", 4, "ALL", "release provenance", "manifest/cache identity",
            "generator code hash; dependency versions; cache config identity",
            "v3 manifest 未记录生成器/库代码哈希和依赖版本；cache_is_valid 只校验 schema、event、source parquet 和缓存文件哈希。",
            "生成和缓存身份没有绑定实现代码与完整配置。",
            "当前源码无法被证明就是冻结产物生成源码；算法改动可能复用旧缓存。",
            "不可复现或陈旧缓存造成静默污染。",
            "P1",
            "manifest 加入 generator/library/frontend git blob+SHA256、Python/依赖 lock；cache identity 加 algorithm_version、config_sha256、feature_schema_sha256。",
            "确定依赖锁文件和缓存 schema 迁移策略。",
            "M（2-4 人日）",
            "修改任一算法常量或 config 后 cache_is_valid 必须失败；正式 manifest 的所有代码/依赖 hash 可重算一致。",
            "身份不完整时强制全量重算且 status=PROVENANCE_INCOMPLETE。",
            repo_relative(GENERATOR_SOURCE), "lines 351-370;2677-2810",
        ),
        p(
            "P-005", 5, "ALL", "model support", "Kish ESS gate",
            "row-level ESS vs event clusters",
            "fit_weight=sample_weight/该事件行数；ESS 按行计算可达数千，但 track fold 训练独立事件仅 2 个。",
            "把相关行当独立支持单位；变量 sample_weight 还使事件总权重并非严格相等。",
            "“Kish ESS 足够”给用户过强稳定性信号。",
            "伪重复导致门槛虚高，置信度偏乐观。",
            "P1",
            "同时发布 event-cluster count、event-level ESS、driver/stint cluster bootstrap；track model 最少训练事件和 final-test 事件单独门控。",
            "先定义独立抽样单位和事件权重。",
            "M（3-5 人日）",
            "track fold 只有 2 个训练事件时，不论行级 ESS 多大均 FAIL；事件总权重在容差 1e-9 内相等；输出 cluster CI。",
            "样本不足时回退 cluster/universal，并标记 low_independent_support。",
            repo_relative(GENERATOR_SOURCE), "lines 927-946",
        ),
        p(
            "P-006", 6, "F1pace", "traffic_ratio", "2025 Qatar external tail",
            "HUL lap7/max_abs_error_pp",
            "外部零重拟合 1,067 单元 MAE=2.238pp、P90=6.848pp、max=72.309pp；参考颜色读数 max error 1.5pp。",
            "连续比例模型在极端/缺失/图像单元上存在长尾，发布门只看 MAE/P90/分类准确率。",
            "热图局部可严重错色，99.25% 分类准确率掩盖连续数值失败。",
            "尾部误差可能改变逐圈解释。",
            "P1",
            "加入 max/conditional-tail 门、逐车手/圈型残差账本与图像读取不确定带；复核 HUL lap7、HAM lap38、COL lap1。",
            "保留参考单元定位与颜色读数置信区间。",
            "M（2-4 人日）",
            "零重拟合 final event 的 max<=15pp、P99<=15pp，且所有 >10pp 单元有可审计原因；阈值须在新 final event 前冻结。",
            "尾部门失败时仅发布 33% 分类，不发布连续比例色值。",
            repo_relative(
                next(
                    (REPO_ROOT / "frontend/public/data/reference-analysis-lab/v2").glob(
                        "run=*/qatar_external_cells.csv"
                    )
                )
            ),
            "sort abs_error_pp desc; HUL lap7",
        ),
        p(
            "P-007", 7, "F1TelemetryData", "qualifying_q1_q2_q3_phase",
            "reference truth identity", "2025 Abu Dhabi/status",
            "chart_reference_matrix 把 Q phase 标为 REFERENCE_INFORMED_SAME_EVENT/public_reference_available=True；model_comparison 同任务为 NOT_TESTED。",
            "is_public_reference 分支先于 Q phase 缺口分支，事件级有参考被错误扩张为任务级有数值参考。",
            "UI/统计可能把缺真值任务算成已参考验证。",
            "NOT_TESTED 被污染，优势率/覆盖率可能虚增。",
            "P1",
            "按 event×task 定义 public_numeric_truth_available；Q phase 在没有逐阶段真值时始终 NOT_TESTED。",
            "梳理 7 个 chart task 的独立证据清单。",
            "S（1-2 人日）",
            "2025 Abu Q phase 两张表状态一致为 NOT_TESTED；自动测试禁止 event-level boolean 覆盖 task-level truth。",
            "暂时从 reference_matrix 的 public rows 计数中排除 Q phase。",
            repo_relative(
                V3_ROOT / "targets/f1telemetrydata/chart_reference_matrix.csv"
            ),
            "event=2025-24-abu-dhabi-grand-prix; task=qualifying_q1_q2_q3_phase",
        ),
        p(
            "P-008", 8, "ALL", "frontend", "layer isolation and holdout wording",
            "state.layer/renderComparison/renderCoverage",
            "layer 仅切换 method card active；comparison/coverage/registry 使用同一数据。UI 称专用模型在“完全留出的年份上稳定改善/holdout gate passed”。",
            "视觉层与 audited 层没有独立数据源，且把 selection fold 写成最终 holdout。",
            "用户以为切换后看到了不同证据层，并高估验证强度。",
            "语义混排和验证身份误导。",
            "P1",
            "给每层独立 data_source/metric_role/legend；内部代理与外部真值分表；改文案为“selection-validation gate passed; final confirmation NOT_TESTED”。",
            "先完成 P-001/P-003 的 schema 身份。",
            "M（3-5 人日）",
            "E2E 切层后至少一个 dataset id 和表行集合改变；快照断言不再出现“完全留出/holdout passed”直到 final-test PASS。",
            "在分层完成前移除 layer 控件，固定显示 audited_analysis。",
            repo_relative(FRONTEND_SOURCE), "lines 331-350;375-404;548-560",
        ),
        p(
            "P-009", 9, "ALL", "model uncertainty", "2-3 event track metrics",
            "confidence intervals and fold stability",
            "多数赛道只有 2-3 个 test events；fold improvement 2/3 即可通过，未发布 cluster bootstrap/CI。",
            "点估计发布门没有表达事件间方差和小样本不确定性。",
            "用户看见精确改善百分比却不知道其区间可能跨过零。",
            "模型选择不稳定、最坏尾部不可预期。",
            "P1",
            "按事件 cluster bootstrap 或时间块 bootstrap 发布 MAE/P90 improvement CI；最终折只做确认，不参与 CI 阈值选择。",
            "P-001 的数据身份；至少 3 个 selection events。",
            "M（3-5 人日）",
            "专用模型只有在改善 90% CI 下界>0、最坏折不劣化且 final-test 过门时发布；不足则 audit_only。",
            "样本不足时只发布点估计+WIDE_UNCERTAINTY，不选专用模型。",
            repo_relative(V3_ROOT / "targets/deltadata/model_comparison.csv"),
            "columns=test_events,fold_improvement_share",
        ),
        p(
            "P-010", 10, "F1pace|DeltaData|FDataAnalysis", "registry semantics",
            "reference use identity", "reference_informed flag",
            "A-C registry 写 reference_informed=False，但方法、阈值和特征设计已看过公开参考；blind_holdout_claim_allowed=False。",
            "单一 boolean 混合了“参考用于设计”“参考用作拟合目标”“最终确认盲测”三种身份。",
            "用户会把 false 理解为完全盲设计。",
            "参考泄漏语义不清，难以审计选择偏差。",
            "P1",
            "拆为 method_design_reference_informed、reference_used_as_fit_target、selection_reference_seen、confirmation_blind 四字段。",
            "逐目标建立证据使用登记。",
            "S（1-2 人日）",
            "A-C 的 method_design_reference_informed=True；只有未见过的 final confirmation 可 confirmation_blind=True。",
            "无法拆分前统一标记 REFERENCE_INFORMED_NOT_BLIND。",
            repo_relative(GENERATOR_SOURCE), "lines 1149-1150;1945-1946",
        ),
        p(
            "P-011", None, "ALL", "run identity", "frozen_at chronology",
            "config frozen_at vs manifest created_at",
            "config frozen_at=2026-07-31T23:59+08，但 manifest created_at≈2026-07-31T15:06+08，冻结时间晚于产物创建约 8.88 小时。",
            "run_id/frozen_at 是预设标签，不是实际生成时间的单调记录。",
            "用户无法用时间字段证明“先冻结、后比较”。",
            "时间身份不能支持防泄漏声明。",
            "P1",
            "记录 observed_input_frozen_at、run_started_at、run_finished_at，并断言 input_frozen<=started<=finished。",
            "明确冻结动作的实际时间源。",
            "S（1 人日）",
            "manifest 时间单调断言通过；未来时间标签导致构建失败。",
            "时间异常时保留 hash 身份但 status=TIME_IDENTITY_UNPROVEN。",
            repo_relative(CONFIG), "$.frozen_at",
        ),
        p(
            "P-012", None, "F1TelemetryData", "task registry", "task granularity/max",
            "7 tasks vs 24 track registry rows; team throttle max",
            "model_comparison 有 7 个任务；registry 仅 24 个赛道通用行。team throttle v3 comparison 缺 max，v2 有 3.084442pp。",
            "以赛道为唯一 registry 粒度，且 v3 手工摘取指标字段不完整。",
            "任务级 PASS/NOT_TESTED 和尾部无法完整展示。",
            "覆盖与尾部审计失真。",
            "P1",
            "registry 主键改为 target×track×chart_task；从 v2 validation 程序化映射 MAE/max。",
            "修复 P-007 的 task truth identity。",
            "S（1-2 人日）",
            "24×7=168 个任务身份完整；team throttle max_abs_error=3.084442pp 可重算。",
            "未迁移前以 model_comparison 为唯一任务真相源。",
            repo_relative(V3_ROOT / "targets/f1telemetrydata/model_comparison.csv"),
            "chart_task=qualifying_team_throttle",
        ),
        p(
            "P-013", None, "F1pace|DeltaData", "data gap cards", "status distribution",
            "data_gap_audit.md status distribution",
            "A/B coverage 没有 status/q1/layout 字段，通用生成逻辑输出“状态分布：{}”。",
            "data-gap 摘要按少数字段名硬编码，而非目标专属缺口指标。",
            "用户看不到排除率、missing rate 和尾部最坏赛道。",
            "数据缺口被空摘要掩盖。",
            "P2",
            "按 target schema 生成 gap cards：missing、coverage loss、ledger closure、worst exclusion tracks。",
            "定义每目标 gap summary schema。",
            "S（1-2 人日）",
            "A/B data_gap 不得出现空对象；数字与 coverage/ledger 重算一致。",
            "空 schema 时显示 NOT_AVAILABLE，不显示 {}。",
            repo_relative(V3_ROOT / "targets/f1pace/data_gap_audit.md"),
            "状态分布",
        ),
        p(
            "P-014", None, "FDataAnalysis|F1TelemetryData|GP Tempo",
            "sampling identity", "formal manifest identity",
            "formal sampling evidence",
            "expanded-v4 event median 约 7.25-8.13Hz 只能从 runtime-cache segments 重算，v3 正式 coverage/manifest 未发布。",
            "采样身份留在缓存或 v2 报告，没有进入 v3 目标 manifest schema。",
            "用户无法从正式产物解释控制占比和段内曲线差异。",
            "采样差异导致结果不可比。",
            "P1",
            "正式 coverage 加 source_sample_hz、resampled_hz、interpolation、smoothing、missing_rate。",
            "冻结采样统计定义。",
            "S（1-2 人日）",
            "70 个事件均有采样身份且与 segments 重算误差<1e-9；无身份时 NOT_TESTED。",
            "只在 audit 报告披露 runtime-cache 推导，并明确非正式证据。",
            repo_relative(V3_ROOT / "targets/fdataanalysis/coverage_matrix.csv"),
            "sampling columns absent",
        ),
        p(
            "P-015", None, "GP Tempo", "segment validation",
            "point truth identity", "sector endpoint vs segment truth",
            "270 个端点 max error=0 是 affine sector 构造约束；public point truth events=0，segment_mae=null。",
            "端点不变量容易被误读为整段曲线准确。",
            "用户可能把 0 误差扩张到 sector 内。",
            "伪造段内精度的风险。",
            "P1",
            "UI/报告把 endpoint invariant 与 segment accuracy 分栏；获取许可允许的逐点真值后才算 segment MAE。",
            "至少 2 个独立事件逐点冻结真值。",
            "M（数据依赖，2-5 人日）",
            "无 point truth 时 segment status 恒为 NOT_TESTED，任何非 null MAE 触发测试失败。",
            "只发布端点约束和敏感性带。",
            repo_relative(V3_ROOT / "targets/gptempo/model_comparison.csv"),
            "segment_mae_status",
        ),
        p(
            "P-016", None, "FDataAnalysis", "same-event truth",
            "event identity", "2026 Hungary reference vs 2025 Abu pilot",
            "冻结参考图片与 F1 TR pilot 不是同事件/会话；v3 指标对自身 profile。",
            "缺少可许可的同事件逐点/表值转录。",
            "无法回答对 FDataAnalysis 的数值差距。",
            "平台差距只能 UNKNOWN。",
            "P1",
            "冻结至少 3 个同事件公开图表，预注册距离轴标定和读取不确定度，保留一个 final event。",
            "许可、URL、抓取时间、图像 hash、坐标标定。",
            "L（数据依赖，5-8 人日）",
            "至少 2 个 selection events+1 个 untouched final event；同距离点 MAE/P90/max 含读图误差带。",
            "继续只报内部稳定性，不报平台 gap。",
            repo_relative(
                next(
                    (REPO_ROOT / "frontend/public/data/reference-analysis-lab/v2").glob(
                        "run=*/report.json"
                    )
                )
            ),
            "$.reference_identity.images",
        ),
        p(
            "P-017", None, "DeltaData", "method fidelity",
            "opaque parameter identity",
            "manual labels/clean threshold/season weights",
            "精确阈值、人工标签、回归代码和赛季权重闭源；80% 是 F1 TR 工程选择。",
            "只能复刻公开语义，不能一对一重建。",
            "METHOD_EQUIVALENT 容易被误读为算法相同。",
            "方法保真度未知。",
            "P1",
            "UI 固定显示 SKIPPED_OPAQUE_METHOD；用多事件同场输出做黑盒结果验证而非宣称代码等价。",
            "新增公开同场输出冻结。",
            "M/L（数据依赖）",
            "所有算法等价文案包含 opaque boundary；至少 5 个独立同场事件报告结果误差。",
            "维持 UNKNOWN，不尝试猜测闭源参数。",
            repo_relative(
                next(
                    (REPO_ROOT / "frontend/public/data/reference-analysis-lab/v2").glob(
                        "run=*/deltadata_reverse_engineered_v2.json"
                    )
                )
            ),
            "$.method_card.not_identifiable",
        ),
        p(
            "P-018", None, "ALL", "product comparison", "complete product scope",
            "local frozen subset vs full public product",
            "本地只冻结有限页面、图片和方法卡，没有完整平台功能清单、历史覆盖、交互性能或更新 SLA。",
            "证据范围是样本，不是完整产品审计。",
            "用户可能把子任务计数当成总体产品排名。",
            "外部有效性与范围越界。",
            "P2",
            "每平台建立 capability inventory，逐项标注 public evidence/local freeze/inference；禁止总复刻率。",
            "只读冻结公开页面并遵守许可。",
            "M（3-5 人日）",
            "每个产品结论都有 inference_scope；未冻结能力一律 UNKNOWN，不进入优势率。",
            "保留当前矩阵的 UNKNOWN，不做总体排名。",
            repo_relative(V2_INDEX), "$.targets",
        ),
    ]
    if sorted(row["top_risk_rank"] for row in rows if row["top_risk_rank"]) != list(
        range(1, 11)
    ):
        raise AssertionError("Top 10 风险排序必须完整为 1..10")
    for row in rows:
        if not row["evidence"] or not row["acceptance_test"]:
            raise AssertionError(f"{row['problem_id']} 缺 evidence/acceptance_test")
        if not resolve_repo_path(row["evidence_path"]).exists():
            raise FileNotFoundError(row["evidence_path"])
    return rows


def backlog_item(
    backlog_id: str,
    category: str,
    title: str,
    priority: str,
    dependency_order: int,
    dependencies: str,
    prerequisites: str,
    expected_effort: str,
    acceptance_threshold: str,
    verifiable_benefit: str,
    code_changes: str,
    test_changes: str,
    artifacts: str,
    documentation: str,
    fallback: str,
    top5_rank: int | None = None,
) -> dict[str, Any]:
    return {
        "backlog_id": backlog_id,
        "category": category,
        "title": title,
        "priority": priority,
        "top5_rank": top5_rank,
        "dependency_order": dependency_order,
        "dependencies": dependencies,
        "prerequisites": prerequisites,
        "expected_effort": expected_effort,
        "acceptance_threshold": acceptance_threshold,
        "verifiable_benefit": verifiable_benefit,
        "code_changes": code_changes,
        "test_changes": test_changes,
        "artifacts": artifacts,
        "documentation": documentation,
        "fallback": fallback,
    }


def build_backlog() -> list[dict[str, Any]]:
    b = backlog_item
    rows = [
        b(
            "B-001", "算法改进", "建立 selection-validation 与 untouched final-test 三层协议",
            "P0", 1, "无", "冻结时间顺序、final 事件数、回退规则",
            "L（5-8 人日）",
            "每个发布模型至少 2 个 final events；final 标签不参与选择；最终 MAE/P90/max 单独输出。",
            "消除选择复用偏差；11 个 DeltaData 专用模型获得可验证确认或自动回退。",
            "research/run_reference_analysis_lab_track_validation_v4.py 或新增 protocol 模块；不回写 v3。",
            "新增 split disjointness、final-label invariance、selection perturbation tests。",
            "split_registry_v4.csv、selection_registry.json、final_confirmation.json。",
            "docs/algorithm-models.md、docs/implementation-results.md、docs/progress.md。",
            "未完成前专用模型 audit_only，保留 universal。",
            1,
        ),
        b(
            "B-002", "算法改进", "加入绝对误差门、尾部门和事件簇不确定区间",
            "P0", 2, "B-001", "产品容忍度与 final-test 身份",
            "M（3-5 人日）",
            "Delta 试行 MAE<=0.25s、P90<=0.50s、max<=1.50s；改善 90% cluster-CI 下界>0；阈值在 final test 前冻结。",
            "当前 Australia 11.168s 模型自动 FAIL；避免相对坏基线的伪发布。",
            "candidate_publication_decision v4、event-cluster bootstrap。",
            "绝对门边界、Australia 回归、CI 可重复性测试。",
            "absolute_gate_registry.json、uncertainty.csv、tail_ledger.csv。",
            "docs/algorithm-models.md、docs/implementation-results.md。",
            "门失败则 audit_only 且回退 universal/no-model。",
            2,
        ),
        b(
            "B-003", "当前冻结数据即可完成", "补齐代码/配置/依赖/缓存/时间身份",
            "P1", 1, "无", "确定 cache schema v4 与依赖锁",
            "M（2-4 人日）",
            "所有生成代码、config、lock hash 可重算；任一算法/config 改动强制 cache miss；frozen<=started<=finished。",
            "把“当前源码产生当前产物”从推断变为可验证事实。",
            "manifest writer、cache_is_valid、run clock identity。",
            "hash graph、cachebuster、future-time rejection tests。",
            "provenance.json、cache identity v4、environment-lock.txt。",
            "docs/implementation-results.md、docs/progress.md。",
            "身份不完整时全量重算并标 PROVENANCE_INCOMPLETE。",
            3,
        ),
        b(
            "B-004", "需新增公开数据冻结", "扩充五目标同场数值真值并保留最终事件",
            "P1", 3, "B-001,B-003", "许可允许的 URL/图片/表值冻结",
            "L（5-10 人日，受公开数据可得性约束）",
            "每个可量化目标至少 3 个独立事件、2 条赛道；至少 1 个事件 untouched final；URL/抓取时间/page id/hash/读图误差齐全。",
            "把 FData/GP 段内/Q phase 的 UNKNOWN/NOT_TESTED 转为可审计同量纲指标。",
            "只新增 reference freezer、transcription schema 和 reader uncertainty 模块。",
            "hash/许可/坐标标定、零重拟合 final-event 测试。",
            "reference_inventory.json、frozen truth tables、attempts.jsonl。",
            "method cards、data-gap audit、docs/algorithm-models.md。",
            "三次实质不同尝试仍无真值则保持 UNKNOWN/NOT_TESTED。",
            4,
        ),
        b(
            "B-005", "可视化/产品改进", "真正分离 visual_replication 与 audited_analysis 并修正文案",
            "P1", 4, "B-001,B-003", "定义每层独立 dataset id/metric role",
            "M（3-5 人日）",
            "切层后数据源和表行集合确实变化；internal_proxy/外部 truth 标签始终可见；无 final PASS 时不出现 holdout passed。",
            "消除证据混排和验证强度误导，用户能区分“像平台”与“可审计”。",
            "frontend/src/track-validation-v4.js、数据 schema；不修改冻结 v3 页面。",
            "E2E layer isolation、文案禁词、任务状态渲染测试。",
            "两层独立 public JSON、browser acceptance manifest。",
            "用户指南、docs/implementation-results.md。",
            "无法分层时移除 layer 控件，只显示 audited_analysis。",
            5,
        ),
        b(
            "B-006", "当前冻结数据即可完成", "修复 D 目标 Q phase 状态与任务级 registry",
            "P1", 2, "B-003", "7 个 task 的 evidence inventory",
            "S（1-2 人日）",
            "2025 Abu Q phase 两表均 NOT_TESTED；registry 具有 24×7 个 task identities；team throttle max=3.084442pp。",
            "防止 NOT_TESTED 被计作已参考，恢复任务级尾部可见性。",
            "v4 generator task truth matrix；不回写 v3。",
            "event×task truth、registry cardinality、metric completeness tests。",
            "chart_reference_matrix_v4.csv、task_model_registry.json。",
            "docs/implementation-results.md。",
            "v3 审计层继续覆盖矛盾并从计数排除。",
        ),
        b(
            "B-007", "当前冻结数据即可完成", "发布正式采样频率、缺失率与处理链",
            "P1", 2, "B-003", "采样统计定义",
            "S（1-2 人日）",
            "70/70 event 有 source/resampled Hz、missing、interpolation、smoothing；与缓存重算误差<1e-9。",
            "控制占比与段内曲线差异可被追踪到处理身份。",
            "coverage schema v4 和 sample identity collector。",
            "70-event sampling identity test。",
            "sampling_registry.csv。",
            "docs/database.md（若 schema 变更）、docs/algorithm-models.md。",
            "缺采样身份的任务 NOT_TESTED。",
        ),
        b(
            "B-008", "算法改进", "改为事件级支持门与事件簇 bootstrap",
            "P1", 2, "B-001", "独立抽样单位定义",
            "M（3-5 人日）",
            "track fold 独立训练事件<3 时 FAIL；事件总权重相等；发布 event-level ESS 与 90% CI。",
            "阻止行级伪重复把 2 个事件包装成数千 ESS。",
            "weighting/ESS/cluster bootstrap 模块。",
            "event weight equality、low-cluster failure tests。",
            "support_registry.json、uncertainty.csv。",
            "docs/algorithm-models.md。",
            "支持不足回退 cluster/universal。",
        ),
        b(
            "B-009", "算法改进", "F1pace 连续比例尾部专门校准",
            "P1", 3, "B-001,B-004", "新外部 final event 和读图不确定度",
            "M（2-4 人日）",
            "预注册 final event max<=15pp、P99<=15pp；所有 >10pp 单元有原因账本。",
            "在不牺牲 33% 分类的前提下压低 72.3pp 极端误差。",
            "tail calibration/abstention，禁止对 final event 调参。",
            "HUL/HAM/COL 回归、final zero-refit tests。",
            "tail_ledger.csv、reader_uncertainty.json。",
            "method card、implementation results。",
            "尾部失败时只发布分类，不发布连续比例。",
        ),
        b(
            "B-010", "需新增公开数据冻结", "获取 FData 同场距离轴和 GP Tempo 段内逐点真值",
            "P1", 3, "B-003", "许可和可机器读取的公开真值",
            "L（5-8 人日，可能不可得）",
            "FData/GP 各>=3 事件；逐点真值带轴标定/读取误差；1 个 final event。",
            "首次允许报告真正的逐点 MAE/P90/max，而非端点或自一致性。",
            "reference freezer/axis calibrator。",
            "same-event identity、axis monotonicity、reader uncertainty tests。",
            "point_truth.csv、calibration.json。",
            "method cards。",
            "不可得时永远 UNKNOWN/NOT_TESTED，不估算 MAE。",
        ),
        b(
            "B-011", "可视化/产品改进", "目标身份、代理身份和来源角色显式化",
            "P1", 4, "B-003", "target identity schema",
            "S（1-2 人日）",
            "每个指标显示 public_reference/local_frozen/internal_proxy/official/unofficial；internal_proxy 不进入平台 gap。",
            "避免用户把 OpenF1、图片转录和 FIA 官方数据混为一谈。",
            "frontend badges + schema。",
            "role rendering/forbidden inference tests。",
            "source_role_registry.json。",
            "用户指南。",
            "无法确定来源角色则显示 UNKNOWN SOURCE。",
        ),
        b(
            "B-012", "可视化/产品改进", "任务级最坏尾部、排除账本和数据缺口卡",
            "P2", 5, "B-006,B-007", "任务级 registry",
            "M（2-3 人日）",
            "每任务显示 MAE/P90/max、最坏事件/圈、排除率、Kish/event ESS；A/B 不再显示 {}。",
            "从平均值导向尾部和支持证据，快速发现 Australia/HUL 等失败。",
            "workbench components。",
            "worst-tail sort、ledger closure、empty-card tests。",
            "browser acceptance screenshots。",
            "用户指南、implementation results。",
            "字段缺失时显示 NOT_AVAILABLE 而不是空对象。",
        ),
        b(
            "B-013", "当前冻结数据即可完成", "拆分 reference-informed 身份",
            "P1", 2, "B-003", "证据使用登记",
            "S（1-2 人日）",
            "四字段身份完整；A-C design_reference_informed=True；只有未见 final 可 confirmation_blind=True。",
            "可追溯设计参考、拟合参考与真正盲测的差别。",
            "registry schema v4。",
            "semantic identity tests。",
            "reference_use_registry.json。",
            "algorithm-models。",
            "统一标 REFERENCE_INFORMED_NOT_BLIND。",
        ),
        b(
            "B-014", "需新增公开数据冻结", "扩充 F1TelemetryData Q 阶段与多事件图表真值",
            "P1", 3, "B-003,B-006", "可许可公开图表与逐阶段数值",
            "M/L（4-8 人日）",
            "至少 3 事件、Q1/Q2/Q3 各有独立数值真值；1 final event；控制/油门/维修区任务均含 max。",
            "验证 2.639pp/1.196pp 误差是否跨事件稳定，消除 Q phase NOT_TESTED。",
            "reference freezer/task transcriber。",
            "phase identity、same-event、final isolation tests。",
            "q_phase_truth.csv、multi_event_chart_truth.json。",
            "method card。",
            "无逐阶段真值则保持 NOT_TESTED。",
        ),
        b(
            "B-015", "当前冻结数据即可完成", "完整产品能力清单与推断边界",
            "P2", 1, "无", "本地五份 method card 与 URL 清单",
            "S（1-2 人日）",
            "每个平台能力均标 public_reference/local_freeze/inference；未冻结项 UNKNOWN；不生成总复刻率。",
            "防止把本审计 57 个子项误当完整产品排名。",
            "仅追加 inventory generator。",
            "UNKNOWN exclusion、scope label tests。",
            "capability_inventory.csv/json。",
            "本审计报告附录。",
            "证据不足则不补全能力清单。",
        ),
    ]
    categories = Counter(row["category"] for row in rows)
    required = {
        "当前冻结数据即可完成",
        "需新增公开数据冻结",
        "算法改进",
        "可视化/产品改进",
    }
    if set(categories) != required:
        raise AssertionError(f"backlog 分类不完整：{categories}")
    if sorted(row["top5_rank"] for row in rows if row["top5_rank"]) != list(
        range(1, 6)
    ):
        raise AssertionError("Top 5 改进排序必须完整为 1..5")
    return rows


def count_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_platform: dict[str, Counter[str]] = defaultdict(Counter)
    by_status: Counter[str] = Counter()
    for row in rows:
        by_platform[row["platform"]][row["relative_position"]] += 1
        by_status[row["status"]] += 1
    platform_counts: dict[str, dict[str, int]] = {}
    for platform in (
        "F1pace",
        "DeltaData",
        "FDataAnalysis",
        "F1TelemetryData",
        "GP Tempo",
    ):
        counts = by_platform[platform]
        platform_counts[platform] = {
            "leading": counts["LEADING"],
            "parity": counts["PARITY"],
            "behind": counts["BEHIND"],
            "unknown": counts["UNKNOWN"],
            "total": sum(counts.values()),
        }
    totals = Counter(row["relative_position"] for row in rows)
    return {
        "by_platform": platform_counts,
        "overall": {
            "leading": totals["LEADING"],
            "parity": totals["PARITY"],
            "behind": totals["BEHIND"],
            "unknown": totals["UNKNOWN"],
            "total": len(rows),
        },
        "by_status": dict(sorted(by_status.items())),
        "verified_accuracy_leads": sum(
            row["relative_position"] == "LEADING"
            and row["finding_category"] == "VERIFIED_SAME_SCALE"
            for row in rows
        ),
        "advantage_rate_denominator": sum(
            row["status"] not in {"UNKNOWN", "NOT_TESTED"} for row in rows
        ),
        "advantage_rate_numerator": sum(
            bool(row["counts_toward_advantage_rate"]) for row in rows
        ),
    }


def render_counts_table(counts: dict[str, Any]) -> str:
    lines = [
        "| 平台 | 领先 | 持平 | 落后 | 未知 | 合计 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for platform, row in counts["by_platform"].items():
        lines.append(
            f"| {platform} | {row['leading']} | {row['parity']} | "
            f"{row['behind']} | {row['unknown']} | {row['total']} |"
        )
    overall = counts["overall"]
    lines.append(
        f"| **合计** | **{overall['leading']}** | **{overall['parity']}** | "
        f"**{overall['behind']}** | **{overall['unknown']}** | "
        f"**{overall['total']}** |"
    )
    return "\n".join(lines)


def render_roadmap(backlog: list[dict[str, Any]]) -> str:
    categories = (
        "当前冻结数据即可完成",
        "需新增公开数据冻结",
        "算法改进",
        "可视化/产品改进",
    )
    lines = [
        "# F1 TR v3 专业平台差距改进路线图",
        "",
        "本路线图不修改冻结 v3；所有改进进入后续 append-only run。依赖顺序优先于类别顺序。",
        "",
        "## 依赖主链",
        "",
        "1. B-001 冻结三层验证协议。",
        "2. B-002 在 selection-validation 预注册绝对门和不确定区间，并在 untouched final test 确认。",
        "3. B-003 补齐代码、缓存、依赖和时间身份。",
        "4. B-004 扩充同场公开真值，同时永久保留至少一个 final event。",
        "5. B-005 在产品层真正分离 visual_replication 与 audited_analysis。",
        "",
    ]
    for category in categories:
        lines.extend([f"## {category}", ""])
        for row in sorted(
            (item for item in backlog if item["category"] == category),
            key=lambda item: (item["dependency_order"], item["backlog_id"]),
        ):
            lines.extend(
                [
                    f"### {row['backlog_id']} {row['title']}",
                    "",
                    f"- 优先级/顺序：{row['priority']} / {row['dependency_order']}",
                    f"- 依赖：{row['dependencies']}",
                    f"- 工作量：{row['expected_effort']}",
                    f"- 验收阈值：{row['acceptance_threshold']}",
                    f"- 可验证收益：{row['verifiable_benefit']}",
                    f"- 代码：{row['code_changes']}",
                    f"- 测试：{row['test_changes']}",
                    f"- 产物：{row['artifacts']}",
                    f"- 文档：{row['documentation']}",
                    f"- 回退：{row['fallback']}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def render_report(
    recalculation: dict[str, Any],
    matrix: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    backlog: list[dict[str, Any]],
    counts: dict[str, Any],
) -> str:
    raw = recalculation["raw_race_coverage"]
    f1 = recalculation["f1pace"]
    delta = recalculation["deltadata"]
    fdata = recalculation["fdataanalysis"]
    tele = recalculation["f1telemetrydata"]
    gp = recalculation["gptempo"]
    top10 = sorted(
        (row for row in problems if row["top_risk_rank"]),
        key=lambda row: row["top_risk_rank"],
    )
    top5 = sorted(
        (row for row in backlog if row["top5_rank"]),
        key=lambda row: row["top5_rank"],
    )
    delta_worst = delta["selected_model_metrics"]["worst_max"][0]
    delta_australia = next(
        row
        for row in delta["selected_track_metrics"]
        if row["track_key"] == "australian-grand-prix"
    )
    f1_external = f1["v2_qatar_external_validation"]
    lines = [
        "# F1 TR v3 数据与算法专业平台差距审计",
        "",
        f"- 审计运行：`{RUN_ID}`",
        "- 审计性质：append-only、离线、只读复核；未修改任何 v1/v2/v3 或 Race Dossier v14-v17 产物。",
        f"- 冻结 v3：`{repo_relative(V3_MANIFEST)}`，SHA-256 `{sha256_file(V3_MANIFEST)}`。",
        f"- Race Dossier v17：`{repo_relative(V17_MANIFEST)}`，SHA-256 `{sha256_file(V17_MANIFEST)}`。",
        f"- 数据重算：{raw['events']} events / 24 tracks / {raw['raw_points']:,} Race raw points；invalid/skipped source files={raw['invalid_source_files']}/{raw['skipped_source_files']}。",
        "- 网络：未调用。五个目标的 URL、抓取时间、页面标识与哈希已有本地冻结证据，继续联网不会改善当前同量纲真值边界。",
        "",
        "## 执行摘要",
        "",
        "结论不是“v3 已追平五个平台”。57 个可审计子项中，**已证实预测准确率领先为 0**。"
        f"按矩阵可重算为：领先 {counts['overall']['leading']}、持平 {counts['overall']['parity']}、"
        f"落后 {counts['overall']['behind']}、未知 {counts['overall']['unknown']}。"
        "持平主要是同场表值、公开阈值或端点不变量；它们不能外推完整产品持平。",
        "",
        "最关键的发布结论是：v3 对 A-C 的大部分误差是 **F1 TR 内部代理/自一致性**，不是对 F1pace、DeltaData、FDataAnalysis 的公开逐点真值误差。"
        "DeltaData 的 11 个 track/cluster 选择还使用了同一批 outer fold 结果完成选择和报告，缺少 untouched final test；因此只能视为 selection-validation 结果。",
        "",
        "已证实的外部数值差距集中在：F1pace Qatar 连续交通比例 "
        f"MAE={f1_external['mae_pp']:.3f}pp/P90={f1_external['p90_abs_error_pp']:.3f}pp/"
        f"max={f1_external['max_abs_error_pp']:.3f}pp；DeltaData 3 行 H2H "
        f"MAE={delta['v2_reference_benchmark']['metrics']['mae_pp']:.5f}pp；"
        "F1TelemetryData 控制区段 2.639pp、车队油门 1.196pp、pit lane 0.065808s。"
        "FDataAnalysis 没有同事件数值真值；GP Tempo 只有 270 个构造端点零误差，段内 MAE 必须保持 NOT_TESTED。",
        "",
        render_counts_table(counts),
        "",
        "计数规则：每行只取一个 `relative_position`。`UNKNOWN/NOT_TESTED` 永不进入优势率；"
        "透明度、账本和 fail-close 只列为方法学证据，不被偷换成预测准确率优势。"
        "本报告不生成跨任务总复刻率。",
        "",
        "## 已证实优势",
        "",
        "没有已证实的预测准确率领先项（0 项）。现有公开真值只允许确认若干持平和落后，"
        "不能证明五个平台在这些任务上更差。F1 TR 的哈希链、排除账本、不可识别边界和 PAC/OVR=null "
        "属于可审计工程能力；因为无法证明对方完整产品没有同等能力，所以不计为领先。",
        "",
        "## 暂定/方法学优势",
        "",
        "- 70 场、24 条赛道的内部代理覆盖，以及按赛道/事件的 coverage、split、exclusion ledger，支持定位而不是只给平均分。",
        "- F1pace 的 Abu/Qatar 外部参考从 v3 训练排除；GP Tempo 对跨 session 混杂和直接车手/车辆因果结论 fail-close。",
        "- FDataAnalysis 目标用可观测布局不连续代理禁止 Dutch track model，避免把布局变化误当跨年稳定。",
        "- Race Dossier v17 保持车辆基线→车手交付→策略/进站/位置秒数→残差框架，并保持 PAC/OVR 为 null。",
        "",
        "这些是**暂定方法学优势**：本地冻结证据只描述 F1 TR，自身透明度不能证明平台不透明，更不能证明预测更准。",
        "",
        "## 已证实差距",
        "",
        f"1. F1pace 2025 Qatar 1,067 个外部零重拟合单元连续比例长尾明显："
        f"MAE {f1_external['mae_pp']:.6f}pp、P90 {f1_external['p90_abs_error_pp']:.6f}pp、"
        f"max {f1_external['max_abs_error_pp']:.6f}pp；颜色读取本身 max 1.5pp，无法解释 72.3pp 最大残差。",
        "2. DeltaData 同场 H2H 仅 3 行且是 reference-informed，MAE 0.09134pp；只有 1 行通过可比门，2 行仍是 audit_only。公开的逐圈人工复核能力尚未实现，F1 TR 以 Huber 自动降权替代。",
        "3. F1TelemetryData 同场：控制区段 MAE/max=2.639122/6.157325pp；车队油门=1.195668/3.084442pp；pit lane=0.065808/0.363s。",
        "4. v3 发布协议缺 untouched final test；相同 outer folds 同时决定选择和报告，不能称最终独立确认。",
        f"5. DeltaData {delta_worst['meeting']} selected {delta_worst['model_type']} 的内部 max error="
        f"{delta_worst['max_abs_error']:.6f}s；相对发布门没有绝对质量上限。",
        "6. D 目标 Q phase 的 chart_reference_matrix 与 model_comparison 状态冲突；E 目标端点 0 误差是构造不变量，不是段内精度。",
        "",
        "## 因闭源/缺真值未知",
        "",
        "- F1pace 完整交通算法、逐点配对、缺失积分和产品全量覆盖。",
        "- DeltaData 精确 clean-air 阈值、人工标签、回归代码、赛季权重，以及是否有额外非公开燃油信息。",
        "- FDataAnalysis 同事件逐点距离轴真值、采样/平滑/阈值和完整产品覆盖。",
        "- F1TelemetryData Q1/Q2/Q3 数值真值及完整阈值实现；69/70 事件没有本地同场公开参考。",
        "- GP Tempo sector 内逐点真值和除 2025 Abu Dhabi 外的冻结官方输入；69/70 事件 NOT_TESTED。",
        "- 五个平台的完整产品功能、更新 SLA、历史覆盖、交互质量与内部验证流程。局部截图/页面不能支持总体产品排名。",
        "",
        "真实燃油、SOC、胎温、胎压、物理磨损、设定、损伤、动力模式和车队指令均不在公开 telemetry 中；"
        "相关结论只能是条件代理。`classification_proxy` 不是 FIA final classification。",
        "",
        "## 五个平台逐项审计",
        "",
        "### F1pace",
        "",
        "- 同场 race pace 表值 20/20 在 0.001s 内，最大差 0.000491s；这是表值复算持平，不是因果方法持平。",
        "- 2s 与 33% 语义一致，Qatar 分类准确率 99.250%；但连续比例 max 72.309pp，必须同时展示尾部。",
        "- v3 universal 24/24；内部代理 mean MAE "
        f"{f1['selected_model_metrics']['mae']['mean']:.9f} ratio，最坏 max "
        f"{f1['selected_model_metrics']['max_abs_error']['max']:.6f}。这些数字不是平台 gap。",
        "",
        "### DeltaData",
        "",
        "- selected model=track 6/cluster 5/universal 13；这是选择折结果，不是最终测试。",
        "- v17 输入 31,904 点，经互斥账本后 14,040 clean-air model rows；交通阈值 80% 是 F1 TR 工程选择。",
        "- selected 内部 proxy 的 mean MAE "
        f"{delta['selected_model_metrics']['mae']['mean']:.6f}s；Australia "
        f"MAE/P90/max={delta_australia['mae']:.6f}/"
        f"{delta_australia['p90_abs_error']:.6f}/"
        f"{delta_australia['max_abs_error']:.6f}s 仍通过，说明绝对门缺失。",
        "- 公开 0.032s/lap 只能称具名燃油情景，不能称真实燃油识别。",
        "",
        "### FDataAnalysis",
        "",
        "- v3 universal 24/24，mean MAE "
        f"{fdata['selected_model_metrics']['mae']['mean']:.9f} lap fraction；"
        "目标是自身 1,200 轴重采样一致性。",
        "- 冻结公开参考是 2026 Hungary Race/FP2，而本地 pilot 是 2025 Abu Dhabi；不同事件/会话，数值对照 NOT_TESTED。",
        "- runtime-cache 只读重算的事件 median sampling interval 范围 "
        f"{fdata['sampling']['sample_interval_median_s']['min']:.6f}-"
        f"{fdata['sampling']['sample_interval_median_s']['max']:.6f}s（约 "
        f"{fdata['sampling']['approx_hz']['min']:.3f}-"
        f"{fdata['sampling']['approx_hz']['max']:.3f}Hz）；该统计尚未进入正式 v3 manifest。",
        "",
        "### F1TelemetryData",
        "",
        "- 持平：Q lap delta=0、race average gap MAE=0.000216s/max=0.000471s、fastest lap=0、top speed=0、top-3 sector=0、strategy top5=5/5。",
        "- 落后：control sections、team throttle、pit lane 三项如上；采样频率与不透明阈值是明确限制。",
        "- Q phase model_comparison 为 NOT_TESTED，但 Abu reference matrix 被错误标记为 reference-informed；当前计数按 NOT_TESTED 处理。",
        "",
        "### GP Tempo",
        "",
        "- 270 sector endpoints 和 90 finish checks 均 max error=0；这是官方 timing 约束实现持平。",
        "- public point truth events=0，segment_mae=null；不得伪造段内 MAE。",
        "- raw sample median 4.167Hz、401 点轴、linear/nearest、no smoothing；尚无采样/插值/平滑敏感性带。",
        "",
        "## 数据、验证与产品完成度",
        "",
        f"- 原始覆盖重算：2023={raw['by_year']['2023']['events']} events/"
        f"{raw['by_year']['2023']['rows']:,} points；2024={raw['by_year']['2024']['events']}/"
        f"{raw['by_year']['2024']['rows']:,}；2025={raw['by_year']['2025']['events']}/"
        f"{raw['by_year']['2025']['rows']:,}。",
        f"- v3+目标文件 hash graph 共复核 {recalculation['hash_graph']['checks']} 项，0 失败；"
        "但正式 manifest 未绑定生成器代码、库代码、依赖版本。",
        "- v3 浏览器 acceptance 的两次运行、目标 manifest 与 20 张截图哈希已复核；"
        "首轮 CSS viewport 身份失败被保留，latest r2 为 PASS，5 个移动端 CSS width 均为 390。"
        "v2 五目标 acceptance 也已复核；F1pace v2 只有 browser_acceptance.json、没有独立 acceptance_manifest.json。",
        "- 训练/测试事件在单个 fold 内分离；问题在于 fold 的测试结果随后又参与模型族选择，因此不是 untouched final test。",
        "- Kish ESS 是行级权重 ESS；track fold 只有 2 个独立训练事件时，数千行 ESS 不能代表数千独立样本。",
        "- `state.layer` 只激活方法卡，comparison/coverage 没有按层分流；当前 visual/audited 分层是标签，不是数据隔离。",
        "- 浏览器验收保留首轮 CSS viewport 失败和 r2 通过，证明画面可用性；它没有验证 layer 数据隔离或统计语义。",
        "",
        "## 最高风险 Top 10",
        "",
    ]
    for row in top10:
        lines.append(
            f"{row['top_risk_rank']}. **{row['problem_id']} {row['field_track_year_task_metric_or_gate']}**"
            f"（{row['priority']}）：{row['scientific_risk']} 验收：{row['acceptance_test']}"
        )
    lines.extend(
        [
            "",
            "## 最值得先做的 5 项优化",
            "",
        ]
    )
    for row in top5:
        lines.append(
            f"{row['top5_rank']}. **{row['backlog_id']} {row['title']}**："
            f"{row['verifiable_benefit']} 验收阈值：{row['acceptance_threshold']}"
        )
    lines.extend(
        [
            "",
            "完整依赖顺序、工作量、代码/测试/产物/文档清单见 `improvement_roadmap.md` 和 "
            "`improvement_backlog.csv/json`。",
            "",
            "## 机器可读证据入口",
            "",
            "- `audit_matrix.csv/json`：57 个子项，平台计数可重算。",
            "- `problem_register.csv/json`：18 个细粒度问题，每项含证据、根因、影响、验收与回退。",
            "- `improvement_backlog.csv/json`：四类 15 项 backlog。",
            "- `independent_recalculation.json`：hash graph、覆盖、selected metrics、最坏单元、源码语义和状态矛盾。",
            "- `manifest.json`：输入/输出哈希、命令、计数、网络跳过与限制。",
            "",
            "## 可推断与不可推断",
            "",
            "可以推断：本地冻结事件上的同量纲误差、公开阈值/端点不变量、v3 内部协议和产品代码行为。"
            "不能推断：闭源实现细节、未冻结事件准确率、平台完整产品能力、真实燃油/SOC/胎况/设定/损伤，以及任何跨任务总体排名。",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def validate_outputs_before_manifest(
    matrix: list[dict[str, Any]],
    problems: list[dict[str, Any]],
    backlog: list[dict[str, Any]],
    counts: dict[str, Any],
) -> None:
    if len(matrix) != counts["overall"]["total"]:
        raise AssertionError("matrix count 不一致")
    if any(
        row["status"] in {"UNKNOWN", "NOT_TESTED"}
        and row["counts_toward_advantage_rate"]
        for row in matrix
    ):
        raise AssertionError("UNKNOWN/NOT_TESTED 被计入优势率")
    if counts["verified_accuracy_leads"] != 0:
        raise AssertionError("当前证据不支持预测准确率领先")
    for row in problems:
        if not row["evidence"] or not row["acceptance_test"]:
            raise AssertionError(f"{row['problem_id']} 不完整")
    if set(row["category"] for row in backlog) != {
        "当前冻结数据即可完成",
        "需新增公开数据冻结",
        "算法改进",
        "可视化/产品改进",
    }:
        raise AssertionError("backlog 四分类不完整")


def input_identities(
    reports: dict[str, Path],
) -> list[dict[str, Any]]:
    paths = [
        ("v3_manifest", V3_MANIFEST),
        ("v3_browser_acceptance_index", V3_BROWSER_INDEX),
        ("v2_index", V2_INDEX),
        ("v17_manifest", V17_MANIFEST),
        ("v3_config", CONFIG),
        ("f1pace_v1_validation_support", F1PACE_V1_VALIDATION),
        ("v3_generator_source", GENERATOR_SOURCE),
        ("v3_generator_library", GENERATOR_LIBRARY),
        ("v3_frontend_source", FRONTEND_SOURCE),
    ]
    for key, path in reports.items():
        paths.append((f"v2_report_{key}", path))
    for index, path in enumerate(
        sorted(
            (
                REPO_ROOT
                / "research/records/reference_analysis_lab_v3_browser_acceptance"
            ).glob("run=*/acceptance_manifest.json")
        ),
        start=1,
    ):
        paths.append((f"v3_browser_acceptance_manifest_{index}", path))
    for path in sorted(
        (
            REPO_ROOT / "research/records/reference_analysis_lab_v2"
        ).glob("*/run=*/acceptance_manifest.json")
    ):
        paths.append(
            (
                f"v2_acceptance_manifest_{path.parents[1].name}",
                path,
            )
        )
    f1pace_browser = next(
        (
            REPO_ROOT / "research/records/reference_analysis_lab_v2/f1pace"
        ).glob("run=*/browser_acceptance.json")
    )
    paths.append(("v2_f1pace_browser_acceptance", f1pace_browser))
    return [
        {
            "input_id": input_id,
            "path": repo_relative(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for input_id, path in paths
    ]


def generate(output_dir: Path) -> dict[str, Any]:
    if output_dir.resolve() != HERE.resolve():
        raise ValueError("为保持 append-only 身份，本脚本只允许写入自身运行目录")
    recalculation, reports = build_recalculation()
    matrix = build_matrix(recalculation, reports)
    problems = build_problem_register(recalculation)
    backlog = build_backlog()
    counts = count_matrix(matrix)
    validate_outputs_before_manifest(matrix, problems, backlog, counts)

    write_json(output_dir / "independent_recalculation.json", recalculation)
    write_json(output_dir / "audit_matrix.json", matrix)
    write_csv(output_dir / "audit_matrix.csv", matrix)
    write_json(output_dir / "problem_register.json", problems)
    write_csv(output_dir / "problem_register.csv", problems)
    write_json(output_dir / "improvement_backlog.json", backlog)
    write_csv(output_dir / "improvement_backlog.csv", backlog)
    (output_dir / "improvement_roadmap.md").write_text(
        render_roadmap(backlog), encoding="utf-8"
    )
    (output_dir / "audit_report.md").write_text(
        render_report(recalculation, matrix, problems, backlog, counts),
        encoding="utf-8",
    )

    generated_files = []
    for filename in (
        *AUDIT_FILENAMES,
        "audit_professional_gap.py",
        "test_audit_outputs.py",
    ):
        path = output_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"预期生成/审计文件缺失：{path}")
        generated_files.append(
            {
                "path": repo_relative(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "schema_version": "reference-analysis-lab-v3-professional-gap-audit-manifest-v1",
        "run_id": RUN_ID,
        "created_at": datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat(timespec="seconds"),
        "status": "PASS_WITH_VERIFIED_GAPS_AND_UNKNOWN_BOUNDARIES",
        "append_only": True,
        "offline_only": True,
        "audit_script": {
            "version": SCRIPT_VERSION,
            "path": repo_relative(Path(__file__)),
            "sha256": sha256_file(Path(__file__)),
        },
        "run_commands": [
            f'python "{repo_relative(Path(__file__))}"',
            (
                "python -m pytest "
                f'"{repo_relative(HERE / "test_audit_outputs.py")}" -q'
            ),
            "python tools/audit_reference_analysis_lab_v3.py",
            (
                "python tools/audit_race_dossier_v17_release.py --manifest "
                f'"{repo_relative(V17_MANIFEST)}"'
            ),
        ],
        "inputs": input_identities(reports),
        "generated_files": generated_files,
        "conclusion_counts": counts,
        "problem_counts": {
            "total": len(problems),
            "P0": sum(row["priority"] == "P0" for row in problems),
            "P1": sum(row["priority"] == "P1" for row in problems),
            "P2": sum(row["priority"] == "P2" for row in problems),
            "top10": 10,
        },
        "backlog_counts": {
            "total": len(backlog),
            "by_category": dict(Counter(row["category"] for row in backlog)),
            "top5": 5,
        },
        "network": {
            "attempts": [],
            "skipped": True,
            "reason": (
                "五目标本地冻结来源已包含 URL、抓取时间、页面/版本标识与 SHA-256；"
                "当前缺口是闭源或无逐点真值，联网不会使不同量纲变得可比。"
            ),
        },
        "audit_limitations": [
            "只比较本地冻结的公开能力子集，不推断五个平台完整产品。",
            "图片转录/颜色读取是真值代理，保留其读图误差。",
            "A-C v3 指标主要是内部代理/自一致性，不能当平台 gap。",
            "D Q phase 与 E segment point truth 保持 NOT_TESTED。",
            "真实燃油、SOC、胎况、设定、损伤、动力模式和车队指令不可识别。",
            "没有跨任务总复刻率；PAC/OVR 均为 null。",
        ],
        "boundaries": {
            "unknown_not_tested_counted_as_advantage": False,
            "transparency_is_prediction_accuracy": False,
            "visual_similarity_is_numeric_accuracy": False,
            "cross_task_total_score": None,
            "pac": None,
            "ovr": None,
        },
        "manifest_self_hash": None,
        "manifest_self_hash_reason": "避免递归自哈希；独立测试直接对 manifest 语义与其余文件哈希验收。",
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def verify_existing(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest["run_id"] != RUN_ID:
        raise AssertionError("run_id 不一致")
    for item in manifest["inputs"]:
        verify_hash(
            resolve_repo_path(item["path"]),
            item["sha256"],
            f"input:{item['input_id']}",
        )
    for item in manifest["generated_files"]:
        verify_hash(
            resolve_repo_path(item["path"]),
            item["sha256"],
            f"output:{Path(item['path']).name}",
        )
    matrix = load_json(output_dir / "audit_matrix.json")
    counts = count_matrix(matrix)
    if counts != manifest["conclusion_counts"]:
        raise AssertionError("matrix 重算计数与 manifest 不一致")
    return {
        "status": "PASS",
        "run_id": RUN_ID,
        "inputs": len(manifest["inputs"]),
        "outputs": len(manifest["generated_files"]),
        "conclusion_counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只校验已生成 manifest、输入/输出哈希和矩阵计数。",
    )
    args = parser.parse_args()
    if args.verify_only:
        result = verify_existing(HERE)
    else:
        result = generate(HERE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
