// User intent: a long /goal session's Session tab shows everything it did — its current task, every task it touched (newest
// first, worked on vs only read) and every handover it wrote — read from its own transcript, failing to a line, never a crash.
import { describe, expect, mock, test } from 'claude-code/testing'

import { demoSessionHistory } from '../hooks/demo'
import { sessionScanOf } from '../hooks/restore/model'
import { type RestoreHost, runSessionScan } from '../hooks/restore/scan'
import { createHistoryFlows, FRESH_SESSION_HISTORY, HISTORY_PAGE_SIZE, HISTORY_TTL_MS, sessionHistoryView } from '../hooks/session-history'
import type { TmSessionHistory, TmSessionScan } from '../types'
import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf, widthOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { refsReply } from './fixtures/restore'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, type World, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const TAB = { ...pane('tm-session'), props: { ...pane('tm-session').props, bodyColumns: 112 } }
const NOW_MS = 1_791_700_000_000
const NOW_S = NOW_MS / 1000
const ENV = { USERPROFILE: 'C:\\Users\\me', TEMP: 'C:\\tmp' }
const PATHS = { configDir: 'C:\\cfg', cache: 'C:\\tmp\\tm-restore-scan-cache.json', script: 'C:\\mod\\scanner\\scan.py' }

const SCAN: TmSessionScan = {
  scannedAt: NOW_S,
  found: true,
  tasks: [
    { id: 'a-001', at: NOW_S - 7200, wrote: true },
    { id: 'a-002', at: NOW_S - 600, wrote: false },
    { id: 'a-003', at: NOW_S - 3600, wrote: true },
  ],
  handovers: [
    { id: 'ho-early', path: '', tldr: 'First stretch done', next: 'Start the second', at: NOW_S - 9000 },
    { id: 'ho-late', path: '', tldr: 'Second stretch done', next: 'Wrap up', at: NOW_S - 1200 },
  ],
}

describe('sessionScanOf', () => {
  test('reads the scanner answer field by field and drops what has no id', () => {
    const scan = sessionScanOf({
      scannedAt: 5,
      found: true,
      tasks: [{ id: 't-1', at: 10, wrote: true }, { id: '', at: 1 }, { id: 't-2', at: 'soon' }, 'junk'],
      handovers: [{ id: 'h-1', path: 'p', tldr: 'x', next: 'y', at: 7 }, { tldr: 'no id' }],
    })
    expect(scan).toEqual({
      scannedAt: 5,
      found: true,
      tasks: [{ id: 't-1', at: 10, wrote: true }, { id: 't-2', at: 0, wrote: false }],
      handovers: [{ id: 'h-1', path: 'p', tldr: 'x', next: 'y', at: 7 }],
    })
  })

  test('anything that is not a session answer is null', () => {
    for (const bad of [null, 'x', [], {}, { tasks: 'no' }]) expect(sessionScanOf(bad)).toBeNull()
    expect(sessionScanOf({ tasks: [] })).toEqual({ scannedAt: 0, found: false, tasks: [], handovers: [] })
  })
})

describe('runSessionScan', () => {
  const hostOf = (stdout: string, exitCode = 0): { rh: RestoreHost; runs: (readonly string[])[] } => {
    const runs: (readonly string[])[] = []
    return { runs, rh: { paths: async () => PATHS, run: async argv => (runs.push(argv), { exitCode, stdout, stderr: 'Traceback\nboom\n' }) } }
  }

  test('runs the session mode isolated with this session and its own cache file', async () => {
    const { rh, runs } = hostOf(JSON.stringify({ found: true, tasks: [{ id: 't-1', at: 1, wrote: false }], handovers: [] }))
    const scan = await runSessionScan(rh, { root: 'C:\\work\\proj', sessionId: 'sess-A' })
    expect(runs[0]).toEqual(['python', '-I', PATHS.script, 'session', '--config-dir', 'C:\\cfg', '--root', 'C:\\work\\proj', '--session', 'sess-A', '--cache', 'C:\\tmp\\tm-restore-scan-cache-session.json'])
    expect(scan.tasks.map(t => t.id)).toEqual(['t-1'])
  })

  test('a scanner error, a crash and non-JSON each become a reason', async () => {
    await expect(runSessionScan(hostOf(JSON.stringify({ error: 'unusable session id' })).rh, { root: 'r', sessionId: 'x' })).rejects.toThrow('unusable session id')
    await expect(runSessionScan(hostOf('', 1).rh, { root: 'r', sessionId: 'x' })).rejects.toThrow('boom')
    await expect(runSessionScan(hostOf('oops').rh, { root: 'r', sessionId: 'x' })).rejects.toThrow('the scanner printed no JSON')
    await expect(runSessionScan(hostOf('{"v":1}').rh, { root: 'r', sessionId: 'x' })).rejects.toThrow('no session history')
  })
})

