/*
 * The Gamma Map -- Tradovate custom indicator (build-order step 7).
 *
 * A THIN RENDERER. It draws EXACTLY five horizontal lines plus one regime readout and
 * NOTHING else:
 *
 *     Call Wall, Put Wall, 0DTE Call Wall, 0DTE Put Wall   (one line each, at its strike)
 *     Gamma Flip                                           (one neutral gray line)
 *     TOTAL: +/-   0DTE: +/-                                (one small regime readout)
 *
 * It renders on the MNQ chart. It does NOT compute GEX. It does NOT draw OI, IV, per-strike
 * greeks, Max Pain, the full chain, distance-to-level, order flow, or volume profiles --
 * those are computed upstream (the Python engine) or added MANUALLY by the trader. That
 * scope boundary is a hard rule (build-spec.md SS0 / SS9a), not a default; do not add
 * plots here.
 *
 * All numbers come from a point-in-time levels JSON file produced by the engine
 * (src/gammamap/levels.py, schema "levels-1.0.0"). This indicator only PARSES and DRAWS.
 *
 * WHY Tradovate (not TradingView/Pine): a Tradovate custom indicator can read a fed file /
 * external data source, so the precomputed levels file can be handed to it directly. Pine
 * cannot read an external file, which is why the paper picked Tradovate as the renderer.
 *
 * CODE SHAPE: the levels-parsing + line/label/regime logic is PURE (buildRenderModel and
 * its helpers below) so it is unit-testable with no chart present. The Tradovate-specific
 * drawing calls are isolated in the clearly marked ADAPTER section at the bottom -- that is
 * the only integration seam that touches the Tradovate API.
 */

'use strict';

/* =============================================================================
 * SECTION 1 -- MACHINE-CHECKABLE RENDER SPEC (the contract)
 *
 * This object is the single source of truth for the field names and formatting rules the
 * renderer applies. It is intentionally declarative and data-only so a test (in any
 * language) can load it and assert the exact same contract the JS enforces, without
 * re-implementing the logic. The Python contract test (tests/test_indicator_contract.py)
 * reads THESE rules -- do not fork the formatting into two places.
 * ========================================================================== */
const RENDER_SPEC = {
  schemaVersion: 'levels-1.0.0',
  renderTarget: 'MNQ',

  // The exactly-five lines this indicator draws, in top-to-bottom render order, keyed by
  // the (universe, side) of the wall in the levels file. `flip` is the gamma flip line.
  lines: [
    { key: 'call.total', universe: 'total', side: 'call', label: 'CALL WALL', kind: 'wall' },
    { key: 'call.0dte', universe: '0dte', side: 'call', label: '0DTE CALL WALL', kind: 'wall' },
    { key: 'flip', kind: 'flip', label: 'GAMMA FLIP' },
    { key: 'put.0dte', universe: '0dte', side: 'put', label: '0DTE PUT WALL', kind: 'wall' },
    { key: 'put.total', universe: 'total', side: 'put', label: 'PUT WALL', kind: 'wall' },
  ],

  // Maximum number of lines. 4 walls + 1 flip. Missing lines are SKIPPED, never faked.
  maxLines: 5,

  // Label format for a wall line (build-spec.md SS9a example), fields joined by the
  // separator below:  "CALL WALL   26,200   $3.35B / 1%   EXTREME"
  wallLabelFields: ['label', 'strike', 'gex', 'strength'],
  labelSeparator: '   ',

  // Numeric formatting.
  strike: { thousandsSeparator: ',', decimals: 0 },
  gex: { divisor: 1e9, decimals: 2, prefix: '$', suffix: 'B / 1%', absolute: true },

  // Regime readout.  "TOTAL: NEGATIVE   0DTE: NEGATIVE"
  regime: {
    template: 'TOTAL: {total}   0DTE: {zerodte}',
    signWords: { '+': 'POSITIVE', '-': 'NEGATIVE', '0': 'NEUTRAL' },
    divergenceSuffix: '   (DIVERGENCE)',
    proxySuffix: '   (proxy)', // appended when the chain was a volume proxy (never OI-GEX)
  },

  // Valid strength bands (order = ascending dominance). Rendered verbatim.
  strengthBands: ['WEAK', 'MODERATE', 'STRONG', 'EXTREME'],

  // Flip line styling intent (the adapter maps these to Tradovate style calls).
  flipStyle: { color: 'gray', neutral: true, dotted: true },
};

/* =============================================================================
 * SECTION 2 -- PURE RENDER MODEL (no Tradovate API, fully testable)
 * ========================================================================== */

