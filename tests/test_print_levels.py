"""Tests for scripts/print_levels.py -- the human-readable levels printer.

WHY THESE TESTS
---------------
print_levels.format_levels is the render-facing display of a Gamma Map levels file
(build-spec.md SS9a). It must:

  * show the four walls by their emitted label with MNQ price + strength band,
  * show the Gamma Flip MNQ price,
  * show the TOTAL/0DTE regime with a divergence note,
  * render a null wall/flip price as "n/a" instead of crashing,
  * warn when the levels came from a volume PROXY source (provenance.proxy == true),
  * NOT leak ingredient fields (OI/IV/greeks).

We feed synthetic dicts that match the exact schema gammamap.levels emits (walls list,
gamma_flip block, regime block, provenance) so the printer is exercised against the real
contract, not a mock shape.

scripts/ is not a package, so we load print_levels.py by file path via importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "print_levels.py"
# Ensure the in-repo gammamap resolves for the script's `from gammamap.levels import ...`.
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_print_levels():
    spec = importlib.util.spec_from_file_location("print_levels_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


print_levels = _load_print_levels()


def _live_levels() -> dict:
    """A true-GEX (non-proxy) MNQ levels dict, with one wall priced null."""
    return {
        "schema_version": "levels-1.0.0",
        "asof": "2026-09-06T23:06:12+00:00",
        "instrument": "QQQ",
        "render_target": "MNQ",
        "gex_unit": "usd_delta_per_1pct_move",
        "walls": [
            {"label": "Call Wall", "universe": "total", "side": "call",
             "mnq_price": 30043.0957, "gex_per_1pct": 8.3e8, "strength": "high", "confidence": "high"},
            {"label": "Put Wall", "universe": "total", "side": "put",
             "mnq_price": 28807.8001, "gex_per_1pct": -1.3e9, "strength": "med", "confidence": "high"},
            {"label": "0DTE Call Wall", "universe": "0dte", "side": "call",
             "mnq_price": None, "gex_per_1pct": 0.0, "strength": "low", "confidence": "high"},
            {"label": "0DTE Put Wall", "universe": "0dte", "side": "put",
             "mnq_price": 29500.25, "gex_per_1pct": -1.0e7, "strength": "low", "confidence": "high"},
        ],
        "gamma_flip": {"mnq_price": 29634.6692, "neutral": False, "other_roots": []},
        "regime": {"total_sign": "-", "zerodte_sign": "0", "divergence": False},
        "provenance": {"source": "cboe_delayed", "oi_available": True, "proxy": False,
                       "quality_flags": []},
    }


def _proxy_levels() -> dict:
    """A volume-proxy (Kaggle historical) levels dict: proxy=true, all mnq_price null."""
    return {
        "schema_version": "levels-1.0.0",
        "asof": "2021-01-04T21:00:00+00:00",
        "instrument": "QQQ",
        "render_target": "QQQ_native",
        "walls": [
            {"label": "Call Wall", "universe": "total", "side": "call",
             "native_level": 320.0, "mnq_price": None, "gex_per_1pct": 5.0e7,
             "strength": "high", "confidence": "high"},
            {"label": "Put Wall", "universe": "total", "side": "put",
             "native_level": 300.0, "mnq_price": None, "gex_per_1pct": -6.0e7,
             "strength": "med", "confidence": "high"},
            {"label": "0DTE Call Wall", "universe": "0dte", "side": "call",
             "native_level": None, "mnq_price": None, "gex_per_1pct": 0.0,
             "strength": "low", "confidence": "high"},
            {"label": "0DTE Put Wall", "universe": "0dte", "side": "put",
             "native_level": None, "mnq_price": None, "gex_per_1pct": 0.0,
             "strength": "low", "confidence": "high"},
        ],
        "gamma_flip": {"native_level": 310.0, "mnq_price": None, "neutral": False,
                       "other_roots": []},
        "regime": {"total_sign": "+", "zerodte_sign": "-", "divergence": True},
        "provenance": {"source": "kaggle_qqq_eod", "oi_available": False, "proxy": True,
                       "quality_flags": ["oi_absent_volume_proxy"]},
    }


def test_format_shows_all_five_labels():
    out = print_levels.format_levels(_live_levels())
    for label in ("Call Wall", "Put Wall", "0DTE Call Wall", "0DTE Put Wall", "Gamma Flip"):
        assert label in out


def test_format_shows_header_fields():
    out = print_levels.format_levels(_live_levels())
    assert "QQQ" in out
    assert "MNQ" in out
    assert "2026-09-06T23:06:12+00:00" in out


def test_format_shows_prices_and_strength_bands():
    out = print_levels.format_levels(_live_levels())
    # Prices are thousands-formatted with two decimals.
    assert "30,043.10" in out  # Call Wall
    assert "28,807.80" in out  # Put Wall
    assert "29,634.67" in out  # Gamma Flip
    # Strength bands are normalized to LOW/MED/HIGH.
    assert "[HIGH]" in out
    assert "[MED]" in out
    assert "[LOW]" in out


def test_format_null_wall_price_is_na_not_crash():
    out = print_levels.format_levels(_live_levels())
    # The 0DTE Call Wall has mnq_price=None -> must render n/a, not raise.
    assert "n/a" in out


def test_format_regime_line():
    out = print_levels.format_levels(_live_levels())
    assert "TOTAL -" in out
    assert "0DTE 0" in out
    assert "aligned" in out  # divergence False


def test_format_regime_divergence():
    out = print_levels.format_levels(_proxy_levels())
    assert "TOTAL +" in out
    assert "0DTE -" in out
    assert "DIVERGE" in out  # divergence True


def test_format_proxy_warning():
    out = print_levels.format_levels(_proxy_levels())
    assert "PROXY" in out.upper()
    assert "not true GEX" in out


def test_format_no_proxy_warning_for_true_gex():
    out = print_levels.format_levels(_live_levels())
    assert "WARNING" not in out
    assert "not true GEX" not in out


def test_format_does_not_leak_ingredient_fields():
    out = print_levels.format_levels(_live_levels())
    # Only render-facing numbers: no raw gex_per_1pct dump, OI, IV or greeks.
    for leaked in ("gex_per_1pct", "833359502", "open_interest", "iv", "greeks"):
        assert leaked not in out


def test_proxy_all_null_prices_render_na():
    out = print_levels.format_levels(_proxy_levels())
    # Every wall + flip is null on the historical proxy path.
    assert out.count("n/a") >= 5


def test_main_reads_file_and_prints(tmp_path, capsys):
    import json
    path = tmp_path / "levels.json"
    path.write_text(json.dumps(_live_levels()))
    rc = print_levels.main([str(path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "GAMMA MAP LEVELS" in captured.out
    assert "Call Wall" in captured.out


def test_main_missing_file_returns_error(tmp_path, capsys):
    rc = print_levels.main([str(tmp_path / "does_not_exist.json")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no such levels file" in captured.err
