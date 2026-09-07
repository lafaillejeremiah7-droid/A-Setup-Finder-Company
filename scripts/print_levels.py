#!/usr/bin/env python3
"""Print a Gamma Map levels file as plain, human-readable terminal text.

WHAT THIS DOES
--------------
Loads a levels JSON file (produced by gammamap.levels.write_levels, read back with
gammamap.levels.read_levels) and prints the five render-facing lines a human draws by
hand on a Tradovate MNQ chart:

    * the four walls   -- Call Wall, Put Wall, 0DTE Call Wall, 0DTE Put Wall
                          (each with its MNQ price and strength band)
    * the Gamma Flip   -- MNQ price
    * the regime line  -- TOTAL sign, 0DTE sign, and whether they diverge

This is a DISPLAY-ONLY helper (build-spec.md SS9a is the render contract). It reads ONLY
the fields the indicator renders: the walls, the flip, the regime and the provenance
proxy/quality flags. OI, IV, greeks, MaxPain and the full chain are deliberately NOT
printed -- they are ingredients, not render-facing numbers, and dumping them would invite
recomputing (and mis-computing) the walls off the file.

A wall whose price is null (no strike selected, or a historical date with no MNQ basis)
prints "n/a" rather than crashing. If the levels came from a volume PROXY source
(provenance.proxy == true -- no open interest, e.g. the Kaggle historical path), a short
warning notes the numbers are a volume proxy, not true GEX.

The formatting logic is the pure function `format_levels(levels) -> str`; `main()` just
loads the file and prints it, so the formatting is unit-testable without capturing stdout.

USAGE
-----
    PYTHONPATH=src python scripts/print_levels.py data/levels/2026-09-06/230612Z_QQQ.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Allow running as a plain script (python scripts/print_levels.py ...) without PYTHONPATH:
# tests/ and src/ are siblings of scripts/ under the repo root.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gammamap.levels import read_levels  # noqa: E402

# The four wall labels, in the order build_levels emits them (build-spec.md SS9a). We key
# the lookup on the emitted "label" field so the display name comes straight from the file.
_WALL_ORDER = ("Call Wall", "Put Wall", "0DTE Call Wall", "0DTE Put Wall")

# Human-readable strength bands. The engine emits either the SS4 concentration bands
# (WEAK/MODERATE/STRONG/EXTREME) or a coarse low/med/high; we normalize both to an
# uppercase LOW/MED/HIGH-style token for the readout without inventing values.
_STRENGTH_DISPLAY = {
    "low": "LOW",
    "med": "MED",
    "medium": "MED",
    "high": "HIGH",
}

_NA = "n/a"


def _fmt_price(value: Any) -> str:
    """Format an MNQ price, or 'n/a' when it is null/None (e.g. no strike / historical)."""
    if value is None:
        return _NA
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return _NA


def _fmt_strength(value: Any) -> str:
    """Normalize a strength token for display; pass through unknown values uppercased."""
    if not value:
        return _NA
    text = str(value)
    return _STRENGTH_DISPLAY.get(text.lower(), text.upper())


def _wall_by_label(levels: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index the emitted walls list by its 'label' field for ordered lookup."""
    return {str(w.get("label")): w for w in levels.get("walls", []) or []}


def _regime_line(regime: dict[str, Any]) -> str:
    """One-line TOTAL/0DTE regime readout with an explicit divergence note (SS6)."""
    total = regime.get("total_sign", _NA)
    zerodte = regime.get("zerodte_sign", _NA)
    divergence = bool(regime.get("divergence"))
    diverge_txt = "DIVERGE" if divergence else "aligned"
    return f"TOTAL {total}   0DTE {zerodte}   ({diverge_txt})"


def format_levels(levels: dict[str, Any]) -> str:
    """Render a levels dict as a plain, human-readable block of terminal text.

    Pure function: no I/O, no stdout. Reads only the render-facing fields (the four walls,
    the gamma flip, the regime, and the provenance proxy flag) so the output matches the
    indicator contract (build-spec.md SS9a). A null wall/flip price renders as "n/a".
    """
    lines: list[str] = []

    # -- header: instrument, render target and the as-of instant. --------------
    instrument = levels.get("instrument", "?")
    render_target = levels.get("render_target", "?")
    asof = levels.get("asof", "?")
    lines.append("=" * 52)
    lines.append(f"  GAMMA MAP LEVELS  --  {instrument} -> {render_target}")
    lines.append(f"  as of {asof}")
    lines.append("=" * 52)

    # -- proxy warning (provenance.proxy): volume proxy, not true GEX. ----------
    provenance = levels.get("provenance", {}) or {}
    if provenance.get("proxy"):
        lines.append("")
        lines.append("  ! WARNING: volume-proxy levels (no open interest).")
        lines.append("  ! These are a VOLUME PROXY, not true GEX.")

    # -- the four walls: MNQ price + strength band. -----------------------------
    walls = _wall_by_label(levels)
    lines.append("")
    lines.append("  Walls:")
    for label in _WALL_ORDER:
        wall = walls.get(label)
        if wall is None:
            lines.append(f"    {label:<16}  {_NA}")
            continue
        price = _fmt_price(wall.get("mnq_price"))
        strength = _fmt_strength(wall.get("strength"))
        lines.append(f"    {label:<16}  {price:>12}   [{strength}]")

    # -- gamma flip: MNQ price. -------------------------------------------------
    flip = levels.get("gamma_flip", {}) or {}
    lines.append("")
    lines.append(f"    {'Gamma Flip':<16}  {_fmt_price(flip.get('mnq_price')):>12}")

    # -- regime line. -----------------------------------------------------------
    lines.append("")
    lines.append("  Regime:")
    lines.append(f"    {_regime_line(levels.get('regime', {}) or {})}")
    lines.append("")

    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "path", metavar="LEVELS_JSON",
        help="Path to an existing levels JSON file (written by gammamap.levels.write_levels).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"error: no such levels file: {path}", file=sys.stderr)
        return 2
    levels = read_levels(path)
    print(format_levels(levels))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
