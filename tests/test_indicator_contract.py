"""Contract test for the Tradovate JS indicator (build-order step 7, build-spec.md SS9a).

WHY THIS EXISTS (and why it is Python, not `node --test`)
---------------------------------------------------------
The indicator (tradovate/gamma_map_indicator.js) is a THIN renderer: it parses a levels
file and draws EXACTLY five lines (4 walls + gamma flip) plus one TOTAL/0DTE regime
readout, and nothing else. FEAT-006 asks for a `node --test` unit test of that pure render
model; node is NOT installed in this environment (confirmed: `node --version` -> command
not found), so per the feature's documented fallback we verify the SAME render contract
here in Python instead.

To avoid re-implementing the JS logic (which would test a fork, not the indicator), the
formatting rules live in ONE machine-checkable place: tradovate/render_spec.json. The JS
SECTION 1 `RENDER_SPEC` mirrors that JSON, and this test:

  1. loads render_spec.json and asserts the JS file references the same field names /
     formatting tokens (so the two cannot silently drift), then
  2. applies those exact rules to the REAL committed levels file
     (data/levels/2026-09-06/230612Z_QQQ.json, the FEAT-005 output) and asserts the render
     model the JS would build: line count (<=5, nulls skipped), each label formatted per
     the SS9a example, and the regime string.

The sample file has null 0DTE walls (its trading day predates every listed expiry), so it
exercises the skip rule for those two lines: the model contains the two real Total walls
plus the (now spot-repriced) gamma-flip line -- three lines -- never a faked line at a
guessed level. A populated four-wall example lives on the synthetic same-day fixture
(data/levels/2026-09-08/, see test_full_five_line_render and the levels tests).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRADOVATE = _REPO_ROOT / "tradovate"
_SPEC_PATH = _TRADOVATE / "render_spec.json"
_JS_PATH = _TRADOVATE / "gamma_map_indicator.js"
_LEVELS_PATH = _REPO_ROOT / "data" / "levels" / "2026-09-06" / "230612Z_QQQ.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(_SPEC_PATH.read_text())


@pytest.fixture(scope="module")
def levels() -> dict:
    return json.loads(_LEVELS_PATH.read_text())


@pytest.fixture(scope="module")
def js_source() -> str:
    return _JS_PATH.read_text()


# ---------------------------------------------------------------------------
# Pure render-model helpers -- a FAITHFUL port of the JS SECTION 2 functions, driven
# entirely by render_spec.json so there is no hidden second copy of the rules.
# ---------------------------------------------------------------------------
def _format_strike(mnq_price: float, spec: dict) -> str:
    n = round(mnq_price)
    return f"{n:,}".replace(",", spec["strike"]["thousandsSeparator"])


def _format_gex(gex_per_1pct: float, spec: dict) -> str:
    g = spec["gex"]
    value = (abs(gex_per_1pct) if g["absolute"] else gex_per_1pct) / g["divisor"]
    return f'{g["prefix"]}{value:.{g["decimals"]}f}{g["suffix"]}'


def _sign_word(sign: str, spec: dict) -> str:
    return spec["regime"]["signWords"].get(sign, spec["regime"]["signWords"]["0"])


def build_render_model(levels: dict, spec: dict) -> dict:
    """Port of buildRenderModel() in gamma_map_indicator.js SECTION 2."""
    is_proxy = bool(levels.get("provenance", {}).get("proxy"))
    by_key = {f'{w["side"]}.{w["universe"]}': w for w in levels.get("walls", [])}
    sep = spec["labelSeparator"]
    lines: list[dict] = []

    for line_spec in spec["lines"]:
        if line_spec["kind"] == "flip":
            flip = levels.get("gamma_flip") or {}
            if flip.get("mnq_price") is None:
                continue
            lines.append(
                {
                    "kind": "flip",
                    "price": flip["mnq_price"],
                    "text": line_spec["label"] + sep + _format_strike(flip["mnq_price"], spec),
                }
            )
            continue

        wall = by_key.get(f'{line_spec["side"]}.{line_spec["universe"]}')
        if not wall:
            continue
        if wall.get("mnq_price") is None:
            continue
        if not wall.get("gex_per_1pct"):
            continue
        fields = {
            "label": line_spec["label"],
            "strike": _format_strike(wall["mnq_price"], spec),
            "gex": _format_gex(wall["gex_per_1pct"], spec),
            "strength": wall["strength"],
        }
        text = sep.join(fields[f] for f in spec["wallLabelFields"])
        lines.append({"kind": "wall", "price": wall["mnq_price"], "text": text})

    regime = levels.get("regime", {})
    regime_text = (
        spec["regime"]["template"]
        .replace("{total}", _sign_word(regime.get("total_sign"), spec))
        .replace("{zerodte}", _sign_word(regime.get("zerodte_sign"), spec))
    )
    if regime.get("divergence"):
        regime_text += spec["regime"]["divergenceSuffix"]
    if is_proxy:
        regime_text += spec["regime"]["proxySuffix"]

    return {"lines": lines, "regime": regime_text, "proxy": is_proxy}


# ---------------------------------------------------------------------------
# (a) The JS and the JSON spec cannot silently drift.
# ---------------------------------------------------------------------------
def test_js_references_the_shared_spec_tokens(js_source: str, spec: dict) -> None:
    # The JS must consume the same schema, target, and formatting tokens the spec defines.
    assert spec["schemaVersion"] in js_source
    assert spec["renderTarget"] in js_source
    assert spec["gex"]["suffix"] in js_source
    assert spec["regime"]["template"] in js_source
    assert spec["regime"]["proxySuffix"] in js_source
    for band in spec["strengthBands"]:
        assert band in js_source
    for line in spec["lines"]:
        assert line["label"] in js_source


def _strip_js_comments(src: str) -> str:
    """Remove /* ... */ block comments and // line comments so we inspect only CODE.

    The file header legitimately NAMES the forbidden features to document that it does not
    draw them; that documentation must not trip the scope-boundary check below.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def test_js_is_thin_renderer_no_forbidden_draws(js_source: str) -> None:
    # Hard scope boundary (build-spec.md SS0/SS9a): the indicator must NOT compute or draw
    # OI / IV / greeks / Max Pain / full chain / distance-to-level / order flow / volume
    # profiles. Assert none of those appear in the executable CODE (comments stripped).
    lowered = _strip_js_comments(js_source).lower()
    for forbidden in (
        "maxpain",
        "max pain",
        "volume profile",
        "order flow",
        "orderflow",
        "distance-to-level",
        "openinterest",
    ):
        assert forbidden not in lowered, f"indicator must not reference {forbidden!r}"


