from __future__ import annotations

from src.market.market_structure import StructureConfig
from src.observer.aggregate import aggregate
from src.observer.engine import _detect_line_event, _line_value_at_time
from src.observer.structure import detect_structure
from tests.observer.sample_data import build_sample_bars

bars = build_sample_bars()
print(f"total 5m bars: {len(bars)}")

b1h = aggregate(bars, 60)
b4h = aggregate(bars, 240)
print(f"1h buckets: {len(b1h)}  4h buckets: {len(b4h)}")

cfg = StructureConfig()
struct = detect_structure(bars_4h=b4h[:-1] if len(b4h) > 1 else b4h,
                          bars_1h=b1h[:-1] if len(b1h) > 1 else b1h, config=cfg)
print("levels:", [(l.kind.value, round(l.center, 1)) for l in struct.levels])
print("1h up line:", struct.lines_1h.up)
print("1h down line:", struct.lines_1h.down)
print("1h atr:", struct.lines_1h.atr)
print("4h up line:", struct.lines_4h.up)
print("4h down line:", struct.lines_4h.down)
print("4h atr:", struct.lines_4h.atr)

# Walk the tail and see if any line event fires
for end in range(len(bars) - 30, len(bars)):
    window = bars[: end + 1]
    b1 = aggregate(window, 60)
    b4 = aggregate(window, 240)
    b1c = b1[:-1] if len(b1) > 1 else b1
    b4c = b4[:-1] if len(b4) > 1 else b4
    s = detect_structure(bars_4h=b4c, bars_1h=b1c, config=cfg)
    for lines, htf in ((s.lines_1h, b1c), (s.lines_4h, b4c)):
        hit = _detect_line_event(window, lines, htf)
        if hit:
            bar = window[-1]
            lv_d = _line_value_at_time(lines.down, bar.timestamp, htf) if lines.down else None
            print(f"bar {end}: HIT {hit.direction.value} {hit.event.value} "
                  f"{hit.timeframe.value} close={bar.close:.1f} down_line={lv_d}")
