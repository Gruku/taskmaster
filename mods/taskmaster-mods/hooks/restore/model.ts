// User intent: what the restore pane knows about a closed session, as plain functions (spec §10, §10.2) — the scanner's
// facts read safely, the one backlog_query that resolves their task and handover ids, and the status Taskmaster gives
// them — so a session is called unfinished by tm's word, never by a regex.
import type {
  TmRefs,
  TmRefTask,
  TmRestoreData,
  TmRestorePending,
  TmRestoreView,
  TmResumeMode,
  TmScan,
  TmScannedHandover,
  TmScannedSession,
  TmSessionBurst,
} from '../../types'
import { launchCommand, type LaunchSpec, type Platform, resumePrompt } from '../launch'
import { handoverPath } from '../model'
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
/** A task's status badge tone on a restore card. */
export const TASK_TONE: Readonly<Record<string, RrTone>> = {
  todo: 'signature',
  'in-progress': 'info',
  'in-review': 'warning',
  blocked: 'critical',
  done: 'success',
  archived: 'success',
}
/** `/tm-restore` with no argument scans this many days; the session-start scan, SIGNAL_DAYS (spec §10.4). */
export const DEFAULT_DAYS = 4
export const SIGNAL_DAYS = 3
export const FRESH_RESTORE_DATA: TmRestoreData = { scan: null, refs: null, error: '', days: DEFAULT_DAYS }
export const FRESH_RESTORE_VIEW: TmRestoreView = {
  page: 0,
  focus: '',
  selected: {},
  modes: {},
  opened: {},
  compact: false,
  busy: '',
  launching: [],
  briefing: [],
  briefs: {},
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

/** The rows the pane lists: every scanned session but this one (it is scanned for its burst, never offered back to itself). */
export function paneRows(data: TmRestoreData, self: string): RestoreRow[] {
  return rowsOf(data.scan, data.refs).filter(r => r.s.id !== self)
}

/** Restore's phase counts (spec §5): the closed sessions per bound task's phase, NO_TASK for the rest. */
export function phaseCounts(rows: readonly RestoreRow[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const row of rows) if (!row.s.running) counts.set(row.phase, (counts.get(row.phase) ?? 0) + 1)
  return counts
}

// ── Bursts, pages, ticks and resume modes (ported from cm-restore's pure helpers) ─────────────────────────────────────

/** Sessions closed within this many minutes of the next one belong to one burst. */
export const BURST_GAP_MIN = 10
/** Older sessions page this many at a time, newest first (spec §10, §15.6). */
export const RESTORE_PAGE_SIZE = 3
/** Context a resume reopens: warning once heavy, critical near a 1M window's end. */
export const CTX_WARN = 400_000
export const CTX_HIGH = 700_000
/** The band's restore keycap shows for a burst this recent at most (spec §10.4). */
export const PENDING_WITHIN_S = 48 * 3600

/** Sessions closed together. `open`: members not running and not done. Newest burst first. */
export type RestoreBurst = { readonly ids: readonly string[]; readonly from: number; readonly to: number; readonly open: number }
/** A page: page 1 is the closing burst and what closed after it; `burstSize` counts the burst, `since` what closed after. */
export type RestorePage = {
  readonly ids: readonly string[]
  readonly from: number
  readonly to: number
  readonly isBurst: boolean
  readonly burstSize: number
  readonly since: number
}

const pad = (n: number): string => String(n).padStart(2, '0')
export function hhmm(epoch: number): string {
  const d = new Date(epoch * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
export function dayLabel(epoch: number, now: number): string {
  const d = new Date(epoch * 1000)
  const t = new Date(now * 1000)
  const start = (x: Date): number => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((start(t) - start(d)) / 86_400_000)
  if (diff === 0) return 'today'
  if (diff === 1) return 'yesterday'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
/** `16:31` today, `yesterday 16:31`, else the date and time. */
export function when(epoch: number, now: number): string {
  const day = dayLabel(epoch, now)
  return day === 'today' ? hhmm(epoch) : `${day} ${hhmm(epoch)}`
}
/** `today 16:14–16:31` within a day, `yesterday 22:10 – 09:05` across days. */
export function spanLabel(from: number, to: number, now: number): string {
  if (from === to) return `${dayLabel(to, now)} ${hhmm(to)}`
  if (dayLabel(from, now) === dayLabel(to, now)) return `${dayLabel(to, now)} ${hhmm(from)}–${hhmm(to)}`
  return `${dayLabel(from, now)} ${hhmm(from)} – ${when(to, now)}`
}
export function ago(epoch: number, now: number): string {
  const m = Math.max(0, Math.round((now - epoch) / 60))
  if (m < 60) return `${m}m ago`
  const hours = Math.round(m / 60)
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`
}
export function formatTokens(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}k`
}
/** The ctx figure's tone: critical from CTX_HIGH, warning from CTX_WARN, else none (subtle text). */
export function ctxTone(ctx: number): 'critical' | 'warning' | null {
  return ctx >= CTX_HIGH ? 'critical' : ctx >= CTX_WARN ? 'warning' : null
}
/** One line of prose from markdown-ish text. */
export function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_`#>]+/g, '')
    .replace(/(^|\s)[-•]\s+/gm, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Closing bursts, newest first: closed sessions (and running ones that closed before, by that close) whose close times sit
 * within `gapMin` minutes of the next. A session that never closed (running, no cost-state) is in none.
 */
export function burstsOf(rows: readonly RestoreRow[], gapMin = BURST_GAP_MIN): RestoreBurst[] {
  const closed = rows.filter(r => !r.s.running || r.s.hasClosed).sort((a, b) => b.s.closedEpoch - a.s.closedEpoch)
  const bursts: { ids: string[]; from: number; to: number; open: number }[] = []
  for (const r of closed) {
    const last = bursts.at(-1)
    if (last !== undefined && last.from - r.s.closedEpoch <= gapMin * 60) {
      last.ids.push(r.s.id)
      last.from = r.s.closedEpoch
    } else {
      bursts.push({ ids: [r.s.id], from: r.s.closedEpoch, to: r.s.closedEpoch, open: 0 })
    }
    if (!r.s.running && r.status !== 'done') bursts[bursts.length - 1]!.open += 1
  }
  return bursts
}

/** Page 1's burst: the newest of two or more sessions, else the newest. */
export function defaultBurst(bursts: readonly RestoreBurst[]): number {
  const at = bursts.findIndex(b => b.ids.length >= 2)
  return at >= 0 ? at : 0
}

/** A row under the strip's choice: All, a phase id, or NO_TASK. */
export const inPhase = (row: RestoreRow, choice: string): boolean => choice === '' || row.phase === choice

const chunks = (rows: readonly RestoreRow[], size: number): RestorePage[] => {
  const pages: RestorePage[] = []
  for (let i = 0; i < rows.length; i += size) {
    const chunk = rows.slice(i, i + size)
    pages.push({ ids: chunk.map(r => r.s.id), from: chunk.at(-1)!.s.closedEpoch, to: chunk[0]!.s.closedEpoch, isBurst: false, burstSize: 0, since: 0 })
  }
  return pages
}

/**
 * Burst-first paging (spec §10): under All, page 1 is the closing burst plus anything closed after it, then the rest newest
 * first, RESTORE_PAGE_SIZE a page. Under a phase, that phase's sessions newest first. Running sessions are on no page.
 */
export function pagesOf(rows: readonly RestoreRow[], bursts: readonly RestoreBurst[], choice: string, size = RESTORE_PAGE_SIZE): RestorePage[] {
  const closed = rows.filter(r => !r.s.running && inPhase(r, choice)).sort((a, b) => b.s.closedEpoch - a.s.closedEpoch)
  if (choice !== '' || bursts.length === 0) return chunks(closed, size)
  const at = defaultBurst(bursts)
  const burst = bursts[at]!
  const head = new Set(bursts.slice(0, at + 1).flatMap(b => b.ids))
  const first = closed.filter(r => head.has(r.s.id))
  const members = new Set(burst.ids)
  const page: RestorePage = {
    ids: first.map(r => r.s.id),
    from: burst.from,
    to: bursts[0]!.to,
    isBurst: true,
    burstSize: burst.ids.length,
    since: first.filter(r => !members.has(r.s.id)).length,
  }
  return [page, ...chunks(closed.filter(r => !head.has(r.s.id)), size)]
}

/** A page's rows: the burst page unfinished first (STATUS_ORDER, then newest); other pages strictly newest first. */
export function pageRows(rows: readonly RestoreRow[], page: RestorePage | undefined): RestoreRow[] {
  if (page === undefined) return []
  const byId = new Map(rows.map(r => [r.s.id, r]))
  const list = page.ids.map(id => byId.get(id)).filter((r): r is RestoreRow => r !== undefined)
  const rank = (r: RestoreRow): number => STATUS_ORDER.indexOf(r.status)
  return page.isBurst ? list.sort((a, b) => rank(a) - rank(b) || b.s.closedEpoch - a.s.closedEpoch) : list.sort((a, b) => b.s.closedEpoch - a.s.closedEpoch)
}

/** The page's header: `4 closed together · today 16:14–16:31 · 12m ago · 1 closed since`, or an older page's span. */
export function pageHeader(page: RestorePage, now: number): string {
  const span = spanLabel(page.from, page.to, now)
  if (!page.isBurst) return `${page.ids.length} ${page.ids.length === 1 ? 'session' : 'sessions'} · newest first · ${span}`
  return [
    page.burstSize === 1 ? 'Closed alone' : `${page.burstSize} closed together`,
    span,
    ago(page.to, now),
    page.since > 0 ? `${page.since} closed since` : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

/** Sessions open in another terminal now, most recently active first. */
export function runningRows(rows: readonly RestoreRow[]): RestoreRow[] {
  return rows.filter(r => r.s.running).sort((a, b) => b.s.closedEpoch - a.s.closedEpoch)
}

/** Why a session cannot be reopened from here, or '' when it can. */
export function blockedWhy(row: RestoreRow): string {
  if (row.s.running) return `${row.s.title} is open in another terminal`
  if (!row.s.cwdExists) return `${row.s.title}: its folder is gone (${row.s.cwd || 'unknown'})`
  return ''
}

/** Default ticks (spec §10): unfinished, resumable and not yet reopened from here; an explicit toggle wins. */
export function isTicked(row: RestoreRow, selected: Readonly<Record<string, boolean>>, opened: Readonly<Record<string, number>>): boolean {
  if (blockedWhy(row) !== '') return false
  return selected[row.s.id] ?? (row.status !== 'done' && opened[row.s.id] === undefined)
}

/**
 * How a session reopens (spec §10.3): with a handover, `fresh` (a new session told to resume from it) when its context is
 * past CTX_WARN, else `resume`; an explicit toggle wins. Without a handover, always `resume`.
 */
export function resumeModeOf(row: RestoreRow, modes: Readonly<Record<string, TmResumeMode>>): TmResumeMode {
  if (row.handover === null) return 'resume'
  return modes[row.s.id] ?? (row.s.ctx > CTX_WARN ? 'fresh' : 'resume')
}

/** The card's one line of what was going on: the handover's next step, the interrupted prompt, the last reply, or why. */
export function detailOf(row: RestoreRow): { readonly label: string; readonly text: string } {
  if (row.handover !== null && row.status === 'handed-off') return { label: 'Next', text: plain(row.handover.next || row.handover.tldr) }
  if (row.status === 'interrupted' && row.s.lastPrompt !== '') return { label: 'Working on', text: plain(row.s.lastPrompt) }
  if (row.s.lastReply !== '') return { label: 'Last reply', text: plain(row.s.lastReply) }
  return { label: 'Note', text: row.why }
}

const handoverPathOf = (h: TmScannedHandover, root: string): string => h.path || handoverPath(root, h.id)

/** What the launcher opens for a session (spec §10.3): a fresh session told to resume from its handover, or --resume. */
export function launchSpecOf(row: RestoreRow, mode: TmResumeMode, compact: boolean, root: string): LaunchSpec {
  const base = { cwd: row.s.cwd, title: row.s.title }
  if (mode === 'fresh' && row.handover !== null) {
    return { ...base, mode: 'fresh', prompt: resumePrompt(row.handover.id, handoverPathOf(row.handover, root)) }
  }
  return { ...base, mode: 'resume', sessionId: row.s.id, ...(compact ? { compact: true } : {}) }
}

/**
 * `c`: one line to paste in a new terminal, built by the launcher itself (its quoting and session-id check), never by
 * concatenation here. Throws on a session id the launcher refuses.
 */
export function copyTextOf(row: RestoreRow, mode: TmResumeMode, root: string, platform: Platform): string {
  return launchCommand(platform, launchSpecOf(row, mode, false, root), undefined)
}

/** A brief is cached per session and close (spec §10.6): a session closed again gets a new one. */
export const briefKey = (row: RestoreRow): string => `brief:${row.s.id}:${Math.round(row.s.closedEpoch)}`
const attr = (text: string): string => text.replace(/[<>"&]/g, ' ')

/** The brief's prompt (spec §10.6): the scanner's digest and what tm said, tag-structured, asking for state / next / blocker. */
export function briefPrompt(row: RestoreRow, digest: { readonly first: string; readonly tail: string }): string {
  const h = row.handover
  return [
    '<task>A developer closed this Claude Code session and wants to resume it. From the transcript excerpt, say where the work stopped and the single next step. Be concrete: name files, task ids, branches, open questions. No preamble.</task>',
    `<session title="${attr(row.s.title)}" branch="${attr(row.s.branch)}" status="${row.status}" task="${attr(row.boundId)}"/>`,
    ...(h === null ? [] : [`<handover id="${attr(h.id)}" status="${attr(row.handoverStatus)}">${h.tldr} | next: ${h.next}</handover>`]),
    `<opening>${digest.first}</opening>`,
    `<transcript_tail>${digest.tail}</transcript_tail>`,
    '<output_format>Reply with exactly: <state>one or two sentences</state><next>one sentence</next><blocker>one sentence or none</blocker></output_format>',
  ].join('\n')
}

/** The reply's tags as `State:` / `Next:` / `Blocker:` lines (a `none` blocker left out); an untagged reply as it came. */
export function briefText(reply: string): string {
  const pick = (tag: string): string | undefined => new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`).exec(reply)?.[1]?.trim()
  const state = pick('state')
  if (state === undefined) return reply.trim()
  const blocker = pick('blocker')
  return [`State: ${state}`, `Next: ${pick('next') ?? '?'}`, ...(blocker !== undefined && !/^none\.?$/i.test(blocker) ? [`Blocker: ${blocker}`] : [])].join('\n')
}

/**
 * The band's restore keycap (spec §10.4): page 1's burst when it holds two or more sessions, at least one unfinished, and
 * closed under 48 hours ago; null otherwise. `epoch` names the burst (its last close, whole seconds).
 */
export function restorePendingOf(bursts: readonly RestoreBurst[], now: number): TmRestorePending | null {
  const burst = bursts[defaultBurst(bursts)]
  if (burst === undefined || burst.ids.length < 2 || burst.open < 1 || now - burst.to >= PENDING_WITHIN_S) return null
  return { epoch: Math.round(burst.to), unfinished: burst.open }
}

/**
 * The Session tab's burst line (spec §7): the burst this session closed in (a --resume keeps its id), or the burst of the
 * session that wrote the handover it resumed from; `others`: the rest of that burst still unfinished and not open.
 */
export function sessionBurstOf(
  bursts: readonly RestoreBurst[],
  rows: readonly RestoreRow[],
  sessionId: string,
  resumedFrom: string | null,
): TmSessionBurst | null {
  const writer = resumedFrom === null ? undefined : rows.find(r => r.s.handovers.some(h => h.id === resumedFrom))?.s.id
  const mine = new Set([sessionId, ...(writer === undefined ? [] : [writer])])
  const burst = bursts.find(b => b.ids.some(id => mine.has(id)))
  if (burst === undefined || burst.ids.length < 2) return null
  const byId = new Map(rows.map(r => [r.s.id, r]))
  const others = burst.ids.filter(id => {
    const r = byId.get(id)
    return !mine.has(id) && r !== undefined && !r.s.running && r.status !== 'done'
  }).length
  return { at: hhmm(burst.from), others }
}

// ── The page's sections (spec §15.6, Restore.dc.html): unfinished, handed off, idle ───────────────────────────────

export type RestoreSection = 'unfinished' | 'handed-off' | 'idle'
export const SECTION_ORDER: readonly RestoreSection[] = ['unfinished', 'handed-off', 'idle']
export const SECTION_OF: Readonly<Record<RestoreStatus, RestoreSection>> = {
  interrupted: 'unfinished',
  wip: 'unfinished',
  unknown: 'unfinished',
  'handed-off': 'handed-off',
  done: 'idle',
}
export const SECTION_LABEL: Readonly<Record<RestoreSection, string>> = { unfinished: 'UNFINISHED', 'handed-off': 'HANDED OFF', idle: 'IDLE' }
export const SECTION_NOTE: Readonly<Record<RestoreSection, string>> = {
  unfinished: 'interrupted or mid-work, no handover',
  'handed-off': 'an open handover carries the next step',
  idle: 'done or ended cleanly',
}

/** A page's rows by section, in section order, each keeping the page's order; empty sections left out. */
export function sectionsOf(rows: readonly RestoreRow[]): { readonly section: RestoreSection; readonly rows: readonly RestoreRow[] }[] {
  return SECTION_ORDER.map(section => ({ section, rows: rows.filter(r => SECTION_OF[r.status] === section) })).filter(s => s.rows.length > 0)
}
