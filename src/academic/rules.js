import { sentences, words } from '../text/tokenize.js';
import { makeIssue } from '../issues/schema.js';

const CONTRACTIONS = {
  "don't": 'do not', "doesn't": 'does not', "didn't": 'did not', "can't": 'cannot', "won't": 'will not',
  "isn't": 'is not', "aren't": 'are not', "wasn't": 'was not', "weren't": 'were not', "it's": 'it is',
  "that's": 'that is', "there's": 'there is', "they're": 'they are', "we're": 'we are', "you're": 'you are',
  "i'm": 'I am', "i've": 'I have', "we've": 'we have', "they've": 'they have', "shouldn't": 'should not',
  "wouldn't": 'would not', "couldn't": 'could not', "haven't": 'have not', "hasn't": 'has not', "let's": 'let us',
  "i'd": 'I would', "we'd": 'we would', "they'd": 'they would', "i'll": 'I will', "we'll": 'we will',
};

const INFORMAL = {
  gonna: 'going to', wanna: 'want to', gotta: 'have to', kids: 'children', okay: 'acceptable', ok: 'acceptable',
  awesome: 'impressive', cool: 'notable', huge: 'substantial', basically: '(delete, or state the core point)',
  totally: 'entirely', super: 'highly', stuff: 'materials / factors', guys: 'people', tons: 'a large number',
  anyways: 'in any case', kinda: 'somewhat', sorta: 'somewhat', yeah: 'yes', nowadays: 'currently',
};

const VAGUE = ['thing', 'things', 'a lot', 'lots of', 'very', 'really', 'kind of', 'sort of', 'nice', 'good', 'bad', 'big', 'stuff'];
const BOOSTERS = { proves: 'suggests', prove: 'suggest', proved: 'indicated', always: 'typically', never: 'rarely', everyone: 'most people', everybody: 'most people', nobody: 'few people', clearly: '(state the evidence instead)', obviously: '(state the evidence instead)', undeniably: 'arguably', certainly: 'likely', definitely: 'likely' };
const WEAK_OPENERS = { and: 'Furthermore,', but: 'However,', so: 'Therefore,', also: 'In addition,', plus: 'Moreover,', then: 'Subsequently,' };
const SUBORDINATE_START = /^(because|although|since|while|which|whereas|if)\b/i;
const BE = /\b(is|are|was|were|be|been|being)\s+(\w+ly\s+)?(\w+ed|built|done|made|given|taken|shown|known|seen|written|found|held|brought|chosen|told|said|thought|kept|left|paid|sent|set|put|led|grown|drawn|driven)\b/gi;

const R = (id, ruleName, hint, explanation, severity = 'style') => ({ id, ruleName, hint, explanation, severity });

export const RULES = {
  contraction: R('contraction', 'Contraction in formal writing', 'Register check: is this formal enough?', 'Formal academic registers spell out contracted forms. Contractions read as conversational.'),
  informal: R('informal', 'Informal / colloquial word', 'Register check: this word is conversational.', 'Colloquial words signal speech, not scholarship. Academic readers expect neutral, precise vocabulary.', 'warning'),
  vague: R('vague', 'Vague word', 'Precision check: what exactly do you mean?', 'Academic claims are judged on precision. Vague words make the reader guess what you mean, and that weakens the argument.'),
  person: R('person', 'Personal voice', 'Voice check: who is speaking here?', 'Generic “you” and stacked “I think/I believe” shift attention from the evidence to the writer. Let the claim and its support carry the weight.'),
  overclaim: R('overclaim', 'Overclaiming', 'Strength check: can the evidence support this?', 'Absolute words (always, proves, everyone) claim more than evidence usually allows. Academic writing calibrates certainty with hedges.', 'warning'),
  transition: R('transition', 'Weak transition', 'Flow check: how does this sentence relate to the last one?', 'Starting with a bare conjunction is conversational and leaves the logical relationship vague. A precise transition tells the reader whether you are adding, contrasting, or concluding.'),
  variety: R('variety', 'Little sentence-length variety', 'Rhythm check: read these sentences aloud.', 'When every sentence has a similar length, prose reads as monotonous. Mixing short emphatic sentences with longer complex ones shows control.'),
  passive: R('passive', 'Heavy passive voice', 'Voice check: who is doing the action?', 'Some passive voice is normal in academic writing, but too much hides who did what and makes sentences longer.'),
  runon: R('runon', 'Run-on sentence', 'Structure check: how many ideas are in this sentence?', 'Several independent clauses chained with “and/but/so” or a bare comma blur the logic. Each main claim deserves clear boundaries.', 'warning'),
  fragment: R('fragment', 'Sentence fragment', 'Structure check: is this a complete sentence?', 'A clause that starts with a subordinator (because, although, which…) depends on a main clause. On its own, it is incomplete.', 'warning'),
  opener: R('opener', 'Repeated sentence openers', 'Rhythm check: look at how these sentences begin.', 'Starting several nearby sentences with the same word makes prose feel list-like. Vary the structure to show how ideas connect.'),
};

function* findAll(text, re) {
  for (const m of text.matchAll(re)) yield { start: m.index, end: m.index + m[0].length, match: m[0] };
}

function wordRe(list) {
  const esc = list.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/'/g, "['’]"));
  return new RegExp(`\\b(${esc.join('|')})\\b`, 'gi');
}

