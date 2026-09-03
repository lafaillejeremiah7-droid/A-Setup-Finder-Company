#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
from datetime import timezone
from pathlib import Path

import pandas as pd
import requests

from src.backtest.engine import BacktestConfig, run_backtest
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
from src.market.normalize_tradovate_bar import MarketBar
from src.market.trade_plan import TradeDirection, TradePlanConfig, build_trade_plan
from src.risk.prop_risk import AccountState, PropFirmRules, assess_prop_risk
from src.signal.composer import SignalAction, SignalResult
from src.signal.setup_lifecycle import LifecycleConfig, SetupLifecycle, SetupObservation, SetupState

MGC_URL = "https://raw.githubusercontent.com/axb0306/cme-futures-ohlc/main/MGC/MGC_5min_20260120_20260415.csv"


def load_mgc() -> list[MarketBar]:
    r = requests.get(MGC_URL, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime", keep="last")
    return [MarketBar(
        timestamp=x.datetime.to_pydatetime(), open=float(x.open), high=float(x.high),
        low=float(x.low), close=float(x.close), volume=float(x.volume)
    ) for x in df.itertuples(index=False)]


def main() -> int:
    bars = load_mgc()
    scfg = StructureConfig()
    tcfg = TradePlanConfig()
    atr_values = wilder_atr(bars, scfg.atr_period)
    all_pivots = detect_confirmed_pivots(bars, scfg.pivot_width)
    lifecycle = SetupLifecycle(LifecycleConfig(max_confirmation_bars=6, require_absorption=False))
    unknown_absorption = AbsorptionSignal(AbsorptionSide.UNKNOWN, BubbleColor.NONE, None, None, "PUBLIC_OHLCV_NO_SIDE_VOLUME")

    rules = PropFirmRules(
        starting_balance=25_000.0,
        maximum_loss=1_500.0,
        drawdown_type="EOD",
        maximum_contracts=2,
        maximum_risk_per_trade=300.0,
        daily_loss_limit=None,
        consistency_limit_pct=None,
    )
    state = AccountState(25_000.0, 23_500.0, None, None)

    def latest(known, kind):
        xs = [p for p in known if p.kind is kind]
        return xs[-1] if xs else None

    def targets(known, atr, direction):
        if direction is TradeDirection.LONG:
            zones = cluster_price_zones(known, PivotType.HIGH, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
            return [p.price for p in known if p.kind is PivotType.HIGH] + [z.lower for z in zones]
        zones = cluster_price_zones(known, PivotType.LOW, atr, cluster_atr=scfg.sr_cluster_atr, half_width_atr=scfg.sr_half_width_atr)
        return [p.price for p in known if p.kind is PivotType.LOW] + [z.upper for z in zones]

    def provider(context):
        i = context.index
        atr = atr_values[i]
        if atr is None or atr <= 0:
            return None
        known = pivots_known_by(all_pivots, i)
        hi = latest(known, PivotType.HIGH)
        lo = latest(known, PivotType.LOW)
        bar = bars[i]

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

        bull_conf = hi is not None and bullish_bos(bar, hi, atr, scfg.bos_buffer_atr)
        bear_conf = lo is not None and bearish_bos(bar, lo, atr, scfg.bos_buffer_atr)

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
            absorption=unknown_absorption,
            bullish_confirmation=bull_conf,
            bearish_confirmation=bear_conf,
            bullish_invalidation_price=lo.price if lo else None,
            bearish_invalidation_price=hi.price if hi else None,
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
            target_candidates=targets(known, atr, direction),
            config=tcfg,
        )
        if not plan.valid or not plan.rr_pass or plan.risk_per_contract is None or plan.stop is None or plan.target is None or plan.rr is None:
            return SignalResult(bar.timestamp, "MGC", SignalAction.NO_TRADE, None, None, None, 0, None, None, "IGNORED", (f"TRADE_PLAN_FAIL:{plan.reason}",))
        risk = assess_prop_risk(risk_per_contract=plan.risk_per_contract, rules=rules, state=state)
        if not risk.passed or risk.contracts < 1:
            return SignalResult(bar.timestamp, "MGC", SignalAction.NO_TRADE, None, None, None, 0, None, None, "IGNORED", (f"RISK_FAIL:{risk.reason}",))
        action = SignalAction.LONG if direction is TradeDirection.LONG else SignalAction.SHORT
        return SignalResult(
            timestamp=bar.timestamp,
            symbol="MGC",
            action=action,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            contracts=risk.contracts,
            dollar_risk=risk.total_risk,
            rr=plan.rr,
            dxy_state="NOT_REQUIRED",
            reasons=("CORE_SIGNAL_GATES_PASS",),
        )

    result = run_backtest(
        mgc_bars=bars,
        dxy_bars=[],
        signal_provider=provider,
        config=BacktestConfig(
            point_value=10.0,
            min_tick=0.1,
            entry_slippage_ticks=1.0,
            exit_slippage_ticks=1.0,
            round_trip_cost_per_contract=2.0,
        ),
    )

    out = Path("fast_core_artifact")
    out.mkdir(exist_ok=True)
    fields = ["signal_timestamp","entry_timestamp","exit_timestamp","action","contracts","intended_entry","filled_entry","stop_loss","take_profit","filled_exit","exit_reason","bars_held","gross_pnl","costs","net_pnl","initial_risk","r_multiple","bar_level_mae","bar_level_mfe"]
    with (out / "trades_core.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for t in result.trades:
            w.writerow({
                "signal_timestamp":t.signal_timestamp.isoformat(), "entry_timestamp":t.entry_timestamp.isoformat(), "exit_timestamp":t.exit_timestamp.isoformat(),
                "action":t.action.value, "contracts":t.contracts, "intended_entry":t.intended_entry, "filled_entry":t.filled_entry,
                "stop_loss":t.stop, "take_profit":t.target, "filled_exit":t.filled_exit, "exit_reason":t.exit_reason.value,
                "bars_held":t.bars_held, "gross_pnl":t.gross_pnl, "costs":t.costs, "net_pnl":t.net_pnl,
                "initial_risk":t.initial_risk, "r_multiple":t.r_multiple, "bar_level_mae":t.bar_level_mae, "bar_level_mfe":t.bar_level_mfe,
            })
    summary = {
        "label":"FAST_CORE_ONLY_EXPLORATORY",
        "core_definition":"location + later 5m BOS + structural SL/TP + minimum 2R + prop-risk pass; DXY and absorption not required",
        "mgc_first":bars[0].timestamp.isoformat(), "mgc_last":bars[-1].timestamp.isoformat(), "mgc_bars":len(bars),
        "backtest":result.summary.__dict__,
        "limitations":["Public MGC root-symbol OHLCV only", "No individual contract identity", "No executed-side volume", "Exploratory, not production-grade validation"],
    }
    (out / "summary_core.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
