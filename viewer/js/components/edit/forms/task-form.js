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
import { EstimateField } from '../fields/estimate-field.js';
import { KeyValueField } from '../fields/keyvalue-field.js';
import { TASK_STATUS, PRIORITY } from '../../status.js';

// The select offers the marker's own words in the marker table's order, so "In progress" is never "In Progress".
const optionsOf = (table) => Object.entries(table).map(([value, { label }]) => ({ value, label }));
const STATUS_OPTIONS = optionsOf(TASK_STATUS);
const PRIORITY_OPTIONS = optionsOf(PRIORITY);

export function taskSchema({ getBacklog }) {
  const epicOptions = () => (getBacklog()?.epics || []).map(e => ({ value: e.id, label: e.name || e.id }));
  const phaseOptions = () => [{ value: '', label: '—' }].concat(
    (getBacklog()?.phases || []).map(p => ({ value: p.id, label: p.id })));

  return {
    entity: 'task',
    label: 'Task',
    fields: [
      // `wide` spans both columns of the form grid. The rest of Basics and Tracking pair up, so no row ends on a lone field.
      { key: 'title',    label: 'Title',    renderer: TextField, group: 'basics', wide: true,
        required: true, maxLength: 140 },
      { key: 'status',   label: 'Status',   renderer: EnumSelect, group: 'basics',
        required: true, options: STATUS_OPTIONS, marker: 'status' },
      { key: 'priority', label: 'Priority', renderer: EnumSelect, group: 'basics',
        required: true, options: PRIORITY_OPTIONS, marker: 'priority' },
      { key: 'epic',     label: 'Epic',     renderer: EnumSelect, group: 'basics',
        required: true,
        // Dynamic options — resolved at validation/edit time.
        get options() { return epicOptions(); },
        validate(value, { required }) {
          if (required && !value) return 'required';
          if (value && !(getBacklog()?.epics || []).some(e => e.id === value)) return 'unknown epic';
          return null;
        }},
      { key: 'phase',    label: 'Phase',    renderer: EnumSelect, group: 'basics',
        get options() { return phaseOptions(); },
        validate(value) {
          if (!value) return null;
          if (!(getBacklog()?.phases || []).some(p => p.id === value)) return 'unknown phase';
          return null;
        }},
      { key: 'estimate', label: 'Estimate', renderer: EstimateField, group: 'basics' },
      { key: 'stage',    label: 'Stage',    renderer: NumberField, group: 'basics', min: 0 },
      { key: 'sub_repo', label: 'Sub-repo', renderer: TextField, group: 'tracking', maxLength: 64 },
      { key: 'release',  label: 'Release',  renderer: TextField, group: 'tracking', maxLength: 32 },
      { key: 'branch',   label: 'Branch',   renderer: TextField, group: 'tracking', maxLength: 200 },
      { key: 'worktree', label: 'Worktree', renderer: TextField, group: 'tracking', maxLength: 200 },
      { key: 'depends_on', label: 'Depends on', renderer: RelationPicker, group: 'relations',
        kind: 'tasks', getBacklog },
      // Stored as a map { type: path or URL }; edited as rows and saved as that same map.
      { key: 'docs',     label: 'Docs',     renderer: KeyValueField, group: 'relations',
        keyLabel: 'Type', valueLabel: 'Path or URL', addLabel: 'Add doc' },
      { key: 'anchors',  label: 'Anchors',  renderer: ChipInput, group: 'relations',
        allowFree: true, placeholder: 'add a file or path…' },
      { key: 'description', label: 'Description', renderer: MdField, group: 'content', open: true },
      { key: 'specification', label: 'Specification', renderer: MdField, group: 'content' },
      { key: 'plan', label: 'Plan', renderer: MdField, group: 'content' },
      { key: 'notes', label: 'Notes', renderer: MdField, group: 'content' },
      { key: 'review_instructions', label: 'Review instructions', renderer: MdField, group: 'content' },
      { key: 'patchnote', label: 'Patchnote', renderer: MdField, group: 'content' },
    ],
    systemManaged: [
      'id', 'created', 'started', 'completed', 'last_referenced',
      'activity', 'spec_review', 'auto_mode', 'locked_by',
      'claim_expires', 'claim_expires_for',
    ],
    crossField: [
      // Self-dep guard: it needs the owning task's id, so it is checked here. Cycle detection is server-side via backlog_validate.
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
