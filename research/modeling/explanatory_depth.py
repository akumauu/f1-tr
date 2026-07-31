"""v6 可解释深度层：更强的上下文剥离、信号/噪声分离度、加性毫秒重建与理论上限。

本模块是 v5 逐站合同的**严格超集**，不改动 v2–v5 模块的对外行为：

- Layer A：在身份无关的上下文头里加入 compound×tyre 斜率与 traffic/dirty-air 代理，
  用 2024 时间外 MAE 门控（退化即回退），得到更干净的 ``driver_neutral_vehicle_z``。
- Layer B：每站队间/队内方差比（分离度指数）+ 复用 Gibbs 后验的排序正确概率。
- Layer C：把每 driver-event 的可加成分转有符号毫秒并做重建 R²/MAE + 名次构念检查。
- Layer D：对驾驶中和后的车辆前沿做半正态执行损失校正，得到带 CI 的理论上限。

所有"更明显"都来自更干净的上下文与显式信号/噪声分解，不改动 v3 先验强度。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import Ridge
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from modeling.bayesian_counterfactual import (
    aggregate_with_context_fit,
    gelman_rubin_rhat,
)
from modeling.event_stint_attribution import (
    _block_draws,
    _frontier_point,
    advantage_to_ms,
    bootstrap_event_frontiers,
    delta_z_to_ms,
    summarize_samples,
)
from modeling.seasonal_attribution import (
    NUMERIC_FEATURES,
    log_pct_to_ms,
    meeting_driver_weights,
)


BASELINE_NUMERIC = tuple(NUMERIC_FEATURES)
CANDIDATE_FEATURES = ("compound_tyre_slope", "traffic_dirty_air")


def validate_explanatory_depth_config(config: Mapping[str, Any]) -> None:
    """拒绝会制造伪上限、伪区分度或绝对圈时融合的配置。"""

    if not config.get("offline_only"):
        raise ValueError("v6 只允许离线配置。")
    if bool(config["boundaries"]["absolute_qualifying_race_time_merge"]):
        raise ValueError("排位与正赛绝对圈时不能直接融合。")
    frontier = config["frontier"]
    race_q = float(frontier["race_ceiling_quantile"])
    baseline_q = float(frontier["usable_baseline_quantile"])
    floor_q = float(frontier["performance_floor_quantile"])
    if not (0.0 < race_q < baseline_q < floor_q < 1.0):
        raise ValueError("Race 前沿分位必须满足 0 < ceiling < baseline < floor < 1。")
    if not 0.0 < float(frontier["qualifying_ceiling_quantile"]) < 0.5:
        raise ValueError("Qualifying 上沿分位必须位于 (0, 0.5)。")
    weights = frontier["weekend_session_weights"]
    if set(weights) != {"Qualifying", "Race"} or not np.isclose(
        sum(float(value) for value in weights.values()), 1.0
    ):
        raise ValueError("周末权重必须且只含 Qualifying/Race 且和为 1。")
    if int(frontier["bootstrap_repetitions"]) < 100:
        raise ValueError("前沿 bootstrap 至少需要 100 次。")
    fastest_fraction = float(frontier.get("half_normal_fastest_fraction", 0.10))
    shortfall_cap = float(frontier.get("half_normal_shortfall_cap_z", 0.30))
    if not 0.0 < fastest_fraction <= 0.20:
        raise ValueError("半正态执行损失只能使用最快 (0, 20%] 圈。")
    if not 0.0 < shortfall_cap <= 0.50:
        raise ValueError("半正态执行损失硬上限必须位于 (0, 0.50] z。")
    for feature in config["context_head"]["candidate_features"]:
        if feature not in CANDIDATE_FEATURES:
            raise ValueError(f"未知候选上下文特征：{feature}")
    if float(config["context_head"]["oos_gate"]["tolerance"]) < 0.0:
        raise ValueError("上下文门控容忍度必须非负。")
    separation = config["separation"]
    high_confidence = float(separation["high_pair_confidence"])
    medium_confidence = float(separation["medium_pair_confidence"])
    if not 0.5 <= medium_confidence < high_confidence <= 1.0:
        raise ValueError("成对置信度阈值必须满足 0.5 <= medium < high <= 1。")
    high_index = float(separation["separation_index_reference_high"])
    medium_index = float(separation["separation_index_reference_medium"])
    if not 0.0 < medium_index < high_index:
        raise ValueError("方差比分离阈值必须满足 0 < medium < high。")
    if int(separation["minimum_team_event_clean_laps"]) < 1:
        raise ValueError("逐队最小干净圈数必须为正整数。")


# --------------------------------------------------------------------------- #
# Layer A —— traffic 提取与更强的上下文头
# --------------------------------------------------------------------------- #


def extract_traffic_lap_metrics(files: Sequence[Path]) -> pd.DataFrame:
    """从扩展 v4 遥测 parquet 逐圈聚合 gap-to-ahead 与 position（全场，非仅四队）。"""

    import duckdb

    query = """
        SELECT
            CAST(year AS INTEGER) AS year,
            replace(meeting, '_', ' ') AS meeting_name,
            arg_min(driver_acronym, sample_index) AS driver_id,
            arg_min(team, sample_index) AS team_name,
            CAST(lap AS INTEGER) AS lap_number,
            CAST(median(distance_to_driver_ahead) AS DOUBLE) AS median_gap_to_ahead_m,
            CAST(min(distance_to_driver_ahead) AS DOUBLE) AS min_gap_to_ahead_m,
            CAST(avg(CAST(position AS DOUBLE)) AS DOUBLE) AS mean_position,
            count(*) AS telemetry_samples,
            count(distance_to_driver_ahead) AS gap_samples
        FROM read_parquet(?, hive_partitioning=false)
        GROUP BY year, meeting, driver_number, lap
        ORDER BY year, meeting, driver_number, lap
    """
    frames: list[pd.DataFrame] = []
    connection = duckdb.connect()
    try:
        connection.execute("SET threads TO 4")
        for path in files:
            frames.append(connection.execute(query, [str(path)]).fetch_df())
    finally:
        connection.close()
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def attach_traffic(
    race: pd.DataFrame, traffic: pd.DataFrame, settings: Mapping[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """把 gap-to-ahead 代理按圈级键 merge，构造 dirty-air 指标与缺口特征。

    只按 ``(year, meeting_name, driver_id, lap_number)`` merge，不含 team_name，
    避免冻结帧的车队别名与 parquet 原始车队名不一致导致的静默漏配。
    """

    merge_keys = ["year", "meeting_name", "driver_id", "lap_number"]
    metric_columns = [
        "median_gap_to_ahead_m",
        "min_gap_to_ahead_m",
        "mean_position",
        "telemetry_samples",
        "gap_samples",
    ]
    work = race.copy()
    if traffic.empty:
        merged = work
        for column in metric_columns:
            if column not in merged.columns:
                merged[column] = np.nan
    else:
        subset = traffic[merge_keys + metric_columns].drop_duplicates(subset=merge_keys)
        merged = work.merge(subset, how="left", on=merge_keys, validate="one_to_one")
    threshold = float(settings["traffic_gap_dirty_air_threshold_m"])
    cap = float(settings["traffic_gap_cap_m"])
    min_samples = int(settings["minimum_samples_per_lap"])
    gap = pd.to_numeric(merged["median_gap_to_ahead_m"], errors="coerce")
    enough = pd.to_numeric(merged.get("gap_samples"), errors="coerce").fillna(0.0) >= min_samples
    available = gap.notna() & enough
    merged["traffic_available"] = available
    # dirty_air 与 gap_deficit 越大代表越受前车影响；缺失按 0（无交通惩罚，中性）。
    merged["dirty_air"] = np.where(available & (gap < threshold), 1.0, 0.0)
    deficit = (threshold - gap.clip(upper=threshold)) / threshold
    merged["gap_deficit"] = np.where(available, deficit.clip(lower=0.0, upper=1.0), 0.0)
    capped = gap.clip(lower=0.0, upper=cap) / cap
    merged["gap_ratio"] = np.where(available, capped, 1.0)
    audit = {
        "race_clean_laps": int(len(merged)),
        "traffic_key_matches": int(gap.notna().sum()),
        "traffic_key_match_rate": float(gap.notna().mean()) if len(merged) else None,
        "usable_gap_laps": int(available.sum()),
        "usable_gap_rate": float(available.mean()) if len(merged) else None,
        "dirty_air_lap_share": float(merged["dirty_air"].mean()) if len(merged) else None,
        "coverage_by_year": [
            {
                "year": int(year),
                "race_clean_laps": int(len(group)),
                "usable_gap_laps": int(group["traffic_available"].sum()),
                "usable_gap_rate": float(group["traffic_available"].mean()),
                "dirty_air_lap_share": float(group["dirty_air"].mean()),
            }
            for year, group in merged.groupby("year", sort=True)
        ],
        "threshold_m": threshold,
        "missingness_policy": "缺失 traffic 不判定为 dirty-air，按中性 0 进入上下文头；覆盖率须随结果披露。",
        "note": "gap-to-ahead 为逐圈 distance_to_driver_ahead 中位数；缺失不冒充 clean-air，单独留痕。",
    }
    return merged, audit


def _compound_slope_columns(frame: pd.DataFrame, compounds: Sequence[str]) -> np.ndarray:
    tyre = pd.to_numeric(frame["tyre_age"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    compound = frame["compound"].fillna("UNKNOWN").astype(str).to_numpy()
    columns = [np.where(compound == value, tyre, 0.0) for value in compounds]
    if not columns:
        return np.zeros((len(frame), 0), dtype=float)
    return np.column_stack(columns)


def _traffic_columns(frame: pd.DataFrame) -> np.ndarray:
    dirty = pd.to_numeric(frame.get("dirty_air"), errors="coerce").fillna(0.0).to_numpy(float)
    deficit = pd.to_numeric(frame.get("gap_deficit"), errors="coerce").fillna(0.0).to_numpy(float)
    return np.column_stack([dirty, deficit])


@dataclass
class ContextHead:
    """身份无关的上下文回归头；只用于生成更干净的描述性残差。"""

    features: tuple[str, ...]
    compounds: tuple[str, ...]
    numeric_scaler: StandardScaler
    numeric_medians: pd.Series
    compound_encoder: OneHotEncoder
    circuit_encoder: OneHotEncoder
    model: Ridge

    def _design(self, frame: pd.DataFrame) -> sparse.csr_matrix:
        numeric = (
            frame[list(BASELINE_NUMERIC)]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(self.numeric_medians)
        )
        pieces: list[sparse.spmatrix] = [
            sparse.csr_matrix(self.numeric_scaler.transform(numeric))
        ]
        if "compound_tyre_slope" in self.features:
            slope = _compound_slope_columns(frame, self.compounds)
            pieces.append(sparse.csr_matrix(slope / 10.0))
        if "traffic_dirty_air" in self.features:
            pieces.append(sparse.csr_matrix(_traffic_columns(frame)))
        pieces.append(
            self.compound_encoder.transform(
                frame[["compound"]].fillna("UNKNOWN").astype(str)
            )
        )
        pieces.append(
            self.circuit_encoder.transform(
                frame[["circuit_name"]].fillna("UNKNOWN").astype(str)
            )
        )
        return sparse.hstack(pieces, format="csr")

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(self._design(frame)), dtype=float)


def fit_context_head(
    frame: pd.DataFrame, features: Sequence[str], alpha: float = 1.0
) -> ContextHead:
    """拟合身份无关上下文头（不含 team/driver），可选加入候选特征。"""

    if frame.empty:
        raise ValueError("上下文头输入不能为空。")
    numeric = frame[list(BASELINE_NUMERIC)].apply(pd.to_numeric, errors="coerce")
    numeric_medians = numeric.median().fillna(0.0)
    numeric_scaler = StandardScaler().fit(numeric.fillna(numeric_medians))
    compounds = tuple(sorted(frame["compound"].fillna("UNKNOWN").astype(str).unique()))
    compound_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=True, dtype=np.float64)
    compound_encoder.fit(frame[["compound"]].fillna("UNKNOWN").astype(str))
    circuit_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=True, dtype=np.float64)
    circuit_encoder.fit(frame[["circuit_name"]].fillna("UNKNOWN").astype(str))
    head = ContextHead(
        features=tuple(features),
        compounds=compounds,
        numeric_scaler=numeric_scaler,
        numeric_medians=numeric_medians,
        compound_encoder=compound_encoder,
        circuit_encoder=circuit_encoder,
        model=Ridge(alpha=float(alpha), solver="lsqr", tol=1e-5, max_iter=30000),
    )
    design = head._design(frame)
    target = frame["target_log_pct"].to_numpy(dtype=float)
    weights = meeting_driver_weights(frame)
    head.model.fit(design, target, sample_weight=weights)
    return head


def _driver_event_mae(frame: pd.DataFrame, prediction: np.ndarray) -> float:
    """驱动-赛事聚合的时间外 MAE：先按 driver×event 取残差中位，再取绝对均值。"""

    residual = frame["target_log_pct"].to_numpy(dtype=float) - np.asarray(prediction, float)
    table = pd.DataFrame(
        {
            "meeting_key": frame["meeting_key"].to_numpy(),
            "driver_id": frame["driver_id"].to_numpy(),
            "residual": residual,
        }
    )
    grouped = table.groupby(["meeting_key", "driver_id"])["residual"].median()
    return float(np.mean(np.abs(grouped.to_numpy(dtype=float))))


def context_oos_gate(
    race: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """仅用 2024 时间外前推选特征；2025 只作锁定报告，不参与选特征。"""

    head_config = config["context_head"]
    gate = head_config["oos_gate"]
    selection_year = int(gate["selection_year"])
    adaptation = int(gate["adaptation_races"])
    locked_year = int(gate["locked_year"])
    tolerance = float(gate["tolerance"])
    candidates = list(head_config["candidate_features"])

    def split(target_year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
        train = race.loc[
            (race["year"] < target_year)
            | ((race["year"] == target_year) & (race["round_index"] <= adaptation))
        ]
        test = race.loc[
            (race["year"] == target_year) & (race["round_index"] > adaptation)
        ]
        return train.reset_index(drop=True), test.reset_index(drop=True)

    def evaluate(features: Sequence[str], target_year: int) -> float:
        train, test = split(target_year)
        if train.empty or test.empty:
            return float("nan")
        head = fit_context_head(train, features, float(head_config["ridge_alpha"]))
        return _driver_event_mae(test, head.predict(test))

    baseline_2024 = evaluate([], selection_year)
    selected: list[str] = []
    trace: list[dict[str, Any]] = []
    current = baseline_2024
    for feature in candidates:
        trial = selected + [feature]
        trial_mae = evaluate(trial, selection_year)
        improved = np.isfinite(trial_mae) and trial_mae <= current + tolerance
        trace.append(
            {
                "feature": feature,
                "candidate_set": trial,
                "selection_year_mae": trial_mae,
                "previous_mae": current,
                "kept": bool(improved),
            }
        )
        if improved:
            selected.append(feature)
            current = trial_mae

    locked = {
        "baseline": evaluate([], locked_year),
        "selected": evaluate(selected, locked_year),
    }
    report = {
        "selection_year": selection_year,
        "locked_year": locked_year,
        "tolerance": tolerance,
        "baseline_selection_year_mae": baseline_2024,
        "selected_features": selected,
        "selected_selection_year_mae": current,
        "feature_trace": trace,
        "locked_year_mae": locked,
        "role": "features chosen only on selection-year time-forward; locked year reported not selected",
    }
    return selected, report


# --------------------------------------------------------------------------- #
# Layer C —— 加性毫秒重建 + 名次构念
# --------------------------------------------------------------------------- #


def _rank_map(values: Mapping[str, float]) -> dict[str, int]:
    ordered = sorted(values, key=lambda key: (values[key], key))
    return {key: index + 1 for index, key in enumerate(ordered)}


def _spearman(a: Mapping[str, int], b: Mapping[str, int]) -> float | None:
    keys = sorted(set(a) & set(b))
    if len(keys) < 2:
        return None
    left = pd.Series([float(a[key]) for key in keys])
    right = pd.Series([float(b[key]) for key in keys])
    value = left.corr(right, method="spearman")
    return float(value) if pd.notna(value) else None


def _kendall(a: Mapping[str, int], b: Mapping[str, int]) -> tuple[float | None, float | None]:
    keys = sorted(set(a) & set(b))
    if len(keys) < 2:
        return None, None
    concordant = discordant = 0
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            sign_a = np.sign(a[keys[i]] - a[keys[j]])
            sign_b = np.sign(b[keys[i]] - b[keys[j]])
            if sign_a * sign_b > 0:
                concordant += 1
            elif sign_a * sign_b < 0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None, None
    tau = (concordant - discordant) / total
    return float(tau), float(concordant / total)


def build_reconstruction(
    lap_v6: pd.DataFrame,
    reporting_teams: Sequence[str],
    config: Mapping[str, Any],
    classification_reference: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """把每 driver-event 的可加成分转有符号毫秒，并分开检查配速与赛果代理构念。"""

    settings = config["reconstruction"]
    four_team = lap_v6.loc[lap_v6["team_name"].isin(reporting_teams)].copy()
    group_keys = ["year", "meeting_name", "circuit_name", "driver_id", "team_name"]
    rows: list[dict[str, Any]] = []
    for keys, group in four_team.groupby(group_keys, observed=True, sort=True):
        year, meeting, circuit, driver, team = keys
        reference = float(group["session_reference"].median())

        def ms(column: str) -> float:
            return log_pct_to_ms(float(group[column].median()), reference)

        observed_z = float(group["target_log_pct"].median())
        expected_z = float(group["expected_target_z"].median())
        rows.append(
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "driver": str(driver),
                "team": str(team),
                "clean_laps": int(len(group)),
                "reference_lap_seconds": reference,
                "observed_pace_z_p50": observed_z,
                "expected_pace_z_p50": expected_z,
                "reconstruction_residual_ms": log_pct_to_ms(observed_z - expected_z, reference),
                "component_car_ms": -ms("car_state_z"),
                "component_driver_ms": -ms("driver_state_z"),
                "component_interaction_ms": -ms("interaction_state_z"),
                "component_context_ms": ms("context_prediction_z"),
                "component_execution_loss_ms": float(group["execution_loss_ms"].median()),
                "component_unexplained_ms": float(
                    group["model_residual_ms"].median()
                    - group["execution_loss_ms"].median()
                ),
                "identity_expected_z_p50": float(group["identity_expected_z"].median()),
                "context_adjusted_z_p50": float(group["context_adjusted_z"].median()),
            }
        )
    reconstruction = pd.DataFrame(rows)
    reconstruction["classification_proxy_rank"] = np.nan
    if classification_reference is not None and not classification_reference.empty:
        reference_columns = {
            "year",
            "meeting_name",
            "driver",
            "classification_proxy_rank",
        }
        missing = reference_columns - set(classification_reference.columns)
        if missing:
            raise ValueError(f"classification reference 缺少字段：{sorted(missing)}")
        reference = classification_reference[list(reference_columns)].copy()
        reference["year"] = pd.to_numeric(reference["year"], errors="raise").astype(int)
        reference["classification_proxy_rank"] = pd.to_numeric(
            reference["classification_proxy_rank"], errors="coerce"
        )
        if reference.duplicated(["year", "meeting_name", "driver"]).any():
            raise ValueError("classification reference 存在重复 driver-event 键。")
        reconstruction = reconstruction.drop(columns="classification_proxy_rank").merge(
            reference,
            how="left",
            on=["year", "meeting_name", "driver"],
            validate="one_to_one",
        )

    def _fit_stats(frame: pd.DataFrame) -> dict[str, float]:
        if frame.empty:
            return {"driver_events": 0, "mae_ms": None, "rmse_ms": None, "r2": None}
        obs = frame["observed_pace_z_p50"].to_numpy(dtype=float)
        exp = frame["expected_pace_z_p50"].to_numpy(dtype=float)
        ref = frame["reference_lap_seconds"].to_numpy(dtype=float)
        residual_ms = np.array(
            [log_pct_to_ms(o - e, r) for o, e, r in zip(obs, exp, ref)], dtype=float
        )
        ss_res = float(np.sum((obs - exp) ** 2))
        ss_tot = float(np.sum((obs - np.mean(obs)) ** 2))
        return {
            "driver_events": int(len(frame)),
            "mae_ms": float(np.mean(np.abs(residual_ms))),
            "rmse_ms": float(np.sqrt(np.mean(residual_ms**2))),
            "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else None,
        }

    quality_by_year = [
        {"year": int(year), **_fit_stats(group)}
        for year, group in reconstruction.groupby("year", sort=True)
    ]

    # 构念分层：配速模型序 vs 观测干净圈序；观测干净圈序 vs classification proxy。
    # 后者才是“配速≠赛果代理”，且仍然不是策略/可靠性等因素的因果归因。
    tolerance = int(settings["rank_tolerance"])
    anomaly_gap = int(settings["anomaly_rank_gap"])
    rank_rows: list[dict[str, Any]] = []
    pace_model_anomalies: list[dict[str, Any]] = []
    result_proxy_anomalies: list[dict[str, Any]] = []
    for (year, meeting), group in reconstruction.groupby(["year", "meeting_name"], sort=True):
        model_order = _rank_map(
            {row.driver: row.identity_expected_z_p50 for row in group.itertuples()}
        )
        observed_order = _rank_map(
            {row.driver: row.context_adjusted_z_p50 for row in group.itertuples()}
        )
        result_order = {
            str(row.driver): int(row.classification_proxy_rank)
            for row in group.itertuples()
            if pd.notna(row.classification_proxy_rank)
        }
        pace_tau, pace_pair_rate = _kendall(model_order, observed_order)
        result_tau, result_pair_rate = _kendall(observed_order, result_order)
        rank_rows.append(
            {
                "year": int(year),
                "meeting_name": str(meeting),
                "drivers": int(len(group)),
                "classification_proxy_drivers": int(len(result_order)),
                "model_vs_observed_spearman": _spearman(model_order, observed_order),
                "model_vs_observed_kendall_tau": pace_tau,
                "pairwise_explained_rate": pace_pair_rate,
                "observed_vs_classification_proxy_spearman": _spearman(
                    observed_order, result_order
                ),
                "observed_vs_classification_proxy_kendall_tau": result_tau,
                "observed_vs_classification_proxy_pairwise_rate": result_pair_rate,
                "model_vs_classification_proxy_spearman": _spearman(
                    model_order, result_order
                ),
                "model_combination_order": model_order,
                "observed_clean_pace_order": observed_order,
                "classification_proxy_order": result_order,
            }
        )
        for row in group.itertuples():
            gap = int(observed_order[row.driver]) - int(model_order[row.driver])
            if abs(gap) >= anomaly_gap:
                pace_model_anomalies.append(
                    {
                        "year": int(year),
                        "meeting_name": str(meeting),
                        "driver": row.driver,
                        "team": row.team,
                        "model_combination_rank": int(model_order[row.driver]),
                        "observed_clean_pace_rank": int(observed_order[row.driver]),
                        "rank_gap": gap,
                        "reconstruction_residual_ms": float(row.reconstruction_residual_ms),
                        "note": "模型组合序与观测干净圈序不一致；这是配速模型构念偏差，不是赛果异常。",
                    }
                )
            if pd.notna(row.classification_proxy_rank):
                result_gap = int(row.classification_proxy_rank) - int(
                    observed_order[row.driver]
                )
                if abs(result_gap) >= anomaly_gap:
                    result_proxy_anomalies.append(
                        {
                            "year": int(year),
                            "meeting_name": str(meeting),
                            "driver": row.driver,
                            "team": row.team,
                            "observed_clean_pace_rank": int(observed_order[row.driver]),
                            "classification_proxy_rank": int(row.classification_proxy_rank),
                            "rank_gap": result_gap,
                            "reconstruction_residual_ms": float(
                                row.reconstruction_residual_ms
                            ),
                            "note": "观测干净配速序与 classification proxy 不一致；只标记策略、可靠性、处罚、事故等待查原因，不作因果归因。",
                        }
                    )
    rank_frame = pd.DataFrame(rank_rows)

    def _mean(column: str) -> float | None:
        if rank_frame.empty or column not in rank_frame:
            return None
        values = pd.to_numeric(rank_frame[column], errors="coerce").dropna()
        return float(values.mean()) if len(values) else None

    return {
        "role": "additive_ms_reconstruction_plus_separate_pace_and_result_proxy_construct_checks",
        "driver_event_records": reconstruction.to_dict(orient="records"),
        "reconstruction_quality_overall": _fit_stats(reconstruction),
        "reconstruction_quality_by_year": quality_by_year,
        "rank_construct_check": {
            "events": rank_rows,
            "overall": {
                "events": int(len(rank_frame)),
                "mean_model_vs_observed_spearman": _mean(
                    "model_vs_observed_spearman"
                ),
                "mean_pairwise_explained_rate": _mean("pairwise_explained_rate"),
                "mean_observed_vs_classification_proxy_spearman": _mean(
                    "observed_vs_classification_proxy_spearman"
                ),
                "mean_observed_vs_classification_proxy_pairwise_rate": _mean(
                    "observed_vs_classification_proxy_pairwise_rate"
                ),
                "mean_model_vs_classification_proxy_spearman": _mean(
                    "model_vs_classification_proxy_spearman"
                ),
            },
            "anomalies_model_vs_observed_pace": pace_model_anomalies,
            "anomalies_pace_vs_classification_proxy": result_proxy_anomalies,
            "anomalies_result_gap": result_proxy_anomalies,
            "boundary": config["boundaries"]["rank_explanation_role"],
        },
    }


# --------------------------------------------------------------------------- #
# Layer D —— 半正态校正的理论上限
# --------------------------------------------------------------------------- #


def _half_normal_shortfall(
    values: np.ndarray,
    cap: float = 0.30,
    fastest_fraction: float = 0.10,
) -> float:
    """从最快圈尾部离散度估计 half-normal E[u]=sigma*sqrt(2/pi)，硬上限封顶。

    只看已接近极限的最快圈，避免把 Q1/Q2/Q3、进出站、保胎等异质慢圈误当执行损失，
    从而防止排位这类异质会话把理论上限抬到不现实的水平。
    """

    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size < 8:
        return 0.0
    frontier = float(np.quantile(array, float(fastest_fraction)))
    fast = array[array <= frontier]
    if fast.size >= 2:
        sigma = float(np.std(fast))
    else:
        sigma = max(frontier - float(np.quantile(array, 0.05)), 0.0)
    shortfall = sigma * np.sqrt(2.0 / np.pi)
    return float(min(max(shortfall, 0.0), cap))


def _bootstrap_half_normal_shortfall(
    event: pd.DataFrame,
    teams: Sequence[str],
    repetitions: int,
    seed: int,
    cap: float,
    fastest_fraction: float,
) -> dict[str, np.ndarray]:
    """按车手内重采样，让执行损失校正的不确定性进入最终 CI。"""

    rng = np.random.default_rng(int(seed))
    output = {team: np.zeros(int(repetitions), dtype=float) for team in teams}
    values_by_team_driver: dict[str, list[np.ndarray]] = {}
    for team in teams:
        values_by_team_driver[team] = [
            pd.to_numeric(group["driver_neutral_vehicle_z"], errors="coerce")
            .dropna()
            .to_numpy(dtype=float)
            for _, group in event.loc[event["team_name"] == team].groupby(
                "driver_id", observed=True
            )
            if len(group)
        ]
    for repetition in range(int(repetitions)):
        for team, driver_arrays in values_by_team_driver.items():
            resampled = [
                rng.choice(values, size=len(values), replace=True)
                for values in driver_arrays
                if len(values)
            ]
            if resampled:
                output[team][repetition] = _half_normal_shortfall(
                    np.concatenate(resampled),
                    cap=cap,
                    fastest_fraction=fastest_fraction,
                )
    return output


def build_car_ceiling(
    race_event: pd.DataFrame,
    qualifying_event: pd.DataFrame,
    teams: Sequence[str],
    frontier: Mapping[str, Any],
    year: int | None = None,
    meeting_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """对驾驶中和后的车辆前沿做半正态校正，得到 Q/R/融合 理论上限（带 CI）。"""

    reference_ms = float(frontier["reference_lap_seconds"])
    race_q = float(frontier["race_ceiling_quantile"])
    q_q = float(frontier["qualifying_ceiling_quantile"])
    baseline_q = float(frontier["usable_baseline_quantile"])
    floor_q = float(frontier["performance_floor_quantile"])
    weights = frontier["weekend_session_weights"]
    repetitions = int(frontier["bootstrap_repetitions"])
    seed = int(frontier["bootstrap_seed"])
    fastest_fraction = float(frontier.get("half_normal_fastest_fraction", 0.10))
    shortfall_cap = float(frontier.get("half_normal_shortfall_cap_z", 0.30))
    if year is None and "year" in race_event and len(race_event):
        year = int(race_event["year"].iloc[0])
    if meeting_name is None and "meeting_name" in race_event and len(race_event):
        meeting_name = str(race_event["meeting_name"].iloc[0])
    event_seed_parts = (year if year is not None else "unknown", meeting_name or "unknown")

    race_point = _frontier_point(
        race_event, "driver_neutral_vehicle_z", teams, race_q, baseline_q, floor_q
    )
    race_boot = bootstrap_event_frontiers(
        race_event,
        "driver_neutral_vehicle_z",
        teams,
        race_q,
        repetitions,
        stable_run_seed(seed, *event_seed_parts, "race_frontier"),
    )
    race_shortfall_boot = _bootstrap_half_normal_shortfall(
        race_event,
        teams,
        repetitions,
        stable_run_seed(seed, *event_seed_parts, "race_shortfall"),
        shortfall_cap,
        fastest_fraction,
    )
    if qualifying_event.empty:
        q_point: dict[str, Any] = {}
        q_boot = {
            team: {
                "robust_advantage_z": np.full(repetitions, np.nan),
                "strict_advantage_z": np.full(repetitions, np.nan),
            }
            for team in teams
        }
    else:
        q_point = _frontier_point(
            qualifying_event, "driver_neutral_vehicle_z", teams, q_q, 0.50, 0.90
        )
        q_boot = bootstrap_event_frontiers(
            qualifying_event,
            "driver_neutral_vehicle_z",
            teams,
            q_q,
            repetitions,
            stable_run_seed(seed, *event_seed_parts, "qualifying_frontier"),
        )
        q_shortfall_boot = _bootstrap_half_normal_shortfall(
            qualifying_event,
            teams,
            repetitions,
            stable_run_seed(seed, *event_seed_parts, "qualifying_shortfall"),
            shortfall_cap,
            fastest_fraction,
        )

    output: dict[str, dict[str, Any]] = {}
    # 半正态缺口：以每队执行损失上尾估计，换算成"上限比稳健前沿再快多少"的正向 z。
    for team in teams:
        if team not in race_point:
            continue
        race_group = race_event.loc[race_event["team_name"] == team, "driver_neutral_vehicle_z"]
        shortfall = _half_normal_shortfall(
            race_group.to_numpy(dtype=float), shortfall_cap, fastest_fraction
        )
        race_robust = race_boot[team]["robust_advantage_z"]
        race_ceiling = race_robust + race_shortfall_boot[team]
        available: list[tuple[float, np.ndarray]] = [(float(weights["Race"]), race_ceiling)]
        if team in q_point:
            q_group = qualifying_event.loc[
                qualifying_event["team_name"] == team, "driver_neutral_vehicle_z"
            ]
            q_shortfall = _half_normal_shortfall(
                q_group.to_numpy(dtype=float), shortfall_cap, fastest_fraction
            )
            q_ceiling = q_boot[team]["robust_advantage_z"] + q_shortfall_boot[team]
            available.append((float(weights["Qualifying"]), q_ceiling))
        else:
            q_shortfall = None
            q_ceiling = None
        weight_sum = sum(weight for weight, _ in available)
        fused = sum(weight * values for weight, values in available) / weight_sum
        baseline_ms = delta_z_to_ms(
            float(race_point[team]["baseline_raw_z"] - race_point[team]["strict_raw_z"]),
            reference_ms,
        )
        fused_summary = summarize_samples(fused)
        output[team] = {
            "race_ceiling_advantage_z": summarize_samples(race_ceiling),
            "race_half_normal_shortfall_z": shortfall,
            "race_half_normal_shortfall_uncertainty_z": summarize_samples(
                race_shortfall_boot[team]
            ),
            "qualifying_ceiling_advantage_z": (
                summarize_samples(q_ceiling) if q_ceiling is not None else None
            ),
            "qualifying_half_normal_shortfall_z": q_shortfall,
            "qualifying_half_normal_shortfall_uncertainty_z": (
                summarize_samples(q_shortfall_boot[team])
                if q_ceiling is not None
                else None
            ),
            "weekend_theoretical_ceiling_advantage_z": fused_summary,
            "weekend_theoretical_ceiling_90s_ms": (
                advantage_to_ms(float(fused_summary["median"]), reference_ms)
                if fused_summary["median"] is not None
                else None
            ),
            "ceiling_to_baseline_headroom_90s_ms": baseline_ms,
            "race_clean_laps": int(race_point[team]["laps"]),
            "ceiling_uncertainty_includes_shortfall_bootstrap": True,
            "boundary": "统计前沿+最快圈尾部 half-normal 形状执行损失校正，校正不确定性已进入 CI；非物理极限。",
        }
    return output


# --------------------------------------------------------------------------- #
# Layer B —— 信号/噪声分离度
# --------------------------------------------------------------------------- #


def _posterior_team_event_draws(
    posterior, year: int, meeting_name: str, circuit_name: str, teams: Sequence[str]
) -> dict[str, np.ndarray]:
    draws: dict[str, np.ndarray] = {}
    for team in teams:
        draws[team] = (
            _block_draws(posterior, "team_year", f"{year}|{team}")
            + _block_draws(posterior, "team_circuit", f"{team}|{circuit_name}")
            + _block_draws(posterior, "team_event", f"{year}|{meeting_name}|{team}")
        )
    return draws


def build_separation(
    race_event: pd.DataFrame,
    posterior,
    year: int,
    meeting_name: str,
    circuit_name: str,
    teams: Sequence[str],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """队间/队内方差比 + 后验排序正确概率 → 诚实的分离度等级。"""

    present = [team for team in teams if team in set(race_event["team_name"])]
    team_medians: dict[str, float] = {}
    team_clean_laps: dict[str, int] = {}
    within: list[float] = []
    grand: list[float] = []
    for team in present:
        subset = race_event.loc[race_event["team_name"] == team, "driver_neutral_vehicle_z"]
        values = pd.to_numeric(subset, errors="coerce").dropna().to_numpy(dtype=float)
        if values.size == 0:
            continue
        team_clean_laps[team] = int(values.size)
        median = float(np.median(values))
        team_medians[team] = median
        mad = float(1.4826 * np.median(np.abs(values - median)))
        within.append(mad**2)
        grand.extend(values.tolist())
    separation_index = None
    if len(team_medians) >= 2 and within:
        between = float(np.var(list(team_medians.values()), ddof=1))
        within_mean = float(np.mean(within))
        if within_mean > 1e-9:
            separation_index = between / within_mean

    # 后验排序正确概率（复用 Gibbs team_event draws）。
    draws = _posterior_team_event_draws(posterior, year, meeting_name, circuit_name, present)
    p_order = None
    top_p_rank_1: dict[str, float] = {}
    separated_pairs: list[dict[str, Any]] = []
    pair_confidences: list[float] = []
    if len(present) >= 2 and all(draws[team].size for team in present):
        stack = np.stack([draws[team].reshape(-1) for team in present])  # team × draws
        # z 越小车越快 → 优势 = 中心 - z。
        center = np.mean(stack, axis=0)
        advantage = center[None, :] - stack
        point_order = sorted(present, key=lambda team: float(np.mean(advantage[present.index(team)])), reverse=True)
        matches = np.ones(stack.shape[1], dtype=bool)
        for higher, lower in zip(point_order, point_order[1:]):
            matches &= advantage[present.index(higher)] > advantage[present.index(lower)]
        p_order = float(np.mean(matches))
        # rank[j] = 1 + (优势严格高于 j 的队数)：最快队 → 0 更快 → rank 1。
        ranks = 1 + np.sum(advantage[:, None, :] > advantage[None, :, :], axis=0)
        for index, team in enumerate(present):
            top_p_rank_1[team] = float(np.mean(ranks[index] == 1))
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                diff = advantage[i] - advantage[j]
                p_first_faster = float(np.mean(diff > 0))
                confidence = max(p_first_faster, 1.0 - p_first_faster)
                if p_first_faster >= 0.5:
                    faster_team, slower_team = present[i], present[j]
                else:
                    faster_team, slower_team = present[j], present[i]
                pair_confidences.append(confidence)
                separated_pairs.append(
                    {
                        "comparison_first_team": present[i],
                        "comparison_second_team": present[j],
                        "p_first_faster": p_first_faster,
                        "faster_team": faster_team,
                        "slower_team": slower_team,
                        "p_faster_than_slower": confidence,
                        "clean_separation": bool(confidence >= 0.90),
                    }
                )

    # 双证据门控：最终等级取方差比等级和后验成对置信度等级的较低者；
    # 任一车队样本不足时强制 low，避免少数极端圈制造“分明”。
    mean_pair_confidence = float(np.mean(pair_confidences)) if pair_confidences else None
    high_conf = float(settings["high_pair_confidence"])
    medium_conf = float(settings["medium_pair_confidence"])
    high_index = float(settings["separation_index_reference_high"])
    medium_index = float(settings["separation_index_reference_medium"])
    minimum_laps = int(settings["minimum_team_event_clean_laps"])
    posterior_grade = "low"
    if mean_pair_confidence is not None and mean_pair_confidence >= high_conf:
        posterior_grade = "high"
    elif mean_pair_confidence is not None and mean_pair_confidence >= medium_conf:
        posterior_grade = "medium"
    variance_grade = "low"
    if separation_index is not None and separation_index >= high_index:
        variance_grade = "high"
    elif separation_index is not None and separation_index >= medium_index:
        variance_grade = "medium"
    coverage_sufficient = bool(team_clean_laps) and all(
        team_clean_laps.get(team, 0) >= minimum_laps for team in present
    )
    grade_order = {"low": 0, "medium": 1, "high": 2}
    grade = min((posterior_grade, variance_grade), key=grade_order.get)
    if not coverage_sufficient:
        grade = "low"
    return {
        "separation_index_between_within": separation_index,
        "posterior_p_full_order_correct": p_order,
        "mean_pairwise_ordering_confidence": mean_pair_confidence,
        "clean_separated_pair_count": int(
            sum(row["clean_separation"] for row in separated_pairs)
        ),
        "posterior_top_team_p_rank_1": top_p_rank_1,
        "separated_pairs": separated_pairs,
        "team_clean_laps": team_clean_laps,
        "minimum_team_event_clean_laps": minimum_laps,
        "coverage_sufficient": coverage_sufficient,
        "grade_components": {
            "variance_ratio": variance_grade,
            "posterior_pairwise": posterior_grade,
            "sample_coverage": "pass" if coverage_sufficient else "fail",
        },
        "separation_grade": grade,
        "boundary": "等级取方差比与后验成对置信度的较低者，并受逐队最小样本门控；不改任何先验强度。",
    }


def stable_run_seed(base_seed: int, *parts: object) -> int:
    token = "|".join(str(part) for part in parts).encode("utf-8")
    digest = int.from_bytes(hashlib.sha256(token).digest()[:4], "big")
    return int((int(base_seed) + digest) % (2**32 - 1))
