// plugins/taskmaster/viewer/js/screens/epic-detail.js
import { getEpic } from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { mountEpicDetail } from '../components/epic-detail-document.js';

export const meta = { title: 'Epic', icon: '⬡', sidebarKey: 'epics' };

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export async function mount(root, { subpath, params, store, prefs }) {
  const id = subpath?.[0] || params?.id || null;
  root.innerHTML = '';
  claimTopbar();
  const cleanup = () => { root.replaceChildren(); };

  if (!id) {
    root.innerHTML = `<div class="ed-empty">No epic selected. <a href="#/epics">Back to Epics</a>.</div>`;
    return cleanup;
  }
  if (prefs?.patch) prefs.patch({ ui: { last_epic_id: id } });

  let epic;
  try {
    epic = await getEpic(id);
  } catch (e) {
    root.innerHTML = `<div class="ed-empty">Epic <code>${esc(id)}</code> not found. `
      + `<a href="#/epics">Back to Epics</a>.</div>`;
    return cleanup;
  }

  const dispose = mountEpicDetail(root, {
    epic, store,
    onNavigate: (tid) => { location.hash = `#/task/${encodeURIComponent(tid)}`; },
    chrome: 'page',
  });
  return () => { dispose(); cleanup(); };
}
