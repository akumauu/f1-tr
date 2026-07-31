"""F1 TR v3 专业平台差距审计产物的独立验收。"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[4]
ALLOWED_STATUS = {"PASS", "FAIL", "NOT_TESTED", "UNKNOWN"}
ALLOWED_POSITION = {"LEADING", "PARITY", "BEHIND", "UNKNOWN"}


def load_json(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: str) -> Path:
    return REPO_ROOT / Path(value)


def recompute_counts(rows):
    by_platform = defaultdict(Counter)
    status = Counter()
    overall = Counter()
    for row in rows:
        by_platform[row["platform"]][row["relative_position"]] += 1
        overall[row["relative_position"]] += 1
        status[row["status"]] += 1
    platforms = {}
    for platform in (
        "F1pace",
        "DeltaData",
        "FDataAnalysis",
        "F1TelemetryData",
        "GP Tempo",
    ):
        count = by_platform[platform]
        platforms[platform] = {
            "leading": count["LEADING"],
            "parity": count["PARITY"],
            "behind": count["BEHIND"],
            "unknown": count["UNKNOWN"],
            "total": sum(count.values()),
        }
    return {
        "by_platform": platforms,
        "overall": {
            "leading": overall["LEADING"],
            "parity": overall["PARITY"],
            "behind": overall["BEHIND"],
            "unknown": overall["UNKNOWN"],
            "total": len(rows),
        },
        "by_status": dict(sorted(status.items())),
        "verified_accuracy_leads": sum(
            row["relative_position"] == "LEADING"
            and row["finding_category"] == "VERIFIED_SAME_SCALE"
            for row in rows
        ),
        "advantage_rate_denominator": sum(
            row["status"] not in {"UNKNOWN", "NOT_TESTED"} for row in rows
        ),
        "advantage_rate_numerator": sum(
            bool(row["counts_toward_advantage_rate"]) for row in rows
        ),
    }


def test_each_problem_has_evidence_and_acceptance():
    for row in load_json("problem_register.json"):
        assert row["evidence"].strip()
        assert row["acceptance_test"].strip()
        assert row["evidence_locator"].strip()
        assert repo_path(row["evidence_path"]).exists()


def test_all_matrix_paths_and_statuses_are_valid():
    rows = load_json("audit_matrix.json")
    assert len(rows) >= 50
    assert len({row["row_id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["status"] in ALLOWED_STATUS
        assert row["relative_position"] in ALLOWED_POSITION
        assert row["evidence_locator"].strip()
        assert repo_path(row["evidence_path"]).exists()


def test_numeric_gaps_are_comparable_or_explicitly_excluded():
    rows = load_json("audit_matrix.json")
    for row in rows:
        if row["finding_category"] in {
            "VERIFIED_SAME_SCALE",
            "VERIFIED_METHOD_MATCH",
        }:
            assert row["comparison_scope"] in {
                "same_event_same_task_same_unit",
                "same_method_invariant",
            }
            assert row["unit"]
            assert row["metric"]
        if row["comparison_scope"] in {
            "f1tr_internal_proxy_only",
            "f1tr_internal_protocol_only",
            "different_evidence_universe",
            "no_same_scale_truth",
            "opaque_product_scope",
            "non_identifiable",
        }:
            assert row["relative_position"] == "UNKNOWN"


def test_unknown_and_not_tested_never_count_as_advantage():
    rows = load_json("audit_matrix.json")
    for row in rows:
        if row["status"] in {"UNKNOWN", "NOT_TESTED"}:
            assert row["relative_position"] == "UNKNOWN"
            assert row["counts_toward_advantage_rate"] is False
    assert all(
        not row["counts_toward_advantage_rate"]
        or row["relative_position"] == "LEADING"
        for row in rows
    )


def test_manifest_hashes_and_counts_are_consistent():
    manifest = load_json("manifest.json")
    for item in manifest["inputs"]:
        path = repo_path(item["path"])
        assert path.is_file()
        assert sha256_file(path) == item["sha256"]
    for item in manifest["generated_files"]:
        path = repo_path(item["path"])
        assert path.is_file()
        assert sha256_file(path) == item["sha256"]
    rows = load_json("audit_matrix.json")
    assert recompute_counts(rows) == manifest["conclusion_counts"]
    assert manifest["boundaries"]["unknown_not_tested_counted_as_advantage"] is False
    assert manifest["boundaries"]["cross_task_total_score"] is None
    assert manifest["boundaries"]["pac"] is None
    assert manifest["boundaries"]["ovr"] is None


def test_csv_json_row_counts_and_required_columns_match():
    pairs = (
        ("audit_matrix.csv", "audit_matrix.json"),
        ("problem_register.csv", "problem_register.json"),
        ("improvement_backlog.csv", "improvement_backlog.json"),
    )
    for csv_name, json_name in pairs:
        with (HERE / csv_name).open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            csv_rows = list(csv.DictReader(handle))
        json_rows = load_json(json_name)
        assert len(csv_rows) == len(json_rows)
        assert set(csv_rows[0]) == set(json_rows[0])


def test_problem_top10_and_backlog_top5_are_complete():
    problems = load_json("problem_register.json")
    assert sorted(
        row["top_risk_rank"] for row in problems if row["top_risk_rank"]
    ) == list(range(1, 11))
    backlog = load_json("improvement_backlog.json")
    assert sorted(
        row["top5_rank"] for row in backlog if row["top5_rank"]
    ) == list(range(1, 6))
    assert {row["category"] for row in backlog} == {
        "当前冻结数据即可完成",
        "需新增公开数据冻结",
        "算法改进",
        "可视化/产品改进",
    }


def test_recalculation_preserves_fail_close_boundaries():
    audit = load_json("independent_recalculation.json")
    assert audit["raw_race_coverage"]["events"] == 70
    assert audit["raw_race_coverage"]["raw_points"] == 55_248_206
    assert audit["selected_model_counts"]["deltadata"] == {
        "cluster": 5,
        "track": 6,
        "universal": 13,
    }
    assert audit["f1telemetrydata"]["q_phase_status_contradiction"] is True
    assert audit["hash_graph"]["failures"] == 0
    assert audit["acceptance_audit"]["v3"]["latest_status"] == "PASS"
    assert audit["acceptance_audit"]["v3"]["latest_exact_mobile_width"] == 390
    assert audit["acceptance_audit"]["v3"]["prior_failure_preserved"] is True
    assert audit["acceptance_audit"]["v2"]["targets"] == 5
    assert audit["acceptance_audit"]["v2"]["all_status_pass"] is True
    assert (
        audit["source_and_product_audit"]["selection_reuses_outer_fold_results"][
            "untouched_final_confirmation_fold"
        ]
        is False
    )
    assert audit["gptempo"]["v3_model_comparison"][0]["segment_mae"] == ""
    assert audit["boundaries"]["pac"] is None
    assert audit["boundaries"]["ovr"] is None


def test_report_does_not_publish_total_replication_rate():
    report = (HERE / "audit_report.md").read_text(encoding="utf-8")
    assert "已证实预测准确率领先为 0" in report
    assert "不生成跨任务总复刻率" in report
    assert "segment_mae=null" in report
    assert "untouched final test" in report
