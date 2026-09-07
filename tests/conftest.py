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


def _purge_stale_bytecode() -> None:
    """Delete any src/gammamap .pyc whose source .py is newer (stale-bytecode guard).

    WHY (review issue #5): a stale __pycache__/*.pyc can be imported in preference to a
    just-edited .py under some interpreter/timestamp conditions, so a formula change (e.g.
    the GEX 0.01 factor) can be MASKED locally -- a fresh test run then shows failures that
    look like a source bug but are really a cache artifact. Rather than trust the
    interpreter's invalidation, we remove any compiled file whose matching source is newer
    before collection, so `pytest` always exercises the current source. Cheap, and it runs
    once at import time. (The .pyc files are gitignored, so this never touches tracked
    state.)
    """
    pkg = _SRC / "gammamap"
    if not pkg.exists():
        return
    for pyc in pkg.rglob("*.pyc"):
        # __pycache__/<mod>.cpython-XY.pyc  ->  <mod>.py in the parent-of-__pycache__ dir.
        stem = pyc.name.split(".", 1)[0]
        src = pyc.parent.parent / f"{stem}.py"
        try:
            if not src.exists() or src.stat().st_mtime > pyc.stat().st_mtime:
                pyc.unlink()
        except OSError:
            # A guard must never break the run; a leftover .pyc is the caller's problem.
            pass


_purge_stale_bytecode()


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


@pytest.fixture(scope="session")
def fixture_0dte_snapshot_dir() -> Path:
    """A synthetic same-trading-day-expiry QQQ snapshot (scripts/make_0dte_fixture.py).

    The canonical live snapshot was captured over a weekend, so its trading day precedes
    every listed expiry and both 0DTE walls are empty on it. This committed synthetic
    snapshot's quote day IS a listed 0DTE expiry, so build_levels populates all four walls,
    a non-WEAK 0DTE strength band and a gamma-flip line end to end (review issues #2/#6).
    """
    return _REPO_ROOT / "data" / "snapshots" / "2026-09-08" / "120000Z"
