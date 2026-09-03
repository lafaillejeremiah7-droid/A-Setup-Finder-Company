"""Event-driven, no-lookahead backtesting primitives."""

from .engine import (
    AmbiguousBarPolicy,
    BacktestConfig,
    BacktestContext,
    BacktestResult,
    BacktestSummary,
    ClosedBarView,
    EntryTiming,
    ExitReason,
    TradeRecord,
    run_backtest,
)

__all__ = [
    "AmbiguousBarPolicy",
    "BacktestConfig",
    "BacktestContext",
    "BacktestResult",
    "BacktestSummary",
    "ClosedBarView",
    "EntryTiming",
    "ExitReason",
    "TradeRecord",
    "run_backtest",
]
