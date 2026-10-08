// User intent: one table of every viewer route with the mocks that put real content on it and the selector that says
// it has loaded, so every route-level tool (the a11y gate, the capture scenes) covers the same routes the same way.
import { expect } from '@playwright/test';
import {
  dashboardMocks, kanbanMocks, tableMocks, epicsMocks, epicDetailMocks, taskPageMocks, issuesMocks, issueDetailMocks,
  bugsMocks, bugDetailMocks, ideasMocks, sessionsMocks, archivedMocks, settingsMocks,
} from './mock-fixtures.js';

const KANBAN_READY = '.card-task[data-task-id] > .link-row__link';
const MISSING = '.tm-empty[data-state="missing"]';

export const ROUTES = [
  // { name, route, build, ready, open?, state? }
  //   route: the hash the page is opened on
  //   build: ({ theme }) → the mockApi table (a plan 3 builder, or one spread with a single override key)
  //   ready: a selector visible only once the route shows real content — or, with `state`, its state block
  //   open:  async (page) → void, run after `ready` for a state that is not a route (the detail modal)
  //   state: 'missing' for the routes that exist to show a not-found block
  { name: 'dashboard', route: '#/dashboard', build: dashboardMocks, ready: '.dk-note[data-note-id="NOTE-001"] .dk-note__body' },
  { name: 'kanban', route: '#/kanban', build: kanbanMocks, ready: KANBAN_READY },
  { name: 'detail-modal', route: '#/kanban', build: kanbanMocks, ready: KANBAN_READY,
    open: async (page) => {
      // At a phone width one column shows at a time: pick T-102's (in progress) from the column tabs first.
      const tab = page.locator('#kanban-col-in-progress-tab');
      if (await tab.isVisible()) await tab.click();
      await page.locator(`.card-task[data-task-id="T-102"] > .link-row__link`).click();
      await expect(page.locator('.modal--detail .td-doc--embedded')).toBeVisible();
      // The modal fades and rises in; read it once it has landed, not mid-fade (axe would read the fade's contrast).
      // Only running, finite animations are awaited: a paused one would never finish.
      await page.evaluate(() => Promise.all(document.getAnimations()
        .filter((a) => a.playState === 'running' && a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished)));
    } },
  { name: 'table', route: '#/table', build: tableMocks, ready: 'table.tbl .tbl-row' },
  { name: 'epics', route: '#/epics', build: epicsMocks, ready: '.epic-row .link-row__link' },
  { name: 'epic', route: '#/epic/viewer', build: epicDetailMocks, ready: 'h1.ed-title' },
  { name: 'epic-missing', route: '#/epic/NOPE-999',
    build: (o) => ({ ...epicDetailMocks(o), '/api/epic/NOPE-999': { status: 404, json: { ok: false, error: 'unknown epic' } } }),
    ready: MISSING, state: 'missing' },
  { name: 'task', route: '#/task/T-102', build: taskPageMocks, ready: '.td-page-A h1.td-title' },
  { name: 'task-b', route: '#/task/T-102?view=B', build: taskPageMocks, ready: '.td-page-B h1.td-title' },
  { name: 'task-missing', route: '#/task/NOPE-999', build: taskPageMocks, ready: MISSING, state: 'missing' },
  { name: 'issues', route: '#/issues', build: issuesMocks, ready: '.issues-col .issue-card' },
  { name: 'issue', route: '#/issue/ISS-012', build: issueDetailMocks, ready: '.dp-page--issue h1.td-title' },
  { name: 'issue-missing', route: '#/issue/ISS-999', build: issueDetailMocks, ready: MISSING, state: 'missing' },
  { name: 'bugs', route: '#/bugs', build: bugsMocks, ready: '.bugs__list .bug-row' },
  { name: 'bug', route: '#/bug/B-031', build: bugDetailMocks, ready: '.dp-page--bug h1.td-title' },
  { name: 'bug-missing', route: '#/bug/B-999', build: bugDetailMocks, ready: MISSING, state: 'missing' },
  { name: 'ideas', route: '#/ideas', build: ideasMocks, ready: '.ideas__list .idea-row' },
  { name: 'sessions', route: '#/sessions', build: sessionsMocks, ready: '.ho-child[data-handover-id="2026-07-13-m1-shipped"]' },
  { name: 'archived', route: '#/archived', build: archivedMocks, ready: '.arch-row[data-task-id="T-1001"] .link-row__link' },
  { name: 'settings', route: '#/settings', build: settingsMocks,
    ready: '.set-control[role="group"] .tm-segmented > button[data-key="system"]' },
];
