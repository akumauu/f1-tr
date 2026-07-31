#!/usr/bin/env python3
"""运行综合评分系统的历史客观性与 2026 发布资格门控。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

import run_experiments as base
from modeling.objective_evaluation import evaluate_objectivity


ROOT = base.ROOT
DEFAULT_CONFIG = ROOT / "research" / "configs" / "objective_rating_review_v1.json"


def _load(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"客观性门控输入不存在：{path}")
    return base.load_json(path)


def _input_identity(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "path": str(path),
            "sha256": base.file_sha256(ROOT / path),
        }
        for name, path in config["inputs"].items()
    }


def _summary(result: Mapping[str, Any]) -> str:
    report = result["evaluation"]
    lines = [
        "# 综合评分系统客观性门控",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        f"- 历史核心门控：{'通过' if report['core_historical_passed'] else '未通过'}",
        f"- 完整车手卡：{'可发布' if report['full_driver_card_ready'] else '尚不可发布'}",
        f"- 状态：`{report['release_status']}`",
        "",
        "## 门控结果",
        "",
        "| 门控 | 角色 | 结果 | 实际值 | 阈值 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for gate in report["gates"]:
        lines.append(
            f"| `{gate['gate_id']}` | `{gate['role']}` | "
            f"{'PASS' if gate['passed'] else 'FAIL'} | `{json.dumps(gate['value'], ensure_ascii=False)}` | "
            f"`{json.dumps(gate['threshold'], ensure_ascii=False)}` |"
        )
    lines.extend(
        [
            "",
            "## 模块成熟度",
            "",
            "| 模块 | 状态 |",
            "| --- | --- |",
        ]
    )
    for module, status in report["module_coverage"].items():
        lines.append(f"| `{module}` | `{status}` |")
    lines.extend(["", "## 弱识别与边界", ""])
    for flag in report["weak_identification_flags"]:
        lines.append(f"- `{flag}`")
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(
        [
            "",
            "历史结果解释层通过不等于完整车手能力卡已经完成；缺失模块必须继续按机会暴露和时间外协议验证。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = base.load_json(args.config)
    seasonal = _load(config["inputs"]["seasonal_result"])
    bayesian = _load(config["inputs"]["bayesian_result"])
    race_by_race = _load(config["inputs"]["race_by_race_result"])
    conditional_pair = _load(config["inputs"]["conditional_pair_prototype"])
    actual_ability_fields = conditional_pair["actual_design_audit"]["fields"]
    module_coverage = dict(config["module_coverage"])
    module_coverage["PAC"] = (
        "validated"
        if conditional_pair.get("pac") is not None
        and conditional_pair.get("publication_gate", {}).get("passed") is True
        else "failed"
    )
    evaluation = evaluate_objectivity(
        seasonal,
        bayesian,
        race_by_race,
        ability_feature_names=actual_ability_fields,
        module_coverage=module_coverage,
        thresholds=config["thresholds"],
    )
    result = {
        "run_id": f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "network_calls": [],
        "input_identity": _input_identity(config),
        "evaluation": evaluation.to_dict(),
        "pac_validation_reference": {
            "path": config["inputs"]["conditional_pair_prototype"],
            "protocol_hash": conditional_pair["protocol_hash"],
            "status": conditional_pair["pac_status"],
            "publication_gate_passed": conditional_pair["publication_gate"]["passed"],
            "pac": conditional_pair["pac"],
            "ability_feature_audit_source": "actual_design_audit.fields",
        },
        "interpretation": {
            "historical_core": "车辆/实跑结果构念、时间前推、区间和泄漏门控",
            "full_driver_card": "所有能力模块验证前保持不可发布",
            "season_2026": "事件暴露不足时只允许逐场探索性分析",
        },
    }
    output = ROOT / config["outputs"]["result"]
    summary = ROOT / config["outputs"]["summary"]
    base.atomic_json(output, result)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(_summary(result), encoding="utf-8")
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    return 0 if evaluation.core_historical_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
