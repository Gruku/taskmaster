// viewer/js/components/edit/fields/enum-select-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { EnumSelect } from './enum-select.js';

const STATUSES = [
  { value: 'todo', label: 'Todo' },
  { value: 'in-progress', label: 'In Progress' },
  { value: 'in-review', label: 'In Review' },
  { value: 'done', label: 'Done' },
  { value: 'blocked', label: 'Blocked' },
];

registerDemo('EnumSelect', (root) => {
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read: '),
    EnumSelect.read({ value: 'in-progress', options: STATUSES, readOnly: false }),
  ]));
  const editHost = h('div', {}, [h('strong', {}, 'Edit (changes commit immediately): ')]);
  editHost.appendChild(EnumSelect.edit({
    value: 'todo', options: STATUSES,
    onChange: (v) => console.log('enum change', v),
    onCommit: (v) => console.log('enum commit', v),
    onCancel: () => console.log('enum cancel'),
  }));
  root.appendChild(editHost);
});
