#!/usr/bin/env python3
"""执行 2024 训练、2025 锁定验证的 F1 车辆—车手分离实验。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import psycopg
import sklearn
import build_v4_frozen_data as v4_freeze
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "research" / "configs" / "2024_train_2025_validate.json"
DATA_DIR = ROOT / "research" / "artifacts" / "data"
MANIFEST_PATH = ROOT / "research" / "artifacts" / "data_manifest.json"
RECORDS_DIR = ROOT / "research" / "records"
LOG_PATH = RECORDS_DIR / "experiment_log.jsonl"
RESULTS_PATH = RECORDS_DIR / "latest_results.json"
SUMMARY_PATH = RECORDS_DIR / "latest_summary.md"


MODEL_SPECS = {
    "context_ridge": {
        "phase": "phase_1",
        "categorical": ["compound", "circuit_name", "session_name"],
    },
    "driver_only_ridge": {
        "phase": "phase_1",
        "categorical": ["compound", "circuit_name", "session_name", "driver_id"],
    },
    "team_only_ridge": {
        "phase": "phase_1",
        "categorical": ["compound", "circuit_name", "session_name", "team_name"],
    },
    "driver_team_ridge": {
        "phase": "phase_2",
        "categorical": [
            "compound",
            "circuit_name",
            "session_name",
            "driver_id",
            "team_name",
        ],
    },
}

NUMERIC_FEATURES = ["lap_fraction", "lap_fraction_sq", "tyre_age", "tyre_age_sq"]


@dataclass
class AttemptResult:
    value: Any
    attempts: int
    status: str


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(f"无法 JSON 序列化: {type(value)!r}")


def append_log(event: dict[str, Any]) -> None:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=json_default) + "\n")


def run_with_retries(
    run_id: str,
    phase: str,
    method: str,
    max_attempts: int,
    action: Callable[[int], Any],
) -> AttemptResult:
    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            value = action(attempt)
            duration = time.perf_counter() - started
            append_log(
                {
                    "run_id": run_id,
                    "timestamp": now_iso(),
                    "phase": phase,
                    "method": method,
                    "attempt": attempt,
                    "status": "success",
                    "duration_seconds": round(duration, 3),
                }
            )
            return AttemptResult(value=value, attempts=attempt, status="success")
        except Exception as exc:  # noqa: BLE001 - 失败必须完整进入实验记录
            duration = time.perf_counter() - started
            append_log(
                {
                    "run_id": run_id,
                    "timestamp": now_iso(),
                    "phase": phase,
                    "method": method,
                    "attempt": attempt,
                    "status": "failed",
                    "duration_seconds": round(duration, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:2000],
                    "traceback": traceback.format_exc(limit=8)[-6000:],
                }
            )
    append_log(
        {
            "run_id": run_id,
            "timestamp": now_iso(),
            "phase": phase,
            "method": method,
            "attempts": max_attempts,
            "status": "skipped_after_3_failures",
        }
    )
    return AttemptResult(value=None, attempts=max_attempts, status="skipped_after_3_failures")


def load_database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DATABASE_URL":
                return value.strip().strip('"').strip("'")
    raise RuntimeError("缺少 DATABASE_URL；请设置环境变量或项目 .env。")


def fetch_frame(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...]) -> pd.DataFrame:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [description.name for description in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


DETAILED_LAP_SQL = """
SELECT
    m.year,
    m.meeting_key,
    m.meeting_name,
    COALESCE(NULLIF(m.circuit_name, ''), NULLIF(m.location, ''), m.meeting_name) AS circuit_name,
    s.session_key,
    s.session_name,
    s.date_start AS session_date_start,
    l.driver_number,
    COALESCE(NULLIF(BTRIM(d.name_acronym), ''), l.driver_number::text) AS driver_id,
    COALESCE(NULLIF(d.full_name, ''), BTRIM(d.name_acronym), l.driver_number::text) AS driver_name,
    COALESCE(NULLIF(d.team_name, ''), '<UNKNOWN_TEAM>') AS team_name,
    l.lap_number,
    l.lap_duration,
    COALESCE(l.is_pit_out_lap, FALSE) AS is_pit_out_lap,
    UPPER(COALESCE(NULLIF(st.compound, ''), 'UNKNOWN')) AS compound,
    st.tyre_age_at_start,
    st.lap_start AS stint_lap_start,
    st.lap_end AS stint_lap_end,
    st.stint_number,
    EXISTS (
        SELECT 1 FROM pit_stops p
        WHERE p.session_key = l.session_key
          AND p.driver_number = l.driver_number
          AND p.lap_number = l.lap_number
    ) AS is_pit_in_lap
