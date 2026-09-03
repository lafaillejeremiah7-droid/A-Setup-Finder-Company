from __future__ import annotations

"""Faithful Python port of the 'Buy Sell Signal' Pine indicator, run as a
backtest on free OHLCV data.

Pine logic reproduced exactly:
  emaFast = EMA(close, 5); emaSlow = EMA(close, 13); atr = ATR(14)
  bullTrend = emaFast > emaSlow ; bearTrend = emaFast < emaSlow
  trendChange = bullTrend != bullTrend[1]          (crossover this bar)
  buy  = bullTrend and trendChange and (close>open if confirmCandle)
  sell = bearTrend and trendChange and (close<open if confirmCandle)
  entry = close
  LONG:  stop = low  - ATR*0.5 ; tp = entry + risk*RR   (risk = entry-stop)
  SHORT: stop = high + ATR*0.5 ; tp = entry - risk*RR   (risk = stop-entry)
  opposite signal invalidates/closes the open position (counts as exit at close)
  one position at a time.

Exit accounting (bar-by-bar, conservative same-bar = STOP first):
  LONG  win if high>=tp before low<=stop ; SHORT win if low<=tp before high>=stop.
  If an opposite signal fires while in position, close at that bar's close
  (realised R = (exitClose-entry)/risk for long, inverse for short).
"""

import argparse
from dataclasses import dataclass

from src.market.normalize_tradovate_bar import MarketBar


