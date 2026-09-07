"""Green-baseline tests for the already-built capture layer (sources/distill/capture).

WHY THIS EXISTS
---------------
Before any new engine code (IV surface, GEX, mapping, levels) is added, we prove the
committed capture output is well-formed and round-trips. If this suite is red, no
downstream feature can be trusted, because everything consumes these distilled files.

These tests exercise the REAL committed fixture at data/snapshots/2026-09-06/230612Z --
not mocks. That fixture is distilled CBOE data produced by scripts/capture_snapshot.py,
so a passing round-trip here is evidence the capture layer itself is sound.

The GEX identity under test (paper section 2 / build-spec SS2):

    GEX = Gamma * OI * M * S^2 * 0.01        [dollars of dealer gamma per 1% move]

  where M is the contract multiplier (100 for QQQ) and S is spot. This is the gross
  (unsigned) form; dealer sign is applied later in the engine. For a green baseline we
  only assert the gross number is finite and positive, which is enough to catch a
  broken join, an all-zero gamma column, or a botched OI merge.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from pathlib import Path

import pytest

from gammamap import distill

# QQQ contract multiplier and the fixture spot (meta.json derived.qqq_spot).
QQQ_MULTIPLIER = 100.0
QQQ_SPOT = 717.5


def _read_gz_csv(path: Path) -> list[dict[str, str]]:
    """Read a gzipped CSV into a list of dict rows. stdlib only, matching engine style."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _contract_key(row: dict[str, str]) -> tuple[str, str, str, float]:
    """The mandatory join key (C7): root is part of contract identity, not decoration."""
    return (row["root"], row["expiry"], row["type"], float(row["strike"]))


# ---------------------------------------------------------------------------
# (b) parse_osi round-trips known OSI symbols, including the _NDX-style PM root.
# ---------------------------------------------------------------------------
def test_parse_osi_roundtrips_qqq_symbol() -> None:
    # root(alpha) + expiry(yymmdd) + type + strike(8 digits, thousandths of a dollar).
    assert distill.parse_osi("QQQ260904C00715000") == ("QQQ", "260904", "C", 715.0)


def test_parse_osi_roundtrips_ndxp_symbol() -> None:
    # NDXP is the PM-settled root that carries the daily/weekly expiries (C7). The
    # parser must keep it distinct from NDX -- merging them mis-stated GEX by $7.1B.
    assert distill.parse_osi("NDXP260908P29000000") == ("NDXP", "260908", "P", 29000.0)


def test_parse_osi_rejects_garbage_without_raising() -> None:
    # An unrecognised symbol is counted and flagged upstream, never allowed to raise.
    assert distill.parse_osi("not-a-symbol") is None
    assert distill.parse_osi("") is None


# ---------------------------------------------------------------------------
# (a) Load committed distilled QQQ greeks + its OI, join on the full key,
#     recompute gross GEX, assert finite and > 0.
# ---------------------------------------------------------------------------
def test_qqq_gross_gex_roundtrip_is_finite_and_positive(fixture_snapshot_dir: Path,
                                                         snapshots_root: Path) -> None:
    greeks = _read_gz_csv(fixture_snapshot_dir / "greeks_qqq.csv.gz")
    # meta.json records the exact oi file for this snapshot; read it from meta rather
    # than hardcoding the content-addressed digest so the test survives a re-distill.
    meta = json.loads((fixture_snapshot_dir / "meta.json").read_text())
    oi_rel = meta["distilled"]["QQQ"]["oi_file"]
    oi_rows = _read_gz_csv(snapshots_root / oi_rel)

    # Sanity: the committed rows_kept matches meta so we know we read the whole file.
    assert len(greeks) == meta["distilled"]["QQQ"]["rows_kept"] == 7943

    # Join OI onto greeks on the full (root,expiry,type,strike) identity.
    oi_by_key = {_contract_key(r): int(r["open_interest"]) for r in oi_rows}
    assert len(oi_by_key) == len(oi_rows)  # full key is unique -> no ambiguous merge

    gross_gex = 0.0
    matched = 0
    for row in greeks:
        key = _contract_key(row)
        oi = oi_by_key.get(key)
        if oi is None:
            continue
        matched += 1
        gamma = float(row["gamma"])
        # GEX = Gamma * OI * M * S^2 * 0.01 (gross/unsigned).
        gross_gex += gamma * oi * QQQ_MULTIPLIER * (QQQ_SPOT ** 2) * 0.01

    # The greeks and OI files are two projections of the same contract set, so every
    # greeks row must find its OI. A miss means the join key is wrong.
    assert matched == len(greeks)
    assert math.isfinite(gross_gex)
    assert gross_gex > 0.0


# ---------------------------------------------------------------------------
# (c) The NDX distilled fixture contains BOTH NDX and NDXP roots (C7 split),
#     and the per-root counts match meta.json.
# ---------------------------------------------------------------------------
def test_ndx_fixture_contains_both_ndx_and_ndxp_roots(fixture_snapshot_dir: Path) -> None:
    rows = _read_gz_csv(fixture_snapshot_dir / "greeks_ndx.csv.gz")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["root"]] = counts.get(r["root"], 0) + 1

    # Both settlement roots must be present and distinct (C7): AM-settled NDX vs
    # PM-settled NDXP. Their absence would mean the split regressed.
    assert "NDX" in counts
    assert "NDXP" in counts

    meta = json.loads((fixture_snapshot_dir / "meta.json").read_text())
    expected = meta["distilled"]["_NDX"]["roots"]
    assert counts == expected
    assert expected == {"NDX": 2568, "NDXP": 3283}
