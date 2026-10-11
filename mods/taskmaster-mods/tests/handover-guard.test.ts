// User intent: the cache-cold handover guard as the person relies on it — off unless enabled; at 55 idle minutes over a big
// context, one handover written from a fork and then a compaction, the main conversation never prompted; never when a
// handover was already written, never twice, never late, never a compaction without its handover.
import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'
import type { MockClock } from 'claude-code/testing'

import {
  compactInstructions,
  GUARD_DEFAULTS,
  guardConfigOf,
  guardVerdict,
  handoverArgs,
  isHandoverWritten,
  parseForkHandover,
} from '../hooks/handover-guard'
import { PLUGIN } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, type World, worldOf } from './fixtures/world'

const MIN = 60_000
const ON = { options: { handoverGuard: true }, plugins: [RR_STUB] }
const OFF = { plugins: [RR_STUB] }
const TURN_END = { answer: 'ok', reason: 'answer', durationMs: 1, isAborted: false, turnId: 't1' } as never
const CREATE = 'mcp__plugin_taskmaster_tm__backlog_handover_create'
const WRITTEN = 'Handover written: H-0042\n- File: .taskmaster/handovers/H-0042.md'

const FORK_REPLY = [
  'Here it is.',
  '<handover>',
  '<tldr>Guard writes handovers from a fork</tldr>',
  '<next_action>Run the mod tests</next_action>',
  '<task_ids>tm-1, tm-2</task_ids>',
  "<body>## Decisions\n- fork, then compact\n## Where I'd start\n- tests</body>",
  '</handover>',
].join('\n')

type ForkAnswer = { isAnswered: true; text: string } | { isAnswered: false; reason: string }
type GuardWorld = {
  tokens: number | undefined
  submits: string[]
  forks: string[]
  fork: ForkAnswer
  forkMs: number
  compacts: (string | undefined)[]
  compactSkip: string | undefined
  reply: { text: string; isError?: boolean }
  stallMs: number
}

/** Beneath the plugins: the session's context fill (answered `stallMs` late on the clock given), the prompt box's
 *  submissions, the fork (answered `forkMs` late), the compactions, and the tm server's handover reply (tool and mcp). */
function guardWorld(on: On, world: World, tokens: number | undefined, clock?: MockClock): GuardWorld {
  const g: GuardWorld = {
    tokens,
    submits: [],
    forks: [],
    fork: { isAnswered: true, text: FORK_REPLY },
    forkMs: 0,
    compacts: [],
    compactSkip: undefined,
    reply: { text: WRITTEN },
    stallMs: 0,
  }
  on('session.usage', async () => {
    if (clock !== undefined && g.stallMs > 0) await clock.sleep(g.stallMs)
    return { value: { startedAt: 0, context: { tokens: g.tokens, window: 1_000_000 }, rateLimits: [] } } as never
  })
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('prompt.submit', ($, e) => {
    g.submits.push(e.text)
    return { text: e.text }
  })
  on('model.fork', async ($, e) => {
    g.forks.push(e.prompt)
    if (clock !== undefined && g.forkMs > 0) await clock.sleep(g.forkMs)
    return { value: { ...g.fork, usage: {} } } as never
  })
  on('session.compact', ($, e) => {
    g.compacts.push(e.instructions)
    return (g.compactSkip === undefined ? { messages: [{ role: 'user', text: 'summary', toolUses: [] }] } : { skip: g.compactSkip }) as never
  })
  on('tool.call', () => ({ result: { content: [{ type: 'text', text: g.reply.text }] }, text: g.reply.text, isError: g.reply.isError }) as never)
  world.mcp = tool => (tool === 'backlog_handover_create' ? { text: g.reply.text, isError: g.reply.isError } : 'offline')
  return g
}

const created = (world: World) => world.calls.filter(c => c.tool === 'backlog_handover_create')

