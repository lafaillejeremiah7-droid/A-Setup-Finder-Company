#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.run_fast_core_backtest import load_mgc
from src.market.absorption import AbsorptionSide, AbsorptionSignal, BubbleColor
from src.market.market_structure import (
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
from src.signal.setup_lifecycle import LifecycleConfig, SetupLifecycle, SetupObservation, SetupState


def main() -> int:
    bars = load_mgc()
    scfg = StructureConfig()
    tcfg = TradePlanConfig()
    atr_values = wilder_atr(bars, scfg.atr_period)
    pivots = detect_confirmed_pivots(bars, scfg.pivot_width)
    lifecycle = SetupLifecycle(LifecycleConfig(max_confirmation_bars=6, require_absorption=False))
    unknown_abs = AbsorptionSignal(AbsorptionSide.UNKNOWN, BubbleColor.NONE, None, None, "PUBLIC_OHLCV")
    rules = PropFirmRules(25_000.0, 1_500.0, "EOD", 2, 300.0, None, None)
    state = AccountState(25_000.0, 23_500.0, None, None)

    counts = Counter()
    lifecycle_reasons = Counter()
    plan_reasons = Counter()
    risk_reasons = Counter()

    def latest(known, kind):
        xs = [p for p in known if p.kind is kind]
        return xs[-1] if xs else None

    def target_candidates(known, atr, direction):
        if direction is TradeDirection.LONG:
            zones = cluster_price_zones(known, PivotType.HIGH, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
            return [p.price for p in known if p.kind is PivotType.HIGH] + [z.lower for z in zones]
        zones = cluster_price_zones(known, PivotType.LOW, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        return [p.price for p in known if p.kind is PivotType.LOW] + [z.upper for z in zones]

    for i, bar in enumerate(bars):
        atr = atr_values[i]
        if atr is None or atr <= 0:
            counts["atr_unavailable"] += 1
            continue
        known = pivots_known_by(pivots, i)
        hi = latest(known, PivotType.HIGH)
        lo = latest(known, PivotType.LOW)

        support = cluster_price_zones(known, PivotType.LOW, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        resistance = cluster_price_zones(known, PivotType.HIGH, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        bull_loc = any(zone_touched(bar, z) for z in support)
        bear_loc = any(zone_touched(bar, z) for z in resistance)
        bull_line = latest_valid_trendline(known, PivotType.LOW)
        bear_line = latest_valid_trendline(known, PivotType.HIGH)
        if bull_line is not None:
            bull_loc = bull_loc or trendline_touch(bar, i, bull_line, atr, scfg.trendline_touch_atr)
        if bear_line is not None:
            bear_loc = bear_loc or trendline_touch(bar, i, bear_line, atr, scfg.trendline_touch_atr)

        if bull_loc:
            counts["bullish_location_bars"] += 1
        if bear_loc:
            counts["bearish_location_bars"] += 1
        if bull_loc and bear_loc:
            counts["ambiguous_location_bars"] += 1

        bull_conf = hi is not None and bullish_bos(bar, hi, atr, scfg.bos_buffer_atr)
        bear_conf = lo is not None and bearish_bos(bar, lo, atr, scfg.bos_buffer_atr)
        if bull_conf:
            counts["bullish_bos_bars"] += 1
        if bear_conf:
            counts["bearish_bos_bars"] += 1

        if lifecycle.armed is None:
            if bull_loc and lo is None:
                bull_loc = False
            if bear_loc and hi is None:
                bear_loc = False

        obs = SetupObservation(
            index=i,
            timestamp=bar.timestamp,
            bullish_location=bull_loc,
            bearish_location=bear_loc,
            absorption=unknown_abs,
            bullish_confirmation=bull_conf,
            bearish_confirmation=bear_conf,
            bullish_invalidation_price=lo.price if lo else None,
            bearish_invalidation_price=hi.price if hi else None,
            close=bar.close,
        )
        event = lifecycle.update(obs)
        lifecycle_reasons[event.reason] += 1
        counts[f"lifecycle_{event.state.value.lower()}"] += 1
        if event.state is not SetupState.CONFIRMED or event.setup is None:
            continue

        counts["confirmed_setups"] += 1
        direction = event.setup.direction
        plan = build_trade_plan(
            direction=direction,
            confirmation_close=bar.close,
            invalidation_price=event.setup.invalidation_price,
            atr=atr,
            target_candidates=target_candidates(known, atr, direction),
            config=tcfg,
        )
        plan_reasons[plan.reason] += 1
        if not plan.valid:
            counts["plan_invalid"] += 1
            continue
        counts["plan_valid"] += 1
        if not plan.rr_pass:
            counts["rr_failed"] += 1
            continue
        counts["rr_passed"] += 1
        if plan.risk_per_contract is None:
            counts["risk_missing"] += 1
            continue
        risk = assess_prop_risk(risk_per_contract=plan.risk_per_contract, rules=rules, state=state)
        risk_reasons[risk.reason] += 1
        if not risk.passed or risk.contracts < 1:
            counts["prop_risk_failed"] += 1
            continue
        counts["actionable_signals"] += 1

    report = {
        "bars": len(bars),
        "counts": dict(counts),
        "lifecycle_reasons": dict(lifecycle_reasons),
        "plan_reasons": dict(plan_reasons),
        "risk_reasons": dict(risk_reasons),
    }
    out = Path("fast_core_artifact")
    out.mkdir(exist_ok=True)
    (out / "diagnostics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
