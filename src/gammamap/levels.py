"""Levels writer for The Gamma Map (build-order step 6, build-spec.md SS9a / SS7).

WHY THIS EXISTS
---------------
This is the one module that turns a point-in-time snapshot into the small, versioned JSON
file the Tradovate indicator consumes. It orchestrates the whole pipeline in order:

    adapters (source abstraction)  ->  surface (parity forward, C9)  ->  gex (walls, flip,
    regime)  ->  mapping (index/ETF -> MNQ price, C2/C9)  ->  emit a deterministic dict.

Load-bearing rules, each traceable to the spec/findings:

  * The file carries ONLY what the indicator renders (build-spec.md SS9a: the 4 walls, the
    flip line, the TOTAL/0DTE regime, provenance/quality flags). OI, IV, greeks, MaxPain
    and the full chain are INGREDIENTS -- they are used to compute the levels and then
    deliberately NOT written. Leaking them would turn the render file into a data dump and
    tempt a consumer to recompute (and mis-compute) the walls client-side.

  * Basis is the C9-corrected parity basis (mapping.corrected_basis), and BOTH the raw
    feed basis and the corrected parity basis are RECORDED in provenance. We never
    silently rewrite the historical meta.json (capture.py's Derived.basis stays as-is);
    the correction is computed here at levels-build time.

  * Strict NO-LOOK-AHEAD (build-spec.md SS0 replay-correctness): build_levels consumes
    only data known as of the `asof` timestamp. An explicit guard asserts that `asof` is
    >= every source timestamp used (chain quote/feed times, quote retrieval times) and
    that no future snapshot leaks in. A violation raises LookAheadError -- a replay that
    silently used tomorrow's data is worse than one that fails loudly.

  * Deterministic serialization: json.dumps(sort_keys=True) so the same snapshot always
    produces byte-identical output (auditable, diffable, cacheable).

v1 path is QQQ (clean feed gamma, true OI from the distilled snapshot -> true-GEX). The
NDX path is wired the same way but uses the IV surface for gamma (C8); it is available but
QQQ is what v1 commits.

stdlib-first (json/gzip/datetime/pathlib) + the engine modules, dataclasses, quality flags
emitted not silently fixed -- matching surface.py / gex.py / mapping.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from . import mapping
from .adapters import from_distilled_snapshot, is_true_gex
from .chain import NormalizedChain
from .gex import (
    GEX_MODEL_VERSION,
    GEX_UNIT,
    SignModel,
    Wall,
    assess_regime,
    assess_strength,
    compute_strike_gex,
    feed_gamma_source,
    select_0dte_universe,
    select_total_universe,
    select_wall,
    solve_gamma_flip,
)
from .surface import SURFACE_MODEL_VERSION, forward_from_parity

# ---------------------------------------------------------------------------
# Versioning (build-spec.md SS7). The schema version is the file contract; the model
# versions pin the exact engines that produced the numbers so a historical level is fully
# reproducible.
# ---------------------------------------------------------------------------
LEVELS_SCHEMA_VERSION = "levels-1.0.0"

# Emitted on every historical Kaggle level: those dates have NO synchronized NQ futures
# quote, so the NDX->NQ basis (C2) cannot be measured. We map into QQQ/NDX-equivalent
# INDEX space and set mnq_price=None rather than fabricate a basis (build-spec.md SS2.1;
# FEAT-007 documented v1 limitation). A consumer that wants an MNQ price for a historical
# date must supply a dated basis itself; we never invent one.
BASIS_UNAVAILABLE_HISTORICAL = "basis_unavailable_no_synchronized_nq_quote"

# The four wall lines the indicator draws (build-spec.md SS9a). (universe, side, label).
_WALL_SPEC = (
    ("total", "call", "Call Wall"),
    ("total", "put", "Put Wall"),
    ("0dte", "call", "0DTE Call Wall"),
    ("0dte", "put", "0DTE Put Wall"),
)


class LookAheadError(AssertionError):
    """Raised when build_levels would consume a source timestamp later than `asof`.

    Replay correctness (build-spec.md SS0): a level built "as of" a point in time may only
    use data observed at or before that instant. A future-dated source means the snapshot
    leaked look-ahead, which invalidates any backtest -- so we fail loudly rather than
    emit a contaminated level.
    """


def _parse_ts(value: str | None) -> datetime | None:
    """Coerce an ISO timestamp to tz-aware UTC. Naive input is read as Eastern wall-clock.

    The snapshot quote timestamps are Eastern local (e.g. '2026-09-04T16:14:59') while the
    capture/retrieval stamps are UTC ('...+00:00'); normalizing both to UTC lets the
    no-look-ahead comparison be apples-to-apples.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T")) if "T" not in value and " " in value else datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Eastern wall-clock: attach the correct US Eastern offset for the date.
        dt = dt.replace(tzinfo=timezone(timedelta(hours=_et_offset_hours(dt.date()))))
    return dt.astimezone(timezone.utc)


