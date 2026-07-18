#!/usr/bin/env python3
"""用 2023→2024 选择跨赛季衰减，再评估 2024→2025。"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

import analyze_model_comparisons as comparisons
import run_experiments as base


DECAY_CONFIG_PATH = base.ROOT / "research" / "configs" / "dynamic_decay.json"
ALIASES_PATH = base.ROOT / "research" / "configs" / "entity_aliases.json"
TUNING_MANIFEST_PATH = base.ROOT / "research" / "artifacts" / "dynamic_tuning_data_manifest.json"
OUTPUT_PATH = base.RECORDS_DIR / "dynamic_decay_results.json"
SUMMARY_PATH = base.RECORDS_DIR / "dynamic_decay_summary.md"

CATEGORICAL = ["compound", "circuit_name", "session_name", "driver_id", "team_name"]


def canonicalize(frame: pd.DataFrame, aliases: dict[str, str]) -> pd.DataFrame:
    result = frame.copy()
    result["team_name"] = result["team_name"].replace(aliases)
    return result


def decay_prediction(
    pipeline: Any,
    frame: pd.DataFrame,
    driver_decay: float,
    team_decay: float,
) -> np.ndarray:
    features = CATEGORICAL + base.NUMERIC_FEATURES
    transformed = pipeline.named_steps["preprocess"].transform(frame[features])
    names = pipeline.named_steps["preprocess"].get_feature_names_out()
    coefficients = pipeline.named_steps["model"].coef_.copy()
    driver_mask = np.asarray([str(name).startswith("cat__driver_id_") for name in names])
    team_mask = np.asarray([str(name).startswith("cat__team_name_") for name in names])
    coefficients[driver_mask] *= float(driver_decay)
    coefficients[team_mask] *= float(team_decay)
    return np.asarray(
        transformed @ coefficients + pipeline.named_steps["model"].intercept_
    ).reshape(-1)


def fit_pipeline(
    train: pd.DataFrame,
    alpha: float,
    solver: str,
) -> Any:
    features = CATEGORICAL + base.NUMERIC_FEATURES
    pipeline = base.make_pipeline(CATEGORICAL, alpha, solver)
    pipeline.fit(train[features], train["target_log_pct"].to_numpy(dtype=float))
    return pipeline


def refined_values(best: float, half_width: float, step: float) -> list[float]:
    low = max(0.0, best - half_width)
    high = min(1.0, best + half_width)
    count = int(round((high - low) / step))
    values = [round(low + index * step, 10) for index in range(count + 1)]
    values.extend([0.0, 1.0, best])
    return sorted(set(float(np.clip(value, 0.0, 1.0)) for value in values))


def tune_decay(
    pipeline: Any,
    validation_2024: pd.DataFrame,
    decay_config: dict[str, Any],
) -> tuple[dict[str, float], list[dict[str, float]]]:
    y = validation_2024["target_log_pct"].to_numpy(dtype=float)
    coarse = [float(value) for value in decay_config["coarse_decay_grid"]]
    rows: list[dict[str, float]] = []
    for driver_decay in coarse:
        for team_decay in coarse:
            prediction = decay_prediction(
                pipeline, validation_2024, driver_decay, team_decay
            )
            rows.append(
                {
                    "driver_decay": driver_decay,
                    "team_decay": team_decay,
                    "mae": float(mean_absolute_error(y, prediction)),
                }
            )
    coarse_best = min(rows, key=lambda row: row["mae"])
    driver_values = refined_values(
        coarse_best["driver_decay"],
        float(decay_config["refinement_half_width"]),
        float(decay_config["refinement_step"]),
    )
    team_values = refined_values(
        coarse_best["team_decay"],
        float(decay_config["refinement_half_width"]),
        float(decay_config["refinement_step"]),
    )
    for driver_decay in driver_values:
        for team_decay in team_values:
            if any(
                row["driver_decay"] == driver_decay and row["team_decay"] == team_decay
                for row in rows
            ):
                continue
            prediction = decay_prediction(
                pipeline, validation_2024, driver_decay, team_decay
            )
            rows.append(
                {
                    "driver_decay": driver_decay,
                    "team_decay": team_decay,
                    "mae": float(mean_absolute_error(y, prediction)),
                }
            )
    best = min(rows, key=lambda row: row["mae"])
    rows.sort(key=lambda row: row["mae"])
    return best, rows


def run_decay_model(
    train_2023: pd.DataFrame,
    tune_2024: pd.DataFrame,
    train_2024: pd.DataFrame,
    validation_2025: pd.DataFrame,
    parent_config: dict[str, Any],
    decay_config: dict[str, Any],
    locked_results: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    solver = {1: "lsqr", 2: "sag", 3: "sparse_cg"}[attempt]
    alpha_2023, alpha_cv_2023 = base.select_alpha(
        train_2023, CATEGORICAL, parent_config, solver
    )
    tuning_pipeline = fit_pipeline(train_2023, alpha_2023, solver)
    best_decay, search_rows = tune_decay(tuning_pipeline, tune_2024, decay_config)

    final_alpha = float(locked_results["models"]["driver_team_ridge"]["selected_alpha"])
    final_pipeline = fit_pipeline(train_2024, final_alpha, solver)
    dynamic_prediction = decay_prediction(
        final_pipeline,
        validation_2025,
        best_decay["driver_decay"],
        best_decay["team_decay"],
    )
    full_prediction = decay_prediction(final_pipeline, validation_2025, 1.0, 1.0)
    zero_prediction = decay_prediction(final_pipeline, validation_2025, 0.0, 0.0)
    y_2025 = validation_2025["target_log_pct"].to_numpy(dtype=float)
    return {
        "status": "success",
        "attempt": attempt,
        "solver": solver,
        "selected_alpha_2023_group_cv": alpha_2023,
        "alpha_2023_cv_scores": alpha_cv_2023,
        "final_alpha_2024_locked": final_alpha,
        "selected_decay_on_2023_to_2024": best_decay,
        "decay_search_top_20": search_rows[:20],
        "validation_2025": {
            "dynamic_decay": base.regression_metrics(y_2025, dynamic_prediction),
            "full_carryover": base.regression_metrics(y_2025, full_prediction),
            "zero_driver_team_carryover": base.regression_metrics(y_2025, zero_prediction),
        },
        "validation_2025_by_session": {
            str(session_name): base.regression_metrics(
                subset["target_log_pct"].to_numpy(dtype=float),
                dynamic_prediction[np.asarray(positions, dtype=int)],
            )
            for session_name, positions in validation_2025.groupby("session_name").indices.items()
            for subset in [validation_2025.iloc[np.asarray(positions, dtype=int)]]
        },
        "predictions": {
            "dynamic": dynamic_prediction,
            "full": full_prediction,
            "zero": zero_prediction,
        },
    }


def write_summary(results: dict[str, Any]) -> None:
    model = results["model"]
    decay = model["selected_decay_on_2023_to_2024"]
    validation = model["validation_2025"]
    paired = results["dynamic_vs_primary_raw_joint"]
    lines = [
        "# 跨赛季动态衰减实验",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        "- 衰减系数仅由 2023→2024 选择；2025 不参与超参数选择。",
        f"- 车手效应保留比例：{decay['driver_decay']:.2f}",
        f"- 车队效应保留比例：{decay['team_decay']:.2f}",
        f"- 2023→2024 调参 MAE：{decay['mae']:.5f}",
        "",
        "| 2025 方案 | MAE | RMSE | R² |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, metric in validation.items():
        lines.append(
            f"| {name} | {metric['mae']:.5f} | {metric['rmse']:.5f} | {metric['r2']:.5f} |"
        )
    lines.extend(
        [
            "",
            "## 动态衰减 vs 首轮原始联合模型",
            "",
            f"- MAE delta：{paired['observed_mae_delta']:.5f}",
            f"- 95% CI：[{paired['ci95_low']:.5f}, {paired['ci95_high']:.5f}]",
            f"- 动态衰减更优概率：{paired['probability_first_better']:.3f}",
            "",
            "该实验仍属于探索性：虽然衰减没有读取 2025 结果进行选择，但动态模型是在查看首轮结果后提出。最终确认需使用 2026 冻结集。",
            "",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parent_config = base.load_json(base.DEFAULT_CONFIG)
    decay_config = base.load_json(DECAY_CONFIG_PATH)
    alias_config = base.load_json(ALIASES_PATH)
    locked_results = base.load_json(base.RESULTS_PATH)
    train_2024, validation_2025, primary_manifest = base.load_frozen_data(parent_config)

    tune_config = copy.deepcopy(parent_config)
    tune_config["experiment_name"] = "dynamic_decay_tuning_2023_to_2024"
    tune_config["train_year"] = 2023
    tune_config["validation_year"] = 2024
    run_id = f"{decay_config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    data_attempt = base.run_with_retries(
        run_id,
        "phase_3_dynamic",
        "reuse_v4_dynamic_tuning_freeze_2023_2024_v1",
        int(decay_config["max_attempts"]),
        lambda _attempt: base.load_frozen_pair(tune_config, TUNING_MANIFEST_PATH),
    )
    if data_attempt.value is None:
        return 2
    train_2023, tune_2024, tuning_manifest = data_attempt.value

    aliases = alias_config["team_aliases"]
    train_2023 = canonicalize(train_2023.reset_index(drop=True), aliases)
    tune_2024 = canonicalize(tune_2024.reset_index(drop=True), aliases)
    train_2024 = canonicalize(train_2024.reset_index(drop=True), aliases)
    validation_2025 = canonicalize(validation_2025.reset_index(drop=True), aliases)

    model_attempt = base.run_with_retries(
        run_id,
        "phase_3_dynamic",
        "interseason_driver_team_decay_ridge_v1",
        int(decay_config["max_attempts"]),
        lambda attempt: run_decay_model(
            train_2023,
            tune_2024,
            train_2024,
            validation_2025,
            parent_config,
            decay_config,
            locked_results,
            attempt,
        ),
    )
    if model_attempt.value is None:
        return 2
    model = model_attempt.value

    # 首轮原始联合模型使用原始名称，严格复现后再做配对比较。
    raw_pipeline = fit_pipeline(
        base.load_frozen_data(parent_config)[0].reset_index(drop=True),
        float(locked_results["models"]["driver_team_ridge"]["selected_alpha"]),
        "lsqr",
    )
    raw_validation = base.load_frozen_data(parent_config)[1].reset_index(drop=True)
    raw_prediction = raw_pipeline.predict(raw_validation[CATEGORICAL + base.NUMERIC_FEATURES])
    paired = comparisons.paired_bootstrap(
        raw_validation,
        model["predictions"]["dynamic"],
        raw_prediction,
        seed=int(parent_config["random_seed"]) + 1200,
        repetitions=5000,
    )
    model["predictions"] = {
        name: {"sha256": base.canonical_hash(value.tolist()), "rows": len(value)}
        for name, value in model["predictions"].items()
    }
    results = {
        "run_id": run_id,
        "created_at": base.now_iso(),
        "decay_config": decay_config,
        "alias_version": alias_config["version"],
        "primary_manifest_hash": base.canonical_hash(primary_manifest),
        "tuning_manifest": tuning_manifest,
        "model": model,
        "dynamic_vs_primary_raw_joint": paired,
    }
    base.atomic_json(OUTPUT_PATH, results)
    write_summary(results)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
