from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from src.market.normalize_tradovate_bar import MarketBar

BASE = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)


def _bar(i: int, o: float, h: float, low: float, c: float,
         vol: float = 100.0, bid: float | None = None, off: float | None = None) -> MarketBar:
    hi = max(h, o, c)
    lo = min(low, o, c)
    return MarketBar(
        timestamp=BASE + timedelta(minutes=5 * i),
        open=round(o, 1), high=round(hi, 1), low=round(lo, 1), close=round(c, 1),
        volume=vol, bid_volume=bid, offer_volume=off,
    )


def build_sample_bars() -> list[MarketBar]:
    """Large synthetic 5m series (~1600 bars ≈ 5.5 days) engineered so that:

      * 4H and 1H aggregation yields enough bars for ATR(14) + pivots,
      * a descending resistance trend line and rising support form a coil,
      * price then breaks UP through resistance with a lower-wick buying-
        absorption bubble on the pre-break bar (to reach A+).

    Illustrative only — NOT real MGC data. Purpose: exercise the full
    feed->aggregate->structure->engine->notify pipeline offline.
    """
    bars: list[MarketBar] = []
    i = 0

    # A large-scale oscillation builds swing highs/lows on the higher
    # timeframes. Amplitude slowly narrows (a coil) with a descending top and
    # a rising bottom so a converging triangle forms.
    n_pre = 1400
    top0, bot0 = 2440.0, 2360.0          # starting resistance/support
    top_slope = -0.030                    # resistance descends per 5m bar
    bot_slope = +0.022                    # support rises per 5m bar
    period = 220.0                        # 5m bars per oscillation

    price = 2400.0
    for k in range(n_pre):
        top = top0 + top_slope * k
        bot = bot0 + bot_slope * k
        mid = (top + bot) / 2.0
        amp = (top - bot) / 2.0
        target = mid + amp * math.sin(2 * math.pi * k / period)
        nxt = price + (target - price) * 0.5
        o = price
        c = nxt
        h = max(o, c) + amp * 0.06
        low = min(o, c) - amp * 0.06
        bars.append(_bar(i, o, h, low, c)); i += 1
        price = c

    # Bring price up to just under the current descending resistance so the
    # next candle can break it cleanly. Give this run bid/offer volume so the
    # absorption detector has a full, clean lookback window (delta ~ balanced).
    top_now = top0 + top_slope * n_pre
    for _ in range(25):
        o = price
        c = price + (top_now - 4 - price) * 0.3
        bars.append(_bar(i, o, max(o, c) + 1, min(o, c) - 1, c,
                         vol=100, bid=50, off=50)); i += 1
        price = c

    # Pre-break absorption bubble: heavy aggressive SELLING absorbed by buyers
    # (delta strongly negative vs the balanced history), long lower wick, close
    # holds up => bullish (red / lower-wick buying absorption).
    o = price
    low = price - 6
    c = price + 0.5           # closed back near the top of the range
    h = price + 1
    bars.append(_bar(i, o, h, low, c, vol=600, bid=560, off=40)); i += 1
    price = c

    # Decisive break UP through the descending resistance line.
    o = price
    c = top_now + 10
    bars.append(_bar(i, o, c + 1, o - 1, c, vol=400, bid=180, off=220)); i += 1
    price = c

    # Continuation.
    for _ in range(10):
        o = price
        c = price + 2.5
        bars.append(_bar(i, o, c + 1, o - 1, c, vol=180, bid=80, off=100)); i += 1
        price = c

    return bars
