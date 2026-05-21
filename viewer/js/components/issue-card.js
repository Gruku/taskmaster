import { severityGlyph, injectSeverityDefs } from './severity-glyph.js';
import { agingBar } from './aging-bar.js';
import { severityLabel } from '../util/severity-label.js';
import { pluralize } from '../util/pluralize.js';
import { computeBlocksCount } from '../util/issue-blocks.js';
import { formatRelative } from '../lib/time.js';

const LOCATION_RE = /^(.*?):(\d+)(?::\d+)?$/;

function _renderLocation(loc) {
  const el = document.createElement('div');
  el.className = 'issue-card__location';
  el.textContent = 'at ';
  for (const item of loc) {
    const m = LOCATION_RE.exec(item);
    if (m) {
      el.appendChild(document.createTextNode(`${m[1]}:`));
      const num = document.createElement('span');
      num.className = 'issue-card__location-num';
      num.textContent = m[2];
      el.appendChild(num);
    } else {
      el.appendChild(document.createTextNode(item));
    }
    el.appendChild(document.createTextNode('  '));
  }
  return el;
}

export function issueCard(issue, { tasksIndex = {}, agingCfg, onTaskClick, suppressSeverityChip = false } = {}) {
  injectSeverityDefs();
  const label = issue.severity_label || severityLabel(issue.severity);
  const url = `#/issue/${encodeURIComponent(issue.id)}`;
  const card = document.createElement('a');
  card.className = 'issue-card';
  card.href = url;
  card.setAttribute('data-issue-id', issue.id);
  card.setAttribute('data-status', issue.status || 'open');

  card.addEventListener('click', (ev) => {
    // Let real link behavior take over for modified clicks (new tab, etc.)
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
    if (ev.target.closest('.issue-card__task-pill')) { ev.preventDefault(); return; }
    if (ev.target.closest('summary')) { ev.preventDefault(); return; }
    // Default anchor navigation handles the rest; do nothing here.
  });

  // ---- head: glyph · id · title · sev chip · blocks chip
  const head = document.createElement('div');
  head.className = 'issue-card__head';
  head.appendChild(severityGlyph(label));
  const id = document.createElement('span');
  id.className = 'issue-card__id';
  id.dataset.sev = label;
  id.textContent = issue.id;
  head.appendChild(id);
  const title = document.createElement('span');
  title.className = 'issue-card__title';
  title.textContent = issue.title;
  head.appendChild(title);
  if (!suppressSeverityChip) {
    const sev = document.createElement('span');
    sev.className = 'issue-card__sev-chip';
    sev.dataset.sev = label;
    sev.textContent = label;
    head.appendChild(sev);
  }

  const blocks = computeBlocksCount(issue, tasksIndex);
  if (blocks > 0) {
    const chip = document.createElement('span');
    chip.className = 'issue-card__blocks';
    chip.textContent = `⊘ blocks ${blocks}`;
    head.appendChild(chip);
  }
  card.appendChild(head);

  // ---- console-style location
  if (issue.location && issue.location.length) {
    card.appendChild(_renderLocation(issue.location));
  }

  // ---- italic-serif symptom
  if (issue.symptom) {
    const sym = document.createElement('div');
    sym.className = 'issue-card__symptom';
    sym.textContent = issue.symptom;
    card.appendChild(sym);
  }

  // ---- repro block (collapsed by default in live columns)
  if (issue.repro && issue.repro.length) {
    const det = document.createElement('details');
    det.className = 'issue-card__repro';
    const sum = document.createElement('summary');
    sum.className = 'issue-card__repro-summary';
    sum.textContent = `Repro · ${issue.repro.length} ${pluralize(issue.repro.length, 'step', 'steps')} · click to expand`;
    det.appendChild(sum);
    const ol = document.createElement('ol');
    ol.className = 'issue-card__repro-list';
    for (const step of issue.repro) {
      const li = document.createElement('li');
      li.textContent = step;
      ol.appendChild(li);
    }
    det.appendChild(ol);
    card.appendChild(det);
  }

  // ---- impact paragraph
  if (issue.impact) {
    const imp = document.createElement('div');
    imp.className = 'issue-card__impact';
    imp.innerHTML = issue.impact.replace(/`([^`]+)`/g, '<code>$1</code>');
    card.appendChild(imp);
  }

  // ---- aging bar
  if (agingCfg) {
    const bar = agingBar({ ...issue, severity_label: label }, agingCfg);
    if (bar) card.appendChild(bar);
  }

  // ---- evidence sentence
  if (issue.evidence) {
    const ev = document.createElement('div');
    ev.className = 'issue-card__evidence';
    const lbl = document.createElement('span');
    lbl.className = 'lbl';
    lbl.textContent = 'Evidence: ';
    ev.appendChild(lbl);
    ev.appendChild(document.createTextNode(issue.evidence));
    card.appendChild(ev);
  }

  // ---- promoted-from bug links
  if (issue.promoted_from && Array.isArray(issue.promoted_from) && issue.promoted_from.length) {
    const pf = document.createElement('div');
    pf.className = 'issue-card__promoted-from';
    const lbl = document.createElement('span');
    lbl.className = 'lbl';
    lbl.textContent = 'Promoted from: ';
    pf.appendChild(lbl);
    for (const [i, b] of issue.promoted_from.entries()) {
      if (i > 0) pf.appendChild(document.createTextNode(', '));
      const a = document.createElement('a');
      a.href = `#/bug/${encodeURIComponent(b)}`;
      a.textContent = b;
      pf.appendChild(a);
    }
    card.appendChild(pf);
  }

  // ---- footer: task pills + investigating tag
  const footer = document.createElement('div');
  footer.className = 'issue-card__footer';
  const pills = document.createElement('div');
  pills.className = 'issue-card__task-pills';
  for (const tid of (issue.related_tasks || [])) {
    const p = document.createElement('span');
    p.className = 'issue-card__task-pill';
    p.textContent = tid;
    p.addEventListener('click', (ev) => { ev.preventDefault(); ev.stopPropagation(); onTaskClick?.(tid); });
    pills.appendChild(p);
  }
  footer.appendChild(pills);
  if (issue.status === 'investigating') {
    const tag = document.createElement('span');
    tag.className = 'issue-card__investigating';
    tag.textContent = 'looking at it';
    footer.appendChild(tag);
  }
  card.appendChild(footer);

  return card;
}

