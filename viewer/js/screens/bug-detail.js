import * as api from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { formatRelative, formatAbsolute } from '../lib/time.js';

export const meta = { title: 'Bug', icon: '⊘', sidebarKey: 'bugs' };

const STATUS_LABEL = {
  open:    'Open',
  shelved: 'Shelved',
  fixed:   'Fixed',
  adopted: 'Adopted into task',
  archived:'Archived',
};

function _fmtDate(iso) {
  return iso ? formatAbsolute(iso, { time: false, year: true }) : '—';
}

function _fmtRel(iso) {
  return iso ? formatRelative(iso, { now: Date.now() }) : '—';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function mount(root, { params, subpath, store, prefs }) {
  const id = subpath?.[0] || params?.id || null;
  root.innerHTML = '';
  root.classList.add('bug-detail');

  if (!id) {
    const empty = document.createElement('div');
    empty.className = 'id-empty';
    empty.innerHTML = 'No bug selected. <a href="#/bugs">Back to Bugs</a>.';
    root.appendChild(empty);
    claimTopbar();
    return () => { root.classList.remove('bug-detail'); };
  }

  let bug;
  try {
    bug = await api.getBug(id);
  } catch (e) {
    const empty = document.createElement('div');
    empty.className = 'id-empty';
    empty.textContent = `Could not load bug ${id}: ${e.message}`;
    root.appendChild(empty);
    claimTopbar();
    return () => { root.classList.remove('bug-detail'); };
  }

  if (!bug) {
    const empty = document.createElement('div');
    empty.className = 'id-empty';
    empty.textContent = `Bug ${id} not found. `;
    const back = document.createElement('a');
    back.href = '#/bugs';
    back.textContent = 'Back to Bugs';
    empty.appendChild(back);
    root.appendChild(empty);
    claimTopbar();
    return () => { root.classList.remove('bug-detail'); };
  }

  claimTopbar();
  render();

  function render() {
    root.replaceChildren();

    const status = bug.status || 'open';

    // ── Crumb row ‹ Bugs / Status
    const crumb = document.createElement('div');
    crumb.className = 'id-crumb';
    const back = document.createElement('a');
    back.className = 'id-back';
    back.href = '#/bugs';
    back.textContent = '‹ Bugs';
    const sep = document.createElement('span');
    sep.className = 'id-crumb-sep';
    sep.textContent = '/';
    const statusCrumb = document.createElement('span');
    statusCrumb.className = 'id-crumb-status';
    statusCrumb.textContent = STATUS_LABEL[status] || status;
    crumb.append(back, sep, statusCrumb);
    root.appendChild(crumb);

    // ── Header: id · severity · status · title
    const head = document.createElement('header');
    head.className = 'id-head';

    const metaRow = document.createElement('div');
    metaRow.className = 'id-meta';

    const idEl = document.createElement('span');
    idEl.className = 'id-id';
    idEl.textContent = bug.id;

    const sevEl = document.createElement('span');
    sevEl.className = 'bug-detail__sev';
    sevEl.dataset.sev = bug.severity || '';
    sevEl.textContent = bug.severity || '';

    const statusPill = document.createElement('span');
    statusPill.className = `id-status bug-detail__status--${status}`;
    statusPill.textContent = STATUS_LABEL[status] || status;

    const createdEl = document.createElement('span');
    createdEl.className = 'id-created';
    createdEl.textContent = `discovered ${_fmtDate(bug.discovered)}`;

    metaRow.append(idEl, sevEl, statusPill, createdEl);
    head.appendChild(metaRow);

    const titleEl = document.createElement('h1');
    titleEl.className = 'id-title';
    titleEl.textContent = bug.title || '(untitled)';
    head.appendChild(titleEl);

    root.appendChild(head);

    // ── Body grid: main + side
    const grid = document.createElement('div');
    grid.className = 'id-grid';

    // ---- main column
    const main = document.createElement('div');
    main.className = 'id-main';

    if (bug.description) {
      const descSec = document.createElement('section');
      descSec.className = 'bug-detail__section';
      const h = document.createElement('h2'); h.className = 'id-h'; h.textContent = 'Description';
      const body = document.createElement('p');
      body.className = 'id-body';
      body.textContent = bug.description;
      descSec.append(h, body);
      main.appendChild(descSec);
    }

    if (bug.body) {
      const bodySec = document.createElement('section');
      bodySec.className = 'bug-detail__section';
      const h = document.createElement('h2'); h.className = 'id-h'; h.textContent = 'Details';
      const body = document.createElement('p');
      body.className = 'id-body';
      body.textContent = bug.body;
      bodySec.append(h, body);
      main.appendChild(bodySec);
    }

    // Disposition action bar (open or shelved bugs only)
    if (status === 'open' || status === 'shelved') {
      const actions = document.createElement('div');
      actions.className = 'bug-detail__actions';

      const btnFix = mkBtn('Mark fixed', async () => {
        const commit = prompt('Commit SHA or reference for the fix (optional):');
        if (commit === null) return; // user cancelled
        try {
          await api.updateBug(bug.id, { status: 'fixed', fix_commit: commit || undefined });
          location.hash = '#/bugs';
        } catch (e) {
          alert(`Failed to mark fixed: ${e.message}`);
        }
      });

      const btnShelve = mkBtn('Shelve', async () => {
        if (!confirm(`Shelve bug ${bug.id}? You can reopen it later.`)) return;
        try {
          await api.updateBug(bug.id, { status: 'shelved' });
          location.hash = '#/bugs';
        } catch (e) {
          alert(`Failed to shelve: ${e.message}`);
        }
      });

      const btnAdopt = mkBtn('Spawn follow-up task', async () => {
        const taskId = prompt('Follow-up task ID to adopt this bug into (e.g. T-042):');
        if (!taskId || !taskId.trim()) return;
        try {
          await api.updateBug(bug.id, { status: 'adopted', adopted_into: taskId.trim() });
          location.hash = '#/bugs';
        } catch (e) {
          alert(`Failed to adopt into task: ${e.message}`);
        }
      });

      const btnPromote = mkBtn('Promote to Issue', async () => {
        const evidence = prompt(
          'Evidence — cite recurring/systemic/outstanding criterion:\n' +
          '(Must confirm this meets the bar for an Issue)'
        );
        if (!evidence || !evidence.trim()) return;
        const title = prompt('Issue title:', bug.title) ?? bug.title;
        const severity = prompt('Severity (P0/P1/P2/P3):', 'P1') ?? 'P1';
        try {
          const result = await api.promoteBugs({
            bug_ids: [bug.id],
            title,
            severity,
            evidence_text: evidence.trim(),
          });
          const issueId = result?.issue_id || result?.id || '';
          const msg = issueId
            ? `Promoted to ${issueId}. Navigate to Issues to view it.`
            : 'Promoted to Issue.';
          alert(msg);
          location.hash = '#/bugs';
        } catch (e) {
          alert(`Failed to promote: ${e.message}`);
        }
      });

      // Only show Shelve if currently open (shelved bugs can be fixed/adopted/promoted but not re-shelved)
      const buttons = status === 'open'
        ? [btnFix, btnShelve, btnAdopt, btnPromote]
        : [btnFix, btnAdopt, btnPromote];
      buttons.forEach(b => actions.appendChild(b));
      main.appendChild(actions);
    }

    grid.appendChild(main);

    // ---- side column
    const side = document.createElement('aside');
    side.className = 'id-side';

    // Signals block
    const signals = document.createElement('section');
    signals.className = 'id-side-block';
    const sh = document.createElement('h2'); sh.className = 'id-h'; sh.textContent = 'Details';
    signals.appendChild(sh);

    const dl = document.createElement('dl');
    dl.className = 'id-dl';

    const rows = [
      ['Status', STATUS_LABEL[status] || status],
      ['Severity', bug.severity || '—'],
      ['Discovered', _fmtRel(bug.discovered)],
    ];
    if (bug.found_in) rows.push(['Found in', bug.found_in]);
    if (bug.discovered_by) rows.push(['Reported by', bug.discovered_by]);
    if (bug.fix_commit) rows.push(['Fix commit', bug.fix_commit]);
    if (bug.adopted_into) rows.push(['Adopted into', bug.adopted_into]);
    if (bug.promoted_to) rows.push(['Promoted to', bug.promoted_to]);
    if (bug.components && bug.components.length) {
      rows.push(['Components', bug.components.join(', ')]);
    }

    for (const [k, v] of rows) {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.append(dt, dd);
    }
    signals.appendChild(dl);
    side.appendChild(signals);

    grid.appendChild(side);
    root.appendChild(grid);
  }

  return () => {
    root.classList.remove('bug-detail');
  };
}

function mkBtn(label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'cmp-btn bug-detail__action-btn';
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}
