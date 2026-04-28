// Variant B — Graph layout for Task Detail.
// Renders a compact head, an SVG-based dependency graph, a context band,
// graph controls, and tabs (Spec / Plan / Notes / Activity / Anchors / Raw YAML).

import { computeGraphLayout } from './dependency-graph.js';
import { mountRightRail } from './right-rail.js';
import { renderMarkdown } from './markdown.js';

export function mountTaskDetailGraph(root, ctx) {
  root.innerHTML = '';
  root.classList.add('td-page', 'td-page-B');

  root.appendChild(renderHeader(ctx));
  root.appendChild(renderGrid(ctx));
  return () => { root.innerHTML = ''; };
}

function h(tag, attrs = {}, children = []) {
  const NS = tag === 'svg' || tag === 'g' || tag === 'path' || tag === 'rect' || tag === 'text' || tag === 'circle' || tag === 'line';
  const el = NS ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') NS ? el.setAttribute('class', v) : (el.className = v);
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

function renderBody(ctx) {
  const main = h('main', { class: 'td-body' });
  main.appendChild(renderCompactHead(ctx.task));
  main.appendChild(renderGraphFrame(ctx));
  main.appendChild(renderTabs(ctx));
  return main;
}

function renderCompactHead(task) {
  return h('div', { class: 'td-head-block', 'data-test': 'compact-head' }, [
    h('div', { class: 'td-doc-meta' }, [
      h('span', { class: 'td-id mono' }, task?.id || ''),
      h('span', { class: 'td-sep' }, '·'),
      h('span', {}, task?.epic || ''),
      h('span', { class: 'td-sep' }, '·'),
      h('span', {}, task?.phase || ''),
    ]),
    h('h2', { class: 'td-head-title' }, task?.title || ''),
  ]);
}

function renderRail(ctx) {
  const aside = h('aside', { class: 'td-rail-mount', 'data-test': 'rail' });
  queueMicrotask(() => mountRightRail(aside, ctx));
  return aside;
}

function renderGraphFrame(ctx) {
  return h('div', { class: 'td-graph-placeholder', 'data-test': 'graph-frame' }, '');
}
function renderTabs(ctx) {
  return h('div', { class: 'td-tabs-placeholder', 'data-test': 'tabs' }, '');
}
