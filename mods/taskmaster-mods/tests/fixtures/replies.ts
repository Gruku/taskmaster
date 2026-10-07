// User intent: a small coherent backlog as the Taskmaster server words it (format strings from taskmaster/backlog_server.py
// and native/domain.py), so parsers and tm-mode surfaces are tested against every quirk without a server.
export const LIST_IN_REVIEW = [
  '**2 tasks:**',
  '- `unified-chat-022` — Unified chat pre-build gets the full supervisor toolset; cookbook owns the order (critical, unified-chat, in-review) — Pre-build uses the full toolset',
  '    waiting-on-human: Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):',
  'first unified message stays on intent/brief and does not build.',
  '- `tm-audit-031` — Handover auto-supersede (keeps newest) (P1, tm-audit, in-review)',
  '    waiting-on-human: Open the viewer and confirm one open handover per thread.',
].join('\n')
export const LIST_EMPTY = 'No tasks found matching: status=in-review'
export const LIST_CAPPED = [
  '**3 tasks (showing first 1):**',
  '- `a-001` — A (low, ep, in-review)',
  '…2 more tasks — pass status/epic/phase filters or limit=0 for all',
].join('\n')

export const CONTINUITY_REVIEW = JSON.stringify({
  view: 'action',
  total: 3,
  items: [
    { id: 'unified-chat-022', type: 'task', title: 'Unified chat pre-build gets the full supervisor toolset; cookbook owns the order', where: 'unified-chat', next: 'in-review', action_class: 'review', timestamp: '2026-10-05T09:00', age_days: 0, task_id: 'unified-chat-022', branch: '' },
    { id: 'tm-audit-031', type: 'task', title: 'Handover auto-supersede (keeps newest)', where: 'tm-audit', next: 'in-review', action_class: 'review', timestamp: '2026-10-04T12:00', age_days: 1, task_id: 'tm-audit-031', branch: 'feat/tm-audit-031' },
    { id: 'ISS-7', type: 'issue', title: 'Viewer drops edits on slow disks', where: '', next: 'P1 · open', action_class: 'review', timestamp: '2026-10-01', age_days: 4, task_id: '', branch: '' },
  ],
})
export const CONTINUITY_DECIDE = JSON.stringify({
  view: 'action',
  total: 1,
  items: [
    { id: 'DEC-4', type: 'decision', title: 'Ship rr-tui as its own plugin or beside Taskmaster', where: '', next: 'open', action_class: 'decide', timestamp: '2026-10-02T10:00', age_days: 3, task_id: '', branch: '' },
  ],
})
export const CONTINUITY_ERROR = JSON.stringify({ items: [], view: 'action', error: 'no backlog' })

export const HANDOVERS_OPEN = JSON.stringify({
  handovers: [
    { id: '2026-10-04-audit-fixes-landed', date: '2026-10-04', created: '2026-10-04T18:20', thread: 'tm-audit', session_kind: 'build', status: 'open', tldr: 'Audit fixes landed on the native branch', next_action: 'Record the merge for tm-audit-030', task_ids: ['tm-audit-030'], tip_commit: '40e75d9', branch: 'feat/database-native-foundation', links: [], superseded_by: null },
    { id: '2026-10-05-shipped-unified-chat-022', date: '2026-10-05', created: '2026-10-05T09:10', thread: 'unified-chat', session_kind: 'build', status: 'open', tldr: 'Shipped unified-chat-022 to dev', next_action: 'Live check, then sign off', task_ids: ['unified-chat-022'], tip_commit: '397a57c', branch: 'main', links: [{ type: 'task', target: 'unified-chat-022' }], superseded_by: null },
    { id: '2026-10-03-old-plan', date: '2026-10-03', created: '2026-10-03T08:00', thread: 'viewer', session_kind: 'plan', status: 'superseded', tldr: 'Old plan', next_action: '', task_ids: [], tip_commit: '', branch: '', links: [], superseded_by: '2026-10-04-audit-fixes-landed' },
  ],
  returned: 3,
  total: 7,
  truncated: true,
  archived_omitted: 0,
})

export const GET_TASK_BOUND = [
  '## `tm-audit-030` — Agent tool-use audit fixes',
  '',
  '**tldr:** Fix what the audit found',
  '**status:** in-progress',
  '**priority:** high',
  '**epic:** tm-audit',
  '**lane:** full',
  '**gate_state:** review-gate:pass',
  '**branch:** feat/tm-audit-030',
  '**docs_available:** plan',
  '',
  '**links:**',
  '- task: [tm-audit-029]',
].join('\n')
export const GET_TASK_REVIEW = [
  '## `unified-chat-022` — Unified chat pre-build gets the full supervisor toolset; cookbook owns the order',
  '',
  '**status:** in-review',
  '**priority:** critical',
  '**lane:** full',
  '**gate_state:** review-gate:pass',
  '**human_action:** Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):',
  'first unified message stays on intent/brief and does not build.',
  '- second bullet of the check',
  '**open_handovers:** 1',
].join('\n')
export const GET_TASK_031 = [
  '## `tm-audit-031` — Handover auto-supersede (keeps newest)',
  '',
  '**status:** in-review',
  '**priority:** high',
  '**lane:** full',
  '**gate_state:** spec-review:pass',
  '**branch:** feat/tm-audit-031',
  '**human_action:** Open the viewer and confirm one open handover per thread.',
].join('\n')
export const GET_TASK_MISSING = 'Error: task `zz-missing-999` not found'

