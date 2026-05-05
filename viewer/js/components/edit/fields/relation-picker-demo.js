// viewer/js/components/edit/fields/relation-picker-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { RelationPicker } from './relation-picker.js';

const FAKE_BACKLOG = () => ({
  tasks: [
    { id: 'v3-edit-001', title: 'Field renderers', status: 'todo' },
    { id: 'v3-edit-002', title: 'Modal shell', status: 'todo' },
    { id: 'v3-polish-029', title: 'Flat tasks fix', status: 'in-review' },
  ],
  epics: [
    { id: 'v3-edit', name: 'V3 Edit-in-UI' },
    { id: 'v3-polish', name: 'V3 Polish' },
  ],
});

registerDemo('RelationPicker (tasks)', (root) => {
  const editHost = h('div', {}, [h('strong', {}, 'Add task deps: ')]);
  editHost.appendChild(RelationPicker.edit({
    value: ['v3-polish-029'], kind: 'tasks', getBacklog: FAKE_BACKLOG,
    onChange: (v) => console.log('rel change', v),
    onCommit: (v) => console.log('rel commit', v),
    onCancel: () => console.log('rel cancel'),
  }));
  root.appendChild(editHost);
});
