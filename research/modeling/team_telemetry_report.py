"""同队逐段差异、外场响应基线与异常证据门控。

本模块处理的是公开遥测上的统计差异。它刻意把直接时间差、输入调整后的
响应残差和异常发布门控分开；任何输出都不能单独识别具体机械故障、真实
能量状态或主动空力状态。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, sqrt
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .telemetry_explanation import TrackPhaseMap, apply_persistent_fdr_gate


LAP_KEYS = ("year", "meeting", "session", "team", "driver_acronym", "lap")
RESPONSE_INPUTS = (
    "entry_speed_kmh",
    "mean_throttle_pct",
    "brake_fraction",
    "mean_gear",
    "mean_rpm",
)


@dataclass(frozen=True)
class SegmentProfile:
    phase_id: int
    label: str
    phase: str
    distance_start_m: float
    distance_end_m: float
    matched_pairs: int
    delta_time_ms: float
    delta_time_interval_80_ms: tuple[float, float]
    input_adjusted_response_gap_ms: float | None
    target_entry_speed_gap_kmh: float | None
    target_exit_speed_gap_kmh: float | None
    confidence: str
    interpretation_scope: str = "matched_public_telemetry_difference"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["delta_time_interval_80_ms"] = list(self.delta_time_interval_80_ms)
        return value


def _required(frame: pd.DataFrame, names: Iterable[str], label: str) -> None:
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段：{missing}")


def _first_finite(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return float(finite.iloc[0]) if len(finite) else np.nan


def _last_finite(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return float(finite.iloc[-1]) if len(finite) else np.nan


def build_segment_lap_features(
    resampled_laps: pd.DataFrame,
    phase_map: TrackPhaseMap,
    *,
    group_columns: Sequence[str] = LAP_KEYS,
) -> pd.DataFrame:
    """把等距离遥测聚合为逐圈逐赛段的操作—响应特征。"""

    required = {
        *group_columns,
        "distance",
        "speed",
        "segment_dt_s",
        "throttle",
        "brake",
        "gear",
        "rpm",
    }
    _required(resampled_laps, required, "逐段特征输入")
    annotated = phase_map.annotate(resampled_laps)
    annotated = annotated.loc[annotated["track_segment_id"].notna()].copy()
    annotated["track_segment_id"] = annotated["track_segment_id"].astype(int)
    annotated["full_throttle"] = pd.to_numeric(
        annotated["throttle"], errors="coerce"
    ).ge(95.0)
    annotated["brake_active"] = pd.to_numeric(
        annotated["brake"], errors="coerce"
    ).gt(0.0)
    keys = [*group_columns, "track_segment_id", "track_phase"]
    rows: list[dict[str, Any]] = []
    for key, group in annotated.groupby(
        keys, dropna=False, observed=True, sort=True
    ):
        group = group.sort_values("distance", kind="stable")
        row = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
        segment_time = pd.to_numeric(group["segment_dt_s"], errors="coerce")
        row.update(
            {
                "distance_start_m": float(group["distance"].min()),
                "distance_end_m": float(group["distance"].max()),
                "segment_time_s": float(segment_time.sum(min_count=1)),
                "entry_speed_kmh": _first_finite(group["speed"]),
                "exit_speed_kmh": _last_finite(group["speed"]),
                "minimum_speed_kmh": float(
                    pd.to_numeric(group["speed"], errors="coerce").min()
                ),
                "maximum_speed_kmh": float(
                    pd.to_numeric(group["speed"], errors="coerce").max()
                ),
                "mean_speed_kmh": float(
                    pd.to_numeric(group["speed"], errors="coerce").mean()
                ),
                "mean_throttle_pct": float(
                    pd.to_numeric(group["throttle"], errors="coerce").mean()
                ),
                "full_throttle_fraction": float(group["full_throttle"].mean()),
                "brake_fraction": float(group["brake_active"].mean()),
                "mean_gear": float(pd.to_numeric(group["gear"], errors="coerce").mean()),
                "mean_rpm": float(pd.to_numeric(group["rpm"], errors="coerce").mean()),
                "mean_acc_x_proxy": float(
                    pd.to_numeric(
                        group.get("acc_x", pd.Series(np.nan, index=group.index)),
                        errors="coerce",
                    ).mean()
                ),
                "samples": int(len(group)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def fit_field_response_baseline(
    field_segments: pd.DataFrame,
    scored_segments: pd.DataFrame,
    *,
    input_columns: Sequence[str] = RESPONSE_INPUTS,
    alpha: float = 2.0,
    minimum_rows_per_segment: int = 12,
) -> pd.DataFrame:
    """用非目标车队样本冻结逐段输入—时间模型，并给待解释样本算残差。

    这只是事件内的统计诊断基线，不是物理车辆模型，也不应进入长期能力训练。
    """

    required = {"track_segment_id", "segment_time_s", *input_columns}
    _required(field_segments, required, "外场响应基线")
    _required(scored_segments, required, "响应评分输入")
    if alpha < 0:
        raise ValueError("ridge alpha 不得为负")
    if minimum_rows_per_segment < len(input_columns) + 2:
        raise ValueError("每赛段最小样本数不足以支持输入调整")

    output = scored_segments.copy()
    output["field_response_prediction_s"] = np.nan
    output["field_response_residual_s"] = np.nan
    output["response_model_rows"] = 0
    output["response_model_status"] = "insufficient"
    for segment_id, test_group in output.groupby(
        "track_segment_id", observed=True, sort=True
    ):
        train = field_segments.loc[
            field_segments["track_segment_id"].eq(segment_id),
            ["segment_time_s", *input_columns],
        ].copy()
        train = train.replace([np.inf, -np.inf], np.nan).dropna()
        test = test_group[[*input_columns]].replace([np.inf, -np.inf], np.nan)
        valid_test = test.notna().all(axis=1)
        if len(train) < minimum_rows_per_segment or not valid_test.any():
            continue
        model = Pipeline(
            [
                ("scale", StandardScaler()),
                ("ridge", Ridge(alpha=float(alpha))),
            ]
        )
        model.fit(train[list(input_columns)], train["segment_time_s"])
        positions = test.index[valid_test]
        prediction = model.predict(test.loc[positions, list(input_columns)])
        output.loc[positions, "field_response_prediction_s"] = prediction
        output.loc[positions, "field_response_residual_s"] = (
            pd.to_numeric(output.loc[positions, "segment_time_s"], errors="coerce")
            - prediction
        )
        output.loc[positions, "response_model_rows"] = int(len(train))
        output.loc[positions, "response_model_status"] = "available"
    output["response_interpretation_scope"] = (
        "event_field_trained_input_adjusted_statistical_residual"
    )
    return output


def build_pair_segment_differences(
    matches: pd.DataFrame,
    scored_segments: pd.DataFrame,
) -> pd.DataFrame:
    """把一对一可比圈展开为目标车手减参考车手的逐段差。"""

    required_match = {
        "target_driver_acronym",
        "target_lap",
        "reference_driver_acronym",
        "reference_lap",
        "match_quality",
    }
    _required(matches, required_match, "可比圈")
    _required(
        scored_segments,
        {
            "driver_acronym",
            "lap",
            "track_segment_id",
            "track_phase",
            "segment_time_s",
        },
        "逐段差输入",
    )
    if matches.empty:
        return pd.DataFrame()
    segment_columns = [
        "driver_acronym",
        "lap",
        "track_segment_id",
        "track_phase",
        "distance_start_m",
        "distance_end_m",
        "segment_time_s",
        "entry_speed_kmh",
        "exit_speed_kmh",
        "minimum_speed_kmh",
        "maximum_speed_kmh",
        "mean_speed_kmh",
        "mean_throttle_pct",
        "full_throttle_fraction",
        "brake_fraction",
        "mean_gear",
        "mean_rpm",
        "mean_acc_x_proxy",
        "field_response_prediction_s",
        "field_response_residual_s",
        "response_model_rows",
        "response_model_status",
    ]
    segment_columns = [column for column in segment_columns if column in scored_segments]
    target = scored_segments[segment_columns].add_prefix("target_")
    reference = scored_segments[segment_columns].add_prefix("reference_")
    pairs = matches.copy().reset_index(drop=True)
    pairs["pair_id"] = np.arange(len(pairs), dtype=int)
    joined = pairs.merge(
        target,
        on=["target_driver_acronym", "target_lap"],
        how="inner",
    )
    joined = joined.merge(
        reference,
        left_on=[
            "reference_driver_acronym",
            "reference_lap",
            "target_track_segment_id",
        ],
        right_on=[
            "reference_driver_acronym",
            "reference_lap",
            "reference_track_segment_id",
        ],
        how="inner",
    )
    joined["track_segment_id"] = joined["target_track_segment_id"].astype(int)
    joined["track_phase"] = joined["target_track_phase"]
    joined["external_condition_matched_gap_s"] = (
        joined["target_segment_time_s"] - joined["reference_segment_time_s"]
    )
    if {
        "target_field_response_residual_s",
        "reference_field_response_residual_s",
    }.issubset(joined.columns):
        joined["input_adjusted_response_gap_s"] = (
            joined["target_field_response_residual_s"]
            - joined["reference_field_response_residual_s"]
        )
    else:
        joined["input_adjusted_response_gap_s"] = np.nan
    for feature in (
        "entry_speed_kmh",
        "exit_speed_kmh",
        "minimum_speed_kmh",
        "maximum_speed_kmh",
        "mean_throttle_pct",
        "brake_fraction",
        "mean_gear",
        "mean_rpm",
        "mean_acc_x_proxy",
    ):
        target_column = f"target_{feature}"
        reference_column = f"reference_{feature}"
        if target_column in joined and reference_column in joined:
            joined[f"delta_{feature}"] = joined[target_column] - joined[reference_column]
    joined["directness_level"] = "derived"
    joined["interpretation_scope"] = "matched_difference_not_causal_effect"
    return joined


def _bootstrap_interval(
    values: Sequence[float],
    *,
    seed: int,
    level: float = 0.80,
    draws: int = 1000,
) -> tuple[float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        return (np.nan, np.nan)
    if len(array) == 1:
        return (float(array[0]), float(array[0]))
    rng = np.random.default_rng(seed)
    samples = rng.choice(array, size=(draws, len(array)), replace=True)
    statistics = np.median(samples, axis=1)
    tail = (1.0 - level) / 2.0
    return tuple(float(value) for value in np.quantile(statistics, [tail, 1.0 - tail]))


def _phase_labels(
    pairs: pd.DataFrame,
    corners: Mapping[str, Sequence[Any]] | None,
) -> dict[int, str]:
    corner_rows: list[tuple[float, str]] = []
    if corners:
        numbers = corners.get("CornerNumber", ())
        distances = corners.get("Distance", ())
        for number, distance in zip(numbers, distances):
            try:
                corner_rows.append((float(distance), f"T{int(number)}"))
            except (TypeError, ValueError):
                continue
    labels: dict[int, str] = {}
    for segment_id, group in pairs.groupby("track_segment_id", observed=True, sort=True):
        phase = str(group["track_phase"].mode().iloc[0])
        lower = float(group["target_distance_start_m"].median())
        upper = float(group["target_distance_end_m"].median())
        in_segment = [label for distance, label in corner_rows if lower - 30 <= distance <= upper + 30]
        if phase == "corner" and in_segment:
            labels[int(segment_id)] = "/".join(in_segment)
        elif phase == "straight":
            preceding = [
                (distance, label)
                for distance, label in corner_rows
                if 0.0 <= lower - distance <= 160.0
            ]
            following = [
                (distance, label)
                for distance, label in corner_rows
                if 0.0 <= distance - upper <= 160.0
            ]
            if preceding:
                _, corner = max(preceding)
                labels[int(segment_id)] = f"{corner} 后直道"
            elif following:
                _, corner = min(following)
                labels[int(segment_id)] = f"{corner} 前直道"
            else:
                labels[int(segment_id)] = f"直道 S{int(segment_id)}"
        elif phase == "corner":
            labels[int(segment_id)] = f"弯道段 C{int(segment_id)}"
        else:
            labels[int(segment_id)] = f"过渡段 X{int(segment_id)}"
    return labels


def summarize_segment_profiles(
    pair_segments: pd.DataFrame,
    *,
    corners: Mapping[str, Sequence[Any]] | None = None,
    random_seed: int = 20260723,
    force_low_confidence: bool = False,
) -> list[dict[str, Any]]:
    """汇总同队逐段时间来源，同时保留直接差和输入调整响应差。"""

    if pair_segments.empty:
        return []
    _required(
        pair_segments,
        {
            "track_segment_id",
            "track_phase",
            "external_condition_matched_gap_s",
            "input_adjusted_response_gap_s",
            "target_distance_start_m",
            "target_distance_end_m",
        },
        "逐段汇总",
    )
    labels = _phase_labels(pair_segments, corners)
    rows: list[dict[str, Any]] = []
    for segment_id, group in pair_segments.groupby(
        "track_segment_id", observed=True, sort=True
    ):
        direct_ms = (
            pd.to_numeric(group["external_condition_matched_gap_s"], errors="coerce")
            .dropna()
            .to_numpy(dtype=float)
            * 1000.0
        )
        if not len(direct_ms):
            continue
        adjusted = (
            pd.to_numeric(group["input_adjusted_response_gap_s"], errors="coerce")
            .dropna()
            .to_numpy(dtype=float)
            * 1000.0
        )
        interval = _bootstrap_interval(
            direct_ms,
            seed=int(random_seed) + int(segment_id),
        )
        n = int(len(direct_ms))
        confidence = (
            "low"
            if force_low_confidence
            else "medium" if n >= 12 and interval[0] * interval[1] > 0 else "low"
        )
        row = SegmentProfile(
            phase_id=int(segment_id),
            label=labels[int(segment_id)],
            phase=str(group["track_phase"].mode().iloc[0]),
            distance_start_m=float(group["target_distance_start_m"].median()),
            distance_end_m=float(group["target_distance_end_m"].median()),
            matched_pairs=n,
            delta_time_ms=float(np.median(direct_ms)),
            delta_time_interval_80_ms=interval,
            input_adjusted_response_gap_ms=(
                float(np.median(adjusted)) if len(adjusted) else None
            ),
            target_entry_speed_gap_kmh=(
                float(pd.to_numeric(group["delta_entry_speed_kmh"], errors="coerce").median())
                if "delta_entry_speed_kmh" in group
                else None
            ),
            target_exit_speed_gap_kmh=(
                float(pd.to_numeric(group["delta_exit_speed_kmh"], errors="coerce").median())
                if "delta_exit_speed_kmh" in group
                else None
            ),
            confidence=confidence,
        ).to_dict()
        row["positive_direction"] = "target_driver_slower"
        row["comparability_warning"] = bool(force_low_confidence)
        row["alternative_explanations"] = [
            "未完全观测的交通与尾流",
            "设定、规格或车辆状态差异",
            "驾驶操作与比赛目标差异",
            "事件内外场响应模型误差",
        ]
        rows.append(row)
    return rows


def _robust_center_scale(values: pd.Series) -> tuple[float, float]:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric)]
    if len(numeric) == 0:
        return np.nan, np.nan
    center = float(numeric.median())
    scale = float(1.4826 * (numeric - center).abs().median())
    if not np.isfinite(scale) or scale <= 1e-9:
        standard = float(numeric.std(ddof=1)) if len(numeric) > 1 else np.nan
        scale = standard if np.isfinite(standard) and standard > 1e-9 else np.nan
    return center, scale


def _two_sided_normal_p(z: pd.Series) -> pd.Series:
    def calculate(value: Any) -> float:
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            return 1.0
        if not np.isfinite(number):
            return 1.0
        # 2 * normal.sf(|z|) = erfc(|z| / sqrt(2))。
        return float(1.0 - erf(number / sqrt(2.0)))

    return z.map(calculate)


def build_anomaly_evidence(
    pair_segments: pd.DataFrame,
    field_segments: pd.DataFrame,
    *,
    minimum_persistent_laps: int = 3,
    minimum_independent_feature_families: int = 2,
    practical_time_loss_ms: float = 20.0,
    false_discovery_rate: float = 0.10,
    corners: Mapping[str, Sequence[Any]] | None = None,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """用自身、队友、全场三基线和 FDR/持续性生成异常 Episode。"""

    if pair_segments.empty:
        return [], pd.DataFrame()
    required = {
        "target_lap",
        "target_driver_acronym",
        "reference_driver_acronym",
        "track_segment_id",
        "track_phase",
        "target_segment_time_s",
        "external_condition_matched_gap_s",
        "input_adjusted_response_gap_s",
    }
    _required(pair_segments, required, "异常证据")
    _required(field_segments, {"track_segment_id", "segment_time_s"}, "全场基线")
    work = pair_segments.copy().sort_values(
        ["track_segment_id", "target_lap"], kind="stable"
    )
    field_median = (
        field_segments.groupby("track_segment_id", observed=True)["segment_time_s"]
        .median()
        .rename("field_segment_time_s")
    )
    work = work.merge(field_median, on="track_segment_id", how="left")
    work["field_gap_s"] = work["target_segment_time_s"] - work["field_segment_time_s"]
    work["self_baseline_s"] = work.groupby(
        "track_segment_id", observed=True
    )["target_segment_time_s"].transform(
        lambda values: values.shift(1).rolling(5, min_periods=3).median()
    )
    work["self_gap_s"] = work["target_segment_time_s"] - work["self_baseline_s"]

    for source, centered in (
        ("self_gap_s", "self_centered_s"),
        ("external_condition_matched_gap_s", "teammate_centered_s"),
        ("field_gap_s", "field_centered_s"),
        ("input_adjusted_response_gap_s", "response_centered_s"),
    ):
        work[centered] = np.nan
        work[f"{centered}_z"] = np.nan
        for _, positions in work.groupby(
            "track_segment_id", observed=True, sort=False
        ).groups.items():
            positions = list(positions)
            center, scale = _robust_center_scale(work.loc[positions, source])
            work.loc[positions, centered] = work.loc[positions, source] - center
            if np.isfinite(scale):
                work.loc[positions, f"{centered}_z"] = (
                    work.loc[positions, centered] / scale
                )

    kinematic_columns = [
        column
        for column in (
            "delta_exit_speed_kmh",
            "delta_minimum_speed_kmh",
            "delta_mean_speed_kmh",
            "delta_maximum_speed_kmh",
        )
        if column in work
    ]
    kinematic_z_columns: list[str] = []
    for column in kinematic_columns:
        centered = f"{column}_centered"
        z_column = f"{column}_centered_z"
        work[centered] = np.nan
        work[z_column] = np.nan
        for _, positions in work.groupby(
            "track_segment_id", observed=True, sort=False
        ).groups.items():
            positions = list(positions)
            center, scale = _robust_center_scale(work.loc[positions, column])
            work.loc[positions, centered] = work.loc[positions, column] - center
            if np.isfinite(scale):
                work.loc[positions, z_column] = work.loc[positions, centered] / scale
        kinematic_z_columns.append(z_column)

    signs = np.sign(
        work[["self_centered_s", "teammate_centered_s", "field_centered_s"]]
    )
    work["three_baseline_direction_consistent"] = (
        signs.notna().all(axis=1)
        & signs.ne(0).all(axis=1)
        & signs.nunique(axis=1).eq(1)
    )
    minimum_z = work[
        [
            "self_centered_s_z",
            "teammate_centered_s_z",
            "field_centered_s_z",
        ]
    ].abs().min(axis=1)
    work["p_value"] = _two_sided_normal_p(minimum_z)
    work.loc[~work["three_baseline_direction_consistent"], "p_value"] = 1.0
    work["effect"] = (
        work[["self_centered_s", "teammate_centered_s", "field_centered_s"]]
        .median(axis=1)
        .mul(1000.0)
    )
    direct_family = work["effect"].abs().ge(practical_time_loss_ms)
    response_family = (
        np.sign(work["response_centered_s"]).eq(np.sign(work["effect"]))
        & work["response_centered_s"].abs().mul(1000.0).ge(practical_time_loss_ms / 2.0)
    )
    if kinematic_z_columns:
        # 速度差为负通常支持正的时间损失，因此取反后与 effect 对齐。至少一个
        # 独立运动学响应族达到 1 个稳健尺度，才算第二个特征族。
        work["kinematic_response_z"] = -work[kinematic_z_columns].median(axis=1)
        kinematic_family = (
            np.sign(work["kinematic_response_z"]).eq(np.sign(work["effect"]))
            & work["kinematic_response_z"].abs().ge(1.0)
        )
    else:
        work["kinematic_response_z"] = np.nan
        kinematic_family = pd.Series(False, index=work.index)
    work["direct_time_family_supported"] = direct_family
    work["input_adjusted_response_supported"] = response_family
    work["kinematic_response_family_supported"] = kinematic_family
    # 输入调整时间残差是必要证据通道，但与直接时间同源，不冒充独立特征族。
    work["evidence_family_count"] = direct_family.astype(int) + kinematic_family.astype(int)
    work.loc[
        work["evidence_family_count"].lt(minimum_independent_feature_families)
        | ~response_family,
        "p_value",
    ] = 1.0
    gated = apply_persistent_fdr_gate(
        work,
        entity_columns=["track_segment_id"],
        event_order_column="target_lap",
        p_value_column="p_value",
        effect_column="effect",
        alpha=false_discovery_rate,
        minimum_consecutive_events=minimum_persistent_laps,
        minimum_absolute_effect=practical_time_loss_ms,
        fdr_group_columns=["target_lap"],
    )
    gated["anomaly_triggered"] = (
        gated["persistent_gate_passed"]
        & gated["three_baseline_direction_consistent"]
        & gated["input_adjusted_response_supported"]
        & gated["evidence_family_count"].ge(minimum_independent_feature_families)
    )
    labels = _phase_labels(gated, corners)
    episodes: list[dict[str, Any]] = []
    triggered = gated.loc[gated["anomaly_triggered"]]
    for segment_id, group in triggered.groupby(
        "track_segment_id", observed=True, sort=True
    ):
        group = group.sort_values("target_lap", kind="stable")
        first = group.iloc[0]
        last = group.iloc[-1]
        run = int(first["persistence_run_length"])
        start_candidates = gated.loc[
            gated["track_segment_id"].eq(segment_id)
            & gated["target_lap"].le(first["target_lap"])
        ].sort_values("target_lap")
        lap_start = int(
            start_candidates.tail(max(1, run))["target_lap"].min()
        )
        effect = float(group["effect"].median())
        adjusted = float(group["response_centered_s"].median() * 1000.0)
        episodes.append(
            {
                "primary_class": "single_car_observed_response_divergence",
                "segment_id": int(segment_id),
                "segment_label": labels[int(segment_id)],
                "lap_start": lap_start,
                "lap_end": int(last["target_lap"]),
                "target_driver": str(first["target_driver_acronym"]),
                "reference_driver": str(first["reference_driver_acronym"]),
                "impact_ms": effect,
                "input_adjusted_response_gap_ms": adjusted,
                "feature_families": ["segment_time", "kinematic_response"],
                "required_evidence_channels": ["input_adjusted_response"],
                "baseline_support": ["self", "teammate", "field"],
                "minimum_q_value": float(group["q_value"].min()),
                "confidence": "medium" if len(group) >= 2 else "low",
                "summary": (
                    "自身、队友和全场基线方向一致，并通过多赛段 FDR 与持续性门控；"
                    "该结果只支持可观测响应发生改变。"
                ),
                "alternative_explanations": [
                    "设定或规格差异",
                    "未观测损伤或可靠性状态",
                    "动力与空气动力操作模式差异",
                    "未完全匹配的交通或比赛目标",
                ],
                "forbidden_claims": [
                    "不得命名具体故障部件",
                    "不得确认 SOC、部署功率或主动空力状态",
                ],
            }
        )
    return episodes, gated


def build_stint_dossiers(
    lap_states: pd.DataFrame,
    episode_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """生成不混淆能力与当场兑现的轻量 StintDossier。"""

    _required(
        lap_states,
        {
            "driver_acronym",
            "team",
            "stint",
            "lap",
            "stint_phase",
            "is_clean",
            "lap_duration",
            "traffic_state",
        },
        "StintDossier",
    )
    episodes = pd.DataFrame(list(episode_rows))
    rows: list[dict[str, Any]] = []
    valid = lap_states.loc[lap_states["stint"].notna()].copy()
    context_keys = [
        column for column in ("year", "meeting", "session") if column in valid
    ]
    group_keys = [*context_keys, "driver_acronym", "team", "stint"]
    for key, group in valid.groupby(group_keys, observed=True, sort=True):
        values = key if isinstance(key, tuple) else (key,)
        identity = dict(zip(group_keys, values))
        driver = identity["driver_acronym"]
        team = identity["team"]
        stint = identity["stint"]
        group = group.sort_values("lap", kind="stable")
        clean = group.loc[group["is_clean"]]
        phase_counts = group["stint_phase"].value_counts().to_dict()
        driver_episodes = (
            episodes.loc[
                episodes.get("driver", pd.Series(dtype=object)).eq(driver)
                & episodes.get("lap_start", pd.Series(dtype=float)).between(
                    group["lap"].min(), group["lap"].max()
                )
                & episodes.get("lap_end", pd.Series(dtype=float)).between(
                    group["lap"].min(), group["lap"].max()
                )
            ]
            if not episodes.empty and {"driver", "lap_start", "lap_end"}.issubset(episodes)
            else pd.DataFrame()
        )
        compound = (
            str(group["compound"].dropna().astype(str).mode().iloc[0])
            if "compound" in group
            and not group["compound"].dropna().astype(str).empty
            else "UNKNOWN"
        )
        rows.append(
            {
                **{column: identity[column] for column in context_keys},
                "driver": str(driver),
                "team": str(team),
                "stint": int(stint),
                "compound": compound,
                "lap_start": int(group["lap"].min()),
                "lap_end": int(group["lap"].max()),
                "laps": int(len(group)),
                "clean_laps": int(len(clean)),
                "data_coverage": float(len(clean) / len(group)) if len(group) else 0.0,
                "phase_lap_counts": {
                    str(key): int(value) for key, value in phase_counts.items()
                },
                "median_clean_lap_s": (
                    float(pd.to_numeric(clean["lap_duration"], errors="coerce").median())
                    if len(clean)
                    else None
                ),
                "traffic_state_counts": {
                    str(key): int(value)
                    for key, value in group["traffic_state"].value_counts().items()
                },
                "episode_count": int(len(driver_episodes)),
                "vehicle_state_assessment": "not_identified_from_public_telemetry_alone",
                "ability_assessment": "not_scored_from_single_stint",
                "confidence": "medium" if len(clean) >= 6 else "low",
            }
        )
    return rows
