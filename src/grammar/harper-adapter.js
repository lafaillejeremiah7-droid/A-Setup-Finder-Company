import { describeRule } from './rule-catalog.js';
import { makeIssue, scrubLeak } from '../issues/schema.js';

/**
 * Create a Harper linter. 'worker' runs off the main thread in the browser; 'local' is for Node/tests.
 * @param {'worker'|'local'} kind
 */
export async function createLinter(kind = 'worker') {
  const harper = await import('harper.js');
  const { binary } = await import('harper.js/binary');
  const Ctor = kind === 'worker' ? harper.WorkerLinter : harper.LocalLinter;
  const linter = new Ctor({ binary, dialect: harper.Dialect.American });
  await linter.setup?.();
  // Harper suppresses every lint that overlaps a LongSentences span, which hides real
  // errors inside run-ons. Our academic "Run-on sentence" rule covers length instead.
  const config = await linter.getLintConfig();
  await linter.setLintConfig({ ...config, LongSentences: false });
  return {
    /** @param {string} text */
    async lint(text) {
      if (!text.trim()) return [];
      const organized = await linter.organizedLints(text, { language: 'plaintext' });
      return lintsToIssues(organized, text);
    },
  };
}

/** Harper spans are Unicode code-point offsets; JS strings index UTF-16 units. */
export function codepointToUtf16(text) {
  // fast path: no astral chars
  if (!/[\uD800-\uDBFF]/.test(text)) return (i) => i;
  const map = [];
  let u = 0;
  for (const ch of text) {
    map.push(u);
    u += ch.length;
  }
  map.push(u);
  return (i) => map[Math.min(i, map.length - 1)];
}

/**
 * @param {Record<string, any[]>} organized
 * @param {string} text
 */
export function lintsToIssues(organized, text) {
  const toU = codepointToUtf16(text);
  const issues = [];
  const seen = new Map();
  for (const [ruleKey, lints] of Object.entries(organized)) {
    for (const lint of lints) {
      const sp = lint.span();
      const span = { start: toU(sp.start), end: toU(sp.end) };
      const d = describeRule(ruleKey, lint.lint_kind());
      const sugs = lint.suggestions().map((s) => s.get_replacement_text?.() ?? '').filter(Boolean);
      const suggestion = sugs.length ? sugs.slice(0, 3).join(' / ') : null;
      const problem = text.slice(span.start, span.end);
      const key = `${d.ruleName}|${problem.toLowerCase()}`;
      const occ = seen.get(key) ?? 0;
      seen.set(key, occ + 1);
      issues.push(
        makeIssue(
          {
            span,
            category: d.category,
            ruleName: d.ruleName,
            severity: d.severity,
            hint: d.hint,
            explanation: scrubLeak(d.explanation, sugs[0]),
            suggestion,
            rawMessage: lint.message(),
            source: 'harper',
          },
          text,
          occ,
        ),
      );
    }
  }
  return issues;
}
