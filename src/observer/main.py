from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

from src.market.market_structure import StructureConfig
from src.observer.aggregate import aggregate
from src.observer.config import AppConfig, load_config
from src.observer.engine import Setup, evaluate
from src.observer.notify import NotifyError, TelegramNotifier
from src.observer.structure import detect_structure


def format_setup(setup: Setup, symbol: str) -> str:
    arrow = "🟢 LONG" if setup.direction.value == "LONG" else "🔴 SHORT"
    zone = f"inside {setup.zone_kind} zone" if setup.in_zone else "clean context"
    return (
        f"*{setup.grade.value} SETUP* — {symbol}\n"
        f"{arrow}  ({setup.event.value} of {setup.line_timeframe.value} line)\n"
        f"Entry: `{setup.entry}`\n"
        f"Stop:  `{setup.stop}`  (safety line)\n"
        f"Target:`{setup.target}`\n"
        f"R:R:   `{setup.rr}`   Risk: `{setup.risk_points}` pts\n"
        f"Context: {zone}\n"
        f"Absorption: {setup.absorption}\n"
        f"_{' | '.join(setup.reasons)}_\n"
        f"`{setup.timestamp.isoformat()}`"
    )


class DailyGate:
    """Enforces max alerts/day and one-alert-per-setup dedupe."""

    def __init__(self, max_per_day: int) -> None:
        self._max = max_per_day
        self._day: date | None = None
        self._count = 0
        self._seen: set = set()

    def _roll(self, when: datetime) -> None:
        d = when.astimezone(timezone.utc).date()
        if d != self._day:
            self._day = d
            self._count = 0
            self._seen = set()

    def allow(self, setup: Setup) -> bool:
        self._roll(setup.timestamp)
        if setup.key() in self._seen:
            return False
        if self._count >= self._max:
            return False
        self._seen.add(setup.key())
        self._count += 1
        return True


def run(config: AppConfig, feed, *, dry_run: bool = False) -> int:
    notifier = TelegramNotifier(config.telegram)
    gate = DailyGate(config.max_alerts_per_day)
    struct_cfg = StructureConfig()

    history = list(feed.history_5m())
    window: list = list(history)

    alerts = 0
    for bar in feed.stream_5m():
        window.append(bar)
        # keep a bounded window (enough for 4H detection: ~ a few weeks of 5m)
        if len(window) > 5000:
            window = window[-5000:]

        bars_1h = aggregate(window, 60)
        bars_4h = aggregate(window, 240)
        # exclude the still-forming final HTF bucket so HTF stays closed-bar
        bars_1h_closed = bars_1h[:-1] if len(bars_1h) > 1 else bars_1h
        bars_4h_closed = bars_4h[:-1] if len(bars_4h) > 1 else bars_4h

        structure = detect_structure(
            bars_4h=bars_4h_closed, bars_1h=bars_1h_closed, config=struct_cfg
        )
        setup = evaluate(
            bars_5m=window,
            structure=structure,
            htf_bars_4h=bars_4h_closed,
            htf_bars_1h=bars_1h_closed,
        )
        if setup is None:
            continue
        if not gate.allow(setup):
            continue

        message = format_setup(setup, config.symbol)
        alerts += 1
        if dry_run:
            print("=== ALERT ===")
            print(message)
        else:
            try:
                notifier.send(message)
                print(f"[sent] {setup.grade.value} {setup.direction.value} @ {setup.entry}")
            except NotifyError as exc:
                print(f"[notify-error] {exc}", file=sys.stderr)
    return alerts


def _build_feed(args, config: AppConfig):
    if args.replay:
        from tests.observer.sample_data import build_sample_bars

        from src.observer.feed_replay import ReplayFeed

        bars = build_sample_bars()
        return ReplayFeed(bars, warmup=max(0, len(bars) - args.replay_stream))

    from src.observer.feed_tradovate import TradovateFeed

    feed = TradovateFeed(config.tradovate, config.symbol)
    feed.authenticate()
    return feed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MGCV26 reactive setup observer")
    parser.add_argument("--replay", action="store_true", help="Run on built-in sample bars")
    parser.add_argument("--replay-stream", type=int, default=60, help="How many bars to stream in replay")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts instead of sending Telegram")
    parser.add_argument("--test-telegram", action="store_true", help="Send one test message and exit")
    args = parser.parse_args(argv)

    config = load_config()

    if args.test_telegram:
        TelegramNotifier(config.telegram).send("✅ MGCV26 observer: Telegram wired correctly.")
        print("[sent] test message")
        return 0

    feed = _build_feed(args, config)
    alerts = run(config, feed, dry_run=args.dry_run)
    print(f"[done] {alerts} alert(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
