#!/usr/bin/env python3
"""从规范 v4 Parquet 构建 2023–2025 排位准确推圈冻结集。"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import build_v4_frozen_data as frozen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "research" / "configs" / "four_team_2023_2025_event_stint_v4.json"
)


def prepare_qualifying_laps(
    raw: pd.DataFrame, settings: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, int]]:
    """保留 FastF1/TracingInsights 标记为 accurate 的干地排位推圈。"""

    frame = frozen.prepare_compatible_columns(raw)
    duration_ok = frame["lap_duration"].between(
        float(settings["minimum_lap_duration_seconds"]),
        float(settings["maximum_lap_duration_seconds"]),
    )
    dry_ok = frame["compound"].isin(settings["dry_compounds"])
    accurate_ok = frame["is_accurate"].astype(bool)
    deleted_ok = ~frame["deleted"].astype(bool)
    green_ok = frame["track_status"].fillna("").astype(str).eq("1")
    single_commit_ok = frame["source_commit_count"].eq(1)
    mask = (
        duration_ok
        & dry_ok
        & accurate_ok
        & deleted_ok
        & green_ok
        & single_commit_ok
    )
    candidate = frame.loc[mask].copy()
    counts = candidate.groupby("session_key")["lap_duration"].transform("size")
    minimum = int(settings["minimum_accurate_laps_per_session"])
    clean = candidate.loc[counts >= minimum].copy()
    if clean.empty:
        raise RuntimeError("排位准确推圈清洗后为空。")

    clean["session_reference"] = clean.groupby("session_key")[
        "lap_duration"
    ].transform("median")
    clean["target_log_pct"] = 100.0 * np.log(
        clean["lap_duration"] / clean["session_reference"]
    )
    session_max = clean.groupby("session_key")["lap_number"].transform("max")
    clean["lap_fraction"] = clean["lap_number"] / session_max.clip(lower=1)
    clean["lap_fraction_sq"] = clean["lap_fraction"] ** 2
    clean["tyre_age"] = pd.to_numeric(clean["tyre_age"], errors="coerce")
    clean["tyre_age_sq"] = clean["tyre_age"] ** 2
    driver_session = ["year", "meeting_name", "session_name", "driver_id"]
    clean["driver_session_best_seconds"] = clean.groupby(driver_session)[
        "lap_duration"
    ].transform("min")
    clean["gap_to_driver_best_pct"] = 100.0 * (
        clean["lap_duration"] / clean["driver_session_best_seconds"] - 1.0
    )
    clean["push_rank"] = clean.groupby(driver_session)["lap_duration"].rank(
        method="first"
    )
    clean = clean.sort_values(
        ["year", "meeting_name", "driver_id", "lap_number"]
    ).reset_index(drop=True)
    audit = {
        "raw_laps": int(len(frame)),
        "duration_out_of_bounds": int((~duration_ok).sum()),
        "non_dry_compound": int((~dry_ok).sum()),
        "not_accurate": int((~accurate_ok).sum()),
        "deleted": int((~deleted_ok).sum()),
        "non_green_track_status": int((~green_ok).sum()),
        "multiple_source_commits_in_lap": int((~single_commit_ok).sum()),
        "below_session_minimum": int(mask.sum() - len(clean)),
        "clean_accurate_push_laps": int(len(clean)),
    }
    return clean, audit


OUTPUT_COLUMNS = frozen.OUTPUT_COLUMNS + [
    "driver_session_best_seconds",
    "gap_to_driver_best_pct",
    "push_rank",
]


def write_deterministic_csv(frame: pd.DataFrame, path: Path) -> None:
    """写入字节可复现的 gzip CSV。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0
        ) as gzip_handle:
            frame[OUTPUT_COLUMNS].to_csv(
                gzip_handle, index=False, encoding="utf-8", lineterminator="\n"
            )
    temporary.replace(path)


def build(config_path: Path, parquet_root: Path) -> dict[str, Any]:
    config = frozen.load_json(config_path)
    settings = config["qualifying_freeze"]
    years = [int(value) for value in settings["years"]]
    sessions = [str(value) for value in settings["sessions"]]
    files, provenance = frozen.source_catalog(parquet_root, years, sessions)
    raw = frozen.extract_laps(files)
    clean, audit = prepare_qualifying_laps(raw, settings)
    output = ROOT / settings["output"]
    manifest_path = ROOT / settings["manifest"]
    write_deterministic_csv(clean, output)
    manifest = {
        "created_at": frozen.now_iso(),
        "experiment_name": config["experiment_name"],
        "schema_version": frozen.SCHEMA_VERSION,
        "query_version": "tracinginsights_expanded_v4_qualifying_push_freeze_v1",
        "config_path": config_path.relative_to(ROOT).as_posix(),
        "config_hash": frozen.canonical_hash(config),
        "source_provenance": provenance,
        "coverage": {
            "rows": int(len(clean)),
            "years": sorted(clean["year"].astype(int).unique().tolist()),
            "meetings": int(clean["meeting_key"].nunique()),
            "sessions": int(clean["session_key"].nunique()),
            "drivers": int(clean["driver_id"].nunique()),
            "summary_by_year": clean.groupby("year")
            .agg(
                rows=("lap_duration", "size"),
                meetings=("meeting_key", "nunique"),
                drivers=("driver_id", "nunique"),
            )
            .reset_index()
            .to_dict(orient="records"),
        },
        "cleaning_audit": audit,
        "file": {
            "path": output.relative_to(ROOT).as_posix(),
            "sha256": frozen.file_sha256(output),
            "bytes": output.stat().st_size,
        },
        "boundaries": {
            "accurate_flag_role": "FastF1 accurate lap proxy for push-lap eligibility",
            "q1_q2_q3_phase": "not_available_in_current_freeze",
            "setup_identity": "not_observed",
        },
    }
    frozen.atomic_json(manifest_path, manifest)
    return manifest


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--parquet-root", type=Path, default=frozen.DEFAULT_PARQUET_ROOT
    )
    args = parser.parse_args()
    manifest = build(args.config.resolve(), args.parquet_root.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=frozen.json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
