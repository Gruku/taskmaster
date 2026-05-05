// viewer/js/components/edit/fields/date-field-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { DateField } from './date-field.js';

registerDemo('DateField', (root) => {
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read: '),
    DateField.read({ value: '2026-05-04', readOnly: false }),
    h('span', { style: 'margin-left:16px' }, ''),
    h('strong', {}, 'Read (null): '),
    DateField.read({ value: null, readOnly: false }),
  ]));
  const editHost = h('div', {}, [h('strong', {}, 'Edit (Enter to commit): ')]);
  editHost.appendChild(DateField.edit({
    value: '2026-05-04',
    onChange: (v) => console.log('date change', v),
    onCommit: (v) => console.log('date commit', v),
    onCancel: () => console.log('date cancel'),
  }));
  root.appendChild(editHost);
});
