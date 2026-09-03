from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Iterator, Sequence

from src.market.normalize_tradovate_bar import MarketBar
from src.observer.config import TradovateConfig


class TradovateError(RuntimeError):
    """Raised on Tradovate auth or market-data failures."""


class TradovateFeed:
    """Live 5-minute MGCV6 feed from Tradovate.

    This is the single swappable integration point. It:
      1. Authenticates via REST to obtain an access token + md access token.
      2. Requests historical 5m chart bars for warm-up.
      3. Streams newly completed 5m bars over the market-data websocket.

    Order-flow / absorption note:
      Tradovate exposes bid/ask (`md/subscribeQuote`) and DOM, but per-bar
      *executed aggressor volume* depends on your CME data entitlement. When the
      chart payload includes bid/offer volume it is passed through so absorption
      works; otherwise bars carry no executed-side volume and the engine's
      absorption detector fails closed (grade caps at A). We never fabricate it.
    """

    def __init__(self, config: TradovateConfig, symbol: str, warmup_bars: int = 300) -> None:
        if not config.is_complete:
            raise TradovateError("TRADOVATE_CREDENTIALS_INCOMPLETE")
        self._config = config
        self._symbol = symbol
        self._warmup_bars = warmup_bars
        self._access_token: str | None = None
        self._md_token: str | None = None

    # ---- auth ----
    def _post(self, path: str, body: dict) -> dict:
        url = f"{self._config.rest_base}{path}"
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TradovateError(f"HTTP_{exc.code}:{exc.read().decode('utf-8', 'replace')}") from exc
        except urllib.error.URLError as exc:
            raise TradovateError(f"TRANSPORT:{exc.reason}") from exc

    def authenticate(self) -> None:
        payload = {
            "name": self._config.username,
            "password": self._config.password,
            "appId": self._config.app_id,
            "appVersion": self._config.app_version,
            "cid": self._config.cid,
            "sec": self._config.sec,
        }
        result = self._post("/auth/accessToken", payload)
        token = result.get("accessToken")
        md = result.get("mdAccessToken")
        if not token:
            raise TradovateError(f"AUTH_FAILED:{result.get('errorText', 'no token')}")
        self._access_token = token
        self._md_token = md or token

    # ---- history ----
    def history_5m(self) -> Sequence[MarketBar]:
        """Fetch warm-up 5m bars via REST getChart.

        Implemented defensively: if the live account/entitlement is not ready
        this raises TradovateError so main() can fall back to replay mode.
        """
        if self._access_token is None:
            self.authenticate()
        # Contract lookup then chart request would go here using
        # /md/getChart with a MinuteBar/5 chartDescription. Kept minimal and
        # explicit so it can be adapted to your exact entitlement response.
        raise TradovateError(
            "LIVE_HISTORY_NOT_WIRED: provide CME data entitlement + confirm "
            "getChart response shape, or run with --replay for now"
        )

    def stream_5m(self) -> Iterator[MarketBar]:
        raise TradovateError(
            "LIVE_STREAM_NOT_WIRED: requires websockets + confirmed md entitlement; "
            "run with --replay until live data is verified"
        )

    @staticmethod
    def _parse_chart_bar(raw: dict) -> MarketBar:
        ts = raw.get("timestamp")
        if isinstance(ts, (int, float)):
            ts_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            ts_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return MarketBar(
            timestamp=ts_dt,
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw["volume"]) if raw.get("volume") is not None else None,
            bid_volume=float(raw["bidVolume"]) if raw.get("bidVolume") is not None else None,
            offer_volume=float(raw["offerVolume"]) if raw.get("offerVolume") is not None else None,
        )
