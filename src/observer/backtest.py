from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date

from src.market.market_structure import StructureConfig
from src.observer.aggregate import aggregate
from src.observer.data_yahoo import fetch_yahoo_bars
from src.observer.engine import Direction, Grade, Setup, evaluate
from src.observer.structure import detect_structure


@dataclass
class TradeResult:
    setup: Setup
    outcome: str        # "TARGET", "STOP", "OPEN_END"
    r_multiple: float


def _simulate_forward(setup: Setup, future_bars) -> TradeResult:
    """Resolve a setup by walking forward 5m bars until stop or target is hit.

    Conservative same-bar rule: if a bar spans both stop and target, count it as
    a STOP (never assume the favorable fill). Mirrors the repo backtest engine's
    STOP_FIRST policy.
    """
    entry, stop, target = setup.entry, setup.stop, setup.target
    risk = abs(entry - stop)
    for bar in future_bars:
        if setup.direction is Direction.LONG:
            hit_stop = bar.low <= stop
            hit_tgt = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_tgt = bar.low <= target
        if hit_stop and hit_tgt:
            return TradeResult(setup, "STOP", -1.0)
        if hit_stop:
            return TradeResult(setup, "STOP", -1.0)
        if hit_tgt:
            r = abs(target - entry) / risk if risk > 0 else 0.0
            return TradeResult(setup, "TARGET", r)
    # never resolved within the data
    return TradeResult(setup, "OPEN_END", 0.0)


def run_backtest(
    *,
    source: str = "yahoo",
    symbol: str = "GC=F",
    interval: str = "5m",
    range_: str = "60d",
    binance_bars: int = 8000,
    warmup: int = 800,
    max_per_day: int = 2,
    forward_bars: int = 288,  # ~1 trading day of 5m to resolve a day-trade
) -> dict:
    if source == "binance":
        from src.observer.data_binance import fetch_binance_bars

        bars = fetch_binance_bars(symbol, interval, binance_bars)
    else:
        bars = fetch_yahoo_bars(symbol, interval, range_)
    if len(bars) < warmup + 50:
        raise RuntimeError(f"NOT_ENOUGH_DATA: got {len(bars)} bars")

    cfg = StructureConfig()
    results: list[TradeResult] = []
    seen_keys: set = set()
    day_counts: dict[date, int] = {}

    # Precompute the full 1H/4H aggregations once, then slice by "how many HTF
    # bars are fully closed at 5m bar i". Structure is only recomputed when a
    # new HTF bar closes (it can't change mid-bar), making this O(n) not O(n^2).
    full_1h = aggregate(bars, 60)
    full_4h = aggregate(bars, 240)

    def closed_count(htf_bars, ts) -> int:
        # number of HTF bars whose bucket has fully closed by timestamp ts
        step = (htf_bars[1].timestamp - htf_bars[0].timestamp) if len(htf_bars) > 1 else None
        if step is None:
            return 0
        n = 0
        for b in htf_bars:
            if b.timestamp + step <= ts:
                n += 1
            else:
                break
        return n

    cached_structure = None
    cached_sig = None  # (n4, n1) signature
    # keep a bounded 5m window for pivots/absorption on the execution side
    win_len = 1500

    for i in range(warmup, len(bars)):
        bar = bars[i]
        ts = bar.timestamp
        n1 = closed_count(full_1h, ts)
        n4 = closed_count(full_4h, ts)
        if n4 < cfg.atr_period + cfg.pivot_width + 2:
            continue
        b1c = full_1h[:n1]
        b4c = full_4h[:n4]

        if (n4, n1) != cached_sig:
            cached_structure = detect_structure(bars_4h=b4c, bars_1h=b1c, config=cfg)
            cached_sig = (n4, n1)
        structure = cached_structure

        window = bars[max(0, i - win_len) : i + 1]
        setup = evaluate(bars_5m=window, structure=structure, htf_bars_4h=b4c, htf_bars_1h=b1c)
        if setup is None:
            continue

        d = setup.timestamp.date()
        key = (d,) + setup.key()
        if key in seen_keys:
            continue
        if day_counts.get(d, 0) >= max_per_day:
            continue
        seen_keys.add(key)
        day_counts[d] = day_counts.get(d, 0) + 1

        future = bars[i + 1 : i + 1 + forward_bars]
        results.append(_simulate_forward(setup, future))

    return _summarize(results, symbol, interval, range_, len(bars))


