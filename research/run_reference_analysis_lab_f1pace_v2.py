#!/usr/bin/env python3
"""发布 F1pace 交通算法倒推 v2：阿布扎比校准、卡塔尔零重拟合验证。"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from reference_analysis_lab.f1pace_reverse_engineered_v2 import (
    REFERENCE_COLOR_CALIBRATION_MAE_PP,
    REFERENCE_COLOR_CALIBRATION_MAX_ERROR_PP,
    SURROGATE_FEATURE_COLUMNS,
    build_time_headway_features,
    extract_reference_traffic_grid,
    fit_surrogate,
    grouped_cross_validation,
    merge_reference_and_features,
    predict_surrogate,
    sha256_file,
    validation_metrics,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "20260731T120000+0800-f1pace-reverse-engineered-v2"
RECORDS_BASE = (
    ROOT / "research" / "records" / "reference_analysis_lab_v2" / "f1pace"
)
FRONTEND_BASE = (
    ROOT / "frontend" / "public" / "data" / "reference-analysis-lab" / "v2"
)
FRONTEND_INDEX = FRONTEND_BASE / "manifest.json"
V17_MANIFEST = (
    ROOT
    / "research"
    / "records"
    / "race_dossier_v17"
    / "run=20260726T124625+0800-ce93134a40b8"
    / "manifest.json"
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
QATAR_TELEMETRY = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
    / "year=2025"
    / "commit=f7a5324cae58"
    / "session=Race"
    / "meeting=Qatar_Grand_Prix"
    / "telemetry.parquet"
)
ABU_REFERENCE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v1"
    / "f1pace"
    / "reference-images"
    / "traffic_laps.webp"
)
QATAR_REFERENCE = (
    ROOT
    / ".runtime-cache"
    / "reference-analysis-lab-v2"
    / "f1pace-calibration"
    / "qatar-reference"
    / "gp_23_2025_race_laps_traffic_ind.webp"
)

ABU_REFERENCE_URL = (
    "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/"
    "gp_24_2025_race_laps_traffic_ind.webp"
)
QATAR_REFERENCE_URL = (
    "https://f1pace.com/p/2025-qatar-gp-race-pace/"
    "gp_23_2025_race_laps_traffic_ind.webp"
)
ABU_PAGE_URL = "https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/"
QATAR_PAGE_URL = "https://f1pace.com/p/2025-qatar-gp-race-pace/"

ABU_DRIVER_ORDER = (
    "NOR",
    "LEC",
    "VER",
    "PIA",
    "HAM",
    "RUS",
    "HUL",
    "ALB",
    "GAS",
    "ALO",
    "OCO",
    "STR",
    "COL",
    "BEA",
    "TSU",
    "ANT",
    "SAI",
    "LAW",
    "BOR",
    "HAD",
)
QATAR_DRIVER_ORDER = (
    "PIA",
    "NOR",
    "VER",
    "SAI",
    "ANT",
    "RUS",
    "ALO",
    "HAD",
    "LEC",
    "LAW",
    "TSU",
    "ALB",
    "STR",
    "HAM",
    "GAS",
    "BOR",
    "OCO",
    "COL",
    "BEA",
    "HUL",
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
)


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(type(value).__name__)


def write_json(path: Path, payload: Any) -> str:
    if path.exists():
        raise FileExistsError(f"追加式实验拒绝覆盖已有文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    return sha256_file(path)


def write_text(path: Path, content: str) -> str:
    if path.exists():
        raise FileExistsError(f"追加式实验拒绝覆盖已有文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return sha256_file(path)


def _telemetry_identity(path: Path) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha256_file(path),
        "rows": int(parquet.metadata.num_rows),
        "columns": int(len(parquet.schema.names)),
        "schema": "tracinginsights-expanded-v4",
        "source_commit": "f7a5324cae58",
    }


def _reference_identity(
    path: Path,
    *,
    page_url: str,
    image_url: str,
    role: str,
) -> dict[str, Any]:
    modified_at = datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    return {
        "role": role,
        "page_url": page_url,
        "image_url": image_url,
        "cache_path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha256_file(path),
        "bytes": int(path.stat().st_size),
        "cache_modified_at": modified_at,
        "use_boundary": (
            "仅用于语义、网格与颜色数值校准/验收；不复制图片、logo 或品牌字体到产品"
        ),
    }


def _driver_summary(frame: pd.DataFrame, ratio_column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for driver, group in frame.groupby("driver", sort=False):
        ratio = pd.to_numeric(group[ratio_column], errors="coerce")
        rows.append(
            {
                "driver": str(driver),
                "laps": int(len(group)),
                "mean_ratio": (
                    float(ratio.mean()) if ratio.notna().any() else None
                ),
                "traffic_laps_over_33pct": int((ratio > 1.0 / 3.0).sum()),
                "missing_headway_mean": float(
                    pd.to_numeric(group["missing"], errors="coerce").mean()
                ),
            }
        )
    return rows


def _cell_records(
    frame: pd.DataFrame,
    prediction_column: str,
) -> list[dict[str, Any]]:
    selected = frame[
        [
            "driver",
            "lap",
            "reference_traffic_ratio",
            prediction_column,
            "cross_2.0",
            "missing",
            "telemetry_samples",
        ]
    ].copy()
    selected["residual_pp"] = (
        selected[prediction_column] - selected["reference_traffic_ratio"]
    ) * 100.0
    selected = selected.rename(
        columns={
            prediction_column: "visual_replication_ratio",
            "cross_2.0": "audited_analysis_ratio",
        }
    )
    return selected.to_dict(orient="records")


def _method_card_markdown(report: dict[str, Any]) -> str:
    calibration = report["calibration"]
    external = calibration["external_validation"]
    internal = calibration["grouped_cross_validation"]["aggregate"]
    references = report["reference_identity"]
    return f"""# F1pace 交通热图倒推 v2 method card

