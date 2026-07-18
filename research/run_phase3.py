#!/usr/bin/env python3
"""运行阶段 3 探索性模型；2025 指标不再视为完全未见的确认性验证。"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import BayesianRidge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import run_experiments as base


PHASE3_CONFIG_PATH = base.ROOT / "research" / "configs" / "phase3_exploratory.json"
PHASE3_RESULTS_PATH = base.RECORDS_DIR / "phase3_results.json"
PHASE3_SUMMARY_PATH = base.RECORDS_DIR / "phase3_summary.md"


RIDGE_SPECS = {
    "session_head_ridge": [
        "compound",
        "circuit_name",
        "session_name",
        "driver_id",
        "team_name",
        "driver_session_head",
        "team_session_head",
    ],
    "team_track_ridge": [
        "compound",
        "circuit_name",
        "session_name",
        "driver_id",
        "team_name",
        "team_circuit_head",
    ],
}

BAYESIAN_CATEGORICAL = [
    "compound",
    "circuit_name",
    "session_name",
    "driver_id",
    "team_name",
]


def add_exploratory_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["driver_session_head"] = (
        result["driver_id"].astype(str) + "|" + result["session_name"].astype(str)
    )
    result["team_session_head"] = (
        result["team_name"].astype(str) + "|" + result["session_name"].astype(str)
    )
    result["team_circuit_head"] = (
        result["team_name"].astype(str) + "|" + result["circuit_name"].astype(str)
    )
    return result


def metrics(y_true: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, prediction)),
        "rmse": float(math.sqrt(mean_squared_error(y_true, prediction))),
        "r2": float(r2_score(y_true, prediction)),
        "bias": float(np.mean(prediction - y_true)),
    }


def by_session_metrics(
    validation: pd.DataFrame, prediction: np.ndarray
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for session_name, positions in validation.groupby("session_name").indices.items():
        index = np.asarray(positions, dtype=int)
        output[str(session_name)] = metrics(
            validation.iloc[index]["target_log_pct"].to_numpy(dtype=float),
            prediction[index],
        )
    return output


def fit_exploratory_ridge(
    name: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    parent_config: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    categorical = RIDGE_SPECS[name]
    solver = {1: "lsqr", 2: "sag", 3: "sparse_cg"}[attempt]
    best_alpha, cv_scores = base.select_alpha(train, categorical, parent_config, solver)
    pipeline = base.make_pipeline(categorical, best_alpha, solver)
    features = categorical + base.NUMERIC_FEATURES
    y_train = train["target_log_pct"].to_numpy(dtype=float)
    y_validation = validation["target_log_pct"].to_numpy(dtype=float)
    pipeline.fit(train[features], y_train)
    prediction = pipeline.predict(validation[features])
    return {
        "name": name,
        "status": "success",
        "attempt": attempt,
        "solver": solver,
        "selected_alpha": best_alpha,
        "group_cv_mae_by_alpha": cv_scores,
        "validation_metrics": metrics(y_validation, prediction),
        "validation_metrics_by_session": by_session_metrics(validation, prediction),
        "bootstrap_mae_delta_vs_zero": base.bootstrap_delta_vs_zero(
            validation, prediction, seed=int(parent_config["random_seed"]) + 100 + attempt
        ),
        "unseen_category_rates": base.unseen_rates(train, validation, categorical),
        "ratings": base.extract_ratings(pipeline),
        "feature_count_after_encoding": int(
            len(pipeline.named_steps["preprocess"].get_feature_names_out())
        ),
    }


def make_dense_preprocess(categorical: list[str]) -> ColumnTransformer:
    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "onehot",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False, dtype=np.float64),
            ),
        ]
    )
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    return ColumnTransformer(
        [
            ("cat", categorical_pipeline, categorical),
            ("num", numeric_pipeline, base.NUMERIC_FEATURES),
        ],
        sparse_threshold=0.0,
    )


def bayesian_ratings(
    names: np.ndarray,
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> dict[str, list[dict[str, float | str]]]:
    output: dict[str, list[dict[str, float | str]]] = {"drivers": [], "teams": []}
    for label, prefix in (("drivers", "cat__driver_id_"), ("teams", "cat__team_name_")):
        rows: list[dict[str, float | str]] = []
        for index, name in enumerate(names):
            if not str(name).startswith(prefix):
                continue
            advantage = -float(coefficients[index])
            standard_deviation = float(math.sqrt(max(float(covariance[index, index]), 0.0)))
            rows.append(
                {
                    "entity": str(name)[len(prefix) :],
                    "pace_advantage_pct": advantage,
                    "coefficient_sd": standard_deviation,
                    "ci95_low": advantage - 1.96 * standard_deviation,
                    "ci95_high": advantage + 1.96 * standard_deviation,
                }
            )
        if rows:
            center = float(np.mean([float(row["pace_advantage_pct"]) for row in rows]))
            for row in rows:
                row["pace_advantage_pct"] = float(row["pace_advantage_pct"]) - center
                row["ci95_low"] = float(row["ci95_low"]) - center
                row["ci95_high"] = float(row["ci95_high"]) - center
            rows.sort(key=lambda row: float(row["pace_advantage_pct"]), reverse=True)
        output[label] = rows
    return output


def fit_bayesian_driver_team(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    parent_config: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    features = BAYESIAN_CATEGORICAL + base.NUMERIC_FEATURES
    preprocess = make_dense_preprocess(BAYESIAN_CATEGORICAL)
    x_train = preprocess.fit_transform(train[features])
    x_validation = preprocess.transform(validation[features])
    y_train = train["target_log_pct"].to_numpy(dtype=float)
    y_validation = validation["target_log_pct"].to_numpy(dtype=float)

    settings = {
        1: {"max_iter": 300, "tol": 1e-3},
        2: {"max_iter": 800, "tol": 1e-4},
        3: {
            "max_iter": 1200,
            "tol": 1e-5,
            "alpha_1": 1e-5,
            "alpha_2": 1e-5,
            "lambda_1": 1e-5,
            "lambda_2": 1e-5,
        },
    }[attempt]
    model = BayesianRidge(compute_score=True, fit_intercept=True, **settings)
    model.fit(x_train, y_train)
    prediction, prediction_std = model.predict(x_validation, return_std=True)
    residual = y_validation - prediction
    normal_nll = 0.5 * np.log(2.0 * np.pi * prediction_std**2) + 0.5 * (
        residual / prediction_std
    ) ** 2

    coverage: dict[str, float] = {}
    for label, z_value in (("80", 1.2815515655446004), ("95", 1.959963984540054)):
        coverage[label] = float((np.abs(residual) <= z_value * prediction_std).mean())

    names = preprocess.get_feature_names_out()
    return {
        "name": "bayesian_driver_team",
        "status": "success",
        "attempt": attempt,
        "settings": settings,
        "validation_metrics": metrics(y_validation, prediction),
        "validation_metrics_by_session": by_session_metrics(validation, prediction),
        "predictive_interval_coverage": coverage,
        "mean_normal_negative_log_likelihood": float(np.mean(normal_nll)),
        "mean_predictive_std": float(np.mean(prediction_std)),
        "alpha_noise_precision": float(model.alpha_),
        "lambda_weight_precision": float(model.lambda_),
        "effective_iterations": int(model.n_iter_),
        "unseen_category_rates": base.unseen_rates(
            train, validation, BAYESIAN_CATEGORICAL
        ),
        "ratings": bayesian_ratings(names, model.coef_, model.sigma_),
        "feature_count_after_encoding": int(len(names)),
        "bootstrap_mae_delta_vs_zero": base.bootstrap_delta_vs_zero(
            validation, prediction, seed=int(parent_config["random_seed"]) + 200
        ),
    }


def write_summary(results: dict[str, Any]) -> None:
    lines = [
        "# 阶段 3 探索性模型摘要",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        f"- 父实验：`{results['phase3_config']['parent_run_id']}`",
        "- 重要：模型结构是在查看首轮 2025 结果后确定，因此本页 2025 指标只属于探索性验证。",
        "- 最终确认集：2026 已完成 Race/Sprint，后续冻结。",
        "",
        "| 模型 | 状态 | 2024 CV MAE | 2025 MAE | 2025 RMSE | 2025 R² |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name in results["phase3_config"]["models"]:
        model = results["models"].get(name, {})
        if model.get("status") != "success":
            lines.append(f"| {name} | {model.get('status', 'not_run')} | — | — | — | — |")
            continue
        metric = model["validation_metrics"]
        cv_value = "—"
        if model.get("group_cv_mae_by_alpha"):
            cv_value = f"{min(model['group_cv_mae_by_alpha'].values()):.5f}"
        lines.append(
            f"| {name} | success | {cv_value} | {metric['mae']:.5f} | "
            f"{metric['rmse']:.5f} | {metric['r2']:.5f} |"
        )

    bayesian = results["models"].get("bayesian_driver_team", {})
    if bayesian.get("status") == "success":
        lines.extend(
            [
                "",
                "## 贝叶斯预测校准",
                "",
                f"- 80% 区间实际覆盖率：{bayesian['predictive_interval_coverage']['80']:.4f}",
                f"- 95% 区间实际覆盖率：{bayesian['predictive_interval_coverage']['95']:.4f}",
                f"- 平均预测标准差：{bayesian['mean_predictive_std']:.4f}",
                f"- 平均正态负对数似然：{bayesian['mean_normal_negative_log_likelihood']:.4f}",
                "",
                "覆盖率明显偏离名义水平时，不发布个体可信区间，下一阶段应改用重尾观测或分站层级方差。",
            ]
        )
    PHASE3_SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    phase3_config = base.load_json(PHASE3_CONFIG_PATH)
    parent_config = base.load_json(base.DEFAULT_CONFIG)
    run_id = f"{phase3_config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    max_attempts = int(phase3_config["max_attempts"])
    base.append_log(
        {
            "run_id": run_id,
            "timestamp": base.now_iso(),
            "phase": "phase_3",
            "method": "phase3_exploratory_run",
            "status": "started",
            "validation_status": phase3_config["validation_status"],
        }
    )
    train, validation, manifest = base.load_frozen_data(parent_config)
    train = add_exploratory_features(train.reset_index(drop=True))
    validation = add_exploratory_features(validation.reset_index(drop=True))

    results: dict[str, Any] = {
        "run_id": run_id,
        "created_at": base.now_iso(),
        "phase3_config": phase3_config,
        "data_manifest_hash": base.canonical_hash(manifest),
        "models": {},
    }
    for name in phase3_config["models"]:
        if name in RIDGE_SPECS:
            attempt_result = base.run_with_retries(
                run_id,
                "phase_3",
                name,
                max_attempts,
                lambda attempt, model_name=name: fit_exploratory_ridge(
                    model_name, train, validation, parent_config, attempt
                ),
            )
        elif name == "bayesian_driver_team":
            attempt_result = base.run_with_retries(
                run_id,
                "phase_3",
                name,
                max_attempts,
                lambda attempt: fit_bayesian_driver_team(
                    train, validation, parent_config, attempt
                ),
            )
        else:
            raise RuntimeError(f"未知阶段 3 模型: {name}")
        results["models"][name] = (
            attempt_result.value
            if attempt_result.value is not None
            else {
                "name": name,
                "status": attempt_result.status,
                "attempts": attempt_result.attempts,
            }
        )

    base.atomic_json(PHASE3_RESULTS_PATH, results)
    write_summary(results)
    base.append_log(
        {
            "run_id": run_id,
            "timestamp": base.now_iso(),
            "phase": "phase_3",
            "method": "phase3_exploratory_run",
            "status": "completed",
            "successful_models": sum(
                1 for model in results["models"].values() if model.get("status") == "success"
            ),
        }
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
