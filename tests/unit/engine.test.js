import { describe, it, expect, beforeAll } from 'vitest';
import { ALL } from '../fixtures/texts.js';
import { analyze } from '../../src/app/analyzer.js';
import { createLinter } from '../../src/grammar/harper-adapter.js';
import { bandFor } from '../../src/scoring/level.js';
import { LevelSmoother } from '../../src/scoring/smoother.js';
import { IssueTracker } from '../../src/coach/hint-ladder.js';
import { academicIssues } from '../../src/academic/rules.js';
import { cleanOcr } from '../../src/text/cleanup.js';
import { SessionStore } from '../../src/session/session-store.js';

let linter;
beforeAll(async () => {
  linter = await createLinter('local');
});

describe('level meter', () => {
  it('scores the fixtures in plausible bands', async () => {
    const r = {};
    for (const [k, t] of Object.entries(ALL)) r[k] = (await analyze(t, linter)).level;
    expect(r.K.score).toBeLessThan(3);
    expect(r.HS.score).toBeGreaterThanOrEqual(8);
    expect(r.HS.score).toBeLessThan(13);
    expect(r.ACADEMIC.score).toBeGreaterThanOrEqual(13);
    expect(r.ERRORS.score).toBeLessThanOrEqual(r.ERRORS.base - 3);
    expect(r.ERRORS.score).toBeLessThan(13);
    expect(r.ERRORS.dims.accuracy).toBeLessThan(r.HS.dims.accuracy);
  });

  it('maps band boundaries', () => {
    expect(bandFor(0.9)).toBe('Kindergarten');
    expect(bandFor(1)).toBe('Grade 1');
    expect(bandFor(12.99)).toBe('Grade 12');
    expect(bandFor(13)).toBe('Undergraduate');
    expect(bandFor(17)).toBe("Master's");
    expect(bandFor(19)).toBe('PhD');
  });

  it('smoother resists jitter and resets on new text', () => {
    const s = new LevelSmoother();
    const t = 'the same words in this text every time';
    s.push({ score: 12.8, confidence: 'high' }, t);
    expect(s.push({ score: 13.1, confidence: 'high' }, t).band).toBe('Grade 12');
    expect(s.push({ score: 3, confidence: 'high' }, 'completely different stuff here now').score).toBe(3);
  });
});

describe('hint ladder', () => {
  it('never puts the fix in hint or explanation', async () => {
    for (const t of [...Object.values(ALL), 'He go to school.']) {
      for (const i of (await analyze(t, linter)).issues) {
        if (!i.suggestion || i.suggestion.length < 3) continue;
        const first = i.suggestion.split(' / ')[0].toLowerCase();
        expect(i.hint.toLowerCase()).not.toContain(first);
        expect(i.explanation.toLowerCase()).not.toContain(first);
      }
    }
  });

  it('credits self-correction vs reveal, and ignores unrelated text swaps', () => {
    const tr = new IssueTracker();
    const mk = (fp, text) => ({ id: fp, fingerprint: fp, text, span: { start: 0, end: 1 }, category: 'Grammar', severity: 'error' });
    const t1 = 'he go to school and they is late every single day';
    tr.update([mk('a', 'go'), mk('b', 'is')], t1, { now: 0 });
    tr.reveal('b');
    tr.update([], 'he goes to school and they are late every single day', { now: 5000 });
    expect(tr.stats).toMatchObject({ selfCorrected: 1, revealed: 1, fixedAfterReveal: 1 });

    tr.update([mk('c', 'x')], 'brand new document about something else', { now: 6000 });
    tr.update([], 'an unrelated page with other words entirely', { now: 9000 });
    expect(tr.stats.selfCorrected).toBe(1);
  });

  it('OCR mode needs two consecutive absent reads', () => {
    const tr = new IssueTracker();
    const mk = { id: 'a', fingerprint: 'a', text: 'go', span: { start: 0, end: 1 }, category: 'Grammar', severity: 'error' };
    const t = 'he go to school every day of the week';
    tr.update([mk], t, { mode: 'ocr', now: 0 });
    tr.update([], t, { mode: 'ocr', now: 3000 });
    expect(tr.stats.selfCorrected).toBe(0);
    tr.update([], t, { mode: 'ocr', now: 6000 });
    expect(tr.stats.selfCorrected).toBe(1);
  });
});

describe('academic register + OCR cleanup', () => {
  it('flags contractions, overclaiming, generic you; leaves formal prose alone', () => {
    const names = academicIssues("It's obvious that you always win.").map((i) => i.ruleName);
    expect(names).toEqual(expect.arrayContaining(['Contraction in formal writing', 'Personal voice', 'Overclaiming']));
    expect(academicIssues('The results suggest a moderate association.')).toHaveLength(0);
  });

  it('dehyphenates, drops menu junk, and marks low-confidence words', () => {
    const w = (text, confidence = 95) => ({ text, confidence });
    const out = cleanOcr([
      { lines: [{ words: [w('File'), w('Edit'), w('View')] }] },
      { lines: [{ words: [w('The'), w('experi-')] }, { words: [w('ment'), w('was'), w('zqx', 30), w('done.')] }] },
    ]);
    expect(out.text).toBe('The experiment was zqx done.');
    expect(out.unreliableRanges).toHaveLength(1);
  });
});

describe('session store', () => {
  it('stores numbers only, never text', () => {
    const mem = new Map();
    const storage = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) };
    const s = new SessionStore(storage);
    s.record({ score: 9.5, dims: { vocabulary: 9, syntax: 7, readability: 10, accuracy: 20 }, words: 120, issuesByCategory: { Grammar: 3 }, stats: { selfCorrected: 2, revealed: 1, fixedAfterReveal: 0 } }, 1);
    s.save();
    const raw = [...mem.values()].join('');
    expect(raw).toContain('9.5');
    expect(s.whereIStand().focus[0].label).toMatch(/syntax/);
  });
});
