from __future__ import annotations

from src.market.market_structure import StructureConfig
from src.observer.aggregate import aggregate
from src.observer.data_yahoo import fetch_yahoo_bars
from src.observer.engine import evaluate
from src.observer.structure import detect_structure

bars = fetch_yahoo_bars("GC=F", "5m", "1mo")
print(f"{len(bars)} bars, {bars[0].timestamp} -> {bars[-1].timestamp}")

cfg = StructureConfig()
full_1h = aggregate(bars, 60)
full_4h = aggregate(bars, 240)


def closed_count(htf, ts):
    step = (htf[1].timestamp - htf[0].timestamp) if len(htf) > 1 else None
    if step is None:
        return 0
    n = 0
    for b in htf:
        if b.timestamp + step <= ts:
            n += 1
        else:
            break
    return n


shown = 0
for i in range(400, len(bars)):
    ts = bars[i].timestamp
    n1 = closed_count(full_1h, ts)
    n4 = closed_count(full_4h, ts)
    if n4 < cfg.atr_period + cfg.pivot_width + 2:
        continue
    b1c, b4c = full_1h[:n1], full_4h[:n4]
    s = detect_structure(bars_4h=b4c, bars_1h=b1c, config=cfg)
    setup = evaluate(bars_5m=bars[max(0, i - 1500): i + 1], structure=s,
                     htf_bars_4h=b4c, htf_bars_1h=b1c)
    if setup is None:
        continue
    # what happens in the next 40 bars?
    fut = bars[i + 1: i + 41]
    fmax = max((b.high for b in fut), default=None)
    fmin = min((b.low for b in fut), default=None)
    print(f"{setup.timestamp} {setup.direction.value:5} {setup.event.value:7} "
          f"entry={setup.entry} stop={setup.stop} tgt={setup.target} rr={setup.rr} "
          f"| next40 hi={fmax} lo={fmin}")
    shown += 1
    if shown >= 12:
        break
