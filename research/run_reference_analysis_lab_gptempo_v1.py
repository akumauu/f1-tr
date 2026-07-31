#!/usr/bin/env python3
"""发布 GP Tempo 公开分段 Delta 方法复刻 v1。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from reference_analysis_lab.gptempo_reverse_engineered_v1 import (
    TARGET_ID,
    TARGET_STATUS,
    build_gptempo_reverse_engineered_v1,
    build_track_geometry,
    prepare_raw_lap_trace,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260731T213000+0800-gptempo-reverse-engineered-v1"
CAPTURED_AT = "2026-07-31T21:30:00+08:00"
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
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / "run=20260726T124625+0800-ce93134a40b8"
    / "manifest.json"
)
F1TD_OPENF1 = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v2"
    / "f1telemetrydata"
    / "openf1"
)
GPTEMPO_CACHE = (
    ROOT / ".runtime-cache" / "reference-analysis-lab-v2" / "gptempo"
)
GPTEMPO_SOURCE_MANIFEST = GPTEMPO_CACHE / "source-manifest.json"
QUALIFYING_CAR_DATA = (
    F1TD_OPENF1 / "qualifying_fastest_lap_car_data.json"
)
RACE_CAR_DATA = GPTEMPO_CACHE / "race_fastest_lap_car_data.json"

RECORD_DIR = (
    ROOT
    / "research"
    / "records"
    / "reference_analysis_lab_v2"
    / "gptempo"
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

LOCAL_COLUMNS = (
    "driver_acronym",
    "driver_number",
    "lap",
    "rel_distance",
    "x",
    "y",
    "compound",
    "tyre_life",
    "track_status",
    "team",
    "is_accurate",
    "deleted",
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


def parquet_identity(path: Path) -> dict[str, Any]:
    file = pq.ParquetFile(path)
    return {
        "path": _relative(path),
        "sha256": sha256_file(path),
        "rows": int(file.metadata.num_rows),
        "columns": list(file.schema.names),
        "source_commit": "f7a5324cae58",
    }


def frozen_json_identity(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": _relative(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "rows": len(payload) if isinstance(payload, list) else None,
    }


def openf1_frame(name: str) -> pd.DataFrame:
    return pd.DataFrame(
        json.loads((F1TD_OPENF1 / f"{name}.json").read_text(encoding="utf-8"))
    )


def fastest_driver_laps(
    laps: pd.DataFrame,
    drivers: pd.DataFrame,
    *,
    count: int = 5,
) -> pd.DataFrame:
    mapping = (
        drivers.drop_duplicates("driver_number")
        .set_index("driver_number")[["name_acronym", "team_name"]]
    )
    table = laps.loc[
        laps["lap_duration"].notna()
        & laps["duration_sector_1"].notna()
        & laps["duration_sector_2"].notna()
        & laps["duration_sector_3"].notna()
    ].copy()
    table = (
        table.sort_values(["lap_duration", "lap_number"], kind="stable")
        .groupby("driver_number", as_index=False)
        .first()
        .sort_values(["lap_duration", "driver_number"], kind="stable")
        .head(count)
    )
    table["driver"] = table["driver_number"].map(mapping["name_acronym"])
    table["team"] = table["driver_number"].map(mapping["team_name"])
    return table


def local_metadata(
    local_points: pd.DataFrame,
    driver: str,
    lap_number: int,
) -> dict[str, Any]:
    rows = local_points.loc[
        local_points["driver_acronym"].eq(driver)
        & pd.to_numeric(local_points["lap"], errors="coerce").eq(lap_number)
    ]
    if rows.empty:
        raise ValueError(f"本地冻结遥测缺少 {driver} L{lap_number}")
    row = rows.iloc[0]
    return {
        "compound": str(row["compound"]),
        "tyre_life": (
            float(row["tyre_life"])
            if pd.notna(row["tyre_life"])
            else None
        ),
        "track_status": str(row["track_status"]),
        "team": str(row["team"]),
    }


def prepare_session_laps(
    *,
    session: str,
    timing_laps: pd.DataFrame,
    raw_car_data: pd.DataFrame,
    local_points: pd.DataFrame,
) -> list[dict[str, Any]]:
    driver_column = (
        "_f1tr_driver_acronym"
        if "_f1tr_driver_acronym" in raw_car_data.columns
        else "_f1tr_driver"
    )
    prepared = []
    for row in timing_laps.itertuples(index=False):
        driver = str(row.driver)
        lap_number = int(row.lap_number)
        condition = local_metadata(local_points, driver, lap_number)
        car = raw_car_data.loc[
            raw_car_data[driver_column].eq(driver)
        ].copy()
        identity = {
            "lap_id": (
                f"{'Q' if session == 'Qualifying' else 'R'}-"
                f"{driver}-L{lap_number}"
            ),
            "session": session,
            "driver": driver,
            "driver_number": int(row.driver_number),
            "team": str(row.team),
            "lap_number": lap_number,
            **condition,
        }
        prepared.append(
            prepare_raw_lap_trace(
                car,
                date_start=row.date_start,
                lap_duration_s=float(row.lap_duration),
                sector_times_s=[
                    float(row.duration_sector_1),
                    float(row.duration_sector_2),
                    float(row.duration_sector_3),
                ],
                identity=identity,
            )
        )
    return prepared


def source_identity() -> dict[str, Any]:
    frozen = json.loads(
        GPTEMPO_SOURCE_MANIFEST.read_text(encoding="utf-8")
    )
    return {
        "run_id": RUN_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "primary_local": {
            "qualifying": parquet_identity(QUALIFYING_TELEMETRY),
            "race": parquet_identity(RACE_TELEMETRY),
        },
        "raw_car_channel": {
            "qualifying": frozen_json_identity(QUALIFYING_CAR_DATA),
            "race": frozen_json_identity(RACE_CAR_DATA),
            "role": (
                "about 3.7 Hz public raw channel used for method-equivalent "
                "sampling; local expanded-v4 supplies geometry/condition"
            ),
        },
        "openf1_timing": {
            name: frozen_json_identity(F1TD_OPENF1 / f"{name}.json")
            for name in (
                "qualifying_laps",
                "qualifying_drivers",
                "race_laps",
                "race_drivers",
            )
        },
        "gptempo_source_manifest": {
            "path": _relative(GPTEMPO_SOURCE_MANIFEST),
            "sha256": sha256_file(GPTEMPO_SOURCE_MANIFEST),
            "captured_at": frozen["captured_at"],
        },
        "v17_truth_reference": {
            "path": _relative(V17_MANIFEST),
            "sha256": sha256_file(V17_MANIFEST),
            "role": "coverage and non-identifiability boundary only",
        },
    }


def reference_identity() -> dict[str, Any]:
    frozen = json.loads(
        GPTEMPO_SOURCE_MANIFEST.read_text(encoding="utf-8")
    )
    about = frozen["public_method"]["about"]
    return {
        "about_url": "https://gp-tempo.com/about",
        "x_profile_url": "https://x.com/f1_tempo_",
        "captured_at": frozen["captured_at"],
        "about_html": {
            key: about[key] for key in ("path", "sha256", "bytes", "status")
        },
        "about_script": about["assets"][0],
        "x_profile": frozen["public_method"]["x_profile"],
        "method_disclosure": "EXPLICIT_PUBLIC_THREE_STEP_DELTA_METHOD",
        "numeric_reference_status": (
            "NOT_APPLICABLE_PUBLIC_METHOD_PAGE_HAS_NO_FIXED_PILOT_VALUES"
        ),
        "visual_license_boundary": (
            "logic reimplemented; no source logo, font, screenshot or CSS "
            "is distributed in the product"
        ),
    }


def flatten_sidecars(report: Mapping[str, Any]) -> dict[str, pd.DataFrame]:
    laps = []
    traces = []
    for session_rows in report["visual_replication"]["sessions"].values():
        for lap in session_rows:
            laps.append(
                {
                    key: value
                    for key, value in lap.items()
                    if key != "trace"
                }
            )
            trace = lap["trace"]
            for index, axis in enumerate(trace["axis"]):
                traces.append(
                    {
                        "lap_id": lap["lap_id"],
                        "session": lap["session"],
                        "driver": lap["driver"],
                        "rel_distance": axis,
                        "elapsed_s": trace["elapsed_s"][index],
                        "speed_kph": trace["speed_kph"][index],
                        "throttle_pct": trace["throttle_pct"][index],
                        "brake": trace["brake"][index],
                        "gear": trace["gear"][index],
                        "drs": trace["drs"][index],
                    }
                )
    comparisons = []
    endpoints = []
    for comparison in report["visual_replication"]["comparisons"]:
        comparisons.append(
            {
                "reference_lap_id": comparison["reference_lap_id"],
                "candidate_lap_id": comparison["candidate_lap_id"],
                "expected_finish_delta_s": comparison[
                    "expected_finish_delta_s"
                ],
                "actual_finish_delta_s": comparison[
                    "actual_finish_delta_s"
                ],
                "finish_error_s": comparison["finish_error_s"],
                "max_abs_sector_endpoint_error_s": comparison[
                    "max_abs_sector_endpoint_error_s"
                ],
                "endpoint_status": comparison["endpoint_status"],
                "condition_status": comparison["condition"]["status"],
                "condition_reasons": "|".join(
                    comparison["condition"]["reasons"]
                ),
            }
        )
        for row in comparison["sector_endpoints"]:
            endpoints.append(
                {
                    "reference_lap_id": comparison["reference_lap_id"],
                    "candidate_lap_id": comparison["candidate_lap_id"],
                    **row,
                }
            )
    geometry = pd.DataFrame(report["visual_replication"]["track_geometry"])
    return {
        "laps": pd.DataFrame(laps),
        "display_traces": pd.DataFrame(traces),
        "comparisons": pd.DataFrame(comparisons),
        "sector_endpoints": pd.DataFrame(endpoints),
        "track_geometry": geometry,
        "data_gap_audit": pd.DataFrame(report["data_gap_audit"]),
    }


def method_card(report: Mapping[str, Any]) -> str:
    ref = report["reference_identity"]
    validation = report["validation"]
    return f"""# GP Tempo 公开分段 Delta 方法卡

