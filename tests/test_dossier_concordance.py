"""赛后一致性审计层与跨 Stint 可比分组的合成反例测试。

真实全量运行前必须先通过本文件的合成反例：包括"配速更快却无解释地
落后"的倒挂必须被标为 unexplained、策略/位置代理覆盖差距时必须判为
已解释、未完赛必须走退赛代理、不同配方或胎龄差过大的 Stint 不得进入
直接比较。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.dossier_concordance import (  # noqa: E402
    _kendall_tau_b,
    build_result_performance_concordance,
    build_season_concordance_summary,
    build_stint_comparison_table,
    derive_driver_classification_proxy,
    derive_event_incident_profile,
)


CLEAN_GATE = {
    "maximum_non_green_lap_fraction": 0.05,
    "maximum_field_retirements": 2,
    "maximum_unexplained_slow_lap_fraction": 0.02,
}
PAIR_RULES = {
    "context_cover_tolerance_s": 0.0,
    "pace_noise_floor_s_per_lap": 0.05,
}
COMPARISON_RULES = {
    "require_valid_status": True,
    "maximum_reference_tyre_age_gap_laps": 2.0,
    "maximum_phase_midpoint_fraction_gap": 0.2,
}
RACE_LAPS = 50


def _event_laps(
    specs: list[dict], race_laps: int = RACE_LAPS, green_fraction: float = 1.0
) -> pd.DataFrame:
    rows = []
    green_cutoff = int(race_laps * green_fraction)
    for spec in specs:
        laps = int(spec.get("laps", race_laps))
        for lap in range(1, laps + 1):
            rows.append(
                {
                    "driver_id": spec["driver"],
                    "team_name": spec["team"],
                    "lap_number": lap,
                    "position": spec["position"],
                    "all_green": lap <= green_cutoff,
                }
            )
    return pd.DataFrame(rows)


def _dossier(
    decomposition_rows: list[dict],
    race_lap_span: int = RACE_LAPS,
    team_order: list[dict] | None = None,
    proxy_comparison: list[dict] | None = None,
    stints: list[dict] | None = None,
) -> dict:
    return {
        "coverage": {"race_lap_span": race_lap_span},
        "vehicle_driver_decomposition": [
            {
                "driver": row["driver"],
                "team": row["team"],
                "observed_accounted_delta_s": (
                    row["pace"] + row["context"]
                ),
                "components": {
                    "vehicle_common_baseline_s": row["pace"],
                    "driver_realization_deviation_s": 0.0,
                    "strategy_track_position_proxy_s": row["context"],
                    "unexplained_residual_s": 0.0,
                },
            }
            for row in decomposition_rows
        ],
        "race_analysis": {
            "descriptive_team_order": team_order or [],
        },
        "result_impact_audit": {
            "classification_proxy_comparison": proxy_comparison or [],
        },
        "event_ledger": {
            "observed_laps": race_lap_span * max(
                len(decomposition_rows), 1
            ),
            "lap_category_counts": {"unexplained_slow_lap_proxy": 0},
        },
        "stint_dossiers": stints or [],
    }


def _stint(
    driver: str,
    number: int,
    compound: str,
    pace: float,
    age: float,
    lap_start: int,
    lap_end: int,
    *,
    team: str = "Team X",
    status: str = "valid",
    pace_status: str = "identified_conditional_proxy",
    interval: list[float] | None = None,
    slope: float | None = -0.05,
) -> dict:
    return {
        "driver": driver,
        "team": team,
        "stint_number": number,
        "compound": compound,
        "status": status,
        "lap_start": lap_start,
        "lap_end": lap_end,
        "representative_tyre_age_pace_s": pace,
        "representative_tyre_age_laps": age,
        "representative_tyre_age_pace_status": pace_status,
        "representative_tyre_age_pace_fuel_sensitivity_interval_s": (
            interval if interval is not None else [pace - 0.3, pace + 0.3]
        ),
        "degradation_s_per_tyre_lap": slope,
    }


def _classify(laps: pd.DataFrame) -> list[dict]:
    return derive_driver_classification_proxy(
        laps, classified_completion_fraction=0.9
    )


def _incident(laps: pd.DataFrame, dossier: dict) -> dict:
    return derive_event_incident_profile(
        laps, _classify(laps), dossier, clean_gate=CLEAN_GATE
    )


def test_classification_ranks_by_laps_then_position() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 1},
            {"driver": "BBB", "team": "T2", "position": 2},
            {"driver": "CCC", "team": "T3", "position": 5, "laps": 20},
        ]
    )
    rows = _classify(laps)
    ranks = {row["driver"]: row["classification_rank_proxy"] for row in rows}
    assert ranks == {"AAA": 1, "BBB": 2, "CCC": 3}
    classified = {row["driver"]: row["classified_proxy"] for row in rows}
    assert classified["AAA"] and classified["BBB"]
    assert not classified["CCC"]


def test_incident_profile_clean_and_dirty() -> None:
    specs = [
        {"driver": "AAA", "team": "T1", "position": 1},
        {"driver": "BBB", "team": "T2", "position": 2},
    ]
    dossier = _dossier([])
    clean = _incident(_event_laps(specs), dossier)
    assert clean["clean_race_proxy"]
    dirty = _incident(_event_laps(specs, green_fraction=0.8), dossier)
    assert not dirty["clean_race_proxy"]
    assert "non_green_fraction_above_gate" in dirty["not_clean_reasons"]


def _concordance(
    laps: pd.DataFrame, dossier: dict
) -> dict:
    classification = _classify(laps)
    incident = derive_event_incident_profile(
        laps, classification, dossier, clean_gate=CLEAN_GATE
    )
    return build_result_performance_concordance(
        dossier, classification, incident, pair_rules=PAIR_RULES
    )


def test_clean_race_concordant() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 1},
            {"driver": "BBB", "team": "T2", "position": 2},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    assert result["status"] == "computed"
    assert result["verdict"] == "clean_race_fully_concordant"
    assert result["unexplained_inversion_count"] == 0
    assert result["kendall_tau_pace_vs_classification"] == 1.0


def test_inversion_explained_by_context_proxy() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 2},
            {"driver": "BBB", "team": "T2", "position": 1},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 40.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    assert result["verdict"] == "clean_race_fully_concordant"
    assert result["explained_inversion_count"] == 1
    pair = result["pairs"][0]
    assert (
        pair["primary_reason"]
        == "strategy_track_position_proxy_covers_margin"
    )


def test_unexplained_inversion_is_flagged() -> None:
    """核心合成反例：配速快 30s、无任何解释却落后，必须被点名。"""

    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 2},
            {"driver": "BBB", "team": "T2", "position": 1},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    assert result["verdict"] == "clean_race_unexplained_mismatch"
    assert result["unexplained_inversion_count"] == 1
    pair = result["unexplained_pairs"][0]
    assert pair["pace_faster_driver"] == "AAA"
    assert pair["primary_reason"] == "unexplained_inversion"


def test_retirement_inversion_uses_retired_reason() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 8, "laps": 20},
            {"driver": "BBB", "team": "T2", "position": 1},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    pair = result["pairs"][0]
    assert pair["result_inverted"]
    assert pair["primary_reason"] == "not_classified_or_retired_proxy"
    assert result["unexplained_inversion_count"] == 0


def test_small_margin_inversion_within_noise_floor() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 2},
            {"driver": "BBB", "team": "T2", "position": 1},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -2.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    pair = result["pairs"][0]
    assert pair["primary_reason"] == "within_pace_noise_floor"
    assert result["verdict"] == "clean_race_fully_concordant"


def test_incident_race_is_descriptive_only() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 2},
            {"driver": "BBB", "team": "T2", "position": 1},
        ],
        green_fraction=0.7,
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ]
    )
    result = _concordance(laps, dossier)
    assert result["verdict"] == "incident_race_descriptive_only"
    assert result["unexplained_inversion_count"] == 1


def test_team_rank_interval_annotation() -> None:
    laps = _event_laps(
        [
            {"driver": "AAA", "team": "T1", "position": 1},
            {"driver": "BBB", "team": "T2", "position": 2},
        ]
    )
    dossier = _dossier(
        [
            {"driver": "AAA", "team": "T1", "pace": -30.0, "context": 0.0},
            {"driver": "BBB", "team": "T2", "pace": 0.0, "context": 0.0},
        ],
        team_order=[
            {"team": "T1", "rank": 1, "rank_interval_80": [1, 2]},
            {"team": "T2", "rank": 2, "rank_interval_80": [1, 2]},
        ],
        proxy_comparison=[
            {
                "team": "T1",
                "performance_proxy_rank": 1,
                "classification_proxy_rank": 2,
                "rank_difference": 1,
            },
            {
                "team": "T2",
                "performance_proxy_rank": 2,
                "classification_proxy_rank": 3,
                "rank_difference": 1,
            },
        ],
    )
    result = _concordance(laps, dossier)
    annotated = {
        row["team"]: row["classification_within_pace_rank_interval_80"]
        for row in result["team_comparison"]
    }
    assert annotated["T1"] is True
    assert annotated["T2"] is False


def test_stint_comparison_gates() -> None:
    stints = [
        _stint("AAA", 1, "MEDIUM", 90.0, 10.0, 1, 20),
        _stint("BBB", 1, "MEDIUM", 90.6, 11.5, 2, 21, team="Team Y"),
        _stint("CCC", 1, "MEDIUM", 89.0, 14.0, 3, 22, team="Team Z"),
        _stint("DDD", 1, "HARD", 90.1, 10.0, 1, 20, team="Team W"),
        _stint(
            "EEE",
            1,
            "MEDIUM",
            90.2,
            10.5,
            35,
            54,
            team="Team V",
        ),
        _stint(
            "FFF",
            1,
            "MEDIUM",
            90.3,
            10.0,
            1,
            20,
            team="Team U",
            status="audit_only",
        ),
    ]
    dossier = _dossier([], stints=stints)
    table = build_stint_comparison_table(
        dossier, comparison_rules=COMPARISON_RULES
    )
    assert table["status"] == "computed"
    pair_keys = {
        (pair["first_driver"], pair["second_driver"])
        for pair in table["qualified_pairs"]
    }
    assert pair_keys == {("AAA", "BBB")}
    assert table["excluded_stint_counts"]["status_not_valid"] == 1
    pair = table["qualified_pairs"][0]
    assert pair["reference_pace_delta_s_first_minus_second"] == pytest.approx(
        -0.6
    )
    assert pair["fuel_scenario_sign_stable"] is False


def test_stint_comparison_fuel_sign_stability() -> None:
    stints = [
        _stint("AAA", 1, "SOFT", 90.0, 5.0, 1, 15, interval=[89.8, 90.2]),
        _stint(
            "BBB",
            1,
            "SOFT",
            91.5,
            5.5,
            2,
            16,
            team="Team Y",
            interval=[91.3, 91.7],
        ),
    ]
    dossier = _dossier([], stints=stints)
    table = build_stint_comparison_table(
        dossier, comparison_rules=COMPARISON_RULES
    )
    pair = table["qualified_pairs"][0]
    assert pair["fuel_scenario_sign_stable"] is True


def test_stint_comparison_rejects_pre_v16_schema() -> None:
    dossier = _dossier(
        [],
        stints=[
            {
                "driver": "AAA",
                "team": "T1",
                "stint_number": 1,
                "compound": "SOFT",
                "status": "valid",
                "lap_start": 1,
                "lap_end": 20,
            }
        ],
    )
    table = build_stint_comparison_table(
        dossier, comparison_rules=COMPARISON_RULES
    )
    assert table["status"] == "not_available_schema_before_v16"


def test_season_summary_aggregation() -> None:
    def _event_report(
        year: int,
        meeting: str,
        clean: bool,
        unexplained: int,
    ) -> dict:
        return {
            "scope": {"year": year, "meeting": meeting},
            "incident_profile": {"clean_race_proxy": clean},
            "concordance": {
                "status": "computed",
                "verdict": (
                    "incident_race_descriptive_only"
                    if not clean
                    else (
                        "clean_race_fully_concordant"
                        if unexplained == 0
                        else "clean_race_unexplained_mismatch"
                    )
                ),
                "unexplained_inversion_count": unexplained,
                "unexplained_pairs": [
                    {
                        "pace_faster_driver": "AAA",
                        "pace_slower_driver": "BBB",
                        "pace_margin_s": 10.0,
                        "context_delta_s": 0.0,
                        "result_inverted": True,
                        "primary_reason": "unexplained_inversion",
                        "applicable_reasons": [],
                    }
                ]
                * unexplained,
                "kendall_tau_pace_vs_classification": 0.8,
            },
        }

    summary = build_season_concordance_summary(
        [
            _event_report(2025, "A", True, 0),
            _event_report(2025, "B", True, 1),
            _event_report(2025, "C", False, 2),
        ]
    )
    assert summary["events_with_concordance"] == 3
    assert summary["clean_race_count"] == 2
    assert summary["clean_race_fully_concordant_count"] == 1
    assert summary["clean_race_unexplained_mismatch_count"] == 1
    assert summary["unexplained_inversions_total"] == 3
    assert summary["clean_race_unexplained_mismatch_events"] == [
        {
            "year": 2025,
            "meeting": "B",
            "unexplained_inversion_count": 1,
        }
    ]


def test_kendall_tau_b_direction() -> None:
    assert _kendall_tau_b([1, 2, 3], [1, 2, 3]) == 1.0
    assert _kendall_tau_b([1, 2, 3], [3, 2, 1]) == -1.0
    assert _kendall_tau_b([1], [1]) is None
