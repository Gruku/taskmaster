// plugins/taskmaster/viewer/js/components/component-diagram.js
// HTML-first Epic Architecture Map: component blocks in dependency-rank columns,
// each holding the real task cards, with an SVG overlay drawing neutral after-edges.
import { computeComponentLayout } from './component-graph-layout.js';
import { renderMinimalCard } from './card.js';
import { tasksForComponent } from '../lib/epic-format.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// NS-aware element factory — pattern copied from task-detail-graph.js:39–53
// (util/h.js is HTML-only and cannot create SVG elements).
function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// rollup status → block visual-state. Exported + pinned exhaustively in Task 3.
export function blockVisualState(status) {
  switch (status) {
    case 'done': return 'done';
    case 'in-progress': return 'progress';
    case 'blocked': return 'attention';
    default: return 'todo'; // todo, undefined, unknown-future-status → neutral
  }
}

function rollupSummary(r) {
  if (!r) return 'todo';
  const status = r.status || 'todo';
  const blocked = r.blocked || 0;
  if (status === 'in-progress' && blocked) return `in-progress · ${blocked} blocked`;
  return status;
}

// Pure: cubic-bezier `d` from two rendered rects, relative to the host rect.
// Same bezier family as dependency-graph.js:makeEdge (horizontal pull at 60%).
export function computeEdgePath(from, to, host) {
  const fx = from.right - host.left;
  const fy = from.top - host.top + from.height / 2;
  const tx = to.left - host.left;
  const ty = to.top - host.top + to.height / 2;
  const dx = (tx - fx) * 0.6;
  return `M ${fx.toFixed(1)} ${fy.toFixed(1)} C ${(fx + dx).toFixed(1)} ${fy.toFixed(1)}, `
       + `${(tx - dx).toFixed(1)} ${ty.toFixed(1)}, ${tx.toFixed(1)} ${ty.toFixed(1)}`;
}

// Adaptation from spec: layout nodes do NOT carry `blocked` count — that lives
// only in the rollup input. buildBlock receives rollup so it can look up
// rollup[node.id].blocked directly.
function buildBlock({ node, tasks, rollup, onComponentNav }) {
  const block = document.createElement('div');
  // Derive visual state from node.status (which comes from rollup[id].status via layout engine).
  const state = node.unassigned ? 'unassigned' : blockVisualState(node.status);
  block.className = `cd-block cd-block--${state}`;
  block.dataset.id = node.id;
  block.dataset.rank = String(node.rank ?? 0);
  block.tabIndex = 0;
  block.setAttribute('role', 'button');

  const head = document.createElement('div');
  head.className = 'cd-block__head';
  const title = document.createElement('span');
  title.className = 'cd-block__title';
  title.textContent = node.unassigned ? 'Unassigned' : (node.title || node.id);
  const summary = document.createElement('span');
  summary.className = 'cd-block__summary';

  // Build summary from the rollup entry directly to get blocked count.
  const ro = rollup[node.id] || {};
  summary.textContent = node.unassigned
    ? `${node.total || 0} task${(node.total || 0) === 1 ? '' : 's'}`
    : rollupSummary({ status: node.status, blocked: ro.blocked });

  head.append(title, summary);
  block.setAttribute('aria-label', `${title.textContent} — ${summary.textContent}. Open kanban filtered to this component.`);
  block.appendChild(head);

  const cards = document.createElement('div');
  cards.className = 'cd-block__cards';
  const list = tasksForComponent(tasks, node.id);
  if (list.length) {
    for (const t of list) cards.appendChild(renderMinimalCard(t, { groupBy: 'component', now: Date.now() }));
  } else {
    const empty = document.createElement('div');
    empty.className = 'cd-block__empty';
    empty.textContent = 'no tasks yet';
    cards.appendChild(empty);
  }
  block.appendChild(cards);

  // Block navigates to the kanban filter — but only when the click did not land
  // on an embedded task card (cards own their own click → task detail).
  const fire = () => { if (typeof onComponentNav === 'function') onComponentNav(node.id); };
  head.addEventListener('click', fire);
  block.addEventListener('click', (ev) => {
    if (ev.target.closest('.card-task')) return; // card click wins
    if (ev.target.closest('.cd-block__head')) return; // head handler already fired
    fire();
  });
  block.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fire(); }
  });
  return block;
}

export function mountComponentDiagram(container, { components, rollup, tasks, onComponentNav } = {}) {
  container.replaceChildren();
  if (!components || !Object.keys(components).length) return () => {};

  const layout = computeComponentLayout({ components, rollup });
  const map = document.createElement('div');
  map.className = 'cd-map';

  // SVG overlay first so blocks paint over the connectors.
  const svg = svgEl('svg', { class: 'cd-connectors' });
  map.appendChild(svg);

  // Group real (non-unassigned) nodes by rank, render one column per rank.
  const realNodes = layout.nodes.filter(n => !n.unassigned);
  const ranks = [...new Set(realNodes.map(n => n.rank ?? 0))].sort((a, b) => a - b);
  for (const rank of ranks) {
    const col = document.createElement('div');
    col.className = 'cd-rank';
    col.dataset.rank = String(rank);
    const inRank = realNodes.filter(n => (n.rank ?? 0) === rank).sort((a, b) => (a.row ?? 0) - (b.row ?? 0));
    for (const node of inRank) col.appendChild(buildBlock({ node, tasks, rollup, onComponentNav }));
    map.appendChild(col);
  }

  // Trailing unassigned column, only when an unassigned node exists.
  const unassigned = layout.nodes.find(n => n.unassigned);
  if (unassigned) {
    const col = document.createElement('div');
    col.className = 'cd-rank cd-rank--unassigned';
    col.appendChild(buildBlock({ node: unassigned, tasks, rollup, onComponentNav }));
    map.appendChild(col);
  }

  // One edge path per after-edge. d is computed from rendered rects (drawEdges).
  const edges = layout.edges || [];
  for (const e of edges) {
    const path = svgEl('path', { class: 'cd-edge', 'data-edge': `${e.from}__${e.to}`, d: '' });
    svg.appendChild(path);
  }

  container.appendChild(map);

  function drawEdges() {
    const hostRect = map.getBoundingClientRect();
    svg.setAttribute('width', String(map.clientWidth || 0));
    svg.setAttribute('height', String(map.clientHeight || 0));
    for (const e of edges) {
      const a = map.querySelector(`.cd-block[data-id="${e.from}"]`);
      const b = map.querySelector(`.cd-block[data-id="${e.to}"]`);
      const path = svg.querySelector(`path[data-edge="${e.from}__${e.to}"]`);
      if (!a || !b || !path) continue;
      path.setAttribute('d', computeEdgePath(a.getBoundingClientRect(), b.getBoundingClientRect(), hostRect));
    }
  }

  const raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (fn) => fn();
  raf(drawEdges);
  let ro = null;
  if (typeof ResizeObserver === 'function') { ro = new ResizeObserver(() => drawEdges()); ro.observe(map); }

  return () => { if (ro) ro.disconnect(); container.replaceChildren(); };
}
