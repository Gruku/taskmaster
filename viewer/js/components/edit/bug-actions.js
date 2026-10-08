// viewer/js/components/edit/bug-actions.js
// User intent: the Bug page's actions are in-app forms, never browser dialogs — a fix needs its commit, an adoption a
// task that is really on the board, a promotion its evidence — and a refused write is said in words.

import { openEntityModal } from './entity-modal.js';
import { describeWriteError } from './write-errors.js';
import { TextField } from './fields/text-field.js';
import { MdField } from './fields/md-field.js';
import { EnumSelect } from './fields/enum-select.js';
import { confirmDialog } from '../modal.js';
import { severityMeta } from '../status.js';
import { updateBug, promoteBugs } from '../../api.js';

const SEVERITY_OPTIONS = ['P0', 'P1', 'P2', 'P3'].map((value) => ({ value, label: severityMeta(value)?.label ?? value }));
const trim = (v) => (typeof v === 'string' ? v.trim() : '');

// One form per action: the write runs in onSave, a refusal stays in the form as words, success hands off to onDone.
function openAction({ bug, title, saveLabel, fields, initialEntity = {}, write, onDone, onClose }) {
  openEntityModal({
    schema: { entity: 'bug', label: 'Bug', fields },
    mode: 'create', title, eyebrow: bug.id, saveLabel, initialEntity,
    onSave: async (draft) => {
      let result;
      try {
        result = await write(draft);
      } catch (e) {
        return { error: describeWriteError(e, { noun: 'bug' }) };
      }
      // The write landed: a fault in the page's follow-up must not reopen the form as a refusal, but it is not hidden.
      await Promise.resolve().then(() => onDone?.(result)).catch((e) => { console.error('bug action: after the write', e); });
    },
    onCancel: () => {},
    onClose,
  });
}

export function openMarkFixed({ bug, onDone, onClose }) {
  openAction({
    bug, title: 'Mark fixed', saveLabel: 'Mark fixed', onDone, onClose,
    fields: [{ key: 'fix_commit', label: 'Fix commit', renderer: TextField, wide: true, required: true, maxLength: 200 }],
    write: (d) => updateBug(bug.id, { status: 'fixed', fix_commit: trim(d.fix_commit) }),
  });
}

export function openAdopt({ bug, getBacklog, onDone, onClose }) {
  const onBoard = (id) => {
    const tasks = getBacklog?.()?.tasks;
    return Array.isArray(tasks) && tasks.some((t) => t?.id === id);
  };
  openAction({
    bug, title: 'Adopt into a task', saveLabel: 'Adopt', onDone, onClose,
    fields: [{
      key: 'adopted_into', label: 'Task', renderer: TextField, wide: true, required: true, maxLength: 40,
      validate: (v) => {
        const id = trim(v);
        if (!id) return 'required'; // the shared wording: "Task is required"
        return onBoard(id) ? null : `No task ${id} on the board`;
      },
    }],
    write: (d) => updateBug(bug.id, { status: 'adopted', adopted_into: trim(d.adopted_into) }),
  });
}

export function openPromote({ bug, onDone, onClose }) {
  openAction({
    bug, title: 'Promote to an issue', saveLabel: 'Promote', onClose,
    initialEntity: { title: bug.title ?? '', severity: bug.severity ?? 'P1' },
    fields: [
      { key: 'title', label: 'Title', renderer: TextField, wide: true, required: true, maxLength: 140 },
      { key: 'severity', label: 'Severity', renderer: EnumSelect, required: true, options: SEVERITY_OPTIONS },
      { key: 'evidence_text', label: 'Evidence — why it is recurring, systemic or outstanding', renderer: MdField, wide: true, required: true },
    ],
    write: (d) => promoteBugs({ bug_ids: [bug.id], title: trim(d.title), severity: d.severity, evidence_text: trim(d.evidence_text) }),
    onDone: (result) => onDone?.(result?.issue_id),
  });
}

// undefined = shelved; { cancelled: true } = kept open; { error } = refused, in words.
// onConfirm runs once the user says Shelve, before the write goes out.
export async function shelveBug({ bug, onConfirm }) {
  const ok = await confirmDialog({
    title: `Shelve ${bug.id}?`,
    message: 'It leaves the open list. It can still be marked fixed, adopted or promoted later.',
    confirmLabel: 'Shelve', cancelLabel: 'Keep open', alert: true,
  });
  if (!ok) return { cancelled: true };
  onConfirm?.();
  try {
    await updateBug(bug.id, { status: 'shelved' });
  } catch (e) {
    return { error: describeWriteError(e, { noun: 'bug' }) };
  }
  return undefined;
}
