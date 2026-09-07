"""GEX engine for The Gamma Map (build-order step 4, build-spec.md SS2/SS3/SS5/SS9a).

WHY THIS EXISTS
---------------
This module turns a NormalizedChain into the exact objects the indicator renders: the
four wall lines, the gamma-flip line, the TOTAL/0DTE regime, and a within-snapshot
strength rating. It is the one place that applies the dealer sign convention and the
$GEX/1% formula, so every number downstream carries a single declared convention
(build-spec.md SS2.1.1: "$5B GEX is meaningless without its convention").

The formula (build-spec.md SS2, findings CONFIRMED table):

    GEX_i = s_i * Gamma_i * OI_i * M_i * S^2 * 0.01        # $ delta per 1% move
    NetGEX  = sum(GEX_i)     GrossGEX = sum(|GEX_i|)       # report BOTH (SS2.1.1)

Load-bearing decisions, each traceable to a measured correction:

  * Sign is a DEALER convention, not option gamma (C6 / build-spec.md SS1.0). Option
    gamma is positive for a long call AND a long put; Gamma_call == Gamma_put (proven).
    So s_i is a POSITIONING assumption: under the standard dealer-short-gamma model
    calls are +1 and puts -1. It is kept an explicit, swappable parameter (SignModel) so
    an alternative can be stressed for the flip (build-spec.md SS5).

  * Gamma is a CALLABLE, not read blindly from the feed (C8 / build-spec.md SS10). QQQ
    v1 passes the clean feed gamma; NDX passes IVSurface.surface_gamma, because the
    feed's NDX gamma is quantized to ~14 values. This module never decides the source --
    the caller injects it, so the C8 fix lives in one place (the surface) and cannot be
    bypassed here.

  * OI vs volume PROXY (C1 / chain.oi_available). True OI when the chain has it; when it
    does not (Kaggle), volume is substituted and the result is tagged proxy=True with the
    'oi_absent_volume_proxy' flag. A proxy wall is NEVER presented as classic OI-GEX.

  * 0DTE for NDX is the NDXP PM-settled root only (C7 / build-spec.md SS6). An AM-settled
    NDX contract has already stopped trading on its own expiry date, so summing it into
    "0DTE gamma" fabricates exposure no dealer is hedging. We do not fabricate it.

  * Strength = within-snapshot GlobalShare, not probability (C1 / build-spec.md SS4/SS0).
    Phase-1 bands are share thresholds, explicitly marked 'uncalibrated' because the
    paper's bands are ultimately quantile-based on history we do not yet have. The
    +-S*sigma*sqrt(T) resolution floor (findings contribution 3) lowers CONFIDENCE when
    the #1 and #2 strikes are indistinguishably close with near-equal |GEX|; it does NOT
    set a zone width (the no-zones simplification, SS9a).

stdlib-first + numpy, dataclasses, quality flags emitted not silently fixed -- matching
surface.py / chain.py / adapters.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Iterable, Sequence

import numpy as np

from .chain import (
    AM_SETTLED_ROOTS,
    OI_ABSENT_VOLUME_PROXY,
    NormalizedChain,
    NormalizedContract,
)

# ---------------------------------------------------------------------------
# Versioning (build-spec.md SS7: stamp the model into every stored level).
# ---------------------------------------------------------------------------
GEX_MODEL_VERSION = "gex-1.0.0"

# Index-option contract multiplier. build-spec.md SS2 / user spec: M = 100 for BOTH QQQ
# and NDX index options ($100 per index point of premium). NQ/MNQ futures multipliers
# ($20 / $2 per point) are a SEPARATE mapping concern (build-spec.md SS2.1) handled by the
# mapping engine, never mixed into the option-side GEX here.
DEFAULT_MULTIPLIER = 100.0

# The one declared unit for every GEX number this module emits (build-spec.md SS2.1.1 /
# SS9a: "$1.82B GEX / 1%"). Dollars of dealer delta that must be hedged per 1% move in S.
GEX_UNIT = "usd_delta_per_1pct_move"

# Quality-flag string constants (emitted, never used to silently correct a value).
GEX_VOLUME_PROXY = OI_ABSENT_VOLUME_PROXY  # re-export the chain flag under the engine ns
GEX_NO_CONTRACTS = "gex_no_contracts"
GEX_ALT_SIGN_MODEL = "gex_alternative_sign_model"
STRENGTH_UNCALIBRATED = "strength_uncalibrated"  # Phase-1 bands are share, not quantile
RESOLUTION_FLOOR_TIE = "resolution_floor_near_equal_neighbour"
FLIP_MULTIPLE_ROOTS = "flip_multiple_roots"
FLIP_NO_ROOT = "flip_no_root"


# ---------------------------------------------------------------------------
# Sign model -- the dealer POSITIONING convention (C6 / build-spec.md SS1.0, SS5).
# ---------------------------------------------------------------------------
class SignModel(Enum):
    """Dealer sign convention applied to option gamma.

    Option gamma itself is positive for both a long call and a long put (C6); the sign
    here encodes an ASSUMPTION about which side of the option the dealer is on. Kept an
    explicit enum, not a hardcoded +/-, so the flip scan can stress an alternative and so
    the flip-root invariance under a GLOBAL sign flip (findings contribution 4) is
    testable rather than assumed.
    """

    # Standard dealer-short-gamma: dealers are net short calls (customers buy them) and
    # this desk models calls +1 / puts -1. This is the default the paper's dashboards use.
    DEALER_SHORT_GAMMA = "dealer_short_gamma"
    # The global negation. Roots of NetGEX(S) are invariant under this (proven), only the
    # regime labels invert -- used to demonstrate/stress that invariance.
    DEALER_LONG_GAMMA = "dealer_long_gamma"


def sign_for(contract_type: str, model: SignModel = SignModel.DEALER_SHORT_GAMMA) -> float:
    """Return s_i in {+1, -1} for a contract type under the chosen dealer sign model.

    DEALER_SHORT_GAMMA: call -> +1, put -> -1 (the standard convention, build-spec SS1.0).
    DEALER_LONG_GAMMA:  the global negation (call -> -1, put -> +1).
    A malformed type contributes +1 magnitude with no assumed direction rather than
    raising -- the caller sees the contract in the aggregate regardless.
    """
    base = 1.0 if contract_type == "C" else (-1.0 if contract_type == "P" else 1.0)
    if model is SignModel.DEALER_LONG_GAMMA:
        return -base
    return base


# A gamma source is a callable (contract, spot) -> gamma. QQQ v1 injects the feed gamma;
# NDX injects a surface-gamma closure. This module never chooses -- see feed_gamma_source
# and surface_gamma_source below.
GammaSource = Callable[[NormalizedContract, float], float]


def feed_gamma_source(contract: NormalizedContract, spot: float) -> float:
    """Gamma straight from the feed (QQQ v1 path).

    Valid ONLY for QQQ, whose feed gamma is clean (~0.2% of peak, feed-quality.md). Must
    NEVER be used for NDX, whose feed gamma is quantized to ~14 values (C8) -- use
    surface_gamma_source for that. `spot` is accepted for interface symmetry and ignored:
    the feed gamma is already a fixed per-contract number.
    """
    return float(contract.gamma) if contract.gamma is not None else 0.0


def surface_gamma_source(surface, *, r: float = 0.0, q: float = 0.0) -> GammaSource:
    """Build a gamma source backed by a fitted IVSurface (NDX path, the C8 fix).

    Returns a closure that, per contract, evaluates surface.surface_gamma(root, expiry,
    strike) -- fitted IV -> BS gamma at the parity forward and exact clock. `spot` is
    ignored because the surface already uses the parity forward per expiry as its spot
    (C9); passing the feed spot here would reintroduce the exact error C9 forbids.
    """

    def _src(contract: NormalizedContract, spot: float) -> float:
        return float(
            surface.surface_gamma(
                contract.root, contract.expiry_yymmdd, contract.strike, r=r, q=q
            )
        )

    return _src


# ---------------------------------------------------------------------------
# Per-strike GEX aggregation.
# ---------------------------------------------------------------------------
@dataclass
class StrikeGEX:
    """Aggregated signed + gross GEX at one strike (summed over contract types/expiries).

    net   -- signed $GEX/1% at this strike (calls and puts netted under the sign model).
    gross -- sum of |per-contract GEX| at this strike (for GrossGEX and share math).
    call_gex / put_gex -- side-specific signed contributions, so the wall selector can
             rank a strike by its CALL |GEX| or its PUT |GEX| independently.
    """

    strike: float
    net: float = 0.0
    gross: float = 0.0
    call_gex: float = 0.0
    put_gex: float = 0.0


@dataclass
class GEXProfile:
    """The per-strike GEX curve for one universe (Total or 0DTE), plus its aggregates.

    proxy -- True when built from a volume proxy (no OI). NEVER present as classic OI-GEX.
    spot  -- the reference S used in S^2 (parity forward when available, else feed spot).
    """

    universe: str  # "total" or "0dte"
    strikes: list[float]
    by_strike: dict[float, StrikeGEX]
    net_gex: float
    gross_gex: float
    spot: float
    multiplier: float
    sign_model: SignModel
    proxy: bool
    n_contracts: int
    quality_flags: list[str] = field(default_factory=list)
    model_version: str = GEX_MODEL_VERSION

    def net_by_strike(self) -> np.ndarray:
        return np.array([self.by_strike[k].net for k in self.strikes], dtype=float)


def _oi_or_proxy(contract: NormalizedContract, oi_available: bool) -> tuple[float, bool]:
    """Return (weight, used_proxy). True OI when available; else volume as a proxy (C1).

    used_proxy is True whenever the volume fallback was taken, so the caller can tag the
    whole profile proxy=True. A missing OI on an OI-available chain contributes 0 (an
    absent join, distinct from OI=0) rather than silently borrowing volume.
    """
    if oi_available:
        return (float(contract.open_interest) if contract.open_interest is not None else 0.0, False)
    # No OI column at all: substitute volume as an explicit proxy weight.
    return (float(contract.volume) if contract.volume is not None else 0.0, True)


def compute_strike_gex(
    contracts: Iterable[NormalizedContract],
    *,
    spot: float,
    gamma_source: GammaSource,
    oi_available: bool,
    universe: str,
    sign_model: SignModel = SignModel.DEALER_SHORT_GAMMA,
    multiplier: float = DEFAULT_MULTIPLIER,
    extra_flags: Sequence[str] = (),
) -> GEXProfile:
    """Aggregate signed + gross GEX by strike over `contracts` (build-spec.md SS2).

    GEX_i = s_i * Gamma_i * W_i * M * S^2 * 0.01, where W_i is OI (true) or volume (proxy).
    Emits proxy=True + the volume-proxy flag when any weight came from volume, so a proxy
    profile can never be mistaken for OI-GEX downstream.
    """
    by_strike: dict[float, StrikeGEX] = {}
    used_proxy = False
    n = 0
    s2 = spot * spot

    for c in contracts:
        n += 1
        gamma = gamma_source(c, spot)
        if gamma is None or gamma <= 0.0:
            # A dead / degenerate contract contributes no gamma (and no GEX). The strike
            # still exists in the universe but adds nothing here.
            gamma = 0.0
        weight, proxied = _oi_or_proxy(c, oi_available)
        used_proxy = used_proxy or proxied

        s = sign_for(c.type, sign_model)
        gex = s * gamma * weight * multiplier * s2 * 0.01

        agg = by_strike.get(c.strike)
        if agg is None:
            agg = StrikeGEX(strike=c.strike)
            by_strike[c.strike] = agg
        agg.net += gex
        agg.gross += abs(gex)
        if c.type == "C":
            agg.call_gex += gex
        elif c.type == "P":
            agg.put_gex += gex

    strikes = sorted(by_strike)
    net_gex = float(sum(a.net for a in by_strike.values()))
    gross_gex = float(sum(a.gross for a in by_strike.values()))

    flags = list(extra_flags)
    if used_proxy and GEX_VOLUME_PROXY not in flags:
        flags.append(GEX_VOLUME_PROXY)
    if sign_model is not SignModel.DEALER_SHORT_GAMMA:
        flags.append(GEX_ALT_SIGN_MODEL)
    if n == 0:
        flags.append(GEX_NO_CONTRACTS)

    return GEXProfile(
        universe=universe,
        strikes=strikes,
        by_strike=by_strike,
        net_gex=net_gex,
        gross_gex=gross_gex,
        spot=spot,
        multiplier=multiplier,
        sign_model=sign_model,
        proxy=used_proxy,
        n_contracts=n,
        quality_flags=flags,
    )


# ---------------------------------------------------------------------------
# NQ-contract translation of a dollar-GEX figure (build-spec.md SS2 / Appendix).
# ---------------------------------------------------------------------------
# NQ (and MNQ) trade at $20 per index point (NQ; MNQ is $2 but shares the price level,
# build-spec.md SS2.1). A GEX figure is dollars of dealer delta per 1% move; expressing it
# as "NQ contracts a dealer must trade per 1% move" is the appendix's intuition pump.
NQ_POINT_VALUE = 20.0


def gex_to_nq_contracts(gex_usd_per_1pct: float, spot: float, point_value: float = NQ_POINT_VALUE) -> float:
    """Convert a $GEX/1% figure to NQ-contract-equivalents hedged per 1% move.

    A 1% move covers 0.01*S index points; one NQ contract's dollar exposure over that
    move is point_value * 0.01 * S. So contracts = GEX / (point_value * 0.01 * S). This
    is the appendix's "250 NQ contracts" translation of a dollar-GEX magnitude, kept as a
    pure helper so the units are auditable in one place.
    """
    denom = point_value * 0.01 * spot
    if denom == 0.0:
        return 0.0
    return gex_usd_per_1pct / denom


# ---------------------------------------------------------------------------
# Max Pain (build-spec.md SS2). Diagnostic only -- NOT the flip (C4). No gamma/IV/sign.
# ---------------------------------------------------------------------------
def max_pain(
    contracts: Iterable[NormalizedContract],
    *,
    multiplier: float = DEFAULT_MULTIPLIER,
    candidate_strikes: Sequence[float] | None = None,
) -> float | None:
    """MaxPain = argmin_P Pain(P), Pain(P) = sum_calls OI*max(P-K,0)*M + sum_puts OI*max(K-P,0)*M.

    Uses ONLY strike, OI, intrinsic payout and multiplier -- no gamma, IV, delta or sign
    model (build-spec.md SS2, C4: Max Pain is NOT the gamma flip). Candidate settlement
    prices default to the set of listed strikes (the argmin lives at a strike). Returns
    None when there is nothing to evaluate.
    """
    calls: list[tuple[float, float]] = []
    puts: list[tuple[float, float]] = []
    strikes: set[float] = set()
    for c in contracts:
        oi = float(c.open_interest) if c.open_interest is not None else 0.0
        strikes.add(c.strike)
        if c.type == "C":
            calls.append((c.strike, oi))
        elif c.type == "P":
            puts.append((c.strike, oi))

    candidates = list(candidate_strikes) if candidate_strikes is not None else sorted(strikes)
    if not candidates:
        return None

    best_p: float | None = None
    best_pain = float("inf")
    for P in candidates:
        pain = 0.0
        for K, oi in calls:
            if P > K:
                pain += oi * (P - K) * multiplier
        for K, oi in puts:
            if K > P:
                pain += oi * (K - P) * multiplier
        if pain < best_pain:
            best_pain = pain
            best_p = P
    return best_p


# ---------------------------------------------------------------------------
# Universe selection: Total vs 0DTE (build-spec.md SS3 step 3, SS6, C7).
# ---------------------------------------------------------------------------
def _is_live_on(contract: NormalizedContract, trading_day: date) -> bool:
    """Whether a contract is still live intraday on `trading_day` (C7 / build-spec SS6).

    An AM-settled root (NDX, SPX) whose expiry IS the trading day has already settled on
    that day's open, so it is NOT live during the session. Everything else that has not
    expired before the day is live.
    """
    if contract.expiry < trading_day:
        return False
    if contract.expiry == trading_day and contract.root in AM_SETTLED_ROOTS:
        # AM-settled on its own expiry date: stopped trading at the open. Not live (C7).
        return False
    return True


def select_total_universe(
    chain: NormalizedChain, trading_day: date
) -> list[NormalizedContract]:
    """The broad-expiry universe for the Total walls: every still-live contract (SS3)."""
    return [c for c in chain.contracts if _is_live_on(c, trading_day)]


def select_0dte_universe(
    chain: NormalizedChain, trading_day: date
) -> list[NormalizedContract]:
    """The 0DTE universe: only contracts expiring ON `trading_day` and still live (C7).

    For NDX this excludes the AM-settled NDX root on its expiry date (already settled at
    the open) and keeps only the PM-settled NDXP dailies -- exactly the C7 rule. We never
    fabricate 0DTE exposure from an AM-settled contract that is no longer trading.
    """
    return [
        c
        for c in chain.contracts
        if c.expiry == trading_day and _is_live_on(c, trading_day)
    ]


# ---------------------------------------------------------------------------
# Wall selection: single max-|GEX| strike per side (build-spec.md SS9a, no zones).
# ---------------------------------------------------------------------------
@dataclass
class Wall:
    """One wall line: the single strongest strike for its side (build-spec.md SS9a).

    side       -- "call" or "put".
    strike     -- the max-|GEX| strike on that side (the line the indicator draws).
    signed_gex -- signed $GEX/1% at that strike for that side (positive for a call wall,
                  negative for a put wall under dealer-short-gamma).
    abs_gex    -- |signed_gex|, used for the strength share.
    proxy      -- carried from the profile: a proxy wall is labelled, never shown as OI-GEX.
    """

    universe: str
    side: str
    strike: float | None
    signed_gex: float
    abs_gex: float
    proxy: bool
    model_version: str = GEX_MODEL_VERSION


def _side_abs(agg: StrikeGEX, side: str) -> float:
    return abs(agg.call_gex) if side == "call" else abs(agg.put_gex)


def _side_signed(agg: StrikeGEX, side: str) -> float:
    return agg.call_gex if side == "call" else agg.put_gex


def select_wall(profile: GEXProfile, side: str) -> Wall:
    """Rank strikes by side-specific |GEX| and take the #1 strike (build-spec.md SS9a).

    A single line, no clustering, no zones. Ties are broken deterministically by strike
    (smallest first) so the result is stable across runs. Returns a Wall with strike=None
    when the side has no exposure at all.
    """
    best_strike: float | None = None
    best_abs = -1.0
    best_signed = 0.0
    for k in profile.strikes:
        agg = profile.by_strike[k]
        a = _side_abs(agg, side)
        if a > best_abs or (a == best_abs and best_strike is not None and k < best_strike):
            best_abs = a
            best_strike = k
            best_signed = _side_signed(agg, side)
    if best_strike is None or best_abs <= 0.0:
        return Wall(profile.universe, side, None, 0.0, 0.0, profile.proxy)
    return Wall(profile.universe, side, best_strike, best_signed, best_abs, profile.proxy)


# ---------------------------------------------------------------------------
# Strength: within-snapshot GlobalShare -> bands (build-spec.md SS4/SS0, C1).
# ---------------------------------------------------------------------------
class Strength(Enum):
    """Structural-importance band. NOT a probability of reversal (C1 / build-spec SS4)."""

    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    EXTREME = "EXTREME"


# Phase-1 within-snapshot GlobalShare thresholds (build-spec.md SS0/SS4). These are
# DOCUMENTED share cutoffs, explicitly 'uncalibrated' because the paper's bands are
# ultimately quantile-based against history we do not yet have (SS4). A strike carrying a
# large fraction of the universe's total |GEX| is structurally dominant TODAY; that is all
# GlobalShare claims. Ordered high->low so the first satisfied band wins.
_STRENGTH_BANDS = (
    (0.25, Strength.EXTREME),   # >=25% of all |GEX| in one strike: dominates the snapshot
    (0.15, Strength.STRONG),    # >=15%
    (0.07, Strength.MODERATE),  # >=7%
    (0.0, Strength.WEAK),       # everything else
)


def global_share(abs_gex: float, gross_gex: float) -> float:
    """GlobalShare = strike |GEX| / total |GEX| in the universe (build-spec.md SS4/SS0)."""
    if gross_gex <= 0.0:
        return 0.0
    return abs_gex / gross_gex


def band_for_share(share: float) -> Strength:
    """Map a GlobalShare to a Phase-1 band via the documented share thresholds."""
    for threshold, band in _STRENGTH_BANDS:
        if share >= threshold:
            return band
    return Strength.WEAK


@dataclass
class WallStrength:
    """Strength + confidence for a wall (build-spec.md SS4 -- two SEPARATE fields).

    band       -- structural-importance band from GlobalShare (WEAK..EXTREME).
    global_share -- the raw share, so the caller can display it and Phase-2 can re-band.
    confidence -- "high" or "lowered": lowered when the resolution floor (+-S*sigma*sqrt(T))
                  cannot distinguish the #1 strike from a near-equal #2 (findings 3 / SS9a).
    uncalibrated -- always True in Phase-1 (share-based, not quantile-based).
    flags      -- e.g. RESOLUTION_FLOOR_TIE.
    """

    band: Strength
    global_share: float
    confidence: str
    uncalibrated: bool = True
    flags: list[str] = field(default_factory=list)


def _resolution_floor(spot: float, sigma: float, T: float) -> float:
    """The +-S*sigma*sqrt(T) physical resolution limit (findings contribution 3).

    Material gamma lives within about this half-width of spot; any two strikes closer than
    it are not meaningfully distinguishable. It floors CONFIDENCE, not zone width (SS9a).
    """
    if spot <= 0.0 or sigma <= 0.0 or T <= 0.0:
        return 0.0
    return spot * sigma * float(np.sqrt(T))


def assess_strength(
    profile: GEXProfile,
    wall: Wall,
    side: str,
    *,
    sigma: float | None = None,
    T: float | None = None,
    near_equal_ratio: float = 0.85,
) -> WallStrength:
    """Strength band + confidence for `wall` within `profile` (build-spec.md SS4/SS9a).

    Band comes from GlobalShare (always marked uncalibrated in Phase-1). Confidence is
    LOWERED when the second-strongest same-side strike is (a) within the +-S*sigma*sqrt(T)
    resolution floor of the winner AND (b) carries near-equal |GEX| (>= near_equal_ratio
    of the winner). That is the case where the winning line is not meaningfully distinct
    from its neighbour -- we lower confidence rather than move or widen the line (SS9a).
    """
    share = global_share(wall.abs_gex, profile.gross_gex)
    band = band_for_share(share)
    confidence = "high"
    flags: list[str] = [STRENGTH_UNCALIBRATED]

    if wall.strike is not None and sigma is not None and T is not None:
        floor = _resolution_floor(profile.spot, sigma, T)
        # Find the strongest OTHER same-side strike.
        runner_strike: float | None = None
        runner_abs = -1.0
        for k in profile.strikes:
            if k == wall.strike:
                continue
            a = _side_abs(profile.by_strike[k], side)
            if a > runner_abs:
                runner_abs = a
                runner_strike = k
        if (
            runner_strike is not None
            and floor > 0.0
            and abs(runner_strike - wall.strike) <= floor
            and wall.abs_gex > 0.0
            and runner_abs >= near_equal_ratio * wall.abs_gex
        ):
            confidence = "lowered"
            flags.append(RESOLUTION_FLOOR_TIE)

    return WallStrength(
        band=band,
        global_share=share,
        confidence=confidence,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Gamma flip: root of NetGEX(S)=0 across a hypothetical-spot grid (build-spec.md SS5).
# ---------------------------------------------------------------------------
@dataclass
class GammaFlip:
    """Result of the flip scan (build-spec.md SS5).

    flip       -- the nearest economically relevant root (spot where NetGEX crosses 0), or
                  None if the curve does not cross zero on the grid.
    all_roots  -- every crossing found (sorted); >1 raises FLIP_MULTIPLE_ROOTS.
    sign_below / sign_above -- the ACTUAL sign of NetGEX just below/above the chosen root,
                  measured from the curve -- NOT hardcoded (build-spec.md SS5).
    surface_dynamics -- the repricing assumption stated for the scan (e.g. sticky-strike).
    """

    flip: float | None
    all_roots: list[float]
    grid_min: float
    grid_max: float
    sign_below: float
    sign_above: float
    surface_dynamics: str
    sign_model: SignModel
    quality_flags: list[str] = field(default_factory=list)
    model_version: str = GEX_MODEL_VERSION


def net_gex_at_spot(
    contracts: Iterable[NormalizedContract],
    hypothetical_spot: float,
    *,
    gamma_at: Callable[[NormalizedContract, float], float],
    oi_available: bool,
    sign_model: SignModel = SignModel.DEALER_SHORT_GAMMA,
    multiplier: float = DEFAULT_MULTIPLIER,
) -> float:
    """NetGEX evaluated at a HYPOTHETICAL spot, repricing gamma at that spot (SS5).

    `gamma_at(contract, S)` MUST recompute gamma at the passed spot S (that is the whole
    point of the flip scan -- gamma is not held fixed). For NDX this recomputes BS gamma
    from the surface IV at S; for QQQ v1 a spot-shifted BS gamma is used. S^2 also moves
    with the hypothetical spot.
    """
    s2 = hypothetical_spot * hypothetical_spot
    total = 0.0
    for c in contracts:
        g = gamma_at(c, hypothetical_spot)
        if g is None or g <= 0.0:
            continue
        weight, _ = _oi_or_proxy(c, oi_available)
        total += sign_for(c.type, sign_model) * g * weight * multiplier * s2 * 0.01
    return total


def solve_gamma_flip(
    contracts: Iterable[NormalizedContract],
    *,
    gamma_at: Callable[[NormalizedContract, float], float],
    oi_available: bool,
    grid_min: float,
    grid_max: float,
    reference_spot: float,
    n_grid: int = 400,
    sign_model: SignModel = SignModel.DEALER_SHORT_GAMMA,
    multiplier: float = DEFAULT_MULTIPLIER,
    surface_dynamics: str = "sticky_strike",
) -> GammaFlip:
    """Solve NetGEX(S*)=0 by scanning a dense hypothetical-spot grid (build-spec.md SS5).

    Repricing: NetGEX is recomputed at every grid S (gamma AND S^2 move). Every sign
    change between adjacent grid points is a root, located by linear interpolation of the
    NetGEX curve across that bracket. When multiple roots exist we return the one nearest
    `reference_spot` (the economically relevant crossing) and flag the others
    (FLIP_MULTIPLE_ROOTS). The sign on each side is READ from the curve at the chosen root,
    never assumed (SS5: "do not hardcode 'above flip = positive'"). `surface_dynamics`
    records the repricing assumption (default sticky-strike; stress an alternative via the
    sign_model / a different gamma_at closure, findings 4).
    """
    contracts = list(contracts)
    grid = np.linspace(grid_min, grid_max, n_grid)
    net = np.array(
        [
            net_gex_at_spot(
                contracts,
                float(S),
                gamma_at=gamma_at,
                oi_available=oi_available,
                sign_model=sign_model,
                multiplier=multiplier,
            )
            for S in grid
        ],
        dtype=float,
    )

    # A root is a genuine SIGN CHANGE of NetGEX. We locate it by interpolating each
    # bracket whose endpoints straddle zero. A flat-zero region (e.g. a range of S where
    # no contract has live gamma) is NOT a family of roots -- only the point where the
    # curve actually transitions between signs counts, so we detect crossings against the
    # nearest non-zero neighbours rather than treating every exact zero as its own root.
    roots: list[float] = []
    for i in range(len(grid) - 1):
        y0, y1 = net[i], net[i + 1]
        if y0 * y1 < 0.0:
            # Opposite signs: interpolate the zero crossing within the bracket.
            x0, x1 = grid[i], grid[i + 1]
            root = x0 - y0 * (x1 - x0) / (y1 - y0)
            roots.append(float(root))
        elif y0 != 0.0 and y1 == 0.0:
            # Curve lands exactly on zero at grid[i+1]. Only count it as a crossing if the
            # curve LEAVES zero on the far side with the opposite sign (a true transition),
            # not if it merely touches or flattens onto zero.
            j = i + 2
            while j < len(grid) and net[j] == 0.0:
                j += 1
            if j < len(grid) and y0 * net[j] < 0.0:
                roots.append(float(grid[i + 1]))
    # De-duplicate roots that a shared node can double-count.
    roots = sorted(set(round(r, 9) for r in roots))

    flags: list[str] = []
    if not roots:
        flags.append(FLIP_NO_ROOT)
        return GammaFlip(
            flip=None,
            all_roots=[],
            grid_min=grid_min,
            grid_max=grid_max,
            sign_below=0.0,
            sign_above=0.0,
            surface_dynamics=surface_dynamics,
            sign_model=sign_model,
            quality_flags=flags,
        )

    # Nearest economically relevant root = closest to the reference spot (SS5).
    chosen = min(roots, key=lambda r: abs(r - reference_spot))
    if len(roots) > 1:
        flags.append(FLIP_MULTIPLE_ROOTS)

    # ACTUAL sign each side, measured from the curve just off the chosen root (SS5).
    eps = max((grid_max - grid_min) / (n_grid * 4.0), 1e-9)
    below = net_gex_at_spot(
        contracts, chosen - eps, gamma_at=gamma_at, oi_available=oi_available,
        sign_model=sign_model, multiplier=multiplier,
    )
    above = net_gex_at_spot(
        contracts, chosen + eps, gamma_at=gamma_at, oi_available=oi_available,
        sign_model=sign_model, multiplier=multiplier,
    )

    return GammaFlip(
        flip=chosen,
        all_roots=roots,
        grid_min=grid_min,
        grid_max=grid_max,
        sign_below=float(np.sign(below)),
        sign_above=float(np.sign(above)),
        surface_dynamics=surface_dynamics,
        sign_model=sign_model,
        quality_flags=flags,
    )


# ---------------------------------------------------------------------------
# Regime: TOTAL vs 0DTE signs + divergence flag (build-spec.md SS6).
# ---------------------------------------------------------------------------
@dataclass
class Regime:
    """TOTAL / 0DTE gamma regime readout (build-spec.md SS6 table).

    total_sign / dte_sign -- "+"/"-"/"0" from the sign of each universe's NetGEX.
    divergence -- True when the two signs disagree (the SS6 divergence rows).
    """

    total_net_gex: float
    dte_net_gex: float
    total_sign: str
    dte_sign: str
    divergence: bool
    model_version: str = GEX_MODEL_VERSION


def _sign_str(x: float) -> str:
    if x > 0.0:
        return "+"
    if x < 0.0:
        return "-"
    return "0"


def assess_regime(total: GEXProfile, dte: GEXProfile) -> Regime:
    """Emit TOTAL/0DTE signs and a divergence flag when they disagree (build-spec SS6).

    Divergence is only meaningful when both universes carry a definite (non-zero) sign;
    an empty 0DTE universe is "0" and not flagged as divergence.
    """
    ts = _sign_str(total.net_gex)
    ds = _sign_str(dte.net_gex)
    divergence = ts in ("+", "-") and ds in ("+", "-") and ts != ds
    return Regime(
        total_net_gex=total.net_gex,
        dte_net_gex=dte.net_gex,
        total_sign=ts,
        dte_sign=ds,
        divergence=divergence,
    )
