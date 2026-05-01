// Quest Spine — vertical SVG chain with active node + satellites.
// Reads layout from auto-spine-layout.js; pure render.

import { computeSpineLayout } from './auto-spine-layout.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

const SUBAGENT_LABELS = {
  'general-purpose': 'G',
  'Explore': 'E',
  'Plan': 'P',
  'code-reviewer': 'R',
  'code-architect': 'A',
};

/**
 * Mount a Quest Spine into root. Returns a cleanup function.
 * @param {HTMLElement} root
 * @param {object} state - auto-mode session state
 */
export function renderQuestSpine(root, state) {
  root.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'spine-head';
  if (state) {
    head.innerHTML = `
      <span class="spine-head-id">${escape(state.session_id ?? state.task_id ?? '—')}</span>
      <span class="spine-head-title">${escape(state.title ?? '')}</span>
      <span class="spine-head-wt">${escape(state.worktree ?? '')}</span>
    `;
  } else {
    head.innerHTML = '<span class="spine-head-empty">No auto-mode session running.</span>';
  }
  root.appendChild(head);

  if (!state || !state.cursor) {
    const empty = document.createElement('div');
    empty.className = 'spine-empty';
    empty.textContent = 'No auto-mode session is running.';
    root.appendChild(empty);
    return () => { root.innerHTML = ''; };
  }

  const wrap = document.createElement('div');
  wrap.className = 'spine-frame';
  root.appendChild(wrap);

  const width = 360;
  const height = 520;
  const padding = 50;

  const cursorStage = state?.cursor?.stage ?? null;
  const completed = state?.completed ?? [];
  const subagents = (state?.subagents ?? []).filter((s) => s.status === 'running');

  const layout = computeSpineLayout({
    cursorStage, completed, subagents, width, height, padding,
  });

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'spine-svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  wrap.appendChild(svg);

  // Connectors (drawn first so nodes layer above)
  for (const c of layout.connectors) {
    const line = document.createElementNS(SVG_NS, 'line');
    line.setAttribute('class', `spine-connector spine-connector--${c.fromState}`);
    line.setAttribute('x1', c.x1); line.setAttribute('y1', c.y1);
    line.setAttribute('x2', c.x2); line.setAttribute('y2', c.y2);
    svg.appendChild(line);
  }

  // Satellite bezier connectors + nodes
  for (const sat of layout.satellites) {
    const path = document.createElementNS(SVG_NS, 'path');
    const b = sat.bezier;
    path.setAttribute('class', 'spine-satellite-edge');
    path.setAttribute('d',
      `M ${b.startX} ${b.startY} C ${b.c1x} ${b.c1y} ${b.c2x} ${b.c2y} ${b.endX} ${b.endY}`);
    svg.appendChild(path);

    const sn = document.createElementNS(SVG_NS, 'circle');
    sn.setAttribute('class', 'spine-satellite');
    sn.setAttribute('cx', sat.node.x);
    sn.setAttribute('cy', sat.node.y);
    sn.setAttribute('r', sat.node.r);
    svg.appendChild(sn);

    const tx = document.createElementNS(SVG_NS, 'text');
    tx.setAttribute('class', 'spine-satellite-label');
    tx.setAttribute('x', sat.node.x);
    tx.setAttribute('y', sat.node.y + 3);
    tx.setAttribute('text-anchor', 'middle');
    tx.textContent = SUBAGENT_LABELS[sat.type] ?? sat.type.slice(0, 1).toUpperCase();
    svg.appendChild(tx);
  }

  // Spine nodes
  for (const node of layout.nodes) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', `spine-node spine-node--${node.state}`);
    g.setAttribute('data-stage', node.stage);

    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('cx', node.x);
    circle.setAttribute('cy', node.y);
    circle.setAttribute('r', node.r);
    circle.setAttribute('class', 'spine-node-circle');
    g.appendChild(circle);

    if (node.state === 'done') {
      const check = document.createElementNS(SVG_NS, 'text');
      check.setAttribute('class', 'spine-node-check');
      check.setAttribute('x', node.x);
      check.setAttribute('y', node.y + 3);
      check.setAttribute('text-anchor', 'middle');
      check.textContent = '✓';
      g.appendChild(check);
    }

    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('class', 'spine-node-label');
    label.setAttribute('x', node.x + node.r + 12);
    label.setAttribute('y', node.y + 4);
    label.textContent = stageLabel(node.stage);
    g.appendChild(label);

    svg.appendChild(g);
  }

  return () => { root.innerHTML = ''; };
}

function stageLabel(stage) {
  switch (stage) {
    case 'PICK': return 'Pick';
    case 'IMPLEMENT': return 'Implement';
    case 'REVIEW': return 'Review';
    case 'HANDOVER_STUB': return 'Handover';
    case 'COMPLETE': return 'Complete';
    default: return stage;
  }
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
