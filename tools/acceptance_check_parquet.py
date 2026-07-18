# -*- coding: utf-8 -*-
"""规范 Parquet 层最终验收：清单对账、schema/排序抽查、跨季查询。"""
from __future__ import annotations

import hashlib
import io
import json
import random
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(r"D:\vibe-coding\F1 TR\data\normalized\tracinginsights\schema=tracinginsights-expanded-v4")
random.seed(20260717)

EXPECTED_SORT = ["driver_number", "lap", "time"]
report: dict = {"years": {}, "schema_checks": [], "sort_checks": [], "sha_checks": [], "errors": []}

print("=" * 100)
print("A. 清单对账（manifest vs 磁盘实际）")
print("=" * 100)
grand_rows = grand_bytes = grand_parts = grand_src = grand_invalid = 0
ref_schema = None
for year_dir in sorted(ROOT.glob("year=*")):
    year = year_dir.name.split("=")[1]
    commits = sorted(year_dir.glob("commit=*"))
    if len(commits) != 1:
        report["errors"].append(f"{year}: 期望 1 个 commit 目录，实际 {len(commits)}")
    cdir = commits[0]
    manifest = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
    t = manifest["totals"]
    disk_files = sorted(cdir.rglob("telemetry.parquet"))
    disk_bytes = sum(p.stat().st_size for p in disk_files)
    # manifest 分区路径与磁盘一一对应
    man_paths = {item["parquet_path"] for item in manifest["partitions"]}
    disk_rel = {p.relative_to(cdir).as_posix() for p in disk_files}
    missing_on_disk = man_paths - disk_rel
    extra_on_disk = disk_rel - man_paths
    # 实际行数（读 parquet 元数据，不扫数据体）
    actual_rows = sum(pq.ParquetFile(p).metadata.num_rows for p in disk_files)
    sessions = sorted({item["session"] for item in manifest["partitions"]})
    ok = (
        t["partitions"] == len(disk_files)
        and t["rows"] == actual_rows
        and t["bytes"] == disk_bytes
        and not missing_on_disk
        and not extra_on_disk
    )
    status = "OK " if ok else "FAIL"
    print(
        f"[{status}] {year} commit={manifest['source_commit'][:12]} 分区={len(disk_files)}"
        f" 行数={actual_rows:,} (manifest {t['rows']:,})"
        f" 体积={disk_bytes/1e9:.2f}GB (manifest {t['bytes']/1e9:.2f}GB)"
        f" 源文件={t['source_files']:,} 无效={t['invalid_source_files']}"
    )
    print(f"       sessions: {sessions}")
    if missing_on_disk or extra_on_disk:
        print(f"       缺失分区={sorted(missing_on_disk)[:3]} 多余分区={sorted(extra_on_disk)[:3]}")
        report["errors"].append(f"{year}: 分区不对账")
    if not ok and t["rows"] != actual_rows:
        report["errors"].append(f"{year}: 行数不对账 manifest={t['rows']} actual={actual_rows}")
    report["years"][year] = {
        "partitions": len(disk_files), "rows": actual_rows, "bytes": disk_bytes,
        "sessions": sessions, "invalid_source_files": t["invalid_source_files"],
        "source_files": t["source_files"],
    }
    grand_rows += actual_rows; grand_bytes += disk_bytes; grand_parts += len(disk_files)
    grand_src += t["source_files"]; grand_invalid += t["invalid_source_files"]

    # B/C. 每年抽 3 个分区做 sha256 + schema + 排序检查
    manifest_by_path = {item["parquet_path"]: item for item in manifest["partitions"]}
    for p in random.sample(disk_files, min(3, len(disk_files))):
        rel = p.relative_to(cdir).as_posix()
        entry = manifest_by_path[rel]
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        sha_ok = digest.hexdigest() == entry["parquet_sha256"]
        report["sha_checks"].append({"file": f"{year}/{rel}", "ok": sha_ok})
        if not sha_ok:
            report["errors"].append(f"SHA256 不匹配: {year}/{rel}")

        pf = pq.ParquetFile(p)
        schema = pf.schema_arrow
        if ref_schema is None:
            ref_schema = schema
        schema_ok = schema.equals(ref_schema)
        meta = schema.metadata or {}
        sort_meta = meta.get(b"sort_order", b"").decode()
        f32 = all(str(schema.field(c).type) == "float" for c in ("speed", "throttle", "rpm", "x", "y", "z", "acc_x", "time"))
        report["schema_checks"].append({"file": f"{year}/{rel}", "schema_uniform": schema_ok, "float32": f32, "sort_meta": sort_meta})
        if not schema_ok:
            report["errors"].append(f"schema 与参考不一致: {year}/{rel}")
        if not f32:
            report["errors"].append(f"连续量不是 float32: {year}/{rel}")
        if sort_meta != "driver_number,lap,time":
            report["errors"].append(f"sort_order 元数据缺失/不符: {year}/{rel}")

        tbl = pf.read(columns=EXPECTED_SORT)
        drv = tbl["driver_number"].to_numpy()
        lap = tbl["lap"].to_numpy()
        tim = tbl["time"].to_numpy()
        key_prev = (drv[:-1].astype(np.int64) << 32) | lap[:-1].astype(np.int64)
        key_next = (drv[1:].astype(np.int64) << 32) | lap[1:].astype(np.int64)
        group_ok = bool(np.all(key_next >= key_prev))
        same = key_next == key_prev
        time_ok = bool(np.all(tim[1:][same] >= tim[:-1][same]))
        report["sort_checks"].append({"file": f"{year}/{rel}", "group_order_ok": group_ok, "time_order_ok": time_ok, "rows": len(drv)})
        if not (group_ok and time_ok):
            report["errors"].append(f"排序违规: {year}/{rel}")

