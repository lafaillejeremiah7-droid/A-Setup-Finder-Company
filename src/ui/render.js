import { bandFor } from '../scoring/level.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** Analyzed text with highlighted issue spans. */
export function renderText(text, items) {
  const el = $('text-view');
  if (!text.trim()) {
    el.innerHTML = '<span class="muted">Nothing yet.</span>';
    return;
  }
  const spans = items.map((i) => i.issue).sort((a, b) => a.span.start - b.span.start);
  let html = '';
  let pos = 0;
  for (const s of spans) {
    if (s.span.start < pos) continue; // skip overlaps
    html += esc(text.slice(pos, s.span.start));
    html += `<mark class="${s.severity}" data-issue-id="${esc(s.id)}">${esc(text.slice(s.span.start, s.span.end))}</mark>`;
    pos = s.span.end;
  }
  el.innerHTML = html + esc(text.slice(pos));
}

/**
 * Issue cards. The fix (suggestion / raw message) is inserted into the DOM only at level 3.
 */
export function renderIssues(items) {
  const list = $('issue-list');
  $('issue-count').textContent = items.length ? `(${items.length})` : '';
  if (!items.length) {
    list.innerHTML = '<li class="muted">No issues in view. Keep writing.</li>';
    return;
  }
  list.innerHTML = items
    .map(({ issue: i, level }) => {
      const quote = i.text.length > 80 ? i.text.slice(0, 77) + '…' : i.text;
      let body = `<div class="row"><div><div class="cat">${esc(i.category)}</div><div class="quote">“${esc(quote)}”</div></div>
        <div class="actions">${level === 1 ? `<button data-act="rule" data-testid="hint-rule-btn">Rule</button>` : ''}${
          level === 2 ? `<button data-act="reveal" data-testid="reveal-btn">Reveal fix</button>` : ''
        }</div></div>
        <div class="hint">${esc(i.hint)}</div>`;
      if (level >= 2) body += `<div class="rule"><b data-testid="rule-name">${esc(i.ruleName)}.</b> ${esc(i.explanation)}</div>`;
      if (level >= 3) {
        body += `<div class="fix" data-testid="suggestion">${i.suggestion ? `Try: <code>${esc(i.suggestion)}</code>` : 'No single fix; rework the phrase using the rule above.'}</div>`;
        if (i.rawMessage) body += `<div class="fix muted">${esc(i.rawMessage)}</div>`;
      }
      return `<li class="issue ${i.severity}" data-testid="issue-card" data-issue-id="${esc(i.id)}">${body}</li>`;
    })
    .join('');
}

export function renderLevel(level, smoothed) {
  if (!level) {
    $('level-band').textContent = '—';
    $('level-score').textContent = '—';
    $('level-confidence').textContent = 'no data';
    $('level-confidence').className = 'pill';
    $('marker').style.display = 'none';
    $('dims').innerHTML = '';
    $('penalty').textContent = '';
    return;
  }
  $('level-band').textContent = smoothed.band;
  $('level-score').textContent = smoothed.score.toFixed(1);
  const conf = $('level-confidence');
  conf.textContent = `${level.confidence} confidence`;
  conf.className = `pill ${level.confidence}`;
  const m = $('marker');
  m.style.display = 'block';
  m.style.left = `calc(${(Math.min(22, smoothed.score) / 22) * 100}% - 2px)`;
  $('penalty').textContent =
    level.penalty > 0.2 ? `Errors are costing you ${level.penalty.toFixed(1)} points (${level.density} weighted errors per 100 words).` : '';
  const labels = { vocabulary: 'Vocabulary', syntax: 'Syntax', readability: 'Readability', accuracy: 'Accuracy' };
  $('dims').innerHTML = Object.entries(level.dims)
    .map(
      ([k, v]) =>
        `<div class="dim" data-testid="dim-${k}"><span>${labels[k]}</span><div class="bar"><i style="width:${(v / 22) * 100}%"></i></div><span class="lab">${bandFor(v)}</span></div>`,
    )
    .join('');
}

export function renderStats(tracker) {
  $('stat-self').textContent = tracker.stats.selfCorrected;
  $('stat-revealed').textContent = tracker.stats.revealed;
  const ind = tracker.independence();
  $('stat-indep').textContent = ind === null ? '—' : `${ind}%`;
  const cats = Object.entries(tracker.byCategory).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...cats.map((c) => c[1]));
  $('categories').innerHTML = cats
    .map(([c, n]) => `<div class="c"><span>${esc(c)}</span><i style="width:${(n / max) * 100}%"></i><span>${n}</span></div>`)
    .join('');
}

export function renderTrend(trend) {
  const svg = $('trend');
  if (trend.length < 2) {
    svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="#8b939e" font-size="11">trend appears as you write</text>';
    return;
  }
  const t0 = trend[0].t;
  const t1 = trend[trend.length - 1].t || t0 + 1;
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * 296 + 2;
  const y = (s) => 88 - (Math.min(22, s) / 22) * 86;
  const grid = [1, 13, 17, 19]
    .map((g) => `<line x1="0" x2="300" y1="${y(g)}" y2="${y(g)}" stroke="#2a3038" stroke-dasharray="2 3"/>`)
    .join('');
  const pts = trend.map((p) => `${x(p.t).toFixed(1)},${y(p.score).toFixed(1)}`).join(' ');
  svg.innerHTML = `${grid}<polyline points="${pts}" fill="none" stroke="#7cc4ff" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
}

export function renderFocus({ focus, comparison }) {
  $('focus').innerHTML = focus.length
    ? focus.map((f) => `<li><div>${esc(f.label)}</div><div class="act">${esc(f.action)}</div></li>`).join('')
    : '<li class="muted">Write at least a paragraph to get focus areas.</li>';
  $('compare').textContent = comparison || '';
}

export function renderHistory(sessions) {
  $('history').innerHTML = sessions.length
    ? sessions
        .slice()
        .reverse()
        .map((s) => `<li>${new Date(s.startedAt).toLocaleString()}: ${s.lastScore != null ? bandFor(s.lastScore) + ' (' + s.lastScore + ')' : '—'} · ${s.wordsAnalyzed} words</li>`)
        .join('')
    : '<li>No past sessions.</li>';
}
