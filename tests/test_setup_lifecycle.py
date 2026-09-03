from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.market.absorption import AbsorptionSide, AbsorptionSignal, BubbleColor
from src.signal.setup_lifecycle import (
    LifecycleConfig,
    SetupLifecycle,
    SetupObservation,
    SetupState,
)


BASE = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


def absorption(side: AbsorptionSide) -> AbsorptionSignal:
    color = (
        BubbleColor.RED
        if side is AbsorptionSide.BUYING
        else BubbleColor.GREEN
        if side is AbsorptionSide.SELLING
        else BubbleColor.NONE
    )
    return AbsorptionSignal(side, color, 2.5, 0.5, "TEST")


def obs(
    index: int,
    *,
    bullish_location: bool = False,
    bearish_location: bool = False,
    side: AbsorptionSide = AbsorptionSide.NONE,
    bullish_confirmation: bool = False,
    bearish_confirmation: bool = False,
    close: float = 100.0,
) -> SetupObservation:
    return SetupObservation(
        index=index,
        timestamp=BASE + timedelta(minutes=5 * index),
        bullish_location=bullish_location,
        bearish_location=bearish_location,
        absorption=absorption(side),
        bullish_confirmation=bullish_confirmation,
        bearish_confirmation=bearish_confirmation,
        bullish_invalidation_price=99.0,
        bearish_invalidation_price=101.0,
        close=close,
    )


class SetupLifecycleTests(unittest.TestCase):
    def test_long_arms_then_confirms_only_on_later_bar(self) -> None:
        lifecycle = SetupLifecycle()
        armed = lifecycle.update(obs(10, bullish_location=True, side=AbsorptionSide.BUYING))
        self.assertEqual(armed.state, SetupState.ARMED)
        self.assertFalse(armed.actionable)

        confirmed = lifecycle.update(obs(11, bullish_confirmation=True))
        self.assertEqual(confirmed.state, SetupState.CONFIRMED)
        self.assertTrue(confirmed.actionable)
        self.assertIsNone(lifecycle.armed)

    def test_short_arms_with_selling_absorption(self) -> None:
        lifecycle = SetupLifecycle()
        result = lifecycle.update(obs(3, bearish_location=True, side=AbsorptionSide.SELLING))
        self.assertEqual(result.state, SetupState.ARMED)
        assert result.setup is not None
        self.assertEqual(result.setup.direction.value, "SHORT")

    def test_wrong_absorption_does_not_arm_when_required(self) -> None:
        lifecycle = SetupLifecycle()
        result = lifecycle.update(obs(1, bullish_location=True, side=AbsorptionSide.SELLING))
        self.assertEqual(result.state, SetupState.IDLE)

    def test_location_can_arm_when_absorption_is_off_for_ablation(self) -> None:
        lifecycle = SetupLifecycle(LifecycleConfig(require_absorption=False))
        result = lifecycle.update(obs(1, bullish_location=True, side=AbsorptionSide.NONE))
        self.assertEqual(result.state, SetupState.ARMED)
        self.assertEqual(result.reason, "LOCATION_ARMED_ABSORPTION_OFF")

    def test_ambiguous_dual_location_fails_closed_when_absorption_is_off(self) -> None:
        lifecycle = SetupLifecycle(LifecycleConfig(require_absorption=False))
        result = lifecycle.update(
            obs(1, bullish_location=True, bearish_location=True, side=AbsorptionSide.NONE)
        )
        self.assertEqual(result.state, SetupState.IDLE)
        self.assertEqual(result.reason, "AMBIGUOUS_BOTH_DIRECTIONS_ARMABLE")

    def test_invalidation_price_kills_long_before_confirmation(self) -> None:
        lifecycle = SetupLifecycle()
        lifecycle.update(obs(2, bullish_location=True, side=AbsorptionSide.BUYING))
        result = lifecycle.update(obs(3, close=98.9, bullish_confirmation=True))
        self.assertEqual(result.state, SetupState.INVALIDATED)
        self.assertFalse(result.actionable)

    def test_opposite_structure_kills_setup(self) -> None:
        lifecycle = SetupLifecycle()
        lifecycle.update(obs(5, bearish_location=True, side=AbsorptionSide.SELLING))
        result = lifecycle.update(obs(6, bullish_confirmation=True))
        self.assertEqual(result.state, SetupState.INVALIDATED)
        self.assertEqual(result.reason, "OPPOSITE_STRUCTURE_CONFIRMED")

    def test_setup_expires_after_configured_window(self) -> None:
        lifecycle = SetupLifecycle(LifecycleConfig(max_confirmation_bars=2))
        lifecycle.update(obs(1, bullish_location=True, side=AbsorptionSide.BUYING))
        self.assertEqual(lifecycle.update(obs(2)).state, SetupState.ARMED)
        self.assertEqual(lifecycle.update(obs(3)).state, SetupState.ARMED)
        result = lifecycle.update(obs(4))
        self.assertEqual(result.state, SetupState.EXPIRED)

    def test_same_index_after_arm_is_rejected(self) -> None:
        lifecycle = SetupLifecycle()
        lifecycle.update(obs(8, bullish_location=True, side=AbsorptionSide.BUYING))
        with self.assertRaisesRegex(ValueError, "OBSERVATIONS_MUST_ADVANCE_AFTER_ARM"):
            lifecycle.update(obs(8, bullish_confirmation=True))


if __name__ == "__main__":
    unittest.main()
