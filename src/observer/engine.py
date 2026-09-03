from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from src.market.absorption import (
    AbsorptionConfig,
    AbsorptionSide,
    AbsorptionSignal,
    detect_absorption,
)
from src.market.market_structure import Trendline
from src.market.normalize_tradovate_bar import MarketBar
from src.observer.structure import Level, Structure, Timeframe, TrendLines


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Grade(str, Enum):
    A_MINUS = "A-"
    A = "A"
    A_PLUS = "A+"


class EventType(str, Enum):
    BREAK = "BREAK"
    RETEST = "RETEST"
    BOUNCE = "BOUNCE"


# --- geometry / tuning (all hypotheses, mirror the strategy spec) ---
BREAK_ATR = 0.10        # close must clear the line by this * ATR to count as a break
TOUCH_ATR = 0.15        # within this * ATR of the line = interacting with it
STOP_BUFFER_ATR = 0.15  # stop placed this * ATR beyond the safety line
MIN_STOP_ATR = 0.50     # reject setups whose stop sits inside routine noise
MIN_RR = 2.0            # reward:risk gate
ABS_Z_MIN = 2.0         # medium/big bubble threshold (|z| >= this)


@dataclass(frozen=True)
class Setup:
    timestamp: datetime
    direction: Direction
    grade: Grade
    event: EventType
    line_timeframe: Timeframe
    entry: float
    stop: float
    target: float
    rr: float
    risk_points: float
    in_zone: bool
    zone_kind: str | None
    absorption: str
    reasons: tuple[str, ...]

    def key(self) -> tuple:
        # Dedupe by the *nature* of the setup, not exact price, so a cluster of
        # near-identical retest/bounce bars in chop counts as one setup rather
        # than repeatedly burning the daily alert budget.
        return (self.direction, self.event, self.line_timeframe)


def _line_value_at_time(line: Trendline, ts: datetime, htf_bars: Sequence[MarketBar]) -> float | None:
    """Project a higher-timeframe trend line to an arbitrary timestamp.

    The Trendline stores integer bar indices from its own timeframe. We convert
    the target timestamp into fractional bar-index space of that timeframe using
    the higher-timeframe bar spacing, then evaluate. Uses only completed HTF bars.
    """
    if len(htf_bars) < 2:
        return None
    step = (htf_bars[1].timestamp - htf_bars[0].timestamp).total_seconds()
    if step <= 0:
        return None
    base_ts = htf_bars[0].timestamp
    frac_index = (ts - base_ts).total_seconds() / step
    return line.value_at(frac_index)


def _nearest_opposing_level(
    entry: float, direction: Direction, structure: Structure
) -> float | None:
    if direction is Direction.LONG:
        above = [lvl.center for lvl in structure.levels if lvl.center > entry]
        return min(above) if above else None
    below = [lvl.center for lvl in structure.levels if lvl.center < entry]
    return max(below) if below else None


def _favorable_level(entry: float, direction: Direction, structure: Structure, atr: float) -> Level | None:
    tol = TOUCH_ATR * atr
    for lvl in structure.levels:
        if abs(lvl.center - entry) <= tol or lvl.contains(entry):
            if direction is Direction.LONG and lvl.is_support:
                return lvl
            if direction is Direction.SHORT and lvl.is_resistance:
                return lvl
    return None


def _absorption_confirms(direction: Direction, sig: AbsorptionSignal) -> bool:
    """Red/lower-wick buying absorption => bullish (long).
    Green/upper-wick selling absorption => bearish (short)."""
    if sig.aggression_z is None:
        return False
    if abs(sig.aggression_z) < ABS_Z_MIN:
        return False
    if direction is Direction.LONG:
        return sig.side is AbsorptionSide.BUYING
    return sig.side is AbsorptionSide.SELLING


@dataclass(frozen=True)
class _LineHit:
    direction: Direction
    event: EventType
    line: Trendline
    timeframe: Timeframe


