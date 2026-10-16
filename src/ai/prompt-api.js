// Optional: Chrome's built-in on-device model (Prompt API). Feature-detected; the app works without it.

const SYSTEM = `You are a demanding academic writing coach. Never praise. Never rewrite the whole text.
Find at most 4 paragraph-level problems with argument clarity, cohesion, or unsupported claims.
For each, quote the exact problematic phrase from the text (verbatim, 3-15 words), name the issue,
give a one-line hint that does NOT state the fix, an explanation of why it matters academically, and a suggested fix.`;

const SCHEMA = {
  type: 'array',
  maxItems: 4,
  items: {
    type: 'object',
    properties: {
      quote: { type: 'string' },
      ruleName: { type: 'string' },
      hint: { type: 'string' },
      explanation: { type: 'string' },
      suggestion: { type: 'string' },
    },
    required: ['quote', 'ruleName', 'hint', 'explanation', 'suggestion'],
  },
};

export async function promptApiAvailable() {
  try {
    if (!('LanguageModel' in self)) return false;
    return (await self.LanguageModel.availability()) !== 'unavailable';
  } catch {
    return false;
  }
}

export class DeepFeedback {
  constructor(onStatus = () => {}) {
    this.onStatus = onStatus;
    this.session = null;
  }

  async ensure() {
    if (this.session) return this.session;
    this.session = await self.LanguageModel.create({
      initialPrompts: [{ role: 'system', content: SYSTEM }],
      monitor: (m) => m.addEventListener('downloadprogress', (e) => this.onStatus(`Downloading on-device model ${Math.round(e.loaded * 100)}%`)),
    });
    return this.session;
  }

  /** @returns {Promise<{span:{start:number,end:number}, ruleName:string, hint:string, explanation:string, suggestion:string}[]>} */
  async review(text) {
    try {
      const s = await this.ensure();
      const out = await s.prompt(text.slice(-4000), { responseConstraint: SCHEMA });
      const items = JSON.parse(out);
      return items
        .map((it) => {
          const at = text.indexOf(it.quote);
          return at < 0 ? null : { ...it, span: { start: at, end: at + it.quote.length } };
        })
        .filter(Boolean);
    } catch (e) {
      this.onStatus('Deep feedback unavailable right now.');
      return [];
    }
  }

  destroy() {
    this.session?.destroy?.();
    this.session = null;
  }
}
