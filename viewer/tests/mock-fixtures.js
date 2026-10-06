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
// One active epic (`LONG_CLOSEABLE_EPIC`) has every task done or archived, so the "Closeable" path meets long data too.
export const LONG_CLOSEABLE_EPIC = 'epic-02';
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
      status: epics[i % 27].id === LONG_CLOSEABLE_EPIC ? (i % 2 ? 'done' : 'archived') : statuses[i % 10], priority: ['critical', 'high', 'medium', 'low'][i % 4], epic: epics[i % 27].id, phase: 'P1',
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
    // Blocked tasks, and any other task that names blockers (`blocked: false`), in task order.
    attention: tasks.filter((t) => t.status === 'blocked' || t.blockers)
      .map((t) => (t.status === 'blocked' ? { id: t.id, title: t.title, blocked: true, why: t.blockers || '' }
        : { id: t.id, title: t.title, blocked: false, why: t.blockers })),
    tasks: tasks.map((t) => ({ id: t.id, title: t.title, status: t.status || 'todo', component: t.component ?? null,
      priority: t.priority, phase: t.phase, design_change: t.design_change ?? null })),
    ...extra,
  };
}

// ── Sessions ──
// GET /api/threads: one open thread, one parked.
export const THREADS = [
  { name: 'team-relayout', status: 'open', tldr: 'M1 shipped', next_action: 'start M2',
    task_ids: ['T-1'], branch: 'feat/relayout', last_touched: '2026-07-13T10:00:00+00:00', staleness_days: 0 },
  { name: 'guard-hooks-polish', status: 'parked', tldr: 'awaiting review', next_action: '',
    task_ids: [], branch: '', last_touched: '2026-07-10T10:00:00+00:00', staleness_days: 3 },
];

// GET /api/sessions: two threads, three handovers in all, one of each status.
export const SESSIONS = [
  { id: 'team-relayout', kind: 'thread', status: 'open',
    start: '2026-07-12T09:00:00+00:00', end: '2026-07-13T10:00:00+00:00', duration: 90000, time_resolution: 'full',
    handover_ids: ['2026-07-12-scope', '2026-07-13-m1-shipped'],
    handovers: [
      { id: '2026-07-12-scope', status: 'closed', viewer_kind: 'mid-task', tldr: 'Scope the relayout' },
      { id: '2026-07-13-m1-shipped', status: 'open', viewer_kind: 'checkpoint', tldr: 'M1 shipped' },
    ],
    task_ids: ['T-102'], tldr: 'M1 shipped', next_action: 'start M2' },
  { id: 'guard-hooks-polish', kind: 'thread', status: 'closed',
    start: '2026-07-10T10:00:00+00:00', end: '2026-07-10T10:00:00+00:00', duration: 0, time_resolution: 'full',
    handover_ids: ['2026-07-10-hooks-old'],
    handovers: [{ id: '2026-07-10-hooks-old', status: 'superseded', viewer_kind: 'wrap', tldr: 'Old hook plan' }],
    task_ids: ['T-102'], tldr: 'Old hook plan', next_action: '' },
];

// Ten files, one of them a 140-character path, so the rail's list is cut at eight and its long line is truncated.
const FILES_TOUCHED = [
  'viewer/js/screens/sessions.js', 'viewer/js/components/right-rail.js', 'viewer/css/components/right-rail.css',
  'viewer/css/components/handover-status.css', 'viewer/index.html',
  `viewer/tests/${'deeply-nested-fixture-directory/'.repeat(3)}${'x'.repeat(140 - 13 - 32 * 3 - 8)}.spec.js`,
  'viewer/tests/unit/right-rail.test.js', 'viewer/tests/mock-fixtures.js', 'docs/plans/3e.md', 'CHANGELOG.md',
];
const sessionHandover = (id, viewer_kind, status, tldr, created) => ({
  id, viewer_kind, status, tldr, created,
  done_items: ['Rail rebuilt from nodes', 'Status pill says its word'],
  open_items: ['Rows become buttons', 'Phone layout check'],
  task_ids: ['T-102'], files_touched: FILES_TOUCHED,
  next_action: `Continue from ${id}`,
  resume_prompt: `Resume ${id}: read the plan, then pick up the open items.`,
});

