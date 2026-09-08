import test from "node:test";
import assert from "node:assert/strict";
import {
  FlashAlphaProvider,
  normalizeFlashAlphaSnapshot,
} from "../src/providers/flashAlphaProvider.js";

const levels = {
  symbol: "NQ=F",
  as_of: "2026-09-08T14:30:00Z",
  underlying_price: 25142.25,
  live_gamma_flip: 25077,
  live_call_wall: 25250,
  live_put_wall: 24950,
};

const gex = {
  symbol: "NQ=F",
  as_of: "2026-09-08T14:29:55Z",
  underlying_price: 25142.0,
  live_net_gex: -2850000000,
  live_net_gex_label: "negative",
  live_gamma_flip: 25076,
  strikes: [
    { strike: 25250, call_gex: 1780000000, put_gex: 120000000, net_gex: 1660000000 },
    { strike: 25300, call_gex: 920000000, put_gex: 90000000, net_gex: 830000000 },
    { strike: 24950, call_gex: 110000000, put_gex: 1460000000, net_gex: -1350000000 },
    { strike: 24850, call_gex: 80000000, put_gex: 700000000, net_gex: -620000000 },
  ],
};

test("FlashAlpha normalization uses published NQ walls and GEX at those strikes", () => {
  const snapshot = normalizeFlashAlphaSnapshot(levels, gex);

  assert.equal(snapshot.provider, "FLASHALPHA");
  assert.equal(snapshot.sourceSymbol, "NQ=F");
  assert.equal(snapshot.sourceUnderlying, "NQ");
  assert.equal(snapshot.sourcePrice, 25142.25);
  assert.equal(snapshot.mnqPrice, 25142.25);
  assert.equal(snapshot.netGex, -2850000000);
  assert.equal(snapshot.gammaFlip, 25077);
  assert.deepEqual(snapshot.callWall, { strike: 25250, gex: 1780000000 });
  assert.deepEqual(snapshot.putWall, { strike: 24950, gex: 1460000000 });
  assert.equal(snapshot.callLevels.length, 4);
  assert.equal(snapshot.putLevels.length, 4);
});

test("FlashAlpha normalization tolerates a null published wall and keeps strike arrays for fallback", () => {
  const snapshot = normalizeFlashAlphaSnapshot(
    { ...levels, live_call_wall: null },
    gex,
  );

  assert.equal(snapshot.callWall, null);
  assert.equal(snapshot.callLevels[0].strike, 25250);
  assert.equal(snapshot.callLevels[0].gex, 1780000000);
});

test("FlashAlpha provider calls encoded NQ flow endpoints with X-Api-Key and caches full GEX", async () => {
  const calls = [];
  const fakeFetch = async (url, options) => {
    calls.push({ url, options });
    const isLevels = url.includes("/levels/");
    return {
      ok: true,
      status: 200,
      async json() {
        return isLevels ? levels : gex;
      },
      async text() {
        return "";
      },
    };
  };

  const provider = new FlashAlphaProvider(
    {
      DEMO_MODE: "false",
      FLASHALPHA_API_KEY: "test-key",
      FLASHALPHA_SYMBOL: "NQ=F",
      FLASHALPHA_GEX_REFRESH_MS: "300000",
    },
    fakeFetch,
  );

  const first = await provider.fetchSnapshot();
  const second = await provider.fetchSnapshot();

  assert.equal(first.callWall.strike, 25250);
  assert.equal(second.putWall.strike, 24950);
  assert.equal(calls.length, 3, "first refresh should call levels+gex; second should call levels only");
  assert.ok(calls[0].url.includes("NQ%3DF"));
  assert.equal(calls[0].options.headers["X-Api-Key"], "test-key");
  assert.equal(calls.filter((call) => call.url.includes("/gex/")).length, 1);
  assert.equal(calls.filter((call) => call.url.includes("/levels/")).length, 2);
});
