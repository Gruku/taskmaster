// User intent: built-in sample data so taskmaster-mods can be seen, screenshotted and tuned without a Taskmaster backlog
// (userConfig source = demo); one task refuses sign-off exactly as the server words it.
import type {
  TmHandover,
  TmHandoverList,
  TmHandoverSummary,
  TmLinkedTask,
  TmPhase,
  TmPhasePane,
  TmRefs,
  TmScan,
  TmScannedSession,
  TmSessionHistory,
  TmSnapshot,
  TmTaskDetail,
} from '../types'
import { handoverPath } from './model'
import { NO_TASK } from './phases'

/**
 * The demo snapshot's `reason`: how register.tsx tells demo data from real data when the source setting changes. It carries
 * the seed's version: bump DEMO_SEED whenever the demo data's shape changes, so a seed an older module left in $.state across
 * a reload is rebuilt rather than drawn (2: handovers gained branch / taskIds, summaries were added; 3: handovers gained
 * thread, for the copy block's Resume line; 4: phases gained dates and per-pane counts, seeded with the snapshot; 5: the
 * session was resumed from a handover and its tasks carry their phase, for the band and the Session tab; 7: the handovers
 * pane's own list, with closed handovers and linked tasks, seeded beside the snapshot; 8: the restore pane's sessions, refs
 * and brief).
 */
export const DEMO_SEED = 8
export const DEMO_REASON = `demo:${DEMO_SEED}`

/** Any demo seed, of any version (tm mode never draws one). */
export const isDemoSnapshot = (s: TmSnapshot | null): boolean => s !== null && (s.reason === 'demo' || s.reason.startsWith('demo:'))
/** A demo seed of this module's version: demo mode keeps it (the flows may have changed it); any other is rebuilt. */
export const isCurrentDemo = (s: TmSnapshot | null): boolean => s !== null && s.reason === DEMO_REASON
/** Demo's phases (review counts); the demo queue itself is not filtered by them. */
export const DEMO_PHASES: readonly TmPhase[] = [
  { id: 'release-2', status: 'planned', order: 3, count: 2, name: 'Release 2.0 — Demo scope', date: '' },
  { id: 'patch-1-9', status: 'active', order: 2, count: 3, name: 'Patch 1.9 — Demo scope', date: '2026-10-06' },
  { id: 'patch-1-8', status: 'done', order: 1, count: 0, name: 'Patch 1.8 — Demo scope', date: '2026-10-05' },
]
/** Demo handovers per phase through their linked tasks (DEMO_TASKS), open and closed; NO_TASK: those linking none. */
const DEMO_HANDOVER_COUNTS: Readonly<Record<string, { readonly open: number; readonly closed: number }>> = {
  'patch-1-9': { open: 2, closed: 0 },
  'release-2': { open: 1, closed: 0 },
  'patch-1-8': { open: 0, closed: 1 },
  [NO_TASK]: { open: 2, closed: 1 },
}

/** Each pane's demo phases: review's counts; handovers' open and closed handovers plus a no task tab; restore's are counted later (0). */
export function demoPhases(pane: TmPhasePane): readonly TmPhase[] {
  if (pane === 'review') return DEMO_PHASES
  const of = (id: string) => (pane === 'handovers' ? (DEMO_HANDOVER_COUNTS[id] ?? { open: 0, closed: 0 }) : { open: 0, closed: 0 })
  const counted = DEMO_PHASES.map(p => ({ ...p, count: of(p.id).open, closed: of(p.id).closed }))
  return [...counted, { id: NO_TASK, status: 'none', order: 0, count: of(NO_TASK).open, closed: of(NO_TASK).closed, name: 'no task', date: '' }]
}
export const DEMO_ROOT = 'C:\\Users\\demo\\project'
/** The demo session was resumed from this handover (seeded into `resumedFrom`): the band and Session tab show it. */
export const DEMO_RESUMED_FROM = '2026-10-04-audit-fixes-landed'
export const DEMO_REFUSING_ID = 'tm-audit-031'
export const DEMO_REFUSAL =
  'Cannot complete `tm-audit-031` — outstanding gates for lane `full`: review-gate. Record each (backlog_record_gate) or skip it (backlog_skip_gate).'

const HOUR = 3_600_000

