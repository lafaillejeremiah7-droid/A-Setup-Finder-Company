from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from src.market.market_structure import (
    PivotType,
    StructureConfig,
    Trendline,
    cluster_price_zones,
    detect_confirmed_pivots,
    latest_valid_trendline,
    pivots_known_by,
    wilder_atr,
)
from src.market.normalize_tradovate_bar import MarketBar


class Timeframe(str, Enum):
    H4 = "4H"
    H1 = "1H"


class LevelKind(str, Enum):
    AS = "AS"  # 4H accumulation support
    AR = "AR"  # 4H accumulation resistance
    S = "S"    # 1H support
    R = "R"    # 1H resistance


@dataclass(frozen=True)
class Level:
    kind: LevelKind
    timeframe: Timeframe
    center: float
    lower: float
    upper: float

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    @property
    def is_support(self) -> bool:
        return self.kind in (LevelKind.AS, LevelKind.S)

    @property
    def is_resistance(self) -> bool:
        return self.kind in (LevelKind.AR, LevelKind.R)


@dataclass(frozen=True)
class TrendLines:
    timeframe: Timeframe
    up: Trendline | None      # support line (drawn on lows)
    down: Trendline | None    # resistance line (drawn on highs)
    atr: float | None


@dataclass(frozen=True)
class Structure:
    levels: tuple[Level, ...]
    lines_4h: TrendLines
    lines_1h: TrendLines

    def zone_containing(self, price: float) -> Level | None:
        """Return the tightest level/zone that currently contains price, if any."""
        containing = [lvl for lvl in self.levels if lvl.contains(price)]
        if not containing:
            return None
        return min(containing, key=lambda lvl: lvl.upper - lvl.lower)


def _levels_from_zones(
    bars: Sequence[MarketBar],
    config: StructureConfig,
    timeframe: Timeframe,
) -> list[Level]:
    atr_values = wilder_atr(bars, config.atr_period)
    atr = atr_values[-1] if atr_values else None
    if atr is None or atr <= 0:
        return []

    pivots = detect_confirmed_pivots(bars, config.pivot_width)
    known = pivots_known_by(pivots, len(bars) - 1)

    support_zones = cluster_price_zones(
        known, PivotType.LOW, atr,
        cluster_atr=config.sr_cluster_atr, half_width_atr=config.sr_half_width_atr,
    )
    resistance_zones = cluster_price_zones(
        known, PivotType.HIGH, atr,
        cluster_atr=config.sr_cluster_atr, half_width_atr=config.sr_half_width_atr,
    )

    support_kind = LevelKind.AS if timeframe is Timeframe.H4 else LevelKind.S
    resistance_kind = LevelKind.AR if timeframe is Timeframe.H4 else LevelKind.R

    levels: list[Level] = []
    for zone in support_zones:
        levels.append(Level(support_kind, timeframe, zone.center, zone.lower, zone.upper))
    for zone in resistance_zones:
        levels.append(Level(resistance_kind, timeframe, zone.center, zone.lower, zone.upper))
    return levels


def _lines_for(bars: Sequence[MarketBar], config: StructureConfig, timeframe: Timeframe) -> TrendLines:
    atr_values = wilder_atr(bars, config.atr_period)
    atr = atr_values[-1] if atr_values else None
    pivots = detect_confirmed_pivots(bars, config.pivot_width)
    known = pivots_known_by(pivots, len(bars) - 1)
    up = latest_valid_trendline(known, PivotType.LOW)
    down = latest_valid_trendline(known, PivotType.HIGH)
    return TrendLines(timeframe=timeframe, up=up, down=down, atr=atr)


def detect_structure(
    *,
    bars_4h: Sequence[MarketBar],
    bars_1h: Sequence[MarketBar],
    config: StructureConfig = StructureConfig(),
) -> Structure:
    """Auto-detect the full top-down structure.

    4H: AS/AR zones + 4H trend lines.
    1H: S/R levels  + 1H trend lines.
    Trend lines are detected on the higher timeframes only (never 5m), matching
    the rule that 5m line-drawing is noise.
    """
    levels: list[Level] = []
    if bars_4h:
        levels.extend(_levels_from_zones(bars_4h, config, Timeframe.H4))
    if bars_1h:
        levels.extend(_levels_from_zones(bars_1h, config, Timeframe.H1))

    lines_4h = _lines_for(bars_4h, config, Timeframe.H4) if bars_4h else TrendLines(Timeframe.H4, None, None, None)
    lines_1h = _lines_for(bars_1h, config, Timeframe.H1) if bars_1h else TrendLines(Timeframe.H1, None, None, None)

    return Structure(tuple(levels), lines_4h, lines_1h)
