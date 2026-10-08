// User intent: Settings holds the viewer's three choices (theme, card density, detail view) as segmented controls that
// apply at once; the Theme control stays in step with the topbar toggle, and a theme choice persists with no flash.
import { claimTopbar, tmSegmented } from '../lib/topbar.js';
import { detailViewMode } from '../lib/view-mode.js';
import { currentPref, setThemePref } from '../lib/theme.js';

export const meta = { title: 'Settings', icon: '⚙', sidebarKey: 'settings' };

function block(key, title, description, control) {
  const sec = document.createElement('section');
  sec.className = 'set-block';
  sec.setAttribute('aria-labelledby', `set-${key}-h`);
  const h = document.createElement('h2');
  h.className = 'set-h';
  h.id = `set-${key}-h`;
  h.textContent = title;
  const p = document.createElement('p');
  p.className = 'set-desc';
  p.id = `set-${key}-desc`;
  p.textContent = description;
  const group = document.createElement('div');
  group.className = 'set-control';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', h.id);
  group.setAttribute('aria-describedby', p.id);
  group.appendChild(control);
  sec.append(h, p, group);
  return sec;
}

function press(seg, key) {
  for (const b of seg.querySelectorAll('button')) {
    const on = b.dataset.key === key;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  }
}

export async function mount(root, { store, prefs }) {
  root.innerHTML = '';
  claimTopbar();
  const page = document.createElement('div');
  page.className = 'settings';

  const theme = tmSegmented(
    [{ key: 'dark', label: 'Dark' }, { key: 'light', label: 'Light' }, { key: 'system', label: 'System' }],
    { value: currentPref(), onChange: (k) => setThemePref(k) },
  );
  const density = tmSegmented(
    [{ key: 'full', label: 'Full' }, { key: 'minimal', label: 'Minimal' }],
    {
      value: store.getPrefs()?.card_density === 'minimal' ? 'minimal' : 'full',
      onChange: (k) => prefs.patch({ card_density: k }),
    },
  );
  const detail = tmSegmented(
    [{ key: 'modal', label: 'Modal' }, { key: 'full', label: 'Full page' }],
    { value: detailViewMode(store.getPrefs()), onChange: (k) => prefs.patch({ ui: { detail_view_mode: k } }) },
  );

  page.append(
    block('theme', 'Theme', "Choose the theme. System follows your computer's setting.", theme),
    block('density', 'Card density', 'How much each Kanban card shows.', density),
    block('detail', 'Detail view', 'How a task or epic opens when you click it.', detail),
  );
  root.appendChild(page);

  // The topbar toggle (or another tab's choice applied here) moves the Theme control with it.
  const onTheme = (e) => press(theme, e.detail.pref);
  document.addEventListener('theme:changed', onTheme);
  return () => { document.removeEventListener('theme:changed', onTheme); };
}
