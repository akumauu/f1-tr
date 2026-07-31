#!/usr/bin/env python3
"""发布 DeltaData clean-air pace 算法倒推 v2。

主 pilot 为 2025 Abu Dhabi Race；2026 Japan Race、Miami Sprint 和
Miami Race 只用于与公开 H2H 图表做参考知情的数值基准，不混入
2023--2025 产品排名。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from reference_analysis_lab.deltadata_reverse_engineered_v2 import (
    FUEL_SCENARIOS_S_PER_LAP,
    PUBLIC_CLEAN_AIR_SHARE_MIN,
    PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
    PUBLIC_TRAFFIC_RATIO_MAX,
    analyse_event,
    extract_h2h_delta,
    reference_benchmark_metrics,
)
from reference_analysis_lab.f1pace_reverse_engineered_v2 import (
    build_time_headway_features,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "20260731T153000+0800-deltadata-reverse-engineered-v2"
STATUS = "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
TARGET_ID = "deltadata-reverse-engineered-v2"
CAPTURED_AT = "2026-07-31T14:30:00+08:00"

RECORDS_BASE = (
    ROOT / "research" / "records" / "reference_analysis_lab_v2" / "deltadata"
)
FRONTEND_BASE = (
    ROOT / "frontend" / "public" / "data" / "reference-analysis-lab" / "v2"
)
FRONTEND_INDEX = FRONTEND_BASE / "manifest.json"
CACHE = ROOT / ".runtime-cache" / "reference-analysis-lab-v2" / "deltadata"
TRAFFIC_CACHE = CACHE / "traffic-features"

V17_RUN_ID = "20260726T124625+0800-ce93134a40b8"
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / f"run={V17_RUN_ID}"
    / "manifest.json"
)
V17_ABU_SIDECAR = (
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
V1_REPORT = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v1"
    / "deltadata"
    / "run=20260730T235900+0800-deltadata-abu-dhabi-v1"
    / "deltadata_2025_abu_dhabi.json"
)
REFERENCE_IMAGE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v1"
    / "deltadata"
    / "reference-images"
    / "h2h-clear-air-race-pace.jpg"
)
INVALID_REFERENCE_IMAGE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v1"
    / "deltadata"
    / "reference-images"
    / "team-representative-race-laps.jpg"
)

ABU_TELEMETRY = (
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

EVENTS = (
    {
        "event_id": "2026-japan-race",
        "year": 2026,
        "meeting": "Japanese Grand Prix",
        "session": "Race",
        "telemetry": (
            ROOT
            / "data"
            / "normalized"
            / "tracinginsights"
            / "schema=tracinginsights-expanded-v4"
            / "year=2026"
            / "commit=81b90e61051a"
            / "session=Race"
            / "meeting=Japanese_Grand_Prix"
            / "telemetry.parquet"
        ),
        "reference_delta_pct": -0.131,
        "reference_label": "JAPAN RACE",
    },
    {
        "event_id": "2026-miami-sprint",
        "year": 2026,
        "meeting": "Miami Grand Prix",
        "session": "Sprint",
        "telemetry": (
            ROOT
            / "data"
            / "normalized"
            / "tracinginsights"
            / "schema=tracinginsights-expanded-v4"
            / "year=2026"
            / "commit=81b90e61051a"
            / "session=Sprint"
            / "meeting=Miami_Grand_Prix"
            / "telemetry.parquet"
        ),
        "reference_delta_pct": -0.270,
        "reference_label": "MIAMI SPRINT",
    },
    {
        "event_id": "2026-miami-race",
        "year": 2026,
        "meeting": "Miami Grand Prix",
        "session": "Race",
        "telemetry": (
            ROOT
            / "data"
            / "normalized"
            / "tracinginsights"
            / "schema=tracinginsights-expanded-v4"
            / "year=2026"
            / "commit=81b90e61051a"
            / "session=Race"
            / "meeting=Miami_Grand_Prix"
            / "telemetry.parquet"
        ),
        "reference_delta_pct": -0.572,
        "reference_label": "MIAMI RACE",
    },
)

REFERENCE_TRANSCRIPTION = (
    ("JAPAN RACE", -0.131, "TESTED_LOCAL_2026_FREEZE"),
    ("MIAMI SPRINT", -0.270, "TESTED_LOCAL_2026_FREEZE"),
    ("MIAMI RACE", -0.572, "TESTED_LOCAL_2026_FREEZE"),
    ("CANADA SPRINT", -0.096, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("CANADA RACE", -0.124, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("MONACO RACE", -0.354, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("BARCELONA RACE", -0.067, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("AUSTRIA RACE", -0.444, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("GREAT BRITAIN SPRINT", -0.477, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("GREAT BRITAIN RACE", -0.572, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
    ("HUNGARY RACE", -0.225, "NOT_TESTED_NO_LOCAL_2026_TELEMETRY"),
)

TELEMETRY_COLUMNS = (
    "driver_acronym",
    "lap",
    "sample_index",
    "time",
    "lap_start_raw",
    "lap_duration",
    "rel_distance",
    "distance",
    "speed",
    "driver_ahead",
    "distance_to_driver_ahead",
    "position",
    "stint",
    "compound",
    "tyre_life",
    "track_status",
    "deleted",
    "is_accurate",
    "team",
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
    content = (text.rstrip() + "\n").encode("utf-8")
    return write_bytes_once(path, content)


def write_csv_once(path: Path, rows: Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    frame = pd.DataFrame(list(rows))
    content = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return write_bytes_once(path, content), int(len(frame))


def load_points(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(path, columns=list(TELEMETRY_COLUMNS))


def traffic_cache_for(event_id: str) -> Path:
    return TRAFFIC_CACHE / f"{event_id}.parquet"


def build_or_load_traffic_features(
    *,
    event_id: str,
    telemetry_path: Path,
    points: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache_path = traffic_cache_for(event_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        features = pd.read_parquet(cache_path)
        cache_status = "REUSED_RUNTIME_CACHE"
    else:
        features = build_time_headway_features(points)
        features.to_parquet(cache_path, index=False)
        cache_status = "BUILT_RUNTIME_CACHE"
    required = {
        "driver",
        "lap",
        "audited_traffic_ratio",
        "missing",
        "observed_time_s",
    }
    missing = sorted(required.difference(features.columns))
    if missing:
        raise ValueError(
            f"{event_id} 交通缓存字段不完整：" + ", ".join(missing)
        )
    identity = {
        "event_id": event_id,
        "telemetry_path": _relative(telemetry_path),
        "telemetry_sha256": sha256_file(telemetry_path),
        "traffic_cache_path": _relative(cache_path),
        "traffic_cache_sha256": sha256_file(cache_path),
        "traffic_cache_status": cache_status,
        "traffic_rows": int(len(features)),
        "drivers": int(features["driver"].nunique()),
        "laps": int(features["lap"].max()),
        "definition": (
            "nearest_physical_car_same_track_location_crossing_headway_le_2s"
        ),
    }
    meta_path = cache_path.with_suffix(".identity.json")
    meta_content = _json_bytes(identity)
    if meta_path.exists():
        if meta_path.read_bytes() != meta_content:
            meta_path.write_bytes(meta_content)
    else:
        meta_path.write_bytes(meta_content)
    identity["identity_path"] = _relative(meta_path)
    identity["identity_sha256"] = sha256_file(meta_path)
    return features, identity


def build_reference_evidence() -> dict[str, Any]:
    evidence = {
        "schema_version": "deltadata-public-method-evidence-v2",
        "captured_at": CAPTURED_AT,
        "official_profile": {
            "url": "https://x.com/DeltaData_",
            "status": "PROFILE_AVAILABLE_POST_TEXT_NOT_MACHINE_EXTRACTABLE",
        },
        "public_post_mirror_snapshot": {
            "url": "https://mobile.twstalker.com/DeltaData_",
            "provider_role": "third_party_search_mirror_not_authoritative_archive",
            "retrieval_observation": (
                "搜索索引在抓取时显示 2026 Clean Air Pace Analysis 方法帖；"
                "镜像可能变更或失效，因此仅冻结公开文字要点，不把镜像当原始作者。"
            ),
            "publicly_stated_points": [
                "1,021 laps reviewed individually for the 2026 analysis",
                "fuel discount: 0.032 s/lap",
                "representative laps in clean air with fuel correction",
                "every lap manually reviewed to remove non-representative laps",
                "prefer same tyre compound and similar track phases",
                "different compounds may be used when a representative identical-condition comparison is impossible",
                "Grand Prix aggregation weight depends on sample size and quality",
                "very limited samples may be shown but receive minimal season weight",
                "known mechanical-issue laps may be excluded",
                "different strategies may be standardized for tyre degradation and stints",
            ],
        },
        "reference_chart": {
            "name": "Last 11 H2H Mercedes Clear Air Race Pace",
            "post_url": "https://x.com/DeltaData_/status/2082785101931311292",
            "cache_path": _relative(REFERENCE_IMAGE),
            "sha256": sha256_file(REFERENCE_IMAGE),
            "dimensions_px": [1200, 800],
            "transcription": [
                {
                    "event": event,
                    "antonelli_minus_russell_pct": value,
                    "status": status,
                }
                for event, value, status in REFERENCE_TRANSCRIPTION
            ],
            "transcription_role": (
                "manual_numeric_transcription_from_reference_image_lower_pct_is_faster"
            ),
            "product_asset_status": "REFERENCE_ONLY_NOT_REPUBLISHED",
        },
        "invalid_cached_asset": {
            "cache_path": _relative(INVALID_REFERENCE_IMAGE),
            "sha256": sha256_file(INVALID_REFERENCE_IMAGE)
            if INVALID_REFERENCE_IMAGE.exists()
            else None,
            "status": "INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH",
            "reason": (
                "文件内容是 Ferrari 赛车照片，不是其文件名声称的代表圈方法图；"
                "禁止把它当方法证据或产品资产。"
            ),
        },
        "licensing_boundary": (
            "只重实现公开语义、数值口径和布局关系；不复制人物照片、logo、"
            "品牌字体或原图作为产品资产。"
        ),
    }
    path = CACHE / "public-method-evidence.json"
    content = _json_bytes(evidence)
    if path.exists():
        if path.read_bytes() != content:
            path.write_bytes(content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    evidence["cache_path"] = _relative(path)
    evidence["sha256"] = sha256_file(path)
    return evidence


def _find_pair(
    analysis: Mapping[str, Any],
    *,
    team: str,
) -> Mapping[str, Any] | None:
    return next(
        (
            row
            for row in analysis["visual_replication"]["teammate_h2h"]
            if str(row["team"]) == team
        ),
        None,
    )


def build_reference_benchmark() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    event_evidence: list[dict[str, Any]] = []
    for config in EVENTS:
        path = Path(config["telemetry"])
        points = load_points(path)
        traffic, source_identity = build_or_load_traffic_features(
            event_id=str(config["event_id"]),
            telemetry_path=path,
            points=points,
        )
        analysis = analyse_event(
            points,
            traffic_features=traffic,
            include_lap_evidence=False,
        )
        model_delta = extract_h2h_delta(
            analysis,
            team="Mercedes",
            left_driver="ANT",
            right_driver="RUS",
        )
        pair = _find_pair(analysis, team="Mercedes")
        reference_delta = float(config["reference_delta_pct"])
        row = {
            "event_id": config["event_id"],
            "event": config["reference_label"],
            "session": config["session"],
            "reference_delta_pct": reference_delta,
            "model_delta_pct": model_delta,
            "error_pp": (
                model_delta - reference_delta
                if model_delta is not None
                else None
            ),
            "absolute_error_pp": (
                abs(model_delta - reference_delta)
                if model_delta is not None
                else None
            ),
            "direction_match": (
                bool(np.sign(model_delta) == np.sign(reference_delta))
                if model_delta is not None
                else None
            ),
            "audited_status": pair.get("audited_status") if pair else None,
            "gate_failures": pair.get("gate_failures") if pair else [],
            "antonelli_representative_laps": (
                pair.get("left_sample", {}).get("representative_laps")
                if pair and pair.get("left_driver") == "ANT"
                else pair.get("right_sample", {}).get("representative_laps")
                if pair
                else None
            ),
            "russell_representative_laps": (
                pair.get("right_sample", {}).get("representative_laps")
                if pair and pair.get("left_driver") == "ANT"
                else pair.get("left_sample", {}).get("representative_laps")
                if pair
                else None
            ),
        }
        rows.append(row)
        event_evidence.append(
            {
                "event_id": config["event_id"],
                "source_identity": source_identity,
                "exclusion_ledger": analysis["exclusion_ledger"],
                "representative_ledger": analysis["representative_ledger"],
                "model_summary": {
                    key: analysis["model"][key]
                    for key in (
                        "model_type",
                        "fuel_discount_s_per_lap",
                        "candidate_rows",
                        "representative_rows",
                        "downweighted_outlier_rows",
                        "scale_s",
                        "fit_metrics",
                    )
                },
                "mercedes_pair": pair,
            }
        )
    return {
        "protocol": (
            "reference_informed_reverse_engineering_benchmark_not_blind_holdout"
        ),
        "reference_values_used_as_model_training_target": False,
        "human_model_choice_informed_by_seen_reference": True,
        "parameter_search_against_reference_accepted": False,
        "tested_rows": rows,
        "metrics": reference_benchmark_metrics(rows),
        "untested_reference_rows": [
            {
                "event": event,
                "reference_delta_pct": value,
                "status": status,
            }
            for event, value, status in REFERENCE_TRANSCRIPTION
            if status != "TESTED_LOCAL_2026_FREEZE"
        ],
        "event_evidence": event_evidence,
        "scope_boundary": (
            "2026 三场只用于算法复核，不进入 2023--2025 Race Dossier 或排名。"
        ),
    }


def _centered_gaps(
    rows: Sequence[Mapping[str, Any]],
    *,
    identity_key: str,
    pace_key: str,
) -> dict[str, float]:
    finite = [
        (str(row[identity_key]), float(row[pace_key]))
        for row in rows
        if row.get(pace_key) is not None
    ]
    if not finite:
        return {}
    fastest = min(value for _, value in finite)
    return {identity: value - fastest for identity, value in finite}


def _rank_order(
    rows: Sequence[Mapping[str, Any]],
    *,
    identity_key: str,
    pace_key: str,
) -> list[str]:
    return [
        identity
        for identity, _ in sorted(
            (
                (str(row[identity_key]), float(row[pace_key]))
                for row in rows
                if row.get(pace_key) is not None
            ),
            key=lambda item: (item[1], item[0]),
        )
    ]


def build_legacy_comparison(abu_analysis: Mapping[str, Any]) -> dict[str, Any]:
    legacy = json.loads(V1_REPORT.read_text(encoding="utf-8"))
    legacy_drivers = legacy["visual_replication"]["clean_air_pace"][
        "driver_ranking"
    ]
    legacy_teams = legacy["visual_replication"]["clean_air_pace"]["team_ranking"]
    current_drivers = abu_analysis["visual_replication"]["driver_ranking"]
    current_teams = abu_analysis["visual_replication"]["team_ranking"]

    legacy_driver_gaps = _centered_gaps(
        legacy_drivers,
        identity_key="driver",
        pace_key="base_reference_pace_s",
    )
    current_driver_gaps = _centered_gaps(
        current_drivers,
        identity_key="driver",
        pace_key="standardized_pace_s",
    )
    common_drivers = [
        driver for driver in legacy_driver_gaps if driver in current_driver_gaps
    ]
    driver_rows = [
        {
            "driver": driver,
            "v1_centered_gap_s": legacy_driver_gaps[driver],
            "v2_centered_gap_s": current_driver_gaps[driver],
            "v2_minus_v1_gap_s": (
                current_driver_gaps[driver] - legacy_driver_gaps[driver]
            ),
        }
        for driver in common_drivers
    ]

    legacy_team_gaps = _centered_gaps(
        legacy_teams,
        identity_key="team",
        pace_key="base_reference_pace_s",
    )
    current_team_gaps = _centered_gaps(
        current_teams,
        identity_key="team",
        pace_key="standardized_pace_s",
    )
    common_teams = [
        team for team in legacy_team_gaps if team in current_team_gaps
    ]
    team_rows = [
        {
            "team": team,
            "v1_centered_gap_s": legacy_team_gaps[team],
            "v2_centered_gap_s": current_team_gaps[team],
            "v2_minus_v1_gap_s": (
                current_team_gaps[team] - legacy_team_gaps[team]
            ),
        }
        for team in common_teams
    ]

    v1_driver_order = [
        driver
        for driver in _rank_order(
            legacy_drivers,
            identity_key="driver",
            pace_key="base_reference_pace_s",
        )
        if driver in common_drivers
    ]
    v2_driver_order = [
        driver
        for driver in _rank_order(
            current_drivers,
            identity_key="driver",
            pace_key="standardized_pace_s",
        )
        if driver in common_drivers
    ]
    v1_team_order = [
        team
        for team in _rank_order(
            legacy_teams,
            identity_key="team",
            pace_key="base_reference_pace_s",
        )
        if team in common_teams
    ]
    v2_team_order = [
        team
        for team in _rank_order(
            current_teams,
            identity_key="team",
            pace_key="standardized_pace_s",
        )
        if team in common_teams
    ]
    driver_abs = [
        abs(float(row["v2_minus_v1_gap_s"])) for row in driver_rows
    ]
    team_abs = [abs(float(row["v2_minus_v1_gap_s"])) for row in team_rows]
    return {
        "legacy_report": {
            "path": _relative(V1_REPORT),
            "sha256": sha256_file(V1_REPORT),
            "run_id": legacy["run_id"],
            "status": legacy["status"],
        },
        "comparison_role": (
            "same_event_method_change_diagnostic_not_same_metric_ground_truth"
        ),
        "driver_rows": driver_rows,
        "team_rows": team_rows,
        "summary": {
            "shared_drivers": len(common_drivers),
            "shared_teams": len(common_teams),
            "driver_order_exact_match": v1_driver_order == v2_driver_order,
            "team_order_exact_match": v1_team_order == v2_team_order,
            "v1_driver_order": v1_driver_order,
            "v2_driver_order": v2_driver_order,
            "v1_team_order": v1_team_order,
            "v2_team_order": v2_team_order,
            "driver_centered_gap_mae_s": float(np.mean(driver_abs))
            if driver_abs
            else None,
            "driver_centered_gap_max_s": float(np.max(driver_abs))
            if driver_abs
            else None,
            "team_centered_gap_mae_s": float(np.mean(team_abs))
            if team_abs
            else None,
            "team_centered_gap_max_s": float(np.max(team_abs))
            if team_abs
            else None,
        },
        "difference_sources": [
            "v1 只消费 v17 sidecar 的 4 支 reporting teams；v2 使用原始全场 20 车手。",
            "v1 主情景为 0.051724 s/圈；v2 视觉主口径冻结为公开 0.032 s/圈。",
            "v1 使用 v17 cross-fit/analysis_weight 与 Stint 参考胎龄；v2 使用逐点物理交通、80% clean-air 和全场 Huber 条件标准化。",
            "v2 对极少 clean-air 支持的车手 fail-close，不用 sidecar 条件模型补齐视觉排名。",
            "两版绝对参考点不同，因此只比较相对最快者的 centered gap，不比较绝对秒数。",
        ],
    }


def build_method_card(reference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "creator": "Delta Data / @DeltaData_",
        "original_profile": "https://x.com/DeltaData_",
        "public_method_mirror": "https://mobile.twstalker.com/DeltaData_",
        "reference_chart": {
            "name": "Last 11 H2H Mercedes Clear Air Race Pace",
            "post_url": "https://x.com/DeltaData_/status/2082785101931311292",
            "sha256": reference["reference_chart"]["sha256"],
            "captured_at": CAPTURED_AT,
        },
        "publicly_stated_method": [
            "只使用代表性的 clean-air 圈，并做燃油修正。",
            "2026 公开燃油折损为 0.032 s/圈。",
            "逐圈人工复核，删除即使处于 clean air 但仍不具代表性的圈。",
            "优先比较相同轮胎配方和相近赛道阶段。",
            "完全相同条件不可得时，可在认为具有代表性的前提下使用不同配方。",
            "赛季汇总按样本量与样本质量加权，小样本可展示但影响应很小。",
            "已知机械问题圈可排除；不同策略尝试标准化轮胎衰减与 Stint。",
        ],
        "reverse_engineered_or_reimplemented": [
            "clean air 操作定义：一圈至少 80% 可观测时间不在物理前车 2 秒内。",
            "交通比例来自逐点同位置 crossing headway 和时间权重，不用圈级 median gap。",
            "HuberRegressor(epsilon=1.5, alpha=0.01) 自动鲁棒复核，替代不可得的人工逐圈标签。",
            "条件矩阵包含车手、配方、胎龄/二次项、比赛阶段/二次项和配方×胎龄。",
            "把所有车手投影到同一代表圈条件分布，形成视觉方法等价排序。",
            "审计层另行门控同配方、共同胎龄、阶段重叠、交通平衡、样本量与 Kish ESS。",
        ],
        "visual_inference": [
            "80% clean-air 是对 clean-air 语义的可解释工程化阈值，不是原作者公开数字。",
            "图中 lower % is faster，因此 H2H 定义为左车手减右车手并除以右车手标准化配速。",
            "人工审查可能同时考虑车手管理、事故、故障或视觉比赛上下文；当前自动模型无法完全复现。",
        ],
        "not_identifiable": [
            "原作者精确 clean-air 阈值、逐圈人工标签、回归代码与赛季权重公式。",
            "真实燃油量、SOC、胎温、胎压、物理磨损、设定、损伤、动力模式和车队指令。",
            "车手是否主动管理轮胎/能量或是否被策略指令限制。",
        ],
        "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
        "evidence_snapshot": {
            "path": reference["cache_path"],
            "sha256": reference["sha256"],
            "mirror_warning": (
                "公开方法文本来自搜索镜像快照；原始作者身份以 X profile 为准。"
            ),
        },
    }


def build_data_gap_audit() -> list[dict[str, Any]]:
    return [
        {
            "required_field": "逐圈 clean-air 时间比例",
            "repository_field": (
                "逐点绝对时间、rel_distance、distance、speed；重建 audited_traffic_ratio"
            ),
            "gap": "原作者阈值未公开",
            "substitute": "同位置物理前车 headway≤2s；交通≤20%",
            "publication": "ALLOW_AS_NAMED_PROXY",
        },
        {
            "required_field": "代表圈人工复核标签",
            "repository_field": "is_accurate/deleted/旗态/进出站边界",
            "gap": "人工逐圈判定不可得",
            "substitute": "Huber 自动鲁棒下权异常残差",
            "publication": "METHOD_EQUIVALENT_ONLY",
        },
        {
            "required_field": "真实燃油与燃油效应",
            "repository_field": "圈号和比赛阶段",
            "gap": "真实油量不可识别",
            "substitute": "public 0.032 与 v17 low/base/high 具名线性情景",
            "publication": "SCENARIO_ONLY",
        },
        {
            "required_field": "轮胎配方与胎龄",
            "repository_field": "compound、tyre_life、stint",
            "gap": "胎温/胎压/物理磨损不可识别",
            "substitute": "观测胎龄条件项及共同支持",
            "publication": "ALLOW_PROXY_NOT_PHYSICAL_WEAR",
        },
        {
            "required_field": "相近赛道阶段",
            "repository_field": "lap/max_lap",
            "gap": "真实赛道抓地演化不可单独识别",
            "substitute": "lap_fraction 一次/二次条件项与阶段重叠门",
            "publication": "ALLOW_CONDITIONAL_PROXY",
        },
        {
            "required_field": "故障/损伤/动力模式",
            "repository_field": "无稳定逐圈真值",
            "gap": "不可识别",
            "substitute": "Huber 只可下权异常，不能给出原因",
            "publication": "NOT_IDENTIFIABLE",
        },
        {
            "required_field": "赛季 GP 质量权重",
            "repository_field": "样本量、Kish ESS、支持重叠、敏感性",
            "gap": "原作者精确权重公式未公开",
            "substitute": "本 pilot 只披露质量字段，不做 2026 赛季汇总",
            "publication": "NOT_PUBLISHED_AS_SEASON_ORDER",
        },
        {
            "required_field": "2025 Abu Dhabi 原作者对应数值图",
            "repository_field": "没有有效公开参考图缓存",
            "gap": "缓存第二图实际是 Ferrari 赛车照片",
            "substitute": "只做真实 pilot 与 v1 方法差异；不声称对图误差",
            "publication": "NO_EXTERNAL_NUMERIC_CLAIM",
        },
    ]


def _method_card_markdown(report: Mapping[str, Any]) -> str:
    card = report["method_card"]
    lines = [
        "# DeltaData clean-air pace 倒推方法卡 v2",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- status: `{report['status']}`",
        f"- 原作者主页: {card['original_profile']}",
        f"- 公开方法镜像: {card['public_method_mirror']}",
        f"- 参考图 SHA-256: `{card['reference_chart']['sha256']}`",
        f"- 抓取冻结时间: `{card['reference_chart']['captured_at']}`",
        "",
        "## 原作者公开方法",
        "",
    ]
    lines.extend(f"- {value}" for value in card["publicly_stated_method"])
    lines.extend(["", "## 方法等价实现", ""])
    lines.extend(
        f"- {value}" for value in card["reverse_engineered_or_reimplemented"]
    )
    lines.extend(["", "## 视觉反推", ""])
    lines.extend(f"- {value}" for value in card["visual_inference"])
    lines.extend(["", "## 不可识别", ""])
    lines.extend(f"- {value}" for value in card["not_identifiable"])
    lines.extend(
        [
            "",
            "## 许可边界",
            "",
            report["reference_identity"]["licensing_boundary"],
            "",
            "精确源码状态：`SKIPPED_OPAQUE_METHOD`。",
        ]
    )
    return "\n".join(lines)


def _data_gap_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# DeltaData data-gap audit v2",
        "",
        "| 目标字段 | 当前字段 | 缺口 | 替代代理 | 发布状态 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in report["data_gap_audit"]:
        lines.append(
            "| "
            + " | ".join(
                str(row[key]).replace("|", "\\|")
                for key in (
                    "required_field",
                    "repository_field",
                    "gap",
                    "substitute",
                    "publication",
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _reference_difference_markdown(report: Mapping[str, Any]) -> str:
    benchmark = report["reference_benchmark"]
    legacy = report["legacy_comparison"]
    metrics = benchmark["metrics"]
    lines = [
        "# DeltaData v2 数值差异说明",
        "",
        "## 对公开 2026 Mercedes H2H 图",
        "",
        "| 事件 | 公开 ANT-RUS | v2 ANT-RUS | 误差 pp | 审计状态 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in benchmark["tested_rows"]:
        lines.append(
            f"| {row['event']} | {row['reference_delta_pct']:.3f}% | "
            f"{row['model_delta_pct']:.3f}% | {row['error_pp']:+.3f} | "
            f"{row['audited_status']} |"
        )
    lines.extend(
        [
            "",
            f"- 3 场 MAE: `{metrics['mae_pp']:.6f} pp`",
            f"- RMSE: `{metrics['rmse_pp']:.6f} pp`",
            f"- 最大绝对误差: `{metrics['max_abs_error_pp']:.6f} pp`",
            f"- 方向准确率: `{metrics['direction_accuracy']:.1%}`",
            "",
            "这些事件值在模型家族选择时已被看到，因此是参考知情的反推基准，"
            "不是盲测或零重拟合外部验证。",
            "",
            "## 对本仓库 DeltaData v1",
            "",
            f"- 共享 8 名车手顺序完全一致: `{legacy['summary']['driver_order_exact_match']}`",
            f"- 共享 4 支车队顺序完全一致: `{legacy['summary']['team_order_exact_match']}`",
            f"- 车手 centered-gap MAE: `{legacy['summary']['driver_centered_gap_mae_s']:.6f} s`",
            f"- 车队 centered-gap MAE: `{legacy['summary']['team_centered_gap_mae_s']:.6f} s`",
            "",
            "主要差异来源：",
            "",
        ]
    )
    lines.extend(
        f"- {value}" for value in legacy["difference_sources"]
    )
    return "\n".join(lines)


def _attempt_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    metric = report["reference_benchmark"]["metrics"]
    return [
        {
            "attempt": 1,
            "issue": "原始 clean-air 圈均值与图表差距过大",
            "hypothesis": "只做燃油修正但未标准化胎龄、阶段和配方会产生混杂",
            "action": "测试 raw mean/median 与公开 0.032 s/lap",
            "output": (
                "Japan/Miami Sprint/Miami Race 约为 "
                "-1.022%/-0.568%/-0.836%，拒绝发布"
            ),
            "next": "加入条件标准化与鲁棒代表圈",
            "status": "REJECTED_CONFOUNDED",
        },
        {
            "attempt": 2,
            "issue": "阈值/燃油率网格可把数字贴近参考图",
            "hypothesis": "自由调阈值可能降低表面误差",
            "action": "审计 traffic threshold、fuel rate、mean/median 网格",
            "output": (
                "发现 0.05 + 0.076 的偶然低误差，但违背公开 0.032 且属于"
                "参考值后验调参，明确拒绝"
            ),
            "next": "按公开语义冻结 80% clean-air 与 0.032，改用 Huber",
            "status": "REJECTED_REFERENCE_OVERFIT",
        },
        {
            "attempt": 3,
            "issue": "需要自动替代不可得的逐圈人工复核",
            "hypothesis": "Huber 条件模型能鲁棒下权非代表圈且保留可审计参数",
            "action": (
                "逐点物理交通 + 80% clean-air + Huber + 公共条件分布；"
                "同队另走 ESS/配方/胎龄/阶段/交通门"
            ),
            "output": (
                f"3 场 MAE={metric['mae_pp']:.6f} pp，方向准确率="
                f"{metric['direction_accuracy']:.1%}；日本/冲刺因样本门保持 audit_only"
            ),
            "next": "冻结 v2；未来新增未见事件再做真正外部验证",
            "status": "ACCEPTED_METHOD_EQUIVALENT",
        },
    ]


def build_report(
    *,
    run_id: str,
    abu_analysis: Mapping[str, Any],
    abu_source: Mapping[str, Any],
    reference: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    legacy: Mapping[str, Any],
) -> dict[str, Any]:
    v17 = json.loads(V17_MANIFEST.read_text(encoding="utf-8"))
    method_card = build_method_card(reference)
    data_gap = build_data_gap_audit()
    return {
        "schema_version": "reference-analysis-lab-deltadata-v2",
        "target_id": TARGET_ID,
        "status": STATUS,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
        "source_identity": {
            "pilot": abu_source,
            "v17_manifest": {
                "path": _relative(V17_MANIFEST),
                "sha256": sha256_file(V17_MANIFEST),
                "run_id": v17["run_id"],
                "coverage_reverified": v17["coverage"],
            },
            "v17_abu_sidecar": {
                "path": _relative(V17_ABU_SIDECAR),
                "sha256": sha256_file(V17_ABU_SIDECAR),
            },
            "normalized_feed_commits": {
                "pilot_2025": "f7a5324cae58",
                "reference_benchmark_2026": "81b90e61051a",
            },
        },
        "reference_identity": reference,
        "method_card": method_card,
        "data_gap_audit": data_gap,
        "pilot": {
            "event": "2025 Abu Dhabi Grand Prix",
            "session": "Race",
            "real_source_only": True,
            "synthetic_laps": 0,
            "analysis": abu_analysis,
        },
        "visual_replication": {
            **abu_analysis["visual_replication"],
            "scope": "2025 Abu Dhabi real pilot",
            "fuel_scenario": {
                "name": "public_2026",
                "discount_s_per_lap": PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
                "transfer_warning": (
                    "公开 0.032 来自 2026 方法帖；用于 2025 pilot 只是具名迁移情景。"
                ),
            },
            "clean_air_share_min": PUBLIC_CLEAN_AIR_SHARE_MIN,
            "status": "METHOD_EQUIVALENT_VISUAL_PROXY",
        },
        "audited_analysis": {
            **abu_analysis["audited_analysis"],
            "scope": "2025 Abu Dhabi real pilot",
            "direct_total_order_allowed": False,
            "visual_ranking_reused_as_audited_ranking": False,
            "v17_coverage_gate": v17["coverage"],
            "boundary": (
                "审计视图只允许通过同配方、共同胎龄、阶段、交通和 ESS 门的"
                "同队子集；其余保持 audit_only。"
            ),
        },
        "exclusion_ledger": abu_analysis["exclusion_ledger"],
        "representative_ledger": abu_analysis["representative_ledger"],
        "fuel_and_threshold_sensitivity": abu_analysis["sensitivity"],
        "model": abu_analysis["model"],
        "lap_evidence": abu_analysis["lap_evidence"],
        "reference_benchmark": benchmark,
        "legacy_comparison": legacy,
        "validation": {
            "real_source_only": True,
            "synthetic_laps": 0,
            "pilot_raw_points": int(abu_source["raw_points"]),
            "pilot_laps": int(abu_analysis["exclusion_ledger"]["all_laps"]),
            "pilot_drivers": int(abu_analysis["exclusion_ledger"]["drivers"]),
            "pilot_teams": int(abu_analysis["exclusion_ledger"]["teams"]),
            "clean_air_candidates": int(
                abu_analysis["exclusion_ledger"]["clean_air_candidates"]
            ),
            "representative_laps": int(
                abu_analysis["representative_ledger"]["representative_laps"]
            ),
            "reference_benchmark_events": benchmark["metrics"]["events"],
            "reference_benchmark_mae_pp": benchmark["metrics"]["mae_pp"],
            "reference_benchmark_direction_accuracy": benchmark["metrics"][
                "direction_accuracy"
            ],
            "reference_benchmark_is_blind_holdout": False,
            "public_fuel_discount_s_per_lap": PUBLIC_FUEL_DISCOUNT_S_PER_LAP,
            "traffic_ratio_max": PUBLIC_TRAFFIC_RATIO_MAX,
            "frozen_fuel_scenarios_s_per_lap": dict(
                FUEL_SCENARIOS_S_PER_LAP
            ),
            "creator_exact_code_available": False,
            "creator_manual_labels_available": False,
            "one_to_one_claim_allowed": False,
        },
        "method_boundaries": [
            "80% clean-air 阈值是本实验的可解释反推，不是原作者公开值。",
            "Huber 只是人工逐圈复核的自动方法等价替代，不是人工标签复原。",
            "三场参考值在模型选择时已被看到，不能称盲测。",
            "2026 基准不进入 2023--2025 排名。",
            "真实燃油、SOC、物理磨损、胎温胎压、设定、损伤、动力模式和车队指令不可识别。",
        ],
    }


def publish(run_id: str) -> dict[str, Any]:
    record_dir = RECORDS_BASE / f"run={run_id}"
    frontend_dir = FRONTEND_BASE / f"run={run_id}"
    if record_dir.exists() or frontend_dir.exists():
        raise FileExistsError(f"追加式 run 已存在：{run_id}")

    reference = build_reference_evidence()
    abu_points = load_points(ABU_TELEMETRY)
    abu_traffic, abu_source = build_or_load_traffic_features(
        event_id="2025-abu-dhabi-race",
        telemetry_path=ABU_TELEMETRY,
        points=abu_points,
    )
    abu_source = {
        **abu_source,
        "raw_points": int(len(abu_points)),
        "session": "Race",
        "year": 2025,
        "meeting": "Abu Dhabi Grand Prix",
        "source_commit": "f7a5324cae58",
    }
    abu_analysis = analyse_event(
        abu_points,
        traffic_features=abu_traffic,
        include_lap_evidence=True,
    )
    benchmark = build_reference_benchmark()
    legacy = build_legacy_comparison(abu_analysis)
    report = build_report(
        run_id=run_id,
        abu_analysis=abu_analysis,
        abu_source=abu_source,
        reference=reference,
        benchmark=benchmark,
        legacy=legacy,
    )

    report_path = record_dir / "deltadata_reverse_engineered_v2.json"
    report_hash = write_json_once(report_path, report)
    method_hash = write_text_once(
        record_dir / "method_card.md",
        _method_card_markdown(report),
    )
    gap_hash = write_text_once(
        record_dir / "data_gap_audit.md",
        _data_gap_markdown(report),
    )
    difference_hash = write_text_once(
        record_dir / "reference_difference.md",
        _reference_difference_markdown(report),
    )

    numeric_tables: list[dict[str, Any]] = []

    def add_csv(
        name: str,
        rows: Sequence[Mapping[str, Any]],
        role: str,
    ) -> None:
        digest, count = write_csv_once(record_dir / name, rows)
        numeric_tables.append(
            {
                "path": name,
                "sha256": digest,
                "rows": count,
                "role": role,
            }
        )

    add_csv(
        "abu_driver_ranking.csv",
        report["visual_replication"]["driver_ranking"],
        "visual_replication_driver_ranking",
    )
    add_csv(
        "abu_team_ranking.csv",
        report["visual_replication"]["team_ranking"],
        "visual_replication_team_ranking",
    )
    add_csv(
        "abu_teammate_h2h.csv",
        report["visual_replication"]["teammate_h2h"],
        "visual_and_audited_pair_ledger",
    )
    add_csv(
        "abu_lap_evidence.csv",
        report["lap_evidence"],
        "real_lap_sample_and_exclusion_evidence",
    )
    add_csv(
        "reference_benchmark.csv",
        report["reference_benchmark"]["tested_rows"],
        "reference_informed_numeric_benchmark",
    )
    add_csv(
        "legacy_driver_difference.csv",
        report["legacy_comparison"]["driver_rows"],
        "v1_v2_centered_driver_gap_difference",
    )
    add_csv(
        "legacy_team_difference.csv",
        report["legacy_comparison"]["team_rows"],
        "v1_v2_centered_team_gap_difference",
    )
    coefficient_rows = [
        {
            "feature": feature,
            "coefficient": coefficient,
        }
        for feature, coefficient in zip(
            report["model"]["identity"]["feature_names"],
            report["model"]["coefficients"],
        )
    ]
    add_csv(
        "model_coefficients.csv",
        coefficient_rows,
        "serialized_huber_model_coefficients",
    )

    attempt_rows = _attempt_rows(report)
    attempts_content = "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in attempt_rows
    )
    attempts_hash = write_text_once(
        record_dir / "attempts.jsonl",
        attempts_content,
    )
    manifest = {
        "schema_version": "reference-analysis-lab-manifest-v2",
        "run_id": run_id,
        "target_id": TARGET_ID,
        "status": STATUS,
        "report": report_path.name,
        "report_sha256": report_hash,
        "method_card": {
            "path": "method_card.md",
            "sha256": method_hash,
        },
        "data_gap_audit": {
            "path": "data_gap_audit.md",
            "sha256": gap_hash,
        },
        "reference_difference": {
            "path": "reference_difference.md",
            "sha256": difference_hash,
        },
        "numeric_tables": numeric_tables,
        "attempts": {
            "path": "attempts.jsonl",
            "sha256": attempts_hash,
            "rows": len(attempt_rows),
        },
        "source_hashes": {
            "abu_telemetry": abu_source["telemetry_sha256"],
            "abu_traffic_features": abu_source["traffic_cache_sha256"],
            "reference_chart": reference["reference_chart"]["sha256"],
            "public_method_evidence": reference["sha256"],
            "v17_manifest": sha256_file(V17_MANIFEST),
            "v17_abu_sidecar": sha256_file(V17_ABU_SIDECAR),
            "legacy_v1_report": sha256_file(V1_REPORT),
        },
    }
    manifest_hash = write_json_once(record_dir / "manifest.json", manifest)

    frontend_dir.mkdir(parents=True, exist_ok=False)
    for path in record_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, frontend_dir / path.name)
    if sha256_file(frontend_dir / report_path.name) != report_hash:
        raise RuntimeError("前端复制后的 DeltaData report SHA-256 不一致")
    if sha256_file(frontend_dir / "manifest.json") != manifest_hash:
        raise RuntimeError("前端复制后的 DeltaData manifest SHA-256 不一致")

    index = json.loads(FRONTEND_INDEX.read_text(encoding="utf-8"))
    targets = [
        row for row in index.get("targets", []) if row.get("target_id") != TARGET_ID
    ]
    targets.append(
        {
            "target_id": TARGET_ID,
            "label": "DeltaData · clean-air pace reverse-engineered v2",
            "status": STATUS,
            "run_id": run_id,
            "manifest": (
                f"data/reference-analysis-lab/v2/run={run_id}/manifest.json"
            ),
            "report_sha256": report_hash,
            "page": "deltadata-reverse-engineered-v2.html",
        }
    )
    index["targets"] = targets
    index["latest_run_id"] = run_id
    FRONTEND_INDEX.write_bytes(_json_bytes(index))
    return {
        "run_id": run_id,
        "record_dir": str(record_dir),
        "frontend_dir": str(frontend_dir),
        "report_sha256": report_hash,
        "manifest_sha256": manifest_hash,
        "benchmark": report["reference_benchmark"]["metrics"],
        "pilot_validation": report["validation"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    result = publish(args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
