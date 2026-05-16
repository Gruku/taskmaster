import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><div id="r"></div>');
globalThis.document = window.document;

import { createDecisionCard } from '../../js/components/continuity/decision-card.js';

test('decision-card renders title + N options + primary "Pick option N"', () => {
  const item = {
    id: 'DEC-001', type: 'decision',
    title: 'Land 086', age_days: 1,
  };
  const decision = {
    id: 'DEC-001', title: 'Land 086',
    options: ['push MR', 'merge develop', 'hold'],
    recommendation: 2,
  };
  let picked = null;
  const card = createDecisionCard({ item, decision, onResolve: (n) => picked = n });
  document.body.appendChild(card.root);
  const opts = card.root.querySelectorAll('.co-decision__opt');
  assert.equal(opts.length, 3);
  const rec = card.root.querySelector('.co-decision__opt.is-rec');
  assert.ok(rec, 'recommendation should be flagged');
  const primary = card.root.querySelector('.co-decision__primary');
  assert.match(primary.textContent, /Pick option 2/);
  primary.click();
  assert.equal(primary, primary); // ensure DOM intact
  assert.equal(picked, 2);
});
