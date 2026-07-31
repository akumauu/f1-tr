"""综合评分系统的独立客观性门控。

本模块只读取模型已经产出的机器可读结果，不重新拟合，也不允许把赛果
回流为车手能力标签。它把“配速归因是否稳定”“结果层能否解释赛季”以及
“概率区间是否校准”拆成独立门控，避免用单一积分榜相关系数宣布模型成立。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence


PROHIBITED_ABILITY_INPUTS = frozenset(
    {
        "actual_points",
        "championship_points",
        "finish_position",
        "finishing_position",
        "championship_position",
        "podiums",
        "wins",
        "constructor_position",
    }
)


@dataclass(frozen=True)
class GateResult:
    """一个可审计的发布门控。"""

    gate_id: str
    passed: bool
    value: Any
    threshold: Any
    role: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ObjectiveEvaluationReport:
    """历史验证、完整评分和 2026 发布状态的汇总。"""

    gates: list[GateResult]
    weak_identification_flags: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    module_coverage: Mapping[str, str] = field(default_factory=dict)

    @property
    def core_historical_passed(self) -> bool:
        core = [gate for gate in self.gates if gate.role == "core_historical"]
        return bool(core) and all(gate.passed for gate in core)

    @property
    def full_driver_card_ready(self) -> bool:
        required = {
            "PAC",
            "TYM",
            "EXT",
            "ATK",
            "DEF",
            "AWA",
            "STR",
            "ADP",
        }
        return self.core_historical_passed and all(
            self.module_coverage.get(module) == "validated" for module in required
        )

    @property
    def release_status(self) -> str:
        if self.full_driver_card_ready:
            return "full_modular_rating_ready"
        if self.core_historical_passed:
            return "historical_result_accounting_passed_full_driver_card_not_ready"
        return "research_only_failed_objectivity_gate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_status": self.release_status,
            "core_historical_passed": self.core_historical_passed,
            "full_driver_card_ready": self.full_driver_card_ready,
            "gates": [gate.to_dict() for gate in self.gates],
            "module_coverage": dict(self.module_coverage),
            "weak_identification_flags": list(self.weak_identification_flags),
            "limitations": list(self.limitations),
        }


def audit_ability_feature_leakage(
    feature_names: Iterable[str],
    prohibited: Iterable[str] = PROHIBITED_ABILITY_INPUTS,
) -> list[str]:
    """返回能力模型中命中的赛果泄漏字段，使用精确词与路径尾部匹配。"""

    prohibited_set = {str(value).strip().lower() for value in prohibited}
    hits: list[str] = []
    for raw_name in feature_names:
        name = str(raw_name).strip().lower()
        leaf = name.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
        if name in prohibited_set or leaf in prohibited_set:
            hits.append(str(raw_name))
    return sorted(set(hits))


def _all_at_least(rows: Sequence[Mapping[str, Any]], key: str, threshold: float) -> tuple[bool, float | None]:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return False, None
    return min(values) >= threshold, min(values)


def _metric(mapping: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def evaluate_objectivity(
    seasonal_result: Mapping[str, Any],
    bayesian_result: Mapping[str, Any],
    race_by_race_result: Mapping[str, Any],
    *,
    ability_feature_names: Iterable[str],
    module_coverage: Mapping[str, str],
    thresholds: Mapping[str, float] | None = None,
) -> ObjectiveEvaluationReport:
    """按冻结阈值评估历史客观性与 2026 发布资格。

    赛果相关只验证结果生成层的外部构念，不参与能力模块拟合。完整车手卡还
    必须逐模块达到 ``validated``，不能用较高的积分榜相关掩盖 ATK/DEF 等缺口。
    """

    limits = {
        "constructor_each_year_spearman": 0.80,
        "realized_driver_each_year_spearman": 0.80,
        "pooled_constructor_spearman": 0.85,
        "pooled_realized_driver_spearman": 0.85,
        "maximum_rhat": 1.05,
        "locked_mae_improvement_fraction": 0.10,
        "calibrated_95_coverage_low": 0.90,
        "calibrated_95_coverage_high": 0.99,
        "minimum_rolling_origins": 20.0,
        "minimum_2026_events": 8.0,
    }
    if thresholds:
        limits.update({key: float(value) for key, value in thresholds.items()})

    standings = seasonal_result.get("standings_calibration", {})
    constructor_rows = list(standings.get("constructor_by_year", []))
    driver_rows = list(standings.get("driver_by_year", []))
    constructor_ok, constructor_min = _all_at_least(
        constructor_rows,
        "spearman_car_z_vs_standings_position",
        limits["constructor_each_year_spearman"],
    )
    realized_ok, realized_min = _all_at_least(
        driver_rows,
        "spearman_realized_vs_standings",
        limits["realized_driver_each_year_spearman"],
    )

    leakage = audit_ability_feature_leakage(ability_feature_names)
    posterior_rhat = _metric(bayesian_result, ["posterior_diagnostics", "maximum_scale_rhat"])
    locked = _metric(bayesian_result, ["time_forward_validation", "years", "2025"])
    locked = locked if isinstance(locked, Mapping) else {}
    zero_mae = _metric(locked, ["zero", "event_balanced_mae_z"])
    model_mae = _metric(locked, ["bayesian_joint", "event_balanced_mae_z"])
    improvement = None
    if zero_mae not in (None, 0) and model_mae is not None:
        improvement = (float(zero_mae) - float(model_mae)) / float(zero_mae)
    coverage95 = _metric(locked, ["bayesian_joint", "calibrated_95_coverage"])
    rolling_origins = _metric(seasonal_result, ["rolling_origin", "n_origins"])
    observed_2026 = _metric(race_by_race_result, ["projection_2026", "observed_events"])
    pac_validation = seasonal_result.get("pac_time_forward_validation")
    pac_validation = pac_validation if isinstance(pac_validation, Mapping) else {}
    pac_locked_events = pac_validation.get("locked_events")
    pac_rank_stability = pac_validation.get("rank_spearman")
    pac_interval_coverage = pac_validation.get("interval_coverage")
    pac_gate_passed = bool(
        pac_locked_events is not None
        and float(pac_locked_events) >= limits["minimum_rolling_origins"]
        and pac_rank_stability is not None
        and float(pac_rank_stability) >= 0.70
        and pac_interval_coverage is not None
        and limits["calibrated_95_coverage_low"]
        <= float(pac_interval_coverage)
        <= limits["calibrated_95_coverage_high"]
    )

    pooled_constructor = standings.get("pooled_spearman_car_vs_constructors")
    pooled_realized = standings.get("pooled_spearman_realized_vs_drivers")
    gates = [
        GateResult(
            "ability_feature_leakage",
            not leakage,
            leakage,
            "no prohibited result fields",
            "core_historical",
            "积分、完赛名次和冠军身份不得进入长期能力输入。",
        ),
        GateResult(
            "constructor_construct_each_year",
            constructor_ok,
            constructor_min,
            limits["constructor_each_year_spearman"],
            "core_historical",
            "只验证车辆/结果层与制造商赛季结果方向，不定义车辆物理真值。",
        ),
        GateResult(
            "realized_driver_construct_each_year",
            realized_ok,
            realized_min,
            limits["realized_driver_each_year_spearman"],
            "core_historical",
            "车+手实跑组合应与车手赛季结果一致；纯车手能力不要求复制积分榜。",
        ),
        GateResult(
            "constructor_construct_pooled",
            pooled_constructor is not None
            and float(pooled_constructor) >= limits["pooled_constructor_spearman"],
            pooled_constructor,
            limits["pooled_constructor_spearman"],
            "core_historical",
            "跨赛季车辆结果构念检查。",
        ),
        GateResult(
            "realized_driver_construct_pooled",
            pooled_realized is not None
            and float(pooled_realized) >= limits["pooled_realized_driver_spearman"],
            pooled_realized,
            limits["pooled_realized_driver_spearman"],
            "core_historical",
            "跨赛季实跑组合结果构念检查。",
        ),
        GateResult(
            "posterior_convergence",
            posterior_rhat is not None and float(posterior_rhat) <= limits["maximum_rhat"],
            posterior_rhat,
            limits["maximum_rhat"],
            "core_historical",
            "只检查主层级尺度收敛，不替代后验预测检验。",
        ),
        GateResult(
            "locked_time_forward_improvement",
            improvement is not None
            and improvement >= limits["locked_mae_improvement_fraction"],
            improvement,
            limits["locked_mae_improvement_fraction"],
            "core_historical",
            "2025 锁定段必须相对零模型有实际改进。",
        ),
        GateResult(
            "locked_interval_calibration_95",
            coverage95 is not None
            and limits["calibrated_95_coverage_low"]
            <= float(coverage95)
            <= limits["calibrated_95_coverage_high"],
            coverage95,
            [
                limits["calibrated_95_coverage_low"],
                limits["calibrated_95_coverage_high"],
            ],
            "core_historical",
            "只接受时间外校准后的覆盖率。",
        ),
        GateResult(
            "rolling_origin_depth",
            rolling_origins is not None
            and float(rolling_origins) >= limits["minimum_rolling_origins"],
            rolling_origins,
            limits["minimum_rolling_origins"],
            "core_historical",
            "逐站前推 origin 数必须足够，不能只做一个赛季切分。",
        ),
        GateResult(
            "pac_driver_specific_time_forward",
            pac_gate_passed,
            {
                "locked_events": pac_locked_events,
                "rank_spearman": pac_rank_stability,
                "interval_coverage": pac_interval_coverage,
            },
            {
                "minimum_locked_events": limits["minimum_rolling_origins"],
                "minimum_rank_spearman": 0.70,
                "interval_coverage": [
                    limits["calibrated_95_coverage_low"],
                    limits["calibrated_95_coverage_high"],
                ],
            },
            "ability_module",
            "联合圈速预测通过不等于池化 driver_base_z 的 PAC 排序与区间已通过时间外验证。",
        ),
        GateResult(
            "2026_confirmatory_exposure",
            observed_2026 is not None
            and float(observed_2026) >= limits["minimum_2026_events"],
            observed_2026,
            limits["minimum_2026_events"],
            "future_release",
            "不足时仍可生成逐场探索报告，但不得发布确认性赛季评分。",
        ),
    ]

    transfer = seasonal_result.get("transfer_graph", {})
    flags: list[str] = []
    if not bool(transfer.get("connected", False)):
        flags.append(
            f"driver_team_graph_disconnected:{transfer.get('n_components', 'unknown')}_components"
        )
    effective_module_coverage = dict(module_coverage)
    if not pac_gate_passed:
        effective_module_coverage["PAC"] = "prototype"
    missing_modules = sorted(
        key for key, status in effective_module_coverage.items() if status != "validated"
    )
    if missing_modules:
        flags.append("unvalidated_driver_modules:" + ",".join(missing_modules))

    return ObjectiveEvaluationReport(
        gates=gates,
        module_coverage=effective_module_coverage,
        weak_identification_flags=flags,
        limitations=[
            "赛果相关性只验证结果生成层，不把赛果变成长期开车能力标签。",
            "转会图不连通时，孤立车队内部的车辆—车手分离依赖层级先验。",
            "2026 新规车辆响应、主动空力和能量代理必须按新 era 重置。",
            "当前 rolling-origin 验证的是联合圈速预测，不是池化 PAC 的车手级排序稳定性或区间覆盖。",
        ],
    )
