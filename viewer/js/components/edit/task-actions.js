// viewer/js/components/edit/task-actions.js
// Shared wrappers for opening the task modal in create/edit mode.
// User intent: what reaches the server is what the user changed and nothing else — a new task carries its defaults and
// what was typed, an edit carries only the changed fields, and a write that lost a race is settled field by field.

import { openEntityModal } from './entity-modal.js';
import { sameValue } from './same-value.js';
import { describeWriteError, lostRace } from './write-errors.js';
import { taskSchema } from './forms/task-form.js';

// The write has landed; a board that fails to refresh now catches up on its next poll. The form must not stay open
// over it, or a second Save would make the same write again.
const refresh = (store, api) => Promise.resolve(store.refreshBoard(api)).catch(() => {});

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
      } catch (e) {
        return { error: describeWriteError(e) };
      }
      await refresh(store, api);
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
    const { showFullConflict, optionText } = await import('./conflict-banner.js');
    const current = e.current || {};
    return new Promise((done) => {
      showFullConflict({
        entityKind: 'task', entityId: task.id,
        // Only the user's own changes are in question; what else moved on the server is simply the server's.
        localDraft: { ...current, ...changes }, currentValue: current,
        // Each field is named as the form names it, never by its stored key.
        labels: Object.fromEntries(schema.fields.map((f) => [f.key, f.label])),
        // ...and each choice field's values in the words its picker shows.
        texts: Object.fromEntries(schema.fields.filter((f) => f.options).map((f) => [f.key, optionText(f)])),
        onResolve: async (merged) => {
          const patch = Object.fromEntries(Object.keys(changes)
            .filter((k) => !sameValue(merged[k], current[k])).map((k) => [k, merged[k]]));
          try {
            // Written against the revision the banner showed, which is stored only once the write lands (the write
            // stores the revision it made). A failed write leaves the old one, so the next Save is compared again
            // instead of overwriting the fields the user chose to take from the server.
            if (Object.keys(patch).length) await api.patchTask(task.id, patch, { ifMatch: e.current_etag });
            else store.setEtag?.(`task:${task.id}`, e.current_etag);
          } catch (err) {
            done({ error: lostRace(err) ? 'The task changed again — save to compare once more' : describeWriteError(err) });
            return;
          }
          await refresh(store, api);
          done();
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
      } catch (e) {
        if (lostRace(e)) return { error: 'Conflict — see banner', wait: resolveConflict(e, changes) };
        return { error: describeWriteError(e) };
      }
      await refresh(store, api);
    },
    onCancel: () => {},
  });
}
