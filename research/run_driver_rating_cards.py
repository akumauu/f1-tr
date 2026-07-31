#!/usr/bin/env python3
"""生成 2023–2025 全场历史车手评分候选卡；未过模块门控时保持空值。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Mapping

import run_experiments as base
from modeling.driver_cards import build_driver_rating_cards


ROOT = base.ROOT
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "driver_rating_cards_2023_2025_v1.json"
)


def _load_input(relative_path: str) -> tuple[Path, dict[str, Any]]:
    path = ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"车手卡输入不存在：{path}")
    return path, base.load_json(path)


def _summary(result: Mapping[str, Any]) -> str:
    reference = result["reference_distribution"]
    coverage = result["coverage"]
    future = result["season_2026_release_gate"]
    lines = [
        "# 2023–2025 全场历史车手评分卡",
        "",
        f"- 运行 ID：`{result['run_id']}`",
        f"- 发布范围：`{', '.join(coverage['published_modules'])}`",
        f"- 车手数：**{coverage['driver_count']}**",
        f"- 赛事 / 清洁圈：**{coverage['race_meetings']} / {coverage['clean_laps']}**",
        f"- 冻结总体标准差：`{reference['population_sd']:.17g}`（ddof=0）",
        "- PAC 候选公式：`clip(75 + 10z, 0, 100)`；不按样本量二次收缩。",
        "- PAC 当前值与区间均为 `null`：尚缺车手级时间前推排序稳定性与池化区间覆盖。",
        "",
        "## 车手卡",
        "",
        "| 排名 | 车手 | PAC | z | CI | 年份 | 识别标志 |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for card in result["cards"]:
        pac = card["metrics"]["PAC"]
        flags = ", ".join(card["identification"]["flags"])
        years = "/".join(str(year) for year in card["years"])
        pac_rating = f"{pac['rating']:.2f}" if pac else "`null`"
        pac_z = f"{pac['z']:+.3f}" if pac else "`null`"
        lines.append(
            f"| {card['pac_rank'] or '—'} | `{card['driver']}` | "
            f"{pac_rating} | {pac_z} | `null` | {years} | `{flags}` |"
        )

    lines.extend(
        [
            "",
            "## 模块成熟度与空值合同",
            "",
            "| 指标 | 成熟度 / 状态 | 当前值 |",
            "| --- | --- | --- |",
        ]
    )
    for module, status in coverage["module_coverage"].items():
        value = "已发布" if module in coverage["published_modules"] else "`null`"
        lines.append(f"| `{module}` | `{status}` | {value} |")
    lines.extend(
        [
            "| `EventDelivery` | `not_estimated` | `null` |",
            "| `OVR` | `blocked_until_all_ability_modules_validated` | `null` |",
            "",
            "## 2026 发布门槛",
            "",
            f"- 已观测 / 最低分站：**{future['observed_events']} / {future['minimum_events']}**",
            f"- 门控：{'PASS' if future['passed'] else 'FAIL'}",
            f"- 当前模式：`{future['publication_mode']}`",
            f"- 说明：{future['note']}",
            "",
            "## 解释边界",
            "",
        ]
    )
    for flag in result["identification"]["flags"]:
        lines.append(f"- `{flag}`")
    lines.extend(
        [
            "- 积分、完赛位置、冠军身份等赛果字段不得进入能力评分。",
            "- `sample_count_total` 只说明证据覆盖，不改变本层 PAC。",
            "- 当前结果是池化长期配速能力卡，不是单场发挥、轮胎管理或轮对轮能力卡。",
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
    seasonal_path, seasonal = _load_input(config["inputs"]["seasonal_result"])
    objective_path, objective = _load_input(config["inputs"]["objective_result"])
    card_set = build_driver_rating_cards(
        seasonal,
        objective,
        config["rating_contract"],
    )
    result = {
        "run_id": f"{config['experiment_name']}-{base.now_iso().replace(':', '').replace('-', '')}",
        "created_at": base.now_iso(),
        "git_head": base.git_head(),
        "config": config,
        "config_hash": base.canonical_hash(config),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "network_calls": [],
        "input_identity": {
            "seasonal_result": {
                "path": config["inputs"]["seasonal_result"],
                "sha256": base.file_sha256(seasonal_path),
                "run_id": seasonal["run_id"],
                "config_hash": seasonal["config_hash"],
            },
            "objective_result": {
                "path": config["inputs"]["objective_result"],
                "sha256": base.file_sha256(objective_path),
                "run_id": objective["run_id"],
                "config_hash": objective["config_hash"],
            },
        },
        **card_set,
    }
    output = ROOT / config["outputs"]["result"]
    summary = ROOT / config["outputs"]["summary"]
    base.atomic_json(output, result)
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(_summary(result), encoding="utf-8")
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=base.json_default))
    # 候选评分即使因科学门控保持空值，也应成功生成可审计机器记录。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
