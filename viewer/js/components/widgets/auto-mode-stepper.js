// auto-mode-stepper widget — dashboard tile that mirrors the auto-mode page.
// Replaces the Plan-4 stub. See spec §3.15 (Compact Horizontal Stepper).

import { registerWidget } from '../widget-catalog.js';
import { pluralize } from '../../util/pluralize.js';

export const meta = {
  id: 'auto-mode-stepper',
  type: 'auto-mode-stepper',
  defaultSize: 'wide',           // ~480px tile
  title: 'Auto Mode',
  // keep Plan-4 catalog fields for compatibility
  label: 'Auto Mode · stepper',
  sizes: ['medium', 'wide'],
  defaultRail: 'right',
};

const STEPS = [
  { key: 'PICK',          label: 'Pick' },
  { key: 'PLAN',          label: 'Plan' },
  { key: 'SPEC_REVIEW',   label: 'Review' },
  { key: 'TESTS',         label: 'Tests' },
  { key: 'IMPLEMENT',     label: 'Implement' },
  { key: 'TEST',          label: 'Test' },
  { key: 'REVIEW',        label: 'Review' },
  { key: 'COMPLETE',      label: 'Done' },
];

export async function mount(root, ctx) {
  root.innerHTML = '';
  const tile = document.createElement('div');
  tile.className = 'stepper-widget';
  tile.setAttribute('role', 'link');
  tile.setAttribute('tabindex', '0');
  root.appendChild(tile);

  function go() { window.location.hash = '#/auto'; }
  tile.addEventListener('click', go);
  tile.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
  });

  let cleanupSub = null;

  function paint(state) {
    if (!state || !state.cursor) {
      tile.innerHTML = `
        <div class="stepper-head">
          <span class="stepper-h">Auto Mode</span>
          <span class="stepper-link">Open</span>
        </div>
        <div class="stepper-empty">No auto-mode running</div>
      `;
      return;
    }
    const completed = new Set(state.completed ?? []);
    const cursor = state.cursor.stage;
    const subagentCount = (state.subagents ?? []).filter((s) => s.status === 'running').length;
    const tokens = state?.budget?.tokens;

    const stepsHtml = STEPS.map((s, i) => {
      let cls = 'pending';
      if (completed.has(s.key)) cls = 'done';
      else if (s.key === cursor) cls = 'active';
      return `
        <div class="stepper-step stepper-step--${cls}" data-step="${s.key}">
          <div class="stepper-circle"></div>
          <div class="stepper-label">${s.label}</div>
        </div>
      `;
    }).join('');

    tile.innerHTML = `
      <div class="stepper-head">
        <span class="stepper-h">Auto Mode <span class="stepper-running">· running</span></span>
        <span class="stepper-link">Open</span>
      </div>
      <div class="stepper-task">
        <span class="stepper-id">${escape(state.session_id ?? state.task_id ?? '')}</span>
        <span class="stepper-title">${escape(state.title ?? '')}</span>
        <span class="stepper-wt">${escape(state.worktree ?? '')}</span>
      </div>
      <div class="stepper-track">${stepsHtml}</div>
      <div class="stepper-foot">
        <span class="stepper-dot"></span>
        <span class="stepper-elapsed">${elapsed(state.started_at)}</span>
        <span class="stepper-foot-sep">·</span>
        <span class="stepper-sub">${subagentCount} ${pluralize(subagentCount, 'subagent', 'subagents')}</span>
        ${tokens ? `<span class="stepper-foot-sep">·</span><span class="stepper-tokens">${formatTokens(tokens.used)} / ${formatTokens(tokens.limit)}</span>` : ''}
      </div>
    `;
  }

  paint(ctx.store.getAutoState?.() ?? null);
  cleanupSub = ctx.store.subscribe?.('autoState', paint);

  ctx.api.autoListSessions?.().then((sessions) => {
    if (!sessions || sessions.length <= 1) return;
    const moreEl = tile.querySelector('.stepper-link');
    if (!moreEl) return;
    const pill = document.createElement('span');
    pill.className = 'stepper-more';
    pill.textContent = `+${sessions.length - 1} more`;
    moreEl.before(pill);
  }).catch(() => {});

  return () => { cleanupSub?.(); root.innerHTML = ''; };
}

function elapsed(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatTokens(n) {
  if (!n && n !== 0) return '?';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

registerWidget({ meta, mount });
