#!/usr/bin/env python3
"""以前台单线程低资源模式生成 Race Dossier v11。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_experiments as base
import run_race_dossiers_v8 as engine


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "race_dossier_2023_2025_v11.json"
)


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
    config = base.load_json(config_path)
    if config.get("method_version") != "v11":
        parser.error("v11 runner 只接受 method_version=v11")
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
