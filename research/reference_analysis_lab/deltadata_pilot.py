"""DeltaData 参考图表的方法等价复刻 pilot。

本模块只读取已经冻结的 v17 `stint_curve_evidence.json`，不生成合成圈时，
也不把真实燃油、物理轮胎磨损或创作者未公开的选圈算法当作可识别字段。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from typing import Any, Iterable, Mapping


SCENARIOS = ("low", "base", "high")
TRAFFIC_STATES = ("clean_air", "intermediate_gap", "dirty_air", "unknown_gap")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def kish_effective_sample_size(weights: Iterable[float]) -> float:
    """按 分析权重计算 Kish ESS。"""

    values = [max(_finite(weight) or 0.0, 0.0) for weight in weights]
    total = sum(values)
    denominator = sum(value * value for value in values)
    if denominator <= 0.0:
        return 0.0
    return total * total / denominator


def weighted_mean(values: Iterable[float], weights: Iterable[float]) -> float | None:
    """计算非负软权重均值；没有有效权重时 fail-close。"""

    pairs = []
    for value, weight in zip(values, weights):
        numeric_value = _finite(value)
        numeric_weight = max(_finite(weight) or 0.0, 0.0)
        if numeric_value is not None and numeric_weight > 0.0:
            pairs.append((numeric_value, numeric_weight))
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0.0:
        return None
    return sum(value * weight for value, weight in pairs) / total_weight


def _weighted_distribution(
    points: Iterable[Mapping[str, Any]],
    *,
    weight_key: str = "analysis_weight",
) -> dict[str, float]:
    totals = {state: 0.0 for state in TRAFFIC_STATES}
    for point in points:
        state = str(point.get("traffic_state") or "unknown_gap")
        if state not in totals:
            state = "unknown_gap"
        totals[state] += max(_finite(point.get(weight_key)) or 0.0, 0.0)
    total = sum(totals.values())
    return {state: (value / total if total > 0.0 else 0.0) for state, value in totals.items()}


def _raw_distribution(points: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    counts = Counter()
    for point in points:
        state = str(point.get("traffic_state") or "unknown_gap")
        counts[state if state in TRAFFIC_STATES else "unknown_gap"] += 1
    total = sum(counts.values())
    return {state: (counts[state] / total if total else 0.0) for state in TRAFFIC_STATES}


def _fuel_summary(stint: Mapping[str, Any]) -> dict[str, dict[str, float | str | None]]:
    scenarios = (stint.get("primary_fit") or {}).get("fuel_scenarios") or {}
    output: dict[str, dict[str, float | str | None]] = {}
    for scenario in SCENARIOS:
        entry = scenarios.get(scenario) or {}
        output[scenario] = {
            "status": str(entry.get("status") or "NOT_AVAILABLE"),
            "reference_pace_s": _finite(entry.get("reference_pace_s")),
            "slope_s_per_tyre_lap": _finite(entry.get("slope_s_per_tyre_lap")),
        }
    return output


def _stint_summary(stint: Mapping[str, Any]) -> dict[str, Any]:
    points = list(stint.get("points") or [])
    fit_points = [
        point
        for point in points
        if point.get("used_for_primary_fit")
        and _finite(point.get("adjusted_pace_s")) is not None
        and max(_finite(point.get("analysis_weight")) or 0.0, 0.0) > 0.0
    ]
    quality_fit_points = [point for point in fit_points if point.get("quality_ok")]
    clean_air_points = [
        point for point in quality_fit_points if point.get("traffic_state") == "clean_air"
    ]
    clean_air_weights = [point.get("analysis_weight") for point in clean_air_points]
    fit_weights = [point.get("analysis_weight") for point in quality_fit_points]
    sample_audit = dict(stint.get("sample_audit") or {})
    primary_fit = dict(stint.get("primary_fit") or {})
    condition_profile = dict(stint.get("condition_profile") or {})
    disposition_counts = Counter(
        str(point.get("analysis_disposition") or "UNKNOWN") for point in points
    )
    fuel_scenarios = _fuel_summary(stint)
    slopes = [
        fuel_scenarios[scenario].get("slope_s_per_tyre_lap")
        for scenario in SCENARIOS
    ]
    slope_values = [value for value in slopes if value is not None]
    fuel_reference_values = [
        fuel_scenarios[scenario].get("reference_pace_s") for scenario in SCENARIOS
    ]
    fuel_reference_values = [value for value in fuel_reference_values if value is not None]
    return {
        "stint_key": stint.get("stint_key"),
        "year": stint.get("year"),
        "meeting_name": stint.get("meeting_name"),
        "driver": stint.get("driver"),
        "team": stint.get("team"),
        "stint_number": stint.get("stint_number"),
        "compound": stint.get("compound"),
        "lap_range": stint.get("lap_range"),
        "reference_tyre_age_laps": _finite(primary_fit.get("reference_tyre_age_laps")),
        "sample_size": {
            "observed_points": len(points),
            "fit_points": len(fit_points),
            "quality_fit_points": len(quality_fit_points),
            "clean_air_points": len(clean_air_points),
            "clean_air_kish_ess": kish_effective_sample_size(clean_air_weights),
            "fit_kish_ess": kish_effective_sample_size(fit_weights),
            "sidecar_observed_laps": sample_audit.get("observed_laps"),
            "sidecar_fit_laps": sample_audit.get("fit_laps"),
            "sidecar_kish_effective_laps": _finite(sample_audit.get("kish_effective_laps")),
        },
        "clean_air": {
            "pace_proxy_s": weighted_mean(
                [point.get("adjusted_pace_s") for point in clean_air_points],
                clean_air_weights,
            ),
            "pace_role": "fuel_adjusted_conditioned_clean_air_proxy_not_raw_creator_metric",
            "weighted_share_of_quality_fit": (
                sum(max(_finite(weight) or 0.0, 0.0) for weight in clean_air_weights)
                / sum(max(_finite(weight) or 0.0, 0.0) for weight in fit_weights)
                if sum(max(_finite(weight) or 0.0, 0.0) for weight in fit_weights) > 0.0
                else 0.0
            ),
            "raw_traffic_distribution": _raw_distribution(quality_fit_points),
            "weighted_traffic_distribution": _weighted_distribution(quality_fit_points),
        },
        "base_reference_pace_s": _finite(primary_fit.get("reference_pace_s")),
        "fuel_scenarios": fuel_scenarios,
        "tyre_degradation": {
            "low_s_per_tyre_lap": slopes[0] if slopes else None,
            "base_s_per_tyre_lap": slopes[1] if len(slopes) > 1 else None,
            "high_s_per_tyre_lap": slopes[2] if len(slopes) > 2 else None,
            "range_s_per_tyre_lap": [min(slope_values), max(slope_values)]
            if slope_values
            else None,
            "role": "observed_tyre_age_slope_conditional_proxy_not_physical_wear",
        },
        "condition_profile": {
            "context_coverage": condition_profile.get("context_coverage"),
            "lap_fraction_median": condition_profile.get("lap_fraction_median"),
            "traffic_state_distribution": condition_profile.get("traffic_state_distribution"),
            "non_green_fraction": condition_profile.get("non_green_fraction"),
            "pit_boundary_fraction": condition_profile.get("pit_boundary_fraction"),
            "low_quality_fraction": condition_profile.get("low_quality_fraction"),
            "nuisance_adjustment_median_s": condition_profile.get("nuisance_adjustment_median_s"),
        },
        "analysis_disposition_counts": dict(sorted(disposition_counts.items())),
    }


def _aggregate_records(records: list[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key))].append(record)
    output = []
    for value, group in grouped.items():
        weights = [
            max(
                _finite(record.get("sample_size", {}).get("sidecar_kish_effective_laps"))
                or _finite(record.get("sample_size", {}).get("fit_kish_ess"))
                or 0.0,
                0.0,
            )
            for record in group
        ]
        clean_weights = [
            max(_finite(record.get("sample_size", {}).get("clean_air_kish_ess")) or 0.0, 0.0)
            for record in group
        ]
        scenario_values = {}
        for scenario in SCENARIOS:
            scenario_values[scenario] = {
                "reference_pace_s": weighted_mean(
                    [record.get("fuel_scenarios", {}).get(scenario, {}).get("reference_pace_s") for record in group],
                    weights,
                ),
                "slope_s_per_tyre_lap": weighted_mean(
                    [record.get("fuel_scenarios", {}).get(scenario, {}).get("slope_s_per_tyre_lap") for record in group],
                    weights,
                ),
            }
        fit_points = sum(int(record.get("sample_size", {}).get("quality_fit_points") or 0) for record in group)
        clean_points = sum(int(record.get("sample_size", {}).get("clean_air_points") or 0) for record in group)
        total_fit_weight = sum(weights)
        clean_share = (
            sum(
                max(_finite(record.get("sample_size", {}).get("fit_kish_ess")) or 0.0, 0.0)
                * max(_finite(record.get("clean_air", {}).get("weighted_share_of_quality_fit")) or 0.0, 0.0)
                for record in group
            )
            / total_fit_weight
            if total_fit_weight > 0.0
            else 0.0
        )
        entry = {
            key: value,
            "team": group[0].get("team"),
            "stints": len(group),
            "compounds": sorted({str(record.get("compound")) for record in group}),
            "observed_points": sum(int(record.get("sample_size", {}).get("observed_points") or 0) for record in group),
            "quality_fit_points": fit_points,
            "clean_air_points": clean_points,
            "clean_air_kish_ess": sum(clean_weights),
            "clean_air_weighted_share": clean_share,
            "base_reference_pace_s": scenario_values["base"]["reference_pace_s"],
            "fuel_scenarios": scenario_values,
            "tyre_degradation": {
                "low_s_per_tyre_lap": scenario_values["low"]["slope_s_per_tyre_lap"],
                "base_s_per_tyre_lap": scenario_values["base"]["slope_s_per_tyre_lap"],
                "high_s_per_tyre_lap": scenario_values["high"]["slope_s_per_tyre_lap"],
            },
            "rank_role": "descriptive_conditional_proxy_sorted_not_causal_order",
        }
        output.append(entry)
    return sorted(
        output,
        key=lambda row: row.get("base_reference_pace_s")
        if row.get("base_reference_pace_s") is not None
        else float("inf"),
    )


def _compact_pair(pair: Mapping[str, Any]) -> dict[str, Any]:
    left_anchor = dict(pair.get("left_anchor") or {})
    right_anchor = dict(pair.get("right_anchor") or {})
    left_fuel = dict(left_anchor.get("fuel_sensitivity_s") or {})
    right_fuel = dict(right_anchor.get("fuel_sensitivity_s") or {})
    fuel_delta = {
        scenario: (
            _finite(left_fuel.get(scenario)) - _finite(right_fuel.get(scenario))
            if _finite(left_fuel.get(scenario)) is not None
            and _finite(right_fuel.get(scenario)) is not None
            else None
        )
        for scenario in SCENARIOS
    }
    common_audit = dict(pair.get("common_support_sample_audit") or {})
    left_audit = dict(common_audit.get("left") or {})
    right_audit = dict(common_audit.get("right") or {})
    balance = dict(pair.get("observable_condition_balance") or {})
    return {
        "pair_key": pair.get("pair_key"),
        "left_stint_key": pair.get("left_stint_key"),
        "right_stint_key": pair.get("right_stint_key"),
        "compound": pair.get("compound"),
        "left_driver": str(pair.get("left_stint_key") or "").split("|")[3],
        "right_driver": str(pair.get("right_stint_key") or "").split("|")[3],
        "status": pair.get("status"),
        "gate_failures": list(pair.get("gate_failures") or []),
        "common_tyre_age_support_laps": pair.get("common_tyre_age_support_laps"),
        "support_overlap_ratio": _finite(pair.get("support_overlap_ratio")),
        "reference_tyre_age_gap_laps": _finite(pair.get("reference_tyre_age_gap_laps")),
        "kish_ess": {
            "left": _finite(left_audit.get("kish_effective_laps")),
            "right": _finite(right_audit.get("kish_effective_laps")),
        },
        "traffic_tvd": _finite(
            (balance.get("components") or {}).get("traffic_tvd", {}).get("absolute_difference")
        ),
        "balance_status": balance.get("status"),
        "maximum_normalized_distance": _finite(balance.get("maximum_normalized_distance")),
        "direct_comparison_left_minus_right_pace_s": _finite(
            pair.get("direct_comparison_left_minus_right_pace_s")
        ),
        "audit_only_conditional_model_left_minus_right_pace_s": _finite(
            pair.get("audit_only_conditional_model_left_minus_right_pace_s")
        ),
        "fuel_sensitivity_delta_s": fuel_delta,
        "fuel_sensitivity_delta_range_s": [
            min(value for value in fuel_delta.values() if value is not None),
            max(value for value in fuel_delta.values() if value is not None),
        ]
        if any(value is not None for value in fuel_delta.values())
        else None,
        "delta_role": pair.get("delta_role"),
    }


def build_deltadata_pilot(
    curve: Mapping[str, Any],
    *,
    source_identity: Mapping[str, Any],
    reference_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """从真实 v17 sidecar 构造 DeltaData 方法等价复刻报告。"""

    stints = [_stint_summary(stint) for stint in curve.get("stints") or []]
    drivers = _aggregate_records(stints, "driver")
    teams = _aggregate_records(stints, "team")
    pairs = [_compact_pair(pair) for pair in curve.get("pairwise_comparisons") or []]
    status_counts = Counter(str(pair.get("status") or "UNKNOWN") for pair in pairs)
    balance_warning_pairs = sum(
        pair.get("status") == "audit_only_balance_warning" for pair in pairs
    )
    all_points = sum(int(stint["sample_size"]["observed_points"]) for stint in stints)
    fit_points = sum(int(stint["sample_size"]["quality_fit_points"]) for stint in stints)
    clean_points = sum(int(stint["sample_size"]["clean_air_points"]) for stint in stints)
    clean_ess = sum(float(stint["sample_size"]["clean_air_kish_ess"]) for stint in stints)
    disposition_counts = Counter()
    for stint in stints:
        disposition_counts.update(stint.get("analysis_disposition_counts") or {})
    validation = {
        "status": "PASS",
        "real_source_only": True,
        "synthetic_points": 0,
        "stints": len(stints),
        "points": all_points,
        "quality_fit_points": fit_points,
        "clean_air_points": clean_points,
        "clean_air_kish_ess_sum": round(clean_ess, 6),
        "all_stints_have_fuel_scenarios": all(
            set(stint.get("fuel_scenarios", {})) == set(SCENARIOS) for stint in stints
        ),
        "non_comparable_direct_deltas_null": all(
            pair["status"] == "comparable"
            or pair["direct_comparison_left_minus_right_pace_s"] is None
            for pair in pairs
        ),
        "method_equivalent_only": True,
        "one_to_one_status": "SKIPPED_OPAQUE_METHOD",
    }
    return {
        "schema_version": "reference-analysis-lab-deltadata-v1",
        "target_id": "deltadata",
        "status": "METHOD_EQUIVALENT_ONLY",
        "run_id": source_identity.get("run_id"),
        "source_identity": dict(source_identity),
        "reference_identity": dict(reference_identity),
        "method_card": {
            "creator": "DeltaData",
            "source_posts": [
                {
                    "url": "https://x.com/DeltaData_/status/2082785101931311292",
                    "name": "LAST 11 H2H MERCEDES CLEAR AIR RACE PACE",
                    "public_semantics": ["clear-air race pace", "head-to-head", "lower percent is faster"],
                },
                {
                    "url": "https://x.com/DeltaData_/status/2082828055677108520",
                    "name": "Representative race laps analysed by team",
                    "public_semantics": ["representative race laps", "team sample disclosure"],
                },
            ],
            "public_method": [
                "公开帖子展示 clear-air race pace、H2H 百分比和代表圈样本量语义。",
                "公开材料没有给出完整选圈、燃油修正、轮胎拟合或不确定性代码。",
            ],
            "visual_inference": [
                "排名图使用赛季/比赛标签、车手或车队对象和样本量披露。",
                "百分比/排序是描述性结果，不能从图片反推因果模型。",
            ],
            "equivalent_method": [
                "复用 v17 已冻结的 OOF/cross-fit reliability soft weight。",
                "使用 v17 low/base/high 具名燃油修正敏感性，而不是声称真实燃油量。",
                "按 Stint 记录清洁空气代理、交通分布、共同胎龄支持和 Kish ESS。",
            ],
            "not_identifiable": [
                "DeltaData 的精确代码、选圈阈值、燃油模型、胎衰模型和百分比归一化方式。",
                "真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令。",
                "不同配方、胎龄、比赛阶段和停站下的无条件车手/车队因果排名。",
            ],
            "reference_boundary": "参考图只用于语义/布局核对，未复制 logo、品牌字体或原图作为产品资产。",
        },
        "data_gap_audit": [
            {
                "field": "clean_air_lap_selection",
                "current_field": "stints[].points[].traffic_state",
                "status": "AVAILABLE_PROXY",
                "substitute": "v17 条件交通状态 + analysis_weight 的 clean_air 子集",
                "publication": "允许发布为 clean-air pace proxy，不称创作者一比一结果",
            },
            {
                "field": "fuel_load_and_correction",
                "current_field": "stints[].primary_fit.fuel_scenarios",
                "status": "AVAILABLE_PROXY",
                "substitute": "low/base/high named fuel-correction sensitivity",
                "publication": "允许发布情景敏感性，不称真实油量/统计区间",
            },
            {
                "field": "physical_tyre_degradation",
                "current_field": "primary_fit.slope_s_per_tyre_lap",
                "status": "AVAILABLE_PROXY",
                "substitute": "observed tyre-age slope under conditional model",
                "publication": "允许发布胎龄变化代理，不称物理磨损",
            },
            {
                "field": "sample_size_and_effective_sample",
                "current_field": "sample_audit + analysis_weight",
                "status": "AVAILABLE",
                "substitute": "observed/fit points and Kish ESS",
                "publication": "允许发布并随排名披露",
            },
            {
                "field": "creator_exact_selector_and_code",
                "current_field": "NOT_AVAILABLE",
                "status": "NOT_IDENTIFIABLE",
                "substitute": "method-equivalent v17 contract",
                "publication": "禁止声称一比一复现",
            },
            {
                "field": "causal_team_driver_rank",
                "current_field": "cross-compound/global ranking not identifiable",
                "status": "NOT_IDENTIFIABLE",
                "substitute": "descriptive conditional sort + audited pair gates",
                "publication": "只允许描述性顺序；audited_analysis 保持 audit_only",
            },
        ],
        "visual_replication": {
            "method_status": "METHOD_EQUIVALENT_ONLY",
            "clean_air_pace": {
                "stints": stints,
                "driver_ranking": drivers,
                "team_ranking": teams,
                "ranking_note": "按 v17 条件代理的参考胎龄/燃油修正描述性排序，不是全序因果排名。",
            },
            "fuel_sensitivity": {
                "scenarios": list(SCENARIOS),
                "driver_ranking_by_scenario": {
                    scenario: sorted(
                        drivers,
                        key=lambda row: row.get("fuel_scenarios", {})
                        .get(scenario, {})
                        .get("reference_pace_s")
                        if row.get("fuel_scenarios", {}).get(scenario, {}).get("reference_pace_s") is not None
                        else float("inf"),
                    )
                    for scenario in SCENARIOS
                },
                "note": "low/base/high 是具名燃油情景敏感性，不是置信区间或真实油量。",
            },
            "tyre_degradation": {
                "driver_ranking": drivers,
                "note": "slope 单位为秒/胎龄圈，仅表示条件模型观察代理。",
            },
            "sample_disclosure": {
                "stints": len(stints),
                "points": all_points,
                "quality_fit_points": fit_points,
                "clean_air_points": clean_points,
                "clean_air_kish_ess_sum": round(clean_ess, 6),
                "analysis_disposition_counts": dict(sorted(disposition_counts.items())),
            },
        },
        "audited_analysis": {
            "status": "audit_only",
            "reason": "v17 条件平衡、共同胎龄、交通分布和 ESS 门控阻止跨 Stint 全序；保留成对状态和敏感性。",
            "gates": {
                "same_event_session_compound": True,
                "reference_tyre_age_gap_laps": "<=2",
                "common_tyre_age_support_laps": ">=5",
                "kish_ess": ">=8 each side",
                "context_coverage": ">=0.80",
                "traffic_tvd": "<=0.35",
                "normalized_balance_distance": "<=0.65 comparable; 0.65-0.90 warning; >0.90 not_comparable",
            },
            "pairwise": pairs,
            "direct_comparable_pairs": [pair for pair in pairs if pair["status"] == "comparable"],
            "rank_status": {
                "forms_total_order": False,
                "directly_comparable_pairs": status_counts.get("comparable", 0),
                "balance_warning_pairs": balance_warning_pairs,
                "not_comparable_pairs": status_counts.get("not_comparable", 0),
            },
        },
        "exclusion_ledger": {
            "stints": len(stints),
            "observed_points": all_points,
            "quality_fit_points": fit_points,
            "clean_air_points": clean_points,
            "analysis_disposition_counts": dict(sorted(disposition_counts.items())),
            "v17_sidecar_coverage": dict(curve.get("coverage") or {}),
        },
        "validation": validation,
    }


__all__ = [
    "SCENARIOS",
    "build_deltadata_pilot",
    "kish_effective_sample_size",
    "weighted_mean",
]
