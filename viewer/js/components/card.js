// User intent: a Kanban card is one real link to its task — opened by click, Enter or a new tab like any link — with
// its copy and doc buttons beside the link, never inside it; states read as a shape and a word, never a glyph or hue.
//
// Usage: renderCard({ task, density: 'full' | 'minimal', epicIndex, groupBy, now, hideBundleChip })
//   epicIndex — Map epicId → { name, swatch } from lib/epics.js#epicIndex; groupBy other than 'status' shows the status.

import { formatTimeInStatus, classifyTimeInStatus, isoToMs, formatAbsolute } from '../lib/time.js';
import { bindCopy } from '../lib/copy.js';
import { truncate } from '../lib/text.js';
import { linkRow } from './link-row.js';
import { icon } from './icon.js';
import { marker, priorityMarker, statusMarker } from './status.js';
import { laneBadge } from './gate-pipeline.js';
import { renderMergeLadderCompact } from './merge-status.js';

const DAY_MS = 24 * 60 * 60 * 1000;

const SPEC_REVIEW = {
  pass: { label: 'Spec passed', shape: '●', tone: 'success' },
  warn: { label: 'Spec warning', shape: '▲', tone: 'warning' },
  fail: { label: 'Spec failed', shape: '◆', tone: 'critical' },
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

// laneBadge and the merge ladder hand back markup with every task value already escaped.
function fromMarkup(html) {
  if (!html) return null;
  const t = document.createElement('template');
  t.innerHTML = html;
  return t.content.firstElementChild;
}

function copyButton(className, focus, label, text) {
  const btn = el('button', className);
  btn.type = 'button';
  btn.dataset.focus = focus;
  btn.setAttribute('aria-label', label);
  btn.append(truncate(text), icon('copy', { size: 12 }));
  bindCopy(btn, text, { flashClass: 'is-copied' });
  return btn;
}

function epicTag(epicId, epicIndex) {
  const known = epicIndex?.get?.(epicId);
  const tag = el('span', 'card-tag card-epic');
  const swatch = el('span', `card-swatch ${known ? `card-swatch--cat-${known.swatch}` : 'card-swatch--none'}`);
  swatch.setAttribute('aria-hidden', 'true');
  tag.append(swatch, truncate(known ? known.name : epicId));
  return tag;
}

function tags(task, { epicIndex, groupBy, hideBundleChip }) {
  const box = el('div', 'card-tags');
  if (task.epic) box.append(epicTag(task.epic, epicIndex));
  if (task.estimate) box.append(el('span', 'card-tag card-estimate', task.estimate));
  const verdict = task.spec_review?.verdict || task.spec_review;
  if (typeof verdict === 'string' && Object.hasOwn(SPEC_REVIEW, verdict)) box.append(marker(SPEC_REVIEW[verdict]));
  const tracker = parseTrackerId(task.tracker_id);
  if (tracker) {
    const tk = el('span', 'card-tag card-tracker', `${tracker.system} ${tracker.key}`);
    tk.title = task.tracker_id;
    box.append(tk);
  }
  if (typeof task.depends_on_unmet_count === 'number' && task.depends_on_unmet_count > 0) {
    box.append(el('span', 'card-tag card-deps', `${task.depends_on_unmet_count} unmet`));
  } else if (Array.isArray(task.depends_on) && task.depends_on.length) {
    box.append(el('span', 'card-tag card-deps', `${task.depends_on.length} deps`));
  }
  if (task.sub_repo) box.append(el('span', 'card-tag card-subrepo', task.sub_repo));
  if (task.bundle && !hideBundleChip) box.append(el('span', 'card-tag card-bundle', `Bundle ${task.bundle}`));
  const lane = fromMarkup(laneBadge(task));
  if (lane) box.append(lane);
  if (task.gate_state) box.append(el('span', 'card-tag card-gate', task.gate_state));
  const ladder = fromMarkup(renderMergeLadderCompact(task));
  if (ladder) box.append(ladder);
  if (groupBy !== 'status' && task.status) box.append(statusMarker('task', task.status));
  return box.childNodes.length ? box : null;
}

function note(task) {
  // The live board sends a count; a full task payload sends the list.
  const blockers = task.blockers_count ?? (Array.isArray(task.blockers) ? task.blockers.length : 0);
  let lead = null;
  let text = '';
  if (task.status === 'in-review' && task.human_action) {
    lead = marker({ label: 'Waiting on you', shape: '▲', tone: 'warning' });
    text = task.human_action;
  } else if (task.status === 'blocked' && blockers) {
    lead = marker({ label: `Blocked by ${blockers}`, shape: '◆', tone: 'critical' });
  }
  if (!lead) return null;
  const box = el('div', 'card-note');
  box.append(lead);
  if (text) box.append(el('span', 'card-note__text', text));
  return box;
}

export function renderCard({ task, density = 'full', epicIndex = new Map(), groupBy = 'status', now = Date.now(), hideBundleChip = false } = {}) {
  if (!task || !task.id) return document.createComment('empty card');
  const full = density !== 'minimal';
  const title = task.title || '(untitled)';

  const startedMs = isoToMs(task.started);
  const recent = !!startedMs && (now - startedMs) < DAY_MS;

  const name = document.createDocumentFragment();
  name.append(el('span', 'card-sr', `${task.id} `), truncate(title, { lines: 3, className: 'card-title' }));

  const content = [];
  if (recent) content.push(el('span', 'card-new', 'New'));
  if (task.priority) {
    const pri = el('span', 'card-pri');
    pri.append(priorityMarker(String(task.priority).toLowerCase()));
    content.push(pri);
  }
  const anchor = startedMs || isoToMs(task.created);
  const age = formatTimeInStatus(anchor, now);
  if (age) {
    const ageEl = el('span', 'card-age' + (classifyTimeInStatus(anchor, now) === 'stale' ? ' card-age--stale' : ''), age);
    ageEl.title = `Since ${formatAbsolute(anchor, { now })}`;
    content.push(ageEl);
  }
  if (full) {
    content.push(tags(task, { epicIndex, groupBy, hideBundleChip }), note(task));
  } else if (groupBy !== 'status' && task.status) {
    const box = el('div', 'card-tags');
    box.append(statusMarker('task', task.status));
    content.push(box);
  }

  const controls = [copyButton('card-id', 'copy-id', `Copy id ${task.id}`, task.id)];
  if (full && task.branch) controls.push(copyButton('card-branch', 'copy-branch', `Copy branch ${task.branch}`, task.branch));
  const docs = full && task.docs ? Object.values(task.docs).filter(Boolean) : [];
  if (docs.length) {
    const btn = el('button', 'btn btn--ghost btn--icon btn--sm card-docs');
    btn.type = 'button';
    btn.dataset.focus = 'docs';
    btn.setAttribute('aria-label', 'Open primary doc');
    btn.append(icon('document', { size: 14 }));
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      window.open(docs[0], '_blank', 'noopener');
    });
    controls.push(btn);
  }

  const card = linkRow({
    tag: 'div',
    className: `card-task ${density}`,
    href: `#/task/${encodeURIComponent(task.id)}`,
    name,
    title,
    content: content.filter(Boolean),
    controls,
  });
  card.dataset.taskId = task.id;
  card.querySelector(':scope > .link-row__link').dataset.focus = 'link';
  // Started within a day (KB-11): a strong border and the "New" tag, no glow.
  if (recent) card.classList.add('recent');
  return card;
}

// Tracker id format: `<system>-<alias>-<key-lowercased>` (e.g. "linear-cm-eng-42").
// Returns null on malformed input rather than throwing — the chip is purely visual.
export function parseTrackerId(trackerId) {
  if (!trackerId || typeof trackerId !== 'string') return null;
  const parts = trackerId.split('-');
  if (parts.length < 3) return null;
  const [system, alias, ...rest] = parts;
  if (!system || !alias || rest.length === 0) return null;
  return { system, alias, key: rest.join('-').toUpperCase() };
}

// Plan 4 dashboard widgets import these by name; thin density-bound aliases over renderCard.
export const renderMinimalCard = (task, opts = {}) => renderCard({ task, density: 'minimal', ...opts });
export const renderFullCard    = (task, opts = {}) => renderCard({ task, density: 'full',    ...opts });
