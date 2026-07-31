#!/usr/bin/env python3
"""以前台单线程低资源模式生成 Race Dossier 赛后一致性审计 v1。

只读输入：一个已发布 Dossier run 的 manifest（v15/v16 逐场 JSON，逐个
SHA-256 校验）与同源冻结圈宇宙 artifact。输出：逐场一致性审计、跨
Stint 可比对表与赛季汇总，追加式写盘，禁止覆盖。本 runner 不重新拟合
任何模型，也不修改任何 Dossier 发布门。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import run_experiments as base
import run_race_dossiers_v8 as engine
from modeling.dossier_concordance import (
    build_result_performance_concordance,
    build_season_concordance_summary,
    build_stint_comparison_table,
    derive_driver_classification_proxy,
    derive_event_incident_profile,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "dossier_concordance_2023_2025_v1.json"
)


def normalize_dossier_events(
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """把正式 run 与单场 pilot 两种 manifest 归一成事件清单。"""

    schema = str(manifest.get("schema_version", ""))
    if schema.startswith("race-dossier-run-manifest-"):
        return [
            {
                "year": int(row["year"]),
                "round": int(row["round"]),
                "meeting": str(row["meeting"]),
                "path": str(row["path"]),
                "sha256": str(row["sha256"]),
            }
            for row in manifest["events"]
        ]
    if "pilot-manifest" in schema:
        return [
            {
                "year": int(row["year"]),
                "round": None,
                "meeting": str(row["meeting"]),
                "path": str(row["path"]),
                "sha256": str(row["sha256"]),
            }
            for row in manifest["events"]
        ]
    raise ValueError(f"不支持的 Dossier manifest schema：{schema}")


def _load_lap_universe(
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest_path = engine._project_path(
        config["inputs"]["lap_universe_manifest"], "lap_universe_manifest"
    )
    manifest = base.load_json(manifest_path)
    data_path = engine._project_path(
        manifest["file"]["path"], "lap_universe_file"
    )
    actual_hash = base.file_sha256(data_path)
    if actual_hash != manifest["file"]["sha256"]:
        raise RuntimeError("圈宇宙 SHA-256 与 manifest 不一致")
    frame = pd.read_csv(data_path, compression="gzip")
    identity = {
        "manifest": engine._identity(manifest_path),
        "data": engine._identity(data_path),
        "source_run_id": manifest.get("run_id"),
        "identity_verified": True,
    }
    return frame, identity


def _event_summary_md(report: Mapping[str, Any]) -> str:
    concordance = report["concordance"]
    incident = report["incident_profile"]
    scope = report["scope"]
    clean_text = (
        "通过"
        if incident["clean_race_proxy"]
        else "、".join(incident["not_clean_reasons"])
    )
    lines = [
        f"# 一致性审计：{scope['year']} {scope['meeting']}",
        "",
        f"- 清洁场代理：{incident['clean_race_proxy']}（{clean_text}）",
        f"- 结论：{concordance.get('verdict')}",
    ]
    if concordance.get("status") == "computed":
        tau = concordance["kendall_tau_pace_vs_classification"]
        lines.extend(
            [
                (
                    f"- 车手对：{concordance['pair_count']}，"
                    f"同序 {concordance['concordant_pair_count']}，"
                    f"倒挂已解释 "
                    f"{concordance['explained_inversion_count']}，"
                    f"未解释 "
                    f"{concordance['unexplained_inversion_count']}"
                ),
                f"- Kendall tau（配速 vs 成绩代理）：{tau}",
            ]
        )
        for pair in concordance["unexplained_pairs"]:
            lines.append(
                (
                    f"  - 未解释：{pair['pace_faster_driver']} 配速快 "
                    f"{pair['pace_margin_s']:.1f}s 却落后于 "
                    f"{pair['pace_slower_driver']}（上下文差 "
                    f"{pair['context_delta_s']:.1f}s）"
                )
            )
    stint = report["stint_comparison"]
    lines.append(
        (
            f"- 跨 Stint 可比对：{stint.get('qualified_pair_count', 0)}"
            f"（{stint.get('status')}）"
        )
    )
    lines.append("")
    lines.append(
        "成绩代理来自末圈 position，不含罚时/DSQ/FIA 最终修订；"
        "本文件为赛后审计，不进入任何拟合或发布门。"
    )
    return "\n".join(lines)


def _master_md(
    season: Mapping[str, Any],
    events: list[Mapping[str, Any]],
    run_id: str,
) -> str:
    lines = [
        f"# Dossier 一致性审计 run={run_id}",
        "",
        (
            f"- 覆盖 {season['events_with_concordance']} 场；清洁场 "
            f"{season['clean_race_count']}，其中完全一致 "
            f"{season['clean_race_fully_concordant_count']}，"
            f"存在未解释倒挂 "
            f"{season['clean_race_unexplained_mismatch_count']}"
        ),
        (
            f"- 未解释倒挂合计 "
            f"{season['unexplained_inversions_total']} 对；tau 中位数 "
            f"{season['kendall_tau_pace_vs_classification_median']}"
        ),
        "",
        "## 迭代清单（清洁场未解释倒挂）",
        "",
    ]
    mismatch_rows = season["clean_race_unexplained_mismatch_events"]
    if mismatch_rows:
        for row in mismatch_rows:
            lines.append(
                (
                    f"- {row['year']} {row['meeting']}："
                    f"{row['unexplained_inversion_count']} 对"
                )
            )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            (
                "验收规则：清洁场必须 0 个 unexplained_inversion；"
                "未达标场次进入迭代清单。禁止事后调阈值清零；阈值改动"
                "必须新建 concordance-v2。"
            ),
        ]
    )
    return "\n".join(lines)


def run(
    config: Mapping[str, Any],
    *,
    run_id: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    if not bool(config.get("offline_only")):
        raise ValueError("concordance runner 必须 offline_only=true")
    config_hash = base.canonical_hash(config)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    resolved_run_id = run_id or f"{timestamp}-{config_hash[:12]}"
    records_base = engine._project_path(
        config["outputs"]["records_base"], "records_base"
    )
    records_final = records_base / f"run={resolved_run_id}"
    records_stage = engine._stage_directory(records_final, resolved_run_id)

    dossier_manifest_path = engine._project_path(
        config["inputs"]["dossier_run_manifest"], "dossier_run_manifest"
    )
    dossier_manifest = base.load_json(dossier_manifest_path)
    events = normalize_dossier_events(dossier_manifest)
    laps, lap_identity = _load_lap_universe(config)
    lap_groups = {
        (int(year), str(meeting)): group
        for (year, meeting), group in laps.groupby(
            ["year", "meeting_name"], observed=True
        )
    }

    event_reports: list[dict[str, Any]] = []
    event_manifest_rows: list[dict[str, Any]] = []
    for row in events:
        dossier_path = engine._project_path(row["path"], "race_dossier")
        actual_hash = base.file_sha256(dossier_path)
        if actual_hash != row["sha256"]:
            raise RuntimeError(f"Dossier SHA-256 不一致：{row['path']}")
        dossier = base.load_json(dossier_path)
        key = (row["year"], row["meeting"])
        event_laps = lap_groups.get(key)
        if event_laps is None:
            raise RuntimeError(f"圈宇宙缺少分站：{key}")
        classification = derive_driver_classification_proxy(
            event_laps,
            classified_completion_fraction=float(
                config["classification_proxy"][
                    "classified_completion_fraction"
                ]
            ),
        )
        incident = derive_event_incident_profile(
            event_laps,
            classification,
            dossier,
            clean_gate=config["clean_race_gate"],
        )
        concordance = build_result_performance_concordance(
            dossier,
            classification,
            incident,
            pair_rules=config["pair_explanation"],
        )
        stint_comparison = build_stint_comparison_table(
            dossier, comparison_rules=config["stint_comparison"]
        )
        report = {
            "report_id": (
                f"dossier_concordance_{row['year']}_"
                f"{row['meeting']}_v1"
            ),
            "schema_version": str(config["schema_version"]),
            "scope": {
                "year": row["year"],
                "meeting": row["meeting"],
                "session": "Race",
            },
            "source_dossier": {
                "path": row["path"],
                "sha256": row["sha256"],
                "release_status": dossier.get("release_status"),
                "method_version": dossier.get("schema_version"),
            },
            "classification_proxy_rows": classification,
            "incident_profile": incident,
            "concordance": concordance,
            "stint_comparison": stint_comparison,
            "run_id": resolved_run_id,
            "created_at": base.now_iso(),
            "config_hash": config_hash,
        }
        event_reports.append(report)
        relative = Path(f"year={row['year']}")
        if row["round"] is not None:
            relative = relative / f"round={int(row['round']):02d}"
        relative = relative / f"meeting={engine._slug(row['meeting'])}"
        json_stage = records_stage / relative / "concordance.json"
        engine._write_json_new(json_stage, report)
        engine._write_text_new(
            records_stage / relative / "concordance.md",
            _event_summary_md(report),
        )
        event_manifest_rows.append(
            {
                "year": row["year"],
                "round": row["round"],
                "meeting": row["meeting"],
                "path": (
                    records_final / relative / "concordance.json"
                ).relative_to(ROOT).as_posix(),
                "sha256": base.file_sha256(json_stage),
                "verdict": concordance.get("verdict"),
                "clean_race_proxy": incident["clean_race_proxy"],
                "unexplained_inversion_count": (
                    concordance.get("unexplained_inversion_count")
                    if concordance.get("status") == "computed"
                    else None
                ),
            }
        )
        print(
            (
                f"[concordance] {row['year']} "
                f"{engine._slug(row['meeting'])} - "
                f"{concordance.get('verdict')}"
            ),
            flush=True,
        )

    season = build_season_concordance_summary(event_reports)
    season.update(
        {
            "run_id": resolved_run_id,
            "created_at": base.now_iso(),
            "config_hash": config_hash,
        }
    )
    engine._write_json_new(
        records_stage / "season_concordance_summary.json", season
    )
    manifest = {
        "schema_version": "dossier-concordance-run-manifest-v1",
        "run_id": resolved_run_id,
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config_hash": config_hash,
        "methodological_status": config["validation_status"],
        "offline_only": True,
        "network_calls": [],
        "input_identities": {
            "config": (
                engine._identity(Path(config_path))
                if Path(config_path).is_file()
                else None
            ),
            "dossier_run_manifest": engine._identity(
                dossier_manifest_path
            ),
            "lap_universe": lap_identity,
            "implementation": [
                engine._identity(path)
                for path in (
                    ROOT
                    / "research"
                    / "modeling"
                    / "dossier_concordance.py",
                    ROOT
                    / "research"
                    / "run_dossier_concordance_v1.py",
                )
                if path.is_file()
            ],
        },
        "coverage": {
            "events": len(event_manifest_rows),
            "clean_races": season["clean_race_count"],
            "clean_race_fully_concordant": season[
                "clean_race_fully_concordant_count"
            ],
            "clean_race_unexplained_mismatch": season[
                "clean_race_unexplained_mismatch_count"
            ],
            "unexplained_inversions_total": season[
                "unexplained_inversions_total"
            ],
        },
        "events": event_manifest_rows,
        "season_summary_path": (
            records_final / "season_concordance_summary.json"
        ).relative_to(ROOT).as_posix(),
    }
    engine._write_json_new(records_stage / "manifest.json", manifest)
    engine._write_json_new(
        records_stage / "config.snapshot.json", dict(config)
    )
    engine._write_text_new(
        records_stage / "README.md",
        _master_md(season, event_manifest_rows, resolved_run_id),
    )
    records_stage.replace(records_final)
    return {
        "run_id": resolved_run_id,
        "records": records_final.relative_to(ROOT).as_posix(),
        "coverage": manifest["coverage"],
        "manifest_sha256": base.file_sha256(
            records_final / "manifest.json"
        ),
    }


def main() -> int:
    engine._apply_low_resource_process_policy()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="覆盖配置中的 dossier_run_manifest（仓库内相对或绝对路径）",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    if not config_path.is_relative_to(ROOT):
        parser.error("--config 必须位于项目目录内")
    config = base.load_json(config_path)
    if args.manifest is not None:
        manifest_path = args.manifest
        if manifest_path.is_absolute():
            manifest_path = manifest_path.resolve()
            if not manifest_path.is_relative_to(ROOT):
                parser.error("--manifest 必须位于项目目录内")
            manifest_value = manifest_path.relative_to(ROOT).as_posix()
        else:
            manifest_value = manifest_path.as_posix()
        config["inputs"]["dossier_run_manifest"] = manifest_value
    result = run(config, run_id=args.run_id, config_path=config_path)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