describe('sessionHistoryView', () => {
  const history = (over: Partial<TmSessionHistory> = {}): TmSessionHistory => ({ ...FRESH_SESSION_HISTORY, sessionId: 's', scan: SCAN, finishedAt: 1, ...over })
  const refs = { tasks: { 'a-001': { id: 'a-001', status: 'done', lane: 'full', phase: '', title: 'One' } }, handovers: {} }

  test('lists every touched task newest first, with tm status and title where it has said', () => {
    const v = sessionHistoryView({ history: history({ refs }), snapshot: null, primaryId: '', nowSeconds: NOW_S })
    expect(v.rows.map(r => r.id)).toEqual(['a-002', 'a-003', 'a-001'])
    expect(v.rows.map(r => r.wrote)).toEqual([false, true, true])
    expect(v.rows[2]).toMatchObject({ status: 'done', title: 'One' })
    expect(v.rows[0]).toMatchObject({ status: '', title: '' })
    expect(v.state).toBe('ready')
  })

  test('the live binding shows before any scan lands, and not twice once it has', () => {
    const bound = { taskId: 'live-9', inferred: false, detail: { id: 'live-9', title: 'Live', status: 'in-progress' }, pipeline: null } as never
    const snap = { reachable: true, bound } as never
    const early = sessionHistoryView({ history: history({ scan: null }), snapshot: snap, primaryId: '', nowSeconds: NOW_S })
    expect(early.state).toBe('loading')
    expect(early.rows).toEqual([{ id: 'live-9', status: 'in-progress', title: 'Live', at: NOW_S, wrote: true, current: true }])
    const late = sessionHistoryView({ history: history(), snapshot: snap, primaryId: '', nowSeconds: NOW_S })
    expect(late.rows.filter(r => r.id === 'live-9')).toHaveLength(1)
    expect(late.rows[0]?.id).toBe('live-9')
  })

  test('the other handovers are every one written but the latest drawn, newest first; pages clamp', () => {
    const v = sessionHistoryView({ history: history({ page: 9 }), snapshot: null, primaryId: 'ho-late', nowSeconds: NOW_S })
    expect(v.others.map(h => h.id)).toEqual(['ho-early'])
    expect(v.written).toBe(2)
    expect(v.page).toBe(0)
    const none = sessionHistoryView({ history: history(), snapshot: null, primaryId: '', nowSeconds: NOW_S })
    expect(none.others.map(h => h.id)).toEqual(['ho-late', 'ho-early'])
  })

  test('a failed scan says so and keeps what it had', () => {
    const v = sessionHistoryView({ history: history({ error: 'History unavailable: boom' }), snapshot: null, primaryId: '', nowSeconds: NOW_S })
    expect(v.state).toBe('failed')
    expect(v.rows).toHaveLength(3)
  })
})

describe('createHistoryFlows', () => {
  function fake(scan: () => unknown, now = { ms: NOW_MS }) {
    let state = FRESH_SESSION_HISTORY
    const runs: (readonly string[])[] = []
    const logs: string[] = []
    const flows = createHistoryFlows({
      source: 'tm',
      rh: { paths: async () => PATHS, run: async argv => (runs.push(argv), { exitCode: 0, stdout: JSON.stringify(scan()), stderr: '' }) },
      tm: { call: async () => ({ text: refsReply(['["t","a-001","done","full",""]  One']), isError: false }), sleep: () => new Promise<void>(() => undefined), log: t => void logs.push(t) },
      sessionId: async () => 'sess-A',
      repoRoot: async () => 'C:\\work\\proj',
      now: async () => now.ms,
      read: async () => state,
      write: async change => {
        state = change(state)
      },
    })
    return { flows, runs, logs, now, get state() { return state } }
  }

  test('scans once, resolves the task ids, and asks again only past the TTL or when forced', async () => {
    const f = fake(() => SCAN)
    await f.flows.ask(false)
    expect(f.state.scan?.tasks).toHaveLength(3)
    expect(f.state.refs?.tasks['a-001']?.status).toBe('done')
    expect(f.state).toMatchObject({ pending: false, error: '', sessionId: 'sess-A', finishedAt: NOW_MS })
    await f.flows.ask(false)
    expect(f.runs).toHaveLength(1)
    f.now.ms += HISTORY_TTL_MS + 1
    await f.flows.ask(false)
    expect(f.runs).toHaveLength(2)
    await f.flows.ask(true)
    expect(f.runs).toHaveLength(3)
  })

  test('a failing scan is a line, logged, and the next draw inside the TTL does not retry', async () => {
    const f = fake(() => ({ error: 'transcript unreadable' }))
    await f.flows.ask(false)
    expect(f.state.error).toBe('History unavailable: transcript unreadable')
    expect(f.state.pending).toBe(false)
    expect(f.logs).toHaveLength(1)
    await f.flows.ask(false)
    expect(f.runs).toHaveLength(1)
  })

  test('the demo source runs nothing', async () => {
    const runs: unknown[] = []
    const flows = createHistoryFlows({
      source: 'demo',
      rh: { paths: async () => PATHS, run: async argv => (runs.push(argv), { exitCode: 0, stdout: '{}', stderr: '' }) },
      tm: { call: async () => ({ text: '', isError: false }), sleep: async () => undefined, log: () => undefined },
      sessionId: async () => 's',
      repoRoot: async () => 'r',
      now: async () => 0,
      read: async () => FRESH_SESSION_HISTORY,
      write: async () => undefined,
    })
    await flows.ask(true)
    expect(runs).toEqual([])
  })
})

