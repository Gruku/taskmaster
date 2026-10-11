// User intent: bulk Done / To agent on the selected review cards (spec §8, §11) with today's single-card safety — a y/n with
// n focused, exactly the asked tasks, one write per task at a time, one toast naming every refusal in the server's words.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import { SIGN_OFF } from '../hooks/actions'
import { DEMO_REFUSAL } from '../hooks/demo'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type McpAnswer, type World, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const REVIEW_PANE = pane('tm-review')
const WIDE = { ...REVIEW_PANE, props: { ...REVIEW_PANE.props, bodyColumns: 140 } }
const READS = /^backlog_(list_tasks|issue_list|continuity_items|handover_list|get_task|task_pipeline|query)$/
const writes = (world: World) => world.calls.filter(c => !READS.test(c.tool))
const UC = 'unified-chat-022'
const TA = 'tm-audit-031'
const signed = (id: string) => ({ tool: 'backlog_complete_task', args: { task_id: id, done: SIGN_OFF } })
const status = (id: string) => ({ tool: 'backlog_update_task', args: { task_id: id, field: 'status', value: 'in-progress' } })
const noted = (id: string, note: string) => ({ tool: 'backlog_update_task', args: { task_id: id, next_step: `Back from review: ${note}` } })
const cleared = (id: string) => ({ tool: 'backlog_update_task', args: { task_id: id, field: 'human_action', value: '' } })
const backOk = (args: Record<string, unknown>): string =>
  args.next_step !== undefined ? R.KEYWORD_UPDATED : R.updated(String(args.task_id), String(args.field), String(args.value))

async function tmQueue($: Engine, on: On, over: Record<string, (args: Record<string, unknown>) => McpAnswer>) {
  const clock = mock.clock(on)
  const world = worldOf(on, clock)
  world.mcp = (tool, args) => over[tool]?.(args) ?? R.backlog(tool, args)
  await $.session.start(SESSION)
  await clock.settle()
  await $.command.run(command('tm-review'))
  await clock.settle()
  const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface: 'terminal' })
  return { clock, world, ui }
}