def _ema(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (length + 1)
    ema = values[0]
    out[0] = ema
    for i in range(1, len(values)):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _atr(bars: list[MarketBar], length: int) -> list[float | None]:
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    out: list[float | None] = [None] * len(bars)
    if len(trs) < length:
        return out
    prev = sum(trs[:length]) / length
    out[length - 1] = prev
    for i in range(length, len(trs)):
        prev = (prev * (length - 1) + trs[i]) / length
        out[i] = prev
    return out


@dataclass
class Trade:
    direction: str
    entry: float
    stop: float
    tp: float
    outcome: str      # TARGET / STOP / INVALIDATED
    r: float


def backtest_ema(
    bars: list[MarketBar],
    *,
    fast: int = 5,
    slow: int = 13,
    atr_len: int = 14,
    atr_mult_sl: float = 0.5,
    rr: float = 3.0,
    confirm_candle: bool = True,
    cost_r: float = 0.0,          # optional per-trade friction in R (e.g. 0.05)
    max_per_day: int | None = None,  # cap NEW entries per UTC day (None = unlimited)
) -> dict:
    closes = [b.close for b in bars]
    ema_f = _ema(closes, fast)
    ema_s = _ema(closes, slow)
    atr = _atr(bars, atr_len)

    trades: list[Trade] = []
    in_pos = False
    ptype = ""
    entry = stop = tp = 0.0
    day_count: dict = {}
    cur_day = None

    def _resolve_forward(direction: str, e: float, s: float, t: float, start_i: int) -> tuple[str, float, int]:
        risk = abs(e - s)
        for j in range(start_i, len(bars)):
            b = bars[j]
            if direction == "LONG":
                hit_stop = b.low <= s
                hit_tp = b.high >= t
            else:
                hit_stop = b.high >= s
                hit_tp = b.low <= t
            if hit_stop and hit_tp:
                return "STOP", -1.0, j
            if hit_stop:
                return "STOP", -1.0, j
            if hit_tp:
                return "TARGET", rr, j
        return "OPEN", 0.0, len(bars) - 1

    prev_bull = None
    for i in range(len(bars)):
        if ema_f[i] is None or ema_s[i] is None or atr[i] is None:
            continue
        bull = ema_f[i] > ema_s[i]
        bear = ema_f[i] < ema_s[i]
        trend_change = (prev_bull is not None) and (bull != prev_bull)
        prev_bull = bull

        b = bars[i]
        day = b.timestamp.date()
        if day != cur_day:
            cur_day = day
            day_count.setdefault(day, 0)

        def _cap_ok() -> bool:
            return max_per_day is None or day_count.get(day, 0) < max_per_day

        buy = bull and trend_change and (b.close > b.open if confirm_candle else True)
        sell = bear and trend_change and (b.close < b.open if confirm_candle else True)

        # invalidation: opposite signal closes an open position at this close
        if in_pos and ((ptype == "LONG" and sell) or (ptype == "SHORT" and buy)):
            risk = abs(entry - stop)
            realised = ((b.close - entry) if ptype == "LONG" else (entry - b.close)) / risk if risk > 0 else 0.0
            trades.append(Trade(ptype, entry, stop, tp, "INVALIDATED", realised - cost_r))
            in_pos = False
            ptype = ""

        # A new entry consumes a slot from the daily budget.
        if buy and not (in_pos and ptype == "LONG") and _cap_ok():
            day_count[day] = day_count.get(day, 0) + 1
            entry = b.close
            stop = b.low - atr[i] * atr_mult_sl
            risk = entry - stop
            if risk <= 0:
                continue
            tp = entry + risk * rr
            outcome, r, _ = _resolve_forward("LONG", entry, stop, tp, i + 1)
            if outcome != "OPEN":
                trades.append(Trade("LONG", entry, stop, tp, outcome, r - cost_r))
            in_pos, ptype = True, "LONG"
        elif sell and not (in_pos and ptype == "SHORT") and _cap_ok():
            day_count[day] = day_count.get(day, 0) + 1
            entry = b.close
            stop = b.high + atr[i] * atr_mult_sl
            risk = stop - entry
            if risk <= 0:
                continue
            tp = entry - risk * rr
            outcome, r, _ = _resolve_forward("SHORT", entry, stop, tp, i + 1)
            if outcome != "OPEN":
                trades.append(Trade("SHORT", entry, stop, tp, outcome, r - cost_r))
            in_pos, ptype = True, "SHORT"

    return _summarize(trades)


def _summarize(trades: list[Trade]) -> dict:
    resolved = [t for t in trades if t.outcome in ("TARGET", "STOP", "INVALIDATED")]
    wins = [t for t in resolved if t.r > 0]
    losses = [t for t in resolved if t.r < 0]
    total_r = sum(t.r for t in resolved)
    gw = sum(t.r for t in wins)
    gl = abs(sum(t.r for t in losses))

    def side(name):
        s = [t for t in resolved if t.direction == name]
        w = [t for t in s if t.r > 0]
        return {"n": len(s), "win_rate": round(len(w) / len(s), 3) if s else None,
                "total_r": round(sum(t.r for t in s), 2)}

    return {
        "trades": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "total_R": round(total_r, 2),
        "expectancy_R": round(total_r / len(resolved), 3) if resolved else None,
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "target_hits": sum(1 for t in resolved if t.outcome == "TARGET"),
        "stop_hits": sum(1 for t in resolved if t.outcome == "STOP"),
        "invalidated": sum(1 for t in resolved if t.outcome == "INVALIDATED"),
        "by_direction": {"LONG": side("LONG"), "SHORT": side("SHORT")},
    }


def _report(name, interval, rep, cost_r):
    print("=" * 56)
    print(f" EMA 5/13 CROSS BACKTEST — {name} {interval}"
          + (f"  (cost {cost_r}R/trade)" if cost_r else ""))
    print("=" * 56)
    print(f" trades          : {rep['trades']}  (TP {rep['target_hits']} / SL {rep['stop_hits']} / INV {rep['invalidated']})")
    print(f" win rate        : {rep['win_rate']}")
    print(f" total R         : {rep['total_R']}")
    print(f" expectancy R    : {rep['expectancy_R']}")
    print(f" profit factor   : {rep['profit_factor']}")
    for d, s in rep["by_direction"].items():
        print(f"   {d:5} n={s['n']:<4} win%={s['win_rate']}  R={s['total_r']}")
    print("=" * 56)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Backtest the EMA 5/13 Pine strategy")
    p.add_argument("--symbol", default="NQ=F")
    p.add_argument("--interval", default="5m")
    p.add_argument("--range", dest="range_", default="60d")
    p.add_argument("--source", choices=["yahoo", "binance"], default="yahoo")
    p.add_argument("--binance-bars", type=int, default=10000)
    p.add_argument("--rr", type=float, default=3.0)
    p.add_argument("--cost-r", type=float, default=0.0)
    p.add_argument("--max-per-day", type=int, default=None)
    p.add_argument("--no-confirm", action="store_true")
    args = p.parse_args(argv)

    if args.source == "binance":
        from src.observer.data_binance import fetch_binance_bars
        bars = fetch_binance_bars(args.symbol, args.interval, args.binance_bars)
    else:
        from src.observer.data_yahoo import fetch_yahoo_bars
        bars = fetch_yahoo_bars(args.symbol, args.interval, args.range_)

    rep = backtest_ema(bars, rr=args.rr, confirm_candle=not args.no_confirm,
                       cost_r=args.cost_r, max_per_day=args.max_per_day)
    _report(args.symbol, args.interval, rep, args.cost_r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
