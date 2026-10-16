import { wordOverlap } from '../text/tokenize.js';

/**
 * Tracks each issue across reads and runs the hint ladder:
 *   level 1 = nudge (highlight + category), level 2 = rule + explanation, level 3 = revealed fix.
 * An issue counts as self-corrected only if it disappears while the surrounding text is still in view
 * (so scrolling away or switching documents doesn't count), and — for OCR — for 2 consecutive reads.
 */
export class IssueTracker {
  constructor() {
    /** @type {Map<string, {issue:any, level:1|2|3, revealed:boolean, missCount:number, firstSeen:number}>} */
    this.items = new Map();
    this.prevText = '';
    this.stats = { selfCorrected: 0, revealed: 0, fixedAfterReveal: 0 };
    this.byCategory = {};
  }

  /**
   * @param {any[]} issues
   * @param {string} text
   * @param {{mode?: 'ocr'|'typed'}} opts
   * @returns {{resolved: {issue:any, self:boolean}[]}}
   */
  update(issues, text, { mode = 'typed', now = Date.now() } = {}) {
    const inView = !this.prevText || wordOverlap(this.prevText, text) >= 0.6;
    const needed = mode === 'ocr' ? 2 : 1;
    const present = new Set();
    const resolved = [];

    for (const issue of issues) {
      present.add(issue.fingerprint);
      const cur = this.items.get(issue.fingerprint);
      if (cur) {
        cur.issue = issue;
        cur.missCount = 0;
        cur.seen++;
      } else {
        this.items.set(issue.fingerprint, { issue, level: 1, revealed: false, missCount: 0, seen: 1, firstSeen: now });
        this.byCategory[issue.category] = (this.byCategory[issue.category] || 0) + 1;
      }
    }

    for (const [fp, item] of this.items) {
      if (present.has(fp)) continue;
      if (!inView) {
        // text changed wholesale — forget silently, no credit either way
        this.items.delete(fp);
        continue;
      }
      item.missCount++;
      if (item.missCount >= needed) {
        this.items.delete(fp);
        // transient (an OCR flicker, a half-typed word): seen once, briefly, never engaged with — no credit
        if (item.seen < 2 && now - item.firstSeen < 1500 && !item.revealed && item.level === 1) continue;
        if (item.revealed) this.stats.fixedAfterReveal++;
        else this.stats.selfCorrected++;
        resolved.push({ issue: item.issue, self: !item.revealed });
      }
    }
    this.prevText = text;
    return { resolved };
  }

  /** Active items in text order. */
  active() {
    return [...this.items.values()].filter((i) => i.missCount === 0).sort((a, b) => a.issue.span.start - b.issue.span.start);
  }

  get(id) {
    for (const item of this.items.values()) if (item.issue.id === id) return item;
    return null;
  }

  /** Level 1 → 2: show the rule. */
  advance(id) {
    const item = this.get(id);
    if (item && item.level === 1) item.level = 2;
    return item;
  }

  /** → 3: show the fix. Counts as a reveal. */
  reveal(id) {
    const item = this.get(id);
    if (item && !item.revealed) {
      item.level = 3;
      item.revealed = true;
      this.stats.revealed++;
    }
    return item;
  }

  independence() {
    const { selfCorrected, revealed } = this.stats;
    return selfCorrected + revealed ? Math.round((100 * selfCorrected) / (selfCorrected + revealed)) : null;
  }
}
