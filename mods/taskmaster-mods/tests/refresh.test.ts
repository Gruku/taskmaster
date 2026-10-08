// User intent: Taskmaster data stays fresh without hurting the session — one refresh at a time with triggers collapsed, a
// missing or hung server shown as a fault within 3 s, unreadable replies degraded to no data and logged.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { singleFlight } from '../hooks/refresh'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { backlog, LIST_IN_REVIEW } from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const T = 'mcp__plugin_taskmaster_tm__'
const TURN_END = { answer: '', reason: 'end_turn' } as never
const flush = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await Promise.resolve()
}

function toolAnswers(on: On, answers: Record<string, string>): void {
  on('tool.call', ($, e) => {
    const text = answers[String(e.tool).replace(T, '')] ?? 'ok'
    return { result: { content: [{ type: 'text', text }] }, text } as never
  })
}
const call = (tool: string, input: Record<string, unknown>) => ({ tool: T + tool, ...input }) as never

describe('single flight', () => {
  test('one run at a time; requests during a run collapse into one trailing run', async () => {
    let runs = 0
    const gates: (() => void)[] = []
    const scheduled: (() => void)[] = []
    const refresher = singleFlight(
      async () => {
        runs += 1
        await new Promise<void>(resolve => gates.push(resolve))
      },
      fn => scheduled.push(fn),
    )
    refresher.request()
    refresher.request()
    expect(scheduled.length).toBe(1)
    scheduled[0]?.()
    await flush()
    expect(runs).toBe(1)
    refresher.request()
    refresher.request()
    refresher.request()
    gates[0]?.()
    await flush()
    expect(runs).toBe(2)
    gates[1]?.()
    await refresher.idle()
    expect(runs).toBe(2)
    refresher.request()
    expect(scheduled.length).toBe(2)
  })
})

describe('faults', () => {
  test('a refused connection: ◆ tm offline, the band keeps only the bound id, the pane says unreachable', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    toolAnswers(on, { backlog_pick_task: 'Picked `tm-audit-030` — T' })
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call(call('backlog_pick_task', { task_id: 'tm-audit-030' }))
    await clock.settle()
    expect(world.statuses.at(-1)).toBe('◆ tm offline')
    const tree = JSON.stringify(await $.ui.render(BAND))
    expect(tree).toContain('tm-audit-030')
    expect(tree).not.toContain('waiting on you')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: /Taskmaster unreachable/ })).toBeDefined()
  })

  test('a hung server is cut at 3 s and retried (no fault meanwhile); it reads as offline only when the last retry times out', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = () => ({ hangMs: 600_000 })
    await $.session.start(SESSION)
    await clock.settle()
    await clock.advance(2900)
    expect(world.statuses).toEqual([])
    await clock.advance(100)
    expect(world.statuses).toEqual([undefined])
    // runs at 0, 5, 12, 23 and 42 s, each cut 3 s later: the fifth (the last retry) ends at 45 s
    for (const step of [2000, 3000, 4000, 3000, 8000, 3000, 16_000]) {
      await clock.advance(step)
      await clock.settle()
    }
    expect(world.statuses).not.toContain('◆ tm offline')
    await clock.advance(2900)
    await clock.settle()
    expect(world.statuses).not.toContain('◆ tm offline')
    await clock.advance(100)
    await clock.settle()
    expect(world.statuses.at(-1)).toBe('◆ tm offline')
  })

  test('a server that answers one call at a time (1 s each) is read one call after another, never cut at 3 s', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = backlog
    world.serialMs = 1000
    await $.session.start(SESSION)
    for (let s = 0; s < 60; s += 1) {
      await clock.advance(1000)
      await clock.settle()
    }
    expect(world.statuses).not.toContain('◆ tm offline')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: /Taskmaster unreachable/ })).toBeUndefined()
  })

  test('replies wrapped as {"result": text} (the engine passing on the structured result) read as the text inside', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => ({ text: JSON.stringify({ result: backlog(tool, args).text }) })
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses).not.toContain('ⓘ tm reply unreadable')
    expect(world.statuses).not.toContain('◆ tm offline')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    expect(await ui.find({ type: 'Text', text: /No open handovers/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /Taskmaster unreachable/ })).toBeUndefined()
  })

  test('"Error: … no backlog" from the server reads as offline, with the reason in the pane', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = () => ({ text: 'Error: No backlog found at C:\\work\\proj\\.taskmaster\\backlog.yaml' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses.at(-1)).toBe('◆ tm offline')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: /No backlog found/ })).toBeDefined()
  })

  test('an unreadable reply: ⓘ on the status line, the raw text in the debug log, a good refresh clears the line', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_list_tasks' ? { text: '**garbage' } : backlog(tool, args))
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses.at(-1)).toBe('ⓘ tm reply unreadable')
    expect(world.logs.some(line => line.to === 'debug' && line.text.includes('**garbage'))).toBe(true)
    world.mcp = backlog
    await $.turn.complete(TURN_END)
    await clock.settle()
    expect(world.statuses.at(-1)).toBeUndefined()
  })
})

describe('triggers', () => {
  test('turn ends during a slow refresh collapse into one more; subagent turns and read-only calls trigger nothing', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_list_tasks' ? { hangMs: 1000, text: LIST_IN_REVIEW } : backlog(tool, args))
    toolAnswers(on, {})
    const lists = () => world.calls.filter(c => c.tool === 'backlog_list_tasks').length
    await $.session.start(SESSION)
    await clock.settle()
    expect(lists()).toBe(1)
    for (let i = 0; i < 3; i += 1) await $.turn.complete(TURN_END)
    await clock.advance(1000)
    expect(lists()).toBe(2)
    await clock.advance(1000)
    await $.turn.complete({ answer: '', reason: 'end_turn', agentId: 'ag1' } as never)
    await $.tool.call(call('backlog_get_task', { task_id: 'tm-audit-030' }))
    await clock.settle()
    expect(lists()).toBe(2)
    await $.tool.call(call('backlog_update_task', { task_id: 'tm-audit-031', field: 'tldr', value: 'x' }))
    await clock.settle()
    expect(lists()).toBe(3)
  })
})
