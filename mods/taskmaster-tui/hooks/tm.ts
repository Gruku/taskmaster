// User intent: the one door to the Taskmaster MCP server — every call bounded to 3 s and every reply read by a pure parser —
// so the surfaces never hang on tm, and a missing server or an unreadable reply degrades to a fault instead of a crash.
import type { TmBinding, TmBound, TmFault, TmHandoverSummary, TmSnapshot, TmTaskDetail } from '../types'
import { baseName, inferTaskId } from './binding'
import type { TmHost, TmReply } from './host'
import { orderQueue } from './model'
import {
  continuityTotal,
  firstParagraph,
  isRefusal,
  type Parsed,
  parseContinuity,
  parseGetTask,
  parseHandovers,
  parseHandoverSummary,
  parseIssueList,
  parseListTasks,
  parsePipeline,
} from './parse'

export const TM_TIMEOUT_MS = 3000
/** Every refresh reads a window of the first 50 rows (ruling F2); counts come from the server's totals, not the rows. */
export const QUEUE_WINDOW = 50
export const FAULT_LINE: Readonly<Record<TmFault, string | undefined>> = {
  none: undefined,
  offline: '◆ tm offline',
  unreadable: 'ⓘ tm reply unreadable',
}

export class TmUnreachable extends Error {}

export type TmScope = { readonly waitingOnly: boolean; readonly phase: string }
export type TmIo = {
  readBinding: () => Promise<TmBinding | null>
  setBinding: (binding: TmBinding | null) => Promise<void>
  setSnapshot: (snapshot: TmSnapshot) => Promise<void>
  setFault: (fault: TmFault) => Promise<void>
}
export type FetchInput = {
  readonly boundId: string | null
  readonly inferredId: string | null
  readonly root: string
  readonly now: number
  readonly scope: TmScope
}
export type Unreadable = { readonly tool: string; readonly text: string }
export type FetchResult = { readonly snapshot: TmSnapshot; readonly fault: TmFault; readonly unreadable: readonly Unreadable[] }

export async function callTm(host: TmHost, tool: string, args: Record<string, unknown>): Promise<TmReply> {
  const stop = new AbortController()
  const timer = host.sleep(TM_TIMEOUT_MS, stop.signal).then(
    () => 'timeout' as const,
    () => 'cancelled' as const,
  )
  const work = host.call(tool, args).then(
    reply => ({ reply }),
    (error: unknown) => ({ error }),
  )
  const first = await Promise.race([work, timer])
  stop.abort()
  if (first === 'timeout' || first === 'cancelled') throw new TmUnreachable(`${tool}: no reply within ${TM_TIMEOUT_MS / 1000} s`)
  if ('error' in first) throw new TmUnreachable(`${tool}: ${String(first.error).split('\n')[0]}`)
  if (first.reply.isError) throw new TmUnreachable(`${tool}: ${firstParagraph(first.reply.text) || 'the server reported an error'}`)
  return first.reply
}

function offlineSnapshot(input: FetchInput, reason: string): TmSnapshot {
  return {
    reachable: false,
    reason,
    fetchedAt: input.now,
    queue: [],
    queueTotal: 0,
    handovers: [],
    handoversTotal: 0,
    bound: input.boundId === null ? null : { taskId: input.boundId, inferred: false, detail: null, pipeline: null },
  }
}

function boundFrom(
  input: FetchInput,
  taskId: string | null,
  task: TmReply | null,
  pipeline: TmReply | null,
  unreadable: Unreadable[],
): TmBound | null {
  if (taskId === null || task === null || pipeline === null) return null
  const idOnly: TmBound | null = input.boundId === null ? null : { taskId, inferred: false, detail: null, pipeline: null }
  const detail = parseGetTask(task.text)
  if (!detail.ok) {
    unreadable.push({ tool: 'backlog_get_task', text: task.text })
    return idOnly
  }
  if (detail.value === null) return idOnly
  const pipe = parsePipeline(pipeline.text)
  if (!pipe.ok) unreadable.push({ tool: 'backlog_task_pipeline', text: pipeline.text })
  return { taskId, inferred: input.boundId === null, detail: detail.value, pipeline: pipe.ok ? pipe.value : null }
}

