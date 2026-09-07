"""Tests for the mapping engine (step 5) + levels writer (step 6).

WHY THIS EXISTS
---------------
These tests pin the two corrections the mapping/levels layer exists to enforce and the
replay-correctness the whole product depends on:

  * The verified basis reference (build-spec.md SS2.1 / findings C2, PROVEN): NDX 24200
    with NQ 24242 -> basis +42; NDX 24342 -> NQ 24384; zone 24250-24350 NDX -> 24292-24392
    NQ. Reproduced EXACTLY -- this is the number dropping the basis (the C2 error) breaks.
  * build_levels on the committed QQQ snapshot yields a valid levels dict: 4 walls at real
    MNQ prices, a flip line, a regime, provenance -- and is marked true-GEX (oi_available)
    because the distilled QQQ snapshot carries OI.
  * The levels file schema round-trips (write then read, byte-stable, sorted keys).
  * The no-look-ahead guard raises when a source timestamp is after asof (build-spec SS0).
  * The corrected parity basis differs from the raw current_price basis (C9) and BOTH are
    recorded in provenance.

The committed snapshot data/snapshots/2026-09-06/230612Z is the canonical fixture (real
distilled CBOE data), so these exercise the actual pipeline, not a mock.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gammamap import mapping
from gammamap.levels import (
    LEVELS_SCHEMA_VERSION,
    LookAheadError,
    build_levels,
    read_levels,
    write_levels,
)


# ---------------------------------------------------------------------------
# (a) The verified +42 basis reference (build-spec.md SS2.1 / C2, PROVEN).
# ---------------------------------------------------------------------------
def test_basis_reference_plus_42():
    """NDX 24200 with NQ 24242 -> basis exactly +42 (the PROVEN reference)."""
    assert mapping.basis(24242.0, 24200.0) == pytest.approx(42.0)


def test_ndx_level_maps_with_basis():
    """NDX level 24342 with basis +42 -> NQ (== MNQ) 24384 (build-spec.md SS2.1)."""
    b = mapping.basis(24242.0, 24200.0)
    assert mapping.map_ndx_to_nq(24342.0, b) == pytest.approx(24384.0)
    # NQ<->MNQ is exactly 1:1, so the MNQ price equals the NQ price.
    assert mapping.map_ndx_to_mnq(24342.0, b) == pytest.approx(24384.0)


def test_zone_maps_to_expected_nq_range():
    """Zone 24250-24350 NDX -> 24292-24392 NQ with the +42 basis (build-spec.md SS2.1)."""
    b = mapping.basis(24242.0, 24200.0)
    assert mapping.map_ndx_to_mnq(24250.0, b) == pytest.approx(24292.0)
    assert mapping.map_ndx_to_mnq(24350.0, b) == pytest.approx(24392.0)


def test_basis_leg_is_mandatory():
    """Dropping the basis (the C2 error) misplaces the level; map != raw NDX level."""
    b = mapping.basis(24242.0, 24200.0)
    mapped = mapping.map_ndx_to_mnq(24342.0, b)
    assert mapped != pytest.approx(24342.0)  # would be the C2 bug
    assert mapped - 24342.0 == pytest.approx(42.0)


def test_qqq_path_round_trips_through_ndx_equiv():
    """QQQ strike -> NDX-equiv (÷ratio) -> +basis lands on the expected MNQ level."""
    ratio = mapping.qqq_ndx_ratio(717.5, 29544.1543)
    # A QQQ strike of 590 maps to NDX-equiv 590/ratio, then +basis.
    b = 42.0
    k_ndx_eq = mapping.qqq_strike_to_ndx_equiv(590.0, ratio)
    expected = k_ndx_eq + b
    assert mapping.map_qqq_to_mnq(590.0, ratio, b) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# (e) C9: corrected parity basis differs from raw current_price basis; both recorded.
# ---------------------------------------------------------------------------
def test_corrected_basis_differs_from_raw_current_price_basis():
    """C9: NQ - parity_forward != NQ - current_price when the two anchors disagree.

    Canonical snapshot: NQ 29565.25, NDX current_price 29544.1543 (-> raw basis 21.0957).
    A parity-implied anchor near 29511 gives a materially different corrected basis.
    """
    nq = 29565.25
    current_price = 29544.1543
    parity_forward = 29511.0  # parity-implied NDXP anchor (differs from current_price, C9)

    raw = mapping.raw_feed_basis(nq, current_price)
    corrected = mapping.corrected_basis(nq, parity_forward)

    assert raw.basis == pytest.approx(21.0957, abs=1e-3)
    assert corrected.basis != pytest.approx(raw.basis, abs=1.0)
    assert corrected.anchor_source == "parity_forward"
    assert raw.anchor_source == "feed_current_price"
    # NQ=F is continuous front-month -> flagged on the corrected basis.
    assert mapping.NQ_CONTINUOUS_FRONT_MONTH in corrected.quality_flags


def test_source_skew_is_flagged():
    """Skew beyond the max is flagged, never silently accepted (build-spec.md SS9.1)."""
    res = mapping.corrected_basis(
        29565.25, 29511.0, source_skew_seconds=999.0, max_skew_seconds=120.0
    )
    assert any(f.startswith(mapping.BASIS_SOURCE_SKEW) for f in res.quality_flags)


# ---------------------------------------------------------------------------
# (b) build_levels on the committed QQQ snapshot -> valid true-GEX levels dict.
# ---------------------------------------------------------------------------
def test_build_levels_qqq_snapshot(fixture_snapshot_dir):
    """build_levels on the committed QQQ snapshot yields a valid true-GEX levels dict."""
    levels = build_levels(fixture_snapshot_dir, "QQQ")

    assert levels["schema_version"] == LEVELS_SCHEMA_VERSION
    assert levels["instrument"] == "QQQ"
    assert levels["render_target"] == "MNQ"

    # Exactly the 4 walls, each with the required render fields.
    assert len(levels["walls"]) == 4
    labels = {w["label"] for w in levels["walls"]}
    assert labels == {"Call Wall", "Put Wall", "0DTE Call Wall", "0DTE Put Wall"}
    for w in levels["walls"]:
        assert {"label", "universe", "side", "mnq_price", "gex_per_1pct", "strength", "confidence"} <= set(w)

    # At least the Total walls should land at real MNQ prices in a plausible NQ range.
    total_walls = [w for w in levels["walls"] if w["universe"] == "total"]
    assert all(w["mnq_price"] is not None for w in total_walls)
    for w in total_walls:
        assert 20000.0 < w["mnq_price"] < 40000.0

    # Flip line + regime present.
    assert "mnq_price" in levels["gamma_flip"]
    assert "neutral" in levels["gamma_flip"]
    assert set(levels["regime"]) == {"total_sign", "zerodte_sign", "divergence"}

    # QQQ distilled snapshot carries OI -> true-GEX, not a proxy.
    assert levels["provenance"]["oi_available"] is True
    assert levels["provenance"]["proxy"] is False

    # Both bases recorded (C9).
    prov = levels["provenance"]
    assert prov["basis_raw"] is not None
    assert prov["basis_parity"] is not None


def test_build_levels_hides_ingredients(fixture_snapshot_dir):
    """OI/IV/greeks/MaxPain/full-chain are ingredients and are NOT in the file (SS9a)."""
    levels = build_levels(fixture_snapshot_dir, "QQQ")
    blob = str(levels).lower()
    # None of the ingredient quantities may appear as data in the emitted file. (We check
    # substrings that would only appear if OI/IV/greeks/MaxPain/the full chain leaked; the
    # 'gex_no_contracts' quality flag legitimately contains 'contracts', so we check for a
    # full-chain 'contracts' KEY rather than the raw substring.)
    for forbidden in ("open_interest", "max_pain", "maxpain", "greeks", "'gamma'", "'iv'"):
        assert forbidden not in blob
    # No ingredient KEYS at any level of the emitted structure.
    assert "contracts" not in levels  # no full-chain key
    for w in levels["walls"]:
        assert not ({"open_interest", "iv", "gamma", "delta"} & set(w))
    # Top-level keys are exactly the render-facing set.
    assert set(levels) == {
        "schema_version", "model_versions", "asof", "instrument",
        "render_target", "gex_unit", "walls", "gamma_flip", "regime", "provenance",
    }


def test_build_levels_records_both_bases_and_c9_difference(fixture_snapshot_dir):
    """C9: the emitted file records BOTH the raw and the corrected parity basis."""
    levels = build_levels(fixture_snapshot_dir, "QQQ")
    prov = levels["provenance"]
    assert prov["basis_raw_anchor"] == "feed_current_price"
    assert prov["basis_parity_anchor"] in ("parity_forward",)
    # The two bases should differ (parity anchor != current_price on this snapshot).
    assert prov["basis_raw"] != prov["basis_parity"]


# ---------------------------------------------------------------------------
# (c) Schema round-trips (write then read).
# ---------------------------------------------------------------------------
def test_levels_file_round_trips(fixture_snapshot_dir, tmp_path):
    """write_levels then read_levels returns an equal dict; serialization is deterministic."""
    levels = build_levels(fixture_snapshot_dir, "QQQ")
    path = write_levels(levels, root=tmp_path)
    assert path.exists()
    back = read_levels(path)
    assert back == levels
    # Deterministic: rewriting produces byte-identical content.
    first = path.read_text()
    write_levels(levels, root=tmp_path)
    assert path.read_text() == first


# ---------------------------------------------------------------------------
# (d) No-look-ahead guard.
# ---------------------------------------------------------------------------
def test_no_look_ahead_guard_raises_on_future_asof(fixture_snapshot_dir):
    """An asof BEFORE the snapshot's source timestamps must raise LookAheadError.

    The snapshot's sources were observed in Sep 2026; asking to build "as of" a moment
    before them means a source postdates asof -> look-ahead -> raise.
    """
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(LookAheadError):
        build_levels(fixture_snapshot_dir, "QQQ", asof=past)


def test_no_look_ahead_guard_passes_at_capture_time(fixture_snapshot_dir):
    """asof at/after every source timestamp builds cleanly (no false positive)."""
    # Capture time is the outer bound of all source timestamps in the snapshot.
    future = datetime(2026, 9, 7, tzinfo=timezone.utc)
    levels = build_levels(fixture_snapshot_dir, "QQQ", asof=future)
    assert levels["instrument"] == "QQQ"
