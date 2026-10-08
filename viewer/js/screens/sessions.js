// User intent: the Sessions screen — open threads as link cards, then a timeline of sessions and their handovers whose
// rows are real buttons that lead with a readable title; chips and search narrow it (search hides, never dims), and
// the row picked opens beside the timeline in the right rail, which gives focus back to that row when it closes.
import { renderTimeline, kindLabel, sessionTimeLine } from '../components/timeline.js';
import { RightRail, statusPill, HO_STATUS_LABEL } from '../components/right-rail.js';
import { icon } from '../components/icon.js';
import { chipRow } from '../components/chips.js';
import { linkRow } from '../components/link-row.js';
import { stateBlock } from '../components/empty-state.js';
import { listSessions, getSessionDetail, listThreads } from '../api.js';
import { claimTopbar, setTopbarCount, tmSearch } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { chipClickNext, CHIP_CLICK_HINT } from '../util/chip-toggle.js';
import { formatRelative } from '../lib/time.js';
import { bindCopy } from '../lib/copy.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';

export const meta = { title: 'Sessions', icon: '⊕', sidebarKey: 'sessions' };

const HO_STATUSES = ['open', 'closed', 'superseded'];

export function mount(root, { params, subpath, store, prefs }) {
  // `prefs` is the patch helper; what was saved is read from the store.
  const saved = store?.getPrefs?.()?.screens?.sessions?.handoverStatus;
  const state = {
    sessions: [],
    threads: [],
    detailCache: new Map(),
    kinds: { session: true, handover: true },
    handoverStatus: new Set(Array.isArray(saved) ? saved : ['open', 'closed']),
    searchTerm: '',
    selected: null,     // { kind, id } of the row shown in the rail
    loaded: false,
    failed: false,
  };
  let alive = true;

  const filters = h('div', { class: 'sessions-filters' });
  const board = h('div', { class: 'thread-board', 'data-role': 'board', hidden: '' });
  const list = h('div', { class: 'sessions-mount', 'data-role': 'mount' });
  const railHost = h('div', { class: 'right-rail-host', 'data-role': 'rail-host' });
  root.replaceChildren(h('div', { class: 'sessions-page' }, filters, board, h('div', { class: 'sessions-body' }, list, railHost)));

  // ── Row 2: the search ──
  const topbar = claimTopbar();
  const search = tmSearch({
    placeholder: 'Search sessions…',
    ariaLabel: 'Search sessions',
    onInput: (v) => {
      if (!alive) return;
      state.searchTerm = v.trim().toLowerCase();
      render();
    },
  });
  topbar?.appendChild(search.el);

  // ── The page's filter bar: which kinds of row show, and which handover statuses ──
  const kindRow = chipRow({
    label: 'Show', chips: kindChips(),
    onToggle: (value) => { state.kinds[value] = !state.kinds[value]; render(); },
  });
  const statusRow = chipRow({
    label: 'Status', hint: CHIP_CLICK_HINT, chips: statusChips(),
    onToggle: (value, ev) => {
      state.handoverStatus = new Set(chipClickNext(ev, [...state.handoverStatus], value));
      prefs?.patch?.({ screens: { sessions: { handoverStatus: [...state.handoverStatus] } } });
      render();
    },
  });
  filters.append(kindRow.el, statusRow.el);

  // ── The rail: it marks the row it shows, and the mark goes when it closes ──
  const rail = new RightRail({ host: railHost, label: 'Session details' });
  const rowFor = (sel) => sel && list.querySelector(sel.kind === 'session'
    ? `.ho[data-session-id="${CSS.escape(sel.id)}"]`
    : `.ho-child[data-handover-id="${CSS.escape(sel.id)}"]`);
  // Rows say they control the rail only while it is open: closed, `#right-rail` does not exist.
  function paintSelected() {
    for (const b of list.querySelectorAll('[aria-current]')) b.removeAttribute('aria-current');
    for (const b of list.querySelectorAll('.ho, .ho-child')) {
      if (state.selected) b.setAttribute('aria-controls', 'right-rail');
      else b.removeAttribute('aria-controls');
    }
    rowFor(state.selected)?.setAttribute('aria-current', 'true');
  }
  state.onRailOpen = (sel) => { state.selected = sel; paintSelected(); };
  state.onRailClose = () => {
    // A detail still loading when the rail is closed must not reopen it when it lands.
    nextOpen(state);
    const closed = state.selected;
    state.selected = null;
    paintSelected();
    // A rail swapped for another is still open. One opened by the route, or whose row was redrawn while it was open,
    // has no opener to give focus back to: its row takes it, else the search.
    queueMicrotask(() => {
      if (!alive || rail.isOpen()) return;
      const active = document.activeElement;
      if (active && active !== document.body) return;
      (rowFor(closed) ?? search.input).focus({ preventScroll: true });
    });
  };

  const onSelect = ({ kind, id }, button) => {
    const open = kind === 'session' ? openSessionDetail : openHandoverDetail;
    open(rail, id, state, button).catch((e) => console.error('session detail failed', e));
  };

  // A status changed in the rail is the timeline's too: its row and the chip counts say the new word.
  const onStatusChanged = (ev) => {
    if (!alive) return;
    const { id, status } = ev.detail || {};
    let hit = false;
    for (const s of state.sessions) {
      for (const ho of s.handovers || []) if (ho.id === id) { ho.status = status; hit = true; }
    }
    for (const d of state.detailCache.values()) {
      for (const ho of d?.handovers || []) if (ho.id === id) ho.status = status;
    }
    if (hit) render();
  };

  function kindChips(sessionCount = 0, handoverCount = 0) {
    return [
      { value: 'session', label: 'Threads', count: sessionCount, pressed: state.kinds.session },
      { value: 'handover', label: 'Handovers', count: handoverCount, pressed: state.kinds.handover },
    ];
  }

  function statusChips(counts = {}) {
    return HO_STATUSES.map((s) => ({
      value: s, label: HO_STATUS_LABEL[s], count: counts[s] || 0, pressed: state.handoverStatus.has(s),
    }));
  }

  // No status chip pressed is no status filter.
  const statusShown = (meta) => state.handoverStatus.size === 0 || state.handoverStatus.has(meta?.status || 'open');

  function render() {
    const all = state.sessions;
    const handovers = handoverIndex(all);
    const counts = {};
    for (const ho of Object.values(handovers)) counts[ho.status || 'open'] = (counts[ho.status || 'open'] || 0) + 1;
    const hoCount = Object.keys(handovers).length;
    kindRow.update(kindChips(all.length, hoCount));
    statusRow.update(statusChips(counts));

    if (state.failed) {
      setTopbarCount('');
      return list.replaceChildren(stateBlock({
        label: 'Error', headline: 'Sessions could not be loaded.',
        hint: 'Check that the viewer is still running, then reload the page.',
      }));
    }
    if (!state.loaded) return;

    const matched = matchSessions(all, state.searchTerm);
    const shown = state.kinds.session ? matched : [];
    // A handover counts as visible when its thread shows, the Handovers toggle is on and its status chip admits it.
    const shownHandovers = new Set();
    if (state.kinds.handover) {
      for (const s of shown) for (const id of s.handover_ids || []) if (statusShown(handovers[id])) shownHandovers.add(id);
    }
    const visible = shown.length + shownHandovers.size;
    setTopbarCount(`${all.length} ${pluralize(all.length, 'thread', 'threads')} · ${hoCount} ${pluralize(hoCount, 'handover', 'handovers')}`
      + (visible < all.length + hoCount ? ` · ${visible} visible` : ''));

    if (!all.length) {
      return list.replaceChildren(stateBlock({
        label: 'Sessions', headline: 'No sessions yet', hint: 'Sessions appear here as you start and end your work cycles.',
      }));
    }
    if (state.searchTerm && !matched.length) {
      return list.replaceChildren(stateBlock({
        label: 'Search', headline: 'No sessions match your search',
        action: { label: 'Clear search', onClick: clearSearch },
      }));
    }
    if (!shown.length) {
      return list.replaceChildren(stateBlock({
        label: 'Filters', headline: 'The Show filters hide every session',
        action: { label: 'Show everything', onClick: showEverything },
      }));
    }
    renderTimeline(list, {
      sessions: shown.map((s) => ({
        ...s,
        handover_ids: state.kinds.handover ? (s.handover_ids || []).filter((id) => statusShown(handovers[id])) : [],
      })),
      handovers,
      onSelect,
      selected: state.selected,
    });
  }

  function clearSearch() {
    search.input.value = '';
    search.input.dispatchEvent(new Event('input', { bubbles: true }));
    search.input.focus();
  }

  // The empty state's button goes with the redraw, so focus lands on the first Show chip.
  function showEverything() {
    state.kinds = { session: true, handover: true };
    render();
    kindRow.el.querySelector('button')?.focus();
  }

  function renderBoard() {
    const open = state.threads.filter((t) => t.status === 'open');
    const parked = state.threads.filter((t) => t.status === 'parked');
    board.hidden = !open.length && !parked.length;
    board.replaceChildren(...[
      open.length ? h('div', { class: 'tb-grid' }, open.map(threadCard)) : null,
      parked.length ? h('details', { class: 'tb-parked' },
        h('summary', {}, `${parked.length} parked`),
        h('div', { class: 'tb-grid' }, parked.map(threadCard))) : null,
    ].filter(Boolean));
  }

  // The session the route names (#/sessions/<id>) opens once the list is in; an id with no session opens nothing.
  function routeId() {
    try {
      if (subpath?.[0]) return decodeURIComponent(subpath[0]);
    } catch { return null; }
    return params?.id || null;
  }

  listSessions().then((data) => {
    if (!alive) return;
    state.sessions = Array.isArray(data) ? data : [];
    state.loaded = true;
    render();
    const id = routeId();
    if (id && state.sessions.some((s) => s.id === id)) {
      openSessionDetail(rail, id, state).catch((e) => console.error('session detail failed', e));
    }
  }, (e) => {
    if (!alive) return;
    console.error('sessions load failed', e);
    state.failed = true;
    render();
  });

  listThreads().then((data) => {
    if (!alive) return;
    state.threads = Array.isArray(data) ? data : [];
    renderBoard();
  }, () => {});

  // Last, just before the disposer goes back: a mount that throws earlier leaves no window listener behind.
  window.addEventListener('viewer:handover-status-changed', onStatusChanged);
  return () => {
    alive = false;
    window.removeEventListener('viewer:handover-status-changed', onStatusChanged);
    rail.close({ returnFocus: false });
    kindRow.destroy();
    statusRow.destroy();
  };
}

