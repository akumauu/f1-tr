#!/usr/bin/env python3
"""把科研 JSON 报告导出为静态工作台可读取的数据合同。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "frontend" / "public" / "data" / "telemetry-workbench"


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return text or "report"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"工作台报告必须是 JSON object：{path}")
    return value


def _curve_evidence_source(
    report_path: Path,
    reference: Mapping[str, Any],
) -> Path:
    raw = Path(str(reference.get("path") or ""))
    candidates = []
    if raw.is_absolute():
        candidates.append(raw.resolve())
    else:
        candidates.extend(
            [
                (ROOT / raw).resolve(),
                (report_path.parent / raw).resolve(),
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            if report_path.is_relative_to(ROOT) and not candidate.is_relative_to(
                ROOT
            ):
                raise ValueError("项目内报告的曲线 sidecar 不能位于项目外")
            return candidate
    raise FileNotFoundError(
        f"曲线证据 sidecar 不存在：{reference.get('path')}"
    )


def _scope(report: Mapping[str, Any]) -> Mapping[str, Any]:
    value = report.get("scope")
    if isinstance(value, Mapping):
        return value
    config = report.get("config")
    if isinstance(config, Mapping) and isinstance(config.get("scope"), Mapping):
        return config["scope"]
    return {}


def _report_id(path: Path, report: Mapping[str, Any]) -> str:
    explicit = report.get("report_id") or report.get("run_id")
    return _slug(str(explicit or path.stem))


def _title(scope: Mapping[str, Any], report_id: str) -> str:
    labels = [
        scope.get("year"),
        scope.get("meeting"),
        scope.get("session"),
        scope.get("team"),
    ]
    text = " · ".join(str(value) for value in labels if value not in (None, ""))
    return text or report_id


def report_paths_from_race_dossier_manifest(manifest_path: Path) -> list[Path]:
    """按追加式清单明确列出的路径取报告，不扫描输出目录。"""

    manifest = _json(manifest_path.resolve())
    rows = manifest.get("events")
    if not isinstance(rows, list):
        raise ValueError("Race Dossier manifest 缺少 events 数组")
    paths: list[Path] = []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("path"):
            raise ValueError("Race Dossier manifest.events 存在无效行")
        path = (ROOT / str(row["path"])).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError(f"Race Dossier 报告不能位于项目外：{path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        if row.get("sha256") and _sha256(path) != row["sha256"]:
            raise RuntimeError(f"Race Dossier 报告 SHA-256 不一致：{path}")
        paths.append(path)
    return paths


def export_reports(
    report_paths: Iterable[Path],
    output_root: Path = DEFAULT_OUT,
    *,
    append: bool = False,
) -> dict[str, Any]:
    """原子导出报告与 manifest；不改变科研原始产物。"""

    output_root.mkdir(parents=True, exist_ok=True)
    reports_dir = output_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    manifest_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if append and manifest_path.is_file():
        existing = _json(manifest_path)
        existing_rows = existing.get("reports")
        if not isinstance(existing_rows, list):
            raise ValueError("现有工作台 manifest 缺少 reports 数组")
        for row in existing_rows:
            if not isinstance(row, Mapping) or not row.get("id"):
                raise ValueError("现有工作台 manifest 存在无效报告行")
            report_id = str(row["id"])
            if report_id in seen:
                raise ValueError(f"现有 manifest 重复 report id：{report_id}")
            destination = (output_root / str(row["path"])).resolve()
            if (
                not destination.is_relative_to(output_root.resolve())
                or not destination.is_file()
            ):
                raise FileNotFoundError(destination)
            if row.get("export_sha256") and _sha256(destination) != row[
                "export_sha256"
            ]:
                raise RuntimeError(
                    f"现有工作台报告 SHA-256 不一致：{destination}"
                )
            curve = row.get("stint_curve_evidence")
            if isinstance(curve, Mapping):
                curve_destination = (
                    output_root / str(curve.get("path") or "")
                ).resolve()
                if (
                    not curve_destination.is_relative_to(output_root.resolve())
                    or not curve_destination.is_file()
                ):
                    raise FileNotFoundError(curve_destination)
                if curve.get("export_sha256") and _sha256(
                    curve_destination
                ) != curve["export_sha256"]:
                    raise RuntimeError(
                        "现有工作台曲线 sidecar SHA-256 不一致："
                        f"{curve_destination}"
                    )
            seen.add(report_id)
            manifest_rows.append(dict(row))
    for raw_path in report_paths:
        path = raw_path.resolve()
        report = _json(path)
        report_id = _report_id(path, report)
        if report_id in seen:
            raise ValueError(f"重复 report id：{report_id}")
        seen.add(report_id)
        scope = dict(_scope(report))
        product = (
            "race_dossier"
            if str(report.get("schema_version", "")).startswith("race-dossier-")
            else "telemetry_explanation"
        )
        destination = reports_dir / f"{report_id}.json"
        if append and destination.exists():
            raise FileExistsError(
                f"追加模式禁止覆盖现有工作台报告：{destination}"
            )
        exported_report = copy.deepcopy(report)
        curve_manifest = None
        curve_reference = report.get("stint_curve_evidence")
        if isinstance(curve_reference, Mapping) and (
            curve_reference.get("status") == "available"
        ):
            curve_source = _curve_evidence_source(path, curve_reference)
            curve_source_hash = _sha256(curve_source)
            declared_hash = str(curve_reference.get("sha256") or "")
            if not declared_hash or curve_source_hash != declared_hash:
                raise RuntimeError(
                    f"曲线 sidecar SHA-256 与报告引用不一致：{curve_source}"
                )
            curve_payload = _json(curve_source)
            if curve_payload.get("report_id") != report.get("report_id"):
                raise ValueError(
                    f"曲线 sidecar report_id 与报告不一致：{curve_source}"
                )
            curve_destination = (
                output_root / "curve-evidence" / f"{report_id}.json"
            )
            if append and curve_destination.exists():
                raise FileExistsError(
                    "追加模式禁止覆盖现有工作台曲线 sidecar："
                    f"{curve_destination}"
                )
            curve_destination.parent.mkdir(parents=True, exist_ok=True)
            curve_temporary = curve_destination.with_suffix(".json.tmp")
            curve_temporary.write_bytes(curve_source.read_bytes())
            curve_temporary.replace(curve_destination)
            curve_export_hash = _sha256(curve_destination)
            frontend_path = curve_destination.relative_to(
                output_root
            ).as_posix()
            exported_report["stint_curve_evidence"] = {
                **dict(curve_reference),
                "frontend_path": frontend_path,
                "frontend_sha256": curve_export_hash,
            }
            curve_manifest = {
                "path": frontend_path,
                "schema_version": curve_reference.get("schema_version"),
                "source_path": (
                    curve_source.relative_to(ROOT).as_posix()
                    if curve_source.is_relative_to(ROOT)
                    else str(curve_source)
                ),
                "source_sha256": curve_source_hash,
                "export_sha256": curve_export_hash,
                "bytes": int(curve_destination.stat().st_size),
                "stints": curve_reference.get("stints"),
                "points": curve_reference.get("points"),
                "directly_comparable_pairs": curve_reference.get(
                    "directly_comparable_pairs"
                ),
            }
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                exported_report,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        manifest_rows.append(
            {
                "id": report_id,
                "title": _title(scope, report_id),
                "path": destination.relative_to(output_root).as_posix(),
                "year": scope.get("year"),
                "meeting": scope.get("meeting"),
                "session": scope.get("session"),
                "team": scope.get("team"),
                "release_status": report.get("release_status")
                or report.get("methodological_status"),
                "product": product,
                "source_path": (
                    path.relative_to(ROOT).as_posix()
                    if path.is_relative_to(ROOT)
                    else str(path)
                ),
                "source_sha256": _sha256(path),
                "export_sha256": _sha256(destination),
                "stint_curve_evidence": curve_manifest,
            }
        )
    manifest_rows.sort(
        key=lambda row: (
            0 if row["product"] == "race_dossier" else 1,
            -(int(row["year"] or -1)),
            row["title"],
        )
    )
    manifest = {
        "version": "telemetry-workbench-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": manifest_rows,
    }
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", default=[])
    parser.add_argument(
        "--race-dossier-manifest",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--append",
        action="store_true",
        help="校验并保留现有报告，只追加新 report id",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    report_paths = [path.resolve() for path in args.report]
    for manifest_path in args.race_dossier_manifest:
        report_paths.extend(
            report_paths_from_race_dossier_manifest(manifest_path.resolve())
        )
    if not report_paths:
        parser.error("至少提供一个 --report 或 --race-dossier-manifest")
    manifest = export_reports(
        report_paths,
        args.out.resolve(),
        append=args.append,
    )
    if not args.quiet:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
