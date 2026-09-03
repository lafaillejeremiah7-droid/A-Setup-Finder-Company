#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.run_fast_core_backtest import load_mgc
from src.backtest.engine import BacktestConfig, run_backtest
from src.market.absorption import AbsorptionSide, AbsorptionSignal, BubbleColor
from src.market.market_structure import (
    Pivot,
    PivotType,
    StructureConfig,
    bearish_bos,
    build_trendline,
    bullish_bos,
    cluster_price_zones,
    detect_confirmed_pivots,
    pivots_known_by,
    trendline_touch,
    wilder_atr,
    zone_touched,
)
from src.market.trade_plan import TradeDirection, TradePlanConfig, build_trade_plan
from src.risk.prop_risk import AccountState, PropFirmRules, assess_prop_risk
from src.signal.composer import SignalAction, SignalResult
from src.signal.setup_lifecycle import LifecycleConfig, SetupLifecycle, SetupObservation, SetupState


def pivot_active(pivot: Pivot, bars, atr_values, through_index: int, break_buffer_atr: float) -> bool:
    start = max(pivot.confirmed_at_index + 1, 0)
    for j in range(start, through_index + 1):
        atr = atr_values[j]
        if atr is None or atr <= 0:
            continue
        if pivot.kind is PivotType.HIGH and bars[j].close > pivot.price + break_buffer_atr * atr:
            return False
        if pivot.kind is PivotType.LOW and bars[j].close < pivot.price - break_buffer_atr * atr:
            return False
    return True


def active_pivots(known, bars, atr_values, through_index: int, cfg: StructureConfig):
    # A structural S/R pivot stops being eligible after a confirmed close breaks it.
    # Exclude the current bar from prior validity so a touch can be evaluated before
    # the current close decides whether the level survives.
    prior = through_index - 1
    return [p for p in known if prior < p.confirmed_at_index or pivot_active(p, bars, atr_values, prior, cfg.sr_break_buffer_atr)]


def latest_unbroken_trendline(pivots, kind, bars, atr_values, current_index: int, cfg: StructureConfig):
    xs = [p for p in pivots if p.kind is kind]
    for b in range(len(xs) - 1, 0, -1):
        for a in range(b - 1, -1, -1):
            line = build_trendline(xs[a], xs[b])
            if line is None:
                continue
            broken = False
            for j in range(line.second.confirmed_at_index + 1, current_index):
                atr = atr_values[j]
                if atr is None or atr <= 0:
                    continue
                value = line.value_at(j)
                close = bars[j].close
                if kind is PivotType.LOW and close < value - cfg.trendline_break_atr * atr:
                    broken = True
                    break
                if kind is PivotType.HIGH and close > value + cfg.trendline_break_atr * atr:
                    broken = True
                    break
            if not broken:
                return line
    return None


def nearest_invalidation(active, entry: float, direction: TradeDirection):
    if direction is TradeDirection.LONG:
        lows = [p.price for p in active if p.kind is PivotType.LOW and p.price < entry]
        return max(lows) if lows else None
    highs = [p.price for p in active if p.kind is PivotType.HIGH and p.price > entry]
    return min(highs) if highs else None


def target_candidates(active, atr, direction, cfg):
    if direction is TradeDirection.LONG:
        zones = cluster_price_zones(active, PivotType.HIGH, atr, cluster_atr=cfg.sr_cluster_atr, half_width_atr=cfg.sr_half_width_atr)
        return [p.price for p in active if p.kind is PivotType.HIGH] + [z.lower for z in zones]
    zones = cluster_price_zones(active, PivotType.LOW, atr, cluster_atr=cfg.sr_cluster_atr, half_width_atr=cfg.sr_half_width_atr)
    return [p.price for p in active if p.kind is PivotType.LOW] + [z.upper for z in zones]


