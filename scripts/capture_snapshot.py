#!/usr/bin/env python3
"""Run one Gamma Map capture snapshot.

Usage:
    python3 scripts/capture_snapshot.py [--root data/snapshots] [--quiet]

Intended to be run on a schedule during market hours. 0DTE needs intraday captures,
not one per day: gamma localizes as sqrt(T) decays, so the map changes hour to hour
(paper sections 2.2.8 and 3.4).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gammamap.capture import capture_snapshot  # noqa: E402
from gammamap.sources import FetchError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture one immutable Gamma Map snapshot.")
    ap.add_argument("--root", default="data/snapshots", help="snapshot root directory")
    ap.add_argument("--quiet", action="store_true", help="only print the snapshot path")
    args = ap.parse_args()

    try:
        path = capture_snapshot(args.root)
    except FetchError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"refusing to overwrite: {exc}", file=sys.stderr)
        return 2

    if args.quiet:
        print(path)
        return 0

    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    d = meta["derived"]

    print(f"snapshot: {path}")
    for sym, c in meta["chains"].items():
        print(
            f"  {sym:6} spot={c['spot']} contracts={c['contract_count']} "
            f"quote_ts={c['quote_timestamp']}"
        )
    print(f"  NQ={d['nq_price']} MNQ={d['mnq_price']} NDX={d['ndx_spot']}")
    print(f"  basis (NQ-NDX) = {d['basis']}   NQ-MNQ divergence = {d['nq_mnq_divergence']}")
    print(f"  qqq/ndx ratio  = {d['qqq_ndx_ratio']}")
    print(f"  source skew    = {d['source_skew_seconds']}s")
    if d["quality_flags"]:
        print("  flags:")
        for f in d["quality_flags"]:
            print(f"    - {f}")
    if meta["errors"]:
        print("  errors:")
        for k, v in meta["errors"].items():
            print(f"    - {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