# ---------------------------------------------------------------------------
# (b) The render model built from the REAL FEAT-005 levels file.
# ---------------------------------------------------------------------------
def test_line_count_never_exceeds_five(levels: dict, spec: dict) -> None:
    model = build_render_model(levels, spec)
    assert len(model["lines"]) <= spec["maxLines"] == 5


def test_sample_file_skips_null_walls_and_renders_flip(levels: dict, spec: dict) -> None:
    # The committed sample has null 0DTE walls (trading day predates every listed expiry)
    # -> those two lines are skipped. The two real Total walls render, and the gamma-flip
    # line now renders too (the spot-repriced flip finds a root; review issue #1). A null
    # level is skipped, never drawn at a fake price.
    model = build_render_model(levels, spec)
    assert len(model["lines"]) == 3
    labels = [ln["text"].split(spec["labelSeparator"])[0] for ln in model["lines"]]
    assert labels == ["CALL WALL", "GAMMA FLIP", "PUT WALL"]
    # No line has a null/None price.
    assert all(ln["price"] is not None for ln in model["lines"])


def test_wall_labels_formatted_per_ss9a(levels: dict, spec: dict) -> None:
    model = build_render_model(levels, spec)
    texts = {ln["text"].split(spec["labelSeparator"])[0]: ln["text"] for ln in model["lines"]}
    # Call Wall: mnq_price 30043.0957 -> "30,043"; gex 833359502.83 -> "$0.83B / 1%".
    # Strength is MODERATE under the side-specific share denominator (review issue #3).
    assert texts["CALL WALL"] == "CALL WALL   30,043   $0.83B / 1%   MODERATE"
    # Put Wall: mnq_price 28807.8001 -> "28,808"; gex -1399189590.25 -> "$1.40B / 1%".
    assert texts["PUT WALL"] == "PUT WALL   28,808   $1.40B / 1%   MODERATE"
    # The gamma-flip line renders (spot-repriced flip found a root; review issue #1).
    assert texts["GAMMA FLIP"] == "GAMMA FLIP   29,635"


def test_label_shape_matches_ss9a_regex(levels: dict, spec: dict) -> None:
    # Generic structural check: LABEL <strike> $<gex>B / 1% <STRENGTH>.
    pattern = re.compile(
        r"^.+?   [\d,]+   \$\d+\.\d{2}B / 1%   (WEAK|MODERATE|STRONG|EXTREME)$"
    )
    model = build_render_model(levels, spec)
    for ln in model["lines"]:
        if ln["kind"] == "wall":
            assert pattern.match(ln["text"]), ln["text"]


