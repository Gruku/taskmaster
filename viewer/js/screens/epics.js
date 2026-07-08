// plugins/taskmaster/viewer/js/screens/epics.js
import { claimTopbar } from '../lib/topbar.js';
import { assignEpicColors, epicCssVar } from '../lib/epics.js';
import { progressPercent, closeableBadge } from '../lib/epic-format.js';

export const meta = { title: 'Epics', icon: '⬡', sidebarKey: 'epics' };

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export async function mount(root, { store }) {
  root.innerHTML = '';
  root.classList.add('epics');
  claimTopbar();
  const unsub = store.subscribe('backlog', render);
  render();

  function render() {
    const bl = store.getBacklog() || {};
    const epics = bl.epics || [];
    const tasks = bl.tasks || [];
    const colors = assignEpicColors(epics);
    root.replaceChildren();

    if (!epics.length) {
      const empty = document.createElement('div');
      empty.className = 'epics-empty';
      empty.textContent = 'No epics yet.';
      root.appendChild(empty);
      return;
    }

    const list = document.createElement('div');
    list.className = 'epics-list';
    for (const ep of epics) {
      const mine = tasks.filter(t => t.epic === ep.id);
      const stats = {
        total: mine.length,
        done: mine.filter(t => t.status === 'done').length,
        archived: mine.filter(t => t.status === 'archived').length,
      };
      const pct = progressPercent(stats);
      const a = document.createElement('a');
      a.className = 'epic-row';
      a.href = `#/epic/${encodeURIComponent(ep.id)}`;
      a.setAttribute('style', epicCssVar(colors[ep.id]));
      if (ep.done_when) a.title = `Done when: ${ep.done_when}`;
      a.innerHTML = `
        <span class="epic-row__swatch"></span>
        <span class="epic-row__name">${esc(ep.name || ep.id)}</span>
        <span class="epic-row__ds">${esc(ep.design_status || 'exploring')}</span>
        ${closeableBadge(stats)}
        <span class="epic-row__count">${stats.done}/${stats.total}</span>
        <span class="epic-row__bar"><span style="width:${pct}%"></span></span>`;
      list.appendChild(a);
    }
    root.appendChild(list);
  }

  return () => { unsub(); root.classList.remove('epics'); };
}
