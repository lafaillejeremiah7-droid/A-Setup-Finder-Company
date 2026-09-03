import unittest
from datetime import datetime, timedelta, timezone

from src.market.market_structure import (
    Pivot,
    PivotType,
    StructureState,
    bearish_bos,
    build_trendline,
    bullish_bos,
    classify_market_structure,
    cluster_price_zones,
    detect_confirmed_pivots,
    latest_valid_trendline,
    pivots_known_by,
    trendline_broken,
    trendline_touch,
    true_ranges,
    wilder_atr,
    zone_broken,
    zone_touched,
)
from src.market.normalize_tradovate_bar import MarketBar


def make_bar(index, open_price, high, low, close):
    return MarketBar(
        timestamp=datetime(2026, 9, 2, tzinfo=timezone.utc) + timedelta(minutes=5 * index),
        open=float(open_price),
        high=float(high),
        low=float(low),
        close=float(close),
    )


class MarketStructureTests(unittest.TestCase):
    def test_true_range_includes_gap_from_previous_close(self):
        bars = [
            make_bar(0, 100, 102, 99, 101),
            make_bar(1, 105, 106, 104, 105),
        ]
        self.assertEqual(true_ranges(bars), [3.0, 5.0])

    def test_wilder_atr_uses_seed_then_recursive_smoothing(self):
        bars = [
            make_bar(0, 10, 12, 10, 11),
            make_bar(1, 11, 14, 11, 13),
            make_bar(2, 13, 15, 12, 14),
            make_bar(3, 14, 18, 14, 17),
        ]
        atr = wilder_atr(bars, period=3)
        self.assertEqual(atr[:2], [None, None])
        self.assertAlmostEqual(atr[2], (2 + 3 + 3) / 3)
        self.assertAlmostEqual(atr[3], (((8 / 3) * 2) + 4) / 3)

    def test_pivot_confirmation_has_no_lookahead_leak(self):
        bars = [
            make_bar(0, 100, 101, 99, 100),
            make_bar(1, 100, 103, 98, 101),
            make_bar(2, 101, 110, 100, 105),
            make_bar(3, 105, 106, 97, 101),
            make_bar(4, 101, 104, 96, 100),
        ]
        pivots = detect_confirmed_pivots(bars, width=2)
        high = next(p for p in pivots if p.kind is PivotType.HIGH)
        self.assertEqual(high.index, 2)
        self.assertEqual(high.confirmed_at_index, 4)
        self.assertNotIn(high, pivots_known_by(pivots, 3))
        self.assertIn(high, pivots_known_by(pivots, 4))

    def test_classifies_bullish_and_bearish_structure(self):
        bullish = [
            Pivot(1, 100, PivotType.HIGH, 2),
            Pivot(2, 90, PivotType.LOW, 3),
            Pivot(3, 110, PivotType.HIGH, 4),
            Pivot(4, 95, PivotType.LOW, 5),
        ]
        bearish = [
            Pivot(1, 110, PivotType.HIGH, 2),
            Pivot(2, 100, PivotType.LOW, 3),
            Pivot(3, 105, PivotType.HIGH, 4),
            Pivot(4, 90, PivotType.LOW, 5),
        ]
        self.assertEqual(classify_market_structure(bullish, atr=5).state, StructureState.BULLISH)
        self.assertEqual(classify_market_structure(bearish, atr=5).state, StructureState.BEARISH)

    def test_bos_requires_close_beyond_buffer_not_wick(self):
        high = Pivot(1, 100, PivotType.HIGH, 2)
        low = Pivot(1, 90, PivotType.LOW, 2)
        wick_only = make_bar(2, 99, 102, 95, 100.1)
        close_above = make_bar(3, 100, 103, 99, 100.6)
        close_below = make_bar(4, 91, 92, 88, 89.4)
        self.assertFalse(bullish_bos(wick_only, high, atr=10, buffer_atr=0.05))
        self.assertTrue(bullish_bos(close_above, high, atr=10, buffer_atr=0.05))
        self.assertTrue(bearish_bos(close_below, low, atr=10, buffer_atr=0.05))

    def test_builds_only_directionally_valid_trendlines(self):
        low1 = Pivot(1, 90, PivotType.LOW, 2)
        low2 = Pivot(5, 94, PivotType.LOW, 6)
        high1 = Pivot(1, 110, PivotType.HIGH, 2)
        high2 = Pivot(5, 106, PivotType.HIGH, 6)
        bullish = build_trendline(low1, low2)
        bearish = build_trendline(high1, high2)
        self.assertIsNotNone(bullish)
        self.assertIsNotNone(bearish)
        self.assertAlmostEqual(bullish.value_at(9), 98)
        self.assertAlmostEqual(bearish.value_at(9), 102)

    def test_latest_valid_trendline_prefers_recent_valid_pair(self):
        pivots = [
            Pivot(1, 90, PivotType.LOW, 2),
            Pivot(4, 92, PivotType.LOW, 5),
            Pivot(8, 95, PivotType.LOW, 9),
        ]
        line = latest_valid_trendline(pivots, PivotType.LOW)
        self.assertEqual(line.first.index, 4)
        self.assertEqual(line.second.index, 8)

    def test_trendline_touch_and_break_are_atr_normalized(self):
        line = build_trendline(
            Pivot(0, 100, PivotType.LOW, 1),
            Pivot(10, 110, PivotType.LOW, 11),
        )
        touching = make_bar(12, 113, 114, 111, 113)
        broken = make_bar(12, 112, 113, 108, 109)
        self.assertTrue(trendline_touch(touching, 12, line, atr=10, touch_atr=0.15))
        self.assertTrue(trendline_broken(broken, 12, line, atr=10, break_atr=0.20))

    def test_clusters_support_and_resistance_zones(self):
        pivots = [
            Pivot(1, 100.0, PivotType.LOW, 2),
            Pivot(5, 100.8, PivotType.LOW, 6),
            Pivot(9, 110.0, PivotType.LOW, 10),
            Pivot(2, 120.0, PivotType.HIGH, 3),
            Pivot(6, 120.5, PivotType.HIGH, 7),
        ]
        support = cluster_price_zones(pivots, PivotType.LOW, atr=4)
        resistance = cluster_price_zones(pivots, PivotType.HIGH, atr=4)
        self.assertEqual(len(support), 1)
        self.assertEqual(len(support[0].members), 2)
        self.assertEqual(len(resistance), 1)

    def test_zone_touch_and_break_use_close_for_break(self):
        pivots = [
            Pivot(1, 100.0, PivotType.HIGH, 2),
            Pivot(5, 100.5, PivotType.HIGH, 6),
        ]
        zone = cluster_price_zones(pivots, PivotType.HIGH, atr=4)[0]
        touching = make_bar(8, 99, 101, 98, 100)
        breaking = make_bar(9, 100, 103, 100, 102)
        self.assertTrue(zone_touched(touching, zone))
        self.assertTrue(zone_broken(breaking, zone, atr=4, buffer_atr=0.05))


if __name__ == "__main__":
    unittest.main()
