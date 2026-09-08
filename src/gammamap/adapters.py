"""Adapters that normalize each data origin into the internal chain schema.

WHY THIS EXISTS
---------------
`chain.py` defines the contract; this module is the only place that knows the shape of
each raw source. Two adapters exist, one per origin:

  * from_distilled_snapshot -- reads the committed distilled CBOE snapshot (greeks CSV
    + content-addressed OI CSV referenced by meta.json) and joins them on the full
    (root,expiry,type,strike) key. OI is present, so oi_available=True: this is the
    true-GEX path.

  * from_kaggle_csv -- parses the Kaggle historical QQQ CSV (qqq_2020_2022.csv). That
    file has NO open-interest column, so oi_available=False and every chain it produces
    carries the OI_ABSENT_VOLUME_PROXY flag. It is the only free replay source, but its
    GEX can only ever be a volume-weighted proxy.

Both reuse existing primitives (distill.parse_osi, distill column names) rather than
re-deriving them. Neither adapter fetches anything: capture (build-order step 1) is
already done, and the Kaggle raw file is far too large to touch in tests.

stdlib-only parsing (csv/gzip/datetime), matching the engine's stdlib-first style.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from . import distill
from .chain import (
    OI_ABSENT_VOLUME_PROXY,
    NormalizedChain,
    NormalizedContract,
)


# ---------------------------------------------------------------------------
# CBOE-live via the committed distilled snapshot format.
# ---------------------------------------------------------------------------
def _read_gz_csv(path: Path) -> list[dict[str, str]]:
    """Read a gzipped CSV into dict rows. Mirrors the distilled write format."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _yymmdd_to_date(yymmdd: str) -> date:
    """Convert the distilled 6-digit expiry token to a calendar date.

    The distiller stores expiry as the OSI yymmdd string (never converted), so the
    century is implicit. Options expiries are always in the 20xx range for our data, so
    a bare `%y` (which maps 00-68 -> 2000-2068) is correct here.
    """
    return datetime.strptime(yymmdd, "%y%m%d").date()


def _f(v: str | None) -> float | None:
    """Empty distilled cell -> None (the distiller writes '' for a missing value)."""
    if v is None or v == "":
        return None
    return float(v)


