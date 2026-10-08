// viewer/js/components/edit/fields/relation-picker.js
// Builds source functions for ChipInput that query the live backlog.
// Used by Task.depends_on, Issue.task_ids, Handover.task_ids, and any
// other field that links to a backlog entity.

import { ChipInput } from './chip-input.js';
import { statusMeta } from '../../status.js';

// `exclude` lists ids never offered: a task is not its own dependency.
export function makeRelationSource(kind, getBacklog, { exclude = [] } = {}) {
  const source = _source(kind, getBacklog);
  if (!exclude.length) return source;
  return async (q) => (await source(q)).filter((r) => !exclude.includes(r.value));
}

function _source(kind, getBacklog) {
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
          // Said the way every task status is said: a shape plus a word, never the bare word.
          marker: t.status ? statusMeta('task', t.status) : null,
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
  edit({ value, kind, getBacklog, onChange, onCommit, onCancel, placeholder, id, describedBy, autoFocus, label, entityId }) {
    const source = makeRelationSource(kind, getBacklog, { exclude: entityId ? [entityId] : [] });
    return ChipInput.edit({
      value, source, allowFree: false,
      onChange, onCommit, onCancel, id, describedBy, autoFocus, label,
      placeholder: placeholder || `add ${kind.slice(0, -1)}…`,
    });
  },
  coerce: ChipInput.coerce,
  validate: ChipInput.validate,
};
