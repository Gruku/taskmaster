// User intent: the live tm surfaces the user approved on top of binding and refresh — a waiting count that is the server's
// total over a 50-row window, handover summaries read on expand (and said unavailable, never loading forever), and the band's
// handover-written notice that copies one Telegram-ready block on `3`.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { type Drawn, elementsOf, widthOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const T = 'mcp__plugin_taskmaster_tm__'
const TURN_END = { answer: '', reason: 'end_turn' } as never
const FIRST = '2026-10-05-shipped-unified-chat-022'
const SECOND = '2026-10-04-audit-fixes-landed'

function toolAnswers(on: On, answers: Record<string, string>): void {
  on('tool.call', ($, e) => {
    const text = answers[String(e.tool).replace(T, '')] ?? 'ok'
    return { result: { content: [{ type: 'text', text }] }, text } as never
  })
}
const call = (tool: string, input: Record<string, unknown>) => ({ tool: T + tool, ...input }) as never
const band = async ($: { ui: { render: (i: typeof BAND) => Promise<unknown> } }): Promise<string> => JSON.stringify(await $.ui.render(BAND))
const textOf = (e: Drawn): string => (e.children ?? []).join('')
const flatOf = (tree: unknown): string[] =>
  elementsOf(tree).flatMap(e => (e.type === 'Text' ? [textOf(e)] : e.type === 'Button' ? [String(e.props?.label ?? '')] : []))
const cardOf = (tree: unknown): Drawn => elementsOf(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round')[0]!
const decisions = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ id: `DEC-${i + 1}`, type: 'decision', title: `D${i + 1}`, next: 'open', action_class: 'decide', timestamp: '' }))

describe('the review window and its count', () => {
  test('each refresh asks for a window of 50: waiting tasks by default, both continuity classes capped at 50', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks').map(c => c.args)).toEqual([
      { status: 'in-review', limit: 50, waiting_on_human: true },
    ])
    expect(world.calls.filter(c => c.tool === 'backlog_continuity_items').map(c => c.args)).toEqual([
      { action_class: 'review', limit: 50 },
      { action_class: 'decide', limit: 50 },
    ])
  })

  test('reviewScope all drops the waiting filter; reviewPhase narrows to that phase', { ...TM, options: { reviewScope: 'all', reviewPhase: ' phase-2 ' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks').map(c => c.args)).toEqual([{ status: 'in-review', limit: 50, phase: 'phase-2' }])
  })

  test("the waiting count is the server's total, not the rows it sent", TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) =>
      tool === 'backlog_list_tasks' ? { text: R.LIST_IN_REVIEW.replace('**2 tasks:**', '**355 tasks (showing first 2):**') } : R.backlog(tool, args)
    await $.session.start(SESSION)
    await clock.settle()
    expect(await band($)).toContain('357 waiting on you')
  })

  test('a continuity reply with no total and a full window reads 50+', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => {
      if (tool === 'backlog_list_tasks') return { text: R.LIST_EMPTY }
      if (tool === 'backlog_continuity_items') return { text: JSON.stringify({ view: 'action', items: args.action_class === 'decide' ? decisions(50) : [] }) }
      return R.backlog(tool, args)
    }
    await $.session.start(SESSION)
    await clock.settle()
    const tree = await band($)
    expect(tree).toContain('50+ waiting on you')
    expect(world.statuses).toEqual([undefined])
  })
})

describe('handover summaries in tm mode', () => {
  test('i reads decisions and blockers once with backlog_handover_get, then shows them', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    await ui.press({ key: 'summary' })
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get').map(c => c.args)).toEqual([
      { handover_id: FIRST, sections: ['decisions', 'blockers'] },
    ])
    const shown = flatOf(cardOf(await ui.drawn()))
    expect(shown.slice(shown.indexOf('DECISIONS'), shown.indexOf('NEXT'))).toEqual([
      'DECISIONS',
      'The cookbook owns the build order, not the pre-build',
      'Pre-build hands the supervisor its full toolset',
      'BLOCKERS',
      'Needs unifiedChatGenerate + unifiedChatBuild on dev',
    ])
    await ui.press({ key: 'summary' })
    await ui.press({ key: 'summary' })
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get')).toHaveLength(1)
  })

  test('a summary the server cannot give reads "summary unavailable", and the next expand asks again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_handover_get' ? { text: `Handover not found: ${String(args.handover_id)}` } : R.backlog(tool, args))
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    await ui.press({ key: 'summary' })
    await clock.settle()
    let shown = flatOf(cardOf(await ui.drawn()))
    expect(shown).toContain('summary unavailable')
    expect(shown).not.toContain('loading summary…')
    world.mcp = R.backlog
    await ui.press({ key: 'summary' })
    await ui.press({ key: 'summary' })
    await clock.settle()
    shown = flatOf(cardOf(await ui.drawn()))
    expect(shown).toContain('DECISIONS')
    expect(shown).not.toContain('summary unavailable')
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get')).toHaveLength(2)
  })

  test('an offline server also reads "summary unavailable"', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    world.mcp = () => 'offline'
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    await ui.press({ key: 'summary' })
    await clock.settle()
    expect(flatOf(cardOf(await ui.drawn()))).toContain('summary unavailable')
  })

  test('a refresh forgets the read summaries: the open one is read again, so an edited handover is never stale', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    await ui.press({ key: 'summary' })
    await clock.settle()
    expect(flatOf(cardOf(await ui.drawn()))).toContain('Pre-build hands the supervisor its full toolset')
    const edited = '## Handover: x\n\n### decisions\n- Edited after the first read'
    world.mcp = (tool, args) => (tool === 'backlog_handover_get' ? { text: edited } : R.backlog(tool, args))
    await $.turn.complete(TURN_END)
    await clock.settle()
    await ui.drawn()
    await clock.settle()
    const shown = flatOf(cardOf(await ui.drawn()))
    expect(shown).toContain('Edited after the first read')
    expect(shown).not.toContain('Pre-build hands the supervisor its full toolset')
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get')).toHaveLength(2)
  })

  test('the pane copies the one Telegram-ready block: tldr, absolute path, Resume: thread — next action', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    await ui.press({ key: `ho:${SECOND}` })
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([
      `Audit fixes landed on the native branch\nC:\\work\\proj\\.taskmaster\\handovers\\${SECOND}.md\nResume: tm-audit — Record the merge for tm-audit-030`,
    ])
  })
})