function tmWorld(world: World, scan: unknown): void {
  world.env = ENV
  world.answer = argv => (argv[3] === 'session' ? { exitCode: 0, stdout: JSON.stringify(scan) } : null)
  world.mcp = (tool, args) =>
    tool === 'backlog_query'
      ? { text: refsReply(['["t","a-001","done","full","p1"]  First task', '["t","a-002","in-review","full","p1"]  Second task']) }
      : R.backlog(tool, args)
}

describe('the Session tab over tm', () => {
  test('draws HISTORY newest first and every handover this session wrote, rows inside the pane', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW_MS })
    const world = worldOf(on, clock, { 'binding:sess-A': { taskId: 'a-001', at: 0 } })
    tmWorld(world, SCAN)
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await clock.settle()
    await ui.redraw()
    expect(world.runs.some(argv => argv[3] === 'session' && argv.includes('sess-A'))).toBe(true)
    for (const text of [`  ${'HISTORY'.padEnd(17)}`, '3 tasks · 2 worked on', 'a-002', 'a-003', 'a-001', /read only/, /worked on/, 'ho-early', 'ho-late']) {
      expect(await ui.find({ type: 'Text', text }), String(text)).toBeDefined()
    }
    expect(await ui.find({ type: 'Text', text: 'Second stretch done' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'First stretch done' })).toBeDefined()
    const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
    for (const row of rows) expect(widthOf(row)).toBeLessThanOrEqual(112 - 4)
  })

  test('a failing scan says why under HISTORY and the pane still draws', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW_MS })
    const world = worldOf(on, clock, { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } })
    world.env = ENV
    world.mcp = R.backlog
    world.answer = argv => (argv[3] === 'session' ? { exitCode: 1, stdout: '', stderr: 'Traceback\nno disk\n' } : null)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /History unavailable: no disk/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: `  ${'HISTORY'.padEnd(17)}` })).toBeDefined() // the live binding still shows
  })

  test('u scans again', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW_MS })
    const world = worldOf(on, clock, { 'binding:sess-A': { taskId: 'a-001', at: 0 } })
    tmWorld(world, SCAN)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await clock.settle()
    const before = world.runs.filter(argv => argv[3] === 'session').length
    await ui.press({ key: 'key:u' })
    await clock.settle()
    expect(world.runs.filter(argv => argv[3] === 'session').length).toBe(before + 1)
  })
})

describe('the Session tab in demo mode', () => {
  test('shows seeded history, pages it, and lists the handovers it wrote', DEMO, async ($, on) => {
    const clock = mock.clock(on, { now: NOW_MS })
    worldOf(on, clock)
    const state = stateOf(on)
    await $.session.start(SESSION)
    const all = demoSessionHistory(NOW_MS).scan?.tasks.length ?? 0
    expect(all).toBeGreaterThan(HISTORY_PAGE_SIZE)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: `${all} tasks · ${all - 3} worked on` })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-029' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'viewer-031' })).toBeUndefined() // page 2
    expect(await ui.find({ type: 'Text', text: '2026-10-13-store-hang-fix' })).toBeDefined()
    await ui.press({ key: 'page-next' })
    await ui.redraw()
    expect((state.get(`${PLUGIN}.sessionHistory`) as TmSessionHistory).page).toBe(1)
    expect(await ui.find({ type: 'Text', text: 'viewer-031' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-029' })).toBeUndefined()
  })

  test('every row fits at 60 and 100 columns', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on, { now: NOW_MS }))
    await $.session.start(SESSION)
    for (const columns of [60, 100]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, props: { ...TAB.props, bodyColumns: columns } })
      const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}`).toBeLessThanOrEqual(columns - 4)
      await ui.unmount()
    }
  })
})
