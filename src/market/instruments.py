from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    root: str
    role: str
    point_value: float | None = None
    min_tick: float | None = None
    tick_value: float | None = None


INSTRUMENTS = {
    "MGC": Instrument(
        root="MGC",
        role="TRADED_MARKET",
        point_value=10.0,
        min_tick=0.1,
        tick_value=1.0,
    ),
    "DX": Instrument(
        root="DX",
        role="DXY_FILTER",
    ),
}


def require_resolved_contract(contract: dict | None) -> dict:
    if not contract or not isinstance(contract.get("symbol"), str) or not contract["symbol"]:
        raise ValueError("ACTIVE_CONTRACT_UNRESOLVED")
    return contract
