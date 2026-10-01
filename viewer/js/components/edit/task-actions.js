// viewer/js/components/edit/task-actions.js
// Shared wrappers for opening the task modal in create/edit mode.
// User intent: what reaches the server is what the user changed and nothing else — a new task carries its defaults and
// what was typed, an edit carries only the changed fields, and a write that lost a race is settled field by field.

import { openEntityModal, sameValue } from './entity-modal.js';
import { taskSchema } from './forms/task-form.js';

function describe(e) {
  if (e && e.code === 422 && e.errors) {
    return Object.entries(e.errors).map(([k, v]) => `${k}: ${v}`).join(' · ');
  }
  return e?.message || String(e);
}

export function openTaskCreateModal({ store, api, prefillEpic }) {
  const schema = taskSchema({ getBacklog: () => store.getBacklog() });
  openEntityModal({
    schema, mode: 'create',
    // The defaults are where the form starts, not an edit: an untouched form has nothing to save.
    initialEntity: {
      epic: prefillEpic || store.getBacklog()?.context?.active_epic || '',
      status: 'todo',
      priority: 'medium',
    },
    onSave: async (draft) => {
      try {
        await api.createTask(draft);
        await store.refreshBoard(api);
      } catch (e) {
        return { error: describe(e) };
      }
    },
    onCancel: () => {},
  });
}

export function openTaskEditModal({ store, api, task }) {
  const schema = taskSchema({ getBacklog: () => store.getBacklog() });
  store.beginEdit?.(task.id);

  // The task changed under the form. The banner offers, for each field the user changed, their value or the
  // server's; the form is held until that is answered. Resolves to the form's next state: nothing (saved — close),
  // {} (dismissed — back to editing) or { error }.
  async function resolveConflict(e, changes) {
    const { showFullConflict } = await import('./conflict-banner.js');
    const current = e.current || {};
    return new Promise((done) => {
      showFullConflict({
        entityKind: 'task', entityId: task.id,
        // Only the user's own changes are in question; what else moved on the server is simply the server's.
        localDraft: { ...current, ...changes }, currentValue: current,
        currentEtag: e.current_etag,
        onResolve: async (merged) => {
          try {
            store.setEtag?.(`task:${task.id}`, e.current_etag);
            const patch = Object.fromEntries(Object.keys(changes)
              .filter((k) => !sameValue(merged[k], current[k])).map((k) => [k, merged[k]]));
            if (Object.keys(patch).length) await api.patchTask(task.id, patch);
            await store.refreshBoard(api);
            done();
          } catch (err) {
            done({ error: err && err.code === 409 ? 'The task changed again — save to compare once more' : describe(err) });
          }
        },
        onDismiss: () => done({}),
      });
      document.querySelector('#conflict-banner-host input, #conflict-banner-host button')?.focus();
    });
  }

  openEntityModal({
    schema, mode: 'edit',
    onClose: () => store.endEdit?.(task.id),
    initialEntity: { ...task },
    onSave: async (_draft, { changes }) => {
      try {
        // Only the fields the form reports as changed are sent (never systemManaged ones: they have no field).
        if (Object.keys(changes).length) await api.patchTask(task.id, changes);
        await store.refreshBoard(api);
      } catch (e) {
        if (e && e.code === 409) return { error: 'Conflict — see banner', wait: resolveConflict(e, changes) };
        return { error: describe(e) };
      }
    },
    onCancel: () => {},
  });
}
