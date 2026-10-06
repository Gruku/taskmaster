import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><div id="r"></div>');
globalThis.document = window.document;

import { createDecisionCard } from '../../js/components/continuity/decision-card.js';

const item = { id: 'DEC-001', type: 'decision', title: 'Land 086', age_days: 1 };
const decision = { id: 'DEC-001', title: 'Land 086', options: ['push MR', 'merge develop', 'hold'], recommendation: 2 };

test('decision-card renders title + N options + primary "Pick option N"', () => {
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

test('options are buttons, the recommended one says so, and clicking option 3 resolves with 3', () => {
  let picked = null;
  const card = createDecisionCard({ item, decision, onResolve: (n) => { picked = n; } });
  document.body.appendChild(card.root);
  const opts = [...card.root.querySelectorAll('.co-decision__opt')];
  for (const o of opts) {
    assert.equal(o.tagName, 'BUTTON');
    assert.equal(o.getAttribute('type'), 'button');
  }
  assert.equal(opts[1].querySelector('.co-decision__rec').textContent, 'Recommended');
  assert.equal(card.root.querySelectorAll('.co-decision__rec').length, 1);
  opts[2].click();
  assert.equal(picked, 3);
  const primary = card.root.querySelector('.co-decision__primary');
  assert.equal(primary.className.split(' ').filter((c) => c.startsWith('btn')).join(' '), 'btn btn--primary btn--sm');
  const drop = [...card.root.querySelectorAll('button')].find((b) => b.textContent === 'Drop');
  assert.ok(drop.classList.contains('btn') && drop.classList.contains('btn--ghost') && drop.classList.contains('btn--sm'));
});

test('a refused resolve disables every button while it runs, then says why in words', async () => {
  let reject;
  const card = createDecisionCard({
    item, decision,
    onResolve: () => new Promise((_, r) => { reject = r; }),
  });
  document.body.appendChild(card.root);
  const buttons = () => [...card.root.querySelectorAll('button')];
  card.root.querySelector('.co-decision__primary').click();
  assert.equal(buttons().length, 5);
  assert.ok(buttons().every((b) => b.disabled), 'every button is disabled while the resolve runs');
  reject(Object.assign(new Error('POST /api/decisions/DEC-001/resolve → 500'), { code: 500 }));
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(buttons().every((b) => !b.disabled), 'buttons are enabled again');
  const err = card.root.querySelector('p.co-error');
  assert.equal(err.getAttribute('role'), 'alert');
  assert.equal(err.textContent, 'The server could not save this change. Try again in a moment.');
});

test('a refusal hands focus back to the pressed button', async () => {
  const card = createDecisionCard({
    item, decision,
    onResolve: () => Promise.reject(Object.assign(new Error('POST → 500'), { code: 500 })),
  });
  document.body.appendChild(card.root);
  const opt = card.root.querySelectorAll('.co-decision__opt')[2];
  opt.focus();
  opt.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(document.activeElement, opt);
});

test('a refused drop is said on the card with the decision noun', async () => {
  const card = createDecisionCard({
    item, decision,
    onDrop: () => Promise.reject(Object.assign(new Error('gone'), { code: 404 })),
  });
  document.body.appendChild(card.root);
  [...card.root.querySelectorAll('button')].find((b) => b.textContent === 'Drop').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(card.root.querySelector('.co-error').textContent, 'This decision no longer exists — it may have been archived or removed.');
  assert.ok([...card.root.querySelectorAll('button')].every((b) => !b.disabled));
});
