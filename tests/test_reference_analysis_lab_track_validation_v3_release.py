"""reference-analysis-lab v3 正式发布契约测试。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_reference_analysis_lab_v3 import audit_release  # noqa: E402


def test_reference_analysis_lab_v3_latest_release_passes_independent_audit():
    summary = audit_release()
    assert summary["status"] == "PASS"
    assert summary["events"] == 70
    assert summary["tracks"] == 24
    assert summary["targets"] == 5
