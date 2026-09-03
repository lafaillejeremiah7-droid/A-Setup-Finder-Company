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
    SRRole,
    StructureConfig,
    bearish_bos,
    bullish_bos,
    build_sr_levels,
    detect_confirmed_pivots,
    pivots_known_by,
    price_at_resistance,
    price_at_support,
    wilder_atr,
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
    require_dxy: bool = True


class GIBRCReplayProvider:
    """Stateful historical candidate provider using deterministic closed bars.

    Absorption evidence is collected with the setup but is not allowed to veto
    the core candidate here. OFF / REQUIRED / RANK_ONLY is applied once at the
    ablation boundary. DXY can also be disabled for matched core-vs-DXY research.
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

        sr_levels = build_sr_levels(
            known_pivots,
            bars[: index + 1],
            atr,
            break_buffer_atr=cfg.sr_break_buffer_atr,
        )
        bullish_location = price_at_support(bar, sr_levels, atr, cfg.sr_half_width_atr)
        bearish_location = price_at_resistance(bar, sr_levels, atr, cfg.sr_half_width_atr)
        return bullish_location, bearish_location

    def _target_candidates(
        self,
        *,
        bars: Sequence,
        index: int,
        known_pivots: Sequence[Pivot],
        atr: float,
        direction: TradeDirection,
    ) -> list[float]:
        cfg = self.config.structure
        sr_levels = build_sr_levels(
            known_pivots,
            bars[: index + 1],
            atr,
            break_buffer_atr=cfg.sr_break_buffer_atr,
        )
        if direction is TradeDirection.LONG:
            # Nearest active resistance levels above current price
            return [lvl.price for lvl in sr_levels if lvl.role is SRRole.RESISTANCE]
        else:
            # Nearest active support levels below current price
            return [lvl.price for lvl in sr_levels if lvl.role is SRRole.SUPPORT]

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
            bars=bars,
            index=index,
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
            require_absorption=False,
            require_dxy=self.config.require_dxy,
        )
        return CandidateSignal(
            core_signal=result,
            absorption_side=event.setup.absorption.side,
            absorption_score=event.setup.absorption.aggression_z,
        )

    def __call__(self, context: BacktestContext) -> SignalResult | None:
        candidate = self.candidate(context)
        return None if candidate is None else candidate.core_signal