def test_regime_string(levels: dict, spec: dict) -> None:
    # Sample regime: total_sign '-', zerodte_sign '0', not proxy, no divergence.
    model = build_render_model(levels, spec)
    assert model["regime"] == "TOTAL: NEGATIVE   0DTE: NEUTRAL"
    assert model["proxy"] is False


# ---------------------------------------------------------------------------
# (c) Synthetic cases the sample file does not cover: a full 5-line render, proxy suffix,
#     divergence suffix, and the no-look-ahead replay selection.
# ---------------------------------------------------------------------------
def test_full_five_line_render(spec: dict) -> None:
    levels = {
        "schema_version": "levels-1.0.0",
        "provenance": {"proxy": False},
        "regime": {"total_sign": "-", "zerodte_sign": "-", "divergence": False},
        "gamma_flip": {"mnq_price": 25980.0, "neutral": False},
        "walls": [
            {"universe": "total", "side": "call", "mnq_price": 26200.0, "gex_per_1pct": 3.35e9, "strength": "EXTREME"},
            {"universe": "0dte", "side": "call", "mnq_price": 26150.0, "gex_per_1pct": 1.20e9, "strength": "STRONG"},
            {"universe": "0dte", "side": "put", "mnq_price": 25850.0, "gex_per_1pct": -0.92e9, "strength": "STRONG"},
            {"universe": "total", "side": "put", "mnq_price": 25800.0, "gex_per_1pct": -1.66e9, "strength": "EXTREME"},
        ],
    }
    model = build_render_model(levels, spec)
    assert len(model["lines"]) == 5  # 4 walls + flip
    texts = [ln["text"] for ln in model["lines"]]
    # Reproduces the SS9a example block exactly (top-to-bottom order).
    assert texts[0] == "CALL WALL   26,200   $3.35B / 1%   EXTREME"
    assert texts[1] == "0DTE CALL WALL   26,150   $1.20B / 1%   STRONG"
    assert texts[2] == "GAMMA FLIP   25,980"
    assert texts[3] == "0DTE PUT WALL   25,850   $0.92B / 1%   STRONG"
    assert texts[4] == "PUT WALL   25,800   $1.66B / 1%   EXTREME"
    assert model["regime"] == "TOTAL: NEGATIVE   0DTE: NEGATIVE"


def test_proxy_and_divergence_suffixes(spec: dict) -> None:
    levels = {
        "provenance": {"proxy": True},
        "regime": {"total_sign": "+", "zerodte_sign": "-", "divergence": True},
        "gamma_flip": {"mnq_price": None},
        "walls": [],
    }
    model = build_render_model(levels, spec)
    assert model["regime"] == "TOTAL: POSITIVE   0DTE: NEGATIVE   (DIVERGENCE)   (proxy)"
    assert model["lines"] == []  # nothing drawn: no walls, neutral flip


# ---------------------------------------------------------------------------
# (d) Replay no-look-ahead selection (port of selectLevelsForReplay in JS SECTION 3).
# ---------------------------------------------------------------------------
def _select_for_replay(manifest: list[dict], chart_iso: str) -> dict | None:
    from datetime import datetime

    def ts(s: str) -> float:
        return datetime.fromisoformat(s).timestamp()

    t = ts(chart_iso)
    best, best_t = None, float("-inf")
    for entry in manifest:
        et = ts(entry["asof"])
        if et <= t and et > best_t:
            best, best_t = entry, et
    return best


def test_replay_selects_latest_at_or_before_never_future() -> None:
    manifest = [
        {"asof": "2026-09-04T20:00:00+00:00", "url": "a"},
        {"asof": "2026-09-06T23:06:12+00:00", "url": "b"},
        {"asof": "2026-09-08T20:00:00+00:00", "url": "c"},
    ]
    # Replaying exactly at file b's asof selects b (<= is inclusive).
    assert _select_for_replay(manifest, "2026-09-06T23:06:12+00:00")["url"] == "b"
    # Replaying between b and c still selects b, NEVER the future file c.
    assert _select_for_replay(manifest, "2026-09-07T12:00:00+00:00")["url"] == "b"
    # Replaying before the first file selects nothing (draw nothing).
    assert _select_for_replay(manifest, "2026-09-01T00:00:00+00:00") is None
    # Replaying after the last file selects the last known file.
    assert _select_for_replay(manifest, "2026-12-01T00:00:00+00:00")["url"] == "c"
