// viewer/js/components/edit/fields/text-field-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { TextField } from './text-field.js';

registerDemo('TextField', (root) => {
  // Read mode — populated
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read (editable, populated): '),
    TextField.read({ value: 'A task title', readOnly: false }),
  ]));
  // Read mode — empty
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read (editable, empty): '),
    TextField.read({ value: '', readOnly: false, placeholder: 'no title yet' }),
  ]));
  // Read mode — read-only
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read (read-only): '),
    TextField.read({ value: 'task-id-123', readOnly: true }),
  ]));
  // Edit mode (live)
  const editHost = h('div', {}, [h('strong', {}, 'Edit (live): ')]);
  editHost.appendChild(TextField.edit({
    value: 'click to edit',
    onChange: (v) => console.log('change', v),
    onCommit: (v) => console.log('commit', v),
    onCancel: () => console.log('cancel'),
  }));
  root.appendChild(editHost);
});
