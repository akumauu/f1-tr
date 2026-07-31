#!/usr/bin/env python3
"""生成参考分析复刻实验室 v1 的 F1pace 真实 pilot。"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from reference_analysis_lab.f1pace_pilot import (
    build_f1pace_pilot,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "20260730T154800+0800-f1pace-abu-dhabi-v1"
RACE_PARQUET = (
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
QUALIFYING_CSV = ROOT / "research" / "artifacts" / "data" / "qualifying_2023_2025_v4.csv.gz"
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / "run=20260726T124625+0800-ce93134a40b8"
    / "manifest.json"
)
RUNTIME_REFERENCE_DIR = ROOT / ".runtime-cache" / "reference-analysis-lab-v1" / "f1pace"
RECORDS_BASE = ROOT / "research" / "records" / "reference_analysis_lab_v1" / "f1pace"
FRONTEND_BASE = ROOT / "frontend" / "public" / "data" / "reference-analysis-lab" / "v1"
PUBLIC_ROOT = ROOT / "frontend" / "public"
INDEX_PATH = FRONTEND_BASE / "manifest.json"


REFERENCE_MEANS = {
    "NOR": 88.485,
    "LEC": 88.594,
    "VER": 88.646,
    "PIA": 88.838,
    "HAM": 89.433,
    "RUS": 89.437,
    "HUL": 89.539,
    "ALB": 89.646,
    "GAS": 89.726,
    "ALO": 89.761,
    "OCO": 89.771,
    "STR": 89.829,
    "COL": 89.852,
    "BEA": 89.860,
    "TSU": 89.943,
    "ANT": 89.956,
    "SAI": 89.960,
    "LAW": 90.010,
    "BOR": 90.019,
    "HAD": 90.169,
}

REFERENCE_IMAGE_URLS = {
    "top_10": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_top_10.webp",
    "bottom_10": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_bottom_10.webp",
    "all": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_all.webp",
    "summarized": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_race_pace_summarized.webp",
    "traffic_laps": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_race_laps_traffic_ind.webp",
    "traffic_summary": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_race_laps_traffic_summary.webp",
    "pairwise": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/gp_24_2025_race_pace_comp.webp",
}


def _write_json(path: Path, payload: Any) -> str:
    """只向新的隔离目录写 JSON，并返回文件哈希。"""

    if path.exists():
        raise FileExistsError(f"追加式实验拒绝覆盖既有文件: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return sha256_file(path)


def _reference_identity() -> dict[str, Any]:
    images = []
    for name, url in REFERENCE_IMAGE_URLS.items():
        path = RUNTIME_REFERENCE_DIR / "reference-images" / f"{name}.webp"
        row: dict[str, Any] = {
            "name": name,
            "url": url,
            "local_cache_path": str(path.relative_to(ROOT)),
            "status": "AVAILABLE" if path.exists() else "NOT_FETCHED",
        }
        if path.exists():
            row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
        images.append(row)
    page = RUNTIME_REFERENCE_DIR / "source.html"
    return {
        "page_url": "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/",
        "page_local_cache_path": str(page.relative_to(ROOT)),
        "page_sha256": sha256_file(page) if page.exists() else None,
        "images": images,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "F1pace by F1bythenumbers",
        "license": "CC BY-NC-ND 4.0 boundary retained; reference images are not product assets",
    }


def _source_identity() -> dict[str, Any]:
    parquet = pq.ParquetFile(RACE_PARQUET)
    source_commit = None
    if "source_commit" in parquet.schema.names:
        source_commit = (
            parquet.read(columns=["source_commit"])
            .column("source_commit")
            .unique()
            .to_pylist()
        )
    return {
        "race_telemetry": {
            "path": str(RACE_PARQUET.relative_to(ROOT)),
            "sha256": sha256_file(RACE_PARQUET),
            "rows": int(parquet.metadata.num_rows),
            "schema": "tracinginsights-expanded-v4",
            "source_commit": source_commit,
        },
        "qualifying_freeze": {
            "path": str(QUALIFYING_CSV.relative_to(ROOT)),
            "sha256": sha256_file(QUALIFYING_CSV),
            "phase_field": "absent; q1_q2_q3_phase=not_available_in_current_freeze",
        },
        "v17_audit": {
            "path": str(V17_MANIFEST.relative_to(ROOT)),
            "sha256": sha256_file(V17_MANIFEST),
            "run_id": "20260726T124625+0800-ce93134a40b8",
        },
    }


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    points = pq.ParquetFile(RACE_PARQUET).read().to_pandas()
    qualifying = pd.read_csv(QUALIFYING_CSV, compression="gzip")
    qualifying = qualifying.loc[
        qualifying["meeting_key"].eq("2025|Abu Dhabi Grand Prix")
        & qualifying["session_name"].eq("Qualifying")
    ].copy()
    return points, qualifying


def _validate(pilot: dict[str, Any]) -> dict[str, Any]:
    means = {
        row["driver"]: float(row["mean_lap_s"])
        for row in pilot["visual_replication"]["pace"]["all_drivers"]
    }
    differences = {
        driver: round(means[driver] - expected, 6)
        for driver, expected in REFERENCE_MEANS.items()
    }
    max_abs = max(abs(value) for value in differences.values())
    matrix = pilot["visual_replication"]["pairwise_mean_delta"]["matrix"]
    by_driver = {row["driver"]: row["values"] for row in matrix}
    anti_symmetric = all(
        abs(
            float(by_driver[left][right])
            + float(by_driver[right][left])
        )
        <= 0.001
        for left in by_driver
        for right in by_driver
    )
    traffic_rows = pilot["visual_replication"]["traffic"]["lap_rows"]
    traffic_computation = all(
        row["distance_threshold_basis"] == "speed_kph_to_mps_x_2s"
        and row["telemetry_samples"] > 0
        for row in traffic_rows
    )
    return {
        "status": "PASS" if max_abs <= 0.001 and anti_symmetric and traffic_computation else "FAIL",
        "reference_mean_rows": len(REFERENCE_MEANS),
        "computed_mean_rows": len(means),
        "mean_abs_difference_max_s": max_abs,
        "mean_difference_s": differences,
        "mean_tolerance_s": 0.001,
        "pairwise_matrix_antisymmetric": anti_symmetric,
        "traffic_uses_point_time_weights": traffic_computation,
        "traffic_lap_rows": len(traffic_rows),
        "qualifying_phase_status": pilot["qualifying_analysis"]["q1_q2_q3_phase"],
        "direct_causal_ranking": False,
    }


def _method_card_markdown(pilot: dict[str, Any], validation: dict[str, Any]) -> str:
    reference = pilot["reference_identity"]
    source = pilot["source_identity"]
    ledger = pilot["exclusion_ledger"]
    pace = pilot["visual_replication"]["pace"]
    return f"""# F1pace 2025 Abu Dhabi Race pace method card

