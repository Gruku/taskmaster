// User intent: the one door to the Taskmaster MCP server — reads sent one at a time, each bounded to 3 s, every write to
// 15 s, and every reply read by a pure parser — so the surfaces never hang on tm, and a missing server or an unreadable
// reply degrades to a fault instead of a crash.
import type { TmBinding, TmBound, TmFault, TmHandoverSummary, TmPhase, TmSnapshot, TmTaskDetail } from '../types'
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
  parsePhases,
  parsePipeline,
} from './parse'

export const TM_TIMEOUT_MS = 3000
/** A write may queue behind the store's writer (ruling F14); it is never retried, so it gets longer than a read. */
export const TM_WRITE_TIMEOUT_MS = 15_000
/** Every refresh reads a window of the first 50 rows (ruling F2); counts come from the server's totals, not the rows. */
export const QUEUE_WINDOW = 50
/** Handovers fetched at each refresh (the server's own search window); the pane shows them five to a page. */
export const HANDOVER_WINDOW = 30
export const FAULT_LINE: Readonly<Record<TmFault, string | undefined>> = {
  none: undefined,
  connecting: undefined,
  offline: '◆ tm offline',
  unreadable: 'ⓘ tm reply unreadable',
}

/**
 * A call that got no usable reply. `transient`: worth retrying before reporting offline — the mod's own read timeout (a cold
 * server's first reads), or the engine saying the server is not connected yet (it connects seconds after session start).
 * `pending`: on the mod's own timeout, the call itself, still running (a write may still land); settles when it does.
 */
export class TmUnreachable extends Error {
  constructor(
    message: string,
    readonly transient: boolean = false,
    readonly pending?: Promise<unknown>,
  ) {
    super(message)
  }
}

