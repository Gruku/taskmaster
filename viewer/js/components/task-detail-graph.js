// User intent: the task page's Graph view — the same page head and h1 as the Document view, the task between what it
// depends on and what it unblocks, each neighbour a link the keyboard reaches that says its status in a shape and a
// word, a context band, and the task's documents behind real tabs. No control that does nothing.

import { computeGraphLayout } from './dependency-graph.js';
import { railPanels } from './right-rail.js';
import { stateBlock } from './empty-state.js';
import { statusMeta, priorityMeta, statusMarker, priorityMarker } from './status.js';
import { linkRoute } from './link-pills.js';
import { detailHead, detailTitle, markdownBody, detailGrid } from './detail-page.js';
import { mountTaskTopbar, openEditForm, taskMeta } from './task-detail-document.js';
import { h } from '../util/h.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
let seq = 0;

// `ctx.viewState` is what `dispose.viewState()` of the graph this one replaces returned: a repaint of the same task (a
// poll, another writer) keeps the open tab, the hidden context band and where the canvas was scrolled.
export function mountTaskDetailGraph(root, ctx) {
  if (ctx.etag) ctx.store?.setEtag?.(`task:${ctx.task.id}`, ctx.etag);
  const task = ctx.task && typeof ctx.task === 'object' ? ctx.task : {};
  const timers = new Set();
  const uid = `tdg${++seq}`;
  const kept = ctx.viewState && typeof ctx.viewState === 'object' ? ctx.viewState : {};
  // A frame filling the screen survives a repaint of its own task: fullscreen cannot be asked for again without a click.
  const keptFrame = kept.taskId === task.id && kept.frame?.isConnected && root.contains(kept.frame) ? kept.frame : null;
  const out = keptFrame ? document.createElement('div') : root;
  if (!keptFrame) root.replaceChildren();
  root.classList.add('td-doc', 'td-doc--page', 'td-page', 'td-page-B');
  // A node is a link to its task; on this page it navigates rather than opening a modal over this one.
  root.dataset.detailLinks = 'follow';

  // The switch shows the view on screen, which is this one.
  mountTaskTopbar({ view: 'B', onToggleVariant: ctx.onToggleVariant, onEdit: () => openEditForm(ctx) });

  const markers = h('div', { class: 'td-markers', 'data-test': 'chips' }, [
    statusMarker('task', task.status), priorityMarker(task.priority),
  ]);
  out.appendChild(detailHead({ meta: taskMeta(task, { timers }), title: detailTitle(words(task.title)), after: [markers] }));
  const freshFrame = renderGraphFrame(task, ctx.related, uid, kept);
  const body = h('div', { class: 'td-body' }, [freshFrame, renderTabs(task, uid, kept.tab)]);
  out.appendChild(detailGrid({ body, panels: railPanels({ task, related: ctx.related, level: 2 }) }));
  if (keptFrame && !graft(root, out, keptFrame, freshFrame)) root.replaceChildren(...out.childNodes);
  // A canvas larger than its frame opens on this task, not on its first neighbour — or where a repaint found it. On a
  const canvas = root.querySelector('.td-graph-canvas');
  if (canvas) {
    // Where the canvas opens: on this task, whichever way it is centred (the nodes are wide, so the left edge cut it).
    const own = canvas.querySelector('.node--center rect')?.getBoundingClientRect();
    const view = canvas.getBoundingClientRect();
    const toOwn = own ? canvas.scrollLeft + (own.left + own.width / 2) - (view.left + view.width / 2) : (canvas.scrollWidth - canvas.clientWidth) / 2;
    canvas.scrollLeft = Number.isFinite(kept.scrollLeft) ? kept.scrollLeft : toOwn;
    canvas.scrollTop = Number.isFinite(kept.scrollTop) ? kept.scrollTop : (canvas.scrollHeight - canvas.clientHeight) / 2;
  }
  // A canvas wider than its frame says so: otherwise a cut column reads as a broken one.
  const hint = root.querySelector('.td-graph-hint');
  const sayScrolls = () => { if (canvas && hint) hint.hidden = canvas.scrollWidth <= canvas.clientWidth + 1; };
  sayScrolls();
  window.addEventListener('resize', sayScrolls);
  const fullscreen = root.querySelector('[data-focus="graph:fullscreen"]');
  const sayFullscreen = () => fullscreen?.sayState();
  document.addEventListener('fullscreenchange', sayFullscreen);
  sayFullscreen();

  // `{ keepFrame: true }`: the next mount repaints this same task and grafts its content around the frame.
  const dispose = ({ keepFrame = false } = {}) => {
    document.removeEventListener('fullscreenchange', sayFullscreen);
    window.removeEventListener('resize', sayScrolls);
    for (const timer of timers) clearTimeout(timer);
    if (keepFrame) return;
    // The frame is about to go: never leave the screen filled by a node that is no longer there.
    if (document.fullscreenElement && root.contains(document.fullscreenElement)) document.exitFullscreen?.();
    root.replaceChildren();
    root.classList.remove('td-doc', 'td-doc--page', 'td-page', 'td-page-B');
    delete root.dataset.detailLinks;
  };
  dispose.viewState = () => ({
    tab: root.querySelector('.td-tab[aria-selected="true"]')?.dataset.tab ?? null,
    contextHidden: root.querySelector('[data-test="context-band"]')?.hidden ?? false,
    scrollLeft: canvas?.scrollLeft, scrollTop: canvas?.scrollTop,
    taskId: task.id,
    // Only a frame that fills the screen is worth keeping; otherwise a repaint simply replaces it.
    frame: document.fullscreenElement && root.contains(document.fullscreenElement) ? document.fullscreenElement : null,
  });
  return dispose;
}

