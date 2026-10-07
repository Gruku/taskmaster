// Sidebar renderer. Sections + entries are static here.
// On mobile (< 768px), the sidebar becomes a slide-in drawer triggered by a hamburger
// button injected into the topbar's first row. The drawer is dismissed by Escape, by its
// own close button, by clicking the backdrop scrim, or by choosing a nav link.

import { icon } from './icon.js';

// `icon` is a name from components/icon.js.
const SECTIONS = [
  { label: 'Frontdoor', items: [
    { key: 'dashboard', icon: 'grid',   label: 'Dashboard', hash: '#/dashboard' },
    { key: 'kanban',    icon: 'kanban', label: 'Kanban',    hash: '#/kanban' },
    { key: 'table',     icon: 'table',  label: 'Table',     hash: '#/table' },
    { key: 'epics',     icon: 'folder', label: 'Epics',     hash: '#/epics' },
  ]},
  { label: 'Temporal', items: [
    { key: 'sessions', icon: 'document', label: 'Sessions', hash: '#/sessions' },
  ]},
  { label: 'Knowledge', items: [
    { key: 'issues',   icon: 'alert',   label: 'Issues',   hash: '#/issues' },
    { key: 'bugs',     icon: 'bug',     label: 'Bugs',     hash: '#/bugs' },
    { key: 'ideas',    icon: 'idea',    label: 'Ideas',    hash: '#/ideas' },
    { key: 'archived', icon: 'archive', label: 'Archived', hash: '#/archived' },
  ]},
  { label: 'System', items: [
    { key: 'settings', icon: 'sliders', label: 'Settings', hash: '#/settings' },
  ]},
];

function span(className, child) {
  const el = document.createElement('span');
  el.className = className;
  el.append(child);
  return el;
}

