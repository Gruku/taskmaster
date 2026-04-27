export const meta = { title: 'Recap', icon: '↻', sidebarKey: 'recap' };
export async function mount(root, { subpath }) {
  const sid = subpath[0] || '(no session)';
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `Recap placeholder.<div class="stub-meta">session=${sid} — Plan 5 fills in story+receipts layered layout.</div>`;
  root.appendChild(el);
  return () => {};
}
