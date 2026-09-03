from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.backtest.engine import BacktestContext, ClosedBarView
from src.backtest.gibrc_provider import GIBRCReplayProvider
from src.market.normalize_tradovate_bar import MarketBar
from src.risk.prop_risk import AccountState, PropFirmRules


BASE = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


def bar(index: int, price: float = 100.0) -> MarketBar:
    return MarketBar(
        timestamp=BASE + timedelta(minutes=5 * index),
        open=price,
        high=price + 0.5,
        low=price - 0.5,
        close=price,
        volume=100.0,
        bid_volume=50.0,
        offer_volume=50.0,
    )


class GIBRCReplayProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = PropFirmRules(
            starting_balance=25_000.0,
            maximum_loss=1_500.0,
            drawdown_type="EOD",
            maximum_contracts=2,
            maximum_risk_per_trade=150.0,
        )
        self.provider = GIBRCReplayProvider(
            rules=self.rules,
            account_state_provider=lambda context: AccountState(
                current_equity=25_000.0,
                current_drawdown_floor=23_500.0,
            ),
        )

    def test_insufficient_history_returns_no_candidate(self) -> None:
        mgc = [bar(i) for i in range(5)]
        dxy = [bar(i, 99.0) for i in range(5)]
        context = BacktestContext(
            index=4,
            timestamp=mgc[-1].timestamp,
            mgc=ClosedBarView(mgc, len(mgc)),
            dxy=ClosedBarView(dxy, len(dxy)),
        )
        self.assertIsNone(self.provider.candidate(context))

    def test_provider_is_callable_and_fails_closed_with_no_signal(self) -> None:
        mgc = [bar(i) for i in range(14)]
        dxy = [bar(i, 99.0) for i in range(14)]
        context = BacktestContext(
            index=13,
            timestamp=mgc[-1].timestamp,
            mgc=ClosedBarView(mgc, len(mgc)),
            dxy=ClosedBarView(dxy, len(dxy)),
        )
        self.assertIsNone(self.provider(context))


if __name__ == "__main__":
    unittest.main()
