// User intent: every session names the taskmaster it talks to (local-server-012) — "local · <project> · <version>", "team ·
// <project>", "per-session · <project>" with its reason — in every pane's header always, and in the band only for team and an
// unasked-for per-session server; a fault shows exactly as before, and a normal local connection never makes the band appear.
import { describe, expect, mock, test } from 'claude-code/testing'

import { connectionInBand, connectionLabel, connectionLabels, bandModel, withLastConnection } from '../hooks/model'
import { parseHandovers, parseServer } from '../hooks/parse'
import { connectionOf } from '../hooks/tm'
import type { TmCall } from '../hooks/remote'
import { healthWord, paneFrame, snapshotHealth } from '../hooks/vocab'
import type { TmConnection, TmSnapshot } from '../types'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { testKit, textsOf } from './fixtures/kit-ui'
import { backlog, HANDOVERS_OPEN } from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const SNAP = `${PLUGIN}.snapshot`
const LOCAL: TmConnection = { mode: 'local', project: 'CodeMaestro', version: '7.7.0', reason: '', optedOut: false }
const TEAM: TmConnection = { mode: 'team', project: 'HAY', version: '', reason: '', optedOut: false }
const FALLBACK: TmConnection = { mode: 'per-session', project: 'CodeMaestro', version: '7.7.0', reason: 'the project store is not a ready native store', optedOut: false }
const OPTED: TmConnection = { mode: 'per-session', project: 'CodeMaestro', version: '7.7.0', reason: 'TASKMASTER_LOCAL_SERVER=0', optedOut: true }
const snap = (over: Partial<TmSnapshot>): TmSnapshot => ({
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [],
  queueTotal: 0,
  handovers: [],
  handoversTotal: 0,
  bound: null,
  ...over,
})
const NONE = { handover: null, restore: null, connecting: false }
const SERVER_OBJ = (c: TmConnection) => ({ mode: c.mode, project: c.project, version: c.version, reason: c.reason, opted_out: c.optedOut })
/** The test backlog, its handover listing carrying the server's `server` object. */
const serving = (c: TmConnection | null) => (tool: string, args: Record<string, unknown>) => {
  const r = backlog(tool, args)
  if (tool !== 'backlog_handover_list' || c === null) return r
  return { text: JSON.stringify({ ...(JSON.parse(r.text) as object), server: SERVER_OBJ(c) }) }
}

describe('what the server says', () => {
  test('the listing\'s server object reads as the connection; an older server sends none', () => {
    const withServer = JSON.stringify({ ...(JSON.parse(HANDOVERS_OPEN) as object), server: SERVER_OBJ(OPTED) })
    const read = parseHandovers(withServer, 'C:\\p')
    expect(read.ok && read.value.server).toEqual(OPTED)
    const old = parseHandovers(HANDOVERS_OPEN, 'C:\\p')
    expect(old.ok && old.value.server).toBeNull()
    expect(parseServer({ mode: 'weird' })).toBeNull()
    expect(parseServer('local')).toBeNull()
    expect(parseServer({ mode: 'local' })).toEqual({ mode: 'local', project: '', version: '', reason: '', optedOut: false })
  })

  test('a team clone is team whatever the host sends: its project when it names one, else the clone\'s directory', () => {
    expect(connectionOf(null, true, 'C:\\work\\HAY')).toEqual({ mode: 'team', project: 'HAY', version: '', reason: '', optedOut: false })
    expect(connectionOf({ ...LOCAL, mode: 'team', project: 'hay-main' }, true, 'C:\\work\\HAY')?.project).toBe('hay-main')
    expect(connectionOf(LOCAL, false, 'C:\\x')).toBe(LOCAL)
    expect(connectionOf(null, false, 'C:\\x')).toBeNull()
  })
})