## 来源与身份

- 原作者页面：{reference['page_url']}
- 图表包：Race pace Top 10 / Bottom 10 / All drivers、Summarized race pace、Laps in traffic、Race pace delta
- 页面抓取时间：{reference['fetched_at']}
- 页面 SHA-256：`{reference['page_sha256']}`
- 真实数据：`{source['race_telemetry']['path']}`，SHA-256 `{source['race_telemetry']['sha256']}`，{source['race_telemetry']['rows']:,} 个逐点样本
- 复刻身份：`reference-analysis-f1pace-v1`；本实验输出与 Race Dossier v17 分离

## 公开口径与边界

公开方法明确移除首圈、进/出站圈和 SC/VSC 圈，保留绿/黄旗与湿胎圈；按车手均值排序，Q1/Q3 作为分布四分位，Stint 只决定横向 jitter；交通按逐圈超过 33% 时间处于前车 2 秒内判断，包含回退车。逐点交通实现使用 `distance_to_driver_ahead`、速度换算的 2 秒距离阈值和逐点时间权重，绝不使用圈级 median gap。

视觉上重绘中性网格、四分位背景、逐圈点、Stint jitter、交通热图和反对称均值差矩阵；不复制原始图片、logo 或品牌字体。页面署名保留 F1pace by F1bythenumbers，并遵守 CC BY-NC-ND 边界。

无法从页面公开代码确认的选圈细节不被声称为一比一复现。真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令和全局均值差的因果车辆/车手贡献均不可识别。

## 真实 pilot 验收

- 圈宇宙：{ledger['lap_universe']} 圈；F1pace 口径有效圈：{ledger['pace_eligible']} 圈；逐点样本：{ledger['point_samples']['race_total']:,}。
- 排除账本：首圈 {ledger['excluded']['first_lap']}；进/出站边界并集 {ledger['excluded']['pit_in_out_boundary_union']}；非绿/黄旗 {ledger['excluded']['non_green_or_yellow']}；标记为 deleted 但仍保留的有效圈 {ledger['excluded']['deleted_marker_retained_in_eligible']}。
- 公开均值比对：{validation['computed_mean_rows']}/{validation['reference_mean_rows']} 名车手，最大绝对差 {validation['mean_abs_difference_max_s']:.6f}s，容差 {validation['mean_tolerance_s']:.3f}s。
- pairwise 反对称：`{validation['pairwise_matrix_antisymmetric']}`；交通逐点时间加权：`{validation['traffic_uses_point_time_weights']}`。
- 排位：`{pilot['qualifying_analysis']['interpretation']}`；不把当前冻结集冒充 Q1/Q2/Q3。

## 两种视图

