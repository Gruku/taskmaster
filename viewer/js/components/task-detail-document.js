// Variant A — Document layout for Task Detail.
// Exports `mountTaskDetailDocument(root, { task, related, prefs, onNavigate })`.

import { renderMarkdown } from './markdown.js';
import { mountRightRail } from './right-rail.js';

export function mountTaskDetailDocument(root, ctx) {
  root.innerHTML = '';
  root.classList.add('td-page', 'td-page-A');

  root.appendChild(renderHeader(ctx));
  root.appendChild(renderGrid(ctx));

  return () => { root.innerHTML = ''; };
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

function renderHeader({ task, prefs, onToggleVariant }) {
  return h('div', { class: 'td-ph' }, [
    h('span', { class: 'td-back', on: { click: () => history.back() } }, '‹ back'),
    h('span', { class: 'td-crumb' }, `Tasks / ${task?.epic || ''}`),
    h('div', { class: 'td-right' }, [
      h('div', { class: 'td-seg', 'data-test': 'view-toggle' }, [
        h('button', { class: 'td-seg-btn ' + (prefs?.screens?.task_detail?.view === 'A' ? 'on' : ''), 'data-view': 'A', on: { click: () => onToggleVariant?.('A') } }, 'Document'),
        h('button', { class: 'td-seg-btn ' + (prefs?.screens?.task_detail?.view === 'B' ? 'on' : ''), 'data-view': 'B', on: { click: () => onToggleVariant?.('B') } }, 'Graph'),
      ]),
      h('button', { class: 'td-action' }, 'Edit'),
      h('button', { class: 'td-action' }, 'Archive'),
    ]),
  ]);
}

function renderGrid(ctx) {
  return h('div', { class: 'td-grid' }, [
    renderBody(ctx),
    renderRail(ctx),
  ]);
}

function renderBody({ task }) {
  if (!task) return h('div', { class: 'td-body td-empty' }, 'task not found');
  const children = [
    renderMeta(task),
    renderTitle(task),
  ];
  if (task.locked_by) {
    children.push(h('div', { class: 'td-lock-banner', 'data-test': 'lock-banner' },
      [h('span', {}, '🔒 '), h('span', {}, `locked by ${task.locked_by}`)]));
  }
  children.push(renderChips(task));
  children.push(renderSpecReview(task));
  children.push(renderAutoBanner(task));
  return h('main', { class: 'td-body' }, children.filter(Boolean));
}

function renderAutoBanner(task) {
  const am = task.auto_mode;
  if (!am || !am.running) return null;
  const pct = Math.max(0, Math.min(100, Math.round((am.progress || 0) * 100)));
  return h('div', { class: 'td-auto-banner', 'data-test': 'auto-banner' }, [
    h('span', { class: 'td-auto-step' }, am.step || 'auto-mode running'),
    h('div', { class: 'td-auto-bar' }, h('span', { style: `width:${pct}%` })),
    h('span', { class: 'td-auto-elapsed' }, am.elapsed || ''),
  ]);
}

function renderSpecReview(task) {
  const sr = task.spec_review;
  if (!sr || !sr.verdict) return null;
  const note = sr.codex_note || '';
  return h('div', { class: 'td-spec-block', 'data-test': 'spec-review' }, [
    h('span', { class: `td-spec-badge ${sr.verdict}`,
                on: { click: (e) => e.currentTarget.nextElementSibling?.classList.toggle('open') } },
      sr.verdict.toUpperCase()),
    h('span', { class: 'td-codex-note serif' }, note),
  ]);
}

function renderChips(task) {
  const epicColorVar = `--epic-1`;
  const priClass = (task.priority || '').toLowerCase();
  const chips = [
    h('span', { class: 'td-status-pill' }, task.status || 'unknown'),
    h('span', { class: `td-pri-pill ${priClass === 'critical' ? 'crit' : priClass}` }, task.priority || ''),
    task.estimate ? h('span', { class: 'td-size-chip' }, task.estimate) : null,
    task.epic ? h('span', { class: 'td-epic-chip', style: `--epic-1: var(${epicColorVar})` },
      [h('span', { class: 'td-swatch' }), h('span', {}, task.epic)]) : null,
    task.branch ? h('span', { class: 'td-branch', 'data-test': 'branch', on: { click: (e) => copyToChip(e.currentTarget, task.branch) } },
      [h('span', { class: 'td-id-text' }, `⎇ ${task.branch}`)]) : null,
    task.worktree ? h('span', { class: 'td-worktree', on: { click: (e) => copyToChip(e.currentTarget, task.worktree) } },
      [h('span', { class: 'td-id-text' }, `⌂ ${task.worktree}`)]) : null,
    task.release ? h('span', { class: 'td-release' }, task.release) : null,
    task.sub_repo ? h('span', { class: 'td-subrepo' }, `· ${task.sub_repo}`) : null,
  ].filter(Boolean);
  return h('div', { class: 'td-chips', 'data-test': 'chips' }, chips);
}

async function copyToChip(el, value) {
  try { await navigator.clipboard.writeText(value); } catch {}
  el.classList.add('copied');
  setTimeout(() => el.classList.remove('copied'), 900);
}

function renderMeta(task) {
  return h('div', { class: 'td-doc-meta', 'data-test': 'meta' }, [
    h('span', { class: 'td-id', 'data-test': 'task-id' },
      [h('span', { class: 'td-id-text' }, task.id || '—')]),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, task.epic || ''),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, task.phase || ''),
    h('span', { class: 'td-sep' }, '·'),
    h('span', {}, `created ${task.created || ''}`),
  ]);
}

function renderTitle(task) {
  return h('h1', { class: 'td-title', 'data-test': 'title' }, task.title || '');
}

function renderRail(ctx) {
  const aside = h('aside', { class: 'td-rail-mount', 'data-test': 'rail' });
  queueMicrotask(() => mountRightRail(aside, ctx));
  return aside;
}
