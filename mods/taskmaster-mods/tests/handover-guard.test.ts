// User intent: the cache-cold handover guard as the person relies on it — off unless enabled; at 55 idle minutes over a big
// context, one handover written from a fork and then a compaction, the main conversation never prompted; never when a
// handover was already written, never twice, never late, never a compaction without its handover.
import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'
import type { MockClock } from 'claude-code/testing'

import {
  busyReason,
  compactInstructions,
  GUARD_DEFAULTS,
  guardConfigOf,
  goalAfterCommand,
  goalEndedAtStop,
  guardVerdict,
  handoverArgs,
  isHandoverWritten,
  parseForkHandover,
  REFRESH_PROMPT,
} from '../hooks/handover-guard'
import { PLUGIN } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, type World, worldOf } from './fixtures/world'

const MIN = 60_000
const ON = { options: { handoverGuard: true }, plugins: [RR_STUB] }
const OFF = { plugins: [RR_STUB] }
const FORK_ONLY = { options: { handoverGuard: true, handoverGuardCompact: false }, plugins: [RR_STUB] }
const PROMPT_ONLY = { options: { handoverGuard: true, handoverGuardFork: false, handoverGuardCompact: false }, plugins: [RR_STUB] }
const PROMPT_COMPACT = { options: { handoverGuard: true, handoverGuardFork: false }, plugins: [RR_STUB] }
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
  agents: { id: string; description: string; type: string; status: string }[]
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
    agents: [],
  }
  on('agent.list', () => ({ value: g.agents }) as never)
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
    expect(GUARD_DEFAULTS).toEqual({ enabled: false, idleMs: 55 * MIN, minTokens: 200_000, fork: true, compact: true, maxRefreshes: 6 })
    expect(guardConfigOf({ handoverGuard: true, handoverGuardIdleMinutes: 40, handoverGuardMinTokens: 150_000 })).toEqual({
      enabled: true,
      idleMs: 40 * MIN,
      minTokens: 150_000,
      fork: true,
      compact: true,
      maxRefreshes: 6,
    })
    expect(guardConfigOf({ handoverGuardMaxRefreshes: 0 }).maxRefreshes).toBe(0)
    expect(guardConfigOf({ handoverGuardMaxRefreshes: 2.7 }).maxRefreshes).toBe(2)
    expect(guardConfigOf({ handoverGuardMaxRefreshes: -1 }).maxRefreshes).toBe(6)
    expect(guardConfigOf({ handoverGuardFork: false, handoverGuardCompact: false })).toMatchObject({ fork: false, compact: false })
    expect(guardConfigOf({ handoverGuardFork: 'no', handoverGuardCompact: 0 })).toMatchObject({ fork: true, compact: true })
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

describe('handover guard: modes', () => {
  test('fork without compaction: the fork writes the handover, nothing is compacted', FORK_ONLY, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toHaveLength(1)
    expect(created(world)).toHaveLength(1)
    expect(g.compacts).toEqual([])
    expect(g.submits).toEqual([])
  })

  test('no fork, no compaction: the conversation is asked once, as the guard first did', PROMPT_ONLY, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 250_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toEqual([])
    expect(g.submits).toHaveLength(1)
    expect(g.submits[0]).toContain('taskmaster:handover')
    expect(g.submits[0]).toContain('250k')
    expect(g.submits[0]).not.toContain('compacted')
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
  })

  test('no fork, with compaction: the guard turn that writes a handover is compacted once it ends', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.submits).toHaveLength(1)
    expect(g.submits[0]).toContain('compacted')
    await $.turn.start({ text: '', turnId: 't2' })
    await $.tool.call({ tool: CREATE, tldr: 'wrap  up', next_action: 'run tests' } as never)
    expect(g.compacts).toEqual([])
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toHaveLength(1)
    expect(g.compacts[0]).toContain('H-0042')
    expect(g.compacts[0]).toContain('wrap up')
    expect(g.compacts[0]).toContain('run tests')
    await $.turn.complete(TURN_END)
    await clock.advance(120 * MIN)
    expect(g.compacts).toHaveLength(1)
    expect(g.submits).toHaveLength(1)
  })

  test('no fork, with compaction: a guard turn that writes no handover is not compacted', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    g.reply = { text: 'Error: tldr is required' }
    await $.tool.call({ tool: CREATE, tldr: '' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
    expect(world.logs.some(l => l.text.includes('wrote no handover'))).toBe(true)
  })

  test('no fork, with compaction: a prompt of yours before the guard turn ends cancels the compaction', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.prompt.submit({ text: 'actually, one more thing' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
  })

  test('no fork, with compaction: a turn starting before the delayed compaction runs cancels it', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.turn.complete(TURN_END)
    await $.turn.start({ text: '', turnId: 't3' })
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
    expect(world.logs.some(l => l.text.includes('did not compact'))).toBe(true)
  })
})

