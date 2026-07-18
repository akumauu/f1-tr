#!/usr/bin/env python3
"""评估跨赛季车队名称归一化对锁定模型的影响；属于事后敏感性分析。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

import analyze_model_comparisons as comparisons
import run_experiments as base


ALIASES_PATH = base.ROOT / "research" / "configs" / "entity_aliases.json"
OUTPUT_PATH = base.RECORDS_DIR / "team_alias_sensitivity.json"
SUMMARY_PATH = base.RECORDS_DIR / "team_alias_sensitivity.md"


def canonicalize(frame: Any, aliases: dict[str, str]) -> Any:
    result = frame.copy()
    result["team_name"] = result["team_name"].replace(aliases)
    return result


def fit_original_and_canonical(
    train: Any,
    validation: Any,
    aliases: dict[str, str],
    locked_results: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    solver = {1: "lsqr", 2: "sag", 3: "sparse_cg"}[attempt]
    outputs: dict[str, Any] = {}
    for variant, source_train, source_validation in (
        ("original", train, validation),
        ("canonical", canonicalize(train, aliases), canonicalize(validation, aliases)),
    ):
        variant_predictions: dict[str, np.ndarray] = {}
        variant_metrics: dict[str, Any] = {}
        for name, spec in base.MODEL_SPECS.items():
            categorical = list(spec["categorical"])
            features = categorical + base.NUMERIC_FEATURES
            alpha = float(locked_results["models"][name]["selected_alpha"])
            pipeline = base.make_pipeline(categorical, alpha, solver)
            pipeline.fit(
                source_train[features], source_train["target_log_pct"].to_numpy(dtype=float)
            )
            prediction = pipeline.predict(source_validation[features])
            variant_predictions[name] = prediction
            variant_metrics[name] = {
                "validation_metrics": base.regression_metrics(
                    source_validation["target_log_pct"].to_numpy(dtype=float), prediction
                ),
                "unseen_category_rates": base.unseen_rates(
                    source_train, source_validation, categorical
                ),
            }
        outputs[variant] = {
            "metrics": variant_metrics,
            "predictions": variant_predictions,
        }
    return outputs


def write_summary(results: dict[str, Any]) -> None:
    lines = [
        "# 车队名称归一化敏感性实验",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        "- 性质：查看首轮结果后进行的事后元数据敏感性分析，不替换主验证结果。",
        "- 模型结构和 alpha 均保持锁定，只统一同一车队的跨来源名称。",
        "",
        "| 模型 | 原始 MAE | 归一化 MAE | delta | 原始 team 未见率 | 归一化 team 未见率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in base.MODEL_SPECS:
        original = results["variants"]["original"][name]
        canonical = results["variants"]["canonical"][name]
        original_mae = original["validation_metrics"]["mae"]
        canonical_mae = canonical["validation_metrics"]["mae"]
        original_unseen = original["unseen_category_rates"].get("team_name", 0.0)
        canonical_unseen = canonical["unseen_category_rates"].get("team_name", 0.0)
        lines.append(
            f"| {name} | {original_mae:.5f} | {canonical_mae:.5f} | "
            f"{canonical_mae - original_mae:.5f} | {original_unseen:.4f} | {canonical_unseen:.4f} |"
        )
    paired = results["joint_model_canonical_vs_original"]
    lines.extend(
        [
            "",
            "## 联合模型配对分站 Bootstrap",
            "",
            f"- canonical - original MAE：{paired['observed_mae_delta']:.5f}",
            f"- 95% CI：[{paired['ci95_low']:.5f}, {paired['ci95_high']:.5f}]",
            f"- 归一化版本更优概率：{paired['probability_first_better']:.3f}",
            "",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    config = base.load_json(base.DEFAULT_CONFIG)
    alias_config = base.load_json(ALIASES_PATH)
    locked_results = base.load_json(base.RESULTS_PATH)
    train, validation, manifest = base.load_frozen_data(config)
    train = train.reset_index(drop=True)
    validation = validation.reset_index(drop=True)
    run_id = f"team_alias_sensitivity-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    attempt_result = base.run_with_retries(
        run_id,
        "data_quality_sensitivity",
        "team_alias_sensitivity_v1",
        int(config["max_attempts"]),
        lambda attempt: fit_original_and_canonical(
            train,
            validation,
            alias_config["team_aliases"],
            locked_results,
            attempt,
        ),
    )
    if attempt_result.value is None:
        return 2
    raw = attempt_result.value
    variants: dict[str, Any] = {}
    for variant in ("original", "canonical"):
        variants[variant] = raw[variant]["metrics"]
    paired = comparisons.paired_bootstrap(
        validation,
        raw["canonical"]["predictions"]["driver_team_ridge"],
        raw["original"]["predictions"]["driver_team_ridge"],
        seed=int(config["random_seed"]) + 900,
        repetitions=5000,
    )
    results = {
        "run_id": run_id,
        "created_at": base.now_iso(),
        "methodological_status": alias_config["methodological_status"],
        "alias_version": alias_config["version"],
        "aliases": alias_config["team_aliases"],
        "locked_parent_run_id": locked_results["run_id"],
        "data_manifest_hash": base.canonical_hash(manifest),
        "variants": variants,
        "joint_model_canonical_vs_original": paired,
    }
    base.atomic_json(OUTPUT_PATH, results)
    write_summary(results)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=base.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
