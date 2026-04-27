export const meta = { title: 'Issues', icon: '⚠', sidebarKey: 'issues' };
export async function mount(root) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `Issues placeholder.<div class="stub-meta">Plan 5 fills in hybrid layout, severity glyph, console-style location, italic-serif symptom, repro block, aging bar.</div>`;
  root.appendChild(el);
  return () => {};
}