## 原作者与图表

- 原作者：F1pace by F1bythenumbers
- 校准页：{references['abu_dhabi']['page_url']}
- 事件外验证页：{references['qatar']['page_url']}
- 图表：Laps in traffic / driver×lap traffic percentage heatmap
- 阿布扎比参考图 SHA-256：`{references['abu_dhabi']['sha256']}`
- 卡塔尔参考图 SHA-256：`{references['qatar']['sha256']}`
- 许可边界：参考页标示的 CC BY-NC-ND 边界被保留；原图、logo 和品牌字体均未作为产品资产再发布。

## 公开方法

公开页面说明交通包含回退车；每圈处于前车 2 秒内超过 33% 时间即标记为
`in traffic`。页面也明确称该功能仍在测试，可能存在不准确之处。

## 根据视觉与遥测倒推的部分

1. 网格由固定 driver×lap 单元组成；22 个图内明文标签用于拟合背景绿色通道到
   百分比的线性读图器。读图校准 MAE 为
   {REFERENCE_COLOR_CALIBRATION_MAE_PP:.3f} 个百分点，最大误差约
   {REFERENCE_COLOR_CALIBRATION_MAX_ERROR_PP:.2f} 个百分点。
2. `audited_analysis` 在所有车手共同绝对时间轴上寻找赛道圆周方向最近的物理前车，
   再反插值该前车上一次通过本车当前位置的时刻，按逐点时间权重统计
   `headway <= 2s`。
3. `visual_replication` 使用 frozen-feed 前车、本模型同位置时间差和最近物理前车
   弧长/速度三族 1.5/1.75/2/2.25/2.5 秒比例，共 18 个可解释特征。模型为
   StandardScaler + HuberRegressor，不使用树模型、图像坐标或车手身份记忆。
4. 拟合只使用阿布扎比；卡塔尔完全零重拟合、零参数调整。

## 数值验收