/**
 * Format a strike/price for a label: rounded to an integer MNQ price with thousands
 * separators. e.g. 30043.0957 -> "30,043".
 */
function formatStrike(mnqPrice) {
  const n = Math.round(mnqPrice);
  const sep = RENDER_SPEC.strike.thousandsSeparator;
  // Group the integer part in threes without relying on locale (deterministic output).
  const neg = n < 0;
  const digits = String(Math.abs(n));
  let out = '';
  for (let i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += sep;
    out += digits[i];
  }
  return (neg ? '-' : '') + out;
}

/**
 * Format a signed dollar GEX-per-1% figure as "$X.XXB / 1%" using the MAGNITUDE (the sign
 * lives in the side/regime, not the wall label -- build-spec.md SS9a example shows no sign
 * on the wall's $ figure). e.g. 833359502.83 -> "$0.83B / 1%".
 */
function formatGex(gexPer1pct) {
  const g = RENDER_SPEC.gex;
  const value = (g.absolute ? Math.abs(gexPer1pct) : gexPer1pct) / g.divisor;
  return g.prefix + value.toFixed(g.decimals) + g.suffix;
}

/** Map a regime sign token ('+','-','0') to its display word. */
function signWord(sign) {
  return RENDER_SPEC.regime.signWords[sign] || RENDER_SPEC.regime.signWords['0'];
}

/** Index the levels file's walls by "side.universe" for O(1) lookup. */
function indexWalls(levels) {
  const byKey = {};
  for (const w of levels.walls || []) {
    byKey[w.side + '.' + w.universe] = w;
  }
  return byKey;
}

/**
 * Build the pure render model from a parsed levels object.
 *
 * Returns { lines: [...], regime: {...}, provenance: {...} }.
 * Each line is { kind, label, price, text, style }.
 *
 * SKIP RULE (build-spec.md SS9a, orchestrator scope): a wall or flip with a null price is
 * OMITTED entirely -- we never draw a line at a guessed/fake level. Likewise a wall whose
 * gex magnitude is zero (no contracts on that side/day) is omitted; drawing a "$0.00B"
 * wall would be misleadingly authoritative.
 */
function buildRenderModel(levels) {
  const isProxy = !!(levels.provenance && levels.provenance.proxy);
  const byKey = indexWalls(levels);
  const lines = [];

  for (const spec of RENDER_SPEC.lines) {
    if (spec.kind === 'flip') {
      const flip = levels.gamma_flip || {};
      // A neutral / missing flip (monotone net-GEX curve) has a null price -> skip.
      if (flip.mnq_price == null) continue;
      lines.push({
        kind: 'flip',
        label: spec.label,
        price: flip.mnq_price,
        text: spec.label + RENDER_SPEC.labelSeparator + formatStrike(flip.mnq_price),
        style: RENDER_SPEC.flipStyle,
      });
      continue;
    }

    // Wall line.
    const wall = byKey[spec.side + '.' + spec.universe];
    if (!wall) continue; // wall absent from file -> skip
    if (wall.mnq_price == null) continue; // no strike (e.g. null 0DTE walls) -> skip
    if (!wall.gex_per_1pct) continue; // zero / missing GEX -> skip (don't fake authority)

    const fields = {
      label: spec.label,
      strike: formatStrike(wall.mnq_price),
      gex: formatGex(wall.gex_per_1pct),
      strength: wall.strength,
    };
    const text = RENDER_SPEC.wallLabelFields
      .map((f) => fields[f])
      .join(RENDER_SPEC.labelSeparator);

    lines.push({
      kind: 'wall',
      label: spec.label,
      side: spec.side,
      universe: spec.universe,
      price: wall.mnq_price,
      text: text,
      // Side-appropriate emphasis: calls above, puts below. Solid (walls) vs the flip's
      // gray dotted line. The adapter turns this into concrete Tradovate style calls.
      style: { color: spec.side === 'call' ? 'red' : 'green', solid: true, side: spec.side },
    });
  }

  // Regime readout. Proxy chains (volume-proxy OI) are surfaced with a "(proxy)" suffix so
  // a proxy regime is never mistaken for a classic OI-GEX read (orchestrator scope rule).
  const regime = levels.regime || {};
  let regimeText = RENDER_SPEC.regime.template
    .replace('{total}', signWord(regime.total_sign))
    .replace('{zerodte}', signWord(regime.zerodte_sign));
  if (regime.divergence) regimeText += RENDER_SPEC.regime.divergenceSuffix;
  if (isProxy) regimeText += RENDER_SPEC.regime.proxySuffix;

  return {
    lines: lines,
    regime: { text: regimeText, proxy: isProxy },
    provenance: levels.provenance || {},
  };
}

