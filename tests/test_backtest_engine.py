import unittest
from datetime import datetime, timedelta, timezone

from src.backtest.engine import (
    AmbiguousBarPolicy,
    BacktestConfig,
    ClosedBarView,
    EntryTiming,
    ExitReason,
    run_backtest,
)
from src.market.normalize_tradovate_bar import MarketBar
from src.signal.composer import SignalAction, SignalResult


START = datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc)


def bar(index, open_price, high, low, close, *, minutes=5):
    return MarketBar(
        timestamp=START + timedelta(minutes=minutes * index),
        open=float(open_price),
        high=float(high),
        low=float(low),
        close=float(close),
    )


def signal_for(current_bar, action=SignalAction.LONG, entry=None, stop=90, target=110, contracts=1):
    entry_price = current_bar.close if entry is None else float(entry)
    return SignalResult(
        timestamp=current_bar.timestamp,
        symbol="MGC",
        action=action,
        entry=entry_price,
        stop=float(stop),
        target=float(target),
        contracts=contracts,
        dollar_risk=abs(entry_price - stop) * 10 * contracts,
        rr=2.0,
        dxy_state="BEARISH" if action is SignalAction.LONG else "BULLISH",
        reasons=("ALL_SIGNAL_GATES_PASS",),
    )


class ClosedBarViewTests(unittest.TestCase):
    def test_future_bar_is_not_indexable(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 101, 102, 100, 101)]
        view = ClosedBarView(bars, 1)
        self.assertEqual(len(view), 1)
        self.assertEqual(view[0].close, 100)
        with self.assertRaisesRegex(IndexError, "BAR_NOT_VISIBLE_YET"):
            _ = view[1]


class NoLookaheadReplayTests(unittest.TestCase):
    def test_provider_sees_only_completed_mgc_and_dxy_prefixes(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 101, 102, 100, 101),
            bar(2, 102, 103, 101, 102),
        ]
        dxy = [
            MarketBar(START - timedelta(minutes=1), 99, 100, 98, 99),
            MarketBar(START + timedelta(minutes=4), 100, 101, 99, 100),
            MarketBar(START + timedelta(minutes=9), 101, 102, 100, 101),
        ]
        seen = []

        def provider(ctx):
            seen.append((len(ctx.mgc), len(ctx.dxy), ctx.mgc[-1].timestamp, ctx.timestamp))
            with self.assertRaises(IndexError):
                _ = ctx.mgc[len(ctx.mgc)]
            return None

        result = run_backtest(mgc_bars=mgc, dxy_bars=dxy, signal_provider=provider)
        self.assertEqual([item[:2] for item in seen], [(1, 1), (2, 2), (3, 3)])
        self.assertTrue(all(last == now for _, _, last, now in seen))
        self.assertEqual(result.provider_calls, 3)

    def test_unsorted_bars_are_rejected(self):
        mgc = [bar(1, 100, 101, 99, 100), bar(0, 100, 101, 99, 100)]
        with self.assertRaisesRegex(ValueError, "MGC_TIMESTAMPS_MUST_BE_STRICTLY_INCREASING"):
            run_backtest(mgc_bars=mgc, dxy_bars=[], signal_provider=lambda ctx: None)


