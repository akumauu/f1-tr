#!/usr/bin/env python3
"""低资源验证 v16 单一 Stint 胎龄代表配速，不发布正式产物。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_experiments as base  # noqa: E402
import run_race_dossiers_v8 as engine  # noqa: E402
import run_race_dossiers_v16 as v16_runner  # noqa: E402
from modeling.race_dossier import (  # noqa: E402
    build_race_dossier,
    validate_race_dossier_config,
)


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def validate_event(
    *,
    year: int,
    meeting: str,
    driver: str,
    report_out: Path | None = None,
) -> dict[str, Any]:
    config_path = (
        ROOT
        / "research"
        / "configs"
        / "race_dossier_2023_2025_v16.json"
    )
    config = v16_runner.load_resolved_config(config_path)
    validate_race_dossier_config(config)
    manifest_path = engine._project_path(
        config["inputs"]["lap_universe_manifest"],
        "lap_universe_manifest",
    )
    manifest = base.load_json(manifest_path)
    lap_path = engine._project_path(
        manifest["file"]["path"],
        "lap_universe_file",
    )
    if base.file_sha256(lap_path) != manifest["file"]["sha256"]:
        raise RuntimeError("冻结逐圈宇宙 SHA-256 校验失败")

    lap_universe = pd.read_csv(lap_path, compression="gzip")
    event_laps = lap_universe.loc[
        lap_universe["year"].eq(year)
        & lap_universe["meeting_name"].eq(meeting)
    ].copy()
    if event_laps.empty:
        raise ValueError(f"未找到真实比赛：{year} {meeting}")

    qualifying, _ = engine._load_qualifying(config)
    event_qualifying = qualifying.loc[
        qualifying["year"].eq(year)
        & qualifying["meeting_name"].eq(meeting)
    ].copy()
    v5_index, _ = engine._load_v5_index(config)
    classification_proxy = v5_index.get(
        (year, meeting), {}
    ).get("classification_proxy")
    report = build_race_dossier(
        event_laps,
        event_qualifying,
        config,
        classification_proxy=classification_proxy,
    )
    report["validation_scope"] = (
        "single_event_real_data_pilot_not_full_2023_2025_release"
    )
    report_path_text = None
    if report_out is not None:
        resolved_output = report_out.resolve()
        allowed_root = (
            ROOT / "research" / "records" / "race_dossier_v16"
        ).resolve()
        if (
            not resolved_output.is_relative_to(allowed_root)
            or resolved_output.drive.upper() != "D:"
            or resolved_output.name != "race_dossier.json"
        ):
            raise ValueError(
                "pilot 报告只能写入 D 盘项目内 race_dossier_v16/"
                "pilot=*/race_dossier.json"
            )
        run_id = resolved_output.parent.name.removeprefix("pilot=")
        report["run_id"] = run_id
        report["created_at"] = base.now_iso()
        report["config_hash"] = base.canonical_hash(config)
        engine._write_json_new(resolved_output, report)
        manifest_path = resolved_output.parent / "manifest.json"
        engine._write_json_new(
            manifest_path,
            {
                "schema_version": (
                    "race-dossier-single-event-pilot-manifest-v16"
                ),
                "run_id": run_id,
                "created_at": base.now_iso(),
                "config_hash": report["config_hash"],
                "method_version": "v16",
                "full_season_release": False,
                "expected_full_events": int(
                    config["scope"]["expected_events"]
                ),
                "pilot_events": 1,
                "events": [
                    {
                        "year": year,
                        "meeting": meeting,
                        "path": resolved_output.relative_to(
                            ROOT
                        ).as_posix(),
                        "sha256": base.file_sha256(resolved_output),
                    }
                ],
                "scientific_boundary": (
                    "单场真实数据验收，只验证 Stint 代表配速字段，"
                    "不构成 2023–2025 全量发布。"
                ),
            },
        )
        report_path_text = resolved_output.relative_to(ROOT).as_posix()

    selected = [
        row
        for row in report["stint_dossiers"]
        if row["driver"] == driver
    ]
    if not selected:
        raise ValueError(f"{driver} 在该场没有 Stint")

    checks = {
        "one_row_per_driver_stint": len(selected)
        == len(
            {
                (row["driver"], row["stint_number"])
                for row in selected
            }
        ),
        "all_have_single_reference_pace": all(
            _finite(row["representative_tyre_age_pace_s"])
            for row in selected
        ),
        "all_reference_ages_in_support": all(
            _finite(row["representative_tyre_age_laps"])
            and _finite(row["representative_tyre_age_support_laps"][0])
            and _finite(row["representative_tyre_age_support_laps"][1])
            and float(row["representative_tyre_age_support_laps"][0])
            <= float(row["representative_tyre_age_laps"])
            <= float(row["representative_tyre_age_support_laps"][1])
            for row in selected
        ),
        "all_have_tyre_age_slope": all(
            _finite(row["degradation_s_per_tyre_lap"])
            for row in selected
        ),
        "all_have_fuel_sensitivity_interval": all(
            isinstance(
                row[
                    "representative_tyre_age_pace_"
                    "fuel_sensitivity_interval_s"
                ],
                list,
            )
            and len(
                row[
                    "representative_tyre_age_pace_"
                    "fuel_sensitivity_interval_s"
                ]
            )
            == 2
            for row in selected
        ),
        "ratings_remain_null": report["ratings"]
        == {"PAC": None, "OVR": None},
    }
    if not all(checks.values()):
        raise RuntimeError(f"v16 真实比赛验证失败：{checks}")

    return {
        "schema_version": report["schema_version"],
        "event": {
            "year": year,
            "meeting": meeting,
            "observed_field_laps": report["coverage"][
                "observed_field_laps"
            ],
            "drivers": int(event_laps["driver_id"].nunique()),
            "inclusive_model_status": report[
                "inclusive_robust_model_audit"
            ]["status"],
        },
        "driver": driver,
        "stints": [
            {
                "stint_number": row["stint_number"],
                "compound": row["compound"],
                "lap_range": [row["lap_start"], row["lap_end"]],
                "effective_weight_laps": row[
                    "effective_weight_laps"
                ],
                "reference_tyre_age_laps": row[
                    "representative_tyre_age_laps"
                ],
                "reference_tyre_age_support_laps": row[
                    "representative_tyre_age_support_laps"
                ],
                "representative_pace_s": row[
                    "representative_tyre_age_pace_s"
                ],
                "fuel_sensitivity_interval_s": row[
                    "representative_tyre_age_pace_"
                    "fuel_sensitivity_interval_s"
                ],
                "tyre_age_slope_s_per_lap": row[
                    "degradation_s_per_tyre_lap"
                ],
                "raw_net_slope_s_per_lap": row[
                    "raw_net_slope_s_per_tyre_lap"
                ],
                "status": row["status"],
            }
            for row in selected
        ],
        "checks": checks,
        "pilot_report_path": report_path_text,
        "scientific_boundary": (
            "代表配速是公开燃油质量敏感性与可观测条件修正后的胎龄条件代理；"
            "不是真实油量、真实胎态或车队内部模拟。"
        ),
    }


def main() -> int:
    engine._apply_low_resource_process_policy()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument(
        "--meeting",
        default="Abu Dhabi Grand Prix",
    )
    parser.add_argument("--driver", default="BOR")
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_event(
                year=args.year,
                meeting=args.meeting,
                driver=args.driver,
                report_out=args.report_out,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
