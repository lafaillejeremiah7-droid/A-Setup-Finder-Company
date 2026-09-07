"""Tests for the GEX engine (build-order step 4).

WHY THIS EXISTS
---------------
These tests pin the build-spec Appendix reference points and the measured corrections so
the engine cannot regress:

  * The $GEX/1% formula round-trips numerically, including the appendix's -$7.50B figure
    and its 250-NQ-contracts translation (build-spec.md SS2 / Appendix).
  * The 4-wall selector returns the true max-|GEX| strike per side, one line, no zones
    (build-spec.md SS9a).
  * The MaxPain helper reproduces the appendix MaxPain=100 example using ONLY strike, OI
    and intrinsic payout -- never gamma/IV/sign (build-spec.md SS2, C4).
  * The flip solver finds NetGEX~=0 on a constructed monotone curve, reads the ACTUAL
    sign each side, and reports multiple roots when present (build-spec.md SS5).
  * GlobalShare bands: a dominant strike lands EXTREME, a flat chain lands WEAK, and the
    +-S*sigma*sqrt(T) resolution floor lowers confidence for near-equal neighbours
    (build-spec.md SS4/SS0/SS9a, findings 3).
  * 0DTE for NDX uses only the NDXP PM-settled root and excludes AM-settled NDX on its
    expiry date (C7 / build-spec.md SS6).
  * A Kaggle (no-OI) chain yields GEX tagged proxy=True (C1).

Constructed chains are used for the numeric reference points so the expected values are
exact; the proxy path is exercised against a small real-format Kaggle CSV string.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from gammamap import adapters
from gammamap.chain import NormalizedChain, NormalizedContract
from gammamap.gex import (
    DEFAULT_MULTIPLIER,
    GEX_VOLUME_PROXY,
    FLIP_MULTIPLE_ROOTS,
    RESOLUTION_FLOOR_TIE,
    STRENGTH_UNCALIBRATED,
    GammaFlip,
    Regime,
    SignModel,
    Strength,
    assess_regime,
    assess_strength,
    band_for_share,
    compute_strike_gex,
    feed_gamma_source,
    gex_to_nq_contracts,
    global_share,
    max_pain,
    select_0dte_universe,
    select_total_universe,
    select_wall,
    sign_for,
    solve_gamma_flip,
)


# ---------------------------------------------------------------------------
# Small constructors so each test states its chain inline and readably.
# ---------------------------------------------------------------------------
def _contract(
    root, type_, strike, *, oi=None, gamma=None, volume=None, expiry=date(2026, 9, 8)
):
    return NormalizedContract(
        root=root,
        expiry=expiry,
        expiry_yymmdd=expiry.strftime("%y%m%d"),
        type=type_,
        strike=float(strike),
        open_interest=oi,
        gamma=gamma,
        delta=None,
        iv=None,
        bid=None,
        ask=None,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# (a) GEX round-trips numerically, incl. -$7.50B and the 250-NQ-contracts example.
# ---------------------------------------------------------------------------
def test_gex_formula_roundtrips_minus_7_5_billion():
    # build-spec.md SS2 Appendix: GEX = s*Gamma*OI*M*S^2*0.01. A single dealer-short put
    # at S=25000 with Gamma*OI=12.0 yields exactly -$7.50B ($ delta per 1% move).
    #   -1 * 0.0006 * 20000 * 100 * 25000^2 * 0.01 = -7.5e9
    S = 25000.0
    put = _contract("NDXP", "P", 25000.0, oi=20000, gamma=0.0006)
    prof = compute_strike_gex(
        [put], spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    assert prof.net_gex == pytest.approx(-7.5e9, rel=1e-9)
    # GrossGEX is the magnitude; NetGEX is signed negative (put, dealer-short-gamma).
    assert prof.gross_gex == pytest.approx(7.5e9, rel=1e-9)
    assert prof.net_gex < 0.0


def test_gex_translates_to_250_nq_contracts():
    # The appendix's 250-NQ-contracts translation: a $GEX/1% figure expressed as
    # NQ-contract-equivalents hedged per 1% move. NQ = $20/pt; a 1% move at S covers
    # 0.01*S pts. Construct a call whose GEX -> exactly 250 contracts at S=25000:
    #   GEX = 250 * 20 * 0.01 * 25000 = 1.25e6 ; Gamma*OI = 0.002 -> gamma=0.0001, OI=20.
    S = 25000.0
    call = _contract("NDXP", "C", 26000.0, oi=20, gamma=0.0001)
    prof = compute_strike_gex(
        [call], spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    assert prof.net_gex == pytest.approx(1.25e6, rel=1e-9)
    assert gex_to_nq_contracts(prof.net_gex, S) == pytest.approx(250.0, rel=1e-9)


def test_gex_sign_convention_is_swappable():
    # C6 / build-spec.md SS5: the sign is a dealer POSITIONING assumption, not option
    # gamma. Under the standard model call=+, put=-. The global negation flips both.
    assert sign_for("C", SignModel.DEALER_SHORT_GAMMA) == 1.0
    assert sign_for("P", SignModel.DEALER_SHORT_GAMMA) == -1.0
    assert sign_for("C", SignModel.DEALER_LONG_GAMMA) == -1.0
    assert sign_for("P", SignModel.DEALER_LONG_GAMMA) == 1.0

    S = 25000.0
    put = _contract("NDXP", "P", 25000.0, oi=20000, gamma=0.0006)
    short = compute_strike_gex(
        [put], spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    long = compute_strike_gex(
        [put], spot=S, gamma_source=feed_gamma_source, oi_available=True,
        universe="total", sign_model=SignModel.DEALER_LONG_GAMMA,
    )
    # A global sign flip negates NetGEX exactly (findings contribution 4).
    assert long.net_gex == pytest.approx(-short.net_gex, rel=1e-12)


# ---------------------------------------------------------------------------
# (b) 4-wall selection picks the true max-|GEX| strike per side (build-spec.md SS9a).
# ---------------------------------------------------------------------------
def _four_wall_chain():
    # Total universe (weekly, expires later) with a known dominant call and put strike,
    # plus a 0DTE universe (expires today) with DIFFERENT dominant strikes so the four
    # walls are distinguishable.
    today = date(2026, 9, 8)
    later = date(2026, 9, 18)
    contracts = [
        # Total call side: 26200 dominates (big OI*gamma), 26100 a smaller peak.
        _contract("QQQ", "C", 26100.0, oi=1000, gamma=0.0010, expiry=later),
        _contract("QQQ", "C", 26200.0, oi=5000, gamma=0.0010, expiry=later),
        # Total put side: 25800 dominates.
        _contract("QQQ", "P", 25900.0, oi=1200, gamma=0.0010, expiry=later),
        _contract("QQQ", "P", 25800.0, oi=4000, gamma=0.0010, expiry=later),
        # 0DTE call side: 26150 dominates.
        _contract("QQQ", "C", 26150.0, oi=3000, gamma=0.0010, expiry=today),
        _contract("QQQ", "C", 26050.0, oi=500, gamma=0.0010, expiry=today),
        # 0DTE put side: 25850 dominates.
        _contract("QQQ", "P", 25850.0, oi=2500, gamma=0.0010, expiry=today),
        _contract("QQQ", "P", 25950.0, oi=400, gamma=0.0010, expiry=today),
    ]
    return NormalizedChain(
        symbol="QQQ", asof="2026-09-08T12:00:00", spot_from_feed=26000.0,
        contracts=contracts, oi_available=True,
    )


def test_four_wall_selection_picks_true_max_gex_per_side():
    chain = _four_wall_chain()
    today = date(2026, 9, 8)
    S = 26000.0

    total = compute_strike_gex(
        select_total_universe(chain, today), spot=S,
        gamma_source=feed_gamma_source, oi_available=True, universe="total",
    )
    dte = compute_strike_gex(
        select_0dte_universe(chain, today), spot=S,
        gamma_source=feed_gamma_source, oi_available=True, universe="0dte",
    )

    call_wall = select_wall(total, "call")
    put_wall = select_wall(total, "put")
    dte_call = select_wall(dte, "call")
    dte_put = select_wall(dte, "put")

    assert call_wall.strike == 26200.0
    assert put_wall.strike == 25800.0
    assert dte_call.strike == 26150.0
    assert dte_put.strike == 25850.0

    # Sides carry the right sign: call wall positive, put wall negative (dealer-short).
    assert call_wall.signed_gex > 0.0
    assert put_wall.signed_gex < 0.0
    # Single line, no zone: the wall is one strike, not a range.
    assert isinstance(call_wall.strike, float)


def test_wall_empty_side_returns_none_strike():
    # A universe with no puts yields a put wall with strike None rather than a fabricated
    # level.
    S = 26000.0
    calls = [_contract("QQQ", "C", 26200.0, oi=100, gamma=0.001)]
    prof = compute_strike_gex(
        calls, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    put_wall = select_wall(prof, "put")
    assert put_wall.strike is None
    assert put_wall.abs_gex == 0.0


# ---------------------------------------------------------------------------
# (c) MaxPain helper reproduces the appendix MaxPain=100 example (build-spec.md SS2, C4).
# ---------------------------------------------------------------------------
def test_max_pain_returns_100_for_appendix_example():
    # Pain(P) = sum_calls OI*max(P-K,0)*M + sum_puts OI*max(K-P,0)*M ; MaxPain = argmin.
    # Symmetric OI around 100 -> minimum total intrinsic payout at P=100.
    contracts = []
    for K, oi in [(90, 10), (100, 10), (110, 10)]:
        contracts.append(_contract("X", "C", K, oi=oi))
        contracts.append(_contract("X", "P", K, oi=oi))
    assert max_pain(contracts) == 100.0


def test_max_pain_ignores_gamma_and_iv():
    # C4: Max Pain uses ONLY strike/OI/intrinsic. Adding gamma to the contracts must not
    # move the result -- if IV changes and OI does not, Max Pain does not move.
    a = [
        _contract("X", "C", 90, oi=10, gamma=0.05),
        _contract("X", "C", 110, oi=10, gamma=0.05),
        _contract("X", "P", 90, oi=10, gamma=0.9),
        _contract("X", "P", 110, oi=10, gamma=0.9),
        _contract("X", "C", 100, oi=10),
        _contract("X", "P", 100, oi=10),
    ]
    assert max_pain(a) == 100.0


def test_max_pain_none_on_empty():
    assert max_pain([]) is None


# ---------------------------------------------------------------------------
# (d) Gamma flip: NetGEX~=0 on a constructed curve; multiple roots reported (SS5).
# ---------------------------------------------------------------------------
def _triangular_gamma(peak=0.01, slope=0.0005):
    """A gamma_at closure: triangular gamma peaked at each contract's strike.

    Used to build a controllable NetGEX(S) curve for the flip scan. The key property the
    solver relies on is that gamma is RECOMPUTED at each hypothetical spot S.
    """

    def _g(contract, S):
        return max(0.0, peak - abs(S - contract.strike) * slope)

    return _g


def test_flip_finds_single_root_and_reads_actual_signs():
    # A put below and a call above cross NetGEX through zero once, near 100. The solver
    # must locate it AND read the actual sign each side (negative below, positive above)
    # rather than hardcoding it (build-spec.md SS5).
    put = _contract("X", "P", 95.0, oi=100)
    call = _contract("X", "C", 105.0, oi=100)
    flip = solve_gamma_flip(
        [put, call], gamma_at=_triangular_gamma(), oi_available=True,
        grid_min=90.0, grid_max=110.0, reference_spot=100.0, n_grid=801,
    )
    assert flip.flip == pytest.approx(100.0, abs=0.1)
    assert len(flip.all_roots) == 1
    # Signs READ from the curve, not assumed.
    assert flip.sign_below == -1.0
    assert flip.sign_above == 1.0
    # NetGEX at the reported flip is ~= 0 within tolerance.
    from gammamap.gex import net_gex_at_spot

    net_at_flip = net_gex_at_spot(
        [put, call], flip.flip, gamma_at=_triangular_gamma(), oi_available=True
    )
    assert abs(net_at_flip) < 1e6  # small relative to the ~1e9-scale curve
    assert flip.surface_dynamics == "sticky_strike"


def test_flip_reports_multiple_roots():
    # A curve engineered to cross zero more than once: three narrow triangular peaks with
    # alternating dealer sign produce multiple sign changes across the grid.
    put_lo = _contract("X", "P", 92.0, oi=100)
    call_mid = _contract("X", "C", 100.0, oi=100)
    put_hi = _contract("X", "P", 108.0, oi=100)
    flip = solve_gamma_flip(
        [put_lo, call_mid, put_hi],
        gamma_at=_triangular_gamma(peak=0.02, slope=0.004),
        oi_available=True,
        grid_min=88.0, grid_max=112.0, reference_spot=100.0, n_grid=961,
    )
    assert len(flip.all_roots) >= 2
    assert FLIP_MULTIPLE_ROOTS in flip.quality_flags
    # The chosen root is the one nearest the reference spot.
    nearest = min(flip.all_roots, key=lambda r: abs(r - 100.0))
    assert flip.flip == pytest.approx(nearest)


def test_flip_no_root_flagged():
    # An all-call (all positive) curve never crosses zero -> no flip, flagged.
    call = _contract("X", "C", 100.0, oi=100)
    flip = solve_gamma_flip(
        [call], gamma_at=_triangular_gamma(), oi_available=True,
        grid_min=90.0, grid_max=110.0, reference_spot=100.0, n_grid=401,
    )
    assert flip.flip is None
    assert "flip_no_root" in flip.quality_flags


# ---------------------------------------------------------------------------
# (e) GlobalShare bands: dominant -> EXTREME, flat -> WEAK (build-spec.md SS4/SS0).
# ---------------------------------------------------------------------------
def test_global_share_dominant_strike_is_extreme():
    S = 26000.0
    # One strike carries ~90% of the gross |GEX|.
    contracts = [
        _contract("QQQ", "C", 26200.0, oi=9000, gamma=0.001),
        _contract("QQQ", "C", 26100.0, oi=500, gamma=0.001),
        _contract("QQQ", "C", 26300.0, oi=500, gamma=0.001),
    ]
    prof = compute_strike_gex(
        contracts, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    wall = select_wall(prof, "call")
    strength = assess_strength(prof, wall, "call")
    assert strength.band is Strength.EXTREME
    assert strength.global_share > 0.25
    # Phase-1 bands are explicitly uncalibrated (share-based, not quantile-based).
    assert strength.uncalibrated is True
    assert STRENGTH_UNCALIBRATED in strength.flags


def test_global_share_flat_chain_is_weak():
    S = 26000.0
    # Many equal strikes -> no strike dominates -> WEAK.
    contracts = [
        _contract("QQQ", "C", 26000.0 + 50 * i, oi=1000, gamma=0.001) for i in range(20)
    ]
    prof = compute_strike_gex(
        contracts, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    wall = select_wall(prof, "call")
    strength = assess_strength(prof, wall, "call")
    assert strength.band is Strength.WEAK
    assert strength.global_share < 0.07


def test_band_thresholds_monotone():
    # The documented Phase-1 thresholds map shares to the four bands in order.
    assert band_for_share(0.30) is Strength.EXTREME
    assert band_for_share(0.18) is Strength.STRONG
    assert band_for_share(0.10) is Strength.MODERATE
    assert band_for_share(0.02) is Strength.WEAK
    assert global_share(50.0, 200.0) == pytest.approx(0.25)
    assert global_share(1.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# (f) Resolution-floor confidence lowers for near-equal adjacent strikes (findings 3).
# ---------------------------------------------------------------------------
def test_resolution_floor_lowers_confidence_for_near_equal_neighbours():
    S = 26000.0
    sigma = 0.20
    T = 1.0 / 365.0  # ~1 day: floor = S*sigma*sqrt(T) ~ 272 index points
    # Two adjacent strikes 50 pts apart (well within the floor) with near-equal |GEX|:
    # the #1 line is not meaningfully distinguishable from #2 -> confidence lowered.
    near = [
        _contract("QQQ", "C", 26000.0, oi=5000, gamma=0.001),
        _contract("QQQ", "C", 26050.0, oi=4800, gamma=0.001),
    ]
    prof_near = compute_strike_gex(
        near, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    wall_near = select_wall(prof_near, "call")
    s_near = assess_strength(prof_near, wall_near, "call", sigma=sigma, T=T)
    assert s_near.confidence == "lowered"
    assert RESOLUTION_FLOOR_TIE in s_near.flags

    # A single dominant strike (no near-equal neighbour) keeps high confidence.
    sep = [
        _contract("QQQ", "C", 26000.0, oi=9000, gamma=0.001),
        _contract("QQQ", "C", 27000.0, oi=200, gamma=0.001),
    ]
    prof_sep = compute_strike_gex(
        sep, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    wall_sep = select_wall(prof_sep, "call")
    s_sep = assess_strength(prof_sep, wall_sep, "call", sigma=sigma, T=T)
    assert s_sep.confidence == "high"
    assert RESOLUTION_FLOOR_TIE not in s_sep.flags


# ---------------------------------------------------------------------------
# (g) 0DTE for NDX uses only the NDXP PM-settled root (C7 / build-spec.md SS6).
# ---------------------------------------------------------------------------
def test_0dte_ndx_uses_only_pm_settled_ndxp_root():
    today = date(2026, 9, 8)
    later = date(2026, 9, 18)
    contracts = [
        # AM-settled NDX expiring TODAY: already settled at the open -> NOT 0DTE-live (C7).
        _contract("NDX", "C", 29500.0, oi=1000, gamma=0.0005, expiry=today),
        # PM-settled NDXP expiring today: the genuine daily 0DTE.
        _contract("NDXP", "C", 29600.0, oi=1000, gamma=0.0005, expiry=today),
        _contract("NDXP", "P", 29400.0, oi=1000, gamma=0.0005, expiry=today),
        # A later NDX monthly: part of Total, not 0DTE.
        _contract("NDX", "C", 30000.0, oi=1000, gamma=0.0005, expiry=later),
    ]
    chain = NormalizedChain(
        symbol="_NDX", asof="2026-09-08T12:00:00", spot_from_feed=29500.0,
        contracts=contracts, oi_available=True,
    )

    dte = select_0dte_universe(chain, today)
    roots = {c.root for c in dte}
    strikes = {c.strike for c in dte}
    # Only NDXP survives in 0DTE; the AM-settled NDX today-expiry is excluded.
    assert roots == {"NDXP"}
    assert 29500.0 not in strikes  # the AM-settled NDX 0DTE contract is gone (not fabricated)
    assert 29600.0 in strikes and 29400.0 in strikes

    # Total still includes the live AM-settled monthly (expires later) but NOT the
    # already-settled AM contract that expired today.
    total = select_total_universe(chain, today)
    total_keys = {(c.root, c.strike) for c in total}
    assert ("NDX", 30000.0) in total_keys
    assert ("NDX", 29500.0) not in total_keys  # AM-settled, expired at today's open


# ---------------------------------------------------------------------------
# Regime: TOTAL / 0DTE signs and divergence flag (build-spec.md SS6).
# ---------------------------------------------------------------------------
def test_regime_divergence_flagged_when_signs_disagree():
    S = 26000.0
    # Total leans positive (call-heavy), 0DTE leans negative (put-heavy) -> divergence.
    total_c = [_contract("QQQ", "C", 26200.0, oi=9000, gamma=0.001)]
    dte_c = [_contract("QQQ", "P", 25800.0, oi=9000, gamma=0.001, expiry=date(2026, 9, 8))]
    total = compute_strike_gex(total_c, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total")
    dte = compute_strike_gex(dte_c, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="0dte")
    regime = assess_regime(total, dte)
    assert regime.total_sign == "+"
    assert regime.dte_sign == "-"
    assert regime.divergence is True


def test_regime_no_divergence_when_signs_agree():
    S = 26000.0
    total_c = [_contract("QQQ", "C", 26200.0, oi=9000, gamma=0.001)]
    dte_c = [_contract("QQQ", "C", 26150.0, oi=5000, gamma=0.001, expiry=date(2026, 9, 8))]
    total = compute_strike_gex(total_c, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total")
    dte = compute_strike_gex(dte_c, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="0dte")
    regime = assess_regime(total, dte)
    assert regime.total_sign == "+" and regime.dte_sign == "+"
    assert regime.divergence is False


# ---------------------------------------------------------------------------
# (h) Kaggle (no-OI) chain yields GEX tagged proxy=True (C1).
# ---------------------------------------------------------------------------
_KAGGLE_CSV = (
    "[QUOTE_DATE],[UNDERLYING_LAST],[EXPIRE_DATE],[STRIKE],"
    "[C_GAMMA],[C_VOLUME],[P_GAMMA],[P_VOLUME]\n"
    "2021-06-01,335.0,2021-06-04,330.0,0.02,1500,0.02,900\n"
    "2021-06-01,335.0,2021-06-04,335.0,0.05,5000,0.05,4200\n"
    "2021-06-01,335.0,2021-06-04,340.0,0.02,800,0.02,1100\n"
)


def test_kaggle_chain_yields_proxy_gex_never_true_oi():
    chain = adapters.from_kaggle_csv(_KAGGLE_CSV)
    assert chain.oi_available is False  # no OI column in the Kaggle source

    S = chain.spot_from_feed
    prof = compute_strike_gex(
        chain.contracts, spot=S, gamma_source=feed_gamma_source,
        oi_available=chain.oi_available, universe="total",
    )
    # The result MUST be tagged a volume proxy, never presented as classic OI-GEX (C1).
    assert prof.proxy is True
    assert GEX_VOLUME_PROXY in prof.quality_flags
    # The proxy is weighted by VOLUME: the 335 strike (highest volume) dominates.
    call_wall = select_wall(prof, "call")
    assert call_wall.strike == 335.0
    assert call_wall.proxy is True


def test_true_oi_chain_is_not_flagged_proxy():
    S = 26000.0
    contracts = [_contract("QQQ", "C", 26200.0, oi=1000, gamma=0.001)]
    prof = compute_strike_gex(
        contracts, spot=S, gamma_source=feed_gamma_source, oi_available=True, universe="total"
    )
    assert prof.proxy is False
    assert GEX_VOLUME_PROXY not in prof.quality_flags


# ---------------------------------------------------------------------------
# Multiplier is 100 for both QQQ and NDX index options (build-spec.md SS2).
# ---------------------------------------------------------------------------
def test_default_multiplier_is_100():
    assert DEFAULT_MULTIPLIER == 100.0