describe('the label', () => {
  test('names the project in each of the three cases (user rulings 1-2)', () => {
    expect(connectionLabel(LOCAL)).toBe('local · CodeMaestro · 7.7.0')
    expect(connectionLabel(TEAM)).toBe('team · HAY')
    expect(connectionLabel(FALLBACK)).toBe('per-session · CodeMaestro')
    expect(connectionLabels(LOCAL)).toEqual(['local · CodeMaestro · 7.7.0', 'local · CodeMaestro · 7.7.0', 'local · CodeM… · 7.7.0', 'local · CodeM…'].filter((v, i, a) => a.indexOf(v) === i))
  })

  test('the band names team and an unasked-for per-session server; never local, never an opt-out (lead ruling 6)', () => {
    expect([LOCAL, TEAM, FALLBACK, OPTED].map(connectionInBand)).toEqual([false, true, true, false])
  })

  test('a fault keeps the last clean read\'s connection beside it; a clean read replaces it', () => {
    const clean = snap({ connection: LOCAL })
    const down = withLastConnection(snap({ reachable: false, reason: 'down' }), clean)
    expect(down.lastConnection).toEqual(LOCAL)
    expect(withLastConnection(snap({ reachable: false }), down).lastConnection).toEqual(LOCAL)
    expect(withLastConnection(snap({ connection: TEAM }), down)).toEqual(snap({ connection: TEAM }))
    expect(withLastConnection(snap({}), null)).toEqual(snap({}))
  })
})

describe('the header', () => {
  const words = (s: TmSnapshot | null, width = 120, connecting = false) => healthWord(snapshotHealth(s, { connecting, demo: false }), 'review', width)

  test('a clean read shows the label in place of tm ok; per-session warns; an older server keeps tm ok', () => {
    expect(words(snap({ connection: LOCAL }))).toBe('local · CodeMaestro · 7.7.0')
    expect(words(snap({ connection: TEAM }))).toBe('team · HAY')
    expect(words(snap({ connection: OPTED }))).toBe('per-session · CodeMaestro')
    expect(snapshotHealth(snap({ connection: FALLBACK }), { connecting: false, demo: false }).kind).toBe('warning')
    expect(snapshotHealth(snap({ connection: LOCAL }), { connecting: false, demo: false }).kind).toBe('success')
    expect(words(snap({}))).toBe('tm ok')
  })

  test('faults show as before; the last label follows where it fits, and goes before the fault does (lead ruling 5)', () => {
    const offline = snap({ reachable: false, lastConnection: LOCAL })
    expect(words(offline)).toBe('offline · local · CodeMaestro · 7.7.0')
    expect(words(offline, 40)).toBe('offline')
    expect(words(snap({ reachable: false }))).toBe('offline')
    expect(words(null, 120, true)).toBe('connecting')
    expect(words(snap({ reachable: false, lastConnection: TEAM }), 120, true)).toBe('connecting · team · HAY')
    expect(snapshotHealth(offline, { connecting: false, demo: false }).kind).toBe('critical')
    expect(snapshotHealth(snap({ connection: LOCAL }), { connecting: false, demo: true }).word).toBe('demo')
  })

  test('narrow, the project shortens, then the version goes, then the plain word', () => {
    const local = snap({ connection: LOCAL })
    expect(words(local, 64)).toBe('local · CodeMaestro · 7.7.0')
    expect(words(local, 58)).toBe('local · CodeM… · 7.7.0')
    expect(words(local, 50)).toBe('local · CodeM…')
    expect(words(local, 40)).toBe('tm ok')
  })

  test('the frame draws the fitted label at the top right', () => {
    const frame = paneFrame(testKit(), { name: 'review', health: snapshotHealth(snap({ connection: LOCAL }), { connecting: false, demo: false }), width: 72 }, [])
    expect(textsOf(frame)).toEqual(expect.arrayContaining(['● ', 'local · CodeMaestro · 7.7.0']))
  })
})

describe('the band model', () => {
  test('a local connection with nothing else to show keeps the band hidden (lead ruling 4)', () => {
    expect(bandModel(snap({ connection: LOCAL }), NONE)).toBeNull()
    expect(bandModel(snap({ connection: OPTED }), NONE)).toBeNull()
  })

  test('team and an unasked-for per-session server show the band, with the label and the reason', () => {
    expect(bandModel(snap({ connection: TEAM }), NONE)?.connection).toEqual({ label: 'team · HAY', labels: ['team · HAY', 'team'], reason: '', warn: false })
    expect(bandModel(snap({ connection: FALLBACK }), NONE)?.connection).toEqual({
      label: 'per-session · CodeMaestro',
      labels: ['per-session · CodeMaestro', 'per-session · CodeM…', 'per-session'],
      reason: 'the project store is not a ready native store',
      warn: true,
    })
  })

  test('a fault never carries the label into the band', () => {
    expect(bandModel(snap({ reachable: false, lastConnection: TEAM }), NONE)).toBeNull()
    expect(bandModel(snap({ reachable: false, lastConnection: TEAM }), { ...NONE, connecting: true })?.connection).toBeUndefined()
  })
})

