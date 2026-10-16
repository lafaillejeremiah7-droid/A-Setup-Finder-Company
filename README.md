# English Coach

A live academic-English coach you screen-share with. It reads what you're writing, points at problems without handing you the answers, and shows where your writing sits from **Kindergarten → Grade 12 → Undergraduate → Master's → PhD** — updating as you write.

**Open it:** https://lafaillejeremiah7-droid.github.io/A-Setup-Finder-Company/ (desktop Chrome or Edge)

## How to use

1. Click **Share screen** and pick the window you're writing in (Google Docs, Word, anything).
2. **Drag a box** over the preview around just your text. This keeps toolbars and menus out of the reading.
3. Keep writing. Every ~2.5 s the coach re-reads the box when something changed.

You can also use the **Type / paste** tab instead of sharing.

## Not babying you

Each issue has three steps, and you control them:

| Step | You see |
|---|---|
| 1. Nudge | The phrase is underlined, plus a category ("Something's off here: grammar.") |
| 2. **Rule** | The rule's name and why it matters, with no fix |
| 3. **Reveal fix** | The suggested correction |

Fix an issue yourself and it counts as **self-corrected**. Revealing counts against your **independence** score. Fixes only count when the text around them is still in view, so scrolling away doesn't earn credit.

Besides grammar and spelling, it coaches **academic register**: contractions, chatty or vague words, generic "you" / "I think", overclaiming (*proves, always*), weak transitions, run-ons and fragments, heavy passive voice, repetitive sentence openers, and little variety in sentence length.

## How the level is estimated

The score runs from 0 to 22 and blends four dimensions, each shown separately:

- **Readability**: Flesch-Kincaid, Coleman-Liau, SMOG
- **Vocabulary**: word length, academic-vocabulary rate, lexical diversity (MTLD)
- **Syntax**: sentence length (capped, so run-ons earn nothing) and subordinate-clause use
- **Accuracy**: grammar/spelling/mechanics/usage errors per 100 words. Errors pull the overall level down and cap it, so long sentences full of mistakes don't score high.

Bands: <1 Kindergarten · 1–12 Grades · 13–16 Undergraduate · 17–18 Master's · 19+ PhD. Below ~50 words the confidence is low. **These are estimates, not a certified test.**

**Where you stand** lists your top 3 focus areas. Sessions are saved in your browser (numbers only) so you can compare over time.

## Privacy

Everything runs in your browser: grammar checking ([Harper](https://writewithharper.com), WebAssembly), OCR ([Tesseract.js](https://tesseract.projectnaptha.com/), self-hosted) and scoring. No account, no API key, and **nothing you write is sent anywhere or stored**. Only scores and counts are kept, in `localStorage`.

If Chrome's built-in on-device AI is available, a **Deep feedback** toggle appears that adds paragraph-level argument and cohesion hints. It also runs locally.

## Development

```bash
npm install
npm run dev        # local dev server
npm test           # unit tests (vitest)
npx playwright install chromium && npm run test:e2e   # browser tests
npm run build
```

Pushing to `main` deploys to GitHub Pages via `.github/workflows/deploy.yml`.
