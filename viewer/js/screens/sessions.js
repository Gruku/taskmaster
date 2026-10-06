import { renderTimeline } from '../components/timeline.js';
import { RightRail, statusPill } from '../components/right-rail.js';
import { icon } from '../components/icon.js';
import { listSessions, getSessionDetail, listThreads } from '../api.js';
import { claimTopbar, tmSubcount, tmSearch } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import { chipClickNext } from '../util/chip-toggle.js';
import { formatRelative, formatAbsolute, formatDurationCompact } from '../lib/time.js';
import { bindCopy } from '../lib/copy.js';
import { truncate } from '../lib/text.js';
import { h } from '../util/h.js';

export const meta = { title: 'Sessions', icon: '⊕', sidebarKey: 'sessions' };

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function mount(root, { params, store, prefs }) {
  // Gotcha: `prefs` is the patch helper, not the data.
  // Read persisted state from store.getPrefs() (used below for handover status).
  const prefsData = store?.getPrefs?.() || {};

  root.innerHTML = `
    <div class="sessions-page">
      <div class="thread-board" data-role="board"></div>
      <div class="sessions-kinds" data-role="kinds">
        <span class="sessions-kind-chip session on" data-kind="session">
          <span class="dot"></span> Threads <span class="ct">0</span>
        </span>
        <span class="sessions-kind-chip handover on" data-kind="handover">
          <span class="dot"></span> Handovers <span class="ct">0</span>
        </span>
      </div>
      <div class="handover-status-chips" data-role="ho-status">
        <span class="status-chip on" data-status="open">open <span class="ct">0</span></span>
        <span class="status-chip on" data-status="closed">closed <span class="ct">0</span></span>
        <span class="status-chip" data-status="superseded">superseded <span class="ct">0</span></span>
      </div>
      <div class="right-rail-host" data-role="rail-host"></div>
      <div class="sessions-mount" data-role="mount"></div>
    </div>
  `;

  // ── Topbar (#topbar-actions) ───────────────────────────────
  const topbar = claimTopbar();
  const subcount = tmSubcount('… sessions');
  const searchBuilt = tmSearch({
    placeholder: 'Search sessions…',
    onInput: (v) => {
      state.searchTerm = v.trim().toLowerCase();
      refreshKindCounts(root, _filteredSessions(state), subcount, state.sessions.length);
      render(root, state, rail);
    },
  });
  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);

  const rail = new RightRail({ host: root.querySelector('[data-role=rail-host]'), label: 'Session details' });
  const persistedStatus = (prefsData.screens?.sessions?.handoverStatus) || ['open', 'closed'];
  const state = {
    sessions: [],
    threads: [],
    detailCache: new Map(),
    kinds: { session: true, handover: true },
    handoverStatus: new Set(persistedStatus),
    searchTerm: '',
    selectedSessionId: params && params.id || null,
  };

  bindKindChips(root, state, () => render(root, state, rail));
  bindStatusChips(root, state, () => {
    window.dispatchEvent(new CustomEvent('viewer:prefs-patch', {
      detail: { screens: { sessions: { handoverStatus: [...state.handoverStatus] } } },
    }));
    render(root, state, rail);
  });

  state.sessions = await listSessions();
  refreshKindCounts(root, state.sessions, subcount);
  render(root, state, rail);

  try {
    state.threads = await listThreads();
  } catch { state.threads = []; }
  renderBoard(root, state);

  if (state.selectedSessionId) openSessionDetail(rail, state.selectedSessionId, state);

  return () => { rail.close(); };
}

function renderBoard(root, state) {
  const host = root.querySelector('[data-role=board]');
  if (!host) return;
  const open = (state.threads || []).filter(t => t.status === 'open');
  const parked = (state.threads || []).filter(t => t.status === 'parked');
  host.innerHTML = '';
  if (!open.length && !parked.length) { host.style.display = 'none'; return; }
  host.style.display = '';
  const grid = document.createElement('div');
  grid.className = 'tb-grid';
  for (const t of open) grid.appendChild(threadCard(t));
  host.appendChild(grid);
  if (parked.length) {
    const fold = document.createElement('details');
    fold.className = 'tb-parked';
    fold.innerHTML = `<summary>${parked.length} parked</summary>`;
    const pgrid = document.createElement('div');
    pgrid.className = 'tb-grid';
    for (const t of parked) pgrid.appendChild(threadCard(t));
    fold.appendChild(pgrid);
    host.appendChild(fold);
  }
}

