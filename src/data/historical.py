from __future__ import annotations

from csv import DictReader
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from math import isfinite
from typing import Iterable, Mapping, Sequence

from src.market.normalize_tradovate_bar import MarketBar


class HistoricalDataError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalBar:
    contract: str
    bar: MarketBar


@dataclass(frozen=True)
class HistoricalSeries:
    instrument: str
    bars: tuple[HistoricalBar, ...]
    requires_executed_side_volume: bool

    def market_bars(self) -> tuple[MarketBar, ...]:
        return tuple(item.bar for item in self.bars)


@dataclass(frozen=True)
class RollSegment:
    contract: str
    start: datetime
    end: datetime


def _parse_timestamp(value: str) -> datetime:
    if not value:
        raise HistoricalDataError("TIMESTAMP_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataError("INVALID_TIMESTAMP") from exc
    if parsed.tzinfo is None:
        raise HistoricalDataError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def _float(row: Mapping[str, str], key: str, *, required: bool = True) -> float | None:
    raw = row.get(key)
    if raw in (None, ""):
        if required:
            raise HistoricalDataError(f"{key.upper()}_REQUIRED")
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HistoricalDataError(f"INVALID_{key.upper()}") from exc
    if not isfinite(value):
        raise HistoricalDataError(f"INVALID_{key.upper()}")
    if key not in {"open", "high", "low", "close"} and value < 0:
        raise HistoricalDataError(f"NEGATIVE_{key.upper()}")
    return value


def parse_canonical_csv(
    text: str,
    *,
    instrument: str,
    require_executed_side_volume: bool,
) -> HistoricalSeries:
    """Parse normalized research bars from a provider-independent CSV schema.

    Required columns:
      timestamp, contract, open, high, low, close, volume

    For MGC absorption research, bid_volume and offer_volume are also mandatory.
    Timestamps represent BAR CLOSE/COMPLETION time and must be timezone-aware.
    The loader does not create synthetic continuous prices or back-adjust rolls.
    """
    reader = DictReader(StringIO(text))
    required_columns = {"timestamp", "contract", "open", "high", "low", "close", "volume"}
    if reader.fieldnames is None or not required_columns.issubset(set(reader.fieldnames)):
        raise HistoricalDataError("CANONICAL_COLUMNS_MISSING")
    if require_executed_side_volume and not {"bid_volume", "offer_volume"}.issubset(set(reader.fieldnames)):
        raise HistoricalDataError("EXECUTED_SIDE_VOLUME_COLUMNS_MISSING")

    parsed: list[HistoricalBar] = []
    previous_ts: datetime | None = None
    for row_number, row in enumerate(reader, start=2):
        try:
            timestamp = _parse_timestamp(row.get("timestamp", ""))
            contract = (row.get("contract") or "").strip()
            if not contract:
                raise HistoricalDataError("CONTRACT_REQUIRED")
            open_price = _float(row, "open")
            high = _float(row, "high")
            low = _float(row, "low")
            close = _float(row, "close")
            volume = _float(row, "volume")
            bid_volume = _float(row, "bid_volume", required=require_executed_side_volume)
            offer_volume = _float(row, "offer_volume", required=require_executed_side_volume)
        except HistoricalDataError as exc:
            raise HistoricalDataError(f"ROW_{row_number}:{exc}") from exc

        assert open_price is not None and high is not None and low is not None and close is not None
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise HistoricalDataError(f"ROW_{row_number}:INVALID_OHLC_GEOMETRY")
        if previous_ts is not None and timestamp <= previous_ts:
            raise HistoricalDataError(f"ROW_{row_number}:TIMESTAMPS_NOT_STRICTLY_INCREASING")
        previous_ts = timestamp

        parsed.append(
            HistoricalBar(
                contract=contract,
                bar=MarketBar(
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    bid_volume=bid_volume,
                    offer_volume=offer_volume,
                ),
            )
        )

    if not parsed:
        raise HistoricalDataError("NO_DATA_ROWS")

    return HistoricalSeries(
        instrument=instrument,
        bars=tuple(parsed),
        requires_executed_side_volume=require_executed_side_volume,
    )


def validate_roll_segments(segments: Sequence[RollSegment]) -> None:
    if not segments:
        raise HistoricalDataError("ROLL_SEGMENTS_REQUIRED")
    previous_end: datetime | None = None
    seen: set[str] = set()
    for segment in segments:
        if not segment.contract.strip():
            raise HistoricalDataError("ROLL_CONTRACT_REQUIRED")
        if segment.start.tzinfo is None or segment.end.tzinfo is None:
            raise HistoricalDataError("ROLL_TIMESTAMPS_MUST_BE_TIMEZONE_AWARE")
        if segment.end <= segment.start:
            raise HistoricalDataError("ROLL_SEGMENT_END_MUST_FOLLOW_START")
        if previous_end is not None and segment.start < previous_end:
            raise HistoricalDataError("ROLL_SEGMENTS_OVERLAP")
        if segment.contract in seen:
            raise HistoricalDataError("ROLL_CONTRACT_REUSED")
        previous_end = segment.end
        seen.add(segment.contract)


def apply_roll_schedule(
    series: HistoricalSeries,
    segments: Sequence[RollSegment],
) -> HistoricalSeries:
    """Select actual tradable contract bars according to an explicit roll schedule.

    No back-adjustment is performed. Bars outside the schedule are dropped.
    Each included bar must match the contract designated for its timestamp.
    """
    validate_roll_segments(segments)
    selected: list[HistoricalBar] = []
    segment_index = 0

    for item in series.bars:
        ts = item.bar.timestamp
        while segment_index < len(segments) and ts >= segments[segment_index].end:
            segment_index += 1
        if segment_index >= len(segments):
            break
        segment = segments[segment_index]
        if ts < segment.start:
            continue
        if item.contract != segment.contract:
            raise HistoricalDataError(
                f"CONTRACT_MISMATCH_AT_{ts.isoformat()}:EXPECTED_{segment.contract}:GOT_{item.contract}"
            )
        selected.append(item)

    if not selected:
        raise HistoricalDataError("ROLL_SCHEDULE_SELECTED_NO_BARS")
    return HistoricalSeries(series.instrument, tuple(selected), series.requires_executed_side_volume)


def validate_mgc_dx_alignment(
    mgc: HistoricalSeries,
    dx: HistoricalSeries,
    *,
    max_dx_lag_seconds: int = 900,
) -> None:
    """Fail closed if DX cannot provide historically available context for MGC.

    This does not require identical bar timestamps. It verifies that every MGC
    timestamp has at least one completed DX bar at or before it and that the most
    recent DX completion is not older than the configured lag tolerance.
    """
    if max_dx_lag_seconds < 0:
        raise HistoricalDataError("MAX_DX_LAG_SECONDS_MUST_BE_NONNEGATIVE")
    dx_bars = dx.market_bars()
    if not dx_bars:
        raise HistoricalDataError("DX_SERIES_EMPTY")

    dxi = 0
    latest_dx: MarketBar | None = None
    for mgc_item in mgc.bars:
        while dxi < len(dx_bars) and dx_bars[dxi].timestamp <= mgc_item.bar.timestamp:
            latest_dx = dx_bars[dxi]
            dxi += 1
        if latest_dx is None:
            raise HistoricalDataError("DX_NOT_AVAILABLE_BEFORE_MGC")
        lag = (mgc_item.bar.timestamp - latest_dx.timestamp).total_seconds()
        if lag > max_dx_lag_seconds:
            raise HistoricalDataError(
                f"DX_STALE_AT_{mgc_item.bar.timestamp.isoformat()}:LAG_{int(lag)}S"
            )
