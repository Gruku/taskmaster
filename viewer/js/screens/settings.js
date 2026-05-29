// plugins/taskmaster/viewer/js/screens/settings.js
import { claimTopbar } from '../lib/topbar.js';
import { detailViewMode } from '../lib/view-mode.js';

export const meta = { title: 'Settings', icon: '⚙', sidebarKey: 'settings' };

export async function mount(root, { store, prefs }) {
  root.innerHTML = '';
  root.classList.add('settings');
  claimTopbar();

  const current = detailViewMode(store.getPrefs());

  const sec = document.createElement('section');
  sec.className = 'set-block set-detail-view';
  sec.innerHTML = `
    <h2 class="set-h">Detail view</h2>
    <p class="set-desc">How task and epic detail opens when you click it.</p>`;

  for (const [val, label, hint] of [
    ['modal', 'Open in modal', 'A quick overlay on top of the current screen.'],
    ['full', 'Open full page', 'Navigate to the dedicated detail route.'],
  ]) {
    const row = document.createElement('label');
    row.className = 'set-radio';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'detail_view_mode';
    input.value = val;
    input.checked = current === val;
    input.addEventListener('change', () => {
      if (input.checked) prefs.patch({ ui: { detail_view_mode: val } });
    });
    const txt = document.createElement('span');
    txt.className = 'set-radio__txt';
    txt.innerHTML = `<span class="set-radio__label">${label}</span><span class="set-radio__hint">${hint}</span>`;
    row.append(input, txt);
    sec.appendChild(row);
  }

  root.appendChild(sec);
  return () => { root.classList.remove('settings'); };
}