export function mountSidebar(el, { store, prefs }) {
  el.innerHTML = '';
  const shell = document.querySelector('.shell');

  // Logo + collapse toggle
  const logo = document.createElement('div');
  logo.className = 'sidebar-logo';
  logo.innerHTML = `
    <div class="brand">
      <div class="name">TASKMASTER</div>
      <div class="ver" id="sidebar-version">v?</div>
    </div>
    <button class="sidebar-collapse-btn" type="button"></button>
    <button class="sidebar-close-btn" type="button" aria-label="Close navigation" title="Close navigation"></button>
  `;
  el.appendChild(logo);

  // Shown only in the mobile drawer, where it takes the collapse button's place.
  const closeBtn = logo.querySelector('.sidebar-close-btn');
  closeBtn.appendChild(icon('dismiss'));
  closeBtn.addEventListener('click', () => closeDrawer());

  // The chevron is one glyph; CSS turns it to point the way the sidebar will move.
  const collapseBtn = logo.querySelector('.sidebar-collapse-btn');
  collapseBtn.appendChild(icon('chevron', { size: 16 }));
  const syncCollapseBtn = (collapsed) => {
    const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    collapseBtn.classList.toggle('is-collapsed', collapsed);
    collapseBtn.setAttribute('aria-label', label);
    collapseBtn.title = label;
  };
  collapseBtn.addEventListener('click', () => {
    const next = !shell.classList.contains('sidebar-collapsed');
    shell.classList.toggle('sidebar-collapsed', next);
    syncCollapseBtn(next);
    if (prefs) prefs.patch({ ui: { sidebar_collapsed: next } });
  });
  syncCollapseBtn(!!shell?.classList.contains('sidebar-collapsed'));

  // Nav scroll body — sections sit inside here so the logo above is never
  // inside the overflow region and always has the full sidebar width.
  const nav = document.createElement('nav');
  nav.className = 'sidebar-nav';
  nav.setAttribute('aria-label', 'Primary');
  el.appendChild(nav);

  // Sections
  for (const sect of SECTIONS) {
    const h = document.createElement('div');
    h.className = 'sidebar-section-h';
    h.textContent = sect.label;
    nav.appendChild(h);

    for (const item of sect.items) {
      const a = document.createElement('a');
      a.className = 'sidebar-link';
      a.dataset.key = item.key;
      a.href = item.hash;
      a.title = item.label;
      a.append(span('ic', icon(item.icon)), span('lbl', item.label));
      // Closing on the tap itself (not on route:changed) also covers the current page's
      // link and a screen that fails to mount, and leaves alone a drawer opened mid-mount.
      a.addEventListener('click', () => closeDrawer());
      nav.appendChild(a);
    }
  }

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
  const applyIdentity = (id) => {
    if (id?.version) el.querySelector('#sidebar-version').textContent = 'v' + id.version;
  };
  const unsubIdentity = store.subscribe('identity', applyIdentity);
  applyIdentity(store.getIdentity());   // replay — identity is set before sidebar mounts

  // ── Mobile hamburger drawer (< 768px) ──────────────────────────────────────
  // The sidebar becomes a fixed overlay; a hamburger button is injected into
  // the topbar's first row and a backdrop scrim is appended to the shell. Both
  // are torn down when the screen widens past --bp-md or when the sidebar is
  // unmounted. Exactly one side is inert at a time: the off-screen sidebar while
  // the drawer is closed, the page behind the scrim while it is open — so Tab
  // never lands on something the user cannot see or reach.

  // A browser that refuses media queries gets the desktop sidebar rather than a failed boot.
  let mql = null;
  try { mql = window.matchMedia('(max-width: 768px)'); } catch { /* stay on the desktop layout */ }
  const mainEl = shell?.querySelector('.main');
  let hamburger = null;
  let backdrop  = null;

  function syncHamburger(open) {
    if (!hamburger) return;
    const label = open ? 'Close navigation' : 'Open navigation';
    hamburger.replaceChildren(icon(open ? 'dismiss' : 'menu'));
    hamburger.setAttribute('aria-expanded', String(open));
    hamburger.setAttribute('aria-label', label);
    hamburger.title = label;
  }

  // Tab wraps at the drawer's ends: past its last stop the browser would otherwise leave the page for its own chrome.
  function onDrawerKeydown(e) {
    if (e.key === 'Escape') { closeDrawer(); return; }
    if (e.key !== 'Tab') return;
    const stops = [...el.querySelectorAll('a[href], button:not([disabled])')].filter((n) => n.checkVisibility());
    if (!stops.length) return;
    const first = stops[0], last = stops[stops.length - 1];
    const at = document.activeElement;
    const to = !el.contains(at) ? (e.shiftKey ? last : first)
      : !e.shiftKey && at === last ? first
      : e.shiftKey && at === first ? last
      : null;
    if (!to) return;
    e.preventDefault();
    to.focus();
  }

  function openDrawer() {
    shell.classList.add('sidebar-drawer-open');
    el.inert = false;
    if (mainEl) mainEl.inert = true;
    syncHamburger(true);
    document.addEventListener('keydown', onDrawerKeydown);
    el.querySelector('.sidebar-link')?.focus();
  }

  // Safe to call when the drawer is not open (link clicks on desktop, teardown): focus
  // goes back to the hamburger, the drawer's opener, only when a drawer really closed.
  // `widening`: the drawer becomes the desktop sidebar, so it stays reachable and focus stays where it is
  // (the hamburger is about to go).
  function closeDrawer({ widening = false } = {}) {
    const wasOpen = shell.classList.contains('sidebar-drawer-open');
    shell.classList.remove('sidebar-drawer-open');
    document.removeEventListener('keydown', onDrawerKeydown);
    if (mainEl) mainEl.inert = false;
    syncHamburger(false);
    if (widening) return;
    if (hamburger) el.inert = true;
    if (wasOpen) hamburger?.focus();
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

    // Hamburger button — prepended to the topbar's first row
    const row1 = document.querySelector('#topbar .topbar-row1');
    if (row1) {
      hamburger = document.createElement('button');
      hamburger.type = 'button';
      hamburger.className = 'topbar-hamburger';
      hamburger.setAttribute('aria-controls', el.id);
      hamburger.addEventListener('click', toggleDrawer);
      row1.prepend(hamburger);
      syncHamburger(false);
      el.inert = true;
    }

    // Backdrop scrim — appended to shell
    backdrop = document.createElement('div');
    backdrop.className = 'sidebar-backdrop';
    backdrop.addEventListener('click', closeDrawer);
    shell.appendChild(backdrop);
  }

  function detachMobileChrome() {
    closeDrawer({ widening: true });
    if (hamburger) {
      hamburger.removeEventListener('click', toggleDrawer);
      hamburger.remove();
      hamburger = null;
    }
    el.inert = false;
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

  mql?.addEventListener('change', onMqlChange);
  if (mql?.matches) attachMobileChrome();

  // Return teardown function that removes all listeners and subscriptions.
  return () => {
    document.removeEventListener('route:changed', onRouteChanged);
    if (typeof unsubIdentity === 'function') unsubIdentity();
    mql?.removeEventListener('change', onMqlChange);
    detachMobileChrome();
  };
}
