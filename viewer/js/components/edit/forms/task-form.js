// viewer/js/components/edit/forms/task-form.js
// Schema for the Task entity. Drives both the task creation/edit modal
// and the inline-field wrappers on Task Detail.
//
// `getBacklog: () => backlog` is required so enum options for epic/phase
// can resolve dynamically against the live backlog.

import { TextField }     from '../fields/text-field.js';
import { MdField }       from '../fields/md-field.js';
import { EnumSelect }    from '../fields/enum-select.js';
import { NumberField }   from '../fields/number-field.js';
import { ChipInput }     from '../fields/chip-input.js';
import { RelationPicker } from '../fields/relation-picker.js';

const STATUS_OPTIONS = [
  { value: 'todo',        label: 'Todo' },
  { value: 'in-progress', label: 'In Progress' },
  { value: 'in-review',   label: 'In Review' },
  { value: 'done',        label: 'Done' },
  { value: 'blocked',     label: 'Blocked' },
  { value: 'archived',    label: 'Archived' },
];

const PRIORITY_OPTIONS = [
  { value: 'critical', label: 'Critical' },
  { value: 'high',     label: 'High' },
  { value: 'medium',   label: 'Medium' },
  { value: 'low',      label: 'Low' },
];

export function taskSchema({ getBacklog }) {
  const epicOptions = () => (getBacklog()?.epics || []).map(e => ({ value: e.id, label: e.id }));
  const phaseOptions = () => [{ value: '', label: '—' }].concat(
    (getBacklog()?.phases || []).map(p => ({ value: p.id, label: p.id })));

  return {
    entity: 'task',
    label: 'Task',
    fields: [
      { key: 'title',    label: 'Title',    renderer: TextField,
        required: true, maxLength: 140 },
      { key: 'status',   label: 'Status',   renderer: EnumSelect,
        required: true, options: STATUS_OPTIONS },
      { key: 'priority', label: 'Priority', renderer: EnumSelect,
        required: true, options: PRIORITY_OPTIONS },
      { key: 'epic',     label: 'Epic',     renderer: EnumSelect,
        required: true,
        // Dynamic options — resolved at validation/edit time.
        get options() { return epicOptions(); },
        validate(value, { required }) {
          if (required && !value) return 'required';
          if (value && !(getBacklog()?.epics || []).some(e => e.id === value)) return 'unknown epic';
          return null;
        }},
      { key: 'phase',    label: 'Phase',    renderer: EnumSelect,
        get options() { return phaseOptions(); },
        validate(value) {
          if (!value) return null;
          if (!(getBacklog()?.phases || []).some(p => p.id === value)) return 'unknown phase';
          return null;
        }},
      { key: 'estimate', label: 'Estimate (S/M/L or "Nd")', renderer: TextField,
        maxLength: 16 },
      { key: 'stage',    label: 'Stage',    renderer: NumberField, min: 0 },
      { key: 'sub_repo', label: 'Sub-repo', renderer: TextField, maxLength: 64 },
      { key: 'branch',   label: 'Branch',   renderer: TextField, maxLength: 200 },
      { key: 'worktree', label: 'Worktree', renderer: TextField, maxLength: 200 },
      { key: 'release',  label: 'Release',  renderer: TextField, maxLength: 32 },
      { key: 'depends_on', label: 'Depends on', renderer: RelationPicker,
        kind: 'tasks', getBacklog,
        validate(value, spec) {
          // Self-dep guard. Cycle detection is server-side via backlog_validate.
          if (!Array.isArray(value)) return null;
          // The owning task's id is passed via crossField (see below).
          return null;
        }},
      { key: 'docs',     label: 'Docs',     renderer: ChipInput,
        allowFree: true,
        // Stored as object { type: url } in YAML; the form treats it as an
        // array of "type:url" strings on the wire and the form layout is
        // responsible for serializing back. For Phase A we keep it as the
        // already-flat chip-input view — Task 13 wires the round-trip.
      },
      { key: 'anchors',  label: 'Anchors',  renderer: ChipInput,
        allowFree: true },
      { key: 'description', label: 'Description', renderer: MdField },
      { key: 'specification', label: 'Specification', renderer: MdField },
      { key: 'plan', label: 'Plan', renderer: MdField },
      { key: 'notes', label: 'Notes', renderer: MdField },
      { key: 'review_instructions', label: 'Review instructions', renderer: MdField },
      { key: 'patchnote', label: 'Patchnote', renderer: MdField },
    ],
    systemManaged: [
      'id', 'created', 'started', 'completed', 'last_referenced',
      'activity', 'spec_review', 'auto_mode', 'locked_by',
    ],
    crossField: [
      // Self-dep guard.
      (entity) => {
        const id = entity.id;
        const deps = entity.depends_on || [];
        if (id && Array.isArray(deps) && deps.includes(id)) {
          return { key: 'depends_on', error: 'cannot depend on itself' };
        }
        return null;
      },
    ],
  };
}
