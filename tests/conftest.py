"""Shared test fixtures for Chaitya Core tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SDK_SRC = ROOT / "sdk" / "src"
for path in (str(SRC), str(SDK_SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for test artifacts."""
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Provide a temporary SQLite database path."""
    return tmp_path / "test_chaitya.db"
