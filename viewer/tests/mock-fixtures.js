// User intent: small, realistic API payloads shared by the mocked specs, so a screen is checked with content on it
// rather than in its empty state.
const task = (id, title, status, priority, epic, extra = {}) =>
  ({ id, title, status, priority, epic, phase: 'P1', depends_on: [], ...extra });

export const BOARD = {
  revision: 'r1',
  cursor: 'c1',
  meta: { project: 'Fixture' },
  phases: [{ id: 'P1', name: 'Foundation', status: 'active' }],
  epics: [
    { id: 'viewer', name: 'Viewer re-skin', status: 'active', phase: 'P1' },
    { id: 'store', name: 'Native store', status: 'active', phase: 'P1' },
  ],
  tasks: [
    task('T-101', 'Token foundation and theme switch', 'done', 'high', 'viewer'),
    task('T-102', 'Re-skin the Kanban cards and columns', 'in-progress', 'critical', 'viewer'),
    task('T-103', 'Dashboard notes read as paper stickers', 'in-progress', 'medium', 'viewer'),
    task('T-104', 'Sessions timeline: replace the legacy palette', 'todo', 'medium', 'viewer'),
    task('T-105', 'Incremental related-index rebuild under the writer mutex', 'todo', 'high', 'store'),
    task('T-106', 'Quarantined rows must not force a projection scan', 'blocked', 'low', 'store', { depends_on: ['T-105'] }),
    task('T-107', 'Review the cutover checklist', 'in-review', 'medium', 'store'),
  ],
};

const ago = (hours) => new Date(Date.now() - hours * 3_600_000).toISOString();
export const NOTES = {
  notes: [
    { id: 'NOTE-001', author: 'user', pinned: true, created: ago(2),
      body: 'Ship the viewer re-skin before the weekend. Check the **light theme** on the laptop screen, not only the big monitor.' },
    { id: 'NOTE-002', author: 'claude', pinned: false, created: ago(5),
      body: 'The store write hang is the O(N²) related-index rebuild under the writer mutex. Fix verified in scratch; needs `pytest -m scale` before merge.' },
    { id: 'NOTE-003', author: 'user', pinned: false, created: ago(26),
      body: 'Ask about the Linear sync retries.' },
    { id: 'NOTE-004', author: 'claude', pinned: false, created: ago(50),
      body: 'Three follow-ups left from the cutover:\n\n- restore scalars\n- re-run the bypass gate\n- [handover](#/sessions) for tomorrow' },
  ],
};

// A task with every editable field filled, for the forms and the detail views. `docs` is a map, as it is stored.
export const RICH_TASK = {
  id: 'T-102', title: 'Re-skin the Kanban cards and columns', status: 'in-progress', priority: 'critical', epic: 'viewer', phase: 'P1',
  estimate: 'M', stage: 2, sub_repo: 'viewer', branch: 'feat/viewer-reality-reprojection', worktree: '.worktrees/viewer-rr', release: '7.1.0',
  depends_on: ['T-101'],
  docs: { spec: 'docs/specs/2026-10-01-viewer-reality-reprojection-design.md', plan: 'docs/plans/2026-10-01-viewer-rr-2a-task-modals.md' },
  anchors: ['viewer/js/components/card.js', 'viewer/css/screens/kanban.css'],
  created: '2026-09-28T09:00:00Z', last_referenced: '2026-09-30T16:20:00Z',
  description: 'Cards and columns take the **Reality Reprojection** grounds.\n\nSee [the spec](docs/specs/2026-10-01-viewer-reality-reprojection-design.md).',
  specification: '## Requirements\n\n1. Cards are the raised ground.\n2. Columns are the milled channel.\n3. No shadows.\n\n```css\n.card { background: var(--card-bg); }\n```',
  plan: '1. Tokens\n2. Cards\n3. Columns\n\n| Step | State |\n|---|---|\n| Tokens | done |\n| Cards | in progress |',
  notes: 'Light theme: check the card edge on the laptop screen.',
  review_instructions: 'Open the board in both themes and drag a card across columns.',
  patchnote: 'The board takes the new look.',
};

// The payload of GET /api/task/<id>/detail for `task`.
export const taskDetail = (task, etag = 't1:fixture', related = {}) => ({ task, related, claim: null, etag });

// ── Detail views ──
// What GET /api/task/<id>/detail carries beside the task: resolved neighbours, handovers and issues.
export const RICH_RELATED = {
  dependencies: [
    { id: 'T-101', title: 'Token foundation and theme switch', status: 'done' },
    { id: 'T-105', title: 'Incremental related-index rebuild under the writer mutex', status: 'todo' },
  ],
  unblocks: [{ id: 'T-104', title: 'Sessions timeline: replace the legacy palette', status: 'todo' }],
  handovers: [
    { id: '2026-09-30-kanban-reskin', kind: 'checkpoint', status: 'open', created: '2026-09-30T16:20:00Z',
      quote: '## Run summary\n\nCards are done; **columns** are next. See `kanban.css`.' },
  ],
  issues: [{ id: 'ISS-012', title: 'Card edge vanishes on the light page ground', severity: 'High' }],
};

