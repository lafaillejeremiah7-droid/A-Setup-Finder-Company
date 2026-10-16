// Academic-vocabulary estimate. Instead of bundling a published word list, this
// combines (a) morphology typical of academic/Latinate vocabulary and (b) a short
// hand-picked set of common academic words that the morphology misses.

const ACADEMIC_SUFFIXES = [
  'tion', 'tions', 'sion', 'sions', 'ment', 'ments', 'ity', 'ities', 'ence', 'ences', 'ance', 'ances',
  'ency', 'ancy', 'ism', 'isms', 'ize', 'izes', 'ized', 'izing', 'ise', 'ised', 'ative', 'itive', 'ive',
  'ical', 'ically', 'ology', 'ologies', 'ological', 'ous', 'ary', 'ory', 'ist', 'ists', 'ent', 'ant',
  'ial', 'ual', 'ible', 'able', 'ify', 'ified', 'ate', 'ated', 'ates', 'ating', 'istic', 'esis', 'ysis',
];

// Short words that would pass the suffix test but are everyday vocabulary.
const COMMON_FALSE_POSITIVES = new Set([
  'nation', 'station', 'mention', 'question', 'moment', 'city', 'pity', 'once', 'since', 'dance', 'chance',
  'fence', 'hence', 'famous', 'nervous', 'serious', 'various', 'dangerous', 'library', 'story', 'history',
  'sorry', 'every', 'very', 'many', 'party', 'present', 'parent', 'student', 'agent', 'want', 'giant',
  'plant', 'ant', 'want', 'went', 'sent', 'spent', 'event', 'rent', 'tent', 'cent', 'late', 'date', 'gate',
  'hate', 'plate', 'state', 'rate', 'create', 'table', 'able', 'cable', 'vegetable', 'comfortable', 'possible',
  'terrible', 'horrible', 'give', 'live', 'five', 'drive', 'arrive', 'alive', 'active', 'list', 'ist',
  'special', 'social', 'usual', 'actual', 'ate', 'kite', 'mate', 'gym', 'test', 'ment',
]);

// Hand-picked academic words without a telltale suffix.
const ACADEMIC_WORDS = new Set([
  'analyze', 'analyse', 'approach', 'assess', 'assume', 'assert', 'attain', 'attribute', 'axis', 'bias',
  'capacity', 'cite', 'coherent', 'cohort', 'comprise', 'concept', 'conclude', 'consequent', 'consist',
  'constrain', 'construct', 'context', 'contingent', 'contrary', 'contrast', 'converse', 'core', 'criteria',
  'criterion', 'crucial', 'data', 'deduce', 'denote', 'derive', 'diverse', 'domain', 'empirical', 'emphasis',
  'ensure', 'entity', 'estimate', 'evaluate', 'evident', 'explicit', 'facet', 'factor', 'feature', 'finite',
  'framework', 'function', 'furthermore', 'hence', 'hypothesis', 'hypothesize', 'imply', 'implicit', 'impose',
  'incidence', 'infer', 'inherent', 'integral', 'interpret', 'intrinsic', 'investigate', 'justify', 'likewise',
  'mediate', 'method', 'methodology', 'moreover', 'nevertheless', 'nonetheless', 'notion', 'objective',
  'obtain', 'outcome', 'paradigm', 'parameter', 'perceive', 'perspective', 'phenomenon', 'phenomena', 'pose',
  'posit', 'premise', 'presume', 'prior', 'proceed', 'proportion', 'qualitative', 'quantitative', 'rationale',
  'refine', 'regime', 'reinforce', 'reside', 'rigorous', 'robust', 'salient', 'scheme', 'scope', 'sector',
  'significant', 'subsequent', 'substantial', 'sufficient', 'synthesis', 'theory', 'theoretical', 'thereby',
  'thesis', 'trend', 'underlie', 'underlying', 'utilize', 'valid', 'variable', 'variance', 'whereas', 'yield',
  'longitudinal', 'heterogeneous', 'homogeneous', 'attenuate', 'stratification', 'precarity', 'cumulative',
  'discourse', 'framework', 'implication', 'indicate', 'interval', 'mechanism', 'paradox', 'prevalent',
]);

/**
 * Is this (lowercase) word academic vocabulary?
 * @param {string} w
 */
export function isAcademicWord(w) {
  const word = w.toLowerCase().replace(/['’]s$/, '');
  if (ACADEMIC_WORDS.has(word)) return true;
  // crude stem check against the hand list (plural / -ed / -ing / -s)
  const stem = word.replace(/(ies|es|s|ed|ing|ly)$/, '');
  if (stem.length >= 4 && (ACADEMIC_WORDS.has(stem) || ACADEMIC_WORDS.has(stem + 'e'))) return true;
  if (word.length < 7 || COMMON_FALSE_POSITIVES.has(word)) return false;
  return ACADEMIC_SUFFIXES.some((s) => word.endsWith(s));
}
