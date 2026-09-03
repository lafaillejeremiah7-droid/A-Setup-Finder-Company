from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from src.backtest.ablation import CandidateSignal
from src.backtest.engine import BacktestContext
from src.market.absorption import AbsorptionConfig, detect_absorption
from src.market.dxy import evaluate_dxy_state
from src.market.market_structure import (
    Pivot,
    PivotType,
    StructureConfig,
    bearish_bos,
    bullish_bos,
    cluster_price_zones,
    detect_confirmed_pivots,
    latest_valid_trendline,
    pivots_known_by,
    trendline_touch,
    wilder_atr,
    zone_touched,
)
from src.market.trade_plan import TradeDirection, TradePlanConfig, build_trade_plan
from src.risk.prop_risk import AccountState, PropFirmRules, assess_prop_risk
from src.signal.composer import SignalAction, SignalResult, compose_signal
from src.signal.setup_lifecycle import (
    LifecycleConfig,
    SetupLifecycle,
    SetupObservation,
    SetupState,
)


AccountStateProvider = Callable[[BacktestContext], AccountState]


@dataclass(frozen=True)
class GIBRCReplayConfig:
    structure: StructureConfig = StructureConfig()
    absorption: AbsorptionConfig = AbsorptionConfig()
    trade_plan: TradePlanConfig = TradePlanConfig()
    lifecycle: LifecycleConfig = LifecycleConfig()


