// Shared right-rail used by Task Detail Variants A and B.
// `mountRightRail(root, { task, related, onNavigate })` renders seven panels:
//   Docs · Links (typed, Plan C) · Lessons in scope · Handovers · Issues
//   · Dependencies + Unblocks · Blockers
// Returns a cleanup function.

import { renderLinkPills, legacyLinksToTyped } from './link-pills.js';

export function mountRightRail(root, { task, related, onNavigate }) {
  root.innerHTML = '';
  root.classList.add('td-rail');

  root.appendChild(panelDocs(task));
  root.appendChild(panelLinks(task, onNavigate));
  root.appendChild(panelLessons(related?.lessons || []));
  root.appendChild(panelHandovers(related?.handovers || []));
  root.appendChild(panelIssues(related?.issues || [], onNavigate));
  root.appendChild(panelDeps(related?.dependencies || [], related?.unblocks || [], onNavigate));
  root.appendChild(panelBlockers(task?.blockers || []));

  return () => { root.innerHTML = ''; };
}


function panelLinks(task, onNavigate) {
  // Plan C: typed links surface from `task.links`. Falls back to legacy fields
  // when the project hasn't been migrated yet.
  const links = task?.links && task.links.length
    ? task.links
    : legacyLinksToTyped(task || {}, 'task');
  if (!links.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Links'), h('div', { class: 'td-empty' }, 'none')]);
  }
  const wrap = h('section', { class: 'td-panel td-panel-links' },
    [panelHeader('Links')]);
  const pillsRoot = h('div', { class: 'td-link-pills-mount' });
  pillsRoot.innerHTML = renderLinkPills({ ...task, links });
  // Make link-pill anchors navigate within the SPA when applicable.
  pillsRoot.querySelectorAll('a.link-pill').forEach((a) => {
    a.addEventListener('click', (e) => {
      const target = a.getAttribute('href')?.slice(1) || '';
      if (target.startsWith('T-') || target.startsWith('ue-') || /^[a-z][\w-]+-\d+$/.test(target)) {
        e.preventDefault();
        onNavigate?.(target);
      }
    });
  });
  wrap.appendChild(pillsRoot);
  return wrap;
}

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'on') for (const [evt, fn] of Object.entries(v)) el.addEventListener(evt, fn);
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

function panelHeader(label) {
  return h('div', { class: 'td-rail-h' }, label);
}

function panelDocs(task) {
  const docs = task?.docs || {};
  const items = Object.entries(docs).map(([type, href]) =>
    h('a', { class: `td-doc td-doc-${type}`, href, target: '_blank', rel: 'noopener' },
      [h('span', { class: 'td-doc-type' }, type), h('span', { class: 'td-doc-path mono' }, href)])
  );
  return h('section', { class: 'td-panel td-panel-docs' },
    [panelHeader('Docs'), ...(items.length ? items : [h('div', { class: 'td-empty' }, 'no docs')])]);
}

function panelLessons(lessons) {
  if (!lessons.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Lessons in scope'), h('div', { class: 'td-empty' }, 'no anchor matches')]);
  }
  return h('section', { class: 'td-panel td-panel-lessons' },
    [panelHeader('Lessons in scope'),
     h('div', { class: 'td-rail-hint' }, 'Surfaced via anchor match'),
     ...lessons.map((l) =>
       h('div', { class: 'td-lesson' },
         [h('span', { class: 'mono td-lesson-id' }, l.id),
          h('span', { class: 'td-lesson-title' }, l.title || ''),
          h('span', { class: 'td-lesson-anchor mono' }, (l.anchors || []).join(' · '))]))]);
}

function panelHandovers(handovers) {
  if (!handovers.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Handovers'), h('div', { class: 'td-empty' }, 'none')]);
  }
  return h('section', { class: 'td-panel td-panel-handovers' },
    [panelHeader('Handovers'),
     ...handovers.map((ho) => {
       const when = formatHandoverTime(ho.created);
       const idLine = `${ho.id} · ${ho.kind || ''}${when ? ` · ${when}` : ''}`;
       const status = ho.status || 'todo';
       return h('div', { class: `td-handover td-handover-${ho.kind || 'mid-task'}` },
         [statusPill(ho.id, status),
          h('div', { class: 'mono td-handover-id' }, idLine),
          h('blockquote', { class: 'serif td-handover-quote' }, `"${ho.quote || ''}"`)]);
     })]);
}

export function statusPill(handoverId, status) {
  return h('button', {
    class: `ho-status-pill ho-status-pill-${status}`,
    'data-handover-id': handoverId,
    'data-status': status,
    title: `Status: ${status} — click to change`,
    on: { click: (ev) => openStatusMenu(ev.currentTarget, handoverId, ev.currentTarget.dataset.status) },
  }, status);
}

