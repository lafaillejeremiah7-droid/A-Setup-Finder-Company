"""Generate a synthetic same-trading-day-expiry QQQ snapshot in the distilled format.

WHY THIS EXISTS
---------------
The only committed LIVE snapshot (data/snapshots/2026-09-06/230612Z) was captured over a
weekend, so its trading day (Fri 2026-09-04) precedes every listed expiry (earliest is Mon
2026-09-08) -- the Friday 0DTE had already expired and Monday was Labor Day. As a result
both 0DTE walls are empty on it and there is no committed example where the flagship 0DTE
walls and an informative strength band actually render (review issues #2 / #6).

This script writes a SMALL, SYNTHETIC snapshot in exactly the committed distilled format
(meta.json + greeks_qqq.csv.gz + oi/qqq_<digest>.csv.gz) whose quote timestamp falls on a
trading day that IS a listed expiry, so build_levels populates all four walls, a non-WEAK
0DTE strength band, and a gamma-flip line end to end. It is a hand-built fixture, clearly
marked synthetic in provenance, used only by the test suite -- never presented as real
market data.

The chain is intentionally minimal but structurally realistic:
  * an intraday quote timestamp of 2026-09-08T12:00:00 ET (a Tuesday session that IS the
    0DTE expiry 260908),
  * a broad Total universe across three expiries with a dominant call/put strike,
  * a 0DTE (260908) strip concentrated at a call strike and a put strike so both 0DTE
    walls populate with a MODERATE+ within-side share,
  * clean two-sided quotes + a per-contract IV so the parity forward and the spot-shifted
    gamma flip both resolve.

Run:  PYTHONPATH=src python scripts/make_0dte_fixture.py
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

# The fixture lives under a dedicated synthetic date so it is never confused with a real
# capture. 2026-09-08 is a Tuesday (a real trading day); its 0DTE expiry token is 260908.
_REPO = Path(__file__).resolve().parent.parent
_SNAP_DATE = "2026-09-08"
_SNAP_TIME = "120000Z"
_QUOTE_TS = "2026-09-08T12:00:00"           # Eastern wall-clock intraday, IS the 0DTE day
_CAPTURED_AT = "2026-09-08T16:00:05+00:00"  # capture instant (outer no-look-ahead bound)
_SPOT = 590.0                               # QQQ spot in dollars

# NQ futures quote (needed for the NDX->NQ basis leg). Chosen near the NDX-equivalent of
# the QQQ spot so the mapped MNQ prices land in a plausible range.
_NQ_PRICE = 24300.0
_NDX_SPOT = 24290.0                         # feed NDX cash (for the raw basis + ratio)


def _greek_row(root, expiry, typ, strike, gamma, delta, iv, bid, ask, volume, oi):
    return {
        "greeks": {
            "root": root, "expiry": expiry, "type": typ, "strike": f"{strike}",
            "gamma": f"{gamma}", "delta": f"{delta}", "iv": f"{iv}",
            "bid": f"{bid}", "ask": f"{ask}", "volume": f"{volume}",
        },
        "oi": {
            "root": root, "expiry": expiry, "type": typ, "strike": f"{strike}",
            "open_interest": f"{oi}",
        },
    }


# Per-expiry forward (QQQ dollars) implied by the synthetic quotes. Set slightly above
# spot so the parity regression recovers a clean, finite, positive forward (DF ~= 1) rather
# than tripping the parity_nonpositive_df / low_r2 guards. Quotes below are built to satisfy
# put-call parity EXACTLY at these forwards: mid_call - mid_put = F - K.
_FORWARD = 590.5
_TIME_VALUE = 6.0  # a common per-leg time premium so both legs stay two-sided and positive


def _parity_quotes(strike: float, forward: float, tv: float):
    """Two-sided (call, put) mids that satisfy C - P = F - K exactly, plus a tight spread.

    call_mid = max(F-K,0) + tv ; put_mid = max(K-F,0) + tv  ->  call_mid - put_mid = F - K.
    Returns ((c_bid,c_ask),(p_bid,p_ask)) with a 0.10 spread around each mid.
    """
    call_mid = max(forward - strike, 0.0) + tv
    put_mid = max(strike - forward, 0.0) + tv
    return (
        (round(call_mid - 0.05, 2), round(call_mid + 0.05, 2)),
        (round(put_mid - 0.05, 2), round(put_mid + 0.05, 2)),
    )


def _build_rows():
    """Build the synthetic (greeks, oi) rows.

    OI is concentrated so a single call and a single put strike dominate each side of both
    the Total and the 0DTE universe -- enough to clear the within-side MODERATE share floor
    without being a degenerate single-strike chain. The call/put OI is deliberately
    ASYMMETRIC so each universe's Net GEX carries a definite sign (a non-'0' regime).
    """
    rows = []
    iv = 0.20  # near-money IV so bs_gamma (and the spot-shifted flip) resolve

    # ---- 0DTE strip (expiry == quote day, 260908). PM-settled QQQ is live intraday. ----
    # Dominant 0DTE call at 595, dominant 0DTE put at 585; call side heavier overall so the
    # 0DTE Net GEX is net POSITIVE (a definite regime sign, not 0).
    dte = "260908"
    dte_specs = [
        # strike, call_oi, put_oi
        (580.0, 300, 700),
        (585.0, 400, 4000),   # dominant 0DTE put
        (590.0, 1500, 1500),  # ATM
        (595.0, 8000, 400),   # dominant 0DTE call (call-heavy -> Net GEX +)
        (600.0, 1200, 300),
    ]
    for k, coi, poi in dte_specs:
        dist = abs(k - _SPOT)
        gamma = round(max(0.0005, 0.02 - dist * 0.0016), 4)
        (cb, ca), (pb, pa) = _parity_quotes(k, _FORWARD, _TIME_VALUE)
        rows.append(_greek_row("QQQ", dte, "C", k, gamma, 0.5, iv, cb, ca, 500, coi))
        rows.append(_greek_row("QQQ", dte, "P", k, gamma, -0.5, iv, pb, pa, 500, poi))

    # ---- Broader Total universe: two later weekly expiries with their own dominant walls. --
    for exp in ("260911", "260918"):
        total_specs = [
            (575.0, 500, 1000),
            (580.0, 800, 8000),   # dominant put wall (Total)
            (590.0, 2000, 2000),
            (600.0, 11000, 800),  # dominant call wall (Total; call-heavy -> Net GEX +)
            (610.0, 1400, 500),
        ]
        for k, coi, poi in total_specs:
            dist = abs(k - _SPOT)
            gamma = round(max(0.0004, 0.015 - dist * 0.0010), 4)
            (cb, ca), (pb, pa) = _parity_quotes(k, _FORWARD, _TIME_VALUE)
            rows.append(_greek_row("QQQ", exp, "C", k, gamma, 0.5, iv, cb, ca, 300, coi))
            rows.append(_greek_row("QQQ", exp, "P", k, gamma, -0.5, iv, pb, pa, 300, poi))
    return rows


def _write_gz_csv(path: Path, fieldnames, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    data = buf.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(data)
    return data


def main():
    rows = _build_rows()
    greeks = [r["greeks"] for r in rows]
    oi = [r["oi"] for r in rows]

    snap_dir = _REPO / "data" / "snapshots" / _SNAP_DATE / _SNAP_TIME
    snapshots_root = _REPO / "data" / "snapshots"

    greeks_path = snap_dir / "greeks_qqq.csv.gz"
    _write_gz_csv(
        greeks_path,
        ["root", "expiry", "type", "strike", "gamma", "delta", "iv", "bid", "ask", "volume"],
        greeks,
    )

    # OI file is content-addressed by a digest of its rows (mirrors distill.py policy).
    oi_bytes = _write_gz_csv(
        snapshots_root / "oi" / "qqq_TMP.csv.gz",
        ["root", "expiry", "type", "strike", "open_interest"],
        oi,
    )
    digest = hashlib.sha1(oi_bytes).hexdigest()[:12]
    oi_rel = f"oi/qqq_{digest}.csv.gz"
    final_oi = snapshots_root / oi_rel
    (snapshots_root / "oi" / "qqq_TMP.csv.gz").rename(final_oi)

    meta = {
        "schema_version": "1.1.0",
        "captured_at": _CAPTURED_AT,
        "synthetic": True,
        "synthetic_note": (
            "Hand-built same-trading-day-expiry QQQ fixture for the 0DTE + strength "
            "end-to-end test (review issues #2/#6). NOT real market data."
        ),
        "chains": {
            "QQQ": {
                "symbol": "QQQ",
                "retrieved_at": _CAPTURED_AT,
                "feed_timestamp": "2026-09-08 12:00:00",
                "quote_timestamp": _QUOTE_TS,
                "spot": _SPOT,
                "contract_count": len(greeks),
                "raw_file": "chain_qqq.json.gz",
                "oi_freshness": "previous_settlement_unknown_date",
                "expiry_contract_counts": {},
            }
        },
        "quotes": {
            "NQ=F": {
                "symbol": "NQ=F", "retrieved_at": _CAPTURED_AT, "price": _NQ_PRICE,
                "currency": "USD", "exchange": "CME", "is_continuous_series": True,
            },
            "MNQ=F": {
                "symbol": "MNQ=F", "retrieved_at": _CAPTURED_AT, "price": _NQ_PRICE,
                "currency": "USD", "exchange": "CME", "is_continuous_series": True,
            },
            "^NDX": {
                "symbol": "^NDX", "retrieved_at": _CAPTURED_AT, "price": _NDX_SPOT,
                "currency": "USD", "exchange": "NIM", "is_continuous_series": False,
            },
        },
        "distilled": {
            "QQQ": {
                "greeks_file": "greeks_qqq.csv.gz",
                "oi_file": oi_rel,
                "oi_digest": digest,
                "rows_in": len(greeks),
                "rows_kept": len(greeks),
                "rows_dropped_no_exposure": 0,
                "rows_unparsed": 0,
                "dropped_oi_total": 0,
                "dropped_volume_total": 0,
                "lossless_for_gex": True,
                "roots": {"QQQ": len(greeks)},
                "duplicate_keys": 0,
            }
        },
        "derived": {
            "ndx_spot": _NDX_SPOT,
            "qqq_spot": _SPOT,
            "nq_price": _NQ_PRICE,
            "mnq_price": _NQ_PRICE,
            "basis": round(_NQ_PRICE - _NDX_SPOT, 4),
            "qqq_ndx_ratio": _SPOT / _NDX_SPOT,
            "nq_mnq_divergence": 0.0,
            "source_skew_seconds": 0.0,
            "quality_flags": ["nq_quote_is_continuous_front_month", "synthetic_fixture"],
        },
        "errors": {},
        "notes": {
            "mapping": "NDX strike --(+basis)--> NQ --(1:1)--> MNQ. Output renders on MNQ.",
            "primary_instrument": "QQQ",
            "purpose": "0DTE + strength end-to-end fixture (synthetic).",
        },
    }
    (snap_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {snap_dir}/meta.json")
    print(f"wrote {greeks_path}")
    print(f"wrote {final_oi}")


if __name__ == "__main__":
    main()
