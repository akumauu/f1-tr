"""车辆能力包络原型的纯函数单测：Δt 恒等式、重采样、分段、包络与发挥映射。

运行：python -m pytest tests/test_car_driver_envelope.py
（对齐 tests/test_v4_frozen_data.py：把 research/ 加入 sys.path 后导入模块。）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_car_driver_envelope as mod  # noqa: E402


def test_integrate_constant_speed_lap():
    # 匀速 180 km/h 跑 1000 m，时间 = 1000 / (180/3.6) = 20 s
    grid = np.linspace(0.0, 1000.0, 501)
    speed = np.full_like(grid, 180.0)
    assert abs(mod.integrate_lap_time(speed, grid) - 20.0) < 1e-6


def test_segment_delta_additivity_and_zero():
    grid = np.linspace(0.0, 2000.0, 300)
    rng = np.random.default_rng(0)
    speed_a = 200.0 + rng.normal(0, 10, grid.shape)
    speed_b = 200.0 + rng.normal(0, 10, grid.shape)
    per_segment, total = mod.segment_delta_time(speed_a, speed_b, grid)
    # 恒等式：逐段差之和 == 总差
    assert abs(per_segment.sum() - total) < 1e-9
    # 相同速度 -> 零差
    per_segment_zero, total_zero = mod.segment_delta_time(speed_a, speed_a, grid)
    assert abs(total_zero) < 1e-9
    assert np.allclose(per_segment_zero, 0.0)


def test_segment_delta_matches_integrated_difference():
    grid = np.linspace(0.0, 1500.0, 400)
    speed_a = np.full_like(grid, 210.0)
    speed_b = np.full_like(grid, 190.0)
    _, total = mod.segment_delta_time(speed_a, speed_b, grid)
    diff = mod.integrate_lap_time(speed_a, grid) - mod.integrate_lap_time(speed_b, grid)
    assert abs(total - diff) < 1e-9


def test_sector_times_sum_to_lap_time():
    grid = np.linspace(0.0, 4000.0, 1000)
    rng = np.random.default_rng(1)
    speed = 150.0 + 100.0 * rng.random(grid.shape)
    n_sectors = 25
    sector_ids = mod.sector_ids_for_grid(grid, n_sectors)
    sector_times = mod.lap_sector_times(speed, grid, sector_ids, n_sectors)
    assert sector_times.shape == (n_sectors,)
    assert abs(sector_times.sum() - mod.integrate_lap_time(speed, grid)) < 1e-9


def test_resample_continuous_and_discrete():
    distance = np.array([0.0, 10.0, 20.0, 30.0])
    grid = np.array([0.0, 15.0, 30.0])
    # 连续：线性插值
    cont = mod.resample_lap(distance, np.array([0.0, 10.0, 20.0, 30.0]), grid)
    assert np.allclose(cont, [0.0, 15.0, 30.0])
    # 离散：前向保持（15 处取距离<=15 的最后一个样本，即 index 1 -> 值 1）
    disc = mod.resample_lap(distance, np.array([0, 1, 0, 1]), grid, discrete=True)
    assert disc[0] == 0 and disc[1] == 1 and disc[2] == 1


def test_resample_handles_unsorted_and_duplicates():
    distance = np.array([20.0, 0.0, 10.0, 10.0])
    values = np.array([2.0, 0.0, 1.0, 1.0])
    grid = np.array([5.0, 15.0])
    out = mod.resample_lap(distance, values, grid)
    assert np.allclose(out, [0.5, 1.5])


def test_car_envelope_ceiling_faster_than_floor():
    # 构造两名车手 sector 时间矩阵：ceiling(低分位)应小于 floor(高分位)
    rng = np.random.default_rng(2)
    a = 1.0 + rng.random((20, 4)) * 0.2
    b = 1.05 + rng.random((20, 4)) * 0.2
    config = {"ceiling_sector_quantile": 0.10, "floor_sector_quantile": 0.60}
    env = mod.car_envelope({"A": a, "B": b}, config)
    assert np.all(env["ceiling_frontier_p90"] <= env["floor"])
    # 拼接上限（每 sector 取两车最好）应 <= 池化前沿或与之接近，但不慢于任一车单独最优
    assert np.all(env["ceiling_ideal_lap_splice"] <= np.minimum(
        env["per_driver_best"]["A"], env["per_driver_best"]["B"]) + 1e-9)


def test_driver_extraction_endpoints():
    ceiling = np.array([1.0, 1.0])
    floor = np.array([1.2, 1.2])
    # 车手低油最优==上限 -> 100%（单圈时 P10 即该圈本身）
    at_ceiling = mod.driver_extraction(np.array([[1.0, 1.0]]), ceiling, floor, 0.10)
    assert np.allclose(at_ceiling["extraction_pct"], 100.0)
    # 车手低油最优==下限 -> 0%
    at_floor = mod.driver_extraction(np.array([[1.2, 1.2]]), ceiling, floor, 0.10)
    assert np.allclose(at_floor["extraction_pct"], 0.0)
    # 发挥%基于低油最优(P10)，不是中位：给多圈时应取更快端
    multi = mod.driver_extraction(
        np.array([[1.0, 1.0], [1.2, 1.2], [1.1, 1.1]]), ceiling, floor, 0.0)
    assert np.allclose(multi["extraction_pct"], 100.0)


def test_corner_type_labels_partition():
    grid = np.linspace(0.0, 1000.0, 101)
    # 一半低速一半高速的参照速度
    ref = np.concatenate([np.full(50, 100.0), np.full(51, 300.0)])
    n_sectors = 10
    sector_ids = mod.sector_ids_for_grid(grid, n_sectors)
    config = {
        "corner_speed_bins_kmh": {"low_max": 130.0, "medium_max": 210.0},
        "cleaning": {"full_throttle_threshold_pct": 95.0},
    }
    labels = mod.corner_type_labels(ref, grid, sector_ids, n_sectors, config)
    assert len(labels) == n_sectors
    assert "low_speed_corner" in labels
    assert "straight_or_flat" in labels or "high_speed_corner" in labels
