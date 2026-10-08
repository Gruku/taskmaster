// User intent: a tm server that is still connecting at session start, or cold and slow on its first reads, is retried with
// backoff (2, 4, 8, 16 s) and shown as a neutral "Connecting to Taskmaster…" — the offline fault only once the retries are
// spent; a refusal still faults at once; retries never start from a draw.
import { describe, expect, mock, test } from 'claude-code/testing'

import { RETRY_DELAYS_MS, singleFlight } from '../hooks/refresh'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { backlog } from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type McpAnswer, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const TURN_END = { answer: '', reason: 'end_turn' } as never
const CONNECTING = 'Connecting to Taskmaster…'
const notConnected = (tool: string): McpAnswer => ({ deny: `no connected MCP tool "${tool}" on a server named "plugin:taskmaster:tm"` })
const flush = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await Promise.resolve()
}

describe('single flight with backoff', () => {
  test('a transient run is retried at 2, 4, 8 and 16 s; the last retry is the final one; then it stops', async () => {
    const finals: boolean[] = []
    const scheduled: { fn: () => void; delay: number }[] = []
    const refresher = singleFlight(
      async final => {
        finals.push(final)
        return 'transient'
      },
      (fn, delay) => scheduled.push({ fn, delay }),
    )
    refresher.request()
    for (let i = 0; i < 5; i += 1) {
      scheduled[i]?.fn()
      await flush()
    }
    expect(scheduled.map(s => s.delay)).toEqual([0, ...RETRY_DELAYS_MS])
    expect(RETRY_DELAYS_MS).toEqual([2000, 4000, 8000, 16000])
    expect(finals).toEqual([false, false, false, false, true])
  })

  test('a request while a retry waits collapses into it; a success resets the backoff', async () => {
    const outcomes: ('transient' | 'done')[] = ['transient', 'done', 'transient']
    const scheduled: { fn: () => void; delay: number }[] = []
    const refresher = singleFlight(
      async () => outcomes.shift() ?? 'done',
      (fn, delay) => scheduled.push({ fn, delay }),
    )
    refresher.request()
    scheduled[0]?.fn()
    await flush()
    expect(scheduled.map(s => s.delay)).toEqual([0, 2000])
    refresher.request()
    refresher.request()
    expect(scheduled).toHaveLength(2)
    scheduled[1]?.fn()
    await flush()
    refresher.request()
    scheduled[2]?.fn()
    await flush()
    expect(scheduled.map(s => s.delay)).toEqual([0, 2000, 0, 2000])
  })
})

describe('cold start in a session', () => {
  test('not connected yet, then connected on retry 1: no fault ever, "Connecting…" meanwhile, then the data', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    let connected = false
    world.mcp = (tool, args) => (connected ? backlog(tool, args) : notConnected(tool))
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses).toEqual([undefined])
    expect(JSON.stringify(await $.ui.render(BAND))).toContain(CONNECTING)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: CONNECTING })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Taskmaster unreachable/ })).toBeUndefined()
    connected = true
    await clock.advance(2000)
    await clock.settle()
    expect(world.statuses).toEqual([undefined, undefined])
    expect(world.statuses).not.toContain('◆ tm offline')
    expect(JSON.stringify(await $.ui.render(BAND))).toContain('4 waiting on you')
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
  })

  test('a cold server that times out, then answers on the retry: no fault, the data shows', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_list_tasks' ? { hangMs: 10_000, text: 'late' } : backlog(tool, args))
    await $.session.start(SESSION)
    await clock.settle()
    await clock.advance(3000)
    await clock.settle()
    expect(world.statuses).toEqual([undefined])
    world.mcp = backlog
    await clock.advance(2000)
    await clock.settle()
    expect(world.statuses).not.toContain('◆ tm offline')
    expect(JSON.stringify(await $.ui.render(BAND))).toContain('4 waiting on you')
  })

  test('every retry fails: the offline fault appears only after the last one, with the reason in the pane', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = tool => notConnected(tool)
    const lists = () => world.calls.filter(c => c.tool === 'backlog_list_tasks').length
    await $.session.start(SESSION)
    await clock.settle()
    for (const delay of [2000, 4000, 8000]) {
      await clock.advance(delay)
      await clock.settle()
      expect(world.statuses).not.toContain('◆ tm offline')
    }
    expect(lists()).toBe(4)
    await clock.advance(16_000)
    await clock.settle()
    expect(lists()).toBe(5)
    expect(world.statuses.at(-1)).toBe('◆ tm offline')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: /Taskmaster unreachable.*no connected MCP tool "backlog_list_tasks"/ })).toBeDefined()
    await clock.advance(60_000)
    await clock.settle()
    expect(lists()).toBe(5)
  })

  test('a refusal faults at once and is not retried', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = () => ({ text: 'Error: No backlog found at C:\\work\\proj\\.taskmaster\\backlog.yaml' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses).toEqual(['◆ tm offline'])
    await clock.advance(60_000)
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks')).toHaveLength(1)
  })

  test('retries never start from a draw: drawing the band and the panes for a minute calls nothing', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = tool => notConnected(tool)
    for (let i = 0; i < 3; i += 1) {
      await $.ui.render(BAND)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
      await ui.drawn()
      await ui.unmount()
      await clock.advance(20_000)
      await clock.settle()
    }
    expect(world.calls).toEqual([])
    expect(world.statuses).toEqual([])
  })

  test('the backoff starts over after a success: a later transient failure retries at 2 s again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    let down = true
    world.mcp = (tool, args) => (down ? notConnected(tool) : backlog(tool, args))
    const lists = () => world.calls.filter(c => c.tool === 'backlog_list_tasks').length
    await $.session.start(SESSION)
    await clock.settle()
    await clock.advance(2000)
    await clock.settle()
    down = false
    await clock.advance(4000)
    await clock.settle()
    expect(lists()).toBe(3)
    down = true
    await $.turn.complete(TURN_END)
    await clock.settle()
    expect(lists()).toBe(4)
    down = false
    await clock.advance(2000)
    await clock.settle()
    expect(lists()).toBe(5)
    expect(world.statuses).not.toContain('◆ tm offline')
    expect(JSON.stringify(await $.ui.render(BAND))).toContain('4 waiting on you')
  })
})