def main() -> int:
    bars = load_mgc()
    scfg = StructureConfig()
    tcfg = TradePlanConfig()
    atr_values = wilder_atr(bars, scfg.atr_period)
    all_pivots = detect_confirmed_pivots(bars, scfg.pivot_width)
    lifecycle = SetupLifecycle(LifecycleConfig(max_confirmation_bars=6, require_absorption=False))
    unknown_abs = AbsorptionSignal(AbsorptionSide.UNKNOWN, BubbleColor.NONE, None, None, "PUBLIC_OHLCV")
    rules = PropFirmRules(25_000.0, 1_500.0, "EOD", 2, 300.0, None, None)
    state = AccountState(25_000.0, 23_500.0, None, None)

    def latest(xs, kind):
        items = [p for p in xs if p.kind is kind]
        return items[-1] if items else None

    def provider(context):
        i = context.index
        atr = atr_values[i]
        if atr is None or atr <= 0:
            return None
        known = pivots_known_by(all_pivots, i)
        active = active_pivots(known, bars, atr_values, i, scfg)
        hi = latest(active, PivotType.HIGH)
        lo = latest(active, PivotType.LOW)
        bar = bars[i]

        support = cluster_price_zones(active, PivotType.LOW, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        resistance = cluster_price_zones(active, PivotType.HIGH, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        bull_loc = any(zone_touched(bar, z) and bar.close >= z.lower - scfg.sr_break_buffer_atr * atr for z in support)
        bear_loc = any(zone_touched(bar, z) and bar.close <= z.upper + scfg.sr_break_buffer_atr * atr for z in resistance)

        bull_line = latest_unbroken_trendline(active, PivotType.LOW, bars, atr_values, i, scfg)
        bear_line = latest_unbroken_trendline(active, PivotType.HIGH, bars, atr_values, i, scfg)
        if bull_line is not None:
            bull_loc = bull_loc or trendline_touch(bar, i, bull_line, atr, scfg.trendline_touch_atr)
        if bear_line is not None:
            bear_loc = bear_loc or trendline_touch(bar, i, bear_line, atr, scfg.trendline_touch_atr)

        # BOS must use an active swing on the opposite side of price, not a stale
        # historical pivot that price already invalidated long ago.
        bull_conf = hi is not None and hi.price < bar.close and bullish_bos(bar, hi, atr, scfg.bos_buffer_atr)
        bear_conf = lo is not None and lo.price > bar.close and bearish_bos(bar, lo, atr, scfg.bos_buffer_atr)

        if lifecycle.armed is None:
            long_inv = nearest_invalidation(active, bar.close, TradeDirection.LONG)
            short_inv = nearest_invalidation(active, bar.close, TradeDirection.SHORT)
            if bull_loc and long_inv is None:
                bull_loc = False
            if bear_loc and short_inv is None:
                bear_loc = False
        else:
            long_inv = lifecycle.armed.invalidation_price if lifecycle.armed.direction is TradeDirection.LONG else nearest_invalidation(active, bar.close, TradeDirection.LONG)
            short_inv = lifecycle.armed.invalidation_price if lifecycle.armed.direction is TradeDirection.SHORT else nearest_invalidation(active, bar.close, TradeDirection.SHORT)

        obs = SetupObservation(
            index=i,
            timestamp=bar.timestamp,
            bullish_location=bull_loc,
            bearish_location=bear_loc,
            absorption=unknown_abs,
            bullish_confirmation=bull_conf,
            bearish_confirmation=bear_conf,
            bullish_invalidation_price=long_inv,
            bearish_invalidation_price=short_inv,
            close=bar.close,
        )
        event = lifecycle.update(obs)
        if event.state is not SetupState.CONFIRMED or event.setup is None:
            return None

        direction = event.setup.direction
        plan = build_trade_plan(
            direction=direction,
            confirmation_close=bar.close,
            invalidation_price=event.setup.invalidation_price,
            atr=atr,
            target_candidates=target_candidates(active, atr, direction, scfg),
            config=tcfg,
        )
        if not plan.valid or not plan.rr_pass or plan.risk_per_contract is None or plan.stop is None or plan.target is None or plan.rr is None:
            return SignalResult(bar.timestamp, "MGC", SignalAction.NO_TRADE, None, None, None, 0, None, None, "IGNORED", (f"TRADE_PLAN_FAIL:{plan.reason}",))
        risk = assess_prop_risk(risk_per_contract=plan.risk_per_contract, rules=rules, state=state)
        if not risk.passed or risk.contracts < 1:
            return SignalResult(bar.timestamp, "MGC", SignalAction.NO_TRADE, None, None, None, 0, None, None, "IGNORED", (f"RISK_FAIL:{risk.reason}",))
        action = SignalAction.LONG if direction is TradeDirection.LONG else SignalAction.SHORT
        return SignalResult(bar.timestamp, "MGC", action, plan.entry, plan.stop, plan.target, risk.contracts, risk.total_risk, plan.rr, "NOT_REQUIRED", ("CORE_SIGNAL_GATES_PASS",))

    result = run_backtest(
        mgc_bars=bars,
        dxy_bars=[],
        signal_provider=provider,
        config=BacktestConfig(point_value=10.0, min_tick=0.1, entry_slippage_ticks=1.0, exit_slippage_ticks=1.0, round_trip_cost_per_contract=2.0),
    )

    out = Path("fast_core_fixed_artifact")
    out.mkdir(exist_ok=True)
    fields = ["signal_timestamp","entry_timestamp","exit_timestamp","action","contracts","intended_entry","filled_entry","stop_loss","take_profit","filled_exit","exit_reason","bars_held","gross_pnl","costs","net_pnl","initial_risk","r_multiple","bar_level_mae","bar_level_mfe"]
    with (out / "trades_core_fixed.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for t in result.trades:
            w.writerow({"signal_timestamp":t.signal_timestamp.isoformat(),"entry_timestamp":t.entry_timestamp.isoformat(),"exit_timestamp":t.exit_timestamp.isoformat(),"action":t.action.value,"contracts":t.contracts,"intended_entry":t.intended_entry,"filled_entry":t.filled_entry,"stop_loss":t.stop,"take_profit":t.target,"filled_exit":t.filled_exit,"exit_reason":t.exit_reason.value,"bars_held":t.bars_held,"gross_pnl":t.gross_pnl,"costs":t.costs,"net_pnl":t.net_pnl,"initial_risk":t.initial_risk,"r_multiple":t.r_multiple,"bar_level_mae":t.bar_level_mae,"bar_level_mfe":t.bar_level_mfe})

    summary = {
        "label":"FAST_CORE_ONLY_FIXED_EXPLORATORY",
        "fixes":[
            "Broken historical pivots are removed from active S/R and TP candidates.",
            "Trendlines with an intervening structural break are rejected.",
            "Stops use the nearest active pivot on the correct side of entry instead of the latest pivot by time.",
            "BOS references only active structural pivots."
        ],
        "mgc_first":bars[0].timestamp.isoformat(),"mgc_last":bars[-1].timestamp.isoformat(),"mgc_bars":len(bars),
        "backtest":result.summary.__dict__,
        "provider_calls":result.provider_calls,
        "limitations":["Public MGC root-symbol OHLCV only","No individual contract identity","No executed-side volume","DXY and absorption intentionally disabled for core bug isolation","Exploratory, not production-grade validation"]
    }
    (out / "summary_core_fixed.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
