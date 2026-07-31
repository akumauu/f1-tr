"""2026 Ferrari 四站批处理的轻量合同测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import run_2026_telemetry_batch as batch  # noqa: E402


def _batch_config() -> dict:
    return json.loads(
        (
            ROOT
            / "research"
            / "configs"
            / "telemetry_explanation_2026_ferrari_four_event_v7.json"
        ).read_text(encoding="utf-8")
    )


def _event_report(
    meeting: str,
    *,
    matched_pairs: int,
    gap: float | None,
    anomalies: int,
    ham_mode: str = "balanced_mode",
    lec_mode: str = "late_gain_mode",
) -> dict:
    return {
        "scope": {
            "meeting": meeting,
            "session": "Race",
            "team": "Ferrari",
        },
        "release_status": "exploratory_2026_rules_reset_insufficient_season_exposure",
        "data_identity": {"schema": "tracinginsights-expanded-v5"},
        "data_quality": {
            "sample_rows": 12000,
            "driver_laps": 100,
            "eligible_clean_laps": 64,
            "team_clean_laps": 20,
            "field_reference_laps": 30,
            "segment_coverage": 0.98,
            "response_baseline_coverage": 0.92,
            "raw_state_preserved": True,
            "raw_state_informative": False,
            "absolute_time_preserved": True,
            "limitations": ["合成质量字段，仅用于聚合测试。"],
        },
        "team_difference": {
            "orientation": "HAM - LEC",
            "comparability": {
                "status": "comparable" if matched_pairs >= 3 else "not_comparable",
                "matched_pairs": matched_pairs,
            },
            "anomaly_episodes": [{"id": index} for index in range(anomalies)],
        },
        "race_value_ledger": {
            "target_minus_reference_median_s_per_comparable_lap": gap,
            "interval_80_s_per_comparable_lap": (
                [gap - 0.01, gap + 0.01] if gap is not None else [None, None]
            ),
            "delta_expected_points": None,
        },
        "strategy_mode_fingerprints": [
            {
                "driver_acronym": "HAM",
                "status": "ok",
                "mode_label": ham_mode,
            },
            {
                "driver_acronym": "LEC",
                "status": "ok",
                "mode_label": lec_mode,
            },
        ],
    }


def test_event_config_only_overrides_event_scope_and_outputs():
    config = _batch_config()
    base_config = json.loads(
        (ROOT / config["base_config"]).read_text(encoding="utf-8")
    )
    china = config["events"][1]
    event_config = batch.build_event_config(base_config, config, china)
    assert event_config["scope"] == {
        "year": 2026,
        "meeting": "Chinese Grand Prix",
        "meeting_partition": "Chinese_Grand_Prix",
        "session": "Race",
        "team": "Ferrari",
        "target_driver": "HAM",
        "reference_driver": "LEC",
    }
    assert event_config["telemetry"]["raw_corners"].endswith(
        "Chinese Grand Prix/Race/corners.json"
    )
    assert event_config["outputs"]["result"].endswith(
        "telemetry_explanation_2026_china_ferrari_v7.json"
    )
    assert base_config["scope"]["meeting"] == "Australian Grand Prix"


def test_aggregate_reports_keeps_physical_evidence_and_null_release_scores():
    reports = [
        _event_report("Australian Grand Prix", matched_pairs=8, gap=0.12, anomalies=1),
        _event_report("Chinese Grand Prix", matched_pairs=6, gap=-0.04, anomalies=0),
        _event_report("Japanese Grand Prix", matched_pairs=2, gap=None, anomalies=0),
        _event_report(
            "Miami Grand Prix",
            matched_pairs=5,
            gap=0.08,
            anomalies=2,
            ham_mode="save_like_mode",
        ),
    ]
    result = batch.aggregate_reports(reports, _batch_config())
    gate = result["confirmatory_exposure_gate"]
    assert gate == {
        "observed_events": 4,
        "quality_qualified_events": 3,
        "required_events": 16,
        "passed": False,
        "status": "exploratory_only",
    }
    assert result["physical_gap_summary"][
        "median_of_event_medians_s_per_comparable_lap"
    ] == pytest.approx(0.08)
    assert result["events"][0]["matched_pairs"] == 8
    assert result["events"][2]["comparability"] == "not_comparable"
    assert result["events"][3]["anomaly_trigger_count"] == 2
    assert result["events"][3]["strategy_main_modes"]["HAM"] == "save_like_mode"
    assert result["events"][0]["data_schema"] == "tracinginsights-expanded-v5"
    assert result["events"][0]["data_quality"]["segment_coverage"] == pytest.approx(
        0.98
    )
    assert result["season_rating_outputs"]["ability_ovr"] is None
    assert result["season_rating_outputs"]["event_delivery"] is None
    assert result["season_rating_outputs"]["delta_expected_points"] is None
    assert all(event["ability_ovr"] is None for event in result["events"])
    assert all(event["event_delivery"] is None for event in result["events"])
    assert all(event["delta_expected_points"] is None for event in result["events"])
    assert result["result_label_audit"]["result_labels_used"] is False
    assert result["result_label_audit"][
        "classification_or_points_fields_consumed"
    ] == []


def test_markdown_states_exploratory_boundary_and_quality():
    reports = [
        _event_report(name, matched_pairs=4, gap=0.01, anomalies=0)
        for name in (
            "Australian Grand Prix",
            "Chinese Grand Prix",
            "Japanese Grand Prix",
            "Miami Grand Prix",
        )
    ]
    markdown = batch.batch_summary(
        batch.aggregate_reports(reports, _batch_config())
    )
    assert "4/16" in markdown
    assert "OVR" in markdown
    assert "Event Delivery" in markdown
    assert "期望积分均为空" in markdown
    assert "不使用完赛名次、积分或年度排名标签" in markdown
    assert "赛段覆盖" in markdown
    assert "tracinginsights-expanded-v5" in markdown