// The rich task as the detail views meet it: notes with headings and a table, a second dependency, blockers, typed
// links, a review verdict, gates, a merge ladder and activity.
export const DETAIL_TASK = {
  ...RICH_TASK,
  depends_on: ['T-101', 'T-105'],
  started: '2026-09-29T08:30:00Z',
  notes: '## Findings\n\nThe card edge is too faint on the laptop screen.\n\n### Contrast\n\n| Surface | Dark | Light |\n|---|---|---|\n| Card | 5.1 | 4.8 |\n| Column | 4.6 | 4.0 |\n\n- [x] Check dark\n- [ ] Check light',
  links: [{ type: 'relates_to', target: 'ISS-012' }, { type: 'references', target: 'T-103' }],
  blockers: ['Waiting on the design review of the column ground'],
  spec_review: { verdict: 'warn', codex_note: 'Requirement 3 has no test: "no shadows" needs a style-rule check.' },
  lane: 'full',
  gates: { 'spec-review': { verdict: 'pass' }, 'plan-review': { verdict: 'warn' } },
  gate_state: 'review-gate:pending',
  merge_status: { develop: { merged_at: '2026-09-30T12:00:00Z', merge_commit: '2774d25c0ffee' } },
  activity: ['2026-09-30 16:20 checkpoint: cards done', '2026-09-30 11:05 spec-review: warn', '2026-09-29 08:30 started'],
};

export const DONE_TASK = {
  id: 'T-101', title: 'Token foundation and theme switch', status: 'done', priority: 'high', epic: 'viewer', phase: 'P1',
  estimate: 'L', depends_on: [], release: '7.1.0',
  created: '2026-09-20T09:00:00Z', started: '2026-09-21T09:00:00Z', completed: '2026-09-27T17:45:00Z',
  specification: 'Tokens come from the design system snapshot; both themes switch without a reload.',
  plan: '1. Generate tokens\n2. Wire the toggle',
  patchnote: 'The viewer takes the **Reality Reprojection** tokens and gains a light theme.\n\n- Dark is the default\n- The toggle remembers the choice',
  lane: 'standard', gates: { 'design-review': { verdict: 'pass' }, 'review-gate': { verdict: 'pass' } },
  merge_status: { develop: { merged_at: '2026-09-27T18:00:00Z', merge_commit: 'abfb1b9c0ffee' }, stage: { merged_at: '2026-09-28T09:00:00Z' } },
};

export const REVIEW_TASK = {
  id: 'T-107', title: 'Review the cutover checklist', status: 'in-review', priority: 'medium', epic: 'store', phase: 'P1',
  estimate: '2d', depends_on: [], branch: 'feat/cutover-checklist',
  created: '2026-09-25T10:00:00Z', started: '2026-09-26T10:00:00Z',
  specification: 'The checklist names every step of the cutover and who signs each one off.',
  review_instructions: '1. Run the cutover against the scratch store.\n2. Compare the row counts.\n3. Confirm the rollback step restores the old layout.',
  lane: 'full',
  gates: { 'spec-review': { verdict: 'pass' }, 'plan-review': { skipped: true }, 'review-gate': { verdict: 'fail' } },
  gate_state: 'review-gate:fail',
};

// Every optional field null: nothing to show but the title, the markers and when it was created.
export const EMPTY_TASK = {
  id: 'T-104', title: 'Sessions timeline: replace the legacy palette', status: 'todo', priority: 'medium', epic: 'viewer', phase: null,
  estimate: null, stage: null, sub_repo: null, branch: null, worktree: null, release: null, depends_on: null, docs: null, anchors: null,
  links: null, blockers: null, description: null, specification: null, plan: null, notes: null, review_instructions: null, patchnote: null,
  created: '2026-09-30T09:00:00Z', started: null, completed: null,
};

// Review focus 5: a title with no break in it, a plan of thousands of lines, dozens of dependencies.
const LONG_IDS = Array.from({ length: 40 }, (_, i) => `T-${300 + i}`);
export const LONG_TASK = {
  id: 'T-105', title: 'x'.repeat(140), status: 'todo', priority: 'high', epic: 'store', phase: 'P1', depends_on: LONG_IDS,
  branch: 'feat/' + 'very-long-branch-name-'.repeat(6) + 'end',
  created: '2026-09-22T09:00:00Z',
  specification: 'The rebuild is incremental.',
  plan: Array.from({ length: 5000 }, (_, i) => `${i + 1}. step ${i + 1} of the plan`).join('\n'),
};
export const LONG_RELATED = {
  dependencies: LONG_IDS.map((id, i) => ({ id, title: `Dependency ${i + 1} with a title long enough to wrap onto a second line in the rail`, status: i % 3 ? 'todo' : 'done' })),
};

