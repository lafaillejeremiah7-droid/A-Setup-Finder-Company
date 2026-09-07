"""Mapping engine for The Gamma Map (build-order step 5, build-spec.md SS2.1 / C2 / C9).

WHY THIS EXISTS
---------------
Every wall, flip line, and zone is computed in the OPTIONS' native space (NDX index
points, or QQQ dollars) but must render on an MNQ futures chart. That translation is two
DIFFERENT mappings which the build-spec (SS2.1) warns must never be conflated -- doing so
is exactly correction C2:

    NDX strike ──(+basis)──▶ NQ level ──(1:1)──▶ MNQ level
    QQQ strike ──(÷ratio)──▶ NDX-equiv ──(+basis)──▶ NQ ──(1:1)──▶ MNQ

Two load-bearing facts, each a measured correction:

  * The NDX->NQ leg is a FUTURES BASIS, not 1:1 (C2 / build-spec.md SS2.1). NQ trades at a
    basis to the cash index; +21.10 pts (84 ticks, ~$422/NQ) on the canonical snapshot.
    Dropping it -- the original C2 error -- silently misplaces every index-derived level.
    So the basis leg here is MANDATORY: map_ndx_to_mnq always adds it.

  * The basis must be measured against the options' OWN parity-implied index anchor, NOT
    the feed's current_price (C9 / feed-quality.md SS4). current_price disagrees with the
    parity forward by -46 NDX pts, and capture.py's Derived.basis is computed from
    current_price, so it is contaminated. This module computes basis from the parity
    anchor (surface.ParityForward.F) and the levels writer records BOTH the raw feed basis
    and the corrected parity basis (never silently rewriting the historical meta.json).

  * NQ <-> MNQ is exactly 1:1 (verified difference 0.0000 on the canonical snapshot). The
    final hop is therefore an identity on the PRICE LEVEL. (The $ multiplier differs 10x --
    NQ $20/pt vs MNQ $2/pt -- but that is a hedge-capacity concern, never a price-level
    one, so it is deliberately NOT applied to a mapped level here; build-spec.md SS2.1.)

KNOWN LIMITATION (documented, v1 scope): NQ=F / MNQ=F are CONTINUOUS front-month
symbols. The basis they imply is the front-month basis; on a quarterly roll the basis
steps to the next contract. v1 uses the front-month quote as-is and FLAGS it
('nq_quote_is_continuous_front_month'); resolving the exact roll/back-adjustment to a
dated contract is out of v1 scope (build-spec.md SS2 futures-roll awareness is a v2 item).

Verified reference (build-spec.md SS2.1 / findings C2, PROVEN):
    NDX 24200 with NQ 24242 -> basis +42; NDX level 24342 -> NQ 24384;
    zone 24250-24350 NDX -> 24292-24392 NQ.

stdlib-only + dataclasses, quality flags emitted not silently fixed -- matching the rest
of the engine (surface.py / gex.py / adapters.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Versioning (build-spec.md SS7: stamp the model into every stored level).
# ---------------------------------------------------------------------------
MAPPING_MODEL_VERSION = "mapping-1.0.0"

# Beyond this many seconds apart, the option feed and the NQ quote are too far apart to
# form a trustworthy basis (build-spec.md SS9.1). Reused CONCEPT from capture.py's
# MAX_SOURCE_SKEW_SECONDS so the mapping layer enforces the same synchronization rule the
# capture layer does -- an unsynchronized basis is worse than no basis.
MAX_SOURCE_SKEW_SECONDS = 120.0

# Quality-flag string constants (emitted onto MappedLevel/basis results, never used to
# silently correct a number).
NQ_CONTINUOUS_FRONT_MONTH = "nq_quote_is_continuous_front_month"
BASIS_SOURCE_SKEW = "basis_source_skew_exceeds_max"
BASIS_ANCHOR_NONFINITE = "basis_anchor_nonfinite"
MAPPING_PROXY_ANCHOR = "mapping_anchor_is_feed_spot_not_parity"  # fell back off C9 path


# ---------------------------------------------------------------------------
# The two atomic legs.
# ---------------------------------------------------------------------------
def basis(nq_price: float, index_anchor: float) -> float:
    """basis = NQ - index_anchor (build-spec.md SS2.1, C2).

    `index_anchor` MUST be the options' own parity-implied index level (the parity
    forward F from surface.py), NOT the feed's current_price (C9). This function is pure
    arithmetic; the C9 discipline lives in the CALLER's choice of anchor, made explicit by
    corrected_basis() below and by the levels writer, which records both the raw and the
    parity basis so the contaminated value is never silently used.
    """
    return nq_price - index_anchor


def map_ndx_to_nq(ndx_level: float, basis_value: float) -> float:
    """NDX index level -> NQ level = NDX + basis (build-spec.md SS2.1, MANDATORY leg).

    The basis leg is never optional: omitting it is exactly the C2 error, which misplaced
    every index-derived level by ~21 index points (84 ticks).
    """
    return ndx_level + basis_value


def nq_to_mnq(nq_level: float) -> float:
    """NQ level -> MNQ level, exactly 1:1 (verified difference 0.0000, build-spec SS2.1).

    Kept as an explicit named hop rather than an inlined identity so the 1:1 assumption is
    a single auditable point and so a future divergence check has one place to live.
    """
    return nq_level


def map_ndx_to_mnq(ndx_level: float, basis_value: float) -> float:
    """NDX index level -> MNQ price: (+basis) then (1:1). build-spec.md SS2.1.

    This is the whole NDX render path: MappedNQ = Level_NDX + basis, and MNQ == NQ on the
    price level. Reproduces the verified reference exactly (NDX 24342 with basis +42 ->
    24384).
    """
    return nq_to_mnq(map_ndx_to_nq(ndx_level, basis_value))


# ---------------------------------------------------------------------------
# QQQ path: ratio to NDX-equivalent, then the same basis leg.
# ---------------------------------------------------------------------------
def qqq_ndx_ratio(qqq_spot: float, ndx_anchor: float) -> float:
    """r = QQQ / NDX (build-spec.md SS2.1). Drifts with dividends/expense ratio, so it is
    recomputed live from the current spots, never cached across snapshots.
    """
    return qqq_spot / ndx_anchor


def qqq_strike_to_ndx_equiv(k_qqq: float, ratio: float) -> float:
    """QQQ strike -> NDX-equivalent index level: K_NDXeq = K_QQQ / r (build-spec SS2.1)."""
    return k_qqq / ratio


def map_qqq_to_mnq(k_qqq: float, ratio: float, basis_value: float) -> float:
    """QQQ strike -> MNQ via the NDX-equivalent path (build-spec.md SS2.1, C2).

        K_NDXeq = K_QQQ / r ;  MappedNQ = K_NDXeq + basis ;  MNQ == NQ.

    This is the documented DEFAULT QQQ mapping: it routes through NDX-equivalent space so
    the SAME futures basis used for NDX walls applies, keeping QQQ- and NDX-derived levels
    on one consistent scale. The alternative direct QQQ->NQ mapping (map_qqq_to_mnq_direct)
    is exposed for completeness but is NOT the default -- see its docstring.
    """
    k_ndx_eq = qqq_strike_to_ndx_equiv(k_qqq, ratio)
    return map_ndx_to_mnq(k_ndx_eq, basis_value)


def map_qqq_to_mnq_direct(k_qqq: float, qqq_to_nq: float) -> float:
    """Direct QQQ-strike -> MNQ using a single measured QQQ->NQ scale (documented ALTERNATIVE).

    `qqq_to_nq` is a directly-fitted "NQ points per QQQ point" factor (e.g. NQ / QQQ at
    synchronized quotes). This collapses the ratio and basis legs into one empirical scale.

    WHY IT IS NOT THE DEFAULT: it hides the futures basis inside a blended scale, so it
    cannot separate the (drifting) QQQ/NDX ratio from the (roll-sensitive) futures basis,
    and it re-opens the door to the C2 conflation the two-leg path exists to prevent. It
    is provided for cross-checking a mapped level, not for production levels. The default
    remains map_qqq_to_mnq (ratio -> NDX-equiv -> +basis -> 1:1).
    """
    return k_qqq * qqq_to_nq


# ---------------------------------------------------------------------------
# The corrected basis used by the levels writer (the C9 fix, recorded alongside raw).
# ---------------------------------------------------------------------------
@dataclass
class BasisResult:
    """A basis figure plus WHERE its index anchor came from and its quality trail.

    basis          -- NQ - index_anchor.
    nq_price       -- the futures quote used.
    index_anchor   -- the index level subtracted (parity forward F for the corrected
                      path; current_price/feed spot for the raw path).
    anchor_source  -- "parity_forward" | "feed_current_price" | "feed_spot".
    parity_r2      -- R^2 of the parity fit when the anchor is a parity forward (the C9
                      coherence metric), else None.
    source_skew_seconds -- max timestamp spread across the sources used, when known.
    quality_flags  -- NQ_CONTINUOUS_FRONT_MONTH, BASIS_SOURCE_SKEW, etc.
    model_version  -- MAPPING_MODEL_VERSION stamp (build-spec.md SS7).
    """

    basis: float
    nq_price: float
    index_anchor: float
    anchor_source: str
    parity_r2: float | None = None
    source_skew_seconds: float | None = None
    quality_flags: list[str] = field(default_factory=list)
    model_version: str = MAPPING_MODEL_VERSION


def corrected_basis(
    nq_price: float,
    parity_forward: float,
    *,
    parity_r2: float | None = None,
    nq_is_continuous_front_month: bool = True,
    source_skew_seconds: float | None = None,
    max_skew_seconds: float = MAX_SOURCE_SKEW_SECONDS,
) -> BasisResult:
    """The C9-corrected basis: NQ - parity_forward, with synchronization + roll flags.

    This is the basis the levels writer USES (as opposed to capture.py's contaminated
    current_price-based Derived.basis, which is only RECORDED for comparison). The parity
    forward is the options' own implied index anchor (surface.ParityForward.F), internally
    consistent with the quotes (C9). Skew beyond `max_skew_seconds` and the continuous
    front-month nature of NQ=F are FLAGGED, never silently accepted (build-spec.md SS9.1).
    """
    flags: list[str] = []
    anchor = parity_forward
    anchor_source = "parity_forward"
    # If the parity forward is unusable (nan/inf/non-positive), the corrected path cannot
    # run; the caller must fall back to the feed spot and it is flagged so the level is
    # never mistaken for a C9-clean basis.
    if not _finite_positive(parity_forward):
        flags.append(BASIS_ANCHOR_NONFINITE)

    if nq_is_continuous_front_month:
        flags.append(NQ_CONTINUOUS_FRONT_MONTH)

    if source_skew_seconds is not None and source_skew_seconds > max_skew_seconds:
        flags.append(f"{BASIS_SOURCE_SKEW}:{source_skew_seconds}")

    return BasisResult(
        basis=basis(nq_price, anchor),
        nq_price=nq_price,
        index_anchor=anchor,
        anchor_source=anchor_source,
        parity_r2=parity_r2,
        source_skew_seconds=source_skew_seconds,
        quality_flags=flags,
    )


def raw_feed_basis(nq_price: float, feed_spot: float) -> BasisResult:
    """The contaminated raw basis (NQ - current_price), recorded ONLY for comparison (C9).

    This reproduces what capture.py's Derived.basis computes. The levels writer records it
    next to the corrected parity basis so the C9 gap is visible and auditable, but it is
    NEVER used to place a level.
    """
    return BasisResult(
        basis=basis(nq_price, feed_spot),
        nq_price=nq_price,
        index_anchor=feed_spot,
        anchor_source="feed_current_price",
        parity_r2=None,
    )


def _finite_positive(x: float) -> bool:
    """True when x is a finite, strictly-positive float (a usable index anchor)."""
    return x == x and x not in (float("inf"), float("-inf")) and x > 0.0
