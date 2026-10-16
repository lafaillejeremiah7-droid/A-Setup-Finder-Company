import './styles.css';
import { createLinter } from './grammar/harper-adapter.js';
import { analyze } from './app/analyzer.js';
import { IssueTracker } from './coach/hint-ladder.js';
import { LevelSmoother } from './scoring/smoother.js';
import { SessionStore } from './session/session-store.js';
import { cleanOcr, normalizeTyped } from './text/cleanup.js';
import { words } from './text/tokenize.js';
import { ScreenCapture } from './capture/screen-capture.js';
import { attachCropSelector } from './capture/crop-selector.js';
import { promptApiAvailable, DeepFeedback } from './ai/prompt-api.js';
import { makeIssue } from './issues/schema.js';
import * as ui from './ui/render.js';

const $ = (id) => document.getElementById(id);

const tracker = new IssueTracker();
const smoother = new LevelSmoother();
const store = new SessionStore();
let linterPromise = createLinter('worker');
let ocrPromise = null;
let mode = 'ocr';
let seq = 0;
let lastText = '';
let lastLevel = null;
let aiIssues = [];
let deep = null;

const status = (msg) => ($('ocr-status').textContent = msg);

// ---------- analysis pipeline ----------

/** Drop issues on the word still being typed (no trailing space/punctuation yet). */
function dropTrailingWord(issues, text) {
  if (/[\s.,;:!?)"']$/.test(text)) return issues;
  const end = text.length;
  return issues.filter((i) => i.span.end < end);
}

async function runAnalysis(text, opts = {}) {
  const my = ++seq;
  const linter = await linterPromise;
  const extra = aiIssues.filter((a) => text.slice(a.span.start, a.span.end) === a.text);
  const result = await analyze(text, linter, { ...opts, extraIssues: extra });
  if (my !== seq) return; // a newer read superseded this one
  const issues = opts.typing ? dropTrailingWord(result.issues, text) : result.issues;
  tracker.update(issues, text, { mode });
  lastText = text;
  lastLevel = result.level;
  render();
  if (result.level) {
    const smoothed = smoother.push(result.level, text);
    ui.renderLevel(result.level, smoothed);
    store.record({
      score: smoothed.score,
      dims: result.level.dims,
      words: result.metrics.W,
      issuesByCategory: tracker.byCategory,
      stats: tracker.stats,
    });
    ui.renderTrend(store.current.trend);
    ui.renderFocus(store.whereIStand());
  } else {
    smoother.reset();
    ui.renderLevel(null);
  }
  scheduleDeep(text);
}

function render() {
  const items = tracker.active();
  ui.renderText(lastText, items);
  ui.renderIssues(items);
  ui.renderStats(tracker);
}

// ---------- hint ladder interactions ----------

$('issue-list').addEventListener('click', (e) => {
  const card = e.target.closest('[data-issue-id]');
  if (!card) return;
  const id = card.dataset.issueId;
  const act = e.target.closest('button')?.dataset.act;
  if (act === 'rule') tracker.advance(id);
  else if (act === 'reveal') tracker.reveal(id);
  else {
    const mark = [...document.querySelectorAll('#text-view mark')].find((m) => m.dataset.issueId === id);
    if (mark) {
      mark.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      mark.classList.add('flash');
      setTimeout(() => mark.classList.remove('flash'), 900);
    }
    return;
  }
  render();
  Object.assign(store.current, tracker.stats);
});

$('text-view').addEventListener('click', (e) => {
  const mark = e.target.closest('mark');
  if (!mark) return;
  const card = [...document.querySelectorAll('#issue-list [data-issue-id]')].find((c) => c.dataset.issueId === mark.dataset.issueId);
  card?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
});

// ---------- tabs ----------

document.querySelectorAll('.tab').forEach((tab) =>
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    document.querySelectorAll('.pane').forEach((p) => (p.hidden = p.dataset.pane !== tab.dataset.tab));
    mode = tab.dataset.tab === 'type' ? 'typed' : 'ocr';
    if (mode === 'typed') $('textarea').focus();
  }),
);

// ---------- typed input ----------