def _detect_line_event(
    bars_5m: Sequence[MarketBar],
    lines: TrendLines,
    htf_bars: Sequence[MarketBar],
) -> _LineHit | None:
    """Reactive detection on the just-completed 5m bar.

    BREAK  : close clears the line by BREAK_ATR * ATR (down-line up = long,
             up-line down = short).
    BOUNCE : bar touches the line within TOUCH_ATR and closes rejecting it
             (respecting the line) without breaking through.
    RETEST : previous bar broke; this bar returns to the broken line from the
             far side and closes continuing the break direction.
    """
    if len(bars_5m) < 2 or lines.atr is None or lines.atr <= 0:
        return None
    atr = lines.atr
    bar = bars_5m[-1]
    prev = bars_5m[-2]
    ts = bar.timestamp

    for line, is_up in ((lines.down, False), (lines.up, True)):
        if line is None:
            continue
        lv = _line_value_at_time(line, ts, htf_bars)
        prev_lv = _line_value_at_time(line, prev.timestamp, htf_bars)
        if lv is None or prev_lv is None:
            continue

        if not is_up:
            # down-line (resistance): break UP => long
            broke_up = bar.close > lv + BREAK_ATR * atr
            prev_broke = prev.close > prev_lv + BREAK_ATR * atr
            if broke_up and not prev_broke:
                return _LineHit(Direction.LONG, EventType.BREAK, line, lines.timeframe)
            # retest: prev broke up, now dips back to line and closes back up
            if prev_broke and bar.low <= lv + TOUCH_ATR * atr and bar.close > lv:
                return _LineHit(Direction.LONG, EventType.RETEST, line, lines.timeframe)
            # bounce off resistance => short (touch from below, reject down)
            touched = bar.high >= lv - TOUCH_ATR * atr and bar.close < lv
            if touched and bar.close < bar.open and bar.high <= lv + BREAK_ATR * atr:
                return _LineHit(Direction.SHORT, EventType.BOUNCE, line, lines.timeframe)
        else:
            # up-line (support): break DOWN => short
            broke_down = bar.close < lv - BREAK_ATR * atr
            prev_broke = prev.close < prev_lv - BREAK_ATR * atr
            if broke_down and not prev_broke:
                return _LineHit(Direction.SHORT, EventType.BREAK, line, lines.timeframe)
            if prev_broke and bar.high >= lv - TOUCH_ATR * atr and bar.close < lv:
                return _LineHit(Direction.SHORT, EventType.RETEST, line, lines.timeframe)
            # bounce off support => long (touch from above, reject up)
            touched = bar.low <= lv + TOUCH_ATR * atr and bar.close > lv
            if touched and bar.close > bar.open and bar.low >= lv - BREAK_ATR * atr:
                return _LineHit(Direction.LONG, EventType.BOUNCE, line, lines.timeframe)
    return None


