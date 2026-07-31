"""历史全场车手评分卡冻结合同测试。"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from modeling.driver_cards import (  # noqa: E402
    DriverCardContractError,
    NULL_ONLY_METRICS,
    build_driver_rating_cards,
)


def _inputs():
    seasonal = json.loads(
        (ROOT / "research/records/seasonal_standings_calibration_results.json").read_text(
            encoding="utf-8"
        )
    )
    objective = json.loads(
        (ROOT / "research/records/objective_rating_review_2023_2026_v1.json").read_text(
            encoding="utf-8"
        )
    )
    config = json.loads(
        (
            ROOT
            / "research/configs/driver_rating_cards_2023_2025_v1.json"
        ).read_text(encoding="utf-8")
    )
    return seasonal, objective, config


def _force_pac_validated(objective):
    value = deepcopy(objective)
    value["evaluation"]["module_coverage"]["PAC"] = "validated"
    return value


def test_pac_uses_each_unique_driver_once_and_frozen_population_scale():
    seasonal, objective, config = _inputs()
    result = build_driver_rating_cards(
        seasonal,
        _force_pac_validated(objective),
        config["rating_contract"],
    )

    assert result["coverage"]["driver_count"] == 28
    assert len(result["cards"]) == 28
    assert len({card["driver"] for card in result["cards"]}) == 28
    reference = result["reference_distribution"]
    assert reference["sd_ddof"] == 0
    assert reference["population_sd"] == pytest.approx(0.22839362345273181)
    assert reference["source_run_id"] == seasonal["run_id"]
    assert reference["source_config_hash"] == seasonal["config_hash"]

    ver = next(card for card in result["cards"] if card["driver"] == "VER")
    expected_z = (
        -ver["metrics"]["PAC"]["raw_driver_base_z"] - reference["population_mean"]
    ) / reference["population_sd"]
    assert ver["metrics"]["PAC"]["z"] == pytest.approx(expected_z)
    assert ver["metrics"]["PAC"]["rating"] == pytest.approx(
        min(100.0, max(0.0, 75.0 + 10.0 * expected_z))
    )


def test_unavailable_modules_and_pooled_interval_remain_null():
    seasonal, objective, config = _inputs()
    result = build_driver_rating_cards(
        seasonal,
        objective,
        config["rating_contract"],
    )

    assert result["coverage"]["module_coverage"]["TYM"] == "prototype"
    assert result["coverage"]["module_coverage"]["ATK"] == "missing"
    assert result["coverage"]["module_coverage"]["PAC"] == "prototype"
    for card in result["cards"]:
        assert card["metrics"]["PAC"] is None
        assert card["pac_rank"] is None
        assert all(card["metrics"][metric] is None for metric in NULL_ONLY_METRICS)
        assert card["metrics"]["EventDelivery"] is None
        assert card["metrics"]["OVR"] is None
    flags = result["identification"]["flags"]
    assert "pooled_pac_interval_not_exported_ci_null" in flags
    assert "annual_driver_intervals_not_reused_for_pooled_pac" in flags


def test_sample_count_is_coverage_only_and_never_shrinks_pac_twice():
    seasonal, objective, config = _inputs()
    baseline = build_driver_rating_cards(
        seasonal,
        _force_pac_validated(objective),
        config["rating_contract"],
    )
    modified = deepcopy(seasonal)
    for index, row in enumerate(modified["season_ratings"]["drivers"], start=1):
        row["sample_count"] = index * 100_000
    changed = build_driver_rating_cards(
        modified,
        _force_pac_validated(objective),
        config["rating_contract"],
    )

    baseline_ratings = {
        card["driver"]: card["metrics"]["PAC"]["rating"] for card in baseline["cards"]
    }
    changed_ratings = {
        card["driver"]: card["metrics"]["PAC"]["rating"] for card in changed["cards"]
    }
    assert changed_ratings == baseline_ratings
    assert changed["reference_distribution"]["additional_sample_count_shrinkage"] is False


def test_source_and_population_drift_fail_closed():
    seasonal, objective, config = _inputs()
    bad_binding = deepcopy(config["rating_contract"])
    bad_binding["seasonal_source"]["config_hash"] = "unexpected"
    with pytest.raises(DriverCardContractError, match="config_hash 漂移"):
        build_driver_rating_cards(seasonal, objective, bad_binding)

    inconsistent = deepcopy(seasonal)
    repeated = [
        row for row in inconsistent["season_ratings"]["drivers"] if row["driver"] == "VER"
    ]
    assert len(repeated) > 1
    repeated[-1]["driver_base_z"] += 0.01
    with pytest.raises(DriverCardContractError, match="年度行之间不一致"):
        build_driver_rating_cards(
            inconsistent,
            objective,
            config["rating_contract"],
        )


def test_identification_leakage_and_2026_release_gate_are_explicit():
    seasonal, objective, config = _inputs()
    result = build_driver_rating_cards(
        seasonal,
        objective,
        config["rating_contract"],
    )

    assert result["identification"]["transfer_graph_connected"] is False
    nor = next(card for card in result["cards"] if card["driver"] == "NOR")
    assert "driver_teammate_network_weakly_anchored" in nor["identification"]["flags"]
    assert "component_not_cross_grid_transfer_anchored" in nor["identification"]["flags"]
    prohibited = result["forbidden_ability_input_fields"]
    assert "actual_points" in prohibited
    assert "finish_position" in prohibited
    assert "winner" in prohibited

    gate = result["season_2026_release_gate"]
    assert gate["observed_events"] == 4
    assert gate["minimum_events"] == 8
    assert gate["passed"] is False
    assert gate["publication_mode"] == "exploratory_only"


def test_driver_rating_cards_cli_runs_offline_and_emits_machine_record():
    completed = subprocess.run(
        [sys.executable, "research/run_driver_rating_cards.py", "--quiet"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result_path = ROOT / "research/records/driver_rating_cards_2023_2025_v1.json"
    summary_path = ROOT / "research/records/driver_rating_cards_2023_2025_v1.md"
    assert result_path.exists()
    assert summary_path.exists()
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["network_calls"] == []
    assert payload["coverage"]["driver_count"] == 28
    assert payload["coverage"]["published_modules"] == []
    assert all(card["metrics"]["PAC"] is None for card in payload["cards"])
    assert math.isclose(
        payload["reference_distribution"]["population_sd"],
        0.22839362345273181,
        rel_tol=0.0,
        abs_tol=1e-15,
    )
