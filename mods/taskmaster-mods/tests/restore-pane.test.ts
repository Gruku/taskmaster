// User intent: the restore pane as a person uses it (spec §10): /tm-restore scans this repo, tm names each session's status,
// the pane draws on the terminal and the desktop, its keys tick, move, flip modes and page, a --resume is recorded before it
// launches, Brief asks the configured model — and the session-start scan feeds the sidebar's Restore entry (spec §10.4).
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { refsReply, scanOf, session } from './fixtures/restore'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, type World, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const RESTORE_PANE = { ...pane('tm-restore'), props: { ...pane('tm-restore').props, bodyColumns: 120 } }
const ENV = { USERPROFILE: 'C:\\Users\\me', TEMP: 'C:\\tmp' }
const HO = { id: 'h-1', path: 'C:\\work\\proj\\.taskmaster\\handovers\\h-1.md', tldr: 'Half done', next: 'Finish the rows' }

/** Three sessions closed together a quarter hour before the mock clock's now, plus an older one. */
function tmWorld(world: World, nowS: number): void {
  const scan = scanOf([
    session('u5', { closedEpoch: nowS - 900, title: 'U5 brief approval', handovers: [HO], ctx: 120_000, taskIds: ['t-1'], cwd: world.root }),
    session('audit', { closedEpoch: nowS - 1000, title: 'Audit supersede', interrupted: true, taskIds: ['t-2'], cwd: world.root }),
    session('counter', { closedEpoch: nowS - 1100, title: 'Row counter', endedSession: true, cwd: world.root }),
    session('old', { closedEpoch: nowS - 90_000, title: 'Old work', cwd: world.root }),
  ])
  world.answer = argv =>
    String(argv[2] ?? '').endsWith('scan.py')
      ? argv[3] === 'digest'
        ? { exitCode: 0, stdout: JSON.stringify({ first: 'USER: start', tail: 'ASSISTANT: stopped' }) }
        : { exitCode: 0, stdout: JSON.stringify(scan) }
      : null
  world.mcp = (tool, args) =>
    tool !== 'backlog_query'
      ? 'offline'
      : String(args.sql).includes("p.kind='phase'")
        ? { text: 'phase                                   name\n["p1","active",1,0,0,"2026-10-01"]    Patch 1 — Restore\n1 row' }
        : { text: refsReply(['["t","t-1","in-review","full","p1"]  Sign off', '["t","t-2","in-progress","full","p1"]  Supersede', '["h","h-1","open"]']) }
}

describe('/tm-restore in demo mode', () => {
  test('opens the pane, names the burst, and draws on the terminal and the desktop without running the scanner', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-restore'))).toEqual({ text: 'Restore opened: 4 sessions closed together, 3 unfinished.' })
    expect(world.opened).toEqual(['tm-restore'])
    expect(world.runs.some(argv => argv.some(a => a.endsWith('scan.py')))).toBe(false)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE, surface })
      expect((await ui.find({ type: 'Button', key: 'restore-resume-all' }))?.props).toMatchObject({ hotkey: 'b', label: 'resume 4 sessions' })
      expect(await ui.find({ type: 'Button', key: 'resume:demo-audit' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /4 closed together/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: 'RUNNING NOW' })).toBeDefined()
      await ui.unmount()
    }
  })

  test('the pane draws the demo sessions even before the command ran', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    expect(await ui.find({ type: 'Button', key: 'sel:demo-audit' })).toBeDefined()
  })
})