def evaluate(
    *,
    bars_5m: Sequence[MarketBar],
    structure: Structure,
    htf_bars_4h: Sequence[MarketBar],
    htf_bars_1h: Sequence[MarketBar],
    absorption_config: AbsorptionConfig = AbsorptionConfig(),
) -> Setup | None:
    """Evaluate the just-completed 5m bar. Returns a graded Setup or None.

    Order of operations (all reactive, closed-bar only):
      1. Detect a break/retest/bounce of a 4H or 1H trend line.
      2. Build safety-line stop + nearest-opposing-level target; gate on
         MIN_STOP_ATR and MIN_RR.
      3. Determine IN_ZONE (price inside a 4H AS/AR or 1H S/R).
      4. Absorption: required (medium/big, confirming) when IN_ZONE, else bonus.
      5. Grade A-/A/A+.
    """
    if not bars_5m:
        return None

    # 1H lines take priority (tighter/execution), then 4H.
    for lines, htf in ((structure.lines_1h, htf_bars_1h), (structure.lines_4h, htf_bars_4h)):
        hit = _detect_line_event(bars_5m, lines, htf)
        if hit is not None:
            break
    if hit is None:
        return None

    bar = bars_5m[-1]
    atr = lines.atr
    if atr is None or atr <= 0:
        return None

    entry = bar.close
    direction = hit.direction

    # --- safety line = the OPPOSITE line to the one that was broken ---
    # A long breaks the down (resistance) line, so the up (support) line is the
    # safety line, and vice versa. This is the strategy's action/safety pairing.
    safety_line = lines.up if direction is Direction.LONG else lines.down

    reasons: list[str] = [f"{hit.event.value}_{hit.timeframe.value}_LINE"]

    stop: float | None = None
    if safety_line is not None:
        safety_val = _line_value_at_time(safety_line, bar.timestamp, htf)
        if safety_val is not None:
            if direction is Direction.LONG:
                stop = safety_val - STOP_BUFFER_ATR * atr
            else:
                stop = safety_val + STOP_BUFFER_ATR * atr
    if stop is None:
        # fall back to a structural ATR stop behind entry if no opposing line
        stop = entry - MIN_STOP_ATR * atr if direction is Direction.LONG else entry + MIN_STOP_ATR * atr
        reasons.append("FALLBACK_ATR_STOP")

    risk_points = abs(entry - stop)
    if risk_points < MIN_STOP_ATR * atr:
        return None  # stop inside noise

    # Target: prefer a real opposing level (structural, honest reward). If none
    # exists in open space, fall back to a projected MIN_RR target so a clean
    # A- break is not silently dropped just because auto-detection found no
    # discrete level ahead.
    level_target = _nearest_opposing_level(entry, direction, structure)
    used_level_target = False
    if level_target is not None and (
        (direction is Direction.LONG and level_target > entry)
        or (direction is Direction.SHORT and level_target < entry)
    ):
        target = level_target
        reward_points = abs(target - entry)
        rr = reward_points / risk_points if risk_points > 0 else 0.0
        if rr >= MIN_RR:
            used_level_target = True
        else:
            # level too close for min R:R -> project instead
            target = entry + MIN_RR * risk_points if direction is Direction.LONG else entry - MIN_RR * risk_points
            reward_points = abs(target - entry)
            rr = MIN_RR
            reasons.append("PROJECTED_TARGET_LEVEL_TOO_CLOSE")
    else:
        target = entry + MIN_RR * risk_points if direction is Direction.LONG else entry - MIN_RR * risk_points
        reward_points = abs(target - entry)
        rr = MIN_RR
        reasons.append("PROJECTED_TARGET_NO_LEVEL")

    # --- context: is price inside a zone? ---
    zone = structure.zone_containing(entry)
    in_zone = zone is not None

    # --- absorption ---
    absorption = detect_absorption(bars_5m, len(bars_5m) - 1, absorption_config)
    abs_confirms = _absorption_confirms(direction, absorption)
    c_sr = _favorable_level(entry, direction, structure, atr) is not None
    c_abs = abs_confirms

    if in_zone:
        # chop inside a zone: require a medium/big confirming bubble
        if not c_abs:
            return None
        reasons.append("IN_ZONE_ABSORPTION_REQUIRED_PASS")
    else:
        reasons.append("CLEAN_CONTEXT_ABSORPTION_OPTIONAL")

    if c_sr:
        reasons.append("SR_CONFLUENCE")
    if c_abs:
        reasons.append(f"ABSORPTION_CONFIRM_z={absorption.aggression_z:.2f}")

    points = int(c_sr) + int(c_abs)
    grade = Grade.A_MINUS if points == 0 else Grade.A if points == 1 else Grade.A_PLUS

    return Setup(
        timestamp=bar.timestamp,
        direction=direction,
        grade=grade,
        event=hit.event,
        line_timeframe=hit.timeframe,
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        rr=round(rr, 2),
        risk_points=round(risk_points, 2),
        in_zone=in_zone,
        zone_kind=zone.kind.value if zone else None,
        absorption=absorption.side.value,
        reasons=tuple(reasons),
    )
