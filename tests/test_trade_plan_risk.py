import unittest

from src.market.trade_plan import (
    TradeDirection,
    TradePlanConfig,
    build_trade_plan,
    select_nearest_target,
)
from src.risk.prop_risk import AccountState, PropFirmRules, assess_prop_risk


class TradePlanTests(unittest.TestCase):
    def test_long_stop_is_structural_and_not_changed_for_position_size(self):
        plan = build_trade_plan(
            direction=TradeDirection.LONG,
            confirmation_close=100.0,
            invalidation_price=95.0,
            atr=2.0,
            target_candidates=[114.0, 112.0],
            config=TradePlanConfig(sl_buffer_atr=0.15, minimum_rr=2.0),
        )
        self.assertTrue(plan.valid)
        self.assertAlmostEqual(plan.stop, 94.7)
        self.assertAlmostEqual(plan.target, 112.0)
        self.assertAlmostEqual(plan.risk_per_contract, 53.0)
        self.assertAlmostEqual(plan.reward_per_contract, 120.0)
        self.assertTrue(plan.rr_pass)
        self.assertGreater(plan.rr, 2.0)

    def test_short_uses_nearest_target_below_entry(self):
        self.assertEqual(
            select_nearest_target(100.0, [80.0, 92.0, 105.0], TradeDirection.SHORT),
            92.0,
        )

    def test_long_uses_nearest_target_above_entry(self):
        self.assertEqual(
            select_nearest_target(100.0, [112.0, 108.0, 95.0], TradeDirection.LONG),
            108.0,
        )

    def test_missing_opposing_target_invalidates_plan(self):
        plan = build_trade_plan(
            direction=TradeDirection.LONG,
            confirmation_close=100.0,
            invalidation_price=95.0,
            atr=2.0,
            target_candidates=[99.0, 98.0],
        )
        self.assertFalse(plan.valid)
        self.assertEqual(plan.reason, "NO_VALID_OPPOSING_TARGET")

    def test_bad_structural_stop_invalidates_plan(self):
        plan = build_trade_plan(
            direction=TradeDirection.LONG,
            confirmation_close=100.0,
            invalidation_price=101.0,
            atr=1.0,
            target_candidates=[110.0],
        )
        self.assertFalse(plan.valid)
        self.assertEqual(plan.reason, "LONG_STOP_NOT_BELOW_ENTRY")

    def test_rr_below_minimum_is_explicit_but_plan_math_remains_valid(self):
        plan = build_trade_plan(
            direction=TradeDirection.LONG,
            confirmation_close=100.0,
            invalidation_price=95.0,
            atr=2.0,
            target_candidates=[108.0],
            config=TradePlanConfig(minimum_rr=2.0),
        )
        self.assertTrue(plan.valid)
        self.assertFalse(plan.rr_pass)
        self.assertEqual(plan.reason, "RR_BELOW_MINIMUM")


class PropRiskTests(unittest.TestCase):
    def _rules(self, **overrides):
        values = dict(
            starting_balance=25000.0,
            maximum_loss=1500.0,
            drawdown_type="EOD",
            maximum_contracts=2,
            maximum_risk_per_trade=300.0,
            daily_loss_limit=None,
            consistency_limit_pct=None,
        )
        values.update(overrides)
        return PropFirmRules(**values)

    def test_sizes_by_risk_budget_without_moving_stop(self):
        decision = assess_prop_risk(
            risk_per_contract=126.0,
            rules=self._rules(),
            state=AccountState(current_equity=24738.60, current_drawdown_floor=23500.0),
        )
        self.assertTrue(decision.passed)
        self.assertEqual(decision.contracts, 2)
        self.assertAlmostEqual(decision.total_risk, 252.0)
        self.assertAlmostEqual(decision.remaining_loss_allowance, 1238.60)
        self.assertAlmostEqual(decision.remaining_after_stop, 986.60)

    def test_rejects_when_no_contract_fits_per_trade_budget(self):
        decision = assess_prop_risk(
            risk_per_contract=350.0,
            rules=self._rules(maximum_risk_per_trade=300.0),
            state=AccountState(current_equity=24738.60, current_drawdown_floor=23500.0),
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.contracts, 0)
        self.assertEqual(decision.reason, "NO_CONTRACT_FITS_RISK_BUDGET")

    def test_touching_drawdown_floor_fails_closed(self):
        decision = assess_prop_risk(
            risk_per_contract=126.0,
            rules=self._rules(maximum_risk_per_trade=200.0),
            state=AccountState(current_equity=23626.0, current_drawdown_floor=23500.0),
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "STOP_WOULD_REACH_DRAWDOWN_FLOOR")

    def test_daily_loss_limit_can_reduce_contract_count(self):
        decision = assess_prop_risk(
            risk_per_contract=126.0,
            rules=self._rules(daily_loss_limit=500.0),
            state=AccountState(
                current_equity=24738.60,
                current_drawdown_floor=23500.0,
                current_daily_pnl=-300.0,
            ),
        )
        self.assertTrue(decision.passed)
        self.assertEqual(decision.contracts, 1)
        self.assertAlmostEqual(decision.total_risk, 126.0)

    def test_active_consistency_rule_requires_verified_external_state(self):
        decision = assess_prop_risk(
            risk_per_contract=126.0,
            rules=self._rules(consistency_limit_pct=40.0),
            state=AccountState(current_equity=24738.60, current_drawdown_floor=23500.0),
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason, "CONSISTENCY_STATE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
