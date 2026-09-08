"""Distill raw CBOE chains into a durable, version-controllable form.

WHY THIS EXISTS
---------------
No free source serves historical options chains, so the only history we will ever
have is what we record. That makes storage durability a correctness problem, not a
housekeeping one: a snapshot that cannot be committed is a snapshot that does not
survive the sandbox.

A raw chain pair is ~1.3 MB. At hourly capture that is ~2.3 GB/year -- not
version-controllable. Distillation gets it to ~190 KB/snapshot (~335 MB/year) by
exploiting two structural facts about the feed:

  1. Open interest is PREVIOUS-SETTLEMENT data (paper section 2.2.8). It does not move
     intraday. Storing it per snapshot duplicates it 7x/day, so it is content-addressed
     and stored once per distinct value-set instead.

  2. Contracts with no open interest AND no volume contribute EXACTLY zero to GEX,
     because GEX is proportional to OI. Dropping them is provably lossless rather
     than a judgement call. Verified on live chains: the dropped set had sum(OI)=0,
     sum(volume)=0, and gross GEX was unchanged to 1e-10.

Everything the GEX engine, IV surface, and zone algorithm consume is retained. The raw
chain is still written for same-session audit (paper section 11.1) but is gitignored --
it is a convenience copy, not the record of truth.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Occupational Symbology Initiative symbol, e.g. QQQ260904C00715000
# root(alpha) + expiry(yymmdd) + type(C|P) + strike(8 digits, thousandths of a dollar)
_OSI = re.compile(r"^([A-Z^_]+)(\d{6})([CP])(\d{8})$")

# ROOT IS PART OF THE IDENTITY OF A CONTRACT, NOT DECORATION.
#
# The NDX chain carries two roots that share strikes and expiry dates:
#   NDX  -- AM-settled (settles on the opening print after the last trading day)
#   NDXP -- PM-settled (settles on the close); the daily/weekly expiries are THIS root
#
# 257 (expiry, type, strike) pairs occur under both. Keying without the root silently
# merged their open interest and mis-stated NDX gross GEX by $7.1B (11.69 vs 18.80).
# Caught by round-tripping GEX from the distilled files back against the raw chain.
#
# The distinction also matters downstream, not just for storage: on its stated expiry
# date an AM-settled contract has already ceased trading, so its gamma is not live
# intraday, while PM-settled 0DTE gamma is live all session and peaks into the close.
# Treating the two alike would fabricate 0DTE exposure. The GEX engine needs the root
# to tell them apart, so it is carried in every row.
DYNAMIC_COLUMNS = (
    "root",
    "expiry",
    "type",
    "strike",
    "gamma",
    "delta",
    "iv",
    "bid",
    "ask",
    "volume",
)

# Content-addressed columns: previous-settlement data, static intraday.
STATIC_COLUMNS = ("root", "expiry", "type", "strike", "open_interest")

# Roots whose stated expiry date settles on the OPEN, so intraday gamma on that date is
# not live. Kept as data rather than logic so the GEX engine can consult one source.
AM_SETTLED_ROOTS = frozenset({"NDX", "SPX"})


@dataclass
class Contract:
    """One parsed option contract, restricted to fields with downstream consumers."""

    root: str  # e.g. "NDX" vs "NDXP" -- different settlement, NOT interchangeable
    expiry: str  # yymmdd as published; NOT converted to a date here -- see note below
    type: str  # "C" or "P"
    strike: float
    open_interest: int
    gamma: float | None
    delta: float | None
    iv: float | None
    bid: float | None
    ask: float | None
    volume: int

    @property
    def is_am_settled(self) -> bool:
        return self.root in AM_SETTLED_ROOTS

    @property
    def key(self) -> tuple[str, str, str, float]:
        """Full identity. Any join between greeks and OI must use this, not a subset."""
        return (self.root, self.expiry, self.type, self.strike)


@dataclass
class DistilledChain:
    """A chain reduced to its GEX-relevant content, plus an audit trail of the reduction."""

    symbol: str
    spot: float | None
    contracts: list[Contract] = field(default_factory=list)

    # Reduction audit. These make the losslessness claim checkable after the fact
    # instead of trusted.
    rows_in: int = 0
    rows_kept: int = 0
    rows_dropped_no_exposure: int = 0
    rows_unparsed: int = 0
    dropped_oi_total: int = 0
    dropped_volume_total: int = 0
    # Contract count per root, so a new root appearing in the feed is visible.
    roots: dict[str, int] = field(default_factory=dict)
    duplicate_keys: int = 0

    @property
    def is_lossless_for_gex(self) -> bool:
        """True when every dropped row provably contributed zero to GEX."""
        return self.dropped_oi_total == 0 and self.dropped_volume_total == 0

    def quality_flags(self) -> list[str]:
        flags: list[str] = []
        if self.rows_unparsed:
            # Unparsed symbols are the dangerous case: unlike zero-exposure rows, we do
            # not know what they contained, so they cannot be assumed harmless.
            flags.append(f"distill_unparsed_symbols:{self.symbol}:{self.rows_unparsed}")
        if not self.is_lossless_for_gex:
            flags.append(
                f"distill_dropped_nonzero_exposure:{self.symbol}:"
                f"oi={self.dropped_oi_total},vol={self.dropped_volume_total}"
            )
        if self.duplicate_keys:
            # Would make the greeks/OI join ambiguous -- the exact failure that mis-stated
            # NDX GEX by $7.1B before the root was included in the key.
            flags.append(f"distill_duplicate_keys:{self.symbol}:{self.duplicate_keys}")
        unknown = set(self.roots) - {"NDX", "NDXP", "QQQ", "SPX", "SPXW"}
        if unknown:
            flags.append(f"distill_unknown_root:{self.symbol}:{','.join(sorted(unknown))}")
        return flags


def parse_osi(symbol: str) -> tuple[str, str, str, float] | None:
    """Parse an OSI option symbol into (root, expiry_yymmdd, type, strike).

    Returns None rather than raising: an unrecognised symbol should be counted and
    flagged, not allowed to abort a capture that is otherwise good.
    """
    m = _OSI.match(symbol or "")
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), int(m.group(4)) / 1000.0


def distill(symbol: str, payload: dict[str, Any]) -> DistilledChain:
    """Reduce a raw CBOE payload to contracts that can affect GEX or the IV surface."""
    data = payload.get("data") or {}
    options = data.get("options") or []

    out = DistilledChain(symbol=symbol, spot=data.get("current_price"), rows_in=len(options))

    for o in options:
        parsed = parse_osi(o.get("option", ""))
        if parsed is None:
            out.rows_unparsed += 1
            continue

        root, expiry, kind, strike = parsed
        oi = int(o.get("open_interest") or 0)
        vol = int(o.get("volume") or 0)

        # GEX is proportional to OI, so a contract with neither OI nor volume cannot
        # move any output. Volume is retained as a keep-condition because a contract
        # traded today will carry OI tomorrow -- dropping it would blind us to the
        # position being built.
        if oi <= 0 and vol <= 0:
            out.rows_dropped_no_exposure += 1
            out.dropped_oi_total += oi
            out.dropped_volume_total += vol
            continue

        out.contracts.append(
            Contract(
                root=root,
                expiry=expiry,
                type=kind,
                strike=strike,
                open_interest=oi,
                gamma=o.get("gamma"),
                delta=o.get("delta"),
                iv=o.get("iv"),
                bid=o.get("bid"),
                ask=o.get("ask"),
                volume=vol,
            )
        )

    # Deterministic order: makes files byte-reproducible and content hashes stable.
    out.contracts.sort(key=lambda c: c.key)
    out.rows_kept = len(out.contracts)

    for c in out.contracts:
        out.roots[c.root] = out.roots.get(c.root, 0) + 1

    # A duplicate full key would mean the OI join is still ambiguous. This must be zero;
    # if it is not, the feed has changed shape and the join needs rethinking.
    seen: set[tuple[str, str, str, float]] = set()
    for c in out.contracts:
        if c.key in seen:
            out.duplicate_keys += 1
        seen.add(c.key)

    return out


def _to_csv(rows: Iterable[Iterable[Any]], header: Iterable[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode("utf-8")


def _write_gz(path: Path, body: bytes) -> None:
    # mtime=0 so identical content produces identical bytes -- required for the
    # content-addressing below to actually dedupe.
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0) as fh:
        fh.write(body)


def write_static_oi(root: Path, chain: DistilledChain) -> tuple[str, str]:
    """Write the open-interest column content-addressed. Returns (relative_path, digest).

    Content-addressing rather than one-file-per-day is deliberate. A US trading session
    crosses a UTC date boundary, so "per UTC day" would split one settlement's OI across
    two files and imply a change that did not happen. Hashing the values sidesteps
    calendar reasoning entirely: identical OI collapses to one file, and a genuine
    settlement update naturally produces a new one.
    """
    body = _to_csv(
        ([c.root, c.expiry, c.type, c.strike, c.open_interest] for c in chain.contracts),
        STATIC_COLUMNS,
    )
    digest = hashlib.sha256(body).hexdigest()[:12]
    slug = chain.symbol.lstrip("_").lower()
    rel = f"oi/{slug}_{digest}.csv.gz"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    # Immutable by construction: same content -> same path, so a rewrite is a no-op.
    if not path.exists():
        _write_gz(path, body)
    return rel, digest


def write_dynamic(snap_dir: Path, chain: DistilledChain) -> str:
    """Write the per-snapshot moving columns. Returns the filename."""
    body = _to_csv(
        (
            [
                c.root,
                c.expiry,
                c.type,
                c.strike,
                c.gamma,
                c.delta,
                c.iv,
                c.bid,
                c.ask,
                c.volume,
            ]
            for c in chain.contracts
        ),
        DYNAMIC_COLUMNS,
    )
    slug = chain.symbol.lstrip("_").lower()
    fname = f"greeks_{slug}.csv.gz"
    _write_gz(snap_dir / fname, body)
    return fname
