export const meta = { title: 'Kanban', icon: '▦', sidebarKey: 'kanban' };

export async function mount(root) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `
    Kanban placeholder.
    <div class="stub-meta">Plan 2 fills in phase stepper, epic chips, group-by toggle, cards (Minimal/Full), auto-mode strip.</div>
  `;
  root.appendChild(el);
  return () => {};
}
