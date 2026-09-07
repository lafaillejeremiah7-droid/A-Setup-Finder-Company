"""Shared pytest fixtures for the Gamma Map engine test suite.

WHY THIS FILE
-------------
There is no packaging (no pyproject/setup), so `import gammamap...` only resolves when
src/ is on sys.path. The documented way to run the suite is
`PYTHONPATH=src python -m pytest tests/ -q`, but we ALSO insert src/ here so the suite
resolves the package even if a runner forgets the env var. Belt and suspenders: a test
that fails to import is indistinguishable from a broken engine, and we do not want that
ambiguity.

The fixtures point at the committed live-format snapshot at
data/snapshots/2026-09-06/230612Z. That snapshot is the canonical fixture (paper
section 11.1 audit copy): it is real distilled CBOE data, not a mock, so a test that
round-trips it is exercising the actual capture layer output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Repo layout: tests/ and src/ are siblings under the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"

# Insert rather than append so a stray site-installed `gammamap` never shadows the
# in-repo source we actually want to test.
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return _REPO_ROOT


@pytest.fixture(scope="session")
def snapshots_root() -> Path:
    """Root of the committed snapshot store (holds per-snapshot dirs and content-addressed oi/)."""
    return _REPO_ROOT / "data" / "snapshots"


@pytest.fixture(scope="session")
def fixture_snapshot_dir() -> Path:
    """The canonical committed live-format snapshot directory used across the suite."""
    return _REPO_ROOT / "data" / "snapshots" / "2026-09-06" / "230612Z"
