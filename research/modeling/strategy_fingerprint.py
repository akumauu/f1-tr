"""2026 公开遥测的中性操作模式指纹。

本模块从逐圈直道派生特征中识别重复出现的观测模式。输出标签只描述公开
信号的形状，不等价于电池荷电状态、ERS-K 实际功率、主动翼位置或
Overtake/Boost 按钮状态。聚类不读取积分、名次或比赛结果标签。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


MODE_LABELS = (
    "early_gain_mode",
    "late_gain_mode",
    "balanced_mode",
    "save_like_mode",
    "high_response_mode",
)

DEFAULT_FEATURE_COLUMNS = (
    "early_accel_mps2",
    "late_accel_mps2",
    "early_speed_residual_kmh",
    "late_speed_residual_kmh",
    "full_throttle_response_mps2",
    "speed_plateau_fraction",
    "lift_coast_fraction",
    "full_throttle_fraction",
    "throttle_modulation_per_100m",
    "high_rpm_dwell_fraction",
)

FORBIDDEN_CLAIMS = (
    "确认电池 SOC、剩余能量或每圈能量收支",
    "确认 ERS-K 实际功率、回收功率或部署功率",
    "确认主动翼处于 Corner/Straight Mode 或具体翼面位置",
    "确认 Overtake、Boost 或其他车手按钮状态",
    "把模式差异直接断言为车辆故障、损伤或设定原因",
)

ALTERNATIVE_EXPLANATIONS = (
    "出弯初速、尾流和前车距离改变了直道速度曲线",
    "燃油、轮胎、天气与赛道演化未被公开频道完全观测",
    "车手操作、车辆设定与单车状态可能产生相似指纹",
    "采样频率、距离插值和公开频道缺口可能改变派生特征",
)

MODE_INTERPRETATIONS = {
    "early_gain_mode": "符合直道早段收益相对集中的公开遥测模式",
    "late_gain_mode": "符合直道后段仍保留相对收益的公开遥测模式",
    "balanced_mode": "符合直道前后段收益较均衡的公开遥测模式",
    "save_like_mode": "符合较多收油/滑行或较低全油门暴露的公开信号组合",
    "high_response_mode": "符合全油门响应、高转驻留或高速平台较强的公开信号组合",
}

_RESULT_LABEL_TOKENS = (
    "points",
    "standings",
    "championship",
    "classification",
    "race_result",
    "finish_position",
    "finishing_position",
    "podium",
    "winner",
    "expected_points",
    "race_value",
    "lap_time",
    "lap_duration",
    "delta_to_leader",
)


@dataclass(frozen=True)
class StraightFeatureConfig:
    """从距离域采样提取逐圈直道特征的固定合同。"""

    group_columns: tuple[str, ...] = (
        "year",
        "meeting",
        "session",
        "team",
        "driver_acronym",
        "driver_number",
        "lap",
    )
    distance_column: str = "distance"
    speed_column: str = "speed"
    phase_column: str = "track_phase"
    segment_column: str = "track_segment_id"
    reference_speed_column: str = "phase_reference_speed_kmh"
    throttle_column: str = "throttle"
    brake_column: str = "brake"
    gear_column: str = "gear"
    rpm_column: str = "rpm"
    minimum_samples: int = 30
    minimum_straight_segments: int = 2
    minimum_straight_distance_m: float = 300.0
    minimum_segment_distance_m: float = 30.0


@dataclass(frozen=True)
class StrategyFingerprintResult:
    """逐圈聚类结果以及解释边界。"""

    status: str
    reason: str
    method: str
    feature_columns: tuple[str, ...]
    assignments: pd.DataFrame = field(repr=False)
    context_audit: tuple[Mapping[str, Any], ...] = ()
    directness_level: str = "public_telemetry_proxy_not_direct_state"
    forbidden_claims: tuple[str, ...] = FORBIDDEN_CLAIMS
    alternative_explanations: tuple[str, ...] = ALTERNATIVE_EXPLANATIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "method": self.method,
            "feature_columns": list(self.feature_columns),
            "assignments": self.assignments.to_dict(orient="records"),
            "context_audit": [dict(item) for item in self.context_audit],
            "directness_level": self.directness_level,
            "forbidden_claims": list(self.forbidden_claims),
            "alternative_explanations": list(self.alternative_explanations),
        }


def _required(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{missing}")


def _numeric(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)


def _normalized_control(series: pd.Series) -> np.ndarray:
    """兼容 0-1 与 0-100 的油门/制动公开编码。"""

    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(float).to_numpy(dtype=float)
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size and float(np.nanquantile(np.abs(finite), 0.95)) > 1.5:
        values = values / 100.0
    return np.clip(values, 0.0, 1.0)


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    value_array = np.asarray(values, dtype=float)
    weight_array = np.asarray(weights, dtype=float)
    valid = np.isfinite(value_array) & np.isfinite(weight_array) & (weight_array > 0.0)
    if not valid.any():
        return float("nan")
    return float(np.average(value_array[valid], weights=weight_array[valid]))


def _segment_features(group: pd.DataFrame, config: StraightFeatureConfig) -> dict[str, float] | None:
    work = group.sort_values(config.distance_column, kind="stable").copy()
    work[config.distance_column] = pd.to_numeric(work[config.distance_column], errors="coerce")
    work[config.speed_column] = pd.to_numeric(work[config.speed_column], errors="coerce")
    work = work.dropna(subset=[config.distance_column, config.speed_column])
    work = work.groupby(config.distance_column, observed=True, sort=True).first().reset_index()
    if len(work) < 4:
        return None

    distance = _numeric(work[config.distance_column])
    speed_kmh = _numeric(work[config.speed_column])
    length = float(distance[-1] - distance[0])
    if not np.isfinite(length) or length < config.minimum_segment_distance_m:
        return None
    progress = (distance - distance[0]) / length
    speed_mps = speed_kmh / 3.6
    with np.errstate(divide="ignore", invalid="ignore"):
        acceleration = speed_mps * np.gradient(speed_mps, distance)
    acceleration[~np.isfinite(acceleration)] = np.nan
    early = progress <= 0.40
    late = progress >= 0.60

    throttle = (
        _normalized_control(work[config.throttle_column])
        if config.throttle_column in work
        else np.full(len(work), np.nan)
    )
    brake = (
        _normalized_control(work[config.brake_column])
        if config.brake_column in work
        else np.zeros(len(work), dtype=float)
    )
    full_throttle = np.isfinite(throttle) & (throttle >= 0.97)
    stable_gear = np.ones(len(work), dtype=bool)
    if config.gear_column in work:
        gear = _numeric(work[config.gear_column])
        stable_gear[1:] = np.isfinite(gear[1:]) & np.isfinite(gear[:-1]) & (gear[1:] == gear[:-1])

    reference_residual = np.full(len(work), np.nan)
    if config.reference_speed_column in work:
        reference_residual = speed_kmh - _numeric(work[config.reference_speed_column])

    late_acceleration = acceleration[late]
    plateau = (
        float(np.mean(np.abs(late_acceleration[np.isfinite(late_acceleration)]) <= 0.30))
        if np.isfinite(late_acceleration).any()
        else float("nan")
    )
    high_speed = speed_kmh >= 0.75 * float(np.nanmax(speed_kmh))
    lift_coast = np.isfinite(throttle) & (throttle <= 0.25) & (brake <= 0.05) & high_speed

    high_rpm_fraction = float("nan")
    if config.rpm_column in work:
        rpm = _numeric(work[config.rpm_column])
        finite_rpm = rpm[np.isfinite(rpm)]
        if finite_rpm.size and float(np.nanmax(finite_rpm)) > 0.0:
            high_rpm_fraction = float(np.mean(rpm[np.isfinite(rpm)] >= 0.92 * np.nanmax(finite_rpm)))

    throttle_modulation = float("nan")
    finite_throttle = throttle[np.isfinite(throttle)]
    if finite_throttle.size >= 2:
        throttle_modulation = float(np.sum(np.abs(np.diff(finite_throttle))) / length * 100.0)

    response_mask = full_throttle & stable_gear & np.isfinite(acceleration)
    return {
        "length_m": length,
        "samples": float(len(work)),
        "early_accel_mps2": float(np.nanmedian(acceleration[early])),
        "late_accel_mps2": float(np.nanmedian(acceleration[late])),
        "early_speed_residual_kmh": float(np.nanmedian(reference_residual[early])),
        "late_speed_residual_kmh": float(np.nanmedian(reference_residual[late])),
        "full_throttle_response_mps2": (
            float(np.nanmedian(acceleration[response_mask])) if response_mask.any() else float("nan")
        ),
        "speed_plateau_fraction": plateau,
        "lift_coast_fraction": float(np.mean(lift_coast)),
        "full_throttle_fraction": (
            float(np.mean(full_throttle[np.isfinite(throttle)]))
            if np.isfinite(throttle).any()
            else float("nan")
        ),
        "throttle_modulation_per_100m": throttle_modulation,
        "high_rpm_dwell_fraction": high_rpm_fraction,
    }


def derive_strategy_lap_features(
    samples: pd.DataFrame,
    *,
    config: StraightFeatureConfig | None = None,
) -> pd.DataFrame:
    """从已由冻结 ``TrackPhaseMap`` 标注的采样生成逐圈操作代理。

    ``acceleration`` 由速度与距离差分得到，只作为低频一致性特征；本函数不
    使用结果、积分、名次或圈时作为输入特征。
    """

    cfg = config or StraightFeatureConfig()
    groups = [column for column in cfg.group_columns if column in samples.columns]
    _required(
        samples,
        {cfg.distance_column, cfg.speed_column, cfg.phase_column, cfg.segment_column},
        "策略指纹采样",
    )
    if not groups:
        raise ValueError("策略指纹采样缺少逐圈分组字段")

    frame = samples.loc[
        samples[cfg.phase_column].astype(str).str.lower().eq("straight")
    ].copy()
    rows: list[dict[str, Any]] = []
    for key, lap in frame.groupby(groups, dropna=False, observed=True, sort=True):
        keys = key if isinstance(key, tuple) else (key,)
        row: dict[str, Any] = dict(zip(groups, keys))
        segment_rows: list[dict[str, float]] = []
        for _, segment in lap.groupby(cfg.segment_column, dropna=True, observed=True, sort=True):
            derived = _segment_features(segment, cfg)
            if derived is not None:
                segment_rows.append(derived)
        weights = [item["length_m"] for item in segment_rows]
        for feature in DEFAULT_FEATURE_COLUMNS:
            row[feature] = _weighted_mean(
                [item.get(feature, float("nan")) for item in segment_rows], weights
            )
        row["straight_distance_m"] = float(np.sum(weights)) if weights else 0.0
        row["straight_segments"] = len(segment_rows)
        row["feature_sample_count"] = int(sum(item["samples"] for item in segment_rows))
        row["feature_coverage"] = float(
            np.mean([np.isfinite(row[feature]) for feature in DEFAULT_FEATURE_COLUMNS])
        )
        enough = (
            row["feature_sample_count"] >= cfg.minimum_samples
            and row["straight_segments"] >= cfg.minimum_straight_segments
            and row["straight_distance_m"] >= cfg.minimum_straight_distance_m
        )
        row["feature_status"] = "ok" if enough else "insufficient"
        row["directness_level"] = "derived_from_public_speed_distance_controls"
        rows.append(row)

    columns = [
        *groups,
        *DEFAULT_FEATURE_COLUMNS,
        "straight_distance_m",
        "straight_segments",
        "feature_sample_count",
        "feature_coverage",
        "feature_status",
        "directness_level",
    ]
    return pd.DataFrame(rows, columns=columns)


def _contains_result_label(feature_columns: Sequence[str]) -> list[str]:
    contaminated: list[str] = []
    for column in feature_columns:
        normalized = str(column).strip().lower()
        if normalized == "position" or any(token in normalized for token in _RESULT_LABEL_TOKENS):
            contaminated.append(str(column))
    return sorted(set(contaminated))


def _manual_standardize(values: np.ndarray) -> np.ndarray:
    mean = np.mean(values, axis=0)
    scale = np.std(values, axis=0)
    scale[~np.isfinite(scale) | (scale <= 1e-12)] = 1.0
    return (values - mean) / scale


def _fallback_kmeans(values: np.ndarray, clusters: int) -> tuple[np.ndarray, np.ndarray]:
    """无 sklearn 时使用确定性最远点初始化的 Lloyd 退化实现。"""

    norms = np.sum(values * values, axis=1)
    chosen = [int(np.argmax(norms))]
    while len(chosen) < clusters:
        distance = np.min(
            np.stack([np.sum((values - values[index]) ** 2, axis=1) for index in chosen]),
            axis=0,
        )
        distance[chosen] = -1.0
        chosen.append(int(np.argmax(distance)))
    centers = values[chosen].copy()
    labels = np.zeros(len(values), dtype=int)
    for _ in range(100):
        distances = np.stack([np.sum((values - center) ** 2, axis=1) for center in centers], axis=1)
        new_labels = np.argmin(distances, axis=1)
        new_centers = centers.copy()
        for cluster in range(clusters):
            members = values[new_labels == cluster]
            if len(members):
                new_centers[cluster] = np.mean(members, axis=0)
        if np.array_equal(new_labels, labels) and np.allclose(new_centers, centers):
            labels, centers = new_labels, new_centers
            break
        labels, centers = new_labels, new_centers
    return labels, centers


def _canonicalize(labels: np.ndarray, centers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = sorted(range(len(centers)), key=lambda index: tuple(np.round(centers[index], 12)))
    remap = {old: new for new, old in enumerate(order)}
    return np.asarray([remap[int(label)] for label in labels], dtype=int), centers[order]


def _cluster(
    values: np.ndarray,
    clusters: int,
    *,
    random_seed: int,
    prefer_sklearn: bool,
) -> tuple[np.ndarray, np.ndarray, str]:
    if prefer_sklearn:
        try:
            from sklearn.cluster import KMeans
            from sklearn.preprocessing import StandardScaler

            standardized = StandardScaler().fit_transform(values)
            model = KMeans(n_clusters=clusters, random_state=random_seed, n_init=20)
            labels = model.fit_predict(standardized)
            labels, centers = _canonicalize(labels, model.cluster_centers_)
            return labels, centers, "sklearn_kmeans_standard_scaler"
        except (ImportError, ModuleNotFoundError):
            pass
    standardized = _manual_standardize(values)
    labels, centers = _fallback_kmeans(standardized, clusters)
    labels, centers = _canonicalize(labels, centers)
    return labels, centers, "deterministic_fallback_kmeans"


def _mean_matching(center: np.ndarray, names: Sequence[str], tokens: Sequence[str]) -> float:
    indexes = [
        index
        for index, name in enumerate(names)
        if any(token in name.lower() for token in tokens)
    ]
    return float(np.mean(center[indexes])) if indexes else 0.0


def _mode_scores(center: np.ndarray, names: Sequence[str]) -> dict[str, float]:
    early = _mean_matching(center, names, ("early",))
    late = _mean_matching(center, names, ("late",))
    response = _mean_matching(center, names, ("response", "rpm", "plateau", "full_throttle"))
    save = _mean_matching(center, names, ("lift", "coast", "modulation", "save"))
    return {
        "early_gain_mode": early - 0.55 * late,
        "late_gain_mode": late - 0.55 * early,
        "balanced_mode": -float(np.mean(np.abs(center))),
        "save_like_mode": save - 0.50 * response,
        "high_response_mode": response - 0.50 * save,
    }


def _eligible_modes(names: Sequence[str]) -> tuple[str, ...]:
    lowered = [name.lower() for name in names]
    eligible = ["balanced_mode"]
    if any("early" in name for name in lowered) and any("late" in name for name in lowered):
        eligible[0:0] = ["early_gain_mode", "late_gain_mode"]
    if any(any(token in name for token in ("lift", "coast", "modulation", "save")) for name in lowered):
        eligible.append("save_like_mode")
    if any(any(token in name for token in ("response", "rpm", "plateau", "full_throttle")) for name in lowered):
        eligible.append("high_response_mode")
    return tuple(mode for mode in MODE_LABELS if mode in eligible)


def _assign_mode_names(centers: np.ndarray, names: Sequence[str]) -> dict[int, str]:
    eligible = _eligible_modes(names)
    if len(eligible) < len(centers):
        eligible = MODE_LABELS
    score_rows = [_mode_scores(center, names) for center in centers]
    best_labels: tuple[str, ...] | None = None
    best_score = -np.inf
    for labels in permutations(eligible, len(centers)):
        score = float(sum(score_rows[index][label] for index, label in enumerate(labels)))
        if score > best_score + 1e-12:
            best_score = score
            best_labels = labels
    assert best_labels is not None
    return {index: label for index, label in enumerate(best_labels)}


def _confidence(
    standardized: np.ndarray,
    labels: np.ndarray,
    centers: np.ndarray,
    *,
    minimum_cluster_size: int,
    method: str,
) -> tuple[np.ndarray, list[str]]:
    distances = np.stack(
        [np.sqrt(np.sum((standardized - center) ** 2, axis=1)) for center in centers], axis=1
    )
    ordered = np.sort(distances, axis=1)
    margin = (ordered[:, 1] - ordered[:, 0]) / (ordered[:, 1] + 1e-12)
    center_distances = np.stack(
        [np.sqrt(np.sum((centers - center) ** 2, axis=1)) for center in centers], axis=0
    )
    np.fill_diagonal(center_distances, np.inf)
    separation = np.tanh(np.min(center_distances, axis=1) / 2.0)
    counts = np.bincount(labels, minlength=len(centers))
    support = np.minimum(1.0, counts[labels] / max(1.0, minimum_cluster_size * 2.0))
    score = 0.50 * margin + 0.25 * separation[labels] + 0.25 * support
    if method.startswith("deterministic_fallback"):
        score = np.minimum(score, 0.74)
    score = np.clip(score, 0.0, 1.0)
    levels = ["high" if item >= 0.70 else "medium" if item >= 0.42 else "low" for item in score]
    return score, levels


def _empty_result(status: str, reason: str, features: Sequence[str]) -> StrategyFingerprintResult:
    return StrategyFingerprintResult(
        status=status,
        reason=reason,
        method="none",
        feature_columns=tuple(features),
        assignments=pd.DataFrame(),
    )


def fit_strategy_mode_fingerprints(
    lap_features: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = DEFAULT_FEATURE_COLUMNS,
    n_modes: int = 4,
    random_seed: int = 2026,
    context_columns: Sequence[str] = ("year", "meeting", "session"),
    minimum_rows: int = 8,
    minimum_cluster_size: int = 2,
    minimum_feature_coverage: float = 0.75,
    prefer_sklearn: bool = False,
) -> StrategyFingerprintResult:
    """在每个可比 session 内形成逐圈中性模式指纹。

    行顺序会在拟合前固定排序；同一输入和种子得到相同结果。缺字段返回
    ``not_comparable``，样本量、覆盖或变化不足返回 ``insufficient``。
    """

    features = tuple(dict.fromkeys(str(column) for column in feature_columns))
    if n_modes < 2 or n_modes > len(MODE_LABELS):
        raise ValueError(f"n_modes 必须在 2 到 {len(MODE_LABELS)} 之间")
    if minimum_rows < 2 or minimum_cluster_size < 1:
        raise ValueError("minimum_rows 至少为 2，minimum_cluster_size 至少为 1")
    contaminated = _contains_result_label(features)
    if contaminated:
        return _empty_result("not_comparable", f"特征合同包含结果标签：{contaminated}", features)
    missing = sorted(set(features) - set(lap_features.columns))
    if missing:
        return _empty_result("not_comparable", f"缺少请求特征：{missing}", features)
    if lap_features.empty:
        return _empty_result("insufficient", "没有逐圈特征行", features)

    work = lap_features.copy().reset_index(names="source_index")
    if "feature_status" in work:
        work = work.loc[work["feature_status"].astype(str).eq("ok")].copy()
    contexts = [column for column in context_columns if column in work.columns]
    context_audit: list[dict[str, Any]] = []
    assignments: list[pd.DataFrame] = []
    methods: list[str] = []
    iterator: Iterable[tuple[Any, pd.DataFrame]]
    if contexts:
        iterator = work.groupby(contexts, dropna=False, observed=True, sort=True)
    else:
        iterator = [("all_rows", work)]

    for context_key, raw_context in iterator:
        context_values = context_key if isinstance(context_key, tuple) else (context_key,)
        audit: dict[str, Any] = dict(zip(contexts, context_values))
        audit["comparison_scope"] = "session_matched" if contexts else "caller_supplied_single_context"
        audit["input_rows"] = int(len(raw_context))
        if len(raw_context) < minimum_rows:
            audit.update(status="insufficient", reason="rows_below_minimum")
            context_audit.append(audit)
            continue

        numeric = raw_context.loc[:, features].apply(pd.to_numeric, errors="coerce")
        coverage = numeric.notna().mean()
        usable = [
            column
            for column in features
            if coverage[column] >= minimum_feature_coverage
            and numeric[column].nunique(dropna=True) >= 2
            and float(numeric[column].std(skipna=True)) > 1e-12
        ]
        audit["usable_features"] = usable
        audit["feature_coverage"] = {column: float(coverage[column]) for column in features}
        if len(usable) < 2:
            audit.update(status="insufficient", reason="feature_coverage_or_variation_insufficient")
            context_audit.append(audit)
            continue

        filled = numeric[usable].fillna(numeric[usable].median())
        valid = np.isfinite(filled.to_numpy(dtype=float)).all(axis=1)
        context = raw_context.loc[valid].copy()
        filled = filled.loc[valid].copy()
        if len(context) < minimum_rows:
            audit.update(status="insufficient", reason="finite_rows_below_minimum")
            context_audit.append(audit)
            continue

        stable_sort = [column for column in (*contexts, "driver_acronym", "lap") if column in context]
        order_frame = pd.concat(
            [context[stable_sort].astype(str) if stable_sort else pd.DataFrame(index=context.index), filled],
            axis=1,
        )
        order = order_frame.sort_values(list(order_frame.columns), kind="stable").index
        context = context.loc[order].copy()
        filled = filled.loc[order].copy()
        values = filled.to_numpy(dtype=float)
        unique_rows = int(len(np.unique(np.round(_manual_standardize(values), 12), axis=0)))
        eligible_count = len(_eligible_modes(usable))
        maximum = min(n_modes, eligible_count, unique_rows, len(context) // minimum_cluster_size)
        selected: tuple[np.ndarray, np.ndarray, str] | None = None
        for clusters in range(maximum, 1, -1):
            labels, centers, method = _cluster(
                values,
                clusters,
                random_seed=random_seed,
                prefer_sklearn=prefer_sklearn,
            )
            if np.bincount(labels, minlength=clusters).min() >= minimum_cluster_size:
                selected = labels, centers, method
                break
        if selected is None:
            audit.update(status="insufficient", reason="no_stable_multi_mode_partition")
            context_audit.append(audit)
            continue

        labels, centers, method = selected
        standardized = _manual_standardize(values)
        mode_names = _assign_mode_names(centers, usable)
        scores, levels = _confidence(
            standardized,
            labels,
            centers,
            minimum_cluster_size=minimum_cluster_size,
            method=method,
        )
        output = context.copy()
        output["cluster_id"] = labels
        output["mode_label"] = [mode_names[int(label)] for label in labels]
        output["confidence_score"] = scores
        output["confidence"] = levels
        output["interpretation"] = output["mode_label"].map(MODE_INTERPRETATIONS)
        output["directness_level"] = "public_telemetry_proxy_not_direct_state"
        output["feature_columns_used"] = [tuple(usable)] * len(output)
        output["alternative_explanations"] = [ALTERNATIVE_EXPLANATIONS] * len(output)
        output["forbidden_claims"] = [FORBIDDEN_CLAIMS] * len(output)
        output["exposure_rows_in_context"] = len(output)
        assignments.append(output)
        methods.append(method)
        audit.update(
            status="ok",
            reason="stable_partition_found",
            rows_used=int(len(output)),
            modes=int(len(centers)),
            cluster_support={
                mode_names[index]: int(np.sum(labels == index)) for index in range(len(centers))
            },
            method=method,
        )
        context_audit.append(audit)

    if not assignments:
        reason = "all_contexts_insufficient" if context_audit else "没有通过质量门的逐圈特征行"
        return StrategyFingerprintResult(
            status="insufficient",
            reason=reason,
            method="none",
            feature_columns=features,
            assignments=pd.DataFrame(),
            context_audit=tuple(context_audit),
        )
    combined = pd.concat(assignments, ignore_index=True, sort=False)
    method = methods[0] if len(set(methods)) == 1 else "mixed_by_context"
    return StrategyFingerprintResult(
        status="ok",
        reason=("all_contexts_ok" if all(item["status"] == "ok" for item in context_audit) else "some_contexts_insufficient"),
        method=method,
        feature_columns=features,
        assignments=combined,
        context_audit=tuple(context_audit),
    )


def build_strategy_mode_fingerprints(
    lap_features: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    n_modes: int = 4,
    random_seed: int = 2026,
    group_columns: Sequence[str] = ("driver_acronym",),
    minimum_rows: int = 8,
    prefer_sklearn: bool = False,
) -> list[dict[str, Any]]:
    """生成工作台可直接消费的每组主模式摘要。

    稳定入口：输入是一行一圈的公开遥测派生特征，返回每个 ``group_columns``
    组合的主模式、模式分布、样本暴露、置信度和解释边界。若无法比较，返回
    一条 ``status=not_comparable/insufficient`` 的审计记录。
    """

    result = fit_strategy_mode_fingerprints(
        lap_features,
        feature_columns=feature_columns,
        n_modes=n_modes,
        random_seed=random_seed,
        minimum_rows=minimum_rows,
        prefer_sklearn=prefer_sklearn,
    )
    if result.status != "ok":
        return [
            {
                "status": result.status,
                "reason": result.reason,
                "mode_label": None,
                "confidence": "low",
                "exposure": {"rows": 0, "minimum_rows": minimum_rows},
                "feature_columns": list(result.feature_columns),
                "directness_level": result.directness_level,
                "alternative_explanations": list(result.alternative_explanations),
                "forbidden_claims": list(result.forbidden_claims),
            }
        ]

    missing_groups = sorted(set(group_columns) - set(result.assignments.columns))
    if missing_groups:
        return [
            {
                "status": "not_comparable",
                "reason": f"缺少摘要分组字段：{missing_groups}",
                "mode_label": None,
                "confidence": "low",
                "exposure": {"rows": 0, "minimum_rows": minimum_rows},
                "feature_columns": list(result.feature_columns),
                "directness_level": result.directness_level,
                "alternative_explanations": list(result.alternative_explanations),
                "forbidden_claims": list(result.forbidden_claims),
            }
        ]

    summaries: list[dict[str, Any]] = []
    for key, group in result.assignments.groupby(
        list(group_columns), dropna=False, observed=True, sort=True
    ):
        keys = key if isinstance(key, tuple) else (key,)
        counts = group["mode_label"].value_counts().sort_index()
        maximum = int(counts.max())
        dominant = sorted(counts[counts == maximum].index)[0]
        dominant_rows = group.loc[group["mode_label"].eq(dominant)]
        confidence_score = float(dominant_rows["confidence_score"].mean())
        confidence = "high" if confidence_score >= 0.70 else "medium" if confidence_score >= 0.42 else "low"
        summary: dict[str, Any] = dict(zip(group_columns, keys))
        summary.update(
            status="ok",
            mode_label=dominant,
            confidence=confidence,
            confidence_score=confidence_score,
            interpretation=MODE_INTERPRETATIONS[dominant],
            mode_distribution={label: int(value) for label, value in counts.items()},
            exposure={
                "rows": int(len(group)),
                "dominant_rows": int(len(dominant_rows)),
                "dominant_share": float(len(dominant_rows) / len(group)),
            },
            feature_columns=list(result.feature_columns),
            evidence={
                column: float(pd.to_numeric(dominant_rows[column], errors="coerce").mean())
                for column in result.feature_columns
                if column in dominant_rows
            },
            directness_level=result.directness_level,
            alternative_explanations=list(result.alternative_explanations),
            forbidden_claims=list(result.forbidden_claims),
            method=result.method,
        )
        summaries.append(summary)
    return summaries
