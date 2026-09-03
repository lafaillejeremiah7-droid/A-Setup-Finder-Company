from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isclose

from src.market.absorption import AbsorptionSide, AbsorptionSignal
from src.market.dxy import DxyState, dxy_supports_gold_long, dxy_supports_gold_short
from src.market.trade_plan import TradeDirection, TradePlan
from src.risk.prop_risk import RiskDecision


class SignalAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class SignalResult:
    timestamp: datetime
    symbol: str
    action: SignalAction
    entry: float | None
    stop: float | None
    target: float | None
    contracts: int
    dollar_risk: float | None
    rr: float | None
    dxy_state: str
    reasons: tuple[str, ...]


def compose_signal(
    *,
    timestamp: datetime,
    symbol: str,
    direction: TradeDirection,
    location_pass: bool,
    structure_confirmation_pass: bool,
    absorption: AbsorptionSignal,
    dxy: DxyState,
    trade_plan: TradePlan,
    risk: RiskDecision,
    require_absorption: bool = True,
) -> SignalResult:
    """Combine already-computed deterministic setup components.

    ``require_absorption`` defaults to True so direct/live callers retain the
    strict production behavior. Research candidate generation sets it False so
    OFF / REQUIRED / RANK_ONLY can be applied exactly once by the ablation layer.
    """
    if not symbol.strip():
        raise ValueError("SYMBOL_REQUIRED")

    reasons: list[str] = []

    if not location_pass:
        reasons.append("LOCATION_FAIL")
    if not structure_confirmation_pass:
        reasons.append("STRUCTURE_CONFIRMATION_FAIL")

    if direction is TradeDirection.LONG:
        if require_absorption and absorption.side is not AbsorptionSide.BUYING:
            reasons.append("SELL_SIDE_ABSORPTION_REQUIRED")
        if not dxy_supports_gold_long(dxy):
            reasons.append("DXY_NOT_BEARISH")
    else:
        if require_absorption and absorption.side is not AbsorptionSide.SELLING:
            reasons.append("BUY_SIDE_ABSORPTION_REQUIRED")
        if not dxy_supports_gold_short(dxy):
            reasons.append("DXY_NOT_BULLISH")

    if trade_plan.direction is not direction:
        reasons.append("TRADE_PLAN_DIRECTION_MISMATCH")
    if not trade_plan.valid:
        reasons.append(f"TRADE_PLAN_INVALID:{trade_plan.reason}")
    if not trade_plan.rr_pass:
        reasons.append("RR_FAIL")

    if not risk.passed:
        reasons.append(f"PROP_RISK_FAIL:{risk.reason}")
    if risk.contracts < 1:
        reasons.append("NO_APPROVED_CONTRACTS")

    if trade_plan.risk_per_contract is None:
        reasons.append("TRADE_PLAN_RISK_MISSING")
    elif not isclose(
        trade_plan.risk_per_contract,
        risk.risk_per_contract,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        reasons.append("RISK_PLAN_MISMATCH")

    if trade_plan.stop is None:
        reasons.append("STOP_MISSING")
    if trade_plan.target is None:
        reasons.append("TARGET_MISSING")
    if trade_plan.rr is None:
        reasons.append("RR_MISSING")

    if reasons:
        return SignalResult(
            timestamp=timestamp,
            symbol=symbol,
            action=SignalAction.NO_TRADE,
            entry=None,
            stop=None,
            target=None,
            contracts=0,
            dollar_risk=None,
            rr=None,
            dxy_state=dxy.state.value,
            reasons=tuple(reasons),
        )

    action = SignalAction.LONG if direction is TradeDirection.LONG else SignalAction.SHORT
    return SignalResult(
        timestamp=timestamp,
        symbol=symbol,
        action=action,
        entry=trade_plan.entry,
        stop=trade_plan.stop,
        target=trade_plan.target,
        contracts=risk.contracts,
        dollar_risk=risk.total_risk,
        rr=trade_plan.rr,
        dxy_state=dxy.state.value,
        reasons=("CORE_SIGNAL_GATES_PASS" if not require_absorption else "ALL_SIGNAL_GATES_PASS",),
    )