def _et_offset_hours(d: date) -> int:
    """US Eastern UTC offset for a date (EDT -4 / EST -5), matching surface.py's clock."""
    year = d.year

    def _nth_sunday(month: int, n: int) -> date:
        first = date(year, month, 1)
        first_sunday = 1 + (6 - first.weekday()) % 7
        return date(year, month, first_sunday + 7 * (n - 1))

    return -4 if _nth_sunday(3, 2) <= d < _nth_sunday(11, 1) else -5


def _source_timestamps(chain: NormalizedChain, meta: dict[str, Any], symbol: str) -> dict[str, datetime]:
    """Collect every observation timestamp the level depends on, normalized to UTC.

    These are exactly the instants the no-look-ahead guard checks against `asof`: the
    option chain's quote/feed times and the NQ/MNQ quote retrieval times. Capture time is
    included as the outer bound (nothing in the snapshot was observed after it).
    """
    stamps: dict[str, datetime] = {}
    prov = chain.provenance
    for label, key in (("chain_quote", "quote_timestamp"), ("chain_feed", "feed_timestamp"), ("chain_retrieved", "retrieved_at")):
        ts = _parse_ts(prov.get(key))  # type: ignore[arg-type]
        if ts is not None:
            stamps[label] = ts
    # The futures quotes feed the basis; their retrieval time must also precede asof.
    for qsym, q in meta.get("quotes", {}).items():
        ts = _parse_ts(q.get("retrieved_at"))
        if ts is not None:
            stamps[f"quote:{qsym}"] = ts
    cap = _parse_ts(meta.get("captured_at"))
    if cap is not None:
        stamps["captured_at"] = cap
    return stamps


def _assert_no_look_ahead(asof: datetime, stamps: dict[str, datetime]) -> None:
    """Guard: `asof` must be >= every source timestamp used (build-spec.md SS0).

    Raises LookAheadError naming the offending source(s). A small tolerance is NOT applied
    -- a source dated even one second after asof means the snapshot contains information
    from the future relative to the replay instant, which is precisely what we forbid.
    """
    future = {name: ts for name, ts in stamps.items() if ts > asof}
    if future:
        detail = ", ".join(f"{n}={ts.isoformat()}" for n, ts in sorted(future.items()))
        raise LookAheadError(
            f"look-ahead: source timestamp(s) after asof={asof.isoformat()}: {detail}"
        )


@dataclass
class _WallLevel:
    """Intermediate: a wall mapped to MNQ price with its strength, before serialization."""

    label: str
    universe: str
    side: str
    strike: float | None
    mnq_price: float | None
    gex_per_1pct: float
    strength: str
    confidence: str
    global_share: float


