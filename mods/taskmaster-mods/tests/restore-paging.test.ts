// User intent: prove the restore pane opens on the sessions closed together last time, unfinished first, ticks what should
// reopen and picks fresh or --resume sensibly (spec §10, cm-restore's pure helpers and tests, ported), and that the band's
// restore keycap and the Session tab's burst line follow the spec §10.4 rule.
import { describe, expect, test } from 'claude-code/testing'

import type { TmScannedSession } from '../types'
import { NO_TASK } from '../hooks/phases'
import {
  burstsOf,
  ctxTone,
  defaultBurst,
  detailOf,
  formatTokens,
  isTicked,
  pageHeader,
  pageRows,
  pagesOf,
  plain,
  restorePendingOf,
  type RestoreRow,
  type RestoreStatus,
  resumeModeOf,
  rowOf,
  runningRows,
  sectionsOf,
  sessionBurstOf,
  spanLabel,
} from '../hooks/restore/model'
import { NO_REFS, session } from './fixtures/restore'

const row = (id: string, status: RestoreStatus, closedEpoch: number, over: Partial<TmScannedSession> = {}): RestoreRow => ({
  ...rowOf(session(id, { closedEpoch, ...over }), NO_REFS),
  status,
})
const HO = { id: 'h-1', path: 'C:\\p\\.taskmaster\\handovers\\h-1.md', tldr: 'Half done', next: 'Finish the **error rows**' }

// A lone session closed after a burst of three; the burst page keeps it rather than cost it a page of its own.
const ROWS: readonly RestoreRow[] = [
  row('solo', 'wip', 2000),
  row('a', 'done', 1000),
  row('b', 'interrupted', 990),
  row('c', 'wip', 980),
]

describe('bursts', () => {
  test('sessions closed within ten minutes of each other burst together, newest first; open counts the unfinished', () => {
    const bursts = burstsOf(ROWS)
    expect(bursts.map(b => b.ids)).toEqual([['solo'], ['a', 'b', 'c']])
    expect(bursts[1]).toMatchObject({ from: 980, to: 1000, open: 2 })
    expect(defaultBurst(bursts)).toBe(1)
    expect(defaultBurst(burstsOf([row('x', 'wip', 10)]))).toBe(0)
  })

  test('a running session that closed before keeps its burst place but is not open; one that never closed is in none', () => {
    const rows = [...ROWS, row('back', 'wip', 985, { running: true, hasClosed: true }), row('new', 'wip', 5000, { running: true, hasClosed: false })]
    const bursts = burstsOf(rows)
    expect(bursts.map(b => b.ids)).toEqual([['solo'], ['a', 'b', 'back', 'c']])
    expect(bursts[1]?.open).toBe(2)
    expect(runningRows(rows).map(r => r.s.id)).toEqual(['new', 'back'])
  })
})

describe('pages', () => {
  test('page 1 is the closing burst plus what closed after; older sessions page newest first, three a page', () => {
    // Twenty minutes apart: each older session is a burst of its own.
    const older = [5, 4, 3, 2, 1, 0, -1].map(n => row(`o${n}`, n % 2 ? 'done' : 'wip', -10_000 + n * 1200))
    const rows = [...ROWS, ...older]
    const pages = pagesOf(rows, burstsOf(rows), '')
    expect(pages.map(p => p.isBurst)).toEqual([true, false, false, false])
    expect(pages[0]).toMatchObject({ burstSize: 3, since: 1, from: 980, to: 2000 })
    expect(pageRows(rows, pages[0]).map(r => r.s.id)).toEqual(['b', 'solo', 'c', 'a'])
    expect(pageRows(rows, pages[1]).map(r => r.s.id)).toEqual(['o5', 'o4', 'o3'])
    expect(pageRows(rows, pages[2]).map(r => r.s.id)).toEqual(['o2', 'o1', 'o0'])
    expect(pageRows(rows, pages[3]).map(r => r.s.id)).toEqual(['o-1'])
    expect(pagesOf([], [], '')).toEqual([])
    expect(pageRows(rows, undefined)).toEqual([])
  })

  test('within the burst page: interrupted, wip, ?, handed off, done, then newest', () => {
    const rows = [row('d', 'done', 100), row('h', 'handed-off', 101), row('u', 'unknown', 102), row('w', 'wip', 103), row('i', 'interrupted', 99)]
    expect(pageRows(rows, pagesOf(rows, burstsOf(rows), '')[0]).map(r => r.s.id)).toEqual(['i', 'w', 'u', 'h', 'd'])
  })

  test('under a phase: that phase only, newest first, no burst page; running sessions are on no page', () => {
    const rows = [
      { ...row('p1', 'wip', 300), phase: 'patch-1-9' },
      { ...row('p2', 'wip', 200), phase: 'patch-1-9' },
      { ...row('p3', 'wip', 250), phase: 'release-2' },
      { ...row('p4', 'wip', 400, { running: true }), phase: 'patch-1-9' },
      row('n1', 'wip', 100),
    ]
    const bursts = burstsOf(rows)
    expect(pagesOf(rows, bursts, 'patch-1-9')).toEqual([{ ids: ['p1', 'p2'], from: 200, to: 300, isBurst: false, burstSize: 0, since: 0 }])
    expect(pagesOf(rows, bursts, NO_TASK).flatMap(p => p.ids)).toEqual(['n1'])
  })

  test('the header names the burst, its span, its age and what closed since; an older page its span', () => {
    const noon = new Date(2026, 9, 8, 12, 0).getTime() / 1000
    expect(spanLabel(noon - 600, noon, noon)).toBe('today 11:50–12:00')
    expect(spanLabel(noon - 86_400, noon, noon)).toBe('yesterday 12:00 – 12:00')
    expect(pageHeader({ ids: ['a', 'b', 'c', 'd', 'e'], from: noon - 1020, to: noon, isBurst: true, burstSize: 4, since: 1 }, noon + 720)).toBe(
      '4 closed together · today 11:43–12:00 · 12m ago · 1 closed since',
    )
    expect(pageHeader({ ids: ['x'], from: noon, to: noon, isBurst: true, burstSize: 1, since: 0 }, noon)).toBe('Closed alone · today 12:00 · 0m ago')
    expect(pageHeader({ ids: ['x', 'y'], from: noon - 600, to: noon, isBurst: false, burstSize: 0, since: 0 }, noon)).toBe('2 sessions · newest first · today 11:50–12:00')
  })
})

