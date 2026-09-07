#!/usr/bin/env python3
"""Historical QQQ backfill from the free CC0 Kaggle dataset (build-order step 8, FEAT-007).

WHAT THIS DOES
--------------
Downloads the CC0 Kaggle dataset

    kylegraupe/qqq-daily-option-chains-q1-2020-to-q4-2022

(verified: an unauthenticated GET on the API download URL returns HTTP 200; the ~132MB
zip contains a single qqq_2020_2022.csv, ~628MB uncompressed), streams the CSV grouping
rows by QUOTE_DATE (each date is one EOD 16:00-ET point-in-time chain), then for a
BOUNDED, documented SAMPLE of dates runs the Gamma Map engine via the FEAT-002 Kaggle
adapter to produce a small per-date levels file at

    data/levels/kaggle/<quote_date>_qqq.json

Two hard constraints, both spec-mandated (FEAT-007):

  * VOLUME PROXY, ALWAYS. The Kaggle CSV has NO open-interest column, so GEX is built from
    the volume proxy and every output carries proxy=True and 'oi_absent_volume_proxy'. A
    historical levels file is never presented as classic OI-GEX.

  * NO FABRICATED BASIS. Historical QQQ has no synchronized NQ futures quote, so the
    NDX->NQ basis (C2) cannot be measured. Levels are emitted in the options' NATIVE QQQ
    index space (native_level), mnq_price=None, flagged BASIS_UNAVAILABLE_HISTORICAL. We
    never invent a basis. Documented v1 limitation.

SANDBOX / MEMORY NOTES
----------------------
The raw CSV is 628MB, so it is streamed line by line (never loaded whole into memory) and
grouped by date; only the rows for the sampled dates are retained. The raw download lands
under data/kaggle_raw/ which is gitignored -- only the small per-date levels JSON files
are committed. Because a fresh sandbox container wipes /tmp and env between bash calls,
the download and parse happen in ONE process run: `python scripts/backfill_kaggle.py`
downloads (if the raw file is absent) and parses in the same invocation.

USAGE
-----
    # Full run: download (if needed) + build the default monthly sample across 2020-2022.
    PYTHONPATH=src python scripts/backfill_kaggle.py

    # Bounded run without a full download (stream only the first N distinct dates):
    PYTHONPATH=src python scripts/backfill_kaggle.py --max-dates 6

    # Explicit dates:
    PYTHONPATH=src python scripts/backfill_kaggle.py --dates 2020-03-16 2022-01-03

stdlib-only I/O (urllib/zipfile/csv/gzip), matching the engine's stdlib-first style.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path

# Make `import gammamap...` resolve when run as a script without PYTHONPATH=src, matching
# tests/conftest.py's belt-and-suspenders path insert.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gammamap import adapters  # noqa: E402
from gammamap.levels import (  # noqa: E402
    build_kaggle_levels,
    kaggle_levels_path,
)

# VERIFIED: unauthenticated GET returns 200; ~132MB zip -> qqq_2020_2022.csv (~628MB).
KAGGLE_URL = (
    "https://www.kaggle.com/api/v1/datasets/download/"
    "kylegraupe/qqq-daily-option-chains-q1-2020-to-q4-2022"
)
RAW_DIR = _REPO_ROOT / "data" / "kaggle_raw"  # gitignored; persists under the repo path
RAW_ZIP = RAW_DIR / "qqq-daily-option-chains-q1-2020-to-q4-2022.zip"
LEVELS_DIR = _REPO_ROOT / "data" / "levels" / "kaggle"

# The Kaggle CSV's date column, once the bracketed/space-padded header is cleaned.
_QUOTE_DATE_COL = "QUOTE_DATE"


def _clean_header(cell: str) -> str:
    """Strip brackets + surrounding whitespace from a Kaggle header cell (same as adapter)."""
    return cell.strip().strip("[]").strip()


def download_raw(url: str = KAGGLE_URL, dest: Path = RAW_ZIP, *, force: bool = False) -> Path:
    """Download the CC0 zip into data/kaggle_raw/ (gitignored). No auth needed.

    Skips the download when the file already exists (idempotent re-runs), unless force is
    set. Returns the path to the downloaded zip.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and dest.stat().st_size > 0:
        print(f"[backfill] raw zip already present: {dest} ({dest.stat().st_size} bytes)")
        return dest
    print(f"[backfill] downloading {url} -> {dest}")
    req = urllib.request.Request(url, headers={"User-Agent": "gammamap-backfill/1.0"})
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fh:  # noqa: S310 (https, CC0)
        while True:
            chunk = resp.read(1 << 20)  # 1MB chunks -- never buffer the whole 132MB body
            if not chunk:
                break
            fh.write(chunk)
    print(f"[backfill] downloaded {dest.stat().st_size} bytes")
    return dest


def _open_csv_stream(zip_path: Path):
    """Yield text lines from the single CSV inside the Kaggle zip, streamed (not loaded).

    The zip holds one qqq_*.csv member; we open it as a binary stream wrapped in a text
    reader so the 628MB payload is iterated line by line rather than materialized.
    """
    zf = zipfile.ZipFile(zip_path)
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise RuntimeError(f"no CSV member found in {zip_path}: {zf.namelist()}")
    member = names[0]
    raw = zf.open(member, "r")
    return io.TextIOWrapper(raw, encoding="utf-8", newline="")


def _parse_date_cell(raw: str) -> date | None:
    """Parse the QUOTE_DATE cell (YYYY-MM-DD, tolerant of a trailing time). None if blank."""
    token = raw.strip().split()[0] if raw.strip() else ""
    if not token:
        return None
    try:
        return datetime.strptime(token, "%Y-%m-%d").date()
    except ValueError:
        return None


