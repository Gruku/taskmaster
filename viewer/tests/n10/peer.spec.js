import {test, expect} from '@playwright/test';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const execute = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const python = process.env.N10_PYTHON || path.resolve(repo, '../../.venv/Scripts/python.exe');
async function peer(page, task, value, field = 'title') {
  const {root} = await (await page.request.get('/api/identity')).json();
  await execute(python, [path.join(repo, 'scripts/viewer_fixture_server.py'), 'ctl', '--root', root,
    '--task', task, '--field', field, '--value', value], {windowsHide: true});
}

test('an unrelated peer edit does not conflict with the first save', async ({page}) => {
  await page.goto('/#/task/board-001');
  const field = page.locator('.if-wrap[data-key="title"]');
  await expect(field).toBeVisible();
  await field.click();
  await peer(page, 'board-002', 'Unrelated peer edit');
  const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/tasks/board-001'));
  await field.locator('input').fill('Saved across unrelated change');
  await field.locator('input').press('Enter');
  expect((await saved).status()).toBe(200);
});

for (const resolution of ['Keep mine', 'Use server']) {
  test(`draft survives a peer delta; ${resolution} resolves the stale first save`, async ({page}) => {
    await page.goto('/#/task/board-001');
    const field = page.locator('.if-wrap[data-key="title"]');
    await expect(field).toBeVisible();
    let release, intercepted;
    const held = new Promise(resolve => { release = resolve; });
    const arrived = new Promise(resolve => { intercepted = resolve; });
    let first = true;
    await page.route('**/api/tasks/board-001', async route => {
      if (first && route.request().method() === 'PATCH') {
        first = false; intercepted(); await held;
      }
      await route.continue();
    });
    await field.click();
    const input = field.locator('input');
    await input.fill(`Local draft ${resolution}`);
    await input.press('Enter');
    await arrived;
    const delta = page.waitForResponse(async r => r.url().includes('/api/board?since=') && r.status() === 200 && (await r.json()).tasks_upsert?.some(t => t.id === 'board-001'));
    await peer(page, 'board-001', `Peer value ${resolution}`);
    await delta;
    await expect(input).toHaveValue(`Local draft ${resolution}`);
    const conflict = page.waitForResponse(r => r.request().method() === 'PATCH' && r.status() === 409);
    release();
    await conflict;
    await expect(page.locator('.cb-field')).toBeVisible();
    await page.getByRole('button', {name: resolution, exact: true}).click();
    const expected = resolution === 'Keep mine' ? `Local draft ${resolution}` : `Peer value ${resolution}`;
    await expect(field).toContainText(expected);
    const server = await (await page.request.get('/api/task/board-001/detail')).json();
    expect(server.task.title).toBe(expected);
  });
}

test('an idle detail and a reopened modal refresh after a peer delta', async ({page}) => {
  await page.goto('/#/task/board-001');
  await expect(page.locator('.if-wrap[data-key="title"]')).toBeVisible();
  await peer(page, 'board-001', 'Idle detail peer update');
  await expect(page.locator('.if-wrap[data-key="title"]')).toContainText('Idle detail peer update', {timeout: 10000});
  await page.goto('/#/kanban');
  await page.locator('.card-task[data-task-id="board-001"]').click();
  await expect(page.locator('.dm-modal')).toContainText('Idle detail peer update');
  await page.keyboard.press('Escape');
  await expect(page.locator('.dm-modal')).toHaveCount(0);
  await peer(page, 'board-001', 'Reopened peer update');
  await page.locator('.card-task[data-task-id="board-001"]').click();
  await expect(page.locator('.dm-modal')).toContainText('Reopened peer update');
  await page.getByRole('link', {name: 'Open full'}).click();
  await expect(page.locator('.if-wrap[data-key="title"]')).toContainText('Reopened peer update');
});
