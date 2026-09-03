from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite

from src.market.absorption import AbsorptionSide, AbsorptionSignal
from src.market.trade_plan import TradeDirection


class SetupState(str, Enum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class LifecycleConfig:
    """Rules for sequencing location -> absorption -> later confirmation.

    Confirmation is deliberately required on a later completed bar than the
    absorption event. `max_confirmation_bars` is a hypothesis and must be tested.
    """

    max_confirmation_bars: int = 6


@dataclass(frozen=True)
class SetupObservation:
    """All information known at one completed MGC bar.

    Upstream deterministic modules calculate location, absorption, structural
    confirmation, and structural invalidation. This state machine only enforces
    their temporal order; it never peeks at future bars.
    """

    index: int
    timestamp: datetime
    bullish_location: bool
    bearish_location: bool
    absorption: AbsorptionSignal
    bullish_confirmation: bool
    bearish_confirmation: bool
    bullish_invalidation_price: float | None
    bearish_invalidation_price: float | None
    close: float


@dataclass(frozen=True)
class ArmedSetup:
    setup_id: str
    direction: TradeDirection
    armed_index: int
    armed_timestamp: datetime
    invalidation_price: float
    absorption: AbsorptionSignal


@dataclass(frozen=True)
class LifecycleEvent:
    state: SetupState
    setup: ArmedSetup | None
    reason: str
    actionable: bool


class SetupLifecycle:
    """Stateful, closed-bar GIBRC setup sequencer.

    A setup arms only when location and directionally-correct absorption occur
    on the same completed bar. It can confirm only on a later completed bar.
    Opposite structural invalidation, a close through the setup invalidation,
    or expiry kills the setup. A new setup receives a deterministic identity so
    downstream alert/execution deduplication can operate at setup level.
    """

    def __init__(self, config: LifecycleConfig = LifecycleConfig()) -> None:
        if config.max_confirmation_bars < 1:
            raise ValueError("MAX_CONFIRMATION_BARS_MUST_BE_POSITIVE")
        self.config = config
        self._armed: ArmedSetup | None = None
        self._last_terminal_setup_id: str | None = None

    @property
    def armed(self) -> ArmedSetup | None:
        return self._armed

    def reset(self) -> None:
        self._armed = None

    @staticmethod
    def _require_price(value: float | None, name: str) -> float:
        if value is None or not isfinite(value) or value <= 0:
            raise ValueError(f"{name}_MUST_BE_POSITIVE_FINITE")
        return float(value)

    @staticmethod
    def _setup_id(direction: TradeDirection, obs: SetupObservation) -> str:
        return f"{direction.value}:{obs.index}:{obs.timestamp.isoformat()}"

    def _arm(self, direction: TradeDirection, obs: SetupObservation) -> LifecycleEvent:
        if direction is TradeDirection.LONG:
            invalidation = self._require_price(
                obs.bullish_invalidation_price, "BULLISH_INVALIDATION"
            )
        else:
            invalidation = self._require_price(
                obs.bearish_invalidation_price, "BEARISH_INVALIDATION"
            )

        setup = ArmedSetup(
            setup_id=self._setup_id(direction, obs),
            direction=direction,
            armed_index=obs.index,
            armed_timestamp=obs.timestamp,
            invalidation_price=invalidation,
            absorption=obs.absorption,
        )
        self._armed = setup
        return LifecycleEvent(SetupState.ARMED, setup, "LOCATION_AND_ABSORPTION_ARMED", False)

    def update(self, obs: SetupObservation) -> LifecycleEvent:
        if obs.index < 0:
            raise ValueError("OBSERVATION_INDEX_MUST_BE_NONNEGATIVE")
        if not isfinite(obs.close) or obs.close <= 0:
            raise ValueError("OBSERVATION_CLOSE_MUST_BE_POSITIVE_FINITE")

        setup = self._armed

        if setup is None:
            bullish_arm = (
                obs.bullish_location
                and obs.absorption.side is AbsorptionSide.BUYING
            )
            bearish_arm = (
                obs.bearish_location
                and obs.absorption.side is AbsorptionSide.SELLING
            )

            # Ambiguous simultaneous long+short location is not guessed.
            if bullish_arm and bearish_arm:
                return LifecycleEvent(
                    SetupState.IDLE, None, "AMBIGUOUS_BOTH_DIRECTIONS_ARMABLE", False
                )
            if bullish_arm:
                return self._arm(TradeDirection.LONG, obs)
            if bearish_arm:
                return self._arm(TradeDirection.SHORT, obs)
            return LifecycleEvent(SetupState.IDLE, None, "NO_ARMABLE_SETUP", False)

        if obs.index <= setup.armed_index:
            raise ValueError("OBSERVATIONS_MUST_ADVANCE_AFTER_ARM")

        bars_since_arm = obs.index - setup.armed_index
        if bars_since_arm > self.config.max_confirmation_bars:
            expired = setup
            self._armed = None
            self._last_terminal_setup_id = expired.setup_id
            return LifecycleEvent(SetupState.EXPIRED, expired, "CONFIRMATION_WINDOW_EXPIRED", False)

        if setup.direction is TradeDirection.LONG:
            invalidated = obs.close <= setup.invalidation_price
            opposite_confirmation = obs.bearish_confirmation
            confirmed = obs.bullish_confirmation
        else:
            invalidated = obs.close >= setup.invalidation_price
            opposite_confirmation = obs.bullish_confirmation
            confirmed = obs.bearish_confirmation

        if invalidated or opposite_confirmation:
            dead = setup
            self._armed = None
            self._last_terminal_setup_id = dead.setup_id
            reason = (
                "STRUCTURAL_INVALIDATION_PRICE_BREACHED"
                if invalidated
                else "OPPOSITE_STRUCTURE_CONFIRMED"
            )
            return LifecycleEvent(SetupState.INVALIDATED, dead, reason, False)

        if confirmed:
            done = setup
            self._armed = None
            self._last_terminal_setup_id = done.setup_id
            return LifecycleEvent(
                SetupState.CONFIRMED,
                done,
                "LATER_CLOSED_BAR_STRUCTURE_CONFIRMED",
                True,
            )

        return LifecycleEvent(SetupState.ARMED, setup, "WAITING_FOR_STRUCTURE_CONFIRMATION", False)