// Every handover named by a session: id → { id, viewer_kind, tldr, status }.
function handoverIndex(sessions) {
  const out = {};
  for (const s of sessions) {
    for (const hid of s.handover_ids || []) {
      if (out[hid]) continue;
      const meta = (s.handovers || []).find((ho) => ho.id === hid) || {};
      out[hid] = { id: hid, viewer_kind: meta.viewer_kind || '', tldr: meta.tldr || '', status: meta.status || 'open' };
    }
  }
  return out;
}

// A session matches when the search is in its id, tldr, task ids, or a handover's id or tldr.
function matchSessions(sessions, q) {
  if (!q) return sessions;
  return sessions.filter((s) => [
    s.id || '', s.tldr || '', ...(s.task_ids || []), ...(s.handover_ids || []),
    ...(s.handovers || []).map((ho) => ho.tldr || ''),
  ].join(' ').toLowerCase().includes(q));
}

// An open or parked thread: a link to its session, with its resume line's copy button beside the link.
function threadCard(t) {
  const name = String(t.name ?? '');
  const copy = h('button', {
    type: 'button', class: 'tc-copy btn btn--ghost btn--sm', 'aria-label': `Copy resume line for ${name}`,
  }, icon('copy', { size: 14 }), 'Resume line');
  bindCopy(copy, `Resume: ${name} — ${t.next_action || t.tldr || ''}`);
  const tags = [
    ...(t.task_ids || []).slice(0, 4).map((id) => h('span', { class: 'tc-task' }, String(id))),
    t.branch ? truncate(t.branch, { className: 'tc-branch' }) : null,
  ].filter(Boolean);
  return linkRow({
    href: `#/sessions/${encodeURIComponent(name)}`,
    name: truncate(name, { className: 'tc-name' }),
    className: `thread-card thread-card-${t.status}`,
    content: [
      h('span', { class: 'tc-stale' }, t.staleness_days > 0 ? `${t.staleness_days}d` : 'today'),
      t.tldr ? truncate(t.tldr, { lines: 2, className: 'tc-tldr' }) : null,
      t.next_action ? h('span', { class: 'tc-next' }, `→ ${t.next_action}`) : null,
      tags.length ? h('span', { class: 'tc-foot' }, tags) : null,
    ],
    controls: [copy],
  });
}

