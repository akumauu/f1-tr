#!/usr/bin/env python3
"""以前台单线程低资源模式生成 Race Dossier v16。"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import run_experiments as base
import run_race_dossiers_v8 as engine


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "race_dossier_2023_2025_v16.json"
)


def _deep_merge(
    base_value: Mapping[str, Any],
    overlay_value: Mapping[str, Any],
) -> dict[str, Any]:
    """递归合并冻结的 v15 基线与很小的 v16 协议增量。"""

    merged = deepcopy(dict(base_value))
    for key, value in overlay_value.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_resolved_config(config_path: Path) -> dict[str, Any]:
    overlay = base.load_json(config_path)
    extends = Path(str(overlay["extends"]))
    base_path = (ROOT / extends).resolve()
    if not base_path.is_relative_to(ROOT) or not base_path.is_file():
        raise ValueError("v16 extends 必须指向项目内存在的冻结配置")
    resolved = _deep_merge(base.load_json(base_path), overlay)
    additions = list(resolved.pop("model_registry_append", []))
    accepted_v15 = list(
        resolved["model_registry"].get("accepted_for_v15", [])
    )
    resolved["model_registry"]["accepted_for_v16"] = (
        accepted_v15 + additions
    )
    resolved["resolved_base_config"] = extends.as_posix()
    return resolved


def main() -> int:
    engine._apply_low_resource_process_policy()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-id")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    if not config_path.is_relative_to(ROOT):
        parser.error("--config 必须位于项目目录内")
    config = load_resolved_config(config_path)
    if config.get("method_version") != "v16":
        parser.error("v16 runner 只接受 method_version=v16")
    result = engine.run(
        config,
        run_id=args.run_id,
        config_path=config_path,
    )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