class ExecutionModelTests(unittest.TestCase):
    def test_signal_close_cannot_exit_on_the_signal_generating_bar(self):
        mgc = [
            bar(0, 100, 115, 85, 100),
            bar(1, 100, 111, 99, 108),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1]) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(entry_slippage_ticks=0, exit_slippage_ticks=0),
        )
        self.assertEqual(result.summary.trades, 1)
        self.assertEqual(result.trades[0].exit_timestamp, mgc[1].timestamp)
        self.assertEqual(result.trades[0].exit_reason, ExitReason.TARGET)
        self.assertEqual(result.trades[0].net_pnl, 100.0)

    def test_ambiguous_bar_defaults_to_stop_first(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 111, 89, 100),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1]) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(entry_slippage_ticks=0, exit_slippage_ticks=0),
        )
        self.assertEqual(result.trades[0].exit_reason, ExitReason.STOP)
        self.assertEqual(result.trades[0].net_pnl, -100.0)

    def test_target_first_must_be_explicitly_requested(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 111, 89, 100),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1]) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(
                entry_slippage_ticks=0,
                exit_slippage_ticks=0,
                ambiguous_bar_policy=AmbiguousBarPolicy.TARGET_FIRST,
            ),
        )
        self.assertEqual(result.trades[0].exit_reason, ExitReason.TARGET)
        self.assertEqual(result.trades[0].net_pnl, 100.0)

    def test_next_open_entry_uses_only_the_following_bar_open(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 103, 104, 98, 102),
            bar(2, 102, 111, 101, 108),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1]) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(
                entry_timing=EntryTiming.NEXT_BAR_OPEN,
                entry_slippage_ticks=0,
                exit_slippage_ticks=0,
            ),
        )
        self.assertEqual(result.trades[0].filled_entry, 103.0)
        self.assertEqual(result.trades[0].entry_timestamp, mgc[1].timestamp)

    def test_stop_gap_fills_at_first_available_open_not_theoretical_stop(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 85, 88, 80, 84),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1]) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(entry_slippage_ticks=0, exit_slippage_ticks=0),
        )
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, ExitReason.STOP)
        self.assertEqual(trade.filled_exit, 85.0)
        self.assertEqual(trade.net_pnl, -150.0)

    def test_slippage_and_round_trip_costs_are_charged(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 111, 99, 108),
        ]

        def provider(ctx):
            return signal_for(ctx.mgc[-1], contracts=2) if ctx.index == 0 else None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(
                entry_slippage_ticks=1,
                exit_slippage_ticks=1,
                round_trip_cost_per_contract=3.0,
            ),
        )
        trade = result.trades[0]
        self.assertAlmostEqual(trade.filled_entry, 100.1)
        self.assertAlmostEqual(trade.filled_exit, 109.9)
        self.assertAlmostEqual(trade.gross_pnl, 196.0)
        self.assertAlmostEqual(trade.costs, 6.0)
        self.assertAlmostEqual(trade.net_pnl, 190.0)

    def test_repeated_identical_actionable_signal_is_suppressed_until_reset(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 101, 99, 100),
            bar(2, 100, 101, 99, 100),
        ]

        def provider(ctx):
            if ctx.index in (0, 1):
                return signal_for(ctx.mgc[-1])
            return None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(
                entry_slippage_ticks=0,
                exit_slippage_ticks=0,
                force_close_at_end=True,
            ),
        )
        self.assertEqual(result.duplicate_signals_suppressed, 1)
        self.assertEqual(result.summary.trades, 1)


class SummaryTests(unittest.TestCase):
    def test_summary_reports_profit_factor_expectancy_and_closed_trade_drawdown(self):
        mgc = [
            bar(0, 100, 101, 99, 100),
            bar(1, 100, 111, 99, 110),
            bar(2, 100, 101, 99, 100),
            bar(3, 100, 101, 89, 90),
        ]

        def provider(ctx):
            if ctx.index in (0, 2):
                return signal_for(ctx.mgc[-1])
            return None

        result = run_backtest(
            mgc_bars=mgc,
            dxy_bars=[],
            signal_provider=provider,
            config=BacktestConfig(entry_slippage_ticks=0, exit_slippage_ticks=0),
        )
        self.assertEqual(result.summary.trades, 2)
        self.assertEqual(result.summary.wins, 1)
        self.assertEqual(result.summary.losses, 1)
        self.assertAlmostEqual(result.summary.win_rate, 0.5)
        self.assertAlmostEqual(result.summary.net_pnl, 0.0)
        self.assertAlmostEqual(result.summary.profit_factor, 1.0)
        self.assertAlmostEqual(result.summary.expectancy_per_trade, 0.0)
        self.assertAlmostEqual(result.summary.max_closed_trade_drawdown, 100.0)


if __name__ == "__main__":
    unittest.main()
