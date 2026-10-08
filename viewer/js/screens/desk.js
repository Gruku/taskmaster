import { h } from '../util/h.js';
import { claimTopbar } from '../lib/topbar.js';
import { createSpine, createSpineHead } from '../components/continuity/spine.js';
import { createItemRow } from '../components/continuity/item-row.js';
import { createDecisionCard } from '../components/continuity/decision-card.js';
import { renderBlock } from '../lib/xml-render.js';
import { buildRails, sortNotes } from '../lib/desk.js';
import { createNoteCard } from '../components/desk/note-card.js';
import { createComposer } from '../components/desk/composer.js';
import { describeWriteError } from '../components/edit/write-errors.js';
import { createSummaryStrip, summaryCounts } from '../components/desk/summary-strip.js';
import { getIssues, listBugs } from '../api.js';
import { stateBlock } from '../components/empty-state.js';

export const meta = { title: 'Dashboard', icon: '◧', sidebarKey: 'dashboard' };

// Where the "+N older" link points per rail — these screens hold the full,
// uncapped list for the rail's entity type.
const OLDER_TARGET = { resume: '#/sessions', review: '#/table', decide: '#/table', cleanup: '#/issues' };
const RAIL_LABEL = { resume: 'Resume', review: 'Review', decide: 'Decide', cleanup: 'Clean-up' };
// The note controls a board refresh puts focus back on.
const NOTE_CONTROLS = ['dk-note__edit', 'dk-note__pin', 'dk-note__archive', 'dk-note__more'];

