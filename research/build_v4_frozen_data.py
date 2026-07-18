#!/usr/bin/env python3
"""从验收通过的 TracingInsights v4 Parquet 构建圈级研究冻结数据。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARQUET_ROOT = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
)
DEFAULT_CONFIG = ROOT / "research" / "configs" / "2024_train_2025_validate.json"
DEFAULT_DECAY_CONFIG = ROOT / "research" / "configs" / "dynamic_decay.json"
ALIASES_CONFIG = ROOT / "research" / "configs" / "entity_aliases.json"
DATA_DIR = ROOT / "research" / "artifacts" / "data"
PRIMARY_MANIFEST = ROOT / "research" / "artifacts" / "data_manifest.json"
TUNING_MANIFEST = ROOT / "research" / "artifacts" / "dynamic_tuning_data_manifest.json"
COMPARISON_REPORT = ROOT / "research" / "artifacts" / "v4_migration_comparison.json"
ACCEPTANCE_REPORT = ROOT / "data" / "logs" / "parquet-acceptance-report.json"
SCHEMA_VERSION = "tracinginsights-expanded-v4"
QUERY_VERSION = "tracinginsights_expanded_v4_lap_freeze_v1"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    temporary.replace(path)


def json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(f"无法序列化 {type(value)!r}")


def source_catalog(
    parquet_root: Path,
    years: list[int],
    sessions: list[str],
) -> tuple[list[Path], dict[str, Any]]:
    files: list[Path] = []
    catalogs: list[dict[str, Any]] = []
    chain_rows: list[str] = []
    for year in sorted(years):
        manifests = sorted((parquet_root / f"year={year}").glob("commit=*/manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError(f"{year} 应且仅应有一个 v4 manifest，实际为 {len(manifests)}")
        manifest_path = manifests[0]
        manifest = load_json(manifest_path)
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError(f"schema 不匹配: {manifest_path}")
        selected = [row for row in manifest["partitions"] if row["session"] in sessions]
        if not selected:
            raise RuntimeError(f"{year} 没有目标赛段: {sessions}")
        for row in selected:
            path = manifest_path.parent / row["parquet_path"]
            if not path.is_file():
                raise FileNotFoundError(path)
            files.append(path)
            relative = path.relative_to(ROOT).as_posix()
            chain_rows.append(f"{relative}:{row['parquet_sha256']}")
        catalogs.append(
            {
                "year": year,
                "manifest": manifest_path.relative_to(ROOT).as_posix(),
                "manifest_sha256": file_sha256(manifest_path),
                "source_repository": manifest.get("source_repository"),
                "source_commit": manifest["source_commit"],
                "selected_partitions": len(selected),
                "selected_rows": int(sum(int(row["rows"]) for row in selected)),
                "selected_parquet_bytes": int(
                    sum(int(row["parquet_bytes"]) for row in selected)
                ),
            }
        )
    return files, {
        "schema_version": SCHEMA_VERSION,
        "catalogs": catalogs,
        "selected_file_count": len(files),
        "selected_file_set_sha256": hashlib.sha256(
            "\n".join(sorted(chain_rows)).encode("utf-8")
        ).hexdigest(),
        "acceptance_report": {
            "path": ACCEPTANCE_REPORT.relative_to(ROOT).as_posix(),
            "sha256": file_sha256(ACCEPTANCE_REPORT),
        },
    }


def extract_laps(files: list[Path]) -> pd.DataFrame:
    connection = duckdb.connect()
    try:
        connection.execute("SET threads TO 4")
        frames: list[pd.DataFrame] = []
        for index, path in enumerate(files, start=1):
            frames.append(connection.execute(
                """
            SELECT
                CAST(year AS INTEGER) AS year,
                meeting,
                session,
                CAST(driver_number AS INTEGER) AS driver_number,
                CAST(lap AS INTEGER) AS lap_number,
                arg_min(driver_acronym, sample_index) AS driver_id,
                arg_min(team, sample_index) AS team_name,
                CAST(arg_min(lap_duration, sample_index) AS DOUBLE) AS lap_duration,
                upper(trim(arg_min(compound, sample_index))) AS compound,
                CAST(arg_min(stint, sample_index) AS INTEGER) AS stint_number,
                CAST(arg_min(tyre_life, sample_index) AS DOUBLE) AS tyre_age,
                arg_min(coalesce(deleted, false), sample_index) AS deleted,
                arg_min(coalesce(is_accurate, false), sample_index) AS is_accurate,
                arg_min(coalesce(track_status, ''), sample_index) AS track_status,
                arg_min(source_commit, sample_index) AS source_commit,
                1 AS source_commit_count,
                count(*) AS sample_count
            FROM read_parquet(?, hive_partitioning=false)
            GROUP BY year, meeting, session, driver_number, lap
            ORDER BY year, meeting, session, driver_number, lap
            """
                , [str(path)]).fetch_df())
            if index % 10 == 0 or index == len(files):
                print(f"已聚合 v4 分区: {index}/{len(files)}", flush=True)
        return pd.concat(frames, ignore_index=True)
    finally:
        connection.close()


def prepare_compatible_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["meeting_key"] = (
        result["year"].astype(str) + "|" + result["meeting"].astype(str)
    )
    result["meeting_name"] = result["meeting"].astype(str)
    result["circuit_name"] = result["meeting"].astype(str)
    result["session_key"] = (
        result["meeting_key"] + "|" + result["session"].astype(str)
    )
    result["session_name"] = result["session"].astype(str)
    result["session_date_start"] = pd.NaT
    result["driver_id"] = result["driver_id"].fillna(result["driver_number"].astype(str))
    result["driver_name"] = result["driver_id"]
    result["team_name"] = result["team_name"].fillna("<UNKNOWN_TEAM>").astype(str)
    result["is_pit_out_lap"] = False
    result["is_pit_in_lap"] = False
    result["is_disrupted_lap"] = result["track_status"] != "1"
    stint_group = ["year", "meeting", "session", "driver_number", "stint_number"]
    result["stint_lap_start"] = result.groupby(stint_group, dropna=False)[
        "lap_number"
    ].transform("min")
    result["stint_lap_end"] = result.groupby(stint_group, dropna=False)[
        "lap_number"
    ].transform("max")
    result["tyre_age_at_start"] = (
        result["tyre_age"] - result["lap_number"] + result["stint_lap_start"]
    ).clip(lower=0)
    return result


def clean_laps(frame: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, int]]:
    result = prepare_compatible_columns(frame)
    clean_cfg = config["cleaning"]
    duration_ok = result["lap_duration"].between(
        float(clean_cfg["minimum_lap_duration_seconds"]),
        float(clean_cfg["maximum_lap_duration_seconds"]),
    )
    lap_number_ok = result["lap_number"] >= int(clean_cfg["minimum_lap_number"])
    dry_ok = result["compound"].isin(config["dry_compounds"])
    tyre_ok = result["tyre_age"].notna()
    accurate_ok = result["is_accurate"].astype(bool)
    deleted_ok = ~result["deleted"].astype(bool)
    green_ok = ~result["is_disrupted_lap"].astype(bool)
    single_commit_ok = result["source_commit_count"] == 1
    base_mask = (
        duration_ok
        & lap_number_ok
        & dry_ok
        & tyre_ok
        & accurate_ok
        & deleted_ok
        & green_ok
        & single_commit_ok
    )
    candidate = result.loc[base_mask].copy()
    if candidate.empty:
        raise RuntimeError("v4 清洗后没有候选圈")

    session_median = candidate.groupby("session_key")["lap_duration"].transform("median")
    absolute_deviation = (candidate["lap_duration"] - session_median).abs()
    session_mad = absolute_deviation.groupby(candidate["session_key"]).transform("median")
    robust_ok = absolute_deviation <= (
        float(clean_cfg["robust_mad_multiplier"]) * session_mad.clip(lower=0.05)
    )
    candidate = candidate.loc[robust_ok].copy()
    clean_counts = candidate.groupby("session_key").size()
    eligible_sessions = clean_counts[
        clean_counts >= int(clean_cfg["minimum_clean_laps_per_session"])
    ].index
    clean = candidate[candidate["session_key"].isin(eligible_sessions)].copy()
    if clean.empty:
        raise RuntimeError("v4 没有赛段达到最小清洁圈数量")

    clean["session_reference"] = clean.groupby("session_key")["lap_duration"].transform(
        "median"
    )
    clean["target_log_pct"] = 100.0 * np.log(
        clean["lap_duration"] / clean["session_reference"]
    )
    session_max = clean.groupby("session_key")["lap_number"].transform("max").clip(lower=1)
    clean["lap_fraction"] = clean["lap_number"] / session_max
    clean["lap_fraction_sq"] = clean["lap_fraction"] ** 2
    clean["tyre_age_sq"] = clean["tyre_age"] ** 2
    clean = clean.sort_values(
        ["year", "meeting_key", "session_key", "driver_number", "lap_number"]
    ).reset_index(drop=True)

    exclusion_counts = {
        "candidate_rows_before_robust_filter": int(base_mask.sum()),
        "duration_out_of_bounds": int((~duration_ok).sum()),
        "lap_before_minimum": int((~lap_number_ok).sum()),
        "non_dry_compound": int((~dry_ok).sum()),
        "missing_tyre_age": int((~tyre_ok).sum()),
        "inaccurate_including_pit_or_sc": int((~accurate_ok).sum()),
        "deleted": int((~deleted_ok).sum()),
        "non_green_track_status": int((~green_ok).sum()),
        "multiple_source_commits_in_lap": int((~single_commit_ok).sum()),
        "robust_mad_outlier": int((~robust_ok).sum()),
    }
    return clean, exclusion_counts


OUTPUT_COLUMNS = [
    "year",
    "meeting_key",
    "meeting_name",
    "circuit_name",
    "session_key",
    "session_name",
    "session_date_start",
    "driver_number",
    "driver_id",
    "driver_name",
    "team_name",
    "lap_number",
    "lap_duration",
    "is_pit_out_lap",
    "compound",
    "tyre_age_at_start",
    "stint_lap_start",
    "stint_lap_end",
    "stint_number",
    "is_pit_in_lap",
    "is_disrupted_lap",
    "tyre_age",
    "session_reference",
    "target_log_pct",
    "lap_fraction",
    "lap_fraction_sq",
    "tyre_age_sq",
    "source_commit",
    "sample_count",
]


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame[OUTPUT_COLUMNS].to_csv(
        temporary,
        index=False,
        encoding="utf-8",
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    temporary.replace(path)


def pair_manifest(
    clean: pd.DataFrame,
    raw: pd.DataFrame,
    exclusions: dict[str, int],
    config: dict[str, Any],
    train_year: int,
    validation_year: int,
    train_path: Path,
    validation_path: Path,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    train = clean[clean["year"] == train_year]
    validation = clean[clean["year"] == validation_year]
    selected = clean[clean["year"].isin([train_year, validation_year])]
    raw_selected = raw[raw["year"].isin([train_year, validation_year])]
    return {
        "created_at": now_iso(),
        "config_hash": canonical_hash(config),
        "query_version": QUERY_VERSION,
        "data_source": "canonical_parquet",
        "degraded": False,
        "repository_commit": git_head(),
        "source_provenance": provenance,
        "raw_rows": int(len(raw_selected)),
        "candidate_rows_before_robust_filter": int(
            exclusions["candidate_rows_before_robust_filter"]
        ),
        "clean_rows": int(len(selected)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "train_sessions": int(train["session_key"].nunique()),
        "validation_sessions": int(validation["session_key"].nunique()),
        "train_meetings": int(train["meeting_key"].nunique()),
        "validation_meetings": int(validation["meeting_key"].nunique()),
        "summary_by_year_session": (
            selected.groupby(["year", "session_name"])
            .agg(rows=("lap_duration", "size"), sessions=("session_key", "nunique"))
            .reset_index()
            .to_dict(orient="records")
        ),
        "independent_exclusion_counts": exclusions,
        "files": {
            "train": {
                "path": train_path.relative_to(ROOT).as_posix(),
                "sha256": file_sha256(train_path),
            },
            "validation": {
                "path": validation_path.relative_to(ROOT).as_posix(),
                "sha256": file_sha256(validation_path),
            },
        },
    }


def archive_existing() -> Path | None:
    existing = [
        DATA_DIR / "train_2024.csv.gz",
        DATA_DIR / "validation_2025.csv.gz",
        DATA_DIR / "dynamic_tune_train_2023.csv.gz",
        DATA_DIR / "dynamic_tune_validation_2024.csv.gz",
        PRIMARY_MANIFEST,
        TUNING_MANIFEST,
    ]
    if not any(path.exists() for path in existing):
        return None
    archive = DATA_DIR / "legacy-postgres-pre-v4"
    archive.mkdir(parents=True, exist_ok=True)
    for path in existing:
        if path.exists():
            destination = archive / path.name
            if not destination.exists():
                shutil.copy2(path, destination)
    return archive


def compare_pair(old_path: Path, new_path: Path) -> dict[str, Any] | None:
    if not old_path.exists() or not new_path.exists():
        return None
    old = pd.read_csv(old_path, compression="gzip")
    new = pd.read_csv(new_path, compression="gzip")
    keys = ["year", "meeting_name", "session_name", "driver_number", "lap_number"]
    merged = old.merge(new, on=keys, how="outer", suffixes=("_old", "_v4"), indicator=True)
    shared = merged[merged["_merge"] == "both"].copy()
    lap_delta = (
        shared["lap_duration_v4"] - shared["lap_duration_old"]
        if not shared.empty
        else pd.Series(dtype=float)
    )
    aliases = load_json(ALIASES_CONFIG)["team_aliases"]
    old_team = shared["team_name_old"].replace(aliases)
    new_team = shared["team_name_v4"].replace(aliases)
    session_keys = ["year", "meeting_name", "session_name"]
    old_sessions = {
        tuple(row) for row in old[session_keys].drop_duplicates().itertuples(index=False, name=None)
    }
    new_sessions = {
        tuple(row) for row in new[session_keys].drop_duplicates().itertuples(index=False, name=None)
    }
    return {
        "old_rows": int(len(old)),
        "v4_rows": int(len(new)),
        "shared_rows": int(len(shared)),
        "old_only_rows": int((merged["_merge"] == "left_only").sum()),
        "v4_only_rows": int((merged["_merge"] == "right_only").sum()),
        "lap_duration_mean_abs_delta_seconds": (
            float(lap_delta.abs().mean()) if not lap_delta.empty else None
        ),
        "lap_duration_max_abs_delta_seconds": (
            float(lap_delta.abs().max()) if not lap_delta.empty else None
        ),
        "target_log_pct_correlation": (
            float(shared["target_log_pct_old"].corr(shared["target_log_pct_v4"]))
            if len(shared) > 1
            else None
        ),
        "compound_agreement_on_shared": (
            float((shared["compound_old"] == shared["compound_v4"]).mean())
            if not shared.empty
            else None
        ),
        "team_agreement_on_shared": (
            float((shared["team_name_old"] == shared["team_name_v4"]).mean())
            if not shared.empty
            else None
        ),
        "canonical_team_agreement_on_shared": (
            float((old_team == new_team).mean()) if not shared.empty else None
        ),
        "old_only_sessions": [list(value) for value in sorted(old_sessions - new_sessions)],
        "v4_only_sessions": [list(value) for value in sorted(new_sessions - old_sessions)],
    }


def build_all(parquet_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    years = [2023, 2024, 2025]
    sessions = list(config["session_names"])
    primary_files, primary_provenance = source_catalog(
        parquet_root, [2024, 2025], sessions
    )
    tuning_files, tuning_provenance = source_catalog(
        parquet_root, [2023, 2024], sessions
    )
    files = sorted(set(primary_files + tuning_files))
    raw = extract_laps(files)
    primary_raw = raw[raw["year"].isin([2024, 2025])].copy()
    tuning_raw = raw[raw["year"].isin([2023, 2024])].copy()
    primary_clean, primary_exclusions = clean_laps(primary_raw, config)
    tuning_clean, tuning_exclusions = clean_laps(tuning_raw, config)

    paths = {
        2023: DATA_DIR / "dynamic_tune_train_2023.csv.gz",
        2024: DATA_DIR / "train_2024.csv.gz",
        2025: DATA_DIR / "validation_2025.csv.gz",
    }
    write_csv(tuning_clean[tuning_clean["year"] == 2023], paths[2023])
    write_csv(primary_clean[primary_clean["year"] == 2024], paths[2024])
    write_csv(primary_clean[primary_clean["year"] == 2025], paths[2025])
    # 2024 同时是动态衰减调参验证集；使用同一批冻结字节的独立副本保持旧接口兼容。
    tuning_2024 = DATA_DIR / "dynamic_tune_validation_2024.csv.gz"
    write_csv(tuning_clean[tuning_clean["year"] == 2024], tuning_2024)

    primary_manifest = pair_manifest(
        primary_clean,
        primary_raw,
        primary_exclusions,
        config,
        2024,
        2025,
        paths[2024],
        paths[2025],
        primary_provenance,
    )
    tuning_config = json.loads(json.dumps(config))
    tuning_config["experiment_name"] = "dynamic_decay_tuning_2023_to_2024"
    tuning_config["train_year"] = 2023
    tuning_config["validation_year"] = 2024
    tuning_manifest = pair_manifest(
        tuning_clean,
        tuning_raw,
        tuning_exclusions,
        tuning_config,
        2023,
        2024,
        paths[2023],
        tuning_2024,
        tuning_provenance,
    )
    atomic_json(PRIMARY_MANIFEST, primary_manifest)
    atomic_json(TUNING_MANIFEST, tuning_manifest)
    return {"primary": primary_manifest, "tuning": tuning_manifest}


def write_comparison(archive: Path | None) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "created_at": now_iso(),
        "old_baseline": archive.relative_to(ROOT).as_posix() if archive else None,
        "pairs": {},
    }
    if archive:
        for name, old_name, new_path in (
            ("train_2024", "train_2024.csv.gz", DATA_DIR / "train_2024.csv.gz"),
            ("validation_2025", "validation_2025.csv.gz", DATA_DIR / "validation_2025.csv.gz"),
            (
                "dynamic_tune_train_2023",
                "dynamic_tune_train_2023.csv.gz",
                DATA_DIR / "dynamic_tune_train_2023.csv.gz",
            ),
            (
                "dynamic_tune_validation_2024",
                "dynamic_tune_validation_2024.csv.gz",
                DATA_DIR / "dynamic_tune_validation_2024.csv.gz",
            ),
        ):
            comparison["pairs"][name] = compare_pair(archive / old_name, new_path)
    atomic_json(COMPARISON_REPORT, comparison)
    return comparison


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet-root", type=Path, default=DEFAULT_PARQUET_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--archive-existing", action="store_true")
    parser.add_argument("--compare-only", action="store_true")
    args = parser.parse_args()

    config = load_json(args.config)
    archive = archive_existing() if args.archive_existing else DATA_DIR / "legacy-postgres-pre-v4"
    if not archive.exists():
        archive = None
    if args.compare_only:
        print(json.dumps(write_comparison(archive), ensure_ascii=False, indent=2))
        return 0
    manifests = build_all(args.parquet_root.resolve(), config)
    write_comparison(archive)
    print(json.dumps(manifests, ensure_ascii=False, indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