- 目标：GP Tempo / X `@f1_tempo_`
- 公开方法页：{ref["about_url"]}
- 参考抓取：`{ref["captured_at"]}`
- About HTML SHA-256：`{ref["about_html"]["sha256"]}`
- Bundled method script SHA-256：`{ref["about_script"]["sha256"]}`
- X profile SHA-256：`{ref["x_profile"]["sha256"]}`
- 真实 pilot：2025 Abu Dhabi，5 个 Qualifying 最快圈 + 5 个 Race 最快圈
- 状态：`{report["status"]}`

## 公开方法

1. 使用官方 timing feed via FastF1，遥测通常约 4 Hz。
2. 按三个官方 sector time 分段，把 sector 端点当成硬约束。
3. 段内把候选圈距离线性插值到参考圈距离轴。
4. 段内时间线性缩放，使首尾严格等于官方累计差。
5. 只有 sector 端点保证精确；段内曲线是估计。
6. 支持跨 session，但必须提示配方、燃油、天气和赛道演化混合。

## 本次实现

- 真实圈：{validation["real_laps"]}
- 原始 car-channel 样本：{validation["raw_sample_count"]}
- 采样率中位：{validation["sample_hz_median"]} Hz
- 有序比较：{validation["ordered_pair_comparisons"]}
- sector 端点检查：{validation["sector_endpoint_checks"]}
- 最大端点误差：{validation["max_abs_sector_endpoint_error_s"]} s
- 默认选择门：`{validation["default_selection_gate"]}`

