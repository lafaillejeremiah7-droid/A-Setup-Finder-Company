#!/usr/bin/env python3
"""Run the largest credential-free overlap backtest currently available.

This is deliberately labelled EXPLORATORY because:
- MGC is public TopstepX root-symbol OHLCV, not individual-contract tick history.
- UDXUSD is the free HistData U.S. Dollar Index quote series, used only as a
  structure/bias proxy for ICE DX futures.
- MGC public OHLCV has no executed bid/offer split, so absorption is OFF.

The runner still uses the repository's deterministic GIBRC setup, structural
trade-plan, prop-risk and backtest modules. No lookahead data is introduced.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from src.backtest.ablation import AbsorptionMode, apply_absorption_mode
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.gibrc_provider import GIBRCReplayProvider
from src.market.normalize_tradovate_bar import MarketBar
from src.risk.prop_risk import AccountState, PropFirmRules


DEFAULT_MGC_URL = (
    "https://raw.githubusercontent.com/axb0306/cme-futures-ohlc/main/"
    "MGC/MGC_5min_20260120_20260415.csv"
)


def load_mgc(url: str) -> list[MarketBar]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.sort_values("datetime").drop_duplicates("datetime", keep="last")
    bars: list[MarketBar] = []
    for row in frame.itertuples(index=False):
        bars.append(
            MarketBar(
                timestamp=row.datetime.to_pydatetime(),
                open=float(row.open), high=float(row.high), low=float(row.low),
                close=float(row.close), volume=float(row.volume),
            )
        )
    return bars


def _parse_histdata_file(path: Path) -> list[tuple[datetime, float, float, float, float, float]]:
    rows: list[tuple[datetime, float, float, float, float, float]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return rows
    for raw in text.splitlines():
        parts = raw.strip().split(";")
        if len(parts) < 5:
            continue
        try:
            # HistData timestamps are EST (UTC-5) with no DST adjustment.
            local = datetime.strptime(parts[0].strip(), "%Y%m%d %H%M%S")
            ts = (local + timedelta(hours=5)).replace(tzinfo=timezone.utc)
            o, h, l, c = map(float, parts[1:5])
            v = float(parts[5]) if len(parts) > 5 and parts[5] else 0.0
        except (ValueError, TypeError):
            continue
        rows.append((ts, o, h, l, c, v))
    return rows


def load_udx_proxy(root: Path) -> list[MarketBar]:
    records: list[tuple[datetime, float, float, float, float, float]] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".csv", ".txt"}:
            records.extend(_parse_histdata_file(path))
    if not records:
        raise RuntimeError(f"NO_HISTDATA_UDX_ROWS_FOUND_UNDER:{root}")

    frame = pd.DataFrame(records, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    frame = frame.set_index("timestamp")

    # The strategy's DXY role is higher-timeframe structure/bias. Aggregate the
    # 1-minute cash-index proxy to completed 1-hour bars; label at bar completion.
    hourly = frame.resample("1h", closed="left", label="right", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "high", "low", "close"])

    return [
        MarketBar(
            timestamp=idx.to_pydatetime(),
            open=float(row.open), high=float(row.high), low=float(row.low),
            close=float(row.close), volume=float(row.volume),
        )
        for idx, row in hourly.iterrows()
    ]


def write_trades(path: Path, result) -> None:
    fields = [
        "signal_timestamp", "entry_timestamp", "exit_timestamp", "action", "contracts",
        "intended_entry", "filled_entry", "stop_loss", "take_profit", "filled_exit",
        "exit_reason", "bars_held", "gross_pnl", "costs", "net_pnl", "initial_risk",
        "r_multiple", "bar_level_mae", "bar_level_mfe",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for t in result.trades:
            writer.writerow({
                "signal_timestamp": t.signal_timestamp.isoformat(),
                "entry_timestamp": t.entry_timestamp.isoformat(),
                "exit_timestamp": t.exit_timestamp.isoformat(),
                "action": t.action.value,
                "contracts": t.contracts,
                "intended_entry": t.intended_entry,
                "filled_entry": t.filled_entry,
                "stop_loss": t.stop,
                "take_profit": t.target,
                "filled_exit": t.filled_exit,
                "exit_reason": t.exit_reason.value,
                "bars_held": t.bars_held,
                "gross_pnl": t.gross_pnl,
                "costs": t.costs,
                "net_pnl": t.net_pnl,
                "initial_risk": t.initial_risk,
                "r_multiple": t.r_multiple,
                "bar_level_mae": t.bar_level_mae,
                "bar_level_mfe": t.bar_level_mfe,
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mgc-url", default=DEFAULT_MGC_URL)
    parser.add_argument("--dxy-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("backtest_artifact"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    mgc = load_mgc(args.mgc_url)
    dxy = load_udx_proxy(args.dxy_root)
    start, end = mgc[0].timestamp, mgc[-1].timestamp
    dxy = [bar for bar in dxy if bar.timestamp <= end and bar.timestamp >= start - timedelta(days=14)]
    if not dxy:
        raise RuntimeError("NO_DXY_PROXY_OVERLAP")

    rules = PropFirmRules(
        starting_balance=25_000.0,
        maximum_loss=1_500.0,
        drawdown_type="EOD",
        maximum_contracts=2,
        maximum_risk_per_trade=300.0,
        daily_loss_limit=None,
        consistency_limit_pct=None,
    )

    def account_state(_context):
        return AccountState(
            current_equity=25_000.0,
            current_drawdown_floor=23_500.0,
            current_daily_pnl=None,
            consistency_pass=None,
        )

    provider = GIBRCReplayProvider(rules=rules, account_state_provider=account_state)

    def signal_provider(context):
        return apply_absorption_mode(provider.candidate(context), AbsorptionMode.OFF)

    config = BacktestConfig(
        point_value=10.0,
        min_tick=0.1,
        entry_slippage_ticks=1.0,
        exit_slippage_ticks=1.0,
        round_trip_cost_per_contract=2.00,
    )
    result = run_backtest(mgc_bars=mgc, dxy_bars=dxy, signal_provider=signal_provider, config=config)

    trades_path = args.out_dir / "trades.csv"
    write_trades(trades_path, result)
    summary = {
        "label": "EXPLORATORY_PUBLIC_OVERLAP_ABSORPTION_OFF",
        "mgc_source": args.mgc_url,
        "dxy_source": "HistData UDXUSD 1-minute quote proxy, aggregated to 1h",
        "mgc_first": start.isoformat(),
        "mgc_last": end.isoformat(),
        "mgc_bars": len(mgc),
        "dxy_1h_bars": len(dxy),
        "absorption_mode": "OFF",
        "limitations": [
            "MGC public root-symbol OHLCV has no executed bid/offer volume.",
            "UDXUSD is a cash/index quote proxy, not individual ICE DX futures.",
            "MGC root-symbol history does not preserve individual futures contract identity.",
            "Therefore this run is exploratory and is not the final production-quality GIBRC validation.",
        ],
        "backtest": result.summary.__dict__,
        "provider_calls": result.provider_calls,
        "duplicate_signals_suppressed": result.duplicate_signals_suppressed,
        "signals_ignored_while_position_open": result.signals_ignored_while_position_open,
        "unfilled_pending_entries": result.unfilled_pending_entries,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