const AGENT = { id: 'ag1', description: 'long build', type: 'general-purpose', status: 'running' }
const refreshes = (g: GuardWorld) => g.forks.filter(f => f === REFRESH_PROMPT)

describe('handover guard: busy, not idle', () => {
  test('busy: a listed agent at work, or a sub-loop tool call within the window; an idle teammate is not', () => {
    expect(busyReason([{ status: 'running' }], null, 0, 55 * MIN)).toContain('1 agent')
    expect(busyReason([{ status: 'pending' }, { status: 'waiting' }], null, 0, 55 * MIN)).toContain('2 agents')
    expect(busyReason([{ status: 'idle' }, { status: 'completed' }, { status: 'failed' }], null, 0, 55 * MIN)).toBeNull()
    expect(busyReason([], 10 * MIN, 60 * MIN, 55 * MIN)).toContain('50 min ago')
    expect(busyReason([], 1 * MIN, 60 * MIN, 55 * MIN)).toBeNull()
  })

  test('an agent at work gets cache refreshes, never a handover or compaction; once it is done the guard fires', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const state = stateOf(on)
    const g = guardWorld(on, world, 300_000)
    g.agents = [AGENT]
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.forks).toEqual([REFRESH_PROMPT])
    expect(created(world)).toEqual([])
    expect(g.compacts).toEqual([])
    expect(g.submits).toEqual([])
    expect(state.get(`${PLUGIN}.handoverGuard`)).toMatchObject({ latch: 'none' })
    expect(world.logs.some(l => l.text.includes('refreshed the cache (1/6)'))).toBe(true)
    await clock.advance(55 * MIN)
    expect(refreshes(g)).toHaveLength(2)
    g.agents = [{ ...AGENT, status: 'completed' }]
    await clock.advance(55 * MIN)
    expect(refreshes(g)).toHaveLength(2)
    expect(g.forks).toHaveLength(3)
    expect(created(world)).toHaveLength(1)
    expect(g.compacts).toHaveLength(1)
  })

  test('an idle teammate does not hold the guard back', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.agents = [{ ...AGENT, type: 'teammate', status: 'idle' }]
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(refreshes(g)).toEqual([])
    expect(created(world)).toHaveLength(1)
  })

  test('the refresh cap stops refreshing and leaves the conversation alone', { options: { handoverGuard: true, handoverGuardMaxRefreshes: 2 }, plugins: [RR_STUB] }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.agents = [AGENT]
    await $.turn.complete(TURN_END)
    await clock.advance(3 * 55 * MIN)
    expect(refreshes(g)).toHaveLength(2)
    expect(world.logs.some(l => l.text.includes('2 cache refreshes are spent'))).toBe(true)
    g.agents = []
    await clock.advance(3 * 55 * MIN)
    expect(g.forks).toHaveLength(2)
    expect(created(world)).toEqual([])
    expect(g.compacts).toEqual([])
  })

  test('a failed refresh is logged and the timer still restarts', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.agents = [AGENT]
    g.fork = { isAnswered: false, reason: 'api-error' }
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(world.logs.some(l => l.text.includes('refresh 1/6 failed (api-error)'))).toBe(true)
    await clock.advance(55 * MIN)
    expect(refreshes(g)).toHaveLength(2)
  })

  test('a main-loop turn end gives the refreshes back', { options: { handoverGuard: true, handoverGuardMaxRefreshes: 1 }, plugins: [RR_STUB] }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, world, 300_000)
    g.agents = [AGENT]
    await $.turn.complete(TURN_END)
    await clock.advance(2 * 55 * MIN)
    expect(refreshes(g)).toHaveLength(1)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(refreshes(g)).toHaveLength(2)
  })
})

