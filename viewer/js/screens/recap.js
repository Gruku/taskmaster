import { listSessions, getRecap, putRecap, getSnapshotDiff } from '../api.js';
import { renderReceiptsGrid } from '../components/recap-receipts-grid.js';
import { claimTopbar, tmAction } from '../lib/topbar.js';
import { emptyState } from '../components/empty-state.js';

export const meta = { title: 'Recap', icon: '⚯', sidebarKey: 'recap' };

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function mount(root, { params, subpath }) {
  root.innerHTML = `<div class="recap-page" data-role="root"><div class="stub">Loading recap…</div></div>`;
  const sessions = await listSessions();
  const recapSessions = sessions.filter(s => s.recap_id);

  const targetId = (subpath && subpath[0]) || (params && params.id) || (recapSessions[0] && recapSessions[0].id);
  if (!targetId) {
    const host = root.querySelector('[data-role=root]');
    host.innerHTML = '';
    host.appendChild(emptyState({
      headline: 'No recaps yet',
      hint: 'Close a session to generate one.',
    }));
    return () => {};
  }

  const idx = recapSessions.findIndex(s => s.id === targetId);
  const cur = recapSessions[idx];
  if (!cur) {
    const host = root.querySelector('[data-role=root]');
    host.innerHTML = '';
    host.appendChild(emptyState({
      headline: `No recap found for ${targetId}`,
      hint: 'It may have been deleted, or the id is wrong.',
    }));
    return () => {};
  }
  const prev = recapSessions[idx + 1] || null;
  const next = recapSessions[idx - 1] || null;

  const recap = await getRecap(cur.id);
  let diff = { tasks_added:[], tasks_changed:[], tasks_removed:[],
               files_touched:[], lessons_fired:[], issues_opened:[], issues_transitioned:[] };
  let snapshotsUnavailable = false;
  if (recap && recap.frontmatter && recap.frontmatter.snapshot_before && recap.frontmatter.snapshot_after) {
    try {
      diff = await getSnapshotDiff(recap.frontmatter.snapshot_before, recap.frontmatter.snapshot_after);
    } catch (e) {
      console.warn('snapshot diff failed', e);
      snapshotsUnavailable = true;
    }
  } else {
    snapshotsUnavailable = true;
  }
  diff._unavailable = snapshotsUnavailable;

  let editing = false;
  function paint() {
    renderRecapPage(root, { cur, prev, next, recap, diff, editing });
    bindFilterChips(root);
    mountTopbar();
  }

  function mountTopbar() {
    const topbar = claimTopbar();
    if (!topbar) return;
    if (prev) topbar.appendChild(tmAction({
      icon: '‹', variant: 'icon', title: 'Previous recap',
      onClick: () => { window.location.hash = `#/recap/${prev.id}`; },
    }));
    if (next) topbar.appendChild(tmAction({
      icon: '›', variant: 'icon', title: 'Next recap',
      onClick: () => { window.location.hash = `#/recap/${next.id}`; },
    }));
    if (editing) {
      const cancelBtn = tmAction({
        label: 'Cancel', title: 'Cancel edits',
        onClick: () => { editing = false; paint(); },
      });
      const saveBtn = tmAction({
        icon: '✓', label: 'Save', variant: 'primary', title: 'Save recap',
        onClick: async () => {
          const wh = root.querySelector('[data-role=ed-what-happened]').value;
          const wl = root.querySelector('[data-role=ed-what-landed]').value;
          const wn = root.querySelector('[data-role=ed-whats-next]').value;
          const title = root.querySelector('[data-role=ed-title]').value;
          const fm = (recap && recap.frontmatter) || {};
          await putRecap(cur.id, {
            frontmatter: {
              snapshot_before: fm.snapshot_before, snapshot_after: fm.snapshot_after,
              generator: 'manual', generated_at: new Date().toISOString(),
              token_cost: 0,
            },
            title, what_happened: wh, what_landed: wl, whats_next: wn,
          });
          recap = await getRecap(cur.id);
          editing = false; paint();
        },
      });
      const regenBtn = tmAction({
        icon: '↻', label: 'Regenerate', title: 'Restore draft from disk',
        onClick: async () => {
          const fresh = await getRecap(cur.id);
          root.querySelector('[data-role=ed-title]').value = (fresh && fresh.title) || '';
          root.querySelector('[data-role=ed-what-happened]').value = (fresh && fresh.what_happened) || '';
          root.querySelector('[data-role=ed-what-landed]').value = (fresh && fresh.what_landed) || '';
          root.querySelector('[data-role=ed-whats-next]').value = (fresh && fresh.whats_next) || '';
        },
      });
      topbar.append(cancelBtn, saveBtn, regenBtn);
    } else {
      const copyBtn = tmAction({
        icon: '⧉', label: 'Copy resume', title: 'Copy resume to clipboard',
        onClick: () => {
          const text = `${recap?.what_happened || ''}\n\n${recap?.whats_next || ''}`;
          navigator.clipboard?.writeText(text);
          copyBtn.replaceChildren();
          const i = document.createElement('span'); i.className = 'tm-action__icon'; i.textContent = '✓';
          const t = document.createElement('span'); t.textContent = 'Copied';
          copyBtn.append(i, t);
          setTimeout(() => mountTopbar(), 1200);
        },
      });
      const openBtn = tmAction({
        icon: '↗', label: 'Open in Sessions', title: 'Open the owning session',
        onClick: () => { window.location.hash = `#/sessions/${cur.id}`; },
      });
      const editBtn = tmAction({
        icon: '✎', label: 'Edit recap', variant: 'primary', title: 'Edit recap',
        onClick: () => { editing = true; paint(); },
      });
      topbar.append(copyBtn, openBtn, editBtn);
    }
  }

  paint();
  return () => {};
}

