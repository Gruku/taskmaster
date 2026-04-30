import { listSessions, getRecap, putRecap, getSnapshotDiff } from '../api.js';
import { renderReceiptsGrid } from '../components/recap-receipts-grid.js';

export const meta = { title: 'Recap', icon: '⚯', sidebarKey: 'recap' };

const escapeHtml = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function mount(root, { params }) {
  root.innerHTML = `<div class="recap-page" data-role="root"><div class="stub">Loading recap…</div></div>`;
  const sessions = await listSessions();
  const recapSessions = sessions.filter(s => s.recap_id);

  const targetId = (params && params.id) || (recapSessions[0] && recapSessions[0].id);
  if (!targetId) {
    root.querySelector('[data-role=root]').innerHTML =
      `<div class="stub">No recaps yet. Close a session to generate one.</div>`;
    return () => {};
  }

  const idx = recapSessions.findIndex(s => s.id === targetId);
  const cur = recapSessions[idx];
  if (!cur) {
    root.querySelector('[data-role=root]').innerHTML =
      `<div class="stub">No recap found for ${escapeHtml(targetId)}.</div>`;
    return () => {};
  }
  const prev = recapSessions[idx + 1] || null;
  const next = recapSessions[idx - 1] || null;

  const recap = await getRecap(cur.id);
  let diff = { tasks_added:[], tasks_changed:[], tasks_removed:[],
               files_touched:[], lessons_fired:[], issues_opened:[], issues_transitioned:[] };
  if (recap && recap.frontmatter && recap.frontmatter.snapshot_before && recap.frontmatter.snapshot_after) {
    try {
      diff = await getSnapshotDiff(recap.frontmatter.snapshot_before, recap.frontmatter.snapshot_after);
    } catch (e) {
      console.warn('snapshot diff failed', e);
    }
  }

  renderRecapPage(root, { cur, prev, next, recap, diff, editing: false });
  bindNav(root, prev, next);
  bindActions(root, cur, recap, diff);
  bindFilterChips(root);
  return () => {};
}

function bindNav(root, prev, next) {
  const p = root.querySelector('[data-role=prev]');
  const n = root.querySelector('[data-role=next]');
  p && p.addEventListener('click', () => prev && (window.location.hash = `#/recap/${prev.id}`));
  n && n.addEventListener('click', () => next && (window.location.hash = `#/recap/${next.id}`));
}

function bindActions(root, cur, recap, diff) {
  const editBtn = root.querySelector('[data-role=edit]');
  if (editBtn) editBtn.addEventListener('click', () => {
    renderRecapPage(root, { cur,
      prev: null, next: null,
      recap, diff, editing: true });
    bindEditing(root, cur, recap, diff);
    bindFilterChips(root);
  });
  const copyBtn = root.querySelector('[data-role=copy-resume]');
  if (copyBtn && recap) copyBtn.addEventListener('click', () => {
    const text = `${recap.what_happened || ''}\n\n${recap.whats_next || ''}`;
    navigator.clipboard?.writeText(text);
    copyBtn.textContent = '✓ copied';
    setTimeout(() => { copyBtn.textContent = '⧉ copy resume'; }, 1500);
  });
  const openBtn = root.querySelector('[data-role=open-sessions]');
  if (openBtn) openBtn.addEventListener('click', () =>
    window.location.hash = `#/sessions/${cur.id}`);
}

function bindEditing(root, cur, recap, diff) {
  const save = root.querySelector('[data-role=save]');
  const cancel = root.querySelector('[data-role=cancel]');
  const regen = root.querySelector('[data-role=regenerate]');

  cancel && cancel.addEventListener('click', () => {
    renderRecapPage(root, { cur, prev: null, next: null, recap, diff, editing: false });
    bindNav(root, null, null);
    bindActions(root, cur, recap, diff);
    bindFilterChips(root);
  });

  save && save.addEventListener('click', async () => {
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
    const fresh = await getRecap(cur.id);
    renderRecapPage(root, { cur, prev: null, next: null, recap: fresh, diff, editing: false });
    bindActions(root, cur, fresh, diff);
    bindFilterChips(root);
  });

  regen && regen.addEventListener('click', () => {
    // Restore the on-disk draft into the edit fields. Generation itself happens server-side
    // (Plan 5b owns the regenerate hook); for now we re-fetch the saved recap to drop
    // unsaved edits and signal "draft restored".
    getRecap(cur.id).then(fresh => {
      root.querySelector('[data-role=ed-title]').value = (fresh && fresh.title) || '';
      root.querySelector('[data-role=ed-what-happened]').value = (fresh && fresh.what_happened) || '';
      root.querySelector('[data-role=ed-what-landed]').value = (fresh && fresh.what_landed) || '';
      root.querySelector('[data-role=ed-whats-next]').value = (fresh && fresh.whats_next) || '';
    });
  });
}

function renderRecapPage(root, { cur, prev, next, recap, diff, editing }) {
  const fm = (recap && recap.frontmatter) || {};
  const stats = computeStats(diff);
  const draftCaption = fm.generated_at
    ? `draft generated ${escapeHtml(String(fm.generated_at).slice(0, 16))} by ${escapeHtml(fm.generator || '?')}`
    : '';

  root.innerHTML = (
    `<div class="recap-page">`
    + `<div class="recap-topbar">`
    + (prev !== null ? `<button class="recap-nav-arrow" data-role="prev" ${prev?'':'disabled'}>‹</button>` : '')
    + `<div class="recap-picker">`
    + `<span class="pid mono">${escapeHtml(cur.id)}</span>`
    + `<span class="ptitle">${escapeHtml((recap && recap.title) || '—')}</span>`
    + `<span class="pdate">${escapeHtml(String(cur.start).slice(0, 10))}</span>`
    + `<span class="chev">▾</span>`
    + `</div>`
    + (next !== null ? `<button class="recap-nav-arrow" data-role="next" ${next?'':'disabled'}>›</button>` : '')
    + `<div class="spacer"></div>`
    + (editing
        ? `<button class="recap-action" data-role="cancel">Cancel</button>`
          + `<button class="recap-action primary" data-role="save">Save</button>`
          + `<button class="recap-action" data-role="regenerate">↺ regenerate</button>`
        : `<button class="recap-action" data-role="copy-resume">⧉ copy resume</button>`
          + `<button class="recap-action" data-role="open-sessions">Open in Sessions</button>`
          + `<button class="recap-action primary" data-role="edit">✎ edit recap</button>`)
    + `</div>`
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