def _map_strike_to_mnq(
    strike: float,
    *,
    instrument: str,
    basis_value: float,
    ratio: float | None,
) -> float:
    """Map one native strike to an MNQ price via the correct path for the instrument.

    QQQ routes strike -> NDX-equivalent (÷ratio) -> +basis -> 1:1 (build-spec.md SS2.1,
    the documented default). NDX routes strike -> +basis -> 1:1. Both end on the shared
    NQ/MNQ price level.
    """
    if instrument == "QQQ":
        assert ratio is not None, "QQQ mapping requires the live QQQ/NDX ratio"
        return mapping.map_qqq_to_mnq(strike, ratio, basis_value)
    return mapping.map_ndx_to_mnq(strike, basis_value)


def build_levels(
    snap_dir: Path | str,
    instrument: str = "QQQ",
    *,
    asof: str | datetime | None = None,
    sign_model: SignModel = SignModel.DEALER_SHORT_GAMMA,
) -> dict[str, Any]:
    """Build the versioned levels dict for one snapshot + instrument (build-spec.md SS9a).

    Pipeline: load the distilled chain (adapters) -> parity forward per the near-term
    expiry (surface, C9) -> per-strike GEX + walls + flip + regime (gex) -> map each
    wall/flip to MNQ price (mapping, C2/C9) -> assemble a deterministic dict.

    `instrument` is the meta.json chain key: 'QQQ' (v1 path, clean feed gamma) or '_NDX'.
    `asof` defaults to the chain's own quote timestamp; the no-look-ahead guard rejects any
    source timestamp later than it. Only the 4 walls, flip, regime and provenance/flags are
    emitted -- OI/IV/greeks/MaxPain/full-chain are ingredients and are NOT written (SS9a).
    """
    snap_dir = Path(snap_dir)
    meta = json.loads((snap_dir / "meta.json").read_text())
    symbol = instrument  # meta.json chain key ('QQQ' or '_NDX')

    chain = from_distilled_snapshot(snap_dir, symbol)

    # asof: the point-in-time this snapshot represents. Default to the snapshot's CAPTURE
    # time (`captured_at`), not the option chain's own quote time: the capture instant is
    # when the whole snapshot -- delayed option chain PLUS the live NQ/MNQ quotes that feed
    # the basis -- was assembled and known. The chain's quote timestamp is legitimately
    # EARLIER (a delayed feed), so using it as asof would (correctly) flag the live futures
    # quotes as "look-ahead". Capture time is the honest replay anchor; a caller replaying
    # a precise historical instant can still pass an explicit asof.
    if asof is not None:
        asof_dt = _parse_ts(str(asof))
    else:
        asof_dt = _parse_ts(meta.get("captured_at")) or _parse_ts(chain.asof)
    if asof_dt is None:
        raise ValueError("cannot determine asof: no explicit asof and snapshot has no timestamp")

    # NO-LOOK-AHEAD guard (build-spec.md SS0): fail loudly if any source postdates asof.
    stamps = _source_timestamps(chain, meta, symbol)
    _assert_no_look_ahead(asof_dt, stamps)

    quality_flags: list[str] = list(chain.quality_flags)

    # -- trading day + universes (C7 lives in gex.select_*). --------------------
    # The trading day is the calendar date of asof (Eastern session it belongs to). The
    # snapshot's quote timestamp is Eastern local, so its date is the trading day.
    trading_day = _asof_trading_day(chain.asof, asof_dt)
    total_contracts = select_total_universe(chain, trading_day)
    dte_contracts = select_0dte_universe(chain, trading_day)

    # -- spot + parity anchor (C9). --------------------------------------------
    feed_spot = chain.spot_from_feed or 0.0
    # Use the nearest-expiry parity forward of the primary root as the index anchor. For
    # QQQ the parity forward is in QQQ-dollar space; the NDX-equivalent anchor for basis is
    # derived from the NDX current_price via the ratio path, so basis is computed in NDX
    # space using the meta-recorded NDX spot + parity where available.
    anchor_forward, parity_r2, parity_flags = _index_anchor(chain, meta, symbol, trading_day)
    quality_flags.extend(parity_flags)

    # -- basis (C9 corrected) + raw (recorded only). ---------------------------
    nq_price = float(meta["quotes"]["NQ=F"]["price"])
    nq_continuous = bool(meta["quotes"]["NQ=F"].get("is_continuous_series", False))
    skew = meta.get("derived", {}).get("source_skew_seconds")
    corrected = mapping.corrected_basis(
        nq_price,
        anchor_forward,
        parity_r2=parity_r2,
        nq_is_continuous_front_month=nq_continuous,
        source_skew_seconds=skew,
    )
    ndx_feed_spot = float(meta.get("derived", {}).get("ndx_spot") or 0.0)
    raw = mapping.raw_feed_basis(nq_price, ndx_feed_spot)
    quality_flags.extend(corrected.quality_flags)

    # QQQ->NDX ratio, recomputed live from the snapshot spots (build-spec.md SS2.1).
    ratio = None
    if instrument == "QQQ":
        qqq_spot = float(meta.get("derived", {}).get("qqq_spot") or feed_spot)
        ratio = mapping.qqq_ndx_ratio(qqq_spot, ndx_feed_spot) if ndx_feed_spot else None

    # -- gamma source (C8): QQQ v1 uses clean feed gamma; NDX would use the surface. -----
    gamma_source = feed_gamma_source  # v1 QQQ path

    # -- per-strike GEX profiles for Total and 0DTE universes. ------------------
    oi_available = is_true_gex(chain)
    total_profile = compute_strike_gex(
        total_contracts, spot=feed_spot, gamma_source=gamma_source,
        oi_available=oi_available, universe="total", sign_model=sign_model,
    )
    dte_profile = compute_strike_gex(
        dte_contracts, spot=feed_spot, gamma_source=gamma_source,
        oi_available=oi_available, universe="0dte", sign_model=sign_model,
    )
    quality_flags.extend(f for f in total_profile.quality_flags if f not in quality_flags)
    quality_flags.extend(f for f in dte_profile.quality_flags if f not in quality_flags)

    # -- walls (SS9a: one line per side, mapped to MNQ). ------------------------
    profiles = {"total": total_profile, "0dte": dte_profile}
    wall_levels: list[_WallLevel] = []
    for universe, side, label in _WALL_SPEC:
        profile = profiles[universe]
        wall: Wall = select_wall(profile, side)
        strength = assess_strength(profile, wall, side)
        mnq_price = None
        if wall.strike is not None:
            mnq_price = round(
                _map_strike_to_mnq(
                    wall.strike, instrument=instrument, basis_value=corrected.basis, ratio=ratio
                ),
                4,
            )
        wall_levels.append(
            _WallLevel(
                label=label,
                universe=universe,
                side=side,
                strike=wall.strike,
                mnq_price=mnq_price,
                gex_per_1pct=round(wall.signed_gex, 2),
                strength=strength.band.value,
                confidence=strength.confidence,
                global_share=round(strength.global_share, 6),
            )
        )

    # -- gamma flip (SS5), mapped to MNQ. --------------------------------------
    flip_block = _build_flip(
        total_contracts, feed_spot, gamma_source, oi_available, sign_model,
        instrument=instrument, basis_value=corrected.basis, ratio=ratio,
    )

    # -- regime (SS6). ---------------------------------------------------------
    regime = assess_regime(total_profile, dte_profile)

    # -- assemble the emitted dict. ONLY render-facing fields (SS9a). ----------
    levels: dict[str, Any] = {
        "schema_version": LEVELS_SCHEMA_VERSION,
        "model_versions": {
            "surface": SURFACE_MODEL_VERSION,
            "gex": GEX_MODEL_VERSION,
            "mapping": mapping.MAPPING_MODEL_VERSION,
        },
        "asof": asof_dt.isoformat(),
        "instrument": instrument,
        "render_target": "MNQ",
        "gex_unit": GEX_UNIT,
        "walls": [
            {
                "label": w.label,
                "universe": w.universe,
                "side": w.side,
                "mnq_price": w.mnq_price,
                "gex_per_1pct": w.gex_per_1pct,
                "strength": w.strength,
                "confidence": w.confidence,
            }
            for w in wall_levels
        ],
        "gamma_flip": flip_block,
        "regime": {
            "total_sign": regime.total_sign,
            "zerodte_sign": regime.dte_sign,
            "divergence": regime.divergence,
        },
        "provenance": {
            "source": chain.provenance.get("source"),
            "oi_available": oi_available,
            "proxy": not oi_available,
            "quality_flags": sorted(set(quality_flags)),
            "basis_raw": round(raw.basis, 4),
            "basis_raw_anchor": raw.anchor_source,
            "basis_parity": round(corrected.basis, 4),
            "basis_parity_anchor": corrected.anchor_source,
            "basis_parity_r2": (round(parity_r2, 6) if parity_r2 is not None else None),
            "nq_price": nq_price,
            "qqq_ndx_ratio": (round(ratio, 10) if ratio is not None else None),
            "nq_quote_is_continuous_front_month": nq_continuous,
        },
    }
    return levels


