from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from src.market.normalize_tradovate_bar import MarketBar


def _floor_to_bucket(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp to the start of its N-minute bucket (UTC)."""
    ts = ts.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = ts - epoch
    total_min = int(delta.total_seconds() // 60)
    floored = (total_min // minutes) * minutes
    return epoch + timedelta(minutes=floored)


def aggregate(bars: Sequence[MarketBar], minutes: int) -> list[MarketBar]:
    """Aggregate a series of smaller bars into N-minute bars.

    Timestamp of each aggregated bar is the bucket START. Only fully formed
    buckets are returned when `only_completed` semantics are needed; callers
    that need the last (possibly partial) bucket handle it explicitly.

    Volumes and executed-side volumes are summed so absorption stays valid on
    aggregated bars when the source provides bid/offer volume.
    """
    if minutes <= 0:
        raise ValueError("MINUTES_MUST_BE_POSITIVE")
    if not bars:
        return []

    buckets: dict[datetime, list[MarketBar]] = {}
    order: list[datetime] = []
    for bar in bars:
        key = _floor_to_bucket(bar.timestamp, minutes)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(bar)

    result: list[MarketBar] = []
    for key in order:
        group = buckets[key]
        result.append(_merge(group, key))
    return result


def _sum_optional(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _merge(group: Sequence[MarketBar], bucket_start: datetime) -> MarketBar:
    first = group[0]
    return MarketBar(
        timestamp=bucket_start,
        open=first.open,
        high=max(b.high for b in group),
        low=min(b.low for b in group),
        close=group[-1].close,
        volume=_sum_optional([b.volume for b in group]),
        bid_volume=_sum_optional([b.bid_volume for b in group]),
        offer_volume=_sum_optional([b.offer_volume for b in group]),
        up_volume=_sum_optional([b.up_volume for b in group]),
        down_volume=_sum_optional([b.down_volume for b in group]),
    )
