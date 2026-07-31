"""发布 FDataAnalysis 参考分析复刻实验室 v1 的真实 Abu Dhabi pilot。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from reference_analysis_lab.fdataanalysis_pilot import build_fdataanalysis_pilot


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260730T235950+0800-fdataanalysis-abu-dhabi-v1"
V17_RUN_ID = "20260726T124625+0800-ce93134a40b8"
TELEMETRY = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
    / "year=2025"
    / "commit=f7a5324cae58"
    / "session=Race"
    / "meeting=Abu_Dhabi_Grand_Prix"
    / "telemetry.parquet"
)
V17_MANIFEST = ROOT / "research" / "records" / "race_dossier_v17" / f"run={V17_RUN_ID}" / "manifest.json"
CACHE = ROOT / ".runtime-cache" / "reference-analysis-lab-v1" / "fdataanalysis"
SOURCE_HTML = CACHE / "source.html"
REFERENCE_IMAGES = CACHE / "reference-images"
RECORD_DIR = ROOT / "research" / "records" / "reference_analysis_lab_v1" / "fdataanalysis" / f"run={RUN_ID}"
FRONTEND_ROOT = ROOT / "frontend" / "public" / "data" / "reference-analysis-lab" / "v1"
FRONTEND_DIR = FRONTEND_ROOT / f"run={RUN_ID}"
ROOT_INDEX = FRONTEND_ROOT / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(encoded + b"\n")
    return hashlib.sha256(encoded + b"\n").hexdigest()


def build_source_identity() -> dict[str, Any]:
    parquet = pq.ParquetFile(TELEMETRY)
    return {
        "run_id": RUN_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "real_tracinginsights_expanded_v4",
        "session": "Race",
        "year": 2025,
        "meeting": "Abu Dhabi Grand Prix",
        "telemetry": {
            "path": str(TELEMETRY.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(TELEMETRY),
            "rows": parquet.metadata.num_rows,
            "columns": parquet.schema_arrow.names,
            "source_commit": "f7a5324cae58",
        },
        "v17_manifest": {
            "path": str(V17_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(V17_MANIFEST),
            "run_id": V17_RUN_ID,
            "role": "coverage/audit reference only; no v17 numbers are rewritten",
        },
    }


def build_reference_identity() -> dict[str, Any]:
    images = []
    for name, public_name, post_url in [
        (
            "race-pace-hungarian-v2.jpg",
            "FDataAnalysis RACE PACE #HungarianGP",
            "https://x.com/FDataAnalysis/status/2081706503862378548",
        ),
        (
            "long-runs-fp2-hungarian.jpg",
            "FDataAnalysis LONG RUNS DATA",
            "https://x.com/FDataAnalysis/status/2081027167987634407",
        ),
    ]:
        path = REFERENCE_IMAGES / name
        images.append(
            {
                "name": public_name,
                "post_url": post_url,
                "cache_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_file(path) if path.exists() else None,
                "status": "AVAILABLE" if path.exists() else "MISSING",
            }
        )
    return {
        "source_profile_url": "https://x.com/FDataAnalysis",
        "source_profile_sha256": sha256_file(SOURCE_HTML),
        "source_profile_cache_path": str(SOURCE_HTML.relative_to(ROOT)).replace("\\", "/"),
        "source_profile_fetch_status": "HTTP_200",
        "source_profile_title": "Formula Data Analysis (@FDataAnalysis) / X",
        "web_extraction_observation": "web open returned zero extracted lines; cached HTML exposed public profile metadata and current posts",
        "images": images,
        "license_or_republication_boundary": "仅重绘公开语义与布局；不复制 X 原图、logo、品牌字体或人物照片作为产品资产。",
    }


def write_method_card(report: dict[str, Any]) -> None:
    method = report["method_card"]
    source = report["source_identity"]
    reference = report["reference_identity"]
    lines = [
        "# FDataAnalysis 方法卡 · 2025 Abu Dhabi Race pilot",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- profile: {reference['source_profile_url']}",
        f"- profile_sha256: `{reference['source_profile_sha256']}`",
        f"- fetched_at: `{source['created_at']}`",
        "",
        "## 原作者页面/帖子",
        "",
    ]
    for post in method["source_posts"]:
        lines.append(f"- [{post['name']}]({post['url']})：" + "、".join(post["public_semantics"]))
    lines += ["", "## 公开方法与视觉反推", ""]
    lines.extend(f"- {item}" for item in method["public_method"])
    lines.append("视觉反推：")
    lines.extend(f"- {item}" for item in method["visual_inference"])
    lines.append("本 pilot 实现：")
    lines.extend(f"- {item}" for item in method["pilot_method"])
    lines.append("不可识别/不发布：")
    lines.extend(f"- {item}" for item in method["not_identifiable"])
    lines += [
        "",
        "## 输入身份",
        "",
        f"- telemetry: `{source['telemetry']['path']}`",
        f"- telemetry_sha256: `{source['telemetry']['sha256']}`",
        f"- rows: `{source['telemetry']['rows']}`",
        f"- v17_manifest_sha256: `{source['v17_manifest']['sha256']}`",
        "",
        "## 边界",
        "",
        method["reference_boundary"],
    ]
    (RECORD_DIR / "method_card.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_data_gap_audit(report: dict[str, Any]) -> None:
    lines = [
        "# FDataAnalysis data-gap audit · 2025 Abu Dhabi Race",
        "",
        "| 目标字段 | 仓库当前字段 | 状态 | 替代代理 | 发布规则 |",
        "|---|---|---|---|---|",
    ]
    for row in report["data_gap_audit"]:
        lines.append(
            "| {field} | {current_field} | `{status}` | {substitute} | {publication} |".format(**row)
        )
    lines += [
        "",
        "结论：速度/控制字段可从真实逐点遥测发布；弯道/直道 anchor 是距离轴 proxy，",
        "胎龄变化是条件观察代理。真实下压力、引擎功率、物理胎损和未公开创作者代码",
        "不可识别，audited_analysis 保持 audit_only。",
    ]
    (RECORD_DIR / "data_gap_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_frontend_index(target_manifest_path: str, report_sha256: str) -> None:
    index = json.loads(ROOT_INDEX.read_text(encoding="utf-8"))
    if any(target.get("target_id") == "fdataanalysis" for target in index.get("targets", [])):
        raise RuntimeError("fdataanalysis 已在根 manifest 中，拒绝覆盖既有目标")
    index.setdefault("targets", []).append(
        {
            "target_id": "fdataanalysis",
            "label": "FDataAnalysis · Abu Dhabi telemetry feature pilot",
            "status": "PASS",
            "run_id": RUN_ID,
            "manifest": target_manifest_path,
            "report_sha256": report_sha256,
            "page": "fdataanalysis-pilot.html",
        }
    )
    write_json(ROOT_INDEX, index)


def main() -> int:
    for path in (TELEMETRY, SOURCE_HTML, V17_MANIFEST):
        if not path.exists():
            raise FileNotFoundError(path)
    columns = [
        "year", "meeting", "session", "driver_acronym", "driver_number", "lap",
        "sample_index", "time", "distance", "rel_distance", "speed", "throttle",
        "brake", "gear", "rpm", "drs", "driver_ahead", "distance_to_driver_ahead",
        "lap_duration", "compound", "stint", "tyre_life", "team", "track_status",
        "deleted", "is_accurate", "position",
    ]
    table = pq.ParquetFile(TELEMETRY).read(columns=columns)
    points = table.to_pandas()
    source_identity = build_source_identity()
    reference_identity = build_reference_identity()
    report = build_fdataanalysis_pilot(
        points,
        source_identity=source_identity,
        reference_identity=reference_identity,
    )
    record_report = RECORD_DIR / "fdataanalysis_2025_abu_dhabi.json"
    report_sha256 = write_json(record_report, report)
    write_method_card(report)
    write_data_gap_audit(report)
    target_report_path = f"data/reference-analysis-lab/v1/run={RUN_ID}/report.json"
    target_manifest_path = f"data/reference-analysis-lab/v1/run={RUN_ID}/manifest.json"
    target_manifest = {
        "schema_version": "reference-analysis-lab-target-manifest-v1",
        "target_id": "fdataanalysis",
        "status": report["status"],
        "run_id": RUN_ID,
        "report": {
            "path": target_report_path,
            "sha256": report_sha256,
            "bytes": record_report.stat().st_size,
        },
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
        "frontend_page": "fdataanalysis-pilot.html",
    }
    frontend_report = FRONTEND_DIR / "report.json"
    frontend_sha256 = write_json(frontend_report, report)
    if frontend_sha256 != report_sha256:
        raise RuntimeError("前端复制报告哈希不一致")
    write_json(FRONTEND_DIR / "manifest.json", target_manifest)
    update_frontend_index(target_manifest_path, report_sha256)
    record_manifest = {
        "schema_version": "reference-analysis-lab-record-manifest-v1",
        "target_id": "fdataanalysis",
        "status": report["status"],
        "run_id": RUN_ID,
        "report": {
            "path": str(record_report.relative_to(ROOT)).replace("\\", "/"),
            "sha256": report_sha256,
            "bytes": record_report.stat().st_size,
        },
        "method_card": str((RECORD_DIR / "method_card.md").relative_to(ROOT)).replace("\\", "/"),
        "data_gap_audit": str((RECORD_DIR / "data_gap_audit.md").relative_to(ROOT)).replace("\\", "/"),
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
    }
    write_json(RECORD_DIR / "manifest.json", record_manifest)
    print(json.dumps({"status": "PASS", "run_id": RUN_ID, "report_sha256": report_sha256, "record_dir": str(RECORD_DIR)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
