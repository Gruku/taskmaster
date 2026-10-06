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

// The Dashboard's continuity band: seven open handovers (five fit the Resume rail, two are "older"), two tasks to
// review, one open decision, and a task, an issue and an idea to clean up.
const continuityDaysAgo = (d) => ago(d * 24);
const HANDOVER_TLDR = ['Cards done, columns next', 'Columns re-skinned', 'Detail modal header', 'Topbar row 1 at 390px',
  'Chips overflow row', 'Popover stacking ladder', 'Theme toggle saved'];
export const CONTINUITY = {
  items: [
    ...HANDOVER_TLDR.map((title, i) => ({
      id: `2026-10-05-r${i + 1}`, type: 'handover', title, action_class: 'resume', age_days: i + 1, timestamp: continuityDaysAgo(i + 1),
      next: 'Pick up the next screen', where: 'rr3/e',
    })),
    { id: 'T-107', type: 'task', title: 'Review the cutover checklist', action_class: 'review', age_days: 1, timestamp: continuityDaysAgo(1),
      next: 'in-review', where: 'store' },
    { id: 'T-102', type: 'task', title: 'Re-skin the Kanban cards and columns', action_class: 'review', age_days: 2, timestamp: continuityDaysAgo(2),
      next: 'in-review', where: 'viewer' },
    { id: 'DEC-001', type: 'decision', title: 'Land the cutover', action_class: 'decide', age_days: 1, timestamp: continuityDaysAgo(1),
      next: 'rec: Merge develop first', where: 'T-107' },
    { id: 'T-106', type: 'task', title: 'Quarantined rows must not force a projection scan', action_class: 'clean-up', age_days: 9,
      timestamp: continuityDaysAgo(9), next: 'in-progress', where: 'store' },
    { id: 'ISS-012', type: 'issue', title: 'Light theme pills fail contrast', action_class: 'clean-up', age_days: 15,
      timestamp: continuityDaysAgo(15), next: 'P2 · open', where: 'viewer' },
    { id: 'IDEA-7', type: 'idea', title: 'Pin a handover to the desk', action_class: 'clean-up', age_days: 8, timestamp: continuityDaysAgo(8),
      next: 'brainstorm', where: 'brainstorm' },
  ],
};

export const DECISION = {
  id: 'DEC-001', title: 'Land the cutover', options: ['Push the MR', 'Merge develop first', 'Hold'], recommendation: 2, body: '',
};

// Settings: the prefs alone; `prefs` merges any other stored values (card_density, ui.detail_view_mode) into them.
// Its content is loaded when `.set-control[role="group"] .tm-segmented > button[data-key="system"]` is on the page.
export const settingsMocks = ({ theme = 'dark', ...prefs } = {}) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {}, ...prefs },
});

// ---- 3b Task 5: Epic detail ------------------------------------------------------------------------------------------

// Fixture epic used for architecture-map e2e tests (copied verbatim from epic-detail.spec.js).
// Three components with two edges: ingest→thumb, thumb→cdn.
// One unassigned task so the trailing _unassigned bucket is also rendered.
export const ARCH_EPIC_FIXTURE = {
  id: 'arch-test',
  name: 'Architecture Test Epic',
  status: 'active',
  design_status: 'exploring',
  description: 'Fixture epic for architecture-map e2e tests.',
  docs: {},
  stats: { total: 4, done: 1 },
  components: {
    ingest: { title: 'Ingest', after: [] },
    thumb:  { title: 'Thumbnailer', after: ['ingest'] },
    cdn:    { title: 'CDN', after: ['thumb'] },
  },
  component_rollup: {
    ingest: { status: 'done',        total: 1, done: 1 },
    thumb:  { status: 'in-progress', total: 2, done: 0 },
    cdn:    { status: 'todo',        total: 0, done: 0 },
    _unassigned: { status: 'todo', total: 1, done: 0 },
  },
  attention: [],
  tasks: [
    { id: 'ING-1', title: 'Decode frames', status: 'done',    component: 'ingest', priority: 'high' },
    { id: 'THM-1', title: 'Resize',        status: 'todo',    component: 'thumb',  priority: 'medium' },
    { id: 'THM-2', title: 'Watermark',     status: 'todo',    component: 'thumb',  priority: 'low' },
    { id: 'X-1',   title: 'Loose task',    status: 'todo',    component: null,     priority: 'low' },
  ],
};

