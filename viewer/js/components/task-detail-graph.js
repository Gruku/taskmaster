// Variant B — Graph layout for Task Detail.
// Renders a compact head, an SVG-based dependency graph, a context band,
// graph controls, and tabs (Spec / Plan / Notes / Activity / Anchors / Raw YAML).

import { computeGraphLayout } from './dependency-graph.js';
import { railPanels } from './right-rail.js';
import { renderMarkdown } from './markdown.js';
import { stateBlock } from './empty-state.js';
import { mountTaskTopbar, openEditForm } from './task-detail-document.js';

export function mountTaskDetailGraph(root, ctx) {
  if (ctx.etag) ctx.store?.setEtag?.(`task:${ctx.task.id}`, ctx.etag);
  root.innerHTML = '';
  root.classList.add('td-doc', 'td-doc--page', 'td-page', 'td-page-B');
  root.dataset.detailLinks = 'follow';

  // The switch shows the view on screen, which is this one.
  mountTaskTopbar({ view: 'B', onToggleVariant: ctx.onToggleVariant, onEdit: () => openEditForm(ctx) });
  root.appendChild(renderGrid(ctx));
  return () => {
    root.innerHTML = '';
    root.classList.remove('td-doc', 'td-doc--page', 'td-page', 'td-page-B');
    delete root.dataset.detailLinks;
  };
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

function renderGrid(ctx) {
  // With nothing related there is no rail, and the body takes the width.
  const panels = railPanels({ task: ctx.task, related: ctx.related, level: 2 });
  const rail = panels.length ? h('aside', { class: 'td-rail', 'data-test': 'rail', 'aria-label': 'Related' }, panels) : null;
  return h('div', { class: `td-grid${rail ? '' : ' td-grid--solo'}` }, [
    renderBody(ctx),
    rail,
  ]);
}

function renderBody(ctx) {
  const body = h('div', { class: 'td-body' });
  body.appendChild(renderCompactHead(ctx.task));
  body.appendChild(renderGraphFrame(ctx));
  body.appendChild(renderTabs(ctx));
  return body;
}

const words = (v) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');

function renderCompactHead(task) {
  const epic = words(task?.epic);
  const parts = [
    h('span', { class: 'td-id-text' }, words(task?.id)),
    h('a', { href: '#/kanban' }, 'Tasks'),
    epic ? h('a', { href: `#/epic/${encodeURIComponent(epic)}` }, epic) : null,
    words(task?.phase) ? h('span', {}, words(task.phase)) : null,
  ].filter(Boolean);
  const meta = h('div', { class: 'td-meta' });
  parts.forEach((part, i) => {
    if (i) meta.appendChild(h('span', { class: 'td-sep', 'aria-hidden': 'true' }, '·'));
    meta.appendChild(part);
  });
  return h('div', { class: 'td-head-block', 'data-test': 'compact-head' }, [
    meta,
    h('h2', { class: 'td-head-title' }, words(task?.title)),
  ]);
}

function renderGraphFrame(ctx) {
  const frame = h('div', { class: 'td-graph-frame', 'data-test': 'graph-frame' });
  const list = (v) => (Array.isArray(v) ? v : []);
  // A task with no neighbours has no graph to draw: say so instead of framing one lonely node.
  if (!list(ctx.related?.dependencies).length && !list(ctx.related?.unblocks).length) {
    frame.classList.add('td-graph-frame--empty');
    frame.appendChild(stateBlock({
      state: 'empty', label: 'Graph', headline: 'No dependencies to draw',
      hint: 'This task depends on nothing and nothing waits on it.',
    }));
    return frame;
  }
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
  const neighbours = (v) => (Array.isArray(v) ? v.filter((d) => d && typeof d === 'object') : []);
  const upstream = neighbours(related?.dependencies).map((d) => ({
    id: d.id, title: d.title, status: d.status, depth: 1,
  }));
  const downstream = neighbours(related?.unblocks).map((d) => ({
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
    g.appendChild(h('rect', { class: 'node-progress-track', x: n.x + 10, y: n.y + n.h - 22, width: barW, height: 3 }));
    g.appendChild(h('rect', { class: 'node-progress-fill', x: n.x + 10, y: n.y + n.h - 22, width: barW * pct, height: 3 }));
    if (n.step) {
      g.appendChild(h('text', { class: 'node-meta', x: n.x + 10, y: n.y + n.h - 26 }, truncate(n.step, 22)));
    }
  }
  return g;
}

function truncate(s, n) { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function renderContextBand({ related, onNavigate }) {
  const handovers = Array.isArray(related?.handovers) ? related.handovers.filter(Boolean) : [];
  const issues    = Array.isArray(related?.issues) ? related.issues.filter(Boolean) : [];
  const band = h('div', { class: 'td-graph-context-band', 'data-test': 'context-band' });
  if (handovers.length) {
    band.appendChild(h('span', { class: 'lbl' }, 'Handovers'));
    for (const ho of handovers.slice(0, 3)) {
      band.appendChild(h('span', { class: 'ctx-pill handover' },
        [h('span', { class: 'glyph' }, '§'), h('span', {}, ho.id)]));
    }
  }
  if (issues.length) {
    band.appendChild(h('span', { class: 'lbl' }, 'Issues'));
    for (const i of issues.slice(0, 4)) {
      band.appendChild(h('span', { class: 'ctx-pill issue' },
        [h('span', { class: 'glyph' }, '!'), h('span', { class: 'mono' }, i.id)]));
    }
  }
  return band;
}

function renderGraphControls(ctx) {
  const wrap = h('div', { class: 'td-graph-controls', 'data-test': 'graph-controls' });
  const buttons = [
    { id: 'depth', label: 'Depth: 2', toggle: () => {} },
    { id: 'show-all', label: 'Show all', toggle: () => {} },
    { id: 'hide-context', label: 'Hide context', toggle: (el) => el.classList.toggle('on') },
    { id: 'fullscreen', label: 'Fullscreen', toggle: (el) => {
        const f = el.closest('.td-graph-frame');
        if (!document.fullscreenElement) f.requestFullscreen?.();
        else document.exitFullscreen?.();
      } },
  ];
  for (const b of buttons) {
    const btn = h('button', { type: 'button', class: 'gc-btn', 'data-id': b.id, on: { click: (e) => b.toggle(e.currentTarget) } }, b.label);
    wrap.appendChild(btn);
  }
  wrap.querySelector('[data-id="hide-context"]').addEventListener('click', (e) => {
    const frame = e.currentTarget.closest('.td-graph-frame');
    frame.querySelector('.td-graph-context-band')?.classList.toggle('hidden');
  });
  return wrap;
}

function renderTabs({ task, related }) {
  const tabs = [
    ['spec',     'Spec',     () => renderMd(task.specification || task.description)],
    ['plan',     'Plan',     () => renderMd(task.plan)],
    ['notes',    'Notes',    () => renderMd(task.notes)],
    ['activity', 'Activity', () => renderActivityList(task.activity || [])],
    ['anchors',  'Anchors',  () => renderAnchors(task.anchors || [])],
    ['raw',      'Raw YAML', () => renderRaw(task)],
  ];
  const wrap = h('div', { class: 'td-tabs-wrap', 'data-test': 'tabs' });
  const bar = h('div', { class: 'td-tabs' });
  const panels = h('div', { class: 'td-tab-panels' });
  tabs.forEach(([id, label, build], idx) => {
    const tab = h('button', { type: 'button', class: `td-tab ${idx === 0 ? 'on' : ''}`, 'data-tab': id }, label);
    const panel = h('div', { class: `td-tab-panel ${idx === 0 ? 'on' : ''}`, 'data-tab-panel': id });
    panel.appendChild(build());
    tab.addEventListener('click', () => {
      bar.querySelectorAll('.td-tab').forEach((t) => t.classList.toggle('on', t === tab));
      panels.querySelectorAll('.td-tab-panel').forEach((p) => p.classList.toggle('on', p.dataset.tabPanel === id));
    });
    bar.appendChild(tab);
    panels.appendChild(panel);
  });
  wrap.appendChild(bar);
  wrap.appendChild(panels);
  return wrap;
}

function renderMd(src) {
  const div = document.createElement('div');
  if (typeof src !== 'string' || !src.trim()) {
    div.className = 'td-empty';
    div.textContent = 'Nothing written.';
    return div;
  }
  div.className = 'md-body';
  // renderMarkdown sanitises; it is the only path task text takes into innerHTML.
  div.innerHTML = renderMarkdown(src);
  return div;
}
function renderActivityList(lines) {
  const ul = document.createElement('ul');
  ul.className = 'td-activity';
  for (const l of (Array.isArray(lines) ? lines : []).slice(0, 30)) {
    const li = document.createElement('li');
    li.textContent = typeof l === 'string' ? l : JSON.stringify(l);
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = '<li class="td-empty">No activity.</li>';
  return ul;
}
function renderAnchors(anchors) {
  const wrap = document.createElement('div');
  anchors = Array.isArray(anchors) ? anchors.filter((a) => typeof a === 'string') : [];
  if (!anchors.length) { wrap.className = 'td-empty'; wrap.textContent = 'No anchors.'; return wrap; }
  for (const a of anchors) {
    const pill = document.createElement('span');
    pill.className = 'td-anchor-pill';
    pill.textContent = a;
    wrap.appendChild(pill);
  }
  return wrap;
}
function renderRaw(task) {
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(task, null, 2);
  return pre;
}
