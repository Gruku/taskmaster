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
