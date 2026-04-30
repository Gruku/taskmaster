import { computePlacements } from '../components/dashboard-grid.js';
import { createBriefingStrip } from '../components/briefing-strip.js';
import { createBoardSurface } from '../components/board-surface.js';
import { createWidgetFrame } from '../components/widget-frame.js';
import { getWidget, defaultLayout } from '../components/widget-catalog.js';
import { createAutoModeStrip } from '../components/auto-mode-strip.js';
import { createEditMode, createAddTile } from '../components/edit-mode.js';

import './../components/widgets/suggested-next.js';
import './../components/widgets/phase-deliverables.js';
import './../components/widgets/newly-unblocked.js';
import './../components/widgets/what-changed.js';
import './../components/widgets/last-session.js';
import './../components/widgets/open-issues.js';
import './../components/widgets/build-test-pulse.js';
import './../components/widgets/lessons-digest.js';
import './../components/widgets/quick-capture.js';
import './../components/widgets/recent-commits.js';
import './../components/widgets/agent-activity.js';
import './../components/widgets/stale-tasks.js';
import './../components/widgets/auto-mode-stepper.js';

export const meta = { title: 'Dashboard', icon: '◧', sidebarKey: 'dashboard' };

export async function mount(root, { store, api, prefs }) {
  root.classList.add('dash');
  root.dataset.edit = '0';

  const briefing = createBriefingStrip({ store, api, prefs });
  const autoSlot = document.createElement('section'); autoSlot.className = 'dash-automode';
  const autoStrip = createAutoModeStrip({ store, api, mode: 'dashboard' });
  if (autoStrip && autoStrip.root) autoSlot.appendChild(autoStrip.root);

  const bento  = document.createElement('section'); bento.className  = 'dash-bento';
  const bottom = document.createElement('section'); bottom.className = 'dash-bottom';
  const railLeft  = document.createElement('div'); railLeft.className  = 'dash-bento__rail dash-bento__rail--left';
  const railRight = document.createElement('div'); railRight.className = 'dash-bento__rail dash-bento__rail--right';
  const board = createBoardSurface({ store });
  bento.append(railLeft, board.root, railRight);

  let widgetCleanups = [];

  const edit = createEditMode({
    root, api, prefs,
    refresh: () => render(),
  });

  // Header row holds the edit toggle.
  const headerRow = document.createElement('header');
  headerRow.style.cssText = 'display:flex;justify-content:flex-end;';
  headerRow.appendChild(edit.toggle);

  root.replaceChildren(headerRow, briefing.root, autoSlot, bento, bottom);

  // Seed layout if empty.
  let layout = (prefs && prefs.dashboard && prefs.dashboard.layout) || [];
  if (!layout.length) {
    layout = defaultLayout();
    await api.savePrefs({ dashboard: { layout } });
    prefs.dashboard = prefs.dashboard || {};
    prefs.dashboard.layout = layout;
  }

  async function render() {
    // Tear down current widgets.
    for (const fn of widgetCleanups) await fn?.();
    widgetCleanups = [];
    railLeft.replaceChildren();
    railRight.replaceChildren();
    bottom.replaceChildren();

    const placements = computePlacements(prefs.dashboard.layout || []);
    for (const { instance } of placements) {
      const mod = getWidget(instance.type);
      if (!mod) continue;
      const frame = createWidgetFrame({
        instance,
        label: mod.meta.label,
        onRemove: async (id) => { await edit.onRemove(id); },
      });
      const target =
        instance.rail === 'right'  ? railRight :
        instance.rail === 'bottom' ? bottom    : railLeft;
      target.appendChild(frame.root);
      const cleanup = await mod.mount(frame.body, { store, api, prefs, size: instance.size, instance });
      widgetCleanups.push(cleanup);
    }

    // Add tiles per rail (visible only in edit mode via CSS).
    railLeft.appendChild(createAddTile({ rail: 'left',  onAdd: edit.onAdd }));
    railRight.appendChild(createAddTile({ rail: 'right', onAdd: edit.onAdd }));
    bottom.appendChild(createAddTile({ rail: 'bottom', onAdd: edit.onAdd }));
  }

  await render();

  return async () => {
    briefing.destroy();
    board.destroy();
    autoStrip && autoStrip.destroy && autoStrip.destroy();
    for (const fn of widgetCleanups) await fn?.();
  };
}
