// Center board surface: 2-col preview (Up next + In progress only),
// active phase, max 4 cards each. ⤢ inline-expand affordance toggles a
// "expanded" class; "open full board →" navigates to #/kanban.
// Reuses Plan 2's card.js Minimal renderer.

import { renderMinimalCard } from './card.js';

export function createBoardSurface({ store }) {
  const root = document.createElement('section');
  root.className = 'dash-board';
  root.setAttribute('aria-label', 'Board preview');

  const head = document.createElement('header');
  head.className = 'dash-board__head';

  const title = document.createElement('div');
  title.className = 'dash-board__title';
  title.textContent = 'Board preview';

  const actions = document.createElement('div');
  actions.className = 'dash-board__actions';

  const expand = document.createElement('button');
  expand.type = 'button';
  expand.className = 'dash-board__expand';
  expand.title = 'Expand inline';
  expand.textContent = '⤢';
  expand.addEventListener('click', () => root.classList.toggle('is-expanded'));

  const open = document.createElement('a');
  open.className = 'dash-board__open';
  open.href = '#/kanban';
  open.textContent = 'open full board →';

  actions.append(expand, open);

  head.append(title, actions);

  const cols = document.createElement('div');
  cols.className = 'dash-board__cols';

  function makeCol(label) {
    const col = document.createElement('div');
    col.className = 'dash-board__col';
    const h = document.createElement('div');
    h.className = 'dash-board__col-head';
    h.textContent = label;
    const list = document.createElement('div');
    list.className = 'dash-board__list';
    col.append(h, list);
    return { col, list };
  }
  const upnext = makeCol('Up next');
  const inprog = makeCol('In progress');
  cols.append(upnext.col, inprog.col);

  root.append(head, cols);

  function refresh() {
    const backlog = (store.getBacklog && store.getBacklog()) || { tasks: [] };
    const active = (backlog.phases || []).find(p => p.status === 'active');
    const phaseId = active ? active.id : null;
    const tasks = (backlog.tasks || []).filter(t => !phaseId || t.phase === phaseId);
    const upn = tasks.filter(t => t.status === 'todo' || t.status === 'ready').slice(0, 4);
    const ipg = tasks.filter(t => t.status === 'in-progress').slice(0, 4);
    upnext.list.replaceChildren(...upn.map(t => renderMinimalCard(t, { backlog })));
    inprog.list.replaceChildren(...ipg.map(t => renderMinimalCard(t, { backlog })));
    title.textContent = active ? `Phase: ${active.name}` : 'Board preview';
  }

  refresh();
  const unsub = store.subscribe ? store.subscribe('backlog', refresh) : () => {};

  return {
    root,
    refresh,
    destroy() { if (typeof unsub === 'function') unsub(); },
  };
}