def build_kaggle_levels(
    chain: NormalizedChain,
    quote_date: date,
    *,
    sign_model: SignModel = SignModel.DEALER_SHORT_GAMMA,
) -> dict[str, Any]:
    """Build a levels dict for one historical Kaggle QQQ EOD chain (FEAT-007 / build step 8).

    This is the historical-replay counterpart to build_levels. It runs the SAME engine
    (universes -> per-strike GEX -> walls -> flip -> regime) on a Kaggle-sourced
    NormalizedChain, but differs from the live path in two spec-mandated ways:

      * PROXY, always. The Kaggle CSV has no open-interest column, so GEX is built from the
        VOLUME proxy (gex._oi_or_proxy falls back to volume when oi_available is False).
        Every output carries proxy=True and the 'oi_absent_volume_proxy' flag and is never
        presented as classic OI-GEX (C1 / build-spec.md SS2, FEAT-007).

      * NO MNQ MAPPING. There is no synchronized NQ futures quote for a 2020-2022 date, so
        the NDX->NQ basis (C2) cannot be measured. We refuse to fabricate one: walls and
        the flip are emitted in the options' NATIVE QQQ index space (the 'native_level'
        field), mnq_price is None, and BASIS_UNAVAILABLE_HISTORICAL is flagged. The parity
        forward per the near expiry is still recorded as the honest index anchor. This is a
        documented v1 limitation (FEAT-007 step 3, build-spec.md SS2.1).

    `quote_date` is the EOD (16:00 ET) trading day the chain represents. asof is anchored
    to 16:00 ET of that date. QQQ v1 uses the clean feed gamma (C8 applies to NDX only).
    """
    quality_flags: list[str] = list(chain.quality_flags)
    # Historical dates have no synchronized futures quote -> no measurable basis (C2).
    if BASIS_UNAVAILABLE_HISTORICAL not in quality_flags:
        quality_flags.append(BASIS_UNAVAILABLE_HISTORICAL)

    root = str(chain.provenance.get("root") or "QQQ")
    feed_spot = chain.spot_from_feed or 0.0

    # asof = 16:00 America/New_York on the quote date (the EOD point-in-time this chain is).
    asof_dt = datetime(
        quote_date.year, quote_date.month, quote_date.day, 16, 0, 0,
        tzinfo=timezone(timedelta(hours=_et_offset_hours(quote_date))),
    ).astimezone(timezone.utc)

    # Universes (C7). For QQQ every listed contract that has not expired is live; the 0DTE
    # universe is the contracts expiring ON the quote date.
    total_contracts = select_total_universe(chain, quote_date)
    dte_contracts = select_0dte_universe(chain, quote_date)

    # Parity forward for the nearest still-relevant expiry: the honest index anchor for
    # this date, recorded even though we cannot turn it into an MNQ basis.
    anchor_forward: float | None = None
    parity_r2: float | None = None
    for yymmdd in _sorted_expiry_tokens(chain, root, quote_date):
        fwd = forward_from_parity(chain, root, yymmdd)
        if fwd.F == fwd.F and fwd.F > 0.0:  # finite + positive
            anchor_forward = fwd.F
            parity_r2 = fwd.r2
            break

    gamma_source = feed_gamma_source  # QQQ v1: clean feed gamma (C8 is NDX-only)
    oi_available = is_true_gex(chain)  # False for Kaggle -> volume proxy

    total_profile = compute_strike_gex(
        total_contracts, spot=feed_spot, gamma_source=gamma_source,
        oi_available=oi_available, universe="total", sign_model=sign_model,
    )
    dte_profile = compute_strike_gex(
        dte_contracts, spot=feed_spot, gamma_source=gamma_source,
        oi_available=oi_available, universe="0dte", sign_model=sign_model,
    )
    quality_flags.extend(f for f in total_profile.quality_flags if f not in quality_flags)
    quality_flags.extend(f for f in dte_profile.quality_flags if f not in quality_flags)

    profiles = {"total": total_profile, "0dte": dte_profile}
    wall_levels: list[_WallLevel] = []
    for universe, side, label in _WALL_SPEC:
        profile = profiles[universe]
        wall: Wall = select_wall(profile, side)
        strength = assess_strength(profile, wall, side)
        wall_levels.append(
            _WallLevel(
                label=label,
                universe=universe,
                side=side,
                strike=wall.strike,
                mnq_price=None,  # no synchronized NQ basis for historical dates (C2)
                gex_per_1pct=round(wall.signed_gex, 2),
                strength=strength.band.value,
                confidence=strength.confidence,
                global_share=round(strength.global_share, 6),
            )
        )

    # Gamma flip in native QQQ space (no MNQ mapping): solve the root, keep it native.
    flip_native = None
    flip_others: list[float] = []
    flip_neutral = True
    if total_contracts and feed_spot > 0.0:
        def gamma_at(contract, S):
            return gamma_source(contract, S)

        flip = solve_gamma_flip(
            total_contracts, gamma_at=gamma_at, oi_available=oi_available,
            grid_min=feed_spot * 0.90, grid_max=feed_spot * 1.10,
            reference_spot=feed_spot, sign_model=sign_model,
        )
        if flip.flip is not None:
            flip_native = round(flip.flip, 4)
            flip_others = [round(r, 4) for r in flip.all_roots if r != flip.flip]
            flip_neutral = False

    regime = assess_regime(total_profile, dte_profile)

    levels: dict[str, Any] = {
        "schema_version": LEVELS_SCHEMA_VERSION,
        "model_versions": {
            "surface": SURFACE_MODEL_VERSION,
            "gex": GEX_MODEL_VERSION,
            "mapping": mapping.MAPPING_MODEL_VERSION,
        },
        "asof": asof_dt.isoformat(),
        "quote_date": quote_date.isoformat(),
        "instrument": root,
        # Historical levels are emitted in the options' NATIVE index space, NOT MNQ: there
        # is no synchronized NQ basis to map through (see BASIS_UNAVAILABLE_HISTORICAL).
        "render_target": "QQQ_native",
        "gex_unit": GEX_UNIT,
        "walls": [
            {
                "label": w.label,
                "universe": w.universe,
                "side": w.side,
                "native_level": w.strike,  # QQQ index strike; NOT mapped to MNQ
                "mnq_price": w.mnq_price,   # always None for historical dates
                "gex_per_1pct": w.gex_per_1pct,
                "strength": w.strength,
                "confidence": w.confidence,
            }
            for w in wall_levels
        ],
        "gamma_flip": {
            "native_level": flip_native,  # QQQ index level; NOT mapped to MNQ
            "mnq_price": None,
            "neutral": flip_neutral,
            "other_roots": flip_others,
        },
        "regime": {
            "total_sign": regime.total_sign,
            "zerodte_sign": regime.dte_sign,
            "divergence": regime.divergence,
        },
        "provenance": {
            "source": chain.provenance.get("source"),
            "oi_available": oi_available,
            # PROXY is always True here: GEX came from the volume proxy, never OI.
            "proxy": not oi_available,
            "quality_flags": sorted(set(quality_flags)),
            "parity_forward": (round(anchor_forward, 4) if anchor_forward is not None else None),
            "parity_r2": (round(parity_r2, 6) if parity_r2 is not None else None),
            "feed_spot": (round(feed_spot, 4) if feed_spot else None),
            # No NQ quote existed for this date; recorded explicitly rather than fabricated.
            "nq_price": None,
            "basis_parity": None,
        },
    }
    return levels


