const DEFAULT_THRESHOLDS = Object.freeze({
  extreme: 0.35,
  strong: 0.22,
  moderate: 0.12,
});

export function classifyStrength(levels, wall, thresholds = DEFAULT_THRESHOLDS) {
  if (!wall || !Array.isArray(levels) || levels.length === 0) return "N/A";

  const totalAbs = levels.reduce((sum, item) => sum + Math.abs(Number(item.gex) || 0), 0);
  if (totalAbs <= 0) return "N/A";

  const share = Math.abs(wall.gex) / totalAbs;
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

export function buildGexState(payload) {
  const callWallSource = strongestLevel(payload.callLevels);
  const putWallSource = strongestLevel(payload.putLevels);

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
        strength: classifyStrength(payload.callLevels, callWallSource),
      }
    : null;

  const putWall = putWallSource
    ? {
        ...putWallSource,
        mnqLevel: mapLevelToMnq(putWallSource.strike, market),
        strength: classifyStrength(payload.putLevels, putWallSource),
      }
    : null;

  const gammaFlipSource = Number(payload.gammaFlip);
  const gammaFlip = Number.isFinite(gammaFlipSource)
    ? {
        sourceLevel: gammaFlipSource,
        mnqLevel: mapLevelToMnq(gammaFlipSource, market),
      }
    : null;

  return {
    timestamp: payload.timestamp || new Date().toISOString(),
    sourceUnderlying: String(payload.sourceUnderlying || "NDX").toUpperCase(),
    sourcePrice: Number(payload.sourcePrice),
    mnqPrice: Number(payload.mnqPrice),
    basis:
      String(payload.sourceUnderlying || "NDX").toUpperCase() === "NDX"
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
