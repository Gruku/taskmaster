// Variant B — Graph layout for Task Detail.
// Renders a compact head, an SVG-based dependency graph, a context band,
// graph controls, and tabs (Spec / Plan / Notes / Activity / Anchors / Raw YAML).

import { computeGraphLayout } from './dependency-graph.js';
import { mountRightRail } from './right-rail.js';
import { renderMarkdown } from './markdown.js';
import { claimTopbar, tmSegmented, tmAction } from '../lib/topbar.js';

export function mountTaskDetailGraph(root, ctx) {
  root.innerHTML = '';
  root.classList.add('td-page', 'td-page-B');

  mountTopbar(ctx);
  root.appendChild(renderHeader(ctx));
  root.appendChild(renderGrid(ctx));
  return () => {
    root.innerHTML = '';
    root.classList.remove('td-page', 'td-page-B');
  };
}

function mountTopbar({ prefs, onToggleVariant }) {
  const topbar = claimTopbar();
  if (!topbar) return;
  const view = prefs?.screens?.task_detail?.view === 'B' ? 'B' : 'A';
  const seg = tmSegmented(
    [
      { key: 'A', label: 'Document' },
      { key: 'B', label: 'Graph' },
    ],
    { value: view, onChange: (v) => onToggleVariant?.(v) },
  );
  const editBtn = tmAction({ icon: '✎', label: 'Edit', title: 'Edit task — coming soon', disabled: true });
  const archiveBtn = tmAction({ icon: '✕', label: 'Archive', title: 'Archive task — coming soon', disabled: true });
  topbar.append(seg, editBtn, archiveBtn);
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

function renderHeader({ task }) {
  return h('div', { class: 'td-ph' }, [
    h('span', { class: 'td-back', on: { click: () => history.back() } }, '‹ back'),
    h('span', { class: 'td-crumb' }, `Tasks / ${task?.epic || ''}`),
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
  mountRightRail(aside, ctx);
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
function renderContextBand({ related, onNavigate }) {
  const lessons   = related?.lessons || [];
  const handovers = related?.handovers || [];
  const issues    = related?.issues || [];
  const band = h('div', { class: 'td-graph-context-band', 'data-test': 'context-band' });
  if (lessons.length) {
    band.appendChild(h('span', { class: 'lbl' }, 'Lessons'));
    for (const l of lessons.slice(0, 4)) {
      band.appendChild(h('span', { class: 'ctx-pill lesson' },
        [h('span', { class: 'glyph' }, '✦'), h('span', { class: 'mono' }, l.id)]));
    }
  }
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
    const btn = h('button', { class: 'gc-btn', 'data-id': b.id, on: { click: (e) => b.toggle(e.currentTarget) } }, b.label);
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
    const tab = h('button', { class: `td-tab ${idx === 0 ? 'on' : ''}`, 'data-tab': id }, label);
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
  div.className = 'md-body';
  div.innerHTML = renderMarkdown(src || '_(empty)_');
  return div;
}
function renderActivityList(lines) {
  const ul = document.createElement('ul');
  for (const l of (lines || []).slice(0, 30)) {
    const li = document.createElement('li');
    li.className = 'mono';
    li.textContent = l;
    ul.appendChild(li);
  }
  if (!ul.children.length) ul.innerHTML = '<li class="td-empty">no activity</li>';
  return ul;
}
function renderAnchors(anchors) {
  const wrap = document.createElement('div');
  if (!anchors.length) { wrap.className = 'td-empty'; wrap.textContent = 'no anchors'; return wrap; }
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
