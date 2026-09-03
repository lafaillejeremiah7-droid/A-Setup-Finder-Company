from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import inf, isclose, isfinite
from typing import Callable, Iterator, Sequence, overload

from src.market.normalize_tradovate_bar import MarketBar
from src.signal.composer import SignalAction, SignalResult


class EntryTiming(str, Enum):
    """How an approved closed-bar signal becomes an executed position."""

    SIGNAL_CLOSE = "SIGNAL_CLOSE"
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"


class AmbiguousBarPolicy(str, Enum):
    """Resolution when the same OHLC bar touches both stop and target.

    With bar data there is no defensible way to know which level traded first.
    STOP_FIRST is the conservative default. Tick replay should eventually replace
    this policy for final validation.
    """

    STOP_FIRST = "STOP_FIRST"
    TARGET_FIRST = "TARGET_FIRST"
    REJECT_TRADE = "REJECT_TRADE"


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    AMBIGUOUS_BAR_REJECTED = "AMBIGUOUS_BAR_REJECTED"
    END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True)
class BacktestConfig:
    point_value: float = 10.0
    min_tick: float = 0.1
    entry_timing: EntryTiming = EntryTiming.SIGNAL_CLOSE
    entry_slippage_ticks: float = 1.0
    exit_slippage_ticks: float = 1.0
    round_trip_cost_per_contract: float = 0.0
    ambiguous_bar_policy: AmbiguousBarPolicy = AmbiguousBarPolicy.STOP_FIRST
    force_close_at_end: bool = True
    require_signal_timestamp_match: bool = True
    require_signal_close_entry_match: bool = True
    deduplicate_consecutive_signals: bool = True


class ClosedBarView(Sequence[MarketBar]):
    """Read-only prefix view over bars known at one historical instant.

    The view intentionally exposes only the completed prefix. This avoids
    repeatedly copying large slices while preventing ordinary strategy code from
    indexing bars that have not completed yet.
    """

    __slots__ = ("__bars", "__end")

    def __init__(self, bars: Sequence[MarketBar], end: int) -> None:
        if end < 0 or end > len(bars):
            raise ValueError("INVALID_VISIBLE_BAR_END")
        self.__bars = bars
        self.__end = end

    def __len__(self) -> int:
        return self.__end

    @overload
    def __getitem__(self, index: int) -> MarketBar: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[MarketBar, ...]: ...

    def __getitem__(self, index: int | slice) -> MarketBar | tuple[MarketBar, ...]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self.__end)
            return tuple(self.__bars[i] for i in range(start, stop, step))

        resolved = index
        if resolved < 0:
            resolved += self.__end
        if resolved < 0 or resolved >= self.__end:
            raise IndexError("BAR_NOT_VISIBLE_YET")
        return self.__bars[resolved]

    def __iter__(self) -> Iterator[MarketBar]:
        for index in range(self.__end):
            yield self.__bars[index]


@dataclass(frozen=True)
class BacktestContext:
    index: int
    timestamp: datetime
    mgc: ClosedBarView
    dxy: ClosedBarView


SignalProvider = Callable[[BacktestContext], SignalResult | None]


@dataclass(frozen=True)
class TradeRecord:
    signal_timestamp: datetime
    entry_timestamp: datetime
    exit_timestamp: datetime
    symbol: str
    action: SignalAction
    contracts: int
    intended_entry: float
    filled_entry: float
    stop: float
    target: float
    filled_exit: float
    exit_reason: ExitReason
    bars_held: int
    gross_pnl: float
    costs: float
    net_pnl: float
    initial_risk: float
    r_multiple: float | None
    bar_level_mae: float
    bar_level_mfe: float


@dataclass(frozen=True)
class BacktestSummary:
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float | None
    expectancy_per_trade: float | None
    average_r: float | None
    max_closed_trade_drawdown: float


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[TradeRecord, ...]
    summary: BacktestSummary
    provider_calls: int
    duplicate_signals_suppressed: int
    signals_ignored_while_position_open: int
    unfilled_pending_entries: int


@dataclass
class _PendingEntry:
    signal: SignalResult
    signal_index: int


@dataclass
class _OpenTrade:
    signal: SignalResult
    entry_index: int
    entry_timestamp: datetime
    filled_entry: float
    min_low: float
    max_high: float


