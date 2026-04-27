export const meta = { title: 'Lessons', icon: '✦', sidebarKey: 'lessons' };
export async function mount(root) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `Lessons placeholder.<div class="stub-meta">Plan 5 fills in Core/Active/Retired shelves, active+passive signals, Reinforce button.</div>`;
  root.appendChild(el);
  return () => {};
}
