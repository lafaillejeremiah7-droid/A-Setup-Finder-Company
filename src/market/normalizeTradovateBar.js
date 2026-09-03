export function normalizeTradovateBar(raw) {
  const bar = {
    timestamp: new Date(raw.timestamp),
    open: Number(raw.open),
    high: Number(raw.high),
    low: Number(raw.low),
    close: Number(raw.close),
    bidVolume: raw.bidVolume == null ? null : Number(raw.bidVolume),
    offerVolume: raw.offerVolume == null ? null : Number(raw.offerVolume),
    upVolume: raw.upVolume == null ? null : Number(raw.upVolume),
    downVolume: raw.downVolume == null ? null : Number(raw.downVolume),
  };

  validateBar(bar);
  return bar;
}

export function validateBar(bar) {
  if (!(bar.timestamp instanceof Date) || Number.isNaN(bar.timestamp.getTime())) {
    throw new Error('INVALID_TIMESTAMP');
  }

  for (const key of ['open', 'high', 'low', 'close']) {
    if (!Number.isFinite(bar[key])) throw new Error(`INVALID_${key.toUpperCase()}`);
  }

  if (bar.high < Math.max(bar.open, bar.close)) throw new Error('INVALID_HIGH');
  if (bar.low > Math.min(bar.open, bar.close)) throw new Error('INVALID_LOW');
  if (bar.high < bar.low) throw new Error('INVALID_RANGE');

  for (const key of ['bidVolume', 'offerVolume', 'upVolume', 'downVolume']) {
    if (bar[key] != null && (!Number.isFinite(bar[key]) || bar[key] < 0)) {
      throw new Error(`INVALID_${key.toUpperCase()}`);
    }
  }
}

export function hasExecutedSideVolume(bar) {
  return Number.isFinite(bar.bidVolume) && Number.isFinite(bar.offerVolume);
}

export function barDelta(bar) {
  if (!hasExecutedSideVolume(bar)) return null;
  return bar.offerVolume - bar.bidVolume;
}

export function barDeltaPct(bar) {
  if (!hasExecutedSideVolume(bar)) return null;
  const total = bar.offerVolume + bar.bidVolume;
  return total === 0 ? 0 : (bar.offerVolume - bar.bidVolume) / total;
}