// The Epic detail route table: a full epic (`viewer`), the architecture map (`arch-test`), no tasks (`empty`), a real
// backlog's volume (`big`), a 404 (`nope`) and a server failure whose raw text must never reach the page (`broken`).
export function epicDetailMocks({ theme = 'dark' } = {}) {
  return {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
    '/api/epic/viewer': epicPayload(BOARD, 'viewer', {
      design_status: 'locked',
      description: 'Every screen takes the **Reality Reprojection** system.',
      done_when: 'All screens pass the audit.',
      docs: { spec: 'docs/specs/viewer.md' },
      attention: [{ id: 'T-102', title: 'Re-skin the Kanban cards and columns', blocked: false, why: 'critical and in progress' }],
    }),
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK),
    '/api/epic/arch-test': ARCH_EPIC_FIXTURE,
    '/api/epic/empty': epicPayload(BOARD, 'empty', { id: 'empty', name: 'Empty' }),
    '/api/epic/nope': { status: 404, json: { ok: false, error: 'epic not found' } },
    '/api/epic/broken': { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } },
    '/api/epic/big': epicPayload({ ...LONG_IDS_BOARD, epics: [{ id: 'big', name: 'Big epic', status: 'active' }],
      tasks: LONG_IDS_BOARD.tasks.slice(0, 60).map((t) => ({ ...t, epic: 'big' })) }, 'big'),
  };
}

// GET /api/issues: the server adds severity_label and aging.
const issue = (id, title, status, severity, extra = {}) => ({ id, title, status, severity,
  severity_label: { P0: 'Critical', P1: 'High', P2: 'Medium', P3: 'Low' }[severity], aging: { percent: 10, tier: 'Fresh' }, ...extra });
export const LIST_ISSUES = [
  issue('ISS-001', 'Board poll redraws every card on a quiet tick', 'investigating', 'P1', { component: 'viewer',
    location: ['viewer/js/main.js:121'], related_tasks: ['T-102'], discovered: daysAgo(50), aging: { percent: 90, tier: 'Stale' },
    evidence: Array.from({ length: 8 }, (_, i) => `Line ${i + 1}: the poll answered 200 with an unchanged revision and the board still repainted.`).join(' ') }),
  issue('ISS-002', 'Writer mutex waits without a bound', 'open', 'P0', { component: 'store', related_tasks: ['T-105', 'T-106'],
    promoted_from: ['B-029'], evidence: 'Six seconds per write on a large backlog.', discovered: daysAgo(10) }),
  issue('ISS-003', 'Light theme pills fail contrast', 'open', 'P2', { component: 'viewer', evidence: 'axe: 33 nodes.', discovered: daysAgo(4) }),
  issue('ISS-004', 'Inbox triage skips archived items', 'open', 'P3', { discovered: daysAgo(3) }),
  issue('ISS-005', 'Legacy mirror written after cutover', 'fixed', 'P1', { resolved: daysAgo(6) }),
  issue('ISS-006', 'Search hint shows the Mac glyph on Windows', 'wontfix', 'P3', { resolved: daysAgo(12) }),
  issue('ISS-007', 'Duplicate of the poll repaint', 'duplicate', 'P2', { resolved: daysAgo(1) }),
];
const ISSUE_STATUSES = ['investigating', 'open', 'open', 'open', 'fixed', 'wontfix', 'duplicate', 'investigating'];
export const LONG_ISSUES = Array.from({ length: 24 }, (_, i) => issue(`ISS-${1201 + i}`, longText(`Issue ${1201 + i}`, { unbroken: i === 3 }),
  ISSUE_STATUSES[i % 8], `P${i % 4}`, { component: ['viewer', 'store', 'sync'][i % 3], related_tasks: [`T-${1234 + i}`],
    location: [`viewer/js/screens/a-rather-long-module-name-${i}.js:${100 + i}`], evidence: longText(`Evidence ${i}`).repeat(3),
    discovered: daysAgo(5 + i), resolved: ISSUE_STATUSES[i % 8] === 'open' || ISSUE_STATUSES[i % 8] === 'investigating' ? undefined : daysAgo(i) }));
// The table plan 4's a11y gate reuses for #/issues; loaded when `.issues-col .issue-card` is visible.
export const issuesMocks = ({ theme = 'dark' } = {}) => ({ '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/issues': { issues: LIST_ISSUES }, '/api/board': BOARD, '/api/backlog': BOARD,
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1', RICH_RELATED) });
// ── Plan 3e: Sessions ──
// The table plan 4's a11y gate reuses for #/sessions; loaded when `.ho-child[data-handover-id="2026-07-13-m1-shipped"]`
// is visible.
export const sessionsMocks = ({ theme = 'dark' } = {}) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/sessions': SESSIONS,
  '/api/threads': THREADS,
  ...Object.fromEntries(Object.entries(SESSION_DETAILS).map(([id, d]) => [`/api/sessions/${id}`, d])),
  '/api/board': BOARD, '/api/backlog': BOARD,
});

