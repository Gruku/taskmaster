import { computePlacements } from '../components/dashboard-grid.js';
import { createBriefingStrip } from '../components/briefing-strip.js';
import { createBoardSurface } from '../components/board-surface.js';

export const meta = { title: 'Dashboard', icon: '◧', sidebarKey: 'dashboard' };

export async function mount(root, { store, api, prefs }) {
  root.classList.add('dash');
  root.dataset.edit = '0';

  const briefing = createBriefingStrip({ store, api, prefs });
  root.appendChild(briefing.root);

  const automode = document.createElement('section');
  automode.className = 'dash-automode';
  root.appendChild(automode); // M3 fills this in via Plan 2's auto-mode-strip

  const bento = document.createElement('section');
  bento.className = 'dash-bento';
  root.appendChild(bento);

  const railLeft   = document.createElement('div'); railLeft.className   = 'dash-bento__rail dash-bento__rail--left';
  const railRight  = document.createElement('div'); railRight.className  = 'dash-bento__rail dash-bento__rail--right';
  const board = createBoardSurface({ store });
  bento.append(railLeft, board.root, railRight);

  const bottom = document.createElement('section');
  bottom.className = 'dash-bottom';
  root.appendChild(bottom);

  // Placeholder placements; widget mounting added in M2/M3.
  const placements = computePlacements((prefs && prefs.dashboard && prefs.dashboard.layout) || []);
  console.debug('[dashboard] placements', placements.length);

  return async () => {
    briefing.destroy();
    board.destroy();
  };
}
