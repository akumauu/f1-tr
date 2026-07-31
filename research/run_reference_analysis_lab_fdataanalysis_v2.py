#!/usr/bin/env python3
"""发布 FDataAnalysis 算法倒推与方法等价复刻 v2。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from reference_analysis_lab.fdataanalysis_reverse_engineered_v2 import (
    build_fdataanalysis_reverse_engineered_v2,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260731T170000+0800-fdataanalysis-reverse-engineered-v2"
TARGET_ID = "fdataanalysis-reverse-engineered-v2"
STATUS = "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
CAPTURED_AT = "2026-07-31T16:30:00+08:00"

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
TRAFFIC_CACHE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v2"
    / "deltadata"
    / "traffic-features"
    / "2025-abu-dhabi-race.parquet"
)
TRAFFIC_IDENTITY = TRAFFIC_CACHE.with_suffix(".identity.json")
V17_RUN_ID = "20260726T124625+0800-ce93134a40b8"
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / f"run={V17_RUN_ID}"
    / "manifest.json"
)
V1_REPORT = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v1"
    / "fdataanalysis"
    / "run=20260730T235950+0800-fdataanalysis-abu-dhabi-v1"
    / "fdataanalysis_2025_abu_dhabi.json"
)
V1_SOURCE_HTML = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v1"
    / "fdataanalysis"
    / "source.html"
)
REFERENCE_IMAGES = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v1"
    / "fdataanalysis"
    / "reference-images"
)
RACE_PACE_IMAGE = REFERENCE_IMAGES / "race-pace-hungarian-v2.jpg"
LONG_RUN_IMAGE = REFERENCE_IMAGES / "long-runs-fp2-hungarian.jpg"
INVALID_IMAGE = REFERENCE_IMAGES / "race-pace-hungarian.jpg"

CACHE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v2"
    / "fdataanalysis"
)
RECORD_DIR = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v2"
    / "fdataanalysis"
    / f"run={RUN_ID}"
)
FRONTEND_BASE = (
    ROOT
    / "frontend"
    / "public"
    / "data"
    / "reference-analysis-lab"
    / "v2"
)
FRONTEND_DIR = FRONTEND_BASE / f"run={RUN_ID}"
FRONTEND_INDEX = FRONTEND_BASE / "manifest.json"

TELEMETRY_COLUMNS = (
    "driver_acronym",
    "lap",
    "sample_index",
    "time",
    "distance",
    "rel_distance",
    "speed",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "drs",
    "acc_x",
    "acc_y",
    "lap_duration",
    "compound",
    "stint",
    "tyre_life",
    "team",
    "track_status",
    "deleted",
    "is_accurate",
    "position",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")


def write_bytes_once(path: Path, content: bytes) -> str:
    if path.exists():
        raise FileExistsError(f"追加式实验拒绝覆盖已有文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def write_json_once(path: Path, payload: Any) -> str:
    return write_bytes_once(path, _json_bytes(payload))


def write_text_once(path: Path, text: str) -> str:
    return write_bytes_once(path, (text.rstrip() + "\n").encode("utf-8"))


def write_csv_once(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    content = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    digest = write_bytes_once(path, content)
    return {
        "path": _relative(path),
        "sha256": digest,
        "bytes": len(content),
        "rows": int(len(frame)),
        "columns": list(frame.columns),
    }


def build_source_identity() -> dict[str, Any]:
    parquet = pq.ParquetFile(TELEMETRY)
    traffic_identity = json.loads(
        TRAFFIC_IDENTITY.read_text(encoding="utf-8")
    )
    if traffic_identity["telemetry_sha256"] != sha256_file(TELEMETRY):
        raise RuntimeError("交通缓存与 Abu Dhabi telemetry 哈希不一致")
    if traffic_identity["traffic_cache_sha256"] != sha256_file(TRAFFIC_CACHE):
        raise RuntimeError("交通缓存 identity 中的哈希不一致")
    return {
        "run_id": RUN_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "real_tracinginsights_expanded_v4",
        "year": 2025,
        "meeting": "Abu Dhabi Grand Prix",
        "session": "Race",
        "telemetry": {
            "path": _relative(TELEMETRY),
            "sha256": sha256_file(TELEMETRY),
            "rows": int(parquet.metadata.num_rows),
            "columns": parquet.schema_arrow.names,
            "source_commit": "f7a5324cae58",
        },
        "traffic_features": {
            "path": _relative(TRAFFIC_CACHE),
            "sha256": sha256_file(TRAFFIC_CACHE),
            "identity_path": _relative(TRAFFIC_IDENTITY),
            "identity_sha256": sha256_file(TRAFFIC_IDENTITY),
            "rows": int(traffic_identity["traffic_rows"]),
            "definition": traffic_identity["definition"],
            "role": "reused_f1pace_v2_point_level_physical_headway_cache",
        },
        "v17_manifest": {
            "path": _relative(V17_MANIFEST),
            "sha256": sha256_file(V17_MANIFEST),
            "run_id": V17_RUN_ID,
            "role": "coverage_and_audit_reference_only_no_v17_rewrite",
        },
    }


def build_reference_evidence() -> dict[str, Any]:
    evidence = {
        "schema_version": "fdataanalysis-public-method-evidence-v2",
        "captured_at": CAPTURED_AT,
        "official_profile": {
            "url": "https://x.com/FDataAnalysis",
            "status": "PROFILE_AVAILABLE_POST_BODY_NOT_RELIABLY_EXTRACTABLE",
        },
        "same_creator_cross_profile": {
            "url": "https://bsky.app/profile/fdataanalysis.bsky.social",
            "status": "PROFILE_METADATA_AVAILABLE",
            "profile_claim": (
                "Learn To Read F1 Telemetry Data; performance engineer; "
                "PhD in motorcycle dynamics"
            ),
        },
        "public_method_sources": [
            {
                "url": (
                    "https://www.reddit.com/r/F1DataAnalysis/comments/"
                    "1di5ciz/qualifying_telemetry_analysis_2022_barhain_gp/"
                ),
                "role": "archived_secondary_repost_of_creator_analysis",
                "observable_method": [
                    "speed versus lap distance",
                    "corner entry, apex/minimum and exit speed",
                    "braking later and accelerating earlier",
                    "terminal speed as an observed output",
                    "150–250 km/h straight acceleration curve slope",
                    "creator interprets some outputs as grip/downforce/power/drag",
                ],
                "boundary": (
                    "文字可用于复刻观测特征；物理解释受燃油、SOC、设定和"
                    "动力模式混杂，F1 TR 不直接复用其因果命名。"
                ),
            },
            {
                "url": (
                    "https://www.overtake.gg/news/2025-formula-one-"
                    "spanish-grand-prix-community-review.3256/"
                ),
                "role": "secondary_article_quoting_creator_top_speed_chart",
                "observable_method": [
                    "maximum speed",
                    "average of the 15 highest speeds",
                    "minimum speed in the slowest corner",
                    "DRS context",
                ],
            },
            {
                "url": (
                    "https://www.reddit.com/r/formula1/comments/"
                    "1govia2/fdataanalysis_ive_spent_the_whole_season/"
                ),
                "role": "community_repost_and_method_limitation_discussion",
                "observable_method": [
                    "corner-profile speeds",
                    "speed change through a stint",
                    "methodology and missing-variable concerns are not fully disclosed",
                ],
            },
        ],
        "reference_images": [
            {
                "name": "2026 Hungarian Grand Prix Race Pace",
                "post_url": (
                    "https://x.com/FDataAnalysis/status/2081706503862378548"
                ),
                "cache_path": _relative(RACE_PACE_IMAGE),
                "sha256": sha256_file(RACE_PACE_IMAGE),
                "status": "REFERENCE_ONLY_DIFFERENT_EVENT_SESSION",
                "manual_transcription": {
                    "chart_semantics": [
                        "pit-equated driver gap table",
                        "box/mean/median distribution",
                        "smoothed lap-by-lap race pace",
                    ],
                    "leading_rows": [
                        {"driver": "NOR", "gap_s": 0.00},
                        {"driver": "VER", "gap_s": 0.11},
                        {"driver": "ANT", "gap_s": 0.15},
                        {"driver": "HAM", "gap_s": 0.29},
                    ],
                },
            },
            {
                "name": "2026 Hungarian Grand Prix FP2 Long Runs",
                "post_url": (
                    "https://x.com/FDataAnalysis/status/2081027167987634407"
                ),
                "cache_path": _relative(LONG_RUN_IMAGE),
                "sha256": sha256_file(LONG_RUN_IMAGE),
                "status": "REFERENCE_ONLY_DIFFERENT_EVENT_SESSION",
                "manual_transcription": {
                    "chart_semantics": [
                        "lap time versus tyre age",
                        "driver violin distribution",
                        "compound point colour",
                        "fastest/mean gap/standard deviation/push laps",
                    ],
                    "rows": [
                        {
                            "driver": "ANT",
                            "gap_s": 0.000,
                            "displayed_lap_time_s": 83.946,
                            "sd_s": 0.188,
                            "push_laps": 6,
                        },
                        {
                            "driver": "VER",
                            "gap_s": 0.604,
                            "displayed_lap_time_s": 84.550,
                            "sd_s": 0.824,
                            "push_laps": 12,
                        },
                        {
                            "driver": "LEC",
                            "gap_s": 0.847,
                            "displayed_lap_time_s": 84.793,
                            "sd_s": 1.119,
                            "push_laps": 7,
                        },
                        {
                            "driver": "HAM",
                            "gap_s": 0.890,
                            "displayed_lap_time_s": 84.836,
                            "sd_s": 0.326,
                            "push_laps": 5,
                        },
                        {
                            "driver": "NOR",
                            "gap_s": 0.897,
                            "displayed_lap_time_s": 84.843,
                            "sd_s": 0.714,
                            "push_laps": 11,
                        },
                        {
                            "driver": "RUS",
                            "gap_s": 1.004,
                            "displayed_lap_time_s": 84.950,
                            "sd_s": 0.919,
                            "push_laps": 12,
                        },
                        {
                            "driver": "PIA",
                            "gap_s": 1.554,
                            "displayed_lap_time_s": 85.500,
                            "sd_s": 0.315,
                            "push_laps": 8,
                        },
                    ],
                },
            },
        ],
        "invalid_cached_asset": {
            "cache_path": _relative(INVALID_IMAGE),
            "sha256": sha256_file(INVALID_IMAGE),
            "status": "INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH",
            "reason": (
                "图片是 Stefano Domenicali 引语海报，不是 FDataAnalysis "
                "race-pace 或 telemetry 图，禁止进入方法证据和产品资产。"
            ),
        },
        "licensing_boundary": (
            "只重新实现公开逻辑和布局语义；不复制原图、logo、品牌字体或人物照片。"
        ),
    }
    cache_path = CACHE / "public-method-evidence.json"
    content = _json_bytes(evidence)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        if cache_path.read_bytes() != content:
            raise RuntimeError("已有 FDataAnalysis 方法证据缓存内容不同")
    else:
        cache_path.write_bytes(content)
    evidence["cache_path"] = _relative(cache_path)
    evidence["sha256"] = sha256_file(cache_path)
    return evidence


def build_reference_identity(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source_profile_url": "https://x.com/FDataAnalysis",
        "source_profile_cache_path": _relative(V1_SOURCE_HTML),
        "source_profile_sha256": sha256_file(V1_SOURCE_HTML),
        "source_profile_fetch_status": "HTTP_200_METADATA_ONLY",
        "captured_at": CAPTURED_AT,
        "evidence_snapshot_path": evidence["cache_path"],
        "evidence_snapshot_sha256": evidence["sha256"],
        "images": evidence["reference_images"],
        "invalid_cached_asset": evidence["invalid_cached_asset"],
        "same_event_numeric_reference_available": False,
        "reference_event": "2026 Hungarian Grand Prix",
        "pilot_event": "2025 Abu Dhabi Grand Prix Race",
        "numeric_comparison_status": (
            "NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION"
        ),
        "license_or_republication_boundary": evidence[
            "licensing_boundary"
        ],
    }


def build_legacy_comparison(report: Mapping[str, Any]) -> dict[str, Any]:
    legacy = json.loads(V1_REPORT.read_text(encoding="utf-8"))
    legacy_profiles = {
        str(row["driver"]): row
        for row in legacy["visual_replication"]["driver_profiles"]
    }
    current_profiles = {
        str(row["driver"]): row
        for row in report["visual_replication"]["driver_profiles"]
    }
    common = sorted(set(legacy_profiles) & set(current_profiles))
    slope_rows: list[dict[str, Any]] = []
    throttle_rows: list[dict[str, Any]] = []
    for driver in common:
        old_slope = legacy_profiles[driver]["tyre_age_effects"][
            "lap_time_s_per_tyre_lap"
        ]
        new_slope = current_profiles[driver]["visual_tyre_age_change"][
            "raw_lap_time_s_per_tyre_lap"
        ]
        slope_rows.append(
            {
                "driver": driver,
                "v1_raw_slope_s_per_tyre_lap": old_slope,
                "v2_raw_visual_slope_s_per_tyre_lap": new_slope,
                "v2_minus_v1": (
                    float(new_slope) - float(old_slope)
                    if new_slope is not None and old_slope is not None
                    else None
                ),
            }
        )
        old_throttle = legacy_profiles[driver]["control_usage"][
            "full_throttle_share"
        ]
        new_throttle = current_profiles[driver]["control_usage"][
            "audited_time_full_throttle_share"
        ]
        throttle_rows.append(
            {
                "driver": driver,
                "v1_time_full_throttle_share": old_throttle,
                "v2_time_full_throttle_share": new_throttle,
                "v2_minus_v1_pp": (
                    100.0 * (float(new_throttle) - float(old_throttle))
                    if new_throttle is not None and old_throttle is not None
                    else None
                ),
            }
        )

    def finite_abs(rows: list[dict[str, Any]], key: str) -> list[float]:
        return [
            abs(float(row[key]))
            for row in rows
            if row.get(key) is not None
        ]

    legacy_exit_below_minimum = sum(
        1
        for profile in legacy_profiles.values()
        for corner in profile.get("corner_features", [])
        if corner.get("exit_speed_kph") is not None
        and corner.get("minimum_speed_kph") is not None
        and float(corner["exit_speed_kph"])
        < float(corner["minimum_speed_kph"])
    )
    current_exit_below_minimum = sum(
        1
        for profile in current_profiles.values()
        for corner in profile.get("corner_features", [])
        if corner.get("exit_speed_kph") is not None
        and corner.get("minimum_speed_kph") is not None
        and float(corner["exit_speed_kph"])
        < float(corner["minimum_speed_kph"]) - 1e-9
    )
    slope_abs = finite_abs(slope_rows, "v2_minus_v1")
    throttle_abs = finite_abs(throttle_rows, "v2_minus_v1_pp")
    return {
        "legacy_report": {
            "path": _relative(V1_REPORT),
            "sha256": sha256_file(V1_REPORT),
            "run_id": legacy["run_id"],
            "status": legacy["status"],
        },
        "comparison_role": (
            "same_event_v1_to_v2_method_change_diagnostic_not_creator_ground_truth"
        ),
        "structural_gap": {
            "axis_bins_v1": int(legacy["validation"]["axis_bins"]),
            "axis_intervals_v2": int(
                report["validation"]["axis_intervals"]
            ),
            "distance_resolution_multiplier": (
                float(report["validation"]["axis_intervals"])
                / float(legacy["validation"]["axis_bins"])
            ),
            "corner_proxies_v1": int(
                legacy["validation"]["corner_proxy_count"]
            ),
            "corner_proxies_v2": int(
                report["validation"]["corner_proxy_count"]
            ),
            "corner_proxy_count_delta": (
                int(report["validation"]["corner_proxy_count"])
                - int(legacy["validation"]["corner_proxy_count"])
            ),
            "v1_exit_below_reported_minimum_rows": (
                legacy_exit_below_minimum
            ),
            "v2_exit_below_reported_minimum_rows": (
                current_exit_below_minimum
            ),
            "top15_public_formula_v1": "NOT_IMPLEMENTED",
            "top15_public_formula_v2": "IMPLEMENTED",
            "lap_time_reconstruction_v1": "NOT_TESTED",
            "lap_time_reconstruction_v2_mae_s": report["validation"][
                "lap_time_reconstruction"
            ]["mae_s"],
            "same_team_v1": (
                "20 directional unconditional scalar differences"
            ),
            "same_team_v2": (
                f"{report['validation']['teammate_matching']['teams']} "
                "canonical pairs with condition gates; "
                f"{report['validation']['teammate_matching']['comparable_pairs']} "
                "COMPARABLE"
            ),
        },
        "same_formula_checks": {
            "shared_drivers": len(common),
            "raw_tyre_slope_mae_s_per_tyre_lap": (
                float(np.mean(slope_abs)) if slope_abs else None
            ),
            "raw_tyre_slope_max_abs_s_per_tyre_lap": (
                float(np.max(slope_abs)) if slope_abs else None
            ),
            "time_full_throttle_mae_pp": (
                float(np.mean(throttle_abs)) if throttle_abs else None
            ),
            "time_full_throttle_max_abs_pp": (
                float(np.max(throttle_abs)) if throttle_abs else None
            ),
        },
        "raw_tyre_slope_rows": slope_rows,
        "full_throttle_rows": throttle_rows,
        "reference_chart_gap": {
            "status": "NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION",
            "reference": "2026 Hungarian GP Race/FP2",
            "pilot": "2025 Abu Dhabi GP Race",
            "numeric_mae_allowed": False,
            "why": (
                "赛道、年份、车手阵容、赛制和 session 不同；硬算 MAE "
                "会把赛事差异误报成算法误差。"
            ),
            "semantic_coverage": {
                "speed_distance_features": "IMPLEMENTED_FROM_PUBLIC_METHOD_TEXT",
                "entry_min_exit": "IMPLEMENTED_AS_NON_OFFICIAL_PROXY",
                "top15_speed": "IMPLEMENTED",
                "tyre_age_curve": "IMPLEMENTED_WITH_AUDITED_COMPANION",
                "push_lap_manual_labels": "NOT_AVAILABLE",
                "pit_equated_race_pace": (
                    "OUT_OF_SCOPE_FOR_THIS_TARGET; F1pace/DeltaData pages cover pace"
                ),
                "creator_vehicle_total_score": (
                    "NOT_REPLICATED_OPAQUE_AND_CAUSALLY_UNSAFE"
                ),
            },
        },
        "difference_sources": [
            "v1 用 200 个粗分箱和固定 ±1.25% 窗口；v2 用 1,000 区间和动态横向加速度/制动边界。",
            "v1 只识别 6 个大减速区；v2 同时识别高速方向变化，共 16 个稳定代理峰。",
            "v1 的同队差值未平衡配方、胎龄、阶段和交通；v2 一对一条件匹配并 fail-close。",
            "公开最高 15 样本与逐圈 P99 是不同统计量；本场两者差距单独披露，不手工贴数。",
            "真实燃油、SOC、设定和胎态缺失仍是无法消除的主要模型差距。",
        ],
    }


def method_card_markdown(report: Mapping[str, Any]) -> str:
    method = report["method_card"]
    reference = report["reference_identity"]
    source = report["source_identity"]
    lines = [
        "# FDataAnalysis 算法倒推 v2 方法卡",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- status: `{report['status']}`",
        f"- original profile: {method['original_profile']}",
        f"- captured_at: `{reference['captured_at']}`",
        f"- telemetry_sha256: `{source['telemetry']['sha256']}`",
        f"- v17_manifest_sha256: `{source['v17_manifest']['sha256']}`",
        "",
        "## 可公开确认的方法",
        "",
    ]
    lines.extend(f"- {value}" for value in method["publicly_observable_method"])
    lines += ["", "## 倒推或方法等价实现", ""]
    lines.extend(
        f"- {value}" for value in method["reverse_engineered_or_reimplemented"]
    )
    lines += ["", "## 视觉反推与不可识别", ""]
    lines.extend(f"- {value}" for value in method["visual_inference"])
    lines.append("")
    lines.extend(f"- 不可识别：{value}" for value in method["not_identifiable"])
    lines += [
        "",
        "## 参考资产",
        "",
    ]
    for image in reference["images"]:
        lines.append(
            f"- {image['name']}: `{image['sha256']}` · "
            f"`{image['status']}` · {image['post_url']}"
        )
    invalid = reference["invalid_cached_asset"]
    lines += [
        f"- 无效资产：`{invalid['sha256']}` · `{invalid['status']}`",
        "",
        "## 边界",
        "",
        method["reference_boundary"],
    ]
    return "\n".join(lines)


def data_gap_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# FDataAnalysis v2 data-gap audit",
        "",
        "| 目标字段 | 当前字段 | 缺口 | 替代代理 | 发布规则 |",
        "|---|---|---|---|---|",
    ]
    for row in report["data_gap_audit"]:
        lines.append(
            "| {required_field} | {repository_field} | {gap} | "
            "{substitute} | `{publication}` |".format(**row)
        )
    lines += [
        "",
        "结论：允许发布观测速度与控制特征；物理车辆强弱和真实轮胎磨损不可识别，",
        "所有跨车比较必须保留 visual_replication 与 audited_analysis 两套口径。",
    ]
    return "\n".join(lines)


def reference_difference_markdown(
    comparison: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    structural = comparison["structural_gap"]
    validation = report["validation"]
    return "\n".join(
        [
            "# FDataAnalysis v2 与参考/v1 差异",
            "",
            "## 可量化的同场 v1→v2 差异",
            "",
            f"- 距离分辨率：{structural['axis_bins_v1']} → "
            f"{structural['axis_intervals_v2']}，提升 "
            f"{structural['distance_resolution_multiplier']:.1f}×。",
            f"- 弯角代理：{structural['corner_proxies_v1']} → "
            f"{structural['corner_proxies_v2']}，增加 "
            f"{structural['corner_proxy_count_delta']} 个高速/复合弯观察峰。",
            f"- v1 出口速度低于其标称最低速度："
            f"{structural['v1_exit_below_reported_minimum_rows']} 行；v2 为 "
            f"{structural['v2_exit_below_reported_minimum_rows']} 行。",
            f"- 统一轴圈时重构 MAE："
            f"{validation['lap_time_reconstruction']['mae_s']:.6f}s，P90 "
            f"{validation['lap_time_reconstruction']['p90_abs_error_s']:.6f}s。",
            f"- 公开 top15 与审计逐圈 P99 的车手 MAE："
            f"{validation['top_speed_formula_gap']['driver_mae_faithful_top15_vs_audited_p99_kph']:.3f} km/h。",
            f"- 样本全油门与时间加权全油门 MAE："
            f"{validation['full_throttle_weighting_gap']['driver_mae_sample_vs_time_pp']:.3f}pp。",
            "",
            "## 与历史参考图",
            "",
            "- 数值状态：`NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION`。",
            "- 参考是 2026 Hungarian Race/FP2，pilot 是 2025 Abu Dhabi Race；",
            "  不计算伪 MAE。当前只做公式、字段、布局语义和样本披露复核。",
            "",
            "## 主要差距来源",
            "",
            *[f"- {value}" for value in comparison["difference_sources"]],
        ]
    )


def attempts_jsonl() -> str:
    row = {
        "target_id": TARGET_ID,
        "attempt": 1,
        "issue": "NumPy runtime lacks np.trapezoid alias",
        "hypothesis": "runtime NumPy predates the alias; integration formula is available as np.trapz",
        "action": "replace np.trapezoid with mathematically identical np.trapz and add a unit-test identity",
        "output": "resolved; real pilot then completed with lap reconstruction PASS",
        "next_step": "continue full artifact/browser acceptance",
        "status": "RESOLVED",
    }
    return json.dumps(row, ensure_ascii=False)


def update_frontend_index(
    *,
    target_manifest_path: str,
    report_sha256: str,
) -> None:
    index = json.loads(FRONTEND_INDEX.read_text(encoding="utf-8"))
    if any(
        target.get("target_id") == TARGET_ID
        for target in index.get("targets", [])
    ):
        raise RuntimeError(f"{TARGET_ID} 已存在，拒绝覆盖根索引")
    index.setdefault("targets", []).append(
        {
            "target_id": TARGET_ID,
            "label": "FDataAnalysis · telemetry features reverse-engineered v2",
            "status": STATUS,
            "run_id": RUN_ID,
            "manifest": target_manifest_path,
            "report_sha256": report_sha256,
            "page": "fdataanalysis-reverse-engineered-v2.html",
        }
    )
    index["latest_run_id"] = RUN_ID
    FRONTEND_INDEX.write_bytes(_json_bytes(index))


def main() -> int:
    for path in (
        TELEMETRY,
        TRAFFIC_CACHE,
        TRAFFIC_IDENTITY,
        V17_MANIFEST,
        V1_REPORT,
        V1_SOURCE_HTML,
        RACE_PACE_IMAGE,
        LONG_RUN_IMAGE,
        INVALID_IMAGE,
        FRONTEND_INDEX,
    ):
        if not path.exists():
            raise FileNotFoundError(path)
    if RECORD_DIR.exists() or FRONTEND_DIR.exists():
        raise FileExistsError(
            "追加式 run 目录已存在，拒绝覆盖；请使用新的 run_id"
        )

    points = pd.read_parquet(TELEMETRY, columns=list(TELEMETRY_COLUMNS))
    traffic = pd.read_parquet(TRAFFIC_CACHE)
    source_identity = build_source_identity()
    evidence = build_reference_evidence()
    reference_identity = build_reference_identity(evidence)
    report, sidecar_frames = build_fdataanalysis_reverse_engineered_v2(
        points,
        traffic_features=traffic,
        source_identity=source_identity,
        reference_identity=reference_identity,
    )
    if report["validation"]["status"] != "PASS":
        raise RuntimeError("FDataAnalysis v2 验证未通过，拒绝发布 PASS 产物")
    if report["status"] != STATUS:
        raise RuntimeError(
            f"报告状态 {report['status']} 与发布状态 {STATUS} 不一致"
        )

    sidecars: dict[str, Any] = {}
    for name, frame in sidecar_frames.items():
        sidecars[name] = write_csv_once(
            RECORD_DIR / f"{name}.csv",
            frame,
        )
    report["same_event_legacy_comparison"] = build_legacy_comparison(report)
    report["artifacts"] = sidecars

    report_path = RECORD_DIR / "fdataanalysis_reverse_engineered_v2.json"
    report_content = _json_bytes(report)
    report_sha256 = write_bytes_once(report_path, report_content)
    method_sha = write_text_once(
        RECORD_DIR / "method_card.md",
        method_card_markdown(report),
    )
    gap_sha = write_text_once(
        RECORD_DIR / "data_gap_audit.md",
        data_gap_markdown(report),
    )
    difference_sha = write_text_once(
        RECORD_DIR / "reference_difference.md",
        reference_difference_markdown(
            report["same_event_legacy_comparison"],
            report,
        ),
    )
    attempts_sha = write_text_once(
        RECORD_DIR / "attempts.jsonl",
        attempts_jsonl(),
    )

    frontend_report_path = FRONTEND_DIR / "report.json"
    frontend_report_sha = write_bytes_once(
        frontend_report_path,
        report_content,
    )
    if frontend_report_sha != report_sha256:
        raise RuntimeError("前端报告与研究报告哈希不一致")
    target_manifest_path = (
        f"data/reference-analysis-lab/v2/run={RUN_ID}/manifest.json"
    )
    frontend_target_manifest = {
        "schema_version": "reference-analysis-lab-target-manifest-v2",
        "target_id": TARGET_ID,
        "status": STATUS,
        "run_id": RUN_ID,
        "report": {
            "path": f"data/reference-analysis-lab/v2/run={RUN_ID}/report.json",
            "sha256": report_sha256,
            "bytes": len(report_content),
        },
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
        "frontend_page": "fdataanalysis-reverse-engineered-v2.html",
    }
    frontend_manifest_sha = write_json_once(
        FRONTEND_DIR / "manifest.json",
        frontend_target_manifest,
    )
    update_frontend_index(
        target_manifest_path=target_manifest_path,
        report_sha256=report_sha256,
    )

    record_manifest = {
        "schema_version": "reference-analysis-lab-record-manifest-v2",
        "target_id": TARGET_ID,
        "status": STATUS,
        "run_id": RUN_ID,
        "report": {
            "path": _relative(report_path),
            "sha256": report_sha256,
            "bytes": len(report_content),
        },
        "method_card": {
            "path": _relative(RECORD_DIR / "method_card.md"),
            "sha256": method_sha,
        },
        "data_gap_audit": {
            "path": _relative(RECORD_DIR / "data_gap_audit.md"),
            "sha256": gap_sha,
        },
        "reference_difference": {
            "path": _relative(RECORD_DIR / "reference_difference.md"),
            "sha256": difference_sha,
        },
        "attempts": {
            "path": _relative(RECORD_DIR / "attempts.jsonl"),
            "sha256": attempts_sha,
            "resolved_attempts": 1,
        },
        "sidecars": sidecars,
        "frontend_manifest": {
            "path": _relative(FRONTEND_DIR / "manifest.json"),
            "sha256": frontend_manifest_sha,
        },
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
    }
    manifest_sha = write_json_once(
        RECORD_DIR / "manifest.json",
        record_manifest,
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "run_id": RUN_ID,
                "report_sha256": report_sha256,
                "manifest_sha256": manifest_sha,
                "record_dir": str(RECORD_DIR),
                "corner_proxies": report["validation"][
                    "corner_proxy_count"
                ],
                "comparable_teammate_pairs": report["validation"][
                    "teammate_matching"
                ]["comparable_pairs"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
