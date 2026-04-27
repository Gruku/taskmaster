export const meta = { title: 'Auto Mode', icon: '⌬', sidebarKey: 'auto_mode' };
export async function mount(root) {
  const el = document.createElement('div');
  el.className = 'stub';
  el.innerHTML = `Auto Mode placeholder.<div class="stub-meta">Plan 6 fills in Quest Spine SVG, sessions strip, side panels, Spine|Log toggle.</div>`;
  root.appendChild(el);
  return () => {};
}
