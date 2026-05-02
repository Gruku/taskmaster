import * as api from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { severityGlyph, injectSeverityDefs } from '../components/severity-glyph.js';
import { severityLabel } from '../util/severity-label.js';
import { pluralize } from '../util/pluralize.js';
import { agingBar } from '../components/aging-bar.js';
import { formatRelative, formatAbsolute } from '../lib/time.js';

export const meta = { title: 'Issue', icon: '!', sidebarKey: 'issues' };

const STATUS_LABEL = {
  open: 'Open',
  investigating: 'Investigating',
  fixed: 'Fixed',
  wontfix: 'Won’t fix',
};

function _fmtDate(iso) {
  return iso ? formatAbsolute(iso, { time: false, year: true }) : '—';
}

function _fmtRel(iso, now) {
  return iso ? formatRelative(iso, { now: now ? (now instanceof Date ? now.getTime() : now) : Date.now() }) : '—';
}

export async function mount(root, { params, store, prefs, subpath }) {
  const id = subpath?.[0] || params?.id || null;
  root.innerHTML = '';
  root.classList.add('issue-detail');
  injectSeverityDefs();

  if (!id) {
    root.innerHTML = `<div class="id-empty">No issue selected. <a href="#/issues">Back to Issues</a>.</div>`;
    claimTopbar();
    return () => { root.classList.remove('issue-detail'); };
  }

  if (prefs?.patch) prefs.patch({ ui: { last_issue_id: id } });

  let issues = store?.getIssues?.() || [];
  if (issues.length === 0) {
    try {
      const data = await api.getIssues({ includeResolved: true });
      issues = data.issues || [];
      store?.setIssues?.(issues);
    } catch (e) {
      const empty = document.createElement('div');
      empty.className = 'id-empty';
      empty.textContent = `Could not load issues: ${e.message}`;
      root.replaceChildren(empty);
      claimTopbar();
      return () => { root.classList.remove('issue-detail'); };
    }
  }
  const issue = issues.find(i => i.id === id);
  if (!issue) {
    const empty = document.createElement('div');
    empty.className = 'id-empty';
    empty.textContent = `Issue ${id} not found. `;
    const back = document.createElement('a');
    back.href = '#/issues';
    back.textContent = 'Back to Issues';
    empty.append(back, '.');
    root.replaceChildren(empty);
    claimTopbar();
    return () => { root.classList.remove('issue-detail'); };
  }

  claimTopbar();

  render();

  function render() {
    root.replaceChildren();

    const label = issue.severity_label || severityLabel(issue.severity);
    const status = issue.status || 'open';

    // Crumb row: ‹ Issues / Status
    const crumb = document.createElement('div');
    crumb.className = 'id-crumb';
    const back = document.createElement('a');
    back.className = 'id-back';
    back.href = '#/issues';
    back.textContent = '‹ Issues';
    const sep = document.createElement('span');
    sep.className = 'id-crumb-sep';
    sep.textContent = '/';
    const statusCrumb = document.createElement('span');
    statusCrumb.className = 'id-crumb-status';
    statusCrumb.textContent = STATUS_LABEL[status] || status;
    crumb.append(back, sep, statusCrumb);
    root.appendChild(crumb);

    // Header: glyph · sev · id · status · created
    const head = document.createElement('header');
    head.className = 'id-head';
    const meta = document.createElement('div');
    meta.className = 'id-meta';
    const glyph = severityGlyph(label);
    glyph.classList.add('id-glyph');
    const sev = document.createElement('span');
    sev.className = 'id-sev';
    sev.dataset.sev = label;
    sev.textContent = label;
    const idEl = document.createElement('span');
    idEl.className = 'id-id';
    idEl.textContent = issue.id;
    const statusPill = document.createElement('span');
    statusPill.className = `id-status id-status--${status}`;
    statusPill.textContent = STATUS_LABEL[status] || status;
    const created = document.createElement('span');
    created.className = 'id-created';
    created.textContent = `since ${_fmtDate(issue.created)}`;
    meta.append(glyph, sev, idEl, statusPill, created);
    head.appendChild(meta);

    const title = document.createElement('h1');
    title.className = 'id-title';
    title.textContent = issue.title || '(untitled)';
    head.appendChild(title);

    if (issue.location && issue.location.length) {
      const loc = document.createElement('div');
      loc.className = 'id-location';
      loc.textContent = `at ${issue.location.join(' · ')}`;
      head.appendChild(loc);
    }
    root.appendChild(head);

    // Body grid
    const grid = document.createElement('div');
    grid.className = 'id-grid';

    // ---- main column
    const main = document.createElement('div');
    main.className = 'id-main';

    if (issue.symptom) {
      const symSec = document.createElement('section');
      symSec.className = 'id-symptom';
      const h = document.createElement('h2'); h.className = 'id-h'; h.textContent = 'Symptom';
      const body = document.createElement('p');
      body.className = 'id-body id-body--italic';
      body.textContent = issue.symptom;
      symSec.append(h, body);
      main.appendChild(symSec);
    }

    if (issue.repro && issue.repro.length) {
      const reproSec = document.createElement('section');
      reproSec.className = 'id-repro';
      const h = document.createElement('h2');
      h.className = 'id-h';
      h.textContent = `Reproduction · ${issue.repro.length} ${pluralize(issue.repro.length, 'step', 'steps')}`;
      reproSec.appendChild(h);
      const ol = document.createElement('ol');
      ol.className = 'id-repro-list';
      for (const step of issue.repro) {
        const li = document.createElement('li');
        li.textContent = step;
        ol.appendChild(li);
      }
      reproSec.appendChild(ol);
      main.appendChild(reproSec);
    }

    if (issue.impact) {
      const impSec = document.createElement('section');
      impSec.className = 'id-impact';
      const h = document.createElement('h2'); h.className = 'id-h'; h.textContent = 'Impact';
      const body = document.createElement('p');
      body.className = 'id-body';
      body.innerHTML = String(issue.impact).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
        .replace(/`([^`]+)`/g, '<code>$1</code>');
      impSec.append(h, body);
      main.appendChild(impSec);
    }

    if (issue.summary) {
      const sumSec = document.createElement('section');
      sumSec.className = 'id-summary';
      const h = document.createElement('h2'); h.className = 'id-h'; h.textContent = 'Notes';
      const body = document.createElement('p');
      body.className = 'id-body';
      body.textContent = issue.summary;
      sumSec.append(h, body);
      main.appendChild(sumSec);
    }

    grid.appendChild(main);

    // ---- side column
    const side = document.createElement('aside');
    side.className = 'id-side';

    const signals = document.createElement('section');
    signals.className = 'id-side-block';
    const sh = document.createElement('h2'); sh.className = 'id-h'; sh.textContent = 'Signals';
    signals.appendChild(sh);

    // aging bar (if cfg available)
    const agingCfg = store?.getPrefs?.()?.issues?.aging || {};
    if (agingCfg && Object.keys(agingCfg).length) {
      const ab = agingBar({ ...issue, severity_label: label }, agingCfg);
      ab.classList.add('id-aging');
      signals.appendChild(ab);
    }

    const dl = document.createElement('dl');
    dl.className = 'id-dl';
    const rows = [
      ['Severity', label],
      ['Status', STATUS_LABEL[status] || status],
      ['Created', _fmtRel(issue.created)],
    ];
    if (issue.resolved) rows.push(['Resolved', _fmtRel(issue.resolved)]);
    for (const [k, v] of rows) {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.append(dt, dd);
    }
    signals.appendChild(dl);
    side.appendChild(signals);

    const tasks = issue.related_tasks || [];
    const relatedIssues = issue.related_issues || [];
    if (tasks.length || relatedIssues.length) {
      const rel = document.createElement('section');
      rel.className = 'id-side-block';
      const rh = document.createElement('h2'); rh.className = 'id-h'; rh.textContent = 'Related';
      rel.appendChild(rh);
      const list = document.createElement('div');
      list.className = 'id-rel-list';
      for (const t of tasks) {
        const tid = typeof t === 'string' ? t : (t.id || '');
        if (!tid) continue;
        const a = document.createElement('a');
        a.className = 'id-rel-pill';
        a.href = `#/task/${encodeURIComponent(tid)}`;
        a.textContent = tid;
        list.appendChild(a);
      }
      for (const i of relatedIssues) {
        const iid = typeof i === 'string' ? i : (i.id || '');
        if (!iid) continue;
        const a = document.createElement('a');
        a.className = 'id-rel-pill id-rel-pill--issue';
        a.href = `#/issue/${encodeURIComponent(iid)}`;
        a.textContent = iid;
        list.appendChild(a);
      }
      rel.appendChild(list);
      side.appendChild(rel);
    }

    grid.appendChild(side);
    root.appendChild(grid);
  }

  return () => {
    root.classList.remove('issue-detail');
  };
}
