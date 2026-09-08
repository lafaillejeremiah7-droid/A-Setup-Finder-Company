import test from "node:test";
import assert from "node:assert/strict";
import {
  buildGexState,
  classifyStrength,
  gammaRegime,
  mapLevelToMnq,
  strongestLevel,
} from "../src/gexEngine.js";

test("strongestLevel selects largest absolute GEX", () => {
  const wall = strongestLevel([
    { strike: 100, gex: 10 },
    { strike: 110, gex: -50 },
    { strike: 120, gex: 30 },
  ]);
  assert.deepEqual(wall, { strike: 110, gex: -50 });
});

test("NDX levels map to MNQ with synchronized basis", () => {
  const mapped = mapLevelToMnq(25200, {
    sourceUnderlying: "NDX",
    sourcePrice: 25000,
    mnqPrice: 25042.25,
  });
  assert.equal(mapped, 25242.25);
});

test("NQ/MNQ source levels do not need basis conversion", () => {
  assert.equal(mapLevelToMnq(25200, { sourceUnderlying: "MNQ", mnqPrice: 25100 }), 25200);
  assert.equal(mapLevelToMnq(25200, { sourceUnderlying: "NQ", mnqPrice: 25100 }), 25200);
});

test("QQQ mapping normalizes to NDX then applies MNQ basis", () => {
  const mapped = mapLevelToMnq(504, {
    sourceUnderlying: "QQQ",
    sourcePrice: 500,
    ndxPrice: 25000,
    mnqPrice: 25040,
  });
  assert.equal(mapped, 25240);
});

test("gamma regime preserves sign", () => {
  assert.equal(gammaRegime(1), "POSITIVE");
  assert.equal(gammaRegime(-1), "NEGATIVE");
  assert.equal(gammaRegime(0), "NEUTRAL");
  assert.equal(gammaRegime(undefined), "UNKNOWN");
});

test("strength is concentration, not a probability", () => {
  const levels = [
    { strike: 100, gex: 40 },
    { strike: 110, gex: 30 },
    { strike: 120, gex: 20 },
    { strike: 130, gex: 10 },
  ];
  assert.equal(classifyStrength(levels, levels[0]), "EXTREME");
});

test("buildGexState returns one call line, one put line, and one flip line", () => {
  const state = buildGexState({
    timestamp: "2026-09-07T14:30:00Z",
    sourceUnderlying: "NDX",
    sourcePrice: 25000,
    mnqPrice: 25042,
    netGex: -2000000000,
    gammaFlip: 24950,
    callLevels: [
      { strike: 25200, gex: -1500000000 },
      { strike: 25300, gex: -500000000 },
    ],
    putLevels: [
      { strike: 24800, gex: 1200000000 },
      { strike: 24700, gex: 400000000 },
    ],
  });

  assert.equal(state.regime, "NEGATIVE");
  assert.equal(state.callWall.strike, 25200);
  assert.equal(state.callWall.mnqLevel, 25242);
  assert.equal(state.putWall.strike, 24800);
  assert.equal(state.putWall.mnqLevel, 24842);
  assert.equal(state.gammaFlip.mnqLevel, 24992);
  assert.equal(state.basis, 42);
});