describe('handover guard: decisions', () => {
  const cfg = { ...GUARD_DEFAULTS, enabled: true }
  const idle = { lastTurnEnd: 1_000, latch: 'none' as const }

  test('fires at the idle mark over the token floor', () => {
    expect(guardVerdict(cfg, idle, 1_000 + 55 * MIN, 200_000)).toEqual({ fire: true, tokens: 200_000 })
  })

  test('skips: disabled, under the floor, tokens unknown, latched, no turn end, late timer', () => {
    expect(guardVerdict(GUARD_DEFAULTS, idle, 1_000 + 55 * MIN, 300_000)).toMatchObject({ fire: false, reason: 'disabled' })
    expect(guardVerdict(cfg, idle, 1_000 + 55 * MIN, 199_999)).toMatchObject({ fire: false })
    expect(guardVerdict(cfg, idle, 1_000 + 55 * MIN, undefined)).toMatchObject({ fire: false })
    expect(guardVerdict(cfg, { ...idle, latch: 'fired' }, 1_000 + 55 * MIN, 300_000)).toMatchObject({ fire: false })
    expect(guardVerdict(cfg, { ...idle, latch: 'handover' }, 1_000 + 55 * MIN, 300_000)).toMatchObject({ fire: false })
    expect(guardVerdict(cfg, { lastTurnEnd: null, latch: 'none' }, 55 * MIN, 300_000)).toMatchObject({ fire: false })
    const late = guardVerdict(cfg, idle, 1_000 + 58 * MIN, 300_000)
    expect(late).toMatchObject({ fire: false })
    expect(late.fire ? '' : late.reason).toContain('late')
  })

  test('config: off by default, 55 min and 200k; bad values fall back; idle stays under the late mark', () => {
    expect(guardConfigOf({})).toEqual(GUARD_DEFAULTS)
    expect(GUARD_DEFAULTS).toEqual({ enabled: false, idleMs: 55 * MIN, minTokens: 200_000 })
    expect(guardConfigOf({ handoverGuard: true, handoverGuardIdleMinutes: 40, handoverGuardMinTokens: 150_000 })).toEqual({
      enabled: true,
      idleMs: 40 * MIN,
      minTokens: 150_000,
    })
    expect(guardConfigOf({ handoverGuardIdleMinutes: -3, handoverGuardMinTokens: Number.NaN })).toEqual(GUARD_DEFAULTS)
    expect(guardConfigOf({ handoverGuardIdleMinutes: 90 }).idleMs).toBe(57 * MIN)
  })

  test('a handover counts as written only on the tm reply that says so', () => {
    expect(isHandoverWritten({ text: WRITTEN })).toBe(true)
    expect(isHandoverWritten({ text: 'Error: tldr is required' })).toBe(false)
    expect(isHandoverWritten({ text: WRITTEN, isError: true })).toBe(false)
    expect(isHandoverWritten({ deny: 'blocked' })).toBe(false)
  })
})

describe('handover guard: the fork reply', () => {
  test('parses the handover element; prose around it is ignored, task ids split on commas and spaces', () => {
    expect(parseForkHandover(FORK_REPLY)).toEqual({
      tldr: 'Guard writes handovers from a fork',
      nextAction: 'Run the mod tests',
      taskIds: ['tm-1', 'tm-2'],
      body: "## Decisions\n- fork, then compact\n## Where I'd start\n- tests",
    })
  })

  test('no tldr or no body is no handover; empty task ids and next action are fine', () => {
    expect(parseForkHandover('<tldr>x</tldr>')).toBeNull()
    expect(parseForkHandover('<body>x</body>')).toBeNull()
    expect(parseForkHandover('<tldr> </tldr><body>x</body>')).toBeNull()
    expect(parseForkHandover('<tldr>a\n  b</tldr><task_ids></task_ids><body>x</body>')).toEqual({ tldr: 'a b', nextAction: '', taskIds: [], body: 'x' })
  })

  test('files as a continuity handover with the context size; task ids only when named', () => {
    const h = parseForkHandover(FORK_REPLY)!
    expect(handoverArgs(h, 250_000)).toEqual({
      tldr: h.tldr,
      body: h.body,
      next_action: 'Run the mod tests',
      session_kind: 'continuity',
      task_ids: ['tm-1', 'tm-2'],
      options: { context_size_at_write: 250_000 },
    })
    expect(handoverArgs({ ...h, taskIds: [] }, 1)).not.toHaveProperty('task_ids')
    expect(compactInstructions('H-0042', h)).toContain('H-0042')
  })
})

