// viewer/js/components/edit/fields/number-field-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { NumberField } from './number-field.js';

registerDemo('NumberField', (root) => {
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read (5): '),
    NumberField.read({ value: 5, readOnly: false }),
    h('span', { style: 'margin-left:16px' }, ''),
    h('strong', {}, 'Read (null): '),
    NumberField.read({ value: null, readOnly: false }),
  ]));
  const editHost = h('div', {}, [h('strong', {}, 'Edit (Enter to commit): ')]);
  editHost.appendChild(NumberField.edit({
    value: 3, min: 0, max: 100,
    onChange: (v) => console.log('num change', v),
    onCommit: (v) => console.log('num commit', v),
    onCancel: () => console.log('num cancel'),
  }));
  root.appendChild(editHost);
});