function renderRecapPage(root, { cur, prev, next, recap, diff, editing }) {
  const fm = (recap && recap.frontmatter) || {};
  const stats = computeStats(diff);
  const draftCaption = fm.generated_at
    ? `draft generated ${escapeHtml(String(fm.generated_at).slice(0, 16))} by ${escapeHtml(fm.generator || '?')}`
    : '';

  root.innerHTML = (
    `<div class="recap-page">`
    + `<div class="recap-hero">`
    + `<div class="recap-hero-top">`
    + `<span class="recap-hero-kind">RECAP</span>`
    + `<div class="recap-hero-meta">`
    + `<span class="mono">${escapeHtml(cur.id)}</span><span class="dot"></span>`
    + `<span>${escapeHtml(fm.generator || 'claude')}</span><span class="dot"></span>`
    + `<span>${formatDuration(cur.duration)}</span><span class="dot"></span>`
    + `<span class="mono">vs ${escapeHtml(fm.snapshot_before || '—')}</span>`
    + `</div>`
    + `</div>`
    + (editing
        ? `<input class="narr-edit" data-role="ed-title" value="${escapeHtml((recap&&recap.title)||'')}" style="width:100%; padding:8px; font-size:20px; background:var(--bg-deep); color:var(--ink); border:1px solid var(--border); border-radius:6px; margin-bottom:8px;">`
        : `<h1 class="recap-hero-title">${escapeHtml((recap && recap.title) || '(untitled)')}</h1>`)
    + `<div class="recap-hero-subtitle mono">${escapeHtml(cur.id)} → ${escapeHtml(fm.snapshot_after || '—')} · diff vs ${escapeHtml(fm.snapshot_before || '—')}</div>`
    + `<div class="narrative">`
    + section('What happened',  recap && recap.what_happened, editing, 'ed-what-happened', draftCaption)
    + section('What landed',    recap && recap.what_landed,   editing, 'ed-what-landed',   draftCaption)
    + section("What's next",    recap && recap.whats_next,    editing, 'ed-whats-next',    draftCaption)
    + `</div>`
    + `<div class="recap-stats">`
    +   stat('add', stats.tasks_done,   'tasks done')
    +   stat('mod', stats.tasks_moved,  'tasks moved')
    +   stat('',    stats.lessons_fired,'lessons fired')
    +   stat('del', stats.issues_opened,'issues opened')
    +   stat('add', stats.files_touched,'files touched')
    + `</div>`
    + `</div>`
    + `<div class="receipts-h">`
    + `<h3>Receipts</h3>`
    + `<span class="vs mono">diff <span class="snap">${escapeHtml(fm.snapshot_before || '—')}</span> → <span class="snap">${escapeHtml(fm.snapshot_after || '—')}</span></span>`
    + `<div class="filt" data-role="filt">`
    +   ['All','Tasks','Lessons','Issues','Files'].map((label, i) =>
        `<span class="filt-chip ${i===0?'on':''}" data-filt="${label.toLowerCase()}">${label}</span>`).join('')
    + `</div>`
    + `</div>`
    + renderReceiptsGrid(diff)
    + `<div class="recap-footer">`
    + `<span>Handovers: ${(cur.handover_ids||[]).map(h => `<a href="#/sessions/${escapeHtml(cur.id)}">${escapeHtml(h)}</a>`).join(' · ') || '—'}</span>`
    + `<span>Snapshot: <span class="mono">${escapeHtml(fm.snapshot_after || '—')}</span></span>`
    + `<span class="grow"></span>`
    + `<span>${escapeHtml(fm.generated_at || '')} · ${escapeHtml(String(fm.token_cost || 0))} tok</span>`
    + `</div>`
    + `</div>`
  );
}

