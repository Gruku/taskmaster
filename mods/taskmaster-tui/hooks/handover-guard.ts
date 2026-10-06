// User intent: an opt-in guard for long sessions left idle — before the 1-hour prompt cache goes cold over a big context,
// ask the model once to write a handover, so the session can be resumed cheaply; at most one per session, never a loop.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On, PluginOptions, Timer, ToolCallResult } from 'claude-code'

import type { TmHandoverGuard } from '../types'

const MIN = 60_000
/** The prompt cache lives 60 minutes; a check this late (a laptop asleep, a stalled host) finds it cold already. */
const LATE_MS = 58 * MIN
const MAX_IDLE_MINUTES = 57

export type GuardConfig = { readonly enabled: boolean; readonly idleMs: number; readonly minTokens: number }
export type GuardVerdict = { readonly fire: true; readonly tokens: number } | { readonly fire: false; readonly reason: string }

export const GUARD_DEFAULTS: GuardConfig = { enabled: false, idleMs: 55 * MIN, minTokens: 200_000 }
const FRESH_GUARD: TmHandoverGuard = { lastTurnEnd: null, latch: 'none' }

const GUARD = atom({ plugin: 'taskmaster-tui', key: 'handoverGuard' } as const, FRESH_GUARD)

const positive = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null)

export function guardConfigOf(options: PluginOptions): GuardConfig {
  const minutes = positive(options.handoverGuardIdleMinutes)
  return {
    enabled: options.handoverGuard === true,
    idleMs: minutes === null ? GUARD_DEFAULTS.idleMs : Math.min(minutes, MAX_IDLE_MINUTES) * MIN,
    minTokens: positive(options.handoverGuardMinTokens) ?? GUARD_DEFAULTS.minTokens,
  }
}

export function guardVerdict(cfg: GuardConfig, guard: TmHandoverGuard, now: number, tokens: number | undefined): GuardVerdict {
  if (!cfg.enabled) return { fire: false, reason: 'disabled' }
  if (guard.latch !== 'none') return { fire: false, reason: guard.latch === 'handover' ? 'a handover was written this session' : 'already fired this session' }
  if (guard.lastTurnEnd === null) return { fire: false, reason: 'no turn has ended in this session' }
  const idle = now - guard.lastTurnEnd
  if (idle >= LATE_MS) return { fire: false, reason: `late timer (${Math.round(idle / MIN)} min idle): the cache is cold already` }
  if (tokens === undefined) return { fire: false, reason: 'context size unknown' }
  if (tokens < cfg.minTokens) return { fire: false, reason: `context ${tokens} tokens is under ${cfg.minTokens}` }
  return { fire: true, tokens }
}

/** The tm server answers a refusal as text with no error flag; only its "Handover written:" receipt is a handover. */
export function isHandoverWritten(ran: { readonly deny?: string; readonly text?: string; readonly isError?: boolean }): boolean {
  if (ran.deny !== undefined || ran.isError === true) return false
  return typeof ran.text === 'string' && /\bHandover written: \S/.test(ran.text)
}

export const guardPrompt = (idleMinutes: number, tokens: number): string =>
  `Cache-cold guard: this session has been idle ${idleMinutes} min at ${Math.round(tokens / 1000)}k tokens of context and the ` +
  'prompt cache expires at 60 min. Write a handover now with the taskmaster:handover skill, then stop.'

// The pending check of this module instance; a hot reload cancels it with the old environment, the latch stays in $.state.
const guard: { cfg: GuardConfig; pending: Timer | null } = { cfg: GUARD_DEFAULTS, pending: null }

function disarm(): void {
  guard.pending?.cancel()
  guard.pending = null
}

async function check($: EngineInterface): Promise<void> {
  guard.pending = null
  const cfg = guard.cfg
  let tokens: number | undefined
  try {
    tokens = (await $.session.usage()).context.tokens
  } catch {
    tokens = undefined
  }
  const verdict = guardVerdict(cfg, await read($, GUARD), await $.clock.now(), tokens)
  if (!verdict.fire) {
    if (verdict.reason !== 'disabled') $.ui.log(`taskmaster-tui: handover guard skipped: ${verdict.reason}`, { to: 'debug' })
    return
  }
  // Latch before submitting: the prompt's own turn ends and re-arms, and only an unlatched session may fire.
  let won = false
  await update($, GUARD, (g): TmHandoverGuard => {
    won = g.latch === 'none'
    return won ? { ...g, latch: 'fired' } : g
  })
  if (!won) return
  const minutes = Math.round(cfg.idleMs / MIN)
  $.ui.log(`taskmaster-tui: handover guard fired at ${verdict.tokens} tokens after ${minutes} idle min`, { to: 'debug' })
  $.ui.toast(`Idle ${minutes} min at ${Math.round(verdict.tokens / 1000)}k tokens: asking for a handover before the cache goes cold`)
  try {
    await $.prompt.submit({ text: guardPrompt(minutes, verdict.tokens) })
  } catch (error) {
    $.ui.log(`taskmaster-tui: handover guard prompt not submitted: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
  }
}

async function arm($: EngineInterface): Promise<void> {
  disarm()
  if (!guard.cfg.enabled) return
  const now = await $.clock.now()
  const held = await update($, GUARD, (g): TmHandoverGuard => (g.latch === 'none' ? { ...g, lastTurnEnd: now } : g))
  if (held.latch !== 'none') return
  guard.pending = $.clock.after(guard.cfg.idleMs, () => {
    void check($).catch(() => undefined) // a check that cannot read the state now just does not ask
  })
}

async function noteHandover($: EngineInterface): Promise<void> {
  disarm()
  await update($, GUARD, (g): TmHandoverGuard => (g.latch === 'handover' ? g : { ...g, latch: 'handover' }))
}

export function onHandoverGuard(on: On, options: PluginOptions): void {
  guard.cfg = guardConfigOf(options)
  disarm()

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId === undefined) await arm($)
    return result
  })

  on('turn.start', async ($, e, next) => {
    disarm()
    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    disarm()
    return next(e)
  })

  // Tracked whether or not the guard is on, so enabling it later in the session still knows a handover exists.
  on('tool.call', { tool: /backlog_handover_create$/ }, async ($, e, next) => {
    const ran: ToolCallResult = await next(e)
    try {
      if (isHandoverWritten(ran)) await noteHandover($)
    } catch {
      // a latch that cannot be written now only lets the guard ask once more; the tool's result is never held up
    }
    return ran
  })
}
