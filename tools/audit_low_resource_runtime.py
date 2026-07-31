#!/usr/bin/env python3
"""审计 Race Dossier 正式入口的低资源、D 盘缓存与零 WMI 合同。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_WMI_QUERIES = (
    "get-ciminstance",
    "get-wmiobject",
    "win32_process",
    "win32_perfformatteddata_perfproc_process",
    "wmic.exe",
)
THREAD_CAPS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def audit_runtime(version: str = "v15") -> dict[str, Any]:
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("version 必须采用 vN 格式")
    operational_files = (
        ROOT / "research" / "run_race_dossiers_v8.py",
        ROOT / "research" / f"run_race_dossiers_{version}.py",
        ROOT / "tools" / f"run_race_dossier_{version}_low_resource.ps1",
    )
    config_path = (
        ROOT
        / "research"
        / "configs"
        / f"race_dossier_2023_2025_{version}.json"
    )
    missing_files = [
        path.relative_to(ROOT).as_posix()
        for path in operational_files
        if not path.is_file()
    ]
    source_by_path = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in operational_files
        if path.is_file()
    }
    forbidden_hits: list[dict[str, str]] = []
    for relative_path, source in source_by_path.items():
        lowered = source.lower()
        for pattern in FORBIDDEN_WMI_QUERIES:
            if pattern in lowered:
                forbidden_hits.append(
                    {"path": relative_path, "pattern": pattern}
                )

    combined = "\n".join(source_by_path.values())
    wrapper_key = f"tools/run_race_dossier_{version}_low_resource.ps1"
    wrapper = source_by_path.get(
        wrapper_key, ""
    )
    direct_win32_priority = "SetPriorityClass" in combined
    foreground_serial = (
        f"& python -u research/run_race_dossiers_{version}.py @args"
        in wrapper
        and "Start-Process" not in wrapper
    )
    thread_caps = {
        name: f'$env:{name} = "1"' in wrapper for name in THREAD_CAPS
    }

    config = json.loads(config_path.read_text(encoding="utf-8"))
    path_values = [
        config["runtime"]["project_cache"],
        *config["outputs"].values(),
    ]
    invalid_project_paths: list[str] = []
    for value in path_values:
        path = (ROOT / str(value)).resolve()
        if not path.is_relative_to(ROOT) or path.drive.upper() != "D:":
            invalid_project_paths.append(str(value))

    passed = bool(
        not missing_files
        and not forbidden_hits
        and direct_win32_priority
        and foreground_serial
        and all(thread_caps.values())
        and not invalid_project_paths
    )
    return {
        "passed": passed,
        "version": version,
        "scope": list(source_by_path),
        "missing_files": missing_files,
        "forbidden_wmi_query_hits": forbidden_hits,
        "direct_win32_priority": direct_win32_priority,
        "foreground_serial_without_child_process": foreground_serial,
        "single_thread_caps": thread_caps,
        "project_local_d_drive_paths": not invalid_project_paths,
        "invalid_project_paths": invalid_project_paths,
        "boundary": (
            "该静态审计证明项目入口未主动查询 WMI；不能证明其他宿主、"
            "终端、杀毒或监控程序不会在外部枚举进程。"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v15")
    args = parser.parse_args()
    result = audit_runtime(args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
