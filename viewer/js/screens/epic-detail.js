// User intent: the /epic/<id> page — Epic detail built inside its own element, loading, not-found and failure as state
// blocks with one action each, and nothing painted once the router has moved on while the epic was still loading.
import { getEpic } from '../api.js';
import { claimTopbar } from '../lib/topbar.js';
import { mountEpicDetail } from '../components/epic-detail-document.js';
import { stateBlock } from '../components/empty-state.js';
import { h } from '../util/h.js';

export const meta = { title: 'Epic', icon: '⬡', sidebarKey: 'epics' };

const ALL_EPICS = { label: 'All epics', href: '#/epics' };

export async function mount(root, { subpath, params, store, prefs }) {
  const id = subpath?.[0] || params?.id || null;
  claimTopbar();
  const page = h('div', { class: 'ed-page' });
  root.replaceChildren(page);

  let dispose = () => {};
  let loads = 0;
  const cleanup = () => { loads += 1; dispose(); dispose = () => {}; page.remove(); };

  if (!id) {
    page.replaceChildren(stateBlock({ label: 'Epics', headline: 'No epic selected.', action: ALL_EPICS }));
    return cleanup;
  }
  if (prefs?.patch) prefs.patch({ ui: { last_epic_id: id } });

  // Resolves false when the answer arrived for a page that is gone (the router replaced the mount) or for a load a
  // later one superseded: it paints nothing then.
  async function load() {
    const mine = ++loads;
    dispose();
    dispose = () => {};
    page.replaceChildren(stateBlock({ state: 'loading', headline: 'Loading…', busy: true }));
    let epic = null;
    let failure = null;
    try { epic = await getEpic(id); } catch (e) { failure = e; }
    if (mine !== loads || !page.isConnected) return false;
    if (failure && failure.code !== 404) {
      page.replaceChildren(stateBlock({
        state: 'error', label: 'Error', headline: 'This epic could not be loaded.',
        hint: 'The server did not answer. Try again in a moment.', action: { label: 'Try again', onClick: load },
      }));
    } else if (failure || !epic || typeof epic !== 'object' || !epic.id) {
      page.replaceChildren(stateBlock({ state: 'missing', label: 'Not found', headline: `There is no epic called ${id}.`, action: ALL_EPICS }));
    } else {
      dispose = mountEpicDetail(page, { epic, store, chrome: 'page' });
    }
    return true;
  }

  if (!(await load())) return () => {};
  return cleanup;
}
