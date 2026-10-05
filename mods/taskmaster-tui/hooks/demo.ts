// User intent: built-in sample data so taskmaster-tui can be seen, screenshotted and tuned without a Taskmaster backlog
// (userConfig source = demo); one task refuses sign-off exactly as the server words it.
import type { TmHandover, TmSnapshot, TmTaskDetail } from '../types'
import { handoverPath } from './model'

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

export function demoSnapshot(now: number): TmSnapshot {
  const ago = (hours: number) => new Date(now - hours * HOUR).toISOString()
  const handover = (id: string, hours: number, tldr: string, nextAction: string): TmHandover => ({
    id,
    created: ago(hours),
    tldr,
    nextAction,
    path: handoverPath(DEMO_ROOT, id),
  })
  const task = (id: string, hours: number) => {
    const d = DEMO_DETAILS[id]!
    return { kind: 'task' as const, id, title: d.title, priority: d.priority, humanAction: d.humanAction, timestamp: ago(hours) }
  }
  return {
    reachable: true,
    reason: '',
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
      handover('2026-10-05-shipped-unified-chat-022', 2, 'Shipped unified-chat-022 to dev', 'Live check, then sign off in the review queue'),
      handover('2026-10-04-audit-fixes-landed', 20, 'Audit fixes landed on the native branch', 'Record the merge for tm-audit-030'),
      handover('2026-10-03-viewer-reskin-2a', 44, 'Viewer RR re-skin plan 2a closed', 'Start plan 2b'),
      handover('2026-10-02-store-read-hang', 70, 'Store read hang root cause found', 'Fix quarantined-row scans first'),
      handover('2026-10-01-release-7-0-0', 96, 'Released 7.0.0', 'Watch for adoption reports'),
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
