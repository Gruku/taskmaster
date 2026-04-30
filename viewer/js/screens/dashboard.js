import { computePlacements } from '../components/dashboard-grid.js';
import { createBriefingStrip } from '../components/briefing-strip.js';
import { createBoardSurface } from '../components/board-surface.js';
import { createWidgetFrame } from '../components/widget-frame.js';
import { getWidget, defaultLayout, listWidgets } from '../components/widget-catalog.js';
import { createAutoModeStrip } from '../components/auto-mode-strip.js';

// Eager-import widget modules so they self-register via widget-catalog.
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

  const cleanups = [];

  const briefing = createBriefingStrip({ store, api, prefs });
  root.appendChild(briefing.root);
  cleanups.push(() => briefing.destroy());

  const autoSlot = document.createElement('section');
  autoSlot.className = 'dash-automode';
  root.appendChild(autoSlot);
  const autoStrip = createAutoModeStrip({ store, api, mode: 'dashboard' });
  if (autoStrip && autoStrip.root) {
    autoSlot.appendChild(autoStrip.root);
    cleanups.push(() => autoStrip.destroy && autoStrip.destroy());
  }

  const bento = document.createElement('section');
  bento.className = 'dash-bento';
  root.appendChild(bento);

  const railLeft  = document.createElement('div'); railLeft.className  = 'dash-bento__rail dash-bento__rail--left';
  const railRight = document.createElement('div'); railRight.className = 'dash-bento__rail dash-bento__rail--right';
  const board = createBoardSurface({ store });
  bento.append(railLeft, board.root, railRight);
  cleanups.push(() => board.destroy());

  const bottom = document.createElement('section');
  bottom.className = 'dash-bottom';
  root.appendChild(bottom);

  // Seed layout if empty
  let layout = (prefs && prefs.dashboard && prefs.dashboard.layout) || [];
  if (!layout.length) {
    layout = defaultLayout();
    await api.savePrefs({ dashboard: { layout } });
  }

  const placements = computePlacements(layout);
  const widgetCleanups = new Map();

  for (const { instance } of placements) {
    const mod = getWidget(instance.type);
    if (!mod) {
      console.warn('[dashboard] unknown widget type', instance.type);
      continue;
    }
    const frame = createWidgetFrame({
      instance,
      label: mod.meta.label,
      onRemove: () => {/* M4 wires this to edit-mode */},
    });
    const target =
      instance.rail === 'right'  ? railRight :
      instance.rail === 'bottom' ? bottom    : railLeft;
    target.appendChild(frame.root);

    const cleanup = await mod.mount(frame.body, { store, api, prefs, size: instance.size, instance });
    widgetCleanups.set(instance.id, cleanup);
  }

  return async () => {
    for (const fn of cleanups) await fn?.();
    for (const fn of widgetCleanups.values()) await fn?.();
  };
}
