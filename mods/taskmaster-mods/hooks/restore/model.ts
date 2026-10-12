// User intent: what the restore pane knows about a closed session, as plain functions (spec §10, §10.2) — the scanner's
// facts read safely, the one backlog_query that resolves their task and handover ids, and the status Taskmaster gives
// them — so a session is called unfinished by tm's word, never by a regex.
import type { TmRefs, TmRefTask, TmScan, TmScannedHandover, TmScannedSession } from '../../types'
import { firstParagraph, isRefusal, type Parsed } from '../parse'
import { NO_TASK } from '../phases'
import type { RrTone } from '../rr'

export type RestoreStatus = 'interrupted' | 'wip' | 'unknown' | 'handed-off' | 'done'
/** The burst page's order (spec §10): the most urgent first; `?` (tm not reached) sits with the unfinished. */
export const STATUS_ORDER: readonly RestoreStatus[] = ['interrupted', 'wip', 'unknown', 'handed-off', 'done']
export const STATUS_LABEL: Readonly<Record<RestoreStatus, string>> = {
  interrupted: 'interrupted',
  wip: 'wip',
  unknown: '?',
  'handed-off': 'handed off',
  done: 'done',
}
export const STATUS_TONE: Readonly<Record<RestoreStatus, RrTone>> = {
  interrupted: 'critical',
  wip: 'warning',
  unknown: 'signature',
  'handed-off': 'info',
  done: 'success',
}
const DONE_TASK = new Set(['done', 'archived'])
const SETTLED_HANDOVER = new Set(['closed', 'superseded'])

