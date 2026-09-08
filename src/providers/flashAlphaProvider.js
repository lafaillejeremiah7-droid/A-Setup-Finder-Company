const DEFAULT_BASE_URL = "https://lab.flashalpha.com";

function finiteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function latestTimestamp(...values) {
  const valid = values
    .filter(Boolean)
    .map((value) => new Date(value))
    .filter((date) => Number.isFinite(date.getTime()))
    .sort((a, b) => b.getTime() - a.getTime());
  return valid[0]?.toISOString() || new Date().toISOString();
}

function wallGexAtStrike(strikes, strike, field) {
  const k = finiteNumber(strike);
  if (k === null || !Array.isArray(strikes)) return null;
  const row = strikes.find((item) => finiteNumber(item?.strike) === k);
  return finiteNumber(row?.[field]);
}

export function normalizeFlashAlphaSnapshot(levels, gex) {
  if (!levels || typeof levels !== "object") throw new Error("FlashAlpha levels payload is missing");
  if (!gex || typeof gex !== "object") throw new Error("FlashAlpha GEX payload is missing");

  const underlyingPrice = finiteNumber(levels.underlying_price ?? gex.underlying_price);
  const netGex = finiteNumber(gex.live_net_gex);
  const gammaFlip = finiteNumber(levels.live_gamma_flip ?? gex.live_gamma_flip);
  const callWallStrike = finiteNumber(levels.live_call_wall);
  const putWallStrike = finiteNumber(levels.live_put_wall);
  const strikes = Array.isArray(gex.strikes) ? gex.strikes : [];

  if (underlyingPrice === null) throw new Error("FlashAlpha payload missing underlying_price");
  if (netGex === null) throw new Error("FlashAlpha payload missing live_net_gex");

  const callLevels = strikes
    .map((item) => ({ strike: finiteNumber(item?.strike), gex: finiteNumber(item?.call_gex) }))
    .filter((item) => item.strike !== null && item.gex !== null);

  const putLevels = strikes
    .map((item) => ({ strike: finiteNumber(item?.strike), gex: finiteNumber(item?.put_gex) }))
    .filter((item) => item.strike !== null && item.gex !== null);

  const callWall = callWallStrike === null
    ? null
    : {
        strike: callWallStrike,
        gex: wallGexAtStrike(strikes, callWallStrike, "call_gex"),
      };

  const putWall = putWallStrike === null
    ? null
    : {
        strike: putWallStrike,
        gex: wallGexAtStrike(strikes, putWallStrike, "put_gex"),
      };

  return {
    timestamp: latestTimestamp(levels.as_of, gex.as_of),
    provider: "FLASHALPHA",
    sourceSymbol: String(levels.symbol || gex.symbol || "NQ=F"),
    // FlashAlpha documents CME futures support as NQ=F. NQ and MNQ use the
    // same Nasdaq-100 index-point scale, so wall/flip strikes plot directly on MNQ.
    sourceUnderlying: "NQ",
    sourcePrice: underlyingPrice,
    mnqPrice: underlyingPrice,
    netGex,
    gammaFlip,
    gexUnit: "USD_PER_1PCT_MOVE",
    callWall,
    putWall,
    callLevels,
    putLevels,
  };
}

function demoSnapshot() {
  return {
    timestamp: new Date().toISOString(),
    provider: "FLASHALPHA_DEMO",
    sourceSymbol: "NQ=F",
    sourceUnderlying: "NQ",
    sourcePrice: 25142.25,
    mnqPrice: 25142.25,
    netGex: -2850000000,
    gammaFlip: 25077,
    gexUnit: "USD_PER_1PCT_MOVE",
    callWall: { strike: 25250, gex: 1780000000 },
    putWall: { strike: 24950, gex: 1460000000 },
    callLevels: [
      { strike: 25250, gex: 1780000000 },
      { strike: 25300, gex: 920000000 },
      { strike: 25150, gex: 610000000 },
    ],
    putLevels: [
      { strike: 24950, gex: 1460000000 },
      { strike: 24850, gex: 700000000 },
      { strike: 25000, gex: 510000000 },
    ],
  };
}

export class FlashAlphaProvider {
  constructor(env = process.env, fetchImpl = globalThis.fetch) {
    this.fetch = fetchImpl;
    this.baseUrl = (env.FLASHALPHA_BASE_URL || DEFAULT_BASE_URL).replace(/\/$/, "");
    this.apiKey = env.FLASHALPHA_API_KEY || env.GEX_API_KEY || "";
    this.symbol = env.FLASHALPHA_SYMBOL || "NQ=F";
    this.expiry = env.FLASHALPHA_EXPIRY || "";
    this.polarity = String(env.FLASHALPHA_POLARITY || "convention").toLowerCase();
    this.gexRefreshMs = Math.max(15000, Number(env.FLASHALPHA_GEX_REFRESH_MS || 300000));
    this.demo = String(env.DEMO_MODE ?? (!this.apiKey)).toLowerCase() === "true";
    this.cachedGex = null;
    this.cachedGexAt = 0;

    if (!["convention", "flow"].includes(this.polarity)) {
      throw new Error("FLASHALPHA_POLARITY must be 'convention' or 'flow'");
    }
    if (typeof this.fetch !== "function") throw new Error("fetch is unavailable");
  }

  buildUrl(endpoint) {
    const url = new URL(`${this.baseUrl}/v1/flow/${endpoint}/${encodeURIComponent(this.symbol)}`);
    if (this.expiry) url.searchParams.set("expiry", this.expiry);
    if (this.polarity !== "convention") url.searchParams.set("polarity", this.polarity);
    return url.toString();
  }

  async request(endpoint) {
    if (!this.apiKey) throw new Error("FLASHALPHA_API_KEY is not configured");
    const response = await this.fetch(this.buildUrl(endpoint), {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-Api-Key": this.apiKey,
      },
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`FlashAlpha ${endpoint} returned HTTP ${response.status}${body ? `: ${body.slice(0, 180)}` : ""}`);
    }
    return response.json();
  }

  async fetchSnapshot() {
    if (this.demo) return demoSnapshot();

    const now = Date.now();
    const needGex = !this.cachedGex || now - this.cachedGexAt >= this.gexRefreshMs;

    const levelsPromise = this.request("levels");
    let gex = this.cachedGex;

    if (needGex) {
      const [levels, freshGex] = await Promise.all([levelsPromise, this.request("gex")]);
      this.cachedGex = freshGex;
      this.cachedGexAt = Date.now();
      return normalizeFlashAlphaSnapshot(levels, freshGex);
    }

    const levels = await levelsPromise;
    return normalizeFlashAlphaSnapshot(levels, gex);
  }
}
