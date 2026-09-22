import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const execute = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const python = process.env.N10_PYTHON || path.resolve(repo, '../../.venv/Scripts/python.exe');
export async function control(page, args) {
  const {root} = await (await page.request.get('/api/identity')).json();
  await execute(python, [path.join(repo, 'scripts/viewer_fixture_server.py'), 'ctl', '--root', root, ...args], {windowsHide: true});
  return root;
}
export const update = (page, field, value, task = 'board-001') => control(page, ['--task', task, '--field', field, '--value', value]);
