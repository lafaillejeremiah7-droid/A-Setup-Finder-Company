import { bandFor } from '../scoring/level.js';

// Only numbers and category counts are stored — never the user's text.
const KEY = 'englishCoach.sessions.v1';
const MAX_SESSIONS = 30;
const MAX_POINTS = 720;
const POINT_EVERY_MS = 5000;
const SAVE_EVERY_MS = 10000;

const ACTIONS = {
  Grammar: 'Before moving on, re-read each sentence for its subject and main verb.',
  Spelling: 'Slow down on longer words; check roots and suffixes.',
  Mechanics: 'Do a final pass only for punctuation and capitals.',
  Usage: 'When a phrase “sounds right” from speech, check its written form.',
  'Academic register': 'Write as if for a journal: no contractions or chatty words, and calibrate certainty.',
  Style: 'Cut words that don’t add meaning.',
  Argument: 'Make each paragraph’s claim explicit, then support it.',
};

const DIM_ACTIONS = {
  vocabulary: 'Use more precise, discipline-specific words in place of general ones.',
  syntax: 'Combine related ideas with subordinate clauses (although, whereas, which).',
  readability: 'Vary sentence length and use more exact multi-syllable terms where they fit.',
  accuracy: 'Your errors are capping your level. Fix the basics before anything else.',
};

export class SessionStore {
  constructor(storage = globalThis.localStorage) {
    this.storage = storage;
    this.lastSave = 0;
    this.lastPoint = 0;
    this.current = {
      id: `s${Date.now()}`,
      startedAt: Date.now(),
      updatedAt: Date.now(),
      trend: [],
      issuesByCategory: {},
      selfCorrected: 0,
      revealed: 0,
      fixedAfterReveal: 0,
      wordsAnalyzed: 0,
      lastScore: null,
      lastDims: null,
    };
  }

  history() {
    try {
      const raw = JSON.parse(this.storage?.getItem(KEY) || '[]');
      return Array.isArray(raw) ? raw : [];
    } catch {
      return [];
    }
  }

  /** Record one analysis tick. */
  record({ score, dims, words, issuesByCategory, stats }, now = Date.now()) {
    const c = this.current;
    c.updatedAt = now;
    c.lastScore = score;
    c.lastDims = dims;
    c.wordsAnalyzed = Math.max(c.wordsAnalyzed, words);
    c.issuesByCategory = { ...issuesByCategory };
    Object.assign(c, { selfCorrected: stats.selfCorrected, revealed: stats.revealed, fixedAfterReveal: stats.fixedAfterReveal });
    if (now - this.lastPoint >= POINT_EVERY_MS || !c.trend.length) {
      c.trend.push({ t: now, score });
      if (c.trend.length > MAX_POINTS) c.trend.shift();
      this.lastPoint = now;
    }
    if (now - this.lastSave >= SAVE_EVERY_MS) this.save(now);
  }

  save(now = Date.now()) {
    if (!this.storage || this.current.lastScore === null) return;
    const all = this.history().filter((s) => s.id !== this.current.id);
    all.push(this.current);
    while (all.length > MAX_SESSIONS) all.shift();
    try {
      this.storage.setItem(KEY, JSON.stringify(all));
    } catch {
      /* quota — ignore */
    }
    this.lastSave = now;
  }

  clearHistory() {
    this.storage?.removeItem(KEY);
  }

  previous() {
    return this.history().filter((s) => s.id !== this.current.id).pop() || null;
  }

  /** Top-3 focus areas + comparison with last session. */
  whereIStand() {
    const c = this.current;
    const per100 = c.wordsAnalyzed ? 100 / c.wordsAnalyzed : 0;
    const candidates = Object.entries(c.issuesByCategory).map(([cat, n]) => ({
      label: `${cat}: ${(n * per100).toFixed(1)} per 100 words`,
      rate: n * per100,
      action: ACTIONS[cat] || ACTIONS.Style,
    }));
    if (c.lastDims) {
      const [dim, val] = Object.entries(c.lastDims).sort((a, b) => a[1] - b[1])[0];
      candidates.push({ label: `${dim} is your lowest dimension (${bandFor(val)})`, rate: 99, action: DIM_ACTIONS[dim] });
    }
    const focus = candidates.sort((a, b) => b.rate - a.rate).slice(0, 3);
    const prev = this.previous();
    let comparison = null;
    if (prev?.lastScore != null && c.lastScore != null) {
      const d = Math.round((c.lastScore - prev.lastScore) * 10) / 10;
      comparison = d === 0 ? 'Level unchanged from last session.' : `${d > 0 ? '+' : ''}${d} vs. last session (${bandFor(prev.lastScore)}).`;
    }
    return { focus, comparison };
  }
}
