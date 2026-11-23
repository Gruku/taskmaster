// User intent: the mods keep working when the plugin's tm is the stdio bridge to the one local server per project: every call
// path (the review/band reads, the writes, the handover guard, the restore refs) is MCP only, a restart blip shows as
// "connecting" and never as offline, and a write the bridge says may have committed is never retried or silently dropped.
import { describe, expect, mock, test } from 'claude-code/testing'

import { tmActions } from '../hooks/actions'
import type { TmHost } from '../hooks/host'
import { callTm, TM_WRITE_TIMEOUT_MS, TmUnreachable } from '../hooks/tm'
import { BAND, command, pane, PLUGIN, SESSION, waitingOf } from './fixtures/inputs'
import { bridgeMayHaveCommitted, bridgeNotSent } from './fixtures/bridge-text'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type McpAnswer, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const CONNECTING = 'Connecting to Taskmaster…'
const READ_TOOLS = ['backlog_list_tasks', 'backlog_issue_list', 'backlog_issue_list', 'backlog_continuity_items', 'backlog_continuity_items', 'backlog_handover_list']
const ID = 'unified-chat-022'

describe('through the bridge: reads', () => {
  test('a refresh is MCP calls one after another and never runs the team reader', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.calls.map(c => c.tool)).toEqual(READ_TOOLS)
    expect(world.runs.filter(argv => argv.some(part => part.includes('tm_read.py')))).toEqual([])
    expect(await waitingOf($)).toContain('4 waiting on you')
  })

  for (const [name, words] of [
    ['the call was not sent', bridgeNotSent],
    ['the call may have committed', bridgeMayHaveCommitted],
  ] as const) {
    test(`the bridge restarting (${name}): "connecting", never offline, and the data shows on the retry`, TM, async ($, on) => {
      const clock = mock.clock(on)
      const world = worldOf(on, clock)
      let up = false
      world.mcp = (tool, args): McpAnswer => (up ? R.backlog(tool, args) : { deny: words(tool) })
      await $.session.start(SESSION)
      await clock.settle()
      expect(world.statuses).toEqual([undefined])
      expect(JSON.stringify(await $.ui.render(BAND))).toContain(CONNECTING)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
      expect(await ui.find({ type: 'Text', text: '▲ tm offline' })).toBeUndefined()
      up = true
      await clock.advance(2000)
      await clock.settle()
      expect(world.statuses).not.toContain('◆ tm offline')
      expect(await waitingOf($)).toContain('4 waiting on you')
    })
  }

  test('a bridge that stays down faults offline only after the last retry', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool): McpAnswer => ({ deny: bridgeNotSent(tool) })
    await $.session.start(SESSION)
    await clock.settle()
    for (const delay of [2000, 4000, 8000]) {
      await clock.advance(delay)
      await clock.settle()
      expect(world.statuses).not.toContain('◆ tm offline')
    }
    await clock.advance(16_000)
    await clock.settle()
    expect(world.statuses).toContain('◆ tm offline')
  })
})

describe('through the bridge: writes', () => {
  const host = (words: (tool: string) => string): Pick<TmHost, 'call' | 'sleep' | 'remote'> => ({
    call: async tool => {
      throw new Error(words(tool))
    },
    sleep: (_ms, signal) => new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('stopped')))),
    remote: async () => null,
  })

  test('a write cut after it was sent says it may have committed, names the step, and is sent once', async () => {
    let sent = 0
    const real = host(bridgeMayHaveCommitted)
    const actions = tmActions(async (tool, args) => {
      sent += 1
      return callTm({ ...real, call: (t, a) => real.call(t, a) }, tool, args).then(r => r)
    })
    const out = await actions.done(ID)
    expect(sent).toBe(1)
    expect(out.ok).toBe(false)
    expect(out).toMatchObject({ ok: false, mayHaveMoved: true })
    expect(out.ok ? '' : out.refusal).toMatch(/Taskmaster unreachable: backlog_complete_task: .*the call may have committed, check its effect before repeating it/)
  })

  test('the error is transient to the reader (retry ladder) but a write is never retried by the mod', async () => {
    const error = await callTm(host(bridgeMayHaveCommitted), 'backlog_complete_task', { task_id: ID }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(TmUnreachable)
    expect((error as TmUnreachable).transient).toBe(true)
    expect(TM_WRITE_TIMEOUT_MS).toBeGreaterThan(3000)
  })

  test('a sign-off from the review pane cut by a restart: a refusal on the card, one write, the queue refreshed', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args): McpAnswer => (tool === 'backlog_complete_task' ? { deny: bridgeMayHaveCommitted(tool) } : R.backlog(tool, args))
    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    await clock.settle()
    const listsBefore = world.calls.filter(c => c.tool === 'backlog_list_tasks').length
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_complete_task')).toHaveLength(1)
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks').length).toBeGreaterThan(listsBefore)
  })
})
