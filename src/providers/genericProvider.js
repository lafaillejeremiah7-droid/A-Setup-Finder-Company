function demoPayload() {
  const now = new Date();
  return {
    timestamp: now.toISOString(),
    sourceUnderlying: "NDX",
    sourcePrice: 25100,
    mnqPrice: 25142.25,
    netGex: -2850000000,
    gammaFlip: 25035,
    gexUnit: "USD_PER_1PCT_MOVE",
    callWall: { strike: 25200, gex: -1780000000, strength: "EXTREME" },
    putWall: { strike: 24900, gex: 1460000000, strength: "STRONG" },
    callLevels: [
      { strike: 25200, gex: -1780000000 },
      { strike: 25300, gex: -920000000 },
      { strike: 25150, gex: -610000000 }
    ],
    putLevels: [
      { strike: 24900, gex: 1460000000 },
      { strike: 24800, gex: 700000000 },
      { strike: 25000, gex: 510000000 }
    ]
  };
}

export class GenericProvider {
  constructor(env = process.env) {
    this.url = env.GEX_SOURCE_URL || "";
    this.apiKey = env.GEX_API_KEY || "";
    this.authHeader = env.GEX_AUTH_HEADER || "Authorization";
    this.authPrefix = env.GEX_AUTH_PREFIX ?? "Bearer ";
    this.demo = String(env.DEMO_MODE ?? (!this.url)).toLowerCase() === "true";
  }

  async fetchSnapshot() {
    if (this.demo) return demoPayload();
    if (!this.url) throw new Error("GEX_SOURCE_URL is not configured");

    const headers = { Accept: "application/json" };
    if (this.apiKey) headers[this.authHeader] = `${this.authPrefix}${this.apiKey}`;

    const response = await fetch(this.url, {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) {
      throw new Error(`GEX provider returned HTTP ${response.status}`);
    }

    const payload = await response.json();
    this.validate(payload);
    return payload;
  }

  validate(payload) {
    const required = ["sourceUnderlying", "sourcePrice", "mnqPrice", "netGex", "gammaFlip"];
    for (const key of required) {
      if (payload[key] === undefined || payload[key] === null) {
        throw new Error(`Provider payload missing required field: ${key}`);
      }
    }

    const hasPublishedWalls = payload.callWall !== undefined && payload.putWall !== undefined;
    const hasLevelArrays = Array.isArray(payload.callLevels) && Array.isArray(payload.putLevels);

    if (!hasPublishedWalls && !hasLevelArrays) {
      throw new Error("Provider must supply callWall + putWall, or callLevels + putLevels");
    }
  }
}
