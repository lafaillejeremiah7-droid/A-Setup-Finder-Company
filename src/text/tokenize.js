import { syllable } from 'syllable';

/** Word tokens per plan §6: letters with internal apostrophes/hyphens. */
export const WORD_RE = /[A-Za-z]+(?:['’-][A-Za-z]+)*/g;

const ABBREVIATIONS = new Set([
  'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'e.g', 'i.e', 'cf', 'al', 'fig', 'no', 'vol', 'approx',
]);

/**
 * @typedef {{start:number, end:number, text:string}} Span
 */

/**
 * Split text into sentences with UTF-16 offsets.
 * Boundaries: [.!?] followed by whitespace/end (skipping common abbreviations), and newlines.
 * @param {string} text
 * @returns {Span[]}
 */
export function sentences(text) {
  /** @type {Span[]} */
  const out = [];
  let start = 0;
  const push = (end) => {
    let s = start;
    let e = end;
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    if (e > s && /[A-Za-z0-9]/.test(text.slice(s, e))) out.push({ start: s, end: e, text: text.slice(s, e) });
    start = end;
  };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '\n') {
      push(i);
      continue;
    }
    if (ch !== '.' && ch !== '!' && ch !== '?') continue;
    let j = i + 1;
    while (j < text.length && /[.!?]/.test(text[j])) j++;
    while (j < text.length && /["'”’)\]]/.test(text[j])) j++;
    if (j < text.length && !/\s/.test(text[j])) {
      i = j - 1;
      continue;
    }
    if (ch === '.') {
      const before = text.slice(start, i).match(/([A-Za-z.]+)$/);
      if (before && ABBREVIATIONS.has(before[1].toLowerCase())) continue;
      if (before && /^[A-Za-z]$/.test(before[1]) && /[A-Z]/.test(before[1])) continue; // initials
    }
    push(j);
    i = j - 1;
  }
  push(text.length);
  return out;
}

/**
 * Word tokens with UTF-16 offsets.
 * @param {string} text
 * @returns {Span[]}
 */
export function words(text) {
  const out = [];
  for (const m of text.matchAll(WORD_RE)) out.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
  return out;
}

/**
 * @param {string} word
 * @returns {number}
 */
export function syllables(word) {
  return Math.max(1, syllable(word));
}

/**
 * Word-set overlap between two texts: |A ∩ B| / max(|A|, |B|), lowercase unique words.
 * 1 = same words, 0 = nothing in common (or either text empty).
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
export function wordOverlap(a, b) {
  const A = new Set(words(a || '').map((w) => w.text.toLowerCase()));
  const B = new Set(words(b || '').map((w) => w.text.toLowerCase()));
  if (!A.size || !B.size) return 0;
  let n = 0;
  for (const w of A) if (B.has(w)) n++;
  return n / Math.max(A.size, B.size);
}