def kaggle_levels_path(root: Path | str, quote_date: date, instrument: str = "qqq") -> Path:
    """Deterministic committed path for a historical Kaggle level: <root>/<date>_<inst>.json.

    build-spec.md SS7: a historical level lands at a stable, sortable, diffable location.
    Instrument is lowercased to match the FEAT-007 path convention (kaggle/<date>_qqq.json).
    """
    return Path(root) / f"{quote_date.isoformat()}_{instrument.lower()}.json"


def _asof_trading_day(chain_asof: str | None, asof_dt: datetime) -> date:
    """The trading day the levels belong to: the Eastern calendar date of the quote time.

    The quote timestamp in the snapshot is Eastern wall-clock, so its raw date is the
    session date. We take it directly from the string when present (avoiding a UTC-shift
    that could roll an after-noon Eastern time onto the next calendar day).
    """
    if chain_asof:
        try:
            return datetime.fromisoformat(chain_asof.replace(" ", "T")).date()
        except ValueError:
            pass
    return asof_dt.date()


def _index_anchor(
    chain: NormalizedChain, meta: dict[str, Any], symbol: str, trading_day: date
) -> tuple[float, float | None, list[str]]:
    """The parity-implied INDEX anchor for the basis (C9), in NDX index space.

    Basis lives in NDX space (NQ tracks the NDX cash index). For the NDX chain we take the
    nearest-expiry NDX/NDXP parity forward directly. For the QQQ chain, the options are in
    QQQ-dollar space, so the parity forward is in QQQ dollars; we convert it to an NDX-
    equivalent index level via the live QQQ/NDX ratio so the basis is computed against the
    options' OWN parity anchor rather than the feed's NDX current_price (still the C9 fix).

    Returns (anchor_in_ndx_space, parity_r2, quality_flags). Falls back to the feed NDX
    spot (flagged) only if no usable parity forward exists.
    """
    flags: list[str] = []
    ndx_feed_spot = float(meta.get("derived", {}).get("ndx_spot") or 0.0)

    # Choose the nearest still-relevant expiry for a well-conditioned parity fit: the
    # first expiry on/after the trading day with enough call/put pairs.
    root_for_symbol = "QQQ" if symbol == "QQQ" else "NDXP"
    expiry_tokens = _sorted_expiry_tokens(chain, root_for_symbol, trading_day)

    for yymmdd in expiry_tokens:
        fwd = forward_from_parity(chain, root_for_symbol, yymmdd)
        if fwd.F == fwd.F and fwd.F > 0.0:  # finite + positive
            if symbol == "QQQ":
                # Convert the QQQ-dollar forward to an NDX-equivalent index level using the
                # live QQQ/NDX ratio: NDXeq = F_qqq / r  (build-spec.md SS2.1).
                qqq_spot = float(meta.get("derived", {}).get("qqq_spot") or 0.0)
                if ndx_feed_spot > 0.0 and qqq_spot > 0.0:
                    r = qqq_spot / ndx_feed_spot
                    return fwd.F / r, fwd.r2, flags
                # No ratio available: cannot express in NDX space.
                break
            return fwd.F, fwd.r2, flags

    # Fallback: feed NDX spot. Flagged so the level is never mistaken for a C9-clean basis.
    flags.append(mapping.MAPPING_PROXY_ANCHOR)
    return ndx_feed_spot, None, flags