def _validate_config(config: BacktestConfig) -> None:
    for value, name in (
        (config.point_value, "POINT_VALUE"),
        (config.min_tick, "MIN_TICK"),
    ):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name}_MUST_BE_POSITIVE_FINITE")

    for value, name in (
        (config.entry_slippage_ticks, "ENTRY_SLIPPAGE_TICKS"),
        (config.exit_slippage_ticks, "EXIT_SLIPPAGE_TICKS"),
        (config.round_trip_cost_per_contract, "ROUND_TRIP_COST_PER_CONTRACT"),
    ):
        if not isfinite(value) or value < 0:
            raise ValueError(f"{name}_MUST_BE_NONNEGATIVE_FINITE")


def _validate_series(bars: Sequence[MarketBar], name: str) -> None:
    previous: datetime | None = None
    for bar in bars:
        if previous is not None and bar.timestamp <= previous:
            raise ValueError(f"{name}_TIMESTAMPS_MUST_BE_STRICTLY_INCREASING")
        previous = bar.timestamp


def _signal_key(signal: SignalResult) -> tuple[object, ...]:
    return (
        signal.symbol,
        signal.action,
        signal.entry,
        signal.stop,
        signal.target,
        signal.contracts,
    )


def _validate_actionable_signal(
    signal: SignalResult,
    bar: MarketBar,
    config: BacktestConfig,
) -> None:
    if signal.action not in (SignalAction.LONG, SignalAction.SHORT):
        raise ValueError("ACTIONABLE_SIGNAL_REQUIRED")
    if signal.entry is None or signal.stop is None or signal.target is None:
        raise ValueError("ACTIONABLE_SIGNAL_MISSING_PRICE")
    if signal.contracts < 1:
        raise ValueError("ACTIONABLE_SIGNAL_REQUIRES_CONTRACTS")

    for value, name in (
        (signal.entry, "ENTRY"),
        (signal.stop, "STOP"),
        (signal.target, "TARGET"),
    ):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"SIGNAL_{name}_MUST_BE_POSITIVE_FINITE")

    if config.require_signal_timestamp_match and signal.timestamp != bar.timestamp:
        raise ValueError("SIGNAL_TIMESTAMP_DOES_NOT_MATCH_VISIBLE_BAR")

    if config.entry_timing is EntryTiming.SIGNAL_CLOSE and config.require_signal_close_entry_match:
        if not isclose(signal.entry, bar.close, rel_tol=0.0, abs_tol=config.min_tick / 2.0):
            raise ValueError("SIGNAL_CLOSE_ENTRY_DOES_NOT_MATCH_BAR_CLOSE")

    if signal.action is SignalAction.LONG:
        if not signal.stop < signal.entry < signal.target:
            raise ValueError("INVALID_LONG_PRICE_GEOMETRY")
    else:
        if not signal.target < signal.entry < signal.stop:
            raise ValueError("INVALID_SHORT_PRICE_GEOMETRY")


def _entry_fill(base_price: float, action: SignalAction, config: BacktestConfig) -> float:
    slippage = config.entry_slippage_ticks * config.min_tick
    if action is SignalAction.LONG:
        return base_price + slippage
    return base_price - slippage


def _exit_fill(base_price: float, action: SignalAction, config: BacktestConfig) -> float:
    slippage = config.exit_slippage_ticks * config.min_tick
    # LONG exits are sells (worse = lower); SHORT exits are buys (worse = higher).
    if action is SignalAction.LONG:
        return base_price - slippage
    return base_price + slippage


def _open_from_signal(
    signal: SignalResult,
    entry_index: int,
    entry_timestamp: datetime,
    base_price: float,
    config: BacktestConfig,
) -> _OpenTrade:
    filled = _entry_fill(base_price, signal.action, config)
    return _OpenTrade(
        signal=signal,
        entry_index=entry_index,
        entry_timestamp=entry_timestamp,
        filled_entry=filled,
        min_low=filled,
        max_high=filled,
    )


def _choose_exit_reason(
    stop_hit: bool,
    target_hit: bool,
    config: BacktestConfig,
) -> ExitReason | None:
    if stop_hit and target_hit:
        if config.ambiguous_bar_policy is AmbiguousBarPolicy.STOP_FIRST:
            return ExitReason.STOP
        if config.ambiguous_bar_policy is AmbiguousBarPolicy.TARGET_FIRST:
            return ExitReason.TARGET
        return ExitReason.AMBIGUOUS_BAR_REJECTED
    if stop_hit:
        return ExitReason.STOP
    if target_hit:
        return ExitReason.TARGET
    return None