export const DEMO_DETAILS: Readonly<Record<string, TmTaskDetail>> = {
  'unified-chat-022': {
    id: 'unified-chat-022',
    title: 'Unified chat pre-build gets the full supervisor toolset; cookbook owns the order',
    status: 'in-review',
    priority: 'critical',
    lane: 'full',
    gateState: 'review-gate:pass',
    branch: '',
    phase: 'patch-1-9',
    humanAction:
      'Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):\nfirst unified message stays on intent/brief and does not build; second message builds with the full toolset.',
    notes: ['Pre-build now hands the supervisor its full toolset; the cookbook owns the step order.'],
    links: ['docs/specs/unified-chat.md'],
    pr: 'https://github.com/demo/project/pull/412',
  },
  'tm-audit-031': {
    id: 'tm-audit-031',
    title: 'Handover auto-supersede keeps the newest per thread',
    status: 'in-review',
    priority: 'high',
    lane: 'full',
    gateState: 'spec-review:pass',
    branch: 'feat/tm-audit-031',
    phase: 'patch-1-9',
    humanAction: 'Open the viewer and confirm one open handover per thread.',
  },
  'docs-012': {
    id: 'docs-012',
    title: 'README mentions the review queue',
    status: 'in-review',
    priority: 'low',
    lane: '',
    gateState: '',
    branch: '',
    phase: 'release-2',
    humanAction: 'Read the README section and say if it reads right.',
  },
}

/** Decisions and blockers per demo handover, seeded like DEMO_DETAILS (tm mode reads them when a summary is expanded). */
export const DEMO_SUMMARIES: Readonly<Record<string, TmHandoverSummary>> = {
  '2026-10-05-shipped-unified-chat-022': {
    decisions: ['The cookbook owns the build order, not the pre-build'],
    blockers: ['Needs unifiedChatGenerate + unifiedChatBuild on dev'],
  },
  '2026-10-04-audit-fixes-landed': {
    decisions: ['Audit fixes ride the native branch; no backport to master'],
    blockers: ['tm-audit-030 merge not recorded yet'],
  },
  '2026-10-03-viewer-reskin-2a': { decisions: ['Plan 2b re-skins the board before the detail drawer'], blockers: [] },
  '2026-10-02-store-read-hang': {
    decisions: ['Fix quarantined-row scans before the mutex split'],
    blockers: ['Needs a CodeMaestro-sized store to measure'],
  },
  '2026-10-01-release-7-0-0': { decisions: [], blockers: [] },
  '2026-09-30-row-counter-ported': { decisions: ['Count rows in the store, not the projection'], blockers: [] },
  '2026-09-29-store-spike-notes': { decisions: [], blockers: ['Spike numbers were taken on a laptop on battery'] },
}

export function demoSnapshot(now: number): TmSnapshot {
  const ago = (hours: number) => new Date(now - hours * HOUR).toISOString()
  const handover = (
    id: string,
    hours: number,
    tldr: string,
    nextAction: string,
    branch: string,
    taskIds: readonly string[],
    thread: string,
  ): TmHandover => ({
    id,
    created: ago(hours),
    tldr,
    nextAction,
    path: handoverPath(DEMO_ROOT, id),
    branch,
    taskIds,
    thread,
  })
  const task = (id: string, hours: number) => {
    const d = DEMO_DETAILS[id]!
    return { kind: 'task' as const, id, title: d.title, priority: d.priority, humanAction: d.humanAction, timestamp: ago(hours) }
  }
  return {
    reachable: true,
    reason: DEMO_REASON,
    fetchedAt: now,
    queue: [
      task('unified-chat-022', 1),
      task('tm-audit-031', 5),
      task('docs-012', 30),
      { kind: 'issue', id: 'ISS-7', title: 'Viewer drops edits on slow disks', severity: 'P1', timestamp: ago(48) },
      { kind: 'decision', id: 'DEC-4', title: 'Ship rr-tui as its own plugin or beside Taskmaster', timestamp: ago(72) },
    ],
    queueTotal: 5,
    handovers: [
      handover(
        '2026-10-05-shipped-unified-chat-022',
        2,
        'Shipped unified-chat-022 to dev',
        'Live check, then sign off in the review queue',
        'main',
        ['unified-chat-022'],
        'unified-chat',
      ),
      handover(
        '2026-10-04-audit-fixes-landed',
        20,
        'Audit fixes landed on the native branch',
        'Record the merge for tm-audit-030',
        'feat/database-native-foundation',
        ['tm-audit-030', 'tm-audit-031'],
        'tm-audit',
      ),
      handover('2026-10-03-viewer-reskin-2a', 44, 'Viewer RR re-skin plan 2a closed', 'Start plan 2b', 'feat/viewer-reskin', [], 'viewer-reskin'),
      handover('2026-10-02-store-read-hang', 70, 'Store read hang root cause found', 'Fix quarantined-row scans first', '', ['B-082'], 'store-hang'),
      handover('2026-10-01-release-7-0-0', 96, 'Released 7.0.0', 'Watch for adoption reports', 'master', [], 'release'),
    ],
    handoversTotal: 6,
    bound: {
      taskId: 'unified-chat-022',
      inferred: false,
      detail: DEMO_DETAILS['unified-chat-022'] ?? null,
      pipeline: { laneless: false, lane: 'full', gateState: 'review-gate:pass', outstanding: [] },
    },
  }
}

