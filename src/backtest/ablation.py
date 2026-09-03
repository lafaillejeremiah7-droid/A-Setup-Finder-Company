from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Sequence

from src.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from src.market.absorption import AbsorptionSide
from src.market.normalize_tradovate_bar import MarketBar
from src.signal.composer import SignalAction, SignalResult


class AbsorptionMode(str, Enum):
    """Research treatments for testing whether absorption adds value."""

    OFF = "OFF"
    REQUIRED = "REQUIRED"
    RANK_ONLY = "RANK_ONLY"


@dataclass(frozen=True)
class CandidateSignal:
    """A core setup plus absorption evidence available at the same timestamp.

    `core_signal` must be produced without making absorption a mandatory gate.
    The ablation layer then applies one of the three treatments to the exact
    same candidate so the comparison changes only the absorption policy.
    """

    core_signal: SignalResult
    absorption_side: AbsorptionSide
    absorption_score: float | None = None


CandidateProvider = Callable[[object], CandidateSignal | None]


@dataclass(frozen=True)
class AblationRun:
    mode: AbsorptionMode
    result: BacktestResult


@dataclass(frozen=True)
class AbsorptionAblationResult:
    runs: Mapping[AbsorptionMode, AblationRun]


def absorption_confirms(action: SignalAction, side: AbsorptionSide) -> bool:
    if action is SignalAction.LONG:
        return side is AbsorptionSide.BUYING
    if action is SignalAction.SHORT:
        return side is AbsorptionSide.SELLING
    return False


def apply_absorption_mode(
    candidate: CandidateSignal | None,
    mode: AbsorptionMode,
) -> SignalResult | None:
    """Transform one core candidate without changing its price/risk geometry.

    OFF: absorption has no veto power.
    REQUIRED: actionable signals survive only when absorption confirms direction.
    RANK_ONLY: execution remains identical to OFF; score is retained upstream for
    later stratification/ranking analysis rather than contaminating trade count.
    """
    if candidate is None:
        return None

    signal = candidate.core_signal
    if signal.action is SignalAction.NO_TRADE:
        return signal

    if mode is AbsorptionMode.REQUIRED and not absorption_confirms(
        signal.action, candidate.absorption_side
    ):
        return SignalResult(
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            action=SignalAction.NO_TRADE,
            entry=None,
            stop=None,
            target=None,
            contracts=0,
            dollar_risk=None,
            rr=None,
            dxy_state=signal.dxy_state,
            reasons=signal.reasons + ("ABLATION_ABSORPTION_REQUIRED_FAIL",),
        )

    return signal


def run_absorption_ablation(
    *,
    mgc_bars: Sequence[MarketBar],
    dxy_bars: Sequence[MarketBar],
    candidate_provider: Callable[[object], CandidateSignal | None],
    config: BacktestConfig = BacktestConfig(),
    modes: Sequence[AbsorptionMode] = (
        AbsorptionMode.OFF,
        AbsorptionMode.REQUIRED,
        AbsorptionMode.RANK_ONLY,
    ),
) -> AbsorptionAblationResult:
    """Run matched backtests that differ only in their absorption treatment.

    This intentionally does not optimize absorption thresholds. Its first job is
    falsification: determine whether requiring absorption improves OOS quality
    enough to justify the lost trade count. RANK_ONLY executes the same trades as
    OFF so later analysis can ask whether stronger absorption predicts outcomes.
    """
    runs: dict[AbsorptionMode, AblationRun] = {}

    for mode in modes:
        def provider(context: object, *, _mode: AbsorptionMode = mode) -> SignalResult | None:
            return apply_absorption_mode(candidate_provider(context), _mode)

        result = run_backtest(
            mgc_bars=mgc_bars,
            dxy_bars=dxy_bars,
            signal_provider=provider,
            config=config,
        )
        runs[mode] = AblationRun(mode=mode, result=result)

    return AbsorptionAblationResult(runs=runs)
