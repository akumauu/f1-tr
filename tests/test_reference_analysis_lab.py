"""参考分析复刻实验室 v1 的计算函数与反例测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from reference_analysis_lab.f1pace_pilot import (  # noqa: E402
    build_lap_table,
    build_pairwise_mean_delta,
    build_pace_stats,
    traffic_lap_rows,
)
from reference_analysis_lab.deltadata_pilot import (  # noqa: E402
    kish_effective_sample_size,
    weighted_mean,
)
from reference_analysis_lab.fdataanalysis_pilot import (  # noqa: E402
    build_straight_proxies,
    detect_distance_corner_proxies,
    linear_slope,
)
from reference_analysis_lab.f1pace_reverse_engineered_v2 import (  # noqa: E402
    REFERENCE_GREEN_INTERCEPT_PCT,
    REFERENCE_GREEN_SLOPE_PCT,
    SURROGATE_FEATURE_COLUMNS,
    _threshold_label,
    _weighted_ratio,
    build_time_headway_features,
    extract_reference_traffic_grid,
    fit_surrogate,
    predict_surrogate,
)
from reference_analysis_lab.deltadata_reverse_engineered_v2 import (  # noqa: E402
    PUBLIC_TRAFFIC_RATIO_MAX,
    build_teammate_comparisons as build_deltadata_v2_teammate_comparisons,
    fit_robust_condition_model,
    predict_condition_model,
    prepare_clean_air_laps,
    reference_benchmark_metrics,
)
from reference_analysis_lab.fdataanalysis_reverse_engineered_v2 import (  # noqa: E402
    detect_dynamic_corner_proxies,
    integrate_speed_trace_seconds,
    robust_theil_sen_slope,
    segmentation_stability,
    top_n_mean,
)
from reference_analysis_lab.f1telemetrydata_reverse_engineered_v1 import (  # noqa: E402
    assign_qualifying_phase,
    build_phase_windows,
    build_pit_summary,
    build_track_dominance,
    control_section_shares,
    interpolate_lap_trace,
)
from reference_analysis_lab.gptempo_reverse_engineered_v1 import (  # noqa: E402
    CROSS_SESSION_WARNING,
    build_display_trace as build_gptempo_display_trace,
    build_sector_constrained_delta,
    build_track_geometry as build_gptempo_track_geometry,
    comparison_condition_status,
    integrate_cumulative_distance,
    linearly_scale_time_to_official_endpoints,
    official_cumulative_sector_times,
    prepare_raw_lap_trace,
)


def _points_for_traffic() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "driver_acronym": "AAA",
                "lap": 2,
                "sample_index": index,
                "time": float(index),
                "speed": 36.0,
                "distance_to_driver_ahead": distance,
                "driver_ahead": "BBB",
                "lap_duration": 3.0,
                "stint": 1,
                "compound": "MEDIUM",
                "tyre_life": 2,
                "track_status": "1",
                "deleted": False,
                "is_accurate": True,
                "team": "Team A",
                "position": 1,
            }
            for index, distance in enumerate((10.0, 30.0, 30.0, 30.0))
        ]
    )


def test_traffic_ratio_uses_point_time_weight_not_lap_median_gap():
    points = _points_for_traffic()
    laps = build_lap_table(points)
    traffic = traffic_lap_rows(points, laps)
    row = traffic.iloc[0]
    # 2 秒阈值为 20m；只有第一个时间区间在交通中，比例应为 1/4，
    # 而圈级 median gap 为 30m，不能代替逐点比例。
    assert row["traffic_ratio"] == pytest.approx(1.0 / 4.0)
    assert row["traffic_samples"] == 1
    assert row["distance_threshold_basis"] == "speed_kph_to_mps_x_2s"


def test_pace_filter_removes_first_lap_and_both_stint_edges():
    points = pd.concat(
        [
            _points_for_traffic().assign(driver_acronym="AAA"),
            _points_for_traffic().assign(
                driver_acronym="BBB",
                lap=2,
                stint=2,
                compound="HARD",
            ),
        ],
        ignore_index=True,
    )
    # 为 BBB 添加首圈、切换前圈与切换后圈，检验边界身份而非只看 stint 号。
    extra = points.loc[points.driver_acronym.eq("BBB")].copy()
    extra["lap"] = 1
    extra["stint"] = 1
    points = pd.concat([points, extra], ignore_index=True)
    laps = build_lap_table(points)
    assert bool(laps.loc[(laps.driver_acronym == "BBB") & (laps.lap == 1), "first_lap"].iloc[0])
    # BBB 的 stint 1/2 只各有一个观测圈，两个圈都在边界上，不得进入 pace。
    assert not laps.loc[laps.driver_acronym.eq("BBB"), "pace_eligible"].any()


def test_stint_jitter_is_stable_identity_and_not_within_stint_axis():
    base = _points_for_traffic()
    first_lap = base.copy()
    first_lap["lap"] = 1
    stint_one_lap_two = base.copy()
    stint_one_lap_two["lap"] = 2
    stint_one_lap_three = base.copy()
    stint_one_lap_three["lap"] = 3
    stint_two_lap_four = base.copy()
    stint_two_lap_four["lap"] = 4
    stint_two_lap_four["stint"] = 2
    stint_two_lap_four["compound"] = "HARD"
    stint_two_lap_five = stint_two_lap_four.copy()
    stint_two_lap_five["lap"] = 5
    points = pd.concat(
        [first_lap, stint_one_lap_two, stint_one_lap_three,
         stint_two_lap_four, stint_two_lap_five],
        ignore_index=True,
    )
    laps = build_lap_table(points)
    pace = build_pace_stats(laps)
    row = pace["all_drivers"][0]
    assert {point["stint"] for point in row["points"]} == {1, 2}
    by_stint = {}
    for point in row["points"]:
        by_stint.setdefault(point["stint"], set()).add(point["stint_jitter"])
    assert len(by_stint[1]) == 1
    assert len(by_stint[2]) == 1
    assert next(iter(by_stint[1])) != next(iter(by_stint[2]))


def test_pairwise_mean_delta_is_antisymmetric():
    rows = [
        {"driver": "AAA", "mean_lap_s": 88.0},
        {"driver": "BBB", "mean_lap_s": 89.25},
        {"driver": "CCC", "mean_lap_s": 90.5},
    ]
    matrix = build_pairwise_mean_delta(rows)
    values = {row["driver"]: row["values"] for row in matrix["matrix"]}
    for left in values:
        for right in values:
            assert values[left][right] == pytest.approx(-values[right][left])


def test_kish_effective_sample_size_penalizes_concentrated_soft_weights():
    assert kish_effective_sample_size([1.0, 1.0, 1.0, 1.0]) == pytest.approx(4.0)
    assert kish_effective_sample_size([4.0, 0.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_deltadata_weighted_mean_fail_closes_and_does_not_use_row_count():
    assert weighted_mean([10.0, 20.0], [1.0, 3.0]) == pytest.approx(17.5)
    assert weighted_mean([10.0, 20.0], [0.0, 0.0]) is None


def test_real_pilot_manifest_matches_public_means_when_generated():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v1"
        / "f1pace"
        / "run=20260730T154800+0800-f1pace-abu-dhabi-v1"
        / "f1pace_2025_abu_dhabi.json"
    )
    if not report.exists():
        pytest.skip("真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    means = {
        row["driver"]: row["mean_lap_s"]
        for row in payload["visual_replication"]["pace"]["all_drivers"]
    }
    assert means["NOR"] == pytest.approx(88.485, abs=0.001)
    assert means["LEC"] == pytest.approx(88.594, abs=0.001)
    assert payload["qualifying_analysis"]["q1_q2_q3_phase"] == (
        "NOT_AVAILABLE_IN_CURRENT_FREEZE"
    )
    assert payload["audited_analysis"]["direct_ranking_allowed"] is False


def test_deltadata_real_pilot_is_method_equivalent_and_audit_only():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v1"
        / "deltadata"
        / "run=20260730T235900+0800-deltadata-abu-dhabi-v1"
        / "deltadata_2025_abu_dhabi.json"
    )
    if not report.exists():
        pytest.skip("DeltaData pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "METHOD_EQUIVALENT_ONLY"
    assert payload["validation"]["real_source_only"] is True
    assert payload["validation"]["synthetic_points"] == 0
    assert payload["validation"]["one_to_one_status"] == "SKIPPED_OPAQUE_METHOD"
    assert payload["exclusion_ledger"]["stints"] == 19
    assert payload["exclusion_ledger"]["observed_points"] == 464
    assert payload["audited_analysis"]["status"] == "audit_only"
    assert payload["audited_analysis"]["rank_status"]["forms_total_order"] is False
    assert payload["audited_analysis"]["rank_status"]["directly_comparable_pairs"] == 4
    assert payload["audited_analysis"]["rank_status"]["balance_warning_pairs"] == 2


def test_fdataanalysis_linear_slope_fail_closes_constant_and_short_inputs():
    assert linear_slope([1, 2, 3], [10, 12, 14]) == pytest.approx(2.0)
    assert linear_slope([1, 1, 1], [10, 12, 14]) is None
    assert linear_slope([1], [10]) is None


def test_fdataanalysis_corner_and_straight_proxies_keep_named_distance_semantics():
    axis = pd.DataFrame(
        {
            "axis_bin": list(range(11)),
            "rel_distance": [0.02, 0.10, 0.18, 0.30, 0.38, 0.50, 0.58, 0.70, 0.78, 0.90, 0.98],
            "speed_kph": [250, 140, 245, 125, 240, 110, 235, 130, 230, 120, 245],
        }
    )
    corners = detect_distance_corner_proxies(axis)
    assert corners
    assert all(row["anchor_definition"].startswith("smoothed median speed") for row in corners)
    straights = build_straight_proxies(corners)
    assert len(straights) == len(corners)
    assert all(row["segment_role"] == "between_proxy_corners_distance_window" for row in straights)


def test_fdataanalysis_real_pilot_is_real_source_and_audit_only():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v1"
        / "fdataanalysis"
        / "run=20260730T235950+0800-fdataanalysis-abu-dhabi-v1"
        / "fdataanalysis_2025_abu_dhabi.json"
    )
    if not report.exists():
        pytest.skip("FDataAnalysis pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["validation"]["real_source_only"] is True
    assert payload["validation"]["synthetic_points"] == 0
    assert payload["validation"]["raw_points"] == 784424
    assert payload["validation"]["valid_points"] == 728315
    assert payload["validation"]["corner_proxy_count"] == 6
    assert payload["validation"]["straight_proxy_count"] == 6
    assert payload["validation"]["same_team_baseline_pairs"] == 20
    assert payload["validation"]["direct_physical_claims_allowed"] is False
    assert len(payload["visual_replication"]["driver_profiles"]) == 20
    assert len(payload["visual_replication"]["team_profiles"]) == 10
    assert len(payload["visual_replication"]["teammate_baselines"]) == 20
    assert payload["audited_analysis"]["status"] == "audit_only"


def test_f1pace_v2_threshold_identity_and_missing_time_denominator():
    assert _threshold_label(2.0) == "2.0"
    assert _threshold_label(1.75) == "1.75"
    # 缺失的 3 秒仍属于整圈可观测时间；只有最后 1 秒能确认在 2 秒内。
    ratio = _weighted_ratio(
        np.array([np.nan, 1.0]),
        np.array([3.0, 1.0]),
        2.0,
    )
    assert ratio == pytest.approx(0.25)


def test_f1pace_v2_reference_color_extractor_is_numeric_proxy(tmp_path):
    image = np.zeros((220, 2240, 3), dtype=np.uint8)
    image[:, :, :] = (240, 100, 240)
    path = tmp_path / "traffic.webp"
    Image.fromarray(image).save(path, format="WEBP", lossless=True)
    grid = extract_reference_traffic_grid(path, ["AAA"], 1)
    expected = np.clip(
        REFERENCE_GREEN_INTERCEPT_PCT + REFERENCE_GREEN_SLOPE_PCT * 100.0,
        0.0,
        100.0,
    ) / 100.0
    assert grid.iloc[0]["reference_traffic_ratio"] == pytest.approx(expected)
    assert grid.iloc[0]["reference_target_role"] == "image_color_derived_proxy"


def test_f1pace_v2_same_location_headway_keeps_unresolved_start_in_denominator():
    rows = []
    start = "2025-01-01T00:00:00.000000000"
    for driver, offset, position in (("AAA", 0.0, 2), ("BBB", 0.1, 1)):
        for index in range(10):
            rel = index / 10.0 + offset
            rows.append(
                {
                    "driver_acronym": driver,
                    "lap": 1,
                    "sample_index": index,
                    "time": float(index),
                    "lap_start_raw": start,
                    "lap_duration": 10.0,
                    "rel_distance": rel,
                    "distance": rel * 1000.0,
                    "speed": 360.0,
                    "driver_ahead": "BBB" if driver == "AAA" else None,
                    "distance_to_driver_ahead": 100.0,
                    "position": position,
                }
            )
    features = build_time_headway_features(pd.DataFrame(rows))
    row = features.loc[features["driver"].eq("AAA")].iloc[0]
    # t=0 时前车尚无可反插值的历史过点；其余 9 秒 headway=1s。
    assert row["cross_2.0"] == pytest.approx(0.9)
    assert row["audited_traffic_ratio"] == pytest.approx(0.9)
    assert row["missing"] == pytest.approx(0.1)


def test_f1pace_v2_huber_model_serializes_without_pickle():
    rows = []
    for index in range(40):
        base = index / 39.0
        row = {
            "driver": f"D{index % 5}",
            "lap": index + 1,
            "reference_traffic_ratio": 0.1 + 0.7 * base,
        }
        for feature_index, column in enumerate(SURROGATE_FEATURE_COLUMNS):
            row[column] = np.clip(base + feature_index * 0.001, 0.0, 1.0)
        rows.append(row)
    training = pd.DataFrame(rows)
    model = fit_surrogate(training)
    prediction = predict_surrogate(training, model)
    assert model["model_type"] == "standard_scaler_plus_huber_regressor"
    assert set(model["feature_columns"]) == set(SURROGATE_FEATURE_COLUMNS)
    assert np.mean(np.abs(prediction - training["reference_traffic_ratio"])) < 0.01


def test_f1pace_v2_real_external_event_is_zero_refit_when_generated():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v2"
        / "f1pace"
        / "run=20260731T120000+0800-f1pace-reverse-engineered-v2"
        / "f1pace_traffic_reverse_engineered_v2.json"
    )
    if not report.exists():
        pytest.skip("F1pace v2 真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    external = payload["calibration"]["external_validation"]
    assert payload["status"] == "METHOD_EQUIVALENT_EXTERNALLY_VALIDATED"
    assert payload["one_to_one_status"] == "SKIPPED_OPAQUE_METHOD"
    assert payload["calibration"]["external_zero_refit"] is True
    assert external["rows"] == 1067
    assert external["mae_pp"] < 5.0
    assert external["p90_abs_error_pp"] < 15.0
    assert external["traffic_lap_accuracy"] > 0.95
    assert payload["audited_analysis"]["status"] == "audit_only"
    assert payload["audited_analysis"]["reference_image_used_in_prediction"] is False


def _delta_v2_points() -> pd.DataFrame:
    rows = []
    for driver, team in (("AAA", "Team A"), ("BBB", "Team A")):
        for lap in range(1, 7):
            rows.append(
                {
                    "driver_acronym": driver,
                    "lap": lap,
                    "sample_index": 0,
                    "lap_duration": 90.0 + lap * 0.02,
                    "stint": 1,
                    "compound": "MEDIUM",
                    "tyre_life": float(lap),
                    "track_status": "1",
                    "deleted": False,
                    "is_accurate": True,
                    "team": team,
                    "position": 1 if driver == "AAA" else 2,
                }
            )
    return pd.DataFrame(rows)


def test_deltadata_v2_clean_air_threshold_is_explicit_and_not_median_gap():
    points = _delta_v2_points()
    features = []
    for row in points.itertuples():
        ratio = 0.0
        if row.driver_acronym == "AAA" and row.lap == 3:
            ratio = PUBLIC_TRAFFIC_RATIO_MAX
        if row.driver_acronym == "AAA" and row.lap == 4:
            ratio = PUBLIC_TRAFFIC_RATIO_MAX + 0.01
        features.append(
            {
                "driver": row.driver_acronym,
                "lap": row.lap,
                "audited_traffic_ratio": ratio,
                "missing": 0.0,
                "observed_time_s": row.lap_duration,
                "telemetry_samples": 100,
                "audited_definition": (
                    "nearest_physical_car_same_track_location_crossing_headway_le_2s"
                ),
            }
        )
    frame, ledger = prepare_clean_air_laps(
        points,
        traffic_features=pd.DataFrame(features),
    )
    at_threshold = frame.loc[
        frame["driver"].eq("AAA") & frame["lap"].eq(3)
    ].iloc[0]
    above_threshold = frame.loc[
        frame["driver"].eq("AAA") & frame["lap"].eq(4)
    ].iloc[0]
    assert bool(at_threshold["clean_air_candidate"])
    assert above_threshold["selection_disposition"] == (
        "traffic_ratio_above_threshold"
    )
    assert ledger["median_gap_used_as_ratio"] is False
    assert ledger["traffic_ratio_max"] == pytest.approx(0.20)


def _delta_v2_model_frame() -> pd.DataFrame:
    rows = []
    for driver_index, (driver, team) in enumerate(
        (
            ("AAA", "Team A"),
            ("BBB", "Team A"),
            ("CCC", "Team B"),
            ("DDD", "Team B"),
        )
    ):
        for lap in range(2, 14):
            tyre_age = float(lap - 1)
            lap_fraction = lap / 14.0
            pace = (
                90.0
                + driver_index * 0.15
                + tyre_age * 0.025
                - lap_fraction * 0.4
            )
            if driver == "DDD" and lap == 13:
                pace += 20.0
            rows.append(
                {
                    "driver": driver,
                    "team": team,
                    "lap": lap,
                    "stint": 1,
                    "compound": "MEDIUM",
                    "tyre_age": tyre_age,
                    "tyre_age_sq": tyre_age**2,
                    "lap_fraction": lap_fraction,
                    "lap_fraction_sq": lap_fraction**2,
                    "lap_time_s": pace,
                    "traffic_ratio": 0.02,
                    "missing": 0.0,
                    "candidate_weight": 0.95,
                    "clean_air_candidate": True,
                }
            )
    return pd.DataFrame(rows)


def test_deltadata_v2_huber_rejects_counterexample_and_serializes():
    frame = _delta_v2_model_frame()
    model, candidates = fit_robust_condition_model(frame)
    outlier = candidates.loc[
        candidates["driver"].eq("DDD") & candidates["lap"].eq(13)
    ].iloc[0]
    assert not bool(outlier["representative_lap"])
    prediction = predict_condition_model(candidates, model)
    assert np.max(
        np.abs(prediction - candidates["condition_prediction_s"].to_numpy())
    ) < 1e-10
    assert model["manual_review_claim_allowed"] is False
    assert model["fuel_role"] == (
        "named_linear_scenario_not_true_fuel_reconstruction"
    )


def test_deltadata_v2_audit_fails_without_common_compound():
    candidates = _delta_v2_model_frame().loc[
        lambda frame: frame["driver"].isin(["AAA", "BBB"])
    ].copy()
    candidates["representative_lap"] = True
    candidates.loc[candidates["driver"].eq("AAA"), "compound"] = "HARD"
    candidates.loc[candidates["driver"].eq("BBB"), "compound"] = "MEDIUM"
    scores = [
        {
            "driver": "AAA",
            "team": "Team A",
            "standardized_pace_s": 90.0,
            "sample": {
                "candidate_laps": 12,
                "representative_laps": 12,
                "downweighted_outliers": 0,
                "kish_ess": 12.0,
                "compounds": ["HARD"],
                "stints": 1,
                "tyre_age_range": [1.0, 12.0],
                "lap_fraction_range": [0.1, 0.9],
                "traffic_distribution": {
                    "mean": 0.02,
                    "median": 0.02,
                    "p90": 0.02,
                    "missing_mean": 0.0,
                },
            },
        },
        {
            "driver": "BBB",
            "team": "Team A",
            "standardized_pace_s": 90.1,
            "sample": {
                "candidate_laps": 12,
                "representative_laps": 12,
                "downweighted_outliers": 0,
                "kish_ess": 12.0,
                "compounds": ["MEDIUM"],
                "stints": 1,
                "tyre_age_range": [1.0, 12.0],
                "lap_fraction_range": [0.1, 0.9],
                "traffic_distribution": {
                    "mean": 0.02,
                    "median": 0.02,
                    "p90": 0.02,
                    "missing_mean": 0.0,
                },
            },
        },
    ]
    pair = build_deltadata_v2_teammate_comparisons(candidates, scores)[0]
    assert pair["audited_status"] == "AUDIT_ONLY"
    assert pair["audited_left_minus_right_pct"] is None
    assert "no_common_compound" in pair["gate_failures"]


def test_deltadata_v2_reference_benchmark_metrics_are_percentage_points():
    metrics = reference_benchmark_metrics(
        [
            {"reference_delta_pct": -0.2, "model_delta_pct": -0.1},
            {"reference_delta_pct": -0.4, "model_delta_pct": -0.6},
        ]
    )
    assert metrics["events"] == 2
    assert metrics["mae_pp"] == pytest.approx(0.15)
    assert metrics["direction_accuracy"] == pytest.approx(1.0)


def test_deltadata_v2_real_report_is_reference_benchmarked_when_generated():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v2"
        / "deltadata"
        / "run=20260731T153000+0800-deltadata-reverse-engineered-v2"
        / "deltadata_reverse_engineered_v2.json"
    )
    if not report.exists():
        pytest.skip("DeltaData v2 真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
    assert payload["one_to_one_status"] == "SKIPPED_OPAQUE_METHOD"
    assert payload["validation"]["real_source_only"] is True
    assert payload["validation"]["synthetic_laps"] == 0
    assert payload["validation"]["pilot_raw_points"] == 784424
    assert payload["validation"]["reference_benchmark_events"] == 3
    assert payload["validation"]["reference_benchmark_mae_pp"] < 0.12
    assert payload["validation"]["reference_benchmark_direction_accuracy"] == 1.0
    assert payload["validation"]["reference_benchmark_is_blind_holdout"] is False
    assert len(payload["visual_replication"]["driver_ranking"]) == 20
    assert len(payload["visual_replication"]["team_ranking"]) == 10
    assert len(payload["visual_replication"]["teammate_h2h"]) == 10
    assert payload["audited_analysis"]["direct_total_order_allowed"] is False
    assert payload["audited_analysis"]["comparable_teammate_pairs"] == 4
    assert payload["legacy_comparison"]["summary"]["driver_order_exact_match"] is True
    assert payload["legacy_comparison"]["summary"]["team_order_exact_match"] is True
    hul = next(
        row
        for row in payload["visual_replication"]["driver_ranking"]
        if row["driver"] == "HUL"
    )
    assert hul["status"] == "INSUFFICIENT_CLEAN_AIR_SAMPLE"
    assert hul["standardized_pace_s"] is None


def _fdata_v2_synthetic_profile() -> dict[str, np.ndarray]:
    grid = np.linspace(0.0, 1.0, 1001)
    lateral = np.zeros_like(grid)
    speed = np.full_like(grid, 300.0)
    brake = np.zeros_like(grid)
    for center, amplitude, minimum in (
        (0.20, 24.0, 115.0),
        (0.50, -30.0, 175.0),
        (0.80, 18.0, 250.0),
    ):
        shape = np.exp(-0.5 * ((grid - center) / 0.008) ** 2)
        lateral += amplitude * shape
        speed -= (300.0 - minimum) * np.exp(
            -0.5 * ((grid - center) / 0.014) ** 2
        )
    brake[(grid >= 0.16) & (grid <= 0.20)] = 1.0
    brake[(grid >= 0.46) & (grid <= 0.50)] = 1.0
    return {
        "grid": grid,
        "speed_kph": speed,
        "throttle_pct": np.full_like(grid, 100.0),
        "brake_share": brake,
        "gear": np.full_like(grid, 7.0),
        "drs_share": np.zeros_like(grid),
        "lateral_accel_mps2": lateral,
        "longitudinal_accel_mps2": np.zeros_like(grid),
    }


def test_fdataanalysis_v2_integral_top15_and_robust_slope_identities():
    # 5 km 全程 180 km/h 应严格为 100 秒；固定梯形公式不能随 NumPy
    # ``trapezoid`` 别名是否存在而变化。
    reconstructed = integrate_speed_trace_seconds(
        np.full(1001, 180.0),
        distance_m=5000.0,
    )
    assert reconstructed == pytest.approx(100.0)
    assert top_n_mean(range(1, 21), 15) == pytest.approx(13.0)
    assert top_n_mean([1.0, 2.0], 15) is None
    # 单个极端异常点不能把 Theil--Sen 的 0.2 s/胎龄圈趋势拉走。
    x = np.arange(1.0, 11.0)
    y = 90.0 + 0.2 * x
    y[-1] += 20.0
    assert robust_theil_sen_slope(x, y) == pytest.approx(0.2)
    assert robust_theil_sen_slope([1.0, 1.0], [2.0, 3.0]) is None


def test_fdataanalysis_v2_dynamic_corner_proxies_are_not_official_anchors():
    profile = _fdata_v2_synthetic_profile()
    corners = detect_dynamic_corner_proxies(profile)
    assert len(corners) == 3
    assert [row["corner_id"] for row in corners] == ["P01", "P02", "P03"]
    assert all(row["official_corner_number"] is None for row in corners)
    assert all(
        row["entry_rel_distance"] < row["exit_rel_distance"]
        for row in corners
    )
    # 无横向加速度峰值的反例必须 fail-close，不能为了“凑官方弯数”造 anchor。
    flat = dict(profile)
    flat["lateral_accel_mps2"] = np.zeros(1001)
    assert detect_dynamic_corner_proxies(flat) == []


def test_fdataanalysis_v2_segmentation_is_stable_across_distance_grids():
    stability = segmentation_stability(_fdata_v2_synthetic_profile())
    assert stability["status"] == "PASS"
    assert stability["baseline_corner_count"] == 3
    assert all(
        row["matched_to_baseline_within_1_5pct"] == 3
        for row in stability["runs"]
    )


def test_fdataanalysis_v2_real_report_closes_v1_window_gap_when_generated():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v2"
        / "fdataanalysis"
        / "run=20260731T170000+0800-fdataanalysis-reverse-engineered-v2"
        / "fdataanalysis_reverse_engineered_v2.json"
    )
    if not report.exists():
        pytest.skip("FDataAnalysis v2 真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    validation = payload["validation"]
    legacy = payload["same_event_legacy_comparison"]["structural_gap"]
    assert payload["status"] == "METHOD_EQUIVALENT_INTERNALLY_VALIDATED"
    assert validation["real_source_only"] is True
    assert validation["synthetic_points"] == 0
    assert validation["raw_points"] == 784424
    assert validation["axis_interpolated_laps"] == 1082
    assert validation["corner_proxy_count"] == 16
    assert validation["segmentation_stability"]["status"] == "PASS"
    assert validation["lap_time_reconstruction"]["mae_s"] < 0.25
    assert validation["lap_time_reconstruction"]["p90_abs_error_s"] < 0.50
    assert validation["teammate_matching"]["max_antisymmetry_error"] == 0.0
    assert legacy["axis_bins_v1"] == 200
    assert legacy["axis_intervals_v2"] == 1000
    assert legacy["corner_proxies_v1"] == 6
    assert legacy["corner_proxies_v2"] == 16
    assert legacy["v1_exit_below_reported_minimum_rows"] > 0
    assert legacy["v2_exit_below_reported_minimum_rows"] == 0
    assert payload["audited_analysis"]["direct_total_order_allowed"] is False
    assert payload["reference_identity"]["numeric_comparison_status"] == (
        "NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION"
    )
    assert payload["reference_identity"]["invalid_cached_asset"]["status"] == (
        "INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH"
    )


def test_f1telemetrydata_phase_windows_assign_lap_start_and_fail_closed_outside():
    race_control = pd.DataFrame(
        [
            {
                "date": f"2025-12-06T14:{minute:02d}:00Z",
                "message": message,
                "qualifying_phase": phase,
            }
            for phase, minute in ((1, 0), (2, 25), (3, 45))
            for message in ("GREEN LIGHT - PIT EXIT OPEN", "CHEQUERED FLAG")
        ]
    )
    # 为每个阶段把方格旗事件向后移动 15 分钟，构成三个不重叠冻结窗口。
    chequered = race_control["message"].eq("CHEQUERED FLAG")
    race_control.loc[chequered, "date"] = (
        pd.to_datetime(race_control.loc[chequered, "date"], utc=True)
        + pd.Timedelta(minutes=15)
    ).astype(str)
    windows = build_phase_windows(race_control)
    assert [row["phase"] for row in windows] == ["Q1", "Q2", "Q3"]
    assert assign_qualifying_phase(
        "2025-12-06T14:04:00Z", 83.0, windows
    ) == "Q1"
    # 方格旗前起跑、旗后完成仍属于该阶段。
    assert assign_qualifying_phase(
        "2025-12-06T14:14:50Z", 83.0, windows
    ) == "Q1"
    # 窗口外不能靠“最接近阶段”猜测标签。
    assert assign_qualifying_phase(
        "2025-12-06T15:20:00Z", 83.0, windows
    ) is None


def test_f1telemetrydata_control_sections_are_exclusive_and_weighting_is_explicit():
    points = pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2025-12-06T14:00:00Z",
                    "2025-12-06T14:00:01Z",
                    "2025-12-06T14:00:04Z",
                    "2025-12-06T14:00:05Z",
                ],
                utc=True,
            ),
            # 第一行同时全油门和刹车，必须由 brake 优先，避免双重计数。
            "throttle": [100.0, 0.0, 50.0, 100.0],
            "brake": [1.0, 0.0, 0.0, 0.0],
        }
    )
    sample = control_section_shares(points, weight_mode="sample")
    timed = control_section_shares(points, weight_mode="time")
    assert sum(sample.values()) == pytest.approx(100.0)
    assert sample == pytest.approx(
        {
            "braking_pct": 25.0,
            "lift_pct": 25.0,
            "partial_throttle_pct": 25.0,
            "full_throttle_pct": 25.0,
        }
    )
    # 不规则采样下样本等权和时间加权不是同一统计量，页面必须披露口径。
    assert timed["lift_pct"] == pytest.approx(50.0)
    assert timed["braking_pct"] == pytest.approx(100.0 / 6.0)
    with pytest.raises(ValueError):
        control_section_shares(points, weight_mode="opaque")


def test_f1telemetrydata_trace_and_track_dominance_share_one_distance_axis():
    source = pd.DataFrame(
        {
            "rel_distance": np.linspace(0.0, 1.0, 12),
            "time": np.linspace(10.0, 20.0, 12),
            "speed": np.linspace(200.0, 300.0, 12),
            "throttle": np.linspace(0.0, 100.0, 12),
            "x": np.linspace(0.0, 11.0, 12),
            "y": np.linspace(11.0, 0.0, 12),
        }
    )
    trace = interpolate_lap_trace(source, intervals=10)
    assert len(trace["axis"]) == 11
    assert trace["time"][0] == pytest.approx(0.0)
    dominance = build_track_dominance(
        [
            {**trace, "driver": "AAA"},
            {
                **trace,
                "driver": "BBB",
                "time": [value + 0.1 for value in trace["time"]],
            },
        ]
    )
    assert set(dominance["winners"]) == {"AAA"}
    assert dominance["driver_distance_shares"]["AAA"] == pytest.approx(1.0)
    # 不同距离轴不能静默按数组下标比较。
    with pytest.raises(ValueError):
        build_track_dominance(
            [
                {**trace, "driver": "AAA"},
                {
                    **trace,
                    "driver": "BBB",
                    "axis": trace["axis"][:-1],
                    "time": trace["time"][:-1],
                },
            ]
        )


def test_f1telemetrydata_pit_lane_and_stationary_stop_are_not_conflated():
    drivers = pd.DataFrame(
        [
            {
                "driver_number": 1,
                "name_acronym": "AAA",
                "team_name": "McLaren",
            },
            {
                "driver_number": 2,
                "name_acronym": "BBB",
                "team_name": "McLaren",
            },
        ]
    )
    pit = pd.DataFrame(
        [
            {
                "driver_number": 1,
                "lap_number": 20,
                "lane_duration": 21.0,
                "stop_duration": 2.4,
                "date": "2025-12-07T13:30:00Z",
            },
            {
                "driver_number": 2,
                "lap_number": 21,
                "lane_duration": 23.0,
                "stop_duration": None,
                "date": "2025-12-07T13:31:00Z",
            },
        ]
    )
    result = build_pit_summary(pit, drivers)
    team = result["team_summary"][0]
    assert team["lane_duration_mean_s"] == pytest.approx(22.0)
    assert team["stationary_stop_mean_s"] == pytest.approx(2.4)
    assert team["stop_duration_missing"] == 1
    assert result["coverage"]["stationary_stop_duration"] == 1
    # 缺失的真实停车时长必须保留为空，不能用 pit-lane transit 或队均值填补。
    missing = next(row for row in result["stops"] if row["driver"] == "BBB")
    assert missing["stop_duration"] is None


def test_f1telemetrydata_real_report_matches_public_same_event_package():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v2"
        / "f1telemetrydata"
        / "run=20260731T193000+0800-f1telemetrydata-reverse-engineered-v1"
        / "f1telemetrydata_reverse_engineered_v1.json"
    )
    if not report.exists():
        pytest.skip("F1TelemetryData v1 真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    validation = payload["validation"]
    assert payload["status"] == "METHOD_EQUIVALENT_REFERENCE_BENCHMARKED"
    assert payload["one_to_one_status"] == "SKIPPED_OPAQUE_METHOD"
    assert validation["status"] == "PASS"
    assert all(validation["exact_reference_gates"].values())
    assert validation["qualifying"]["lap_delta"]["coverage"] == 20
    assert len(validation["qualifying"]["lap_sections"]["rows"]) == 6
    assert validation["qualifying"]["lap_sections"]["mae_pp"] < 2.65
    assert validation["qualifying"]["team_throttle"]["mae"] < 1.20
    assert validation["race"]["average_gap"]["mae"] < 0.0005
    assert validation["race"]["pit_team_means"]["mae"] < 0.07
    assert (
        validation["race"]["strategy_boundaries"]["local_matches"]
        == 47
    )
    assert (
        payload["audited_analysis"]["direct_global_race_pace_causal_order_allowed"]
        is False
    )
    assert any(
        row["publish"] == "NOT_IDENTIFIABLE"
        for row in payload["data_gap_audit"]
    )


def test_gptempo_distance_integration_and_official_sector_contracts():
    distance = integrate_cumulative_distance(
        [0.0, 1.0, 2.0],
        [180.0, 180.0, 180.0],
    )
    assert distance.tolist() == pytest.approx([0.0, 50.0, 100.0])
    assert official_cumulative_sector_times([10.0, 20.0, 30.0]).tolist() == (
        pytest.approx([0.0, 10.0, 30.0, 60.0])
    )
    # 时间轴倒退不能被排序或绝对值静默修补。
    with pytest.raises(ValueError):
        integrate_cumulative_distance(
            [0.0, 1.0, 0.5],
            [180.0, 180.0, 180.0],
        )
    with pytest.raises(ValueError):
        official_cumulative_sector_times([10.0, -1.0, 30.0])


def test_gptempo_time_scaling_closes_both_endpoints_even_for_degenerate_raw_time():
    scaled = linearly_scale_time_to_official_endpoints(
        [3.0, 4.0, 7.0],
        10.0,
        20.0,
    )
    assert scaled.tolist() == pytest.approx([10.0, 12.5, 20.0])
    fallback = linearly_scale_time_to_official_endpoints(
        [5.0, 5.0, 5.0],
        20.0,
        30.0,
    )
    assert fallback.tolist() == pytest.approx([20.0, 25.0, 30.0])
    with pytest.raises(ValueError):
        linearly_scale_time_to_official_endpoints(
            [1.0, 2.0],
            30.0,
            20.0,
        )


def _gptempo_synthetic_trace(
    lap_id: str,
    *,
    sector_times: list[float],
    elapsed_scale: float = 1.0,
) -> dict:
    axis = np.linspace(0.0, 1.0, 13)
    elapsed = np.linspace(0.0, sum(sector_times) * elapsed_scale, 13)
    return {
        "lap_id": lap_id,
        "rel_distance": axis.tolist(),
        "elapsed_s": elapsed.tolist(),
        "sector_times_s": sector_times,
        "sector_boundary_rel_distance": [0.0, 1 / 3, 2 / 3, 1.0],
    }


def test_gptempo_sector_delta_exactly_matches_all_official_endpoints():
    reference = _gptempo_synthetic_trace(
        "Q-AAA-L1",
        sector_times=[10.0, 10.0, 10.0],
        elapsed_scale=1.08,
    )
    candidate = _gptempo_synthetic_trace(
        "Q-BBB-L1",
        sector_times=[10.5, 9.8, 10.2],
        elapsed_scale=0.93,
    )
    result = build_sector_constrained_delta(reference, candidate)
    assert result["endpoint_status"] == "PASS_EXACT_OFFICIAL_SECTOR_ENDPOINTS"
    assert result["max_abs_sector_endpoint_error_s"] == 0.0
    assert [row["actual_cumulative_delta_s"] for row in result["sector_endpoints"]] == (
        pytest.approx([0.5, 0.3, 0.5])
    )
    assert result["actual_finish_delta_s"] == pytest.approx(0.5)
    # 距离边界不单调时必须 fail-close，不能为了画线重排 sector。
    invalid = dict(candidate)
    invalid["sector_boundary_rel_distance"] = [0.0, 0.7, 0.6, 1.0]
    with pytest.raises(ValueError):
        build_sector_constrained_delta(reference, invalid)


def test_gptempo_prepare_trace_display_and_track_geometry_use_real_channel_semantics():
    start = pd.Timestamp("2025-12-06T14:00:00Z")
    elapsed = np.linspace(0.0, 30.0, 121)
    car = pd.DataFrame(
        {
            "date": start + pd.to_timedelta(elapsed, unit="s"),
            "speed": np.full(len(elapsed), 180.0),
            "throttle": np.linspace(0.0, 100.0, len(elapsed)),
            "brake": (elapsed < 2).astype(int),
            "n_gear": np.where(elapsed < 15, 5, 7),
            "drs": np.where(elapsed > 20, 12, 0),
            "rpm": np.full(len(elapsed), 11000.0),
        }
    )
    trace = prepare_raw_lap_trace(
        car,
        date_start=start,
        lap_duration_s=30.0,
        sector_times_s=[10.0, 10.0, 10.0],
        identity={
            "lap_id": "Q-AAA-L1",
            "session": "Qualifying",
            "driver": "AAA",
        },
    )
    assert trace["integrated_distance_m"][-1] == pytest.approx(1500.0)
    assert trace["sector_boundary_rel_distance"] == pytest.approx(
        [0.0, 1 / 3, 2 / 3, 1.0]
    )
    display = build_gptempo_display_trace(trace, intervals=100)
    assert len(display["axis"]) == 101
    assert set(display["gear"]).issubset({5.0, 7.0})
    geometry = build_gptempo_track_geometry(
        pd.DataFrame(
            {
                "rel_distance": np.linspace(0.0, 1.0, 60),
                "x": np.cos(np.linspace(0.0, 2 * np.pi, 60)),
                "y": np.sin(np.linspace(0.0, 2 * np.pi, 60)),
            }
        ),
        intervals=100,
    )
    assert len(geometry["axis"]) == 101
    with pytest.raises(ValueError):
        build_gptempo_track_geometry(
            pd.DataFrame(
                {
                    "rel_distance": [0.0, 1.0],
                    "x": [0.0, 1.0],
                    "y": [0.0, 1.0],
                }
            )
        )


def test_gptempo_condition_gate_warns_on_cross_session_and_tyres():
    qualifying = {
        "session": "Qualifying",
        "compound": "SOFT",
        "tyre_life": 2,
        "track_status": "1",
    }
    same = {
        "session": "Qualifying",
        "compound": "SOFT",
        "tyre_life": 3,
        "track_status": "1",
    }
    race = {
        "session": "Race",
        "compound": "HARD",
        "tyre_life": 16,
        "track_status": "1",
    }
    assert comparison_condition_status(qualifying, same)["status"] == (
        "COMPARABLE_DEFAULT"
    )
    warning = comparison_condition_status(qualifying, race)
    assert warning["status"] == "CONDITION_WARNING"
    assert CROSS_SESSION_WARNING in warning["reasons"]
    assert "WARNING_DIFFERENT_COMPOUND" in warning["reasons"]
    assert warning["default_selectable"] is False


def test_gptempo_real_report_has_exact_sector_endpoints_and_safe_default():
    report = (
        ROOT
        / "research"
        / "records"
        / "reference_analysis_lab_v2"
        / "gptempo"
        / "run=20260731T213000+0800-gptempo-reverse-engineered-v1"
        / "gptempo_reverse_engineered_v1.json"
    )
    if not report.exists():
        pytest.skip("GP Tempo v1 真实 pilot 尚未生成")
    import json

    payload = json.loads(report.read_text(encoding="utf-8"))
    validation = payload["validation"]
    assert payload["status"] == (
        "PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED"
    )
    assert validation["status"] == "PASS"
    assert validation["real_laps"] == 10
    assert validation["synthetic_laps"] == 0
    assert validation["ordered_pair_comparisons"] == 90
    assert validation["sector_endpoint_checks"] == 270
    assert validation["max_abs_sector_endpoint_error_s"] == 0.0
    assert validation["default_selection_gate"] == "PASS"
    assert 3.9 < validation["sample_hz_median"] < 4.3
    assert payload["audited_analysis"][
        "direct_driver_or_vehicle_causal_claim_allowed"
    ] is False
    assert payload["visual_replication"]["default_selection"]["status"] == (
        "COMPARABLE_DEFAULT"
    )
