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

test("NQ/MNQ source levels use the same point scale without needing an MNQ quote", () => {
  assert.equal(mapLevelToMnq(25200, { sourceUnderlying: "MNQ" }), 25200);
  assert.equal(mapLevelToMnq(25200, { sourceUnderlying: "NQ" }), 25200);
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

test("missing wall GEX stays missing instead of becoming zero", () => {
  assert.equal(classifyStrength([{ strike: 100, gex: 10 }], { strike: 100, gex: null }), "N/A");
});

test("derived mode returns one call line, one put line, and one flip line", () => {
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
  assert.equal(state.callWall.source, "DERIVED_CALL_LEVELS");
  assert.equal(state.putWall.strike, 24800);
  assert.equal(state.putWall.mnqLevel, 24842);
  assert.equal(state.gammaFlip.mnqLevel, 24992);
  assert.equal(state.basis, 42);
});

test("published wall mode uses provider wall directly instead of re-ranking arrays", () => {
  const state = buildGexState({
    timestamp: "2026-09-07T14:30:00Z",
    sourceUnderlying: "NDX",
    sourcePrice: 25000,
    mnqPrice: 25040,
    netGex: 1800000000,
    gammaFlip: { level: 24980 },
    callWall: { strike: 25250, gex: 900000000, strength: "strong" },
    putWall: 24850,
    putWallGex: -750000000,
    putWallStrength: "extreme",
    callLevels: [
      { strike: 25300, gex: 2000000000 },
      { strike: 25250, gex: 900000000 },
    ],
    putLevels: [
      { strike: 24850, gex: -750000000 },
      { strike: 24750, gex: -1000000000 },
    ],
  });

  assert.equal(state.regime, "POSITIVE");
  assert.equal(state.callWall.strike, 25250);
  assert.equal(state.callWall.mnqLevel, 25290);
  assert.equal(state.callWall.strength, "STRONG");
  assert.equal(state.callWall.source, "PUBLISHED_WALL");
  assert.equal(state.putWall.strike, 24850);
  assert.equal(state.putWall.mnqLevel, 24890);
  assert.equal(state.putWall.gex, -750000000);
  assert.equal(state.putWall.strength, "EXTREME");
  assert.equal(state.gammaFlip.mnqLevel, 25020);
});

test("NQ-native published walls plot directly on MNQ point scale", () => {
  const state = buildGexState({
    provider: "FLASHALPHA",
    sourceSymbol: "NQ=F",
    sourceUnderlying: "NQ",
    sourcePrice: 25142.25,
    mnqPrice: 25142.25,
    netGex: -1000000000,
    gammaFlip: 25077,
    callWall: { strike: 25250, gex: 800000000 },
    putWall: { strike: 24950, gex: 700000000 },
    callLevels: [{ strike: 25250, gex: 800000000 }],
    putLevels: [{ strike: 24950, gex: 700000000 }],
  });

  assert.equal(state.provider, "FLASHALPHA");
  assert.equal(state.sourceSymbol, "NQ=F");
  assert.equal(state.callWall.mnqLevel, 25250);
  assert.equal(state.putWall.mnqLevel, 24950);
  assert.equal(state.gammaFlip.mnqLevel, 25077);
  assert.equal(state.basis, null);
});