export const PIPELINE_BOUND = [
  '## Pipeline `tm-audit-030` — lane: **full**',
  'gate_state: `review-gate:pass`',
  '',
  '- `spec-review`: pass',
  '- `plan-review`: ⚠ skipped — small change',
  '- `review-gate`: pass',
  '- `merge`: ○ pending',
  '',
  '**Outstanding:** merge',
].join('\n')
export const PIPELINE_READY = [
  '## Pipeline `unified-chat-022` — lane: **full**',
  'gate_state: `(none)`',
  '',
  '- `review-gate`: pass',
  '',
  '**Outstanding:** none — ready for done ✓',
].join('\n')
export const PIPELINE_LANELESS = '`docs-012` is laneless (pre-protocol) — no pipeline enforced.'

export const COMPLETED =
  'Completed `unified-chat-022` — Unified chat pre-build gets the full supervisor toolset; cookbook owns the order\n\n**Next in Unified chat:** `unified-chat-023` — Follow-up (medium) [seq 41]'
export const COMPLETED_NOT_PERSISTED = 'Completed `unified-chat-022` — (not persisted)'
export const GATE_REFUSAL =
  'Cannot complete `tm-audit-031` — outstanding gates for lane `full`: review-gate. Record each (backlog_record_gate) or skip it (backlog_skip_gate).'
export const BUG_REFUSAL =
  'Cannot complete tm-audit-031 — 1 open bug(s) linked via found_in: B-12.\nResolve each (fix/adopt/shelve/promote) before closing the task.'
export const WRONG_STATUS = 'Error: task `tm-audit-031` is `todo`, expected one of: in-progress, in-review, blocked'
export const updated = (id: string, field: string, value: string): string => `Updated \`${id}\` field \`${field}\` → ${value} [seq 42]`
export const NOT_PERSISTED = 'Updated `tm-audit-031` field `status` → (not persisted)'
export const NO_CHANGE = 'No change to `tm-audit-031` field `human_action` — already ``'
export const KEYWORD_UPDATED = 'Updated `tm-audit-031`: next_step → Back from review: tighten the copy [seq 43]'
export const WITH_WARNING = `${updated('tm-audit-031', 'status', 'in-progress')}\n\nWarning: projection conflict in .taskmaster/tasks/tm-audit-031.md`
export const CLAIM_OK = JSON.stringify({ task_id: 'tm-audit-030', holder: 'sess-A', expires_at: '2026-10-05T13:00', live: true, expired: false, state: 'held', ok: true })
export const CLAIM_CONFLICT = JSON.stringify({ ok: false, error: 'claim_conflict', task_id: 'tm-audit-030', holder: 'sess-B', live: true, expires_at: '2026-10-05T13:00', hint: 'another session holds it' })

export const ISSUES_P1 = [
  '- ISS-7 P1 open           — Viewer drops edits on slow disks [viewer, store]',
  '- ISS-9 P1 open           — Export stalls — on big stores — Repro: open a 2k-task store',
].join('\n')
/** The router's P1 list: ISS-7 only, as the continuity review fixture has it. */
export const ISSUES_P1_ONE = '- ISS-7 P1 open           — Viewer drops edits on slow disks [viewer, store] — Edits lost after 30 s'
export const NO_ISSUES = 'No issues match.'

export const HANDOVER_SUMMARY = [
  '## Handover: 2026-10-05-shipped-unified-chat-022',
  '',
  '### decisions',
  '- The cookbook owns the build order, not the pre-build',
  '- Pre-build hands the supervisor its full toolset',
  '### blockers',
  '- Needs unifiedChatGenerate + unifiedChatBuild on dev',
].join('\n')
export const HANDOVER_SUMMARY_DECISIONS_ONLY = ['## Handover: 2026-10-04-audit-fixes-landed', '', '### decisions', '- Audit fixes ride the native branch'].join('\n')
export const HANDOVER_WRITTEN = [
  'Handover written: 2026-10-06-live-data-wired',
  '- File: .taskmaster\\handovers\\2026-10-06-live-data-wired.md',
  '- Path: C:\\work\\proj\\.taskmaster\\handovers\\2026-10-06-live-data-wired.md',
  '- Index entries: 8',
  'Resume: tui-mods — Run the Step 9 live check',
].join('\n')

export function backlog(tool: string, args: Record<string, unknown>): { text: string } {
  if (tool === 'backlog_list_tasks') return { text: LIST_IN_REVIEW }
  if (tool === 'backlog_continuity_items') return { text: args.action_class === 'decide' ? CONTINUITY_DECIDE : CONTINUITY_REVIEW }
  if (tool === 'backlog_handover_list') return { text: HANDOVERS_OPEN }
  if (tool === 'backlog_get_task') {
    const id = String(args.task_id)
    return { text: id === 'tm-audit-030' ? GET_TASK_BOUND : id === 'unified-chat-022' ? GET_TASK_REVIEW : id === 'tm-audit-031' ? GET_TASK_031 : GET_TASK_MISSING }
  }
  if (tool === 'backlog_issue_list') return { text: args.severity === 'P1' ? ISSUES_P1_ONE : NO_ISSUES }
  if (tool === 'backlog_handover_get') {
    const id = String(args.handover_id)
    if (id === '2026-10-05-shipped-unified-chat-022') return { text: HANDOVER_SUMMARY }
    return { text: id === '2026-10-04-audit-fixes-landed' ? HANDOVER_SUMMARY_DECISIONS_ONLY : `Handover not found: ${id}` }
  }
  if (tool === 'backlog_task_pipeline') return { text: String(args.task_id) === 'tm-audit-030' ? PIPELINE_BOUND : PIPELINE_READY }
  return { text: `Error: ${tool} is not part of the test backlog` }
}