/** The tasks the demo handovers link, with live status and phase (the handovers pane's card and phase tabs). */
export const DEMO_TASKS: Readonly<Record<string, TmLinkedTask>> = {
  'unified-chat-022': { id: 'unified-chat-022', status: 'in-review', phase: 'patch-1-9', title: 'Unified chat pre-build gets the full supervisor toolset' },
  'tm-audit-030': { id: 'tm-audit-030', status: 'done', phase: 'patch-1-9', title: 'Agent tool-use audit fixes' },
  'tm-audit-031': { id: 'tm-audit-031', status: 'in-review', phase: 'patch-1-9', title: 'Handover auto-supersede keeps the newest per thread' },
  'B-082': { id: 'B-082', status: 'todo', phase: 'release-2', title: 'Store read hang: quarantined rows scanned under the writer mutex' },
  'tm-audit-029': { id: 'tm-audit-029', status: 'done', phase: 'patch-1-8', title: 'Port the row counter' },
}

/** Closed demo handovers, `hours` old: shown dimmed under `◐ closed`. */
const DEMO_CLOSED: readonly (Omit<TmHandover, 'created' | 'path' | 'status'> & { readonly hours: number })[] = [
  {
    id: '2026-09-30-row-counter-ported',
    hours: 120,
    tldr: 'Row counter ported to the native store',
    nextAction: 'Nothing: closed',
    branch: 'feat/tm-audit-029',
    taskIds: ['tm-audit-029'],
    thread: 'tm-audit',
  },
  { id: '2026-09-29-store-spike-notes', hours: 150, tldr: 'Store spike notes', nextAction: 'Fold them into the spec', branch: '', taskIds: [], thread: 'store-spike' },
]

/** The handovers pane's demo list: the snapshot's open handovers, then the closed ones, with every linked task. */
export function demoHandoverList(now: number): TmHandoverList {
  const s = demoSnapshot(now)
  const closed = DEMO_CLOSED.map(({ hours, ...h }) => ({
    ...h,
    created: new Date(now - hours * HOUR).toISOString(),
    path: handoverPath(DEMO_ROOT, h.id),
    status: 'closed' as const,
  }))
  return { handovers: [...s.handovers.map(h => ({ ...h, status: 'open' as const })), ...closed], total: s.handoversTotal, closed: true, tasks: DEMO_TASKS }
}

/** What tm says of the demo sessions' ids (spec §10.2), as if one backlog_query had read them. */
export const DEMO_REFS: TmRefs = {
  tasks: {
    'unified-chat-022': { id: 'unified-chat-022', status: 'in-review', lane: 'full', phase: 'patch-1-9', title: 'Unified chat pre-build gets the full supervisor toolset' },
    'tm-audit-031': { id: 'tm-audit-031', status: 'in-progress', lane: 'full', phase: 'patch-1-9', title: 'Handover auto-supersede keeps the newest per thread' },
    'docs-012': { id: 'docs-012', status: 'todo', lane: '', phase: 'release-2', title: 'README mentions the review queue' },
    'tm-audit-029': { id: 'tm-audit-029', status: 'done', lane: 'full', phase: 'patch-1-8', title: 'Port the row counter' },
  },
  handovers: { '2026-10-05-shipped-unified-chat-022': 'open' },
}
export const DEMO_BRIEF =
  'State: The handover guard forks and files its handover; compaction after it is not wired yet.\nNext: Wire the compaction call behind the fork and run the guard tests.\nBlocker: none'

/**
 * The restore pane's demo scan: four sessions closed together an hour ago (handed off, interrupted, wip, done), one closed
 * after them, two older ones (one whose folder is gone) and one open in another terminal now.
 */