// Puts `fresh`'s content into `root` without `keep` (the live frame) ever leaving the document: along the path from
// root down to keep, every other child is swapped for its fresh twin, and keep takes freshKeep's class and children.
// Returns false when the two trees do not share that path's shape, so the caller replaces everything instead.
function graft(root, fresh, keep, freshKeep) {
  const path = (node, top) => { const p = []; for (let n = node; n && n !== top; n = n.parentNode) p.unshift(n); return p; };
  const live = path(keep, root), twin = path(freshKeep, fresh);
  if (live.length !== twin.length || live[0]?.parentNode !== root || twin[0]?.parentNode !== fresh) return false;
  const parents = [root, ...live.slice(0, -1)], freshParents = [fresh, ...twin.slice(0, -1)];
  parents.forEach((parent, i) => {
    const before = [], after = [];
    let seen = false;
    for (const c of [...freshParents[i].childNodes]) { if (c === twin[i]) seen = true; else (seen ? after : before).push(c); }
    for (const c of [...parent.childNodes]) if (c !== live[i]) c.remove();
    live[i].before(...before);
    live[i].after(...after);
    if (i > 0) parent.className = freshParents[i].className;
  });
  keep.className = freshKeep.className;
  keep.replaceChildren(...freshKeep.childNodes);
  return true;
}

// Task data is written by many hands: a field of the wrong type is shown as nothing, never as "[object Object]".
const words = (v) => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const list = (v) => (Array.isArray(v) ? v : []);

function s(tag, attrs = {}, children = []) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, v);
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

function renderGraphFrame(task, related, uid, kept) {
  const frame = h('div', { class: 'td-graph-frame', 'data-test': 'graph-frame' });
  // A task with no neighbours has no graph to draw: say so instead of framing one lonely node.
  if (!list(related?.dependencies).length && !list(related?.unblocks).length) {
    frame.classList.add('td-graph-frame--empty');
    frame.appendChild(stateBlock({
      state: 'empty', label: 'Graph', headline: 'No dependencies to draw',
      hint: 'This task depends on nothing and nothing waits on it.',
    }));
    return frame;
  }
  frame.appendChild(h('div', { class: 'td-graph-canvas' }, renderGraphSvg(task, related)));
  frame.appendChild(h('p', { class: 'td-graph-hint', hidden: true, 'data-test': 'graph-hint' }, 'Scroll sideways for the rest of the graph'));
  const band = renderContextBand(related, uid);
  if (band) {
    band.hidden = kept.contextHidden === true;
    frame.appendChild(band);
  }
  const controls = renderGraphControls(frame, band);
  if (controls) frame.appendChild(controls);
  return frame;
}

// The columns that name the sides; the outer two (depth 2) are never drawn, as every neighbour here is one step away.
const SIDE_LABELS = { '-1': 'Depends on', 0: 'This task', 1: 'Unblocks' };

