// User intent: prove what the restore pane's keys and the session-start scan do (spec §10, §10.4): open scans and names the
// burst, a failed scan says why, tm's silence reads `?`, the start scan waits for tm and feeds the band and the Session tab,
// and ticks, modes, compact and paging change only the pane's own state.
import { describe, expect, test } from 'claude-code/testing'

import { createRestoreFlows, RESOLVE_RETRY_MS, SCANNING } from '../hooks/restore/flows'
import { paneRows } from '../hooks/restore/model'
import { fakeRestore, refsReply, ROOT, scanOf, session, T0 } from './fixtures/restore'

const HO = { id: 'h-1', path: 'C:\\work\\proj\\.taskmaster\\handovers\\h-1.md', tldr: 'Half done', next: 'Finish the rows' }
// Three sessions closed together half an hour before `now` (one handed off, one interrupted, one done), one an hour earlier.
const SCAN = scanOf([
  session('u5', { closedEpoch: T0, taskIds: ['t-1'], handovers: [HO], ctx: 431_000 }),
  session('audit', { closedEpoch: T0 - 120, taskIds: ['t-2'], interrupted: true }),
  session('counter', { closedEpoch: T0 - 300, endedSession: true }),
  session('old', { closedEpoch: T0 - 3600 }),
  session('live', { closedEpoch: T0 + 1800, running: true, hasClosed: false }),
])
const REFS = refsReply(['["t","t-1","in-review","full","p1"]  Sign off', '["t","t-2","in-progress","full","p1"]  Supersede', '["h","h-1","open"]'])

describe('open and scan', () => {
  test('/tm-restore opens at page 1, asks for the phases, scans the repo and names the burst', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    f.view = { ...f.view, page: 2, focus: 'old' }
    const flows = createRestoreFlows(f.deps)
    expect(await flows.open(4)).toBe('Restore opened: 3 sessions closed together, 2 unfinished.')
    expect([f.opened, f.phasesAsked]).toEqual([1, 1])
    expect(f.view).toMatchObject({ page: 0, focus: '', busy: '' })
    expect(f.runs[0]).toEqual(['python', '-I', 'C:\\mod\\scanner\\scan.py', 'scan', '--config-dir', 'C:\\Users\\me\\.claude', '--root', ROOT, '--days', '4', '--cache', 'C:\\tmp\\tm-restore-scan-cache.json'])
    expect(f.data).toMatchObject({ scan: SCAN, error: '', days: 4 })
    expect(paneRows(f.data, 'me').map(r => [r.s.id, r.status])).toEqual([
      ['u5', 'handed-off'],
      ['audit', 'interrupted'],
      ['counter', 'done'],
      ['old', 'wip'],
      ['live', 'wip'],
    ])
  })

  test('the busy line shows while the scan runs', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    const seen: string[] = []
    const write = f.deps.write
    ;(f.deps as { write: typeof write }).write = { ...write, data: async change => {
      seen.push(f.view.busy)
      await write.data(change)
    } }
    await createRestoreFlows(f.deps).open(4)
    expect(seen.every(busy => busy === SCANNING)).toBe(true)
    expect(f.view.busy).toBe('')
  })

  test('a scan that fails says why in the pane and the answer, and is logged', async () => {
    const f = fakeRestore({ scan: new Error('ValueError: bad root') })
    expect(await createRestoreFlows(f.deps).open(4)).toBe('Scan failed: ValueError: bad root')
    expect(f.data.error).toBe('Scan failed: ValueError: bad root')
    expect(f.logs).toContain('taskmaster-mods: restore scan failed: ValueError: bad root')
    expect(f.view.busy).toBe('')
  })

  test('tm not answering leaves the facts listed with status ?', async () => {
    const f = fakeRestore({ scan: SCAN, tm: ['offline'] })
    await createRestoreFlows(f.deps).open(4)
    expect(f.data.refs).toBeNull()
    expect(paneRows(f.data, 'me').map(r => r.status)).toEqual(['unknown', 'unknown', 'done', 'unknown', 'unknown'])
    expect(f.waits).toEqual([])
  })

  test('u rescans the same window and keeps the page', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    const flows = createRestoreFlows(f.deps)
    await flows.open(7)
    await flows.turnPage(1)
    await flows.rescan()
    expect(f.runs.at(-1)).toContain('7')
    expect(f.view.page).toBe(1)
  })
})

describe('the session-start scan (spec §10.4)', () => {
  test('3 days, tm asked again while it connects, then the band keycap and the burst line are written', async () => {
    const f = fakeRestore({ scan: SCAN, tm: ['offline', 'offline', REFS] })
    await createRestoreFlows(f.deps).startScan()
    expect(f.runs[0]).toContain('3')
    expect(f.waits).toEqual([RESOLVE_RETRY_MS, RESOLVE_RETRY_MS])
    expect(f.data.refs).not.toBeNull()
    expect(f.signals).toEqual([{ pending: { epoch: T0, unfinished: 2 }, burst: null }])
  })

  test('a session resumed from a burst member is told its burst and how many others are open', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS], self: 'fresh-one', store: { 'resumed-from:fresh-one': 'h-1' } })
    await createRestoreFlows(f.deps).startScan()
    expect(f.signals.at(-1)?.burst).toEqual({ at: expect.any(String), others: 1 })
  })

  test('demo reads the demo sessions: a burst of four, three unfinished', async () => {
    const f = fakeRestore({ source: 'demo' })
    await createRestoreFlows(f.deps).startScan()
    expect(f.runs).toEqual([])
    expect(f.data.refs).not.toBeNull()
    expect(f.signals.at(-1)?.pending).toMatchObject({ unfinished: 3 })
  })
})

describe('the pane state', () => {
  test('s flips a tick from its default; a session that cannot reopen says why', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    const flows = createRestoreFlows(f.deps)
    await flows.open(4)
    await flows.toggle('audit')
    await flows.toggle('counter')
    expect(f.view.selected).toEqual({ audit: false, counter: true })
    await flows.toggle('live')
    expect(f.toasts).toEqual(['Session live is open in another terminal'])
    await flows.select({ audit: true, old: false })
    expect(f.view.selected).toEqual({ audit: true, counter: true, old: false })
  })

  test('m flips fresh and --resume from the default; without a handover it says so', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    const flows = createRestoreFlows(f.deps)
    await flows.open(4)
    await flows.toggleMode('u5')
    expect(f.view.modes).toEqual({ u5: 'resume' })
    await flows.toggleMode('u5')
    expect(f.view.modes).toEqual({ u5: 'fresh' })
    await flows.toggleMode('audit')
    expect(f.toasts).toEqual(['Session audit wrote no handover: it reopens with --resume'])
  })

  test('z toggles compact; a page turn drops the focus; focus moves', async () => {
    const f = fakeRestore({ scan: SCAN, tm: [REFS] })
    const flows = createRestoreFlows(f.deps)
    await flows.toggleCompact()
    await flows.focus('audit')
    expect(f.view).toMatchObject({ compact: true, focus: 'audit' })
    await flows.turnPage(1)
    expect(f.view).toMatchObject({ page: 1, focus: '' })
  })
})
