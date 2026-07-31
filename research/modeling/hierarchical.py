"""透明的历史车手先验与单站分层收缩模型。

该模块实现 M1 圈级原型，不声称完成真正的动态贝叶斯后验。单站中车手
与人车交互共线，因此使用上一赛季车手 ridge 效应作为固定先验偏移，并
把当站剩余差异分配给较弱收缩的车辆状态和较强收缩的车手当站更新。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


HISTORICAL_CATEGORICAL = (
    "compound",
    "circuit_name",
    "session_name",
    "driver_id",
    "team_name",
)
NUMERIC_FEATURES = (
    "lap_fraction",
    "lap_fraction_sq",
    "tyre_age",
    "tyre_age_sq",
)


def make_historical_pipeline(alpha: float) -> Pipeline:
    """构建与首轮实验兼容的历史车手—车队 ridge。"""

    categorical = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "onehot",
                OneHotEncoder(
                    handle_unknown="ignore", sparse_output=True, dtype=np.float64
                ),
            ),
        ]
    )
    numeric = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            ("cat", categorical, list(HISTORICAL_CATEGORICAL)),
            ("num", numeric, list(NUMERIC_FEATURES)),
        ],
        sparse_threshold=0.2,
    )
    return Pipeline(
        [
            ("preprocess", preprocess),
            (
                "model",
                Ridge(alpha=float(alpha), solver="lsqr", tol=1e-4, max_iter=20000),
            ),
        ]
    )


def fit_historical_driver_prior(
    frame: pd.DataFrame, alpha: float
) -> tuple[dict[str, float], Pipeline]:
    """拟合历史模型并返回零均值车手速度优势，正值表示更快。"""

    features = list(HISTORICAL_CATEGORICAL + NUMERIC_FEATURES)
    pipeline = make_historical_pipeline(alpha)
    pipeline.fit(frame[features], frame["target_log_pct"].to_numpy(dtype=float))
    names = pipeline.named_steps["preprocess"].get_feature_names_out()
    coefficients = pipeline.named_steps["model"].coef_
    prefix = "cat__driver_id_"
    raw = {
        str(name)[len(prefix) :]: -float(coefficient)
        for name, coefficient in zip(names, coefficients)
        if str(name).startswith(prefix)
    }
    center = float(np.mean(list(raw.values()))) if raw else 0.0
    return {driver: value - center for driver, value in raw.items()}, pipeline


def log_pct_delta_to_ms(delta_log_pct: float, reference_lap_seconds: float) -> float:
    """把 ``100*log(t/t_ref)`` 差值精确换算为毫秒。"""

    return float(np.expm1(float(delta_log_pct) / 100.0) * reference_lap_seconds * 1000)


@dataclass
class EventModelFit:
    """单站分层收缩模型及其可解释组件。"""

    compound_encoder: OneHotEncoder
    numeric_scaler: StandardScaler
    numeric_medians: pd.Series
    context_coefficients: np.ndarray
    centered_intercept: float
    team_effect_z: dict[str, float]
    driver_update_z: dict[str, float]
    driver_prior_pace: dict[str, float]
    team_penalty: float
    driver_update_penalty: float
    train_residuals: np.ndarray

    def _context_matrix(self, frame: pd.DataFrame) -> sparse.csr_matrix:
        compound = self.compound_encoder.transform(frame[["compound"]].astype(str))
        numeric = (
            frame[list(NUMERIC_FEATURES)]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(self.numeric_medians)
        )
        scaled = sparse.csr_matrix(self.numeric_scaler.transform(numeric))
        return sparse.hstack([compound, scaled], format="csr")

    def components(self, frame: pd.DataFrame) -> pd.DataFrame:
        """返回逐圈可加的历史车手、上下文、车辆和当站更新组件。"""

        context = np.asarray(
            self._context_matrix(frame) @ self.context_coefficients
        ).reshape(-1)
        drivers = frame["driver_id"].astype(str)
        teams = frame["team_name"].astype(str)
        prior_pace = drivers.map(self.driver_prior_pace).fillna(0.0).to_numpy(dtype=float)
        prior_z = -prior_pace
        team_z = teams.map(self.team_effect_z).fillna(0.0).to_numpy(dtype=float)
        driver_update_z = (
            drivers.map(self.driver_update_z).fillna(0.0).to_numpy(dtype=float)
        )
        context_z = context + self.centered_intercept
        prediction = prior_z + context_z + team_z + driver_update_z
        return pd.DataFrame(
            {
                "historical_driver_prior_z": prior_z,
                "context_z": context_z,
                "team_z": team_z,
                "driver_event_update_z": driver_update_z,
                "prediction_z": prediction,
            },
            index=frame.index,
        )

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.components(frame)["prediction_z"].to_numpy(dtype=float)


def _numeric_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    numeric = frame[list(NUMERIC_FEATURES)].apply(pd.to_numeric, errors="coerce")
    medians = numeric.median().fillna(0.0)
    return numeric.fillna(medians), medians


def fit_event_model(
    frame: pd.DataFrame,
    driver_prior_pace: Mapping[str, float],
    team_penalty: float,
    driver_update_penalty: float,
) -> EventModelFit:
    """拟合单场比赛的车辆状态与强收缩车手当站更新。

    车辆虚拟变量的惩罚小于车手当站更新，体现“同队共同残差优先归入车辆，
    队友间差异才归入当站车手/组合项”的透明识别约束。
    """

    if frame.empty:
        raise ValueError("单站模型输入不能为空。")
    if float(team_penalty) <= 0 or float(driver_update_penalty) <= 0:
        raise ValueError("收缩惩罚必须为正数。")

    work = frame.reset_index(drop=True).copy()
    work["compound"] = work["compound"].fillna("UNKNOWN").astype(str)
    work["driver_id"] = work["driver_id"].astype(str)
    work["team_name"] = work["team_name"].astype(str)

    compound_encoder = OneHotEncoder(
        handle_unknown="ignore", sparse_output=True, dtype=np.float64
    )
    compound = compound_encoder.fit_transform(work[["compound"]])
    numeric, numeric_medians = _numeric_frame(work)
    numeric_scaler = StandardScaler()
    numeric_scaled = sparse.csr_matrix(numeric_scaler.fit_transform(numeric))

    team_encoder = OneHotEncoder(
        handle_unknown="ignore", sparse_output=True, dtype=np.float64
    )
    driver_encoder = OneHotEncoder(
        handle_unknown="ignore", sparse_output=True, dtype=np.float64
    )
    team = team_encoder.fit_transform(work[["team_name"]]) / np.sqrt(team_penalty)
    driver = driver_encoder.fit_transform(work[["driver_id"]]) / np.sqrt(
        driver_update_penalty
    )
    context = sparse.hstack([compound, numeric_scaled], format="csr")
    design = sparse.hstack([context, team, driver], format="csr")

    prior_pace = (
        work["driver_id"].map(driver_prior_pace).fillna(0.0).to_numpy(dtype=float)
    )
    target = work["target_log_pct"].to_numpy(dtype=float) + prior_pace
    model = Ridge(alpha=1.0, solver="lsqr", tol=1e-5, max_iter=20000)
    model.fit(design, target)

    context_count = context.shape[1]
    team_categories = [str(value) for value in team_encoder.categories_[0]]
    driver_categories = [str(value) for value in driver_encoder.categories_[0]]
    team_end = context_count + len(team_categories)
    context_coefficients = np.asarray(model.coef_[:context_count], dtype=float)
    raw_team = {
        team_name: float(value) / np.sqrt(team_penalty)
        for team_name, value in zip(
            team_categories, model.coef_[context_count:team_end]
        )
    }
    raw_driver = {
        driver_id: float(value) / np.sqrt(driver_update_penalty)
        for driver_id, value in zip(driver_categories, model.coef_[team_end:])
    }

    driver_team = (
        work[["driver_id", "team_name"]].drop_duplicates().set_index("driver_id")
    )
    if driver_team.index.duplicated().any():
        raise ValueError("同一单站 driver_id 不能对应多个车队。")
    team_driver_mean: dict[str, float] = {}
    for team_name, subset in driver_team.reset_index().groupby("team_name"):
        values = [raw_driver.get(str(driver_id), 0.0) for driver_id in subset["driver_id"]]
        team_driver_mean[str(team_name)] = float(np.mean(values)) if values else 0.0
    centered_driver = {
        driver_id: value
        - team_driver_mean.get(str(driver_team.loc[driver_id, "team_name"]), 0.0)
        for driver_id, value in raw_driver.items()
    }
    absorbed_team = {
        team_name: value + team_driver_mean.get(team_name, 0.0)
        for team_name, value in raw_team.items()
    }
    team_center = float(np.mean(list(absorbed_team.values())))
    centered_team = {
        team_name: value - team_center for team_name, value in absorbed_team.items()
    }
    centered_intercept = float(model.intercept_) + team_center

    result = EventModelFit(
        compound_encoder=compound_encoder,
        numeric_scaler=numeric_scaler,
        numeric_medians=numeric_medians,
        context_coefficients=context_coefficients,
        centered_intercept=centered_intercept,
        team_effect_z=centered_team,
        driver_update_z=centered_driver,
        driver_prior_pace={str(k): float(v) for k, v in driver_prior_pace.items()},
        team_penalty=float(team_penalty),
        driver_update_penalty=float(driver_update_penalty),
        train_residuals=np.array([], dtype=float),
    )
    prediction = result.predict(work)
    result.train_residuals = work["target_log_pct"].to_numpy(dtype=float) - prediction
    return result


def context_standardized_laps(frame: pd.DataFrame, fit: EventModelFit) -> pd.DataFrame:
    """生成车辆包络和车手提取所需的逐圈标准化量。"""

    work = frame.reset_index(drop=True).copy()
    components = fit.components(work).reset_index(drop=True)
    observed = work["target_log_pct"].to_numpy(dtype=float)
    standardized = observed - components["context_z"].to_numpy(dtype=float)
    result = pd.concat([work, components], axis=1)
    result["context_standardized_z"] = standardized
    result["vehicle_lap_z"] = (
        standardized
        - result["historical_driver_prior_z"]
        - result["driver_event_update_z"]
    )
    # 车手提取视角固定车辆、保留 D + I + execution；若再扣除历史车手先验，
    # 得到的只是“相对既往能力的超预期”，不是规范要求的车手总体发挥。
    result["driver_on_car_lap_z"] = standardized
    result["driver_event_execution_lap_z"] = (
        standardized - result["historical_driver_prior_z"]
    )
    result["model_residual_z"] = observed - result["prediction_z"]
    return result


def sensitivity_grid(
    frame: pd.DataFrame,
    driver_prior_pace: Mapping[str, float],
    reporting_teams: Sequence[str],
    team_penalties: Sequence[float],
    driver_update_penalties: Sequence[float],
) -> list[dict[str, object]]:
    """运行预注册收缩网格；只报告稳定性，不据目标赛结果选参。"""

    rows: list[dict[str, object]] = []
    for team_penalty in team_penalties:
        for driver_penalty in driver_update_penalties:
            fit = fit_event_model(
                frame,
                driver_prior_pace,
                float(team_penalty),
                float(driver_penalty),
            )
            effects = {
                team: float(fit.team_effect_z[team])
                for team in reporting_teams
                if team in fit.team_effect_z
            }
            order = sorted(effects, key=effects.get)
            rows.append(
                {
                    "team_penalty": float(team_penalty),
                    "driver_update_penalty": float(driver_penalty),
                    "team_effect_z": effects,
                    "car_order_fast_to_slow": order,
                }
            )
    return rows
