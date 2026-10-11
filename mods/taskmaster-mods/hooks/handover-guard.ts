// User intent: an opt-in guard for long sessions left idle — before the 1-hour prompt cache goes cold over a big context,
// get a handover written (by a fork of the conversation, or by the conversation itself) and optionally compact, as the
// person configures; at most one per session, never a loop, never a compaction without its handover. While subagents
// still work, the session is not idle: keep the cache warm instead (capped), never hand over or compact under them.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On, PluginOptions, Timer, ToolCallResult } from 'claude-code'

import type { TmHandoverGuard, TmHandoverNotice } from '../types'
import { handoverNotice } from './model'
import { parseHandoverWritten, replyText } from './parse'

const MIN = 60_000
/** The prompt cache lives 60 minutes; a check this late (a laptop asleep, a stalled host) finds it cold already. */
const LATE_MS = 58 * MIN
const MAX_IDLE_MINUTES = 57

export type GuardConfig = {
  readonly enabled: boolean
  readonly idleMs: number
  readonly minTokens: number
  /** A fork writes the handover (true) or the main conversation is asked to (false). */
  readonly fork: boolean
  /** Compact once the handover is written. */
  readonly compact: boolean
  /** Cache refreshes in a row while subagents work, before the guard leaves the cache to go cold. */
  readonly maxRefreshes: number
}
export type GuardVerdict = { readonly fire: true; readonly tokens: number } | { readonly fire: false; readonly reason: string }

export const GUARD_DEFAULTS: GuardConfig = {
  enabled: false,
  idleMs: 55 * MIN,
  minTokens: 200_000,
  fork: true,
  compact: true,
  maxRefreshes: 6,
}
const FRESH_GUARD: TmHandoverGuard = { lastTurnEnd: null, latch: 'none' }

const GUARD = atom({ plugin: 'taskmaster-mods', key: 'handoverGuard' } as const, FRESH_GUARD)
// The band's just-written notice, the same state key register.tsx raises it under for a handover the model wrote.
const NOTICE = atom({ plugin: 'taskmaster-mods', key: 'handoverNotice' } as const, null as TmHandoverNotice | null)
const TM_SERVER = 'plugin:taskmaster:tm'

const positive = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null)
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null)

export function guardConfigOf(options: PluginOptions): GuardConfig {
  const minutes = positive(options.handoverGuardIdleMinutes)
  return {
    enabled: options.handoverGuard === true,
    idleMs: minutes === null ? GUARD_DEFAULTS.idleMs : Math.min(minutes, MAX_IDLE_MINUTES) * MIN,
    minTokens: positive(options.handoverGuardMinTokens) ?? GUARD_DEFAULTS.minTokens,
    fork: options.handoverGuardFork !== false,
    compact: options.handoverGuardCompact !== false,
    maxRefreshes: count(options.handoverGuardMaxRefreshes) ?? GUARD_DEFAULTS.maxRefreshes,
  }
}

/** Agents still at work in this session; a teammate waiting for a message (`idle`) is not. */
const BUSY_STATUSES: ReadonlySet<string> = new Set(['pending', 'running', 'waiting'])

/**
 * Why the session is not really idle, or null. A listed subagent at work, or any sub-loop's tool call within the idle
 * window: a workflow's agents are never listed, but their tool calls carry an `agentId` all the same.
 */
export function busyReason(
  agents: readonly { readonly status: string }[],
  lastSubLoopCall: number | null,
  now: number,
  windowMs: number,
): string | null {
  const working = agents.filter(a => BUSY_STATUSES.has(a.status)).length
  if (working > 0) return `${working} agent${working === 1 ? '' : 's'} at work`
  if (lastSubLoopCall !== null && now - lastSubLoopCall < windowMs) {
    return `a subagent or workflow called a tool ${Math.round((now - lastSubLoopCall) / MIN)} min ago`
  }
  return null
}

/** The one message a refresh fork answers: a cache read of the whole prefix, which restarts the cache's hour. */
export const REFRESH_PROMPT = '<cache_refresh>Reply with the single word OK.</cache_refresh>'

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

/** Without a fork: the prompt the main conversation answers with the handover skill. */
export const guardPrompt = (idleMinutes: number, tokens: number, compact: boolean): string =>
  `Cache-cold guard: this session has been idle ${idleMinutes} min at ${Math.round(tokens / 1000)}k tokens of context and the ` +
  'prompt cache expires at 60 min. Write a handover now with the taskmaster:handover skill, then stop.' +
  (compact ? ' The session is compacted once this turn ends.' : '')

