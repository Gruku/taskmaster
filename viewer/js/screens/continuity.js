import { h } from '../util/h.js';
import { createViewSwitcher } from '../components/continuity/view-switcher.js';
import { createAutoModeStrip } from '../components/auto-mode-strip.js';
import { claimTopbar } from '../lib/topbar.js';
import { groupByAction, groupByTime, groupByEntity, pickHero } from '../lib/continuity.js';
import { createHero } from '../components/continuity/hero.js';
import { createSpine } from '../components/continuity/spine.js';
import { createItemRow } from '../components/continuity/item-row.js';

export const meta = { title: 'Continuity', icon: '◧', sidebarKey: 'dashboard' };

export async function mount(root, { store, api, prefs }) {
  root.classList.add('co-dash');

  // Read persisted view preference from store (prefs arg is the patch helper, not data).
  const prefsData = store?.getPrefs?.() || {};
  let activeView = prefsData?.continuity?.view || 'action';

  // Build topbar: project label + view switcher in the #topbar-actions slot.
  const topbarSlot = claimTopbar();
  const projLabel = h('span', { class: 'co-dash__proj' },
    store?.projectName?.() || 'taskmaster');
  const sw = createViewSwitcher({
    active: activeView,
    onSelect: (v) => {
      activeView = v;
      prefs.patch({ continuity: { view: v } });
      render();
    },
  });
  if (topbarSlot) {
    topbarSlot.appendChild(projLabel);
    topbarSlot.appendChild(sw.root);
  }

  // Auto-mode strip slot.
  const autoSlot = document.createElement('section');
  autoSlot.className = 'co-dash__auto';
  const strip = createAutoModeStrip({ store, api, mode: 'dashboard' });
  if (strip?.root) autoSlot.appendChild(strip.root);

  const body = h('section', { class: 'co-dash__body' });
  const footer = h('section', { class: 'co-dash__footer' });

  root.replaceChildren(autoSlot, body, footer);

  // ── Fetch continuity items once; re-use across renders. ────────────────
  let items = [];
  let fetchError = null;

  async function loadItems() {
    try {
      const res = await api.get('/api/continuity');
      items = res?.items || [];
    } catch (e) {
      fetchError = e;
      items = [];
    }
  }

  // ── Helpers for async decision detail fetch. ───────────────────────────
  async function fetchDecision(id) {
    try {
      return await api.get(`/api/decisions/${id}`);
    } catch {
      return null;
    }
  }

  async function resolveDecision(id, optionIndex) {
    try {
      await api.post(`/api/decisions/${id}/resolve`, { resolved_with: optionIndex, rationale: '' });
      await loadItems();
      render();
    } catch (e) {
      console.error('resolve decision failed', e);
    }
  }

  async function dropDecision(id) {
    try {
      await api.post(`/api/decisions/${id}/drop`, { reason: 'dropped via viewer' });
      await loadItems();
      render();
    } catch (e) {
      console.error('drop decision failed', e);
    }
  }

  // ── View renderers ─────────────────────────────────────────────────────

  async function renderActionView() {
    const wrap = h('div', { class: 'co-action-view' });

    const hero = pickHero(items);
    let decision = null;
    if (hero?.type === 'decision') {
      decision = await fetchDecision(hero.id);
    }

    const heroEl = createHero({
      item: hero,
      decision,
      onResolve: (idx) => resolveDecision(hero.id, idx),
      onDrop: (id) => dropDecision(id),
    });
    if (heroEl.root) wrap.appendChild(heroEl.root);

    const rails = groupByAction(items);
    const railsEl = h('div', { class: 'co-action-view__rails' });
    for (const [label, railItems] of Object.entries(rails)) {
      const spine = createSpine({
        label,
        items: railItems,
        empty: true,
        onItemClick: (item) => navigate(item),
      });
      if (spine.root) railsEl.appendChild(spine.root);
    }
    wrap.appendChild(railsEl);
    return wrap;
  }

  function renderTimeView() {
    const wrap = h('div', { class: 'co-time-view' });
    const buckets = groupByTime(items);
    const LABELS = { today: 'Today', yesterday: 'Yesterday', earlier: 'This week', drifting: 'Drifting' };
    for (const [key, bucket] of Object.entries(buckets)) {
      if (bucket.length === 0) continue;
      const section = h('div', { class: 'co-time-view__bucket' });
      section.appendChild(h('div', { class: 'co-time-view__bucket-head' }, LABELS[key] || key));
      for (const item of bucket) {
        const row = createItemRow({ item, onClick: (it) => navigate(it) });
        section.appendChild(row.root);
      }
      wrap.appendChild(section);
    }
    if (items.length === 0) {
      wrap.appendChild(h('p', { class: 'co-spine__empty' }, 'No continuity items.'));
    }
    return wrap;
  }

  function renderEntityView() {
    const wrap = h('div', { class: 'co-entity-view' });
    const groups = groupByEntity(items);
    for (const [type, groupItems] of Object.entries(groups)) {
      if (groupItems.length === 0) continue;
      const spine = createSpine({
        label: type,
        items: groupItems,
        onItemClick: (item) => navigate(item),
      });
      if (spine.root) {
        const section = h('div', { class: 'co-entity-view__section' });
        section.appendChild(spine.root);
        wrap.appendChild(section);
      }
    }
    if (items.length === 0) {
      wrap.appendChild(h('p', { class: 'co-spine__empty' }, 'No continuity items.'));
    }
    return wrap;
  }

  // ── Navigation placeholder (detail panels wired in later tasks). ───────
  function navigate(item) {
    // TODO: open detail panel for item (Task 15+).
    console.log('[continuity] navigate to', item?.id, item?.type);
  }

  // ── Main render ────────────────────────────────────────────────────────
  async function render() {
    body.replaceChildren(h('p', { class: 'co-dash__loading' }, 'Loading…'));
    await loadItems();

    let content;
    if (activeView === 'time') {
      content = renderTimeView();
    } else if (activeView === 'entity') {
      content = renderEntityView();
    } else {
      content = await renderActionView();
    }
    body.replaceChildren(content);
  }

  render();

  return async () => {
    strip?.destroy?.();
  };
}