export async function fetchSnapshot(host: TmHost, input: FetchInput): Promise<FetchResult> {
  const taskId = input.boundId ?? input.inferredId
  const listArgs: Record<string, unknown> = { status: 'in-review', limit: QUEUE_WINDOW }
  if (input.scope.waitingOnly) listArgs.waiting_on_human = true
  if (input.scope.phase !== '') listArgs.phase = input.scope.phase
  let replies: [TmReply, TmReply, TmReply, TmReply, TmReply, TmReply, TmReply | null, TmReply | null]
  try {
    replies = await Promise.all([
      callTm(host, 'backlog_list_tasks', listArgs),
      // P0/P1 issues on their own: continuity ranks every in-review task before any issue, so its 50-row review window
      // holds no issue on a big backlog. Status `open` is continuity's own rule for an issue that asks for review.
      callTm(host, 'backlog_issue_list', { status: 'open', severity: 'P0', limit: QUEUE_WINDOW }),
      callTm(host, 'backlog_issue_list', { status: 'open', severity: 'P1', limit: QUEUE_WINDOW }),
      // Kept for the ages it gives (task and issue timestamps) where its window holds the item.
      callTm(host, 'backlog_continuity_items', { action_class: 'review', limit: QUEUE_WINDOW }),
      callTm(host, 'backlog_continuity_items', { action_class: 'decide', limit: QUEUE_WINDOW }),
      callTm(host, 'backlog_handover_list', { format: 'json', status: 'open', limit: 5 }),
      taskId === null ? Promise.resolve(null) : callTm(host, 'backlog_get_task', { task_id: taskId }),
      taskId === null ? Promise.resolve(null) : callTm(host, 'backlog_task_pipeline', { task_id: taskId }),
    ])
  } catch (error) {
    return { snapshot: offlineSnapshot(input, error instanceof Error ? error.message : String(error)), fault: 'offline', unreadable: [] }
  }
  const [list, p0, p1, review, decide, handovers, task, pipeline] = replies
  const refused = [list, p0, p1, review, decide, handovers].find(reply => isRefusal(reply.text))
  if (refused !== undefined) return { snapshot: offlineSnapshot(input, firstParagraph(refused.text)), fault: 'offline', unreadable: [] }

  const unreadable: Unreadable[] = []
  const take = <T>(tool: string, text: string, parsed: Parsed<T>, empty: T): T => {
    if (parsed.ok) return parsed.value
    unreadable.push({ tool, text })
    return empty
  }
  const listed = take('backlog_list_tasks', list.text, parseListTasks(list.text), { rows: [], total: 0 })
  const none = { issues: [], hidden: 0 }
  const issuesP0 = take('backlog_issue_list', p0.text, parseIssueList(p0.text), none)
  const issuesP1 = take('backlog_issue_list', p1.text, parseIssueList(p1.text), none)
  const reviewItems = take('backlog_continuity_items', review.text, parseContinuity(review.text), []).filter(i => i.actionClass === 'review')
  const decideAll = take('backlog_continuity_items', decide.text, parseContinuity(decide.text), [])
  const decideItems = decideAll.filter(i => i.actionClass === 'decide')
  const ho = take('backlog_handover_list', handovers.text, parseHandovers(handovers.text, input.root), { handovers: [], total: 0 })
  const stamps = new Map(reviewItems.map(i => [i.id, i.timestamp]))
  const issues = [...issuesP0.issues, ...issuesP1.issues]
  const decisions = decideItems.filter(i => i.type === 'decision')
  // The counts are the server's totals: list_tasks' "**N tasks", each issue list's rows plus its "…N more" footer, the
  // decide class's `total` (it holds only open decisions). A decide reply with no total and a full window is cut where the
  // count is unknown: the band shows `N+`.
  const issueCount = issues.length + issuesP0.hidden + issuesP1.hidden
  const decideTotal = continuityTotal(decide.text)
  const decisionCount = decideTotal ?? decisions.length
  const capped = decideTotal === null && decideAll.length >= QUEUE_WINDOW
  const queue = orderQueue([
    ...listed.rows.map(r => ({
      kind: 'task' as const,
      id: r.id,
      title: r.title,
      priority: r.priority,
      humanAction: r.humanAction,
      timestamp: stamps.get(r.id) ?? '',
    })),
    // An issue's age comes from the review window when it holds the issue; the issue list carries no date.
    ...issues.map(i => ({ kind: 'issue' as const, id: i.id, title: i.title, severity: i.severity, timestamp: stamps.get(i.id) ?? '' })),
    ...decisions.map(i => ({ kind: 'decision' as const, id: i.id, title: i.title, timestamp: i.timestamp })),
  ])
  const bound = boundFrom(input, taskId, task, pipeline, unreadable)
  return {
    snapshot: {
      reachable: true,
      reason: '',
      fetchedAt: input.now,
      queue,
      queueTotal: listed.total + issueCount + decisionCount,
      ...(capped ? { queueCapped: true } : {}),
      handovers: ho.handovers,
      handoversTotal: ho.total,
      bound,
    },
    fault: unreadable.length > 0 ? 'unreadable' : 'none',
    unreadable,
  }
}

export async function refreshOnce(host: TmHost, io: TmIo, scope: TmScope): Promise<TmFault> {
  const binding = await io.readBinding()
  const inferredId = binding === null ? inferTaskId([await host.branch(), baseName(await host.cwd())]) : null
  const result = await fetchSnapshot(host, {
    boundId: binding?.taskId ?? null,
    inferredId,
    root: await host.repoRoot(),
    now: await host.now(),
    scope,
  })
  for (const entry of result.unreadable) host.log(`taskmaster-tui: unreadable ${entry.tool} reply: ${entry.text.slice(0, 2000)}`)
  await io.setSnapshot(result.snapshot)
  await io.setFault(result.fault)
  return result.fault
}

export async function loadDetail(host: TmHost, taskId: string): Promise<TmTaskDetail | null> {
  try {
    const parsed = parseGetTask((await callTm(host, 'backlog_get_task', { task_id: taskId })).text)
    return parsed.ok ? parsed.value : null
  } catch {
    return null
  }
}

/**
 * The handovers pane's summary reader (TmFlowDeps.summary): decisions and blockers with backlog_handover_get, bounded like
 * every call. Null when the server refuses, is unreachable or answers unreadably (that text goes to the debug log), so the
 * card says "summary unavailable" and a later expand asks again.
 */
export async function readSummary(host: TmHost, handoverId: string): Promise<TmHandoverSummary | null> {
  try {
    const text = (await callTm(host, 'backlog_handover_get', { handover_id: handoverId, sections: ['decisions', 'blockers'] })).text
    const parsed = parseHandoverSummary(text)
    if (parsed.ok) return parsed.value
    host.log(`taskmaster-tui: unreadable backlog_handover_get reply: ${text.slice(0, 2000)}`)
    return null
  } catch (error) {
    host.log(`taskmaster-tui: handover summary not read: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}