function threadCard(t) {
  const card = document.createElement('div');
  card.className = `thread-card thread-card-${t.status}`;
  const stale = t.staleness_days > 0 ? `${t.staleness_days}d` : 'today';
  card.innerHTML =
    `<div class="tc-head">`
    + `<span class="tc-name mono">${escapeHtml(t.name)}</span>`
    + `<span class="tc-stale mono">${escapeHtml(stale)}</span>`
    + `</div>`
    + `<div class="tc-tldr">${escapeHtml(t.tldr || '')}</div>`
    + (t.next_action ? `<div class="tc-next">→ ${escapeHtml(t.next_action)}</div>` : '')
    + `<div class="tc-foot">`
    + (t.task_ids || []).slice(0, 4).map(id => `<span class="pill task mono">${escapeHtml(id)}</span>`).join('')
    + (t.branch ? `<span class="tc-branch mono">${escapeHtml(t.branch)}</span>` : '')
    + `<button class="tc-copy" title="Copy resume line">⧉ resume</button>`
    + `</div>`;
  const btn = card.querySelector('.tc-copy');
  bindCopy(btn, `Resume: ${t.name} — ${t.next_action || t.tldr || ''}`);
  card.addEventListener('click', (ev) => {
    if (ev.target === btn) return;
    location.hash = `#/sessions/${encodeURIComponent(t.name)}`;
  });
  return card;
}

function _filteredSessions(state) {
  const q = state.searchTerm;
  if (!q) return state.sessions;
  return state.sessions.filter(s => {
    const hay = [
      s.id || '',
      ...(s.task_ids || []),
      ...(s.handover_ids || []),
      s.tldr || '',
    ].join(' ').toLowerCase();
    return hay.includes(q);
  });
}

function bindKindChips(root, state, onChange) {
  const row = root.querySelector('[data-role=kinds]');
  for (const chip of row.querySelectorAll('.sessions-kind-chip')) {
    chip.addEventListener('click', () => {
      const k = chip.dataset.kind;
      state.kinds[k] = !state.kinds[k];
      chip.classList.toggle('on', state.kinds[k]);
      onChange();
    });
  }
}

function bindStatusChips(root, state, onChange) {
  const row = root.querySelector('[data-role=ho-status]');
  for (const chip of row.querySelectorAll('.status-chip')) {
    chip.addEventListener('click', (ev) => {
      const next = new Set(chipClickNext(ev, [...state.handoverStatus], chip.dataset.status));
      state.handoverStatus = next;
      for (const c of row.querySelectorAll('.status-chip')) {
        c.classList.toggle('on', next.has(c.dataset.status));
      }
      onChange();
    });
  }
}

function refreshStatusChipCounts(root, handovers) {
  const counts = { open: 0, closed: 0, superseded: 0 };
  for (const meta of Object.values(handovers)) {
    const s = meta.status || 'open';
    if (counts[s] != null) counts[s] += 1;
  }
  const row = root.querySelector('[data-role=ho-status]');
  if (!row) return;
  for (const chip of row.querySelectorAll('.status-chip')) {
    const ct = chip.querySelector('.ct');
    if (ct) ct.textContent = String(counts[chip.dataset.status] || 0);
  }
}

function refreshKindCounts(root, sessions, subcount, totalCount) {
  const sCount = sessions.length;
  const hCount = sessions.reduce((n, s) => n + (s.handover_ids || []).length, 0);
  const chips = root.querySelectorAll('[data-role=kinds] .sessions-kind-chip');
  chips[0].querySelector('.ct').textContent = sCount;
  chips[1].querySelector('.ct').textContent = hCount;
  if (subcount) {
    const filtered = totalCount != null && totalCount !== sCount;
    const sLabel = pluralize(sCount, 'thread', 'threads');
    const hLabel = pluralize(hCount, 'handover', 'handovers');
    subcount.textContent = filtered
      ? `${sCount} of ${totalCount} ${pluralize(totalCount, 'thread', 'threads')} · ${hCount} ${hLabel}`
      : `${sCount} ${sLabel} · ${hCount} ${hLabel}`;
  }
}