export function issueRow(issue) {
  const label = issue.severity_label || severityLabel(issue.severity);
  const row = document.createElement('div');
  row.className = 'issue-row';
  row.setAttribute('data-issue-id', issue.id);

  const glyph = severityGlyph(label);
  glyph.classList.add('issue-row__glyph');
  row.appendChild(glyph);

  const id = document.createElement('span');
  id.className = 'issue-card__id';
  id.dataset.sev = label;
  id.textContent = issue.id;
  row.appendChild(id);

  const mark = document.createElement('span');
  mark.className = `issue-row__mark issue-row__mark--${issue.status}`;
  mark.textContent = issue.status === 'fixed' ? 'Fixed' : 'Wontfix';
  row.appendChild(mark);

  const title = document.createElement('span');
  title.className = 'issue-row__title';
  title.textContent = issue.title;
  row.appendChild(title);

  const tp = document.createElement('span');
  tp.className = 'issue-card__task-pill';
  tp.textContent = (issue.related_tasks && issue.related_tasks[0]) || '';
  row.appendChild(tp);

  const when = document.createElement('span');
  when.className = 'issue-row__when';
  if (issue.resolved) {
    const rel = formatRelative(issue.resolved);
    when.textContent = `${issue.status === 'fixed' ? 'fixed' : 'closed'} ${rel}`;
  }
  row.appendChild(when);

  return row;
}

export default issueCard;
