"""Data source fetchers for The Gamma Map.

Stdlib only -- no third-party dependencies, so the capture job can run anywhere.

Every fetch returns the payload plus its own retrieval timestamp, because paper
section 9.1 requires synchronized snapshots: using a 10:00 NDX quote with a 10:02 NQ
price fabricates false level movement. Callers must check the skew.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

USER_AGENT = "Mozilla/5.0 (compatible; gammamap-capture/1)"
TIMEOUT = 45

# CBOE delayed quotes -- free, no key. Underscore prefix marks an index.
CBOE_OPTIONS = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

# Yahoo chart endpoint -- used only for the futures quote needed for basis.
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FetchError(RuntimeError):
    """Raised when a source cannot be retrieved or parsed."""


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise FetchError(f"{url}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: invalid JSON ({exc})") from exc


@dataclass
class OptionChain:
    """A raw CBOE options chain plus provenance."""

    symbol: str
    retrieved_at: str
    feed_timestamp: str | None
    quote_timestamp: str | None
    spot: float | None
    contract_count: int
    payload: dict[str, Any] = field(repr=False)

    @property
    def is_usable(self) -> bool:
        return bool(self.contract_count) and self.spot is not None


def fetch_option_chain(symbol: str) -> OptionChain:
    """Fetch a CBOE options chain. Use '_NDX' for the index, 'QQQ' for the ETF."""
    payload = _get_json(CBOE_OPTIONS.format(symbol=symbol))
    retrieved_at = _utcnow()

    data = payload.get("data") or {}
    options = data.get("options") or []

    return OptionChain(
        symbol=symbol,
        retrieved_at=retrieved_at,
        # Feed-level publish time; distinct from the underlying's last trade.
        feed_timestamp=payload.get("timestamp"),
        quote_timestamp=data.get("last_trade_time"),
        spot=data.get("current_price"),
        contract_count=len(options),
        payload=payload,
    )


@dataclass
class Quote:
    """A single price quote plus provenance."""

    symbol: str
    retrieved_at: str
    price: float | None
    currency: str | None
    exchange: str | None
    # Yahoo NQ=F is a CONTINUOUS front-month series. Paper section 9.1 wants the
    # specific traded contract, so this is flagged for the mapping engine to resolve.
    is_continuous_series: bool = False

    @property
    def is_usable(self) -> bool:
        return self.price is not None and self.price > 0


def fetch_quote(symbol: str) -> Quote:
    """Fetch a spot/futures price from Yahoo."""
    payload = _get_json(YAHOO_CHART.format(symbol=symbol))
    retrieved_at = _utcnow()

    try:
        meta = payload["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError) as exc:
        raise FetchError(f"{symbol}: unexpected Yahoo response shape ({exc})") from exc

    return Quote(
        symbol=symbol,
        retrieved_at=retrieved_at,
        price=meta.get("regularMarketPrice"),
        currency=meta.get("currency"),
        exchange=meta.get("exchangeName"),
        is_continuous_series=symbol.endswith("=F"),
    )