def _close_trade(
    trade: _OpenTrade,
    bar: MarketBar,
    bar_index: int,
    reason: ExitReason,
    config: BacktestConfig,
) -> TradeRecord:
    signal = trade.signal
    assert signal.entry is not None and signal.stop is not None and signal.target is not None

    if reason is ExitReason.STOP:
        if signal.action is SignalAction.LONG:
            base_exit = bar.open if bar.open <= signal.stop else signal.stop
        else:
            base_exit = bar.open if bar.open >= signal.stop else signal.stop
    elif reason is ExitReason.TARGET:
        # Limit-style target: do not assume a favorable gap improvement from OHLC data.
        base_exit = signal.target
    elif reason is ExitReason.AMBIGUOUS_BAR_REJECTED:
        # Conservative rejection treats the bar as a stop outcome rather than
        # fabricating an unknowable target-before-stop sequence.
        if signal.action is SignalAction.LONG:
            base_exit = bar.open if bar.open <= signal.stop else signal.stop
        else:
            base_exit = bar.open if bar.open >= signal.stop else signal.stop
    else:
        base_exit = bar.close

    filled_exit = _exit_fill(base_exit, signal.action, config)
    contracts = signal.contracts

    if signal.action is SignalAction.LONG:
        gross_pnl = (filled_exit - trade.filled_entry) * config.point_value * contracts
        bar_level_mae = max(0.0, trade.filled_entry - trade.min_low) * config.point_value * contracts
        bar_level_mfe = max(0.0, trade.max_high - trade.filled_entry) * config.point_value * contracts
    else:
        gross_pnl = (trade.filled_entry - filled_exit) * config.point_value * contracts
        bar_level_mae = max(0.0, trade.max_high - trade.filled_entry) * config.point_value * contracts
        bar_level_mfe = max(0.0, trade.filled_entry - trade.min_low) * config.point_value * contracts

    costs = config.round_trip_cost_per_contract * contracts
    net_pnl = gross_pnl - costs
    initial_risk = abs(trade.filled_entry - signal.stop) * config.point_value * contracts
    r_multiple = None if initial_risk <= 0 else net_pnl / initial_risk

    return TradeRecord(
        signal_timestamp=signal.timestamp,
        entry_timestamp=trade.entry_timestamp,
        exit_timestamp=bar.timestamp,
        symbol=signal.symbol,
        action=signal.action,
        contracts=contracts,
        intended_entry=signal.entry,
        filled_entry=trade.filled_entry,
        stop=signal.stop,
        target=signal.target,
        filled_exit=filled_exit,
        exit_reason=reason,
        bars_held=max(0, bar_index - trade.entry_index + 1),
        gross_pnl=gross_pnl,
        costs=costs,
        net_pnl=net_pnl,
        initial_risk=initial_risk,
        r_multiple=r_multiple,
        bar_level_mae=bar_level_mae,
        bar_level_mfe=bar_level_mfe,
    )


def _evaluate_open_trade(
    trade: _OpenTrade,
    bar: MarketBar,
    bar_index: int,
    config: BacktestConfig,
) -> TradeRecord | None:
    signal = trade.signal
    assert signal.stop is not None and signal.target is not None

    trade.min_low = min(trade.min_low, bar.low)
    trade.max_high = max(trade.max_high, bar.high)

    if signal.action is SignalAction.LONG:
        stop_hit = bar.low <= signal.stop
        target_hit = bar.high >= signal.target
    else:
        stop_hit = bar.high >= signal.stop
        target_hit = bar.low <= signal.target

    reason = _choose_exit_reason(stop_hit, target_hit, config)
    if reason is None:
        return None
    return _close_trade(trade, bar, bar_index, reason, config)


def _summarize(trades: Sequence[TradeRecord]) -> BacktestSummary:
    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    losses = sum(1 for trade in trades if trade.net_pnl < 0)
    gross_profit = sum(max(0.0, trade.net_pnl) for trade in trades)
    gross_loss = sum(max(0.0, -trade.net_pnl) for trade in trades)
    net_pnl = sum(trade.net_pnl for trade in trades)

    if trades:
        win_rate = wins / len(trades)
        expectancy = net_pnl / len(trades)
        r_values = [trade.r_multiple for trade in trades if trade.r_multiple is not None]
        average_r = sum(r_values) / len(r_values) if r_values else None
    else:
        win_rate = None
        expectancy = None
        average_r = None

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = inf
    else:
        profit_factor = None

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        cumulative += trade.net_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return BacktestSummary(
        trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        expectancy_per_trade=expectancy,
        average_r=average_r,
        max_closed_trade_drawdown=max_drawdown,
    )


