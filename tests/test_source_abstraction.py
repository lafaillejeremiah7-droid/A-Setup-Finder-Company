"""Tests for the source-abstraction layer (build-order step 2).

WHY THIS EXISTS
---------------
The engine consumes ONE normalized chain schema regardless of origin. Two origins
differ in the one way that matters most for GEX: CBOE carries open interest (true GEX);
the Kaggle CSV does not (volume proxy only). These tests pin that distinction so it
cannot regress into a silent OI=0, and verify both adapters key contracts on the full
(root,expiry,type,strike) identity (C7).

The CBOE tests run against the REAL committed fixture at
data/snapshots/2026-09-06/230612Z -- not a mock. The Kaggle test uses a tiny inline
fixture that reproduces the dataset's VERIFIED quirk: bracketed, space-padded headers.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from gammamap import adapters
from gammamap.chain import OI_ABSENT_VOLUME_PROXY


# ---------------------------------------------------------------------------
# Inline Kaggle fixture: the EXACT bracketed, space-padded header from
# qqq_2020_2022.csv, plus two hand-written data rows. No open-interest column.
# One data row -> one call + one put contract.
# ---------------------------------------------------------------------------
_KAGGLE_HEADER = (
    "[QUOTE_UNIXTIME], [QUOTE_READTIME], [QUOTE_DATE], [QUOTE_TIME_HOURS], "
    "[UNDERLYING_LAST], [EXPIRE_DATE], [EXPIRE_UNIX], [DTE], [C_DELTA], [C_GAMMA], "
    "[C_VEGA], [C_THETA], [C_RHO], [C_IV], [C_VOLUME], [C_LAST], [C_SIZE], [C_BID], "
    "[C_ASK], [STRIKE], [P_BID], [P_ASK], [P_SIZE], [P_LAST], [P_DELTA], [P_GAMMA], "
    "[P_VEGA], [P_THETA], [P_RHO], [P_IV], [P_VOLUME], [STRIKE_DISTANCE], "
    "[STRIKE_DISTANCE_PCT]"
)
# Two rows for 2022-01-03, expiry 2022-01-07, strikes 300 and 305.
_KAGGLE_ROW_1 = (
    "1641240000, 2022-01-03 16:00, 2022-01-03, 16.0, 302.5, 2022-01-07, 1641589200, "
    "4, 0.62, 0.041, 0.12, -0.08, 0.03, 0.19, 1500, 4.2, 10x10, 4.1, 4.3, 300, "
    "1.5, 1.7, 8x8, 1.6, -0.38, 0.040, 0.11, -0.07, -0.02, 0.20, 2200, 2.5, 0.008"
)
_KAGGLE_ROW_2 = (
    "1641240000, 2022-01-03 16:00, 2022-01-03, 16.0, 302.5, 2022-01-07, 1641589200, "
    "4, 0.41, 0.038, 0.13, -0.09, 0.02, 0.18, 900, 2.1, 5x5, 2.0, 2.2, 305, "
    "3.2, 3.4, 6x6, 3.3, -0.59, 0.037, 0.12, -0.08, -0.03, 0.21, 1100, 2.5, 0.008"
)


def _kaggle_csv() -> str:
    return "\n".join([_KAGGLE_HEADER, _KAGGLE_ROW_1, _KAGGLE_ROW_2]) + "\n"


# ---------------------------------------------------------------------------
# (a) CBOE distilled snapshot -> NormalizedChain with OI, exact contract count.
# ---------------------------------------------------------------------------
def test_distilled_qqq_yields_true_gex_chain(fixture_snapshot_dir: Path) -> None:
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "QQQ")

    # OI column is present -> true-GEX path.
    assert chain.oi_available is True
    assert adapters.is_true_gex(chain) is True

    # Contract count matches meta.json distilled rows_kept (7943 for QQQ).
    meta = json.loads((fixture_snapshot_dir / "meta.json").read_text())
    assert len(chain.contracts) == meta["distilled"]["QQQ"]["rows_kept"] == 7943

    # Provenance identifies the source and every greeks row found its OI counterpart.
    assert chain.provenance["source"] == "cboe_delayed"
    assert all(c.open_interest is not None for c in chain.contracts)

    # expiry is a real date; expiry_yymmdd preserves the original published token.
    sample = chain.contracts[0]
    assert isinstance(sample.expiry, date)
    assert sample.expiry.strftime("%y%m%d") == sample.expiry_yymmdd


def test_distilled_ndx_carries_both_roots_and_full_key(fixture_snapshot_dir: Path) -> None:
    chain = adapters.from_distilled_snapshot(fixture_snapshot_dir, "_NDX")

    roots = {c.root for c in chain.contracts}
    # C7: AM-settled NDX and PM-settled NDXP must both be present and distinct.
    assert "NDX" in roots and "NDXP" in roots

    meta = json.loads((fixture_snapshot_dir / "meta.json").read_text())
    counts: dict[str, int] = {}
    for c in chain.contracts:
        counts[c.root] = counts.get(c.root, 0) + 1
    assert counts == meta["distilled"]["_NDX"]["roots"]

    # The identity key is the full (root,expiry,type,strike) tuple and is unique: NDX
    # and NDXP rows that share (expiry,type,strike) stay distinct because root differs.
    by_key = chain.contracts_by_key()
    assert len(by_key) == len(chain.contracts)
    assert not any(f.startswith("chain_duplicate_key") for f in chain.quality_flags)
    # A key literally begins with the root, so both roots appear among the keys.
    key_roots = {k[0] for k in by_key}
    assert "NDX" in key_roots and "NDXP" in key_roots


# ---------------------------------------------------------------------------
# (b) Kaggle CSV -> call+put contracts, no OI, volume-proxy flag.
# ---------------------------------------------------------------------------
def test_kaggle_csv_parses_bracketed_header_into_call_and_put() -> None:
    chain = adapters.from_kaggle_csv(_kaggle_csv())

    # Two data rows, each split into a call and a put -> 4 contracts.
    assert len(chain.contracts) == 4
    calls = [c for c in chain.contracts if c.type == "C"]
    puts = [c for c in chain.contracts if c.type == "P"]
    assert len(calls) == 2 and len(puts) == 2

    # No open interest anywhere; the proxy flag is emitted and true-GEX is refused.
    assert chain.oi_available is False
    assert adapters.is_true_gex(chain) is False
    assert OI_ABSENT_VOLUME_PROXY in chain.quality_flags
    assert all(c.open_interest is None for c in chain.contracts)

    # Root is QQQ; expiry parsed from EXPIRE_DATE; C_*/P_* fields split correctly.
    strike_300_call = next(c for c in calls if c.strike == 300.0)
    assert strike_300_call.root == "QQQ"
    assert strike_300_call.expiry == date(2022, 1, 7)
    assert strike_300_call.expiry_yymmdd == "220107"
    assert strike_300_call.gamma == 0.041
    assert strike_300_call.iv == 0.19
    assert strike_300_call.volume == 1500

    strike_300_put = next(c for c in puts if c.strike == 300.0)
    assert strike_300_put.delta == -0.38
    assert strike_300_put.gamma == 0.040
    assert strike_300_put.volume == 2200

    # Chain-level asof + spot come from the first usable row.
    assert chain.spot_from_feed == 302.5


# ---------------------------------------------------------------------------
# (c) Identity key is (root,expiry,type,strike); duplicates are flagged.
# ---------------------------------------------------------------------------
def test_duplicate_keys_are_flagged_not_silently_merged() -> None:
    # Two identical rows -> the second call/put collide on the full key.
    csv_text = "\n".join([_KAGGLE_HEADER, _KAGGLE_ROW_1, _KAGGLE_ROW_1]) + "\n"
    chain = adapters.from_kaggle_csv(csv_text)

    by_key = chain.contracts_by_key()
    # Four raw contracts (2 rows x call+put) collapse to two distinct keys.
    assert len(by_key) == 2
    dup_flags = [f for f in chain.quality_flags if f.startswith("chain_duplicate_key")]
    # One collision for the duplicate call, one for the duplicate put.
    assert len(dup_flags) == 2
