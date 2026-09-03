import unittest
from datetime import datetime, timezone

from src.market.absorption import AbsorptionSide, AbsorptionSignal, BubbleColor
from src.market.dxy import DxyState
from src.market.market_structure import StructureSnapshot, StructureState
from src.market.trade_plan import TradeDirection, TradePlan
from src.risk.prop_risk import RiskDecision
from src.signal.composer import SignalAction, compose_signal


def absorption(side):
    color = BubbleColor.RED if side is AbsorptionSide.BUYING else BubbleColor.GREEN
    return AbsorptionSignal(side, color, 2.5, 0.5, "TEST")


def dxy(state):
    return DxyState(
        state=state,
        structure=StructureSnapshot(state, "HH" if state is StructureState.BULLISH else "LH", "HL" if state is StructureState.BULLISH else "LL"),
        atr=1.0,
        reason="TEST",
    )


def plan(direction):
    if direction is TradeDirection.LONG:
        return TradePlan(direction, 100.0, 95.0, 112.0, 50.0, 120.0, 2.4, True, True, "TRADE_PLAN_VALID")
    return TradePlan(direction, 100.0, 105.0, 88.0, 50.0, 120.0, 2.4, True, True, "TRADE_PLAN_VALID")


def risk(passed=True, risk_per_contract=50.0):
    return RiskDecision(
        passed=passed,
        contracts=2 if passed else 0,
        risk_per_contract=risk_per_contract,
        total_risk=100.0 if passed else 0.0,
        remaining_loss_allowance=1000.0,
        remaining_after_stop=900.0 if passed else 1000.0,
        reason="PROP_RISK_PASS" if passed else "TEST_BLOCK",
    )


class SignalComposerTests(unittest.TestCase):
    def setUp(self):
        self.ts = datetime(2026, 9, 3, tzinfo=timezone.utc)

    def test_long_requires_all_locked_gates(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.LONG,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.BUYING),
            dxy=dxy(StructureState.BEARISH),
            trade_plan=plan(TradeDirection.LONG),
            risk=risk(),
        )
        self.assertEqual(result.action, SignalAction.LONG)
        self.assertEqual(result.contracts, 2)
        self.assertEqual(result.dollar_risk, 100.0)
        self.assertEqual(result.reasons, ("ALL_SIGNAL_GATES_PASS",))

    def test_short_requires_all_locked_gates(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.SHORT,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.SELLING),
            dxy=dxy(StructureState.BULLISH),
            trade_plan=plan(TradeDirection.SHORT),
            risk=risk(),
        )
        self.assertEqual(result.action, SignalAction.SHORT)

    def test_wrong_absorption_fails_closed(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.LONG,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.SELLING),
            dxy=dxy(StructureState.BEARISH),
            trade_plan=plan(TradeDirection.LONG),
            risk=risk(),
        )
        self.assertEqual(result.action, SignalAction.NO_TRADE)
        self.assertIn("SELL_SIDE_ABSORPTION_REQUIRED", result.reasons)

    def test_neutral_dxy_fails_closed(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.SHORT,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.SELLING),
            dxy=dxy(StructureState.NEUTRAL),
            trade_plan=plan(TradeDirection.SHORT),
            risk=risk(),
        )
        self.assertEqual(result.action, SignalAction.NO_TRADE)
        self.assertIn("DXY_NOT_BULLISH", result.reasons)

    def test_stale_or_mismatched_risk_math_fails_closed(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.LONG,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.BUYING),
            dxy=dxy(StructureState.BEARISH),
            trade_plan=plan(TradeDirection.LONG),
            risk=risk(risk_per_contract=49.0),
        )
        self.assertEqual(result.action, SignalAction.NO_TRADE)
        self.assertIn("RISK_PLAN_MISMATCH", result.reasons)

    def test_prop_risk_failure_fails_closed(self):
        result = compose_signal(
            timestamp=self.ts,
            symbol="MGC",
            direction=TradeDirection.LONG,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=absorption(AbsorptionSide.BUYING),
            dxy=dxy(StructureState.BEARISH),
            trade_plan=plan(TradeDirection.LONG),
            risk=risk(passed=False),
        )
        self.assertEqual(result.action, SignalAction.NO_TRADE)
        self.assertTrue(any(reason.startswith("PROP_RISK_FAIL:") for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