/** A closed session as the pane draws it: its facts, tm's status for it, its bound task and phase, its last handover. */
export type RestoreRow = {
  readonly s: TmScannedSession
  readonly status: RestoreStatus
  readonly why: string
  /** The last task id it touched ('' none): its bound task. */
  readonly boundId: string
  /** The bound task as tm holds it (null: none, or tm not reached, or tm does not hold it). */
  readonly task: TmRefTask | null
  /** The bound task's phase id; NO_TASK when there is none to name. */
  readonly phase: string
  /** The last handover it wrote (null: none). */
  readonly handover: TmScannedHandover | null
  /** That handover's status in tm ('' unknown). */
  readonly handoverStatus: string
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

function handoverOf(v: unknown): TmScannedHandover | null {
  if (!isObject(v) || str(v.id) === '') return null
  return { id: str(v.id), path: str(v.path), tldr: str(v.tldr), next: str(v.next) }
}

function sessionOf(v: unknown): TmScannedSession | null {
  if (!isObject(v) || str(v.id) === '') return null
  return {
    id: str(v.id),
    cwd: str(v.cwd),
    cwdExists: v.cwdExists === true,
    branch: str(v.branch),
    title: str(v.title) || str(v.id).slice(0, 8),
    firstPrompt: str(v.firstPrompt),
    lastPrompt: str(v.lastPrompt),
    lastReply: str(v.lastReply),
    lastEvent: str(v.lastEvent),
    interrupted: v.interrupted === true,
    prompts: num(v.prompts),
    ctx: num(v.ctx),
    cost: num(v.cost),
    closedEpoch: num(v.closedEpoch),
    hasClosed: v.hasClosed === true,
    running: v.running === true,
    resumedAfterClose: v.resumedAfterClose === true,
    taskIds: Array.isArray(v.taskIds) ? v.taskIds.filter((id): id is string => typeof id === 'string' && id !== '') : [],
    handovers: Array.isArray(v.handovers) ? v.handovers.map(handoverOf).filter((h): h is TmScannedHandover => h !== null) : [],
    endedSession: v.endedSession === true,
    promptsAfterEnd: num(v.promptsAfterEnd),
  }
}

/** The scanner's JSON, read field by field (a missing or mistyped field takes its empty value); null when it is no scan. */
export function scanOf(value: unknown): TmScan | null {
  if (!isObject(value) || !Array.isArray(value.sessions)) return null
  const sessions = value.sessions.map(sessionOf).filter((s): s is TmScannedSession => s !== null)
  return { scannedAt: num(value.scannedAt), root: str(value.root), sessions }
}

// Ids go into SQL as literals: only what a tm id can look like is sent, so a transcript can never shape the query.
const SAFE_ID = /^[A-Za-z0-9._:-]{1,120}$/
const literals = (ids: readonly string[]): string => [...new Set(ids.filter(id => SAFE_ID.test(id)))].map(id => `'${id}'`).join(',')

/**
 * The one backlog_query that resolves a scan (spec §10.2): a row per held task, `["t",id,status,lane,phase]` and its title,
 * and per held handover `["h",id,status]` (cells kept short of the server's 80-character cut). Null when there is nothing
 * to ask.
 */
export function refsSql(scan: TmScan): string | null {
  const tasks = literals(scan.sessions.flatMap(s => s.taskIds))
  const handovers = literals(scan.sessions.flatMap(s => s.handovers.map(h => h.id)))
  const parts = [
    ...(tasks === ''
      ? []
      : [
          `SELECT json_array('t', e.id, e.status, coalesce(json_extract(e.doc,'$.lane'),''), coalesce(json_extract(e.doc,'$.phase'),'')) AS ref, json_extract(e.doc,'$.title') AS title FROM entities e WHERE e.kind='task' AND e.deleted=0 AND e.id IN (${tasks})`,
        ]),
    ...(handovers === ''
      ? []
      : [`SELECT json_array('h', e.id, e.status) AS ref, '' AS title FROM entities e WHERE e.kind='handover' AND e.deleted=0 AND e.id IN (${handovers})`]),
  ]
  return parts.length === 0 ? null : parts.join(' UNION ALL ')
}

const REF_ROW = /^(\[\S*\])(?:\s+(.*))?$/
const ROWS_FOOTER = /^\d+ rows?(?: \(capped\))?$/

/** refsSql's table: a header, a row per held id (one whose cell does not read is left out), then `N rows`. */
export function parseRefs(raw: string): Parsed<TmRefs> {
  if (isRefusal(raw)) return { ok: false, reason: `backlog_query: ${firstParagraph(raw)}` }
  const lines = raw.replace(/\r\n/g, '\n').trim().split('\n')
  if (!ROWS_FOOTER.test((lines[lines.length - 1] ?? '').trim())) return { ok: false, reason: 'backlog_query: no "N rows" footer' }
  const tasks: Record<string, TmRefTask> = {}
  const handovers: Record<string, string> = {}
  for (const line of lines.slice(1, -1)) {
    const row = REF_ROW.exec(line.trim())
    if (row === null) continue
    let cells: unknown
    try {
      cells = JSON.parse(row[1] ?? '')
    } catch {
      continue
    }
    if (!Array.isArray(cells) || typeof cells[1] !== 'string' || cells[1] === '') continue
    if (cells[0] === 't') {
      tasks[cells[1]] = { id: cells[1], status: str(cells[2]), lane: str(cells[3]), phase: str(cells[4]), title: (row[2] ?? '').trim() }
    } else if (cells[0] === 'h') {
      handovers[cells[1]] = str(cells[2])
    }
  }
  return { ok: true, value: { tasks, handovers } }
}

/**
 * Spec §10.2, first match wins: DONE (end-session ran with no prompt after it, its bound task is done, or its last handover
 * was closed or superseded) → HANDED OFF (that handover is open) → INTERRUPTED (closed mid-turn) → WIP. Without tm only
 * the end-session rule can be told; anything else reads `?`.
 */
export function classify(s: TmScannedSession, refs: TmRefs | null): { readonly status: RestoreStatus; readonly why: string } {
  if (s.endedSession && s.promptsAfterEnd === 0) return { status: 'done', why: 'ran end-session' }
  if (refs === null) return { status: 'unknown', why: 'Taskmaster not reached: status unknown' }
  const bound = s.taskIds.at(-1)
  const task = bound === undefined ? undefined : refs.tasks[bound]
  if (task !== undefined && DONE_TASK.has(task.status)) return { status: 'done', why: `${task.id} is ${task.status}` }
  const handover = s.handovers.at(-1)
  const state = handover === undefined ? '' : (refs.handovers[handover.id] ?? '')
  if (handover !== undefined && SETTLED_HANDOVER.has(state)) return { status: 'done', why: `handover ${handover.id} is ${state}` }
  if (handover !== undefined && state === 'open') return { status: 'handed-off', why: `left handover ${handover.id} open` }
  if (s.interrupted) return { status: 'interrupted', why: 'closed mid-turn: the agent was still working' }
  return { status: 'wip', why: s.endedSession ? 'kept working after end-session' : 'no end-session and no open handover' }
}

export function rowOf(s: TmScannedSession, refs: TmRefs | null): RestoreRow {
  const { status, why } = classify(s, refs)
  const boundId = s.taskIds.at(-1) ?? ''
  const task = refs === null || boundId === '' ? null : (refs.tasks[boundId] ?? null)
  const handover = s.handovers.at(-1) ?? null
  return {
    s,
    status,
    why,
    boundId,
    task,
    phase: task !== null && task.phase !== '' ? task.phase : NO_TASK,
    handover,
    handoverStatus: handover === null || refs === null ? '' : (refs.handovers[handover.id] ?? ''),
  }
}

export function rowsOf(scan: TmScan | null, refs: TmRefs | null): RestoreRow[] {
  return scan === null ? [] : scan.sessions.map(s => rowOf(s, refs))
}

/** Restore's phase counts (spec §5): the closed sessions per bound task's phase, NO_TASK for the rest. */
export function phaseCounts(rows: readonly RestoreRow[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const row of rows) if (!row.s.running) counts.set(row.phase, (counts.get(row.phase) ?? 0) + 1)
  return counts
}
