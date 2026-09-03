from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite


@dataclass(frozen=True)
class PropFirmRules:
    starting_balance: float
    maximum_loss: float
    drawdown_type: str
    maximum_contracts: int
    maximum_risk_per_trade: float
    daily_loss_limit: float | None = None
    consistency_limit_pct: float | None = None


@dataclass(frozen=True)
class AccountState:
    current_equity: float
    current_drawdown_floor: float
    current_daily_pnl: float | None = None
    consistency_pass: bool | None = None


@dataclass(frozen=True)
class RiskDecision:
    passed: bool
    contracts: int
    risk_per_contract: float
    total_risk: float
    remaining_loss_allowance: float
    remaining_after_stop: float
    reason: str


def _positive_finite(value: float, name: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name}_MUST_BE_POSITIVE_FINITE")


def _validate_rules(rules: PropFirmRules) -> None:
    _positive_finite(rules.starting_balance, "STARTING_BALANCE")
    _positive_finite(rules.maximum_loss, "MAXIMUM_LOSS")
    _positive_finite(rules.maximum_risk_per_trade, "MAXIMUM_RISK_PER_TRADE")
    if rules.maximum_contracts < 1:
        raise ValueError("MAXIMUM_CONTRACTS_MUST_BE_POSITIVE")
    if not rules.drawdown_type.strip():
        raise ValueError("DRAWDOWN_TYPE_REQUIRED")
    if rules.daily_loss_limit is not None:
        _positive_finite(rules.daily_loss_limit, "DAILY_LOSS_LIMIT")
    if rules.consistency_limit_pct is not None:
        if not isfinite(rules.consistency_limit_pct) or not 0 < rules.consistency_limit_pct <= 100:
            raise ValueError("CONSISTENCY_LIMIT_PCT_OUT_OF_RANGE")


def _validate_state(state: AccountState) -> None:
    if not isfinite(state.current_equity) or not isfinite(state.current_drawdown_floor):
        raise ValueError("ACCOUNT_STATE_MUST_BE_FINITE")
    if state.current_daily_pnl is not None and not isfinite(state.current_daily_pnl):
        raise ValueError("CURRENT_DAILY_PNL_MUST_BE_FINITE")


def assess_prop_risk(
    *,
    risk_per_contract: float,
    rules: PropFirmRules,
    state: AccountState,
) -> RiskDecision:
    """Size a trade without changing its structural stop.

    The caller supplies the current drawdown floor rather than asking this
    module to guess a firm's EOD/trailing calculation. If an active rule cannot
    be evaluated from supplied account state, the function fails closed.
    """
    _validate_rules(rules)
    _validate_state(state)
    _positive_finite(risk_per_contract, "RISK_PER_CONTRACT")

    remaining = state.current_equity - state.current_drawdown_floor
    if remaining <= 0:
        return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "DRAWDOWN_FLOOR_REACHED")

    # Consistency mechanics vary materially by firm. Do not invent the formula.
    # If the rule is active, an upstream verified calculation must explicitly pass.
    if rules.consistency_limit_pct is not None:
        if state.consistency_pass is None:
            return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "CONSISTENCY_STATE_UNVERIFIED")
        if not state.consistency_pass:
            return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "CONSISTENCY_RULE_BLOCKED")

    if rules.daily_loss_limit is not None and state.current_daily_pnl is None:
        return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "DAILY_PNL_UNAVAILABLE")

    # A worst-case stop must leave equity strictly above the drawdown floor.
    floor_budget = max(0.0, remaining)
    dollar_budget = min(rules.maximum_risk_per_trade, floor_budget)
    contracts = min(rules.maximum_contracts, floor(dollar_budget / risk_per_contract))

    if contracts < 1:
        return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "NO_CONTRACT_FITS_RISK_BUDGET")

    # Reduce size if touching the drawdown floor exactly; fail closed rather than
    # relying on whether a particular firm treats equality as a breach.
    while contracts > 0 and risk_per_contract * contracts >= remaining:
        contracts -= 1
    if contracts < 1:
        return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "STOP_WOULD_REACH_DRAWDOWN_FLOOR")

    if rules.daily_loss_limit is not None:
        assert state.current_daily_pnl is not None
        while contracts > 0 and state.current_daily_pnl - (risk_per_contract * contracts) <= -rules.daily_loss_limit:
            contracts -= 1
        if contracts < 1:
            return RiskDecision(False, 0, risk_per_contract, 0.0, remaining, remaining, "DAILY_LOSS_LIMIT_WOULD_BE_REACHED")

    total_risk = risk_per_contract * contracts
    remaining_after = remaining - total_risk
    return RiskDecision(
        passed=True,
        contracts=contracts,
        risk_per_contract=risk_per_contract,
        total_risk=total_risk,
        remaining_loss_allowance=remaining,
        remaining_after_stop=remaining_after,
        reason="PROP_RISK_PASS",
    )