- 阿布扎比整车手分组交叉验证：平均 MAE
  {internal['mean_mae_pp']:.3f} pp，最差折 MAE
  {internal['worst_fold_mae_pp']:.3f} pp，交通圈判定准确率
  {internal['mean_traffic_lap_accuracy'] * 100:.2f}%。
- 卡塔尔事件外验证：{external['rows']} 个真实单元，MAE
  {external['mae_pp']:.3f} pp，RMSE {external['rmse_pp']:.3f} pp，
  P90 绝对误差 {external['p90_abs_error_pp']:.3f} pp，交通圈判定准确率
  {external['traffic_lap_accuracy'] * 100:.2f}%。

## 无法识别与发布边界

原作者精确代码、颜色映射、前车空间算法、异常圈处理和逐点采样权重没有公开，因此
本结果是“事件外验证的方法等价倒推”，不是一比一源码复现。首圈、退赛/事故圈、
进出站与相位积分误差仍可能形成尾部异常。真实燃油、SOC、胎温胎压、物理磨损、
设定、损伤、动力模式和车队指令均不在该交通模型中被识别。
"""


def _data_gap_markdown() -> str:
    return """# F1pace 交通热图倒推 v2 data-gap audit

| 目标字段 | 仓库字段 | 缺口 | 替代代理 | 发布规则 |
|---|---|---|---|---|
| 每个采样时刻的物理前车 | 全车 `lap_start_raw/time/rel_distance` | feed 的 `driver_ahead` 不含全部回退车语义 | 共同绝对时间轴上的圆周最近物理车 | 允许，标为重建 |
| 同位置前车时间差 | 约 4–20Hz 逐点距离/时间 | 无官方连续 timing-loop 真值 | 单调累计相位的反插值 crossing headway | 允许，披露积分误差 |
| 每圈交通时间比例 | 逐点时间与 headway | 采样不等距且有缺口 | 左区间时间权重；缺失区间保留在分母并单列 missing | 允许 |
| 原作者精确百分比 | 公开栅格图 | 无机器可读原值；`>90%` 标签有删失 | 背景色反解代理 | 只用于校准/验收 |
| 原作者精确源码/选点 | 未公开 | 闭源 | Huber 方法等价模型 | 不得称一比一复现 |
| 因果驾驶表现 | 交通比例 | 燃油/轮胎/策略等混杂 | 无 | 禁止因果排名 |
"""


def _difference_markdown(report: dict[str, Any]) -> str:
    external = report["calibration"]["external_validation"]
    raw = report["calibration"]["external_audited_raw_validation"]
    return f"""# 与参考图的差异说明

卡塔尔未参与任何参数拟合。稳健视觉校准器在 {external['rows']} 个真实单元上的
MAE 为 {external['mae_pp']:.3f} pp，P90 为
{external['p90_abs_error_pp']:.3f} pp；纯物理 2 秒 crossing 模型 MAE 为
{raw['mae_pp']:.3f} pp。校准器的主要收益是降低 RMSE 和错误交通圈判定，而不是
把每个单元强行贴图。

剩余差异主要来自：

