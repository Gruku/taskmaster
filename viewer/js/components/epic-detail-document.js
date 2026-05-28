// plugins/taskmaster/viewer/js/components/epic-detail-document.js
// Render an epic's C1 detail into `container`. Pure of page chrome: it never
// calls claimTopbar() and writes nothing outside `container`, so it drops
// cleanly into both the /epic/<id> screen and the detail modal.
import { mountMarkdown } from './markdown.js';
import { assignEpicColors, epicCssVar } from '../lib/epics.js';
import {
  designBadge, componentGlyph, progressPercent, tasksForComponent,
} from '../lib/epic-format.js';

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// chrome: 'page' (route screen) | 'embedded' (modal). actionsHost: optional
// element to receive an actions row (none in C1). onNavigate(taskId): jump to a task.
export function mountEpicDetail(container, { epic, store, onNavigate, chrome = 'page' } = {}) {
  container.classList.add('ed-root');
  const colors = assignEpicColors((store?.getBacklog?.() || {}).epics || [{ id: epic.id }]);
  container.setAttribute('style', epicCssVar(colors[epic.id]));

  const go = onNavigate || ((tid) => { location.hash = `#/task/${encodeURIComponent(tid)}`; });

  container.replaceChildren();

  // crumb (only meaningful as a screen; harmless in modal)
  if (chrome === 'page') {
    const crumb = document.createElement('div');
    crumb.className = 'ed-crumb';
    crumb.innerHTML = `<a class="ed-back" href="#/epics">‹ Epics</a>`;
    container.appendChild(crumb);
  }

  // header
  const badge = designBadge(epic.design_status);
  const pct = progressPercent(epic.stats);
  const head = document.createElement('header');
  head.className = 'ed-head';
  head.innerHTML = `
    <div class="ed-meta">
      <span class="ed-swatch"></span>
      <span class="ed-id">${esc(epic.id)}</span>
      <span class="ed-ds ed-ds--${badge.cls}">${badge.locked ? '🔒 ' : ''}${esc(badge.label)}</span>
      <span class="ed-epic-status">${esc(epic.status || 'active')}</span>
    </div>
    <h1 class="ed-title">${esc(epic.name || epic.id)}</h1>
    <div class="ed-progress">
      <span class="ed-progress__bar"><span style="width:${pct}%"></span></span>
      <span class="ed-progress__label">${(epic.stats?.done || 0)}/${(epic.stats?.total || 0)} done · ${pct}%</span>
    </div>`;
  container.appendChild(head);

  const grid = document.createElement('div');
  grid.className = 'ed-grid';
  const main = document.createElement('div'); main.className = 'ed-main';
  const side = document.createElement('aside'); side.className = 'ed-side';
  grid.append(main, side);
  container.appendChild(grid);

  // narrative (description field + body markdown)
  const narrative = [epic.description, epic._body].filter(Boolean).join('\n\n');
  if (narrative) {
    const sec = document.createElement('section');
    sec.className = 'ed-narrative';
    const h = document.createElement('h2'); h.className = 'ed-h'; h.textContent = 'Design';
    const md = document.createElement('div'); md.className = 'ed-md';
    mountMarkdown(md, narrative);
    sec.append(h, md);
    main.appendChild(sec);
  }

  // Component / task / attention / docs sections are filled by Task 8; this
  // task ships the header + narrative + the empty-narrative case. The diagram
  // (C2) will mount into a `.ed-diagram` node added here later (extension point).
  mountEpicDetail._fillBody?.(container, { epic, main, side, go });

  return () => { container.classList.remove('ed-root'); container.replaceChildren(); };
}