describe('in a session', () => {
  test('local: every pane header names it and the band stays hidden', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = serving(LOCAL)
    const state = stateOf(on)
    await $.session.start(SESSION)
    await clock.settle()
    expect((state.get(SNAP) as TmSnapshot).connection).toEqual(LOCAL)
    state.set(SNAP, { ...(state.get(SNAP) as TmSnapshot), bound: null, queue: [], queueTotal: 0, handovers: [], handoversTotal: 0 })
    expect(JSON.stringify(await $.ui.render(BAND))).not.toContain('TASKMASTER')
    for (const id of ['tm-review', 'tm-handovers', 'tm-session']) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(id) })
      expect(await ui.find({ type: 'Text', text: 'local · CodeMaestro · 7.7.0' }), id).toBeDefined()
      await ui.unmount()
    }
  })

  test('per-session for a store that is not native: the header warns and the band leads with it', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = serving(FALLBACK)
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await band.find({ type: 'Text', text: /^▲ per-session/ })).toBeDefined()
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await review.find({ type: 'Text', text: 'per-session · CodeMaestro' })).toBeDefined()
    expect(await review.find({ type: 'Text', text: '▲ ' })).toBeDefined()
  })

  test('per-session by opt-out: the header names it, the band does not', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = serving(OPTED)
    const state = stateOf(on)
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await band.find({ type: 'Text', text: /per-session/ })).toBeUndefined()
    state.set(SNAP, { ...(state.get(SNAP) as TmSnapshot), bound: null, queue: [], queueTotal: 0, handovers: [], handoversTotal: 0 })
    expect(JSON.stringify(await $.ui.render(BAND))).not.toContain('TASKMASTER')
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await review.find({ type: 'Text', text: 'per-session · CodeMaestro' })).toBeDefined()
  })

  test('no task: the band says the per-session reason on its one line', TM, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock).mcp = serving(FALLBACK)
    const state = stateOf(on)
    await $.session.start(SESSION)
    await clock.settle()
    state.set(SNAP, { ...(state.get(SNAP) as TmSnapshot), bound: null, queue: [], queueTotal: 0, handovers: [], handoversTotal: 0 })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await band.find({ type: 'Text', text: '▲ per-session · CodeMaestro' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: /^ · the project store is not a ready/ })).toBeDefined()
  })

  test('narrow, the band shortens the label before the task loses its title (review M3)', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = serving({ ...FALLBACK, project: 'CodeMaestroWithAVeryLongProjectName' })
    world.branch = 'feat/tm-audit-030'
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND, viewport: { columns: 90, rows: 40, isFullscreen: true } })
    const tree = JSON.stringify(await band.drawn())
    expect(tree).not.toContain('CodeMaestroWithAVeryLongProjectName')
    expect(tree).toMatch(/▲ per-session/)
    expect(tree).toContain('tm-audit-030')
    expect(tree).toMatch(/"Agent tool/)
  })

  test('a team clone against a host with no server field: team · <clone directory>, in the header and the band', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.exists = path => /^C:[\\/]work[\\/]proj[\\/]\.taskmaster-server\.json$/.test(path) || /[\\/]hooks[\\/]tm_read\.py$/.test(path)
    world.answer = (argv, stdin) => {
      if (!argv.includes('tools')) return null
      const calls = (JSON.parse(stdin ?? '{}') as { calls: TmCall[] }).calls
      return { exitCode: 0, stdout: JSON.stringify({ v: 1, data: { results: calls.map(c => ({ tool: c.tool, text: backlog(c.tool, c.args).text, is_error: false })) }, source: 'server', elapsed_ms: 80 }) }
    }
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await band.find({ type: 'Text', text: 'team · proj' })).toBeDefined()
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await review.find({ type: 'Text', text: 'team · proj' })).toBeDefined()
  })

  test('an older server: tm ok as before, no band of its own', TM, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock).mcp = serving(null)
    await $.session.start(SESSION)
    await clock.settle()
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await review.find({ type: 'Text', text: 'tm ok' })).toBeDefined()
  })

  test('the sidebar names the connection and the per-session reason', TM, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock).mcp = serving(FALLBACK)
    const state = stateOf(on)
    await $.session.start(SESSION)
    await clock.settle()
    state.set(`${PLUGIN}.nav`, { sidebar: true, last: 'review' })
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await review.find({ type: 'Text', text: '▲ per-session · CodeMaestro' })).toBeDefined()
    expect(await review.find({ type: 'Text', text: /the project store is not a/ })).toBeDefined()
  })
})