export function openStatusMenu(anchor, handoverId, currentStatus) {
  document.querySelectorAll('.ho-status-menu').forEach((m) => m.remove());
  const menu = document.createElement('div');
  menu.className = 'ho-status-menu';
  for (const opt of ['todo', 'in-progress', 'done']) {
    const item = document.createElement('button');
    item.className = `ho-status-menu-item${opt === currentStatus ? ' is-current' : ''}`;
    item.textContent = opt;
    item.addEventListener('click', async () => {
      menu.remove();
      try {
        await fetch(`/api/handover/${encodeURIComponent(handoverId)}/status`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ status: opt, reason: 'viewer-override' }),
        });
        // Patch every pill rendered for this handover (right-rail panel and session-detail rail)
        for (const pill of document.querySelectorAll(`.ho-status-pill[data-handover-id="${CSS.escape(handoverId)}"]`)) {
          pill.classList.remove('ho-status-pill-todo', 'ho-status-pill-in-progress', 'ho-status-pill-done');
          pill.classList.add(`ho-status-pill-${opt}`);
          pill.setAttribute('data-status', opt);
          pill.textContent = opt;
          pill.title = `Status: ${opt} — click to change`;
        }
        window.dispatchEvent(new CustomEvent('viewer:handover-status-changed', {
          detail: { id: handoverId, status: opt },
        }));
      } catch {}
    });
    menu.appendChild(item);
  }
  const rect = anchor.getBoundingClientRect();
  menu.style.position = 'absolute';
  menu.style.top = `${rect.bottom + window.scrollY}px`;
  menu.style.left = `${rect.left + window.scrollX}px`;
  document.body.appendChild(menu);
  setTimeout(() => {
    const off = (e) => {
      if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', off); }
    };
    document.addEventListener('click', off);
  }, 0);
}

function formatHandoverTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function panelIssues(issues, onNavigate) {
  if (!issues.length) {
    return h('section', { class: 'td-panel' },
      [panelHeader('Issues'), h('div', { class: 'td-empty' }, 'none')]);
  }
  return h('section', { class: 'td-panel td-panel-issues' },
    [panelHeader('Issues'),
     ...issues.map((i) =>
       h('div', { class: `td-issue td-issue-${(i.severity || '').toLowerCase()}` },
         [h('span', { class: 'mono td-issue-id' }, i.id),
          h('span', { class: 'td-issue-title' }, i.title || ''),
          h('span', { class: 'td-issue-sev' }, i.severity || '')]))]);
}

function panelDeps(deps, unblocks, onNavigate) {
  return h('section', { class: 'td-panel td-panel-deps' },
    [panelHeader('Dependencies'),
     deps.length
       ? h('ul', { class: 'td-dep-list' },
           deps.map((d) =>
             h('li', { class: `td-dep td-dep-${d.status}`,
                       on: { click: () => onNavigate?.(d.id) } },
               [h('span', { class: 'mono' }, d.id), ' ', d.title])))
       : h('div', { class: 'td-empty' }, 'no dependencies'),
     h('div', { class: 'td-rail-h td-rail-h-sub' }, 'Unblocks'),
     unblocks.length
       ? h('ul', { class: 'td-dep-list' },
           unblocks.map((d) =>
             h('li', { class: `td-dep td-dep-${d.status}`,
                       on: { click: () => onNavigate?.(d.id) } },
               [h('span', { class: 'mono' }, d.id), ' ', d.title])))
       : h('div', { class: 'td-empty' }, 'this task gates nothing')]);
}

function panelBlockers(blockers) {
  return h('section', { class: 'td-panel td-panel-blockers' },
    [panelHeader('Blockers'),
     blockers.length
       ? h('ul', { class: 'td-blocker-list' },
           blockers.map((b) => h('li', { class: 'td-blocker' }, typeof b === 'string' ? b : (b.text || JSON.stringify(b)))))
       : h('div', { class: 'td-empty' }, 'none')]);
}

// ---------------------------------------------------------------------------
// Generic right-rail. Used by Plan 3 (task-detail) and Plan 5a (session-detail).
// Construct once per screen, call open/close as the user picks rows.
// ---------------------------------------------------------------------------

const ATTACH_PARENT_SEL = '.right-rail-host';

export class RightRail {
  /** @param {{width?: number}} opts */
  constructor(opts = {}) {
    this.width = opts.width || 480;
    this.el = null;
    this._cleanup = null;
  }

  /** @param {{render: () => string, onMount?: (root: HTMLElement) => () => void, kind?: string}} args */
  open(args) {
    this.close();
    const host = document.querySelector(ATTACH_PARENT_SEL) || document.body;
    const el = document.createElement('aside');
    el.className = `right-rail right-rail-${args.kind || 'plain'}`;
    el.style.setProperty('--rail-w', this.width + 'px');
    el.innerHTML = args.render();
    host.appendChild(el);
    document.body.classList.add('rail-open');
    this.el = el;
    if (args.onMount) this._cleanup = args.onMount(el) || null;

    // Escape closes the rail.
    this._onKey = (e) => { if (e.key === 'Escape') this.close(); };
    document.addEventListener('keydown', this._onKey);
  }

  close() {
    if (this._onKey) {
      document.removeEventListener('keydown', this._onKey);
      this._onKey = null;
    }
    if (this._cleanup) {
      try { this._cleanup(); } catch {}
      this._cleanup = null;
    }
    if (this.el && this.el.parentNode) this.el.parentNode.removeChild(this.el);
    this.el = null;
    document.body.classList.remove('rail-open');
  }

  isOpen() { return !!this.el; }
}