- `visual_replication`：忠实展示公开页面的全场均值、Q1/Q3、逐圈点、交通比例和 pairwise 均值 delta。
- `audited_analysis`：固定为 `audit_only`，逐车手披露停站、配方、胎龄、交通构成和原因；不从全局均值形成因果全序。
"""


def _data_gap_markdown(pilot: dict[str, Any]) -> str:
    lines = [
        "# F1pace data-gap audit",
        "",
        "| 目标字段 | 当前字段 | 状态 | 替代代理 | 发布边界 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in pilot["data_gap_audit"]:
        lines.append(
            "| {field} | {current_field} | {status} | {substitute} | {publication} |".format(
                **{key: str(value or "—") for key, value in row.items()}
            )
        )
    return "\n".join(lines) + "\n"


def run(run_id: str) -> dict[str, Any]:
    record_dir = RECORDS_BASE / f"run={run_id}"
    frontend_dir = FRONTEND_BASE / f"run={run_id}"
    if record_dir.exists() or frontend_dir.exists():
        raise FileExistsError(f"实验 run 已存在，禁止覆盖: {run_id}")
    for path in (RACE_PARQUET, QUALIFYING_CSV, V17_MANIFEST):
        if not path.is_file():
            raise FileNotFoundError(path)
    points, qualifying = _load_inputs()
    pilot = build_f1pace_pilot(
        points,
        qualifying,
        source_identity=_source_identity(),
        reference_identity=_reference_identity(),
    )
    validation = _validate(pilot)
    created_at = datetime.now(timezone.utc).isoformat()
    report_path = record_dir / "f1pace_2025_abu_dhabi.json"
    report_sha = _write_json(report_path, pilot)
    validation_path = record_dir / "validation.json"
    validation_sha = _write_json(validation_path, validation)
    method_path = record_dir / "method_card.md"
    method_path.parent.mkdir(parents=True, exist_ok=True)
    method_path.write_text(_method_card_markdown(pilot, validation), encoding="utf-8")
    gap_path = record_dir / "data_gap_audit.md"
    gap_path.write_text(_data_gap_markdown(pilot), encoding="utf-8")

    frontend_report = frontend_dir / "f1pace_2025_abu_dhabi.json"
    frontend_sha = _write_json(frontend_report, pilot)
    frontend_manifest = {
        "schema_version": "reference-analysis-lab-target-manifest-v1",
        "target_id": "f1pace",
        "run_id": run_id,
        "status": validation["status"],
        "report": {
            "path": str(frontend_report.relative_to(PUBLIC_ROOT)),
            "sha256": frontend_sha,
        },
        "record_report": str(report_path.relative_to(ROOT)),
        "validation": validation,
        "created_at": created_at,
        "network_calls": [pilot["reference_identity"]["page_url"]],
    }
    frontend_manifest_path = frontend_dir / "manifest.json"
    frontend_manifest_sha = _write_json(frontend_manifest_path, frontend_manifest)
    run_manifest = {
        "schema_version": "reference-analysis-lab-run-manifest-v1",
        "run_id": run_id,
        "method_version": "f1pace-v1",
        "target_id": "f1pace",
        "created_at": created_at,
        "status": validation["status"],
        "record_report": {
            "path": str(report_path.relative_to(ROOT)),
            "sha256": report_sha,
        },
        "frontend_report": {
            "path": str(frontend_report.relative_to(ROOT)),
            "sha256": frontend_sha,
        },
        "frontend_manifest": {
            "path": str(frontend_manifest_path.relative_to(ROOT)),
            "sha256": frontend_manifest_sha,
        },
        "coverage": pilot["exclusion_ledger"],
        "validation": validation,
        "reference_images": pilot["reference_identity"]["images"],
        "network_calls": [pilot["reference_identity"]["page_url"]],
        "publication_boundary": pilot["boundaries"],
    }
    run_manifest_path = record_dir / "manifest.json"
    run_manifest_sha = _write_json(run_manifest_path, run_manifest)

    # 根索引只追加新的目标条目，不重写既有 target 身份。
    FRONTEND_BASE.mkdir(parents=True, exist_ok=True)
    if INDEX_PATH.exists():
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    else:
        index = {
            "schema_version": "reference-analysis-lab-index-v1",
            "targets": [],
        }
    index["latest_run_id"] = run_id
    index["targets"] = [
        row for row in index.get("targets", []) if row.get("target_id") != "f1pace"
    ] + [
        {
            "target_id": "f1pace",
            "title": "F1pace 2025 Abu Dhabi Race pace",
            "status": validation["status"],
            "manifest": str(frontend_manifest_path.relative_to(PUBLIC_ROOT)),
            "run_id": run_id,
        }
    ]
    index_path_tmp = INDEX_PATH.with_suffix(".json.tmp")
    index_path_tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index_path_tmp.replace(INDEX_PATH)
    print(json.dumps({
        "run_id": run_id,
        "status": validation["status"],
        "record_manifest": str(run_manifest_path.relative_to(ROOT)),
        "record_manifest_sha256": run_manifest_sha,
        "frontend_manifest": str(frontend_manifest_path.relative_to(ROOT)),
        "validation": validation,
    }, ensure_ascii=False, indent=2))
    return run_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    run(str(args.run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
