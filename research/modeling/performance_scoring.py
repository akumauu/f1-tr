"""车手表现评分与证据包的最小、可审计合同。

本模块刻意把六类对象分开：

``PerformanceScore``
    能力/表现的标准分，使用 ``75 + 10 × 收缩后 z``；
``RaceValue``
    比赛价值展示分，物理贡献零严格映射到 50，同时保留原始量纲；
``Evidence`` 与 ``Episode``
    观测证据及其支持的比赛情境，不把“没有机会”写成能力为零；
``StintDossier`` 与 ``SeasonProfile``
    stint 和赛季聚合容器，继续保持能力分与赛果价值分离。

这里不拟合模型，只负责把上游后验/区间转换成稳定合同，并在冻结场景阻止
积分、完赛名次等赛果字段进入 OVR。所有显示分都可逆追溯到 z 或物理量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping, Sequence


PERFORMANCE_BASE = 75.0
PERFORMANCE_POINTS_PER_Z = 10.0
RACE_VALUE_NEUTRAL = 50.0

CONFIDENCE_LEVELS = frozenset({"insufficient", "low", "medium", "high"})
DIRECTNESS_LEVELS = frozenset({"direct", "derived", "proxy", "model_based"})

# 仅检查明确的赛果叶字段或语义短语；逐圈 ``position`` 可作为情境证据，
# 但最终完赛位置、积分和冠军身份不能进入冻结能力 OVR。
PROHIBITED_OVR_RESULT_FIELDS = frozenset(
    {
        "actual_points",
        "championship_points",
        "points",
        "finish_position",
        "finishing_position",
        "final_position",
        "result_position",
        "championship_position",
        "constructor_position",
        "classification",
        "classification_position",
        "race_result",
        "result_rank",
        "wins",
        "win",
        "winner",
        "podiums",
        "podium",
        "finish_status",
        "dnf",
        "dns",
        "dsq",
        "fastest_lap",
    }
)

SIX_ATTRIBUTION_KEYS = (
    "vehicle_contribution",
    "driver_contribution",
    "interaction_contribution",
    "strategy_operations_contribution",
    "reliability_incident_contribution",
    "unexplained_contribution",
)


def _finite(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值")
    return number


def _non_empty(name: str, value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    return text


def _interval(name: str, value: Sequence[float], *, contains: float) -> tuple[float, float]:
    if len(value) != 2:
        raise ValueError(f"{name} 必须是 [low, high]")
    low = _finite(f"{name}.low", value[0])
    high = _finite(f"{name}.high", value[1])
    if low > high:
        raise ValueError(f"{name} 下界不能大于上界")
    tolerance = 1e-12 * max(1.0, abs(contains), abs(low), abs(high))
    if contains < low - tolerance or contains > high + tolerance:
        raise ValueError(f"{name} 必须包含点估计")
    return low, high


def _confidence_for_score(
    exposure: float,
    shrinkage_weight: float,
    raw_z_ci: tuple[float, float],
) -> tuple[str, tuple[str, ...]]:
    """用机会暴露、收缩权重和原始区间共同给出保守置信等级。"""

    width_z = raw_z_ci[1] - raw_z_ci[0]
    flags: list[str] = []
    if exposure <= 0:
        return "insufficient", ("no_exposure", "prior_only")
    if shrinkage_weight < 0.25:
        flags.append("strong_prior_shrinkage")
    if width_z > 2.0:
        flags.append("wide_interval")
    if shrinkage_weight >= 0.80 and width_z <= 1.0:
        return "high", tuple(flags)
    if shrinkage_weight >= 0.50 and width_z <= 2.0:
        return "medium", tuple(flags)
    return "low", tuple(flags)


@dataclass(frozen=True)
class PerformanceScore:
    """能力/表现标准分；显示分始终由收缩后 z 机械变换得到。"""

    metric: str
    raw_z: float
    raw_z_ci: tuple[float, float]
    shrunk_z: float
    shrunk_z_ci: tuple[float, float]
    score: float
    score_ci: tuple[float, float]
    exposure: float
    prior_exposure: float
    shrinkage_weight: float
    confidence: str
    confidence_flags: tuple[str, ...] = ()
    model_version: str | None = None
    data_cutoff: str | None = None
    source_already_shrunk: bool = False

    @property
    def posterior_z(self) -> float:
        """``shrunk_z`` 的概率建模语义别名。"""

        return self.shrunk_z

    @property
    def publishable(self) -> bool:
        return self.confidence != "insufficient" and self.exposure > 0

    @classmethod
    def from_z(
        cls,
        metric: str,
        z: float,
        z_ci: Sequence[float],
        *,
        exposure: float,
        prior_exposure: float = 20.0,
        model_version: str | None = None,
        data_cutoff: str | None = None,
        source_already_shrunk: bool = False,
    ) -> "PerformanceScore":
        """按机会暴露做确定性先验收缩，再应用 ``75 + 10z``。

        ``prior_exposure`` 是先验等效样本量。零暴露时权重为零、显示回到 75，
        但 ``publishable`` 为假，避免把先验均值误报为真实能力。
        """

        metric_text = _non_empty("metric", metric)
        raw_z = _finite("z", z)
        raw_ci = _interval("z_ci", z_ci, contains=raw_z)
        observed = _finite("exposure", exposure)
        prior = _finite("prior_exposure", prior_exposure)
        if observed < 0:
            raise ValueError("exposure 不能为负")
        if prior < 0:
            raise ValueError("prior_exposure 不能为负")
        denominator = observed + prior
        weight = (
            1.0
            if source_already_shrunk
            else observed / denominator if denominator > 0 else 0.0
        )
        shrunk_z = weight * raw_z
        shrunk_ci = (weight * raw_ci[0], weight * raw_ci[1])
        score = PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_z
        score_ci = (
            PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_ci[0],
            PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_ci[1],
        )
        confidence, flags = _confidence_for_score(observed, weight, raw_ci)
        if source_already_shrunk:
            flags = (*flags, "upstream_shrinkage_preserved")
        return cls(
            metric=metric_text,
            raw_z=raw_z,
            raw_z_ci=raw_ci,
            shrunk_z=shrunk_z,
            shrunk_z_ci=shrunk_ci,
            score=score,
            score_ci=score_ci,
            exposure=observed,
            prior_exposure=prior,
            shrinkage_weight=weight,
            confidence=confidence,
            confidence_flags=flags,
            model_version=model_version,
            data_cutoff=data_cutoff,
            source_already_shrunk=bool(source_already_shrunk),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "raw_z": self.raw_z,
            "raw_z_ci": list(self.raw_z_ci),
            "shrunk_z": self.shrunk_z,
            "shrunk_z_ci": list(self.shrunk_z_ci),
            "score": self.score,
            "score_ci": list(self.score_ci),
            "formula": "75 + 10 * shrunk_z",
            "exposure": self.exposure,
            "prior_exposure": self.prior_exposure,
            "shrinkage_weight": self.shrinkage_weight,
            "confidence": self.confidence,
            "confidence_flags": list(self.confidence_flags),
            "publishable": self.publishable,
            "model_version": self.model_version,
            "data_cutoff": self.data_cutoff,
            "source_already_shrunk": self.source_already_shrunk,
        }


def performance_score_from_z(
    metric: str,
    z: float,
    z_ci: Sequence[float],
    *,
    exposure: float,
    prior_exposure: float = 20.0,
    model_version: str | None = None,
    data_cutoff: str | None = None,
    source_already_shrunk: bool = False,
) -> PerformanceScore:
    """``PerformanceScore.from_z`` 的函数式入口。"""

    return PerformanceScore.from_z(
        metric,
        z,
        z_ci,
        exposure=exposure,
        prior_exposure=prior_exposure,
        model_version=model_version,
        data_cutoff=data_cutoff,
        source_already_shrunk=source_already_shrunk,
    )


@dataclass(frozen=True)
class RaceValue:
    """带原始物理量的比赛价值；零贡献固定显示为 50。"""

    value_function: str
    physical_value: float
    physical_ci: tuple[float, float]
    unit: str
    scale_per_10_points: float
    higher_is_better: bool
    score: float
    score_ci: tuple[float, float]
    confidence: str = "low"
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_physical(
        cls,
        value_function: str,
        physical_value: float,
        physical_ci: Sequence[float],
        *,
        unit: str,
        scale_per_10_points: float,
        higher_is_better: bool = True,
        confidence: str = "low",
        evidence_ids: Iterable[str] = (),
    ) -> "RaceValue":
        value_name = _non_empty("value_function", value_function)
        physical = _finite("physical_value", physical_value)
        physical_interval = _interval("physical_ci", physical_ci, contains=physical)
        unit_text = _non_empty("unit", unit)
        scale = _finite("scale_per_10_points", scale_per_10_points)
        if scale <= 0:
            raise ValueError("scale_per_10_points 必须大于零")
        if confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"未知 confidence：{confidence}")
        direction = 1.0 if higher_is_better else -1.0

        def transform(value: float) -> float:
            return RACE_VALUE_NEUTRAL + PERFORMANCE_POINTS_PER_Z * direction * value / scale

        score = transform(physical)
        transformed_bounds = tuple(transform(value) for value in physical_interval)
        score_ci = (min(transformed_bounds), max(transformed_bounds))
        return cls(
            value_function=value_name,
            physical_value=physical,
            physical_ci=physical_interval,
            unit=unit_text,
            scale_per_10_points=scale,
            higher_is_better=bool(higher_is_better),
            score=score,
            score_ci=score_ci,
            confidence=confidence,
            evidence_ids=tuple(str(value) for value in evidence_ids),
        )

    @property
    def neutral_physical_value(self) -> float:
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_function": self.value_function,
            "physical_value": self.physical_value,
            "physical_ci": list(self.physical_ci),
            "unit": self.unit,
            "neutral_physical_value": 0.0,
            "neutral_score": RACE_VALUE_NEUTRAL,
            "scale_per_10_points": self.scale_per_10_points,
            "higher_is_better": self.higher_is_better,
            "score": self.score,
            "score_ci": list(self.score_ci),
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
        }


def race_value_from_physical(
    value_function: str,
    physical_value: float,
    physical_ci: Sequence[float],
    *,
    unit: str,
    scale_per_10_points: float,
    higher_is_better: bool = True,
    confidence: str = "low",
    evidence_ids: Iterable[str] = (),
) -> RaceValue:
    return RaceValue.from_physical(
        value_function,
        physical_value,
        physical_ci,
        unit=unit,
        scale_per_10_points=scale_per_10_points,
        higher_is_better=higher_is_better,
        confidence=confidence,
        evidence_ids=evidence_ids,
    )


def _field_leaf(value: Any) -> str:
    text = str(value).strip()
    # 同时兼容 dotted path、JSON pointer、方括号和 camelCase。
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()
    return re.split(r"[./\[\]]+", text)[-1].strip("_ ")


def audit_ovr_result_fields(
    feature_names: Iterable[str],
    prohibited: Iterable[str] = PROHIBITED_OVR_RESULT_FIELDS,
) -> list[str]:
    """返回冻结 OVR 输入中明确命中的赛果字段。"""

    prohibited_set = {str(value).strip().lower() for value in prohibited}
    hits: list[str] = []
    for raw_name in feature_names:
        leaf = _field_leaf(raw_name)
        if leaf in prohibited_set:
            hits.append(str(raw_name))
    return sorted(set(hits))


def _weighted_interval(
    scores: Sequence[PerformanceScore],
    weights: Sequence[float],
    attribute: str,
) -> tuple[float, float]:
    intervals = [getattr(score, attribute) for score in scores]
    return (
        math.fsum(weight * interval[0] for weight, interval in zip(weights, intervals)),
        math.fsum(weight * interval[1] for weight, interval in zip(weights, intervals)),
    )


def build_overall_score(
    components: Mapping[str, PerformanceScore],
    weights: Mapping[str, float] | None = None,
    *,
    feature_names: Iterable[str] = (),
    frozen_scenario: bool = True,
    frozen: bool | None = None,
    scenario: str = "frozen_time_forward",
    model_version: str | None = None,
    data_cutoff: str | None = None,
) -> PerformanceScore:
    """从能力模块构造 OVR，并在冻结场景拒绝赛果字段。

    输入组件必须已经各自完成收缩和区间估计；OVR 只做透明加权，不再次
    收缩。区间采用端点线性传播，置信等级取所有正权重组件中的最低等级。
    """

    is_frozen = bool(frozen_scenario if frozen is None else frozen)
    names = tuple(str(name) for name in feature_names)
    if is_frozen:
        leakage = audit_ovr_result_fields((*names, *components.keys()))
        if leakage:
            raise ValueError(f"冻结场景 OVR 禁止赛果字段：{leakage}")
    if not components:
        raise ValueError("OVR 至少需要一个能力组件")
    for name, score in components.items():
        _non_empty("component name", name)
        if not isinstance(score, PerformanceScore):
            raise TypeError(f"OVR 组件 {name} 必须是 PerformanceScore")

    if weights is None:
        raw_weights = {name: 1.0 for name in components}
    else:
        unknown = sorted(set(weights) - set(components))
        missing = sorted(set(components) - set(weights))
        if unknown or missing:
            raise ValueError(f"OVR 权重键必须与组件一致；missing={missing}, unknown={unknown}")
        raw_weights = {name: _finite(f"weight[{name}]", weights[name]) for name in components}
    if any(value < 0 for value in raw_weights.values()):
        raise ValueError("OVR 权重不能为负")
    total_weight = math.fsum(raw_weights.values())
    if total_weight <= 0:
        raise ValueError("OVR 至少需要一个正权重组件")

    ordered_names = list(components)
    ordered_scores = [components[name] for name in ordered_names]
    normalized = [raw_weights[name] / total_weight for name in ordered_names]
    insufficient = [
        name
        for name, score, weight in zip(ordered_names, ordered_scores, normalized)
        if weight > 0 and (score.confidence == "insufficient" or not score.publishable)
    ]
    if insufficient:
        raise ValueError(
            f"OVR 禁止用缺失/零暴露能力模块填充分数：{insufficient}"
        )
    raw_z = math.fsum(weight * score.raw_z for weight, score in zip(normalized, ordered_scores))
    shrunk_z = math.fsum(
        weight * score.shrunk_z for weight, score in zip(normalized, ordered_scores)
    )
    raw_ci = _weighted_interval(ordered_scores, normalized, "raw_z_ci")
    shrunk_ci = _weighted_interval(ordered_scores, normalized, "shrunk_z_ci")
    exposure = math.fsum(
        weight * score.exposure for weight, score in zip(normalized, ordered_scores)
    )
    prior_exposure = math.fsum(
        weight * score.prior_exposure for weight, score in zip(normalized, ordered_scores)
    )
    shrinkage = math.fsum(
        weight * score.shrinkage_weight for weight, score in zip(normalized, ordered_scores)
    )
    rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    confidence = min(
        (score.confidence for score, weight in zip(ordered_scores, normalized) if weight > 0),
        key=rank.__getitem__,
    )
    flags = ["ability_modules_only", f"scenario:{_non_empty('scenario', scenario)}"]
    if is_frozen:
        flags.append("frozen_result_fields_rejected")
    return PerformanceScore(
        metric="OVR",
        raw_z=raw_z,
        raw_z_ci=raw_ci,
        shrunk_z=shrunk_z,
        shrunk_z_ci=shrunk_ci,
        score=PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_z,
        score_ci=(
            PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_ci[0],
            PERFORMANCE_BASE + PERFORMANCE_POINTS_PER_Z * shrunk_ci[1],
        ),
        exposure=exposure,
        prior_exposure=prior_exposure,
        shrinkage_weight=shrinkage,
        confidence=confidence,
        confidence_flags=tuple(flags),
        model_version=model_version,
        data_cutoff=data_cutoff,
        source_already_shrunk=True,
    )


# 兼容产品文案中的两个常用入口名，仍返回同一个 PerformanceScore 合同。
build_overall_rating = build_overall_score
build_ovr = build_overall_score
OVR = build_overall_score


def shapley_interaction_closure(
    value_neither: float,
    value_vehicle_only: float,
    value_driver_only: float,
    value_both: float,
    *,
    tolerance: float = 1e-10,
) -> dict[str, float | bool]:
    """两方 Shapley 分配，并显式保留非加性交互与闭合误差。"""

    v00 = _finite("value_neither", value_neither)
    v10 = _finite("value_vehicle_only", value_vehicle_only)
    v01 = _finite("value_driver_only", value_driver_only)
    v11 = _finite("value_both", value_both)
    allowed_error = _finite("tolerance", tolerance)
    if allowed_error < 0:
        raise ValueError("tolerance 不能为负")
    vehicle_main = v10 - v00
    driver_main = v01 - v00
    interaction = v11 - v10 - v01 + v00
    vehicle_shapley = vehicle_main + 0.5 * interaction
    driver_shapley = driver_main + 0.5 * interaction
    total_gain = v11 - v00
    reconstructed = math.fsum((vehicle_shapley, driver_shapley))
    closure_error = reconstructed - total_gain
    return {
        "baseline": v00,
        "vehicle_main": vehicle_main,
        "driver_main": driver_main,
        "interaction": interaction,
        "vehicle_shapley": vehicle_shapley,
        "driver_shapley": driver_shapley,
        "total_gain": total_gain,
        "reconstructed_total": reconstructed,
        "closure_error": closure_error,
        "closed": abs(closure_error) <= allowed_error,
    }


shapley_two_party = shapley_interaction_closure


def close_six_attribution_components(
    contributions: Mapping[str, float],
    *,
    tolerance: float = 1e-10,
) -> dict[str, float | bool]:
    """校验六项有符号贡献，并给出交互对称分配后的闭合视图。"""

    missing = sorted(set(SIX_ATTRIBUTION_KEYS) - set(contributions))
    extra = sorted(set(contributions) - set(SIX_ATTRIBUTION_KEYS))
    if missing or extra:
        raise ValueError(f"六项归因字段不完整；missing={missing}, extra={extra}")
    values = {key: _finite(key, contributions[key]) for key in SIX_ATTRIBUTION_KEYS}
    interaction_half = 0.5 * values["interaction_contribution"]
    vehicle_shapley = values["vehicle_contribution"] + interaction_half
    driver_shapley = values["driver_contribution"] + interaction_half
    signed_total = math.fsum(values.values())
    shapley_total = math.fsum(
        (
            vehicle_shapley,
            driver_shapley,
            values["strategy_operations_contribution"],
            values["reliability_incident_contribution"],
            values["unexplained_contribution"],
        )
    )
    closure_error = shapley_total - signed_total
    result: dict[str, float | bool] = dict(values)
    result.update(
        {
            "vehicle_shapley_with_half_interaction": vehicle_shapley,
            "driver_shapley_with_half_interaction": driver_shapley,
            "signed_total": signed_total,
            "shapley_total": shapley_total,
            "closure_error": closure_error,
            "closed": abs(closure_error) <= _finite("tolerance", tolerance),
        }
    )
    return result


@dataclass(frozen=True)
class Evidence:
    """一个可定位、带直接性和局限的证据单元。"""

    evidence_id: str
    source: str
    payload: Mapping[str, Any]
    directness_level: str = "derived"
    confidence: str = "low"
    source_ref: str | None = None
    data_hash: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _non_empty("evidence_id", self.evidence_id))
        object.__setattr__(self, "source", _non_empty("source", self.source))
        if self.directness_level not in DIRECTNESS_LEVELS:
            raise ValueError(f"未知 directness_level：{self.directness_level}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"未知 confidence：{self.confidence}")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload 必须是 Mapping")
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "limitations", tuple(str(value) for value in self.limitations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "payload": dict(self.payload),
            "directness_level": self.directness_level,
            "confidence": self.confidence,
            "source_ref": self.source_ref,
            "data_hash": self.data_hash,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class Episode:
    """一个比赛情境及其机会暴露；观测结果不等同于长期技能。"""

    episode_id: str
    episode_type: str
    driver: str
    event_id: str
    lap_start: int
    lap_end: int
    opportunity: bool
    observed_outcome: str
    exposure: float
    evidence: tuple[Evidence, ...] = ()
    team: str | None = None
    confidence: str = "low"
    alternative_explanations: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("episode_id", "episode_type", "driver", "event_id"):
            object.__setattr__(self, name, _non_empty(name, getattr(self, name)))
        start, end = int(self.lap_start), int(self.lap_end)
        if start < 0 or end < start:
            raise ValueError("Episode 圈号必须满足 0 <= lap_start <= lap_end")
        observed_exposure = _finite("Episode.exposure", self.exposure)
        if observed_exposure < 0:
            raise ValueError("Episode.exposure 不能为负")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"未知 confidence：{self.confidence}")
        evidence = tuple(self.evidence)
        if any(not isinstance(item, Evidence) for item in evidence):
            raise TypeError("Episode.evidence 只能包含 Evidence")
        object.__setattr__(self, "lap_start", start)
        object.__setattr__(self, "lap_end", end)
        object.__setattr__(self, "exposure", observed_exposure)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(
            self,
            "alternative_explanations",
            tuple(str(value) for value in self.alternative_explanations),
        )
        object.__setattr__(
            self, "forbidden_claims", tuple(str(value) for value in self.forbidden_claims)
        )

    @property
    def scoreable(self) -> bool:
        """没有真实机会暴露时不得进入能力分母。"""

        return bool(self.opportunity and self.exposure > 0 and self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "episode_type": self.episode_type,
            "driver": self.driver,
            "team": self.team,
            "event_id": self.event_id,
            "lap_start": self.lap_start,
            "lap_end": self.lap_end,
            "opportunity": self.opportunity,
            "observed_outcome": self.observed_outcome,
            "exposure": self.exposure,
            "scoreable": self.scoreable,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "alternative_explanations": list(self.alternative_explanations),
            "forbidden_claims": list(self.forbidden_claims),
        }


@dataclass(frozen=True)
class StintDossier:
    """单个 stint 的物理量、评分、RaceValue、episode 与证据包。"""

    dossier_id: str
    season: int
    event_id: str
    driver: str
    team: str
    stint_number: int
    lap_start: int
    lap_end: int
    compound: str
    physical_metrics: Mapping[str, Any] = field(default_factory=dict)
    performance_scores: tuple[PerformanceScore, ...] = ()
    race_values: tuple[RaceValue, ...] = ()
    episodes: tuple[Episode, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    data_coverage: float = 0.0
    confidence_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("dossier_id", "event_id", "driver", "team", "compound"):
            object.__setattr__(self, name, _non_empty(name, getattr(self, name)))
        if int(self.season) < 1950:
            raise ValueError("season 不在有效 F1 年份范围")
        if int(self.stint_number) < 0:
            raise ValueError("stint_number 不能为负")
        if int(self.lap_start) < 0 or int(self.lap_end) < int(self.lap_start):
            raise ValueError("StintDossier 圈号范围无效")
        coverage = _finite("data_coverage", self.data_coverage)
        if not 0.0 <= coverage <= 1.0:
            raise ValueError("data_coverage 必须位于 [0, 1]")
        scores = tuple(self.performance_scores)
        values = tuple(self.race_values)
        episodes = tuple(self.episodes)
        evidence = tuple(self.evidence)
        if any(not isinstance(item, PerformanceScore) for item in scores):
            raise TypeError("performance_scores 只能包含 PerformanceScore")
        if any(not isinstance(item, RaceValue) for item in values):
            raise TypeError("race_values 只能包含 RaceValue")
        if any(not isinstance(item, Episode) for item in episodes):
            raise TypeError("episodes 只能包含 Episode")
        if any(not isinstance(item, Evidence) for item in evidence):
            raise TypeError("evidence 只能包含 Evidence")
        for episode in episodes:
            if episode.driver != self.driver or episode.event_id != self.event_id:
                raise ValueError("StintDossier 内 Episode 必须属于同一车手和分站")
            if episode.lap_start < self.lap_start or episode.lap_end > self.lap_end:
                raise ValueError("StintDossier 内 Episode 圈号必须落在 stint 范围内")
        object.__setattr__(self, "season", int(self.season))
        object.__setattr__(self, "stint_number", int(self.stint_number))
        object.__setattr__(self, "lap_start", int(self.lap_start))
        object.__setattr__(self, "lap_end", int(self.lap_end))
        object.__setattr__(self, "physical_metrics", dict(self.physical_metrics))
        object.__setattr__(self, "performance_scores", scores)
        object.__setattr__(self, "race_values", values)
        object.__setattr__(self, "episodes", episodes)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "data_coverage", coverage)
        object.__setattr__(
            self, "confidence_flags", tuple(str(value) for value in self.confidence_flags)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier_id": self.dossier_id,
            "season": self.season,
            "event_id": self.event_id,
            "driver": self.driver,
            "team": self.team,
            "stint_number": self.stint_number,
            "lap_start": self.lap_start,
            "lap_end": self.lap_end,
            "compound": self.compound,
            "physical_metrics": dict(self.physical_metrics),
            "performance_scores": [item.to_dict() for item in self.performance_scores],
            "race_values": [item.to_dict() for item in self.race_values],
            "episodes": [item.to_dict() for item in self.episodes],
            "evidence": [item.to_dict() for item in self.evidence],
            "data_coverage": self.data_coverage,
            "confidence_flags": list(self.confidence_flags),
        }


@dataclass(frozen=True)
class SeasonProfile:
    """赛季能力画像；OVR 与赛果价值继续作为不同字段发布。"""

    profile_id: str
    season: int
    driver: str
    scenario: str
    overall: PerformanceScore | None = None
    performance_scores: tuple[PerformanceScore, ...] = ()
    race_values: tuple[RaceValue, ...] = ()
    stint_dossiers: tuple[StintDossier, ...] = ()
    episodes: tuple[Episode, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    ability_feature_names: tuple[str, ...] = ()
    event_exposure: float = 0.0
    frozen_scenario: bool = True
    release_status: str = "research_only"
    confidence_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("profile_id", "driver", "scenario", "release_status"):
            object.__setattr__(self, name, _non_empty(name, getattr(self, name)))
        if int(self.season) < 1950:
            raise ValueError("season 不在有效 F1 年份范围")
        exposure = _finite("event_exposure", self.event_exposure)
        if exposure < 0:
            raise ValueError("event_exposure 不能为负")
        feature_names = tuple(str(value) for value in self.ability_feature_names)
        if self.frozen_scenario:
            leakage = audit_ovr_result_fields(feature_names)
            if leakage:
                raise ValueError(f"冻结场景 SeasonProfile 禁止 OVR 赛果字段：{leakage}")
        if self.overall is not None:
            if not isinstance(self.overall, PerformanceScore):
                raise TypeError("overall 必须是 PerformanceScore")
            if self.overall.metric.upper() != "OVR":
                raise ValueError("overall.metric 必须是 OVR")
        scores = tuple(self.performance_scores)
        values = tuple(self.race_values)
        dossiers = tuple(self.stint_dossiers)
        episodes = tuple(self.episodes)
        evidence = tuple(self.evidence)
        expected_types = (
            (scores, PerformanceScore, "performance_scores"),
            (values, RaceValue, "race_values"),
            (dossiers, StintDossier, "stint_dossiers"),
            (episodes, Episode, "episodes"),
            (evidence, Evidence, "evidence"),
        )
        for items, expected, field_name in expected_types:
            if any(not isinstance(item, expected) for item in items):
                raise TypeError(f"{field_name} 包含错误对象类型")
        if any(dossier.driver != self.driver or dossier.season != int(self.season) for dossier in dossiers):
            raise ValueError("SeasonProfile 内 StintDossier 必须属于同一车手和赛季")
        if any(episode.driver != self.driver for episode in episodes):
            raise ValueError("SeasonProfile 内 Episode 必须属于同一车手")
        object.__setattr__(self, "season", int(self.season))
        object.__setattr__(self, "event_exposure", exposure)
        object.__setattr__(self, "ability_feature_names", feature_names)
        object.__setattr__(self, "performance_scores", scores)
        object.__setattr__(self, "race_values", values)
        object.__setattr__(self, "stint_dossiers", dossiers)
        object.__setattr__(self, "episodes", episodes)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(
            self, "confidence_flags", tuple(str(value) for value in self.confidence_flags)
        )

    @property
    def module_scores(self) -> tuple[PerformanceScore, ...]:
        return self.performance_scores

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "season": self.season,
            "driver": self.driver,
            "scenario": self.scenario,
            "frozen_scenario": self.frozen_scenario,
            "overall": None if self.overall is None else self.overall.to_dict(),
            "performance_scores": [item.to_dict() for item in self.performance_scores],
            "race_values": [item.to_dict() for item in self.race_values],
            "stint_dossiers": [item.to_dict() for item in self.stint_dossiers],
            "episodes": [item.to_dict() for item in self.episodes],
            "evidence": [item.to_dict() for item in self.evidence],
            "ability_feature_names": list(self.ability_feature_names),
            "event_exposure": self.event_exposure,
            "release_status": self.release_status,
            "confidence_flags": list(self.confidence_flags),
        }


__all__ = [
    "CONFIDENCE_LEVELS",
    "DIRECTNESS_LEVELS",
    "Evidence",
    "Episode",
    "OVR",
    "PERFORMANCE_BASE",
    "PERFORMANCE_POINTS_PER_Z",
    "PROHIBITED_OVR_RESULT_FIELDS",
    "PerformanceScore",
    "RACE_VALUE_NEUTRAL",
    "RaceValue",
    "SIX_ATTRIBUTION_KEYS",
    "SeasonProfile",
    "StintDossier",
    "audit_ovr_result_fields",
    "build_overall_rating",
    "build_overall_score",
    "build_ovr",
    "close_six_attribution_components",
    "performance_score_from_z",
    "race_value_from_physical",
    "shapley_interaction_closure",
    "shapley_two_party",
]