// Exported for the unit tests. A screen left while the detail loads opens nothing (the rail's host is gone).
// `state.onRailOpen` / `state.onRailClose`, when the screen sets them, hear which row the rail shows.
// Only the latest open is applied: a slow fetch for an earlier click must not open over a later one.
const nextOpen = (state) => (state.openSeq = (state.openSeq || 0) + 1);

export async function openSessionDetail(rail, sid, state, opener = null) {
  const seq = nextOpen(state);
  const detail = state.detailCache.get(sid) || await getSessionDetail(sid);
  state.detailCache.set(sid, detail);
  if (seq !== state.openSeq || !rail.host.isConnected || !detail?.session) return;
  const s = detail.session;
  const el = rail.open({
    kind: 'session',
    title: s.tldr || s.id,
    opener,
    onClose: () => state.onRailClose?.(),
    // The button that opens a handover goes with this rail, so the handover's rail hands focus to this rail's opener.
    ...renderSessionRail(detail, (hid) => openHandoverDetail(rail, hid, state, opener)),
  });
  if (el) state.onRailOpen?.({ kind: 'session', id: sid });
}

export async function openHandoverDetail(rail, hid, state, opener = null) {
  // Locate the session containing this handover, then pull its detail.
  const owner = state.sessions.find(s => (s.handover_ids || []).includes(hid));
  if (!owner) return;
  const seq = nextOpen(state);
  const detail = state.detailCache.get(owner.id) || await getSessionDetail(owner.id);
  state.detailCache.set(owner.id, detail);
  if (seq !== state.openSeq || !rail.host.isConnected) return;
  const ho = (detail?.handovers || []).find(x => x.id === hid);
  if (!ho) return;
  const el = rail.open({
    kind: 'handover',
    title: ho.tldr || ho.id,
    opener,
    onClose: () => state.onRailClose?.(),
    ...renderHandoverRail(ho, owner),
  });
  if (el) state.onRailOpen?.({ kind: 'handover', id: hid });
}

