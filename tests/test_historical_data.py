from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.data.historical import (
    HistoricalDataError,
    RollSegment,
    apply_roll_schedule,
    parse_canonical_csv,
    validate_mgc_dx_alignment,
)


class HistoricalDataTests(unittest.TestCase):
    def test_mgc_requires_executed_side_volume(self) -> None:
        text = "timestamp,contract,open,high,low,close,volume\n2026-01-02T14:35:00Z,MGCG6,100,101,99,100.5,10\n"
        with self.assertRaisesRegex(HistoricalDataError, "EXECUTED_SIDE_VOLUME_COLUMNS_MISSING"):
            parse_canonical_csv(text, instrument="MGC", require_executed_side_volume=True)

    def test_timezone_naive_timestamp_rejected(self) -> None:
        text = (
            "timestamp,contract,open,high,low,close,volume,bid_volume,offer_volume\n"
            "2026-01-02T14:35:00,MGCG6,100,101,99,100.5,10,4,6\n"
        )
        with self.assertRaisesRegex(HistoricalDataError, "TIMESTAMP_MUST_BE_TIMEZONE_AWARE"):
            parse_canonical_csv(text, instrument="MGC", require_executed_side_volume=True)

    def test_roll_schedule_selects_actual_contract_without_back_adjustment(self) -> None:
        text = (
            "timestamp,contract,open,high,low,close,volume,bid_volume,offer_volume\n"
            "2026-01-02T14:35:00Z,MGCG6,100,101,99,100.5,10,4,6\n"
            "2026-01-02T14:40:00Z,MGCG6,100.5,102,100,101,11,5,6\n"
            "2026-01-02T14:45:00Z,MGCJ6,105,106,104,105.5,12,5,7\n"
        )
        series = parse_canonical_csv(text, instrument="MGC", require_executed_side_volume=True)
        base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        segments = (
            RollSegment("MGCG6", base, base + timedelta(minutes=15)),
            RollSegment("MGCJ6", base + timedelta(minutes=15), base + timedelta(minutes=30)),
        )
        rolled = apply_roll_schedule(series, segments)
        self.assertEqual([item.contract for item in rolled.bars], ["MGCG6", "MGCG6", "MGCJ6"])
        self.assertEqual(rolled.bars[-1].bar.open, 105.0)

    def test_roll_contract_mismatch_fails_closed(self) -> None:
        text = (
            "timestamp,contract,open,high,low,close,volume,bid_volume,offer_volume\n"
            "2026-01-02T14:35:00Z,MGCJ6,100,101,99,100.5,10,4,6\n"
        )
        series = parse_canonical_csv(text, instrument="MGC", require_executed_side_volume=True)
        start = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        with self.assertRaisesRegex(HistoricalDataError, "CONTRACT_MISMATCH"):
            apply_roll_schedule(series, (RollSegment("MGCG6", start, start + timedelta(hours=1)),))

    def test_dx_alignment_accepts_latest_completed_dx_bar(self) -> None:
        mgc_text = (
            "timestamp,contract,open,high,low,close,volume,bid_volume,offer_volume\n"
            "2026-01-02T14:35:00Z,MGCG6,100,101,99,100.5,10,4,6\n"
            "2026-01-02T14:40:00Z,MGCG6,100.5,102,100,101,11,5,6\n"
        )
        dx_text = (
            "timestamp,contract,open,high,low,close,volume\n"
            "2026-01-02T14:30:00Z,DXH6,99,99.2,98.9,99.1,20\n"
            "2026-01-02T14:40:00Z,DXH6,99.1,99.3,99,99.2,21\n"
        )
        mgc = parse_canonical_csv(mgc_text, instrument="MGC", require_executed_side_volume=True)
        dx = parse_canonical_csv(dx_text, instrument="DX", require_executed_side_volume=False)
        validate_mgc_dx_alignment(mgc, dx, max_dx_lag_seconds=600)

    def test_stale_dx_fails_closed(self) -> None:
        mgc_text = (
            "timestamp,contract,open,high,low,close,volume,bid_volume,offer_volume\n"
            "2026-01-02T15:00:00Z,MGCG6,100,101,99,100.5,10,4,6\n"
        )
        dx_text = (
            "timestamp,contract,open,high,low,close,volume\n"
            "2026-01-02T14:30:00Z,DXH6,99,99.2,98.9,99.1,20\n"
        )
        mgc = parse_canonical_csv(mgc_text, instrument="MGC", require_executed_side_volume=True)
        dx = parse_canonical_csv(dx_text, instrument="DX", require_executed_side_volume=False)
        with self.assertRaisesRegex(HistoricalDataError, "DX_STALE"):
            validate_mgc_dx_alignment(mgc, dx, max_dx_lag_seconds=900)


if __name__ == "__main__":
    unittest.main()
