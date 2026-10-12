// User intent: small, readable restore-pane inputs for tests — a scanned session with every fact defaulted, a scan, and
// backlog_query reply tables shaped as the server prints them — so each test states only the facts it is about.
import type { TmRefs, TmScan, TmScannedSession } from '../../types'

export const ROOT = 'C:\\work\\proj'
/** 2026-10-10 16:31:00 UTC, the demo burst's last close; tests pin `now` from it. */
export const T0 = Date.UTC(2026, 9, 10, 16, 31) / 1000

export function session(id: string, over: Partial<TmScannedSession> = {}): TmScannedSession {
  return {
    id,
    cwd: ROOT,
    cwdExists: true,
    branch: 'main',
    title: `Session ${id}`,
    firstPrompt: `start ${id}`,
    lastPrompt: `keep going on ${id}`,
    lastReply: `Done with step 2 of ${id}.`,
    lastEvent: 'end_turn',
    interrupted: false,
    prompts: 4,
    ctx: 120_000,
    cost: 3,
    closedEpoch: T0,
    hasClosed: true,
    running: false,
    resumedAfterClose: false,
    taskIds: [],
    handovers: [],
    endedSession: false,
    promptsAfterEnd: 0,
    ...over,
  }
}

export const scanOf = (sessions: readonly TmScannedSession[]): TmScan => ({ scannedAt: T0, root: ROOT, sessions })

export const NO_REFS: TmRefs = { tasks: {}, handovers: {} }

/** A refsSql reply: the header, one padded line a row, the footer. */
export function refsReply(rows: readonly string[]): string {
  return ['ref                                           title', ...rows, `${rows.length} ${rows.length === 1 ? 'row' : 'rows'}`].join('\n')
}
