"""Internal normalized chain schema for The Gamma Map.

WHY THIS EXISTS
---------------
The engine will ingest option chains from two structurally different origins:

  1. CBOE-live (via the distilled snapshot format) -- carries PREVIOUS-SETTLEMENT open
     interest, so it supports classic OI-weighted GEX (the "true GEX" path).
  2. The Kaggle historical QQQ CSV (qqq_2020_2022.csv) -- the only free source of
     replay history, but it has NO open-interest column. GEX is proportional to OI, so
     without it we cannot compute classic GEX at all; the best we can do is a
     volume-weighted proxy, which is a DIFFERENT quantity and must never be silently
     substituted for OI-GEX.

Every later engine (surface, GEX, walls, flip, mapping) consumes ONE schema. This
module is that contract. Two facts are load-bearing and are therefore encoded in the
types rather than left to convention:

  * Contract identity is the FULL (root, expiry, type, strike) tuple, exactly as
    distill.py keys it (finding C7). Root is part of identity, not decoration: NDX
    (AM-settled) and NDXP (PM-settled) share strikes and expiries, and merging them
    mis-stated NDX GEX by $7.1B.

  * Whether open interest is available is a PROPERTY OF THE CHAIN, surfaced as
    `oi_available` plus a quality flag, so the true-GEX vs proxy distinction is
    explicit at every downstream boundary and cannot be lost in a join.

Quality problems are FLAGGED, never silently fixed (matching distill.py): a consumer
that ignores a flag is making a choice, but the information is always present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Roots whose stated expiry date settles on the OPEN, so intraday gamma on that date is
# not live. Mirrors distill.AM_SETTLED_ROOTS; kept here too so a NormalizedContract can
# answer the question without importing the distiller. Data, not logic.
AM_SETTLED_ROOTS = frozenset({"NDX", "SPX"})

# Emitted by any adapter over a source that lacks an open-interest column. Downstream
# MUST label such output as a volume-weighted proxy, never as classic OI-GEX.
OI_ABSENT_VOLUME_PROXY = "oi_absent_volume_proxy"


@dataclass
class NormalizedContract:
    """One option contract in the engine's internal form.

    The distilled `Contract` (distill.py) keeps `expiry` as the published yymmdd string
    and never converts it to a date, because at capture time the raw string is the
    record of truth. Here we carry BOTH: `expiry` is a real `date` for the engine's
    per-expiry math (0DTE detection, forward per expiry), and `expiry_yymmdd` preserves
    the original published token so the identity key stays byte-identical to distill's.
    """

    root: str  # e.g. "NDX" vs "NDXP" -- different settlement, NOT interchangeable
    expiry: date  # parsed calendar date, for per-expiry engine math
    expiry_yymmdd: str  # original published token; part of the identity key (C7)
    type: str  # "C" or "P"
    strike: float

    # None when the source carries no open interest (e.g. the Kaggle CSV). Optional is
    # deliberate: a downstream that treats None as 0 would fabricate a true-GEX of zero
    # rather than admitting the quantity is unavailable.
    open_interest: int | None

    gamma: float | None
    delta: float | None
    iv: float | None
    bid: float | None
    ask: float | None
    volume: int | None

    @property
    def is_am_settled(self) -> bool:
        return self.root in AM_SETTLED_ROOTS

    @property
    def key(self) -> tuple[str, str, str, float]:
        """Full identity (C7). Uses the ORIGINAL yymmdd token, matching distill.py so a
        NormalizedChain built from a distilled snapshot keys identically to its source.
        """
        return (self.root, self.expiry_yymmdd, self.type, self.strike)


@dataclass
class NormalizedChain:
    """A source-agnostic option chain plus its provenance and quality trail.

    `oi_available` is the single switch every downstream consumer checks to decide
    whether it may compute classic OI-GEX or must fall back to a volume proxy. It is
    stored, not derived on demand, so the answer is stable even if individual contracts
    are filtered later.
    """

    symbol: str
    asof: str | None  # ISO timestamp the data is "as of" (quote/read time)
    spot_from_feed: float | None
    contracts: list[NormalizedContract] = field(default_factory=list)

    # Whether this chain supports classic OI-weighted GEX. True only for sources that
    # carry an open-interest column (CBOE). See is_true_gex() in adapters.py.
    oi_available: bool = False

    # Per-source provenance: source id, retrieval/quote timestamps, upstream file refs.
    # A dict rather than fixed fields because the two sources expose different metadata
    # and we do not want to force one source's shape onto the other.
    provenance: dict[str, object] = field(default_factory=dict)

    # Propagated + adapter-added flags. Never mutated into silent fixes.
    quality_flags: list[str] = field(default_factory=list)

    def contracts_by_key(self) -> dict[tuple[str, str, str, float], NormalizedContract]:
        """Index contracts on the full identity key (C7).

        A duplicate key means the source is ambiguous the same way an unrooted NDX join
        was -- so instead of letting the last writer win silently, we record a
        `chain_duplicate_key` flag per collision and keep the FIRST occurrence. The
        caller can inspect quality_flags to see it happened.
        """
        out: dict[tuple[str, str, str, float], NormalizedContract] = {}
        for c in self.contracts:
            if c.key in out:
                self.quality_flags.append(
                    f"chain_duplicate_key:{self.symbol}:{c.root}/{c.expiry_yymmdd}/{c.type}/{c.strike}"
                )
                continue
            out[c.key] = c
        return out

    def expiries(self) -> list[date]:
        """Distinct expiry dates present, ascending. Convenience for per-expiry engines."""
        return sorted({c.expiry for c in self.contracts})