// The rail shares the timeline's formatter so the two never disagree about a session's span.
const railSessionTimeLine = sessionTimeLine;

const taskLinks = (ids) => (ids || []).length
  ? ids.map(id => h('a', { class: 'rr-task', href: `#/task/${encodeURIComponent(id)}` }, String(id)))
  : ['—'];

// The rail's head and body for a session; the title (its tldr, or its id) is the rail's own.
function renderSessionRail(detail, openHandover) {
  const s = detail.session;
  const handovers = detail.handovers || [];
  const head = [
    h('span', { class: 'rr-kind' }, 'Thread'),
    h('span', { class: 'rr-when' }, railSessionTimeLine(s)),
  ];
  const body = [
    s.tldr ? h('div', { class: 'rr-slug' }, s.id) : null,
    h('div', { class: 'rr-meta' }, h('span', { class: 'rr-label' }, 'Tasks'), ...taskLinks(s.task_ids)),
    h('section', { class: 'rr-section' },
      h('h3', {}, 'Handovers ', h('span', { class: 'rr-count' }, String(handovers.length))),
      ...handovers.map(ho => h('button', {
        type: 'button',
        class: 'rr-ho btn btn--ghost btn--sm',
        'data-handover-id': ho.id,
        on: { click: () => openHandover(ho.id) },
      },
        h('span', { class: 'rr-kind' }, kindLabel(ho.viewer_kind)),
        h('span', { class: 'rr-ho__id' }, ho.id),
        h('span', { class: 'rr-when' }, formatRelative(ho.created || ho.date)),
      )),
    ),
  ].filter(Boolean);
  return { head, body };
}