def from_distilled_snapshot(snap_dir: Path | str, symbol: str) -> NormalizedChain:
    """Build a NormalizedChain from a committed distilled snapshot directory.

    `symbol` is the meta.json chain key ('QQQ' or '_NDX'). The OI file is looked up from
    meta.json rather than reconstructed from the content-addressed digest, so the loader
    survives a re-distill that changes the hash (same policy as test_capture_layer).

    oi_available=True: the distilled OI column is present, so this is the true-GEX path.
    """
    snap_dir = Path(snap_dir)
    meta = json.loads((snap_dir / "meta.json").read_text())

    dist_meta = meta["distilled"][symbol]
    greeks_rows = _read_gz_csv(snap_dir / dist_meta["greeks_file"])

    # The OI file lives under the snapshots ROOT (content-addressed oi/ dir), which is
    # the snapshot dir's grandparent (data/snapshots/<date>/<time> -> data/snapshots).
    snapshots_root = snap_dir.parent.parent
    oi_rows = _read_gz_csv(snapshots_root / dist_meta["oi_file"])

    # Join OI onto greeks on the FULL identity key (C7). A subset key would re-introduce
    # the NDX/NDXP merge that mis-stated GEX by $7.1B.
    def _key(r: dict[str, str]) -> tuple[str, str, str, float]:
        return (r["root"], r["expiry"], r["type"], float(r["strike"]))

    oi_by_key = {_key(r): int(r["open_interest"]) for r in oi_rows}

    contracts: list[NormalizedContract] = []
    for r in greeks_rows:
        yymmdd = r["expiry"]
        vol = r.get("volume")
        contracts.append(
            NormalizedContract(
                root=r["root"],
                expiry=_yymmdd_to_date(yymmdd),
                expiry_yymmdd=yymmdd,
                type=r["type"],
                strike=float(r["strike"]),
                # None when a greeks row has no OI counterpart. It should not happen for
                # a well-formed snapshot (the two files are projections of one set), but
                # we do not fabricate a zero -- an absent join is different from OI=0.
                open_interest=oi_by_key.get(_key(r)),
                gamma=_f(r.get("gamma")),
                delta=_f(r.get("delta")),
                iv=_f(r.get("iv")),
                bid=_f(r.get("bid")),
                ask=_f(r.get("ask")),
                volume=int(vol) if vol not in (None, "") else None,
            )
        )

    chain_meta = meta["chains"].get(symbol, {})
    provenance: dict[str, object] = {
        "source": "cboe_delayed",
        "snapshot_dir": str(snap_dir),
        "symbol": symbol,
        "captured_at": meta.get("captured_at"),
        "retrieved_at": chain_meta.get("retrieved_at"),
        "feed_timestamp": chain_meta.get("feed_timestamp"),
        "quote_timestamp": chain_meta.get("quote_timestamp"),
        "greeks_file": dist_meta.get("greeks_file"),
        "oi_file": dist_meta.get("oi_file"),
        "oi_digest": dist_meta.get("oi_digest"),
        "oi_freshness": chain_meta.get("oi_freshness"),
        "schema_version": meta.get("schema_version"),
    }

    # Carry forward the snapshot-level quality flags so nothing recorded at capture time
    # is lost crossing into the engine (e.g. the continuous-front-month NQ warning).
    quality_flags = list(meta.get("derived", {}).get("quality_flags", []))

    chain = NormalizedChain(
        symbol=symbol,
        asof=chain_meta.get("quote_timestamp") or chain_meta.get("retrieved_at"),
        spot_from_feed=chain_meta.get("spot"),
        contracts=contracts,
        oi_available=True,
        provenance=provenance,
        quality_flags=quality_flags,
    )
    return chain


# ---------------------------------------------------------------------------
# Kaggle historical QQQ CSV (qqq_2020_2022.csv).
# ---------------------------------------------------------------------------
# VERIFIED schema quirk: the header cells are BRACKETED and space-padded, e.g.
# "[QUOTE_DATE]", " [C_GAMMA]", " [STRIKE]", " [P_IV]". We strip brackets AND
# surrounding whitespace from every header before use. There is NO open-interest column,
# and each data row carries BOTH a call (C_*) and a put (P_*) leg for one strike/expiry.
def _clean_header(cell: str) -> str:
    return cell.strip().strip("[]").strip()


def _kf(row: dict[str, str], name: str) -> float | None:
    """Fetch a numeric field by cleaned name; missing/blank -> None."""
    v = row.get(name)
    if v is None or v.strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _ki(row: dict[str, str], name: str) -> int | None:
    """Fetch an integer field (volume). Kaggle writes volumes as floats/blanks."""
    f = _kf(row, name)
    return None if f is None else int(f)


def _parse_kaggle_date(raw: str) -> date:
    """Parse a Kaggle date cell. The dataset uses YYYY-MM-DD, but a couple of dumps ship
    a trailing time; be tolerant of a leading date token either way.
    """
    token = raw.strip().split()[0]
    return datetime.strptime(token, "%Y-%m-%d").date()