// The engine's words for a server that has not connected (yet): `$.mcp.call: no connected MCP tool "<tool>" on a server
// named "<server>"`. Matched narrowly: any other error stays a fault at once.
const NOT_CONNECTED = /\bno connected MCP tool "[^"]+" on a server named "[^"]+"/

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
/** `transient`: the snapshot is offline only because of a transient failure (see TmUnreachable). */
export type FetchResult = {
  readonly snapshot: TmSnapshot
  readonly fault: TmFault
  readonly unreadable: readonly Unreadable[]
  readonly transient?: boolean
}

/** One call bounded to `ms`: no reply in time, or an engine error, throws TmUnreachable; the reply comes back as given. */
async function bounded(host: TmHost, tool: string, args: Record<string, unknown>, ms: number): Promise<TmReply> {
  const stop = new AbortController()
  const timer = host.sleep(ms, stop.signal).then(
    () => 'timeout' as const,
    () => 'cancelled' as const,
  )
  const work = host.call(tool, args).then(
    reply => ({ reply }),
    (error: unknown) => ({ error }),
  )
  const first = await Promise.race([work, timer])
  stop.abort()
  if (first === 'timeout' || first === 'cancelled') throw new TmUnreachable(`${tool}: no reply within ${ms / 1000} s`, true, work)
  if ('error' in first) {
    const message = String(first.error).split('\n')[0] ?? ''
    throw new TmUnreachable(`${tool}: ${message}`, NOT_CONNECTED.test(message))
  }
  return first.reply
}

export async function callTm(host: TmHost, tool: string, args: Record<string, unknown>): Promise<TmReply> {
  const reply = await bounded(host, tool, args, TM_TIMEOUT_MS)
  if (reply.isError) throw new TmUnreachable(`${tool}: ${firstParagraph(reply.text) || 'the server reported an error'}`)
  return reply
}

/** A write: bounded to 15 s; an isError reply comes back to the caller, whose refusal shows the server's own text. */
export function writeTm(host: TmHost, tool: string, args: Record<string, unknown>): Promise<TmReply> {
  return bounded(host, tool, args, TM_WRITE_TIMEOUT_MS)
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
  // One call after another, never together: the server answers one call at a time, so calls sent together queue and each
  // one's 3 s bound counts the others' time (on CodeMaestro: ~0.7 s each alone, 3.5–4 s each when sent together).
  try {
    replies = [
      await callTm(host, 'backlog_list_tasks', listArgs),
      // P0/P1 issues on their own: continuity ranks every in-review task before any issue, so its 50-row review window
      // holds no issue on a big backlog. Status `open` is continuity's own rule for an issue that asks for review.
      await callTm(host, 'backlog_issue_list', { status: 'open', severity: 'P0', limit: QUEUE_WINDOW }),
      await callTm(host, 'backlog_issue_list', { status: 'open', severity: 'P1', limit: QUEUE_WINDOW }),
      // Kept for the ages it gives (task and issue timestamps) where its window holds the item.
      await callTm(host, 'backlog_continuity_items', { action_class: 'review', limit: QUEUE_WINDOW }),
      await callTm(host, 'backlog_continuity_items', { action_class: 'decide', limit: QUEUE_WINDOW }),
      await callTm(host, 'backlog_handover_list', { format: 'json', status: 'open', limit: HANDOVER_WINDOW }),
      taskId === null ? null : await callTm(host, 'backlog_get_task', { task_id: taskId }),
      taskId === null ? null : await callTm(host, 'backlog_task_pipeline', { task_id: taskId }),
    ]
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    const transient = error instanceof TmUnreachable && error.transient
    return { snapshot: offlineSnapshot(input, reason), fault: 'offline', unreadable: [], transient }
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

/**
 * One refresh. A transient failure while retries remain (`final` false) writes no snapshot (the last one stays drawn) and
 * the `connecting` fault (no status line); with no retry left it is reported as offline, like any other failure.
 */
export async function refreshOnce(host: TmHost, io: TmIo, scope: TmScope, final = true): Promise<{ fault: TmFault; transient: boolean }> {
  const binding = await io.readBinding()
  const inferredId = binding === null ? inferTaskId([await host.branch(), baseName(await host.cwd())]) : null
  const result = await fetchSnapshot(host, {
    boundId: binding?.taskId ?? null,
    inferredId,
    root: await host.repoRoot(),
    now: await host.now(),
    scope,
  })
  for (const entry of result.unreadable) host.log(`taskmaster-mods: unreadable ${entry.tool} reply: ${entry.text.slice(0, 2000)}`)
  const transient = result.transient === true
  if (transient && !final) {
    host.log(`taskmaster-mods: tm not ready, retrying: ${result.snapshot.reason}`)
    await io.setFault('connecting')
    return { fault: 'connecting', transient }
  }
  await io.setSnapshot(result.snapshot)
  await io.setFault(result.fault)
  return { fault: result.fault, transient }
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
    host.log(`taskmaster-mods: unreadable backlog_handover_get reply: ${text.slice(0, 2000)}`)
    return null
  } catch (error) {
    host.log(`taskmaster-mods: handover summary not read: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}

/** The phase list query, newest phase first, each with its in-review tasks the scope lists (naming a human action when `waitingOnly`). */
export function phaseSql(waitingOnly: boolean): string {
  const waiting = waitingOnly ? " AND coalesce(json_extract(t.doc,'$.human_action'),'')<>''" : ''
  return (
    "SELECT json_array(p.id, p.status, json_extract(p.doc,'$.order'), (SELECT count(*) FROM entities t WHERE t.kind='task' AND t.deleted=0 AND t.status='in-review' AND json_extract(t.doc,'$.phase')=p.id" +
    waiting +
    ")) AS phase, json_extract(p.doc,'$.name') AS name FROM entities p WHERE p.kind='phase' AND p.deleted=0 ORDER BY json_extract(p.doc,'$.order') DESC"
  )
}

/**
 * The phase list for the review pane's filter, from one backlog_query. Null when the server refuses, is unreachable or
 * answers unreadably (the reason goes to the debug log), so the filter stays where it was.
 */
export async function readPhases(host: TmHost, waitingOnly: boolean): Promise<readonly TmPhase[] | null> {
  try {
    const text = (await callTm(host, 'backlog_query', { sql: phaseSql(waitingOnly), limit: 200 })).text
    const parsed = parsePhases(text)
    if (parsed.ok) return parsed.value
    host.log(`taskmaster-mods: unreadable backlog_query reply: ${text.slice(0, 2000)}`)
    return null
  } catch (error) {
    host.log(`taskmaster-mods: phases not read: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}