FROM laps l
JOIN sessions s ON s.session_key = l.session_key
JOIN meetings m ON m.meeting_key = s.meeting_key
JOIN drivers d ON d.session_key = l.session_key AND d.driver_number = l.driver_number
LEFT JOIN LATERAL (
    SELECT st0.compound, st0.tyre_age_at_start, st0.lap_start, st0.lap_end, st0.stint_number
    FROM stints st0
    WHERE st0.session_key = l.session_key
      AND st0.driver_number = l.driver_number
      AND l.lap_number BETWEEN st0.lap_start AND st0.lap_end
    ORDER BY st0.stint_number
    LIMIT 1
) st ON TRUE
WHERE m.year IN (%s, %s)
  AND s.session_name = ANY(%s)
  AND l.lap_duration IS NOT NULL
  AND l.lap_duration > 0
ORDER BY m.year, m.meeting_key, s.session_key, l.driver_number, l.lap_number
"""


MINIMAL_LAP_SQL = """
SELECT
    m.year,
    m.meeting_key,
    m.meeting_name,
    COALESCE(NULLIF(m.circuit_name, ''), NULLIF(m.location, ''), m.meeting_name) AS circuit_name,
    s.session_key,
    s.session_name,
    s.date_start AS session_date_start,
    l.driver_number,
    COALESCE(NULLIF(BTRIM(d.name_acronym), ''), l.driver_number::text) AS driver_id,
    COALESCE(NULLIF(d.full_name, ''), BTRIM(d.name_acronym), l.driver_number::text) AS driver_name,
    COALESCE(NULLIF(d.team_name, ''), '<UNKNOWN_TEAM>') AS team_name,
    l.lap_number,
    l.lap_duration,
    COALESCE(l.is_pit_out_lap, FALSE) AS is_pit_out_lap,
    'UNKNOWN'::text AS compound,
    0::smallint AS tyre_age_at_start,
    l.lap_number::smallint AS stint_lap_start,
    l.lap_number::smallint AS stint_lap_end,
    NULL::smallint AS stint_number,
    EXISTS (
        SELECT 1 FROM pit_stops p
        WHERE p.session_key = l.session_key
          AND p.driver_number = l.driver_number
          AND p.lap_number = l.lap_number
    ) AS is_pit_in_lap
FROM laps l
JOIN sessions s ON s.session_key = l.session_key
JOIN meetings m ON m.meeting_key = s.meeting_key
JOIN drivers d ON d.session_key = l.session_key AND d.driver_number = l.driver_number
WHERE m.year IN (%s, %s)
  AND s.session_name = ANY(%s)
  AND l.lap_duration IS NOT NULL
  AND l.lap_duration > 0
ORDER BY m.year, m.meeting_key, s.session_key, l.driver_number, l.lap_number
"""


RACE_CONTROL_SQL = """
SELECT rc.session_key, rc.lap_number, rc.date, rc.category, rc.flag, rc.message
FROM race_control rc
JOIN sessions s ON s.session_key = rc.session_key
JOIN meetings m ON m.meeting_key = s.meeting_key
WHERE m.year IN (%s, %s)
  AND s.session_name = ANY(%s)