print(f"\n总计: 分区={grand_parts} 行数={grand_rows:,} 体积={grand_bytes/1e9:.2f}GB 源文件={grand_src:,} 无效源文件={grand_invalid}")

print()
print("=" * 100)
print("B. schema / float32 / 排序抽查结果")
print("=" * 100)
for item in report["schema_checks"]:
    print(f"  schema_uniform={item['schema_uniform']} float32={item['float32']} sort_meta='{item['sort_meta']}'  {item['file']}")
for item in report["sort_checks"]:
    print(f"  group_ok={item['group_order_ok']} time_ok={item['time_order_ok']} rows={item['rows']:,}  {item['file']}")
for item in report["sha_checks"]:
    print(f"  sha256_ok={item['ok']}  {item['file']}")

print()
print("=" * 100)
print("C. DuckDB 跨季查询")
print("=" * 100)
import duckdb

con = duckdb.connect()
glob = (ROOT / "year=*" / "commit=*" / "session=*" / "meeting=*" / "telemetry.parquet").as_posix()
q1 = con.execute(
    f"""
    SELECT year, session, COUNT(DISTINCT meeting) meetings, COUNT(DISTINCT driver_number) drivers,
           COUNT(*) row_count
    FROM read_parquet('{glob}', union_by_name=false)
    GROUP BY 1,2 ORDER BY 1,2
    """
).fetchall()
print("按 年×赛段 的覆盖：")
for r in q1:
    print(f"  {r[0]} {r[1]:18s} meetings={r[2]:>2} drivers={r[3]:>2} rows={r[4]:>12,}")

q2 = con.execute(
    f"""
    SELECT year, MAX(speed) vmax, ROUND(AVG(speed),1) vavg,
           COUNT(DISTINCT meeting || '/' || session) sess_parts
    FROM read_parquet('{glob}')
    WHERE session='Race' GROUP BY 1 ORDER BY 1
    """
).fetchall()
print("\n正赛速度跨季对比（谓词下推验证）：")
for r in q2:
    print(f"  {r[0]} vmax={r[1]:.0f}km/h vavg={r[2]} race_meetings={r[3]}")

q3 = con.execute(
    f"""
    SELECT year, driver_acronym, MAX(speed) vmax
    FROM read_parquet('{glob}')
    WHERE session='Qualifying'
      AND (meeting LIKE '%Las_Vegas%' OR meeting LIKE '%Monza%')
    GROUP BY 1,2 ORDER BY vmax DESC LIMIT 5
    """
).fetchall()
print("\n排位赛尾速 Top5（验证非正赛数据可查）：")
for r in q3:
    print(f"  {r[0]} {r[1]} {r[2]:.0f} km/h")

report["cross_season"] = {"coverage": [list(r) for r in q1], "race_speed": [list(r) for r in q2]}

print()
verdict = "PASS" if not report["errors"] else "FAIL"
print(f"验收结论: {verdict}")
for e in report["errors"]:
    print(f"  !! {e}")

Path(r"D:\vibe-coding\F1 TR\data\logs\parquet-acceptance-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)
print(r"报告已写入 data\logs\parquet-acceptance-report.json")
