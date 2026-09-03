from __future__ import annotations

from typing import Iterator, Protocol, Sequence

from src.market.normalize_tradovate_bar import MarketBar


class Feed(Protocol):
    """A source of completed 5-minute MGCV6 bars.

    Implementations must yield ONLY completed bars, in strictly increasing
    timestamp order. The engine relies on this for its no-lookahead guarantee.
    """

    def history_5m(self) -> Sequence[MarketBar]:
        """Return the warm-up history of completed 5m bars (oldest first)."""
        ...

    def stream_5m(self) -> Iterator[MarketBar]:
        """Yield each newly completed 5m bar as it closes."""
        ...
