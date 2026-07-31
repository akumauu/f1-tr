"""发布 DeltaData 参考分析复刻实验室 v1 的真实 Abu Dhabi pilot。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reference_analysis_lab.deltadata_pilot import build_deltadata_pilot


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260730T235900+0800-deltadata-abu-dhabi-v1"
V17_RUN_ID = "20260726T124625+0800-ce93134a40b8"
V17_MANIFEST = ROOT / "research" / "records" / "race_dossier_v17" / f"run={V17_RUN_ID}" / "manifest.json"
SIDE_CAR = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / f"run={V17_RUN_ID}"
    / "year=2025"
    / "round=24"
    / "meeting=abu-dhabi-grand-prix"
    / "stint_curve_evidence.json"
)
CACHE = ROOT / ".runtime-cache" / "reference-analysis-lab-v1" / "deltadata"
SOURCE_HTML = CACHE / "source.html"
REFERENCE_IMAGES = CACHE / "reference-images"
RECORD_DIR = ROOT / "research" / "records" / "reference_analysis_lab_v1" / "deltadata" / f"run={RUN_ID}"
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
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")
    path.write_bytes(encoded + b"\n")
    return hashlib.sha256(encoded + b"\n").hexdigest()


def build_source_identity() -> dict[str, Any]:
    sidecar_payload = json.loads(SIDE_CAR.read_text(encoding="utf-8"))
    return {
        "run_id": RUN_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": "Race",
        "year": 2025,
        "meeting": "Abu Dhabi Grand Prix",
        "source_kind": "real_v17_sidecar",
        "sidecar": {
            "path": str(SIDE_CAR.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(SIDE_CAR),
            "stints": len(sidecar_payload.get("stints") or []),
            "points": sum(len(stint.get("points") or []) for stint in sidecar_payload.get("stints") or []),
            "coverage": sidecar_payload.get("coverage"),
        },
        "v17_manifest": {
            "path": str(V17_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(V17_MANIFEST),
            "run_id": V17_RUN_ID,
        },
        "source_commit": sidecar_payload.get("source_identity", {}).get("data_commit"),
    }


def build_reference_identity() -> dict[str, Any]:
    images = []
    for name, public_name, post_url in [
        (
            "h2h-clear-air-race-pace.jpg",
            "DeltaData H2H clear-air race pace",
            "https://x.com/DeltaData_/status/2082785101931311292",
        ),
        (
            "team-representative-race-laps.jpg",
            "DeltaData representative race laps",
            "https://x.com/DeltaData_/status/2082828055677108520",
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
        "source_profile_url": "https://x.com/DeltaData_",
        "source_profile_sha256": sha256_file(SOURCE_HTML),
        "source_profile_cache_path": str(SOURCE_HTML.relative_to(ROOT)).replace("\\", "/"),
        "source_profile_fetch_status": "HTTP_200",
        "source_profile_title": "Delta Data (@DeltaData_) / X",
        "web_extraction_observation": "web open returned zero extracted lines; cached HTML exposed public profile metadata and current posts",
        "images": images,
        "license_or_republication_boundary": "仅重绘公开语义与布局；不复制 X 原图、logo、品牌字体或人物照片作为产品资产。",
    }


def write_method_card(report: dict[str, Any]) -> None:
    method = report["method_card"]
    identity = report["source_identity"]
    reference = report["reference_identity"]
    lines = [
        "# DeltaData 方法卡 · 2025 Abu Dhabi Race pilot",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- target_status: `{report['status']}`",
        f"- profile: {reference['source_profile_url']}",
        f"- profile_sha256: `{reference['source_profile_sha256']}`",
        f"- fetched_at: `{identity['created_at']}`",
        "",
        "## 原作者页面/帖子",
        "",
    ]
    for post in method["source_posts"]:
        lines.append(f"- [{post['name']}]({post['url']})：" + "、".join(post["public_semantics"]))
    lines += [
        "",
        "## 公开方法、视觉反推和不可识别项",
        "",
        "公开方法：",
    ]
    lines.extend(f"- {item}" for item in method["public_method"])
    lines.append("视觉反推：")
    lines.extend(f"- {item}" for item in method["visual_inference"])
    lines.append("方法等价实现：")
    lines.extend(f"- {item}" for item in method["equivalent_method"])
    lines.append("不可识别/不发布：")
    lines.extend(f"- {item}" for item in method["not_identifiable"])
    lines += [
        "",
        "## 输入身份",
        "",
        f"- v17 sidecar: `{identity['sidecar']['path']}`",
        f"- sidecar_sha256: `{identity['sidecar']['sha256']}`",
        f"- v17 manifest_sha256: `{identity['v17_manifest']['sha256']}`",
        f"- reference images: {len(reference['images'])} 张，均在缓存中冻结哈希",
        "",
        "## 边界",
        "",
        method["reference_boundary"],
    ]
    (RECORD_DIR / "method_card.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_data_gap_audit(report: dict[str, Any]) -> None:
    lines = [
        "# DeltaData data-gap audit · 2025 Abu Dhabi Race",
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
        "结论：目标创作者的精确代码、选圈、燃油和百分比归一化没有公开，不能声称一比一",
        "复刻。本 run 只发布真实 v17 sidecar 上的方法等价结果，visual_replication 和",
        "audited_analysis 分开；audited_analysis 保持 `audit_only`，不形成跨配方/胎龄全序。",
    ]
    (RECORD_DIR / "data_gap_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_frontend_index(target_manifest_path: str, target_report_sha256: str) -> None:
    index = json.loads(ROOT_INDEX.read_text(encoding="utf-8"))
    targets = list(index.get("targets") or [])
    if any(target.get("target_id") == "deltadata" for target in targets):
        raise RuntimeError("deltadata 已在根 manifest 中，拒绝覆盖既有目标")
    targets.append(
        {
            "target_id": "deltadata",
            "label": "DeltaData · Abu Dhabi method-equivalent pilot",
            "status": "METHOD_EQUIVALENT_ONLY",
            "run_id": RUN_ID,
            "manifest": target_manifest_path,
            "report_sha256": target_report_sha256,
            "page": "delta-data-pilot.html",
        }
    )
    index["targets"] = targets
    write_json(ROOT_INDEX, index)


def main() -> int:
    for path in (SOURCE_HTML, SIDE_CAR, V17_MANIFEST):
        if not path.exists():
            raise FileNotFoundError(path)
    curve = json.loads(SIDE_CAR.read_text(encoding="utf-8"))
    source_identity = build_source_identity()
    reference_identity = build_reference_identity()
    report = build_deltadata_pilot(
        curve,
        source_identity=source_identity,
        reference_identity=reference_identity,
    )
    report_sha256 = write_json(RECORD_DIR / "deltadata_2025_abu_dhabi.json", report)
    write_method_card(report)
    write_data_gap_audit(report)
    (RECORD_DIR / "attempts.jsonl").write_text(
        json.dumps(
            {
                "attempt": 1,
                "issue": "X web extraction returned no readable lines for the profile page",
                "hypothesis": "X client-rendered HTML may still expose profile metadata and current public posts",
                "action": "cached https://x.com/DeltaData_ through HTTP 200 and froze profile HTML plus two public post images",
                "output": "HTTP_200; exact code and selector remain absent; method-equivalent path retained",
                "next_step": "publish METHOD_EQUIVALENT_ONLY and SKIPPED_OPAQUE_METHOD for one-to-one claim",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    target_report_path = f"data/reference-analysis-lab/v1/run={RUN_ID}/report.json"
    target_manifest_path = f"data/reference-analysis-lab/v1/run={RUN_ID}/manifest.json"
    target_manifest = {
        "schema_version": "reference-analysis-lab-target-manifest-v1",
        "target_id": "deltadata",
        "status": report["status"],
        "run_id": RUN_ID,
        "report": {
            "path": target_report_path,
            "sha256": report_sha256,
            "bytes": (RECORD_DIR / "deltadata_2025_abu_dhabi.json").stat().st_size,
        },
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
        "frontend_page": "delta-data-pilot.html",
    }
    frontend_report_path = FRONTEND_DIR / "report.json"
    frontend_report_sha256 = write_json(frontend_report_path, report)
    if frontend_report_sha256 != report_sha256:
        raise RuntimeError("前端复制报告哈希不一致")
    write_json(FRONTEND_DIR / "manifest.json", target_manifest)
    update_frontend_index(target_manifest_path, report_sha256)
    manifest = {
        "schema_version": "reference-analysis-lab-record-manifest-v1",
        "target_id": "deltadata",
        "status": report["status"],
        "run_id": RUN_ID,
        "report": {
            "path": str((RECORD_DIR / "deltadata_2025_abu_dhabi.json").relative_to(ROOT)).replace("\\", "/"),
            "sha256": report_sha256,
            "bytes": (RECORD_DIR / "deltadata_2025_abu_dhabi.json").stat().st_size,
        },
        "method_card": str((RECORD_DIR / "method_card.md").relative_to(ROOT)).replace("\\", "/"),
        "data_gap_audit": str((RECORD_DIR / "data_gap_audit.md").relative_to(ROOT)).replace("\\", "/"),
        "attempts": str((RECORD_DIR / "attempts.jsonl").relative_to(ROOT)).replace("\\", "/"),
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
    }
    write_json(RECORD_DIR / "manifest.json", manifest)
    print(json.dumps({"status": "PASS", "run_id": RUN_ID, "report_sha256": report_sha256, "record_dir": str(RECORD_DIR)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
