#!/usr/bin/env python3
"""清理已由 v4/快照替代的本地大体积数据，并输出审计报告。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "logs" / "storage-cleanup-report.json"
WORKSPACE_TARGETS = (
    ROOT / "data" / "normalized" / "tracinginsights" / "schema=tracinginsights-expanded-v1",
    ROOT / "data" / "normalized" / "tracinginsights" / "schema=tracinginsights-expanded-v2",
    ROOT / "data" / "normalized" / "tracinginsights" / "schema=tracinginsights-expanded-v3",
    ROOT / "research" / "cache" / "smoke-2023",
    ROOT / "research" / "cache" / "canonical-v4-smoke",
    ROOT / "research" / "cache" / "acceptance-20260717",
)
EXTERNAL_TARGETS = (
    Path(r"D:\vibe-coding\_db_check_tmp.py"),
    Path(r"D:\vibe-coding\_inventory_tmp.py"),
)
CANONICAL_V4 = (
    ROOT
    / "data"
    / "normalized"
    / "tracinginsights"
    / "schema=tracinginsights-expanded-v4"
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = ROOT / ".env"
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    raise RuntimeError("缺少 DATABASE_URL")


def path_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def validate_target(path: Path) -> Path:
    resolved = path.resolve()
    root = ROOT.resolve()
    allowed_external = {item.resolve() for item in EXTERNAL_TARGETS}
    if resolved == CANONICAL_V4.resolve() or CANONICAL_V4.resolve() in resolved.parents:
        raise RuntimeError(f"拒绝删除 v4 规范层: {resolved}")
    if root not in resolved.parents and resolved not in allowed_external:
        raise RuntimeError(f"拒绝删除白名单外路径: {resolved}")
    if resolved not in {item.resolve() for item in WORKSPACE_TARGETS + EXTERNAL_TARGETS}:
        raise RuntimeError(f"拒绝删除未登记路径: {resolved}")
    return resolved


def cleanup_files(apply: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for configured in WORKSPACE_TARGETS + EXTERNAL_TARGETS:
        target = validate_target(configured)
        size = path_bytes(target)
        existed = target.exists()
        if apply and existed:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        rows.append(
            {
                "path": str(target),
                "existed": existed,
                "bytes": size,
                "deleted": bool(apply and existed and not target.exists()),
            }
        )
    return {
        "apply": apply,
        "targets": rows,
        "bytes_reclaimed": sum(row["bytes"] for row in rows if row["deleted"]),
        "canonical_v4_preserved": CANONICAL_V4.is_dir(),
    }


SIZE_SQL = """
SELECT current_database() AS database,
       pg_database_size(current_database()) AS database_bytes,
       c.relname,
       c.reltuples::bigint AS estimated_rows,
       pg_total_relation_size(c.oid) AS total_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('telemetry_car_data', 'telemetry_location', 'f1_api_snapshots')
ORDER BY c.relname
"""


def fetch_dicts(conn: psycopg.Connection[Any], sql: str) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql)
        columns = [description.name for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def postgres_state(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    sizes = fetch_dicts(conn, SIZE_SQL)
    snapshots = fetch_dicts(
        conn,
        """
        SELECT endpoint, COUNT(*) AS snapshots, SUM(record_count) AS records
        FROM f1_api_snapshots
        WHERE provider = 'openf1' AND status = 'ok'
          AND endpoint IN ('car_data', 'location')
        GROUP BY endpoint ORDER BY endpoint
        """,
    )
    rows = fetch_dicts(
        conn,
        """
        SELECT 'telemetry_car_data' AS table_name, COUNT(*) AS rows FROM telemetry_car_data
        UNION ALL
        SELECT 'telemetry_location', COUNT(*) FROM telemetry_location
        """,
    )
    gin = fetch_dicts(
        conn,
        """
        SELECT to_regclass('public.idx_f1_api_snapshots_payload_gin') IS NOT NULL AS exists,
               COALESCE(pg_relation_size(to_regclass('public.idx_f1_api_snapshots_payload_gin')), 0) AS bytes
        """,
    )[0]
    return {"sizes": sizes, "exact_rows": rows, "openf1_snapshots": snapshots, "payload_gin": gin}


def cleanup_postgres(apply: bool) -> dict[str, Any]:
    with psycopg.connect(load_database_url()) as conn:
        before = postgres_state(conn)
        coverage = {row["endpoint"]: int(row["records"] or 0) for row in before["openf1_snapshots"]}
        if coverage.get("car_data", 0) == 0 or coverage.get("location", 0) == 0:
            raise RuntimeError("OpenF1 car_data/location 快照覆盖为空，拒绝清空 PG 遥测")
        if apply:
            with conn.transaction():
                conn.execute("LOCK TABLE telemetry_car_data, telemetry_location IN ACCESS EXCLUSIVE MODE")
                conn.execute("TRUNCATE TABLE telemetry_car_data, telemetry_location")
                conn.execute("DROP INDEX IF EXISTS idx_f1_api_snapshots_payload_gin")
            conn.execute("ANALYZE telemetry_car_data")
            conn.execute("ANALYZE telemetry_location")
            conn.commit()
        after = postgres_state(conn)
    return {"apply": apply, "before": before, "after": after}


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-files", action="store_true")
    parser.add_argument("--apply-postgres", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    report = {
        "created_at": now_iso(),
        "policy": {
            "telemetry_canonical_source": "tracinginsights-expanded-v4 Parquet",
            "openf1_recovery_source": "f1_api_snapshots",
            "postgres_telemetry_role": "empty optional rebuild cache; not a product source",
        },
        "filesystem": cleanup_files(args.apply_files),
        "postgres": cleanup_postgres(args.apply_postgres),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