/* =============================================================================
 * SECTION 3 -- LEVEL SOURCE + REPLAY (no-look-ahead) SHIM
 *
 * A Tradovate custom indicator can be fed an external data source. These helpers pick the
 * correct point-in-time file for the chart's current (possibly replayed) timestamp. Keep
 * this a thin seam: `fetchIndex`/`fetchLevels` are injected so the pure selection logic is
 * testable and the transport (hosted JSON, Tradovate data feed, bundled file) can change
 * without touching the render model.
 * ========================================================================== */

/**
 * From a manifest of available levels files, pick the one whose `asof` is the LATEST that
 * is <= the chart timestamp. NEVER returns a future file (strict no-look-ahead replay,
 * build-spec.md SS0): when the user clicks a replay date they must see only the levels
 * that were known at that instant.
 *
 * @param {Array<{asof:string, url:string}>} manifest  available files (asof = ISO 8601).
 * @param {number|string|Date} chartTime  the chart's current bar time.
 * @returns {{asof:string, url:string}|null}  the selected entry, or null if none qualify.
 */
function selectLevelsForReplay(manifest, chartTime) {
  const t = new Date(chartTime).getTime();
  let best = null;
  let bestTime = -Infinity;
  for (const entry of manifest || []) {
    const et = new Date(entry.asof).getTime();
    if (et <= t && et > bestTime) {
      best = entry;
      bestTime = et;
    }
  }
  return best; // null => no levels known yet at this replay instant (draw nothing)
}

/* =============================================================================
 * SECTION 4 -- TRADOVATE ADAPTER (the ONLY integration seam)
 *
 * Everything above is pure and portable. Everything below is the thin, Tradovate-specific
 * glue that (a) declares the custom indicator, (b) obtains the levels file for the current
 * chart time, and (c) issues the actual horizontal-line + text drawing calls.
 *
 * NOTE ON THE TRADOVATE API: Tradovate custom indicators are authored in a small JS
 * dialect (module with meta/init/map). The exact drawing primitives depend on the account
 * / platform build, so the draw calls are funnelled through the small `draw` abstraction
 * below -- swap its body for the concrete platform calls (e.g. horizontalLine / text /
 * plot) when installing. This keeps the integration point in ONE clearly-marked place.
 * ========================================================================== */

/**
 * Draw a full render model onto a chart-drawing surface. `surface` is the platform object
 * that knows how to draw a horizontal line and a text label; injecting it keeps this
 * function testable and the platform calls in one spot.
 */
function drawRenderModel(surface, model) {
  for (const line of model.lines) {
    surface.horizontalLine(line.price, line.text, line.style);
  }
  surface.regimeText(model.regime.text);
}

/**
 * Tradovate custom-indicator entry point. Pseudocode-shaped to the meta/init/map contract;
 * the file source and drawing surface are the two integration seams to wire up on install.
 *
 * On each computed bar:
 *   1. read the chart's current (replayed) time,
 *   2. select the point-in-time levels file (no look-ahead),
 *   3. fetch + parse it,
 *   4. build the pure render model,
 *   5. draw the 5 lines + regime.
 */
function makeGammaMapIndicator(io) {
  // io = { getManifest(): manifest, getLevels(url): levelsJson, getSurface(ctx): surface }
  return {
    // Tradovate indicator metadata (name shown in the indicator list).
    meta: { name: 'The Gamma Map', overlay: true, instrument: 'MNQ' },

    // Called per bar/tick by the platform.
    render: function (ctx) {
      const chartTime = ctx.time; // current (possibly replayed) bar time
      const manifest = io.getManifest();
      const entry = selectLevelsForReplay(manifest, chartTime);
      if (!entry) return; // nothing known yet at this replay instant -> draw nothing
      const levels = io.getLevels(entry.url);
      if (!levels || levels.schema_version !== RENDER_SPEC.schemaVersion) return;
      const model = buildRenderModel(levels);
      drawRenderModel(io.getSurface(ctx), model);
    },
  };
}

/* =============================================================================
 * EXPORTS -- expose the pure functions + spec for testing / reuse. Guarded so this file
 * loads unchanged inside the Tradovate sandbox (no CommonJS there).
 * ========================================================================== */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    RENDER_SPEC,
    formatStrike,
    formatGex,
    signWord,
    buildRenderModel,
    selectLevelsForReplay,
    drawRenderModel,
    makeGammaMapIndicator,
  };
}
