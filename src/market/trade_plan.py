from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import ceil, floor, isfinite
from typing import Sequence


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class TradePlanConfig:
    sl_buffer_atr: float = 0.15
    minimum_rr: float = 2.0
    point_value: float = 10.0
    min_tick: float = 0.1


@dataclass(frozen=True)
class TradePlan:
    direction: TradeDirection
    entry: float
    stop: float | None
    target: float | None
    risk_per_contract: float | None
    reward_per_contract: float | None
    rr: float | None
    rr_pass: bool
    valid: bool
    reason: str


def _require_positive_finite(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name}_MUST_BE_POSITIVE_FINITE")


def _snap_down(value: float, tick: float) -> float:
    return floor((value / tick) + 1e-12) * tick


def _snap_up(value: float, tick: float) -> float:
    return ceil((value / tick) - 1e-12) * tick


def select_nearest_target(
    entry: float,
    candidates: Sequence[float],
    direction: TradeDirection,
) -> float | None:
    """Select the nearest already-valid opposing structural target.

    Upstream code is responsible for supplying only active opposing S/R or
    structural swing prices. This function never invents a farther target to
    manufacture R:R.
    """
    valid = [float(price) for price in candidates if isfinite(float(price))]
    if direction is TradeDirection.LONG:
        above = [price for price in valid if price > entry]
        return min(above) if above else None
    below = [price for price in valid if price < entry]
    return max(below) if below else None


def build_trade_plan(
    *,
    direction: TradeDirection,
    confirmation_close: float,
    invalidation_price: float,
    atr: float,
    target_candidates: Sequence[float],
    config: TradePlanConfig = TradePlanConfig(),
) -> TradePlan:
    """Build Entry/SL/TP/R:R from structural inputs only.

    V1 entry is the completed confirmation-bar close. The stop is anchored to
    the setup invalidation price plus the configured ATR buffer. Position size
    is deliberately not considered here, so a stop is never tightened merely
    to force a desired contract count.
    """
    _require_positive_finite(confirmation_close, "ENTRY")
    _require_positive_finite(invalidation_price, "INVALIDATION")
    _require_positive_finite(atr, "ATR")
    _require_positive_finite(config.min_tick, "MIN_TICK")
    _require_positive_finite(config.point_value, "POINT_VALUE")
    if config.sl_buffer_atr < 0 or not isfinite(config.sl_buffer_atr):
        raise ValueError("SL_BUFFER_ATR_MUST_BE_NONNEGATIVE_FINITE")
    if config.minimum_rr <= 0 or not isfinite(config.minimum_rr):
        raise ValueError("MINIMUM_RR_MUST_BE_POSITIVE_FINITE")

    entry = float(confirmation_close)
    buffer = config.sl_buffer_atr * atr

    if direction is TradeDirection.LONG:
        raw_stop = invalidation_price - buffer
        stop = _snap_down(raw_stop, config.min_tick)
        if stop >= entry:
            return TradePlan(direction, entry, stop, None, None, None, None, False, False, "LONG_STOP_NOT_BELOW_ENTRY")
    else:
        raw_stop = invalidation_price + buffer
        stop = _snap_up(raw_stop, config.min_tick)
        if stop <= entry:
            return TradePlan(direction, entry, stop, None, None, None, None, False, False, "SHORT_STOP_NOT_ABOVE_ENTRY")

    raw_target = select_nearest_target(entry, target_candidates, direction)
    if raw_target is None:
        return TradePlan(direction, entry, stop, None, None, None, None, False, False, "NO_VALID_OPPOSING_TARGET")

    # Snap targets toward the entry rather than away from it. This is
    # conservative and keeps all prices on the instrument's minimum tick.
    target = _snap_down(raw_target, config.min_tick) if direction is TradeDirection.LONG else _snap_up(raw_target, config.min_tick)
    if direction is TradeDirection.LONG and target <= entry:
        return TradePlan(direction, entry, stop, target, None, None, None, False, False, "LONG_TARGET_NOT_ABOVE_ENTRY")
    if direction is TradeDirection.SHORT and target >= entry:
        return TradePlan(direction, entry, stop, target, None, None, None, False, False, "SHORT_TARGET_NOT_BELOW_ENTRY")

    risk_per_contract = abs(entry - stop) * config.point_value
    reward_per_contract = abs(target - entry) * config.point_value
    if risk_per_contract <= 0:
        return TradePlan(direction, entry, stop, target, None, None, None, False, False, "ZERO_OR_NEGATIVE_RISK")

    rr = reward_per_contract / risk_per_contract
    rr_pass = rr >= config.minimum_rr
    return TradePlan(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        risk_per_contract=risk_per_contract,
        reward_per_contract=reward_per_contract,
        rr=rr,
        rr_pass=rr_pass,
        valid=True,
        reason="TRADE_PLAN_VALID" if rr_pass else "RR_BELOW_MINIMUM",
    )