ORDER BY rc.session_key, rc.date, rc.lap_number
"""


def extract_raw_data(config: dict[str, Any], attempt: int, detailed: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    timeout_ms = int(config["statement_timeout_ms"]) + (attempt - 1) * 30000
    params = (
        int(config["train_year"]),
        int(config["validation_year"]),
        list(config["session_names"]),
    )
    with psycopg.connect(load_database_url()) as conn:
        conn.execute(f"SET statement_timeout TO {timeout_ms}")
        laps = fetch_frame(conn, DETAILED_LAP_SQL if detailed else MINIMAL_LAP_SQL, params)
        controls = fetch_frame(conn, RACE_CONTROL_SQL, params)
    if laps.empty:
        raise RuntimeError("数据提取结果为空。")
    return laps, controls


def disrupted_laps(controls: pd.DataFrame, max_laps: dict[int, int]) -> set[tuple[int, int]]:
    disrupted: set[tuple[int, int]] = set()
    if controls.empty:
        return disrupted
    controls = controls.copy()
    controls["lap_number"] = pd.to_numeric(controls["lap_number"], errors="coerce")
    for session_key, group in controls.groupby("session_key", sort=False):
        session = int(session_key)
        active_start: int | None = None
        last_lap = int(max_laps.get(session, 0))
        ordered = group.sort_values(["date", "lap_number"], na_position="last")
        for row in ordered.itertuples(index=False):
            if pd.isna(row.lap_number):
                continue
            lap = int(row.lap_number)
            message = str(row.message or "").upper().strip()
            flag = str(row.flag or "").upper().strip()
            is_deploy = message in {
                "SAFETY CAR DEPLOYED",
                "VIRTUAL SAFETY CAR DEPLOYED",
                "VSC DEPLOYED",
            }
            is_end = (
                message == "SAFETY CAR IN THIS LAP"
                or "VIRTUAL SAFETY CAR ENDING" in message
                or message == "VSC ENDING"
            )
            if is_deploy and active_start is None:
                active_start = lap
            if flag in {"RED", "YELLOW", "DOUBLE YELLOW"} or "RED FLAG" in message:
                disrupted.add((session, lap))
            if is_end and active_start is not None:
                for affected_lap in range(active_start, lap + 1):
                    disrupted.add((session, affected_lap))
                active_start = None
        if active_start is not None:
            for affected_lap in range(active_start, last_lap + 1):
                disrupted.add((session, affected_lap))
    return disrupted


def clean_and_freeze(
    raw: pd.DataFrame,
    controls: pd.DataFrame,
    config: dict[str, Any],
    degraded: bool,
    file_prefix: str = "",
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    frame = raw.copy()
    frame["lap_duration"] = pd.to_numeric(frame["lap_duration"], errors="coerce")
    frame["lap_number"] = pd.to_numeric(frame["lap_number"], errors="coerce")
    frame["compound"] = frame["compound"].fillna("UNKNOWN").astype(str).str.upper().str.strip()
    frame["driver_id"] = frame["driver_id"].astype(str).str.strip()
    frame["team_name"] = frame["team_name"].astype(str).str.strip()
    frame["circuit_name"] = frame["circuit_name"].fillna("<UNKNOWN_CIRCUIT>").astype(str)
    frame = frame.drop_duplicates(["year", "session_key", "driver_number", "lap_number"])

    max_lap_by_session = (
        frame.groupby("session_key")["lap_number"].max().fillna(0).astype(int).to_dict()
    )
    disrupted = disrupted_laps(controls, max_lap_by_session)
    frame["is_disrupted_lap"] = [
        (int(session), int(lap)) in disrupted
        for session, lap in zip(frame["session_key"], frame["lap_number"])
    ]

    age_start = pd.to_numeric(frame["tyre_age_at_start"], errors="coerce")
    stint_start = pd.to_numeric(frame["stint_lap_start"], errors="coerce")
    frame["tyre_age"] = age_start + frame["lap_number"] - stint_start
    if degraded:
        frame["tyre_age"] = 0.0
    frame["tyre_age"] = frame["tyre_age"].clip(lower=0, upper=80)

    clean_cfg = config["cleaning"]
    duration_ok = frame["lap_duration"].between(
        float(clean_cfg["minimum_lap_duration_seconds"]),
        float(clean_cfg["maximum_lap_duration_seconds"]),
    )
    dry_ok = frame["compound"].isin(config["dry_compounds"]) if not degraded else True
    base_mask = (
        duration_ok
        & (frame["lap_number"] >= int(clean_cfg["minimum_lap_number"]))
        & ~frame["is_pit_out_lap"].astype(bool)
        & ~frame["is_pit_in_lap"].astype(bool)
        & ~frame["is_disrupted_lap"].astype(bool)
        & dry_ok
        & (frame["tyre_age"].notna() if not degraded else True)
    )
    candidate = frame.loc[base_mask].copy()
    if candidate.empty:
        raise RuntimeError("清洗后没有候选圈。")

    session_median = candidate.groupby("session_key")["lap_duration"].transform("median")
    absolute_deviation = (candidate["lap_duration"] - session_median).abs()
    session_mad = absolute_deviation.groupby(candidate["session_key"]).transform("median")
    session_mad = session_mad.clip(lower=0.05)
    robust_ok = absolute_deviation <= float(clean_cfg["robust_mad_multiplier"]) * session_mad
    candidate = candidate.loc[robust_ok].copy()

    clean_counts = candidate.groupby("session_key").size()
    eligible_sessions = clean_counts[
        clean_counts >= int(clean_cfg["minimum_clean_laps_per_session"])
    ].index
    clean = candidate[candidate["session_key"].isin(eligible_sessions)].copy()
    if clean.empty:
        raise RuntimeError("没有赛段达到最小清洁圈数量。")

    clean["session_reference"] = clean.groupby("session_key")["lap_duration"].transform("median")
    clean["target_log_pct"] = 100.0 * np.log(
        clean["lap_duration"] / clean["session_reference"]
    )
    session_max = clean.groupby("session_key")["lap_number"].transform("max").clip(lower=1)
    clean["lap_fraction"] = clean["lap_number"] / session_max
    clean["lap_fraction_sq"] = clean["lap_fraction"] ** 2
    clean["tyre_age_sq"] = clean["tyre_age"] ** 2
    clean["session_date_start"] = pd.to_datetime(clean["session_date_start"], utc=True)
    clean = clean.sort_values(
        ["year", "meeting_key", "session_key", "driver_number", "lap_number"]
    ).reset_index(drop=True)

    train = clean[clean["year"] == int(config["train_year"])].copy()
    validation = clean[clean["year"] == int(config["validation_year"])].copy()
    if train.empty or validation.empty:
        raise RuntimeError(
            f"训练或验证数据为空: train={len(train)}, validation={len(validation)}"
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    train_path = DATA_DIR / f"{file_prefix}train_{config['train_year']}.csv.gz"
    validation_path = DATA_DIR / f"{file_prefix}validation_{config['validation_year']}.csv.gz"
    train.to_csv(train_path, index=False, compression="gzip", encoding="utf-8")
    validation.to_csv(validation_path, index=False, compression="gzip", encoding="utf-8")

    summary_by_year_session = (
        clean.groupby(["year", "session_name"])
        .agg(rows=("lap_duration", "size"), sessions=("session_key", "nunique"))
        .reset_index()
        .to_dict(orient="records")
    )
    manifest = {
        "created_at": now_iso(),
        "config_hash": canonical_hash(config),
        "query_version": "detailed_lateral_stint_v1" if not degraded else "minimal_no_stint_v1",
        "degraded": degraded,
        "raw_rows": int(len(frame)),
        "candidate_rows_before_robust_filter": int(base_mask.sum()),
        "clean_rows": int(len(clean)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "train_sessions": int(train["session_key"].nunique()),
        "validation_sessions": int(validation["session_key"].nunique()),
        "train_meetings": int(train["meeting_key"].nunique()),
        "validation_meetings": int(validation["meeting_key"].nunique()),
        "summary_by_year_session": summary_by_year_session,
        "independent_exclusion_counts": {
            "duration_out_of_bounds": int((~duration_ok).sum()),
            "lap_before_minimum": int(
                (frame["lap_number"] < int(clean_cfg["minimum_lap_number"])).sum()
            ),
            "pit_out": int(frame["is_pit_out_lap"].astype(bool).sum()),
            "pit_in": int(frame["is_pit_in_lap"].astype(bool).sum()),
            "disrupted": int(frame["is_disrupted_lap"].astype(bool).sum()),
            "non_dry_compound": int((~frame["compound"].isin(config["dry_compounds"])).sum()),
            "missing_tyre_age": int(frame["tyre_age"].isna().sum()),
        },
        "files": {
            "train": {
                "path": str(train_path.relative_to(ROOT)),
                "sha256": file_sha256(train_path),
            },
            "validation": {
                "path": str(validation_path.relative_to(ROOT)),
                "sha256": file_sha256(validation_path),
            },
        },
    }
    atomic_json(manifest_path, manifest)
    return train, validation, manifest


def load_frozen_pair(
    config: dict[str, Any], manifest_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    manifest = load_json(manifest_path)
    if manifest.get("config_hash") != canonical_hash(config):
        raise RuntimeError("冻结数据的 config_hash 与当前配置不一致。")
    train_path = ROOT / manifest["files"]["train"]["path"]
    validation_path = ROOT / manifest["files"]["validation"]["path"]
    if file_sha256(train_path) != manifest["files"]["train"]["sha256"]:
        raise RuntimeError("训练冻结文件哈希不一致。")
    if file_sha256(validation_path) != manifest["files"]["validation"]["sha256"]:
        raise RuntimeError("验证冻结文件哈希不一致。")
    train = pd.read_csv(train_path, compression="gzip")
    validation = pd.read_csv(validation_path, compression="gzip")
    return train, validation, manifest


def load_frozen_data(config: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    return load_frozen_pair(config, MANIFEST_PATH)


def make_pipeline(categorical: list[str], alpha: float, solver: str) -> Pipeline:
    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            (
                "onehot",
                OneHotEncoder(handle_unknown="ignore", sparse_output=True, dtype=np.float64),
            ),
        ]
    )
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            ("cat", categorical_pipeline, categorical),
            ("num", numeric_pipeline, NUMERIC_FEATURES),
        ],
        sparse_threshold=0.2,
    )
    ridge = Ridge(
        alpha=float(alpha),
        solver=solver,
        tol=1e-4,
        max_iter=20000,
        random_state=202407 if solver == "sag" else None,
    )
    return Pipeline([("preprocess", preprocess), ("model", ridge)])


def regression_metrics(y_true: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, prediction)),
        "rmse": float(math.sqrt(mean_squared_error(y_true, prediction))),
        "r2": float(r2_score(y_true, prediction)),
        "bias": float(np.mean(prediction - y_true)),
    }


def select_alpha(
    train: pd.DataFrame,
    categorical: list[str],
    config: dict[str, Any],
    solver: str,
) -> tuple[float, dict[str, float]]:
    features = categorical + NUMERIC_FEATURES
    y = train["target_log_pct"].to_numpy(dtype=float)
    groups = train["meeting_key"].to_numpy()
    unique_groups = np.unique(groups)
    splits = min(int(config["group_cv_splits"]), len(unique_groups))
    if splits < 2:
        raise RuntimeError("训练集 meeting 数不足以执行 GroupKFold。")
    cv = GroupKFold(n_splits=splits)
    scores: dict[str, float] = {}
    for alpha in config["ridge_alphas"]:
        fold_scores: list[float] = []
        for fit_index, holdout_index in cv.split(train, y, groups):
            pipeline = make_pipeline(categorical, float(alpha), solver)
            pipeline.fit(train.iloc[fit_index][features], y[fit_index])
            prediction = pipeline.predict(train.iloc[holdout_index][features])
            fold_scores.append(float(mean_absolute_error(y[holdout_index], prediction)))
        scores[str(alpha)] = float(np.mean(fold_scores))
    best_alpha = min(config["ridge_alphas"], key=lambda value: scores[str(value)])
    return float(best_alpha), scores


def unseen_rates(
    train: pd.DataFrame, validation: pd.DataFrame, categorical: list[str]
) -> dict[str, float]:
    result: dict[str, float] = {}
    for column in categorical:
        known = set(train[column].astype(str).unique())
        result[column] = float((~validation[column].astype(str).isin(known)).mean())
    return result


def bootstrap_delta_vs_zero(
    validation: pd.DataFrame,
    prediction: np.ndarray,
    seed: int,
    repetitions: int = 1000,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    y = validation["target_log_pct"].to_numpy(dtype=float)
    meetings = validation["meeting_key"].to_numpy()
    unique_meetings = np.unique(meetings)
    differences: list[float] = []
    for _ in range(repetitions):
        sampled = rng.choice(unique_meetings, size=len(unique_meetings), replace=True)
        indices = np.concatenate([np.flatnonzero(meetings == meeting) for meeting in sampled])
        model_mae = mean_absolute_error(y[indices], prediction[indices])
        zero_mae = mean_absolute_error(y[indices], np.zeros(len(indices)))
        differences.append(float(model_mae - zero_mae))
    low, high = np.quantile(differences, [0.025, 0.975])
    return {
        "mean_mae_delta": float(np.mean(differences)),
        "ci95_low": float(low),
        "ci95_high": float(high),
        "unit": "target_log_pct",
        "negative_is_better_than_zero_baseline": True,
    }


def extract_ratings(pipeline: Pipeline) -> dict[str, list[dict[str, float | str]]]:
    names = pipeline.named_steps["preprocess"].get_feature_names_out()
    coefficients = pipeline.named_steps["model"].coef_
    output: dict[str, list[dict[str, float | str]]] = {"drivers": [], "teams": []}
    for label, prefix in (("drivers", "cat__driver_id_"), ("teams", "cat__team_name_")):
        values: list[tuple[str, float]] = []
        for name, coefficient in zip(names, coefficients):
            if name.startswith(prefix):
                entity = name[len(prefix) :]
                values.append((entity, -float(coefficient)))
        if values:
            center = float(np.mean([value for _, value in values]))
            output[label] = [
                {
                    "entity": entity,
                    "pace_advantage_pct": float(value - center),
                }
                for entity, value in sorted(values, key=lambda pair: pair[1], reverse=True)
            ]
    return output


def fit_model(
    name: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    config: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    spec = MODEL_SPECS[name]
    categorical = list(spec["categorical"])
    solver_by_attempt = {1: "lsqr", 2: "sag", 3: "sparse_cg"}
    solver = solver_by_attempt.get(attempt, "lsqr")
    features = categorical + NUMERIC_FEATURES
    best_alpha, cv_scores = select_alpha(train, categorical, config, solver)
    pipeline = make_pipeline(categorical, best_alpha, solver)
    y_train = train["target_log_pct"].to_numpy(dtype=float)
    y_validation = validation["target_log_pct"].to_numpy(dtype=float)
    pipeline.fit(train[features], y_train)
    train_prediction = pipeline.predict(train[features])
    validation_prediction = pipeline.predict(validation[features])
    by_session: dict[str, dict[str, float]] = {}
    for session_name, subset in validation.groupby("session_name"):
        index = subset.index.to_numpy()
        # validation 在冻结文件中保留连续索引；为安全起见按位置重新映射。
        positions = validation.index.get_indexer(index)
        by_session[str(session_name)] = regression_metrics(
            subset["target_log_pct"].to_numpy(dtype=float), validation_prediction[positions]
        )
    return {
        "name": name,
        "phase": spec["phase"],
        "status": "success",
        "solver": solver,
        "selected_alpha": best_alpha,
        "group_cv_mae_by_alpha": cv_scores,
        "train_metrics": regression_metrics(y_train, train_prediction),
        "validation_metrics": regression_metrics(y_validation, validation_prediction),
        "validation_metrics_by_session": by_session,
        "bootstrap_mae_delta_vs_zero": bootstrap_delta_vs_zero(
            validation,
            validation_prediction,
            seed=int(config["random_seed"]) + list(MODEL_SPECS).index(name),
        ),
        "unseen_category_rates": unseen_rates(train, validation, categorical),
        "ratings": extract_ratings(pipeline),
        "feature_count_after_encoding": int(
            len(pipeline.named_steps["preprocess"].get_feature_names_out())
        ),
    }


def git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - 元数据失败不应阻断实验
        return None


def write_summary(results: dict[str, Any]) -> None:
    lines = [
        "# 2024 训练 / 2025 验证实验摘要",
        "",
        f"- 运行 ID：`{results['run_id']}`",
        f"- 生成时间：{results['created_at']}",
        f"- 训练圈数：{results['data_manifest']['train_rows']}",
        f"- 验证圈数：{results['data_manifest']['validation_rows']}",
        f"- 数据模式：{'降级提取' if results['data_manifest'].get('degraded') else '详细轮胎提取'}",
        "",
        "## 验证结果",
        "",
        "| 模型 | 状态 | 2024 CV MAE | 2025 MAE | 2025 RMSE | 2025 R² | alpha |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    zero = results["zero_baseline"]
    lines.append(
        f"| zero_relative_pace | success | — | {zero['validation_metrics']['mae']:.5f} | "
        f"{zero['validation_metrics']['rmse']:.5f} | {zero['validation_metrics']['r2']:.5f} | — |"
    )
    for name in MODEL_SPECS:
        model = results["models"].get(name, {})
        if model.get("status") == "success":
            cv_best = min(model["group_cv_mae_by_alpha"].values())
            metrics = model["validation_metrics"]
            lines.append(
                f"| {name} | success | {cv_best:.5f} | {metrics['mae']:.5f} | "
                f"{metrics['rmse']:.5f} | {metrics['r2']:.5f} | {model['selected_alpha']:g} |"
            )
        else:
            lines.append(f"| {name} | {model.get('status', 'not_run')} | — | — | — | — | — |")

    joint = results["models"].get("driver_team_ridge", {})
    if joint.get("status") == "success":
        lines.extend(["", "## 2024 正则化速度效应", ""])
        drivers = joint["ratings"].get("drivers", [])
        teams = joint["ratings"].get("teams", [])
        if teams:
            lines.extend(
                [
                    "### 车队",
                    "",
                    "| 车队 | pace_advantage_pct |",
                    "| --- | ---: |",
                ]
            )
            for row in teams:
                lines.append(f"| {row['entity']} | {row['pace_advantage_pct']:.5f} |")
        if drivers:
            lines.extend(
                [
                    "",
                    "### 车手（前 10）",
                    "",
                    "| 车手 | pace_advantage_pct |",
                    "| --- | ---: |",
                ]
            )
            for row in drivers[:10]:
                lines.append(f"| {row['entity']} | {row['pace_advantage_pct']:.5f} |")

    lines.extend(
        [
            "",
            "## 解释限制",
            "",
            "- 2025 数据只用于最终验证，没有参与 alpha 或特征选择。",
            "- 当前目标是每个赛段内部归一化的回顾性相对圈速，不是赛前绝对圈速预测。",
            "- ridge 系数是强正则化条件关联；在车手与车队高度共线时不能直接解释为因果真值。",
            "- 2024 排位圈尚未规范化入库，因此本次没有排位能力结论。",
            "",
        ]
    )
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--reuse-data", action="store_true")
    parser.add_argument(
        "--legacy-postgres",
        action="store_true",
        help="仅用于复现旧基线；正式研究默认从 v4 Parquet 冻结",
    )
    args = parser.parse_args()

    config = load_json(args.config)
    run_id = f"{config['experiment_name']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    max_attempts = int(config["max_attempts"])
    append_log(
        {
            "run_id": run_id,
            "timestamp": now_iso(),
            "phase": "run",
            "method": "experiment_run",
            "status": "started",
            "config_hash": canonical_hash(config),
            "git_head": git_head(),
        }
    )

    if args.reuse_data:
        freeze_result = run_with_retries(
            run_id,
            "phase_0",
            "reuse_frozen_data_v1",
            max_attempts,
            lambda _attempt: load_frozen_data(config),
        )
    elif args.legacy_postgres:
        freeze_result = run_with_retries(
            run_id,
            "phase_0",
            "detailed_data_freeze_v1",
            max_attempts,
            lambda attempt: clean_and_freeze(
                *extract_raw_data(config, attempt, detailed=True),
                config=config,
                degraded=False,
            ),
        )
        if freeze_result.value is None:
            freeze_result = run_with_retries(
                run_id,
                "phase_0",
                "minimal_data_freeze_v1",
                max_attempts,
                lambda attempt: clean_and_freeze(
                    *extract_raw_data(config, attempt, detailed=False),
                    config=config,
                    degraded=True,
                ),
            )
    else:
        freeze_result = run_with_retries(
            run_id,
            "phase_0",
            "canonical_parquet_v4_freeze_v1",
            max_attempts,
            lambda _attempt: (
                v4_freeze.build_all(v4_freeze.DEFAULT_PARQUET_ROOT, config),
                load_frozen_data(config),
            )[1],
        )

    if freeze_result.value is None:
        append_log(
            {
                "run_id": run_id,
                "timestamp": now_iso(),
                "phase": "run",
                "method": "experiment_run",
                "status": "blocked_no_dataset",
            }
        )
        return 2

    train, validation, manifest = freeze_result.value
    train = train.reset_index(drop=True)
    validation = validation.reset_index(drop=True)
    y_validation = validation["target_log_pct"].to_numpy(dtype=float)
    zero_metrics = regression_metrics(y_validation, np.zeros(len(y_validation)))
    append_log(
        {
            "run_id": run_id,
            "timestamp": now_iso(),
            "phase": "phase_1",
            "method": "zero_relative_pace_baseline",
            "attempt": 1,
            "status": "success",
            "validation_mae": zero_metrics["mae"],
        }
    )

    model_results: dict[str, Any] = {}
    for name, spec in MODEL_SPECS.items():
        attempt_result = run_with_retries(
            run_id,
            str(spec["phase"]),
            name,
            max_attempts,
            lambda attempt, model_name=name: fit_model(
                model_name, train, validation, config, attempt
            ),
        )
        if attempt_result.value is None:
            model_results[name] = {
                "name": name,
                "phase": spec["phase"],
                "status": attempt_result.status,
                "attempts": attempt_result.attempts,
            }
        else:
            model_results[name] = attempt_result.value
            model_results[name]["attempts"] = attempt_result.attempts

    results = {
        "run_id": run_id,
        "created_at": now_iso(),
        "git_head": git_head(),
        "config": config,
        "config_hash": canonical_hash(config),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "postgresql_client": psycopg.__version__,
        },
        "data_manifest": manifest,
        "zero_baseline": {"validation_metrics": zero_metrics},
        "models": model_results,
    }
    atomic_json(RESULTS_PATH, results)
    write_summary(results)
    append_log(
        {
            "run_id": run_id,
            "timestamp": now_iso(),
            "phase": "run",
            "method": "experiment_run",
            "status": "completed",
            "successful_models": sum(
                1 for model in model_results.values() if model.get("status") == "success"
            ),
            "skipped_models": sum(
                1
                for model in model_results.values()
                if model.get("status") == "skipped_after_3_failures"
            ),
        }
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