let typeTimer;
$('textarea').addEventListener('input', () => {
  mode = 'typed';
  clearTimeout(typeTimer);
  typeTimer = setTimeout(() => runAnalysis(normalizeTyped($('textarea').value), { typing: true }), 600);
});

// ---------- screen capture ----------

const video = $('preview');
const capture = new ScreenCapture(video, {
  onFrame: async (canvas) => {
    if (!ocrPromise) {
      const { createOcr } = await import('./ocr/ocr-client.js');
      ocrPromise = createOcr((s, p) => status(`OCR: ${s}${p != null && p < 1 ? ` ${Math.round(p * 100)}%` : ''}`));
    }
    const ocr = await ocrPromise;
    const t0 = performance.now();
    const raw = await ocr.recognize(canvas);
    const cleaned = cleanOcr(raw.paragraphs);
    const n = words(cleaned.text).length;
    status(
      `Read ${n} words at ${new Date().toLocaleTimeString()} · OCR confidence ${Math.round(cleaned.meanConfidence)}% · ${Math.round(performance.now() - t0)} ms` +
        (n === 0 ? ' · No text found. Drag a box around your writing.' : ''),
    );
    if (mode !== 'ocr') return;
    await runAnalysis(cleaned.text, { ...cleaned, ocrConfidence: cleaned.meanConfidence });
  },
  onEnd: () => {
    $('share-btn').textContent = 'Share screen';
    $('clear-crop-btn').disabled = true;
    $('pause-btn').disabled = true;
    $('preview-empty').hidden = false;
    status('Sharing stopped.');
  },
});
const crop = attachCropSelector($('crop-overlay'), video, (c) => capture.setCrop(c));

$('share-btn').addEventListener('click', async () => {
  if (capture.active) return capture.stop();
  try {
    await capture.start();
    $('share-btn').textContent = 'Stop sharing';
    $('clear-crop-btn').disabled = false;
    $('pause-btn').disabled = false;
    $('preview-empty').hidden = true;
    status('Sharing. Drag a box around your writing for the best accuracy.');
  } catch (e) {
    status(e?.name === 'NotAllowedError' ? 'Screen share was cancelled.' : `Couldn't start screen share: ${e?.message || e}`);
  }
});
$('clear-crop-btn').addEventListener('click', () => crop.clear());
$('pause-btn').addEventListener('click', () => {
  capture.paused = !capture.paused;
  $('pause-btn').textContent = capture.paused ? 'Resume' : 'Pause';
  if (!capture.paused) capture.invalidate();
});

// ---------- optional on-device deep feedback ----------

let deepTimer;
function scheduleDeep(text) {
  clearTimeout(deepTimer);
  if (!deep || words(text).length < 80) return;
  deepTimer = setTimeout(async () => {
    if (text !== lastText) return;
    status('Deep feedback: reviewing argument and cohesion…');
    const found = await deep.review(text);
    aiIssues = found.map((f, n) =>
      makeIssue(
        { span: f.span, category: 'Argument', ruleName: f.ruleName, severity: 'style', hint: f.hint, explanation: f.explanation, suggestion: f.suggestion, rawMessage: null, source: 'ai' },
        text,
        n,
      ),
    );
    if (text === lastText) runAnalysis(text, { ocrConfidence: lastLevel?.confidence === 'low' ? 60 : 100 });
  }, 5000);
}

promptApiAvailable().then((ok) => {
  if (!ok) return;
  $('deep-wrap').hidden = false;
  $('deep-toggle').addEventListener('change', (e) => {
    if (e.target.checked) deep = new DeepFeedback(status);
    else {
      deep?.destroy();
      deep = null;
      aiIssues = [];
    }
  });
});

// ---------- history ----------

ui.renderHistory(store.history());
ui.renderTrend([]);
ui.renderIssues([]);
$('clear-history').addEventListener('click', () => {
  store.clearHistory();
  ui.renderHistory([]);
  ui.renderFocus(store.whereIStand());
});
const saveNow = () => store.save();
addEventListener('beforeunload', saveNow);
document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' && saveNow());

linterPromise.catch((e) => status(`Grammar engine failed to load: ${e?.message || e}`));

// test hook
window.__englishCoach = { tracker, store };
