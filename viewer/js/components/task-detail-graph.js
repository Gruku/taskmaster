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
  const frame = h('div', { class: 'td-graph-frame', 'data-test': 'graph-frame' });
  frame.appendChild(renderGraphRail());
  frame.appendChild(renderGraphSvg(ctx));
  frame.appendChild(renderContextBand(ctx));
  frame.appendChild(renderGraphControls(ctx));
  return frame;
}

function renderGraphRail() {
  return h('div', { class: 'td-graph-rail' }, [
    h('span', { class: 'axis' }, '← Dependencies | This task | Unblocks →'),
    h('span', { class: 'legend' }, [
      h('span', {}, [h('span', { class: 'dot s-done' }), 'done']),
      h('span', {}, [h('span', { class: 'dot s-progress' }), 'in progress']),
      h('span', {}, [h('span', { class: 'dot s-backlog' }), 'backlog']),
    ]),
  ]);
}

function renderGraphSvg({ task, related, onNavigate }) {
  const upstream = (related?.dependencies || []).map((d) => ({
    id: d.id, title: d.title, status: d.status, depth: 1,
  }));
  const downstream = (related?.unblocks || []).map((d) => ({
    id: d.id, title: d.title, status: d.status, depth: 1,
  }));
  const layout = computeGraphLayout({
    center: { id: task.id, title: task.title, status: task.status,
              priority: task.priority, estimate: task.estimate,
              time_in_status: task.time_in_status,
              progress: task.auto_mode?.progress ?? null,
              step: task.auto_mode?.step ?? null },
    upstream, downstream,
    width: 820, height: 320,
  });

  const svg = h('svg', {
    class: 'td-graph-svg', viewBox: '0 0 820 320',
    preserveAspectRatio: 'xMidYMid meet',
    'data-test': 'graph-svg',
  });

  for (const col of layout.columns) {
    svg.appendChild(h('line', { class: 'col-guide', x1: col.x, y1: 14, x2: col.x, y2: 314 }));
    svg.appendChild(h('text', { class: 'col-label', x: col.x, y: 14, 'text-anchor': 'middle' }, col.label));
  }
  for (const e of layout.edges) {
    svg.appendChild(h('path', { class: 'edge-path', d: e.path }));
  }
  for (const n of layout.nodes) {
    svg.appendChild(renderNode(n, onNavigate));
  }
  return svg;
}

function renderNode(n, onNavigate) {
  const g = h('g', { class: 'node', 'data-id': n.id, on: { click: () => onNavigate?.(n.id) } });
  g.appendChild(h('rect', {
    class: `node-rect ${n.isCenter ? 'center' : ''} ${n.faded ? 'faded' : ''}`,
    x: n.x, y: n.y, width: n.w, height: n.h, rx: 6, ry: 6,
  }));
  g.appendChild(h('circle', {
    class: `status-dot s-${(n.status || '').replace(/[^a-z]/gi, '')}`,
    cx: n.x + 10, cy: n.y + 12, r: 4,
  }));
  g.appendChild(h('text', { class: 'node-id', x: n.x + 20, y: n.y + 16 }, n.id));
  if (n.time_in_status) {
    g.appendChild(h('text', { class: 'node-meta', x: n.x + n.w - 8, y: n.y + 16, 'text-anchor': 'end' }, n.time_in_status));
  }
  g.appendChild(h('text', { class: 'node-title', x: n.x + 10, y: n.y + 36 }, truncate(n.title, n.isCenter ? 20 : 14)));
  if (n.priority || n.estimate) {
    g.appendChild(h('text', { class: 'node-meta', x: n.x + 10, y: n.y + n.h - 8 },
      [n.priority, n.estimate].filter(Boolean).join(' · ')));
  }
  if (n.isCenter && (n.progress != null || n.step)) {
    const barW = n.w - 20;
    const pct = Math.max(0, Math.min(1, n.progress || 0));
    g.appendChild(h('rect', { x: n.x + 10, y: n.y + n.h - 22, width: barW, height: 3, fill: 'rgba(255,255,255,0.05)' }));
    g.appendChild(h('rect', { x: n.x + 10, y: n.y + n.h - 22, width: barW * pct, height: 3, fill: 'var(--accent)' }));
    if (n.step) {
      g.appendChild(h('text', { class: 'node-meta', x: n.x + 10, y: n.y + n.h - 26 }, truncate(n.step, 22)));
    }
  }
  return g;
}

function truncate(s, n) { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function renderTabs(ctx) {
  return h('div', { class: 'td-tabs-placeholder', 'data-test': 'tabs' }, '');
}
