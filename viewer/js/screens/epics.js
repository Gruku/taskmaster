// User intent: the Epics list — every epic as one link row with its real lifecycle and its progress counted from its tasks, in columns that line up, filterable from the topbar.
import { claimTopbar, setTopbarCount, tmSearch } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { epicStats, epicProgress, isCloseable, epicStatusMeta } from '../lib/epic-format.js';
import { epicIndex } from '../lib/epics.js';
import { marker } from '../components/status.js';
import { linkRow } from '../components/link-row.js';
import { stateBlock } from '../components/empty-state.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';

export const meta = { title: 'Epics', icon: '⬡', sidebarKey: 'epics' };

// "Closeable" is a nudge to close an epic that is still open; a done or archived one needs none.
const OPEN_STATUSES = new Set(['active', 'planned']);
const swatchEl = (n) => (n ? h('span', { class: `epic-swatch epic-swatch--cat-${n}`, 'aria-hidden': 'true' }) : null);

function epicRow(ep, tasks, index) {
  const stats = epicStats(tasks.filter((t) => t.epic === ep.id));
  const prog = epicProgress(stats);
  const label = ep.name || ep.id;
  const fill = h('span', { class: 'epic-row__fill' });
  fill.style.width = `${prog.pct}%`;
  const open = ep.status == null || ep.status === '' || OPEN_STATUSES.has(ep.status);
  const row = linkRow({
    tag: 'li',
    className: 'epic-row',
    href: `#/epic/${encodeURIComponent(ep.id)}`,
    name: h('span', { class: 'epic-row__name' }, [swatchEl(index.get(ep.id)?.swatch), truncate(label, { lines: 2 })]),
    content: [
      h('span', { class: 'epic-row__status' }, marker(epicStatusMeta(ep.status))),
      h('span', { class: 'epic-row__bar', 'aria-hidden': 'true' }, fill),
      h('span', { class: 'epic-row__count', title: prog.label }, `${prog.closed}/${prog.total}`),
      h('span', { class: 'epic-row__tags' }, isCloseable(stats) && open ? h('span', { class: 'epic-tag' }, 'Closeable') : null),
    ],
    title: ep.done_when ? `${label}\nDone when: ${ep.done_when}` : label,
  });
  row.dataset.epicId = ep.id;
  return row;
}

export async function mount(root, { store }) {
  const screen = h('section', { class: 'epics-screen' });
  root.replaceChildren(screen);
  let q = '';
  const row2 = claimTopbar();
  const search = tmSearch({ placeholder: 'Filter epics…', onInput: (v) => { q = v; render(); } });
  row2?.append(search.el);

  const unsub = store.subscribe('backlog', render);
  render();

  function render() {
    // Where the keyboard was, so a redraw can put it back on the same epic, or on the row now at its place.
    const was = document.activeElement?.closest?.('.epic-row');
    const wasId = was && screen.contains(was) ? was.dataset.epicId : null;
    const wasAt = wasId != null ? [...screen.querySelectorAll('.epic-row')].indexOf(was) : -1;

    const bl = store.getBacklog() || {};
    const epics = Array.isArray(bl.epics) ? bl.epics : [];
    const tasks = Array.isArray(bl.tasks) ? bl.tasks : [];
    const needle = q.trim().toLowerCase();
    const shown = needle
      ? epics.filter((ep) => `${ep.name ?? ''}\n${ep.id ?? ''}`.toLowerCase().includes(needle))
      : epics;
    setTopbarCount(`${epics.length} ${pluralize(epics.length, 'epic', 'epics')}${needle ? ` · ${shown.length} visible` : ''}`);

    if (!epics.length) {
      screen.replaceChildren(stateBlock({ label: 'Epics', headline: 'No epics yet.', hint: 'An epic groups tasks toward one outcome.' }));
    } else if (!shown.length) {
      screen.replaceChildren(stateBlock({
        label: 'No match',
        headline: `No epic matches “${q.trim()}”.`,
        action: { label: 'Clear search', onClick: clearSearch },
      }));
    } else {
      // Swatches come from the whole list, so a search never recolours an epic.
      const index = epicIndex(epics);
      screen.replaceChildren(h('ul', { class: 'epics-list' }, shown.map((ep) => epicRow(ep, tasks, index))));
    }

    if (wasId == null) return;
    const rows = [...screen.querySelectorAll('.epic-row')];
    const target = rows.find((r) => r.dataset.epicId === wasId) ?? rows[Math.min(wasAt, rows.length - 1)];
    (target?.querySelector('.link-row__link') ?? search.input).focus({ preventScroll: true });
  }

  function clearSearch() {
    search.input.value = '';
    // The field's own input event keeps its clear button in step; the list is drawn now, not after the debounce.
    search.input.dispatchEvent(new Event('input', { bubbles: true }));
    q = '';
    render();
    search.input.focus();
  }

  return () => { unsub(); screen.remove(); };
}
