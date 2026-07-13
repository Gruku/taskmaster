import { renderTimeline } from '../components/timeline.js';
import { RightRail } from '../components/right-rail.js';
import { listSessions, getSessionDetail } from '../api.js';
import { claimTopbar, tmSubcount, tmSearch, tmSegmented, tmAction } from '../lib/topbar.js';
import { pluralize } from '../util/pluralize.js';
import { emptyState } from '../components/empty-state.js';
import { chipClickNext } from '../util/chip-toggle.js';
import { formatRelative, formatAbsolute, formatDurationCompact } from '../lib/time.js';

export const meta = { title: 'Sessions', icon: '⊕', sidebarKey: 'sessions' };

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function mount(root, { params, store, prefs }) {
  // Gotcha: `prefs` is the patch helper, not the data.
  // Read persisted state from store.getPrefs(), then mirror back onto prefs.
  const prefsData = store?.getPrefs?.() || {};
  const sessionPrefs = (prefsData.screens && prefsData.screens.sessions) || {};
  if (!prefs.screens) prefs.screens = {};
  if (!prefs.screens.sessions) prefs.screens.sessions = {};
  prefs.screens.sessions.view = sessionPrefs.view || 'A';

  root.innerHTML = `
    <div class="sessions-page">
      <div class="sessions-kinds" data-role="kinds">
        <span class="sessions-kind-chip session on" data-kind="session">
          <span class="dot"></span> Sessions <span class="ct">0</span>
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
  const initialSessionsView = prefs.screens.sessions.view || 'A';
  const viewToggle = tmSegmented(
    [
      { key: 'A', label: 'Diary' },
      { key: 'B', label: 'Lanes' },
      { key: 'C', label: 'By Task' },
    ],
    {
      value: initialSessionsView,
      onChange: (v) => {
        state.view = v;
        window.dispatchEvent(new CustomEvent('viewer:prefs-patch', {
          detail: { screens: { sessions: { view: v } } },
        }));
        render(root, state, rail);
      },
    },
  );
  const newNoteBtn = tmAction({
    icon: '+', label: 'New note', variant: 'primary',
    title: 'New note — coming soon',
    disabled: true,
  });
  topbar?.appendChild(subcount);
  topbar?.appendChild(searchBuilt.el);
  topbar?.appendChild(viewToggle);
  topbar?.appendChild(newNoteBtn);

  const rail = new RightRail({ width: 480 });
  const persistedStatus = (prefsData.screens?.sessions?.handoverStatus) || ['open', 'closed'];
  const state = {
    sessions: [],
    detailCache: new Map(),
    view: prefs.screens.sessions.view,
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

  if (state.selectedSessionId) openSessionDetail(rail, state.selectedSessionId, state);

  return () => { rail.close(); };
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
    const sLabel = pluralize(sCount, 'session', 'sessions');
    const hLabel = pluralize(hCount, 'handover', 'handovers');
    subcount.textContent = filtered
      ? `${sCount} of ${totalCount} ${pluralize(totalCount, 'session', 'sessions')} · ${hCount} ${hLabel}`
      : `${sCount} ${sLabel} · ${hCount} ${hLabel}`;
  }
}

function render(root, state, rail) {
  const mount = root.querySelector('[data-role=mount]');
  if (state.view !== 'A') {
    mount.innerHTML = `<div class="stub">View "${escapeHtml(state.view)}" — Plan 5b owns Lanes/By-Task.<div class="stub-meta">/sessions?view=${escapeHtml(state.view)}</div></div>`;
    return;
  }

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

async function openSessionDetail(rail, sid, state) {
  const detail = state.detailCache.get(sid) || await getSessionDetail(sid);
  state.detailCache.set(sid, detail);
  rail.open({
    kind: 'session',
    render: () => renderSessionRail(detail),
    onMount: (el) => bindRailClose(el, rail),
  });
}

async function openHandoverDetail(rail, hid, state) {
  // Locate the session containing this handover, then pull its detail.
  const owner = state.sessions.find(s => (s.handover_ids || []).includes(hid));
  if (!owner) return;
  const detail = state.detailCache.get(owner.id) || await getSessionDetail(owner.id);
  state.detailCache.set(owner.id, detail);
  const h = (detail.handovers || []).find(x => x.id === hid);
  if (!h) return;
  rail.open({
    kind: 'handover',
    render: () => renderHandoverRail(h, owner),
    onMount: (el) => {
      const cleanup = bindRailClose(el, rail);
      const pill = el.querySelector('.ho-status-pill');
      if (pill) {
        pill.addEventListener('click', () => {
          import('../components/right-rail.js').then(({ openStatusMenu }) => {
            openStatusMenu(pill, h.id, h.status || 'open');
          });
        });
      }
      return cleanup;
    },
  });
}

function bindRailClose(el, rail) {
  const btn = el.querySelector('[data-role=rail-close]');
  if (btn) btn.addEventListener('click', () => rail.close());
  return () => {};
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

function renderSessionRail(detail) {
  const s = detail.session;
  return (
    `<div class="rr-h">`
    + `<span class="kind-pill session">SESSION</span>`
    + `<span class="ts">${escapeHtml(railSessionTimeLine(s))}</span>`
    + `<span class="actions">`
    + `<button class="ic-btn" data-role="rail-close" title="Close">✕</button>`
    + `</span></div>`
    + `<div class="rr-title">${escapeHtml(s.id)}</div>`
    + `<div class="rr-meta"><span>Tasks: ${(s.task_ids||[]).map(escapeHtml).join(', ') || '—'}</span></div>`
    + (detail.handovers || []).map(h =>
        `<div class="rr-section"><h4>${escapeHtml(h.viewer_kind.toUpperCase())} <span class="ct mono">${escapeHtml(h.id)}</span></h4>`
        + `<div class="ho-summary">${escapeHtml(h.tldr || '')}</div></div>`).join('')
  );
}

function renderHandoverRail(h, owner) {
  const fp = `.taskmaster/handovers/${h.id}.md`;
  const status = h.status || 'open';
  return (
    `<div class="rr-h">`
    + `<span class="kind-pill handover">${escapeHtml(h.viewer_kind.toUpperCase())}</span>`
    + `<span class="ho-status-pill ho-status-pill-${escapeHtml(status)}" `
    +   `data-handover-id="${escapeHtml(h.id)}" data-status="${escapeHtml(status)}" `
    +   `title="Status: ${escapeHtml(status)} — click to change">${escapeHtml(status)}</span>`
    + `<span class="ts">${escapeHtml(formatRelative(h.created || h.date))}</span>`
    + `<span class="actions">`
    + `<button class="ic-btn" title="Edit — coming soon" disabled>✎</button>`
    + `<button class="ic-btn" title="Open file — coming soon" disabled>↗</button>`
    + `<button class="ic-btn" data-role="rail-close" title="Close">✕</button>`
    + `</span></div>`
    + `<div class="rr-title">${escapeHtml(h.tldr || h.id)}</div>`
    + `<div class="rr-meta">`
    + `<span>Session: <a href="#/sessions/${escapeHtml(owner.id)}">${escapeHtml(owner.id)}</a></span>`
    + `<span class="filepath" title="Click to copy">${escapeHtml(fp)}</span>`
    + `</div>`
    + `<div class="rr-resume">`
    + `<span class="label">RESUME</span>`
    + `<button class="copy">⧉ copy</button>`
    + `<div class="body">${escapeHtml(h.resume_prompt || h.next_action || '')}</div>`
    + `</div>`
    + `<div class="rr-section"><h4>What's done <span class="ct mono">${(h.done_items||[]).length}</span></h4>`
    +   (h.done_items||[]).map(i => `<div class="checkitem done"><span class="mark">✓</span><span>${escapeHtml(i)}</span></div>`).join('')
    + `</div>`
    + `<div class="rr-section"><h4>What's open <span class="ct mono">${(h.open_items||[]).length}</span></h4>`
    +   (h.open_items||[]).map(i => `<div class="checkitem open"><span class="mark">○</span><span>${escapeHtml(i)}</span></div>`).join('')
    + `</div>`
    + `<div class="rr-section"><h4>Related</h4>`
    +   (h.task_ids||[]).map(t =>
        `<div class="related-row"><span class="id">${escapeHtml(t)}</span></div>`).join('')
    + `</div>`
    + `<div class="rr-section"><h4>Files touched</h4><div class="files-list">`
    +   (h.files_touched||[]).slice(0, 8).map(f =>
        `<div class="files-row mod"><span class="pre">~</span><span>${escapeHtml(typeof f === 'string' ? f : f.path)}</span></div>`).join('')
    + ((h.files_touched||[]).length > 8
        ? `<div class="more">+ ${(h.files_touched||[]).length - 8} more…</div>` : '')
    + `</div></div>`
  );
}

export default mount;
