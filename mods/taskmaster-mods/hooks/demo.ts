// User intent: built-in sample data so taskmaster-mods can be seen, screenshotted and tuned without a Taskmaster backlog
// (userConfig source = demo); one task refuses sign-off exactly as the server words it.
import type { TmHandover, TmHandoverList, TmHandoverSummary, TmLinkedTask, TmPhase, TmPhasePane, TmSnapshot, TmTaskDetail } from '../types'
import { handoverPath } from './model'
import { NO_TASK } from './phases'

/**
 * The demo snapshot's `reason`: how register.tsx tells demo data from real data when the source setting changes. It carries
 * the seed's version: bump DEMO_SEED whenever the demo data's shape changes, so a seed an older module left in $.state across
 * a reload is rebuilt rather than drawn (2: handovers gained branch / taskIds, summaries were added; 3: handovers gained
 * thread, for the copy block's Resume line; 4: phases gained dates and per-pane counts, seeded with the snapshot; 5: the
 * session was resumed from a handover and its tasks carry their phase, for the band and the Session tab; 7: the handovers
 * pane's own list, with closed handovers and linked tasks, seeded beside the snapshot).
 */
export const DEMO_SEED = 7
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
