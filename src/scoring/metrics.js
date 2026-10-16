import { sentences, words, syllables } from '../text/tokenize.js';
import { isAcademicWord } from './vocab.js';

const STOPWORDS = new Set(
  ('a an the and or but if of to in on at by for with from as is are was were be been being it its this that these those ' +
    'i you he she we they me him her us them my your his our their not no do does did have has had will would can could ' +
    'should may might must shall so than then there here what which who whom whose when where why how all any some each')
    .split(' '),
);

const SUBORDINATORS = /\b(because|although|though|whereas|while|since|unless|if|which|whom|whose|despite|so that|in order to|whether|whereby)\b/gi;

/** Measure of Textual Lexical Diversity (forward+backward average). */
function mtld(tokens, threshold = 0.72) {
  const pass = (arr) => {
    let factors = 0;
    let types = new Set();
    let count = 0;
    for (const t of arr) {
      types.add(t);
      count++;
      if (types.size / count <= threshold) {
        factors++;
        types = new Set();
        count = 0;
      }
    }
    if (count > 0) {
      const ttr = types.size / count;
      factors += (1 - ttr) / (1 - threshold);
    }
    return factors > 0 ? arr.length / factors : arr.length;
  };
  if (!tokens.length) return 0;
  return (pass(tokens) + pass([...tokens].reverse())) / 2;
}

/**
 * Compute raw text metrics.
 * @param {string} text
 */
export function computeMetrics(text) {
  let ws = words(text);
  let src = text;
  if (ws.length > 600) {
    // score only the most recent 600 words
    src = text.slice(ws[ws.length - 600].start);
    ws = words(src);
  }
  const ss = sentences(src);
  const W = ws.length;
  const S = Math.max(1, ss.length);
  if (!W) return { W: 0, S: 0, empty: true };

  let syl = 0;
  let poly = 0;
  let letters = 0;
  for (const w of ws) {
    const n = syllables(w.text);
    syl += n;
    if (n >= 3) poly++;
    letters += w.text.replace(/[^A-Za-z]/g, '').length;
  }
  const wps = W / S;
  const fk = 0.39 * Math.min(wps, 35) + 11.8 * (syl / W) - 15.59;
  const cli = 0.0588 * ((100 * letters) / W) - 0.296 * ((100 * S) / W) - 15.8;
  const smog = S >= 10 ? 1.043 * Math.sqrt((poly * 30) / S) + 3.1291 : null;
  const grades = [fk, cli, smog].filter((g) => g !== null);
  const readGrade = grades.reduce((a, b) => a + b, 0) / grades.length;

  const lower = ws.map((w) => w.text.toLowerCase());
  const content = lower.filter((w) => w.length >= 3 && !STOPWORDS.has(w));
  const academic = content.filter(isAcademicWord).length;
  const academicRate = content.length ? academic / content.length : 0;
  const avgWordLen = letters / W;
  const m = mtld(lower);
  const lexDiv =
    W >= 50 ? clamp((m - 30) / 70, 0, 1) : clamp((new Set(lower).size / W - 0.5) / 0.4, 0, 1) * (W / 50);

  const subCount = (src.match(SUBORDINATORS) || []).length + countMidThat(src);
  const subRate = subCount / S;

  return { W, S, wps, syl, poly, letters, fk, cli, smog, readGrade, avgWordLen, academicRate, mtld: m, lexDiv, subRate };
}

function countMidThat(text) {
  // "that" not at the start of a sentence ≈ complement/relative clause marker
  return (text.match(/[^.!?\s]\s+that\b/gi) || []).length;
}

export function clamp(x, lo, hi) {
  return Math.max(lo, Math.min(hi, x));
}