// GET /api/sessions/<id>: the session and its handovers in full.
export const SESSION_DETAILS = Object.fromEntries(SESSIONS.map((s) => [s.id, {
  session: s,
  handovers: s.handovers.map((ho) => sessionHandover(ho.id, ho.viewer_kind, ho.status, ho.tldr,
    `${ho.id.slice(0, 10)}T10:00:00+00:00`)),
  task_ids: s.task_ids,
}]));

// Review focus 1: a board at real data volume (27 epics, 230 tasks, IDs up to T-1234, 120-character titles) for the
// phone-width checks. Deterministic — no Date.now().
const PHASE_WORDS = ['Foundation and tokens', 'Shell and navigation', 'Shared components and modals',
  'Screens re-skinned onto the shared parts', 'Data fixes and the bug route', 'Cleanup and the full re-audit',
  'Release and the changelog'];
const EPIC_WORDS = ['Viewer re-skin onto RR', 'Native store cutover', 'Linear sync retries', 'Handover quotes', 'Guard hooks',
  'Status line', 'Feedback inbox', 'Agent tool-use evals', 'Release 7.2 notes'];
const STATUS_CYCLE = ['todo', 'todo', 'in-progress', 'todo', 'done', 'in-review', 'todo', 'blocked', 'in-progress', 'done'];
export function longBoard() {
  const phases = [
    { id: 'P0', name: 'Phase 0: The prototype that was dropped before anyone used it', status: 'archived',
      archived_reason: 'superseded', order: 0 },
    ...PHASE_WORDS.map((words, k) => {
      const n = k + 1;
      return { id: `P${n}`, name: `Phase ${n}: ${words}`, status: n <= 3 ? 'done' : n === 4 ? 'active' : 'planned', order: n };
    }),
  ];
  const epics = Array.from({ length: 27 }, (_, k) => {
    const n = k + 1;
    const nn = String(n).padStart(2, '0');
    return { id: `epic-${nn}`, name: `Epic ${nn}: ${EPIC_WORDS[(n - 1) % 9]}`, status: n <= 22 ? 'active' : n <= 25 ? 'done' : 'archived' };
  });
  const tasks = Array.from({ length: 230 }, (_, i) => {
    const id = `T-${1005 + i}`;
    const status = STATUS_CYCLE[i % 10];
    const phase = i % 23 === 0 ? undefined : `P${(i % 7) + 1}`;
    const estimate = ['S', 'M', 'L', '3', undefined][i % 5];
    return {
      id,
      title: `Task ${i + 1}: make every column, chip and row cope with a title that runs past one line`.padEnd(120, ' and more').slice(0, 120),
      status,
      priority: ['critical', 'high', 'medium', 'low', 'medium'][i % 5],
      epic: `epic-${String((i % 27) + 1).padStart(2, '0')}`,
      ...(phase ? { phase } : {}),
      ...(estimate ? { estimate } : {}),
      ...(i % 5 === 0 ? { branch: `feat/${id}-a-branch-name-long-enough-to-be-cut-on-a-card` } : {}),
      ...(i >= 10 && i <= 13 ? { bundle: 'long-bundle-slug-alpha' } : {}),
      depends_on: [],
      created: '2026-09-01T09:00:00Z',
      ...(status !== 'todo' ? { started: '2026-09-20T09:00:00Z' } : {}),
    };
  });
  return { revision: 'long-r1', cursor: 'c1', meta: { project: 'Long fixture' }, phases, epics, tasks };
}

// ── Dashboard ──
// A Claude note eight paragraphs long, ending in a 300-character link, so the clamp, the fade and "Show more" show.
export const LONG_NOTE = {
  id: 'NOTE-099', author: 'claude', pinned: false, created: ago(2),
  body: Array.from({ length: 8 }, (_, i) => `Paragraph ${i + 1}: `
    + 'The cutover moves every row into the native store and checks the counts. '.repeat(7)).join('\n\n')
    + '\n\nhttps://example.com/' + 'a'.repeat(300),
};