describe('ticks and resume modes', () => {
  test('unfinished sessions start ticked; done, reopened, running and folder-less ones do not; a toggle wins', () => {
    const [, a, b] = ROWS
    expect(isTicked(b!, {}, {})).toBe(true)
    expect(isTicked(a!, {}, {})).toBe(false)
    expect(isTicked(a!, { a: true }, {})).toBe(true)
    expect(isTicked(b!, {}, { b: 1 })).toBe(false)
    expect(isTicked(b!, { b: false }, {})).toBe(false)
    expect(isTicked(row('m', 'wip', 1, { cwdExists: false }), { m: true }, {})).toBe(false)
    expect(isTicked(row('r', 'wip', 1, { running: true }), { r: true }, {})).toBe(false)
  })

  test('with a handover: fresh past 400k context, else --resume, a toggle winning; without one always --resume', () => {
    const heavy = { ...row('h', 'handed-off', 1, { ctx: 431_000 }), handover: HO }
    const light = { ...row('l', 'handed-off', 1, { ctx: 120_000 }), handover: HO }
    expect(resumeModeOf(heavy, {})).toBe('fresh')
    expect(resumeModeOf(light, {})).toBe('resume')
    expect(resumeModeOf(heavy, { h: 'resume' })).toBe('resume')
    expect(resumeModeOf(row('n', 'wip', 1, { ctx: 900_000 }), { n: 'fresh' })).toBe('resume')
  })

  test('context colours, token counts and plain prose', () => {
    expect([ctxTone(399_999), ctxTone(400_000), ctxTone(700_000)]).toEqual([null, 'warning', 'critical'])
    expect(formatTokens(431_000)).toBe('431k')
    expect(formatTokens(1_250_000)).toBe('1.3M')
    expect(plain("fg-store-002's wrap-up: - **Docs:** commit `abc`")).toBe("fg-store-002's wrap-up: Docs: commit abc")
  })

  test('the card line: the handover next step, the interrupted prompt, the last reply, else why', () => {
    expect(detailOf({ ...row('h', 'handed-off', 1), handover: HO })).toEqual({ label: 'Next', text: 'Finish the error rows' })
    expect(detailOf(row('i', 'interrupted', 1, { lastPrompt: 'port the `counter`' }))).toEqual({ label: 'Working on', text: 'port the counter' })
    expect(detailOf(row('w', 'wip', 1))).toEqual({ label: 'Last reply', text: 'Done with step 2 of w.' })
    expect(detailOf({ ...row('z', 'wip', 1, { lastReply: '' }), why: 'no end-session and no open handover' })).toEqual({
      label: 'Note',
      text: 'no end-session and no open handover',
    })
  })
})

describe('the band keycap and the Session tab line', () => {
  const now = 1000 + 3600
  test('a burst of two or more, one unfinished, under 48 hours old; anything less is null', () => {
    expect(restorePendingOf(burstsOf(ROWS), now)).toEqual({ epoch: 1000, unfinished: 2 })
    expect(restorePendingOf(burstsOf(ROWS), 1000 + 48 * 3600)).toBeNull()
    expect(restorePendingOf(burstsOf([row('a', 'done', 1000), row('b', 'done', 990)]), now)).toBeNull()
    expect(restorePendingOf(burstsOf([row('a', 'wip', 1000)]), now)).toBeNull()
    expect(restorePendingOf([], now)).toBeNull()
  })

  test('membership by its own id (a --resume) or by the handover it resumed from (fresh); others are the unfinished rest', () => {
    const at = new Date(2026, 9, 10, 16, 14).getTime() / 1000
    const rows = [
      row('a', 'wip', at + 600, { running: true, hasClosed: true }),
      { ...row('b', 'handed-off', at + 300, { handovers: [HO] }), handover: HO },
      row('c', 'interrupted', at),
      row('d', 'done', at + 100),
    ]
    const bursts = burstsOf(rows)
    expect(sessionBurstOf(bursts, rows, 'a', null)).toEqual({ at: '16:14', others: 2 })
    expect(sessionBurstOf(bursts, rows, 'fresh-new', 'h-1')).toEqual({ at: '16:14', others: 1 })
    expect(sessionBurstOf(bursts, rows, 'stranger', null)).toBeNull()
    expect(sessionBurstOf(burstsOf([row('solo', 'wip', 5)]), [row('solo', 'wip', 5)], 'solo', null)).toBeNull()
  })
})

describe('sections', () => {
  test('a page groups as unfinished (interrupted, wip, ?), handed off, idle; empty ones left out; order kept', () => {
    const rows = [row('i', 'interrupted', 5), row('h', 'handed-off', 4), row('u', 'unknown', 3), row('w', 'wip', 2)]
    expect(sectionsOf(rows).map(s => [s.section, s.rows.map(r => r.s.id)])).toEqual([
      ['unfinished', ['i', 'u', 'w']],
      ['handed-off', ['h']],
    ])
    expect(sectionsOf([])).toEqual([])
  })
})