## 边界

产品不复制 GP Tempo 的 logo、字体、截图或 CSS。真实燃油、SOC、胎温胎压、物理磨损、
设定、损伤、动力模式和车队指令不可识别；约 4 Hz 下制动点/油门点可能偏移约一个样本。
"""


def data_gap_card(report: Mapping[str, Any]) -> str:
    lines = [
        "# GP Tempo data-gap audit",
        "",
        "| 需要 | 仓库 | 冻结补充/代理 | 发布规则 |",
        "| --- | --- | --- | --- |",
    ]
    for row in report["data_gap_audit"]:
        lines.append(
            f'| {row["required"]} | {row["repository"]} | '
            f'{row["supplement"]} | `{row["publish"]}` |'
        )
    return "\n".join(lines)


def difference_card(report: Mapping[str, Any]) -> str:
    validation = report["validation"]
    return f"""# GP Tempo 参考差异说明

- 公开页面披露完整三步算法，但不提供固定 2025 Abu Dhabi 数值表，故数值参考状态为
  `NOT_APPLICABLE_PUBLIC_METHOD_PAGE_HAS_NO_FIXED_PILOT_VALUES`。
- 本次以算法不变量验收：{validation["sector_endpoint_checks"]} 个 sector 端点的最大
  绝对误差为 `{validation["max_abs_sector_endpoint_error_s"]}s`；
  {validation["finish_endpoint_checks"]} 个终点全部闭合。
- 原始样本采样率范围为 `{validation["sample_hz_min"]}–{validation["sample_hz_max"]}Hz`，
  中位 `{validation["sample_hz_median"]}Hz`，与公开“约 4 Hz”一致。
- 段内曲线没有公开逐点真值，固定标记
  `ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ`，不报告伪造的逐点 MAE。
- 默认 Qualifying 三圈同 session、同 SOFT、胎龄差不超过 2 圈；跨 session 或轮胎
  条件不满足时只显示条件警告，不改名为纯车手差。