describe('handover-written notice', () => {
  const CREATE = { tldr: 'Live data wired into the band', next_action: 'Run the Step 9 live check', thread: 'tui-mods' }
  const BLOCK = `Live data wired into the band\nC:\\work\\proj\\.taskmaster\\handovers\\2026-10-06-live-data-wired.md\nResume: tui-mods — Run the Step 9 live check`

  test('the main agent writing a handover puts HANDOVER and its tldr on the band; 3 copies the block, toasts and clears the row', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    toolAnswers(on, { backlog_handover_create: R.HANDOVER_WRITTEN })
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call(call('backlog_handover_create', CREATE))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: 'HANDOVER' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Live data wired into the band' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-copy' }))?.props).toMatchObject({ hotkey: '3', label: 'copy' })
    await ui.press({ key: 'band-copy' })
    expect(world.copies).toEqual([BLOCK])
    expect(world.toasts).toEqual(['Copied handover 2026-10-06-live-data-wired'])
    expect(await ui.find({ type: 'Text', text: 'HANDOVER' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /4 waiting on you/ })).toBeDefined()
  })

  test('with no clipboard the toast carries the path and the row stays', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.copyResult = { isCopied: false, reason: 'no-clipboard' }
    toolAnswers(on, { backlog_handover_create: R.HANDOVER_WRITTEN })
    await $.session.start(SESSION)
    await $.tool.call(call('backlog_handover_create', CREATE))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-copy' })
    expect(world.toasts).toEqual([
      'Clipboard unavailable (no-clipboard): copy by hand from C:\\work\\proj\\.taskmaster\\handovers\\2026-10-06-live-data-wired.md',
    ])
    expect(await ui.find({ type: 'Text', text: 'HANDOVER' })).toBeDefined()
  })

  test('the row offers 2: handovers only while the needs-you row does not, and fits a 30-column band', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    toolAnswers(on, { backlog_handover_create: R.HANDOVER_WRITTEN })
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call(call('backlog_handover_create', CREATE))
    expect(world.statuses).toEqual(['◆ tm offline'])
    const wide = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect((await wide.find({ type: 'Button', key: 'notice-handovers' }))?.props).toMatchObject({ hotkey: '2', label: 'handovers' })
    await wide.unmount()
    const narrow = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: 30 } })
    const tree = await narrow.drawn()
    for (const row of elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row')) expect(widthOf(row)).toBeLessThanOrEqual(30)
    expect(await narrow.find({ type: 'Button', key: 'band-copy' })).toBeDefined()
  })

  test('a refused create, or a subagent writing one, puts nothing on the band; /clear clears the row', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    const answers: Record<string, string> = { backlog_handover_create: 'Error: tldr is required' }
    toolAnswers(on, answers)
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call(call('backlog_handover_create', CREATE))
    expect(await band($)).not.toContain('HANDOVER')
    answers.backlog_handover_create = R.HANDOVER_WRITTEN
    await $.tool.call({ tool: `${T}backlog_handover_create`, ...CREATE, agentId: 'ag1' } as never)
    expect(await band($)).not.toContain('HANDOVER')
    await $.tool.call(call('backlog_handover_create', CREATE))
    expect(await band($)).toContain('HANDOVER')
    await $.classic.SessionStart({ source: 'clear', session_id: 'sess-B' } as never)
    await clock.settle()
    expect(await band($)).not.toContain('HANDOVER')
  })
})
