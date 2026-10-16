// Turns noisy OCR output into analyzable prose, tracking low-confidence ranges
// so OCR misreads aren't blamed on the writer.

const JUNK_LINE = /^\W*$|^\d{1,2}:\d{2}|^(File|Edit|View|Insert|Format|Tools|Help|Share|Comments)\b/;
const LOW_CONF = 60;
const SPELL_CONF = 85;

/**
 * @typedef {{text:string, confidence:number}} OcrWord
 * @typedef {{words: OcrWord[]}} OcrLine
 * @typedef {{lines: OcrLine[]}} OcrParagraph
 */

function normalize(s) {
  return s
    .replace(/ﬁ/g, 'fi')
    .replace(/ﬂ/g, 'fl')
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/^[•·▪\-–]\s+/, '');
}

function isJunk(lineText, tokens, standalone) {
  const letters = (lineText.match(/[A-Za-z]/g) || []).length;
  const nonSpace = lineText.replace(/\s/g, '').length || 1;
  if (letters / nonSpace < 0.5) return true;
  if (standalone && tokens <= 2 && !/[.!?]/.test(lineText)) return true; // lone labels/buttons
  return JUNK_LINE.test(lineText.trim());
}

/**
 * @param {OcrParagraph[]} paragraphs
 * @returns {{text:string, unreliableRanges:{start:number,end:number}[], spellUnsureRanges:{start:number,end:number}[], meanConfidence:number}}
 */
export function cleanOcr(paragraphs) {
  let text = '';
  const unreliableRanges = [];
  const spellUnsureRanges = [];
  let confSum = 0;
  let confN = 0;

  for (const para of paragraphs) {
    const lines = para.lines
      .map((l) => l.words.filter((w) => w.text.trim()).map((w) => ({ ...w, text: normalize(w.text) })))
      .filter((ws, _, all) => ws.length && !isJunk(ws.map((w) => w.text).join(' '), ws.length, all.length === 1));
    if (!lines.length) continue;
    if (text) text += '\n\n';
    let pendingHyphen = false;
    lines.forEach((ws, li) => {
      ws.forEach((w, wi) => {
        let t = w.text;
        const lastOfLine = wi === ws.length - 1;
        const hyphenBreak = lastOfLine && li < lines.length - 1 && /\w-$/.test(t);
        if (hyphenBreak) t = t.slice(0, -1);
        if (!(wi === 0 && (li === 0 || pendingHyphen))) text += ' ';
        if (t === '|') t = 'I';
        const start = text.length;
        text += t;
        const range = { start, end: text.length };
        if (w.confidence < LOW_CONF) unreliableRanges.push(range);
        else if (w.confidence < SPELL_CONF) spellUnsureRanges.push(range);
        confSum += w.confidence;
        confN++;
        pendingHyphen = hyphenBreak;
      });
    });
  }
  return { text, unreliableRanges, spellUnsureRanges, meanConfidence: confN ? confSum / confN : 0 };
}

/** Typed/pasted text: only light normalization. */
export function normalizeTyped(s) {
  return s.replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/[ \t]+/g, ' ');
}
