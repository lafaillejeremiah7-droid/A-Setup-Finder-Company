from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from typing import Sequence

from .normalize_tradovate_bar import MarketBar, bar_delta, has_executed_side_volume


class AbsorptionSide(str, Enum):
    BUYING = "BUYING"      # buyers absorb aggressive selling -> bullish evidence
    SELLING = "SELLING"    # sellers absorb aggressive buying -> bearish evidence
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class BubbleColor(str, Enum):
    RED = "RED"            # lower-wick buying absorption, TradingView visual convention
    GREEN = "GREEN"        # upper-wick selling absorption, TradingView visual convention
    NONE = "NONE"


@dataclass(frozen=True)
class AbsorptionConfig:
    lookback: int = 20
    aggression_z_min: float = 2.0
    max_close_location_for_selling_absorption: float = 0.65
    min_close_location_for_buying_absorption: float = 0.35
    min_rejection_wick_fraction: float = 0.20


@dataclass(frozen=True)
class AbsorptionSignal:
    side: AbsorptionSide
    bubble_color: BubbleColor
    aggression_z: float | None
    close_location: float | None
    reason: str

    @property
    def bullish(self) -> bool:
        return self.side is AbsorptionSide.BUYING

    @property
    def bearish(self) -> bool:
        return self.side is AbsorptionSide.SELLING


def _population_zscore(value: float, history: Sequence[float]) -> float | None:
    if len(history) < 2:
        return None
    mean = sum(history) / len(history)
    variance = sum((item - mean) ** 2 for item in history) / len(history)
    if variance <= 0:
        return None
    return (value - mean) / sqrt(variance)


def detect_absorption(
    bars: Sequence[MarketBar],
    index: int,
    config: AbsorptionConfig = AbsorptionConfig(),
) -> AbsorptionSignal:
    """Detect a closed-bar absorption event using executed bid/offer volume.

    RED lower-wick bubble = buyers absorb aggressive selling (bullish evidence).
    GREEN upper-wick bubble = sellers absorb aggressive buying (bearish evidence).

    Numerical thresholds are hypotheses and must be validated on MGC.
    Missing executed-side volume or insufficient clean history fails closed as UNKNOWN.
    """
    if config.lookback < 2:
        raise ValueError("ABSORPTION_LOOKBACK_TOO_SMALL")
    if config.aggression_z_min <= 0:
        raise ValueError("ABSORPTION_Z_THRESHOLD_MUST_BE_POSITIVE")
    if index < 0 or index >= len(bars):
        raise ValueError("INVALID_BAR_INDEX")

    bar = bars[index]
    if not has_executed_side_volume(bar):
        return AbsorptionSignal(
            AbsorptionSide.UNKNOWN,
            BubbleColor.NONE,
            None,
            None,
            "EXECUTED_SIDE_VOLUME_MISSING",
        )

    # Require the entire configured lookback to exist and contain executed-side
    # volume. A partial window can make a z-score look extreme simply because
    # the sample is too small, so the detector fails closed instead.
    if index < config.lookback:
        return AbsorptionSignal(
            AbsorptionSide.UNKNOWN,
            BubbleColor.NONE,
            None,
            None,
            "INSUFFICIENT_DELTA_HISTORY",
        )

    history_start = index - config.lookback
    history_deltas: list[float] = []
    for historical_bar in bars[history_start:index]:
        delta = bar_delta(historical_bar)
        if delta is None:
            return AbsorptionSignal(
                AbsorptionSide.UNKNOWN,
                BubbleColor.NONE,
                None,
                None,
                "DELTA_HISTORY_INCOMPLETE",
            )
        history_deltas.append(delta)

    current_delta = bar_delta(bar)
    assert current_delta is not None
    aggression_z = _population_zscore(current_delta, history_deltas)
    if aggression_z is None:
        return AbsorptionSignal(
            AbsorptionSide.UNKNOWN,
            BubbleColor.NONE,
            None,
            None,
            "DELTA_HISTORY_HAS_NO_VARIANCE",
        )

    candle_range = bar.high - bar.low
    if candle_range <= 0:
        return AbsorptionSignal(
            AbsorptionSide.NONE,
            BubbleColor.NONE,
            aggression_z,
            None,
            "ZERO_RANGE_BAR",
        )

    close_location = (bar.close - bar.low) / candle_range
    body_high = max(bar.open, bar.close)
    body_low = min(bar.open, bar.close)
    upper_wick_fraction = max(0.0, bar.high - body_high) / candle_range
    lower_wick_fraction = max(0.0, body_low - bar.low) / candle_range

    # A positive z-score alone does not guarantee positive delta if the history
    # is strongly negative. Require the raw delta sign to agree with the claimed
    # aggressive side so we never label net selling as aggressive buying, or vice versa.
    if (
        current_delta > 0
        and aggression_z >= config.aggression_z_min
        and close_location <= config.max_close_location_for_selling_absorption
        and upper_wick_fraction >= config.min_rejection_wick_fraction
    ):
        return AbsorptionSignal(
            AbsorptionSide.SELLING,
            BubbleColor.GREEN,
            aggression_z,
            close_location,
            "AGGRESSIVE_BUYING_ABSORBED_BY_SELLERS",
        )

    if (
        current_delta < 0
        and aggression_z <= -config.aggression_z_min
        and close_location >= config.min_close_location_for_buying_absorption
        and lower_wick_fraction >= config.min_rejection_wick_fraction
    ):
        return AbsorptionSignal(
            AbsorptionSide.BUYING,
            BubbleColor.RED,
            aggression_z,
            close_location,
            "AGGRESSIVE_SELLING_ABSORBED_BY_BUYERS",
        )

    return AbsorptionSignal(
        AbsorptionSide.NONE,
        BubbleColor.NONE,
        aggression_z,
        close_location,
        "NO_QUALIFYING_ABSORPTION",
    )
