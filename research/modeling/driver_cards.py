"""历史全场车手评分卡的冻结、可审计生成合同。

当前只允许发布已经通过客观性门控的 ``PAC``。它直接使用三赛季面板中
每名车手唯一一次的池化 ``driver_base_z``，先反向为“越大越快”，再按冻结
的 28 人总体分布标准化：

``PAC = clip(75 + 10 * ((-driver_base_z - mean) / population_sd), 0, 100)``

上游 ``driver_base_z`` 已由面板模型正则化；本层严禁再按 ``sample_count``
收缩。由于当前产物没有导出池化 ``driver_base_z`` 的区间，``PAC.ci`` 必须
为 ``None``，不能借用年度 ``driver_pace_z`` 区间伪装成池化区间。
"""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Iterable, Mapping, Sequence

from modeling.objective_evaluation import PROHIBITED_ABILITY_INPUTS
from modeling.performance_scoring import PROHIBITED_OVR_RESULT_FIELDS


CONTRACT_VERSION = "driver-rating-cards-v1"
ABILITY_MODULES = ("PAC", "TYM", "EXT", "ATK", "DEF", "AWA", "STR", "ADP")
NULL_ONLY_METRICS = ("TYM", "EXT", "ATK", "DEF", "AWA", "STR", "ADP")
CARD_METRICS = (*ABILITY_MODULES, "EventDelivery", "OVR")


class DriverCardContractError(ValueError):
    """输入不满足冻结评分合同时抛出。"""


def _finite_float(value: Any, *, field: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise DriverCardContractError(f"{field} 必须是有限数值") from exc
    if not math.isfinite(converted):
        raise DriverCardContractError(f"{field} 必须是有限数值")
    return converted


def _required_text(mapping: Mapping[str, Any], field: str) -> str:
    value = str(mapping.get(field, "")).strip()
    if not value:
        raise DriverCardContractError(f"缺少必需字段：{field}")
    return value


def _population_stats(values: Sequence[float]) -> tuple[float, float]:
    """返回总体均值与总体标准差（ddof=0）。"""

    if not values:
        raise DriverCardContractError("PAC 参考总体不能为空")
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    sd = math.sqrt(variance)
    if sd <= 0.0:
        raise DriverCardContractError("PAC 参考总体标准差必须大于零")
    return mean, sd


def _close(actual: float, expected: float, tolerance: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance)


def _validate_source_binding(
    payload: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    label: str,
) -> None:
    expected_config_hash = _required_text(binding, "config_hash")
    actual_run_id = _required_text(payload, "run_id")
    actual_config_hash = _required_text(payload, "config_hash")
    expected_run_id = str(binding.get("run_id", "")).strip()
    expected_run_id_prefix = str(binding.get("run_id_prefix", "")).strip()
    if not expected_run_id and not expected_run_id_prefix:
        raise DriverCardContractError(f"{label} 必须绑定 run_id 或 run_id_prefix")
    if expected_run_id and actual_run_id != expected_run_id:
        raise DriverCardContractError(
            f"{label} run_id 漂移：期望 {expected_run_id}，实际 {actual_run_id}"
        )
    if expected_run_id_prefix and not actual_run_id.startswith(expected_run_id_prefix):
        raise DriverCardContractError(
            f"{label} run_id 前缀漂移：期望 {expected_run_id_prefix}，实际 {actual_run_id}"
        )
    if actual_config_hash != expected_config_hash:
        raise DriverCardContractError(
            f"{label} config_hash 漂移：期望 {expected_config_hash}，实际 {actual_config_hash}"
        )


def _unique_driver_rows(
    seasonal_result: Mapping[str, Any],
    *,
    duplicate_tolerance: float,
) -> dict[str, dict[str, Any]]:
    """把年度行折叠成每名车手一行，并验证池化效应没有年度漂移。"""

    season_ratings = seasonal_result.get("season_ratings")
    if not isinstance(season_ratings, Mapping):
        raise DriverCardContractError("seasonal_result.season_ratings 缺失")
    rows = season_ratings.get("drivers")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise DriverCardContractError("seasonal_result.season_ratings.drivers 必须为数组")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise DriverCardContractError(f"drivers[{index}] 必须为对象")
        driver = str(row.get("driver", "")).strip()
        if not driver:
            raise DriverCardContractError(f"drivers[{index}] 缺少 driver")
        grouped[driver].append(row)

    unique: dict[str, dict[str, Any]] = {}
    for driver, driver_rows in grouped.items():
        base_values = [
            _finite_float(row.get("driver_base_z"), field=f"{driver}.driver_base_z")
            for row in driver_rows
        ]
        base = base_values[0]
        if any(not _close(value, base, duplicate_tolerance) for value in base_values[1:]):
            raise DriverCardContractError(
                f"{driver} 的池化 driver_base_z 在年度行之间不一致，不能生成长期 PAC"
            )

        years = sorted({int(row["year"]) for row in driver_rows})
        teams = sorted({str(row["team"]) for row in driver_rows if str(row.get("team", "")).strip()})
        sample_counts = [
            int(row.get("sample_count", 0))
            for row in driver_rows
            if row.get("sample_count") is not None
        ]
        if any(value < 0 for value in sample_counts):
            raise DriverCardContractError(f"{driver}.sample_count 不能为负数")
        unique[driver] = {
            "driver": driver,
            "driver_base_z": base,
            "years": years,
            "teams": teams,
            # 仅作证据覆盖展示，绝不进入 PAC 公式。
            "season_rows": len(driver_rows),
            "sample_count_total": sum(sample_counts),
        }
    return unique


def _component_lookup(transfer_graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    components = transfer_graph.get("components", [])
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)):
        return lookup
    for component in components:
        if not isinstance(component, Mapping):
            continue
        for driver in component.get("drivers", []):
            lookup[str(driver)] = {
                "component_index": int(component.get("index", -1)),
                "component_team_count": int(component.get("n_teams", 0)),
                "internally_transfer_anchored": bool(
                    component.get("internally_transfer_anchored", False)
                ),
                "cross_grid_anchored": bool(component.get("cross_grid_anchored", False)),
            }
    return lookup