export function demoRestore(now: number): { readonly scan: TmScan; readonly refs: TmRefs } {
  const at = (minutes: number) => now / 1000 - minutes * 60
  const base = (id: string, title: string, closedMinutes: number, over: Partial<TmScannedSession>): TmScannedSession => ({
    id,
    cwd: DEMO_ROOT,
    cwdExists: true,
    branch: 'master',
    title,
    firstPrompt: title,
    lastPrompt: `Carry on with ${title.toLowerCase()}`,
    lastReply: `Stopped after the tests for ${title.toLowerCase()}.`,
    lastEvent: 'end_turn',
    interrupted: false,
    prompts: 12,
    ctx: 150_000,
    cost: 4,
    closedEpoch: at(closedMinutes),
    hasClosed: true,
    running: false,
    resumedAfterClose: false,
    taskIds: [],
    handovers: [],
    endedSession: false,
    promptsAfterEnd: 0,
    ...over,
  })
  const shipped = '2026-10-05-shipped-unified-chat-022'
  return {
    refs: DEMO_REFS,
    scan: {
      scannedAt: now / 1000,
      root: DEMO_ROOT,
      sessions: [
        base('demo-live', 'Plan the restore pane', 1, { running: true, hasClosed: false, ctx: 210_000 }),
        base('demo-late', 'Spike: viewer colours', 20, { ctx: 40_000, branch: 'spike/colours' }),
        base('demo-u5', 'U5 brief approval', 43, {
          ctx: 431_000,
          taskIds: ['unified-chat-022'],
          handovers: [{ id: shipped, path: handoverPath(DEMO_ROOT, shipped), tldr: 'Shipped unified-chat-022 to dev', next: 'Live check, then sign off in the review queue' }],
        }),
        base('demo-audit', 'Handover auto-supersede', 48, {
          ctx: 712_000,
          branch: 'feat/tm-audit-031',
          interrupted: true,
          lastEvent: 'interrupt',
          lastPrompt: 'Make the supersede keep the newest handover per thread',
          taskIds: ['tm-audit-031'],
        }),
        base('demo-docs', 'README review queue', 55, { ctx: 95_000, taskIds: ['docs-012'] }),
        base('demo-counter', 'Port the row counter', 60, { taskIds: ['tm-audit-029'], endedSession: true }),
        base('demo-old-1', 'Release 7.0.0 notes', 26 * 60, { endedSession: true }),
        base('demo-old-2', 'Store read hang', 30 * 60, { cwd: 'C:\\Users\\demo\\gone', cwdExists: false, taskIds: ['tm-audit-031'] }),
      ],
    },
  }
}

/**
 * The demo session's own history (a long /goal run): 11 touched tasks, newest first by `at`, some only read, and three
 * handovers written. Drawn instead of a scan, so the Session tab can be checked with no transcript.
 */
export function demoSessionHistory(now: number): TmSessionHistory {
  const ago = (hours: number) => (now - hours * HOUR) / 1000
  const task = (id: string, status: string, title: string, hours: number, wrote: boolean) => ({ at: { id, hours, wrote }, ref: { id, status, lane: 'full', phase: 'patch-1-9', title } })
  const rows = [
    task('unified-chat-022', 'in-review', DEMO_DETAILS['unified-chat-022']?.title ?? '', 0.2, true),
    task('tm-audit-031', 'in-review', 'Handover auto-supersede keeps the newest per thread', 1, true),
    task('tm-audit-030', 'done', 'Agent tool-use audit fixes', 2, true),
    task('docs-012', 'in-review', 'README mentions the review queue', 3, false),
    task('tm-audit-029', 'done', 'Bulk done refuses a task with outstanding gates', 5, true),
    task('tm-audit-028', 'done', 'Session scan survives a truncated last line', 8, true),
    task('store-014', 'in-progress', 'Quarantined rows no longer force a projection scan', 11, true),
    task('store-013', 'todo', 'Measure reads on a CodeMaestro-sized store', 14, false),
    task('viewer-031', 'done', 'Board re-skin: detail drawer', 18, true),
    task('viewer-030', 'done', 'Board re-skin: lanes', 21, true),
    task('rel-007', 'blocked', 'Release notes wait on the audit merge', 26, false),
  ]
  const ho = (id: string, hours: number, tldr: string, next: string) => ({ id, path: handoverPath(DEMO_ROOT, id), tldr, next, at: ago(hours) })
  return {
    sessionId: 'demo',
    scan: {
      scannedAt: now / 1000,
      found: true,
      tasks: rows.map(r => ({ id: r.at.id, at: ago(r.at.hours), wrote: r.at.wrote })),
      handovers: [
        ho('2026-10-13-store-hang-fix', 16, 'Store read hang fixed behind the quarantine flag', 'Run the CodeMaestro-sized read benchmark'),
        ho('2026-10-13-viewer-lanes', 9, 'Viewer lanes re-skinned; drawer next', 'Re-skin the detail drawer'),
        ho('2026-10-14-audit-wrap', 1.5, 'Audit fixes 028-031 landed; one review pending', 'Record the merge for tm-audit-030'),
      ],
    },
    refs: { tasks: Object.fromEntries(rows.map(r => [r.ref.id, r.ref])), handovers: {} },
    pending: false,
    error: '',
    finishedAt: now,
    page: 0,
  }
}
