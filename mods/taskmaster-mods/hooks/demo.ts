// User intent: built-in sample data so taskmaster-mods can be seen, screenshotted and tuned without a Taskmaster backlog
// (userConfig source = demo); one task refuses sign-off exactly as the server words it.
import type { TmHandover, TmHandoverSummary, TmPhase, TmSnapshot, TmTaskDetail } from '../types'
import { handoverPath } from './model'

/**
 * The demo snapshot's `reason`: how register.tsx tells demo data from real data when the source setting changes. It carries
 * the seed's version: bump DEMO_SEED whenever the demo data's shape changes, so a seed an older module left in $.state across
 * a reload is rebuilt rather than drawn (2: handovers gained branch / taskIds, summaries were added; 3: handovers gained
 * thread, for the copy block's Resume line).
 */
export const DEMO_SEED = 3
export const DEMO_REASON = `demo:${DEMO_SEED}`

/** Any demo seed, of any version (tm mode never draws one). */
export const isDemoSnapshot = (s: TmSnapshot | null): boolean => s !== null && (s.reason === 'demo' || s.reason.startsWith('demo:'))
/** A demo seed of this module's version: demo mode keeps it (the flows may have changed it); any other is rebuilt. */
export const isCurrentDemo = (s: TmSnapshot | null): boolean => s !== null && s.reason === DEMO_REASON
/** The phases demo's `f` cycles through; the demo queue itself is not filtered by them. */
export const DEMO_PHASES: readonly TmPhase[] = [
  { id: 'release-2', status: 'planned', order: 3, count: 2, name: 'Release 2.0 — Demo scope' },
  { id: 'patch-1-9', status: 'active', order: 2, count: 3, name: 'Patch 1.9 — Demo scope' },
  { id: 'patch-1-8', status: 'done', order: 1, count: 0, name: 'Patch 1.8 — Demo scope' },
]
export const DEMO_ROOT = 'C:\\Users\\demo\\project'
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