// The Dashboard with notes and an empty continuity band.
export const deskMocks = ({ theme = 'dark', notes = NOTES } = {}) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/notes': notes,
  '/api/continuity': { items: [] },
});

// The Dashboard's summary strip: two open issues, one investigating, one fixed (3 open); two open bugs, one fixed.
export const ISSUES_LIST = {
  issues: [
    { id: 'ISS-011', title: 'Board poll drops a delta under load', status: 'open', severity: 'high', created: ago(30) },
    { id: 'ISS-012', title: 'Light theme pills fail contrast', status: 'open', severity: 'medium', created: ago(20) },
    { id: 'ISS-013', title: 'Writer mutex held across the projection scan', status: 'investigating', severity: 'critical', created: ago(10) },
    { id: 'ISS-009', title: 'Handover list ignores the archive cap', status: 'fixed', severity: 'low', created: ago(90), resolved: ago(40) },
  ],
};

export const BUGS_LIST = [
  { id: 'B-031', title: 'Card edge vanishes at 390px', status: 'open', found_in: 'T-102', discovered: ago(6) },
  { id: 'B-032', title: 'Note fade covers the last line', status: 'open', found_in: 'T-103', discovered: ago(4) },
  { id: 'B-027', title: 'Sessions timeline keeps the legacy palette', status: 'fixed', found_in: 'T-104', discovered: ago(80) },
];

// The Dashboard with a board, issues and bugs behind its summary strip; `extra` overrides any route.
export const summaryMocks = ({ theme = 'dark', ...extra } = {}) => ({
  ...deskMocks({ theme }),
  '/api/board': BOARD, '/api/backlog': BOARD, '/api/issues': ISSUES_LIST, '/api/bugs': BUGS_LIST,
  ...extra,
});

// ── Plan 3d: list screens ──
const daysAgo = (d) => new Date(Date.now() - d * 86_400_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
// 120 characters with spaces in them, so a title wraps; `unbroken` gives one long word that must not push the page wide.
const longText = (lead, { unbroken = false } = {}) =>
  (unbroken ? lead + '-' + 'x'.repeat(120) : `${lead} — the words keep going past the edge of a phone screen and on again`.repeat(2)).slice(0, 120);
// GET /api/bugs?include_archive=1: every bug, the archived ones flagged.
export const LIST_BUGS = [
  { id: 'B-031', title: 'Card edge vanishes on the light ground', status: 'open', severity: 'P1', found_in: 'T-102', components: ['viewer'], discovered: daysAgo(2) },
  { id: 'B-030', title: 'Phase strip clips the current phase name', status: 'open', found_in: 'T-102', discovered: daysAgo(3) },
  { id: 'B-029', title: 'Store write hangs for six seconds on a large backlog', status: 'fixed', severity: 'P0', components: ['store'], discovered: daysAgo(16) },
  { id: 'B-028', title: 'Handover quote loses its heading', status: 'shelved', severity: 'P3', discovered: daysAgo(18) },
  { id: 'B-027', title: 'Inbox message archived twice', status: 'adopted', severity: 'P2', adopted_into: 'T-118', discovered: daysAgo(21) },
  { id: 'B-026', title: 'Legacy mirror written after cutover', status: 'fixed', archived: true, discovered: daysAgo(35) },
];
const BUG_STATUSES = ['open', 'open', 'shelved', 'fixed', 'adopted', 'promoted'];
export const LONG_BUGS = Array.from({ length: 23 }, (_, i) => ({
  id: `B-${1201 + i}`, title: longText(`Bug ${1201 + i}`, { unbroken: i === 4 }), status: BUG_STATUSES[i % 6],
  severity: i % 5 === 4 ? undefined : `P${i % 4}`, found_in: `T-${1234 + i}`, components: ['viewer', 'store'], discovered: daysAgo(i + 1),
}));
// The table plan 4's a11y gate reuses for #/bugs; loaded when `.bugs__list .bug-row` is visible.
export const bugsMocks = ({ theme = 'dark' } = {}) => ({ '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/bugs': LIST_BUGS, '/api/board': BOARD, '/api/backlog': BOARD,
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1', RICH_RELATED) });
