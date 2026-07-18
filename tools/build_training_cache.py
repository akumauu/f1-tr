#!/usr/bin/env python3
"""从规范 Parquet 派生可丢弃的定长训练缓存。"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.compute as pc
import pyarrow.dataset as ds


CONTINUOUS = ["speed", "throttle", "rpm", "x", "y", "z", "acc_x", "acc_y", "acc_z"]
DISCRETE = ["brake", "gear", "drs"]
GROUP = ["year", "meeting", "session", "driver_number", "lap"]


def main() -> int:
    parser = argparse.ArgumentParser(description="从 Parquet 构建实验专用 NPZ 训练缓存")
    parser.add_argument("--parquet-root", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--meeting", required=True, help="必须限定比赛，避免误载整季到内存")
    parser.add_argument("--session", required=True)
    parser.add_argument("--drivers", help="车手编号，逗号分隔")
    parser.add_argument("--laps", help="圈号，逗号分隔")
    parser.add_argument("--grid", choices=("distance", "time"), default="distance")
    parser.add_argument("--points", type=int, default=1024)
    parser.add_argument("--features", default=",".join(CONTINUOUS + DISCRETE))
    parser.add_argument("--out", default="research/cache")
    args = parser.parse_args()
    if args.points < 2:
        parser.error("--points 必须至少为 2")

    drivers = parse_ints(args.drivers)
    laps = parse_ints(args.laps)
    features = [value.strip() for value in args.features.split(",") if value.strip()]
    allowed = set(CONTINUOUS + DISCRETE)
    if unknown := sorted(set(features) - allowed):
        parser.error(f"不支持的特征: {unknown}")

    config = {
        "parquet_root": Path(args.parquet_root).as_posix(),
        "meeting": args.meeting,
        "session": args.session,
        "drivers": drivers,
        "laps": laps,
        "grid": args.grid,
        "points": args.points,
        "features": features,
    }
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    output_dir = Path(args.out).resolve() / safe_name(args.experiment) / config_hash
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "sequences.npz"
    if output_path.exists():
        raise FileExistsError(f"缓存已存在，拒绝覆盖: {output_path}")

    # 分区键同时存在于文件 schema；不让 Hive 推断出的 int32/string 覆盖规范类型。
    dataset = ds.dataset(args.parquet_root, format="parquet", exclude_invalid_files=True)
    predicate = (ds.field("meeting") == args.meeting) & (ds.field("session") == args.session)
    if drivers:
        predicate &= ds.field("driver_number").isin(drivers)
    if laps:
        predicate &= ds.field("lap").isin(laps)
    axis = "distance" if args.grid == "distance" else "time"
    columns = GROUP + [axis] + features
    table = dataset.to_table(columns=list(dict.fromkeys(columns)), filter=predicate)
    if table.num_rows == 0:
        raise ValueError("筛选结果为空")
    indices = pc.sort_indices(table, sort_keys=[(key, "ascending") for key in GROUP + [axis]])
    table = pc.take(table, indices).combine_chunks()

    arrays = {name: table[name].to_numpy(zero_copy_only=False) for name in table.column_names}
    boundaries = group_boundaries(arrays, table.num_rows)
    sequences: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    labels: list[tuple[Any, ...]] = []
    skipped: list[dict[str, Any]] = []
    for start, end in boundaries:
        label = tuple(value[start] for value in (arrays[key] for key in GROUP))
        source_axis = np.asarray(arrays[axis][start:end], dtype=np.float64)
        valid = np.isfinite(source_axis)
        source_axis = source_axis[valid]
        if source_axis.size < 2:
            skipped.append({"group": list(label), "reason": "有效网格点少于 2"})
            continue
        unique_axis, unique_index = np.unique(source_axis, return_index=True)
        if unique_axis.size < 2:
            skipped.append({"group": list(label), "reason": "网格坐标不递增"})
            continue
        target = np.linspace(unique_axis[0], unique_axis[-1], args.points, dtype=np.float32)
        feature_columns: list[np.ndarray] = []
        sample_mask = np.ones(args.points, dtype=np.bool_)
        for feature in features:
            values = np.asarray(arrays[feature][start:end])[valid][unique_index]
            numeric = np.asarray([np.nan if value is None else value for value in values], dtype=np.float64)
            finite = np.isfinite(numeric)
            if finite.sum() < 2:
                feature_columns.append(np.full(args.points, np.nan, dtype=np.float32))
                sample_mask[:] = False
                continue
            if feature in DISCRETE:
                # 离散控制量使用前向保持，避免对挡位/DRS 做数值插值。
                positions = np.searchsorted(unique_axis[finite], target, side="right") - 1
                positions = np.clip(positions, 0, finite.sum() - 1)
                result = numeric[finite][positions]
            else:
                result = np.interp(target, unique_axis[finite], numeric[finite])
            feature_columns.append(result.astype(np.float32))
        sequences.append(np.stack(feature_columns, axis=1))
        masks.append(sample_mask)
        labels.append(label)

    if not sequences:
        raise ValueError("没有可生成的训练序列")
    np.savez_compressed(
        output_path,
        X=np.stack(sequences),
        mask=np.stack(masks),
        groups=np.asarray(labels, dtype=str),
        features=np.asarray(features),
        grid=np.asarray(args.grid),
    )
    manifest = {
        "cache_role": "derived_disposable_training_cache",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_hash": config_hash,
        "config": config,
        "shape": list(np.stack(sequences).shape),
        "groups_written": len(sequences),
        "groups_skipped": skipped,
        "output": output_path.name,
        "sha256": sha256_file(output_path),
        "rebuild_rule": "删除后必须从规范 Parquet 重建，禁止回写主仓",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def group_boundaries(arrays: dict[str, np.ndarray], rows: int) -> list[tuple[int, int]]:
    if rows == 0:
        return []
    changed = np.zeros(rows, dtype=np.bool_)
    changed[0] = True
    for key in GROUP:
        changed[1:] |= arrays[key][1:] != arrays[key][:-1]
    starts = np.flatnonzero(changed)
    ends = np.r_[starts[1:], rows]
    return list(zip(starts.tolist(), ends.tolist()))


def parse_ints(value: str | None) -> list[int] | None:
    return [int(item.strip()) for item in value.split(",") if item.strip()] if value else None


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
