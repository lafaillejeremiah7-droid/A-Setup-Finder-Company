#!/usr/bin/env python3
"""Download real futures trades and build canonical GIBRC research bars.

Install:
    pip install -U databento pandas

Set:
    export DATABENTO_API_KEY='...'

Run (full Databento-covered spans):
    python scripts/download_databento_ticks.py --out data/raw

Important coverage fact:
- MGC / GLBX.MDP3 is available for the requested 2015+ window.
- DX / IFUS.IMPACT starts 2018-12-23 on Databento. This script refuses to
  pretend Databento covers DX before that date.

No back-adjustment is performed. Databento continuous symbology is used only
as a roll selector; every tick is an original trade from the mapped tradable
instrument, and the CSV `contract` field stores that actual instrument ID.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

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

# Strategy-spec timeframes.
TIMEFRAMES = {
    "MGC": {"5m": "5min", "30m": "30min", "4h": "4h"},
    "DX": {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"},
}

DATABENTO_PRODUCTS = {
    "MGC": {
        "dataset": "GLBX.MDP3",
        "continuous": "MGC.v.0",
        "coverage_start": date(2010, 9, 12),
    },
    "DX": {
        "dataset": "IFUS.IMPACT",
        "continuous": "DX.v.0",
        "coverage_start": date(2018, 12, 23),
    },
}


@dataclass(frozen=True)
class Chunk:
    start: datetime
    end: datetime


def month_chunks(start: datetime, end: datetime) -> Iterable[Chunk]:
    cursor = start
    while cursor < end:
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1, day=1)
        yield Chunk(cursor, min(next_month, end))
        cursor = min(next_month, end)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_trade_frame(data):
    """Return Databento trades with exact UTC event time and strict side semantics."""
    df = data.to_df()
    if df.empty:
        return df

    # Databento's DataFrame index may be ts_recv; use exchange event time when present.
    if "ts_event" in df.columns:
        ts = df["ts_event"]
    else:
        ts = df.index
    df = df.copy()
    df["event_time"] = ts
    df["event_time"] = __import__("pandas").to_datetime(df["event_time"], utc=True)

    required = {"instrument_id", "price", "size", "side"}
    missing = required.difference(df.columns)
    if missing:
        raise RuntimeError(f"Databento trades response missing fields: {sorted(missing)}")

    # Databento trade side: A = seller aggressor -> executed at bid;
    # B = buyer aggressor -> executed at ask; N = no side specified.
    unknown = ~df["side"].isin(["A", "B"])
    if unknown.any():
        sample = df.loc[unknown, ["event_time", "instrument_id", "price", "size", "side"]].head(10)
        raise RuntimeError(
            "UNCLASSIFIED_AGGRESSOR_SIDE: strict research mode refuses trades with side N/unknown. "
            "Sample:\n" + sample.to_string(index=False)
        )

    df["contract"] = df["instrument_id"].map(lambda x: f"instrument_id:{int(x)}")
    df["bid_exec"] = df["size"].where(df["side"] == "A", 0)
    df["offer_exec"] = df["size"].where(df["side"] == "B", 0)
    return df[["event_time", "contract", "price", "size", "bid_exec", "offer_exec"]]


def aggregate_canonical(trades, rule: str):
    """Aggregate exact trades into right-labeled bar-completion timestamps."""
    pd = __import__("pandas")
    if trades.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df = trades.sort_values("event_time").set_index("event_time")
    # closed='left', label='right': [09:00,09:05) is known at 09:05.
    grouped = df.groupby(pd.Grouper(freq=rule, closed="left", label="right", origin="epoch"))

    rows = []
    for close_ts, group in grouped:
        if group.empty:
            continue
        contracts = group["contract"].unique()
        if len(contracts) != 1:
            raise RuntimeError(
                f"ROLL_INSIDE_BAR at {close_ts}: {contracts.tolist()}. "
                "Refusing to create a mixed-contract synthetic bar."
            )
        bid_volume = float(group["bid_exec"].sum())
        offer_volume = float(group["offer_exec"].sum())
        volume = float(group["size"].sum())
        if abs((bid_volume + offer_volume) - volume) > 1e-9:
            raise RuntimeError(f"SIDE_VOLUME_DOES_NOT_SUM_TO_TOTAL at {close_ts}")
        rows.append(
            {
                "timestamp": close_ts.isoformat().replace("+00:00", "Z"),
                "contract": contracts[0],
                "open": float(group["price"].iloc[0]),
                "high": float(group["price"].max()),
                "low": float(group["price"].min()),
                "close": float(group["price"].iloc[-1]),
                "volume": volume,
                "bid_volume": bid_volume,
                "offer_volume": offer_volume,
            }
        )
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def append_csv(frame, path: Path) -> None:
    if frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, mode="a", header=not path.exists(), index=False, columns=CANONICAL_COLUMNS)


def download_instrument(client, instrument: str, start: datetime, end: datetime, out_dir: Path) -> None:
    cfg = DATABENTO_PRODUCTS[instrument]
    covered = datetime.combine(cfg["coverage_start"], datetime.min.time(), tzinfo=timezone.utc)
    if start < covered:
        raise RuntimeError(
            f"{instrument}: requested {start.date()} but Databento {cfg['dataset']} coverage begins "
            f"{covered.date()}. Refusing to fabricate the missing span."
        )

    outputs = {name: out_dir / f"{instrument.lower()}_{name}.csv" for name in TIMEFRAMES[instrument]}
    for path in outputs.values():
        if path.exists():
            path.unlink()  # deterministic fresh run; never silently append duplicate history

    for chunk in month_chunks(start, end):
        print(f"{instrument} {chunk.start.date()} -> {chunk.end.date()}")
        data = client.timeseries.get_range(
            dataset=cfg["dataset"],
            schema="trades",
            symbols=cfg["continuous"],
            stype_in="continuous",
            start=chunk.start.isoformat(),
            end=chunk.end.isoformat(),
        )
        trades = _normalize_trade_frame(data)
        for name, rule in TIMEFRAMES[instrument].items():
            append_csv(aggregate_canonical(trades, rule), outputs[name])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01T00:00:00Z")
    parser.add_argument("--end", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    parser.add_argument("--instrument", choices=["MGC", "DX", "both"], default="both")
    args = parser.parse_args()

    try:
        import databento as db
        import pandas  # noqa: F401
    except ImportError as exc:
        raise SystemExit("Install dependencies first: pip install -U databento pandas") from exc

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        raise SystemExit("DATABENTO_API_KEY is required")

    start, end = _utc(args.start), _utc(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    client = db.Historical(api_key)
    instruments = ["MGC", "DX"] if args.instrument == "both" else [args.instrument]
    for instrument in instruments:
        download_instrument(client, instrument, start, end, args.out)


if __name__ == "__main__":
    main()
