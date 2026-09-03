from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from src.market.normalize_tradovate_bar import MarketBar

# Binance public DATA mirror (data-api.binance.vision): same klines schema as
# Binance.com, full liquidity + deep history, and NOT geoblocked. No API key.
# (api.binance.com / api.binance.us are geoblocked or thin from some regions.)
_BASE = "https://data-api.binance.vision/api/v3/klines"


class BinanceDataError(RuntimeError):
    """Raised when Binance kline data cannot be fetched or parsed."""


def _interval_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def fetch_binance_bars(
    symbol: str = "BTCUSDT",
    interval: str = "5m",
    bars: int = 5000,
) -> list[MarketBar]:
    """Fetch OHLCV + real order-flow bars from Binance.US, paginating backward.

    Crucially, Binance klines include `takerBuyBaseVol` = aggressive (taker) BUY
    volume. We derive true executed-side delta:
        offer_volume (aggressive buy)  = takerBuyBaseVol
        bid_volume   (aggressive sell) = volume - takerBuyBaseVol
    This is genuine order flow, so the absorption detector produces REAL bubbles
    and the A+ tier can actually be tested (unlike free futures OHLCV).
    """
    step = _interval_ms(interval)
    end = int(time.time() * 1000)
    collected: list[list] = []
    per_call = 1000

    while len(collected) < bars:
        start = end - per_call * step
        url = f"{_BASE}?symbol={symbol}&interval={interval}&startTime={start}&endTime={end}&limit={per_call}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            chunk = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BinanceDataError(f"HTTP_{exc.code}:{exc.read().decode('utf-8', 'replace')}") from exc
        except urllib.error.URLError as exc:
            raise BinanceDataError(f"TRANSPORT:{exc}") from exc
        if not chunk:
            break
        collected = chunk + collected
        end = chunk[0][0] - 1  # step back before earliest openTime
        if len(chunk) < per_call:
            break
        time.sleep(0.15)  # be polite to the public endpoint

    # de-dup by openTime and sort
    seen: set[int] = set()
    out: list[MarketBar] = []
    for k in collected:
        ot = int(k[0])
        if ot in seen:
            continue
        seen.add(ot)
        volume = float(k[5])
        taker_buy = float(k[9])          # aggressive buy (at ask)
        taker_sell = max(0.0, volume - taker_buy)  # aggressive sell (at bid)
        out.append(
            MarketBar(
                timestamp=datetime.fromtimestamp(ot / 1000, tz=timezone.utc),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=volume,
                offer_volume=taker_buy,    # executed at ask = aggressive buying
                bid_volume=taker_sell,     # executed at bid = aggressive selling
                up_volume=taker_buy,
                down_volume=taker_sell,
            )
        )
    out.sort(key=lambda b: b.timestamp)
    return out
