// User intent: Archived lists every archived task, grouped under its epic's name, read-only apart from a search.
// Rows are links that keep the id whole and the title's words in reach, and a board redraw never loses the reader's place.

import { claimTopbar, setTopbarCount, tmSearch } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { linkRow } from '../components/link-row.js';
import { stateBlock } from '../components/empty-state.js';
import { truncate } from '../lib/text.js';
import { priorityMarker } from '../components/status.js';

export const meta = { title: 'Archived', icon: '⌫', sidebarKey: 'archived' };

const NONE = '__none__';

function archivedTasks(backlog) {
  if (!backlog || typeof backlog !== 'object' || !Array.isArray(backlog.tasks)) return [];
  return backlog.tasks.filter((t) => t && String(t.status || '').toLowerCase() === 'archived');
}

/** Archived tasks matching `query` (id or title, case-insensitive), grouped by epic: listed epics in backlog order,
 *  then unlisted epic ids alphabetically, then '__none__' ("No epic"). Label = epic name, else title, else id. */
export function archivedGroups(backlog, query = '') {
  const q = String(query ?? '').trim().toLowerCase();
  const tasks = archivedTasks(backlog)
    .filter((t) => !q || `${t.id} ${t.title || ''}`.toLowerCase().includes(q));
  if (!tasks.length) return [];

  const byKey = new Map();
  for (const t of tasks) {
    const key = t.epic ? String(t.epic) : NONE;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(t);
  }
  const epics = Array.isArray(backlog.epics) ? backlog.epics.filter((e) => e && e.id != null) : [];
  const listed = new Map(epics.map((e) => [String(e.id), e]));
  const order = [
    ...[...listed.keys()].filter((k) => byKey.has(k)),
    ...[...byKey.keys()].filter((k) => k !== NONE && !listed.has(k)).sort(),
    ...(byKey.has(NONE) ? [NONE] : []),
  ];
  return order.map((key) => {
    const e = listed.get(key);
    const label = key === NONE ? 'No epic' : (e?.name || e?.title || key);
    return { key, label, tasks: byKey.get(key) };
  });
}

function span(className, text) {
  const el = document.createElement('span');
  el.className = className;
  el.textContent = text;
  return el;
}

function archivedRow(t) {
  const name = span('arch-name', '');
  name.append(span('arch-id', String(t.id)), truncate(t.title || String(t.id), { lines: 2, className: 'arch-title' }));
  const reason = String(t.archived_reason || '').trim();
  const row = linkRow({
    href: `#/task/${encodeURIComponent(t.id)}`,
    name,
    content: [
      t.priority ? priorityMarker(t.priority) : null,
      t.phase ? span('arch-phase', `Phase ${t.phase}`) : null,
      reason ? span('arch-reason', reason.charAt(0).toUpperCase() + reason.slice(1)) : null,
    ].filter(Boolean),
    className: 'arch-row',
  });
  row.dataset.taskId = String(t.id);
  return row;
}

export async function mount(root, { store }) {
  let alive = true;
  let q = '';

  const page = document.createElement('div');
  page.className = 'archived-page';
  root.appendChild(page);

  // Row 2: the search only.
  const topbar = claimTopbar();
  const search = tmSearch({
    placeholder: 'Filter by title or id…',
    ariaLabel: 'Filter archived tasks',
    onInput: (value) => {
      if (!alive) return;
      q = value.trim();
      paint();
    },
  });
  topbar.appendChild(search.el);

  function clearSearch() {
    search.input.value = '';
    // The field's own listener hides its clear button; its debounced repaint after ours is a no-op.
    search.input.dispatchEvent(new Event('input', { bubbles: true }));
    q = '';
    paint();
    search.input.focus();
  }

  function paint() {
    if (!alive) return;
    const active = document.activeElement;
    const focusedId = active?.classList?.contains('link-row__link')
      ? active.closest('.arch-row')?.dataset.taskId ?? null
      : null;

    const backlog = store.getBacklog();
    const total = archivedTasks(backlog).length;
    const groups = archivedGroups(backlog, q);
    const shown = groups.reduce((n, g) => n + g.tasks.length, 0);
    const noun = `${total} ${pluralize(total, 'archived task', 'archived tasks')}`;
    setTopbarCount(q && shown < total ? `${noun} · ${shown} visible` : noun);

    if (!total) {
      page.replaceChildren(stateBlock({ label: 'Archived', headline: 'No archived tasks yet' }));
    } else if (!shown) {
      page.replaceChildren(stateBlock({
        label: 'Search',
        headline: `No archived tasks match "${q}"`,
        action: { label: 'Clear search', onClick: clearSearch },
      }));
    } else {
      page.replaceChildren(...groups.map((g, i) => {
        const section = document.createElement('section');
        section.className = 'arch-group';
        const h = document.createElement('h2');
        h.className = 'arch-group-h';
        h.id = `arch-group-${i}`;
        h.append(span('arch-group-label', g.label), span('arch-group-count', String(g.tasks.length)));
        section.setAttribute('aria-labelledby', h.id);
        section.append(h, ...g.tasks.map(archivedRow));
        return section;
      }));
    }

    if (focusedId != null) {
      const row = [...page.querySelectorAll('.arch-row')].find((r) => r.dataset.taskId === focusedId);
      (row?.querySelector('.link-row__link') ?? search.input).focus({ preventScroll: true });
    }
  }

  const unsub = store.subscribe('backlog', paint);
  paint();

  return () => {
    alive = false;
    unsub();
  };
}