def group_rows_by_date(
    zip_path: Path,
    *,
    wanted: set[date] | None = None,
    max_dates: int | None = None,
):
    """Stream the CSV and collect raw CSV LINES per QUOTE_DATE (bounded, memory-safe).

    Only rows whose date is in `wanted` are retained (when `wanted` is given). When
    `max_dates` is set instead, the first `max_dates` DISTINCT dates encountered are
    retained and streaming stops once all are complete (so a full 628MB read is avoided
    for a bounded sample). Returns a dict {date: [header_line, row_line, ...]} where each
    value is a self-contained CSV block (header + that date's rows) ready for the adapter.

    The `max_dates` early-stop assumes the CSV is grouped by date (verified: it is ordered
    ascending from 2021-01-04). If a future dump interleaves dates, `max_dates` may capture
    fewer complete blocks; the full run (wanted=None, max_dates=None) reads everything and
    is unaffected.
    """
    stream = _open_csv_stream(zip_path)
    try:
        header_line = stream.readline()
        if not header_line:
            return {}
        header_cells = next(csv.reader([header_line]))
        clean = [_clean_header(c) for c in header_cells]
        try:
            date_idx = clean.index(_QUOTE_DATE_COL)
        except ValueError:
            raise RuntimeError(f"QUOTE_DATE column not found; cleaned header={clean}")

        blocks: dict[date, list[str]] = {}
        completed: set[date] = set()
        # A date is "complete" once we have seen it and then moved past it (the CSV is
        # ordered by date). We use that to stop early under max_dates.
        current: date | None = None

        for line in stream:
            if not line.strip():
                continue
            row = next(csv.reader([line]))
            if date_idx >= len(row):
                continue
            d = _parse_date_cell(row[date_idx])
            if d is None:
                continue

            if d != current and current is not None:
                completed.add(current)
                if max_dates is not None and len(completed) >= max_dates:
                    break
            current = d

            if wanted is not None and d not in wanted:
                continue
            if max_dates is not None and d not in blocks and len(blocks) >= max_dates:
                continue
            blocks.setdefault(d, [header_line.rstrip("\n")]).append(line.rstrip("\n"))

        return blocks
    finally:
        stream.close()


def default_monthly_sample() -> list[date]:
    """A documented monthly sample across the dataset's ACTUAL coverage (one date per month).

    DATA-QUALITY NOTE (verified by streaming the file): despite the dataset's title
    ("Q1 2020 to Q4 2022"), the shipped qqq_2020_2022.csv actually contains only
    2021-01-04 .. 2022-12-30 (508 trading days; no 2020 rows at all). We therefore sample
    across 2021-2022. Dates are the 3rd-of-month (nudged off weekends toward a nearby
    weekday) so the sample is deterministic and spread evenly. The backfill keeps whichever
    of these dates actually appear as trading days in the CSV; a requested date with no
    chain (holiday/weekend) is simply skipped, never fabricated.
    """
    out: list[date] = []
    for year in (2021, 2022):
        for month in range(1, 13):
            d = date(year, month, 3)
            # Nudge weekends to the next Monday so we land on a likely trading day.
            if d.weekday() == 5:  # Saturday
                d = date(year, month, 5)
            elif d.weekday() == 6:  # Sunday
                d = date(year, month, 4)
            out.append(d)
    return out


def build_one(block_lines: list[str], quote_date: date) -> dict:
    """Run the adapter + engine on one date's CSV block -> a levels dict (volume proxy)."""
    csv_text = "\n".join(block_lines) + "\n"
    chain = adapters.from_kaggle_csv(csv_text)
    return build_kaggle_levels(chain, quote_date)


def write_one(levels: dict, quote_date: date, *, levels_dir: Path = LEVELS_DIR) -> Path:
    """Write a per-date levels file deterministically (sorted keys) and return its path."""
    path = kaggle_levels_path(levels_dir, quote_date, "qqq")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(levels, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run(
    *,
    dates: list[date] | None,
    max_dates: int | None,
    skip_download: bool,
) -> list[Path]:
    """Download (unless skipped), stream+group, build and write per-date levels files."""
    if not skip_download:
        download_raw()
    elif not RAW_ZIP.exists():
        raise SystemExit(
            f"--skip-download set but {RAW_ZIP} is absent; run once without it to fetch."
        )

    wanted = set(dates) if dates else None
    blocks = group_rows_by_date(RAW_ZIP, wanted=wanted, max_dates=max_dates)

    written: list[Path] = []
    for quote_date in sorted(blocks):
        levels = build_one(blocks[quote_date], quote_date)
        path = write_one(levels, quote_date)
        walls = ", ".join(
            f"{w['label']}={w['native_level']}({w['strength']})" for w in levels["walls"]
        )
        print(f"[backfill] {quote_date} -> {path.relative_to(_REPO_ROOT)}  proxy={levels['provenance']['proxy']}  {walls}")
        written.append(path)

    if not written:
        print("[backfill] no dates matched; nothing written.")
    return written


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--dates", nargs="+", metavar="YYYY-MM-DD",
        help="Explicit quote dates to backfill (default: the documented monthly 2020-2022 sample).",
    )
    g.add_argument(
        "--max-dates", type=int, metavar="N",
        help="Stream only the first N distinct dates (bounded run; avoids a full 628MB read).",
    )
    p.add_argument(
        "--skip-download", action="store_true",
        help="Do not download; use an existing data/kaggle_raw/ zip (must already be present).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dates: list[date] | None
    if args.dates:
        dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in args.dates]
    elif args.max_dates is not None:
        dates = None  # bounded by max_dates instead of an explicit set
    else:
        dates = default_monthly_sample()
    run(dates=dates, max_dates=args.max_dates, skip_download=args.skip_download)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
