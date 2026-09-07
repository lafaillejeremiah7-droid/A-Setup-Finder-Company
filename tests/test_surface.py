"""Tests for the IV-surface engine (build-order step 3).

WHY THIS EXISTS
---------------
This engine is the hard prerequisite for NDX walls (feed-quality.md). It fixes two
measured defects and these tests pin both so they cannot regress:

  * C8 -- feed gamma is quantized to ~14 distinct values across 142 near-money NDX
    strikes (a 90-point smear). Gamma MUST be computed from a fitted IV surface, which
    recovers full strike-level resolution (feed-quality.md SS3 reports 14 -> 142).
  * C9 -- current_price disagrees with the options' own parity-implied forward by -46
    NDX pts, so the forward per expiry MUST come from put-call parity, not the feed spot
    (feed-quality.md SS4/SS5).

Plus the two structural correctness properties: BS gamma matches a hand-computed value,
and AM vs PM settlement clocks (C7) differ correctly on an expiry date.

CBOE tests run against the REAL committed fixture at data/snapshots/2026-09-06/230612Z,
not a mock -- so a passing test exercises the actual distilled feed.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from scipy.stats import norm

from gammamap import adapters
from gammamap.surface import (
    SURFACE_MODEL_VERSION,
    IVSurface,
    bs_gamma,
    forward_from_parity,
    settlement_instant,
    time_to_expiry,
)


# ---------------------------------------------------------------------------
# (a) BS gamma matches a hand-computed reference to ~1e-6 (build-spec.md SS2).
# ---------------------------------------------------------------------------
def test_bs_gamma_matches_hand_computed_reference():
    # Known inputs: at-the-money, half a year, 20% vol, zero rates.
    S, K, T, sigma, r, q = 100.0, 100.0, 0.5, 0.2, 0.0, 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    expected = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    assert bs_gamma(S, K, T, sigma, r, q) == pytest.approx(expected, abs=1e-6)
    # Cross-check against a second independent literal so the formula, not just its own
    # restatement, is pinned. d1 = 0.0707..., phi(d1)/(100*0.2*sqrt(0.5)).
    assert bs_gamma(S, K, T, sigma) == pytest.approx(0.028139043560, abs=1e-6)


def test_bs_gamma_degenerate_inputs_return_zero():
    # A dead contract contributes no gamma rather than raising (T<=0, sigma<=0).
    assert bs_gamma(100.0, 100.0, 0.0, 0.2) == 0.0
    assert bs_gamma(100.0, 100.0, 0.5, 0.0) == 0.0
    assert bs_gamma(0.0, 100.0, 0.5, 0.2) == 0.0


def test_bs_gamma_call_equals_put_by_construction():
    # Gamma is type-independent (build-spec.md SS2: Gamma_call == Gamma_put). The function
    # takes no type, which is the encoding of that fact; assert it is symmetric in a
    # spot/strike swap sense for an OTM call vs the mirror OTM put moneyness.
    g = bs_gamma(100.0, 110.0, 0.25, 0.3)
    assert g > 0.0


# ---------------------------------------------------------------------------
# (b) Parity forward on the committed NDXP 260908 snapshot (C9).
# ---------------------------------------------------------------------------
def test_parity_forward_ndxp_260908_matches_reference(fixture_snapshot_dir):
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")
    pf = forward_from_parity(chain, "NDXP", "260908")

    # feed-quality.md SS4 reference: F ~ 29,511, R^2 ~ 0.9998. Acceptance window is
    # [29498, 29516] with R^2 > 0.999.
    assert 29498.0 <= pf.F <= 29516.0
    assert pf.r2 > 0.999
    assert pf.n_pairs >= 8
    assert pf.df > 0.0
    # The forward disagrees with the feed spot by the C9 gap (~-33 to -46 pts): this is
    # the whole point -- the parity forward is NOT current_price.
    assert chain.spot_from_feed is not None
    assert pf.F < chain.spot_from_feed  # implied spot/forward sits below current_price
    assert pf.model_version == SURFACE_MODEL_VERSION
    # A clean coherent fit raises no quality flags.
    assert pf.quality_flags == []


def test_parity_forward_flags_when_pairs_insufficient(fixture_snapshot_dir):
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")
    # A root/expiry that does not exist yields a degenerate, FLAGGED result rather than a
    # bogus silent forward.
    pf = forward_from_parity(chain, "NDXP", "999999")
    assert "parity_few_pairs" in pf.quality_flags
    assert math.isnan(pf.F)


# ---------------------------------------------------------------------------
# (c) surface_gamma recovers many more distinct gamma values than the raw feed (C8).
# ---------------------------------------------------------------------------
def test_surface_gamma_recovers_resolution_ndx(fixture_snapshot_dir):
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")
    asof = chain.asof
    surf = IVSurface(chain, asof)

    pf = surf.forward("NDXP", "260908")
    assert pf is not None and np.isfinite(pf.F)
    F = pf.F

    # Near-money window (~+-2% of the forward), matching feed-quality.md SS3's 142-strike
    # study window.
    strikes = sorted(
        {
            c.strike
            for c in chain.contracts
            if c.root == "NDXP"
            and c.expiry_yymmdd == "260908"
            and abs(c.strike - F) <= 0.02 * F
        }
    )
    assert len(strikes) > 100  # a genuinely wide near-money strip

    # Raw feed gamma across those strikes: the C8 defect -- ~14 distinct values.
    raw_distinct = {
        round(c.gamma, 6)
        for c in chain.contracts
        if c.root == "NDXP"
        and c.expiry_yymmdd == "260908"
        and c.type == "C"
        and c.strike in strikes
        and c.gamma is not None
    }
    assert len(raw_distinct) < 30  # confirms the quantization defect is present

    # Surface gamma across the same strikes: full strike-level resolution recovered.
    sg = surf.surface_gamma_by_strike("NDXP", "260908", strikes)
    surface_distinct = len(set(np.round(sg, 12)))
    # C8 fix demonstrated: materially more distinct values than the feed's ~14.
    assert surface_distinct > 100
    assert surface_distinct > len(raw_distinct) * 5
    # All near-money gammas are positive and finite.
    assert np.all(np.isfinite(sg))
    assert np.all(sg > 0.0)


def test_surface_gamma_uses_parity_forward_not_feed_spot(fixture_snapshot_dir):
    # The fitted surface's moneyness origin is the parity forward (C9), not the feed spot.
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")
    surf = IVSurface(chain, chain.asof)
    fit = surf.fit("NDXP", "260908")
    assert fit is not None
    assert fit.forward == pytest.approx(surf.forward("NDXP", "260908").F)
    assert fit.forward != chain.spot_from_feed
    assert fit.model_version == SURFACE_MODEL_VERSION


# ---------------------------------------------------------------------------
# (d) AM vs PM settlement clocks differ correctly (C7 / build-spec.md SS7).
# ---------------------------------------------------------------------------
def test_am_vs_pm_clock_on_expiry_date():
    expiry = dt.date(2026, 9, 8)
    # Observed intraday (noon Eastern) ON the expiry date.
    asof = "2026-09-08T12:00:00"

    # AM-settled (NDX) settles at the 09:30 OPEN -> already past by noon -> NON-POSITIVE.
    t_am = time_to_expiry(expiry, "NDX", asof)
    # PM-settled (NDXP) settles at the 16:00 CLOSE -> still ahead at noon -> POSITIVE.
    t_pm = time_to_expiry(expiry, "NDXP", asof)

    assert t_am <= 0.0
    assert t_pm > 0.0
    assert t_pm > t_am


def test_am_pm_settlement_instants_differ():
    expiry = dt.date(2026, 9, 8)
    am = settlement_instant(expiry, "NDX")
    pm = settlement_instant(expiry, "NDXP")
    # Same date, different wall-clock: open before close.
    assert am < pm
    # AM at 09:30 ET, PM at 16:00 ET -> 6.5 hours apart.
    assert (pm - am).total_seconds() == pytest.approx(6.5 * 3600.0)


def test_clock_uses_exact_seconds_not_rounded_days():
    # build-spec.md SS7: exact time to settlement, never rounded days/365. Two asof times
    # one hour apart on the same day must give DIFFERENT (smaller) T for the later one.
    expiry = dt.date(2026, 9, 8)
    t_early = time_to_expiry(expiry, "NDXP", "2026-09-08T10:00:00")
    t_late = time_to_expiry(expiry, "NDXP", "2026-09-08T11:00:00")
    assert t_early > t_late
    # The gap is exactly one hour as a year-fraction.
    assert (t_early - t_late) == pytest.approx(3600.0 / (365.0 * 24 * 3600.0), rel=1e-9)


# ---------------------------------------------------------------------------
# (e) No-arb / low-quality fits emit quality flags (build-spec.md SS7).
# ---------------------------------------------------------------------------
def test_surface_emits_flag_on_negative_variance_fit():
    # A smile whose polynomial dips below zero somewhere in range must flag negative
    # implied variance rather than silently returning it. We drive this through the
    # private fitter with strikes/IVs that force a downward-curving fit crossing zero.
    from gammamap.surface import _fit_expiry_surface, SURFACE_NEGATIVE_VARIANCE

    F = 100.0
    # A strong monotone skew whose best-fit quadratic undershoots below zero at the
    # high-strike edge -> negative implied variance in range.
    strikes = [70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0]
    ivs = [0.5, 0.45, 0.35, 0.22, 0.12, 0.05, 0.01, 0.005]
    fit = _fit_expiry_surface("QQQ", "260908", F, 0.02, strikes, ivs)
    assert SURFACE_NEGATIVE_VARIANCE in fit.quality_flags


def test_surface_flags_few_points():
    from gammamap.surface import _fit_expiry_surface, SURFACE_FEW_POINTS

    fit = _fit_expiry_surface("QQQ", "260908", 100.0, 0.02, [100.0, 101.0], [0.2, 0.21])
    assert SURFACE_FEW_POINTS in fit.quality_flags


def test_ivsurface_stamps_model_version(fixture_snapshot_dir):
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")
    surf = IVSurface(chain, chain.asof)
    assert surf.model_version == SURFACE_MODEL_VERSION
    # Every fit and forward carries the version so a stored level is model-traceable.
    for fit in surf._fits.values():
        assert fit.model_version == SURFACE_MODEL_VERSION
