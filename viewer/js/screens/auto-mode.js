import { renderQuestSpine } from '../components/quest-spine.js';
import { renderFlightLog } from '../components/flight-log.js';

export const meta = { title: 'Auto Mode', icon: '◐', sidebarKey: 'auto_mode' };

export async function mount(root, ctx) {
  root.innerHTML = '';
  root.classList.add('auto-page');

  const { store, api, prefs } = ctx;

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

  function startLogPolling() {
    if (logPoll) return;
    logPoll = setInterval(() => {
      if (currentView !== 'B') return;
      const sid = ctx.store.getAutoState?.()?.session_id;
      if (!sid) return;
      ctx.api.autoEvents(sid).then((events) => {
        const cursorStage = ctx.store.getAutoState?.()?.cursor?.stage ?? null;
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
      const sid = ctx.store.getAutoState?.()?.session_id;
      const cursorStage = ctx.store.getAutoState?.()?.cursor?.stage ?? null;
      if (!sid) {
        center.innerHTML = '<div class="flog-empty">No auto-mode session.</div>';
        cleanup = () => { center.innerHTML = ''; };
        return;
      }
      ctx.api.autoEvents(sid).then((events) => {
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
    const sid = store?.getAutoState?.()?.session_id;
    if (!sid) return;
    try {
      await api.autoPause(sid);
      store?.refresh?.('autoState');
    } catch (e) {
      console.error('autoPause failed', e);
    }
  });

  stopBtn.addEventListener('click', async () => {
    const sid = store?.getAutoState?.()?.session_id;
    if (!sid) return;
    if (!confirm(`Stop auto-mode session ${sid}?`)) return;
    try {
      await api.autoStop(sid);
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
    root.classList.remove('auto-page');
  };
}
