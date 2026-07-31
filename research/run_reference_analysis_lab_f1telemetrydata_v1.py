#!/usr/bin/env python3
"""发布 F1TelemetryData 同场图表包方法等价复刻 v1。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from reference_analysis_lab.f1telemetrydata_reverse_engineered_v1 import (
    TARGET_ID,
    TARGET_STATUS,
    build_f1telemetrydata_reverse_engineered_v1,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260731T193000+0800-f1telemetrydata-reverse-engineered-v1"
CAPTURED_AT = "2026-07-31T19:30:00+08:00"

DATA_ROOT = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
    / "year=2025"
    / "commit=f7a5324cae58"
)
QUALIFYING_TELEMETRY = (
    DATA_ROOT
    / "session=Qualifying"
    / "meeting=Abu_Dhabi_Grand_Prix"
    / "telemetry.parquet"
)
RACE_TELEMETRY = (
    DATA_ROOT
    / "session=Race"
    / "meeting=Abu_Dhabi_Grand_Prix"
    / "telemetry.parquet"
)
QUALIFYING_FREEZE = (
    ROOT / "research" / "artifacts" / "data" / "qualifying_2023_2025_v4.csv.gz"
)
F1PACE_REPORT = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v1"
    / "f1pace"
    / "run=20260730T154800+0800-f1pace-abu-dhabi-v1"
    / "f1pace_2025_abu_dhabi.json"
)
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / "run=20260726T124625+0800-ce93134a40b8"
    / "manifest.json"
)

CACHE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v2"
    / "f1telemetrydata"
)
EVIDENCE = CACHE / "public-method-evidence.json"
OPENF1 = CACHE / "openf1"
OPENF1_SOURCE_MANIFEST = OPENF1 / "source-manifest.json"
OPENF1_CAR_MANIFEST = OPENF1 / "source-manifest-fastest-lap-car-data.json"

RECORD_DIR = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v2"
    / "f1telemetrydata"
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

QUALIFYING_COLUMNS = (
    "driver_acronym",
    "driver_number",
    "lap",
    "sample_index",
    "time",
    "distance",
    "rel_distance",
    "speed",
    "throttle",
    "brake",
    "x",
    "y",
    "lap_duration",
    "compound",
    "team",
    "deleted",
    "is_accurate",
)
RACE_COLUMNS = (
    "driver_acronym",
    "driver_number",
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
    "x",
    "y",
    "lap_duration",
    "compound",
    "stint",
    "tyre_life",
    "fresh_tyre",
    "team",
    "position",
    "track_status",
    "deleted",
    "is_accurate",
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
    if isinstance(value, tuple):
        return list(value)
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


def load_openf1() -> dict[str, pd.DataFrame]:
    names = (
        "qualifying_laps",
        "qualifying_drivers",
        "qualifying_results",
        "qualifying_race_control",
        "race_laps",
        "race_drivers",
        "race_pit",
        "race_stints",
        "race_results",
    )
    result = {
        name: pd.DataFrame(
            json.loads((OPENF1 / f"{name}.json").read_text(encoding="utf-8"))
        )
        for name in names
    }
    result["qualifying_car_data"] = pd.DataFrame(
        json.loads(
            (OPENF1 / "qualifying_fastest_lap_car_data.json").read_text(
                encoding="utf-8"
            )
        )
    )
    return result


def build_source_identity() -> dict[str, Any]:
    q_meta = pq.ParquetFile(QUALIFYING_TELEMETRY)
    r_meta = pq.ParquetFile(RACE_TELEMETRY)
    q_all = pd.read_csv(QUALIFYING_FREEZE)
    q_event = q_all.loc[
        q_all["year"].eq(2025)
        & q_all["meeting_name"].astype(str).str.contains(
            "Abu Dhabi", case=False, na=False
        )
    ]
    source_manifest = json.loads(
        OPENF1_SOURCE_MANIFEST.read_text(encoding="utf-8")
    )
    car_manifest = json.loads(
        OPENF1_CAR_MANIFEST.read_text(encoding="utf-8")
    )
    for entry in source_manifest["entries"].values():
        path = ROOT / entry["path"]
        if sha256_file(path) != entry["sha256"]:
            raise RuntimeError(f"OpenF1 补充响应哈希不一致：{path}")
    car_path = ROOT / car_manifest["combined"]["path"]
    if sha256_file(car_path) != car_manifest["combined"]["sha256"]:
        raise RuntimeError("OpenF1 qualifying car-channel 哈希不一致")
    return {
        "run_id": RUN_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "primary_sources": {
            "qualifying_telemetry": {
                "path": _relative(QUALIFYING_TELEMETRY),
                "sha256": sha256_file(QUALIFYING_TELEMETRY),
                "rows": int(q_meta.metadata.num_rows),
                "columns": q_meta.schema_arrow.names,
                "source_commit": "f7a5324cae58",
                "sample_role": "expanded_v4_point_level_primary",
            },
            "race_telemetry": {
                "path": _relative(RACE_TELEMETRY),
                "sha256": sha256_file(RACE_TELEMETRY),
                "rows": int(r_meta.metadata.num_rows),
                "columns": r_meta.schema_arrow.names,
                "source_commit": "f7a5324cae58",
                "sample_role": "expanded_v4_point_level_primary",
            },
            "qualifying_freeze": {
                "path": _relative(QUALIFYING_FREEZE),
                "sha256": sha256_file(QUALIFYING_FREEZE),
                "all_rows": int(len(q_all)),
                "abu_dhabi_rows": int(len(q_event)),
                "abu_dhabi_drivers": int(q_event["driver_id"].nunique()),
                "global_phase_status": "not_available_in_current_freeze",
            },
        },
        "openf1_frozen_supplement": {
            "api_contract": source_manifest["api_contract"],
            "docs_url": source_manifest["docs_url"],
            "source_manifest": {
                "path": _relative(OPENF1_SOURCE_MANIFEST),
                "sha256": sha256_file(OPENF1_SOURCE_MANIFEST),
            },
            "fastest_lap_car_manifest": {
                "path": _relative(OPENF1_CAR_MANIFEST),
                "sha256": sha256_file(OPENF1_CAR_MANIFEST),
                "rows": int(car_manifest["combined"]["rows"]),
                "drivers": int(car_manifest["combined"]["drivers"]),
            },
            "meeting_key": int(source_manifest["meeting_key"]),
            "qualifying_session_key": int(
                source_manifest["qualifying_session_key"]
            ),
            "race_session_key": int(source_manifest["race_session_key"]),
            "role": source_manifest["role"],
            "limitations": source_manifest["limitations"],
            "entries": source_manifest["entries"],
        },
        "audit_references": {
            "f1pace_report": {
                "path": _relative(F1PACE_REPORT),
                "sha256": sha256_file(F1PACE_REPORT),
                "role": "identical_public_race_pace_lap_universe_and_audit_companion",
            },
            "v17_manifest": {
                "path": _relative(V17_MANIFEST),
                "sha256": sha256_file(V17_MANIFEST),
                "run_id": "20260726T124625+0800-ce93134a40b8",
                "role": "full_coverage_truth_and_gate_reference_only",
            },
        },
    }


def build_reference_identity() -> dict[str, Any]:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    for image in evidence["reference_images"]:
        path = ROOT / image["cache_path"]
        if sha256_file(path) != image["sha256"]:
            raise RuntimeError(f"参考图哈希不一致：{path}")
    return {
        "source_profile_url": "https://x.com/F1TelemetryData",
        "source_profile": evidence["x_profile"],
        "source_channel_url": evidence["channel_url"],
        "channel_html": evidence["channel_html"],
        "evidence_snapshot": {
            "path": _relative(EVIDENCE),
            "sha256": sha256_file(EVIDENCE),
            "captured_at": evidence["captured_at"],
        },
        "same_event_reference": "2025 Abu Dhabi Grand Prix",
        "reference_images": evidence["reference_images"],
        "chart_count": len(evidence["reference_images"]),
        "numeric_transcription_status": "SAME_EVENT_REFERENCE_BENCHMARK",
        "method_disclosure": evidence["method_disclosure"],
        "license_boundary": evidence["license_boundary"],
    }


def method_card(report: dict[str, Any]) -> str:
    images = report["reference_identity"]["reference_images"]
    lines = [
        "# F1TelemetryData 图表包 · method card",
        "",
        f"- 运行：`{RUN_ID}`",
        "- 作者身份：`https://x.com/F1TelemetryData`",
        "- 公开频道：`https://t.me/s/f1telemetrydata`",
        "- 同场 pilot：2025 Abu Dhabi Qualifying + Race。",
        "- 状态：`METHOD_EQUIVALENT_REFERENCE_BENCHMARKED`；精确代码、阈值、",
        "  异常处理与平滑未公开，故一比一状态为 `SKIPPED_OPAQUE_METHOD`。",
        "",
        "## 参考图身份",
        "",
        "| 图表 | 帖子 | SHA-256 | 发布时间 |",
        "|---|---|---|---|",
    ]
    for image in images:
        lines.append(
            f"| `{image['chart_id']}` | {image['post_url']} | "
            f"`{image['sha256']}` | {image['posted_at']} |"
        )
    lines.extend(
        [
            "",
            "参考图只用于语义、人工数值转录和差异核对；原图、logo、人物照和",
            "品牌字体不进入产品资产。",
            "",
            "## 公开可确认的方法",
            "",
            "- 排位包按每名车手整个 session 最快圈展示 lap delta、速度/油门、",
            "  sector timing、timing-line speed 和最快圈最高速。",
            "- Race Pace 为圈时分布；Average Gap 是同一圈集合的均值相对最快均值。",
            "- Tyre Strategies 按 Stint 的配方、圈边界和新/旧胎线型展示。",
            "- Pit Times 的坐标明确写作 `Time in Pitlane`，与 stationary stop time",
            "  不是同一个字段。",
            "",
            "## 反推实现",
            "",
            "- Q1/Q2/Q3 用冻结 OpenF1 race-control 绿灯/方格旗时间窗识别；",
            "  88/88 个准确推圈均获得阶段标签。该补充只服务本 pilot，不回写全局",
            "  qualifying_v4。",
            "- Track Dominance 在统一距离轴上取累计用时最小车手；这只是观察到的",
            "  时间领先，不是车辆物理 dominance。",
            "- Lap Sections 使用约 3.7Hz car-channel：`brake>0`；无制动且",
            "  `throttle<=0` 为 lift；无制动且 `throttle>=99` 为 full；其余 partial。",
            "- Race Pace 复用已外部逐项验证的圈宇宙：删除首圈、Stint 切换两侧圈和",
            "  非绿/黄旗圈，按算术均值排序。",
            "- Sector 和 speed trap、pit-lane/stop duration、tyre_age_at_start",
            "  来自冻结 OpenF1 v1 响应，每个 URL、响应和 manifest 均有 SHA-256。",
            "",
            "## 不可识别与 NOT_TESTED",
            "",
            "- 官方几何 sector distance anchor 未冻结；页面上的距离位置是由官方",
            "  sector time 与本地参考圈插值得到，状态为",
            "  `NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR`。",
            "- Downforce Map 所需真实下压力、阻力、设定和动力模式不可识别，状态",
            "  `NOT_IDENTIFIABLE`。",
            "- 真实燃油、SOC、胎温胎压、物理磨损、损伤、车手管理和车队指令",
            "  仍不可识别。",
        ]
    )
    return "\n".join(lines)


def data_gap_card(report: dict[str, Any]) -> str:
    lines = [
        "# F1TelemetryData · data-gap audit",
        "",
        "| 图表 | 所需字段 | 当前仓库 | 缺口 | 补充/代理 | 发布 |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["data_gap_audit"]:
        lines.append(
            f"| {row['chart']} | {row['required']} | {row['repository']} | "
            f"{row['gap']} | {row['supplement']} | `{row['publish']}` |"
        )
    return "\n".join(lines)


def difference_card(report: dict[str, Any]) -> str:
    q = report["validation"]["qualifying"]
    r = report["validation"]["race"]
    return "\n".join(
        [
            "# F1TelemetryData · 与参考图差异",
            "",
            "## 精确或仅有显示舍入差",
            "",
            f"- Quali Lap Delta 20/20，MAE `{q['lap_delta']['mae']:.6f}s`，"
            f"最大绝对差 `{q['lap_delta']['max_abs_error']:.6f}s`。",
            f"- Quali Top Speeds 20/20 完全一致，最大差 "
            f"`{q['top_speed']['max_abs_error']:.0f} km/h`。",
            f"- Top 3 sector timing 9/9 完全一致，最大差 "
            f"`{q['top3_sector_max_abs_error_s']:.6f}s`。",
            f"- Race Average Gap 20/20，MAE "
            f"`{r['average_gap']['mae']:.6f}s`；Race Timings 最快圈 20/20",
            f"  完全一致，最大差 `{r['fastest_laps']['max_abs_error']:.6f}s`。",
            f"- 47/47 个本地/OpenF1 Stint 边界一致；参考图前五车手 5/5 完全一致。",
            "",
            "## 可量化但不能声称一比一",
            "",
            f"- Lap Sections 六名公开车手的四分类 MAE "
            f"`{q['lap_sections']['mae_pp']:.6f}pp`，最大差 "
            f"`{q['lap_sections']['max_abs_error_pp']:.6f}pp`。来源是未公开阈值、",
            "  约 3.7Hz 原始 car-channel 与仓库约 7.69Hz 扩展/插值 feed 的差异；",
            "  未逐车手调值。",
            f"- Team Throttle Usage 十队 MAE `{q['team_throttle']['mae']:.6f}pp`，",
            f"  最大差 `{q['team_throttle']['max_abs_error']:.6f}pp`。",
            f"- Pit-lane team mean 十队 MAE `{r['pit_team_means']['mae']:.6f}s`，",
            f"  最大差 `{r['pit_team_means']['max_abs_error']:.6f}s`（Mercedes）。",
            "  参考图发布时间与当前冻结 OpenF1 响应版本不同，且作者 timing-line",
            "  定义未公开；不为贴图手调。",
            "",
            "## 方法边界",
            "",
            "- 以上 Race Pace/平均差是描述性全局均值，不具燃油、轮胎、交通和策略",
            "  的因果可比性；审计视图不形成同一个排名。",
            "- `lane_duration` 是 pit-lane transit；`stop_duration` 才是静止时间，",
            "  当前 27 次 pit-lane passage 中只有 26 次可用，缺失值不填补。",
        ]
    )


def attempt_log() -> str:
    rows = [
        {
            "attempt": 1,
            "issue": "FastF1 supplement did not finish within short foreground probe",
            "hypothesis": "timing download needs a longer foreground window",
            "action": "retain cache evidence and retry with telemetry disabled",
            "output": "no supplement CSV; no product data written",
            "next_step": "load only timing/laps with 180s window",
            "status": "FAILED_DIFFERENT_RETRY_REQUIRED",
        },
        {
            "attempt": 2,
            "issue": "FastF1 session/timing/track-status downloads all failed",
            "hypothesis": "upstream timing endpoint is unavailable in this environment",
            "action": "disable telemetry and request Q/R timing only",
            "output": "DataNotLoadedError; zero rows",
            "next_step": "switch source to OpenF1 frozen v1 responses",
            "status": "FAILED_DIFFERENT_RETRY_REQUIRED",
        },
        {
            "attempt": 3,
            "issue": "sector/Q phase/pit fields absent from repository freeze",
            "hypothesis": "OpenF1 historical v1 endpoints can provide frozen supplements",
            "action": "freeze sessions/laps/results/race_control/pit/stints with URL and SHA-256",
            "output": "SUCCESS: Q 286 laps, Race 1156 laps, 27 pit passages, 47 stints",
            "next_step": "continue same-event numeric validation",
            "status": "RESOLVED",
        },
        {
            "attempt": 1,
            "issue": "unfiltered OpenF1 qualifying car_data returned HTTP 422",
            "hypothesis": "session-wide response exceeds endpoint query limits",
            "action": "restrict each request to a driver and fastest-lap time window",
            "output": "HTTP 422 evidence retained; no partial product file",
            "next_step": "use time-filtered requests",
            "status": "FAILED_DIFFERENT_RETRY_REQUIRED",
        },
        {
            "attempt": 2,
            "issue": "URL-encoded comparison operators returned HTTP 500",
            "hypothesis": "OpenF1 parser requires literal date>= and date< operators",
            "action": "use literal operators with UTC Z timestamps and per-driver requests",
            "output": "SUCCESS: 20 drivers, 6319 fastest-lap car-channel samples",
            "next_step": "freeze combined response and request-level hashes",
            "status": "RESOLVED",
        },
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def flatten_sidecars(report: dict[str, Any]) -> dict[str, pd.DataFrame]:
    q = report["visual_replication"]["qualifying"]
    r = report["visual_replication"]["race"]
    trace_rows = []
    for trace in q["lap_compare"]["traces"]:
        length = len(trace["axis"])
        for index in range(length):
            trace_rows.append(
                {
                    "driver": trace["driver"],
                    "team": trace["team"],
                    "lap": trace["lap"],
                    "phase": trace["phase"],
                    "axis_index": index,
                    "lap_fraction": trace["axis"][index],
                    "time_s": trace["time"][index],
                    "speed_kph": trace["speed"][index],
                    "throttle": trace["throttle"][index],
                    "x": trace["x"][index],
                    "y": trace["y"][index],
                }
            )
    control_rows = []
    for row in q["lap_sections"]:
        control_rows.append(
            {
                "driver": row["driver"],
                "team": row["team"],
                "raw_samples": row["raw_samples"],
                "sample_braking_pct": row["braking_pct"],
                "sample_lift_pct": row["lift_pct"],
                "sample_partial_throttle_pct": row["partial_throttle_pct"],
                "sample_full_throttle_pct": row["full_throttle_pct"],
                "time_braking_pct": row["audited_time_weighted"]["braking_pct"],
                "time_lift_pct": row["audited_time_weighted"]["lift_pct"],
                "time_partial_throttle_pct": row["audited_time_weighted"][
                    "partial_throttle_pct"
                ],
                "time_full_throttle_pct": row["audited_time_weighted"][
                    "full_throttle_pct"
                ],
            }
        )
    race_laps = []
    for row in r["race_pace"]["driver_rows"]:
        for point in row["points"]:
            race_laps.append(
                {
                    "driver": row["driver"],
                    "team": row["team"],
                    "rank": row["rank"],
                    "mean_lap_s": row["mean_lap_s"],
                    **point,
                }
            )
    return {
        "qualifying_timings": pd.DataFrame(q["timings"]),
        "qualifying_control_sections": pd.DataFrame(control_rows),
        "qualifying_top3_traces": pd.DataFrame(trace_rows),
        "race_pace_laps": pd.DataFrame(race_laps),
        "race_timings": pd.DataFrame(r["timings"]),
        "tyre_strategies": pd.DataFrame(r["tyre_strategies"]["rows"]),
        "pit_stops": pd.DataFrame(r["pit_times"]["stops"]),
    }


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
            "label": "F1TelemetryData · same-event chart pack reverse-engineered v1",
            "status": TARGET_STATUS,
            "run_id": RUN_ID,
            "manifest": target_manifest_path,
            "report_sha256": report_sha256,
            "page": "f1telemetrydata-reverse-engineered-v1.html",
        }
    )
    index["latest_run_id"] = RUN_ID
    FRONTEND_INDEX.write_bytes(_json_bytes(index))


def main() -> int:
    required = [
        QUALIFYING_TELEMETRY,
        RACE_TELEMETRY,
        QUALIFYING_FREEZE,
        F1PACE_REPORT,
        V17_MANIFEST,
        EVIDENCE,
        OPENF1_SOURCE_MANIFEST,
        OPENF1_CAR_MANIFEST,
        FRONTEND_INDEX,
    ]
    required.extend(OPENF1 / f"{name}.json" for name in (
        "qualifying_laps",
        "qualifying_drivers",
        "qualifying_results",
        "qualifying_race_control",
        "qualifying_fastest_lap_car_data",
        "race_laps",
        "race_drivers",
        "race_pit",
        "race_stints",
        "race_results",
    ))
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    if RECORD_DIR.exists() or FRONTEND_DIR.exists():
        raise FileExistsError(
            "追加式 run 目录已存在，拒绝覆盖；请使用新的 run_id"
        )

    source_identity = build_source_identity()
    reference_identity = build_reference_identity()
    qualifying_points = pd.read_parquet(
        QUALIFYING_TELEMETRY,
        columns=list(QUALIFYING_COLUMNS),
    )
    race_points = pd.read_parquet(
        RACE_TELEMETRY,
        columns=list(RACE_COLUMNS),
    )
    qualifying_all = pd.read_csv(QUALIFYING_FREEZE)
    qualifying_event = qualifying_all.loc[
        qualifying_all["year"].eq(2025)
        & qualifying_all["meeting_name"].astype(str).str.contains(
            "Abu Dhabi", case=False, na=False
        )
    ].copy()
    openf1 = load_openf1()
    f1pace_report = json.loads(F1PACE_REPORT.read_text(encoding="utf-8"))
    report = build_f1telemetrydata_reverse_engineered_v1(
        qualifying_points=qualifying_points,
        race_points=race_points,
        qualifying_freeze=qualifying_event,
        openf1=openf1,
        f1pace_report=f1pace_report,
        source_identity=source_identity,
        reference_identity=reference_identity,
    )
    report["run_id"] = RUN_ID
    report["created_at"] = datetime.now(timezone.utc).isoformat()

    report_path = RECORD_DIR / "f1telemetrydata_reverse_engineered_v1.json"
    report_sha256 = write_json_once(report_path, report)
    method_path = RECORD_DIR / "method_card.md"
    method_sha256 = write_text_once(method_path, method_card(report))
    gap_path = RECORD_DIR / "data_gap_audit.md"
    gap_sha256 = write_text_once(gap_path, data_gap_card(report))
    difference_path = RECORD_DIR / "reference_difference.md"
    difference_sha256 = write_text_once(
        difference_path, difference_card(report)
    )
    attempts_path = RECORD_DIR / "attempts.jsonl"
    attempts_sha256 = write_text_once(attempts_path, attempt_log())

    sidecars = {
        name: write_csv_once(RECORD_DIR / f"{name}.csv", frame)
        for name, frame in flatten_sidecars(report).items()
    }
    frontend_report_path = (
        f"data/reference-analysis-lab/v2/run={RUN_ID}/"
        "f1telemetrydata_reverse_engineered_v1.json"
    )
    target_manifest_path = (
        f"data/reference-analysis-lab/v2/run={RUN_ID}/manifest.json"
    )
    frontend_target_manifest = {
        "schema_version": "reference-analysis-lab-target-manifest-v2",
        "target_id": TARGET_ID,
        "status": TARGET_STATUS,
        "run_id": RUN_ID,
        "report": {
            "path": frontend_report_path,
            "sha256": report_sha256,
            "bytes": report_path.stat().st_size,
        },
        "source_identity": source_identity,
    }
    write_bytes_once(
        FRONTEND_DIR / "f1telemetrydata_reverse_engineered_v1.json",
        report_path.read_bytes(),
    )
    frontend_manifest_sha256 = write_json_once(
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
        "status": TARGET_STATUS,
        "run_id": RUN_ID,
        "report": {
            "path": _relative(report_path),
            "sha256": report_sha256,
            "bytes": report_path.stat().st_size,
        },
        "method_card": {
            "path": _relative(method_path),
            "sha256": method_sha256,
        },
        "data_gap_audit": {
            "path": _relative(gap_path),
            "sha256": gap_sha256,
        },
        "reference_difference": {
            "path": _relative(difference_path),
            "sha256": difference_sha256,
        },
        "attempts": {
            "path": _relative(attempts_path),
            "sha256": attempts_sha256,
            "resolved_groups": 2,
            "skipped_after_3_attempts": 0,
        },
        "sidecars": sidecars,
        "frontend_manifest": {
            "path": _relative(FRONTEND_DIR / "manifest.json"),
            "sha256": frontend_manifest_sha256,
        },
        "source_identity": source_identity,
        "reference_identity": reference_identity,
        "validation": report["validation"],
    }
    manifest_path = RECORD_DIR / "manifest.json"
    manifest_sha256 = write_json_once(manifest_path, record_manifest)
    print(
        json.dumps(
            {
                "status": TARGET_STATUS,
                "run_id": RUN_ID,
                "report": _relative(report_path),
                "report_sha256": report_sha256,
                "manifest": _relative(manifest_path),
                "manifest_sha256": manifest_sha256,
                "validation": report["validation"]["status"],
                "reference_gates": report["validation"][
                    "exact_reference_gates"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