def _sorted_expiry_tokens(chain: NormalizedChain, root: str, trading_day: date) -> list[str]:
    """Expiry yymmdd tokens for `root`, nearest-to-trading-day first (on/after preferred)."""
    tokens = sorted({c.expiry_yymmdd for c in chain.contracts if c.root == root and c.expiry >= trading_day})
    if tokens:
        return tokens
    # Nothing on/after: fall back to any expiry for the root (still deterministic).
    return sorted({c.expiry_yymmdd for c in chain.contracts if c.root == root})


def _build_flip(
    total_contracts: list,
    feed_spot: float,
    gamma_source,
    oi_available: bool,
    sign_model: SignModel,
    *,
    instrument: str,
    basis_value: float,
    ratio: float | None,
) -> dict[str, Any]:
    """Solve the gamma flip on a spot grid around the feed spot and map it to MNQ (SS5).

    v1 QQQ uses a spot-shifted feed gamma (gamma held per-contract, S^2 repriced): the
    feed gamma source ignores the hypothetical spot, so the flip reflects the S^2 term and
    the OI-weighted net inventory. `neutral` records that no root was found (a monotone
    net-GEX curve). `other_roots` lists any additional crossings mapped to MNQ.
    """
    if not total_contracts or feed_spot <= 0.0:
        return {"mnq_price": None, "neutral": True, "other_roots": []}

    def gamma_at(contract, S):  # feed gamma is per-contract; S enters only via S^2 (SS5).
        return gamma_source(contract, S)

    lo, hi = feed_spot * 0.90, feed_spot * 1.10
    flip = solve_gamma_flip(
        total_contracts, gamma_at=gamma_at, oi_available=oi_available,
        grid_min=lo, grid_max=hi, reference_spot=feed_spot, sign_model=sign_model,
    )

    def _to_mnq(level: float) -> float:
        return round(_map_strike_to_mnq(level, instrument=instrument, basis_value=basis_value, ratio=ratio), 4)

    if flip.flip is None:
        return {"mnq_price": None, "neutral": True, "other_roots": []}
    others = [_to_mnq(r) for r in flip.all_roots if r != flip.flip]
    return {
        "mnq_price": _to_mnq(flip.flip),
        "neutral": False,
        "other_roots": others,
    }


# ---------------------------------------------------------------------------
# Deterministic write to the stable path (build-spec.md SS7).
# ---------------------------------------------------------------------------
def levels_path(root: Path | str, asof_iso: str, instrument: str) -> Path:
    """The stable output path: <root>/<date>/<time>_<instrument>.json.

    Derived from the asof instant (UTC) so a replay writes to a deterministic, sortable
    location. Time uses HHMMSSZ to match the snapshot directory convention.
    """
    dt = datetime.fromisoformat(asof_iso)
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return Path(root) / dt.strftime("%Y-%m-%d") / f"{dt.strftime('%H%M%SZ')}_{instrument}.json"


def write_levels(levels: dict[str, Any], root: Path | str = "data/levels") -> Path:
    """Serialize `levels` deterministically (sorted keys) and write to the stable path.

    sort_keys=True guarantees byte-identical output for identical inputs, so the file is
    diffable and cacheable and a replay is reproducible (build-spec.md SS7). Returns the
    written path.
    """
    path = levels_path(root, levels["asof"], levels["instrument"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(levels, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_levels(path: Path | str) -> dict[str, Any]:
    """Read a levels file back (the round-trip counterpart to write_levels)."""
    return json.loads(Path(path).read_text())
