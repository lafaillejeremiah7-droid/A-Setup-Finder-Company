from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .normalize_tradovate_bar import MarketBar


class PivotType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class StructureState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: PivotType
    confirmed_at_index: int


@dataclass(frozen=True)
class StructureSnapshot:
    state: StructureState
    high_label: str | None
    low_label: str | None


@dataclass(frozen=True)
class Trendline:
    kind: PivotType
    first: Pivot
    second: Pivot
    slope: float

    def value_at(self, index: int) -> float:
        return self.first.price + self.slope * (index - self.first.index)


@dataclass(frozen=True)
class PriceZone:
    kind: PivotType
    center: float
    lower: float
    upper: float
    members: tuple[Pivot, ...]


@dataclass(frozen=True)
class StructureConfig:
    atr_period: int = 14
    pivot_width: int = 3
    structure_tolerance_atr: float = 0.05
    bos_buffer_atr: float = 0.05
    trendline_touch_atr: float = 0.15
    trendline_break_atr: float = 0.20
    sr_cluster_atr: float = 0.25
    sr_half_width_atr: float = 0.20
    sr_break_buffer_atr: float = 0.05


def true_ranges(bars: Sequence[MarketBar]) -> list[float]:
    values: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            values.append(bar.high - bar.low)
            continue
        previous_close = bars[index - 1].close
        values.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return values


def wilder_atr(bars: Sequence[MarketBar], period: int = 14) -> list[float | None]:
    if period < 1:
        raise ValueError("ATR_PERIOD_MUST_BE_POSITIVE")
    trs = true_ranges(bars)
    result: list[float | None] = [None] * len(trs)
    if len(trs) < period:
        return result

    previous = sum(trs[:period]) / period
    result[period - 1] = previous
    for index in range(period, len(trs)):
        previous = ((previous * (period - 1)) + trs[index]) / period
        result[index] = previous
    return result


def detect_confirmed_pivots(bars: Sequence[MarketBar], width: int = 3) -> list[Pivot]:
    if width < 1:
        raise ValueError("PIVOT_WIDTH_MUST_BE_POSITIVE")

    pivots: list[Pivot] = []
    for index in range(width, len(bars) - width):
        bar = bars[index]
        neighboring_highs = [
            bars[i].high
            for i in range(index - width, index + width + 1)
            if i != index
        ]
        neighboring_lows = [
            bars[i].low
            for i in range(index - width, index + width + 1)
            if i != index
        ]

        if all(bar.high > high for high in neighboring_highs):
            pivots.append(
                Pivot(
                    index=index,
                    price=bar.high,
                    kind=PivotType.HIGH,
                    confirmed_at_index=index + width,
                )
            )
        if all(bar.low < low for low in neighboring_lows):
            pivots.append(
                Pivot(
                    index=index,
                    price=bar.low,
                    kind=PivotType.LOW,
                    confirmed_at_index=index + width,
                )
            )

    return sorted(pivots, key=lambda p: (p.confirmed_at_index, p.index, p.kind.value))


def pivots_known_by(pivots: Sequence[Pivot], bar_index: int) -> list[Pivot]:
    return [pivot for pivot in pivots if pivot.confirmed_at_index <= bar_index]


def classify_market_structure(
    pivots: Sequence[Pivot],
    atr: float | None,
    tolerance_atr: float = 0.05,
) -> StructureSnapshot:
    highs = [pivot for pivot in pivots if pivot.kind is PivotType.HIGH]
    lows = [pivot for pivot in pivots if pivot.kind is PivotType.LOW]
    if len(highs) < 2 or len(lows) < 2 or atr is None or atr <= 0:
        return StructureSnapshot(StructureState.NEUTRAL, None, None)

    tolerance = atr * tolerance_atr
    high_change = highs[-1].price - highs[-2].price
    low_change = lows[-1].price - lows[-2].price

    high_label = "HH" if high_change > tolerance else "LH" if high_change < -tolerance else "EH"
    low_label = "HL" if low_change > tolerance else "LL" if low_change < -tolerance else "EL"

    if high_label == "HH" and low_label == "HL":
        state = StructureState.BULLISH
    elif high_label == "LH" and low_label == "LL":
        state = StructureState.BEARISH
    else:
        state = StructureState.NEUTRAL

    return StructureSnapshot(state, high_label, low_label)