/**
 * Run all academic-register rules.
 * @param {string} text
 */
export function academicIssues(text) {
  const ss = sentences(text);
  const ws = words(text);
  const hits = []; // {rule, start, end, suggestion}
  const add = (rule, start, end, suggestion) => hits.push({ rule, start, end, suggestion });

  for (const h of findAll(text, wordRe(Object.keys(CONTRACTIONS))))
    add('contraction', h.start, h.end, CONTRACTIONS[h.match.toLowerCase().replace('’', "'")]);
  for (const h of findAll(text, wordRe(Object.keys(INFORMAL)))) add('informal', h.start, h.end, INFORMAL[h.match.toLowerCase()]);
  for (const h of findAll(text, /\bpretty\s+(good|bad|big|much|sure|clear|important)\b/gi))
    add('informal', h.start, h.end, 'fairly / quite, or a precise adjective');
  for (const h of findAll(text, wordRe(VAGUE.filter((v) => v !== 'stuff'))))
    add('vague', h.start, h.end, 'Name the specific property, quantity, or quality you mean.');

  // person
  for (const h of findAll(text, /\b(you|your|yourself)\b/gi)) add('person', h.start, h.end, 'one / people / students (name the actual group), or restructure around the claim.');
  for (const h of findAll(text, /\bI\s+(think|believe|feel)\b/g)) add('person', h.start, h.end, 'Drop the frame and state the claim, or hedge it (“This suggests…”).');
  const firstPerson = [...findAll(text, /\b(I|me|my|we|our|us)\b/g)];
  if (ws.length >= 30 && (100 * firstPerson.length) / ws.length > 3)
    for (const h of firstPerson.filter((h) => !hits.some((x) => x.start <= h.start && x.end >= h.end)))
      add('person', h.start, h.end, 'Consider whether the evidence can be the subject instead of you.');

  for (const h of findAll(text, wordRe(Object.keys(BOOSTERS)))) add('overclaim', h.start, h.end, BOOSTERS[h.match.toLowerCase()]);

  // sentence-level rules
  const lens = ss.map((s) => words(s.text).length);
  ss.forEach((s, i) => {
    const first = s.text.match(/^[“"']?([A-Za-z]+)/);
    if (first && WEAK_OPENERS[first[1].toLowerCase()] && i > 0) {
      const st = s.start + s.text.indexOf(first[1]);
      add('transition', st, st + first[1].length, WEAK_OPENERS[first[1].toLowerCase()]);
    }
    const joiners = (s.text.match(/\b(and|but|so|or)\b/gi) || []).length;
    const splice = /,\s+(it|this|they|he|she|we|i)\s+(is|are|was|were|has|have)\b/i.test(s.text);
    if ((lens[i] > 40 && joiners >= 2) || splice) add('runon', s.start, s.end, 'Split into separate sentences, or subordinate one clause to another.');
    if (SUBORDINATE_START.test(s.text) && !s.text.includes(',') && lens[i] < 12)
      add('fragment', s.start, s.end, 'Attach this clause to a main clause, or add one.');
  });

  if (ss.length >= 5) {
    const mean = lens.reduce((a, b) => a + b, 0) / lens.length;
    const sd = Math.sqrt(lens.reduce((a, b) => a + (b - mean) ** 2, 0) / lens.length);
    if (sd < 3) add('variety', ss[0].start, ss[0].end, 'Combine two related sentences, or add a short, direct sentence for emphasis.');
  }

  if (ss.length >= 4) {
    const passiveSpans = ss.map((s) => [...findAll(s.text, BE)].map((h) => ({ start: s.start + h.start, end: s.start + h.end })));
    const share = passiveSpans.filter((p) => p.length).length / ss.length;
    if (share > 0.3) for (const p of passiveSpans.flat()) add('passive', p.start, p.end, 'Name the actor and make it the subject.');
  }

  const openers = ss.map((s) => (s.text.match(/^[“"']?([A-Za-z]+)/) || [])[1]?.toLowerCase());
  const flagged = new Set();
  for (let i = 0; i + 2 < ss.length; i++) {
    const win = openers.slice(i, i + 5);
    for (const w of new Set(win)) {
      if (!w || win.filter((x) => x === w).length < 3) continue;
      for (let j = i; j < Math.min(i + 5, ss.length); j++) if (openers[j] === w) flagged.add(j);
    }
  }
  for (const j of flagged) {
    const s = ss[j];
    const st = s.start + s.text.search(/[A-Za-z]/);
    add('opener', st, st + openers[j].length, 'Restructure: lead with the subordinate clause, the object, or a transition.');
  }

  const occ = new Map();
  return hits.map((h) => {
    const r = RULES[h.rule];
    const t = text.slice(h.start, h.end).toLowerCase();
    const k = `${r.id}|${t}`;
    const n = occ.get(k) ?? 0;
    occ.set(k, n + 1);
    return makeIssue(
      {
        span: { start: h.start, end: h.end },
        category: 'Academic register',
        ruleName: r.ruleName,
        severity: r.severity,
        hint: r.hint,
        explanation: r.explanation,
        suggestion: h.suggestion,
        rawMessage: null,
        source: 'academic',
      },
      text,
      n,
    );
  });
}