function renderGraphSvg(task, related) {
  const neighbours = (v) => list(v)
    .filter((d) => d && typeof d === 'object' && words(d.id))
    .map((d) => ({ id: words(d.id), title: words(d.title), status: words(d.status), priority: words(d.priority), estimate: words(d.estimate), depth: 1 }));
  const upstream = neighbours(related?.dependencies);
  const downstream = neighbours(related?.unblocks);
  const id = words(task.id);
  const layout = computeGraphLayout({
    center: { id, title: words(task.title), status: words(task.status),
              priority: words(task.priority), estimate: words(task.estimate),
              time_in_status: words(task.time_in_status),
              progress: task.auto_mode?.progress ?? null,
              step: words(task.auto_mode?.step) || null },
    upstream, downstream,
    // Wide enough that a title shows ~25 characters: the canvas scrolls inside its frame where the screen is narrower.
    width: 1000, height: 320, nodeW: 160, centerW: 170,
  });

  // The canvas is cut to what is drawn and shown at its own size: a long side is never clipped out of sight (its nodes
  // are links the keyboard still reaches), and node text is never scaled below its 12px.
  const top = Math.min(...layout.nodes.map((n) => n.y));
  const bottom = Math.max(...layout.nodes.map((n) => n.y + n.h));
  const left = Math.min(...layout.nodes.map((n) => n.x)) - 16;
  const right = Math.max(...layout.nodes.map((n) => n.x + n.w)) + 16;
  const box = { x: left, y: top - 34, w: right - left, h: bottom + 16 - (top - 34) };
  const svg = s('svg', {
    class: 'td-graph-svg', viewBox: `${box.x} ${box.y} ${box.w} ${box.h}`, width: box.w, height: box.h,
    role: 'group', 'aria-label': `Dependencies and unblocks of ${id}`,
    'data-test': 'graph-svg',
  });

  const used = new Set(layout.nodes.map((n) => n.column));
  for (const col of layout.columns) {
    if (!Object.hasOwn(SIDE_LABELS, col.depth) || !used.has(col.depth)) continue;
    svg.appendChild(s('line', { class: 'col-guide', x1: col.x, y1: top - 10, x2: col.x, y2: bottom + 6, 'aria-hidden': 'true' }));
    svg.appendChild(s('text', { class: 'col-label', x: col.x, y: top - 16, 'text-anchor': 'middle', 'aria-hidden': 'true' }, SIDE_LABELS[col.depth]));
  }
  for (const e of layout.edges) {
    svg.appendChild(s('path', { class: 'edge-path', d: e.path, 'aria-hidden': 'true' }));
  }
  for (const n of layout.nodes) svg.appendChild(renderNode(n));
  return svg;
}

// Character budgets for a node's lines: JetBrains Mono at 12px is about 7.2 units a character, DM Sans about 6.6.
const PAD = 18;
const monoChars = (n) => Math.floor((n.w - PAD) / 7.2);
const sansChars = (n) => Math.floor((n.w - PAD) / 6.6);

function renderNode(n) {
  const status = statusMeta('task', n.status);
  const parts = [status.label, n.priority ? priorityMeta(n.priority).label : '', words(n.estimate)].filter(Boolean);
  const full = [n.id, n.title, ...parts].filter(Boolean).join(' · ');
  const g = n.isCenter
    ? s('g', { class: 'node node--center', 'data-id': n.id, role: 'img', 'aria-label': `This task: ${[n.id, n.title].filter(Boolean).join(' · ')}` },
      [s('title', {}, [full, n.step].filter(Boolean).join(' · '))])
    : s('a', { class: 'node node--link', href: `#/task/${encodeURIComponent(n.id)}`, 'data-id': n.id }, [s('title', {}, full)]);

  g.appendChild(s('rect', {
    class: `node-rect${n.isCenter ? ' center' : ''}${n.faded ? ' faded' : ''}`,
    x: n.x, y: n.y, width: n.w, height: n.h, rx: 6, ry: 6,
  }));
  g.appendChild(svgShape(status.shape, n.x + 12, n.y + 12, status.tone));
  g.appendChild(s('text', { class: 'node-id', x: n.x + 21, y: n.y + 16 }, n.id));
  if (n.time_in_status) {
    g.appendChild(s('text', { class: 'node-meta', x: n.x + n.w - 8, y: n.y + 16, 'text-anchor': 'end' }, n.time_in_status));
  }
  g.appendChild(s('text', { class: 'node-title', x: n.x + 10, y: n.y + 36 }, cut(n.title, sansChars(n))));
  if (n.isCenter && (n.progress != null || n.step)) {
    const barW = n.w - 20;
    const pct = Math.max(0, Math.min(1, n.progress || 0));
    g.appendChild(s('rect', { class: 'node-progress-track', x: n.x + 10, y: n.y + n.h - 22, width: barW, height: 3 }));
    g.appendChild(s('rect', { class: 'node-progress-fill', x: n.x + 10, y: n.y + n.h - 22, width: barW * pct, height: 3 }));
    if (n.step) g.appendChild(s('text', { class: 'node-meta', x: n.x + 10, y: n.y + n.h - 26 }, cut(n.step, monoChars(n))));
  }
  g.appendChild(s('text', { class: 'node-meta node-status', x: n.x + 10, y: n.y + n.h - 8 }, cutParts(parts, monoChars(n))));
  return g;
}

