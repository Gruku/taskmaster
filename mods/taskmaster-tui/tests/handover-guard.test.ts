// User intent: the cache-cold handover guard as the person relies on it — off unless enabled; one handover prompt per
// session at 55 idle minutes over a big context; never when a handover was already written, never twice, never late.
import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { GUARD_DEFAULTS, guardConfigOf, guardVerdict, isHandoverWritten } from '../hooks/handover-guard'
import { PLUGIN } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const MIN = 60_000
const ON = { options: { handoverGuard: true }, plugins: [RR_STUB] }
const OFF = { plugins: [RR_STUB] }
const TURN_END = { answer: 'ok', reason: 'answer', durationMs: 1, isAborted: false, turnId: 't1' } as never
const CREATE = 'mcp__plugin_taskmaster_tm__backlog_handover_create'
const WRITTEN = 'Handover written: H-0042\n- File: .taskmaster/handovers/H-0042.md'

type GuardWorld = { tokens: number | undefined; submits: string[]; reply: { text: string; isError?: boolean } }

/** Beneath the plugins: the session's context fill, the prompt box's submissions, and the tm server's handover reply. */
function guardWorld(on: On, tokens: number | undefined): GuardWorld {
  const g: GuardWorld = { tokens, submits: [], reply: { text: WRITTEN } }
  on('session.usage', () => ({ value: { startedAt: 0, context: { tokens: g.tokens, window: 1_000_000 }, rateLimits: [] } }) as never)
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('prompt.submit', ($, e) => {
    g.submits.push(e.text)
    return { text: e.text }
  })
  on('tool.call', () => ({ result: { content: [{ type: 'text', text: g.reply.text }] }, text: g.reply.text, isError: g.reply.isError }) as never)
  return g
}

const guardPrompts = (g: GuardWorld) => g.submits.filter(t => t.includes('taskmaster:handover'))

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

describe('handover guard: in a session', () => {
  test('fires exactly once, after the idle period, at 200k and over', ON, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const g = guardWorld(on, 250_000)
    await $.turn.complete(TURN_END)
    await clock.advance(54 * MIN)
    expect(guardPrompts(g)).toEqual([])
    await clock.advance(1 * MIN)
    expect(guardPrompts(g)).toHaveLength(1)
    expect(guardPrompts(g)[0]).toContain('250k')
    expect(world.toasts.some(t => t.includes('handover'))).toBe(true)
    expect(world.logs.some(l => l.to === 'debug' && l.text.includes('fired'))).toBe(true)
    await clock.advance(120 * MIN)
    expect(guardPrompts(g)).toHaveLength(1)
  })

  test('does not fire under 200k, nor when the context size is unknown', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 199_999)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    g.tokens = undefined
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test('does not fire when disabled (the default)', OFF, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 500_000)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test('does not fire after this session wrote a handover', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test('a handover written while the timer is pending stops it', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(10 * MIN)
    await $.tool.call({ tool: CREATE, tldr: 'wrap up' } as never)
    await clock.advance(50 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test('a refused handover create does not count as written', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    g.reply = { text: 'Error: tldr is required' }
    await $.tool.call({ tool: CREATE, tldr: '' } as never)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(guardPrompts(g)).toHaveLength(1)
  })

  test("the guard's own handover turn re-arms but never fires a second time", ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(55 * MIN)
    expect(guardPrompts(g)).toHaveLength(1)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toHaveLength(1)
  })

  test('a user prompt or a new turn cancels the pending timer', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(30 * MIN)
    await $.prompt.submit({ text: 'one more thing' } as never)
    await clock.advance(30 * MIN)
    await $.turn.complete(TURN_END)
    await clock.advance(30 * MIN)
    await $.turn.start({ text: '', turnId: 't2' })
    await clock.advance(30 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test("a subagent's turn end does not arm it", ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    const g = guardWorld(on, 300_000)
    await $.turn.complete({ ...(TURN_END as object), agentId: 'ag1' } as never)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })

  test('after a reload the latch in $.state still holds: no second prompt', ON, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    stateOf(on, { [`${PLUGIN}.handoverGuard`]: { lastTurnEnd: 1, latch: 'fired' } })
    const g = guardWorld(on, 300_000)
    await $.turn.complete(TURN_END)
    await clock.advance(56 * MIN)
    expect(guardPrompts(g)).toEqual([])
  })
})
