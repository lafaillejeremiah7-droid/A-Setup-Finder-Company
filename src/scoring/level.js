import { clamp } from './metrics.js';

/** Tunable constants (calibrated against tests/fixtures/texts.js). */
export const WEIGHTS = {
  vocab: { base: 2, wordLen: 4, wordLenPivot: 3.6, academic: 25, lexDiv: 3 },
  syntax: { wps: 0.45, wpsCap: 25, sub: 4, offset: -1.5 },
  mix: { read: 0.35, vocab: 0.35, syntax: 0.3 },
  accuracy: { ceiling: 22, perError: 1.2 },
  penalty: { perError: 0.6, freeErrors: 1 },
  max: 22,
};

/**
 * Score → band label.
 * @param {number} s
 */
export function bandFor(s) {
  if (s < 1) return 'Kindergarten';
  if (s < 13) return `Grade ${Math.floor(s)}`;
  if (s < 17) return 'Undergraduate';
  if (s < 19) return "Master's";
  return 'PhD';
}

/** Ordered band list for the gradient bar. */
export const BAND_STOPS = [
  { at: 0, label: 'K' },
  { at: 6, label: '6' },
  { at: 12, label: '12' },
  { at: 13, label: 'UG' },
  { at: 17, label: 'MA' },
  { at: 19, label: 'PhD' },
];

/**
 * @param {ReturnType<import('./metrics.js').computeMetrics>} m
 * @param {{errorWeight?: number, ocrConfidence?: number}} opts
 */
export function computeLevel(m, { errorWeight = 0, ocrConfidence = 100 } = {}) {
  if (!m || m.empty || !m.W) return null;
  const V = WEIGHTS.vocab;
  const max = WEIGHTS.max;
  const readGrade = clamp(m.readGrade, 0, max);
  const vocabGrade = clamp(
    V.base + V.wordLen * (m.avgWordLen - V.wordLenPivot) + V.academic * m.academicRate + V.lexDiv * m.lexDiv,
    0,
    max,
  );
  const syntaxGrade = clamp(
    WEIGHTS.syntax.wps * Math.min(m.wps, WEIGHTS.syntax.wpsCap) + WEIGHTS.syntax.sub * Math.min(m.subRate, 2) + WEIGHTS.syntax.offset,
    0,
    max,
  );
  const base = clamp(
    WEIGHTS.mix.read * readGrade + WEIGHTS.mix.vocab * vocabGrade + WEIGHTS.mix.syntax * syntaxGrade,
    0,
    max,
  );
  const density = (100 * errorWeight) / m.W; // weighted errors per 100 words
  const accuracyGrade = clamp(WEIGHTS.accuracy.ceiling - WEIGHTS.accuracy.perError * density, 0, max);
  const penalized = base - WEIGHTS.penalty.perError * Math.max(0, density - WEIGHTS.penalty.freeErrors);
  const final = round1(clamp(Math.min(penalized, accuracyGrade), 0, max));

  let confidence = 'high';
  if (m.W < 50 || ocrConfidence < 70) confidence = 'low';
  else if (m.W < 150) confidence = 'medium';

  return {
    score: final,
    base: round1(base),
    penalty: round1(Math.max(0, base - final)),
    band: bandFor(final),
    confidence,
    density: round1(density),
    dims: {
      vocabulary: round1(vocabGrade),
      syntax: round1(syntaxGrade),
      readability: round1(readGrade),
      accuracy: round1(accuracyGrade),
    },
  };
}

function round1(x) {
  return Math.round(x * 10) / 10;
}
