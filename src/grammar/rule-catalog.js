// Our own names/explanations for Harper rules. Explanations teach the rule
// without stating the fix — Harper's raw message (which often does) is shown only on Reveal.

const KIND = {
  Agreement: ['Grammar', 'error'],
  Grammar: ['Grammar', 'error'],
  WordOrder: ['Grammar', 'error'],
  Spelling: ['Spelling', 'error'],
  Typo: ['Spelling', 'error'],
  Punctuation: ['Mechanics', 'warning'],
  Capitalization: ['Mechanics', 'warning'],
  Formatting: ['Mechanics', 'warning'],
  BoundaryError: ['Mechanics', 'warning'],
  WordChoice: ['Usage', 'warning'],
  Usage: ['Usage', 'warning'],
  Malapropism: ['Usage', 'warning'],
  Eggcorn: ['Usage', 'warning'],
  Nonstandard: ['Usage', 'warning'],
  Regionalism: ['Usage', 'warning'],
};

const KIND_EXPLANATION = {
  Grammar: 'The sentence structure breaks a grammar rule here. Read the clause aloud and identify the subject and its verb.',
  Spelling: 'This word is not spelled the standard way. In academic writing, misspellings cost credibility quickly.',
  Mechanics: 'Punctuation, capitalization, or word boundaries are off. These mechanics signal care to a reader.',
  Usage: 'This wording is nonstandard for formal English. Check how the phrase is conventionally written.',
  Style: 'This is grammatical but weakens the prose. Consider whether it earns its place.',
};

/** Rule-specific entries, matched by exact name or regex. */
const RULES = [
  [/Agreement$/, 'Subject–verb agreement', 'A verb must match its subject in number: singular subjects take singular verbs, plural subjects take plural verbs. Find the subject first, then check the verb.'],
  [/^SpellCheck$/, 'Spelling', 'This word isn’t in the dictionary as written. Sound it out syllable by syllable, or check the root word it comes from.'],
  [/^RepeatedWords$/, 'Repeated word', 'The same word appears twice in a row. Usually one copy is a slip.'],
  [/^(Their|There|They|Your|You|Its|It|To|Too|Then|Than|Affect|Effect|Lose|Loose).*To/, 'Commonly confused words', 'This word sounds like another word with a different meaning or function. Decide which part of speech the sentence needs here (possessive, place, contraction, comparison…).'],
  [/^AnA$/, 'Article choice (a/an)', '“A” and “an” depend on the first SOUND of the next word, not its first letter.'],
  [/Capitalization/, 'Capitalization', 'Sentences, proper nouns, and the pronoun “I” start with a capital letter.'],
  [/^LongSentences?$/, 'Overlong sentence', 'Very long sentences make readers hold too much at once. Look for a natural point to split the idea.'],
  [/^ModalOf$/, 'Modal + “of”', 'After could/should/would/must, English uses an auxiliary verb, not a preposition. The spoken contraction misleads many writers.'],
  [/^OpenCompounds$|Compound/, 'Word boundary', 'Some expressions are written as separate words, others as one. This one is written the other way.'],
  [/^ThenThan$|Than/, 'Then vs. than', 'One of these words is for time/sequence, the other is for comparison.'],
  [/DoubleNegative|Negative/, 'Double negative', 'Two negatives in one clause cancel out (or read as nonstandard). Keep exactly one.'],
  [/Comma/, 'Comma use', 'Commas separate clauses and list items in specific patterns. Check whether this comma joins or separates the right things.'],
];

export function humanize(key) {
  return key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase());
}

/**
 * @param {string} ruleKey  Harper rule key (e.g. "PronounVerbAgreement")
 * @param {string} kind     Harper lint_kind (e.g. "Agreement")
 */
export function describeRule(ruleKey, kind) {
  const [category, severity] = KIND[kind] || ['Style', 'style'];
  const sev = ruleKey === 'RepeatedWords' ? 'error' : severity;
  const hit = RULES.find(([re]) => re.test(ruleKey));
  const ruleName = hit ? hit[1] : humanize(ruleKey);
  const explanation = hit ? hit[2] : KIND_EXPLANATION[category];
  return {
    category: ruleKey === 'RepeatedWords' ? 'Grammar' : category,
    severity: sev,
    ruleName,
    explanation,
    hint: `Something’s off here: ${category.toLowerCase()}.`,
  };
}
