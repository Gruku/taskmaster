// viewer/js/components/edit/task-actions.js
// Shared wrappers for opening the task modal in create/edit mode.

import { openEntityModal } from './entity-modal.js';
import { taskSchema } from './forms/task-form.js';

export function openTaskCreateModal({ store, api, prefillEpic }) {
  const schema = taskSchema({ getBacklog: () => store.getBacklog() });
  openEntityModal({
    schema, mode: 'create',
    initialEntity: {
      epic: prefillEpic || store.getBacklog()?.context?.active_epic || '',
      status: 'todo',
      priority: 'medium',
    },
    onSave: async (draft) => {
      try {
        await api.createTask(draft);
        store.setBacklog(await api.backlog());
      } catch (e) {
        if (e && e.code === 422 && e.errors) {
          const msgs = Object.entries(e.errors).map(([k, v]) => `${k}: ${v}`).join(' · ');
          return { error: msgs };
        }
        return { error: e.message || String(e) };
      }
    },
    onCancel: () => {},
  });
}

export function openTaskEditModal({ store, api, task }) {
  const schema = taskSchema({ getBacklog: () => store.getBacklog() });
  openEntityModal({
    schema, mode: 'edit',
    initialEntity: { ...task },
    onSave: async (draft) => {
      try {
        // Only send changed fields (not systemManaged ones).
        const patch = {};
        for (const f of schema.fields) {
          if (JSON.stringify(draft[f.key] ?? null) !== JSON.stringify(task[f.key] ?? null)) {
            patch[f.key] = draft[f.key];
          }
        }
        if (Object.keys(patch).length) await api.patchTask(task.id, patch);
        store.setBacklog(await api.backlog());
      } catch (e) {
        if (e && e.code === 409) {
          // Surface full conflict; user can pick fields to keep.
          const { showFullConflict } = await import('./conflict-banner.js');
          showFullConflict({
            entityKind: 'task', entityId: task.id,
            localDraft: draft, currentValue: e.current,
            currentEtag: e.current_etag,
            onResolve: async (merged) => {
              const { store: s } = await import('../../store.js');
              s.setEtag(`task:${task.id}`, e.current_etag);
              await api.patchTask(task.id, merged);
              s.setBacklog(await api.backlog());
            },
          });
          return { error: 'Conflict — see banner' };
        }
        if (e && e.code === 422 && e.errors) {
          const msgs = Object.entries(e.errors).map(([k, v]) => `${k}: ${v}`).join(' · ');
          return { error: msgs };
        }
        return { error: e.message || String(e) };
      }
    },
    onCancel: () => {},
  });
}
