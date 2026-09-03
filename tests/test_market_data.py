import unittest

from src.market.instruments import INSTRUMENTS, require_resolved_contract
from src.market.normalize_tradovate_bar import (
    MarketDataError,
    bar_delta,
    bar_delta_pct,
    has_executed_side_volume,
    normalize_tradovate_bar,
)


class MarketDataTests(unittest.TestCase):
    def test_normalizes_bar_and_calculates_executed_side_delta(self):
        bar = normalize_tradovate_bar({
            "timestamp": "2026-09-02T20:00:00.000Z",
            "open": 4400,
            "high": 4403,
            "low": 4398,
            "close": 4401,
            "volume": 300,
            "bidVolume": 120,
            "offerVolume": 180,
        })
        self.assertEqual(bar.volume, 300)
        self.assertTrue(has_executed_side_volume(bar))
        self.assertEqual(bar_delta(bar), 60)
        self.assertAlmostEqual(bar_delta_pct(bar), 0.2)

    def test_missing_bid_offer_volume_disables_order_flow(self):
        bar = normalize_tradovate_bar({
            "timestamp": "2026-09-02T20:00:00.000Z",
            "open": 4400,
            "high": 4403,
            "low": 4398,
            "close": 4401,
        })
        self.assertFalse(has_executed_side_volume(bar))
        self.assertIsNone(bar_delta(bar))
        self.assertIsNone(bar_delta_pct(bar))

    def test_rejects_malformed_ohlc(self):
        with self.assertRaisesRegex(MarketDataError, "INVALID_HIGH"):
            normalize_tradovate_bar({
                "timestamp": "2026-09-02T20:00:00.000Z",
                "open": 4400,
                "high": 4399,
                "low": 4398,
                "close": 4401,
            })

    def test_rejects_negative_total_volume(self):
        with self.assertRaisesRegex(MarketDataError, "INVALID_VOLUME"):
            normalize_tradovate_bar({
                "timestamp": "2026-09-02T20:00:00.000Z",
                "open": 4400,
                "high": 4403,
                "low": 4398,
                "close": 4401,
                "volume": -1,
            })

    def test_mgc_contract_constants(self):
        mgc = INSTRUMENTS["MGC"]
        self.assertEqual(mgc.point_value, 10.0)
        self.assertEqual(mgc.min_tick, 0.1)
        self.assertEqual(mgc.tick_value, 1.0)

    def test_dx_is_dxy_filter(self):
        dx = INSTRUMENTS["DX"]
        self.assertEqual(dx.root, "DX")
        self.assertEqual(dx.role, "DXY_FILTER")

    def test_requires_resolved_active_contract(self):
        with self.assertRaisesRegex(ValueError, "ACTIVE_CONTRACT_UNRESOLVED"):
            require_resolved_contract(None)
        self.assertEqual(require_resolved_contract({"symbol": "MGCV6"})["symbol"], "MGCV6")


if __name__ == "__main__":
    unittest.main()
