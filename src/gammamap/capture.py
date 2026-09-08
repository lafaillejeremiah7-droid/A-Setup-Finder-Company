"""Capture-forward snapshot job for The Gamma Map.

No free source serves historical options chains (every dated URL returns 403), so
history only exists if we record it. This job writes one immutable snapshot per run.

Data-integrity rules enforced here come from paper section 10.5 and section 11.1:

  * timestamp every source SEPARATELY -- a fresh quote does not make OI fresh
  * store the raw chain so results can be audited later
  * NEVER overwrite a snapshot with later information
  * record schema/model version on every record
  * flag, do not silently fix, quality problems

Mapping note: levels ultimately render on MNQ. NQ and MNQ share one price level
(verified difference 0.0000), so the final hop is 1:1. The NDX -> NQ basis leg is
still required because the options originate in NDX space -- omitting it misplaces
every level by roughly 21 index points (84 ticks).
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .distill import distill, write_dynamic, write_static_oi
from .sources import FetchError, OptionChain, Quote, fetch_option_chain, fetch_quote

# 1.1.0 added the `root` column to distilled files. Reading a 1.0.0 file as 1.1.0 would
# silently mis-join open interest, so the version is checked, not assumed.
SCHEMA_VERSION = "1.1.0"

# Options universes. NDX is primary: daily expiries confirmed, finer strike grid in
# index terms, European/cash-settled, no ETF ratio step. QQQ is a secondary
# cross-check -- the two disagree materially (58.9 / 235.3 NQ pts measured), so they
# are stored separately and never blended. See findings-so-far.md finding 8.
CHAIN_SYMBOLS = ("_NDX", "QQQ")

# NQ=F gives the futures price for basis. MNQ=F is captured purely to re-verify the
# 1:1 relationship on every snapshot rather than trusting it once.
QUOTE_SYMBOLS = ("NQ=F", "MNQ=F", "^NDX")

# Beyond this, sources are too far apart to compute a trustworthy basis (section 9.1).
MAX_SOURCE_SKEW_SECONDS = 120.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Derived:
    """Values computed from a snapshot, with their own quality flags."""

    ndx_spot: float | None = None
    qqq_spot: float | None = None
    nq_price: float | None = None
    mnq_price: float | None = None

    # Basis = NQ - NDX. NOT 1:1. Required for every index-derived level.
    basis: float | None = None
    # ratio = QQQ / NDX. Drifts with dividends and expense ratio; recompute every run.
    qqq_ndx_ratio: float | None = None
    # Should be exactly 0.0. Non-zero means the 1:1 assumption needs re-examination.
    nq_mnq_divergence: float | None = None

    source_skew_seconds: float | None = None
    quality_flags: list[str] | None = None

    def __post_init__(self) -> None:
        if self.quality_flags is None:
            self.quality_flags = []


def _compute_derived(chains: dict[str, OptionChain], quotes: dict[str, Quote]) -> Derived:
    d = Derived()
    flags = d.quality_flags
    assert flags is not None

    nq = quotes.get("NQ=F")
    mnq = quotes.get("MNQ=F")
    ndx_q = quotes.get("^NDX")

    d.nq_price = nq.price if nq and nq.is_usable else None
    d.mnq_price = mnq.price if mnq and mnq.is_usable else None

    # Prefer the CBOE chain's own spot so basis uses the same feed as the greeks.
    ndx_chain = chains.get("_NDX")
    if ndx_chain and ndx_chain.spot:
        d.ndx_spot = ndx_chain.spot
    elif ndx_q and ndx_q.is_usable:
        d.ndx_spot = ndx_q.price
        flags.append("ndx_spot_from_yahoo_fallback")

    qqq_chain = chains.get("QQQ")
    if qqq_chain and qqq_chain.spot:
        d.qqq_spot = qqq_chain.spot

    if d.nq_price is not None and d.ndx_spot is not None:
        d.basis = round(d.nq_price - d.ndx_spot, 4)
    else:
        flags.append("basis_unavailable")

    if d.qqq_spot and d.ndx_spot:
        d.qqq_ndx_ratio = d.qqq_spot / d.ndx_spot

    if d.nq_price is not None and d.mnq_price is not None:
        d.nq_mnq_divergence = round(d.nq_price - d.mnq_price, 6)
        if d.nq_mnq_divergence != 0.0:
            flags.append(f"nq_mnq_not_1to1:{d.nq_mnq_divergence}")

    # Synchronization check -- an unsynchronized basis is worse than no basis.
    stamps = [_parse_iso(c.retrieved_at) for c in chains.values()]
    stamps += [_parse_iso(q.retrieved_at) for q in quotes.values()]
    stamps = [s for s in stamps if s is not None]
    if len(stamps) >= 2:
        d.source_skew_seconds = (max(stamps) - min(stamps)).total_seconds()
        if d.source_skew_seconds > MAX_SOURCE_SKEW_SECONDS:
            flags.append(f"source_skew_exceeds_{MAX_SOURCE_SKEW_SECONDS}s")

    if nq and nq.is_continuous_series:
        # Not an error, but the mapping engine must resolve the real contract.
        flags.append("nq_quote_is_continuous_front_month")

    for sym, chain in chains.items():
        if not chain.is_usable:
            flags.append(f"chain_unusable:{sym}")

    return d


def _expiry_summary(chain: OptionChain) -> dict[str, int]:
    """Contract count per expiry (yymmdd from the OSI symbol), for a freshness view."""
    import re

    pattern = re.compile(r"^([A-Z^_]+)(\d{6})([CP])(\d{8})$")
    counts: dict[str, int] = {}
    for opt in chain.payload.get("data", {}).get("options", []) or []:
        m = pattern.match(opt.get("option", ""))
        if m:
            counts[m.group(2)] = counts.get(m.group(2), 0) + 1
    return dict(sorted(counts.items()))


def capture_snapshot(root: Path | str = "data/snapshots") -> Path:
    """Fetch all sources and write one immutable snapshot directory. Returns its path."""
    root = Path(root)
    now = _utcnow()
    snap_dir = root / now.strftime("%Y-%m-%d") / now.strftime("%H%M%SZ")

    if snap_dir.exists():
        # Immutability rule: never overwrite a historical snapshot.
        raise FileExistsError(f"snapshot already exists, refusing to overwrite: {snap_dir}")

    chains: dict[str, OptionChain] = {}
    quotes: dict[str, Quote] = {}
    errors: dict[str, str] = {}

    for sym in CHAIN_SYMBOLS:
        try:
            chains[sym] = fetch_option_chain(sym)
        except FetchError as exc:
            errors[sym] = str(exc)

    for sym in QUOTE_SYMBOLS:
        try:
            quotes[sym] = fetch_quote(sym)
        except FetchError as exc:
            errors[sym] = str(exc)

    if not chains:
        raise FetchError(f"no options chain retrieved; nothing to store. errors={errors}")

    derived = _compute_derived(chains, quotes)

    snap_dir.mkdir(parents=True, exist_ok=False)

    # Raw chains, gzipped. Paper section 11.1 wants the raw chain for audit, but at
    # ~1.3 MB/snapshot these are NOT version-controllable (see distill.py), so they are
    # gitignored: a same-session convenience copy, not the durable record.
    chain_files: dict[str, str] = {}
    for sym, chain in chains.items():
        fname = f"chain_{sym.lstrip('_').lower()}.json.gz"
        with gzip.open(snap_dir / fname, "wt", encoding="utf-8") as fh:
            json.dump(chain.payload, fh, separators=(",", ":"))
        chain_files[sym] = fname

    # Distilled chains ARE the durable record: small enough to commit, and provably
    # lossless for GEX (every dropped row carries zero OI and zero volume).
    distilled_meta: dict[str, dict[str, Any]] = {}
    for sym, chain in chains.items():
        dc = distill(sym, chain.payload)
        oi_rel, oi_digest = write_static_oi(root, dc)
        dyn_name = write_dynamic(snap_dir, dc)
        derived.quality_flags.extend(dc.quality_flags())
        distilled_meta[sym] = {
            "greeks_file": dyn_name,
            "oi_file": oi_rel,
            "oi_digest": oi_digest,
            "rows_in": dc.rows_in,
            "rows_kept": dc.rows_kept,
            "rows_dropped_no_exposure": dc.rows_dropped_no_exposure,
            "rows_unparsed": dc.rows_unparsed,
            "dropped_oi_total": dc.dropped_oi_total,
            "dropped_volume_total": dc.dropped_volume_total,
            "lossless_for_gex": dc.is_lossless_for_gex,
            # NDX splits into NDX (AM-settled) and NDXP (PM-settled, the dailies).
            # They share strikes, so the root is part of the join key.
            "roots": dc.roots,
            "duplicate_keys": dc.duplicate_keys,
        }

    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": now.isoformat(timespec="seconds"),
        "chains": {
            sym: {
                "symbol": c.symbol,
                "retrieved_at": c.retrieved_at,
                "feed_timestamp": c.feed_timestamp,
                "quote_timestamp": c.quote_timestamp,
                "spot": c.spot,
                "contract_count": c.contract_count,
                "raw_file": chain_files[sym],
                # OI is previous-settlement data (section 2.2.8). We cannot observe the
                # settlement date from this feed, so capture time is the honest anchor.
                "oi_freshness": "previous_settlement_unknown_date",
                "expiry_contract_counts": _expiry_summary(c),
            }
            for sym, c in chains.items()
        },
        "quotes": {sym: asdict(q) for sym, q in quotes.items()},
        # The committed record. `raw_file` above is gitignored; these files are what
        # survive, so the reduction audit travels with them.
        "distilled": distilled_meta,
        "derived": asdict(derived),
        "errors": errors,
        "notes": {
            "mapping": "NDX strike --(+basis)--> NQ --(1:1)--> MNQ. Output renders on MNQ.",
            "primary_instrument": "_NDX",
            "secondary_instrument": "QQQ (cross-check only; never blended -- see finding 8)",
        },
    }

    (snap_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    # Append-only index so the dashboard can list captured days without walking the tree.
    root.mkdir(parents=True, exist_ok=True)
    index_row = {
        "captured_at": meta["captured_at"],
        "path": str(snap_dir.relative_to(root)),
        "ndx_spot": derived.ndx_spot,
        "qqq_spot": derived.qqq_spot,
        "nq_price": derived.nq_price,
        "basis": derived.basis,
        "nq_mnq_divergence": derived.nq_mnq_divergence,
        "source_skew_seconds": derived.source_skew_seconds,
        "quality_flags": derived.quality_flags,
        "schema_version": SCHEMA_VERSION,
    }
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(index_row) + "\n")

    return snap_dir
