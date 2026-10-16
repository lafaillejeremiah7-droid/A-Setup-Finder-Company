/** Categories that count against accuracy, with their severity weights. */
export const CORRECTNESS = new Set(['Grammar', 'Spelling', 'Mechanics', 'Usage']);
export const SEVERITY_WEIGHT = { error: 1, warning: 0.5, style: 0 };

/**
 * @typedef {Object} Issue
 * @property {string} id
 * @property {string} fingerprint
 * @property {{start:number,end:number}} span
 * @property {string} text
 * @property {string} category
 * @property {string} ruleName
 * @property {string} explanation  level-2 text; never contains the fix
 * @property {string} hint         level-1 nudge
 * @property {string|null} suggestion  level-3 only
 * @property {string|null} rawMessage  level-3 only
 * @property {'error'|'warning'|'style'} severity
 * @property {'harper'|'academic'|'ai'} source
 */

/** Build an issue; fingerprint is stable while the surrounding text doesn't change. */
export function makeIssue(fields, fullText, occurrence = 0) {
  const { span, ruleName, source } = fields;
  const text = fields.text ?? fullText.slice(span.start, span.end);
  // Identity = rule + flagged text + which occurrence it is. Deliberately NOT the neighboring
  // words: fixing a nearby error must not make an untouched issue look "self-corrected".
  const fingerprint = `${ruleName}|${text.toLowerCase().replace(/\s+/g, ' ')}|${occurrence}`;
  return {
    suggestion: null,
    rawMessage: null,
    ...fields,
    text,
    fingerprint,
    id: `${source}:${fingerprint}`,
  };
}

/** Weighted error count used by the accuracy dimension. */
export function errorWeight(issues) {
  let w = 0;
  for (const i of issues) if (CORRECTNESS.has(i.category)) w += SEVERITY_WEIGHT[i.severity] ?? 0;
  return w;
}

/**
 * Remove an explanation/hint sentence that would give away the fix.
 * Used as a final safety net before level-1/2 text is shown.
 */
export function scrubLeak(str, suggestion) {
  if (!str || !suggestion) return str;
  const s = suggestion.trim().toLowerCase();
  if (s.length < 2) return str;
  return str.toLowerCase().includes(s) ? 'Look closely at this phrase and check it against the rule.' : str;
}
