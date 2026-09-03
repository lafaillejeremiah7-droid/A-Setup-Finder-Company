from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.backtest.ablation import (
    AbsorptionMode,
    CandidateSignal,
    absorption_confirms,
    apply_absorption_mode,
)
from src.market.absorption import AbsorptionSide
from src.signal.composer import SignalAction, SignalResult


NOW = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)


def actionable(action: SignalAction) -> SignalResult:
    if action is SignalAction.LONG:
        entry, stop, target = 100.0, 99.0, 102.0
    else:
        entry, stop, target = 100.0, 101.0, 98.0
    return SignalResult(
        timestamp=NOW,
        symbol="MGC",
        action=action,
        entry=entry,
        stop=stop,
        target=target,
        contracts=1,
        dollar_risk=10.0,
        rr=2.0,
        dxy_state="BEARISH" if action is SignalAction.LONG else "BULLISH",
        reasons=("CORE_SETUP_PASS",),
    )


class AbsorptionAblationTests(unittest.TestCase):
    def test_directional_confirmation_semantics(self) -> None:
        self.assertTrue(absorption_confirms(SignalAction.LONG, AbsorptionSide.BUYING))
        self.assertFalse(absorption_confirms(SignalAction.LONG, AbsorptionSide.SELLING))
        self.assertTrue(absorption_confirms(SignalAction.SHORT, AbsorptionSide.SELLING))
        self.assertFalse(absorption_confirms(SignalAction.SHORT, AbsorptionSide.BUYING))

    def test_off_never_gives_absorption_veto_power(self) -> None:
        signal = actionable(SignalAction.LONG)
        candidate = CandidateSignal(signal, AbsorptionSide.NONE)
        self.assertIs(apply_absorption_mode(candidate, AbsorptionMode.OFF), signal)

    def test_required_blocks_missing_or_wrong_absorption(self) -> None:
        signal = actionable(SignalAction.SHORT)
        candidate = CandidateSignal(signal, AbsorptionSide.BUYING)
        result = apply_absorption_mode(candidate, AbsorptionMode.REQUIRED)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.action, SignalAction.NO_TRADE)
        self.assertIn("ABLATION_ABSORPTION_REQUIRED_FAIL", result.reasons)

    def test_required_allows_directionally_correct_absorption(self) -> None:
        signal = actionable(SignalAction.SHORT)
        candidate = CandidateSignal(signal, AbsorptionSide.SELLING)
        self.assertIs(apply_absorption_mode(candidate, AbsorptionMode.REQUIRED), signal)

    def test_rank_only_does_not_change_execution_sample(self) -> None:
        signal = actionable(SignalAction.LONG)
        candidate = CandidateSignal(signal, AbsorptionSide.NONE, absorption_score=2.7)
        self.assertIs(apply_absorption_mode(candidate, AbsorptionMode.RANK_ONLY), signal)


if __name__ == "__main__":
    unittest.main()