def bullish_bos(bar: MarketBar, swing_high: Pivot, atr: float, buffer_atr: float = 0.05) -> bool:
    if swing_high.kind is not PivotType.HIGH:
        raise ValueError("BULLISH_BOS_REQUIRES_SWING_HIGH")
    return bar.close > swing_high.price + buffer_atr * atr


def bearish_bos(bar: MarketBar, swing_low: Pivot, atr: float, buffer_atr: float = 0.05) -> bool:
    if swing_low.kind is not PivotType.LOW:
        raise ValueError("BEARISH_BOS_REQUIRES_SWING_LOW")
    return bar.close < swing_low.price - buffer_atr * atr


def build_trendline(first: Pivot, second: Pivot) -> Trendline | None:
    if first.kind is not second.kind or second.index <= first.index:
        return None
    if first.kind is PivotType.LOW and second.price <= first.price:
        return None
    if first.kind is PivotType.HIGH and second.price >= first.price:
        return None

    slope = (second.price - first.price) / (second.index - first.index)
    return Trendline(first.kind, first, second, slope)


def latest_valid_trendline(pivots: Sequence[Pivot], kind: PivotType) -> Trendline | None:
    candidates = [pivot for pivot in pivots if pivot.kind is kind]
    for second_index in range(len(candidates) - 1, 0, -1):
        for first_index in range(second_index - 1, -1, -1):
            trendline = build_trendline(candidates[first_index], candidates[second_index])
            if trendline is not None:
                return trendline
    return None


def trendline_touch(
    bar: MarketBar,
    bar_index: int,
    trendline: Trendline,
    atr: float,
    touch_atr: float = 0.15,
) -> bool:
    interaction_price = bar.low if trendline.kind is PivotType.LOW else bar.high
    return abs(interaction_price - trendline.value_at(bar_index)) <= touch_atr * atr


def trendline_broken(
    bar: MarketBar,
    bar_index: int,
    trendline: Trendline,
    atr: float,
    break_atr: float = 0.20,
) -> bool:
    line_value = trendline.value_at(bar_index)
    if trendline.kind is PivotType.LOW:
        return bar.close < line_value - break_atr * atr
    return bar.close > line_value + break_atr * atr


def cluster_price_zones(
    pivots: Sequence[Pivot],
    kind: PivotType,
    atr: float,
    cluster_atr: float = 0.25,
    half_width_atr: float = 0.20,
    minimum_members: int = 2,
) -> list[PriceZone]:
    if atr <= 0:
        raise ValueError("ATR_MUST_BE_POSITIVE")
    if minimum_members < 2:
        raise ValueError("MINIMUM_ZONE_MEMBERS_MUST_BE_AT_LEAST_TWO")

    candidates = sorted(
        (pivot for pivot in pivots if pivot.kind is kind),
        key=lambda pivot: pivot.price,
    )
    threshold = cluster_atr * atr
    clusters: list[list[Pivot]] = []
    current: list[Pivot] = []

    for pivot in candidates:
        if not current:
            current = [pivot]
            continue
        center = sum(member.price for member in current) / len(current)
        if abs(pivot.price - center) <= threshold:
            current.append(pivot)
        else:
            if len(current) >= minimum_members:
                clusters.append(current)
            current = [pivot]

    if len(current) >= minimum_members:
        clusters.append(current)

    half_width = half_width_atr * atr
    zones: list[PriceZone] = []
    for cluster in clusters:
        center = sum(member.price for member in cluster) / len(cluster)
        zones.append(
            PriceZone(
                kind=kind,
                center=center,
                lower=center - half_width,
                upper=center + half_width,
                members=tuple(sorted(cluster, key=lambda pivot: pivot.index)),
            )
        )
    return zones


def zone_touched(bar: MarketBar, zone: PriceZone) -> bool:
    return bar.high >= zone.lower and bar.low <= zone.upper


def zone_broken(bar: MarketBar, zone: PriceZone, atr: float, buffer_atr: float = 0.05) -> bool:
    buffer = buffer_atr * atr
    if zone.kind is PivotType.HIGH:
        return bar.close > zone.upper + buffer
    return bar.close < zone.lower - buffer