/** The one message the fork answers: it has no tools, so it hands the handover back as text for the guard to file. */
export const forkPrompt = (idleMinutes: number, tokens: number): string =>
  [
    `<situation>This session has been idle ${idleMinutes} min at ${Math.round(tokens / 1000)}k tokens of context and the prompt`,
    'cache expires at 60 min. A plugin is filing a Taskmaster handover from this transcript, then compacting the conversation.',
    'You have no tools in this reply: answer in text only.</situation>',
    '<task>Write the handover a cold session needs to resume this work: what was decided and why, what blocks, where to start',
    'next, and the open threads. Name files, commits, task ids and commands exactly as the transcript has them; never invent',
    'one.</task>',
    '<format>Reply with exactly one handover element and nothing else:',
    '<handover>',
    '<tldr>one line: what this session was about and where it stands</tldr>',
    '<next_action>one line: the first thing the next session should do</next_action>',
    '<task_ids>comma-separated Taskmaster task ids this work relates to, or empty</task_ids>',
    "<body>markdown with the sections ## Decisions, ## Blockers, ## Where I'd start, ## Open threads</body>",
    '</handover></format>',
  ].join('\n')

export type ForkHandover = { readonly tldr: string; readonly nextAction: string; readonly taskIds: readonly string[]; readonly body: string }

/** The fork's reply as a handover; null without a one-line tldr and a body (prose around the element is ignored). */
export function parseForkHandover(text: string): ForkHandover | null {
  const tag = (name: string): string => new RegExp(`<${name}>([\\s\\S]*?)</${name}>`).exec(text)?.[1]?.trim() ?? ''
  const line = (name: string): string => tag(name).replace(/\s+/g, ' ')
  const tldr = line('tldr')
  const body = tag('body')
  if (tldr === '' || body === '') return null
  return { tldr, nextAction: line('next_action'), taskIds: tag('task_ids').split(/[\s,]+/).filter(Boolean), body }
}

export function handoverArgs(h: ForkHandover, tokens: number): Record<string, unknown> {
  return {
    tldr: h.tldr,
    body: h.body,
    next_action: h.nextAction,
    session_kind: 'continuity',
    ...(h.taskIds.length > 0 ? { task_ids: [...h.taskIds] } : {}),
    options: { context_size_at_write: tokens },
  }
}

export const compactInstructions = (id: string, h: Pick<ForkHandover, 'tldr' | 'nextAction'>): string =>
  `Handover ${id} was just written for this work: ${h.tldr}` +
  (h.nextAction === '' ? '' : ` Next: ${h.nextAction}`) +
  ` Keep what resuming from it needs, and name ${id} in the summary.`

/** After the guard's own prompt turn ends, the compaction waits this long: a compaction never runs inside a turn. */
const COMPACT_AFTER_TURN_MS = 1_000

type Owed = { id: string | null; tldr: string; nextAction: string }

// The pending check of this module instance; a hot reload cancels it with the old environment, the latch stays in $.state.
// `epoch` moves on at every turn start, prompt and session end: an arm or a check begun before one of them is stale.
// `owed`: the guard prompted the main conversation with compaction on; the handover its turn writes fills it in, and the
// turn's end compacts. A user prompt or a session end lets it go.
// `refreshes`: cache refreshes since the main loop last ended a turn; `lastSubLoopCall`: when a subagent's or a workflow's
// loop last called a tool. Both live with the module: a reload forgets them, which costs at most a few refreshes.
const guard: {
  cfg: GuardConfig
  pending: Timer | null
  epoch: number
  owed: Owed | null
  refreshes: number
  lastSubLoopCall: number | null
} = {
  cfg: GUARD_DEFAULTS,
  pending: null,
  epoch: 0,
  owed: null,
  refreshes: 0,
  lastSubLoopCall: null,
}

function disarm(): void {
  guard.pending?.cancel()
  guard.pending = null
}

function interrupt(): void {
  guard.epoch += 1
  disarm()
}

