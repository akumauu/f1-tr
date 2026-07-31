#!/usr/bin/env python3
"""以前台单线程低资源模式生成带逐圈曲线证据的 Race Dossier v17。"""

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
    ROOT / "research" / "configs" / "race_dossier_2023_2025_v17.json"
)


def _deep_merge(
    base_value: Mapping[str, Any],
    overlay_value: Mapping[str, Any],
) -> dict[str, Any]:
    """递归合并冻结的 v16 基线与 v17 逐圈证据增量。"""

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


def _load_resolved(path: Path) -> dict[str, Any]:
    overlay = base.load_json(path)
    extends = Path(str(overlay["extends"]))
    base_path = (ROOT / extends).resolve()
    if not base_path.is_relative_to(ROOT) or not base_path.is_file():
        raise ValueError("v17 extends 必须指向项目内存在的冻结配置")
    base_config = base.load_json(base_path)
    if "extends" in base_config:
        nested_path = (ROOT / str(base_config["extends"])).resolve()
        if not nested_path.is_relative_to(ROOT) or not nested_path.is_file():
            raise ValueError("v17 的 v16 基线 extends 无效")
        base_config = _deep_merge(
            base.load_json(nested_path),
            base_config,
        )
    return _deep_merge(base_config, overlay)


def load_resolved_config(config_path: Path) -> dict[str, Any]:
    overlay = base.load_json(config_path)
    base_overlay_path = (ROOT / str(overlay["extends"])).resolve()
    base_overlay = base.load_json(base_overlay_path)
    resolved = _load_resolved(config_path)
    resolved.pop("model_registry_append", None)
    additions = [
        *list(base_overlay.get("model_registry_append", [])),
        *list(overlay.get("model_registry_append", [])),
    ]
    accepted = list(
        resolved["model_registry"].get(
            "accepted_for_v16",
            resolved["model_registry"].get("accepted_for_v15", []),
        )
    )
    resolved["model_registry"]["accepted_for_v17"] = accepted + additions
    resolved["resolved_base_config"] = str(
        Path(str(overlay["extends"])).as_posix()
    )
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
    if config.get("method_version") != "v17":
        parser.error("v17 runner 只接受 method_version=v17")
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