const FILES_SHOWN = 8;

function checklist(title, items, mark) {
  if (!(items || []).length) return null;
  return h('section', { class: 'rr-section' },
    h('h3', {}, `${title} `, h('span', { class: 'rr-count' }, String(items.length))),
    h('ul', { class: 'rr-checklist' }, items.map(item => h('li', { class: 'rr-check' },
      icon(mark, { size: 14 }),
      h('span', {}, String(item)),
    ))),
  );
}

// The rail's head and body for one handover; the title (its tldr, or its id) is the rail's own.
function renderHandoverRail(ho, owner) {
  const fp = `.taskmaster/handovers/${ho.id}.md`;
  const resume = ho.resume_prompt || ho.next_action || '';
  const files = (ho.files_touched || []).map(f => (typeof f === 'string' ? f : f && f.path)).filter(Boolean);

  const pathBtn = h('button', { type: 'button', class: 'rr-path btn btn--ghost btn--sm', 'aria-label': `Copy path ${fp}` },
    icon('copy', { size: 14 }), truncate(fp));
  bindCopy(pathBtn, fp);
  const copyBtn = h('button', { type: 'button', class: 'btn btn--ghost btn--sm' }, icon('copy', { size: 14 }), 'Copy');
  bindCopy(copyBtn, resume);

  const head = [
    h('span', { class: 'rr-kind' }, kindLabel(ho.viewer_kind)),
    statusPill(ho.id, ho.status || 'open'),
    h('span', { class: 'rr-when' }, formatRelative(ho.created || ho.date)),
  ];
  const body = [
    h('div', { class: 'rr-slug' }, ho.id),
    h('div', { class: 'rr-meta' },
      h('span', { class: 'rr-label' }, 'Session'),
      h('a', { href: `#/sessions/${encodeURIComponent(owner.id)}` }, owner.id),
      pathBtn,
    ),
    h('section', { class: 'rr-resume' },
      h('span', { class: 'rr-label' }, 'Resume'),
      copyBtn,
      h('div', { class: 'rr-resume__body' }, resume),
    ),
    checklist("What's done", ho.done_items, 'check'),
    checklist("What's open", ho.open_items, 'minus'),
    (ho.task_ids || []).length ? h('section', { class: 'rr-section' },
      h('h3', {}, 'Related'),
      h('div', { class: 'rr-meta' }, ...taskLinks(ho.task_ids)),
    ) : null,
    files.length ? h('section', { class: 'rr-section' },
      h('h3', {}, 'Files touched'),
      h('ul', { class: 'rr-files' },
        ...files.slice(0, FILES_SHOWN).map(f => h('li', {}, truncate(f))),
        files.length > FILES_SHOWN ? h('li', {}, `+ ${files.length - FILES_SHOWN} more`) : null,
      ),
    ) : null,
  ].filter(Boolean);
  return { head, body };
}

export default mount;