// Review focus 1 for Sessions: `n` sessions a day apart, each id `thread-` + 70 slug characters + its index, a
// 120-character tldr and two handovers. Deterministic — no Date.now().
export function manySessions(n) {
  const slug = 'long-slug-'.repeat(7);
  const tldr = (lead) => `${lead} — the summary keeps going past the edge of a phone screen and then on again`.repeat(2).slice(0, 120);
  const sessions = Array.from({ length: n }, (_, i) => {
    const start = new Date(Date.UTC(2026, 7, 1, 9) + i * 86_400_000);
    const day = start.toISOString().slice(0, 10);
    const id = `thread-${slug}${i}`;
    const handovers = ['a', 'b'].map((k, j) => ({
      id: `${day}-${slug}${i}-${k}`, status: j ? 'closed' : 'open', viewer_kind: j ? 'checkpoint' : 'mid-task',
      tldr: tldr(`Handover ${i}${k}`),
    }));
    return {
      id, kind: 'thread', status: 'open', start: start.toISOString(), end: new Date(+start + 3_600_000).toISOString(),
      duration: 3600, time_resolution: 'full', handover_ids: handovers.map((ho) => ho.id), handovers,
      task_ids: [`T-${1234 + i}`], tldr: tldr(`Session ${i}`), next_action: '',
    };
  });
  const details = Object.fromEntries(sessions.map((s) => [s.id, {
    session: s,
    handovers: s.handovers.map((ho) => sessionHandover(ho.id, ho.viewer_kind, ho.status, ho.tldr, s.start)),
    task_ids: s.task_ids,
  }]));
  return { sessions, details };
}

// The issue detail page (plan 3c Task 7): one rich issue, a fixed one it duplicates, and one too long for a phone.
export const ISSUE = { id: 'ISS-012', title: 'Card edge vanishes on the light page ground', severity: 'P1', severity_label: 'High', status: 'investigating', discovered: '2026-08-01T09:00:00Z', evidence: 'Seen on **three** laptops in light theme.', repro: ['Open the board in light', 'Look at a card edge'], impact: 'Cards blur into the column; `--card-bg` sits too close to `--col-bg`.', summary: '## Notes\n\nTracked in T-102.', location: ['viewer/css/screens/kanban.css:87', 'viewer/css/tokens.css'], links: [{ type: 'relates_to', target: 'T-102' }, { type: 'duplicate_of', target: 'ISS-009' }] };
export const ISSUES = { issues: [ISSUE, { id: 'ISS-009', title: 'Light card edge', severity: 'P2', status: 'fixed', discovered: '2026-07-01T09:00:00Z', resolved: '2026-07-10T09:00:00Z' }] };
export const LONG_ISSUE = { ...ISSUE, id: 'ISS-1234', title: 'x'.repeat(120), location: ['viewer/' + 'deeply/nested/'.repeat(14) + 'file.css:1'] };
// ── Plan 3e: Archived ──
// BOARD plus `n` archived tasks T-1001… spread over the two fixture epics, an epic the board does not list (`legacy`)
// and no epic; every fifth title is 120 characters long; T-1002 was superseded, T-1003 is a duplicate. Deterministic.
export function archivedBoard(n) {
  const epics = ['viewer', 'store', 'legacy', undefined];
  const long = (i) => `Archived task ${i} whose title runs well past the edge of a phone screen and keeps on going until it `.repeat(2).slice(0, 120);
  const reasons = { 1: 'superseded by T-140', 2: 'duplicate' };
  const archived = Array.from({ length: n }, (_, i) => task(`T-${1001 + i}`,
    i % 5 === 4 ? long(1001 + i) : `Archived task ${1001 + i}`, 'archived', 'medium', epics[i % 4],
    reasons[i] ? { archived_reason: reasons[i] } : {}));
  return { ...BOARD, tasks: [...BOARD.tasks, ...archived] };
}

// The table plan 4's a11y gate reuses for #/archived; loaded when `.arch-row[data-task-id="T-1001"]` is visible.
export const archivedMocks = ({ theme = 'dark' } = {}) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/board': archivedBoard(40), '/api/backlog': archivedBoard(40),
  '/api/task/T-1001/detail': taskDetail({ ...EMPTY_TASK, id: 'T-1001', title: 'Archived task 1001', status: 'archived' }),
});