// The payload of GET /api/epic/<id>.
export const EPIC = {
  id: 'viewer', name: 'Viewer re-skin', status: 'active', phase: 'P1', design_status: 'locked',
  description: 'Every screen takes the **Reality Reprojection** system in dark and light.',
  done_when: 'All screens pass the audit in both themes.',
  stats: { total: 4, done: 1, archived: 0 },
  attention: [{ id: 'T-102', why: 'critical and in progress' }],
  tasks: BOARD.tasks.filter((t) => t.epic === 'viewer'),
};

// ---- 3b: Table + Epics + Epic detail -----------------------------------------------------------------------------------

// Plan 3's review focus 1: a real backlog's volume — 27 epics, 230 tasks, ids up to 27 characters (slug ids every
// tenth task, one very long one), every third title 120 characters, one task in ten archived, long branches.
const LONG_WORDS = ['store', 'viewer', 'handover', 'linear', 'gate', 'index', 'board', 'theme', 'sync', 'hooks'];
const LONG_SENTENCE = 'keep the whole sentence readable when the table has to cut it short ';
export const LONG_IDS_BOARD = (() => {
  const epics = Array.from({ length: 27 }, (_, i) => (i === 26
    ? { id: 'database-native-tracking', name: 'Database-native tracking: the SQLite store becomes the one authority', status: 'active', phase: 'P1' }
    : { id: `epic-${String(i + 1).padStart(2, '0')}`, name: `${LONG_WORDS[i % 10]} work ${i + 1}`, status: ['active', 'active', 'planned', 'done', 'archived'][i % 5], phase: 'P1' }));
  const statuses = ['todo', 'in-progress', 'in-review', 'blocked', 'done', 'done', 'todo', 'done', 'todo', 'archived'];
  const tasks = Array.from({ length: 230 }, (_, i) => {
    const id = i === 229 ? 'database-native-n17-cutover' : i % 10 === 9 ? `v3-polish-${String(i).padStart(3, '0')}` : `T-${1000 + i}`;
    const base = `${LONG_WORDS[i % 10]} task ${i + 1}`;
    return {
      id, title: i % 3 === 0 ? `${base} — ${LONG_SENTENCE.repeat(3)}`.slice(0, 120) : base,
      status: statuses[i % 10], priority: ['critical', 'high', 'medium', 'low'][i % 4], epic: epics[i % 27].id, phase: 'P1',
      area: ['viewer-ui', 'store', 'docs', 'hooks'][i % 4], estimate: ['S', 'M', 'L', '3d'][i % 4], depends_on: [],
      ...(i % 5 === 0 ? { branch: `feat/${id}-${'long-branch-name-'.repeat(3)}end` } : {}),
      ...(i % 4 === 0 ? { started: `2026-09-2${i % 9}T09:00:00Z` } : {}),
    };
  });
  return { revision: 'r-long', cursor: 'c-long', meta: { project: 'Long fixture' },
    phases: [{ id: 'P1', name: 'Foundation', status: 'active' }], epics, tasks };
})();

// GET /api/epic/<id> for an epic of `board`, built as backlog_server.py _epic_full_from builds it.
export function epicPayload(board, id, extra = {}) {
  const epic = board.epics.find((e) => e.id === id) ?? { id };
  const tasks = board.tasks.filter((t) => t.epic === id);
  const count = (s) => tasks.filter((t) => (t.status || 'todo') === s).length;
  const stats = { total: tasks.length, done: count('done'), 'in-progress': count('in-progress'), 'in-review': count('in-review'),
    todo: count('todo'), blocked: count('blocked'), archived: count('archived') };
  stats.closeable = stats.total > 0 && stats.done + stats.archived === stats.total;
  return {
    description: '', docs: {}, components: {}, design_status: 'exploring', done_when: '', area: null, ...epic,
    stats, closeable: stats.closeable, component_rollup: {},
    attention: tasks.filter((t) => t.status === 'blocked').map((t) => ({ id: t.id, title: t.title, blocked: true, why: t.blockers || '' })),
    tasks: tasks.map((t) => ({ id: t.id, title: t.title, status: t.status || 'todo', component: t.component ?? null,
      priority: t.priority, phase: t.phase, design_change: t.design_change ?? null })),
    ...extra,
  };
}
