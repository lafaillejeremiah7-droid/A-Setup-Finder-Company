from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .market_structure import (
    StructureConfig,
    StructureSnapshot,
    StructureState,
    classify_market_structure,
    detect_confirmed_pivots,
    pivots_known_by,
    wilder_atr,
)
from .normalize_tradovate_bar import MarketBar


@dataclass(frozen=True)
class DxyState:
    state: StructureState
    structure: StructureSnapshot
    atr: float | None
    reason: str


def evaluate_dxy_state(
    bars: Sequence[MarketBar],
    config: StructureConfig = StructureConfig(),
) -> DxyState:
    """Return BULLISH, BEARISH, or NEUTRAL from confirmed DXY structure.

    TradingView's TVC:DXY chart is useful as a visual cross-check, but this
    engine does not copy TradingView's indicator rating. It implements the
    strategy spec directly: HH+HL is bullish, LH+LL is bearish, everything
    else is neutral. Insufficient data fails closed to NEUTRAL.
    """
    if not bars:
        neutral = StructureSnapshot(StructureState.NEUTRAL, None, None)
        return DxyState(StructureState.NEUTRAL, neutral, None, "NO_DXY_BARS")

    atr_values = wilder_atr(bars, config.atr_period)
    latest_atr = atr_values[-1]
    if latest_atr is None or latest_atr <= 0:
        neutral = StructureSnapshot(StructureState.NEUTRAL, None, None)
        return DxyState(StructureState.NEUTRAL, neutral, latest_atr, "DXY_ATR_UNAVAILABLE")

    pivots = detect_confirmed_pivots(bars, config.pivot_width)
    known = pivots_known_by(pivots, len(bars) - 1)
    structure = classify_market_structure(
        known,
        atr=latest_atr,
        tolerance_atr=config.structure_tolerance_atr,
    )
    if structure.state is StructureState.NEUTRAL:
        reason = "DXY_STRUCTURE_NEUTRAL_OR_INSUFFICIENT"
    elif structure.state is StructureState.BULLISH:
        reason = "DXY_CONFIRMED_HH_HL"
    else:
        reason = "DXY_CONFIRMED_LH_LL"

    return DxyState(structure.state, structure, latest_atr, reason)


def dxy_supports_gold_long(state: DxyState) -> bool:
    return state.state is StructureState.BEARISH


def dxy_supports_gold_short(state: DxyState) -> bool:
    return state.state is StructureState.BULLISH