// The status.js shape drawn in SVG, 8×8 around (x, y): the fonts carry none of the glyphs, as in the markers.
const SHAPES = { '○': 'ring', '◐': 'half', '▲': 'triangle', '◆': 'diamond', '●': 'dot', '→': 'arrow', '✕': 'cross' };
function svgShape(shape, x, y, tone) {
  const name = Object.hasOwn(SHAPES, shape) ? SHAPES[shape] : 'ring';
  const stroked = { fill: 'none', stroke: 'currentColor', 'stroke-width': 1.5 };
  const filled = { fill: 'currentColor' };
  const parts = {
    ring: () => [s('circle', { cx: x, cy: y, r: 3.5, ...stroked })],
    half: () => [s('circle', { cx: x, cy: y, r: 3.5, ...stroked }), s('path', { d: `M ${x} ${y - 3.5} A 3.5 3.5 0 0 0 ${x} ${y + 3.5} Z`, ...filled })],
    triangle: () => [s('path', { d: `M ${x} ${y - 4} L ${x + 4} ${y + 3.5} L ${x - 4} ${y + 3.5} Z`, ...filled })],
    diamond: () => [s('path', { d: `M ${x} ${y - 4} L ${x + 4} ${y} L ${x} ${y + 4} L ${x - 4} ${y} Z`, ...filled })],
    dot: () => [s('circle', { cx: x, cy: y, r: 4, ...filled })],
    arrow: () => [s('path', { d: `M ${x - 4} ${y} L ${x + 4} ${y} M ${x + 1} ${y - 3} L ${x + 4} ${y} L ${x + 1} ${y + 3}`, ...stroked })],
    cross: () => [s('path', { d: `M ${x - 3.5} ${y - 3.5} L ${x + 3.5} ${y + 3.5} M ${x + 3.5} ${y - 3.5} L ${x - 3.5} ${y + 3.5}`, ...stroked })],
  }[name]();
  return s('g', { class: `node-shape node-shape--${tone}`, 'data-shape': name, 'aria-hidden': 'true' }, parts);
}

// SVG text cannot wrap or ellipsise itself; the caller keeps the uncut text in the node's <title>.
function cut(text, n) { text = text || ''; return text.length > n ? text.slice(0, Math.max(1, n - 1)) + '…' : text; }
// A line of parts that does not fit drops whole parts from its end, with no mark. The first part (the status word) is always whole,
// even when it alone is longer than the line: a word cut mid-way says less than one that runs into the node's edge.
function cutParts(parts, n) {
  const line = parts.join(' · ');
  if (line.length <= n || parts.length < 2) return line;
  let shown = parts[0];
  for (const part of parts.slice(1)) {
    const next = `${shown} · ${part}`;
    if (next.length + 2 > n) break;
    shown = next;
  }
  // What was dropped goes cleanly: a "…" after a whole word reads as a cut word. The node's <title> keeps the full line.
  return shown;
}

function renderContextBand(related, uid) {
  const handovers = list(related?.handovers).filter((ho) => ho && words(ho.id));
  const issues = list(related?.issues).filter((i) => i && words(i.id));
  if (!handovers.length && !issues.length) return null;
  const band = h('div', { class: 'td-graph-context-band', id: `${uid}-context`, 'data-test': 'context-band' });
  if (handovers.length) {
    band.appendChild(h('span', { class: 'lbl' }, 'Handovers'));
    for (const ho of handovers.slice(0, 3)) band.appendChild(h('span', { class: 'ctx-pill handover' }, words(ho.id)));
  }
  if (issues.length) {
    band.appendChild(h('span', { class: 'lbl' }, 'Issues'));
    for (const i of issues.slice(0, 4)) band.appendChild(h('a', { class: 'ctx-pill issue', href: linkRoute(words(i.id)) }, words(i.id)));
  }
  return band;
}

