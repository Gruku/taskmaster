export const meta = { title: 'Dashboard', icon: '▤', sidebarKey: 'dashboard' };

export async function mount(root, { store }) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `
    Dashboard placeholder.
    <div class="stub-meta">Plan 4 will fill in the bento layout, briefing strip, and customizable widgets.</div>
  `;
  root.appendChild(el);
  return () => {};
}