describe('handover guard: in a session', () => {
  test('fires once after the idle period: fork, handover, compaction; the conversation is never prompted', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const state = stateOf(on)
    const g = guardWorld(on, world, 250_000)
    await $.turn.complete(TURN_END)
    await clock.advance(54 * MIN)
    expect(g.forks).toEqual([])
    await clock.advance(1 * MIN)
    expect(g.forks).toHaveLength(1)
    expect(g.forks[0]).toContain('250k')
    expect(created(world)).toHaveLength(1)
    expect(created(world)[0]!.args).toMatchObject({ tldr: 'Guard writes handovers from a fork', session_kind: 'continuity' })
    expect(g.compacts).toHaveLength(1)
    expect(g.compacts[0]).toContain('H-0042')
    expect(g.submits).toEqual([])
    expect(state.get(`${PLUGIN}.handoverNotice`)).toMatchObject({ id: 'H-0042', tldr: 'Guard writes handovers from a fork' })
    expect(state.get(`${PLUGIN}.handoverGuard`)).toMatchObject({ latch: 'handover' })
    expect(world.toasts.some(t => t.includes('handover'))).toBe(true)
    expect(world.logs.some(l => l.to === 'debug' && l.text.includes('fired'))).toBe(true)
    await $.turn.complete(TURN_END)
    await clock.advance(120 * MIN)
    expect(g.forks).toHaveLength(1)
    expect(g.compacts).toHaveLength(1)
  })

  test('a fork that does not answer writes nothing and does not compact', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.fork = { isAnswered: false, reason: 'api-error' }
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toHaveLength(1)
    expect(created(world)).toEqual([])
    expect(g.compacts).toEqual([])
    expect(world.logs.some(l => l.text.includes('api-error'))).toBe(true)
  })

  test('a fork reply with no handover in it writes nothing and does not compact', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.fork = { isAnswered: true, text: 'I cannot do that.' }
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(created(world)).toEqual([])
    expect(g.compacts).toEqual([])
  })

  test('a handover tm refuses (or tm offline) is no handover: no compaction, and no second try', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.reply = { text: 'Error: tldr is required' }
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(created(world)).toHaveLength(1)
    expect(g.compacts).toEqual([])
    world.mcp = () => 'offline'
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(g.forks).toHaveLength(1)
    expect(g.compacts).toEqual([])
  })

  test('a turn that starts while the fork runs keeps the handover but skips the compaction', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000, clock)
    g.forkMs = 2 * MIN
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toHaveLength(1)
    await $.turn.start({ text: '', turnId: 't2' })
    await clock.advance(3 * MIN)
    expect(created(world)).toHaveLength(1)
    expect(g.compacts).toEqual([])
    expect(world.logs.some(l => l.text.includes('did not compact'))).toBe(true)
  })

  test('a compaction a hook vetoes is logged', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.compactSkip = 'off for now'
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.compacts).toHaveLength(1)
    expect(world.logs.some(l => l.text.includes('off for now'))).toBe(true)
  })

  test('does not fire under 200k, nor when the context size is unknown', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 199_999)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    g.tokens = undefined
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test('does not fire when disabled (the default)', OFF, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 500_000)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test('does not fire after this session wrote a handover', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test('a handover written while the timer is pending stops it', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(10 * MIN)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await clock.advance(50 * MIN)
    expect(g.forks).toEqual([])
  })

  test('a refused handover create does not count as written', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.reply = { text: 'Error: tldr is required' }
    await $.tool.call({ tool: CREATE, tldr: '' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toHaveLength(1)
  })

  test('a user prompt or a new turn cancels the pending timer', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(30 * MIN)
    await $.prompt.submit({ text: 'one more thing' } as never)
    await clock.advance(30 * MIN)
    await $.turn.complete(TURN_END)
    await clock.advance(30 * MIN)
    await $.turn.start({ text: '', turnId: 't2' })
    await clock.advance(30 * MIN)
    expect(g.forks).toEqual([])
  })

  test('a turn that starts while the turn end is still arming is never fired into', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await Promise.all([$.turn.complete(TURN_END), $.turn.start({ text: '', turnId: 't2' })])
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test('a check that runs late (the machine slept past 58 min) is skipped', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000, clock)
    g.stallMs = 15 * MIN // the timer comes due at 55 min, but the check only reads the time at 70
    await $.turn.complete(TURN_END)
    await clock.advance(71 * MIN)
    expect(g.forks).toEqual([])
    expect(world.logs.some(l => l.text.includes('late timer'))).toBe(true)
  })

  test("a session end (/clear, resume) cancels the old session's timer", ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    on('session.end', ($, e) => ({ sessionId: e.sessionId }))
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await $.session.end({ reason: 'clear', sessionId: 'sess-A' } as never)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test("a subagent's turn end does not arm it", ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete({ ...(TURN_END as object), agentId: 'ag1' } as never)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })

  test('after a reload the latch in $.state still holds: no second run', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    stateOf(on, { [`${PLUGIN}.handoverGuard`]: { lastTurnEnd: 1, latch: 'fired' } })
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(g.forks).toEqual([])
  })
})