function renderGraphControls(frame, band) {
  const buttons = [];
  if (band) {
    const hide = h('button', {
      type: 'button', class: 'btn btn--ghost btn--sm', 'aria-pressed': String(band.hidden), 'aria-controls': band.id,
      'data-focus': 'graph:hide-context',
    }, 'Hide context');
    hide.addEventListener('click', () => {
      band.hidden = !band.hidden;
      hide.setAttribute('aria-pressed', String(band.hidden));
    });
    buttons.push(hide);
  }
  if (document.fullscreenEnabled) {
    const full = h('button', { type: 'button', class: 'btn btn--ghost btn--sm', 'aria-pressed': 'false', 'data-focus': 'graph:fullscreen' }, 'Fullscreen');
    // A toggle: its name stays "Fullscreen" and its pressed state says whether the graph fills the screen.
    // The frame on the page may be an earlier one a repaint kept in place, so it is looked up, not closed over.
    const liveFrame = () => full.closest('.td-graph-frame') ?? frame;
    full.sayState = () => {
      const on = document.fullscreenElement === liveFrame();
      full.setAttribute('aria-pressed', String(on));
    };
    full.addEventListener('click', () => {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else liveFrame().requestFullscreen?.();
    });
    buttons.push(full);
  }
  return buttons.length ? h('div', { class: 'td-graph-controls', 'data-test': 'graph-controls' }, buttons) : null;
}

function renderTabs(task, uid, keptTab) {
  const docs = [
    ['spec', 'Spec', () => renderMd(task.specification || task.description)],
    ['plan', 'Plan', () => renderMd(task.plan)],
    ['notes', 'Notes', () => renderMd(task.notes)],
    ['activity', 'Activity', () => renderActivityList(task.activity)],
    ['anchors', 'Anchors', () => renderAnchors(task.anchors)],
    ['raw', 'Raw JSON', () => renderRaw(task)],
  ];
  const bar = h('div', { class: 'td-tabs', role: 'tablist', 'aria-label': 'Task documents' });
  const panels = h('div', { class: 'td-tab-panels' });
  const tabs = [];
  const first = Math.max(0, docs.findIndex(([key]) => key === keptTab));
  docs.forEach(([key, label, build], i) => {
    const tabId = `${uid}-tab-${key}`;
    const panelId = `${uid}-panel-${key}`;
    const tab = h('button', {
      type: 'button', class: 'td-tab', role: 'tab', id: tabId, 'data-tab': key, 'data-focus': `tab:${key}`,
      'aria-selected': String(i === first), 'aria-controls': panelId, tabindex: i === first ? '0' : '-1',
    }, label);
    const panel = h('div', {
      class: 'td-tab-panel', role: 'tabpanel', id: panelId, 'aria-labelledby': tabId, tabindex: '0', 'data-tab-panel': key, 'data-focus': `panel:${key}`,
    }, build());
    panel.hidden = i !== first;
    tab.addEventListener('click', () => select(i));
    tabs.push([tab, panel]);
    bar.appendChild(tab);
    panels.appendChild(panel);
  });
  function select(index, focus = false) {
    tabs.forEach(([tab, panel], i) => {
      tab.setAttribute('aria-selected', String(i === index));
      tab.setAttribute('tabindex', i === index ? '0' : '-1');
      panel.hidden = i !== index;
    });
    if (focus) tabs[index][0].focus();
  }
  bar.addEventListener('keydown', (e) => {
    const at = tabs.findIndex(([tab]) => tab === e.target);
    if (at < 0) return;
    const last = tabs.length - 1;
    const next = { ArrowRight: at === last ? 0 : at + 1, ArrowLeft: at === 0 ? last : at - 1, Home: 0, End: last }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    select(next, true);
  });
  return h('div', { class: 'td-tabs-wrap', 'data-test': 'tabs' }, [bar, panels]);
}

const empty = (text) => h('div', { class: 'td-empty' }, text);

function renderMd(src) {
  return typeof src === 'string' && src.trim() ? markdownBody(src) : empty('Nothing written.');
}
function renderActivityList(lines) {
  const items = list(lines).slice(0, 30);
  if (!items.length) return empty('No activity.');
  return h('ul', { class: 'td-activity' }, items.map((l) => h('li', {}, typeof l === 'string' ? l : JSON.stringify(l))));
}
function renderAnchors(anchors) {
  const names = list(anchors).filter((a) => typeof a === 'string');
  if (!names.length) return empty('No anchors.');
  return h('div', { class: 'td-anchors' }, names.map((a) => h('span', { class: 'td-anchor-pill' }, a)));
}
function renderRaw(task) {
  return h('pre', {}, JSON.stringify(task, null, 2));
}
