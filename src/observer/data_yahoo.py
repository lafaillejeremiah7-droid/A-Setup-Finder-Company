from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from src.market.normalize_tradovate_bar import MarketBar


class YahooDataError(RuntimeError):
    """Raised when Yahoo chart data cannot be fetched or parsed."""


def fetch_yahoo_bars(
    symbol: str = "GC=F",
    interval: str = "5m",
    range_: str = "60d",
) -> list[MarketBar]:
    """Fetch OHLCV bars from Yahoo Finance's public chart endpoint.

    Free, no API key. Gold futures = "GC=F". Intraday history is limited
    (~60 days for 5m). NOTE: OHLCV only — there is NO order-flow / aggressor
    volume, so absorption will be UNKNOWN and grades cap at A. This is for
    validating the core trend-line + S/R edge, not the A+ absorption tier.
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={interval}&range={range_}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise YahooDataError(f"FETCH_FAILED:{exc}") from exc

    try:
        payload = json.loads(raw)
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        opens = quote["open"]
        highs = quote["high"]
        lows = quote["low"]
        closes = quote["close"]
        volumes = quote.get("volume", [None] * len(timestamps))
    except (KeyError, IndexError, TypeError) as exc:
        err = payload.get("chart", {}).get("error") if isinstance(raw, str) else None
        raise YahooDataError(f"PARSE_FAILED:{err or exc}") from exc

    bars: list[MarketBar] = []
    for i, ts in enumerate(timestamps):
        o, h, low, c = opens[i], highs[i], lows[i], closes[i]
        if None in (o, h, low, c):
            continue  # Yahoo pads gaps with nulls
        bars.append(
            MarketBar(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
                volume=float(volumes[i]) if volumes[i] is not None else None,
            )
        )
    bars.sort(key=lambda b: b.timestamp)
    return bars
