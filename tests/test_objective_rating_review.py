"""客观性门控 CLI 的本地冻结产物烟测。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_objective_rating_review_cli_runs_offline():
    completed = subprocess.run(
        [sys.executable, "research/run_objective_rating_review.py", "--quiet"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert (ROOT / "research/records/objective_rating_review_2023_2026_v1.json").exists()
    assert (ROOT / "research/records/objective_rating_review_2023_2026_v1.md").exists()

