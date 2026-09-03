from __future__ import annotations

import unittest

from src.market.market_structure import StructureConfig
from src.observer.aggregate import aggregate
from src.observer.engine import Direction, Grade, evaluate
from src.observer.main import DailyGate
from src.observer.structure import detect_structure
from tests.observer.sample_data import build_sample_bars


class ObserverPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = build_sample_bars()

    def test_aggregation_produces_higher_timeframes(self) -> None:
        b1h = aggregate(self.bars, 60)
        b4h = aggregate(self.bars, 240)
        self.assertGreater(len(b1h), 20)
        self.assertGreater(len(b4h), 10)
        # 4h buckets should be strictly increasing in time
        ts = [b.timestamp for b in b4h]
        self.assertEqual(ts, sorted(ts))

    def test_structure_detects_lines(self) -> None:
        b1h = aggregate(self.bars, 60)
        b4h = aggregate(self.bars, 240)
        s = detect_structure(bars_4h=b4h[:-1], bars_1h=b1h[:-1], config=StructureConfig())
        self.assertIsNotNone(s.lines_1h.up)
        self.assertIsNotNone(s.lines_1h.down)

    def test_pipeline_fires_a_long_break(self) -> None:
        """Replaying the engineered coil must yield at least one valid graded
        setup over the tail, with sane day-trade geometry (stop the correct
        side of entry, R:R >= 2, and each gate respected)."""
        found = 0
        for end in range(len(self.bars) - 60, len(self.bars)):
            window = self.bars[: end + 1]
            b1h = aggregate(window, 60)
            b4h = aggregate(window, 240)
            b1c = b1h[:-1] if len(b1h) > 1 else b1h
            b4c = b4h[:-1] if len(b4h) > 1 else b4h
            s = detect_structure(bars_4h=b4c, bars_1h=b1c, config=StructureConfig())
            setup = evaluate(bars_5m=window, structure=s, htf_bars_4h=b4c, htf_bars_1h=b1c)
            if setup is None:
                continue
            found += 1
            self.assertGreaterEqual(setup.rr, 2.0)
            if setup.direction is Direction.LONG:
                self.assertLess(setup.stop, setup.entry)
            else:
                self.assertGreater(setup.stop, setup.entry)
            self.assertIn(setup.grade, (Grade.A_MINUS, Grade.A, Grade.A_PLUS))
        self.assertGreater(found, 0, "engineered coil produced no valid setup")

    def test_daily_gate_caps_and_dedupes(self) -> None:
        from datetime import datetime, timezone

        from src.observer.engine import EventType, Setup
        from src.observer.structure import Timeframe

        gate = DailyGate(max_per_day=2)
        ts = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)

        def mk(direction: Direction, event: EventType) -> Setup:
            return Setup(
                timestamp=ts, direction=direction, grade=Grade.A_MINUS, event=event,
                line_timeframe=Timeframe.H1, entry=2400.0, stop=2395.0, target=2410.0,
                rr=2.0, risk_points=5.0, in_zone=False, zone_kind=None,
                absorption="NONE", reasons=(),
            )

        long_break = mk(Direction.LONG, EventType.BREAK)
        self.assertTrue(gate.allow(long_break))
        # same nature -> deduped
        self.assertFalse(gate.allow(mk(Direction.LONG, EventType.BREAK)))
        # different nature -> allowed (2nd)
        self.assertTrue(gate.allow(mk(Direction.SHORT, EventType.BREAK)))
        # 3rd distinct -> capped
        self.assertFalse(gate.allow(mk(Direction.LONG, EventType.BOUNCE)))


if __name__ == "__main__":
    unittest.main()
