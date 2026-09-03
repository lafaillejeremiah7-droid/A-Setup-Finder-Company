from __future__ import annotations

from typing import Iterator, Sequence

from src.market.normalize_tradovate_bar import MarketBar


class ReplayFeed:
    """A deterministic feed that replays a fixed list of completed 5m bars.

    Used for local end-to-end testing without live Tradovate data. The first
    `warmup` bars are returned as history; the remainder are streamed one by one
    exactly as a live feed would deliver newly-closed bars.
    """

    def __init__(self, bars: Sequence[MarketBar], warmup: int) -> None:
        if warmup < 0 or warmup > len(bars):
            raise ValueError("INVALID_WARMUP")
        self._bars = list(bars)
        self._warmup = warmup

    def history_5m(self) -> Sequence[MarketBar]:
        return self._bars[: self._warmup]

    def stream_5m(self) -> Iterator[MarketBar]:
        for bar in self._bars[self._warmup :]:
            yield bar
