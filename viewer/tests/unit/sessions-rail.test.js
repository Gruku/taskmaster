// User intent: the Sessions rail must hand focus back to the row that opened it, even after a session's rail has
// swapped itself for one of its handovers — and a screen left while a detail loads must leave no rail or key
// listener behind.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { SESSIONS, SESSION_DETAILS } from '../mock-fixtures.js';

const dom = new JSDOM('<!doctype html><body><div id="host"></div><button id="row">row</button></body>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.HTMLElement = dom.window.HTMLElement;
global.CSS = { escape: (s) => s };

const { RightRail } = await import('../../js/components/right-rail.js');
const { openSessionDetail, openHandoverDetail } = await import('../../js/screens/sessions.js');

const tick = () => new Promise((r) => setTimeout(r, 0));
function fresh() {
  document.body.innerHTML = '<div id="host"></div><button id="row">row</button>';
  return { host: document.getElementById('host'), row: document.getElementById('row') };
}
const cached = () => ({
  sessions: SESSIONS,
  detailCache: new Map(Object.entries(SESSION_DETAILS)),
});

test('a handover opened from a session rail hands focus back to the session\'s row', async () => {
  const { host, row } = fresh();
  const rail = new RightRail({ host, label: 'Session details' });
  await openSessionDetail(rail, 'team-relayout', cached(), row);
  assert.equal(host.querySelector('#right-rail').classList.contains('right-rail--session'), true);

  host.querySelector('button.rr-ho[data-handover-id="2026-07-12-scope"]').click();
  await tick();
  const aside = host.querySelector('#right-rail');
  assert.ok(aside.classList.contains('right-rail--handover'));
  assert.equal(aside.querySelector('.rr-title').textContent, 'Scope the relayout');
  assert.equal(document.activeElement, aside.querySelector('.rr-title'));

  aside.querySelector('.rr-close').click();
  assert.equal(host.querySelector('#right-rail'), null);
  assert.equal(document.activeElement, row, 'focus is back on the row, not dropped to <body>');
});

for (const [name, open] of [
  ['session', (rail, state) => openSessionDetail(rail, 'team-relayout', state)],
  ['handover', (rail, state) => openHandoverDetail(rail, '2026-07-13-m1-shipped', state)],
]) {
  test(`a ${name} detail that arrives after the screen is left opens no rail and adds no key listener`, async () => {
    const { host } = fresh();
    const rail = new RightRail({ host });
    let answer;
    global.fetch = () => new Promise((resolve) => { answer = resolve; });
    const keyListeners = [];
    const add = document.addEventListener;
    document.addEventListener = function (type, ...rest) {
      if (type === 'keydown') keyListeners.push(rest[0]);
      return add.call(this, type, ...rest);
    };
    try {
      const pending = open(rail, { sessions: SESSIONS, detailCache: new Map() });
      await tick();
      assert.ok(answer, 'the detail is being fetched');
      host.remove();   // the screen is left: its root is emptied
      const body = JSON.stringify(SESSION_DETAILS['team-relayout']);
      answer({ ok: true, status: 200, headers: { get: (k) => (/content-type/i.test(k) ? 'application/json' : null) }, text: async () => body });
      await pending;
    } finally {
      document.addEventListener = add;
    }
    assert.equal(rail.isOpen(), false);
    assert.equal(host.querySelector('#right-rail'), null);
    assert.equal(document.querySelector('#right-rail'), null);
    assert.deepEqual(keyListeners, []);
  });
}
