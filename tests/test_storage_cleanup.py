from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import cleanup_obsolete_storage as cleanup  # noqa: E402


def test_cleanup_whitelist_never_contains_v4() -> None:
    assert cleanup.CANONICAL_V4 not in cleanup.WORKSPACE_TARGETS
    assert all("expanded-v4" not in str(path) for path in cleanup.WORKSPACE_TARGETS)


def test_validate_target_rejects_canonical_v4() -> None:
    with pytest.raises(RuntimeError, match="拒绝删除 v4"):
        cleanup.validate_target(cleanup.CANONICAL_V4)


def test_dry_run_preserves_canonical_v4() -> None:
    report = cleanup.cleanup_files(apply=False)
    assert report["apply"] is False
    assert report["bytes_reclaimed"] == 0
    assert report["canonical_v4_preserved"] is True
