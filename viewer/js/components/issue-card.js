import { severityGlyph, injectSeverityDefs } from './severity-glyph.js';
import { agingBar } from './aging-bar.js';
import { severityLabel } from '../util/severity-label.js';
import { computeBlocksCount } from '../util/issue-blocks.js';

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

export function issueCard(issue, { tasksIndex = {}, agingCfg, onTaskClick } = {}) {
  injectSeverityDefs();
  const label = issue.severity_label || severityLabel(issue.severity);
  const card = document.createElement('article');
  card.className = 'issue-card';
  card.setAttribute('data-issue-id', issue.id);
  card.setAttribute('data-status', issue.status || 'open');

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
  const sev = document.createElement('span');
  sev.className = 'issue-card__sev-chip';
  sev.dataset.sev = label;
  sev.textContent = label;
  head.appendChild(sev);

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
    sum.textContent = `Repro · ${issue.repro.length} steps · click to expand`;
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
    card.appendChild(agingBar({ ...issue, severity_label: label }, agingCfg));
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
    p.addEventListener('click', () => onTaskClick?.(tid));
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
    const days = Math.max(0, Math.floor((Date.now() - new Date(issue.resolved).getTime()) / 86_400_000));
    when.textContent = issue.status === 'fixed' ? `fixed ${days}d ago` : `closed ${days}d ago`;
  }
  row.appendChild(when);

  return row;
}

export default issueCard;