def run_backtest(
    *,
    mgc_bars: Sequence[MarketBar],
    dxy_bars: Sequence[MarketBar],
    signal_provider: SignalProvider,
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """Replay completed bars without exposing future MGC or DXY observations.

    Contract for input timestamps: each MarketBar.timestamp must represent the
    bar's completion/close time. If a vendor supplies bar-open timestamps, the
    ingestion layer must convert them before this engine is used.

    The provider is called only after the current MGC bar is complete. It sees a
    prefix view ending at that bar and only DXY bars whose completion timestamp
    is <= the current MGC completion timestamp. This is the central no-lookahead
    guarantee of the bar-level harness.
    """
    _validate_config(config)
    _validate_series(mgc_bars, "MGC")
    _validate_series(dxy_bars, "DXY")

    closed_trades: list[TradeRecord] = []
    pending: _PendingEntry | None = None
    open_trade: _OpenTrade | None = None
    dxy_end = 0
    provider_calls = 0
    duplicate_signals_suppressed = 0
    signals_ignored_while_position_open = 0
    unfilled_pending_entries = 0
    last_actionable_key: tuple[object, ...] | None = None
    reset_seen_since_last_actionable = True

    for index, bar in enumerate(mgc_bars):
        while dxy_end < len(dxy_bars) and dxy_bars[dxy_end].timestamp <= bar.timestamp:
            dxy_end += 1

        # NEXT_BAR_OPEN orders are created only from a prior completed bar, so
        # filling them here cannot use future information.
        if pending is not None and open_trade is None:
            open_trade = _open_from_signal(
                pending.signal,
                entry_index=index,
                entry_timestamp=bar.timestamp,
                base_price=bar.open,
                config=config,
            )
            pending = None

        # Existing positions are evaluated before a new signal is generated from
        # this bar's close. A SIGNAL_CLOSE entry therefore cannot stop/target on
        # the same historical bar that created it.
        if open_trade is not None:
            closed = _evaluate_open_trade(open_trade, bar, index, config)
            if closed is not None:
                closed_trades.append(closed)
                open_trade = None

        context = BacktestContext(
            index=index,
            timestamp=bar.timestamp,
            mgc=ClosedBarView(mgc_bars, index + 1),
            dxy=ClosedBarView(dxy_bars, dxy_end),
        )
        signal = signal_provider(context)
        provider_calls += 1

        if signal is None or signal.action is SignalAction.NO_TRADE:
            reset_seen_since_last_actionable = True
            continue

        _validate_actionable_signal(signal, bar, config)
        key = _signal_key(signal)
        if (
            config.deduplicate_consecutive_signals
            and not reset_seen_since_last_actionable
            and key == last_actionable_key
        ):
            duplicate_signals_suppressed += 1
            continue

        last_actionable_key = key
        reset_seen_since_last_actionable = False

        if open_trade is not None or pending is not None:
            signals_ignored_while_position_open += 1
            continue

        if config.entry_timing is EntryTiming.SIGNAL_CLOSE:
            open_trade = _open_from_signal(
                signal,
                entry_index=index,
                entry_timestamp=bar.timestamp,
                base_price=signal.entry,
                config=config,
            )
        else:
            pending = _PendingEntry(signal=signal, signal_index=index)

    if pending is not None:
        unfilled_pending_entries += 1

    if open_trade is not None and config.force_close_at_end and mgc_bars:
        final_index = len(mgc_bars) - 1
        final_bar = mgc_bars[final_index]
        # Include the final bar in excursion statistics if the position was
        # opened on that bar at SIGNAL_CLOSE only by leaving its initial
        # excursion at zero; the signal-generating bar occurred before entry.
        if open_trade.entry_index < final_index:
            open_trade.min_low = min(open_trade.min_low, final_bar.low)
            open_trade.max_high = max(open_trade.max_high, final_bar.high)
        closed_trades.append(
            _close_trade(
                open_trade,
                final_bar,
                final_index,
                ExitReason.END_OF_DATA,
                config,
            )
        )

    trades_tuple = tuple(closed_trades)
    return BacktestResult(
        trades=trades_tuple,
        summary=_summarize(trades_tuple),
        provider_calls=provider_calls,
        duplicate_signals_suppressed=duplicate_signals_suppressed,
        signals_ignored_while_position_open=signals_ignored_while_position_open,
        unfilled_pending_entries=unfilled_pending_entries,
    )
