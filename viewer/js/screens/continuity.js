import { h } from '../util/h.js';
import { createViewSwitcher } from '../components/continuity/view-switcher.js';
import { createAutoModeStrip } from '../components/auto-mode-strip.js';
import { claimTopbar } from '../lib/topbar.js';

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

  function render() {
    body.replaceChildren(
      h('p', {}, `(${activeView} view placeholder — Task 14 fills this in)`),
    );
  }

  render();

  return async () => {
    strip?.destroy?.();
  };
}