describe('handover guard: a running /goal', () => {
  const goalRun = (args: string) => ({ command: 'goal', args, origin: { kind: 'composer' as const }, presentation: { isFullscreen: true, columns: 180 } })
  /** Beneath the plugins: `/goal` answers as the engine's does; the Stop chain answers `stop` (the evaluator's verdict). */
  function goalWorld(on: On): { stop: { block?: string; stopReason?: string } } {
    const w: { stop: { block?: string; stopReason?: string } } = { stop: {} }
    on('command.run', { command: 'goal' }, ($, e) => {
      const a = e.args.trim()
      if (a === '') return { text: 'No goal set' }
      if (a === 'clear') return { text: 'Goal cleared: tests pass' }
      return { text: `Goal set: ${a}` }
    })
    on('classic.Stop', () => w.stop)
    return w
  }
  const STOP = { hook_event_name: 'Stop', stop_hook_active: false, session_id: 's', transcript_path: 't', cwd: '.' } as never

  test('reads set, clear and status runs; a too-long condition changes nothing', () => {
    expect(goalAfterCommand('all tests pass', 'Goal set: all tests pass')).toBe(true)
    expect(goalAfterCommand('all tests pass', undefined)).toBe(true)
    expect(goalAfterCommand('clear', 'Goal cleared: x')).toBe(false)
    expect(goalAfterCommand(' Cancel ', undefined)).toBe(false)
    expect(goalAfterCommand('', 'Goal active: x (3 turns)')).toBe(true)
    expect(goalAfterCommand('', 'No goal set')).toBe(false)
    expect(goalAfterCommand('', undefined)).toBeNull()
    expect(goalAfterCommand('x'.repeat(5000), 'Goal condition is limited to 4000 characters (got 5000)')).toBeNull()
  })

  test('a Stop ends the goal only on the evaluator verdict of met: a reason and no block', () => {
    expect(goalEndedAtStop({ stopReason: 'the file exists' })).toBe(true)
    expect(goalEndedAtStop({ block: 'not yet: tests fail' })).toBe(false)
    expect(goalEndedAtStop({ block: 'x', stopReason: 'y' })).toBe(false)
    expect(goalEndedAtStop({})).toBe(false)
  })

  test('with a goal set, the fork still writes the handover but nothing is compacted', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const state = stateOf(on)
    goalWorld(on)
    const g = guardWorld(on, world, 300_000)
    await $.command.run(goalRun('all tests pass'))
    expect(state.get(`${PLUGIN}.handoverGuard`)).toMatchObject({ goal: true })
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(created(world)).toHaveLength(1)
    expect(g.compacts).toEqual([])
    expect(world.toasts.some(t => t.includes('compacting'))).toBe(false)
    expect(world.logs.some(l => l.text.includes('/goal is running'))).toBe(true)
  })

  test('no fork: the prompt does not promise a compaction, and the turn is not compacted', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    goalWorld(on)
    const g = guardWorld(on, world, 300_000)
    await $.command.run(goalRun('all tests pass'))
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.submits).toHaveLength(1)
    expect(g.submits[0]).not.toContain('compacted')
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
  })

  test('a goal set while the guard turn runs holds its compaction off', PROMPT_COMPACT, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    goalWorld(on)
    const g = guardWorld(on, world, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.command.run({ ...goalRun('ship it'), origin: { kind: 'plugin', name: 'x' } } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(2_000)
    expect(g.compacts).toEqual([])
    expect(world.logs.some(l => l.text.includes('did not compact: a /goal is running'))).toBe(true)
  })

  test('a goal cleared, or met at a Stop, lets the compaction run again; a blocked or plain Stop does not', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const state = stateOf(on)
    const w = goalWorld(on)
    const g = guardWorld(on, world, 300_000)
    const goal = () => (state.get(`${PLUGIN}.handoverGuard`) as { goal?: boolean } | undefined)?.goal
    await $.command.run(goalRun('all tests pass'))
    await $.command.run(goalRun('clear'))
    expect(goal()).toBe(false)
    await $.command.run(goalRun('all tests pass'))
    w.stop = { block: 'not yet' }
    await $.classic.Stop(STOP)
    w.stop = {}
    await $.classic.Stop(STOP)
    expect(goal()).toBe(true)
    w.stop = { stopReason: 'they pass' }
    await $.classic.Stop(STOP)
    expect(goal()).toBe(false)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(g.compacts).toHaveLength(1)
  })

  test('a status run that finds no goal lets it go; a session end forgets it', ON, async ($, on) => {
    const state = stateOf(on)
    goalWorld(on)
    on('session.end', ($, e) => ({ sessionId: e.sessionId }))
    const goal = () => (state.get(`${PLUGIN}.handoverGuard`) as { goal?: boolean } | undefined)?.goal
    await $.command.run(goalRun('all tests pass'))
    await $.command.run(goalRun(''))
    expect(goal()).toBe(false)
    await $.command.run(goalRun('all tests pass'))
    await $.session.end({ reason: 'clear', sessionId: 'sess-A' } as never)
    expect(goal()).toBe(false)
  })
})
