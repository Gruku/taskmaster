// viewer/js/components/edit/fields/md-field-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { MdField } from './md-field.js';

registerDemo('MdField', (root) => {
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read: '),
    MdField.read({ value: 'Line one.\nLine two with `code`.', readOnly: false }),
  ]));
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Empty placeholder: '),
    MdField.read({ value: '', readOnly: false, placeholder: 'no notes yet' }),
  ]));
  const editHost = h('div', {}, [h('strong', {}, 'Edit (Ctrl+Enter to commit): ')]);
  editHost.appendChild(MdField.edit({
    value: 'Multi-line\nedit here.',
    onChange: (v) => console.log('md change', v.length),
    onCommit: (v) => console.log('md commit', v),
    onCancel: () => console.log('md cancel'),
  }));
  root.appendChild(editHost);
});
