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
  if (!task) {
    return h('div', { class: 'td-body td-empty' }, 'task not found');
  }
  return h('main', { class: 'td-body' }, [
    renderMeta(task),
    renderTitle(task),
  ]);
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
