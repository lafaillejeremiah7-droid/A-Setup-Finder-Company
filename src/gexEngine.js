const DEFAULT_THRESHOLDS = Object.freeze({
  extreme: 0.35,
  strong: 0.22,
  moderate: 0.12,
});

export function classifyStrength(levels, wall, thresholds = DEFAULT_THRESHOLDS) {
  if (!wall || !Array.isArray(levels) || levels.length === 0) return "N/A";

  const totalAbs = levels.reduce((sum, item) => sum + Math.abs(Number(item.gex) || 0), 0);
  if (totalAbs <= 0 || !Number.isFinite(Number(wall.gex))) return "N/A";

  const share = Math.abs(Number(wall.gex)) / totalAbs;
  if (share >= thresholds.extreme) return "EXTREME";
  if (share >= thresholds.strong) return "STRONG";
  if (share >= thresholds.moderate) return "MODERATE";
  return "WEAK";
}

export function strongestLevel(levels = []) {
  const valid = levels
    .map((item) => ({ strike: Number(item.strike), gex: Number(item.gex) }))
    .filter((item) => Number.isFinite(item.strike) && Number.isFinite(item.gex));

  if (valid.length === 0) return null;
  return valid.reduce((best, item) =>
    Math.abs(item.gex) > Math.abs(best.gex) ? item : best
  );
}

export function gammaRegime(netGex) {
  const value = Number(netGex);
  if (!Number.isFinite(value)) return "UNKNOWN";
  if (value > 0) return "POSITIVE";
  if (value < 0) return "NEGATIVE";
  return "NEUTRAL";
}

export function mapLevelToMnq(level, market) {
  const sourceLevel = Number(level);
  if (!Number.isFinite(sourceLevel)) return null;

  const source = String(market.sourceUnderlying || "NDX").toUpperCase();
  const mnqPrice = Number(market.mnqPrice);

  if (!Number.isFinite(mnqPrice)) {
    throw new Error("mnqPrice is required for mapping");
  }

  if (source === "MNQ" || source === "NQ") return sourceLevel;

  if (source === "NDX") {
    const ndxPrice = Number(market.sourcePrice ?? market.ndxPrice);
    if (!Number.isFinite(ndxPrice)) throw new Error("NDX sourcePrice is required");
    return sourceLevel + (mnqPrice - ndxPrice);
  }

  if (source === "QQQ") {
    const qqqPrice = Number(market.sourcePrice ?? market.qqqPrice);
    const ndxPrice = Number(market.ndxPrice);
    if (!Number.isFinite(qqqPrice) || !Number.isFinite(ndxPrice) || qqqPrice === 0) {
      throw new Error("QQQ mapping requires sourcePrice/qqqPrice and ndxPrice");
    }
    const qqqToNdxRatio = qqqPrice / ndxPrice;
    const ndxEquivalent = sourceLevel / qqqToNdxRatio;
    return ndxEquivalent + (mnqPrice - ndxPrice);
  }

  throw new Error(`Unsupported sourceUnderlying: ${source}`);
}

function normalizeDirectWall(value) {
  if (value === undefined || value === null) return null;

  if (typeof value === "number" || typeof value === "string") {
    const strike = Number(value);
    return Number.isFinite(strike) ? { strike, gex: null, strength: null } : null;
  }

  if (typeof value === "object") {
    const strike = Number(value.strike ?? value.level);
    if (!Number.isFinite(strike)) return null;
    const gex = Number(value.gex);
    return {
      strike,
      gex: Number.isFinite(gex) ? gex : null,
      strength: typeof value.strength === "string" ? value.strength.toUpperCase() : null,
    };
  }

  return null;
}

function resolveWall(payload, side) {
  const cap = side[0].toUpperCase() + side.slice(1);
  const wallKey = `${side}Wall`;
  const levelsKey = `${side}Levels`;
  const strengthKey = `${side}WallStrength`;
  const gexKey = `${side}WallGex`;
  const levels = Array.isArray(payload[levelsKey]) ? payload[levelsKey] : [];

  const direct = normalizeDirectWall(payload[wallKey]);
  if (!direct) {
    const derived = strongestLevel(levels);
    if (!derived) return null;
    return {
      ...derived,
      strength: classifyStrength(levels, derived),
      source: `DERIVED_${cap.toUpperCase()}_LEVELS`,
    };
  }

  let gex = direct.gex;
  const explicitGex = Number(payload[gexKey]);
  if (!Number.isFinite(gex) && Number.isFinite(explicitGex)) gex = explicitGex;

  if (!Number.isFinite(gex) && levels.length > 0) {
    const match = levels.find((item) => Number(item.strike) === direct.strike);
    const matchedGex = Number(match?.gex);
    if (Number.isFinite(matchedGex)) gex = matchedGex;
  }

  const explicitStrength = typeof payload[strengthKey] === "string"
    ? payload[strengthKey].toUpperCase()
    : null;
  const strength = direct.strength || explicitStrength || classifyStrength(levels, { strike: direct.strike, gex });

  return {
    strike: direct.strike,
    gex: Number.isFinite(gex) ? gex : null,
    strength,
    source: "PUBLISHED_WALL",
  };
}

export function buildGexState(payload) {
  const callWallSource = resolveWall(payload, "call");
  const putWallSource = resolveWall(payload, "put");

  const market = {
    sourceUnderlying: payload.sourceUnderlying,
    sourcePrice: payload.sourcePrice,
    ndxPrice: payload.ndxPrice,
    qqqPrice: payload.qqqPrice,
    mnqPrice: payload.mnqPrice,
  };

  const callWall = callWallSource
    ? {
        ...callWallSource,
        mnqLevel: mapLevelToMnq(callWallSource.strike, market),
      }
    : null;

  const putWall = putWallSource
    ? {
        ...putWallSource,
        mnqLevel: mapLevelToMnq(putWallSource.strike, market),
      }
    : null;

  const gammaFlipRaw = typeof payload.gammaFlip === "object"
    ? payload.gammaFlip.level ?? payload.gammaFlip.strike
    : payload.gammaFlip;
  const gammaFlipSource = Number(gammaFlipRaw);
  const gammaFlip = Number.isFinite(gammaFlipSource)
    ? {
        sourceLevel: gammaFlipSource,
        mnqLevel: mapLevelToMnq(gammaFlipSource, market),
      }
    : null;

  const sourceUnderlying = String(payload.sourceUnderlying || "NDX").toUpperCase();

  return {
    timestamp: payload.timestamp || new Date().toISOString(),
    sourceUnderlying,
    sourcePrice: Number(payload.sourcePrice),
    mnqPrice: Number(payload.mnqPrice),
    basis:
      sourceUnderlying === "NDX"
        ? Number(payload.mnqPrice) - Number(payload.sourcePrice)
        : null,
    gexUnit: payload.gexUnit || "USD_PER_1PCT_MOVE",
    regime: gammaRegime(payload.netGex),
    netGex: Number(payload.netGex),
    callWall,
    putWall,
    gammaFlip,
  };
}