def _connectivity_lookup(transfer_graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    rows = transfer_graph.get("driver_connectivity", [])
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return lookup
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        driver = str(row.get("driver", "")).strip()
        if not driver:
            continue
        lookup[driver] = {
            "distinct_teams": int(row.get("distinct_teams", 0)),
            "distinct_teammates": int(row.get("distinct_teammates", 0)),
            "graph_degree": int(row.get("degree", 0)),
            "weakly_anchored": bool(row.get("weakly_anchored", False)),
        }
    return lookup


def _driver_identification(
    driver: str,
    *,
    component_lookup: Mapping[str, Mapping[str, Any]],
    connectivity_lookup: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    component = dict(component_lookup.get(driver, {}))
    connectivity = dict(connectivity_lookup.get(driver, {}))
    flags: list[str] = []
    if connectivity.get("weakly_anchored", False):
        flags.append("driver_teammate_network_weakly_anchored")
    if not component.get("cross_grid_anchored", False):
        flags.append("component_not_cross_grid_transfer_anchored")
    if not component.get("internally_transfer_anchored", False):
        flags.append("component_not_internally_transfer_anchored")
    if not flags:
        flags.append("transfer_graph_anchor_available")
    return {**component, **connectivity, "flags": flags}


def _gate_by_id(objective_result: Mapping[str, Any], gate_id: str) -> Mapping[str, Any]:
    evaluation = objective_result.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise DriverCardContractError("objective_result.evaluation 缺失")
    gates = evaluation.get("gates")
    if not isinstance(gates, Sequence) or isinstance(gates, (str, bytes)):
        raise DriverCardContractError("objective_result.evaluation.gates 必须为数组")
    matches = [
        gate
        for gate in gates
        if isinstance(gate, Mapping) and str(gate.get("gate_id", "")) == gate_id
    ]
    if len(matches) != 1:
        raise DriverCardContractError(f"客观性报告中必须且只能有一个 {gate_id} 门控")
    return matches[0]


def _module_coverage(objective_result: Mapping[str, Any]) -> dict[str, str]:
    evaluation = objective_result.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise DriverCardContractError("objective_result.evaluation 缺失")
    coverage = evaluation.get("module_coverage")
    if not isinstance(coverage, Mapping):
        raise DriverCardContractError("objective_result.evaluation.module_coverage 缺失")
    result = {module: str(coverage.get(module, "missing")) for module in ABILITY_MODULES}
    return result


def _forbidden_fields(extra_fields: Iterable[str] = ()) -> list[str]:
    return sorted(
        {
            *(str(value).strip().lower() for value in PROHIBITED_ABILITY_INPUTS),
            *(str(value).strip().lower() for value in PROHIBITED_OVR_RESULT_FIELDS),
            *(str(value).strip().lower() for value in extra_fields),
        }
        - {""}
    )


def build_driver_rating_cards(
    seasonal_result: Mapping[str, Any],
    objective_result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """生成全场历史车手卡，并对冻结来源和总体尺度做硬校验。

    ``contract`` 应来自版本化配置的 ``rating_contract``。任何源身份、车手数、
    总体均值或总体标准差漂移都会失败，避免在不知情的情况下重心化评分。
    """

    seasonal_binding = contract.get("seasonal_source")
    objective_binding = contract.get("objective_source")
    if not isinstance(seasonal_binding, Mapping) or not isinstance(objective_binding, Mapping):
        raise DriverCardContractError("评分合同必须绑定 seasonal_source 与 objective_source")
    _validate_source_binding(
        seasonal_result,
        binding=seasonal_binding,
        label="seasonal_result",
    )
    _validate_source_binding(
        objective_result,
        binding=objective_binding,
        label="objective_result",
    )

    expected_count = int(contract.get("population_size", 0))
    if expected_count <= 1:
        raise DriverCardContractError("population_size 必须大于 1")
    expected_mean = _finite_float(contract.get("population_mean"), field="population_mean")
    expected_sd = _finite_float(contract.get("population_sd"), field="population_sd")
    tolerance = _finite_float(
        contract.get("validation_tolerance", 1e-12),
        field="validation_tolerance",
    )
    if tolerance <= 0.0:
        raise DriverCardContractError("validation_tolerance 必须大于零")
    if expected_sd <= 0.0:
        raise DriverCardContractError("population_sd 必须大于零")
    if int(contract.get("sd_ddof", -1)) != 0:
        raise DriverCardContractError("PAC 冻结参考必须使用总体标准差 ddof=0")
    if bool(contract.get("additional_sample_count_shrinkage", True)):
        raise DriverCardContractError("历史 PAC 禁止按 sample_count 二次收缩")
    if contract.get("pooled_pac_interval_source") is not None:
        raise DriverCardContractError("当前没有池化 PAC 区间，pooled_pac_interval_source 必须为 null")

    unique = _unique_driver_rows(seasonal_result, duplicate_tolerance=tolerance)
    if len(unique) != expected_count:
        raise DriverCardContractError(
            f"冻结总体应有 {expected_count} 名车手，实际为 {len(unique)}"
        )
    advantages = [-row["driver_base_z"] for row in unique.values()]
    actual_mean, actual_sd = _population_stats(advantages)
    if not _close(actual_mean, expected_mean, tolerance):
        raise DriverCardContractError(
            f"冻结总体均值漂移：期望 {expected_mean!r}，实际 {actual_mean!r}"
        )
    if not _close(actual_sd, expected_sd, tolerance):
        raise DriverCardContractError(
            f"冻结总体标准差漂移：期望 {expected_sd!r}，实际 {actual_sd!r}"
        )

    module_coverage = _module_coverage(objective_result)
    transfer_graph = seasonal_result.get("transfer_graph")
    if not isinstance(transfer_graph, Mapping):
        raise DriverCardContractError("seasonal_result.transfer_graph 缺失")
    component_lookup = _component_lookup(transfer_graph)
    connectivity_lookup = _connectivity_lookup(transfer_graph)

    cards: list[dict[str, Any]] = []
    for driver, row in unique.items():
        raw_advantage = -row["driver_base_z"]
        standardized = (raw_advantage - expected_mean) / expected_sd
        pac = min(100.0, max(0.0, 75.0 + 10.0 * standardized))
        pac_metric = (
            {
                "rating": pac,
                "z": standardized,
                "raw_driver_base_z": row["driver_base_z"],
                "raw_advantage_z": raw_advantage,
                "ci": None,
                "status": "validated",
                "source": "pooled_driver_base_z",
                "additional_sample_count_shrinkage": False,
            }
            if module_coverage["PAC"] == "validated"
            else None
        )
        metrics: dict[str, Any] = {"PAC": pac_metric}
        metrics.update({metric: None for metric in NULL_ONLY_METRICS})
        metrics["EventDelivery"] = None
        metrics["OVR"] = None
        metric_status = {
            **module_coverage,
            "EventDelivery": "not_estimated",
            "OVR": "blocked_until_all_ability_modules_validated",
        }
        cards.append(
            {
                "driver": driver,
                "years": row["years"],
                "teams": row["teams"],
                "coverage": {
                    "season_rows": row["season_rows"],
                    "sample_count_total": row["sample_count_total"],
                    "sample_count_role": "evidence_coverage_only_not_rating_shrinkage",
                },
                "identification": _driver_identification(
                    driver,
                    component_lookup=component_lookup,
                    connectivity_lookup=connectivity_lookup,
                ),
                "metrics": metrics,
                "metric_status": metric_status,
            }
        )
    if module_coverage["PAC"] == "validated":
        cards.sort(key=lambda card: (-card["metrics"]["PAC"]["rating"], card["driver"]))
        for rank, card in enumerate(cards, start=1):
            card["pac_rank"] = rank
    else:
        cards.sort(key=lambda card: card["driver"])
        for card in cards:
            card["pac_rank"] = None

    future_gate = _gate_by_id(objective_result, "2026_confirmatory_exposure")
    future_passed = bool(future_gate.get("passed", False))
    sample_scope = seasonal_result.get("sample_scope", {})
    if not isinstance(sample_scope, Mapping):
        sample_scope = {}
    objective_evaluation = objective_result.get("evaluation", {})
    if not isinstance(objective_evaluation, Mapping):
        objective_evaluation = {}

    global_flags: list[str] = []
    if not bool(transfer_graph.get("connected", False)):
        global_flags.append(
            f"driver_team_graph_disconnected:{int(transfer_graph.get('n_components', 0))}_components"
        )
    unvalidated = [
        module for module, status in module_coverage.items() if status != "validated"
    ]
    if unvalidated:
        global_flags.append(f"unvalidated_driver_modules:{','.join(sorted(unvalidated))}")
    global_flags.append("pac_driver_specific_time_forward_gate_not_passed")
    global_flags.append("pooled_pac_interval_not_exported_ci_null")
    global_flags.append("annual_driver_intervals_not_reused_for_pooled_pac")
    global_flags.append("sample_count_not_used_for_second_stage_shrinkage")

    extra_forbidden = contract.get("forbidden_ability_input_fields", [])
    if not isinstance(extra_forbidden, Sequence) or isinstance(extra_forbidden, (str, bytes)):
        raise DriverCardContractError("forbidden_ability_input_fields 必须为数组")

    return {
        "contract_version": CONTRACT_VERSION,
        "release_status": str(
            objective_evaluation.get(
                "release_status",
                "historical_result_accounting_unknown_full_driver_card_not_ready",
            )
        ),
        "reference_distribution": {
            "source_metric": "negative_driver_base_z",
            "source_run_id": seasonal_result["run_id"],
            "source_config_hash": seasonal_result["config_hash"],
            "population_size": expected_count,
            "population_mean": expected_mean,
            "population_sd": expected_sd,
            "sd_ddof": 0,
            "formula": "clip(75 + 10 * ((-driver_base_z - population_mean) / population_sd), 0, 100)",
            "additional_sample_count_shrinkage": False,
            "pooled_interval_available": False,
            "interval_policy": (
                "ci_null_because_pooled_driver_base_interval_not_exported;"
                "annual_driver_pace_intervals_are_not_reused"
            ),
        },
        "coverage": {
            "years": [int(year) for year in sample_scope.get("years", [])],
            "race_meetings": int(sample_scope.get("race_meetings", 0)),
            "clean_laps": int(sample_scope.get("clean_laps", 0)),
            "driver_count": len(cards),
            "driver_season_rows": sum(card["coverage"]["season_rows"] for card in cards),
            "module_coverage": module_coverage,
            "published_modules": [
                module for module, status in module_coverage.items() if status == "validated"
            ],
            "null_metrics": [
                *(
                    ["PAC"]
                    if module_coverage["PAC"] != "validated"
                    else []
                ),
                *NULL_ONLY_METRICS,
                "EventDelivery",
                "OVR",
            ],
        },
        "identification": {
            "transfer_graph_connected": bool(transfer_graph.get("connected", False)),
            "component_count": int(transfer_graph.get("n_components", 0)),
            "weakly_anchored_drivers": sorted(
                str(driver) for driver in transfer_graph.get("weakly_anchored_drivers", [])
            ),
            "flags": global_flags,
        },
        "forbidden_ability_input_fields": _forbidden_fields(extra_forbidden),
        "season_2026_release_gate": {
            "gate_id": "2026_confirmatory_exposure",
            "passed": future_passed,
            "observed_events": int(future_gate.get("value", 0)),
            "minimum_events": int(float(future_gate.get("threshold", 0))),
            "role": str(future_gate.get("role", "future_release")),
            "note": str(future_gate.get("note", "")),
            "publication_mode": (
                "confirmatory_allowed" if future_passed else "exploratory_only"
            ),
        },
        "cards": cards,
    }


__all__ = [
    "ABILITY_MODULES",
    "CARD_METRICS",
    "CONTRACT_VERSION",
    "DriverCardContractError",
    "NULL_ONLY_METRICS",
    "build_driver_rating_cards",
]
