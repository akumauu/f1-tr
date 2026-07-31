"""Race Dossier 赛后一致性与跨 Stint 可比分组派生审计层。

本模块只消费已发布的 Race Dossier 产物与同源冻结圈宇宙，不重新拟合
模型、不修改任何发布门、不参与任何拟合。所有输出都是赛后构念审计：

- 成绩侧来自圈宇宙末圈 ``position`` 代理，不含赛后罚时、DSQ 与 FIA
  最终修订，不得称为 FIA 最终分类；
- 表现侧来自 Dossier 已发布的"车辆共同基线 + 车手兑现偏差"秒数，
  是条件代理，不是因果真值；
- "无意外"由预注册清洁判据操作化；真实结果出来后如需调整阈值，
  必须新建版本，不得回调本层配置。
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


CLASSIFICATION_BASIS = "race_lap_universe_last_lap_position_proxy"

_REPRESENTATIVE_STATUSES = {
    "identified_conditional_proxy",
    "level_only_tyre_age_slope_not_identified",
}


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _kendall_tau_b(
    x_values: Sequence[float], y_values: Sequence[float]
) -> float | None:
    """小样本 O(n^2) Kendall tau-b；并列以 tau-b 分母修正。"""

    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    concordant = 0
    discordant = 0
    ties_x = 0
    ties_y = 0
    for (x_a, y_a), (x_b, y_b) in combinations(
        zip(x_values, y_values), 2
    ):
        delta_x = x_a - x_b
        delta_y = y_a - y_b
        if delta_x == 0 and delta_y == 0:
            ties_x += 1
            ties_y += 1
        elif delta_x == 0:
            ties_x += 1
        elif delta_y == 0:
            ties_y += 1
        elif delta_x * delta_y > 0:
            concordant += 1
        else:
            discordant += 1
    pair_total = concordant + discordant + ties_x + ties_y
    denominator = float(
        np.sqrt(
            (pair_total - ties_x) * (pair_total - ties_y)
        )
    )
    if denominator == 0.0:
        return None
    return float((concordant - discordant) / denominator)


def derive_driver_classification_proxy(
    event_laps: pd.DataFrame,
    *,
    classified_completion_fraction: float,
) -> list[dict[str, Any]]:
    """从末圈 ``position`` 派生全场车手完赛名次代理。

    排序规则近似 FIA 惯例：先按完成圈数降序，再按各自末圈位置升序。
    该代理不含赛后罚时、DSQ 与 FIA 最终修订，仅用于赛后一致性审计。
    """

    required = {"driver_id", "team_name", "lap_number", "position"}
    missing = required - set(event_laps.columns)
    if missing:
        raise ValueError(f"圈宇宙缺少列：{sorted(missing)}")
    laps = event_laps.copy()
    laps["lap_number"] = pd.to_numeric(
        laps["lap_number"], errors="coerce"
    )
    laps["position"] = pd.to_numeric(laps["position"], errors="coerce")
    laps = laps.dropna(subset=["lap_number"])
    if laps.empty:
        return []
    race_lap_span = int(laps["lap_number"].max())
    rows: list[dict[str, Any]] = []
    for (driver, team), group in laps.groupby(
        ["driver_id", "team_name"], observed=True
    ):
        ordered = group.sort_values("lap_number")
        last_lap = int(ordered["lap_number"].max())
        positions = ordered["position"].to_numpy(dtype=float)
        finite_positions = positions[np.isfinite(positions)]
        last_position = (
            float(finite_positions[-1]) if len(finite_positions) else None
        )
        completion = float(last_lap) / float(race_lap_span)
        rows.append(
            {
                "driver": str(driver),
                "team": str(team),
                "laps_completed_proxy": last_lap,
                "race_lap_span": race_lap_span,
                "completion_fraction": completion,
                "classified_proxy": bool(
                    completion
                    >= float(classified_completion_fraction)
                ),
                "last_lap_position": last_position,
                "classification_basis": CLASSIFICATION_BASIS,
            }
        )
    rows.sort(
        key=lambda row: (
            -row["laps_completed_proxy"],
            (
                row["last_lap_position"]
                if row["last_lap_position"] is not None
                else float("inf")
            ),
            row["driver"],
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["classification_rank_proxy"] = rank
    return rows


def derive_event_incident_profile(
    event_laps: pd.DataFrame,
    classification_rows: Sequence[Mapping[str, Any]],
    dossier: Mapping[str, Any],
    *,
    clean_gate: Mapping[str, Any],
) -> dict[str, Any]:
    """把"无意外比赛"操作化为预注册清洁判据。

    判据只用可观测代理：非绿旗圈占比、完赛退赛代理数、未解释慢圈
    占比。判据不通过不代表比赛数据无效，只表示成绩—表现一致性在该场
    只能作描述性审计，不能用于"无意外应当匹配"的验收。
    """

    total = int(len(event_laps))
    non_green_fraction: float | None = None
    if "all_green" in event_laps.columns and total:
        green_flags = (
            event_laps["all_green"]
            .astype("boolean")
            .fillna(False)
            .to_numpy(dtype=bool)
        )
        non_green_fraction = float(1.0 - green_flags.mean())
    retired = [
        dict(row)
        for row in classification_rows
        if not row["classified_proxy"]
    ]
    ledger = dossier.get("event_ledger") or {}
    category = ledger.get("lap_category_counts") or {}
    observed = int(ledger.get("observed_laps") or 0)
    unexplained_slow_fraction = (
        float(category.get("unexplained_slow_lap_proxy", 0)) / observed
        if observed
        else None
    )
    reasons: list[str] = []
    if non_green_fraction is None:
        reasons.append("non_green_fraction_unavailable")
    elif non_green_fraction > float(
        clean_gate["maximum_non_green_lap_fraction"]
    ):
        reasons.append("non_green_fraction_above_gate")
    if len(retired) > int(clean_gate["maximum_field_retirements"]):
        reasons.append("field_retirements_above_gate")
    if unexplained_slow_fraction is None:
        reasons.append("unexplained_slow_fraction_unavailable")
    elif unexplained_slow_fraction > float(
        clean_gate["maximum_unexplained_slow_lap_fraction"]
    ):
        reasons.append("unexplained_slow_fraction_above_gate")
    return {
        "non_green_lap_fraction": non_green_fraction,
        "field_retirement_count_proxy": len(retired),
        "retired_drivers_proxy": sorted(
            row["driver"] for row in retired
        ),
        "unexplained_slow_lap_fraction": unexplained_slow_fraction,
        "clean_race_proxy": not reasons,
        "not_clean_reasons": reasons,
        "gate": dict(clean_gate),
    }


def build_result_performance_concordance(
    dossier: Mapping[str, Any],
    classification_rows: Sequence[Mapping[str, Any]],
    incident_profile: Mapping[str, Any],
    *,
    pair_rules: Mapping[str, Any],
) -> dict[str, Any]:
    """车手成对"配速表现 vs 成绩代理"一致性审计。

    配速表现 = 车辆共同基线 + 车手兑现偏差（秒）；倒挂对按预注册顺序
    归因：未完赛/未完赛代理 → 策略/赛道位置代理足以覆盖差距 → 配速差
    在噪声地板内 → 其余记为 unexplained_inversion。清洁场出现
    unexplained_inversion 即判定"成绩与表现不匹配且模型未解释"。
    """

    decomposition = dossier.get("vehicle_driver_decomposition") or []
    if not decomposition:
        return {
            "status": "not_available_no_decomposition",
            "scope_drivers": [],
            "pairs": [],
            "unexplained_pairs": [],
            "verdict": "not_available",
        }
    race_lap_span = int(dossier["coverage"]["race_lap_span"])
    class_by_driver = {
        str(row["driver"]): row for row in classification_rows
    }
    scoped: list[dict[str, Any]] = []
    missing_classification: list[str] = []
    for row in decomposition:
        code = str(row["driver"])
        components = row["components"]
        pace = float(components["vehicle_common_baseline_s"]) + float(
            components["driver_realization_deviation_s"]
        )
        context = float(
            components["strategy_track_position_proxy_s"]
        ) + float(components["unexplained_residual_s"])
        classification = class_by_driver.get(code)
        if classification is None:
            missing_classification.append(code)
            continue
        scoped.append(
            {
                "driver": code,
                "team": str(row["team"]),
                "pace_performance_s": pace,
                "context_proxy_s": context,
                "accounted_total_s": float(
                    row["observed_accounted_delta_s"]
                ),
                "classified_proxy": bool(
                    classification["classified_proxy"]
                ),
                "classification_rank_proxy": int(
                    classification["classification_rank_proxy"]
                ),
            }
        )
    if len(scoped) < 2:
        return {
            "status": "insufficient_scope",
            "scope_drivers": scoped,
            "missing_classification_drivers": missing_classification,
            "pairs": [],
            "unexplained_pairs": [],
            "verdict": "not_available",
        }
    for rank, row in enumerate(
        sorted(scoped, key=lambda item: item["classification_rank_proxy"]),
        start=1,
    ):
        row["scope_classification_rank"] = rank
    for rank, row in enumerate(
        sorted(scoped, key=lambda item: item["pace_performance_s"]),
        start=1,
    ):
        row["pace_rank"] = rank
    noise_floor_total_s = float(
        pair_rules["pace_noise_floor_s_per_lap"]
    ) * float(race_lap_span)
    tolerance_s = float(pair_rules["context_cover_tolerance_s"])
    pairs: list[dict[str, Any]] = []
    for left, right in combinations(scoped, 2):
        faster, slower = (
            (left, right)
            if left["pace_performance_s"] <= right["pace_performance_s"]
            else (right, left)
        )
        margin_s = (
            slower["pace_performance_s"] - faster["pace_performance_s"]
        )
        inverted = (
            faster["scope_classification_rank"]
            > slower["scope_classification_rank"]
        )
        context_delta_s = (
            faster["context_proxy_s"] - slower["context_proxy_s"]
        )
        applicable: list[str] = []
        if inverted:
            if not (
                faster["classified_proxy"] and slower["classified_proxy"]
            ):
                applicable.append("not_classified_or_retired_proxy")
            if context_delta_s >= margin_s - tolerance_s:
                applicable.append(
                    "strategy_track_position_proxy_covers_margin"
                )
            if margin_s <= noise_floor_total_s:
                applicable.append("within_pace_noise_floor")
        primary = (
            "concordant"
            if not inverted
            else (applicable[0] if applicable else "unexplained_inversion")
        )
        pairs.append(
            {
                "pace_faster_driver": faster["driver"],
                "pace_slower_driver": slower["driver"],
                "pace_margin_s": margin_s,
                "context_delta_s": context_delta_s,
                "result_inverted": inverted,
                "primary_reason": primary,
                "applicable_reasons": applicable,
            }
        )
    unexplained = [
        pair
        for pair in pairs
        if pair["primary_reason"] == "unexplained_inversion"
    ]
    tau_pace = _kendall_tau_b(
        [row["pace_rank"] for row in scoped],
        [row["scope_classification_rank"] for row in scoped],
    )
    accounted_rank = {
        row["driver"]: rank
        for rank, row in enumerate(
            sorted(scoped, key=lambda item: item["accounted_total_s"]),
            start=1,
        )
    }
    tau_accounted = _kendall_tau_b(
        [accounted_rank[row["driver"]] for row in scoped],
        [row["scope_classification_rank"] for row in scoped],
    )
    clean_race = bool(incident_profile.get("clean_race_proxy"))
    if clean_race:
        verdict = (
            "clean_race_fully_concordant"
            if not unexplained
            else "clean_race_unexplained_mismatch"
        )
    else:
        verdict = "incident_race_descriptive_only"
    intervals = {
        str(row["team"]): row.get("rank_interval_80")
        for row in (
            dossier.get("race_analysis", {}).get(
                "descriptive_team_order"
            )
            or []
        )
    }
    team_rows: list[dict[str, Any]] = []
    for row in (
        dossier.get("result_impact_audit", {}).get(
            "classification_proxy_comparison"
        )
        or []
    ):
        interval = intervals.get(str(row["team"]))
        within: bool | None = None
        if interval and row.get("classification_proxy_rank") is not None:
            within = bool(
                interval[0]
                <= int(row["classification_proxy_rank"])
                <= interval[1]
            )
        team_rows.append(
            {
                **dict(row),
                "classification_within_pace_rank_interval_80": within,
            }
        )
    return {
        "status": "computed",
        "pace_performance_basis": (
            "vehicle_common_baseline_plus_driver_realization_deviation_s"
        ),
        "scope_note": (
            "仅覆盖 Dossier 车辆—车手分解发布的车队车手；"
            "不是全场车手榜。"
        ),
        "scope_drivers": sorted(
            scoped, key=lambda item: item["pace_rank"]
        ),
        "missing_classification_drivers": missing_classification,
        "pair_count": len(pairs),
        "concordant_pair_count": sum(
            1 for pair in pairs if not pair["result_inverted"]
        ),
        "explained_inversion_count": sum(
            1
            for pair in pairs
            if pair["result_inverted"]
            and pair["primary_reason"] != "unexplained_inversion"
        ),
        "unexplained_inversion_count": len(unexplained),
        "pairs": pairs,
        "unexplained_pairs": unexplained,
        "kendall_tau_pace_vs_classification": tau_pace,
        "kendall_tau_accounted_vs_classification": tau_accounted,
        "pace_noise_floor_total_s": noise_floor_total_s,
        "clean_race_proxy": clean_race,
        "verdict": verdict,
        "team_comparison": team_rows,
        "boundaries": [
            "classification_proxy_not_fia_final",
            "post_race_construct_audit_not_used_for_fit",
            "context_proxy_is_conditional_not_causal_loss",
        ],
    }


def build_stint_comparison_table(
    dossier: Mapping[str, Any],
    *,
    comparison_rules: Mapping[str, Any],
) -> dict[str, Any]:
    """按 v16 冻结门槛自动生成跨 Stint 直接可比对表。

    只有同配方、参考胎龄差不超过冻结上限、比赛阶段中点相近且自身
    发布门通过的 Stint 才进入两两比较；不同配方或胎龄差过大的组合
    一律不比，不做任何外推。
    """

    stints = dossier.get("stint_dossiers") or []
    if not any(
        "representative_tyre_age_pace_s" in stint for stint in stints
    ):
        return {
            "status": "not_available_schema_before_v16",
            "qualified_pairs": [],
        }
    race_lap_span = int(dossier["coverage"]["race_lap_span"])
    max_age_gap = float(
        comparison_rules["maximum_reference_tyre_age_gap_laps"]
    )
    max_phase_gap = float(
        comparison_rules["maximum_phase_midpoint_fraction_gap"]
    )
    require_valid = bool(comparison_rules["require_valid_status"])
    eligible: list[dict[str, Any]] = []
    excluded_counts = {
        "status_not_valid": 0,
        "representative_pace_missing": 0,
        "representative_status_not_eligible": 0,
    }
    for stint in stints:
        if require_valid and stint.get("status") != "valid":
            excluded_counts["status_not_valid"] += 1
            continue
        pace = _finite_or_none(
            stint.get("representative_tyre_age_pace_s")
        )
        age = _finite_or_none(stint.get("representative_tyre_age_laps"))
        if pace is None or age is None:
            excluded_counts["representative_pace_missing"] += 1
            continue
        if (
            str(stint.get("representative_tyre_age_pace_status"))
            not in _REPRESENTATIVE_STATUSES
        ):
            excluded_counts["representative_status_not_eligible"] += 1
            continue
        midpoint = (
            (float(stint["lap_start"]) + float(stint["lap_end"])) / 2.0
        ) / float(race_lap_span)
        interval = stint.get(
            "representative_tyre_age_pace_fuel_sensitivity_interval_s"
        )
        if (
            not isinstance(interval, (list, tuple))
            or len(interval) != 2
            or _finite_or_none(interval[0]) is None
            or _finite_or_none(interval[1]) is None
        ):
            interval = [pace, pace]
        eligible.append(
            {
                "driver": str(stint["driver"]),
                "team": str(stint["team"]),
                "stint_number": int(stint["stint_number"]),
                "compound": str(stint["compound"]),
                "reference_pace_s": pace,
                "reference_tyre_age_laps": age,
                "slope_s_per_tyre_lap": _finite_or_none(
                    stint.get("degradation_s_per_tyre_lap")
                ),
                "phase_midpoint_fraction": midpoint,
                "fuel_interval_s": [
                    float(interval[0]),
                    float(interval[1]),
                ],
            }
        )
    pairs: list[dict[str, Any]] = []
    for left, right in combinations(eligible, 2):
        if left["compound"] != right["compound"]:
            continue
        age_gap = abs(
            left["reference_tyre_age_laps"]
            - right["reference_tyre_age_laps"]
        )
        if age_gap > max_age_gap:
            continue
        phase_gap = abs(
            left["phase_midpoint_fraction"]
            - right["phase_midpoint_fraction"]
        )
        if phase_gap > max_phase_gap:
            continue
        first, second = sorted(
            (left, right),
            key=lambda item: (item["driver"], item["stint_number"]),
        )
        pace_delta = (
            first["reference_pace_s"] - second["reference_pace_s"]
        )
        delta_low = (
            first["fuel_interval_s"][0] - second["fuel_interval_s"][1]
        )
        delta_high = (
            first["fuel_interval_s"][1] - second["fuel_interval_s"][0]
        )
        sign_stable = (delta_low > 0.0 and delta_high > 0.0) or (
            delta_low < 0.0 and delta_high < 0.0
        )
        slope_delta = (
            first["slope_s_per_tyre_lap"] - second["slope_s_per_tyre_lap"]
            if first["slope_s_per_tyre_lap"] is not None
            and second["slope_s_per_tyre_lap"] is not None
            else None
        )
        pairs.append(
            {
                "compound": first["compound"],
                "first_driver": first["driver"],
                "first_team": first["team"],
                "first_stint_number": first["stint_number"],
                "second_driver": second["driver"],
                "second_team": second["team"],
                "second_stint_number": second["stint_number"],
                "same_driver": first["driver"] == second["driver"],
                "reference_pace_delta_s_first_minus_second": pace_delta,
                "slope_delta_s_per_tyre_lap_first_minus_second": (
                    slope_delta
                ),
                "reference_tyre_age_gap_laps": age_gap,
                "phase_midpoint_fraction_gap": phase_gap,
                "fuel_scenario_sign_stable": sign_stable,
            }
        )
    compound_summary: dict[str, int] = {}
    for pair in pairs:
        compound_summary[pair["compound"]] = (
            compound_summary.get(pair["compound"], 0) + 1
        )
    driver_pair_index: dict[tuple[str, str], list[float]] = {}
    for pair in pairs:
        if pair["same_driver"]:
            continue
        key = (pair["first_driver"], pair["second_driver"])
        driver_pair_index.setdefault(key, []).append(
            pair["reference_pace_delta_s_first_minus_second"]
        )
    driver_pair_summary = [
        {
            "first_driver": key[0],
            "second_driver": key[1],
            "qualified_pair_count": len(values),
            "median_reference_pace_delta_s_first_minus_second": float(
                np.median(values)
            ),
        }
        for key, values in sorted(driver_pair_index.items())
    ]
    return {
        "status": "computed" if pairs else "no_qualified_pairs",
        "eligible_stint_count": len(eligible),
        "excluded_stint_counts": excluded_counts,
        "qualified_pair_count": len(pairs),
        "qualified_pairs": pairs,
        "compound_pair_counts": compound_summary,
        "driver_pair_summary": driver_pair_summary,
        "rules": {
            "maximum_reference_tyre_age_gap_laps": max_age_gap,
            "maximum_phase_midpoint_fraction_gap": max_phase_gap,
            "require_valid_status": require_valid,
            "same_compound_only": True,
        },
        "boundaries": [
            "different_compound_or_far_tyre_age_pairs_not_compared",
            "representative_pace_is_conditional_proxy_not_observed_lap",
        ],
    }


def build_season_concordance_summary(
    event_reports: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """跨场汇总：清洁场一致率、未解释倒挂清单与 tau 分布。"""

    total = len(event_reports)
    computed = [
        report
        for report in event_reports
        if report["concordance"].get("status") == "computed"
    ]
    clean = [
        report
        for report in computed
        if report["incident_profile"]["clean_race_proxy"]
    ]
    clean_concordant = [
        report
        for report in clean
        if report["concordance"]["verdict"]
        == "clean_race_fully_concordant"
    ]
    clean_mismatch = [
        report
        for report in clean
        if report["concordance"]["verdict"]
        == "clean_race_unexplained_mismatch"
    ]
    unexplained_details: list[dict[str, Any]] = []
    for report in computed:
        for pair in report["concordance"]["unexplained_pairs"]:
            unexplained_details.append(
                {
                    "year": report["scope"]["year"],
                    "meeting": report["scope"]["meeting"],
                    "clean_race_proxy": report["incident_profile"][
                        "clean_race_proxy"
                    ],
                    **pair,
                }
            )
    tau_values = [
        report["concordance"]["kendall_tau_pace_vs_classification"]
        for report in computed
        if report["concordance"][
            "kendall_tau_pace_vs_classification"
        ]
        is not None
    ]
    return {
        "events_total": total,
        "events_with_concordance": len(computed),
        "clean_race_count": len(clean),
        "clean_race_fully_concordant_count": len(clean_concordant),
        "clean_race_unexplained_mismatch_count": len(clean_mismatch),
        "clean_race_unexplained_mismatch_events": [
            {
                "year": report["scope"]["year"],
                "meeting": report["scope"]["meeting"],
                "unexplained_inversion_count": report["concordance"][
                    "unexplained_inversion_count"
                ],
            }
            for report in clean_mismatch
        ],
        "incident_race_count": len(computed) - len(clean),
        "unexplained_inversions_total": len(unexplained_details),
        "unexplained_inversion_details": unexplained_details,
        "kendall_tau_pace_vs_classification_median": (
            float(np.median(tau_values)) if tau_values else None
        ),
        "kendall_tau_pace_vs_classification_min": (
            float(np.min(tau_values)) if tau_values else None
        ),
        "acceptance_rule": (
            "清洁场必须 0 个 unexplained_inversion；"
            "未达标场次列入迭代清单，禁止用调阈值方式清零。"
        ),
    }
