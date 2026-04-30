import test from 'node:test';
import assert from 'node:assert/strict';
import { renderDiffRow } from '../../js/components/diff-row.js';

test('add row sets pre to + and renders body', () => {
  const html = renderDiffRow({ kind: 'add', body: '<span class="id">T-3</span> New task' });
  assert.match(html, /diff-row add/);
  assert.match(html, /class="pre">\+/);
  assert.match(html, /T-3/);
});

test('mod row renders from -> to', () => {
  const html = renderDiffRow({
    kind: 'mod',
    body: '<span class="id">T-2</span> <span class="from">in-progress</span> <span class="arrow">→</span> <span class="to">done</span>',
  });
  assert.match(html, /diff-row mod/);
  assert.match(html, /class="pre">~/);
  assert.match(html, /from">in-progress/);
  assert.match(html, /to">done/);
});

test('del row sets pre to -', () => {
  const html = renderDiffRow({ kind: 'del', body: 'gone' });
  assert.match(html, /class="pre">-/);
});