def from_kaggle_csv(
    lines: Iterable[str] | str | Path,
    *,
    root: str = "QQQ",
    source_id: str = "kaggle_qqq_2020_2022",
) -> NormalizedChain:
    """Build a NormalizedChain from Kaggle QQQ CSV rows.

    `lines` may be an iterable of CSV text lines, a raw CSV string, or a Path to a
    (optionally gzipped) CSV file. Passing text/lines is what tests use, so no 628MB
    download is ever required.

    The CSV has NO open-interest column, so every contract gets open_interest=None,
    the chain gets oi_available=False, and the OI_ABSENT_VOLUME_PROXY flag is emitted.
    Each data row yields TWO NormalizedContracts: one call (C_* fields) and one put
    (P_* fields) sharing the row's strike and expiry.
    """
    reader = _open_kaggle(lines)

    contracts: list[NormalizedContract] = []
    asof: str | None = None
    spot: float | None = None

    for raw_row in reader:
        # Re-key the row on cleaned (unbracketed, unpadded) column names.
        row = {_clean_header(k): v for k, v in raw_row.items() if k is not None}

        strike = _kf(row, "STRIKE")
        if strike is None:
            continue  # a malformed/blank row cannot be keyed -- skip, do not fabricate
        exp = _parse_kaggle_date(row["EXPIRE_DATE"])
        yymmdd = exp.strftime("%y%m%d")

        # First usable row fixes the chain-level asof + spot. Quote time is preferred
        # (that is when the option prices were observed); fall back to the date.
        if asof is None:
            qd = row.get("QUOTE_DATE", "").strip()
            qt = row.get("QUOTE_READTIME", "").strip()
            asof = qt or qd or None
        if spot is None:
            spot = _kf(row, "UNDERLYING_LAST")

        # Call leg (C_* fields). No OI in this source.
        contracts.append(
            NormalizedContract(
                root=root,
                expiry=exp,
                expiry_yymmdd=yymmdd,
                type="C",
                strike=strike,
                open_interest=None,
                gamma=_kf(row, "C_GAMMA"),
                delta=_kf(row, "C_DELTA"),
                iv=_kf(row, "C_IV"),
                bid=_kf(row, "C_BID"),
                ask=_kf(row, "C_ASK"),
                volume=_ki(row, "C_VOLUME"),
            )
        )
        # Put leg (P_* fields) for the same strike/expiry.
        contracts.append(
            NormalizedContract(
                root=root,
                expiry=exp,
                expiry_yymmdd=yymmdd,
                type="P",
                strike=strike,
                open_interest=None,
                gamma=_kf(row, "P_GAMMA"),
                delta=_kf(row, "P_DELTA"),
                iv=_kf(row, "P_IV"),
                bid=_kf(row, "P_BID"),
                ask=_kf(row, "P_ASK"),
                volume=_ki(row, "P_VOLUME"),
            )
        )

    provenance: dict[str, object] = {
        "source": source_id,
        "root": root,
        "asof": asof,
    }

    return NormalizedChain(
        symbol=root,
        asof=asof,
        spot_from_feed=spot,
        contracts=contracts,
        # No OI column: classic OI-GEX is impossible from this source. Downstream must
        # treat any GEX built from it as a volume-weighted PROXY, never as OI-GEX.
        oi_available=False,
        provenance=provenance,
        quality_flags=[OI_ABSENT_VOLUME_PROXY],
    )


def _open_kaggle(lines: Iterable[str] | str | Path) -> csv.DictReader:
    """Return a DictReader over Kaggle CSV content from lines/string/path.

    Accepts a Path (optionally .gz), a raw CSV string, or any iterable of text lines.
    The bracketed header is left as-is here; callers clean field names per row so the
    original DictReader keys still line up with values.
    """
    if isinstance(lines, Path):
        if lines.suffix == ".gz":
            fh = gzip.open(lines, "rt", encoding="utf-8", newline="")
        else:
            fh = lines.open("rt", encoding="utf-8", newline="")
        return csv.DictReader(fh)
    if isinstance(lines, str):
        return csv.DictReader(io.StringIO(lines))
    return csv.DictReader(iter(lines))


# ---------------------------------------------------------------------------
# The true-GEX vs proxy switch, in one place.
# ---------------------------------------------------------------------------
def is_true_gex(chain: NormalizedChain) -> bool:
    """True only when the chain carries open interest.

    This is the single gate the GEX engine consults: a True chain may be run through
    classic OI-weighted GEX; a False chain (e.g. Kaggle) must have its output labelled a
    volume-weighted proxy and must never be presented as classic OI-GEX.
    """
    return chain.oi_available
