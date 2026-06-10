import { h } from '../util/h.js';
import { claimTopbar } from '../lib/topbar.js';
import { createAutoModeStrip } from '../components/auto-mode-strip.js';
import { createSpine } from '../components/continuity/spine.js';
import { createDecisionCard } from '../components/continuity/decision-card.js';
import { renderBlock } from '../lib/xml-render.js';
import { buildRails, sortNotes } from '../lib/desk.js';
import { createNoteCard } from '../components/desk/note-card.js';
import { createComposer } from '../components/desk/composer.js';

export const meta = { title: 'Dashboard', icon: '◧', sidebarKey: 'dashboard' };

// Where the "+N older" link points per rail — these screens hold the full,
// uncapped list for the rail's entity type.
const OLDER_TARGET = { resume: '#/sessions', review: '#/table', decide: '#/table', cleanup: '#/issues' };
const RAIL_LABEL = { resume: 'Resume', review: 'Review', decide: 'Decide', cleanup: 'Clean-up' };

export async function mount(root, { store, api }) {
  root.classList.add('dk-desk');

  // Topbar: project label only (no view switcher — the desk has a single view).
  const topbarSlot = claimTopbar();
  if (topbarSlot) topbarSlot.appendChild(h('span', { class: 'dk-proj' }, store?.projectName?.() || ''));

  const autoSlot = h('section', { class: 'dk-auto' });
  const strip = createAutoModeStrip({ store, api, mode: 'dashboard' });
  if (strip?.root) autoSlot.appendChild(strip.root);

  const boardEl = h('section', { class: 'dk-board', 'aria-label': 'Sticky notes' });
  const bandEl = h('section', { class: 'dk-continuity', 'aria-label': 'Continuity' });
  root.replaceChildren(autoSlot, boardEl, bandEl);

  let notes = [];
  let items = [];

  async function loadNotes() {
    try { notes = (await api.notes())?.notes || []; }
    catch (e) { console.error('[desk] notes fetch failed', e); notes = []; }
  }
  async function loadItems() {
    try { items = (await api.get('/api/continuity'))?.items || []; }
    catch (e) { console.error('[desk] continuity fetch failed', e); items = []; }
  }

  // ── Board (sticky notes) ─────────────────────────────────────────────────
  const composer = createComposer({
    onCreate: async (text) => { await api.createNote(text); await refreshBoard(); },
  });

  function renderBoard() {
    boardEl.replaceChildren(composer.root);
    for (const note of sortNotes(notes)) {
      const card = createNoteCard({
        note,
        onPin: async (n) => { await api.updateNote(n.id, { pinned: !n.pinned }); await refreshBoard(); },
        onArchive: async (n) => { await api.archiveNote(n.id); await refreshBoard(); },
        onSave: async (n, text) => { await api.updateNote(n.id, { text }); await refreshBoard(); },
      });
      boardEl.appendChild(card.root);
    }
    if (notes.length === 0) boardEl.appendChild(h('p', { class: 'dk-empty' }, 'Your desk is clear.'));
  }
  async function refreshBoard() { await loadNotes(); renderBoard(); }

  // ── Continuity band ──────────────────────────────────────────────────────
  async function fetchDecision(id) {
    try { return await api.get(`/api/decisions/${encodeURIComponent(id)}`); }
    catch { return null; }
  }

  async function resolveDecision(id, optionIndex) {
    try {
      await api.post(`/api/decisions/${encodeURIComponent(id)}/resolve`, { resolved_with: optionIndex, rationale: '' });
      await loadItems();
      await renderBand();
    } catch (e) { console.error('[desk] resolve decision failed', e); }
  }

  async function dropDecision(id) {
    try {
      await api.post(`/api/decisions/${encodeURIComponent(id)}/drop`, { reason: 'dropped via viewer' });
      await loadItems();
      await renderBand();
    } catch (e) { console.error('[desk] drop decision failed', e); }
  }

  // Decide rail — spine-styled heading + a decision card per item. Decisions
  // are interactive (resolve / drop), so they don't collapse into rows.
  async function renderDecideRail(rail) {
    const railEl = h('section', { class: 'co-spine' });
    railEl.appendChild(h('div', { class: 'co-spine__head' },
      h('span', { class: 'co-spine__label' }, RAIL_LABEL.decide),
      h('span', { class: 'co-spine__count' }, String(rail.items.length)),
    ));
    const list = h('div', { class: 'co-spine__list' });
    for (const item of rail.items) {
      const decision = await fetchDecision(item.id);
      if (!decision) continue;
      const card = createDecisionCard({
        item,
        decision,
        onResolve: (idx) => resolveDecision(item.id, idx),
        onDrop: (id) => dropDecision(id),
      });
      list.appendChild(card.root);
    }
    railEl.appendChild(list);
    return railEl;
  }

  async function renderBand() {
    bandEl.replaceChildren();
    const rails = buildRails(items);
    for (const key of ['resume', 'review', 'decide', 'cleanup']) {
      const rail = rails[key];
      if (rail.items.length === 0 && rail.older === 0) continue;
      let railEl;
      if (key === 'decide' && rail.items.length > 0) {
        railEl = await renderDecideRail(rail);
      } else {
        railEl = createSpine({ label: RAIL_LABEL[key], items: rail.items, empty: true, onItemClick: expandRow }).root;
      }
      if (railEl && rail.older > 0) {
        railEl.appendChild(h('a', { class: 'dk-older', href: OLDER_TARGET[key] }, `+${rail.older} older`));
      }
      if (railEl) bandEl.appendChild(railEl);
    }
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
    if (!doc) return h('p', { class: 'co-xblock__p' }, 'Failed to load body.');
    const body = doc.body || '';
    if (item.type === 'decision') {
      const rationale = doc.resolved_rationale || doc.dropped_reason || '';
      const text = [rationale, body].filter(Boolean).join('\n\n');
      return renderBlock(text || '(no rationale recorded)');
    }
    return renderBlock(body || '(empty body)');
  }

  async function expandRow(item, controller) {
    if (!controller) return;
    if (controller.isExpanded()) {
      controller.clearExpanded();
      return;
    }
    if (item.type !== 'handover' && item.type !== 'decision') return;
    controller.setLoading();
    const doc = await fetchBody(item);
    // Only render if still expanded (user may have collapsed mid-fetch).
    if (!controller.isExpanded()) return;
    controller.setExpanded(buildExpandedNode(item, doc));
  }

  // ── Initial paint ────────────────────────────────────────────────────────
  await Promise.all([loadNotes(), loadItems()]);
  renderBoard();
  await renderBand();
  composer.focus();

  return async () => { strip?.destroy?.(); };
}