describe('bulk done and to agent', () => {
  test('Done N and To agent N count the selected tasks; at 0 they dim and say why', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Button', key: 'bulk-done' }))?.props).toMatchObject({ label: 'Done 0', hotkey: 'b', dimColor: true })
    await ui.press({ key: 'bulk-done' })
    expect(world.toasts).toEqual(['Nothing selected to done'])
    await ui.press({ key: 'sel:unified-chat-022' })
    await ui.press({ key: 'sel:docs-012' })
    expect((await ui.find({ type: 'Button', key: 'bulk-done' }))?.props).toMatchObject({ label: 'Done 2', hotkey: 'b' })
    expect((await ui.find({ type: 'Button', key: 'bulk-agent' }))?.props).toMatchObject({ label: 'To agent 2', hotkey: 'g' })
  })

  test('Done N asks y/n naming the tasks, n focused and drawn after the pager; n cancels', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: `sel:${TA}` })
    await ui.press({ key: 'bulk-done' })
    expect(await ui.find({ type: 'Text', text: ` Done 2: ${UC}, ${TA}? ` })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'bulk-no' }))?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn.indexOf('"bulk-no"')).toBeGreaterThan(drawn.indexOf('"page-next"'))
    await ui.press({ key: 'bulk-no' })
    expect(await ui.find({ type: 'Button', key: 'bulk-yes' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'bulk-done' }))?.props.label).toBe('Done 2')
  })

  test('y signs each off; one toast names every refusal in the server words; a refused card stays selected', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: `sel:${TA}` })
    await ui.press({ key: 'bulk-done' })
    await ui.press({ key: 'bulk-yes' })
    expect(world.toasts).toEqual([`1 done · 1 refused: ${TA} (${DEMO_REFUSAL})`])
    expect(await ui.find({ type: 'Box', key: `card:${UC}` })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: `sel:${TA}` }))?.props.label).toBe('■')
    expect((await ui.find({ type: 'Button', key: 'bulk-done' }))?.props.label).toBe('Done 1')
    expect(await ui.find({ type: 'Text', text: /1 done this pass/ })).toBeDefined()
  })

  test('y signs off exactly the tasks asked: a selection changed before y changes nothing', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: 'bulk-done' })
    await ui.press({ key: 'sel:docs-012' })
    await ui.press({ key: 'bulk-yes' })
    expect(await ui.find({ type: 'Box', key: `card:${UC}` })).toBeUndefined()
    expect(await ui.find({ type: 'Box', key: 'card:docs-012' })).toBeDefined()
  })

  test('tm: one backlog_complete_task per task, one at a time, in list order; while they run the row says so and no y is drawn', TM, async ($, on) => {
    const { clock, world, ui } = await tmQueue($, on, {
      backlog_complete_task: args => ({ hangMs: 1000, text: args.task_id === TA ? R.GATE_REFUSAL : R.COMPLETED }),
    })
    await ui.press({ key: `sel:${TA}` })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: 'bulk-done' })
    void ui.press({ key: 'bulk-yes' })
    await clock.advance(500)
    expect(writes(world)).toEqual([signed(UC)])
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'bulk-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: ' working' })).toBeDefined()
    await clock.advance(1000)
    await clock.advance(1000)
    await clock.settle()
    expect(writes(world)).toEqual([signed(UC), signed(TA)])
    expect(world.toasts).toEqual([`1 done · 1 refused: ${TA} (${R.GATE_REFUSAL.split('\n')[0]})`])
  })

  test('To agent N takes one note on a page of its own; each task goes back; the prompt holds the note and the pane closes', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface: 'terminal' })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: 'sel:docs-012' })
    await ui.press({ key: 'bulk-agent' })
    expect(await ui.find({ type: 'Input', key: 'bulk-note' })).toBeDefined()
    expect((await ui.findAll({ type: 'Button' })).map(b => b.props.key)).toEqual(['bulk-cancel'])
    await ui.input({ key: 'bulk-note', text: 'tighten the copy' })
    expect(world.toasts).toEqual(['2 back to agent'])
    expect(world.fills).toEqual([`Back to ${UC}, docs-012: tighten the copy`])
    expect(world.closed).toEqual(['tm-review'])
  })

  test('tm: status, note, clear for each task in order; a refusal stops that task only, the pane stays, the prompt names who went', TM, async ($, on) => {
    const illegal = `Error: \`${TA}\`: illegal transition \`in-review\` → \`in-progress\`. Legal: done`
    const { clock, world, ui } = await tmQueue($, on, { backlog_update_task: args => ({ text: args.task_id === TA ? illegal : backOk(args) }) })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: `sel:${TA}` })
    await ui.press({ key: 'bulk-agent' })
    await ui.input({ key: 'bulk-note', text: 'x' })
    await clock.settle()
    expect(writes(world)).toEqual([status(UC), noted(UC, 'x'), cleared(UC), status(TA)])
    expect(world.toasts).toEqual([`1 back to agent · 1 refused: ${TA} (status: ${illegal})`])
    expect(world.fills).toEqual([`Back to ${UC}: x`])
    expect(world.closed).toEqual([])
    expect((await ui.find({ type: 'Button', key: `sel:${TA}` }))?.props.label).toBe('■')
  })

  test('a card y/n and the bulk row never stand together', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: 'bulk-done' })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Button', key: 'bulk-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'confirm-yes' })).toBeDefined()
    await ui.press({ key: 'bulk-done' })
    expect(await ui.find({ type: 'Button', key: 'confirm-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'bulk-yes' })).toBeDefined()
  })

  test('asking a bulk action lets go of tick mode: the digits are the strip again', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: `sel:${UC}` })
    await ui.press({ key: 'tick' })
    expect(await ui.find({ type: 'Text', text: /1-9 ticks a check/ })).toBeDefined()
    await ui.press({ key: 'bulk-done' })
    expect(await ui.find({ type: 'Text', text: /1-9 ticks a check/ })).toBeUndefined()
  })
})