"""


def attempt_log() -> str:
    rows = [
        {
            "attempt": 1,
            "issue": "GP Tempo about 首次抓取仅得到 763-byte SPA shell",
            "hypothesis": "公开方法正文位于 bundled JavaScript",
            "action": "保留 HTML 哈希并检查资产入口",
            "output": "发现 /assets/index-BAdvg-92.js",
            "next": "冻结 bundled asset 并核对公开三步方法",
            "status": "RESOLVED_BY_DISTINCT_ATTEMPT_2",
        },
        {
            "attempt": 2,
            "issue": "SPA shell 无法单独承载方法证据",
            "hypothesis": "bundled asset 包含 About 正文",
            "action": "下载 JS/CSS，冻结 URL、字节数与 SHA-256",
            "output": "公开 sector splitting/interpolation/time scaling 文本已固定",
            "next": "按明确方法实现，不再视觉猜测",
            "status": "RESOLVED",
        },
    ]
    return "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    )


def update_frontend_index(
    *,
    target_manifest_path: str,
    report_sha256: str,
) -> None:
    index = json.loads(FRONTEND_INDEX.read_text(encoding="utf-8"))
    if any(row.get("target_id") == TARGET_ID for row in index["targets"]):
        raise ValueError(f"前端索引已存在目标 {TARGET_ID}")
    index["targets"].append(
        {
            "target_id": TARGET_ID,
            "label": "GP Tempo · sector-constrained telemetry delta v1",
            "status": TARGET_STATUS,
            "run_id": RUN_ID,
            "manifest": target_manifest_path,
            "report_sha256": report_sha256,
            "page": "gptempo-reverse-engineered-v1.html",
        }
    )
    index["latest_run_id"] = RUN_ID
    FRONTEND_INDEX.write_bytes(_json_bytes(index))


def main() -> int:
    required = [
        QUALIFYING_TELEMETRY,
        RACE_TELEMETRY,
        V17_MANIFEST,
        GPTEMPO_SOURCE_MANIFEST,
        QUALIFYING_CAR_DATA,
        RACE_CAR_DATA,
        FRONTEND_INDEX,
    ]
    required.extend(
        F1TD_OPENF1 / f"{name}.json"
        for name in (
            "qualifying_laps",
            "qualifying_drivers",
            "race_laps",
            "race_drivers",
        )
    )
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    if RECORD_DIR.exists() or FRONTEND_DIR.exists():
        raise FileExistsError(
            "追加式 run 目录已存在，拒绝覆盖；请使用新的 run_id"
        )

    q_local = pd.read_parquet(
        QUALIFYING_TELEMETRY, columns=list(LOCAL_COLUMNS)
    )
    r_local = pd.read_parquet(
        RACE_TELEMETRY, columns=list(LOCAL_COLUMNS)
    )
    q_laps = fastest_driver_laps(
        openf1_frame("qualifying_laps"),
        openf1_frame("qualifying_drivers"),
    )
    r_laps = fastest_driver_laps(
        openf1_frame("race_laps"),
        openf1_frame("race_drivers"),
    )
    q_car = pd.DataFrame(
        json.loads(QUALIFYING_CAR_DATA.read_text(encoding="utf-8"))
    )
    r_car = pd.DataFrame(
        json.loads(RACE_CAR_DATA.read_text(encoding="utf-8"))
    )
    prepared = prepare_session_laps(
        session="Qualifying",
        timing_laps=q_laps,
        raw_car_data=q_car,
        local_points=q_local,
    )
    prepared.extend(
        prepare_session_laps(
            session="Race",
            timing_laps=r_laps,
            raw_car_data=r_car,
            local_points=r_local,
        )
    )
    geometry_source = q_local.loc[
        q_local["driver_acronym"].eq(str(q_laps.iloc[0]["driver"]))
        & pd.to_numeric(q_local["lap"], errors="coerce").eq(
            int(q_laps.iloc[0]["lap_number"])
        )
    ]
    geometry = build_track_geometry(geometry_source)
    sources = source_identity()
    references = reference_identity()
    report = build_gptempo_reverse_engineered_v1(
        prepared_laps=prepared,
        track_geometry=geometry,
        source_identity=sources,
        reference_identity=references,
    )
    report["run_id"] = RUN_ID
    report["created_at"] = datetime.now(timezone.utc).isoformat()

    report_path = RECORD_DIR / "gptempo_reverse_engineered_v1.json"
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
        "gptempo_reverse_engineered_v1.json"
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
        "source_identity": sources,
    }
    write_bytes_once(
        FRONTEND_DIR / "gptempo_reverse_engineered_v1.json",
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
            "resolved_groups": 1,
            "skipped_after_3_attempts": 0,
        },
        "sidecars": sidecars,
        "frontend_manifest": {
            "path": _relative(FRONTEND_DIR / "manifest.json"),
            "sha256": frontend_manifest_sha256,
        },
        "source_identity": sources,
        "reference_identity": references,
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
                "validation": report["validation"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
