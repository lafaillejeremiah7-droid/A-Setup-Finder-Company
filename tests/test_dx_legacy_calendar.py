from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_dx_barchart_legacy.py"
spec = importlib.util.spec_from_file_location("dx_legacy", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class LegacyDxCalendarTests(unittest.TestCase):
    def test_2015_contract_symbols(self) -> None:
        self.assertEqual(mod.contract_symbol(2015, 3), "DXH15")
        self.assertEqual(mod.contract_symbol(2015, 6), "DXM15")
        self.assertEqual(mod.contract_symbol(2015, 9), "DXU15")
        self.assertEqual(mod.contract_symbol(2015, 12), "DXZ15")

    def test_2015_published_expiry_dates_match_rule(self) -> None:
        self.assertEqual(mod.last_trading_day(2015, 3).date().isoformat(), "2015-03-16")
        self.assertEqual(mod.last_trading_day(2015, 6).date().isoformat(), "2015-06-15")

    def test_quarterly_contracts_cover_requested_span(self) -> None:
        start = datetime(2015, 1, 1, tzinfo=timezone.utc)
        end = datetime(2015, 7, 1, tzinfo=timezone.utc)
        symbols = [symbol for symbol, _ in mod.quarterly_contracts(start, end)]
        self.assertIn("DXH15", symbols)
        self.assertIn("DXM15", symbols)


if __name__ == "__main__":
    unittest.main()
