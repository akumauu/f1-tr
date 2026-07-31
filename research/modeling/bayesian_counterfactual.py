"""本地分层贝叶斯车辆—车手反事实归因。

模型以“车手 × 分站”的稳健圈速中心为观测单位。轮胎、赛道和圈次阶段先由
不含车队/车手身份的上下文模型去除；随后对车辆、车手和人车组合分别设置
零均值正态层级先验，并用共轭 Gibbs 采样得到完整后验。

这里的反事实是系数层面的条件反事实，不是因果试验：长期只效力单一车队的
车手，其纯车手项与人车组合项仍会部分共线，后验区间和先验敏感性必须一起读。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from modeling.seasonal_attribution import (
    BLOCK_COLUMNS,
    CONTEXT_BLOCKS,
    SeasonalAttributionFit,
    fit_seasonal_model,
)


BAYESIAN_BLOCKS = (
    "team_year",
    "team_circuit",
    "team_event",
    "driver",
    "driver_year",
    "driver_team",
)


def _huber_location_scale(
    values: np.ndarray, huber_k: float, iterations: int
) -> tuple[float, float, float]:
    """用全部有限样本估计 Huber 位置、尺度和有效样本量。"""

    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("Huber 聚合不能接收空样本。")
    location = float(np.median(values))
    mad = float(np.median(np.abs(values - location)))
    scale = 1.4826 * mad
    if scale <= 1e-8:
        scale = max(float(np.std(values)), 1e-8)
    weights = np.ones(values.size, dtype=float)
    for _ in range(iterations):
        distance = np.abs(values - location)
        weights = np.minimum(1.0, huber_k * scale / np.maximum(distance, 1e-12))
        location = float(np.average(values, weights=weights))
        variance = float(np.average((values - location) ** 2, weights=weights))
        scale = max(np.sqrt(variance), 1e-8)
    effective_n = float(weights.sum() ** 2 / np.sum(weights**2))
    return location, scale, effective_n


def build_context_adjusted_aggregates(
    frame: pd.DataFrame,
    context_penalties: Mapping[str, float],
    robust_fit: Mapping[str, Any],
    aggregation: Mapping[str, Any],
) -> tuple[pd.DataFrame, SeasonalAttributionFit]:
    """去除非身份上下文，并聚合为车手—分站稳健观测。"""

    context_fit = fit_seasonal_model(
        frame,
        context_penalties,
        blocks=CONTEXT_BLOCKS,
        irls_iterations=int(robust_fit["irls_iterations"]),
        huber_k=float(robust_fit["huber_k"]),
    )
    aggregate = aggregate_with_context_fit(frame, context_fit, aggregation)
    return aggregate, context_fit


def aggregate_with_context_fit(
    frame: pd.DataFrame,
    context_fit: SeasonalAttributionFit,
    aggregation: Mapping[str, Any],
) -> pd.DataFrame:
    """用既有上下文模型转换数据；用于严格的时间外验证。"""

    work = frame.copy()
    work["context_adjusted_z"] = (
        work["target_log_pct"].to_numpy(dtype=float) - context_fit.predict(work)
    )
    group_columns = [
        "year",
        "round_index",
        "meeting_key",
        "meeting_name",
        "circuit_name",
        "team_name",
        "driver_id",
        "team_year",
        "team_circuit",
        "team_event",
        "driver_year",
        "driver_team",
        "driver_event",
    ]
    rows: list[dict[str, Any]] = []
    for keys, group in work.groupby(group_columns, sort=True, observed=True):
        values = group["context_adjusted_z"].to_numpy(dtype=float)
        location, scale, effective_n = _huber_location_scale(
            values,
            huber_k=float(aggregation["huber_k"]),
            iterations=int(aggregation["iterations"]),
        )
        row = dict(zip(group_columns, keys))
        row.update(
            {
                "response_z": location,
                "robust_scale_z": scale,
                "effective_laps": effective_n,
                "lap_count": int(values.size),
                "standard_error_z": float(scale / np.sqrt(max(effective_n, 1.0))),
            }
        )
        rows.append(row)
    aggregate = pd.DataFrame(rows)
    floor = float(aggregation["standard_error_floor_z"])
    precision = 1.0 / np.maximum(
        aggregate["standard_error_z"].to_numpy(dtype=float), floor
    ) ** 2
    median_precision = float(np.median(precision))
    maximum = median_precision * float(aggregation["maximum_relative_precision"])
    precision = np.minimum(precision, maximum)
    aggregate["likelihood_weight"] = precision / float(np.mean(precision))
    return aggregate.reset_index(drop=True)


@dataclass(frozen=True)
class EncodedBayesianData:
    """Gibbs 采样使用的整数编码数据。"""

    frame: pd.DataFrame
    y: np.ndarray
    weights: np.ndarray
    blocks: tuple[str, ...]
    indices: dict[str, np.ndarray]
    categories: dict[str, tuple[str, ...]]
    category_lookup: dict[str, dict[str, int]]


@dataclass
class BayesianPosterior:
    """多链后验样本；每条链的 beta 形状为 draws × categories。"""

    data: EncodedBayesianData
    chains: list[dict[str, Any]]
    settings: dict[str, Any]

    @property
    def draws_per_chain(self) -> int:
        return int(len(self.chains[0]["sigma_z"])) if self.chains else 0

    def combined(self, key: str) -> np.ndarray:
        return np.concatenate([np.asarray(chain[key]) for chain in self.chains], axis=0)

    def block_combined(self, block: str) -> np.ndarray:
        return np.concatenate(
            [np.asarray(chain["beta"][block]) for chain in self.chains], axis=0
        )


def encode_bayesian_data(
    aggregate: pd.DataFrame, blocks: Sequence[str] = BAYESIAN_BLOCKS
) -> EncodedBayesianData:
    """把层级标签转换为稳定整数索引。"""

    if aggregate.empty:
        raise ValueError("贝叶斯模型输入不能为空。")
    indices: dict[str, np.ndarray] = {}
    categories: dict[str, tuple[str, ...]] = {}
    lookup: dict[str, dict[str, int]] = {}
    for block in blocks:
        column = BLOCK_COLUMNS[block]
        values = aggregate[column].astype(str)
        block_categories = tuple(sorted(values.unique()))
        block_lookup = {value: index for index, value in enumerate(block_categories)}
        categories[block] = block_categories
        lookup[block] = block_lookup
        indices[block] = values.map(block_lookup).to_numpy(dtype=np.int64)
    return EncodedBayesianData(
        frame=aggregate.reset_index(drop=True),
        y=aggregate["response_z"].to_numpy(dtype=float),
        weights=aggregate["likelihood_weight"].to_numpy(dtype=float),
        blocks=tuple(blocks),
        indices=indices,
        categories=categories,
        category_lookup=lookup,
    )


def _inverse_gamma_sample(
    rng: np.random.Generator, shape: float, scale: float
) -> float:
    return float(1.0 / rng.gamma(shape=shape, scale=1.0 / scale))


def _sample_chain(
    data: EncodedBayesianData,
    settings: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    """执行一条共轭分块 Gibbs 链。"""

    rng = np.random.default_rng(seed)
    iterations = int(settings["iterations"])
    warmup = int(settings["warmup"])
    thin = int(settings["thin"])
    if not (0 <= warmup < iterations) or thin < 1:
        raise ValueError("Gibbs 的 warmup/iterations/thin 配置无效。")
    expected_draws = (iterations - warmup + thin - 1) // thin

    variance_shape = float(settings["variance_prior_shape"])
    prior_sd = {key: float(value) for key, value in settings["block_prior_sd_z"].items()}
    prior_scale = {
        block: (variance_shape - 1.0) * prior_sd[block] ** 2
        for block in data.blocks
    }
    noise_shape = float(settings["noise_prior"]["shape"])
    noise_scale = (
        noise_shape - 1.0
    ) * float(settings["noise_prior"]["scale_sd_z"]) ** 2
    intercept_variance = float(settings["intercept_prior_sd_z"]) ** 2

    beta = {
        block: np.zeros(len(data.categories[block]), dtype=float)
        for block in data.blocks
    }
    tau2 = {block: prior_sd[block] ** 2 for block in data.blocks}
    sigma2 = float(np.average((data.y - np.average(data.y, weights=data.weights)) ** 2, weights=data.weights))
    sigma2 = max(sigma2, 1e-4)
    intercept = float(np.average(data.y, weights=data.weights))
    mu = np.full(data.y.size, intercept, dtype=float)

    saved_intercept = np.empty(expected_draws, dtype=float)
    saved_sigma = np.empty(expected_draws, dtype=float)
    saved_tau = {
        block: np.empty(expected_draws, dtype=float) for block in data.blocks
    }
    saved_beta = {
        block: np.empty((expected_draws, len(data.categories[block])), dtype=float)
        for block in data.blocks
    }
    saved = 0
    for iteration in range(iterations):
        residual_without_intercept = data.y - (mu - intercept)
        intercept_precision = float(data.weights.sum() / sigma2 + 1.0 / intercept_variance)
        intercept_mean = float(
            np.sum(data.weights * residual_without_intercept) / sigma2 / intercept_precision
        )
        new_intercept = float(
            rng.normal(intercept_mean, 1.0 / np.sqrt(intercept_precision))
        )
        mu += new_intercept - intercept
        intercept = new_intercept

        for block in data.blocks:
            index = data.indices[block]
            old = beta[block]
            residual_without_block = data.y - (mu - old[index])
            category_count = len(old)
            sum_weight = np.bincount(
                index, weights=data.weights, minlength=category_count
            )
            sum_weighted_response = np.bincount(
                index,
                weights=data.weights * residual_without_block,
                minlength=category_count,
            )
            precision = sum_weight / sigma2 + 1.0 / tau2[block]
            mean = (sum_weighted_response / sigma2) / precision
            new = mean + rng.normal(size=category_count) / np.sqrt(precision)
            mu += new[index] - old[index]
            beta[block] = new
            tau2[block] = _inverse_gamma_sample(
                rng,
                variance_shape + category_count / 2.0,
                prior_scale[block] + float(np.sum(new**2)) / 2.0,
            )

        residual = data.y - mu
        sigma2 = _inverse_gamma_sample(
            rng,
            noise_shape + data.y.size / 2.0,
            noise_scale + float(np.sum(data.weights * residual**2)) / 2.0,
        )
        if iteration >= warmup and (iteration - warmup) % thin == 0:
            saved_intercept[saved] = intercept
            saved_sigma[saved] = np.sqrt(sigma2)
            for block in data.blocks:
                saved_tau[block][saved] = np.sqrt(tau2[block])
                saved_beta[block][saved] = beta[block]
            saved += 1

    return {
        "intercept": saved_intercept[:saved],
        "sigma_z": saved_sigma[:saved],
        "tau_z": {block: values[:saved] for block, values in saved_tau.items()},
        "beta": {block: values[:saved] for block, values in saved_beta.items()},
    }


def sample_posterior(
    aggregate: pd.DataFrame,
    settings: Mapping[str, Any],
    blocks: Sequence[str] = BAYESIAN_BLOCKS,
) -> BayesianPosterior:
    """运行多条确定种子的 Gibbs 链。"""

    data = encode_bayesian_data(aggregate, blocks)
    seed = int(settings["random_seed"])
    chains = [
        _sample_chain(data, settings, seed + 1009 * chain_index)
        for chain_index in range(int(settings["chains"]))
    ]
    return BayesianPosterior(data=data, chains=chains, settings=dict(settings))


def gelman_rubin_rhat(chains: np.ndarray) -> float:
    """计算经典 split 前 Gelman–Rubin R-hat。"""

    values = np.asarray(chains, dtype=float)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 2:
        return float("nan")
    n = values.shape[1]
    within = float(np.mean(np.var(values, axis=1, ddof=1)))
    between = float(n * np.var(np.mean(values, axis=1), ddof=1))
    if within <= 1e-15:
        return 1.0 if between <= 1e-15 else float("inf")
    variance = (n - 1.0) / n * within + between / n
    return float(np.sqrt(variance / within))


def posterior_diagnostics(posterior: BayesianPosterior) -> dict[str, Any]:
    """汇总噪声和层级尺度的链收敛。"""

    diagnostics: dict[str, Any] = {
        "chains": len(posterior.chains),
        "draws_per_chain": posterior.draws_per_chain,
        "sigma_z": {
            "mean": float(np.mean(posterior.combined("sigma_z"))),
            "rhat": gelman_rubin_rhat(
                np.stack([chain["sigma_z"] for chain in posterior.chains])
            ),
        },
        "tau_z": {},
    }
    for block in posterior.data.blocks:
        values = np.stack([chain["tau_z"][block] for chain in posterior.chains])
        diagnostics["tau_z"][block] = {
            "mean": float(np.mean(values)),
            "rhat": gelman_rubin_rhat(values),
        }
    rhat_values = [diagnostics["sigma_z"]["rhat"]] + [
        row["rhat"] for row in diagnostics["tau_z"].values()
    ]
    diagnostics["maximum_scale_rhat"] = float(np.nanmax(rhat_values))
    return diagnostics


def _block_draws_by_chain(
    posterior: BayesianPosterior, block: str, label: str
) -> np.ndarray:
    lookup = posterior.data.category_lookup[block]
    if label not in lookup:
        return np.zeros((len(posterior.chains), posterior.draws_per_chain), dtype=float)
    index = lookup[label]
    return np.stack(
        [chain["beta"][block][:, index] for chain in posterior.chains]
    )


def _mean_block_draws_by_chain(
    posterior: BayesianPosterior, block: str, labels: Sequence[str]
) -> np.ndarray:
    values = [
        _block_draws_by_chain(posterior, block, label)
        for label in labels
        if label in posterior.data.category_lookup[block]
    ]
    if not values:
        return np.zeros((len(posterior.chains), posterior.draws_per_chain), dtype=float)
    return np.mean(np.stack(values, axis=0), axis=0)


def _summary(values_by_chain: np.ndarray) -> dict[str, float]:
    values = np.asarray(values_by_chain, dtype=float).reshape(-1)
    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "ci80_low": float(np.quantile(values, 0.10)),
        "ci80_high": float(np.quantile(values, 0.90)),
        "ci95_low": float(np.quantile(values, 0.025)),
        "ci95_high": float(np.quantile(values, 0.975)),
        "rhat": gelman_rubin_rhat(np.asarray(values_by_chain, dtype=float)),
    }


def _advantage_to_ms(values: np.ndarray, reference_lap_seconds: float) -> np.ndarray:
    """正的 z 优势转换为正的参考圈毫秒收益。"""

    return -np.expm1(-np.asarray(values, dtype=float) / 100.0) * reference_lap_seconds * 1000.0


def extract_counterfactual_ratings(
    posterior: BayesianPosterior,
    reference_frame: pd.DataFrame,
    reporting_teams: Sequence[str],
    years: Sequence[int] = (2023, 2024, 2025),
    reference_lap_seconds: float = 90.0,
) -> dict[str, list[dict[str, Any]]]:
    """提取标准车手车辆、标准车辆车手及人车适配三个后验。"""

    cars: list[dict[str, Any]] = []
    drivers: list[dict[str, Any]] = []
    combinations: list[dict[str, Any]] = []
    for year in years:
        year_frame = reference_frame.loc[reference_frame["year"] == int(year)].copy()
        circuits = sorted(year_frame["circuit_name"].astype(str).unique())
        raw_cars: dict[str, np.ndarray] = {}
        for team in reporting_teams:
            team_year = _block_draws_by_chain(
                posterior, "team_year", f"{year}|{team}"
            )
            team_circuit = _mean_block_draws_by_chain(
                posterior,
                "team_circuit",
                [f"{team}|{circuit}" for circuit in circuits],
            )
            event_labels = sorted(
                year_frame.loc[year_frame["team_name"] == team, "team_event"]
                .astype(str)
                .unique()
            )
            team_event = _mean_block_draws_by_chain(
                posterior, "team_event", event_labels
            )
            raw_cars[team] = team_year + team_circuit + team_event
        car_stack = np.stack([raw_cars[team] for team in reporting_teams], axis=0)
        car_center = np.mean(car_stack, axis=0)
        fastest = np.min(car_stack, axis=0)
        for team_index, team in enumerate(reporting_teams):
            advantage = car_center - raw_cars[team]
            rank = 1 + np.sum(car_stack < raw_cars[team][None, :, :], axis=0)
            row = {
                "year": int(year),
                "team": team,
                "reference_driver": "zero driver + zero driver-year + zero driver-team",
                "car_pace_advantage_z": _summary(advantage),
                "car_pace_advantage_90s_ms": _summary(
                    _advantage_to_ms(advantage, reference_lap_seconds)
                ),
                "delta_to_fastest_z": _summary(raw_cars[team] - fastest),
                "expected_rank": float(np.mean(rank)),
                "p_rank_1": float(np.mean(rank == 1)),
                "event_count": int(
                    year_frame.loc[year_frame["team_name"] == team, "meeting_key"].nunique()
                ),
            }
            cars.append(row)

        roster = (
            year_frame.loc[year_frame["team_name"].isin(reporting_teams)]
            .groupby(["driver_id", "team_name"], as_index=False)
            .agg(sample_count=("lap_count", "sum"))
            .sort_values(["driver_id", "sample_count"], ascending=[True, False])
            .drop_duplicates("driver_id")
        )
        raw_drivers: dict[str, np.ndarray] = {}
        primary_team: dict[str, str] = {}
        sample_count: dict[str, int] = {}
        for roster_row in roster.itertuples(index=False):
            driver = str(roster_row.driver_id)
            primary_team[driver] = str(roster_row.team_name)
            sample_count[driver] = int(roster_row.sample_count)
            raw_drivers[driver] = _block_draws_by_chain(
                posterior, "driver", driver
            ) + _block_draws_by_chain(
                posterior, "driver_year", f"{year}|{driver}"
            )
        driver_names = sorted(raw_drivers)
        driver_stack = np.stack([raw_drivers[driver] for driver in driver_names], axis=0)
        driver_center = np.mean(driver_stack, axis=0)
        for driver_index, driver in enumerate(driver_names):
            team = primary_team[driver]
            advantage = driver_center - raw_drivers[driver]
            rank = 1 + np.sum(driver_stack < raw_drivers[driver][None, :, :], axis=0)
            interaction_raw = _block_draws_by_chain(
                posterior, "driver_team", f"{driver}|{team}"
            )
            interaction_advantage = -interaction_raw
            combination_advantage = (
                car_center - raw_cars[team] + advantage + interaction_advantage
            )
            driver_row = {
                "year": int(year),
                "driver": driver,
                "team": team,
                "reference_car": "four-team equal-weight car + zero driver-team",
                "driver_pace_advantage_z": _summary(advantage),
                "driver_pace_advantage_90s_ms": _summary(
                    _advantage_to_ms(advantage, reference_lap_seconds)
                ),
                "expected_rank": float(np.mean(rank)),
                "p_rank_1": float(np.mean(rank == 1)),
                "sample_count": sample_count[driver],
            }
            drivers.append(driver_row)
            combinations.append(
                {
                    "year": int(year),
                    "driver": driver,
                    "team": team,
                    "interaction_pace_advantage_z": _summary(interaction_advantage),
                    "actual_combination_advantage_z": _summary(combination_advantage),
                    "actual_combination_advantage_90s_ms": _summary(
                        _advantage_to_ms(combination_advantage, reference_lap_seconds)
                    ),
                }
            )
    cars.sort(key=lambda row: (row["year"], -row["car_pace_advantage_z"]["mean"]))
    drivers.sort(key=lambda row: (row["year"], -row["driver_pace_advantage_z"]["mean"]))
    combinations.sort(
        key=lambda row: (row["year"], -row["actual_combination_advantage_z"]["mean"])
    )
    return {"cars": cars, "drivers": drivers, "combinations": combinations}


def posterior_mean_prediction(
    posterior: BayesianPosterior, frame: pd.DataFrame
) -> np.ndarray:
    """用训练后验均值预测；未见层级按零均值先验处理。"""

    prediction = np.full(
        len(frame), float(np.mean(posterior.combined("intercept"))), dtype=float
    )
    for block in posterior.data.blocks:
        column = BLOCK_COLUMNS[block]
        effect_mean = np.mean(posterior.block_combined(block), axis=0)
        mapping = {
            category: float(effect_mean[index])
            for index, category in enumerate(posterior.data.categories[block])
        }
        prediction += frame[column].astype(str).map(mapping).fillna(0.0).to_numpy(dtype=float)
    return prediction


def posterior_prediction_draws(
    posterior: BayesianPosterior,
    frame: pd.DataFrame,
    include_observation_noise: bool = False,
    random_seed: int = 0,
) -> np.ndarray:
    """返回 chains × draws × rows 的线性或后验预测样本。"""

    chain_predictions: list[np.ndarray] = []
    rng = np.random.default_rng(random_seed)
    for chain in posterior.chains:
        prediction = np.repeat(
            np.asarray(chain["intercept"], dtype=float)[:, None], len(frame), axis=1
        )
        for block in posterior.data.blocks:
            column = BLOCK_COLUMNS[block]
            lookup = posterior.data.category_lookup[block]
            labels = frame[column].astype(str)
            index = labels.map(lookup).fillna(-1).to_numpy(dtype=int)
            known = index >= 0
            if np.any(known):
                prediction[:, known] += chain["beta"][block][:, index[known]]
            if include_observation_noise and np.any(~known):
                # 时间外的新分站/赛道/年度层级不能只置零；其均值为零，但预测分布
                # 必须包含该层级的后验尺度。同一新标签的抽样效应在相关行间共享。
                tau = np.asarray(chain["tau_z"][block], dtype=float)
                unknown_labels = labels.to_numpy()[~known]
                for label in np.unique(unknown_labels):
                    mask = (~known) & (labels.to_numpy() == label)
                    new_effect = rng.normal(size=len(tau)) * tau
                    prediction[:, mask] += new_effect[:, None]
        if include_observation_noise:
            row_weight = frame.get(
                "likelihood_weight", pd.Series(np.ones(len(frame)), index=frame.index)
            ).to_numpy(dtype=float)
            noise_sd = np.asarray(chain["sigma_z"], dtype=float)[:, None] / np.sqrt(
                row_weight[None, :]
            )
            prediction += rng.normal(size=prediction.shape) * noise_sd
        chain_predictions.append(prediction)
    return np.stack(chain_predictions, axis=0)
