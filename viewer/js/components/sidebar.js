// Sidebar renderer. Sections + entries are static here; live counts come from the store.
// On mobile (< 768px), the sidebar becomes a slide-in drawer triggered by a hamburger
// button injected into #topbar. The drawer is dismissed by clicking the backdrop scrim
// or the hamburger button again.

import { isAutoRunning } from '../lib/auto-state.js';

const SECTIONS = [
  { label: 'Frontdoor', items: [
    { key: 'dashboard', icon: '▤', label: 'Dashboard', hash: '#/dashboard' },
    { key: 'kanban',    icon: '▦', label: 'Kanban',    hash: '#/kanban' },
    { key: 'table',     icon: '▭', label: 'Table',     hash: '#/table' },
    { key: 'task',      icon: '◧', label: 'Task',      hash: '#/task' },
    { key: 'epics',     icon: '⬡', label: 'Epics',     hash: '#/epics' },
  ]},
  { label: 'Structural', items: [
    { key: 'auto_mode', icon: '⌬', label: 'Auto Mode', hash: '#/auto', live: true },
  ]},
  { label: 'Temporal', items: [
    { key: 'recap',    icon: '↻', label: 'Recap',    hash: '#/recap' },
    { key: 'sessions', icon: '⌕', label: 'Sessions', hash: '#/sessions' },
  ]},
  { label: 'Knowledge', items: [
    { key: 'lessons',  icon: '✦', label: 'Lessons',  hash: '#/lessons' },
    { key: 'issues',   icon: '⚠', label: 'Issues',   hash: '#/issues' },
    { key: 'bugs',     icon: '⊘', label: 'Bugs',     hash: '#/bugs' },
    { key: 'ideas',    icon: '💡', label: 'Ideas',    hash: '#/ideas' },
    { key: 'archived', icon: '⌫', label: 'Archived', hash: '#/archived' },
  ]},
  { label: 'Structure', items: [
    { key: 'worktrees', icon: '⤿', label: 'Project', hash: '#/worktrees' },
  ]},
  { label: 'System', items: [
    { key: 'settings', icon: '⚙', label: 'Settings', hash: '#/settings' },
  ]},
];