async function check($: EngineInterface, epoch: number): Promise<void> {
  guard.pending = null
  const cfg = guard.cfg
  let tokens: number | undefined
  try {
    tokens = (await $.session.usage()).context.tokens
  } catch {
    tokens = undefined
  }
  const now = await $.clock.now()
  const verdict = guardVerdict(cfg, await read($, GUARD), now, tokens)
  if (epoch !== guard.epoch) return
  if (!verdict.fire) {
    if (verdict.reason !== 'disabled') $.ui.log(`taskmaster-mods: handover guard skipped: ${verdict.reason}`, { to: 'debug' })
    return
  }
  let agents: readonly { readonly status: string }[] = []
  try {
    agents = await $.agent.list()
  } catch {
    // an unreadable list falls back to the sub-loop clock alone
  }
  const busy = busyReason(agents, guard.lastSubLoopCall, now, cfg.idleMs)
  if (epoch !== guard.epoch) return
  if (busy !== null) return refresh($, epoch, busy)
  // Latch before forking: only an unlatched session may fire, and a failed run never asks again.
  let won = false
  await update($, GUARD, (g): TmHandoverGuard => {
    won = g.latch === 'none'
    return won ? { ...g, latch: 'fired' } : g
  })
  if (!won) return
  const minutes = Math.round(cfg.idleMs / MIN)
  $.ui.log(`taskmaster-mods: handover guard fired at ${verdict.tokens} tokens after ${minutes} idle min`, { to: 'debug' })
  const how = `${cfg.fork ? 'writing a handover from a fork' : 'asking for a handover'}${cfg.compact ? ', then compacting' : ''}`
  $.ui.toast(`Idle ${minutes} min at ${Math.round(verdict.tokens / 1000)}k tokens: ${how}`)
  if (epoch !== guard.epoch) {
    $.ui.log('taskmaster-mods: handover guard latched but not run: the session moved on meanwhile', { to: 'debug' })
    return
  }
  try {
    if (cfg.fork) await forkAndCompact($, epoch, minutes, verdict.tokens, cfg.compact)
    else await promptForHandover($, minutes, verdict.tokens, cfg.compact)
  } catch (error) {
    $.ui.log(`taskmaster-mods: handover guard stopped: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
  }
}

/** Subagents still work: no handover, no compaction; one cheap fork re-reads the cached prefix and the timer restarts. */
async function refresh($: EngineInterface, epoch: number, busy: string): Promise<void> {
  const max = guard.cfg.maxRefreshes
  if (guard.refreshes >= max) {
    $.ui.log(`taskmaster-mods: handover guard: ${busy}, and ${max} cache refreshes are spent: the cache is left to go cold`, { to: 'debug' })
    return
  }
  guard.refreshes += 1
  const n = guard.refreshes
  const reply = await $.model.fork({ prompt: REFRESH_PROMPT })
  if (epoch !== guard.epoch) return
  $.ui.log(
    reply.isAnswered
      ? `taskmaster-mods: handover guard refreshed the cache (${n}/${max}): ${busy}`
      : `taskmaster-mods: handover guard cache refresh ${n}/${max} failed (${reply.reason}): ${busy}`,
    { to: 'debug' },
  )
  const now = await $.clock.now()
  // The refresh is the cache's last touch: the next check, and its lateness, count from here.
  const held = await update($, GUARD, (g): TmHandoverGuard => (g.latch === 'none' ? { ...g, lastTurnEnd: now } : g))
  if (held.latch !== 'none' || epoch !== guard.epoch) return
  schedule($, epoch)
}

/** The main conversation writes it; with compaction on, its turn's end compacts once a handover was written in it. */
async function promptForHandover($: EngineInterface, minutes: number, tokens: number, compact: boolean): Promise<void> {
  guard.owed = compact ? { id: null, tldr: '', nextAction: '' } : null
  try {
    await $.prompt.submit({ text: guardPrompt(minutes, tokens, compact) })
  } catch (error) {
    guard.owed = null
    throw error
  }
}

/** Fork → file the handover → compact (when on); a step that fails stops the rest, so a compaction always has its handover. */
async function forkAndCompact($: EngineInterface, epoch: number, minutes: number, tokens: number, compact: boolean): Promise<void> {
  const fail = (why: string): void => {
    $.ui.log(`taskmaster-mods: handover guard: ${why}`, { to: 'debug' })
    $.ui.toast(`Handover guard: ${why}`)
  }
  const reply = await $.model.fork({ prompt: forkPrompt(minutes, tokens) })
  if (!reply.isAnswered) return fail(`no handover, the fork did not answer (${reply.reason})`)
  const handover = parseForkHandover(reply.text)
  if (handover === null) return fail('no handover, the fork reply had no tldr and body')

  const args = handoverArgs(handover, tokens)
  const ran = await $.mcp.call(TM_SERVER, 'backlog_handover_create', args)
  const text = replyText(
    ran.content.map(block => (block.type === 'text' ? String((block as { text?: unknown }).text ?? '') : '')).join('\n'),
    ran.structuredContent,
  )
  const receipt = ran.isError ? null : parseHandoverWritten(text)
  if (receipt === null) return fail(`no handover, tm refused it: ${text.split('\n')[0] ?? ''}`)
  await noteHandover($)
  const root = (await $.session.repo())?.root ?? (await $.session.root())
  await update($, NOTICE, () => handoverNotice(args, receipt, root))
  $.ui.log(`taskmaster-mods: handover guard wrote ${receipt.id} from a fork`, { to: 'debug' })

  if (!compact) return
  if (epoch !== guard.epoch) {
    $.ui.log(`taskmaster-mods: handover guard wrote ${receipt.id} but did not compact: the session moved on meanwhile`, { to: 'debug' })
    return
  }
  await compactFor($, receipt.id, handover)
}

async function compactFor($: EngineInterface, id: string, h: Pick<ForkHandover, 'tldr' | 'nextAction'>): Promise<void> {
  const compacted = await $.session.compact({ instructions: compactInstructions(id, h) })
  if (compacted.skip !== undefined) $.ui.log(`taskmaster-mods: handover guard did not compact: ${compacted.skip}`, { to: 'debug' })
}

/** The guard's own prompt turn ended: compact a moment later if it wrote a handover, else say why not. */
function settleOwed($: EngineInterface): void {
  const owed = guard.owed
  guard.owed = null
  if (owed === null) return
  const id = owed.id
  if (id === null) {
    $.ui.log('taskmaster-mods: handover guard did not compact: its turn wrote no handover', { to: 'debug' })
    return
  }
  const epoch = guard.epoch
  $.clock.after(COMPACT_AFTER_TURN_MS, () => {
    if (epoch !== guard.epoch) {
      $.ui.log(`taskmaster-mods: handover guard wrote ${id} but did not compact: the session moved on meanwhile`, { to: 'debug' })
      return
    }
    void compactFor($, id, owed).catch(error => {
      $.ui.log(`taskmaster-mods: handover guard did not compact: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    })
  })
}

