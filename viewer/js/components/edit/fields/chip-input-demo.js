// viewer/js/components/edit/fields/chip-input-demo.js
import { h } from '../../../util/h.js';
import { registerDemo } from '../../../dev/edit-demo.js';
import { ChipInput } from './chip-input.js';

const SAMPLE = ['frontend', 'backend', 'css', 'tests', 'viewer', 'mcp', 'docs'];
const fakeSource = async (q) => SAMPLE
  .filter(s => s.includes(q.toLowerCase()))
  .map(s => ({ value: s, label: s }));

registerDemo('ChipInput (free-text)', (root) => {
  root.appendChild(h('div', { style: 'margin-bottom:8px' }, [
    h('strong', {}, 'Read: '),
    ChipInput.read({ value: ['frontend', 'css'], readOnly: false }),
  ]));
  const editHost = h('div', {}, [h('strong', {}, 'Edit (free-text + autocomplete): ')]);
  editHost.appendChild(ChipInput.edit({
    value: ['css'], source: fakeSource, allowFree: true,
    onChange: (v) => console.log('chip change', v),
    onCommit: (v) => console.log('chip commit', v),
    onCancel: () => console.log('chip cancel'),
  }));
  root.appendChild(editHost);
});
