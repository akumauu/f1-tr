#!/usr/bin/env python3
"""独立审计 reference-analysis-lab v3 的身份、无泄漏与发布门。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = (
    ROOT
    / "frontend"
    / "public"
    / "data"
    / "reference-analysis-lab"
    / "v3"
    / "manifest.json"
)
BROWSER_ACCEPTANCE_INDEX_PATH = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v3_browser_acceptance"
    / "manifest.json"
)
TARGET_IDS = (
    "f1pace",
    "deltadata",
    "fdataanalysis",
    "f1telemetrydata",
    "gptempo",
)
F1PACE_EXTERNAL_HOLDOUTS = {
    "2025-23-qatar-grand-prix",
    "2025-24-abu-dhabi-grand-prix",
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为 object：{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_file(value: str, label: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise FileNotFoundError(f"{label} 不存在或越出项目：{path}")
    return path


def _verify_entry(entry: Mapping[str, Any], label: str) -> Path:
    path = _project_file(str(entry["path"]), label)
    actual = _sha256(path)
    expected = str(entry["sha256"])
    if actual != expected:
        raise RuntimeError(
            f"{label} SHA-256 不一致：expected={expected} actual={actual}"
        )
    if "bytes" in entry and int(entry["bytes"]) != path.stat().st_size:
        raise RuntimeError(f"{label} bytes 不闭合")
    return path


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"浏览器截图不是有效 PNG：{path}")
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )


def _json_cell(value: str) -> list[str]:
    if not value:
        return []
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError(f"CSV JSON 单元格必须是 list：{value}")
    return [str(item) for item in decoded]


def _is_true(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _as_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} 必须是数值：{value}") from error
    if not math.isfinite(result):
        raise RuntimeError(f"{label} 必须是有限数值：{value}")
    return result


def _latest_source_manifest() -> Path:
    index = _json(INDEX_PATH)
    latest = str(index["latest_run_id"])
    entries = [
        row
        for row in index.get("targets", [])
        if row.get("run_id") == latest
    ]
    if len(entries) != 1:
        raise RuntimeError("v3 索引 latest_run_id 必须唯一命中")
    public_manifest = _verify_entry(
        {
            "path": "frontend/public/"
            + str(entries[0]["manifest"]),
            "sha256": entries[0]["manifest_sha256"],
        },
        "latest public manifest",
    )
    public = _json(public_manifest)
    return _verify_entry(public["source_manifest"], "source manifest")


def _audit_split_registry(
    target_id: str,
    rows: Iterable[Mapping[str, str]],
    event_lookup: Mapping[str, Mapping[str, str]],
) -> None:
    for row in rows:
        if "training_events" not in row:
            continue
        training = set(_json_cell(row.get("training_events", "")))
        tests = set(
            _json_cell(
                row.get("test_events", "")
                or json.dumps([row.get("test_event_id")])
            )
        )
        if training & tests:
            raise RuntimeError(f"{target_id} 训练/测试事件泄漏")
        if target_id == "f1pace":
            exclusions = set(
                _json_cell(row.get("frozen_training_exclusions", ""))
            )
            if exclusions != F1PACE_EXTERNAL_HOLDOUTS:
                raise RuntimeError("F1pace 外部冻结排除身份不完整")
            if training & F1PACE_EXTERNAL_HOLDOUTS:
                raise RuntimeError("F1pace Abu Dhabi/Qatar 外部事件进入训练")
        if row.get("status") != "PASS" or not training:
            continue
        test_event = event_lookup[str(row["test_event_id"])]
        if row["model_type"] == "track":
            if any(
                event_lookup[event_id]["track_key"]
                != test_event["track_key"]
                for event_id in training
            ):
                raise RuntimeError(f"{target_id} track 折混入其他赛道")
        if row["model_type"] == "cluster":
            if any(
                event_lookup[event_id]["observable_cluster"]
                != test_event["observable_cluster"]
                for event_id in training
            ):
                raise RuntimeError(f"{target_id} cluster 折混入其他族群")


def _audit_selected_models(
    target_id: str,
    registry_rows: list[Mapping[str, Any]],
    comparison_rows: list[Mapping[str, str]],
) -> Counter[str]:
    if len(registry_rows) != 24:
        raise RuntimeError(f"{target_id} model registry 必须覆盖 24 条赛道")
    comparison_lookup = {
        (row.get("track_key"), row.get("model_type")): row
        for row in comparison_rows
        if row.get("track_key")
    }
    selected = Counter()
    for row in registry_rows:
        model_type = str(row["selected_model_type"])
        if model_type not in {"universal", "cluster", "track"}:
            raise RuntimeError(f"{target_id} 出现未知 selected_model_type")
        selected[model_type] += 1
        if model_type == "track" and not _is_true(
            row.get("track_candidate_passed")
        ):
            raise RuntimeError(f"{target_id} 发布了未过门的 track 模型")
        if model_type == "cluster" and not _is_true(
            row.get("cluster_candidate_passed")
        ):
            raise RuntimeError(f"{target_id} 发布了未过门的 cluster 模型")
        comparison = comparison_lookup.get((row.get("track_key"), model_type))
        if (
            model_type != "universal"
            and comparison is not None
            and not _is_true(comparison.get("publication_gate_passed"))
        ):
            raise RuntimeError(f"{target_id} registry 与数值发布门不一致")
    return selected


def _audit_f1pace(target_dir: Path) -> None:
    pairwise = _csv_rows(target_dir / "pairwise_invariants.csv")
    if len(pairwise) != 70 or any(
        row["status"] != "PASS"
        or _as_float(
            row["max_antisymmetry_error_s"],
            "F1pace pairwise antisymmetry",
        )
        != 0.0
        for row in pairwise
    ):
        raise RuntimeError("F1pace 70 场 pairwise 反对称不变量未闭合")


def _audit_fdataanalysis(target_dir: Path) -> None:
    events = _csv_rows(target_dir / "segmentation_events.csv")
    if not events or any(
        _as_float(row["endpoint_start_error"], "分段起点误差") != 0.0
        or _as_float(row["endpoint_finish_error"], "分段终点误差") != 0.0
        for row in events
    ):
        raise RuntimeError("FDataAnalysis 距离轴端点不变量未闭合")
    stability = _csv_rows(target_dir / "layout_stability.csv")
    if not stability or any(
        row["status"] not in {"PASS", "BOUNDARY"} for row in stability
    ):
        raise RuntimeError("FDataAnalysis 布局代理审计状态非法")


def _audit_f1telemetrydata(target_dir: Path) -> int:
    reference = _csv_rows(target_dir / "chart_reference_matrix.csv")
    if len(reference) != 490:
        raise RuntimeError("F1TelemetryData 应为 70 场 x 7 图表任务")
    public_rows = [
        row for row in reference if _is_true(row["public_reference_available"])
    ]
    if (
        len(public_rows) != 7
        or {row["event_id"] for row in public_rows}
        != {"2025-24-abu-dhabi-grand-prix"}
    ):
        raise RuntimeError("F1TelemetryData 同场公开参考身份不闭合")
    comparison = _csv_rows(target_dir / "model_comparison.csv")
    rows_by_task = Counter(row["chart_task"] for row in comparison)
    if (
        len(rows_by_task) != 7
        or any(count != 3 for count in rows_by_task.values())
    ):
        raise RuntimeError("F1TelemetryData 每个图表任务必须显式对照三类模型")
    candidates = [
        row
        for row in comparison
        if row["model_type"] in {"cluster", "track"}
    ]
    for row in candidates:
        if not row["status"].startswith("NOT_TESTED"):
            raise RuntimeError("F1TelemetryData 单参考事件误发专用模型")
        for metric in ("mae", "max_abs_error"):
            if not _is_blank(row.get(metric)):
                raise RuntimeError("F1TelemetryData NOT_TESTED 被写成数值")
    q_phase = [
        row
        for row in comparison
        if row["chart_task"] == "qualifying_q1_q2_q3_phase"
        and row["model_type"] == "universal"
    ]
    if (
        len(q_phase) != 1
        or not q_phase[0]["status"].startswith("NOT_TESTED")
        or not _is_blank(q_phase[0]["mae"])
        or not _is_blank(q_phase[0]["max_abs_error"])
    ):
        raise RuntimeError("F1TelemetryData Q 阶段 universal 缺口未显式 fail-close")
    return sum(
        1
        for row in reference
        if row["status"].startswith("NOT_TESTED")
    )


def _audit_gptempo(target_dir: Path) -> int:
    rows = _csv_rows(target_dir / "sector_endpoint_validation.csv")
    if len(rows) != 3:
        raise RuntimeError("GP Tempo 模型对照必须含三类候选")
    universal = next(row for row in rows if row["model_type"] == "universal")
    if (
        universal["status"] != "PASS"
        or int(universal["sector_endpoint_checks"]) != 270
        or _as_float(
            universal["max_abs_sector_endpoint_error_s"],
            "GP Tempo sector endpoint error",
        )
        != 0.0
        or not _is_blank(universal["segment_mae"])
        or not universal["segment_mae_status"].startswith("NOT_TESTED")
    ):
        raise RuntimeError("GP Tempo 端点不变量或逐点真值边界未闭合")
    for row in rows:
        if row["model_type"] == "universal":
            continue
        if row["status"] != "NOT_TESTED" or not _is_blank(
            row["segment_mae"]
        ):
            raise RuntimeError("GP Tempo 未留出候选被错误发布")
    return sum(row["status"] == "NOT_TESTED" for row in rows)


def _audit_browser_acceptance(run_id: str) -> dict[str, Any]:
    index = _json(BROWSER_ACCEPTANCE_INDEX_PATH)
    if (
        index.get("schema_version")
        != "reference-analysis-lab-v3-browser-acceptance-index"
    ):
        raise RuntimeError("浏览器验收索引 schema 不一致")
    latest_id = str(index["latest_acceptance_id"])
    latest_rows = [
        row
        for row in index.get("runs", [])
        if row.get("acceptance_id") == latest_id
    ]
    if len(latest_rows) != 1 or latest_rows[0].get("status") != "PASS":
        raise RuntimeError("浏览器验收 latest 必须唯一 PASS")
    failed = [
        row
        for row in index.get("runs", [])
        if row.get("status") == "FAILED_CSS_VIEWPORT_IDENTITY_AUDIT"
    ]
    if (
        len(failed) != 1
        or failed[0].get("reason")
        != "CLI_PNG_WIDTH_390_BUT_CSS_VIEWPORT_CLAMPED_ABOVE_390"
    ):
        raise RuntimeError("首轮浏览器 viewport 身份失败未留痕")
    manifest_path = _verify_entry(
        {
            "path": latest_rows[0]["manifest"],
            "sha256": latest_rows[0]["manifest_sha256"],
        },
        "browser acceptance manifest",
    )
    manifest = _json(manifest_path)
    if (
        manifest.get("run_id") != run_id
        or manifest.get("status") != "PASS"
        or int(manifest.get("screenshot_count", 0)) != 10
        or int(manifest.get("exact_css_mobile_width", 0)) != 390
    ):
        raise RuntimeError("浏览器 acceptance manifest 身份不闭合")
    target_refs = manifest.get("target_manifests", [])
    if {row["target_id"] for row in target_refs} != set(TARGET_IDS):
        raise RuntimeError("浏览器 acceptance 未覆盖五个目标")
    for target_ref in target_refs:
        target_path = _verify_entry(target_ref, "target browser manifest")
        target_manifest = _json(target_path)
        if (
            target_manifest.get("status") != "PASS"
            or target_manifest.get("run_id") != run_id
            or len(target_manifest.get("browser_checks", [])) != 2
            or len(target_manifest.get("screenshots", [])) != 2
        ):
            raise RuntimeError("目标浏览器验收必须含桌面与 390px 两例")
    acceptance_path = _verify_entry(
        manifest["browser_acceptance"],
        "browser acceptance JSON",
    )
    acceptance = _json(acceptance_path)
    checks = acceptance.get("browser_checks", [])
    screenshots = acceptance.get("screenshots", [])
    if len(checks) != 10 or len(screenshots) != 10:
        raise RuntimeError("浏览器验收应有 10 个 viewport case")
    for check in checks:
        if (
            check.get("css_viewport_exact") is not True
            or check.get("no_page_horizontal_overflow") is not True
            or check.get("run_identity_visible") is not True
            or check.get("task_control_semantics_passed") is not True
            or int(check.get("runtime_exception_count", -1)) != 0
            or int(check.get("console_error_count", -1)) != 0
            or int(check.get("network_failure_count", -1)) != 0
        ):
            raise RuntimeError("浏览器 viewport/runtime 检查未全部通过")
    for screenshot in screenshots:
        path = _verify_entry(screenshot, "browser screenshot")
        width, height = _png_dimensions(path)
        if (
            width != int(screenshot["width"])
            or height != int(screenshot["height"])
            or int(screenshot["css_inner_width"]) != width
            or int(screenshot["document_scroll_width"]) > width
            or int(screenshot["body_scroll_width"]) > width
        ):
            raise RuntimeError("截图尺寸、CSS viewport 或横向溢出不闭合")
        if screenshot["viewport"] == "mobile-390" and width != 390:
            raise RuntimeError("移动截图不是严格 390px CSS viewport")
    return {
        "acceptance_id": latest_id,
        "viewport_cases": len(checks),
        "screenshots": len(screenshots),
        "mobile_css_width": 390,
        "failed_cli_acceptance_preserved": failed[0]["acceptance_id"],
    }


def audit_release(manifest_path: Path | None = None) -> dict[str, Any]:
    if manifest_path is None:
        manifest_path = _latest_source_manifest()
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_relative_to(ROOT.resolve()):
        raise ValueError("manifest 必须位于项目内")
    manifest = _json(manifest_path)
    if (
        manifest.get("schema_version")
        != "reference-analysis-track-validation-manifest-v3"
        or manifest.get("status") != "PASS_WITH_NOT_TESTED_GAPS"
    ):
        raise RuntimeError("v3 根 manifest schema/status 不一致")
    run_dir = manifest_path.parent
    run_id = str(manifest["run_id"])
    if run_dir.name != f"run={run_id}":
        raise RuntimeError("run_id 与正式目录身份不一致")
    if manifest.get("coverage") != {
        "events": 70,
        "tracks": 24,
        "seasons": 3,
    }:
        raise RuntimeError("v3 覆盖必须为 70 场、24 赛道、3 赛季")
    supersedes = manifest.get("supersedes", {})
    if not supersedes.get("run_id") or not supersedes.get("reason"):
        raise RuntimeError("追加迭代未记录被取代 run 与原因")

    config_path = _verify_entry(manifest["config"], "v3 config")
    config = _json(config_path)
    for key, identity in config["input_identities"].items():
        if key == "frozen_target_reports":
            for target_id, entry in identity.items():
                _verify_entry(entry, f"{target_id} frozen target report")
        else:
            _verify_entry(identity, f"frozen input {key}")
    f1pace_spec = config["universal_baselines"]["f1pace"]
    if (
        float(f1pace_spec["traffic_seconds"]) != 2.0
        or float(f1pace_spec["traffic_lap_ratio_strictly_greater_than"])
        != 1.0 / 3.0
    ):
        raise RuntimeError("F1pace 2 秒/严格 33% 固定语义漂移")
    for identity in manifest["source_identities"].values():
        identities = identity if isinstance(identity, list) else [identity]
        for entry in identities:
            _verify_entry(entry, "冻结源身份")
    for key in (
        "universal_baseline_recheck",
        "report",
        "model_registry",
        "event_catalog",
        "observable_track_registry",
    ):
        _verify_entry(manifest[key], key)

    baseline_path = _project_file(
        str(manifest["universal_baseline_recheck"]["path"]),
        "universal baseline",
    )
    baseline = _json(baseline_path)
    if baseline.get("frozen_before_candidate_comparison") is not True:
        raise RuntimeError("universal baseline 未在候选比较前冻结")
    for target_id in TARGET_IDS:
        _verify_entry(
            baseline[target_id]["identity"],
            f"{target_id} frozen v1/v2 report",
        )
    if baseline["f1pace"].get("abu_and_qatar_used_for_v3_training") is not False:
        raise RuntimeError("F1pace 外部验证污染边界漂移")

    catalog_path = _project_file(
        str(manifest["event_catalog"]["path"]),
        "event catalog",
    )
    catalog = _csv_rows(catalog_path)
    if (
        len(catalog) != 70
        or len({row["event_id"] for row in catalog}) != 70
        or len({row["track_key"] for row in catalog}) != 24
        or {int(row["year"]) for row in catalog} != {2023, 2024, 2025}
    ):
        raise RuntimeError("event catalog 覆盖或唯一性不闭合")
    event_lookup = {row["event_id"]: row for row in catalog}
    if len(manifest.get("cache_identities", [])) != 70:
        raise RuntimeError("缓存身份必须逐事件覆盖 70 场")

    selected_counts: dict[str, dict[str, int]] = {}
    not_tested_rows = 0
    target_refs = manifest.get("target_manifests", [])
    if {row["target_id"] for row in target_refs} != set(TARGET_IDS):
        raise RuntimeError("五个目标 manifest 身份不完整")
    for target_ref in target_refs:
        target_id = str(target_ref["target_id"])
        target_manifest_path = _verify_entry(
            {
                "path": target_ref["manifest"],
                "sha256": target_ref["manifest_sha256"],
            },
            f"{target_id} manifest",
        )
        target_dir = target_manifest_path.parent
        if (
            not target_dir.is_relative_to(run_dir)
            or target_dir.name != target_id
        ):
            raise RuntimeError(f"{target_id} manifest 越出正式 run")
        target_manifest = _json(target_manifest_path)
        if (
            target_manifest.get("run_id") != run_id
            or target_manifest.get("target_id") != target_id
        ):
            raise RuntimeError(f"{target_id} manifest run/target 身份不一致")
        file_entries = target_manifest.get("files", [])
        for entry in file_entries:
            path = _verify_entry(entry, f"{target_id} file")
            if not path.is_relative_to(target_dir):
                raise RuntimeError(f"{target_id} file 越出目标目录")
            if "rows" in entry and _csv_row_count(path) != int(entry["rows"]):
                raise RuntimeError(f"{target_id} CSV rows 不闭合：{path.name}")
        required = {
            "coverage_matrix.csv",
            "split_registry.csv",
            "model_comparison.csv",
            "sample_exclusion_ledger.csv",
            "model_registry.json",
            "method_card.md",
            "data_gap_audit.md",
            "reference_difference.md",
        }
        names = {Path(str(entry["path"])).name for entry in file_entries}
        if not required.issubset(names):
            raise RuntimeError(f"{target_id} 缺少必需交付件")

        registry = _json(target_dir / "model_registry.json")
        comparison = _csv_rows(target_dir / "model_comparison.csv")
        counts = _audit_selected_models(
            target_id,
            registry["rows"],
            comparison,
        )
        if counts != Counter(target_manifest["selected_model_counts"]):
            raise RuntimeError(f"{target_id} selected_model_counts 不闭合")
        selected_counts[target_id] = dict(counts)
        _audit_split_registry(
            target_id,
            _csv_rows(target_dir / "split_registry.csv"),
            event_lookup,
        )
        ledger = _csv_rows(target_dir / "sample_exclusion_ledger.csv")
        if ledger and "ledger_closed" in ledger[0] and any(
            not _is_true(row["ledger_closed"]) for row in ledger
        ):
            raise RuntimeError(f"{target_id} 排除账本未闭合")
        if target_id == "f1pace":
            _audit_f1pace(target_dir)
        elif target_id == "fdataanalysis":
            _audit_fdataanalysis(target_dir)
        elif target_id == "f1telemetrydata":
            not_tested_rows += _audit_f1telemetrydata(target_dir)
        elif target_id == "gptempo":
            not_tested_rows += _audit_gptempo(target_dir)

    public_dir = (
        ROOT
        / "frontend"
        / "public"
        / "data"
        / "reference-analysis-lab"
        / "v3"
        / f"run={run_id}"
    )
    public = _json(public_dir / "manifest.json")
    if public.get("run_id") != run_id:
        raise RuntimeError("public manifest run_id 不一致")
    for key in ("report", "model_registry", "source_manifest"):
        entry = dict(public[key])
        if str(entry["path"]).startswith("data/"):
            entry["path"] = "frontend/public/" + str(entry["path"])
        _verify_entry(entry, f"public {key}")
    index = _json(INDEX_PATH)
    if index.get("latest_run_id") != run_id:
        raise RuntimeError("v3 index latest_run_id 未指向通过 run")
    failed_runs = [
        row
        for row in index.get("targets", [])
        if row.get("status") == "FAILED_POST_PUBLICATION_IDENTITY_AUDIT"
    ]
    if len(failed_runs) != 1:
        raise RuntimeError("首轮发布后失败必须在索引中唯一留痕")
    if (
        failed_runs[0].get("superseded_reason")
        != "TARGET_MANIFEST_PATH_ESCAPES_FORMAL_RUN"
    ):
        raise RuntimeError("首轮身份失败原因在索引中漂移")
    browser_acceptance = _audit_browser_acceptance(run_id)

    return {
        "status": "PASS",
        "run_id": run_id,
        "events": 70,
        "tracks": 24,
        "targets": 5,
        "selected_model_counts": selected_counts,
        "not_tested_rows_checked": not_tested_rows,
        "failed_run_preserved": failed_runs[0]["run_id"],
        "browser_acceptance": browser_acceptance,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        help="可选的正式 run manifest；默认解析 public v3 latest",
    )
    args = parser.parse_args()
    summary = audit_release(args.manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