async function arm($: EngineInterface, epoch: number): Promise<void> {
  disarm()
  if (!guard.cfg.enabled || epoch !== guard.epoch) return
  const now = await $.clock.now()
  const held = await update($, GUARD, (g): TmHandoverGuard => (g.latch === 'none' ? { ...g, lastTurnEnd: now } : g))
  // A turn that started while this one's end was being recorded must never have a timer running under it.
  if (held.latch !== 'none' || epoch !== guard.epoch) return
  schedule($, epoch)
}

function schedule($: EngineInterface, epoch: number): void {
  disarm()
  guard.pending = $.clock.after(guard.cfg.idleMs, () => {
    void check($, epoch).catch(() => undefined) // a check that cannot read the state now just does not ask
  })
}

/** The handover the guard's prompt turn wrote: what its owed compaction names. */
function owe(input: Readonly<Record<string, unknown>>, text: string): void {
  const id = parseHandoverWritten(text)?.id
  if (id === undefined || guard.owed === null) return
  const field = (key: string): string => {
    const v = input[key]
    return typeof v === 'string' ? v.replace(/\s+/g, ' ').trim() : ''
  }
  guard.owed = { id, tldr: field('tldr'), nextAction: field('next_action') }
}

async function noteHandover($: EngineInterface): Promise<void> {
  disarm()
  await update($, GUARD, (g): TmHandoverGuard => (g.latch === 'handover' ? g : { ...g, latch: 'handover' }))
}

export function onHandoverGuard(on: On, options: PluginOptions): void {
  guard.cfg = guardConfigOf(options)
  interrupt()

  on('turn.complete', async ($, e, next) => {
    const epoch = guard.epoch
    const result = await next(e)
    if (e.agentId !== undefined) return result
    guard.refreshes = 0
    try {
      settleOwed($)
      await arm($, epoch)
    } catch (error) {
      $.ui.log(`taskmaster-mods: handover guard not armed: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return result
  })

  on('turn.start', async ($, e, next) => {
    interrupt()
    return next(e)
  })

  // The person's own prompt (the guard's skips its own hooks): a compaction owed to the guard's turn is let go.
  on('prompt.submit', async ($, e, next) => {
    interrupt()
    guard.owed = null
    return next(e)
  })

  // /clear and a resume end this session: a timer armed in it must never check (or fork, or compact) the next one.
  on('session.end', async ($, e, next) => {
    interrupt()
    guard.owed = null
    return next(e)
  })

  // Any loop but the main one at work (a subagent, a workflow's agent): the session is not idle while it lasts. The matcher
  // every call meets keeps this beside the handover-create hook below.
  on('tool.call', { tool: /^/ }, async ($, e, next) => {
    if (e.agentId !== undefined) {
      try {
        guard.lastSubLoopCall = await $.clock.now()
      } catch {
        // a clock that cannot be read leaves the last time standing
      }
    }
    return next(e)
  })

  // Tracked whether or not the guard is on, so enabling it later in the session still knows a handover exists.
  on('tool.call', { tool: /backlog_handover_create$/ }, async ($, e, next) => {
    const ran: ToolCallResult = await next(e)
    try {
      if (isHandoverWritten(ran)) {
        if (e.agentId === undefined) owe(e as unknown as Readonly<Record<string, unknown>>, (ran as { text?: string }).text ?? '')
        await noteHandover($)
      }
    } catch {
      // a latch that cannot be written now only lets the guard ask once more; the tool's result is never held up
    }
    return ran
  })
}