def _summarize(results, symbol, interval, range_, n_bars) -> dict:
    resolved = [r for r in results if r.outcome in ("TARGET", "STOP")]
    wins = [r for r in resolved if r.outcome == "TARGET"]
    losses = [r for r in resolved if r.outcome == "STOP"]

    total_r = sum(r.r_multiple for r in resolved)
    gross_win = sum(r.r_multiple for r in wins)
    gross_loss = abs(sum(r.r_multiple for r in losses))

    by_grade: dict[str, dict] = {}
    for g in (Grade.A_MINUS, Grade.A, Grade.A_PLUS):
        gr = [r for r in resolved if r.setup.grade is g]
        gw = [r for r in gr if r.outcome == "TARGET"]
        by_grade[g.value] = {
            "n": len(gr),
            "wins": len(gw),
            "win_rate": (len(gw) / len(gr)) if gr else None,
            "total_r": round(sum(r.r_multiple for r in gr), 2),
            "expectancy_r": round(sum(r.r_multiple for r in gr) / len(gr), 3) if gr else None,
        }

    by_dir: dict[str, dict] = {}
    for dname in ("LONG", "SHORT"):
        dr = [r for r in resolved if r.setup.direction.value == dname]
        dw = [r for r in dr if r.outcome == "TARGET"]
        by_dir[dname] = {
            "n": len(dr),
            "win_rate": (len(dw) / len(dr)) if dr else None,
            "total_r": round(sum(r.r_multiple for r in dr), 2),
        }

    return {
        "symbol": symbol,
        "interval": interval,
        "range": range_,
        "bars": n_bars,
        "signals": len(results),
        "resolved": len(resolved),
        "unresolved_open": len(results) - len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "total_R": round(total_r, 2),
        "expectancy_R_per_trade": round(total_r / len(resolved), 3) if resolved else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "by_grade": by_grade,
        "by_direction": by_dir,
    }


def _print_report(rep: dict) -> None:
    print("=" * 56)
    print(f" GIBRC BACKTEST — {rep['symbol']} {rep['interval']} / {rep['range']}")
    print("=" * 56)
    print(f" bars scanned      : {rep['bars']}")
    print(f" signals           : {rep['signals']}")
    print(f" resolved trades   : {rep['resolved']}  (open-at-end: {rep['unresolved_open']})")
    print(f" wins / losses     : {rep['wins']} / {rep['losses']}")
    print(f" win rate          : {rep['win_rate']}")
    print(f" total R           : {rep['total_R']}")
    print(f" expectancy R/trade: {rep['expectancy_R_per_trade']}")
    print(f" profit factor     : {rep['profit_factor']}")
    print("-" * 56)
    print(" by grade:")
    for g, s in rep["by_grade"].items():
        print(f"   {g:3} n={s['n']:<4} win%={s['win_rate']}  R={s['total_r']}  E={s['expectancy_r']}")
    print(" by direction:")
    for d, s in rep["by_direction"].items():
        print(f"   {d:5} n={s['n']:<4} win%={s['win_rate']}  R={s['total_r']}")
    print("=" * 56)
    print(" NOTE: free OHLCV only -> absorption UNKNOWN, grades cap at A.")
    print(" Same-bar stop/target resolved conservatively as STOP.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Free GIBRC backtest (Yahoo futures OHLCV or Binance crypto w/ order flow)")
    p.add_argument("--source", choices=["yahoo", "binance"], default="yahoo")
    p.add_argument("--symbol", default="GC=F", help="yahoo: GC=F/NQ=F | binance: BTCUSDT")
    p.add_argument("--interval", default="5m")
    p.add_argument("--range", dest="range_", default="60d", help="yahoo range, e.g. 60d")
    p.add_argument("--binance-bars", type=int, default=8000, help="how many 5m bars to pull from binance")
    p.add_argument("--warmup", type=int, default=800)
    p.add_argument("--max-per-day", type=int, default=2)
    args = p.parse_args(argv)
    rep = run_backtest(
        source=args.source, symbol=args.symbol, interval=args.interval,
        range_=args.range_, binance_bars=args.binance_bars,
        warmup=args.warmup, max_per_day=args.max_per_day,
    )
    _print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
