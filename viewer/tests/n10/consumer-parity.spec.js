import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const golden = JSON.parse(readFileSync(new URL('../../../tests/fixtures/board_dto_b1.json', import.meta.url), 'utf8'));
// The characterization payload: normalized index rows, full task bodies, nested
// duplicates, context and private rows. It exercises consumers, not server reads.
const tasks = golden.expected.tasks.map(row => {
  const task = {...row, ...golden.input.tasks.find(t => t.id === row.id)};
  delete task.blockers_count;
  return task;
});
const historical = {tasks, epics: golden.input.epics.map(e => ({...e, tasks: tasks.filter(t => t.epic === e.id)})),
  phases: golden.expected.phases, context: {active_epic: 'e'}, _rows: {bug: {}}, meta: {_version: 'fixture'}};
// Exercise the compact blocker count against the old array-based callout too.
historical.tasks[0].status = 'blocked';
golden.expected.tasks[0].status = 'blocked';

for (const [route, selector, readyText] of [
  ['kanban', '.kanban-page', 'e-001'],
  ['table', '.tbl-screen', 'e-001'],
  ['epics', '.epics-list', 'Epic'],
  ['archived', '.archived-page', 'e-002'],
  ['issues', '.issues', 'Example issue'],
  ['relation-picker', '#parity-picker', 'e-001'],
]) {
  test(`${route} renders identical text from full payload and compact DTO`, async ({browser, baseURL}, info) => {
    const rendered = [];
    for (const [label, payload] of [['full', historical], ['dto', golden.expected]]) {
      const context = await browser.newContext({baseURL});
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/board*', route => route.fulfill({json: {...payload, revision: 'fixture', cursor: 'fixture'}}));
      await page.route('**/api/viewer/prefs', route => route.fulfill({json: {}}));
      await page.route('**/api/issues*', route => route.fulfill({json: {issues: [
        {id: 'ISS-fixture', title: 'Example issue', status: 'open', severity: 'P2', task_ids: ['e-001'], related_tasks: ['e-001']},
      ]}}));
      await page.goto(`/#/${route === 'relation-picker' ? 'kanban' : route}`);
      await expect.poll(() => page.evaluate(async () => (await import('/static/v3/js/store.js')).store.getBacklog()?.revision)).toBe('fixture');
      if (route === 'relation-picker') {
        await page.evaluate(async () => {
          const {RelationPicker} = await import('/static/v3/js/components/edit/fields/relation-picker.js');
          const {store} = await import('/static/v3/js/store.js');
          const root = document.createElement('div'); root.id = 'parity-picker';
          root.appendChild(RelationPicker.edit({value: [], kind: 'tasks', getBacklog: store.getBacklog}));
          document.body.appendChild(root);
        });
        await page.locator('#parity-picker input').fill('e-');
      }
      await expect(page.locator(selector)).toContainText(readyText);
      rendered.push(await page.locator(selector).innerText());
      await info.attach(`${route}-${label}.txt`, {body: rendered.at(-1), contentType: 'text/plain'});
      expect(errors).toEqual([]);
      await context.close();
    }
    expect(rendered[1]).toBe(rendered[0]);
  });
}
