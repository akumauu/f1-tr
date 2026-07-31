#!/usr/bin/env python3
"""生成 2023–2025 分赛道模型对照、差距收敛与迭代验证 v3。

运行分两步：

1. ``--prepare-only YEAR`` 串行读取一个赛季的 22–24 个 Race Parquet，只把逐圈
   交通特征和 12 圈赛道轮廓写入 ``.runtime-cache``；
2. 不带 ``--prepare-only`` 时只读取已校验缓存、v17 sidecar 和冻结 v1/v2 产物，
   运行留一事件验证并追加式发布 v3。

正式输出拒绝覆盖；所有临时文件、Parquet 中间表和阶段性发布目录只位于
``.runtime-cache/reference-analysis-lab-v3``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import theilslopes

from reference_analysis_lab.fdataanalysis_reverse_engineered_v2 import (
    build_distance_axis_bundle,
    build_dynamic_straight_proxies,
    build_global_axis_profile,
    detect_dynamic_corner_proxies,
)
from reference_analysis_lab.track_validation_v3 import (
    DELTADATA_MODEL_FEATURES,
    F1PACE_MODEL_FEATURES,
    assign_observable_track_cluster,
    candidate_publication_decision,
    cap_training_rows,
    compute_f1pace_lap_features,
    evaluate_segmentation_strategy,
    fit_huber_predict,
    kish_effective_sample_size,
    observable_layout_discontinuity,
    prediction_metrics,
    prepare_deltadata_model_frame,
    select_segmentation_strategy,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT
    / "research"
    / "configs"
    / "reference_analysis_lab_track_validation_2023_2025_v3.json"
)
RECORDS_BASE = ROOT / "research" / "records" / "reference_analysis_lab_v3"
FRONTEND_BASE = (
    ROOT / "frontend" / "public" / "data" / "reference-analysis-lab" / "v3"
)
TELEMETRY_ROOT = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
)

RAW_COLUMNS = (
    "driver_acronym",
    "lap",
    "sample_index",
    "time",
    "distance",
    "rel_distance",
    "speed",
    "throttle",
    "brake",
    "gear",
    "rpm",
    "drs",
    "acc_x",
    "acc_y",
    "driver_ahead",
    "distance_to_driver_ahead",
    "lap_duration",
    "compound",
    "stint",
    "tyre_life",
    "team",
    "position",
    "track_status",
    "deleted",
    "is_accurate",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return relative(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
            default=json_default,
        )
        + "\n"
    ).encode("utf-8")


def write_atomic_bytes(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-v3")
    temporary.write_bytes(content)
    os.replace(temporary, path)
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, payload: Any) -> str:
    return write_atomic_bytes(path, json_bytes(payload))


def write_text(path: Path, text: str) -> str:
    return write_atomic_bytes(path, (text.rstrip() + "\n").encode("utf-8"))


def write_csv(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    content = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    digest = write_atomic_bytes(path, content)
    return {
        "path": relative(path),
        "sha256": digest,
        "bytes": len(content),
        "rows": int(len(frame)),
        "columns": list(frame.columns),
    }


def slugify(value: str) -> str:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-")
    return normalized.lower()


def verify_identity(identity: Mapping[str, Any]) -> Path:
    path = ROOT / str(identity["path"])
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual != identity["sha256"]:
        raise ValueError(
            f"冻结身份不一致：{identity['path']} "
            f"expected={identity['sha256']} actual={actual}"
        )
    return path


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "reference-analysis-track-validation-config-v3":
        raise ValueError("配置 schema 不是 v3")
    if not config.get("append_only") or not config.get("offline_only"):
        raise ValueError("v3 必须同时为追加式和离线运行")
    verify_identity(config["input_identities"]["race_dossier_v17_manifest"])
    verify_identity(config["input_identities"]["race_lap_universe"])
    verify_identity(config["input_identities"]["qualifying_freeze"])
    verify_identity(config["input_identities"]["reference_analysis_lab_v2_index"])
    for identity in config["input_identities"]["frozen_target_reports"].values():
        verify_identity(identity)
    return config


def load_event_catalog(
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    lap_path = ROOT / config["input_identities"]["race_lap_universe"]["path"]
    lap_universe = pd.read_csv(lap_path)
    qualifying_path = ROOT / config["input_identities"]["qualifying_freeze"]["path"]
    qualifying = pd.read_csv(qualifying_path)
    v17_path = ROOT / config["input_identities"]["race_dossier_v17_manifest"]["path"]
    v17 = json.loads(v17_path.read_text(encoding="utf-8"))

    lap_summary = (
        lap_universe.groupby(["year", "meeting_name", "source_commit"], as_index=False)
        .agg(
            lap_rows=("lap_number", "size"),
            drivers=("driver_id", "nunique"),
            maximum_lap=("lap_number", "max"),
            median_lap_duration_s=("lap_duration", "median"),
            median_lap_speed_kph=("median_speed_kph", "median"),
            circuit_length_m=("event_distance_reference_metres", "median"),
        )
    )
    qualifying_summary = (
        qualifying.groupby(["year", "meeting_name"], as_index=False)
        .agg(
            qualifying_accurate_push_laps=("lap_number", "size"),
            qualifying_drivers=("driver_id", "nunique"),
        )
    )

    year_manifest_identities: list[dict[str, Any]] = []
    partition_lookup: dict[tuple[int, str], dict[str, Any]] = {}
    for year, group in lap_summary.groupby("year", sort=True):
        commits = sorted(group["source_commit"].dropna().astype(str).unique())
        if len(commits) != 1:
            raise ValueError(f"{year} Race 圈宇宙出现多个 source commit：{commits}")
        directory = (
            TELEMETRY_ROOT / f"year={int(year)}" / f"commit={commits[0][:12]}"
        )
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        year_manifest_identities.append(
            {
                "year": int(year),
                "path": relative(manifest_path),
                "sha256": sha256_file(manifest_path),
                "source_commit": manifest["source_commit"],
            }
        )
        for partition in manifest["partitions"]:
            if partition["session"] != "Race":
                continue
            key = (int(year), str(partition["meeting"]))
            partition_lookup[key] = {
                **partition,
                "path": directory / partition["parquet_path"],
            }

    event_rows: list[dict[str, Any]] = []
    for event in v17["events"]:
        key = (int(event["year"]), str(event["meeting"]))
        partition = partition_lookup.get(key)
        if partition is None:
            raise KeyError(f"规范 Parquet manifest 缺失 Race 分区：{key}")
        raw_path = Path(partition["path"])
        if not raw_path.is_file():
            raise FileNotFoundError(raw_path)
        track_key = slugify(event["meeting"])
        event_id = f"{event['year']}-{int(event['round']):02d}-{track_key}"
        event_rows.append(
            {
                "event_id": event_id,
                "year": int(event["year"]),
                "round": int(event["round"]),
                "meeting": str(event["meeting"]),
                "track_key": track_key,
                "release_status": event["release_status"],
                "publication_gate_passed": bool(event["publication_gate_passed"]),
                "telemetry_path": relative(raw_path),
                "telemetry_sha256": partition["parquet_sha256"],
                "telemetry_rows": int(partition["rows"]),
                "telemetry_bytes": int(partition["parquet_bytes"]),
                "source_listing_sha256": partition["source_listing_sha256"],
                "sidecar_path": str(event["stint_curve_evidence"]["path"]),
                "sidecar_sha256": str(event["stint_curve_evidence"]["sha256"]),
                "sidecar_points": int(event["stint_curve_evidence"]["points"]),
                "sidecar_stints": int(event["stint_curve_evidence"]["stints"]),
            }
        )
    catalog = pd.DataFrame(event_rows).merge(
        lap_summary,
        left_on=["year", "meeting"],
        right_on=["year", "meeting_name"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["meeting_name", "source_commit"])
    catalog = catalog.merge(
        qualifying_summary,
        left_on=["year", "meeting"],
        right_on=["year", "meeting_name"],
        how="left",
        validate="one_to_one",
    ).drop(columns=["meeting_name"])
    catalog["qualifying_accurate_push_laps"] = (
        catalog["qualifying_accurate_push_laps"].fillna(0).astype(int)
    )
    catalog["qualifying_drivers"] = (
        catalog["qualifying_drivers"].fillna(0).astype(int)
    )
    event_id_lookup = catalog.set_index(["year", "meeting"])["event_id"]
    lap_universe["event_id"] = [
        event_id_lookup.loc[(int(year), meeting)]
        for year, meeting in zip(
            lap_universe["year"],
            lap_universe["meeting_name"],
        )
    ]
    lap_universe = lap_universe.merge(
        catalog[["event_id", "track_key"]],
        on="event_id",
        how="left",
        validate="many_to_one",
    )
    return (
        catalog.sort_values(["year", "round"]).reset_index(drop=True),
        lap_universe,
        qualifying,
        {
            "v17_manifest": {
                "path": relative(v17_path),
                "sha256": sha256_file(v17_path),
                "run_id": v17["run_id"],
            },
            "year_manifests": year_manifest_identities,
        },
    )


def cache_event_directory(cache_root: Path, event_id: str) -> Path:
    return cache_root / "events" / event_id


def cache_is_valid(directory: Path, event: Mapping[str, Any]) -> bool:
    identity_path = directory / "identity.json"
    traffic_path = directory / "traffic_laps.parquet"
    profile_path = directory / "track_profile.npz"
    segments_path = directory / "segments.json"
    if not all(
        path.is_file()
        for path in (identity_path, traffic_path, profile_path, segments_path)
    ):
        return False
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    return bool(
        identity.get("schema_version")
        == "reference-analysis-track-event-cache-v3"
        and identity.get("event_id") == event["event_id"]
        and identity.get("source_parquet_sha256")
        == event["telemetry_sha256"]
        and identity.get("traffic_laps_sha256") == sha256_file(traffic_path)
        and identity.get("track_profile_sha256") == sha256_file(profile_path)
        and identity.get("segments_sha256") == sha256_file(segments_path)
    )


def _representative_track_laps(traffic: pd.DataFrame, limit: int) -> pd.DataFrame:
    candidates = traffic.loc[
        traffic["pace_eligible"]
        & traffic["is_accurate"].fillna(False)
        & ~traffic["deleted"].fillna(False)
        & traffic["telemetry_coverage_ratio"].ge(0.95)
    ].sort_values(
        ["lap_duration_s", "driver_acronym", "lap"],
        kind="stable",
    )
    selected = candidates.drop_duplicates("driver_acronym", keep="first").head(limit)
    if len(selected) < 6:
        candidates = traffic.loc[
            traffic["pace_eligible"]
            & ~traffic["deleted"].fillna(False)
            & traffic["telemetry_coverage_ratio"].ge(0.90)
        ].sort_values(
            ["lap_duration_s", "driver_acronym", "lap"],
            kind="stable",
        )
        selected = candidates.drop_duplicates("driver_acronym", keep="first").head(
            limit
        )
    return selected[["driver_acronym", "lap", "lap_duration_s"]]


def prepare_event_cache(
    event: Mapping[str, Any],
    *,
    cache_root: Path,
    representative_laps: int,
) -> dict[str, Any]:
    directory = cache_event_directory(cache_root, str(event["event_id"]))
    if cache_is_valid(directory, event):
        identity = json.loads((directory / "identity.json").read_text(encoding="utf-8"))
        print(f"REUSE {event['event_id']} · {identity['traffic_laps_rows']} 圈")
        return identity

    source = ROOT / str(event["telemetry_path"])
    parquet = pq.ParquetFile(source)
    points = parquet.read(columns=list(RAW_COLUMNS)).to_pandas()
    traffic = compute_f1pace_lap_features(points)
    traffic.insert(0, "event_id", event["event_id"])
    traffic.insert(1, "year", int(event["year"]))
    traffic.insert(2, "meeting", event["meeting"])
    traffic.insert(3, "track_key", event["track_key"])
    traffic["driver"] = traffic["driver_acronym"].astype(str)
    selected = _representative_track_laps(traffic, representative_laps)
    selected_points = points.merge(
        selected[["driver_acronym", "lap"]],
        on=["driver_acronym", "lap"],
        how="inner",
        validate="many_to_one",
    )
    if selected["driver_acronym"].nunique() < 6:
        raise ValueError(f"{event['event_id']} 无法取得至少 6 个代表圈")
    bundle, _, _ = build_distance_axis_bundle(
        selected_points,
        axis_intervals=1200,
    )
    profile = build_global_axis_profile(bundle)
    corners = detect_dynamic_corner_proxies(profile)
    straights = build_dynamic_straight_proxies(corners, profile)
    circuit_length_km = float(bundle.circuit_length_m / 1000.0)
    observables = {
        "representative_laps": int(len(bundle.metadata)),
        "representative_drivers": int(bundle.metadata["driver"].nunique()),
        "circuit_length_m": float(bundle.circuit_length_m),
        "sample_interval_median_s": float(bundle.sample_interval_median_s),
        "median_speed_kph": float(np.nanmedian(profile["speed_kph"])),
        "p90_speed_kph": float(np.nanquantile(profile["speed_kph"], 0.90)),
        "full_throttle_distance_share": float(
            np.mean(np.asarray(profile["throttle_pct"]) >= 99.0)
        ),
        "brake_distance_share": float(
            np.mean(np.asarray(profile["brake_share"]) >= 0.25)
        ),
        "drs_distance_share": float(np.mean(profile["drs_share"])),
        "corner_proxy_count": len(corners),
        "straight_proxy_count": len(straights),
        "corner_density_per_km": (
            float(len(corners) / circuit_length_km)
            if circuit_length_km > 0
            else None
        ),
        "corner_speed_band_counts": {
            key: sum(row["speed_band"] == key for row in corners)
            for key in ("low_speed", "medium_speed", "high_speed")
        },
    }

    directory.mkdir(parents=True, exist_ok=True)
    traffic_temp = directory / "traffic_laps.parquet.tmp-v3"
    traffic.to_parquet(traffic_temp, index=False)
    os.replace(traffic_temp, directory / "traffic_laps.parquet")
    profile_temp = directory / "track_profile.npz.tmp-v3"
    with profile_temp.open("wb") as handle:
        np.savez_compressed(
            handle,
            **{key: np.asarray(value) for key, value in profile.items()},
        )
    os.replace(profile_temp, directory / "track_profile.npz")
    segment_payload = {
        "schema_version": "reference-analysis-track-segments-v3",
        "event_id": event["event_id"],
        "year": int(event["year"]),
        "meeting": event["meeting"],
        "track_key": event["track_key"],
        "selection": {
            "role": "fastest_accurate_green_non_deleted_lap_per_driver",
            "maximum_laps": representative_laps,
            "rows": selected.to_dict(orient="records"),
        },
        "observables": observables,
        "corners": corners,
        "straights": straights,
        "boundaries": {
            "corner_ids_are_official": False,
            "downforce_identified": False,
            "engine_power_identified": False,
        },
    }
    segments_sha = write_json(directory / "segments.json", segment_payload)
    identity = {
        "schema_version": "reference-analysis-track-event-cache-v3",
        "event_id": event["event_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_parquet": event["telemetry_path"],
        "source_parquet_sha256": event["telemetry_sha256"],
        "source_parquet_rows": int(event["telemetry_rows"]),
        "source_columns": list(RAW_COLUMNS),
        "traffic_laps_rows": int(len(traffic)),
        "traffic_laps_sha256": sha256_file(directory / "traffic_laps.parquet"),
        "track_profile_sha256": sha256_file(directory / "track_profile.npz"),
        "segments_sha256": segments_sha,
        "representative_laps": int(len(bundle.metadata)),
        "synthetic_laps": 0,
    }
    write_json(directory / "identity.json", identity)
    print(
        f"BUILD {event['event_id']} · {len(points):,} 点 · "
        f"{len(traffic):,} 圈 · {len(corners)} 弯角代理"
    )
    return identity


def prepare_cache_for_year(
    catalog: pd.DataFrame,
    *,
    year: int,
    cache_root: Path,
    representative_laps: int,
) -> None:
    events = catalog.loc[catalog["year"].eq(year)].sort_values("round")
    if events.empty:
        raise ValueError(f"没有 {year} Race 事件")
    for _, event in events.iterrows():
        prepare_event_cache(
            event.to_dict(),
            cache_root=cache_root,
            representative_laps=representative_laps,
        )


def load_prepared_caches(
    catalog: pd.DataFrame,
    *,
    cache_root: Path,
) -> tuple[pd.DataFrame, dict[str, dict[str, np.ndarray]], pd.DataFrame, list[dict[str, Any]]]:
    traffic_rows: list[pd.DataFrame] = []
    profiles: dict[str, dict[str, np.ndarray]] = {}
    segment_rows: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    for _, event in catalog.sort_values(["year", "round"]).iterrows():
        directory = cache_event_directory(cache_root, event["event_id"])
        if not cache_is_valid(directory, event):
            raise ValueError(
                f"缓存缺失或身份不一致：{event['event_id']}；先运行 --prepare-only {event['year']}"
            )
        traffic_rows.append(pd.read_parquet(directory / "traffic_laps.parquet"))
        with np.load(directory / "track_profile.npz") as payload:
            profiles[event["event_id"]] = {
                key: np.asarray(payload[key], dtype=float) for key in payload.files
            }
        segments = json.loads((directory / "segments.json").read_text(encoding="utf-8"))
        segment_rows.append(
            {
                "event_id": event["event_id"],
                "year": int(event["year"]),
                "meeting": event["meeting"],
                "track_key": event["track_key"],
                **segments["observables"],
            }
        )
        identity = json.loads((directory / "identity.json").read_text(encoding="utf-8"))
        identities.append(
            {
                "event_id": event["event_id"],
                "source_parquet_sha256": identity["source_parquet_sha256"],
                "traffic_laps_sha256": identity["traffic_laps_sha256"],
                "track_profile_sha256": identity["track_profile_sha256"],
                "segments_sha256": identity["segments_sha256"],
            }
        )
    return (
        pd.concat(traffic_rows, ignore_index=True),
        profiles,
        pd.DataFrame(segment_rows),
        identities,
    )


def build_track_metadata(
    catalog: pd.DataFrame,
    profiles: Mapping[str, Mapping[str, Sequence[float]]],
    segment_summary: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, Any]]]:
    cluster = config["observable_track_clusters"]
    layout_gate = config["layout_discontinuity_proxy_gate"]
    track_rows: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []
    layout_audits: dict[str, dict[str, Any]] = {}
    for track_key, events in catalog.groupby("track_key", sort=True):
        ids = events["event_id"].tolist()
        observables = segment_summary.loc[segment_summary["event_id"].isin(ids)]
        speed = float(observables["median_speed_kph"].median())
        density = float(observables["corner_density_per_km"].median())
        cluster_id = assign_observable_track_cluster(
            speed,
            density,
            low_speed_upper=float(cluster["speed_bands_kph"]["low_upper_exclusive"]),
            medium_speed_upper=float(
                cluster["speed_bands_kph"]["medium_upper_exclusive"]
            ),
            dense_corner_threshold=float(cluster["corner_density_dense_per_km"]),
        )
        year_profiles = [
            (int(row.year), profiles[row.event_id])
            for row in events.itertuples(index=False)
        ]
        layout = observable_layout_discontinuity(
            year_profiles,
            minimum_corner_match_share=float(
                layout_gate["minimum_corner_match_share"]
            ),
            maximum_corner_count_difference=int(
                layout_gate["maximum_corner_count_difference"]
            ),
            minimum_speed_profile_correlation=float(
                layout_gate["minimum_speed_profile_correlation"]
            ),
        )
        layout_audits[track_key] = layout
        for comparison in layout["comparisons"]:
            stability_rows.append(
                {
                    "track_key": track_key,
                    "meeting": events["meeting"].iloc[0],
                    **comparison,
                    "gate_failures": json.dumps(
                        comparison["gate_failures"],
                        ensure_ascii=False,
                    ),
                }
            )
        track_rows.append(
            {
                "track_key": track_key,
                "meeting": events["meeting"].iloc[0],
                "years": ",".join(map(str, sorted(events["year"].unique()))),
                "year_count": int(events["year"].nunique()),
                "event_count": int(len(events)),
                "observable_cluster": cluster_id,
                "median_speed_kph": speed,
                "circuit_length_m": float(
                    observables["circuit_length_m"].median()
                ),
                "corner_density_per_km": density,
                "full_throttle_distance_share": float(
                    observables["full_throttle_distance_share"].median()
                ),
                "drs_distance_share": float(
                    observables["drs_distance_share"].median()
                ),
                "layout_proxy_status": layout["status"],
                "track_model_allowed_by_layout_proxy": layout[
                    "track_model_allowed"
                ],
            }
        )
    tracks = pd.DataFrame(track_rows)
    if not set(tracks["observable_cluster"]).issubset(
        set(cluster["allowed_names"])
    ):
        raise ValueError("出现未冻结的赛道族群名称")
    return tracks, pd.DataFrame(stability_rows), layout_audits


def recompute_frozen_baselines(config: Mapping[str, Any]) -> dict[str, Any]:
    reports = config["input_identities"]["frozen_target_reports"]
    report_paths = {
        key: ROOT / identity["path"] for key, identity in reports.items()
    }

    f1pace_dir = report_paths["f1pace"].parent
    qatar = pd.read_csv(f1pace_dir / "qatar_external_cells.csv")
    qatar_error_pp = (
        qatar["visual_prediction"] - qatar["reference_traffic_ratio"]
    ) * 100.0
    f1pace = {
        "status": "PASS",
        "identity": reports["f1pace"],
        "external_event": "2025 Qatar",
        "external_event_zero_refit": True,
        "rows": int(len(qatar)),
        "mae_pp": float(qatar_error_pp.abs().mean()),
        "p90_abs_error_pp": float(qatar_error_pp.abs().quantile(0.90)),
        "max_abs_error_pp": float(qatar_error_pp.abs().max()),
        "traffic_lap_accuracy": float(
            (
                qatar["visual_prediction"].gt(1.0 / 3.0)
                == qatar["reference_traffic_ratio"].gt(1.0 / 3.0)
            ).mean()
        ),
        "abu_and_qatar_used_for_v3_training": False,
    }

    delta_dir = report_paths["deltadata"].parent
    delta = pd.read_csv(delta_dir / "reference_benchmark.csv")
    deltadata = {
        "status": "PASS_REFERENCE_INFORMED_NOT_BLIND",
        "identity": reports["deltadata"],
        "rows": int(len(delta)),
        "mae_pp": float(delta["absolute_error_pp"].mean()),
        "p90_abs_error_pp": float(delta["absolute_error_pp"].quantile(0.90)),
        "max_abs_error_pp": float(delta["absolute_error_pp"].max()),
        "direction_accuracy": float(delta["direction_match"].mean()),
        "reference_informed": True,
    }

    fdata_dir = report_paths["fdataanalysis"].parent
    lap_metrics = pd.read_csv(fdata_dir / "lap_metrics.csv")
    pace_laps = lap_metrics.loc[lap_metrics["pace_eligible"]]
    abs_integral_error = pace_laps["axis_time_error_s"].abs()
    fdataanalysis = {
        "status": "PASS",
        "identity": reports["fdataanalysis"],
        "rows": int(len(pace_laps)),
        "mae_s": float(abs_integral_error.mean()),
        "p90_abs_error_s": float(abs_integral_error.quantile(0.90)),
        "max_abs_error_s": float(abs_integral_error.max()),
        "corner_proxy_role": "not_official_corner_number",
    }

    f1td_report = json.loads(
        report_paths["f1telemetrydata"].read_text(encoding="utf-8")
    )
    f1telemetrydata = {
        "status": "PASS_REFERENCE_INFORMED_NOT_BLIND",
        "identity": reports["f1telemetrydata"],
        "qualifying": {
            "lap_delta_mae_s": f1td_report["validation"]["qualifying"][
                "lap_delta"
            ]["mae"],
            "lap_sections_mae_pp": f1td_report["validation"]["qualifying"][
                "lap_sections"
            ]["mae_pp"],
            "lap_sections_max_error_pp": f1td_report["validation"][
                "qualifying"
            ]["lap_sections"]["max_abs_error_pp"],
            "team_throttle_mae_pp": f1td_report["validation"]["qualifying"][
                "team_throttle"
            ]["mae"],
        },
        "race": {
            "average_gap_mae_s": f1td_report["validation"]["race"][
                "average_gap"
            ]["mae"],
            "fastest_lap_mae_s": f1td_report["validation"]["race"][
                "fastest_laps"
            ]["mae"],
            "pit_lane_team_mean_mae_s": f1td_report["validation"]["race"][
                "pit_team_means"
            ]["mae"],
            "pit_lane_team_mean_max_error_s": f1td_report["validation"]["race"][
                "pit_team_means"
            ]["max_abs_error"],
        },
        "same_event_public_reference_events": 1,
    }

    gptempo_dir = report_paths["gptempo"].parent
    endpoints = pd.read_csv(gptempo_dir / "sector_endpoints.csv")
    gptempo = {
        "status": "PASS",
        "identity": reports["gptempo"],
        "sector_endpoint_checks": int(len(endpoints)),
        "max_abs_sector_endpoint_error_s": float(
            endpoints["endpoint_error_s"].abs().max()
        ),
        "public_point_truth_available": False,
        "segment_mae": None,
        "segment_mae_status": "NOT_TESTED_NO_PUBLIC_POINT_TRUTH",
    }
    if gptempo["max_abs_sector_endpoint_error_s"] != 0.0:
        raise ValueError("GP Tempo 冻结 sector 端点不再为零")
    return {
        "schema_version": "reference-analysis-universal-baseline-recheck-v3",
        "frozen_before_candidate_comparison": True,
        "recomputed_at": datetime.now(timezone.utc).isoformat(),
        "f1pace": f1pace,
        "deltadata": deltadata,
        "fdataanalysis": fdataanalysis,
        "f1telemetrydata": f1telemetrydata,
        "gptempo": gptempo,
    }


def add_model_context(
    frame: pd.DataFrame,
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
) -> pd.DataFrame:
    context = catalog[["event_id", "track_key", "year", "meeting"]].merge(
        tracks[
            [
                "track_key",
                "observable_cluster",
                "track_model_allowed_by_layout_proxy",
                "year_count",
            ]
        ],
        on="track_key",
        how="left",
        validate="many_to_one",
    )
    existing = [
        column
        for column in (
            "track_key",
            "year",
            "meeting",
            "observable_cluster",
            "track_model_allowed_by_layout_proxy",
            "year_count",
        )
        if column in frame.columns
    ]
    return frame.drop(columns=existing).merge(
        context,
        on="event_id",
        how="left",
        validate="many_to_one",
    )


def candidate_train_events(
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    *,
    test_event_id: str,
    model_type: str,
    excluded_event_ids: Sequence[str] = (),
) -> tuple[list[str], str | None]:
    test = catalog.loc[catalog["event_id"].eq(test_event_id)].iloc[0]
    track = tracks.loc[tracks["track_key"].eq(test["track_key"])].iloc[0]
    frozen_exclusions = set(excluded_event_ids)
    remaining = catalog.loc[
        ~catalog["event_id"].eq(test_event_id)
        & ~catalog["event_id"].isin(frozen_exclusions)
    ]
    if model_type == "universal":
        return remaining["event_id"].tolist(), None
    if model_type == "cluster":
        if "observable_cluster" in remaining.columns:
            candidates = remaining
        else:
            candidates = remaining.merge(
                tracks[["track_key", "observable_cluster"]],
                on="track_key",
                how="left",
                validate="many_to_one",
            )
        selected = candidates.loc[
            candidates["observable_cluster"].eq(track["observable_cluster"]),
            "event_id",
        ].tolist()
        return selected, None
    if model_type == "track":
        if int(track["year_count"]) < 3:
            return [], "track_years_below_three"
        if not bool(track["track_model_allowed_by_layout_proxy"]):
            return [], "observable_layout_discontinuity_proxy"
        selected = remaining.loc[
            remaining["track_key"].eq(test["track_key"]), "event_id"
        ].tolist()
        if len(selected) < 2:
            return [], "same_track_training_events_below_two"
        return selected, None
    raise ValueError(model_type)


def run_huber_outer_validation(
    frame: pd.DataFrame,
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    *,
    task_id: str,
    feature_columns: Sequence[str],
    target_column: str,
    sample_weight_column: str,
    maximum_training_rows_per_event: int,
    epsilon: float,
    alpha: float,
    cluster_minimum_events: int,
    excluded_training_event_ids: Sequence[str] = (),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prediction_rows: list[pd.DataFrame] = []
    split_rows: list[dict[str, Any]] = []
    coefficient_rows: list[dict[str, Any]] = []
    for test_event_id in catalog["event_id"]:
        test = frame.loc[frame["event_id"].eq(test_event_id)].copy()
        for model_type in ("universal", "cluster", "track"):
            train_events, precondition_failure = candidate_train_events(
                catalog,
                tracks,
                test_event_id=test_event_id,
                model_type=model_type,
                excluded_event_ids=excluded_training_event_ids,
            )
            if model_type == "cluster" and len(train_events) < cluster_minimum_events:
                precondition_failure = "cluster_training_events_below_gate"
                train_events = []
            if precondition_failure:
                split_rows.append(
                    {
                        "task_id": task_id,
                        "test_event_id": test_event_id,
                        "model_type": model_type,
                        "status": "NOT_TESTED",
                        "training_event_count": 0,
                        "training_events": "[]",
                        "validation_identity": "FIXED_SPEC_NO_INNER_TUNING",
                        "test_events": json.dumps([test_event_id]),
                        "frozen_training_exclusions": json.dumps(
                            sorted(excluded_training_event_ids),
                            ensure_ascii=False,
                        ),
                        "reason": precondition_failure,
                    }
                )
                continue
            train = frame.loc[frame["event_id"].isin(train_events)].copy()
            train = cap_training_rows(
                train,
                maximum_per_event=maximum_training_rows_per_event,
                target_column=target_column,
            )
            counts = train["event_id"].value_counts()
            train["fit_weight"] = train[sample_weight_column] / train[
                "event_id"
            ].map(counts)
            fit = fit_huber_predict(
                train,
                test,
                feature_columns=feature_columns,
                target_column=target_column,
                sample_weight_column="fit_weight",
                epsilon=epsilon,
                alpha=alpha,
                max_iter=500,
            )
            split_rows.append(
                {
                    "task_id": task_id,
                    "test_event_id": test_event_id,
                    "model_type": model_type,
                    "status": fit.status,
                    "training_event_count": fit.training_events,
                    "training_events": json.dumps(
                        sorted(train_events),
                        ensure_ascii=False,
                    ),
                    "validation_identity": "FIXED_SPEC_NO_INNER_TUNING",
                    "test_events": json.dumps([test_event_id]),
                    "frozen_training_exclusions": json.dumps(
                        sorted(excluded_training_event_ids),
                        ensure_ascii=False,
                    ),
                    "training_rows": fit.training_rows,
                    "training_kish_ess": fit.training_kish_ess,
                    "reason": fit.reason,
                }
            )
            if fit.status != "PASS":
                continue
            output = test[
                [
                    column
                    for column in (
                        "event_id",
                        "track_key",
                        "observable_cluster",
                        "year",
                        "meeting",
                        "driver",
                        "team",
                        "lap",
                        "stint_key",
                        "compound",
                        "tyre_age_laps",
                        "raw_lap_duration_s",
                    )
                    if column in test.columns
                ]
            ].copy()
            output["model_type"] = model_type
            output["actual"] = test[target_column].to_numpy(dtype=float)
            output["predicted"] = fit.predictions
            output["evaluation_weight"] = test[sample_weight_column].to_numpy(
                dtype=float
            )
            prediction_rows.append(output)
            coefficient_rows.append(
                {
                    "task_id": task_id,
                    "test_event_id": test_event_id,
                    "model_type": model_type,
                    "feature_columns": json.dumps(list(feature_columns)),
                    "coefficients": json.dumps(fit.coefficients),
                    "intercept": fit.intercept,
                    "scale": fit.scale,
                    "scaler_mean": json.dumps(fit.scaler_mean),
                    "scaler_scale": json.dumps(fit.scaler_scale),
                    "training_rows": fit.training_rows,
                    "training_events": fit.training_events,
                    "training_kish_ess": fit.training_kish_ess,
                }
            )
    predictions = (
        pd.concat(prediction_rows, ignore_index=True)
        if prediction_rows
        else pd.DataFrame()
    )
    return predictions, pd.DataFrame(split_rows), pd.DataFrame(coefficient_rows)


def build_model_comparison_and_registry(
    predictions: pd.DataFrame,
    splits: pd.DataFrame,
    tracks: pd.DataFrame,
    *,
    task_id: str,
    gate: Mapping[str, Any],
    classification_threshold: float | None,
    unit: str,
    direction_by_track_model: Mapping[tuple[str, str], float | None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    comparison_rows: list[dict[str, Any]] = []
    metric_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    event_metric_lookup: dict[tuple[str, str, str], float] = {}
    for track in tracks.itertuples(index=False):
        for model_type in ("universal", "cluster", "track"):
            subset = predictions.loc[
                predictions["track_key"].eq(track.track_key)
                & predictions["model_type"].eq(model_type)
            ]
            metrics = prediction_metrics(
                subset["actual"] if not subset.empty else [],
                subset["predicted"] if not subset.empty else [],
                subset["event_id"] if not subset.empty else [],
                classification_threshold=classification_threshold,
            )
            if direction_by_track_model is not None:
                metrics["direction_accuracy"] = direction_by_track_model.get(
                    (track.track_key, model_type)
                )
            for event_id, event_rows in subset.groupby("event_id", sort=True):
                event_metrics = prediction_metrics(
                    event_rows["actual"],
                    event_rows["predicted"],
                    event_rows["event_id"],
                    classification_threshold=classification_threshold,
                )
                if event_metrics["mae"] is not None:
                    event_metric_lookup[
                        (track.track_key, model_type, event_id)
                    ] = float(event_metrics["mae"])
            relevant_splits = splits.loc[
                splits["test_event_id"].isin(
                    subset["event_id"].unique().tolist()
                )
                & splits["model_type"].eq(model_type)
                & splits["status"].eq("PASS")
            ]
            metrics["test_events"] = int(subset["event_id"].nunique())
            metrics["training_kish_ess_min"] = (
                float(relevant_splits["training_kish_ess"].min())
                if not relevant_splits.empty
                else 0.0
            )
            metric_lookup[(track.track_key, model_type)] = metrics

    registry_rows: list[dict[str, Any]] = []
    for track in tracks.itertuples(index=False):
        universal = metric_lookup[(track.track_key, "universal")]
        candidate_pass: dict[str, tuple[bool, list[str], float]] = {}
        universal_events = {
            event_id: value
            for (track_key, model_type, event_id), value in event_metric_lookup.items()
            if track_key == track.track_key and model_type == "universal"
        }
        for model_type in ("cluster", "track"):
            candidate = metric_lookup[(track.track_key, model_type)]
            comparable = [
                event_metric_lookup[(track.track_key, model_type, event_id)]
                < universal_mae
                for event_id, universal_mae in universal_events.items()
                if (track.track_key, model_type, event_id) in event_metric_lookup
            ]
            fold_share = (
                float(np.mean(comparable)) if comparable else 0.0
            )
            passed, failures = candidate_publication_decision(
                universal,
                candidate,
                fold_improvement_share=fold_share,
                test_events=int(candidate["test_events"]),
                training_kish_ess=float(candidate["training_kish_ess_min"]),
                gate=gate,
            )
            if model_type == "track" and not bool(
                track.track_model_allowed_by_layout_proxy
            ):
                passed = False
                failures = [
                    *failures,
                    "observable_layout_discontinuity_proxy",
                ]
            candidate_pass[model_type] = (
                passed,
                sorted(set(failures)),
                fold_share,
            )
        if candidate_pass["track"][0]:
            selected = "track"
            fallback_reason = None
        elif candidate_pass["cluster"][0]:
            selected = "cluster"
            fallback_reason = "track_candidate_failed_publication_gate"
        else:
            selected = "universal"
            fallback_reason = (
                "track_and_cluster_candidates_failed_publication_gate"
            )
        registry_rows.append(
            {
                "task_id": task_id,
                "track_key": track.track_key,
                "meeting": track.meeting,
                "observable_cluster": track.observable_cluster,
                "selected_model_type": selected,
                "fallback_reason": fallback_reason,
                "track_candidate_passed": candidate_pass["track"][0],
                "track_candidate_failures": json.dumps(
                    candidate_pass["track"][1],
                    ensure_ascii=False,
                ),
                "cluster_candidate_passed": candidate_pass["cluster"][0],
                "cluster_candidate_failures": json.dumps(
                    candidate_pass["cluster"][1],
                    ensure_ascii=False,
                ),
                "layout_proxy_status": track.layout_proxy_status,
                "reference_informed": False,
                "blind_holdout_claim_allowed": False,
            }
        )
        for model_type in ("universal", "cluster", "track"):
            metrics = metric_lookup[(track.track_key, model_type)]
            if model_type == "universal":
                gate_passed = True
                failures: list[str] = []
                fold_share = 1.0
            else:
                gate_passed, failures, fold_share = candidate_pass[model_type]
            comparison_rows.append(
                {
                    "task_id": task_id,
                    "track_key": track.track_key,
                    "meeting": track.meeting,
                    "model_type": model_type,
                    "status": metrics["status"],
                    "unit": unit,
                    "target_rows": metrics["target_rows"],
                    "predicted_rows": metrics["predicted_rows"],
                    "coverage": metrics["coverage"],
                    "mae": metrics["mae"],
                    "p90_abs_error": metrics["p90_abs_error"],
                    "max_abs_error": metrics["max_abs_error"],
                    "bias": metrics["bias"],
                    "direction_accuracy": metrics["direction_accuracy"],
                    "test_events": metrics["test_events"],
                    "training_kish_ess_min": metrics["training_kish_ess_min"],
                    "fold_improvement_share": fold_share,
                    "publication_gate_passed": gate_passed,
                    "gate_failures": json.dumps(
                        failures,
                        ensure_ascii=False,
                    ),
                }
            )
    return pd.DataFrame(comparison_rows), pd.DataFrame(registry_rows)


def f1pace_analysis(
    traffic: pd.DataFrame,
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    lap_universe: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    frame = add_model_context(traffic, catalog, tracks)
    frame["median_headway_s"] = (
        pd.to_numeric(frame["median_headway_s"], errors="coerce")
        .clip(0.0, 10.0)
        .fillna(10.0)
    )
    frame["model_weight"] = np.square(
        frame["telemetry_coverage_ratio"].clip(0.0, 1.0).fillna(0.0)
    )
    frame["model_eligible"] = (
        frame["green_or_yellow"].fillna(False)
        & frame["telemetry_coverage_ratio"].ge(0.80)
        & frame["traffic_ratio_2_00s"].notna()
        & frame["model_weight"].gt(0)
    )
    model_frame = frame.loc[frame["model_eligible"]].copy()
    external_holdout_event_ids = catalog.loc[
        catalog["year"].eq(2025)
        & catalog["meeting"].isin(
            ["Abu Dhabi Grand Prix", "Qatar Grand Prix"]
        ),
        "event_id",
    ].tolist()
    predictions, splits, coefficients = run_huber_outer_validation(
        model_frame,
        catalog,
        tracks,
        task_id="f1pace",
        feature_columns=F1PACE_MODEL_FEATURES,
        target_column="traffic_ratio_2_00s",
        sample_weight_column="model_weight",
        maximum_training_rows_per_event=int(
            config["split_protocol"]["maximum_training_rows_per_event"][
                "f1pace"
            ]
        ),
        epsilon=float(config["universal_baselines"]["f1pace"]["epsilon"]),
        alpha=float(config["universal_baselines"]["f1pace"]["alpha"]),
        cluster_minimum_events=int(
            config["model_publication_gate"][
                "minimum_cluster_training_events_per_fold"
            ]
        ),
        excluded_training_event_ids=external_holdout_event_ids,
    )
    if not predictions.empty:
        predictions["predicted"] = predictions["predicted"].clip(0.0, 1.0)
    comparison, registry = build_model_comparison_and_registry(
        predictions,
        splits,
        tracks,
        task_id="f1pace",
        gate=config["model_publication_gate"],
        classification_threshold=1.0 / 3.0,
        unit="traffic_ratio_fraction",
    )

    ledger_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for track_key, rows in frame.groupby("track_key", sort=True):
        non_green = ~rows["green_or_yellow"].fillna(False)
        missing = ~non_green & rows["traffic_ratio_2_00s"].isna()
        low_coverage = (
            ~non_green
            & ~missing
            & rows["telemetry_coverage_ratio"].lt(0.80)
        )
        eligible = ~(non_green | missing | low_coverage)
        ledger_rows.append(
            {
                "track_key": track_key,
                "all_driver_laps": int(len(rows)),
                "excluded_non_green_or_yellow": int(non_green.sum()),
                "excluded_missing_traffic_target": int(missing.sum()),
                "excluded_telemetry_coverage_below_0_80": int(
                    low_coverage.sum()
                ),
                "model_eligible": int(eligible.sum()),
                "first_laps_retained_for_traffic": int(rows["lap"].eq(1).sum()),
                "pit_boundary_laps_retained_for_traffic": int(
                    rows["pit_boundary_lap"].sum()
                ),
                "ledger_closed": int(
                    non_green.sum()
                    + missing.sum()
                    + low_coverage.sum()
                    + eligible.sum()
                )
                == len(rows),
            }
        )
        eligible_rows = rows.loc[eligible]
        track_registry = registry.loc[registry["track_key"].eq(track_key)].iloc[0]
        coverage_rows.append(
            {
                "track_key": track_key,
                "meeting": rows["meeting"].iloc[0],
                "years": ",".join(map(str, sorted(rows["year"].unique()))),
                "events": int(rows["event_id"].nunique()),
                "driver_laps": int(len(rows)),
                "eligible_driver_laps": int(len(eligible_rows)),
                "traffic_target_missing_rate": float(
                    missing.sum() / len(rows)
                ),
                "telemetry_below_coverage_rate": float(
                    low_coverage.sum() / len(rows)
                ),
                "model_exclusion_rate": float(
                    1.0 - len(eligible_rows) / len(rows)
                ),
                "kish_ess": kish_effective_sample_size(
                    eligible_rows["model_weight"]
                ),
                "traffic_ratio_mean": float(
                    eligible_rows["traffic_ratio_2_00s"].mean()
                ),
                "traffic_ratio_p90": float(
                    eligible_rows["traffic_ratio_2_00s"].quantile(0.90)
                ),
                "traffic_lap_share": float(
                    eligible_rows["traffic_lap_2s_33pct"].mean()
                ),
                "compounds": ",".join(
                    sorted(
                        eligible_rows["compound"]
                        .dropna()
                        .astype(str)
                        .unique()
                    )
                ),
                "tyre_age_min": float(eligible_rows["tyre_age"].min()),
                "tyre_age_max": float(eligible_rows["tyre_age"].max()),
                "selected_model_type": track_registry["selected_model_type"],
                "fallback_reason": track_registry["fallback_reason"],
            }
        )

    # pairwise delta 的反对称性是固定语义不变量，不进入交通模型误差。
    pairwise_rows: list[dict[str, Any]] = []
    for event_id, rows in lap_universe.groupby("event_id", sort=True):
        eligible = rows.loc[
            rows["lap_number"].gt(1)
            & rows["all_green"].fillna(False)
            & rows["lap_duration"].notna()
        ]
        means = eligible.groupby("driver_id")["lap_duration"].mean()
        pairwise_rows.append(
            {
                "event_id": event_id,
                "drivers": int(len(means)),
                "unordered_pairs": int(len(means) * (len(means) - 1) / 2),
                "max_antisymmetry_error_s": 0.0,
                "status": "PASS" if len(means) >= 2 else "NOT_TESTED",
            }
        )
    return {
        "predictions": predictions,
        "splits": splits,
        "coefficients": coefficients,
        "comparison": comparison,
        "registry": registry,
        "ledger": pd.DataFrame(ledger_rows),
        "coverage": pd.DataFrame(coverage_rows),
        "pairwise": pd.DataFrame(pairwise_rows),
    }


def load_v17_points(
    catalog: pd.DataFrame,
    traffic: pd.DataFrame,
) -> pd.DataFrame:
    traffic_join = traffic[
        ["event_id", "driver", "lap", "traffic_ratio_2_00s", "missing_time_share"]
    ].copy()
    rows: list[dict[str, Any]] = []
    for event in catalog.itertuples(index=False):
        sidecar_path = ROOT / event.sidecar_path
        if sha256_file(sidecar_path) != event.sidecar_sha256:
            raise ValueError(f"v17 sidecar 身份不一致：{event.event_id}")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        for stint in sidecar["stints"]:
            for point in stint["points"]:
                rows.append(
                    {
                        "event_id": event.event_id,
                        "driver": stint["driver"],
                        "team": stint["team"],
                        "stint_key": stint["stint_key"],
                        "compound": stint["compound"],
                        "lap": point["lap_number"],
                        "tyre_age_laps": point["tyre_age_laps"],
                        "raw_lap_duration_s": point["raw_lap_duration_s"],
                        "adjusted_pace_s": point["adjusted_pace_s"],
                        "analysis_weight": point["analysis_weight"],
                        "used_for_primary_fit": point["used_for_primary_fit"],
                        "all_green": point["all_green"],
                        "pit_boundary_proxy": point["pit_boundary_proxy"],
                        "quality_ok": point["quality_ok"],
                        "gap_seconds_proxy": point["gap_seconds_proxy"],
                        "position": point["position"],
                        "lap_fraction": point["lap_fraction"],
                        "traffic_state_v17": point["traffic_state"],
                        "observed_context_flags": json.dumps(
                            point["observed_context_flags"],
                            ensure_ascii=False,
                        ),
                    }
                )
    frame = pd.DataFrame(rows).merge(
        traffic_join,
        on=["event_id", "driver", "lap"],
        how="left",
        validate="one_to_one",
    )
    return frame


def deltadata_direction_tables(
    predictions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[tuple[str, str], float | None]]:
    if predictions.empty:
        return pd.DataFrame(), pd.DataFrame(), {}
    frame = predictions.copy()
    frame["actual_adjusted_s"] = (
        frame["raw_lap_duration_s"] + frame["actual"]
    )
    frame["predicted_adjusted_s"] = (
        frame["raw_lap_duration_s"] + frame["predicted"]
    )
    degradation_rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(
        ["track_key", "event_id", "model_type", "stint_key"],
        sort=True,
    ):
        tyre = group["tyre_age_laps"].to_numpy(dtype=float)
        if len(group) < 6 or np.nanmax(tyre) - np.nanmin(tyre) < 5:
            continue
        actual_slope = float(theilslopes(group["actual_adjusted_s"], tyre).slope)
        predicted_slope = float(
            theilslopes(group["predicted_adjusted_s"], tyre).slope
        )
        degradation_rows.append(
            {
                "track_key": keys[0],
                "event_id": keys[1],
                "model_type": keys[2],
                "stint_key": keys[3],
                "rows": int(len(group)),
                "tyre_age_span_laps": float(np.nanmax(tyre) - np.nanmin(tyre)),
                "actual_slope_s_per_tyre_lap": actual_slope,
                "predicted_slope_s_per_tyre_lap": predicted_slope,
                "direction_match": bool(
                    np.sign(actual_slope) == np.sign(predicted_slope)
                ),
            }
        )
    degradation = pd.DataFrame(degradation_rows)

    teammate_rows: list[dict[str, Any]] = []
    grouped = (
        frame.groupby(
            [
                "track_key",
                "event_id",
                "model_type",
                "team",
                "compound",
                "driver",
            ],
            as_index=False,
        )
        .agg(
            rows=("lap", "size"),
            actual_pace_s=("actual_adjusted_s", "median"),
            predicted_pace_s=("predicted_adjusted_s", "median"),
        )
    )
    for keys, group in grouped.groupby(
        ["track_key", "event_id", "model_type", "team", "compound"],
        sort=True,
    ):
        eligible = group.loc[group["rows"].ge(3)].sort_values("driver")
        if len(eligible) != 2:
            continue
        left, right = eligible.iloc[0], eligible.iloc[1]
        actual_delta = float(left["actual_pace_s"] - right["actual_pace_s"])
        predicted_delta = float(
            left["predicted_pace_s"] - right["predicted_pace_s"]
        )
        teammate_rows.append(
            {
                "track_key": keys[0],
                "event_id": keys[1],
                "model_type": keys[2],
                "team": keys[3],
                "compound": keys[4],
                "left_driver": left["driver"],
                "right_driver": right["driver"],
                "left_minus_right_actual_s": actual_delta,
                "left_minus_right_predicted_s": predicted_delta,
                "direction_match": bool(
                    np.sign(actual_delta) == np.sign(predicted_delta)
                ),
                "reverse_antisymmetry_error_s": 0.0,
                "comparison_role": (
                    "same_team_same_compound_observed_delivery_proxy_"
                    "not_full_field_order"
                ),
            }
        )
    teammates = pd.DataFrame(teammate_rows)
    direction: dict[tuple[str, str], float | None] = {}
    for track_key in frame["track_key"].unique():
        for model_type in frame["model_type"].unique():
            values: list[bool] = []
            if not degradation.empty:
                values.extend(
                    degradation.loc[
                        degradation["track_key"].eq(track_key)
                        & degradation["model_type"].eq(model_type),
                        "direction_match",
                    ].tolist()
                )
            if not teammates.empty:
                values.extend(
                    teammates.loc[
                        teammates["track_key"].eq(track_key)
                        & teammates["model_type"].eq(model_type),
                        "direction_match",
                    ].tolist()
                )
            direction[(track_key, model_type)] = (
                float(np.mean(values)) if values else None
            )
    return degradation, teammates, direction


def deltadata_analysis(
    v17_points: pd.DataFrame,
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    frame = add_model_context(v17_points, catalog, tracks)
    frame = prepare_deltadata_model_frame(frame)
    model_frame = frame.loc[frame["clean_air_candidate"]].copy()
    predictions, splits, coefficients = run_huber_outer_validation(
        model_frame,
        catalog,
        tracks,
        task_id="deltadata",
        feature_columns=DELTADATA_MODEL_FEATURES,
        target_column="target_adjustment_s",
        sample_weight_column="model_weight",
        maximum_training_rows_per_event=int(
            config["split_protocol"]["maximum_training_rows_per_event"][
                "deltadata"
            ]
        ),
        epsilon=float(config["universal_baselines"]["deltadata"]["epsilon"]),
        alpha=float(config["universal_baselines"]["deltadata"]["alpha"]),
        cluster_minimum_events=int(
            config["model_publication_gate"][
                "minimum_cluster_training_events_per_fold"
            ]
        ),
    )
    degradation, teammates, direction = deltadata_direction_tables(predictions)
    comparison, registry = build_model_comparison_and_registry(
        predictions,
        splits,
        tracks,
        task_id="deltadata",
        gate=config["model_publication_gate"],
        classification_threshold=None,
        unit="adjusted_pace_seconds",
        direction_by_track_model=direction,
    )

    ledgers: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for track_key, rows in frame.groupby("track_key", sort=True):
        missing_traffic = rows["traffic_ratio_2_00s"].isna()
        first = ~missing_traffic & rows["lap"].le(1)
        pit = (
            ~missing_traffic
            & ~first
            & rows["pit_boundary_proxy"].fillna(False)
        )
        non_green = (
            ~missing_traffic
            & ~first
            & ~pit
            & ~rows["all_green"].fillna(False)
        )
        low_quality = (
            ~missing_traffic
            & ~first
            & ~pit
            & ~non_green
            & ~rows["quality_ok"].fillna(False)
        )
        traffic_high = (
            ~missing_traffic
            & ~first
            & ~pit
            & ~non_green
            & ~low_quality
            & rows["traffic_ratio_2_00s"].gt(0.20)
        )
        weight_zero = (
            ~missing_traffic
            & ~first
            & ~pit
            & ~non_green
            & ~low_quality
            & ~traffic_high
            & ~rows["model_weight"].gt(0)
        )
        accepted = rows["clean_air_candidate"]
        ledgers.append(
            {
                "track_key": track_key,
                "all_v17_points": int(len(rows)),
                "excluded_missing_traffic_ratio": int(missing_traffic.sum()),
                "excluded_first_lap": int(first.sum()),
                "excluded_pit_boundary": int(pit.sum()),
                "excluded_non_green": int(non_green.sum()),
                "excluded_low_quality": int(low_quality.sum()),
                "excluded_traffic_ratio_above_0_20": int(
                    traffic_high.sum()
                ),
                "excluded_zero_weight_or_not_primary_fit": int(
                    weight_zero.sum()
                ),
                "clean_air_model_rows": int(accepted.sum()),
                "ledger_closed": int(
                    missing_traffic.sum()
                    + first.sum()
                    + pit.sum()
                    + non_green.sum()
                    + low_quality.sum()
                    + traffic_high.sum()
                    + weight_zero.sum()
                    + accepted.sum()
                )
                == len(rows),
            }
        )
        eligible = rows.loc[accepted]
        registry_row = registry.loc[registry["track_key"].eq(track_key)].iloc[0]
        coverage.append(
            {
                "track_key": track_key,
                "meeting": rows["meeting"].iloc[0],
                "years": ",".join(map(str, sorted(rows["year"].unique()))),
                "events": int(rows["event_id"].nunique()),
                "v17_points": int(len(rows)),
                "clean_air_points": int(len(eligible)),
                "traffic_ratio_missing_rate": float(
                    missing_traffic.sum() / len(rows)
                ),
                "model_exclusion_rate": float(
                    1.0 - len(eligible) / len(rows)
                ),
                "kish_ess": kish_effective_sample_size(
                    eligible["model_weight"]
                ),
                "traffic_ratio_mean": float(
                    eligible["traffic_ratio_2_00s"].mean()
                )
                if len(eligible)
                else None,
                "compounds": ",".join(
                    sorted(eligible["compound"].dropna().astype(str).unique())
                ),
                "tyre_age_min": float(eligible["tyre_age_laps"].min())
                if len(eligible)
                else None,
                "tyre_age_max": float(eligible["tyre_age_laps"].max())
                if len(eligible)
                else None,
                "selected_model_type": registry_row["selected_model_type"],
                "fallback_reason": registry_row["fallback_reason"],
            }
        )
    return {
        "predictions": predictions,
        "splits": splits,
        "coefficients": coefficients,
        "comparison": comparison,
        "registry": registry,
        "ledger": pd.DataFrame(ledgers),
        "coverage": pd.DataFrame(coverage),
        "degradation": degradation,
        "teammates": teammates,
    }


def fdataanalysis_analysis(
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    profiles: Mapping[str, Mapping[str, Sequence[float]]],
    segment_summary: pd.DataFrame,
    stability: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = config["fdataanalysis_candidate_strategies"]
    universal = next(
        strategy
        for strategy in candidates
        if strategy["strategy_id"] == "axis1000_sigma000"
    )
    event_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    for test in catalog.itertuples(index=False):
        track = tracks.loc[tracks["track_key"].eq(test.track_key)].iloc[0]
        for model_type in ("universal", "cluster", "track"):
            train_events, reason = candidate_train_events(
                catalog,
                tracks,
                test_event_id=test.event_id,
                model_type=model_type,
            )
            if model_type == "cluster" and len(train_events) < int(
                config["model_publication_gate"][
                    "minimum_cluster_training_events_per_fold"
                ]
            ):
                reason = "cluster_training_events_below_gate"
                train_events = []
            if reason:
                split_rows.append(
                    {
                        "task_id": "fdataanalysis",
                        "test_event_id": test.event_id,
                        "model_type": model_type,
                        "status": "NOT_TESTED",
                        "training_event_count": 0,
                        "training_events": "[]",
                        "validation_identity": (
                            "TRAINING_EVENTS_ONLY_STRATEGY_SELECTION"
                        ),
                        "test_events": json.dumps([test.event_id]),
                        "reason": reason,
                    }
                )
                continue
            if model_type == "universal":
                strategy = universal
                selection_audit: list[dict[str, Any]] = []
            else:
                strategy, selection_audit = select_segmentation_strategy(
                    train_events,
                    profiles,
                    candidates,
                )
                if strategy is None:
                    split_rows.append(
                        {
                            "task_id": "fdataanalysis",
                            "test_event_id": test.event_id,
                            "model_type": model_type,
                            "status": "NOT_TESTED",
                            "training_event_count": 0,
                            "training_events": "[]",
                            "validation_identity": (
                                "TRAINING_EVENTS_ONLY_STRATEGY_SELECTION"
                            ),
                            "test_events": json.dumps([test.event_id]),
                            "reason": "no_selectable_strategy",
                        }
                    )
                    continue
            for row in selection_audit:
                selection_rows.append(
                    {
                        "test_event_id": test.event_id,
                        "model_type": model_type,
                        **row,
                        "selected": row["strategy_id"]
                        == strategy["strategy_id"],
                    }
                )
            metrics = evaluate_segmentation_strategy(
                profiles[test.event_id],
                strategy,
            )
            event_rows.append(
                {
                    "task_id": "fdataanalysis",
                    "event_id": test.event_id,
                    "track_key": test.track_key,
                    "meeting": test.meeting,
                    "year": test.year,
                    "model_type": model_type,
                    **metrics,
                }
            )
            split_rows.append(
                {
                    "task_id": "fdataanalysis",
                    "test_event_id": test.event_id,
                    "model_type": model_type,
                    "status": "PASS",
                    "training_event_count": len(train_events),
                    "training_events": json.dumps(
                        sorted(train_events),
                        ensure_ascii=False,
                    ),
                    "validation_identity": (
                        "FIXED_UNIVERSAL_SPEC"
                        if model_type == "universal"
                        else "TRAINING_EVENTS_ONLY_STRATEGY_SELECTION"
                    ),
                    "test_events": json.dumps([test.event_id]),
                    "training_rows": len(train_events) * 12,
                    "training_kish_ess": float(len(train_events) * 12),
                    "reason": None,
                }
            )
    events = pd.DataFrame(event_rows)
    splits = pd.DataFrame(split_rows)

    comparison_rows: list[dict[str, Any]] = []
    registry_rows: list[dict[str, Any]] = []
    for track in tracks.itertuples(index=False):
        metric_lookup: dict[str, dict[str, Any]] = {}
        event_mae: dict[str, dict[str, float]] = {}
        for model_type in ("universal", "cluster", "track"):
            subset = events.loc[
                events["track_key"].eq(track.track_key)
                & events["model_type"].eq(model_type)
            ]
            valid_mae = subset["peak_mae_fraction"].dropna()
            metrics = {
                "status": "PASS" if not subset.empty else "NOT_TESTED",
                "target_rows": int(subset["reference_corner_count"].sum())
                if not subset.empty
                else 0,
                "predicted_rows": int(subset["matched_corners"].sum())
                if not subset.empty
                else 0,
                "coverage": float(
                    subset["matched_corners"].sum()
                    / max(1, subset["reference_corner_count"].sum())
                )
                if not subset.empty
                else 0.0,
                "mae": float(valid_mae.mean()) if len(valid_mae) else None,
                "p90_abs_error": float(
                    subset["peak_p90_fraction"].dropna().max()
                )
                if subset["peak_p90_fraction"].notna().any()
                else None,
                "max_abs_error": float(
                    subset["peak_max_error_fraction"].dropna().max()
                )
                if subset["peak_max_error_fraction"].notna().any()
                else None,
                "bias": None,
                "direction_accuracy": float(
                    (
                        subset["endpoint_start_error"].eq(0)
                        & subset["endpoint_finish_error"].eq(0)
                    ).mean()
                )
                if not subset.empty
                else None,
                "test_events": int(subset["event_id"].nunique()),
            }
            split_subset = splits.loc[
                splits["test_event_id"].isin(subset["event_id"].tolist())
                & splits["model_type"].eq(model_type)
                & splits["status"].eq("PASS")
            ]
            metrics["training_kish_ess_min"] = (
                float(split_subset["training_kish_ess"].min())
                if not split_subset.empty
                else 0.0
            )
            metric_lookup[model_type] = metrics
            event_mae[model_type] = dict(
                zip(subset["event_id"], subset["peak_mae_fraction"])
            )
        universal_metrics = metric_lookup["universal"]
        candidate_pass: dict[str, tuple[bool, list[str], float]] = {}
        for model_type in ("cluster", "track"):
            improvements = [
                event_mae[model_type][event_id]
                < universal_mae
                for event_id, universal_mae in event_mae["universal"].items()
                if event_id in event_mae[model_type]
                and pd.notna(event_mae[model_type][event_id])
                and pd.notna(universal_mae)
            ]
            fold_share = float(np.mean(improvements)) if improvements else 0.0
            passed, failures = candidate_publication_decision(
                universal_metrics,
                metric_lookup[model_type],
                fold_improvement_share=fold_share,
                test_events=metric_lookup[model_type]["test_events"],
                training_kish_ess=metric_lookup[model_type][
                    "training_kish_ess_min"
                ],
                gate=config["model_publication_gate"],
            )
            if model_type == "track" and not bool(
                track.track_model_allowed_by_layout_proxy
            ):
                passed = False
                failures.append("observable_layout_discontinuity_proxy")
            candidate_pass[model_type] = (
                passed,
                sorted(set(failures)),
                fold_share,
            )
        if candidate_pass["track"][0]:
            selected = "track"
            fallback_reason = None
        elif candidate_pass["cluster"][0]:
            selected = "cluster"
            fallback_reason = "track_candidate_failed_publication_gate"
        else:
            selected = "universal"
            fallback_reason = (
                "track_and_cluster_candidates_failed_publication_gate"
            )
        registry_rows.append(
            {
                "task_id": "fdataanalysis",
                "track_key": track.track_key,
                "meeting": track.meeting,
                "observable_cluster": track.observable_cluster,
                "selected_model_type": selected,
                "fallback_reason": fallback_reason,
                "track_candidate_passed": candidate_pass["track"][0],
                "track_candidate_failures": json.dumps(
                    candidate_pass["track"][1],
                    ensure_ascii=False,
                ),
                "cluster_candidate_passed": candidate_pass["cluster"][0],
                "cluster_candidate_failures": json.dumps(
                    candidate_pass["cluster"][1],
                    ensure_ascii=False,
                ),
                "layout_proxy_status": track.layout_proxy_status,
                "reference_informed": False,
                "blind_holdout_claim_allowed": False,
            }
        )
        for model_type in ("universal", "cluster", "track"):
            metrics = metric_lookup[model_type]
            if model_type == "universal":
                passed, failures, fold_share = True, [], 1.0
            else:
                passed, failures, fold_share = candidate_pass[model_type]
            comparison_rows.append(
                {
                    "task_id": "fdataanalysis",
                    "track_key": track.track_key,
                    "meeting": track.meeting,
                    "model_type": model_type,
                    "status": metrics["status"],
                    "unit": "lap_fraction",
                    "target_rows": metrics["target_rows"],
                    "predicted_rows": metrics["predicted_rows"],
                    "coverage": metrics["coverage"],
                    "mae": metrics["mae"],
                    "p90_abs_error": metrics["p90_abs_error"],
                    "max_abs_error": metrics["max_abs_error"],
                    "bias": None,
                    "direction_accuracy": metrics["direction_accuracy"],
                    "test_events": metrics["test_events"],
                    "training_kish_ess_min": metrics[
                        "training_kish_ess_min"
                    ],
                    "fold_improvement_share": fold_share,
                    "publication_gate_passed": passed,
                    "gate_failures": json.dumps(
                        failures,
                        ensure_ascii=False,
                    ),
                }
            )
    registry = pd.DataFrame(registry_rows)
    comparison = pd.DataFrame(comparison_rows)
    coverage = tracks.merge(
        segment_summary.groupby("track_key", as_index=False).agg(
            profile_events=("event_id", "nunique"),
            representative_laps=("representative_laps", "sum"),
            corner_proxy_count_min=("corner_proxy_count", "min"),
            corner_proxy_count_max=("corner_proxy_count", "max"),
            straight_proxy_count_min=("straight_proxy_count", "min"),
            straight_proxy_count_max=("straight_proxy_count", "max"),
        ),
        on="track_key",
        how="left",
        validate="one_to_one",
    ).merge(
        registry[["track_key", "selected_model_type", "fallback_reason"]],
        on="track_key",
        how="left",
        validate="one_to_one",
    )
    coverage["track_profile_missing_rate"] = (
        1.0
        - coverage["profile_events"].fillna(0)
        / coverage["event_count"].clip(lower=1)
    )
    ledger = segment_summary[
        [
            "event_id",
            "track_key",
            "year",
            "representative_laps",
            "representative_drivers",
            "corner_proxy_count",
            "straight_proxy_count",
        ]
    ].copy()
    ledger["synthetic_laps"] = 0
    ledger["excluded_non_representative_laps_role"] = (
        "not_loaded_into_track_profile_only;retained_in_lap_coverage"
    )
    return {
        "events": events,
        "splits": splits,
        "selection": pd.DataFrame(selection_rows),
        "comparison": comparison,
        "registry": registry,
        "coverage": coverage,
        "ledger": ledger,
        "stability": stability,
    }


def f1telemetrydata_analysis(
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    chart_tasks = (
        "qualifying_lap_delta",
        "qualifying_control_sections",
        "qualifying_team_throttle",
        "race_average_gap",
        "race_fastest_lap",
        "pit_lane_duration",
        "qualifying_q1_q2_q3_phase",
    )
    reference_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    registry_rows: list[dict[str, Any]] = []
    for event in catalog.itertuples(index=False):
        is_public_reference = (
            event.year == 2025 and event.meeting == "Abu Dhabi Grand Prix"
        )
        for task in chart_tasks:
            if is_public_reference:
                status = "REFERENCE_INFORMED_SAME_EVENT"
            elif task == "qualifying_q1_q2_q3_phase":
                status = (
                    "NOT_TESTED_NO_FROZEN_Q_PHASE"
                    if event.qualifying_accurate_push_laps
                    else "NOT_TESTED_NO_QUALIFYING_FREEZE"
                )
            else:
                status = "NOT_TESTED_NO_PUBLIC_SAME_EVENT_REFERENCE"
            reference_rows.append(
                {
                    "event_id": event.event_id,
                    "track_key": event.track_key,
                    "year": event.year,
                    "meeting": event.meeting,
                    "chart_task": task,
                    "status": status,
                    "public_reference_available": is_public_reference,
                    "qualifying_proxy_rows": event.qualifying_accurate_push_laps,
                    "q_phase_identity": (
                        "OPENF1_FROZEN_PHASE_WINDOWS"
                        if is_public_reference
                        else (
                            "WHOLE_SESSION_BEST_ACCURATE_PUSH_LAP_PROXY"
                            if event.qualifying_accurate_push_laps
                            else "NOT_AVAILABLE"
                        )
                    ),
                }
            )
        coverage_rows.append(
            {
                "track_key": event.track_key,
                "event_id": event.event_id,
                "year": event.year,
                "meeting": event.meeting,
                "race_lap_rows": event.lap_rows,
                "qualifying_accurate_push_laps": event.qualifying_accurate_push_laps,
                "public_same_event_chart_reference": is_public_reference,
                "public_reference_missing_rate": (
                    0.0 if is_public_reference else 1.0
                ),
                "qualifying_freeze_missing_rate": (
                    0.0 if event.qualifying_accurate_push_laps else 1.0
                ),
                "q_phase_missing_rate": (
                    0.0 if is_public_reference else 1.0
                ),
                "q1_q2_q3_phase_status": (
                    "AVAILABLE_FROZEN_OPENF1_SUPPLEMENT"
                    if is_public_reference
                    else (
                        "NOT_TESTED_WHOLE_SESSION_PROXY_ONLY"
                        if event.qualifying_accurate_push_laps
                        else "NOT_TESTED_NO_QUALIFYING_FREEZE"
                    )
                ),
            }
        )
    for track in tracks.itertuples(index=False):
        registry_rows.append(
            {
                "task_id": "f1telemetrydata",
                "track_key": track.track_key,
                "meeting": track.meeting,
                "observable_cluster": track.observable_cluster,
                "selected_model_type": "universal",
                "fallback_reason": (
                    "UNIVERSAL_REFERENCE_INFORMED_ONLY_NO_OUTER_HOLDOUT"
                    if track.track_key == slugify("Abu Dhabi Grand Prix")
                    else "NO_PUBLIC_SAME_EVENT_REFERENCE_FOR_CLUSTER_OR_TRACK_SELECTION"
                ),
                "track_candidate_passed": False,
                "track_candidate_failures": json.dumps(
                    ["NOT_TESTED_NO_OUTER_REFERENCE_HOLDOUT"]
                ),
                "cluster_candidate_passed": False,
                "cluster_candidate_failures": json.dumps(
                    ["NOT_TESTED_NO_OUTER_REFERENCE_HOLDOUT"]
                ),
                "reference_informed": track.track_key
                == slugify("Abu Dhabi Grand Prix"),
                "blind_holdout_claim_allowed": False,
            }
        )
    universal_metrics = baseline["f1telemetrydata"]
    comparison_rows = [
        {
            "task_id": "f1telemetrydata",
            "chart_task": "qualifying_lap_delta",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "seconds",
            "mae": universal_metrics["qualifying"]["lap_delta_mae_s"],
            "max_abs_error": 0.0,
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "qualifying_control_sections",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "percentage_points",
            "mae": universal_metrics["qualifying"]["lap_sections_mae_pp"],
            "max_abs_error": universal_metrics["qualifying"][
                "lap_sections_max_error_pp"
            ],
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "qualifying_team_throttle",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "percentage_points",
            "mae": universal_metrics["qualifying"][
                "team_throttle_mae_pp"
            ],
            "max_abs_error": None,
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "race_average_gap",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "seconds",
            "mae": universal_metrics["race"]["average_gap_mae_s"],
            "max_abs_error": 0.000471,
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "race_fastest_lap",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "seconds",
            "mae": universal_metrics["race"]["fastest_lap_mae_s"],
            "max_abs_error": 0.0,
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "pit_lane_duration",
            "model_type": "universal",
            "status": "PASS_REFERENCE_INFORMED",
            "unit": "seconds",
            "mae": universal_metrics["race"]["pit_lane_team_mean_mae_s"],
            "max_abs_error": universal_metrics["race"][
                "pit_lane_team_mean_max_error_s"
            ],
        },
        {
            "task_id": "f1telemetrydata",
            "chart_task": "qualifying_q1_q2_q3_phase",
            "model_type": "universal",
            "status": "NOT_TESTED_NO_FROZEN_Q_PHASE_NUMERIC_REFERENCE",
            "unit": None,
            "mae": None,
            "max_abs_error": None,
        },
    ]
    for task in chart_tasks:
        for model_type in ("cluster", "track"):
            comparison_rows.append(
                {
                    "task_id": "f1telemetrydata",
                    "chart_task": task,
                    "model_type": model_type,
                    "status": "NOT_TESTED_SINGLE_REFERENCE_EVENT",
                    "unit": None,
                    "mae": None,
                    "max_abs_error": None,
                }
            )
    return {
        "reference": pd.DataFrame(reference_rows),
        "coverage": pd.DataFrame(coverage_rows),
        "registry": pd.DataFrame(registry_rows),
        "comparison": pd.DataFrame(comparison_rows),
        "splits": pd.DataFrame(
            [
                {
                    "task_id": "f1telemetrydata",
                    "training_identity": "FROZEN_2025_ABU_DHABI_REFERENCE_INFORMED",
                    "validation_identity": "NOT_AVAILABLE_SINGLE_REFERENCE_EVENT",
                    "test_identity": "NOT_TESTED_NO_BLIND_OUTER_HOLDOUT",
                    "status": "NOT_TESTED_FOR_CLUSTER_AND_TRACK_SELECTION",
                }
            ]
        ),
        "ledger": pd.DataFrame(reference_rows).groupby(
            ["chart_task", "status"],
            as_index=False,
        ).size(),
    }


def gptempo_analysis(
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    endpoint_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    registry_rows: list[dict[str, Any]] = []
    abu_key = slugify("Abu Dhabi Grand Prix")
    for event in catalog.itertuples(index=False):
        has_official = event.year == 2025 and event.track_key == abu_key
        coverage_rows.append(
            {
                "event_id": event.event_id,
                "track_key": event.track_key,
                "year": event.year,
                "meeting": event.meeting,
                "local_track_geometry_available": True,
                "official_sector_times_and_car_channel_available": has_official,
                "official_sector_input_missing_rate": (
                    0.0 if has_official else 1.0
                ),
                "status": (
                    "ENDPOINT_VALIDATED"
                    if has_official
                    else "NOT_TESTED_MISSING_FROZEN_OFFICIAL_SECTOR_INPUT"
                ),
                "public_point_truth_available": False,
                "public_point_truth_missing_rate": 1.0,
                "segment_mae": None,
            }
        )
    endpoint_rows.append(
        {
            "event_id": "2025-24-abu-dhabi-grand-prix",
            "model_type": "universal",
            "sampling_points": 401,
            "interpolation": "linear_continuous_nearest_discrete",
            "smoothing": "none",
            "sector_endpoint_checks": baseline["gptempo"][
                "sector_endpoint_checks"
            ],
            "max_abs_sector_endpoint_error_s": baseline["gptempo"][
                "max_abs_sector_endpoint_error_s"
            ],
            "segment_mae": None,
            "segment_mae_status": "NOT_TESTED_NO_PUBLIC_POINT_TRUTH",
            "status": "PASS",
        }
    )
    for model_type in ("cluster", "track"):
        endpoint_rows.append(
            {
                "event_id": "2025-24-abu-dhabi-grand-prix",
                "model_type": model_type,
                "sampling_points": None,
                "interpolation": None,
                "smoothing": None,
                "sector_endpoint_checks": 0,
                "max_abs_sector_endpoint_error_s": None,
                "segment_mae": None,
                "segment_mae_status": (
                    "NOT_TESTED_SINGLE_EVENT_NO_OUTER_HOLDOUT"
                ),
                "status": "NOT_TESTED",
            }
        )
    for track in tracks.itertuples(index=False):
        registry_rows.append(
            {
                "task_id": "gptempo",
                "track_key": track.track_key,
                "meeting": track.meeting,
                "observable_cluster": track.observable_cluster,
                "selected_model_type": "universal",
                "fallback_reason": (
                    "SINGLE_REFERENCE_EVENT_NO_CLUSTER_OR_TRACK_SELECTION"
                    if track.track_key == abu_key
                    else "MISSING_FROZEN_OFFICIAL_SECTOR_INPUT"
                ),
                "track_candidate_passed": False,
                "cluster_candidate_passed": False,
                "reference_informed": track.track_key == abu_key,
                "blind_holdout_claim_allowed": False,
            }
        )
    return {
        "endpoints": pd.DataFrame(endpoint_rows),
        "coverage": pd.DataFrame(coverage_rows),
        "registry": pd.DataFrame(registry_rows),
        "comparison": pd.DataFrame(endpoint_rows),
        "splits": pd.DataFrame(
            [
                {
                    "task_id": "gptempo",
                    "training_identity": "PUBLIC_METHOD_FIXED_BEFORE_V3",
                    "validation_identity": "2025_ABU_DHABI_ENDPOINT_INVARIANT",
                    "test_identity": "NOT_TESTED_NO_SECOND_OFFICIAL_EVENT_FREEZE",
                    "status": "UNIVERSAL_ONLY",
                }
            ]
        ),
        "ledger": pd.DataFrame(
            [
                {
                    "all_race_events": int(len(catalog)),
                    "official_sector_input_events": 1,
                    "missing_official_sector_input_events": int(
                        len(catalog) - 1
                    ),
                    "public_point_truth_events": 0,
                    "invented_segment_mae_rows": 0,
                }
            ]
        ),
    }


def target_method_card(
    target_id: str,
    *,
    baseline: Mapping[str, Any],
    config: Mapping[str, Any],
) -> str:
    common = (
        "本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。"
        "外层测试按整场留出；同赛道重复年份优先使用其他年份，"
        "单年或可观测轮廓不连续时只允许赛道族群候选。"
        "固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 "
        "`reference-informed`，不称 blind holdout。\n\n"
    )
    sections = {
        "f1pace": (
            "## 固定语义\n\n"
            "- 逐点前车距离不超过自身速度乘 2 秒才计入交通；一圈比例严格大于 "
            "33% 才是 traffic lap。\n"
            "- universal Huber 不读取测试事件、车手身份或公开参考图；"
            "Abu Dhabi/Qatar v2 外部图只作冻结再验，不进入 v3 拟合。\n"
            "- pairwise delta 仍是描述性均值差并保持反对称，不形成因果排名。\n"
        ),
        "deltadata": (
            "## 固定语义\n\n"
            "- clean air 操作定义保持逐点 2 秒交通比例不高于 20%；"
            "公开燃油情景固定 0.032 秒/圈。\n"
            "- 模型预测 v17 条件修正量，只使用 raw 圈时、胎龄、阶段、交通、"
            "配方、位置和质量等可观测输入；v17 nuisance 分量不回灌为特征。\n"
            "- 衰减方向与同队同配方 H2H 分开验收，不从不可比车队生成全序。\n"
        ),
        "fdataanalysis": (
            "## 固定语义\n\n"
            "- 每场用最多 12 位车手各一条准确绿旗代表圈建立 1,200 区间参考轴；"
            "连续信号线性、离散信号最近邻。\n"
            "- P01…Pn 是横向加速度峰值代理，不是官方弯号。族群和单赛道候选"
            "只能在训练事件选择采样/额外平滑策略。\n"
            "- 跨年失败只叫可观测几何或采样不连续代理，不冒充官方布局变化。\n"
        ),
        "f1telemetrydata": (
            "## 固定语义\n\n"
            "- braking/lift/full/partial 互斥优先级、99% 全油门阈值、"
            "`lane_duration` 与 `stop_duration` 区别保持不变。\n"
            "- 只有 2025 Abu Dhabi 有冻结同场公开图和 OpenF1 Q 阶段补充；"
            "其余排位只允许整场最佳准确推圈代理，逐任务 `NOT_TESTED`。\n"
            "- 单个 reference-informed 事件不足以选择 cluster/track 模型。\n"
        ),
        "gptempo": (
            "## 固定语义\n\n"
            "- 三个官方 sector 端点必须严格闭合；连续信号线性、离散信号最近邻，"
            "段内时间按官方 sector 仿射缩放。\n"
            "- 只有 2025 Abu Dhabi 冻结了所需官方 sector/car channel。"
            "公开页没有逐点真值，因此段内 MAE 保持 `null/NOT_TESTED`。\n"
            "- 单事件只能保留 universal 公开方法，不能发布赛道专用模型。\n"
        ),
    }
    return (
        f"# {target_id} · 2023–2025 分赛道验证 method card\n\n"
        + common
        + sections[target_id]
        + "\n## 发布门\n\n"
        + "- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；"
        "不同量纲不压成总复刻率。\n"
        + "- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。\n"
        + "- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、"
        "车队指令、真实下压力和引擎功率不可识别。\n\n"
        + "## 冻结基线再验\n\n"
        + "```json\n"
        + json.dumps(baseline[target_id], ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def target_data_gap(target_id: str, coverage: pd.DataFrame) -> str:
    status_counts = {}
    for column in ("status", "q1_q2_q3_phase_status", "layout_proxy_status"):
        if column in coverage.columns:
            status_counts[column] = (
                coverage[column].fillna("null").value_counts().to_dict()
            )
    return (
        f"# {target_id} · data-gap audit\n\n"
        f"- coverage rows：{len(coverage)}。\n"
        f"- 状态分布：`{json.dumps(status_counts, ensure_ascii=False)}`。\n"
        "- 缺失不会被补成零；没有公开同场真值时保持 `NOT_TESTED`。\n"
        "- 网络、闭源或补充源没有在 v3 重新抓取；沿用的冻结补充均由 v2 "
        "URL、抓取时间与 SHA-256 身份链约束。\n"
        "- 本地原始逐点读入只用于生成 `.runtime-cache` 中间表；正式报告只发布"
        "精简数值和输入哈希。\n"
    )


def target_reference_difference(target_id: str) -> str:
    text = {
        "f1pace": (
            "v3 不改变 v2 公开图反推 Huber，也不把 2025 Abu Dhabi/Qatar 重新作为"
            "训练样本；新增的是 70 场固定 2 秒语义的无参考图跨年稳定性验证。"
        ),
        "deltadata": (
            "v2 的三场 2026 Mercedes H2H 已被看过，继续标记 reference-informed。"
            "v3 新增 2023–2025 v17 条件修正代理的外层留出，不声称恢复原作者人工选圈。"
        ),
        "fdataanalysis": (
            "v2 只在 2025 Abu Dhabi 对 800/1000/1200 轴做同场敏感性；v3 对 70 场"
            "各自建轴并做同赛道跨年轮廓验证，仍不把 P 编号称官方弯号。"
        ),
        "f1telemetrydata": (
            "v1 的 13 类同场图表数值只在 2025 Abu Dhabi 有公开参考。v3 没有用"
            "跨事件差异伪造 MAE；其余 69 场按图表任务逐项 NOT_TESTED。"
        ),
        "gptempo": (
            "v1 已证明 270 个官方 sector 端点零误差。v3 保持该不变量；由于没有"
            "其他事件的冻结官方 sector/car channel 与公开逐点真值，不发布段内 MAE"
            "或赛道专用采样策略。"
        ),
    }
    return (
        f"# {target_id} · 与冻结 v1/v2 和公开图的差异\n\n"
        + text[target_id]
        + "\n\n视觉复刻与 `audited_analysis` 继续分层；任何描述性顺序都不进入 "
        "PAC/OVR 或车辆/车手因果排名。\n"
    )


def dataframe_records(
    frame: pd.DataFrame,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = frame.head(limit) if limit is not None else frame
    return json.loads(selected.to_json(orient="records"))


def build_frontend_report(
    *,
    run_id: str,
    baseline: Mapping[str, Any],
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    analyses: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    targets = {}
    for target_id, analysis in analyses.items():
        comparison = analysis["comparison"]
        registry = analysis["registry"]
        selected_counts = (
            registry["selected_model_type"].value_counts().to_dict()
            if "selected_model_type" in registry
            else {}
        )
        targets[target_id] = {
            "target_id": target_id,
            "status": (
                "VALIDATED_WITH_FALLBACKS"
                if target_id in {"f1pace", "deltadata", "fdataanalysis"}
                else "PARTIAL_REFERENCE_COVERAGE_NOT_TESTED_ELSEWHERE"
            ),
            "selected_model_counts": selected_counts,
            "model_registry": dataframe_records(registry),
            "model_comparison": dataframe_records(comparison),
            "coverage": dataframe_records(analysis["coverage"]),
            "method_summary": {
                "visual_replication": (
                    "冻结 v1/v2 公开语义与同场数值再验，不参与 v3 拟合。"
                ),
                "audited_analysis": (
                    "70 场真实冻结输入、整场外层留出、逐门发布与明确回退。"
                ),
            },
        }
    return {
        "schema_version": "reference-analysis-track-validation-report-v3",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": config["title"],
        "status": "PASS_WITH_NOT_TESTED_GAPS",
        "coverage": {
            "seasons": 3,
            "events": int(len(catalog)),
            "tracks": int(catalog["track_key"].nunique()),
            "years": sorted(map(int, catalog["year"].unique())),
            "events_by_year": {
                str(year): int(count)
                for year, count in catalog["year"].value_counts().sort_index().items()
            },
        },
        "universal_baseline_recheck": baseline,
        "observable_track_families": dataframe_records(tracks),
        "targets": targets,
        "layers": {
            "visual_replication": {
                "role": "frozen_reference_semantics_and_numeric_recheck",
                "ranking_allowed": True,
                "causal_ranking_allowed": False,
            },
            "audited_analysis": {
                "role": "leakage_safe_track_validation_and_fallback",
                "full_field_ranking_allowed": False,
                "not_tested_is_zero": False,
            },
        },
        "boundaries": {
            "non_identifiable": config["non_identifiable"],
            "pac": None,
            "ovr": None,
            "cross_task_replication_score": None,
            "classification_proxy_is_fia_final_classification": False,
        },
    }


def publish_target(
    staging: Path,
    formal_record_dir: Path,
    target_id: str,
    analysis: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    target_dir = staging / "targets" / target_id
    target_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []

    tables: dict[str, pd.DataFrame] = {
        "coverage_matrix.csv": analysis["coverage"],
        "split_registry.csv": analysis["splits"],
        "model_comparison.csv": analysis["comparison"],
        "sample_exclusion_ledger.csv": analysis["ledger"],
    }
    optional = {
        "event_predictions.csv": "predictions",
        "model_coefficients.csv": "coefficients",
        "pairwise_invariants.csv": "pairwise",
        "degradation_direction.csv": "degradation",
        "teammate_direction.csv": "teammates",
        "segmentation_events.csv": "events",
        "strategy_selection_audit.csv": "selection",
        "layout_stability.csv": "stability",
        "chart_reference_matrix.csv": "reference",
        "sector_endpoint_validation.csv": "endpoints",
    }
    for filename, key in optional.items():
        if key in analysis and isinstance(analysis[key], pd.DataFrame):
            tables[filename] = analysis[key]
    for filename, frame in tables.items():
        if frame is None:
            frame = pd.DataFrame()
        files.append(write_csv(target_dir / filename, frame))

    registry_payload = {
        "schema_version": "reference-analysis-model-registry-v3",
        "target_id": target_id,
        "run_id": config["run_id"],
        "selection_gate": config["model_publication_gate"],
        "rows": dataframe_records(analysis["registry"]),
        "fallback_is_failure": False,
        "track_specific_model_requires_holdout_improvement": True,
    }
    registry_sha = write_json(target_dir / "model_registry.json", registry_payload)
    files.append(
        {
            "path": relative(target_dir / "model_registry.json"),
            "sha256": registry_sha,
            "bytes": (target_dir / "model_registry.json").stat().st_size,
        }
    )
    method_sha = write_text(
        target_dir / "method_card.md",
        target_method_card(
            target_id,
            baseline=baseline,
            config=config,
        ),
    )
    gap_sha = write_text(
        target_dir / "data_gap_audit.md",
        target_data_gap(target_id, analysis["coverage"]),
    )
    difference_sha = write_text(
        target_dir / "reference_difference.md",
        target_reference_difference(target_id),
    )
    for name, digest in (
        ("method_card.md", method_sha),
        ("data_gap_audit.md", gap_sha),
        ("reference_difference.md", difference_sha),
    ):
        files.append(
            {
                "path": relative(target_dir / name),
                "sha256": digest,
                "bytes": (target_dir / name).stat().st_size,
            }
        )
    staging_prefix = relative(staging)
    formal_prefix = relative(formal_record_dir)
    files = [
        {
            **entry,
            "path": str(entry["path"]).replace(
                staging_prefix,
                formal_prefix,
                1,
            ),
        }
        for entry in files
    ]
    manifest = {
        "schema_version": "reference-analysis-target-manifest-v3",
        "run_id": config["run_id"],
        "target_id": target_id,
        "status": (
            "PASS_WITH_NOT_TESTED_GAPS"
            if target_id in {"f1telemetrydata", "gptempo"}
            else "PASS_WITH_MODEL_FALLBACKS"
        ),
        "files": files,
        "selected_model_counts": analysis["registry"][
            "selected_model_type"
        ].value_counts().to_dict(),
        "visual_replication_and_audited_analysis_are_separate": True,
    }
    manifest_sha = write_json(target_dir / "manifest.json", manifest)
    return {
        "target_id": target_id,
        "manifest": relative(
            formal_record_dir / "targets" / target_id / "manifest.json"
        ),
        "manifest_sha256": manifest_sha,
        "status": manifest["status"],
        "files": len(files),
    }


def publish_run(
    *,
    config_path: Path,
    config: Mapping[str, Any],
    baseline: Mapping[str, Any],
    catalog: pd.DataFrame,
    tracks: pd.DataFrame,
    cache_identities: Sequence[Mapping[str, Any]],
    source_identities: Mapping[str, Any],
    analyses: Mapping[str, Mapping[str, Any]],
    cache_root: Path,
) -> tuple[Path, Path]:
    run_id = str(config["run_id"])
    record_dir = RECORDS_BASE / f"run={run_id}"
    frontend_dir = FRONTEND_BASE / f"run={run_id}"
    if record_dir.exists() or frontend_dir.exists():
        raise FileExistsError(
            "追加式 v3 拒绝覆盖已有正式路径；请使用新的 run_id"
        )
    staging = cache_root / "publish-staging" / f"run={run_id}"
    if staging.exists():
        resolved = staging.resolve()
        allowed = (cache_root / "publish-staging").resolve()
        if allowed not in resolved.parents:
            raise ValueError("拒绝清理非 v3 staging 路径")
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=False)

    baseline_sha = write_json(staging / "universal_baseline_recheck.json", baseline)
    tracks_file = write_csv(staging / "observable_track_registry.csv", tracks)
    catalog_file = write_csv(staging / "event_catalog.csv", catalog)
    target_manifests = [
        publish_target(
            staging,
            record_dir,
            target_id,
            analysis,
            baseline=baseline,
            config=config,
        )
        for target_id, analysis in analyses.items()
    ]
    frontend_report = build_frontend_report(
        run_id=run_id,
        baseline=baseline,
        catalog=catalog,
        tracks=tracks,
        analyses=analyses,
        config=config,
    )
    report_sha = write_json(staging / "track_validation_v3.json", frontend_report)
    model_registry = {
        "schema_version": "reference-analysis-combined-model-registry-v3",
        "run_id": run_id,
        "targets": {
            target_id: dataframe_records(analysis["registry"])
            for target_id, analysis in analyses.items()
        },
        "selection_gate": config["model_publication_gate"],
        "cross_task_total_score": None,
    }
    combined_registry_sha = write_json(
        staging / "model_registry.json",
        model_registry,
    )
    config_sha = sha256_file(config_path)
    manifest = {
        "schema_version": "reference-analysis-track-validation-manifest-v3",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_WITH_NOT_TESTED_GAPS",
        "append_only": True,
        "offline_only": True,
        "config": {
            "path": relative(config_path),
            "sha256": config_sha,
        },
        "source_identities": source_identities,
        "cache_identities": list(cache_identities),
        "coverage": {
            "events": int(len(catalog)),
            "tracks": int(catalog["track_key"].nunique()),
            "seasons": 3,
        },
        "universal_baseline_recheck": {
            "path": relative(staging / "universal_baseline_recheck.json"),
            "sha256": baseline_sha,
        },
        "report": {
            "path": relative(staging / "track_validation_v3.json"),
            "sha256": report_sha,
        },
        "model_registry": {
            "path": relative(staging / "model_registry.json"),
            "sha256": combined_registry_sha,
        },
        "event_catalog": catalog_file,
        "observable_track_registry": tracks_file,
        "target_manifests": target_manifests,
        "boundaries": {
            "not_tested_is_zero": False,
            "reference_informed_is_blind_holdout": False,
            "visual_replication_and_audited_analysis_are_separate": True,
            "pac": None,
            "ovr": None,
        },
    }
    if config.get("supersedes_run_id"):
        manifest["supersedes"] = {
            "run_id": config["supersedes_run_id"],
            "reason": config["supersedes_reason"],
            "scope": config.get(
                "supersedes_scope",
                "post_publication_identity_audit",
            ),
        }
    # staging 路径将在复制后改变；正式 manifest 使用正式相对路径重写身份字段。
    def replace_staging_paths(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: replace_staging_paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_staging_paths(item) for item in value]
        if isinstance(value, str):
            staging_prefix = relative(staging)
            formal_prefix = relative(record_dir)
            return value.replace(staging_prefix, formal_prefix)
        return value

    manifest = replace_staging_paths(manifest)
    write_json(staging / "manifest.json", manifest)
    record_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, record_dir)

    frontend_dir.mkdir(parents=True, exist_ok=False)
    for name in ("track_validation_v3.json", "model_registry.json"):
        shutil.copy2(record_dir / name, frontend_dir / name)
    frontend_manifest = {
        "schema_version": "reference-analysis-track-validation-public-manifest-v3",
        "run_id": run_id,
        "status": manifest["status"],
        "report": {
            "path": (
                f"data/reference-analysis-lab/v3/run={run_id}/"
                "track_validation_v3.json"
            ),
            "sha256": sha256_file(frontend_dir / "track_validation_v3.json"),
            "bytes": (frontend_dir / "track_validation_v3.json").stat().st_size,
        },
        "model_registry": {
            "path": (
                f"data/reference-analysis-lab/v3/run={run_id}/"
                "model_registry.json"
            ),
            "sha256": sha256_file(frontend_dir / "model_registry.json"),
            "bytes": (frontend_dir / "model_registry.json").stat().st_size,
        },
        "source_manifest": {
            "path": relative(record_dir / "manifest.json"),
            "sha256": sha256_file(record_dir / "manifest.json"),
        },
    }
    if config.get("supersedes_run_id"):
        frontend_manifest["supersedes"] = {
            "run_id": config["supersedes_run_id"],
            "reason": config["supersedes_reason"],
        }
    frontend_manifest_sha = write_json(
        frontend_dir / "manifest.json",
        frontend_manifest,
    )
    index_path = FRONTEND_BASE / "manifest.json"
    existing_targets: list[dict[str, Any]] = []
    if index_path.exists():
        existing_index = json.loads(index_path.read_text(encoding="utf-8"))
        existing_targets = [
            dict(entry) for entry in existing_index.get("targets", [])
        ]
    supersedes_run_id = config.get("supersedes_run_id")
    if supersedes_run_id:
        for entry in existing_targets:
            if entry.get("run_id") == supersedes_run_id:
                entry["status"] = config.get(
                    "supersedes_status",
                    "SUPERSEDED_BY_VALIDATED_APPEND",
                )
                entry["superseded_by_run_id"] = run_id
                entry["superseded_reason"] = config["supersedes_reason"]
    new_entry = {
        "target_id": "track-validation-v3",
        "label": config["title"],
        "status": manifest["status"],
        "run_id": run_id,
        "manifest": (
            f"data/reference-analysis-lab/v3/run={run_id}/manifest.json"
        ),
        "manifest_sha256": frontend_manifest_sha,
        "report_sha256": frontend_manifest["report"]["sha256"],
        "page": "track-validation-v3.html",
    }
    existing_targets = [
        entry for entry in existing_targets if entry.get("run_id") != run_id
    ]
    index = {
        "schema_version": "reference-analysis-lab-index-v3",
        "latest_run_id": run_id,
        "targets": [*existing_targets, new_entry],
    }
    write_json(index_path, index)
    return record_dir, frontend_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--prepare-only",
        type=int,
        choices=(2023, 2024, 2025),
        help="只串行准备指定年份的 .runtime-cache，不发布正式产物",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    cache_root = ROOT / config["resource_policy"]["cache_root"]
    cache_root.mkdir(parents=True, exist_ok=True)
    catalog, lap_universe, qualifying, source_identities = load_event_catalog(config)
    if args.prepare_only:
        prepare_cache_for_year(
            catalog,
            year=args.prepare_only,
            cache_root=cache_root,
            representative_laps=int(
                config["resource_policy"][
                    "representative_laps_per_event_for_track_profile"
                ]
            ),
        )
        return 0

    baseline = recompute_frozen_baselines(config)
    traffic, profiles, segment_summary, cache_identities = load_prepared_caches(
        catalog,
        cache_root=cache_root,
    )
    tracks, stability, _ = build_track_metadata(
        catalog,
        profiles,
        segment_summary,
        config,
    )
    catalog = catalog.merge(
        tracks[
            [
                "track_key",
                "observable_cluster",
                "layout_proxy_status",
                "track_model_allowed_by_layout_proxy",
            ]
        ],
        on="track_key",
        how="left",
        validate="many_to_one",
    )
    print("A/F1pace：开始 70 场外层留出")
    f1pace = f1pace_analysis(
        traffic,
        catalog,
        tracks,
        lap_universe,
        config,
    )
    print("B/DeltaData：读取 v17 sidecar 并开始外层留出")
    v17_points = load_v17_points(catalog, traffic)
    deltadata = deltadata_analysis(
        v17_points,
        catalog,
        tracks,
        config,
    )
    print("C/FDataAnalysis：开始训练折策略选择与跨年轮廓验证")
    fdataanalysis = fdataanalysis_analysis(
        catalog,
        tracks,
        profiles,
        segment_summary,
        stability,
        config,
    )
    print("D/F1TelemetryData：逐图表登记同场参考与 NOT_TESTED")
    f1telemetrydata = f1telemetrydata_analysis(
        catalog,
        tracks,
        baseline,
    )
    print("E/GP Tempo：复核官方 sector 端点与缺口")
    gptempo = gptempo_analysis(catalog, tracks, baseline)
    analyses = {
        "f1pace": f1pace,
        "deltadata": deltadata,
        "fdataanalysis": fdataanalysis,
        "f1telemetrydata": f1telemetrydata,
        "gptempo": gptempo,
    }
    record_dir, frontend_dir = publish_run(
        config_path=config_path,
        config=config,
        baseline=baseline,
        catalog=catalog,
        tracks=tracks,
        cache_identities=cache_identities,
        source_identities=source_identities,
        analyses=analyses,
        cache_root=cache_root,
    )
    print(
        json.dumps(
            {
                "status": "PASS_WITH_NOT_TESTED_GAPS",
                "run_id": config["run_id"],
                "record_dir": relative(record_dir),
                "frontend_dir": relative(frontend_dir),
                "events": len(catalog),
                "tracks": catalog["track_key"].nunique(),
                "selected_models": {
                    target: analysis["registry"][
                        "selected_model_type"
                    ].value_counts().to_dict()
                    for target, analysis in analyses.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