function section(label, body, editing, dataRole, draftCaption) {
  if (editing) {
    return (
      `<div class="narr-section narr-edit">`
      + `<h4>${label}</h4>`
      + (draftCaption ? `<div class="narr-draft-caption">${draftCaption}</div>` : '')
      + `<textarea data-role="${dataRole}">${escapeHtml(body || '')}</textarea>`
      + `</div>`
    );
  }
  return (
    `<div class="narr-section">`
    + `<h4>${label}</h4>`
    + `<div class="narr-body">${(body || '<em class="empty">—</em>')}</div>`
    + `</div>`
  );
}

function stat(klass, num, label) {
  return (
    `<div class="recap-stat">`
    + `<div class="recap-stat-num ${klass}">${num}</div>`
    + `<div class="recap-stat-label">${label}</div>`
    + `</div>`
  );
}

function computeStats(diff) {
  // Spec §3.16: 5 cells (Handovers excluded — they're already in the diary).
  const tasks_done   = diff.tasks_changed.filter(t => (t.to && t.to.status) === 'done').length
                     + (diff.tasks_added || []).filter(t => t.status === 'done').length;
  const tasks_moved  = diff.tasks_changed.length - tasks_done + (diff.tasks_added || []).length;
  const lessons_fired = (diff.lessons_fired || []).reduce((n, l) => n + (l.fires || 1), 0);
  const issues_opened = (diff.issues_opened || []).length;
  const files_touched = (diff.files_touched || []).length;
  return { tasks_done, tasks_moved, lessons_fired, issues_opened, files_touched };
}

function formatDuration(seconds) {
  if (!seconds || seconds < 60) return `${seconds || 0}s`;
  const m = Math.floor(seconds / 60); const h = Math.floor(m / 60);
  if (h) return `${h}h ${m % 60}m`;
  return `${m}m`;
}

function bindFilterChips(root) {
  const row = root.querySelector('[data-role=filt]');
  if (!row) return;
  for (const chip of row.querySelectorAll('.filt-chip')) {
    chip.addEventListener('click', () => {
      row.querySelectorAll('.filt-chip').forEach(c => c.classList.remove('on'));
      chip.classList.add('on');
      const f = chip.dataset.filt;
      const grid = root.querySelector('.receipts-grid');
      if (!grid) return;
      const cards = grid.querySelectorAll('.rcard');
      cards.forEach(card => {
        const ttl = (card.querySelector('.ttl')?.textContent || '').toLowerCase();
        let show = false;
        if (f === 'all') show = true;
        else if (f === 'tasks')   show = ttl.startsWith('tasks');
        else if (f === 'files')   show = ttl.startsWith('files');
        else if (f === 'lessons') show = ttl.startsWith('lessons');
        else if (f === 'issues')  show = ttl.startsWith('issues');
        card.style.display = show ? '' : 'none';
      });
    });
  }
}

export default mount;
