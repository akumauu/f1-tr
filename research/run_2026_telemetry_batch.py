#!/usr/bin/env python3
"""离线运行并汇总 2026 Ferrari 四站遥测解释报告。"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

import run_experiments as base
import run_telemetry_explanation as runner


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT
    / "research"
    / "configs"
    / "telemetry_explanation_2026_ferrari_four_event_v7.json"
)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def build_event_config(
    base_config: Mapping[str, Any],
    batch_config: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """从冻结澳大利亚配置复制单站配置，仅覆盖批处理合同允许的字段。"""

    config = copy.deepcopy(dict(base_config))
    scope_contract = batch_config["scope_contract"]
    event_id = str(event["event_id"])
    config["experiment_name"] = f"telemetry_explanation_2026_{event_id}_ferrari_v7"
    config["validation_status"] = str(batch_config["validation_status"])
    config["offline_only"] = True
    config["scope"].update(
        {
            "year": int(scope_contract["year"]),
            "meeting": str(event["meeting"]),
            "meeting_partition": str(event["meeting_partition"]),
            "session": str(scope_contract["session"]),
            "team": str(scope_contract["team"]),
            "target_driver": str(scope_contract["target_driver"]),
            "reference_driver": str(scope_contract["reference_driver"]),
        }
    )
    config["telemetry"]["raw_corners"] = str(event["raw_corners"])
    config["outputs"] = {
        "result": str(event["result"]),
        "summary": str(event["summary"]),
    }
    return config


def _strategy_main_modes(
    report: Mapping[str, Any], drivers: Sequence[str]
) -> dict[str, str | None]:
    rows = report.get("strategy_mode_fingerprints", [])
    by_driver: dict[str, str | None] = {str(driver): None for driver in drivers}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        driver = str(row.get("driver_acronym") or "")
        if driver not in by_driver or row.get("status") != "ok":
            continue
        label = row.get("mode_label")
        by_driver[driver] = str(label) if label is not None else None
    return by_driver


def _quality_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    """保留单站 runner 的可观测质量量，不另造综合质量分。"""

    quality = report.get("data_quality", {})
    return {
        "sample_rows": quality.get("sample_rows"),
        "driver_laps": quality.get("driver_laps"),
        "eligible_clean_laps": quality.get("eligible_clean_laps"),
        "team_clean_laps": quality.get("team_clean_laps"),
        "field_reference_laps": quality.get("field_reference_laps"),
        "segment_coverage": _optional_float(quality.get("segment_coverage")),
        "response_baseline_coverage": _optional_float(
            quality.get("response_baseline_coverage")
        ),
        "raw_state_preserved": quality.get("raw_state_preserved"),
        "raw_state_informative": quality.get("raw_state_informative"),
        "absolute_time_preserved": quality.get("absolute_time_preserved"),
        "limitations": list(quality.get("limitations", [])),
    }


def _event_snapshot(
    report: Mapping[str, Any], drivers: Sequence[str]
) -> dict[str, Any]:
    comparison = report.get("team_difference", {}).get("comparability", {})
    anomaly_rows = report.get("team_difference", {}).get("anomaly_episodes", [])
    ledger = report.get("race_value_ledger", {})
    return {
        "meeting": report.get("scope", {}).get("meeting"),
        "session": report.get("scope", {}).get("session"),
        "team": report.get("scope", {}).get("team"),
        "orientation": report.get("team_difference", {}).get("orientation"),
        "release_status": report.get("release_status"),
        "matched_pairs": int(comparison.get("matched_pairs") or 0),
        "comparability": comparison.get("status"),
        "median_physical_gap_s_per_comparable_lap": _optional_float(
            ledger.get("target_minus_reference_median_s_per_comparable_lap")
        ),
        "interval_80_s_per_comparable_lap": [
            _optional_float(value)
            for value in ledger.get("interval_80_s_per_comparable_lap", [None, None])
        ],
        "anomaly_trigger_count": len(anomaly_rows)
        if isinstance(anomaly_rows, list)
        else 0,
        "strategy_main_modes": _strategy_main_modes(report, drivers),
        "data_schema": report.get("data_identity", {}).get("schema"),
        "data_quality": _quality_snapshot(report),
        # 发布级评分在四场批处理中必须保持空值，不能从单站赛果反推。
        "ability_ovr": None,
        "event_delivery": None,
        "delta_expected_points": None,
    }


def aggregate_reports(
    reports: Sequence[Mapping[str, Any]],
    batch_config: Mapping[str, Any],
) -> dict[str, Any]:
    """将单站物理证据压缩为探索性四站快照，不生成能力或积分结论。"""

    contract = batch_config["scope_contract"]
    drivers = [str(contract["target_driver"]), str(contract["reference_driver"])]
    events = [_event_snapshot(report, drivers) for report in reports]
    threshold = int(batch_config["confirmatory_event_threshold"])
    observed = len(events)
    qualified_events = [
        event for event in events if event["comparability"] == "comparable"
    ]
    resolved_events = [
        event
        for event in qualified_events
        if event["median_physical_gap_s_per_comparable_lap"] is not None
        and len(event["interval_80_s_per_comparable_lap"]) == 2
        and all(value is not None for value in event["interval_80_s_per_comparable_lap"])
        and (
            event["interval_80_s_per_comparable_lap"][0] > 0
            or event["interval_80_s_per_comparable_lap"][1] < 0
        )
    ]
    physical_gaps = [
        event["median_physical_gap_s_per_comparable_lap"]
        for event in resolved_events
        if event["median_physical_gap_s_per_comparable_lap"] is not None
    ]
    publication_contract = dict(batch_config["publication_contract"])
    if not (
        publication_contract.get("exploratory_only") is True
        and publication_contract.get("result_labels_used") is False
        and publication_contract.get("publish_ability_ovr") is False
        and publication_contract.get("publish_event_delivery") is False
        and publication_contract.get("publish_expected_points") is False
    ):
        raise ValueError("批处理发布合同必须禁止四站 OVR、Event Delivery 与期望积分")
    return {
        "report_id": str(batch_config["experiment_name"]),
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "methodological_status": str(batch_config["validation_status"]),
        "release_status": "exploratory_four_events_below_confirmatory_threshold",
        "config_hash": base.canonical_hash(batch_config),
        "scope": {
            **dict(contract),
            "configured_events": int(len(batch_config["events"])),
            "observed_events": observed,
        },
        "confirmatory_exposure_gate": {
            "observed_events": observed,
            "quality_qualified_events": len(qualified_events),
            "required_events": threshold,
            "passed": len(qualified_events) >= threshold,
            "status": (
                "passed" if len(qualified_events) >= threshold else "exploratory_only"
            ),
        },
        "events": events,
        "physical_gap_summary": {
            "available_event_point_estimates": sum(
                event["median_physical_gap_s_per_comparable_lap"] is not None
                for event in events
            ),
            "quality_qualified_events": len(qualified_events),
            "directionally_resolved_events": len(resolved_events),
            "median_of_event_medians_s_per_comparable_lap": (
                float(statistics.median(physical_gaps)) if physical_gaps else None
            ),
            "interpretation": (
                "只对严格可比且单站 80% 区间不跨零的分站汇总；没有满足条件的分站时保持 null。"
            ),
        },
        "season_rating_outputs": {
            "ability_ovr": None,
            "event_delivery": None,
            "delta_expected_points": None,
            "reason": str(publication_contract["reason"]),
        },
        "publication_contract": publication_contract,
        "result_label_audit": {
            "result_labels_used": False,
            "classification_or_points_fields_consumed": [],
            "note": "汇总仅读取遥测解释报告中的可比性、物理时间差、异常、策略代理和质量字段。",
        },
    }


def _format_number(value: Any, digits: int = 3) -> str:
    number = _optional_float(value)
    return "—" if number is None else f"{number:.{digits}f}"


def batch_summary(report: Mapping[str, Any]) -> str:
    gate = report["confirmatory_exposure_gate"]
    contract = report["scope"]
    lines = [
        "# 2026 Ferrari 四站遥测解释汇总",
        "",
        (
            f"- 范围：`{contract['target_driver']} - {contract['reference_driver']}`，"
            f"`{contract['team']}`，`{contract['session']}`。"
        ),
        (
            f"- 事件暴露：{gate['observed_events']}/{gate['required_events']}；"
            f"严格质量合格：{gate['quality_qualified_events']}/{gate['required_events']}；"
            f"状态：`{gate['status']}`。"
        ),
        "- 本汇总只保留匹配遥测中的物理证据，不使用完赛名次、积分或年度排名标签。",
        "- `OVR`、`Event Delivery` 与期望积分均为空；四场结果只可作为 2026 独立 era 的探索快照。",
        "",
        "| 分站 | 可比圈 | 可比性 | 中位物理圈差 s | 异常 | HAM 模式 | LEC 模式 | schema | 赛段覆盖 | 响应基线覆盖 |",
        "| --- | ---: | --- | ---: | ---: | --- | --- | --- | ---: | ---: |",
    ]
    for event in report["events"]:
        modes = event["strategy_main_modes"]
        quality = event["data_quality"]
        lines.append(
            f"| {event['meeting']} | {event['matched_pairs']} | "
            f"{event['comparability'] or '—'} | "
            f"{_format_number(event['median_physical_gap_s_per_comparable_lap'])} | "
            f"{event['anomaly_trigger_count']} | "
            f"{modes.get('HAM') or '—'} | {modes.get('LEC') or '—'} | "
            f"{event['data_schema'] or '—'} | "
            f"{_format_number(quality.get('segment_coverage'))} | "
            f"{_format_number(quality.get('response_baseline_coverage'))} |"
        )
    lines.extend(
        [
            "",
            "## 发布边界",
            "",
            "- 正物理圈差只表示该报告方向下目标车手在匹配圈更慢，不自动归因给车辆、车手或策略。",
            "- 策略标签是中性操作模式代理，不能声称真实 SOC、部署功率、主动空力或 Overtake 状态。",
            "- 单站异常触发只表示多基线与多特征族共同支持可观测响应变化，不能命名具体机械故障。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_event(config: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    output = ROOT / str(config["outputs"]["result"])
    summary = ROOT / str(config["outputs"]["summary"])
    base.atomic_json(output, report)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(runner._report_summary(report), encoding="utf-8")


def _write_aggregate(
    batch_config: Mapping[str, Any], aggregate: Mapping[str, Any]
) -> None:
    outputs = batch_config["outputs"]
    output = ROOT / str(outputs["result"])
    summary = ROOT / str(outputs["summary"])
    base.atomic_json(output, aggregate)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(batch_summary(aggregate), encoding="utf-8")


def run_event(
    batch_config: Mapping[str, Any], event_id: str, *, write: bool = True
) -> dict[str, Any]:
    """单独运行一站，降低长批处理失败时的重算范围。"""

    event = next(
        (
            row
            for row in batch_config["events"]
            if str(row["event_id"]) == str(event_id)
        ),
        None,
    )
    if event is None:
        raise ValueError(f"批处理配置不存在 event_id={event_id}")
    base_config = base.load_json(ROOT / str(batch_config["base_config"]))
    event_config = build_event_config(base_config, batch_config, event)
    report = runner.run(event_config)
    if write:
        _write_event(event_config, report)
    return report


def aggregate_existing(
    batch_config: Mapping[str, Any], *, write: bool = True
) -> dict[str, Any]:
    """只读取配置声明的四份单站产物并重建汇总。"""

    reports: list[dict[str, Any]] = []
    for event in batch_config["events"]:
        path = ROOT / str(event["result"])
        if not path.is_file():
            raise FileNotFoundError(f"缺少单站报告：{path}")
        reports.append(base.load_json(path))
    aggregate = aggregate_reports(reports, batch_config)
    if write:
        _write_aggregate(batch_config, aggregate)
    return aggregate


def run(batch_config: Mapping[str, Any], *, write: bool = True) -> dict[str, Any]:
    base_config = base.load_json(ROOT / str(batch_config["base_config"]))
    reports: list[dict[str, Any]] = []
    for event in batch_config["events"]:
        event_config = build_event_config(base_config, batch_config, event)
        report = runner.run(event_config)
        if write:
            _write_event(event_config, report)
        reports.append(report)
    aggregate = aggregate_reports(reports, batch_config)
    if write:
        _write_aggregate(batch_config, aggregate)
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--event-id", help="只运行指定 event_id，不改写四站汇总")
    parser.add_argument(
        "--aggregate-existing",
        action="store_true",
        help="不重算遥测，只从已存在的四份单站报告重建汇总",
    )
    args = parser.parse_args()
    batch_config = base.load_json(args.config)
    if args.event_id and args.aggregate_existing:
        parser.error("--event-id 与 --aggregate-existing 不能同时使用")
    if args.event_id:
        report = run_event(
            batch_config, args.event_id, write=not args.no_write
        )
    elif args.aggregate_existing:
        report = aggregate_existing(batch_config, write=not args.no_write)
    else:
        report = run(batch_config, write=not args.no_write)
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
