// viewer/js/components/edit/fields/relation-picker.js
// Builds source functions for ChipInput that query the live backlog.
// Used by Task.depends_on, Issue.task_ids, Handover.task_ids, and any
// other field that links to a backlog entity.

import { ChipInput } from './chip-input.js';

const STATUS_BADGE = {
  todo: 'todo', 'in-progress': '▶', 'in-review': 'rev', done: '✓', blocked: '⛔',
};

export function makeRelationSource(kind, getBacklog) {
  if (kind === 'tasks') {
    return async (q) => {
      const b = getBacklog() || {};
      const tasks = Array.isArray(b.tasks) ? b.tasks : [];
      const ql = q.toLowerCase();
      return tasks
        .filter(t => (t.id && t.id.toLowerCase().includes(ql)) ||
                     (t.title && t.title.toLowerCase().includes(ql)))
        .map(t => ({
          value: t.id,
          label: `${t.id} · ${t.title || ''}`,
          hint: STATUS_BADGE[t.status] || t.status || '',
        }));
    };
  }
  if (kind === 'epics') {
    return async (q) => {
      const b = getBacklog() || {};
      const epics = Array.isArray(b.epics) ? b.epics : [];
      const ql = q.toLowerCase();
      return epics
        .filter(e => (e.id && e.id.toLowerCase().includes(ql)) ||
                     (e.name && e.name.toLowerCase().includes(ql)))
        .map(e => ({ value: e.id, label: `${e.id} · ${e.name || ''}` }));
    };
  }
  if (kind === 'phases') {
    return async (q) => {
      const b = getBacklog() || {};
      const phases = Array.isArray(b.phases) ? b.phases : [];
      const ql = q.toLowerCase();
      return phases
        .filter(p => (p.id && p.id.toLowerCase().includes(ql)) ||
                     (p.name && p.name.toLowerCase().includes(ql)))
        .map(p => ({ value: p.id, label: `${p.id} · ${p.name || ''}` }));
    };
  }
  throw new Error(`unknown relation kind: ${kind}`);
}

// Convenience renderer that combines makeRelationSource with ChipInput.
// Forms can use ChipInput directly + makeRelationSource OR call this helper.
export const RelationPicker = {
  read: ChipInput.read,
  edit({ value, kind, getBacklog, onChange, onCommit, onCancel, placeholder }) {
    const source = makeRelationSource(kind, getBacklog);
    return ChipInput.edit({
      value, source, allowFree: false,
      onChange, onCommit, onCancel,
      placeholder: placeholder || `add ${kind.slice(0, -1)}…`,
    });
  },
  coerce: ChipInput.coerce,
  validate: ChipInput.validate,
};
