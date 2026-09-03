import unittest
from datetime import datetime, timedelta, timezone

from src.market.absorption import (
    AbsorptionConfig,
    AbsorptionSide,
    BubbleColor,
    detect_absorption,
)
from src.market.dxy import (
    dxy_supports_gold_long,
    dxy_supports_gold_short,
    evaluate_dxy_state,
)
from src.market.market_structure import (
    Pivot,
    PivotType,
    StructureConfig,
    StructureState,
    build_trendline,
    independent_trendline_touch,
)
from src.market.normalize_tradovate_bar import MarketBar


def make_bar(index, open_price, high, low, close, bid=None, offer=None):
    return MarketBar(
        timestamp=datetime(2026, 9, 2, tzinfo=timezone.utc) + timedelta(minutes=5 * index),
        open=float(open_price),
        high=float(high),
        low=float(low),
        close=float(close),
        bid_volume=None if bid is None else float(bid),
        offer_volume=None if offer is None else float(offer),
    )


class IndependentTrendlineTouchTests(unittest.TestCase):
    def test_retouch_requires_half_atr_move_away(self):
        line = build_trendline(
            Pivot(0, 100, PivotType.LOW, 1),
            Pivot(4, 104, PivotType.LOW, 5),
        )
        bars = [make_bar(i, 100 + i, 102 + i, 100 + i, 101 + i) for i in range(8)]
        bars[5] = make_bar(5, 106, 107, 105, 106)
        bars[6] = make_bar(6, 111, 112, 110, 111)
        bars[7] = make_bar(7, 108, 109, 107, 108)
        atr_values = [4.0] * len(bars)

        self.assertTrue(
            independent_trendline_touch(
                bars,
                7,
                line,
                atr_values,
                previous_touch_index=5,
                touch_atr=0.15,
                rearm_atr=0.50,
            )
        )

    def test_retouch_does_not_count_without_rearm_move(self):
        line = build_trendline(
            Pivot(0, 100, PivotType.LOW, 1),
            Pivot(4, 104, PivotType.LOW, 5),
        )
        bars = [make_bar(i, 100 + i, 102 + i, 100 + i, 101 + i) for i in range(8)]
        bars[5] = make_bar(5, 106, 107, 105, 106)
        bars[6] = make_bar(6, 107, 108, 106.2, 107)
        bars[7] = make_bar(7, 108, 109, 107, 108)
        atr_values = [4.0] * len(bars)

        self.assertFalse(
            independent_trendline_touch(
                bars,
                7,
                line,
                atr_values,
                previous_touch_index=5,
                touch_atr=0.15,
                rearm_atr=0.50,
            )
        )


class AbsorptionTests(unittest.TestCase):
    def _history(self):
        deltas = [-10, 0, 10, -5, 5]
        bars = []
        for index, delta in enumerate(deltas):
            bid = 100
            offer = 100 + delta
            if delta < 0:
                bid = 100 - delta
                offer = 100
            bars.append(make_bar(index, 100, 102, 98, 100, bid=bid, offer=offer))
        return bars

    def test_green_upper_bubble_means_sellers_absorb_aggressive_buying(self):
        bars = self._history()
        bars.append(make_bar(5, 100, 110, 99, 102, bid=100, offer=200))
        signal = detect_absorption(
            bars,
            5,
            AbsorptionConfig(lookback=5, aggression_z_min=2.0),
        )
        self.assertEqual(signal.side, AbsorptionSide.SELLING)
        self.assertEqual(signal.bubble_color, BubbleColor.GREEN)
        self.assertTrue(signal.bearish)

    def test_red_lower_bubble_means_buyers_absorb_aggressive_selling(self):
        bars = self._history()
        bars.append(make_bar(5, 100, 101, 90, 98, bid=200, offer=100))
        signal = detect_absorption(
            bars,
            5,
            AbsorptionConfig(lookback=5, aggression_z_min=2.0),
        )
        self.assertEqual(signal.side, AbsorptionSide.BUYING)
        self.assertEqual(signal.bubble_color, BubbleColor.RED)
        self.assertTrue(signal.bullish)

    def test_missing_executed_side_volume_fails_closed(self):
        bars = self._history() + [make_bar(5, 100, 110, 99, 102)]
        signal = detect_absorption(bars, 5, AbsorptionConfig(lookback=5))
        self.assertEqual(signal.side, AbsorptionSide.UNKNOWN)

    def test_partial_history_fails_closed(self):
        bars = self._history()[:3]
        bars.append(make_bar(3, 100, 110, 99, 102, bid=100, offer=200))
        signal = detect_absorption(bars, 3, AbsorptionConfig(lookback=5))
        self.assertEqual(signal.side, AbsorptionSide.UNKNOWN)
        self.assertEqual(signal.reason, "INSUFFICIENT_DELTA_HISTORY")

    def test_positive_zscore_with_nonpositive_raw_delta_cannot_be_buying_aggression(self):
        # Historical deltas are strongly negative, so a zero delta can have a high
        # positive z-score. The raw sign gate must prevent calling that aggressive buying.
        bars = []
        historical_deltas = [-100, -90, -110, -95, -105]
        for index, delta in enumerate(historical_deltas):
            bars.append(make_bar(index, 100, 102, 98, 100, bid=100 - delta, offer=100))
        bars.append(make_bar(5, 100, 110, 99, 102, bid=100, offer=100))
        signal = detect_absorption(
            bars,
            5,
            AbsorptionConfig(lookback=5, aggression_z_min=2.0),
        )
        self.assertEqual(signal.side, AbsorptionSide.NONE)


class DxyStateTests(unittest.TestCase):
    def test_bullish_hh_hl_dxy_supports_gold_short(self):
        bars = [
            make_bar(0, 9, 10, 8, 9),
            make_bar(1, 10, 12, 9, 11),
            make_bar(2, 9, 11, 7, 10),
            make_bar(3, 11, 13, 9, 12),
            make_bar(4, 10, 12, 8, 11),
            make_bar(5, 12, 14, 10, 13),
            make_bar(6, 12, 13, 9, 12),
        ]
        state = evaluate_dxy_state(
            bars,
            StructureConfig(atr_period=2, pivot_width=1, structure_tolerance_atr=0.01),
        )
        self.assertEqual(state.state, StructureState.BULLISH)
        self.assertTrue(dxy_supports_gold_short(state))
        self.assertFalse(dxy_supports_gold_long(state))

    def test_bearish_lh_ll_dxy_supports_gold_long(self):
        bars = [
            make_bar(0, 13, 14, 12, 13),
            make_bar(1, 14, 15, 13, 14),
            make_bar(2, 11, 13, 10, 11),
            make_bar(3, 13, 14, 12, 13),
            make_bar(4, 10, 12, 9, 10),
            make_bar(5, 12, 13, 11, 12),
            make_bar(6, 9, 11, 8, 9),
        ]
        state = evaluate_dxy_state(
            bars,
            StructureConfig(atr_period=2, pivot_width=1, structure_tolerance_atr=0.01),
        )
        self.assertEqual(state.state, StructureState.BEARISH)
        self.assertTrue(dxy_supports_gold_long(state))
        self.assertFalse(dxy_supports_gold_short(state))

    def test_missing_dxy_data_is_neutral(self):
        state = evaluate_dxy_state([])
        self.assertEqual(state.state, StructureState.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
