"""End-to-end tests for the Kaggle historical backfill (build-order step 8, FEAT-007).

WHY THIS EXISTS
---------------
The backfill turns a historical Kaggle QQQ EOD chain into a small per-date levels file so
the user can click a replay date and still see GEX levels. Two properties are load-bearing
and pinned here so they cannot regress:

  * PROXY, ALWAYS. The Kaggle CSV has no open-interest column, so GEX is a VOLUME proxy;
    every levels file must carry proxy=True and the 'oi_absent_volume_proxy' flag and must
    never be presented as classic OI-GEX (C1 / build-spec.md SS2).

  * NO FABRICATED BASIS. Historical dates have no synchronized NQ futures quote, so the
    NDX->NQ basis (C2) is not measurable. Levels are emitted in native QQQ index space
    (native_level), mnq_price is None, and the missing basis is flagged, never invented.

These run entirely on a SMALL inline CSV fixture with the dataset's VERIFIED bracketed,
space-padded header. The 628MB dataset is NEVER downloaded here (that is what
scripts/backfill_kaggle.py does in production).
"""

from __future__ import annotations

from datetime import date

from gammamap import adapters
from gammamap.chain import OI_ABSENT_VOLUME_PROXY
from gammamap.levels import (
    BASIS_UNAVAILABLE_HISTORICAL,
    LEVELS_SCHEMA_VERSION,
    build_kaggle_levels,
)


# ---------------------------------------------------------------------------
# Inline Kaggle fixture: the EXACT bracketed, space-padded header from
# qqq_2020_2022.csv (matching tests/test_source_abstraction.py), plus a handful of
# hand-written rows for a single quote date (2022-01-03) and expiry (2022-01-07). No
# open-interest column. One data row -> one call + one put contract.
#
# Volume is concentrated at the 300 strike (call) and 305 strike (put) so a clear wall is
# selected on each side and the volume-proxy weighting is exercised.
# ---------------------------------------------------------------------------
_HEADER = (
    "[QUOTE_UNIXTIME], [QUOTE_READTIME], [QUOTE_DATE], [QUOTE_TIME_HOURS], "
    "[UNDERLYING_LAST], [EXPIRE_DATE], [EXPIRE_UNIX], [DTE], [C_DELTA], [C_GAMMA], "
    "[C_VEGA], [C_THETA], [C_RHO], [C_IV], [C_VOLUME], [C_LAST], [C_SIZE], [C_BID], "
    "[C_ASK], [STRIKE], [P_BID], [P_ASK], [P_SIZE], [P_LAST], [P_DELTA], [P_GAMMA], "
    "[P_VEGA], [P_THETA], [P_RHO], [P_IV], [P_VOLUME], [STRIKE_DISTANCE], "
    "[STRIKE_DISTANCE_PCT]"
)


def _row(strike: float, c_gamma: float, c_vol: int, p_gamma: float, p_vol: int) -> str:
    """One Kaggle-format data row for 2022-01-03, expiry 2022-01-07, given strike/greeks.

    Bid/ask bracket a plausible mid so put-call parity has clean pairs to fit a forward.
    """
    c_bid, c_ask = 4.1, 4.3
    p_bid, p_ask = 1.5, 1.7
    return (
        f"1641240000, 2022-01-03 16:00, 2022-01-03, 16.0, 302.5, 2022-01-07, 1641589200, "
        f"4, 0.55, {c_gamma}, 0.12, -0.08, 0.03, 0.19, {c_vol}, 4.2, 10x10, {c_bid}, {c_ask}, "
        f"{strike}, {p_bid}, {p_ask}, 8x8, 1.6, -0.45, {p_gamma}, 0.11, -0.07, -0.02, 0.20, "
        f"{p_vol}, 2.5, 0.008"
    )


def _kaggle_csv() -> str:
    rows = [
        # strike, C_GAMMA, C_VOLUME, P_GAMMA, P_VOLUME
        _row(295.0, 0.030, 200, 0.031, 300),
        _row(300.0, 0.041, 9000, 0.040, 500),   # dominant CALL volume -> call wall
        _row(305.0, 0.038, 400, 0.037, 8000),   # dominant PUT volume  -> put wall
        _row(310.0, 0.028, 150, 0.029, 250),
    ]
    return "\n".join([_HEADER, *rows]) + "\n"


_QUOTE_DATE = date(2022, 1, 3)


def test_backfill_end_to_end_is_volume_proxy() -> None:
    """Adapter + engine on the inline fixture -> a proxy levels dict with the OI flags."""
    chain = adapters.from_kaggle_csv(_kaggle_csv())
    # Precondition: the source truly has no OI, so this is the proxy path.
    assert chain.oi_available is False
    assert adapters.is_true_gex(chain) is False

    levels = build_kaggle_levels(chain, _QUOTE_DATE)

    assert levels["schema_version"] == LEVELS_SCHEMA_VERSION
    assert levels["quote_date"] == "2022-01-03"
    assert levels["instrument"] == "QQQ"

    prov = levels["provenance"]
    # PROXY must be set on every historical output, and the volume-proxy flag present.
    assert prov["proxy"] is True
    assert prov["oi_available"] is False
    assert OI_ABSENT_VOLUME_PROXY in prov["quality_flags"]


def test_backfill_selects_walls_in_native_space_without_mnq() -> None:
    """A wall is selected on each side, in native QQQ space, with NO fabricated MNQ price."""
    chain = adapters.from_kaggle_csv(_kaggle_csv())
    levels = build_kaggle_levels(chain, _QUOTE_DATE)

    walls = {w["label"]: w for w in levels["walls"]}
    # All four wall lines are present (build-spec.md SS9a).
    assert set(walls) == {"Call Wall", "Put Wall", "0DTE Call Wall", "0DTE Put Wall"}

    call_wall = walls["Call Wall"]
    put_wall = walls["Put Wall"]
    # A wall is actually selected (native level is set), in QQQ strike space.
    assert call_wall["native_level"] is not None
    assert put_wall["native_level"] is not None
    # Dominant volume placed the call wall at 300 and the put wall at 305.
    assert call_wall["native_level"] == 300.0
    assert put_wall["native_level"] == 305.0

    # NO MNQ mapping for historical dates: mnq_price is None on every wall and on the flip.
    assert all(w["mnq_price"] is None for w in levels["walls"])
    assert levels["gamma_flip"]["mnq_price"] is None
    assert levels["render_target"] == "QQQ_native"


def test_backfill_flags_missing_basis_not_fabricated() -> None:
    """The missing NQ basis is flagged explicitly and never invented (C2 / FEAT-007)."""
    chain = adapters.from_kaggle_csv(_kaggle_csv())
    levels = build_kaggle_levels(chain, _QUOTE_DATE)

    prov = levels["provenance"]
    assert BASIS_UNAVAILABLE_HISTORICAL in prov["quality_flags"]
    # No basis and no NQ quote are recorded (they did not exist), not fabricated.
    assert prov["basis_parity"] is None
    assert prov["nq_price"] is None


def test_backfill_output_is_deterministic() -> None:
    """The same inline chain produces byte-identical levels (build-spec.md SS7)."""
    import json

    a = build_kaggle_levels(adapters.from_kaggle_csv(_kaggle_csv()), _QUOTE_DATE)
    b = build_kaggle_levels(adapters.from_kaggle_csv(_kaggle_csv()), _QUOTE_DATE)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
