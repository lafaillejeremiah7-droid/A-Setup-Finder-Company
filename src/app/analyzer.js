import { academicIssues } from '../academic/rules.js';
import { computeMetrics } from '../scoring/metrics.js';
import { computeLevel } from '../scoring/level.js';
import { errorWeight } from '../issues/schema.js';

const near = (a, b, slack = 2) => a.start < b.end + slack && b.start < a.end + slack;

/** Drop duplicates (prefer Harper) and anything touching unreliable OCR text. */
export function mergeIssues(harper, academic, { unreliableRanges = [], spellUnsureRanges = [] } = {}) {
  const out = [...harper];
  for (const a of academic) if (!harper.some((h) => near(h.span, a.span, 0))) out.push(a);
  return out
    .filter((i) => !unreliableRanges.some((r) => near(r, i.span)))
    .filter((i) => i.category !== 'Spelling' || !spellUnsureRanges.some((r) => near(r, i.span, 0)))
    .sort((x, y) => x.span.start - y.span.start);
}

/**
 * Full analysis pass.
 * @param {string} text
 * @param {{lint:(t:string)=>Promise<any[]>}} linter
 * @param {{unreliableRanges?:any[], spellUnsureRanges?:any[], ocrConfidence?:number, extraIssues?:any[]}} opts
 */
export async function analyze(text, linter, opts = {}) {
  const harper = linter ? await linter.lint(text) : [];
  const issues = mergeIssues(harper, [...academicIssues(text), ...(opts.extraIssues || [])], opts);
  const metrics = computeMetrics(text);
  const level = computeLevel(metrics, { errorWeight: errorWeight(issues), ocrConfidence: opts.ocrConfidence ?? 100 });
  return { text, issues, metrics, level };
}
