from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    bid_volume: float | None = None
    offer_volume: float | None = None
    up_volume: float | None = None
    down_volume: float | None = None


class MarketDataError(ValueError):
    """Raised when incoming market data is malformed or unusable."""


def _to_optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"INVALID_{field_name.upper()}") from exc
    if not isfinite(number) or number < 0:
        raise MarketDataError(f"INVALID_{field_name.upper()}")
    return number


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        raise MarketDataError("INVALID_TIMESTAMP")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("INVALID_TIMESTAMP") from exc


def normalize_tradovate_bar(raw: Mapping[str, Any]) -> MarketBar:
    try:
        open_price = float(raw["open"])
        high = float(raw["high"])
        low = float(raw["low"])
        close = float(raw["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("INVALID_OHLC") from exc

    for name, value in {
        "OPEN": open_price,
        "HIGH": high,
        "LOW": low,
        "CLOSE": close,
    }.items():
        if not isfinite(value):
            raise MarketDataError(f"INVALID_{name}")

    if high < max(open_price, close):
        raise MarketDataError("INVALID_HIGH")
    if low > min(open_price, close):
        raise MarketDataError("INVALID_LOW")
    if high < low:
        raise MarketDataError("INVALID_RANGE")

    return MarketBar(
        timestamp=_parse_timestamp(raw.get("timestamp")),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=_to_optional_float(raw.get("volume"), "volume"),
        bid_volume=_to_optional_float(raw.get("bidVolume"), "bid_volume"),
        offer_volume=_to_optional_float(raw.get("offerVolume"), "offer_volume"),
        up_volume=_to_optional_float(raw.get("upVolume"), "up_volume"),
        down_volume=_to_optional_float(raw.get("downVolume"), "down_volume"),
    )


def has_executed_side_volume(bar: MarketBar) -> bool:
    return bar.bid_volume is not None and bar.offer_volume is not None


def bar_delta(bar: MarketBar) -> float | None:
    if not has_executed_side_volume(bar):
        return None
    assert bar.bid_volume is not None and bar.offer_volume is not None
    return bar.offer_volume - bar.bid_volume


def bar_delta_pct(bar: MarketBar) -> float | None:
    if not has_executed_side_volume(bar):
        return None
    assert bar.bid_volume is not None and bar.offer_volume is not None
    total = bar.offer_volume + bar.bid_volume
    return 0.0 if total == 0 else (bar.offer_volume - bar.bid_volume) / total
