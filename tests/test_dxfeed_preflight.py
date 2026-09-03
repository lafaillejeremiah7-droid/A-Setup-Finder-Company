from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "preflight_dxfeed_history.py"
spec = spec_from_file_location("preflight_dxfeed_history", SCRIPT)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_native_aggressor_side_accepts_only_native_field():
    assert module.native_aggressor_side({"aggressorSide": "BUY"}) == "BUY"
    assert module.native_aggressor_side({"AggressorSide": "SELL"}) == "SELL"
    assert module.native_aggressor_side({"aggressor_side": "B"}) == "BUY"
    assert module.native_aggressor_side({"aggressorSide": "S"}) == "SELL"


def test_native_aggressor_side_fails_closed_when_missing_or_unknown():
    assert module.native_aggressor_side({"bidPrice": 100, "askPrice": 101}) is None
    assert module.native_aggressor_side({"aggressorSide": "UNDEFINED"}) is None
    assert module.native_aggressor_side({"side": "BUY"}) is None


def test_events_handles_common_envelopes():
    event = {"eventSymbol": "/MGCZ15:XCEC", "aggressorSide": "BUY"}
    assert module._events([event]) == [event]
    assert module._events({"events": [event]}) == [event]
    assert module._events({"data": [event]}) == [event]
    assert module._events({"wrapper": {"TimeAndSale": [event]}}) == [event]
