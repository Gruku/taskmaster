// User intent: the Session tab as the approved prototype draws it (spec §7, §15.7) — TASK, HANDOVER and SESSION in the label
// column, actions named for their subject under what they act on, one filled key (d mark task done), one dim footer — every
// key reaching the same flows as the band, and every row inside the pane (the clipping bug).
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_REASON } from '../hooks/demo'
import { resumePrompt, wtArgv } from '../hooks/launch'
import type { TmSnapshot } from '../types'
import { BAND, command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf, widthOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const TAB = { ...pane('tm-session'), props: { ...pane('tm-session').props, bodyColumns: 112 } }
const SNAP = `${PLUGIN}.snapshot`
const AUDIT = '2026-10-04-audit-fixes-landed'
const AUDIT_BLOCK = `Audit fixes landed on the native branch\nC:\\Users\\demo\\project\\.taskmaster\\handovers\\${AUDIT}.md\nResume: tm-audit — Record the merge for tm-audit-030`
const BOUND = { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } }

// A demo-mode state no flow reaches: the bound task sits beyond the loaded window.
const FAR: TmSnapshot = {
  reachable: true,
  reason: DEMO_REASON,
  fetchedAt: 0,
  queue: [{ kind: 'task', id: 'docs-012', title: 'README', priority: 'low', humanAction: 'Read it', timestamp: '' }],
  queueTotal: 355,
  handovers: [],
  handoversTotal: 0,
  bound: {
    taskId: 'far-001',
    inferred: false,
    detail: { id: 'far-001', title: 'Bound task beyond the loaded window', status: 'in-review', priority: 'medium', lane: 'full', gateState: 'review-gate:pass', branch: '', humanAction: 'Check the far task' },
    pipeline: null,
  },
}