export function mountSidebar(el, { store, prefs }) {
  el.innerHTML = '';
  const shell = document.querySelector('.shell');

  // Logo + collapse toggle
  const logo = document.createElement('div');
  logo.className = 'sidebar-logo';
  logo.innerHTML = `
    <div class="mark"></div>
    <div class="name">Taskmaster</div>
    <div class="ver" id="sidebar-version">v?</div>
    <button class="sidebar-collapse-btn" type="button" aria-label="Collapse sidebar" title="Collapse sidebar">‹</button>
  `;
  el.appendChild(logo);

  const collapseBtn = logo.querySelector('.sidebar-collapse-btn');
  collapseBtn.addEventListener('click', () => {
    const next = !shell.classList.contains('sidebar-collapsed');
    shell.classList.toggle('sidebar-collapsed', next);
    collapseBtn.textContent = next ? '›' : '‹';
    collapseBtn.setAttribute('aria-label', next ? 'Expand sidebar' : 'Collapse sidebar');
    collapseBtn.title = next ? 'Expand sidebar' : 'Collapse sidebar';
    if (prefs) prefs.patch({ ui: { sidebar_collapsed: next } });
  });
  if (shell?.classList.contains('sidebar-collapsed')) {
    collapseBtn.textContent = '›';
    collapseBtn.setAttribute('aria-label', 'Expand sidebar');
    collapseBtn.title = 'Expand sidebar';
  }

  // Sections
  for (const sect of SECTIONS) {
    const h = document.createElement('div');
    h.className = 'sidebar-section-h';
    h.textContent = sect.label;
    el.appendChild(h);

    for (const item of sect.items) {
      const a = document.createElement('a');
      a.className = 'sidebar-link' + (item.live ? ' live' : '');
      a.dataset.key = item.key;
      a.href = item.hash;
      a.title = item.label;
      a.innerHTML = `<span class="ic">${item.icon}</span><span class="lbl">${item.label}</span><span class="badge"></span>`;
      el.appendChild(a);
    }
  }

  // Footer
  const footer = document.createElement('div');
  footer.className = 'sidebar-footer';
  footer.innerHTML = `<span class="pulse"></span><span></span>`;
  footer.hidden = true;
  el.appendChild(footer);

  // Active sync + aria-current
  const onRouteChanged = (e) => {
    const key = e.detail.sidebarKey;
    el.querySelectorAll('.sidebar-link').forEach(a => {
      const isActive = a.dataset.key === key;
      a.classList.toggle('active', isActive);
      if (isActive) {
        a.setAttribute('aria-current', 'page');
      } else {
        a.removeAttribute('aria-current');
      }
    });
  };
  document.addEventListener('route:changed', onRouteChanged);

  // Identity → version
  const unsubIdentity = store.subscribe('identity', (id) => {
    if (id?.version) el.querySelector('#sidebar-version').textContent = 'v' + id.version;
  });

  // Auto-mode live state → footer pulse + sidebar live-dot on auto_mode link
  const unsubAutoState = store.subscribe('autoState', (auto) => {
    const running = isAutoRunning(auto);
    footer.hidden = !running;
    footer.classList.toggle('auto-running', running);
    footer.querySelector('span:last-child').textContent = running ? 'auto-mode active' : '';
    const link = el.querySelector('.sidebar-link[data-key="auto_mode"]');
    if (link) link.classList.toggle('live', running);
  });

  // ── Mobile hamburger drawer (< 768px) ──────────────────────────────────────
  // The sidebar becomes a fixed overlay; a hamburger button is injected into
  // #topbar and a backdrop scrim is appended to the shell. Both are torn down
  // when the screen widens past --bp-md or when the sidebar is unmounted.

  const mql = window.matchMedia('(max-width: 768px)');
  let hamburger = null;
  let backdrop  = null;

  function openDrawer() {
    shell.classList.add('sidebar-drawer-open');
    if (hamburger) {
      hamburger.textContent = '✕';
      hamburger.setAttribute('aria-label', 'Close navigation');
      hamburger.title = 'Close navigation';
    }
  }

  function closeDrawer() {
    shell.classList.remove('sidebar-drawer-open');
    if (hamburger) {
      hamburger.textContent = '☰';
      hamburger.setAttribute('aria-label', 'Open navigation');
      hamburger.title = 'Open navigation';
    }
  }

  function toggleDrawer() {
    if (shell.classList.contains('sidebar-drawer-open')) {
      closeDrawer();
    } else {
      openDrawer();
    }
  }

  function attachMobileChrome() {
    if (hamburger) return;   // already attached

    // Hamburger button — prepended to #topbar
    const topbarEl = document.getElementById('topbar');
    if (topbarEl) {
      hamburger = document.createElement('button');
      hamburger.type = 'button';
      hamburger.className = 'topbar-hamburger';
      hamburger.textContent = '☰';
      hamburger.setAttribute('aria-label', 'Open navigation');
      hamburger.title = 'Open navigation';
      hamburger.addEventListener('click', toggleDrawer);
      topbarEl.prepend(hamburger);
    }

    // Backdrop scrim — appended to shell
    backdrop = document.createElement('div');
    backdrop.className = 'sidebar-backdrop';
    backdrop.addEventListener('click', closeDrawer);
    shell.appendChild(backdrop);
  }

  function detachMobileChrome() {
    closeDrawer();
    if (hamburger) {
      hamburger.removeEventListener('click', toggleDrawer);
      hamburger.remove();
      hamburger = null;
    }
    if (backdrop) {
      backdrop.removeEventListener('click', closeDrawer);
      backdrop.remove();
      backdrop = null;
    }
  }

  function onMqlChange(e) {
    if (e.matches) {
      attachMobileChrome();
    } else {
      detachMobileChrome();
    }
  }

  mql.addEventListener('change', onMqlChange);
  if (mql.matches) attachMobileChrome();

  // Return teardown function that removes all listeners and subscriptions.
  return () => {
    document.removeEventListener('route:changed', onRouteChanged);
    if (typeof unsubIdentity === 'function') unsubIdentity();
    if (typeof unsubAutoState === 'function') unsubAutoState();
    mql.removeEventListener('change', onMqlChange);
    detachMobileChrome();
  };
}
