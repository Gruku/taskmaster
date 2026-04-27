export const meta = { title: 'Sessions / Handovers', icon: '⌕', sidebarKey: 'sessions' };

export async function mount(root, { subpath }) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `
    Sessions placeholder.
    <div class="stub-meta">${subpath[0] ? 'session=' + subpath[0] + ' — ' : ''}Plan 5 fills in the Hybrid C diary with parallel-block clusters, nested handovers/recaps, right-rail detail.</div>
  `;
  root.appendChild(el);
  return () => {};
}