class GIBRCReplayProvider:
    """Stateful historical signal provider using the same deterministic modules.

    This object consumes only BacktestContext closed-bar views. It discovers
    location from confirmed support/resistance zones or current valid trendlines,
    detects executed-volume absorption, waits for a later BOS confirmation via
    SetupLifecycle, evaluates synchronized DXY, builds structural Entry/SL/TP,
    sizes through the generic prop-risk engine, and finally calls compose_signal.

    It is intentionally conservative. Missing ATR, pivots, targets, DXY state,
    or verified risk state produces no actionable signal rather than guessed data.
    """

    def __init__(
        self,
        *,
        rules: PropFirmRules,
        account_state_provider: AccountStateProvider,
        config: GIBRCReplayConfig = GIBRCReplayConfig(),
    ) -> None:
        self.rules = rules
        self.account_state_provider = account_state_provider
        self.config = config
        self.lifecycle = SetupLifecycle(config.lifecycle)

    @staticmethod
    def _latest(pivots: Sequence[Pivot], kind: PivotType) -> Pivot | None:
        candidates = [pivot for pivot in pivots if pivot.kind is kind]
        return candidates[-1] if candidates else None

    def _location_state(
        self,
        *,
        bars: Sequence,
        index: int,
        known_pivots: Sequence[Pivot],
        atr: float,
    ) -> tuple[bool, bool]:
        bar = bars[index]
        cfg = self.config.structure

        support_zones = cluster_price_zones(
            known_pivots,
            PivotType.LOW,
            atr,
            cluster_atr=cfg.sr_cluster_atr,
            half_width_atr=cfg.sr_half_width_atr,
        )
        resistance_zones = cluster_price_zones(
            known_pivots,
            PivotType.HIGH,
            atr,
            cluster_atr=cfg.sr_cluster_atr,
            half_width_atr=cfg.sr_half_width_atr,
        )
        bullish_location = any(zone_touched(bar, zone) for zone in support_zones)
        bearish_location = any(zone_touched(bar, zone) for zone in resistance_zones)

        bullish_line = latest_valid_trendline(known_pivots, PivotType.LOW)
        bearish_line = latest_valid_trendline(known_pivots, PivotType.HIGH)
        if bullish_line is not None:
            bullish_location = bullish_location or trendline_touch(
                bar, index, bullish_line, atr, cfg.trendline_touch_atr
            )
        if bearish_line is not None:
            bearish_location = bearish_location or trendline_touch(
                bar, index, bearish_line, atr, cfg.trendline_touch_atr
            )
        return bullish_location, bearish_location

    def _target_candidates(
        self,
        *,
        known_pivots: Sequence[Pivot],
        atr: float,
        direction: TradeDirection,
    ) -> list[float]:
        cfg = self.config.structure
        if direction is TradeDirection.LONG:
            zones = cluster_price_zones(
                known_pivots,
                PivotType.HIGH,
                atr,
                cluster_atr=cfg.sr_cluster_atr,
                half_width_atr=cfg.sr_half_width_atr,
            )
            return [pivot.price for pivot in known_pivots if pivot.kind is PivotType.HIGH] + [
                zone.lower for zone in zones
            ]
        zones = cluster_price_zones(
            known_pivots,
            PivotType.LOW,
            atr,
            cluster_atr=cfg.sr_cluster_atr,
            half_width_atr=cfg.sr_half_width_atr,
        )
        return [pivot.price for pivot in known_pivots if pivot.kind is PivotType.LOW] + [
            zone.upper for zone in zones
        ]

    def candidate(self, context: BacktestContext) -> CandidateSignal | None:
        bars = context.mgc
        index = len(bars) - 1
        if index < 0:
            return None

        atr_values = wilder_atr(bars, self.config.structure.atr_period)
        atr = atr_values[index]
        if atr is None or atr <= 0:
            return None

        all_pivots = detect_confirmed_pivots(bars, self.config.structure.pivot_width)
        known = pivots_known_by(all_pivots, index)
        latest_high = self._latest(known, PivotType.HIGH)
        latest_low = self._latest(known, PivotType.LOW)

        bullish_location, bearish_location = self._location_state(
            bars=bars,
            index=index,
            known_pivots=known,
            atr=atr,
        )
        absorption = detect_absorption(bars, index, self.config.absorption)

        bar = bars[index]
        bullish_confirmation = (
            latest_high is not None
            and bullish_bos(
                bar,
                latest_high,
                atr,
                self.config.structure.bos_buffer_atr,
            )
        )
        bearish_confirmation = (
            latest_low is not None
            and bearish_bos(
                bar,
                latest_low,
                atr,
                self.config.structure.bos_buffer_atr,
            )
        )

        observation = SetupObservation(
            index=index,
            timestamp=context.timestamp,
            bullish_location=bullish_location,
            bearish_location=bearish_location,
            absorption=absorption,
            bullish_confirmation=bullish_confirmation,
            bearish_confirmation=bearish_confirmation,
            bullish_invalidation_price=latest_low.price if latest_low is not None else None,
            bearish_invalidation_price=latest_high.price if latest_high is not None else None,
            close=bar.close,
        )

        # A location without a structural invalidation anchor cannot be armed.
        if self.lifecycle.armed is None:
            if bullish_location and latest_low is None:
                bullish_location = False
            if bearish_location and latest_high is None:
                bearish_location = False
            observation = SetupObservation(
                index=observation.index,
                timestamp=observation.timestamp,
                bullish_location=bullish_location,
                bearish_location=bearish_location,
                absorption=observation.absorption,
                bullish_confirmation=observation.bullish_confirmation,
                bearish_confirmation=observation.bearish_confirmation,
                bullish_invalidation_price=observation.bullish_invalidation_price,
                bearish_invalidation_price=observation.bearish_invalidation_price,
                close=observation.close,
            )

        event = self.lifecycle.update(observation)
        if event.state is not SetupState.CONFIRMED or event.setup is None:
            return None

        direction = event.setup.direction
        targets = self._target_candidates(
            known_pivots=known,
            atr=atr,
            direction=direction,
        )
        plan = build_trade_plan(
            direction=direction,
            confirmation_close=bar.close,
            invalidation_price=event.setup.invalidation_price,
            atr=atr,
            target_candidates=targets,
            config=self.config.trade_plan,
        )

        if plan.risk_per_contract is None:
            return CandidateSignal(
                core_signal=SignalResult(
                    timestamp=context.timestamp,
                    symbol="MGC",
                    action=SignalAction.NO_TRADE,
                    entry=None,
                    stop=None,
                    target=None,
                    contracts=0,
                    dollar_risk=None,
                    rr=None,
                    dxy_state="NEUTRAL",
                    reasons=(f"TRADE_PLAN_INVALID:{plan.reason}",),
                ),
                absorption_side=event.setup.absorption.side,
                absorption_score=event.setup.absorption.aggression_z,
            )

        risk = assess_prop_risk(
            risk_per_contract=plan.risk_per_contract,
            rules=self.rules,
            state=self.account_state_provider(context),
        )
        dxy = evaluate_dxy_state(context.dxy, self.config.structure)
        result = compose_signal(
            timestamp=context.timestamp,
            symbol="MGC",
            direction=direction,
            location_pass=True,
            structure_confirmation_pass=True,
            absorption=event.setup.absorption,
            dxy=dxy,
            trade_plan=plan,
            risk=risk,
        )
        return CandidateSignal(
            core_signal=result,
            absorption_side=event.setup.absorption.side,
            absorption_score=event.setup.absorption.aggression_z,
        )

    def __call__(self, context: BacktestContext) -> SignalResult | None:
        candidate = self.candidate(context)
        return None if candidate is None else candidate.core_signal
