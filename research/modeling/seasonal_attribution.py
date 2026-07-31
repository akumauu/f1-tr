"""2023–2025 全赛季车辆—车手鲁棒分层归因工具。

本模块刻意把“预测模型”和“赛后描述模型”分开：前者不含目标分站虚拟变量，
用于时间外验证；后者加入强收缩的车队分站与车手分站更新，用于复盘动态状态。
两者都不使用赛果、积分或队友差作为训练标签。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import Ridge
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from modeling.entities import normalize_team_entities


NUMERIC_FEATURES = (
    "lap_fraction",
    "lap_fraction_sq",
    "tyre_age",
    "tyre_age_sq",
)

BLOCK_COLUMNS = {
    "compound": "compound",
    "circuit": "circuit_name",
    "team_year": "team_year",
    "team_circuit": "team_circuit",
    "driver": "driver_id",
    "driver_year": "driver_year",
    "driver_team": "driver_team",
    "team_event": "team_event",
    "driver_event": "driver_event",
}

CONTEXT_BLOCKS = ("compound", "circuit")
TEAM_BLOCKS = CONTEXT_BLOCKS + ("team_year", "team_circuit")
JOINT_BLOCKS = TEAM_BLOCKS + ("driver", "driver_year", "driver_team")
DESCRIPTIVE_BLOCKS = JOINT_BLOCKS + ("team_event", "driver_event")


def prepare_season_frame(
    frame: pd.DataFrame,
    aliases: Mapping[str, str],
    season_order: Mapping[str, Sequence[str]],
    session_name: str = "Race",
) -> pd.DataFrame:
    """过滤场景、规范实体并构造稳定的层级键。"""

    work = normalize_team_entities(frame, aliases)
    work = work.loc[work["session_name"].astype(str) == session_name].copy()
    work["year"] = pd.to_numeric(work["year"], errors="raise").astype(int)
    work["compound"] = work["compound"].fillna("UNKNOWN").astype(str)
    work["driver_id"] = work["driver_id"].astype(str)
    work["team_name"] = work["team_name"].astype(str)
    work["circuit_name"] = work["circuit_name"].astype(str)
    work["meeting_name"] = work["meeting_name"].astype(str)
    work["meeting_key"] = work["year"].astype(str) + "|" + work["meeting_name"]

    round_maps = {
        int(year): {meeting: index + 1 for index, meeting in enumerate(meetings)}
        for year, meetings in season_order.items()
    }
    work["round_index"] = [
        round_maps.get(int(year), {}).get(str(meeting))
        for year, meeting in zip(work["year"], work["meeting_name"])
    ]
    missing = work.loc[work["round_index"].isna(), ["year", "meeting_name"]]
    if not missing.empty:
        values = missing.drop_duplicates().to_dict(orient="records")
        raise ValueError(f"赛季顺序缺少分站：{values}")
    work["round_index"] = work["round_index"].astype(int)

    year = work["year"].astype(str)
    work["team_year"] = year + "|" + work["team_name"]
    work["team_circuit"] = work["team_name"] + "|" + work["circuit_name"]
    work["driver_year"] = year + "|" + work["driver_id"]
    work["driver_team"] = work["driver_id"] + "|" + work["team_name"]
    work["team_event"] = work["meeting_key"] + "|" + work["team_name"]
    work["driver_event"] = work["meeting_key"] + "|" + work["driver_id"]

    for column in NUMERIC_FEATURES + ("target_log_pct",):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["target_log_pct"]).reset_index(drop=True)
    return work


def meeting_driver_weights(frame: pd.DataFrame) -> np.ndarray:
    """让每个分站中的每位车手拥有相同总权重。"""

    counts = frame.groupby(["meeting_key", "driver_id"])["driver_id"].transform(
        "size"
    )
    weights = 1.0 / counts.to_numpy(dtype=float)
    return weights / float(np.mean(weights))


@dataclass
class SeasonalAttributionFit:
    """已拟合的分块惩罚模型。"""

    blocks: tuple[str, ...]
    penalties: dict[str, float]
    encoders: dict[str, OneHotEncoder]
    numeric_scaler: StandardScaler
    numeric_medians: pd.Series
    model: Ridge
    slices: dict[str, slice]
    train_residuals: np.ndarray
    base_weights: np.ndarray
    robust_weights: np.ndarray

    def _matrix(self, frame: pd.DataFrame) -> sparse.csr_matrix:
        numeric = (
            frame[list(NUMERIC_FEATURES)]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(self.numeric_medians)
        )
        pieces: list[sparse.spmatrix] = [
            sparse.csr_matrix(self.numeric_scaler.transform(numeric))
        ]
        for block in self.blocks:
            column = BLOCK_COLUMNS[block]
            encoded = self.encoders[block].transform(
                frame[[column]].fillna("UNKNOWN").astype(str)
            )
            pieces.append(encoded / np.sqrt(self.penalties[block]))
        return sparse.hstack(pieces, format="csr")

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(self._matrix(frame)), dtype=float)

    def effect_map(self, block: str) -> dict[str, float]:
        """返回块的实际 z 效应；负值代表让圈时更快。"""

        if block not in self.encoders:
            return {}
        values = self.model.coef_[self.slices[block]] / np.sqrt(
            self.penalties[block]
        )
        categories = self.encoders[block].categories_[0]
        return {
            str(category): float(value)
            for category, value in zip(categories, values)
        }

    def components(self, frame: pd.DataFrame) -> pd.DataFrame:
        """返回可加的上下文、车辆、车手、组合与分站执行组件。"""

        matrix = self._matrix(frame)
        numeric_end = len(NUMERIC_FEATURES)
        context = (
            np.asarray(matrix[:, :numeric_end] @ self.model.coef_[:numeric_end]).reshape(-1)
            + float(self.model.intercept_)
        )
        result = pd.DataFrame({"context_z": context}, index=frame.index)
        for block in self.blocks:
            contribution = np.asarray(
                matrix[:, self.slices[block]] @ self.model.coef_[self.slices[block]]
            ).reshape(-1)
            if block in CONTEXT_BLOCKS:
                result["context_z"] = result["context_z"] + contribution
            else:
                result[f"{block}_z"] = contribution
        component_columns = [column for column in result if column.endswith("_z")]
        result["prediction_z"] = result[component_columns].sum(axis=1)
        return result


def _penalty_for_block(block: str, penalties: Mapping[str, float]) -> float:
    if block in CONTEXT_BLOCKS:
        return 1.0
    value = float(penalties[block])
    if value <= 0:
        raise ValueError(f"{block} 惩罚必须为正数。")
    return value


def fit_seasonal_model(
    frame: pd.DataFrame,
    penalties: Mapping[str, float],
    blocks: Sequence[str] = JOINT_BLOCKS,
    irls_iterations: int = 3,
    huber_k: float = 1.5,
) -> SeasonalAttributionFit:
    """拟合分块 ridge，并用 Huber IRLS 限制极端圈和极端队友组合杠杆。"""

    if frame.empty:
        raise ValueError("全赛季模型输入不能为空。")
    if irls_iterations < 1 or huber_k <= 0:
        raise ValueError("IRLS 次数和 Huber k 必须为正数。")

    work = frame.reset_index(drop=True)
    numeric = work[list(NUMERIC_FEATURES)].apply(pd.to_numeric, errors="coerce")
    numeric_medians = numeric.median().fillna(0.0)
    numeric_scaler = StandardScaler()
    numeric_matrix = sparse.csr_matrix(
        numeric_scaler.fit_transform(numeric.fillna(numeric_medians))
    )

    encoders: dict[str, OneHotEncoder] = {}
    slices: dict[str, slice] = {}
    normalized_penalties: dict[str, float] = {}
    pieces: list[sparse.spmatrix] = [numeric_matrix]
    offset = numeric_matrix.shape[1]
    for block in blocks:
        if block not in BLOCK_COLUMNS:
            raise ValueError(f"未知模型块：{block}")
        column = BLOCK_COLUMNS[block]
        encoder = OneHotEncoder(
            handle_unknown="ignore", sparse_output=True, dtype=np.float64
        )
        encoded = encoder.fit_transform(work[[column]].fillna("UNKNOWN").astype(str))
        penalty = _penalty_for_block(block, penalties)
        encoders[block] = encoder
        normalized_penalties[block] = penalty
        pieces.append(encoded / np.sqrt(penalty))
        slices[block] = slice(offset, offset + encoded.shape[1])
        offset += encoded.shape[1]

    design = sparse.hstack(pieces, format="csr")
    target = work["target_log_pct"].to_numpy(dtype=float)
    base_weights = meeting_driver_weights(work)
    robust_weights = np.ones(len(work), dtype=float)
    model = Ridge(alpha=1.0, solver="lsqr", tol=1e-5, max_iter=30000)
    for _ in range(irls_iterations):
        weights = base_weights * robust_weights
        weights = weights / float(np.mean(weights))
        model.fit(design, target, sample_weight=weights)
        residuals = target - np.asarray(model.predict(design), dtype=float)
        center = float(np.median(residuals))
        scale = 1.4826 * float(np.median(np.abs(residuals - center)))
        if scale <= 1e-12:
            robust_weights = np.ones(len(work), dtype=float)
            break
        distance = np.abs(residuals - center)
        robust_weights = np.minimum(1.0, huber_k * scale / np.maximum(distance, 1e-12))

    prediction = np.asarray(model.predict(design), dtype=float)
    return SeasonalAttributionFit(
        blocks=tuple(blocks),
        penalties=normalized_penalties,
        encoders=encoders,
        numeric_scaler=numeric_scaler,
        numeric_medians=numeric_medians,
        model=model,
        slices=slices,
        train_residuals=target - prediction,
        base_weights=base_weights,
        robust_weights=robust_weights,
    )


def split_time_forward(
    frame: pd.DataFrame, target_year: int, adaptation_races: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """以前序赛季和目标年最初 N 站拟合，目标年其余分站验证。"""

    train = frame.loc[
        (frame["year"] < target_year)
        | ((frame["year"] == target_year) & (frame["round_index"] <= adaptation_races))
    ].copy()
    validation = frame.loc[
        (frame["year"] == target_year) & (frame["round_index"] > adaptation_races)
    ].copy()
    if train.empty or validation.empty:
        raise ValueError("时间外切分为空，请检查目标年和适配站数。")
    return train.reset_index(drop=True), validation.reset_index(drop=True)


def metric_bundle(
    frame: pd.DataFrame,
    prediction: np.ndarray,
    train_residuals: np.ndarray | None = None,
) -> dict[str, float]:
    """计算逐圈和分站等权误差，以及可选经验预测区间覆盖。"""

    target = frame["target_log_pct"].to_numpy(dtype=float)
    error = prediction - target
    event_mae = pd.DataFrame(
        {"meeting_key": frame["meeting_key"].to_numpy(), "abs_error": np.abs(error)}
    ).groupby("meeting_key")["abs_error"].mean()
    output = {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "bias": float(np.mean(error)),
        "event_balanced_mae": float(event_mae.mean()),
        "event_count": int(event_mae.size),
        "row_count": int(len(frame)),
    }
    if train_residuals is not None and len(train_residuals):
        absolute = np.abs(np.asarray(train_residuals, dtype=float))
        for level in (80, 95):
            nominal = level / 100.0
            finite_sample_level = min(
                1.0, np.ceil((len(absolute) + 1) * nominal) / len(absolute)
            )
            half_width = float(
                np.quantile(absolute, finite_sample_level, method="higher")
            )
            covered = np.abs(target - prediction) <= half_width
            output[f"interval_{level}_coverage"] = float(np.mean(covered))
            output[f"interval_{level}_width_z"] = float(2.0 * half_width)
    return output


def log_pct_to_ms(delta_z: float, reference_lap_seconds: float = 90.0) -> float:
    """将 z=100*log(t/t_ref) 精确转换成参考圈毫秒差。"""

    return float(np.expm1(float(delta_z) / 100.0) * reference_lap_seconds * 1000.0)


def _mean_effect(mapping: Mapping[str, float], keys: Sequence[str]) -> float:
    values = [float(mapping[key]) for key in keys if key in mapping]
    return float(np.mean(values)) if values else 0.0


def extract_season_ratings(
    fit: SeasonalAttributionFit,
    frame: pd.DataFrame,
    reporting_teams: Sequence[str],
    years: Sequence[int] = (2023, 2024, 2025),
) -> dict[str, list[dict[str, Any]]]:
    """提取标准车手车辆评分和标准车辆车手评分。"""

    effects = {block: fit.effect_map(block) for block in fit.blocks}
    cars: list[dict[str, Any]] = []
    drivers: list[dict[str, Any]] = []

    for year in years:
        year_frame = frame.loc[frame["year"] == year]
        circuits = sorted(year_frame["circuit_name"].astype(str).unique())
        car_rows: list[dict[str, Any]] = []
        for team in reporting_teams:
            team_year_z = effects.get("team_year", {}).get(f"{year}|{team}", 0.0)
            circuit_z = _mean_effect(
                effects.get("team_circuit", {}),
                [f"{team}|{circuit}" for circuit in circuits],
            )
            event_keys = year_frame.loc[
                year_frame["team_name"] == team, "team_event"
            ].drop_duplicates().astype(str).tolist()
            event_z = _mean_effect(effects.get("team_event", {}), event_keys)
            car_rows.append(
                {
                    "year": int(year),
                    "team": team,
                    "team_year_z": float(team_year_z),
                    "team_circuit_mean_z": float(circuit_z),
                    "team_event_mean_z": float(event_z),
                    "car_total_z": float(team_year_z + circuit_z + event_z),
                    "event_count": len(event_keys),
                }
            )
        center = float(np.mean([row["car_total_z"] for row in car_rows]))
        fastest = min(row["car_total_z"] for row in car_rows)
        for row in car_rows:
            row["car_pace_advantage_z"] = float(center - row["car_total_z"])
            row["delta_to_fastest_90s_ms"] = log_pct_to_ms(
                row["car_total_z"] - fastest
            )
        car_rows.sort(key=lambda row: row["car_total_z"])
        for rank, row in enumerate(car_rows, 1):
            row["rank"] = rank
        cars.extend(car_rows)

        roster = (
            year_frame.loc[year_frame["team_name"].isin(reporting_teams)]
            .groupby(["driver_id", "team_name"], as_index=False)
            .size()
            .sort_values(["driver_id", "size"], ascending=[True, False])
            .drop_duplicates("driver_id")
        )
        driver_rows: list[dict[str, Any]] = []
        for row in roster.itertuples(index=False):
            driver = str(row.driver_id)
            team = str(row.team_name)
            base_z = effects.get("driver", {}).get(driver, 0.0)
            year_z = effects.get("driver_year", {}).get(f"{year}|{driver}", 0.0)
            interaction_z = effects.get("driver_team", {}).get(
                f"{driver}|{team}", 0.0
            )
            event_keys = year_frame.loc[
                year_frame["driver_id"] == driver, "driver_event"
            ].drop_duplicates().astype(str).tolist()
            event_z = _mean_effect(effects.get("driver_event", {}), event_keys)
            driver_rows.append(
                {
                    "year": int(year),
                    "driver": driver,
                    "team": team,
                    "driver_base_z": float(base_z),
                    "driver_year_z": float(year_z),
                    "driver_pace_z": float(base_z + year_z),
                    "interaction_z": float(interaction_z),
                    "event_execution_mean_z": float(event_z),
                    "sample_count": int(row.size),
                }
            )
        if driver_rows:
            driver_center = float(
                np.mean([row["driver_pace_z"] for row in driver_rows])
            )
            for row in driver_rows:
                row["driver_pace_advantage_z"] = float(
                    driver_center - row["driver_pace_z"]
                )
                row["driver_pace_advantage_90s_ms"] = -log_pct_to_ms(
                    row["driver_pace_z"] - driver_center
                )
            driver_rows.sort(key=lambda row: row["driver_pace_z"])
            for rank, row in enumerate(driver_rows, 1):
                row["rank"] = rank
        drivers.extend(driver_rows)

    return {"cars": cars, "drivers": drivers}


def local_live_timing_reference(
    year: int,
    raw_root: Path,
    aliases: Mapping[str, str],
    sessions: Sequence[str] = ("Race", "Sprint"),
) -> dict[str, Any]:
    """从本地逐车手 laptimes.json 构造赛道内分类与积分代理。

    该代理不含赛后取消资格、罚时复核和 FIA 最终分类修订，因此只能用于排名
    方向检查，不能称为官方积分。
    """

    race_points = (25, 18, 15, 12, 10, 8, 6, 4, 2, 1)
    sprint_points = (8, 7, 6, 5, 4, 3, 2, 1)
    driver_points: dict[str, float] = {}
    team_points: dict[str, float] = {}
    events: list[dict[str, Any]] = []
    file_count = 0

    for session in sessions:
        for session_dir in sorted(raw_root.glob(f"*/{session}")):
            classifications: list[dict[str, Any]] = []
            for path in sorted(session_dir.glob("*/laptimes.json")):
                try:
                    import json

                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                file_count += 1
                laps = payload.get("lap", [])
                positions = payload.get("pos", [])
                valid_laps = [int(value) for value in laps if isinstance(value, (int, float))]
                valid_positions = [
                    int(value) for value in positions if isinstance(value, (int, float))
                ]
                if not valid_laps or not valid_positions:
                    continue
                team = next(
                    (
                        str(value)
                        for value in reversed(payload.get("team", []))
                        if value not in (None, "None", "")
                    ),
                    "UNKNOWN",
                )
                team = str(aliases.get(team, team))
                lap_times = [
                    float(value)
                    for value in payload.get("time", [])
                    if isinstance(value, (int, float)) and 0 < float(value) < 300
                ]
                classifications.append(
                    {
                        "driver": path.parent.name,
                        "team": team,
                        "position": valid_positions[-1],
                        "completed_laps": max(valid_laps),
                        "fastest_lap": min(lap_times) if lap_times else None,
                    }
                )
            if len(classifications) < 10:
                continue
            max_laps = max(row["completed_laps"] for row in classifications)
            scale = race_points if session == "Race" else sprint_points
            for row in classifications:
                row["classified_proxy"] = row["completed_laps"] >= 0.9 * max_laps
                points = 0.0
                if row["classified_proxy"] and 1 <= row["position"] <= len(scale):
                    points = float(scale[row["position"] - 1])
                row["points_proxy"] = points
                driver_points[row["driver"]] = driver_points.get(row["driver"], 0.0) + points
                team_points[row["team"]] = team_points.get(row["team"], 0.0) + points

            if session == "Race" and year <= 2024:
                fastest = min(
                    (row for row in classifications if row["fastest_lap"] is not None),
                    key=lambda row: row["fastest_lap"],
                    default=None,
                )
                if fastest is not None and fastest["position"] <= 10:
                    fastest["points_proxy"] += 1.0
                    driver_points[fastest["driver"]] += 1.0
                    team_points[fastest["team"]] += 1.0

            team_event: dict[str, dict[str, Any]] = {}
            for row in classifications:
                target = team_event.setdefault(
                    row["team"], {"team": row["team"], "points_proxy": 0.0, "best_position": 99}
                )
                target["points_proxy"] += row["points_proxy"]
                target["best_position"] = min(target["best_position"], row["position"])
            event_teams = sorted(
                team_event.values(),
                key=lambda row: (-row["points_proxy"], row["best_position"]),
            )
            events.append(
                {
                    "meeting_name": session_dir.parent.name,
                    "session_name": session,
                    "winner_team_proxy": min(
                        classifications, key=lambda row: row["position"]
                    )["team"],
                    "teams": event_teams,
                    "drivers": sorted(
                        classifications, key=lambda row: row["position"]
                    ),
                }
            )

    team_table = [
        {"team": team, "points_proxy": float(points)}
        for team, points in team_points.items()
    ]
    team_table.sort(key=lambda row: -row["points_proxy"])
    for rank, row in enumerate(team_table, 1):
        row["rank_proxy"] = rank
    driver_table = [
        {"driver": driver, "points_proxy": float(points)}
        for driver, points in driver_points.items()
    ]
    driver_table.sort(key=lambda row: -row["points_proxy"])
    for rank, row in enumerate(driver_table, 1):
        row["rank_proxy"] = rank
    return {
        "year": int(year),
        "mode": "local_live_timing_classification_proxy",
        "points_are_official": False,
        "file_count": file_count,
        "event_count": len(events),
        "teams": team_table,
        "drivers": driver_table,
        "events": events,
        "limitations": [
            "不含赛后取消资格、罚时复核和最终分类修订。",
            "只用于车队/车手赛季排名方向与单站赛果的外部效度检查。",
            "不进入模型拟合、参数选择或预测标签。",
        ],
    }