function render(root, state, rail) {
  const mount = root.querySelector('[data-role=mount]');

  // Search dims non-matching sessions so the timeline keeps its rhythm;
  // kind-chip toggles still hide rows entirely.
  const matched = _filteredSessions(state);
  const matchedIds = new Set(matched.map(s => s.id));
  const dimmedIds = state.searchTerm
    ? state.sessions.filter(s => !matchedIds.has(s.id)).map(s => s.id)
    : [];
  const visibleSessions = state.kinds.session
    ? state.sessions.map(s => ({
        ...s,
        handover_ids: state.kinds.handover ? (s.handover_ids || []) : [],
      }))
    : [];

  const handovers = {}; // id → {viewer_kind, tldr, status}
  for (const s of state.sessions) {
    for (const hid of s.handover_ids || []) {
      if (!handovers[hid]) {
        // Look up status from session metadata if available; default to 'open' for legacy entries.
        const meta = (s.handovers || []).find(h => h.id === hid) || {};
        handovers[hid] = {
          id: hid,
          viewer_kind: meta.viewer_kind || 'standalone',
          tldr: meta.tldr || '',
          status: meta.status || 'open',
        };
      }
    }
  }

  // Refresh chip counts using the unfiltered map.
  refreshStatusChipCounts(root, handovers);

  // Apply status filter — handovers whose status is not in the active set are excluded.
  const filteredHandovers = {};
  for (const [hid, meta] of Object.entries(handovers)) {
    if (state.handoverStatus.has(meta.status || 'open')) {
      filteredHandovers[hid] = meta;
    }
  }

  const independent = []; // standalone handovers come from a Plan 5b feed; empty here.

  // Empty-state: no sessions in the data, OR search dimmed everything out and
  // the user can't see anything. Kind-chip-only filters leave the rail visible.
  if (state.sessions.length === 0) {
    mount.innerHTML = '';
    mount.appendChild(emptyState({
      headline: 'No sessions yet',
      hint: 'Sessions appear here as you start and end your work cycles.',
    }));
    return;
  }
  if (state.searchTerm && matched.length === 0) {
    mount.innerHTML = '';
    mount.appendChild(emptyState({
      headline: 'No sessions match your search',
      hint: 'Try a different term or clear the search box.',
    }));
    return;
  }

  renderTimeline(mount, {
    sessions: visibleSessions,
    handovers: filteredHandovers,
    independent,
    dimmedIds,
    onSelect: ({ kind, id }) => {
      if (kind === 'session')  return openSessionDetail(rail, id, state);
      if (kind === 'handover') return openHandoverDetail(rail, id, state);
    },
  });
}

async function openSessionDetail(rail, sid, state, opener = null) {
  const detail = state.detailCache.get(sid) || await getSessionDetail(sid);
  state.detailCache.set(sid, detail);
  const s = detail.session;
  rail.open({
    kind: 'session',
    title: s.tldr || s.id,
    opener,
    ...renderSessionRail(detail, (hid, btn) => openHandoverDetail(rail, hid, state, btn)),
  });
}

async function openHandoverDetail(rail, hid, state, opener = null) {
  // Locate the session containing this handover, then pull its detail.
  const owner = state.sessions.find(s => (s.handover_ids || []).includes(hid));
  if (!owner) return;
  const detail = state.detailCache.get(owner.id) || await getSessionDetail(owner.id);
  state.detailCache.set(owner.id, detail);
  const ho = (detail.handovers || []).find(x => x.id === hid);
  if (!ho) return;
  rail.open({
    kind: 'handover',
    title: ho.tldr || ho.id,
    opener,
    ...renderHandoverRail(ho, owner),
  });
}

function railSessionTimeLine(s) {
  const isDateOnly = s.time_resolution === 'date-only';
  if (isDateOnly) {
    return formatAbsolute(s.start, { time: false });
  }
  const startFmt = formatAbsolute(s.start, { date: false });
  const endFmt   = formatAbsolute(s.end,   { date: false });
  let timeLine = (startFmt === endFmt) ? startFmt : `${startFmt} → ${endFmt}`;
  if (s.duration > 0) {
    timeLine += ` · ${formatDurationCompact(s.duration * 1000)}`;
  }
  return timeLine;
}

// 'mid-task' → 'Mid-task'
const sentenceCase = (word) => {
  const w = String(word || '');
  return w.charAt(0).toUpperCase() + w.slice(1);
};

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
        on: { click: (ev) => openHandover(ho.id, ev.currentTarget) },
      },
        h('span', { class: 'rr-kind' }, sentenceCase(ho.viewer_kind || 'standalone')),
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
    h('span', { class: 'rr-kind' }, sentenceCase(ho.viewer_kind || 'standalone')),
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
