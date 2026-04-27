// Sidebar renderer. Sections + entries are static here; live counts come from the store.

const SECTIONS = [
  { label: 'Frontdoor', items: [
    { key: 'dashboard', icon: '▤', label: 'Dashboard', hash: '#/dashboard' },
    { key: 'kanban',    icon: '▦', label: 'Kanban',    hash: '#/kanban' },
  ]},
  { label: 'Structural', items: [
    { key: 'auto_mode', icon: '⌬', label: 'Auto Mode', hash: '#/auto', live: true },
  ]},
  { label: 'Temporal', items: [
    { key: 'recap',    icon: '↻', label: 'Recap',    hash: '#/recap' },
    { key: 'sessions', icon: '⌕', label: 'Sessions', hash: '#/sessions' },
  ]},
  { label: 'Knowledge', items: [
    { key: 'lessons', icon: '✦', label: 'Lessons', hash: '#/lessons' },
    { key: 'issues',  icon: '⚠', label: 'Issues',  hash: '#/issues' },
  ]},
];

export function mountSidebar(el, { store }) {
  el.innerHTML = '';

  // Logo
  const logo = document.createElement('div');
  logo.className = 'sidebar-logo';
  logo.innerHTML = `
    <div class="mark"></div>
    <div class="name">Taskmaster</div>
    <div class="ver" id="sidebar-version">v?</div>
  `;
  el.appendChild(logo);

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
      a.innerHTML = `<span class="ic">${item.icon}</span><span>${item.label}</span><span class="badge"></span>`;
      el.appendChild(a);
    }
  }

  // Footer
  const footer = document.createElement('div');
  footer.className = 'sidebar-footer';
  footer.innerHTML = `<span class="pulse"></span><span>idle</span>`;
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
    const running = !!(auto && auto.mode);
    footer.classList.toggle('auto-running', running);
    footer.querySelector('span:last-child').textContent = running ? 'auto-mode active' : 'idle';
    const link = el.querySelector('.sidebar-link[data-key="auto_mode"]');
    if (link) link.classList.toggle('live', running);
  });

  // Return teardown function that removes all listeners and subscriptions.
  return () => {
    document.removeEventListener('route:changed', onRouteChanged);
    if (typeof unsubIdentity === 'function') unsubIdentity();
    if (typeof unsubAutoState === 'function') unsubAutoState();
  };
}
