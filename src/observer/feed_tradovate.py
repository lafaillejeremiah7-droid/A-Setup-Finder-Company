from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Iterator, Sequence

from src.market.normalize_tradovate_bar import MarketBar
from src.observer.config import TradovateConfig

MD_URL = "wss://md.tradovateapi.com/v1/websocket"  # chart data requires this URL


class TradovateError(RuntimeError):
    """Raised on Tradovate auth or market-data failures."""


class TradovateFeed:
    """Live/demo 5-minute feed from Tradovate, implemented against Tradovate's
    official WebSocket frame protocol (see tradovate/example-api-js).

    Flow:
      1. REST POST /auth/accesstokenrequest -> accessToken + mdAccessToken.
      2. REST GET  /contract/find?name=SYMBOL -> contractId.
      3. Connect MD websocket, send `authorize` frame with mdAccessToken.
      4. Send `md/getChart` for 5m bars; receive an initial history packet then
         streaming bar updates. Keep the socket alive with []-heartbeats.

    Frame protocol: each text message starts with a type char:
      'o' open, 'h' heartbeat, 'a' array-of-json-data, 'c' close.
    Requests are framed as: "<endpoint>\n<id>\n<query>\n<json-body>".
    Responses match on i==id and s==200.

    Order-flow note: chart bars carry OHLCV. True per-bar aggressor volume
    (for absorption) needs a CME order-flow entitlement; when absent, bars have
    no bid/offer volume and the engine's absorption detector fails closed (grade
    caps at A). We never fabricate it.
    """

    def __init__(self, config: TradovateConfig, symbol: str, warmup_bars: int = 400) -> None:
        if not config.is_complete:
            raise TradovateError("TRADOVATE_CREDENTIALS_INCOMPLETE")
        self._config = config
        self._symbol = symbol
        self._warmup_bars = warmup_bars
        self._access_token: str | None = None
        self._md_token: str | None = None
        self._contract_id: int | None = None
        self._history: list[MarketBar] = []
        self._queue: "Queue[MarketBar]" = Queue()
        self._ws = None
        self._counter = 0
        self._seen_ts: set[datetime] = set()

    # ---------- REST ----------
    def _rest(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        url = f"{self._config.rest_base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
        result = self._rest("POST", "/auth/accesstokenrequest", body=payload)
        if result.get("p-ticket"):
            raise TradovateError(
                f"AUTH_CAPTCHA_OR_PENALTY:wait {result.get('p-time')}s ({result.get('p-captcha')})"
            )
        token = result.get("accessToken")
        if not token:
            raise TradovateError(f"AUTH_FAILED:{result.get('errorText', result)}")
        self._access_token = token
        self._md_token = result.get("mdAccessToken") or token

    def _resolve_contract(self) -> None:
        found = self._rest("GET", "/contract/find", query={"name": self._symbol})
        cid = found.get("id") if isinstance(found, dict) else None
        if not cid:
            sugg = self._rest("GET", "/contract/suggest", query={"name": self._symbol, "l": 1})
            if isinstance(sugg, list) and sugg:
                cid = sugg[0].get("id")
        if not cid:
            raise TradovateError(f"CONTRACT_NOT_FOUND:{self._symbol}")
        self._contract_id = cid

    # ---------- WebSocket frame protocol ----------
    def _send_frame(self, url: str, body: dict | None) -> int:
        self._counter += 1
        frame = f"{url}\n{self._counter}\n\n{json.dumps(body) if body is not None else ''}"
        self._ws.send(frame)
        return self._counter

    def _open_socket(self) -> None:
        import websocket  # lazy import so replay mode needs no dependency

        if self._access_token is None:
            self.authenticate()
        if self._contract_id is None:
            self._resolve_contract()

        self._ws = websocket.create_connection(MD_URL, timeout=30)
        # First frame from server is 'o' (open); then we authorize.
        opened = self._ws.recv()
        if not opened.startswith("o"):
            raise TradovateError(f"UNEXPECTED_OPEN_FRAME:{opened[:40]}")
        self._ws.send(f"authorize\n{self._counter + 1}\n\n{self._md_token}")
        self._counter += 1
        # Drain until we see the authorize 200 response.
        self._await_status(self._counter)

    def _await_status(self, req_id: int, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._ws.recv()
            for item in self._parse(msg):
                if item.get("i") == req_id:
                    if item.get("s") == 200:
                        return item
                    raise TradovateError(f"WS_REQUEST_FAILED:{item.get('d')}")
        raise TradovateError("WS_RESPONSE_TIMEOUT")

    @staticmethod
    def _parse(msg: str) -> list[dict]:
        """Decode a Tradovate frame into a list of data dicts (may be empty)."""
        if not msg:
            return []
        kind, payload = msg[0], msg[1:]
        if kind == "a" and payload:
            try:
                arr = json.loads(payload)
                return arr if isinstance(arr, list) else []
            except json.JSONDecodeError:
                return []
        return []  # 'o', 'h', 'c' carry no data array

    # ---------- public feed API ----------
    def history_5m(self) -> Sequence[MarketBar]:
        """Open the socket, request the 5m chart, collect the initial history
        packet, and start a background reader that pushes new bars to a queue."""
        self._open_socket()
        chart_body = {
            "symbol": self._symbol,
            "chartDescription": {
                "underlyingType": "MinuteBar",
                "elementSize": 5,
                "elementSizeUnit": "UnderlyingUnits",
                "withHistogram": False,
            },
            "timeRange": {"asMuchAsElements": self._warmup_bars},
        }
        req_id = self._send_frame("md/getChart", chart_body)
        # Collect bars until the initial history burst settles.
        history: list[MarketBar] = []
        deadline = time.time() + 20.0
        got_response = False
        while time.time() < deadline:
            try:
                msg = self._ws.recv()
            except Exception as exc:  # noqa: BLE001
                raise TradovateError(f"WS_RECV_ERROR:{exc}") from exc
            for item in self._parse(msg):
                if item.get("i") == req_id and item.get("s") == 200:
                    got_response = True
                    continue
                for bar in self._bars_from_item(item):
                    if bar.timestamp not in self._seen_ts:
                        self._seen_ts.add(bar.timestamp)
                        history.append(bar)
            if got_response and history:
                # small settle window for the rest of the history burst
                deadline = min(deadline, time.time() + 2.0)
        if not got_response:
            raise TradovateError("NO_CHART_RESPONSE (check symbol + md entitlement)")
        history.sort(key=lambda b: b.timestamp)
        self._history = history
        self._start_reader()
        return history

    def _start_reader(self) -> None:
        def reader() -> None:
            last_hb = time.time()
            while True:
                try:
                    if time.time() - last_hb > 2.0:
                        self._ws.send("[]")  # heartbeat
                        last_hb = time.time()
                    msg = self._ws.recv()
                except Exception:  # noqa: BLE001
                    break
                for item in self._parse(msg):
                    for bar in self._bars_from_item(item):
                        if bar.timestamp not in self._seen_ts:
                            self._seen_ts.add(bar.timestamp)
                            self._queue.put(bar)

        threading.Thread(target=reader, daemon=True).start()

    def stream_5m(self) -> Iterator[MarketBar]:
        """Yield newly completed 5m bars as they arrive from the socket."""
        while True:
            try:
                yield self._queue.get(timeout=60)
            except Empty:
                continue

    # ---------- parsing ----------
    def _bars_from_item(self, item: dict) -> list[MarketBar]:
        d = item.get("d")
        if not isinstance(d, dict):
            return []
        charts = d.get("charts")
        if not isinstance(charts, list):
            return []
        bars: list[MarketBar] = []
        for chart in charts:
            for b in chart.get("bars", []) or []:
                bars.append(self._parse_chart_bar(b))
        return bars

    @staticmethod
    def _parse_chart_bar(raw: dict) -> MarketBar:
        ts = raw.get("timestamp")
        if isinstance(ts, (int, float)):
            ts_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            ts_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        # Tradovate bar keys: open/high/low/close/upVolume/downVolume/upTicks...
        up_vol = raw.get("upVolume")
        down_vol = raw.get("downVolume")
        total_vol = None
        if up_vol is not None or down_vol is not None:
            total_vol = (up_vol or 0) + (down_vol or 0)
        elif raw.get("volume") is not None:
            total_vol = float(raw["volume"])
        return MarketBar(
            timestamp=ts_dt,
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=total_vol,
            # upVolume = traded at ask (aggressive buy), downVolume = at bid (sell)
            offer_volume=float(up_vol) if up_vol is not None else None,
            bid_volume=float(down_vol) if down_vol is not None else None,
            up_volume=float(up_vol) if up_vol is not None else None,
            down_volume=float(down_vol) if down_vol is not None else None,
        )
