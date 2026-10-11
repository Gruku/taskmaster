// User intent: the Session tab as a person uses it (spec §7) — one ◆ TASKMASTER panel with this session's task (d done, a to
// agent), its handover (copy, open in handovers, new tab, decisions and blockers) and where it came from, each section saying
// so when empty, and every key reaching the same flows as the band.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_REASON } from '../hooks/demo'
import { resumePrompt, wtArgv } from '../hooks/launch'
import type { TmSnapshot } from '../types'
import { BAND, command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
// Wide enough that no line of the panel is cut (the panel takes the pane's width).
const TAB = { ...pane('tm-session'), props: { ...pane('tm-session').props, bodyColumns: 100 } }
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
  test('/tm-session and the band 3 open it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-session'))).toEqual({ text: 'Session opened.' })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-session' })
    expect(world.opened).toEqual(['tm-session', 'tm-session'])
  })

  test('demo: TASK, HANDOVER and SESSION in one ◆ TASKMASTER · SESSION panel, on both surfaces', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, surface })
      for (const text of [
        /◆ TASKMASTER · SESSION/,
        ' demo',
        'TASK',
        ' IN REVIEW ',
        'unified-chat-022',
        'full · patch-1-9 · review-gate pass ✓ · ready for done',
        /^human: Live check on dev/,
        'HANDOVER',
        ' RESUMED FROM ',
        /^2026-10-04-audit-fixes-landed · 20h ago$/,
        'Audit fixes landed on the native branch',
        'Next: Record the merge for tm-audit-030',
        'SESSION',
        `resumed from ${AUDIT}`,
      ]) {
        expect(await ui.find({ type: 'Text', text }), `${surface}: ${String(text)}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('every action is a keycap with its key', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    for (const [key, hotkey, label] of [
      ['session-done', 'd', 'done'],
      ['session-agent', 'a', 'agent'],
      ['session-copy', 'c', 'copy'],
      ['session-open', 'h', 'in handovers'],
      ['session-launch', 'w', 'new tab'],
      ['session-summary', 'i', 'decisions·blockers'],
      ['session-review', '1', '5 waiting on you'],
      ['session-handovers', '2', '6 handovers'],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, label, plain: true })
    }
    expect(await ui.find({ type: 'Button', key: 'session-restore' })).toBeUndefined()
  })

  test('d asks with y/n in the tab and y signs the task off, as the band does', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-done' })
    expect(await ui.find({ type: 'Text', text: ' done unified-chat-022? ' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'session-yes' })
    expect(await ui.find({ type: 'Text', text: 'No task bound to this session' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-review' }))?.props.label).toBe('4 waiting on you')
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

  test('c copies the handover, h opens it in handovers, w says what demo would open, i shows its decisions and blockers', DEMO, async ($, on) => {
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
    expect((await ui.find({ type: 'Box', key: 'session-summary-box' }))?.props.backgroundColor).toBe('#4a5590')
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

  test('offline: the health says offline, d and a dim and say why, the empty sections say so', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: ' offline' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-030' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'No handover for this session yet' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'not resumed from a handover' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-done' }))?.props.dimColor).toBe(true)
    await ui.press({ key: 'session-done' })
    await ui.press({ key: 'session-agent' })
    expect(world.toasts).toEqual(['Taskmaster is offline: try again once it answers', 'Taskmaster is offline: try again once it answers'])
    expect(world.opened).toEqual([])
  })

  test('a task only inferred from the branch: d and a dim and name it; nothing bound says so', TM, async ($, on) => {
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

  test('a restore burst adds 4 · restore and the burst line; 4 opens restore', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    world.mcp = R.backlog
    stateOf(on, { [`${PLUGIN}.restorePending`]: { epoch: 1_760_000_000, unfinished: 3 }, [`${PLUGIN}.sessionBurst`]: { at: '16:14', others: 3 } })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: 'not resumed from a handover · part of the 16:14 burst (3 others open)' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-restore' }))?.props).toMatchObject({ hotkey: '4', label: '3 unfinished · restore' })
    await ui.press({ key: 'session-restore' })
    expect(world.opened).toEqual(['tm-restore'])
  })
})