1. 公开图只有颜色代理而非原始数值，读图本身约有 1–2 pp 上限误差；
2. 冻结遥测的赛道距离由速度积分得到，起步、低速、进出站和丢包会积累相位误差；
3. 圆周最近车辆与原作者内部 timing feed 的前车定义可能不同；
4. 首圈和退赛末圈只有部分可重建区间，极端单元会显著抬高最大误差；
5. 原作者功能公开声明仍在测试，参考图本身不能被当作无误差真值。
"""


def _update_frontend_index(
    run_id: str,
    manifest_path: str,
    report_sha256: str,
    status: str,
) -> None:
    FRONTEND_BASE.mkdir(parents=True, exist_ok=True)
    if FRONTEND_INDEX.exists():
        index = json.loads(FRONTEND_INDEX.read_text(encoding="utf-8"))
    else:
        index = {
            "schema_version": "reference-analysis-lab-index-v2",
            "targets": [],
        }
    if any(row.get("run_id") == run_id for row in index["targets"]):
        raise FileExistsError(f"v2 前端索引已包含 run：{run_id}")
    index["targets"].append(
        {
            "target_id": "f1pace-reverse-engineered-v2",
            "label": "F1pace · traffic reverse-engineered v2",
            "status": status,
            "run_id": run_id,
            "manifest": manifest_path,
            "report_sha256": report_sha256,
            "page": "f1pace-reverse-engineered-v2.html",
        }
    )
    index["latest_run_id"] = run_id
    FRONTEND_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(run_id: str) -> dict[str, Any]:
    record_dir = RECORDS_BASE / f"run={run_id}"
    frontend_dir = FRONTEND_BASE / f"run={run_id}"
    if record_dir.exists() or frontend_dir.exists():
        raise FileExistsError(f"run 已存在，拒绝覆盖：{run_id}")
    for path in (
        ABU_TELEMETRY,
        QATAR_TELEMETRY,
        ABU_REFERENCE,
        QATAR_REFERENCE,
        V17_MANIFEST,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    abu_points = pd.read_parquet(
        ABU_TELEMETRY, columns=list(TELEMETRY_COLUMNS)
    )
    abu_features = build_time_headway_features(abu_points)
    abu_reference = extract_reference_traffic_grid(
        ABU_REFERENCE,
        ABU_DRIVER_ORDER,
        58,
    )
    abu = merge_reference_and_features(abu_reference, abu_features)
    cross_validation = grouped_cross_validation(abu)
    model = fit_surrogate(abu)
    abu["visual_prediction"] = predict_surrogate(abu, model)
    abu_visual_metrics = validation_metrics(
        abu["reference_traffic_ratio"], abu["visual_prediction"]
    )
    abu_raw_metrics = validation_metrics(
        abu["reference_traffic_ratio"], abu["cross_2.0"]
    )

    qatar_points = pd.read_parquet(
        QATAR_TELEMETRY, columns=list(TELEMETRY_COLUMNS)
    )
    qatar_features = build_time_headway_features(qatar_points)
    qatar_reference = extract_reference_traffic_grid(
        QATAR_REFERENCE,
        QATAR_DRIVER_ORDER,
        57,
    )
    qatar = merge_reference_and_features(qatar_reference, qatar_features)
    qatar["visual_prediction"] = predict_surrogate(qatar, model)
    external_metrics = validation_metrics(
        qatar["reference_traffic_ratio"], qatar["visual_prediction"]
    )
    external_raw_metrics = validation_metrics(
        qatar["reference_traffic_ratio"], qatar["cross_2.0"]
    )

    external_pass = (
        external_metrics["mae_pp"] <= 5.0
        and external_metrics["p90_abs_error_pp"] <= 15.0
        and external_metrics["traffic_lap_accuracy"] >= 0.95
    )
    status = (
        "METHOD_EQUIVALENT_EXTERNALLY_VALIDATED"
        if external_pass
        else "METHOD_EQUIVALENT_ONLY"
    )

    created_at = datetime.now(timezone.utc).isoformat()
    report = {
        "schema_version": "reference-analysis-lab-f1pace-v2",
        "run_id": run_id,
        "created_at": created_at,
        "target": "F1pace Laps in traffic",
        "status": status,
        "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
        "method_identity": (
            "interpretable_physical_reconstruction_plus_robust_visual_calibration"
        ),
        "source_identity": {
            "abu_dhabi_calibration": _telemetry_identity(ABU_TELEMETRY),
            "qatar_external_validation": _telemetry_identity(QATAR_TELEMETRY),
            "v17_manifest": {
                "path": str(V17_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_file(V17_MANIFEST),
                "run_id": "20260726T124625+0800-ce93134a40b8",
            },
        },
        "reference_identity": {
            "abu_dhabi": _reference_identity(
                ABU_REFERENCE,
                page_url=ABU_PAGE_URL,
                image_url=ABU_REFERENCE_URL,
                role="calibration_only",
            ),
            "qatar": _reference_identity(
                QATAR_REFERENCE,
                page_url=QATAR_PAGE_URL,
                image_url=QATAR_REFERENCE_URL,
                role="zero_refit_external_validation",
            ),
            "attribution": "F1pace by F1bythenumbers",
            "license_boundary": (
                "CC BY-NC-ND boundary retained; original images are not product assets"
            ),
        },
        "calibration": {
            "target_role": "reference_chart_image_color_derived_proxy",
            "color_extraction": {
                "calibration_labels": 22,
                "mae_pp": REFERENCE_COLOR_CALIBRATION_MAE_PP,
                "max_error_pp": REFERENCE_COLOR_CALIBRATION_MAX_ERROR_PP,
            },
            "feature_columns": list(SURROGATE_FEATURE_COLUMNS),
            "model": model,
            "grouped_cross_validation": cross_validation,
            "abu_dhabi_apparent_validation": abu_visual_metrics,
            "abu_dhabi_audited_raw_validation": abu_raw_metrics,
            "external_event": "2025 Qatar Grand Prix Race",
            "external_zero_refit": True,
            "external_validation": external_metrics,
            "external_audited_raw_validation": external_raw_metrics,
            "release_gate": {
                "mae_pp_lte": 5.0,
                "p90_abs_error_pp_lte": 15.0,
                "traffic_lap_accuracy_gte": 0.95,
                "passed": external_pass,
            },
        },
        "visual_replication": {
            "status": status,
            "definition": (
                "Huber-calibrated interpretable traffic ratios; "
                "trained only on Abu Dhabi reference colors"
            ),
            "abu_dhabi_driver_summary": _driver_summary(
                abu, "visual_prediction"
            ),
            "abu_dhabi_cells": _cell_records(abu, "visual_prediction"),
            "qatar_external_driver_summary": _driver_summary(
                qatar, "visual_prediction"
            ),
            "qatar_external_cells": _cell_records(qatar, "visual_prediction"),
        },
        "audited_analysis": {
            "status": "audit_only",
            "definition": (
                "nearest physical car same-track-location crossing headway <=2s; "
                "point-time weighted, missing intervals retained in denominator"
            ),
            "reference_image_used_in_prediction": False,
            "direct_causal_ranking_allowed": False,
            "abu_dhabi_driver_summary": _driver_summary(abu, "cross_2.0"),
            "qatar_driver_summary": _driver_summary(qatar, "cross_2.0"),
        },
        "exclusion_ledger": {
            "abu_dhabi": {
                "telemetry_points": int(len(abu_points)),
                "reference_cells": int(len(abu_reference)),
                "telemetry_feature_cells": int(len(abu_features)),
                "matched_cells": int(len(abu)),
                "unmatched_reference_cells": int(len(abu_reference) - len(abu)),
            },
            "qatar": {
                "telemetry_points": int(len(qatar_points)),
                "reference_cells": int(len(qatar_reference)),
                "telemetry_feature_cells": int(len(qatar_features)),
                "matched_cells": int(len(qatar)),
                "unmatched_reference_cells": int(len(qatar_reference) - len(qatar)),
            },
            "synthetic_points": 0,
        },
        "method_boundaries": [
            "原作者精确代码与异常圈规则未公开，不能声称一比一源码复现",
            "公开图颜色反解只是带误差的验收代理，不是原始数值导出",
            "赛道相位来自冻结遥测积分，首圈、进出站、退赛末圈可能出现尾部误差",
            "交通比例是描述性观测，不识别燃油、轮胎、车辆或车手因果效果",
        ],
        "difference_causes": [
            "reference_image_color_readout_error",
            "telemetry_distance_integration_and_sampling_error",
            "unknown_creator_ahead_car_and_exception_rules",
            "first_lap_pit_and_retirement_partial_coverage",
            "public_feature_itself_marked_as_testing",
        ],
        "validation": {
            "status": "PASS" if external_pass else "FAIL",
            "real_source_only": True,
            "synthetic_points": 0,
            "external_event_zero_refit": True,
            "external_event_cells": int(len(qatar)),
            "visual_external_metrics": external_metrics,
            "audited_external_metrics": external_raw_metrics,
            "method_card_present": True,
            "data_gap_audit_present": True,
            "numeric_tables_present": True,
            "automated_tests_required": True,
            "desktop_screenshot": "PENDING_BROWSER_ACCEPTANCE",
            "mobile_390px_screenshot": "PENDING_BROWSER_ACCEPTANCE",
        },
    }

    record_dir.mkdir(parents=True, exist_ok=False)
    report_path = record_dir / "f1pace_traffic_reverse_engineered_v2.json"
    report_hash = write_json(report_path, report)
    method_hash = write_text(
        record_dir / "method_card.md", _method_card_markdown(report)
    )
    gap_hash = write_text(
        record_dir / "data_gap_audit.md", _data_gap_markdown()
    )
    difference_hash = write_text(
        record_dir / "reference_difference.md",
        _difference_markdown(report),
    )
    abu_table = record_dir / "abu_dhabi_cells.csv"
    qatar_table = record_dir / "qatar_external_cells.csv"
    abu.to_csv(abu_table, index=False, encoding="utf-8")
    qatar.to_csv(qatar_table, index=False, encoding="utf-8")

    attempts = [
        {
            "attempt": 1,
            "issue": "整数阈值列名被格式化为 cur_2 而非冻结身份 cur_2.0",
            "hypothesis": "通用浮点格式删除了语义身份中的 .0",
            "action": "引入稳定阈值标签函数并添加单元测试",
            "output": "RESOLVED",
            "next_step": "复跑事件外验证",
        },
        {
            "attempt": 2,
            "issue": "首圈有限 headway 样本被重新归一化，领跑车交通比例虚高",
            "hypothesis": "缺失区间被错误移出整圈时间分母",
            "action": "分母固定为全部可观测逐点时间，missing 单独披露",
            "output": (
                f"RESOLVED; Qatar external MAE={external_metrics['mae_pp']:.3f}pp"
            ),
            "next_step": "冻结 v2 并进行浏览器验收",
        },
    ]
    attempts_path = record_dir / "attempts.jsonl"
    attempts_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in attempts
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": "reference-analysis-lab-manifest-v2",
        "run_id": run_id,
        "target_id": "f1pace-reverse-engineered-v2",
        "status": status,
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
        "numeric_tables": [
            {
                "path": abu_table.name,
                "sha256": sha256_file(abu_table),
                "rows": int(len(abu)),
                "role": "calibration_event",
            },
            {
                "path": qatar_table.name,
                "sha256": sha256_file(qatar_table),
                "rows": int(len(qatar)),
                "role": "zero_refit_external_event",
            },
        ],
        "attempts": {
            "path": attempts_path.name,
            "sha256": sha256_file(attempts_path),
            "rows": len(attempts),
        },
        "source_hashes": {
            "abu_telemetry": sha256_file(ABU_TELEMETRY),
            "qatar_telemetry": sha256_file(QATAR_TELEMETRY),
            "abu_reference": sha256_file(ABU_REFERENCE),
            "qatar_reference": sha256_file(QATAR_REFERENCE),
            "v17_manifest": sha256_file(V17_MANIFEST),
        },
    }
    manifest_path = record_dir / "manifest.json"
    manifest_hash = write_json(manifest_path, manifest)

    frontend_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(record_dir, frontend_dir)
    frontend_manifest_path = (
        f"data/reference-analysis-lab/v2/run={run_id}/manifest.json"
    )
    _update_frontend_index(
        run_id,
        frontend_manifest_path,
        report_hash,
        status,
    )
    return {
        "run_id": run_id,
        "status": status,
        "record_dir": str(record_dir),
        "frontend_dir": str(frontend_dir),
        "manifest_sha256": manifest_hash,
        "external_validation": external_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    print(json.dumps(run(args.run_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