describe('session tab', () => {
  test('/tm-session and the band m open it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-session'))).toEqual({ text: 'Session opened.' })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
    expect(world.opened).toEqual(['tm-session', 'tm-session'])
  })

  test('demo: the frame, the toolbar, TASK, HANDOVER and SESSION in the label column, on both surfaces', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, surface })
      for (const text of [
        'SESSION',
        'demo',
        'this session',
        /^ · main · started \d+m ago$/,
        `  ${'TASK'.padEnd(17)}`,
        '● IN REVIEW',
        'unified-chat-022',
        'full · patch-1-9 · review-gate pass',
        'ready for done',
        'human  ',
        /^Live check on dev/,
        `  ${'HANDOVER'.padEnd(17)}`,
        '↳ RESUMED FROM',
        AUDIT,
        ' · 20h ago',
        'Audit fixes landed on the native branch',
        'next  ',
        'Record the merge for tm-audit-030',
        `  ${'SESSION'.padEnd(17)}`,
        'resumed from ',
      ]) {
        expect(await ui.find({ type: 'Text', text }), `${surface}: ${String(text)}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('every action names its subject; d is the one filled key; the footer holds u and m', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    for (const [key, hotkey, label] of [
      ['session-done', 'd', 'mark task done'],
      ['session-agent', 'a', 'send task to agent'],
      ['session-copy', 'c', 'copy handover'],
      ['session-open', 'h', 'show in handovers'],
      ['session-launch', 'w', 'resume in new tab'],
      ['session-summary', 'i', 'handover decisions·blockers ▸'],
      ['key:u', 'u', ''],
      ['key:m', 'm', ''],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, plain: true, ...(label === '' ? {} : { label }) })
    }
    expect((await ui.find({ type: 'Box', key: 'session-done-box' }))?.props.backgroundColor).toBe('#8a9eeb')
    expect((await ui.find({ type: 'Box', key: 'session-copy-box' }))?.props.backgroundColor).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'session-restore' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'session-review' })).toBeUndefined()
  })

  test('every row fits the pane at 60 and 100 columns (no clipping at the right edge)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [60, 100]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, props: { ...TAB.props, bodyColumns: columns } })
      const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}: ${JSON.stringify(row).slice(0, 120)}`).toBeLessThanOrEqual(columns - 4)
      await ui.unmount()
    }
  })

  test('d asks with y/n under the task and y signs it off, as the band does', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-done' })
    expect(await ui.find({ type: 'Text', text: 'mark unified-chat-022 done?' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'session-yes' })
    expect(await ui.find({ type: 'Text', text: 'No task bound to this session' })).toBeDefined()
  })

  test('a opens the review note on the bound task even when the loaded window does not hold it, and the note goes back on it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(SNAP, FAR)
    const tab = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await tab.press({ key: 'session-agent' })
    expect(world.opened).toEqual(['tm-review'])
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    expect(await review.find({ type: 'Text', text: 'far-001' })).toBeDefined()
    expect(await review.find({ text: 'Check the far task' })).toBeDefined()
    await review.input({ key: 'note', text: 'not yet' })
    expect(world.fills).toEqual(['Back to far-001: not yet'])
  })

  test('c copies the handover, h shows it in handovers, w says what demo would open, i shows decisions and blockers', DEMO, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-copy' })
    await ui.press({ key: 'session-open' })
    await ui.press({ key: 'session-launch' })
    await ui.press({ key: 'session-summary' })
    await clock.settle()
    expect(world.copies).toEqual([AUDIT_BLOCK])
    expect(world.opened).toEqual(['tm-handovers'])
    expect(world.toasts).toEqual([`Copied handover ${AUDIT}`, `Demo: would open handover ${AUDIT}`])
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: '· Audit fixes ride the native branch; no backport to master' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '· tm-audit-030 merge not recorded yet' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-summary' }))?.props.label).toBe('handover decisions·blockers ▾')
  })

  test('tm: w opens a Windows Terminal tab whose first prompt resumes from the handover', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    world.mcp = R.backlog
    world.env = { OS: 'Windows_NT' }
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-launch' })
    await clock.settle()
    const path = `C:\\work\\proj\\.taskmaster\\handovers\\${AUDIT}.md`
    const spec = { cwd: 'C:\\work\\proj', title: `handover ${AUDIT}`, mode: 'fresh' as const, prompt: resumePrompt(AUDIT, path) }
    expect(world.runs.filter(argv => argv[0] === 'wt.exe')).toEqual([wtArgv(spec, undefined)])
    expect(world.toasts).toEqual(['Opened 1 tab'])
  })

  test('offline: the health says offline, d and a are disabled and say why, the empty sections say so', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: 'offline' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-030' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'No handover for this session yet' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'not resumed from a handover' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-done' }))?.props.dimColor).toBe(true)
    await ui.press({ key: 'session-done' })
    await ui.press({ key: 'session-agent' })
    expect(world.toasts).toEqual(['Taskmaster is offline: try again once it answers', 'Taskmaster is offline: try again once it answers'])
    expect(world.opened).toEqual([])
  })

  test('a task only inferred from the branch: d and a are disabled and name it; nothing bound says so', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.branch = 'feat/tm-audit-030-fixes'
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: /inferred from the branch/ })).toBeDefined()
    await ui.press({ key: 'session-done' })
    expect(world.toasts).toEqual(['tm-audit-030 is only inferred from the branch: pick it to bind it to this session'])
    world.branch = 'main'
    await $.turn.complete({ answer: '', reason: 'end_turn' } as never)
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'No task bound to this session' })).toBeDefined()
  })

  test('a known burst adds its line and 4 show burst in restore, which goes to the restore pane', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    world.mcp = R.backlog
    stateOf(on, { [`${PLUGIN}.sessionBurst`]: { at: '16:14', others: 3 } })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: ' · part of the 16:14 burst · 3 others open' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-restore' }))?.props).toMatchObject({ hotkey: '4', label: 'show burst in restore' })
    await ui.press({ key: 'session-restore' })
    expect(world.opened).toEqual(['tm-restore'])
  })

  test('u asks tm again; m opens the sidebar over the tab', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'key:u' })
    expect(world.toasts).toEqual(['taskmaster-mods: demo data has nothing newer to read'])
    await ui.press({ key: 'key:m' })
    expect(state.get(`${PLUGIN}.nav`)).toMatchObject({ sidebar: true })
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'nav:session' })).toBeDefined()
  })
})