describe('/tm-restore over tm', () => {
  test('scans this repo isolated, resolves the ids with one backlog_query, and says what it found', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    expect(await $.command.run({ ...command('tm-restore'), args: '2' })).toEqual({ text: 'Restore opened: 3 sessions closed together, 2 unfinished.' })
    const scanRun = world.runs.find(argv => argv[3] === 'scan')
    expect(scanRun?.slice(0, 2)).toEqual(['python', '-I'])
    expect(scanRun?.slice(3)).toEqual(['scan', '--config-dir', 'C:\\Users\\me/.claude', '--root', world.root, '--days', '2', '--cache', 'C:\\tmp/tm-restore-scan-cache.json'])
    expect(world.calls.filter(c => c.tool === 'backlog_query')).toHaveLength(1)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    expect(await ui.find({ type: 'Text', text: '▲ INTERRUPTED' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '● HANDED OFF' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' 1' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'restore-resume-all' }))?.props?.label).toBe('resume 2 sessions')
  })

  test('keys: s ticks, j moves the open card, v flips the mode, z compacts, u rescans, n pages, a phase narrows', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    await $.session.start(SESSION)
    await $.command.run(command('tm-restore'))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    const label = async (key: string) => (await ui.find({ type: 'Button', key }))?.props?.label
    await ui.press({ key: 'sel:audit' })
    await clock.settle()
    await ui.redraw()
    expect(await label('restore-resume-all')).toBe('resume 1 session')
    await ui.press({ key: 'restore-down' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'resume:u5' })).toBeDefined()
    expect(await label('mode:u5')).toBe('switch resume mode')
    expect(await ui.find({ type: 'Text', text: ' --resume ' })).toBeDefined()
    await ui.press({ key: 'mode:u5' })
    await clock.settle()
    await ui.redraw()
    expect(await label('mode:u5')).toBe('switch resume mode')
    expect(await ui.find({ type: 'Text', text: ' fresh from h-1 ' })).toBeDefined()
    await ui.press({ key: 'restore-compact' })
    await clock.settle()
    await ui.redraw()
    expect(await label('restore-compact')).toBe('◐ compact')
    // u goes through the shared TmFlows.refresh('restore'): one more scan of the same window.
    const scans = () => world.runs.filter(argv => argv[3] === 'scan').length
    const before = scans()
    await ui.press({ key: 'refresh' })
    await clock.settle()
    expect(scans()).toBe(before + 1)
    await ui.press({ key: 'page-next' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'sel:old' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'sel:u5' })).toBeUndefined()
    // A phase narrows to that phase's sessions and starts at its page 1.
    await ui.press({ key: 'phase-field' })
    await clock.settle()
    await ui.redraw()
    expect(await label('phase-pick:p1')).toMatch(/^Patch 1 · active +2$/)
    await ui.press({ key: 'phase-pick:p1' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'sel:u5' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'sel:counter' })).toBeUndefined()
    expect(world.store.get(`phase:restore:${world.root}`)).toEqual({ phase: 'p1' })
  })

  test('r on a --resume session records resumed-from before the launcher opens it', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    await $.session.start(SESSION)
    await $.command.run(command('tm-restore'))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    await ui.press({ key: 'restore-down' })
    await clock.settle()
    await ui.redraw()
    await ui.press({ key: 'resume:u5' })
    await clock.settle()
    expect(world.store.get('resumed-from:u5')).toBe('h-1')
  })

  test('b asks the configured brief model and shows State / Next under the card', { ...TM, options: { briefModel: 'claude-sonnet-5-5' } }, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    const models: string[] = []
    on('model.complete', ($$, e) => {
      models.push(e.model)
      return { value: { isAnswered: true, text: '<state>Stopped mid-port.</state><next>Finish the port.</next><blocker>none</blocker>', usage: {} } } as never
    })
    await $.session.start(SESSION)
    await $.command.run(command('tm-restore'))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    await ui.press({ key: 'brief:audit' })
    await clock.settle()
    await ui.redraw()
    expect(models).toEqual(['claude-sonnet-5-5'])
    expect(await ui.find({ type: 'Text', text: 'Finish the port.' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'brief:audit' }))?.props?.label).toBe('hide brief')
  })

  test('a scanner that cannot run says so in the pane and the answer', TM, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    world.env = ENV
    world.answer = argv => (String(argv[2] ?? '').endsWith('scan.py') ? { exitCode: 1, stdout: '', stderr: 'boom\nOSError: no projects folder' } : null)
    expect((await $.command.run(command('tm-restore'))).text).toBe('Scan failed: OSError: no projects folder')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    expect(await ui.find({ type: 'Text', text: ' · OSError: no projects folder' })).toBeDefined()
  })

  test('/tm-restore closes another open Taskmaster pane first, and the sidebar lists Restore live', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    await $.session.start(SESSION)
    await $.command.run(command('tm-session'))
    await $.command.run(command('tm-restore'))
    expect(world.closed).toEqual(['tm-session'])
    expect(world.opened.slice(-1)).toEqual(['tm-restore'])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    await ui.press({ key: 'key:m' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'nav:restore' }))?.props?.autoFocus).toBe(true)
  })
})

describe('the session-start scan (spec §10.4)', () => {
  test('an interactive start scans 3 days off the start and writes the Restore entry\'s burst and the burst line', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    const state = stateOf(on)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    await $.session.start(SESSION)
    expect(world.runs.some(argv => argv[3] === 'scan')).toBe(false)
    await clock.settle()
    expect(world.runs.find(argv => argv[3] === 'scan')).toContain('3')
    expect(state.get('taskmaster-mods.restorePending')).toEqual({ epoch: 1_791_700_000 - 900, unfinished: 2 })
    expect(state.get('taskmaster-mods.sessionBurst')).toBeNull()
  })

  test('a -p run scans nothing', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start({ ...SESSION, surface: null, isInteractive: false })
    await clock.settle()
    expect(world.runs.some(argv => argv[3] === 'scan')).toBe(false)
  })
})
