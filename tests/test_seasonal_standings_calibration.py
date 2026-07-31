"""三季积分榜校准层纯函数单测：全局赛历、rolling-split、转会图连通/割点、连通性统计、Spearman。

运行：python -m pytest tests/test_seasonal_standings_calibration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_seasonal_standings_calibration as mod  # noqa: E402


def _frame(rows):
    return pd.DataFrame(rows, columns=["year", "round_index", "driver_id", "team_name"])


def test_assign_global_round_monotone():
    frame = _frame([
        (2024, 2, "VER", "Red Bull Racing"),
        (2023, 1, "VER", "Red Bull Racing"),
        (2024, 1, "VER", "Red Bull Racing"),
    ])
    out = mod.assign_global_round(frame)
    gr = dict(zip(zip(out["year"], out["round_index"]), out["global_round"]))
    assert gr[(2023, 1)] == 1
    assert gr[(2024, 1)] == 2
    assert gr[(2024, 2)] == 3


def test_rolling_origin_splits():
    splits = mod.rolling_origin_splits([1, 2, 3, 4, 5], warm_start=3)
    assert splits == [(3, 4), (4, 5)]
    # train<=origin, predict=origin+1, 单调非重叠
    for origin, nxt in splits:
        assert nxt == origin + 1


def test_transfer_graph_connectivity_and_bridge():
    # HAM 从 Mercedes 转 Ferrari，桥接两队；移除后分裂
    rows = []
    for _ in range(6):
        rows += [
            (2024, 1, "RUS", "Mercedes"), (2024, 1, "HAM", "Mercedes"),
            (2025, 1, "HAM", "Ferrari"), (2025, 1, "LEC", "Ferrari"),
        ]
    frame = _frame(rows)
    nodes, edges = mod.build_transfer_graph(frame, min_laps=5)
    comps = mod.connected_components(nodes, edges)
    assert len(comps) == 1  # HAM 连通两队
    arts = mod.articulation_points(nodes, edges)
    assert "D:HAM" in arts  # HAM 是唯一桥接割点

    # 移除 HAM 的 Ferrari 边 -> 断成两分量
    frame2 = frame.loc[~((frame["driver_id"] == "HAM") & (frame["team_name"] == "Ferrari"))]
    nodes2, edges2 = mod.build_transfer_graph(frame2, min_laps=5)
    assert len(mod.connected_components(nodes2, edges2)) == 2


def test_min_laps_threshold_drops_thin_edges():
    rows = [(2024, 1, "VER", "Red Bull Racing")] * 3  # 只有 3 圈
    frame = _frame(rows)
    nodes, edges = mod.build_transfer_graph(frame, min_laps=5)
    assert not edges  # 低于阈值不连边


def test_driver_connectivity_stats_flags_single_team():
    rows = []
    for _ in range(6):
        rows += [
            (2024, 1, "SOLO", "Haas F1 Team"), (2024, 1, "MATE", "Haas F1 Team"),
            (2024, 1, "HAM", "Mercedes"), (2024, 1, "RUS", "Mercedes"),
            (2025, 1, "HAM", "Ferrari"),
        ]
    frame = _frame(rows)
    stats = {s["driver"]: s for s in mod.driver_connectivity_stats(frame, min_laps=5)}
    assert stats["HAM"]["distinct_teams"] == 2
    assert stats["HAM"]["weakly_anchored"] is False
    # SOLO 单队且有一个队友 -> weakly_anchored（teammates<=1）
    assert stats["SOLO"]["distinct_teams"] == 1
    assert stats["SOLO"]["weakly_anchored"] is True


def test_spearman_endpoints():
    assert mod.spearman([(1, 1), (2, 2), (3, 3)]) == 1.0
    assert mod.spearman([(1, 3), (2, 2), (3, 1)]) == -1.0
    assert mod.spearman([(1, 1)]) is None
