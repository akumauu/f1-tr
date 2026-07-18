#!/usr/bin/env python3
"""对锁定的首轮模型执行 2025 分站块配对 bootstrap，不改变任何模型。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
from sklearn.metrics import mean_absolute_error

import run_experiments as base


OUTPUT_PATH = base.RECORDS_DIR / "model_comparisons.json"
SUMMARY_PATH = base.RECORDS_DIR / "model_comparisons.md"


def fit_locked_predictions(
    train: Any,
    validation: Any,
    locked_results: dict[str, Any],
    attempt: int,
) -> dict[str, np.ndarray]:
    solver = {1: "lsqr", 2: "sag", 3: "sparse_cg"}[attempt]
    predictions: dict[str, np.ndarray] = {
        "zero_relative_pace": np.zeros(len(validation), dtype=float)
    }
    for name, spec in base.MODEL_SPECS.items():
        locked = locked_results["models"][name]
        categorical = list(spec["categorical"])
        features = categorical + base.NUMERIC_FEATURES
        pipeline = base.make_pipeline(categorical, float(locked["selected_alpha"]), solver)
        pipeline.fit(train[features], train["target_log_pct"].to_numpy(dtype=float))
        predictions[name] = pipeline.predict(validation[features])
    return predictions


def paired_bootstrap(
    validation: Any,
    first: np.ndarray,
    second: np.ndarray,
    seed: int,
    repetitions: int = 5000,
) -> dict[str, Any]:
    y = validation["target_log_pct"].to_numpy(dtype=float)
    meetings = validation["meeting_key"].to_numpy()
    unique_meetings = np.unique(meetings)
    rng = np.random.default_rng(seed)
    deltas: list[float] = []
    for _ in range(repetitions):
        sampled = rng.choice(unique_meetings, len(unique_meetings), replace=True)
        index = np.concatenate([np.flatnonzero(meetings == meeting) for meeting in sampled])
        deltas.append(
            float(
                mean_absolute_error(y[index], first[index])
                - mean_absolute_error(y[index], second[index])
            )
        )
    low, high = np.quantile(deltas, [0.025, 0.975])
    observed = float(mean_absolute_error(y, first) - mean_absolute_error(y, second))
    return {
        "observed_mae_delta": observed,
        "bootstrap_mean": float(np.mean(deltas)),
        "ci95_low": float(low),
        "ci95_high": float(high),
        "probability_first_better": float(np.mean(np.asarray(deltas) < 0)),
        "interpretation": "negative delta favors first_model",
        "bootstrap_unit": "meeting",
        "repetitions": repetitions,
    }


def comparison_set(
    validation: Any,
    predictions: dict[str, np.ndarray],
    seed: int,
) -> dict[str, Any]:
    pairs = [
        ("driver_team_ridge", "zero_relative_pace"),
        ("driver_team_ridge", "context_ridge"),
        ("driver_team_ridge", "driver_only_ridge"),
        ("driver_team_ridge", "team_only_ridge"),
        ("team_only_ridge", "driver_only_ridge"),
    ]
    output: dict[str, Any] = {}
    for offset, (first, second) in enumerate(pairs):
        output[f"{first}_vs_{second}"] = {
            "first_model": first,
            "second_model": second,
            **paired_bootstrap(
                validation,
                predictions[first],
                predictions[second],
                seed=seed + offset,
            ),
        }
    return output


def write_summary(results: dict[str, Any]) -> None:
    lines = [
        "# 首轮模型配对分站 Bootstrap",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        "- 模型和 alpha 完全复用首轮锁定结果，没有重新调参。",
        "- delta = first MAE - second MAE，负数表示 first 更好。",
        "",
        "| 比较 | 观测 delta | 95% CI | first 更优概率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, row in results["all_sessions"].items():
        lines.append(
            f"| {name} | {row['observed_mae_delta']:.5f} | "
            f"[{row['ci95_low']:.5f}, {row['ci95_high']:.5f}] | "
            f"{row['probability_first_better']:.3f} |"
        )
    lines.extend(["", "## Race 与 Sprint 分层", ""])
    for session_name, comparisons in results["by_session"].items():
        lines.extend(
            [
                f"### {session_name}",
                "",
                "| 比较 | 观测 delta | 95% CI | first 更优概率 |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for name, row in comparisons.items():
            lines.append(
                f"| {name} | {row['observed_mae_delta']:.5f} | "
                f"[{row['ci95_low']:.5f}, {row['ci95_high']:.5f}] | "
                f"{row['probability_first_better']:.3f} |"
            )
        lines.append("")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    config = base.load_json(base.DEFAULT_CONFIG)
    locked_results = base.load_json(base.RESULTS_PATH)
    train, validation, manifest = base.load_frozen_data(config)
    train = train.reset_index(drop=True)
    validation = validation.reset_index(drop=True)
    run_id = f"locked_model_pairwise_bootstrap-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    attempt_result = base.run_with_retries(
        run_id,
        "phase_2_evaluation",
        "locked_model_pairwise_bootstrap_v1",
        int(config["max_attempts"]),
        lambda attempt: fit_locked_predictions(train, validation, locked_results, attempt),
    )
    if attempt_result.value is None:
        return 2
    predictions = attempt_result.value
    seed = int(config["random_seed"]) + 500
    results: dict[str, Any] = {
        "run_id": run_id,
        "created_at": base.now_iso(),
        "locked_parent_run_id": locked_results["run_id"],
        "data_manifest_hash": base.canonical_hash(manifest),
        "all_sessions": comparison_set(validation, predictions, seed),
        "by_session": {},
    }
    for offset, (session_name, positions) in enumerate(
        validation.groupby("session_name").indices.items()
    ):
        index = np.asarray(positions, dtype=int)
        subset = validation.iloc[index].reset_index(drop=True)
        subset_predictions = {name: value[index] for name, value in predictions.items()}
        results["by_session"][str(session_name)] = comparison_set(
            subset, subset_predictions, seed + 100 + offset * 10
        )
    base.atomic_json(OUTPUT_PATH, results)
    write_summary(results)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
