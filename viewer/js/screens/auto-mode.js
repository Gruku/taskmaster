import { renderQuestSpine } from '../components/quest-spine.js';
import { renderFlightLog } from '../components/flight-log.js';
import { renderSessionsStrip } from '../components/sessions-strip.js';
import { autoListSessions, autoEvents, autoSession, autoBudget, autoPause as apiAutoPause, autoStop as apiAutoStop } from '../api.js';
import { renderLeftPanel, renderRightPanel } from '../components/auto-side-panels.js';

export const meta = { title: 'Auto Mode', icon: '◐', sidebarKey: 'auto_mode' };

export async function mount(root, ctx) {
  root.innerHTML = '';
  root.classList.add('auto-page');

  const { store, prefs } = ctx;

  const header = document.createElement('div');
  header.className = 'auto-header';
  header.innerHTML = `
    <div class="auto-title">Auto Mode</div>
    <div class="auto-controls">
      <button class="auto-control-btn auto-control-btn--pause" data-action="pause" title="Pause">⏸</button>
      <button class="auto-control-btn auto-control-btn--stop"  data-action="stop"  title="Stop">■</button>
    </div>
    <div class="auto-header-right">
      <div class="auto-toggle" role="tablist">
        <div class="auto-toggle-seg on"  data-view="A">Spine</div>
        <div class="auto-toggle-seg"     data-view="B">Log</div>
      </div>
    </div>
  `;
  root.appendChild(header);

  const stripRoot = document.createElement('div');
  root.appendChild(stripRoot);

  const grid = document.createElement('div');
  grid.className = 'auto-grid';
  const left   = document.createElement('div'); left.className   = 'auto-left';
  const center = document.createElement('div'); center.className = 'auto-center';
  const right  = document.createElement('div'); right.className  = 'auto-right';
  grid.append(left, center, right);
  root.appendChild(grid);

  // Restore persisted view — read from store, NOT from prefs patcher
  const prefsData = store?.getPrefs?.() || {};
  const initialView = prefsData.screens?.auto_mode?.view ?? 'A';
  // Mirror onto patcher so subsequent in-session reads still work
  prefs.screens = prefs.screens || {};
  prefs.screens.auto_mode = prefs.screens.auto_mode || {};
  prefs.screens.auto_mode.view = initialView;

  const segs = header.querySelectorAll('.auto-toggle-seg');
  segs.forEach((s) => {
    s.classList.toggle('on', s.dataset.view === initialView);
    s.setAttribute('role', 'tab');
    s.setAttribute('aria-selected', s.dataset.view === initialView ? 'true' : 'false');
  });

  let currentView = initialView;
  let cleanup;
  let logPoll = null;

  // Sessions strip state
  let sessionsList = [];
  let activeSid = null;

  async function refreshSessions() {
    sessionsList = await autoListSessions().catch(() => []);
    if (!activeSid && sessionsList[0]) activeSid = sessionsList[0].session_id;
    renderSessionsStrip(stripRoot, {
      sessions: sessionsList,
      activeSid,
      onSelect: (sid) => {
        activeSid = sid;
        store?.setActiveAutoSession?.(sid);
        renderSessionsStrip(stripRoot, {
          sessions: sessionsList,
          activeSid,
          onSelect: (s) => {
            activeSid = s;
            store?.setActiveAutoSession?.(s);
            renderSessionsStrip(stripRoot, { sessions: sessionsList, activeSid, onSelect: null });
            renderActiveView();
            refreshSidePanels();
          },
        });
        renderActiveView();
        refreshSidePanels();
      },
    });
  }
  refreshSessions();
  const sessionsPoll = setInterval(refreshSessions, 5000);

  function startLogPolling() {
    if (logPoll) return;
    logPoll = setInterval(() => {
      if (currentView !== 'B') return;
      const sid = activeSid ?? store?.getAutoState?.()?.session_id;
      if (!sid) return;
      autoEvents(sid).then((events) => {
        const cursorStage = store?.getAutoState?.()?.cursor?.stage ?? null;
        cleanup?.();
        cleanup = renderFlightLog(center, { events, cursorStage });
      }).catch(() => {});
    }, 3000);
  }
  function stopLogPolling() { clearInterval(logPoll); logPoll = null; }

  function renderActiveView() {
    cleanup?.();
    const state = store?.getAutoState?.() ?? null;
    if (currentView === 'A') {
      cleanup = renderQuestSpine(center, state);
    } else {
      const sid = activeSid ?? store?.getAutoState?.()?.session_id;
      const cursorStage = store?.getAutoState?.()?.cursor?.stage ?? null;
      if (!sid) {
        center.innerHTML = '<div class="flog-empty">No auto-mode session.</div>';
        cleanup = () => { center.innerHTML = ''; };
        return;
      }
      autoEvents(sid).then((events) => {
        cleanup = renderFlightLog(center, { events, cursorStage });
      }).catch((e) => {
        center.innerHTML = `<div class="flog-empty">Error loading events: ${e.message}</div>`;
        cleanup = () => { center.innerHTML = ''; };
      });
    }
  }

  segs.forEach((seg) => {
    seg.addEventListener('click', () => {
      const v = seg.dataset.view;
      if (v === currentView) return;
      currentView = v;
      prefs.screens.auto_mode.view = v;
      segs.forEach((s) => {
        s.classList.toggle('on', s.dataset.view === v);
        s.setAttribute('aria-selected', s.dataset.view === v ? 'true' : 'false');
      });
      prefs.patch({ screens: { auto_mode: { view: v } } });
      renderActiveView();
    });
  });

  // Pause/stop button handlers
  const pauseBtn = header.querySelector('[data-action="pause"]');
  const stopBtn  = header.querySelector('[data-action="stop"]');
  pauseBtn.setAttribute('aria-label', 'Pause auto-mode session');
  stopBtn.setAttribute('aria-label', 'Stop auto-mode session');

  pauseBtn.addEventListener('click', async () => {
    const sid = activeSid ?? store?.getAutoState?.()?.session_id;
    if (!sid) return;
    try {
      await apiAutoPause(sid);
      store?.refresh?.('autoState');
    } catch (e) {
      console.error('autoPause failed', e);
    }
  });

  stopBtn.addEventListener('click', async () => {
    const sid = activeSid ?? store?.getAutoState?.()?.session_id;
    if (!sid) return;
    if (!confirm(`Stop auto-mode session ${sid}?`)) return;
    try {
      await apiAutoStop(sid);
      store?.refresh?.('autoState');
    } catch (e) {
      console.error('autoStop failed', e);
    }
  });

  // First-visit helper note — read dismissal from store prefs
  if (!(prefsData.screens?.auto_mode?.helper_dismissed)) {
    const note = document.createElement('div');
    note.className = 'auto-helper-note';
    note.setAttribute('aria-live', 'polite');
    note.innerHTML = `
      <span>Spine is the live view. Log swaps to chronological waterfall — same data, denser. Use Log when debugging.</span>
      <span class="dismiss" role="button" aria-label="Dismiss helper note">✕</span>
    `;
    root.insertBefore(note, root.children[1] || null);
    note.querySelector('.dismiss').addEventListener('click', () => {
      prefs.patch({ screens: { auto_mode: { helper_dismissed: true } } });
      note.remove();
    });
  }

  // Side panels
  let leftCleanup = null, rightCleanup = null;

  async function refreshSidePanels() {
    const sid = activeSid ?? store?.getAutoState?.()?.session_id;
    if (!sid) {
      leftCleanup?.(); rightCleanup?.();
      left.innerHTML = ''; right.innerHTML = '';
      return;
    }
    const [detail, budget] = await Promise.all([
      autoSession(sid),
      autoBudget(sid).catch(() => null),
    ]);
    if (!detail) return;
    leftCleanup?.();
    leftCleanup = renderLeftPanel(left, { state: detail });
    rightCleanup?.();
    rightCleanup = renderRightPanel(right, { state: detail, meters: budget?.meters ?? {} });
  }
  refreshSidePanels();
  const panelsPoll = setInterval(refreshSidePanels, 4000);

  // Initial render
  renderActiveView();

  const unsub = store?.subscribe?.('autoState', () => {
    renderActiveView();
  });

  startLogPolling();

  return () => {
    cleanup?.();
    unsub?.();
    stopLogPolling();
    clearInterval(sessionsPoll);
    clearInterval(panelsPoll);
    leftCleanup?.();
    rightCleanup?.();
    root.classList.remove('auto-page');
  };
}
