#!/usr/bin/env python3
"""Preflight dxFeed historical TimeAndSale before paying for / downloading years of data.

Purpose
-------
This script answers one question with a tiny request:

    Does the user's dxFeed entitlement return real historical TimeAndSale records
    for the requested MGC / DX contract AND expose a native aggressor side?

It deliberately does NOT infer aggressor side from price or BBO. If the feed does
not expose a native aggressor-side field, the check fails closed.

Examples
--------
# Token auth (preferred when provided by dxFeed)
export DXFEED_TOKEN='...'
python scripts/preflight_dxfeed_history.py \
  --symbol '/MGCZ15:XCEC' --from-time 2015-11-02T14:30:00Z \
  --to-time 2015-11-02T14:31:00Z

python scripts/preflight_dxfeed_history.py \
  --symbol '/DXZ15:IFUS' --from-time 2015-11-02T14:30:00Z \
  --to-time 2015-11-02T14:31:00Z

# Basic auth if the account uses username/password instead
export DXFEED_USER='...'
export DXFEED_PASSWORD='...'

Security
--------
Never commit credentials. Environment variables only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

import requests

DEFAULT_ENDPOINT = "https://tools.dxfeed.com/webservice/rest/events.json"


def _events(payload: Any) -> list[dict[str, Any]]:
    """Extract event dicts from the common dxFeed REST response shapes."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    # The REST service has used a few envelope shapes across versions.
    for key in ("events", "data", "TimeAndSale", "timeAndSale"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    # Some responses group by event type in nested dicts.
    for value in payload.values():
        if isinstance(value, dict):
            nested = _events(value)
            if nested:
                return nested
        if isinstance(value, list):
            dicts = [x for x in value if isinstance(x, dict)]
            if dicts:
                return dicts
    return []


def _first_present(event: dict[str, Any], names: Iterable[str]) -> Any:
    lowered = {str(k).lower(): v for k, v in event.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def native_aggressor_side(event: dict[str, Any]) -> str | None:
    """Return normalized BUY/SELL only from a native aggressor-side field."""
    value = _first_present(
        event,
        (
            "aggressorSide",
            "aggressor_side",
            "AggressorSide",
        ),
    )
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"BUY", "B", "BUYER"}:
        return "BUY"
    if text in {"SELL", "S", "SELLER"}:
        return "SELL"
    return None


def request_sample(
    *, endpoint: str, symbol: str, from_time: str, to_time: str, timeout: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {
        "events": "TimeAndSale",
        "symbols": symbol,
        "fromTime": from_time,
        "toTime": to_time,
        "timeout": str(timeout),
    }
    headers = {"Accept": "application/json"}
    auth = None

    token = os.environ.get("DXFEED_TOKEN")
    user = os.environ.get("DXFEED_USER")
    password = os.environ.get("DXFEED_PASSWORD")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif user and password:
        auth = (user, password)
    else:
        raise SystemExit(
            "Set DXFEED_TOKEN or both DXFEED_USER and DXFEED_PASSWORD. "
            "Do not paste credentials into source code."
        )

    response = requests.get(
        endpoint,
        params=params,
        headers=headers,
        auth=auth,
        timeout=timeout + 10,
    )
    meta = {
        "status_code": response.status_code,
        "url_without_credentials": response.url,
        "content_type": response.headers.get("content-type"),
    }
    if response.status_code != 200:
        body = response.text[:1000]
        raise RuntimeError(
            f"dxFeed HTTP {response.status_code}. Response excerpt: {body}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "dxFeed response was not JSON; this endpoint/account may require a "
            "different historical service URL supplied by dxFeed."
        ) from exc
    return _events(payload), meta


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from-time", required=True)
    parser.add_argument("--to-time", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    events, meta = request_sample(
        endpoint=args.endpoint,
        symbol=args.symbol,
        from_time=args.from_time,
        to_time=args.to_time,
        timeout=args.timeout,
    )

    if not events:
        print(json.dumps({**meta, "symbol": args.symbol, "events": 0}, indent=2))
        print(
            "FAIL: no historical TimeAndSale records returned. This can mean the "
            "symbol is wrong, the requested period has no trades, or the account "
            "is not entitled to that historical market.",
            file=sys.stderr,
        )
        return 2

    sides = [native_aggressor_side(event) for event in events]
    classified = sum(side in {"BUY", "SELL"} for side in sides)
    fields = sorted({str(k) for event in events for k in event.keys()})

    report = {
        **meta,
        "symbol": args.symbol,
        "events": len(events),
        "native_aggressor_side_classified": classified,
        "native_aggressor_side_fraction": classified / len(events),
        "fields": fields,
        "sample": events[:3],
    }
    print(json.dumps(report, indent=2, default=str))

    if classified != len(events):
        print(
            "FAIL CLOSED: at least one returned TimeAndSale record does not expose "
            "a usable native aggressor side. Do not use this sample for MGC "
            "absorption and do not infer side from BBO/price.",
            file=sys.stderr,
        )
        return 3

    print(
        "PASS: every returned TimeAndSale record in this sample has a usable native "
        "BUY/SELL aggressor side. Safe to proceed to a larger entitlement/coverage "
        "test before a full historical download."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
