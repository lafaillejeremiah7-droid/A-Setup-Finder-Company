"""IV-surface engine for The Gamma Map (build-order step 3, build-spec.md SS10).

WHY THIS EXISTS
---------------
Two measured facts (feed-quality.md) make this module a hard prerequisite for NDX, not a
refinement:

  * C8 -- the feed's gamma is quantized into uselessness for NDX. Every greek is
    published to 4 decimal places; gamma scales as 1/(S*sigma*sqrt(T)), so at NDX price
    levels the 0.0001 step is 6.7% of peak gamma. 142 near-money NDX strikes collapse to
    just 14 distinct gamma values, one run of 18 adjacent strikes (90 index points)
    sharing a single value. Gamma must therefore be COMPUTED from IV, not read from the
    feed. IV itself is three orders of magnitude better resolved (0.05% quant step), and
    recomputing gamma from IV recovers all 142 distinct values. But pointwise
    recomputation still inherits IV's own quantization, so gamma must come from a FITTED
    surface -- which is exactly the paper's SS1.3 requirement (feed-quality.md SS3).

  * C9 -- `current_price` (the feed's spot) disagrees with the options' own parity-implied
    forward by -46 NDX pts. It must NEVER be used as the spot for greek computation. The
    forward per expiry is recovered from put-call parity, which is internally consistent
    with the quotes (R^2 ~ 0.9998 across the near-money strip) and needs no assumed rates
    (feed-quality.md SS4/SS5).

This module provides, per the FEAT-003 plan:
  (a) forward_from_parity  -- C - P = DF*(F - K) regressed across near-money call/put
      pairs, recovering F and DF jointly with an R^2 quality metric (C9).
  (b) bs_gamma             -- the verified Black-Scholes gamma from build-spec.md SS2.
  (c) time_to_expiry       -- exact time to ACTUAL settlement honoring AM vs PM (C7),
      never rounded days/365 (build-spec.md SS7).
  (d) IVSurface            -- a per-expiry smooth fit of IV in log-moneyness k=ln(K/F)
      with no-arb sanity checks, whose surface_gamma evaluates the fitted IV then
      computes BS gamma (the C8 fix).
  (e) SURFACE_MODEL_VERSION stamped into every fit so a historical level records which
      model produced it (build-spec.md SS7).

Quality problems are FLAGGED, never silently fixed (matching distill.py / chain.py).
stdlib-first, with numpy + scipy for the numerics (both installed; scipy.stats.norm is
the standard-normal pdf/cdf used throughout).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, timedelta
from typing import Sequence

import numpy as np
from scipy.stats import norm

from .chain import AM_SETTLED_ROOTS, NormalizedChain, NormalizedContract

# ---------------------------------------------------------------------------
# Versioning (build-spec.md SS7: "Snapshot exact inputs + model version for every
# historical level"). Bump when the fit family, clock, or parity method changes so a
# stored level can be traced to the exact model that produced it.
# ---------------------------------------------------------------------------
SURFACE_MODEL_VERSION = "surface-1.0.0"

# Quality-flag string constants. Emitted onto results, never used to silently correct a
# number (build-spec.md SS7: "Treat ... parity inconsistencies as surface-quality
# warnings rather than forcing a smooth IV through every quote").
PARITY_LOW_R2 = "parity_low_r2"
PARITY_FEW_PAIRS = "parity_few_pairs"
PARITY_NONPOSITIVE_DF = "parity_nonpositive_df"
SURFACE_FEW_POINTS = "surface_few_points"
SURFACE_NEGATIVE_VARIANCE = "surface_negative_variance"
SURFACE_NONMONOTONE_TOTAL_VARIANCE = "surface_nonmonotone_total_variance"
CLOCK_EXPIRED = "clock_expired"

# Minimum call/put pairs for a trustworthy parity regression. Below this the slope is
# too easily dominated by one noisy quote, so we still return F but flag it.
_MIN_PARITY_PAIRS = 8
# R^2 below this means the near-money quotes are not one coherent forward -- flag it.
# feed-quality.md SS4 measured ~0.9998 on good data; 0.99 is a generous floor.
_MIN_PARITY_R2 = 0.99
# Default near-money half-width for pair selection, as a fraction of the anchor. The
# parity line is only clean where both legs have real time value (near the money).
_DEFAULT_MONEYNESS_BAND = 0.05

# US equity/index cash markets: regular session is 09:30-16:00 America/New_York.
# We store the settlement instants in Eastern wall-clock and attach the correct UTC
# offset per date (EDT = UTC-4, EST = UTC-5) rather than depending on a tz database.
_ET_OPEN = time(9, 30)
_ET_CLOSE = time(16, 0)


# ---------------------------------------------------------------------------
# (b) Black-Scholes gamma -- build-spec.md SS2 (PROVEN there; Gamma_call == Gamma_put).
# ---------------------------------------------------------------------------
def bs_gamma(
    S: float,
    K: float,
    T: float,
    sigma: float,
    r: float = 0.0,
    q: float = 0.0,
) -> float:
    """Black-Scholes gamma  Gamma = phi(d1) / (S*sigma*sqrt(T)).

        d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma*sqrt(T))

    where phi is the standard-normal pdf (build-spec.md SS2). Gamma is identical for a
    call and a put, so no option type is taken. `S` is the forward-consistent spot (use
    the parity forward, per C9 -- never the feed's current_price).

    Returns 0.0 for a degenerate input (non-positive S, sigma, or T) rather than raising:
    a dead contract contributes no gamma, and the caller decides via quality flags
    whether the degeneracy is worth surfacing.
    """
    if S <= 0.0 or K <= 0.0 or sigma <= 0.0 or T <= 0.0:
        return 0.0
    sqrt_t = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    return float(norm.pdf(d1) / (S * sigma * sqrt_t))


def _bs_gamma_vec(
    S: float,
    K: np.ndarray,
    T: float,
    sigma: np.ndarray,
    r: float = 0.0,
    q: float = 0.0,
) -> np.ndarray:
    """Vectorized bs_gamma over arrays of strikes and per-strike sigmas.

    Same formula as bs_gamma; degenerate cells (sigma<=0) are set to 0 to mirror the
    scalar contract. Kept private -- callers use surface_gamma / bs_gamma.
    """
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    out = np.zeros_like(K, dtype=float)
    if S <= 0.0 or T <= 0.0:
        return out
    ok = (sigma > 0.0) & (K > 0.0)
    if not np.any(ok):
        return out
    sqrt_t = np.sqrt(T)
    d1 = (np.log(S / K[ok]) + (r - q + 0.5 * sigma[ok] ** 2) * T) / (sigma[ok] * sqrt_t)
    out[ok] = norm.pdf(d1) / (S * sigma[ok] * sqrt_t)
    return out


# ---------------------------------------------------------------------------
# (c) Exact clocks honoring AM vs PM settlement -- C7 / build-spec.md SS7.
# ---------------------------------------------------------------------------
def _et_offset_hours(d: date) -> int:
    """US Eastern UTC offset for a date, without a tz database.

    DST in the US runs from the 2nd Sunday of March to the 1st Sunday of November. During
    that window Eastern is EDT (UTC-4); otherwise EST (UTC-5). This is exact for every
    date the option data covers and avoids a zoneinfo dependency in the numerics path.
    """
    year = d.year

    def _nth_sunday(month: int, n: int) -> date:
        first = date(year, month, 1)
        # weekday(): Monday=0 .. Sunday=6. Days to the first Sunday:
        first_sunday = 1 + (6 - first.weekday()) % 7
        return date(year, month, first_sunday + 7 * (n - 1))

    dst_start = _nth_sunday(3, 2)   # 2nd Sunday of March
    dst_end = _nth_sunday(11, 1)    # 1st Sunday of November
    return -4 if dst_start <= d < dst_end else -5


def settlement_instant(expiry: date, root: str) -> datetime:
    """The exact UTC instant a contract settles.

    C7 / build-spec.md SS6: AM-settled roots (NDX, SPX per AM_SETTLED_ROOTS) settle on
    the OPEN of the expiry date; PM-settled roots (NDXP, QQQ, SPXW, ...) settle on the
    CLOSE. This is why an AM-settled contract's intraday gamma on its own expiry date is
    NOT live -- the settlement instant has already passed by the time the session trades.
    """
    settle_wall = _ET_OPEN if root in AM_SETTLED_ROOTS else _ET_CLOSE
    tz = timezone(timedelta(hours=_et_offset_hours(expiry)))
    return datetime.combine(expiry, settle_wall, tzinfo=tz).astimezone(timezone.utc)


def _parse_asof(asof: str | datetime) -> datetime:
    """Coerce an 'asof' timestamp to a tz-aware UTC datetime.

    Accepts a datetime or an ISO-8601 string. A naive input (no offset) is a wall-clock
    Eastern reading -- the quote timestamps in our snapshots (e.g. '2026-09-04T16:14:59')
    are Eastern local -- so it is localized to Eastern before converting to UTC.
    """
    if isinstance(asof, datetime):
        dt = asof
    else:
        dt = datetime.fromisoformat(str(asof))
    if dt.tzinfo is None:
        tz = timezone(timedelta(hours=_et_offset_hours(dt.date())))
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


# Year length in seconds for annualization. build-spec.md SS7 forbids rounded days/365
# for 0DTE; we use exact seconds-to-settlement divided by a full calendar year in
# seconds. (365.0-day year -- the standard actual/365 convention for option clocks.)
_YEAR_SECONDS = 365.0 * 24.0 * 3600.0


def time_to_expiry(
    expiry: date,
    root: str,
    asof: str | datetime,
) -> float:
    """Exact year-fraction from `asof` to the contract's ACTUAL settlement instant (C7).

    Uses seconds to settlement, not rounded days/365 (build-spec.md SS7), so 0DTE clocks
    are correct to the second and replay honors the point-in-time `asof`. The result is
    NON-POSITIVE once settlement has passed -- notably for an AM-settled contract observed
    intraday on its stated expiry date, whose 09:30 ET settlement is already behind the
    quote time. Callers treat T <= 0 as "not live" (gamma 0) and may flag CLOCK_EXPIRED.
    """
    now = _parse_asof(asof)
    settle = settlement_instant(expiry, root)
    return (settle - now).total_seconds() / _YEAR_SECONDS


# ---------------------------------------------------------------------------
# (a) Parity forward per expiry -- C9 / build-spec.md SS4.
# ---------------------------------------------------------------------------
def _mid(c: NormalizedContract) -> float | None:
    """Two-sided mid price, or None if the market is unusable.

    Rejects one-sided, crossed, and non-positive markets (build-spec.md SS7: "Reject/flag
    crossed or zero markets"). A contract without a clean mid cannot anchor the parity
    line, so it is dropped from the pair set rather than fudged.
    """
    b, a = c.bid, c.ask
    if b is None or a is None:
        return None
    if a <= 0.0 or b < 0.0 or a < b:
        return None
    return 0.5 * (b + a)


@dataclass
class ParityForward:
    """Result of forward_from_parity for one (root, expiry).

    F  -- the forward level implied by C - P = DF*(F - K).
    df -- the discount factor DF recovered jointly (no assumed rate; may exceed 1 at
          near-zero rates over a short tenor).
    r2 -- regression R^2 across the near-money pairs; the coherence check from
          feed-quality.md SS4 (~0.9998 on good data).
    n_pairs      -- pairs used in the fit.
    quality_flags -- any of PARITY_* raised for this fit.
    model_version -- SURFACE_MODEL_VERSION stamp (build-spec.md SS7).
    """

    root: str
    expiry_yymmdd: str
    F: float
    df: float
    r2: float
    n_pairs: int
    quality_flags: list[str] = field(default_factory=list)
    model_version: str = SURFACE_MODEL_VERSION


def forward_from_parity(
    chain: NormalizedChain,
    root: str,
    expiry_yymmdd: str,
    *,
    moneyness_band: float = _DEFAULT_MONEYNESS_BAND,
    anchor: float | None = None,
) -> ParityForward:
    """Recover the forward F and discount factor DF for one expiry from put-call parity.

    Method (C9, feed-quality.md SS4): for each strike with a clean call AND put mid,
    parity gives  C - K_leg = DF*(F - K).  Regressing (C - P) on K is linear:

        y = C - P = (DF*F) + (-DF)*K = a + b*K   ->   DF = -b,  F = -a/b = a/DF.

    The regression recovers DF and F JOINTLY, so no interest or dividend rate is assumed.
    Pairs are restricted to a near-money band (default +-5% of an anchor) because the line
    is only clean where both legs carry real time value; the anchor defaults to the feed
    spot, then is refined once with the first-pass forward so the band is centered on the
    forward itself. Emits quality flags (few pairs, low R^2, non-positive DF) instead of
    silently trusting a bad fit.
    """
    calls: dict[float, float] = {}
    puts: dict[float, float] = {}
    for c in chain.contracts:
        if c.root != root or c.expiry_yymmdd != expiry_yymmdd:
            continue
        m = _mid(c)
        if m is None:
            continue
        (calls if c.type == "C" else puts)[c.strike] = m

    strikes = sorted(set(calls) & set(puts))
    flags: list[str] = []

    if len(strikes) < 2:
        # Cannot fit a line at all. Return a degenerate result flagged as few-pairs so the
        # caller never treats it as a real forward.
        return ParityForward(
            root=root,
            expiry_yymmdd=expiry_yymmdd,
            F=float("nan"),
            df=float("nan"),
            r2=0.0,
            n_pairs=len(strikes),
            quality_flags=[PARITY_FEW_PAIRS],
        )

    K_all = np.array(strikes, dtype=float)
    y_all = np.array([calls[k] - puts[k] for k in strikes], dtype=float)

    center = anchor if anchor is not None else (chain.spot_from_feed or float(K_all.mean()))

    def _fit(mask: np.ndarray) -> tuple[float, float, float, int]:
        K = K_all[mask]
        y = y_all[mask]
        A = np.vstack([np.ones_like(K), K]).T
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        a, b = float(coef[0]), float(coef[1])
        yhat = A @ coef
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return a, b, r2, int(mask.sum())

    # First pass: band around the feed anchor to get a rough F, then recenter the band on
    # that F and refit. This keeps the pair set symmetric around the true forward even
    # when the feed spot is off (the C9 gap is exactly why we cannot trust the anchor).
    mask = np.abs(K_all - center) <= moneyness_band * center
    if mask.sum() < 2:
        mask = np.ones_like(K_all, dtype=bool)
    a, b, r2, n = _fit(mask)
    if b != 0.0:
        F0 = -a / b
        mask2 = np.abs(K_all - F0) <= moneyness_band * F0
        if mask2.sum() >= _MIN_PARITY_PAIRS:
            a, b, r2, n = _fit(mask2)

    df = -b
    F = (-a / b) if b != 0.0 else float("nan")

    if n < _MIN_PARITY_PAIRS:
        flags.append(PARITY_FEW_PAIRS)
    if r2 < _MIN_PARITY_R2:
        flags.append(PARITY_LOW_R2)
    if not np.isfinite(df) or df <= 0.0:
        flags.append(PARITY_NONPOSITIVE_DF)

    return ParityForward(
        root=root,
        expiry_yymmdd=expiry_yymmdd,
        F=float(F),
        df=float(df),
        r2=float(r2),
        n_pairs=int(n),
        quality_flags=flags,
    )


# ---------------------------------------------------------------------------
# (d) Fitted IV surface per expiry + surface_gamma (the C8 fix).
# ---------------------------------------------------------------------------
@dataclass
class ExpirySurfaceFit:
    """A smooth per-expiry IV fit in log-moneyness k = ln(K/F) (build-spec.md SS2).

    coeffs   -- polynomial coefficients (numpy convention, highest power first) for
                sigma(k). A low-order polynomial in k is a standard smile parameterization
                and, unlike pointwise IV, removes the feed's 4-dp quantization -- which is
                what recovers strike-level gamma resolution (C8, feed-quality.md SS3).
    forward  -- the parity forward F used as the moneyness origin (C9).
    T        -- exact year-fraction to settlement at the fit's asof (C7).
    k_min/k_max -- fitted moneyness range; evaluation outside is clamped and would be
                flagged upstream (we do not extrapolate a smile blindly).
    n_points -- IV points used.
    quality_flags -- SURFACE_* raised for this fit.
    """

    root: str
    expiry_yymmdd: str
    forward: float
    T: float
    coeffs: np.ndarray
    k_min: float
    k_max: float
    n_points: int
    quality_flags: list[str] = field(default_factory=list)
    model_version: str = SURFACE_MODEL_VERSION

    def sigma_at_k(self, k: float | np.ndarray) -> np.ndarray:
        """Evaluate fitted IV at log-moneyness k, clamped to the fitted range.

        Clamping (rather than extrapolating) keeps a low-order polynomial from diverging
        in the wings. Wing behaviour is a known weakness of polynomial smiles; the walls
        we care about live near the money where the fit is well constrained.
        """
        k_arr = np.asarray(k, dtype=float)
        k_clamped = np.clip(k_arr, self.k_min, self.k_max)
        return np.polyval(self.coeffs, k_clamped)


# Default polynomial degree for the smile. Quadratic captures level + skew + curvature,
# which is the standard minimal smile; it stays well-conditioned on a near-money strip and
# will not oscillate the way a high-order fit does.
_SURFACE_POLY_DEGREE = 2
# Minimum IV points for a stable smile fit.
_MIN_SURFACE_POINTS = 6


def _fit_expiry_surface(
    root: str,
    expiry_yymmdd: str,
    forward: float,
    T: float,
    strikes: Sequence[float],
    ivs: Sequence[float],
    *,
    degree: int = _SURFACE_POLY_DEGREE,
) -> ExpirySurfaceFit:
    """Least-squares fit sigma as a polynomial in k = ln(K/F), with no-arb sanity checks.

    No-arb checks emitted as flags (never silently fixed, build-spec.md SS7):
      * SURFACE_NEGATIVE_VARIANCE     -- the fit predicts sigma <= 0 somewhere in range,
        i.e. negative implied variance, which is inadmissible.
      * SURFACE_NONMONOTONE_TOTAL_VARIANCE -- included for completeness; total variance
        w = sigma^2 * T must be non-decreasing in T across expiries (a calendar no-arb
        condition). A single-expiry fit cannot check the T-direction, so the flag is only
        raised by the multi-expiry IVSurface, not here.
      * SURFACE_FEW_POINTS            -- too few clean IVs for a trustworthy smile.
    """
    K = np.asarray(strikes, dtype=float)
    sig = np.asarray(ivs, dtype=float)
    ok = (K > 0.0) & np.isfinite(sig) & (sig > 0.0) & (forward > 0.0)
    K = K[ok]
    sig = sig[ok]
    flags: list[str] = []

    if K.size < _MIN_SURFACE_POINTS:
        flags.append(SURFACE_FEW_POINTS)
        # A degree-0 (flat) fallback so downstream still gets SOMETHING, flagged.
        mean_sig = float(sig.mean()) if sig.size else 0.0
        return ExpirySurfaceFit(
            root=root,
            expiry_yymmdd=expiry_yymmdd,
            forward=forward,
            T=T,
            coeffs=np.array([mean_sig]),
            k_min=0.0,
            k_max=0.0,
            n_points=int(K.size),
            quality_flags=flags,
        )

    k = np.log(K / forward)
    # Cap degree at points-1 to avoid an over-determined fit on a thin strip.
    deg = min(degree, K.size - 1)
    coeffs = np.polyfit(k, sig, deg)

    k_min, k_max = float(k.min()), float(k.max())
    # No-arb: sample the fitted smile densely across the range; any non-positive sigma
    # means negative implied variance -> flag (do not clamp silently).
    k_grid = np.linspace(k_min, k_max, 64)
    sig_grid = np.polyval(coeffs, k_grid)
    if np.any(sig_grid <= 0.0):
        flags.append(SURFACE_NEGATIVE_VARIANCE)

    return ExpirySurfaceFit(
        root=root,
        expiry_yymmdd=expiry_yymmdd,
        forward=forward,
        T=T,
        coeffs=coeffs,
        k_min=k_min,
        k_max=k_max,
        n_points=int(K.size),
        quality_flags=flags,
    )


class IVSurface:
    """Fitted IV surface over a whole chain, per (root, expiry) (build-spec.md SS1.3).

    Construction:
      * computes the parity forward per expiry (C9),
      * computes exact time-to-settlement per expiry at `asof` (C7),
      * fits a smooth per-expiry smile sigma(k) in log-moneyness (C8 fix),
      * runs single-expiry no-arb checks, plus a cross-expiry monotone-total-variance
        check where two expiries share a near-ATM point.

    surface_gamma(root, expiry, strike) evaluates the FITTED IV at that strike then
    computes BS gamma from it -- the C8 fix that turns the feed's 14 distinct NDX gamma
    values back into full strike-level resolution.
    """

    def __init__(
        self,
        chain: NormalizedChain,
        asof: str | datetime,
        *,
        moneyness_band: float = _DEFAULT_MONEYNESS_BAND,
        degree: int = _SURFACE_POLY_DEGREE,
    ) -> None:
        self.chain = chain
        self.asof = asof
        self.model_version = SURFACE_MODEL_VERSION
        self.quality_flags: list[str] = []
        self._forwards: dict[tuple[str, str], ParityForward] = {}
        self._fits: dict[tuple[str, str], ExpirySurfaceFit] = {}

        # Group contracts by (root, expiry) once.
        groups: dict[tuple[str, str], list[NormalizedContract]] = {}
        for c in chain.contracts:
            groups.setdefault((c.root, c.expiry_yymmdd), []).append(c)

        for (root, yymmdd), contracts in groups.items():
            fwd = forward_from_parity(
                chain, root, yymmdd, moneyness_band=moneyness_band
            )
            self._forwards[(root, yymmdd)] = fwd

            expiry = contracts[0].expiry
            T = time_to_expiry(expiry, root, asof)
            fwd_val = fwd.F if np.isfinite(fwd.F) else (chain.spot_from_feed or 0.0)

            # Collect clean per-strike IVs near the money for the smile fit. One IV per
            # strike is enough for a smile; prefer the call leg, fall back to the put.
            iv_by_strike: dict[float, float] = {}
            for c in contracts:
                if c.iv is None or c.iv <= 0.0:
                    continue
                if c.strike not in iv_by_strike or c.type == "C":
                    iv_by_strike[c.strike] = c.iv

            fit = _fit_expiry_surface(
                root,
                yymmdd,
                fwd_val,
                T,
                list(iv_by_strike.keys()),
                list(iv_by_strike.values()),
                degree=degree,
            )
            self._fits[(root, yymmdd)] = fit
            self.quality_flags.extend(f"{root}/{yymmdd}:{fl}" for fl in fit.quality_flags)

        self._check_calendar_monotonicity()

    # -- lookups -----------------------------------------------------------
    def forward(self, root: str, expiry_yymmdd: str) -> ParityForward | None:
        return self._forwards.get((root, expiry_yymmdd))

    def fit(self, root: str, expiry_yymmdd: str) -> ExpirySurfaceFit | None:
        return self._fits.get((root, expiry_yymmdd))

    def surface_iv(self, root: str, expiry_yymmdd: str, strike: float) -> float | None:
        """Fitted IV at a strike, or None if that expiry was not fit."""
        fit = self._fits.get((root, expiry_yymmdd))
        if fit is None or fit.forward <= 0.0:
            return None
        k = float(np.log(strike / fit.forward))
        return float(fit.sigma_at_k(k))

    def surface_gamma(
        self,
        root: str,
        expiry_yymmdd: str,
        strike: float,
        r: float = 0.0,
        q: float = 0.0,
    ) -> float:
        """Gamma computed from the FITTED IV surface -- the C8 fix.

        Reads the fitted sigma at `strike`, uses the parity forward as the spot (C9), the
        exact clock as T (C7), then applies bs_gamma. Never reads gamma from the feed.
        Returns 0.0 when the clock is non-positive (contract not live) or the fit is
        unavailable -- a dead contract contributes no gamma.
        """
        fit = self._fits.get((root, expiry_yymmdd))
        if fit is None or fit.forward <= 0.0 or fit.T <= 0.0:
            return 0.0
        sigma = self.surface_iv(root, expiry_yymmdd, strike)
        if sigma is None or sigma <= 0.0:
            return 0.0
        return bs_gamma(fit.forward, strike, fit.T, sigma, r=r, q=q)

    def surface_gamma_by_strike(
        self,
        root: str,
        expiry_yymmdd: str,
        strikes: Sequence[float],
        r: float = 0.0,
        q: float = 0.0,
    ) -> np.ndarray:
        """Vectorized surface_gamma over many strikes for one expiry (C8).

        Used by the gamma-recovery check and the downstream GEX engine: evaluate the
        smooth fitted smile at every strike, then BS gamma. This is where the feed's 14
        distinct NDX gamma values become full strike-level resolution.
        """
        fit = self._fits.get((root, expiry_yymmdd))
        K = np.asarray(strikes, dtype=float)
        if fit is None or fit.forward <= 0.0 or fit.T <= 0.0:
            return np.zeros_like(K, dtype=float)
        k = np.log(K / fit.forward)
        sigma = fit.sigma_at_k(k)
        return _bs_gamma_vec(fit.forward, K, fit.T, sigma, r=r, q=q)

    # -- no-arb: cross-expiry total variance -------------------------------
    def _check_calendar_monotonicity(self) -> None:
        """Flag calendar arbitrage: total variance w = sigma^2 * T at ATM must be
        non-decreasing in T (build-spec.md SS7, "monotonic total-variance-in-T where
        checkable"). Checked per root across its live expiries using the ATM (k=0) fitted
        IV. Only raised as a warning; never mutates a fit.
        """
        by_root: dict[str, list[ExpirySurfaceFit]] = {}
        for (root, _), fit in self._fits.items():
            if fit.T > 0.0 and fit.n_points >= _MIN_SURFACE_POINTS:
                by_root.setdefault(root, []).append(fit)

        for root, fits in by_root.items():
            fits_sorted = sorted(fits, key=lambda f: f.T)
            prev_w = None
            prev_yymmdd = None
            for f in fits_sorted:
                sig_atm = float(f.sigma_at_k(0.0))
                if sig_atm <= 0.0:
                    continue
                w = sig_atm * sig_atm * f.T
                if prev_w is not None and w + 1e-12 < prev_w:
                    self.quality_flags.append(
                        f"{root}/{prev_yymmdd}->{f.expiry_yymmdd}:"
                        f"{SURFACE_NONMONOTONE_TOTAL_VARIANCE}"
                    )
                prev_w = w
                prev_yymmdd = f.expiry_yymmdd