export async function mount(root, { store, api }) {
  root.classList.add('dk-desk');

  // Row 2 stays empty: the desk has a single view and nothing to filter.
  claimTopbar();

  const strip = createSummaryStrip();
  const boardEl = h('section', { class: 'dk-board', 'aria-label': 'Sticky notes' });
  const bandEl = h('section', { class: 'dk-continuity', 'aria-label': 'Continuity' });
  root.replaceChildren(strip.root, boardEl, bandEl);

  // ── Summary strip ────────────────────────────────────────────────────────
  // Issues and bugs are read once; tasks are re-counted on every board emit. A read that fails is null for its count only.
  let openIssues = null;
  let openBugs = null;
  async function loadCounts() {
    const cached = store?.getIssues?.();
    const [issues, bugs] = await Promise.all([
      Array.isArray(cached) ? cached : getIssues({ includeResolved: false }).then((r) => r?.issues).catch(() => null),
      listBugs({ status: 'open' }).catch(() => null),
    ]);
    openIssues = issues;
    openBugs = bugs;
  }
  function renderStrip() {
    strip.update(summaryCounts({ tasks: store?.getBacklog?.()?.tasks, issues: openIssues, bugs: openBugs }));
  }

  // Subscribed before any await, so the returned cleanup always owns it. A mount the router abandoned mid-fetch never
  // gets that cleanup called: it drops the subscription itself once its fetches land (below) or on the next emit.
  const unsubscribe = store?.subscribe?.('backlog', () => {
    if (!strip.root.isConnected) { unsubscribe?.(); return; }
    renderStrip();
  });

  let notes = [];
  let items = [];

  async function loadNotes() {
    try { notes = (await api.notes())?.notes || []; }
    catch (e) { console.error('[desk] notes fetch failed', e); notes = []; }
  }
  // A failed read is remembered so the band can say so, rather than show every rail as empty.
  let itemsFailed = false;
  async function loadItems() {
    try { items = (await api.get('/api/continuity'))?.items || []; itemsFailed = false; }
    catch (e) { console.error('[desk] continuity fetch failed', e); items = []; itemsFailed = true; }
  }

  // ── Board (sticky notes) ─────────────────────────────────────────────────
  // A refused write is said here in words; the note or composer that made it keeps what was typed.
  const errorEl = h('p', { class: 'dk-board-error', role: 'alert' });

  // Every note write: resolves true once it went through (the board is refreshed), false when it was refused.
  async function act(fn) {
    try {
      await fn();
    } catch (e) {
      errorEl.textContent = describeWriteError(e, { noun: 'note' });
      return false;
    }
    errorEl.textContent = '';
    await refreshBoard();
    return true;
  }

  // Notes the person expanded stay expanded across redraws.
  const expandedNotes = new Set();

  // Packs the board (desk.css .is-packed): each child spans the 4px rows its height and one gap need, re-measured
  // whenever it changes size (expanded, edited, re-wrapped). Without ResizeObserver the board stays a plain grid.
  const packer = typeof ResizeObserver === 'function' ? new ResizeObserver((entries) => {
    const cs = getComputedStyle(boardEl);
    const unit = parseFloat(cs.gridAutoRows) || 4;
    const gap = parseFloat(cs.columnGap) || 0;
    for (const { target } of entries) target.style.setProperty('--note-rows', String(Math.ceil((target.offsetHeight + gap) / unit)));
  }) : null;
  if (packer) boardEl.classList.add('is-packed');

  const composer = createComposer({ onCreate: (text) => act(() => api.createNote(text)) });

  // The error line and the composer stay in place across redraws, so the composer keeps its focus and its text.
  // A focused note control is focused again on the redrawn note; when that note is gone, the composer takes focus.
  function renderBoard() {
    const active = document.activeElement;
    const focusedNote = boardEl.contains(active) ? active.closest('[data-note-id]')?.dataset.noteId : null;
    const focusedControl = focusedNote ? NOTE_CONTROLS.find((c) => active.classList.contains(c)) : null;

    for (const el of [...boardEl.children]) if (el !== errorEl && el !== composer.root) el.remove();
    if (errorEl.parentNode !== boardEl || composer.root.parentNode !== boardEl) boardEl.prepend(errorEl, composer.root);
    const cards = new Map();
    for (const note of sortNotes(notes)) {
      const card = createNoteCard({
        note,
        onPin: (n) => act(() => api.updateNote(n.id, { pinned: !n.pinned })),
        onArchive: (n) => act(() => api.archiveNote(n.id)),
        onSave: (n, text) => act(() => api.updateNote(n.id, { text })),
        expanded: expandedNotes.has(note.id),
        onExpand: (open) => { if (open) expandedNotes.add(note.id); else expandedNotes.delete(note.id); },
      });
      cards.set(note.id, card);
      boardEl.appendChild(card.root);
    }
    // Measured now rather than a frame later, so a remembered "Show more" exists to take focus back.
    for (const card of cards.values()) card.measure();
    if (notes.length === 0) boardEl.appendChild(h('p', { class: 'dk-empty' }, 'Your desk is clear.'));
    if (packer) {
      packer.disconnect();
      for (const el of boardEl.children) packer.observe(el);
    }

    if (!focusedNote) return;
    const card = cards.get(focusedNote)?.root;
    if (!card) { composer.focus(); return; }
    (card.querySelector(`.${focusedControl}`) || card.querySelector('.dk-note__edit')).focus();
  }
  async function refreshBoard() { await loadNotes(); renderBoard(); }

  // ── Continuity band ──────────────────────────────────────────────────────
  // Every band write checks the band is still on the page: a screen left (or a mount the router abandoned) is never
  // drawn into. Each render is numbered so a slower, older one never overwrites a newer one.
  const bandLive = () => bandEl.isConnected;
  let bandGeneration = 0;

  // Handovers the person opened stay open across redraws; the body they last read is shown again at once.
  const openRows = new Set();
  const bodies = new Map();

  // The band control focus was last on. A write disables the pressed button, which can drop focus to <body> before the
  // redraw; this remembers that it was in the band. Focus moved somewhere else on the page, or let go onto blank space
  // from a control that is still there and enabled, forgets it.
  let bandFocus = null;
  bandEl.addEventListener('focusin', (e) => { bandFocus = e.target; });
  bandEl.addEventListener('focusout', (e) => {
    const left = e.relatedTarget ? !bandEl.contains(e.relatedTarget) : e.target.isConnected && !e.target.disabled;
    if (left) bandFocus = null;
  });

  const CONTROLS = 'a[href], button';
  // Where focus was, in terms that survive a redraw: the item, the rail, the control's place in its item.
  function focusPlace() {
    const active = document.activeElement;
    const el = bandEl.contains(active) ? active : (!active || active === document.body) ? bandFocus : null;
    if (!el) return null;
    const holder = el.closest('[data-item-id]');
    const railEl = el.closest('[data-rail]');
    return {
      id: holder?.dataset.itemId ?? null,
      control: holder ? [...holder.querySelectorAll(CONTROLS)].indexOf(el) : -1,
      rail: railEl?.dataset.rail ?? null,
      index: holder && railEl ? [...railEl.querySelectorAll('[data-item-id]')].indexOf(holder) : -1,
      older: el.classList.contains('dk-older'),
    };
  }
  // The same control when it is still there; else the next decision (or the Decide heading) for a settled decision;
  // else the band's first control; else the composer — never <body>.
  function restoreFocus(place) {
    const byId = place.id && bandEl.querySelector(`[data-item-id="${CSS.escape(place.id)}"]`);
    if (byId) {
      const controls = [...byId.querySelectorAll(CONTROLS)];
      const target = controls[place.control] || controls[0];
      if (target) { target.focus(); return; }
    }
    const railEl = place.rail && bandEl.querySelector(`[data-rail="${place.rail}"]`);
    if (railEl && place.older) { railEl.querySelector('.dk-older')?.focus(); if (bandEl.contains(document.activeElement)) return; }
    if (railEl && place.rail === 'decide') {
      const held = [...railEl.querySelectorAll('[data-item-id]')];
      const next = held[Math.min(Math.max(place.index, 0), held.length - 1)]?.querySelector(CONTROLS);
      if (next) { next.focus(); return; }
      const heading = railEl.querySelector('.co-spine__label');
      heading.tabIndex = -1;
      heading.focus();
      return;
    }
    const first = bandEl.querySelector(CONTROLS);
    if (first) first.focus();
    else composer.focus();
  }

  async function fetchDecision(id) {
    try { return await api.get(`/api/decisions/${encodeURIComponent(id)}`); }
    catch { return null; }
  }

  // Resolve and drop reject when the server refuses, so the card can say why.
  async function resolveDecision(id, optionIndex) {
    await api.post(`/api/decisions/${encodeURIComponent(id)}/resolve`, { resolved_with: optionIndex, rationale: '' });
    await refreshBand();
  }

  async function dropDecision(id) {
    await api.post(`/api/decisions/${encodeURIComponent(id)}/drop`, { reason: 'dropped via viewer' });
    await refreshBand();
  }

  // Decide rail — the spine heading + a decision card per item, since a decision is settled in place. A decision
  // whose detail cannot be read is still listed, as a row that opens in place.
  async function renderDecideRail(rail) {
    const decisions = await Promise.all(rail.items.map((item) => fetchDecision(item.id)));
    const railEl = h('section', { class: 'co-spine' }, createSpineHead({ label: RAIL_LABEL.decide, count: rail.items.length }));
    const list = h('div', { class: 'co-spine__list' });
    const rows = [];
    rail.items.forEach((item, i) => {
      const decision = decisions[i];
      if (decision) {
        list.appendChild(createDecisionCard({
          item,
          decision,
          onResolve: (idx) => resolveDecision(item.id, idx),
          onDrop: (id) => dropDecision(id),
        }).root);
        return;
      }
      const row = createItemRow({ item, onToggle: toggleRow });
      rows.push({ item, row });
      list.appendChild(row.root);
    });
    railEl.appendChild(list);
    return { root: railEl, rows };
  }

  function bandError() {
    return stateBlock({
      state: 'error', label: 'Continuity', headline: 'Could not load what to pick up next.',
      action: { label: 'Try again', onClick: () => refreshBand() },
    });
  }

  async function renderBand() {
    const generation = ++bandGeneration;
    const rails = buildRails(items);
    const railEls = [];
    const rows = [];
    for (const key of ['resume', 'review', 'decide', 'cleanup']) {
      if (itemsFailed) break;
      const rail = rails[key];
      if (rail.items.length === 0 && rail.older === 0) continue;
      const built = key === 'decide' && rail.items.length > 0
        ? await renderDecideRail(rail)
        : createSpine({ label: RAIL_LABEL[key], items: rail.items, onToggle: toggleRow, olderCount: rail.older });
      const railEl = built.root;
      railEl.dataset.rail = key;
      rows.push(...built.rows);
      if (rail.older > 0) {
        railEl.appendChild(h('a', {
          class: 'dk-older btn btn--ghost btn--sm',
          href: OLDER_TARGET[key],
          'aria-label': `${rail.older} older ${RAIL_LABEL[key].toLowerCase()} items`,
        }, `+${rail.older} older`));
      }
      railEls.push(railEl);
    }
    if (generation !== bandGeneration || !bandLive()) return;

    // Open rows open again: from the body already read when there is one, else fetched as a fresh open. A row that left
    // the band is forgotten; a failed read forgets nothing, so a retry that works shows them open again.
    const shown = new Set(rows.map((r) => r.item.id));
    if (!itemsFailed) for (const id of [...openRows]) if (!shown.has(id)) { openRows.delete(id); bodies.delete(id); }
    const refetch = [];
    for (const { item, row } of rows) {
      if (!openRows.has(item.id) || !row.setExpanded) continue;
      if (bodies.has(item.id)) row.setExpanded(buildExpandedNode(item, bodies.get(item.id)));
      else refetch.push({ item, row });
    }

    const place = focusPlace();
    bandEl.replaceChildren(...(itemsFailed ? [bandError()] : railEls));
    if (place) restoreFocus(place);
    for (const { item, row } of refetch) toggleRow(item, row);
  }

  async function refreshBand() {
    await loadItems();
    await renderBand();
  }

  // ── Inline expansion: fetch handover/decision body, render XML tags. ──────
  async function fetchBody(item) {
    if (!item?.id) return null;
    try {
      if (item.type === 'handover') return await api.get(`/api/handover/${encodeURIComponent(item.id)}`);
      if (item.type === 'decision') return await api.get(`/api/decisions/${encodeURIComponent(item.id)}`);
    } catch (e) {
      console.error('[desk] body fetch failed', e);
    }
    return null;
  }

  function buildExpandedNode(item, doc) {
    if (!doc) return h('p', { class: 'co-xblock__p' }, 'This could not be loaded. Try again in a moment.');
    const body = doc.body || '';
    if (item.type === 'decision') {
      const rationale = doc.resolved_rationale || doc.dropped_reason || '';
      const text = [rationale, body].filter(Boolean).join('\n\n');
      return renderBlock(text || 'No rationale was recorded.');
    }
    return renderBlock(body || 'This handover has no body.');
  }

  // The fetch each open row is waiting on: a row closed and opened again mid-fetch shows only the latest answer.
  const pendingBody = new WeakMap();

  async function toggleRow(item, controller) {
    if (controller.isExpanded()) {
      pendingBody.delete(controller);
      openRows.delete(item.id);
      bodies.delete(item.id);
      controller.clearExpanded();
      return;
    }
    const request = {};
    pendingBody.set(controller, request);
    openRows.add(item.id);
    controller.setLoading();
    const doc = await fetchBody(item);
    if (pendingBody.get(controller) !== request || !controller.root.isConnected) return;
    pendingBody.delete(controller);
    if (doc) bodies.set(item.id, doc);
    controller.setExpanded(buildExpandedNode(item, doc));
  }

  // ── Initial paint ────────────────────────────────────────────────────────
  await Promise.all([loadNotes(), loadItems(), loadCounts()]);
  renderStrip();
  renderBoard();
  await renderBand();
  if (!strip.root.isConnected) { unsubscribe?.(); packer?.disconnect(); return () => {}; }

  // Leaving with a note mid-edit: blurring its editor saves it, once, before the screen is torn down.
  return async () => {
    unsubscribe?.();
    packer?.disconnect();
    if (boardEl.contains(document.activeElement)) document.activeElement.blur();
  };
}
