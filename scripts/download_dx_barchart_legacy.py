#!/usr/bin/env python3
"""Backfill pre-Databento DX futures bars from Barchart individual contracts.

Purpose
-------
The current GIBRC strategy uses executed-side volume only for MGC absorption.
DX is a price/structure filter, so pre-2018 DX needs real unadjusted contract
OHLCV, not aggressor-side volume.

This script requests *individual* ICE U.S. Dollar Index futures contracts from
Barchart OnDemand, stitches them with an explicit expiration roll, and writes
the exact canonical CSV schema consumed by src/data/historical.py.

No back-adjustments. No synthetic prices. `bid_volume` and `offer_volume` are
left blank because they are not required for DX and Barchart getHistory does
not document aggressor-side volume.

Environment:
    export BARCHART_API_KEY='...'

Example:
    python scripts/download_dx_barchart_legacy.py \
      --start 2015-01-01T00:00:00Z \
      --end 2018-12-23T00:00:00Z \
      --out data/raw

Requires only Python stdlib + pandas.
"""

from __future__ import annotations

import argparse
import json
import os
from calendar import monthcalendar, WEDNESDAY
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

CANONICAL_COLUMNS = (
    "timestamp",
    "contract",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "bid_volume",
    "offer_volume",
)
TIMEFRAMES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}
API = "https://ondemand.websol.barchart.com/getHistory.json"


def utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def third_wednesday(year: int, month: int) -> datetime:
    Wednesdays = [week[WEDNESDAY] for week in monthcalendar(year, month) if week[WEDNESDAY]]
    return datetime(year, month, Wednesdays[2], tzinfo=timezone.utc)


def last_trading_day(year: int, month: int) -> datetime:
    # ICE current DX spec: trading ceases two days before third-Wednesday settlement.
    # For 2015 contracts this matches published Barchart expiries (e.g. DXH15 2015-03-16,
    # DXM15 2015-06-15). We switch contracts at 00:00 UTC on that calendar date to
    # avoid mixing contracts in a bar. The roll rule is explicit and unadjusted.
    return third_wednesday(year, month) - timedelta(days=2)


def contract_symbol(year: int, month: int) -> str:
    return f"DX{MONTH_CODE[month]}{year % 100:02d}"


def quarterly_contracts(start: datetime, end: datetime):
    for year in range(start.year - 1, end.year + 2):
        for month in (3, 6, 9, 12):
            expiry = last_trading_day(year, month)
            if expiry >= start - timedelta(days=120) and expiry < end + timedelta(days=120):
                yield contract_symbol(year, month), expiry


def fetch_minutes(api_key: str, symbol: str, start: datetime, end: datetime):
    params = {
        "apikey": api_key,
        "symbol": symbol,
        "type": "minutes",
        "interval": 1,
        "startDate": start.strftime("%Y%m%d%H%M%S"),
        "endDate": end.strftime("%Y%m%d%H%M%S"),
        "maxRecords": 500000,
        "order": "asc",
        "volume": "contract",
        "backAdjust": "false",
    }
    with urlopen(API + "?" + urlencode(params), timeout=120) as response:
        payload = json.load(response)
    status = payload.get("status", {})
    if status.get("code") != 200:
        raise RuntimeError(f"Barchart {symbol}: {status.get('message', 'request failed')}")
    return payload.get("results", [])


def normalized_contract_minutes(rows, requested_symbol: str):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=["event_time", "contract", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    required = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise RuntimeError(f"Barchart response missing fields: {sorted(missing)}")
    symbols = set(df["symbol"].astype(str))
    if symbols != {requested_symbol}:
        raise RuntimeError(f"SYNTHETIC_OR_WRONG_CONTRACT: requested {requested_symbol}, got {sorted(symbols)}")
    df["event_time"] = pd.to_datetime(df["timestamp"], utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    if (df["volume"] < 0).any():
        raise RuntimeError(f"NEGATIVE_VOLUME:{requested_symbol}")
    df["contract"] = requested_symbol
    return df[["event_time", "contract", "open", "high", "low", "close", "volume"]].sort_values("event_time")


def build_front_contract_series(api_key: str, start: datetime, end: datetime):
    import pandas as pd

    contracts = sorted(quarterly_contracts(start, end), key=lambda x: x[1])
    pieces = []
    previous_roll = start
    for symbol, roll_at in contracts:
        segment_start = max(start, previous_roll)
        segment_end = min(end, roll_at)
        if segment_end <= segment_start:
            previous_roll = max(previous_roll, roll_at)
            continue
        print(f"DX {symbol}: {segment_start.isoformat()} -> {segment_end.isoformat()}")
        rows = fetch_minutes(api_key, symbol, segment_start, segment_end)
        frame = normalized_contract_minutes(rows, symbol)
        if frame.empty:
            raise RuntimeError(
                f"NO_BARCHART_MINUTE_DATA:{symbol}:{segment_start.date()}:{segment_end.date()}. "
                "Your Barchart entitlement may not include this legacy intraday span."
            )
        frame = frame[(frame.event_time >= segment_start) & (frame.event_time < segment_end)]
        pieces.append(frame)
        previous_roll = roll_at
        if roll_at >= end:
            break

    if not pieces:
        raise RuntimeError("NO_DX_DATA_DOWNLOADED")
    full = pd.concat(pieces, ignore_index=True).sort_values("event_time")
    if full["event_time"].duplicated().any():
        raise RuntimeError("DUPLICATE_DX_TIMESTAMPS_AFTER_ROLL_STITCH")
    return full


def aggregate(frame, rule: str):
    import pandas as pd

    df = frame.set_index("event_time")
    rows = []
    grouped = df.groupby(pd.Grouper(freq=rule, closed="left", label="right", origin="epoch"))
    for close_ts, group in grouped:
        if group.empty:
            continue
        contracts = group.contract.unique()
        if len(contracts) != 1:
            raise RuntimeError(f"ROLL_INSIDE_BAR:{close_ts}:{contracts.tolist()}")
        rows.append({
            "timestamp": close_ts.isoformat().replace("+00:00", "Z"),
            "contract": contracts[0],
            "open": float(group.open.iloc[0]),
            "high": float(group.high.max()),
            "low": float(group.low.min()),
            "close": float(group.close.iloc[-1]),
            "volume": float(group.volume.sum()),
            "bid_volume": None,
            "offer_volume": None,
        })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01T00:00:00Z")
    parser.add_argument("--end", default="2018-12-23T00:00:00Z")
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    api_key = os.environ.get("BARCHART_API_KEY")
    if not api_key:
        raise SystemExit("BARCHART_API_KEY is required")
    start, end = utc(args.start), utc(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    minute = build_front_contract_series(api_key, start, end)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, rule in TIMEFRAMES.items():
        output = args.out / f"dx_{name}_legacy_2015_2018.csv"
        aggregate(minute, rule).to_csv(output, index=False, columns=CANONICAL_COLUMNS)
        print(output)


if __name__ == "__main__":
    main()
