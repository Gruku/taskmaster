// User intent: taskmaster-tui's front door — bind Claude Code's events to the Taskmaster surfaces: the band above the prompt
// (this session's task, what waits on the user, a just-written handover to copy), the review queue and handovers panes and the
// faults-only status line, reading live tm data and keeping this session's task binding across /clear, /resume and /branch.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { TmBandMode, TmBinding, TmCursor, TmFault, TmHandoverNotice, TmHandoverSummary, TmSnapshot, TmTaskDetail } from '../types'
import { demoActions, readOnlyActions } from './actions'
import { bindingChange, callSucceeded, isWriteTool, parseStoredBinding, pruneBindings, ranText, type TmRan } from './binding'
import { DEMO_DETAILS, DEMO_SUMMARIES, demoSnapshot, isCurrentDemo, isDemoSnapshot } from './demo'
import { bandTree, handoversPaneTree, reviewPaneTree, type Ui } from './draw'
import { createFlows, type TmFlows, type TmWriter } from './flows'
import { isHandoverWritten, onHandoverGuard } from './handover-guard'
import type { TmHost } from './host'
import {
  bandModel,
  cardPosition,
  FRESH_CURSOR,
  handoverNotice,
  HANDOVERS,
  isStaleTicks,
  REVIEW,
  reviewQueue,
  TICKS_PREFIX,
  ticksOf,
} from './model'
import { parseHandoverWritten } from './parse'
import { type Refresher, singleFlight } from './refresh'
import type { Rr } from './rr'
import { FAULT_LINE, loadDetail, readSummary, refreshOnce, type TmIo, type TmScope } from './tm'

const SNAPSHOT = atom({ plugin: 'taskmaster-tui', key: 'snapshot' } as const, null as TmSnapshot | null)
const CURSOR = atom({ plugin: 'taskmaster-tui', key: 'cursor' } as const, FRESH_CURSOR as TmCursor)
const DETAILS = atom({ plugin: 'taskmaster-tui', key: 'details' } as const, {} as Readonly<Record<string, TmTaskDetail>>)
const PICK = atom({ plugin: 'taskmaster-tui', key: 'pick' } as const, '')
const TICKS = atom({ plugin: 'taskmaster-tui', key: 'ticks' } as const, {} as Readonly<Record<string, readonly string[]>>)
const DETAILS_OPEN = atom({ plugin: 'taskmaster-tui', key: 'detailsOpen' } as const, false)
const SUMMARIES = atom({ plugin: 'taskmaster-tui', key: 'summaries' } as const, {} as Readonly<Record<string, TmHandoverSummary>>)
const SUMMARY_OPEN = atom({ plugin: 'taskmaster-tui', key: 'summaryOpen' } as const, '')
const BAND = atom({ plugin: 'taskmaster-tui', key: 'band' } as const, { confirmingId: '', refusal: '' } as TmBandMode)
const FAULT = atom({ plugin: 'taskmaster-tui', key: 'fault' } as const, 'none' as TmFault)
const BINDING = atom({ plugin: 'taskmaster-tui', key: 'binding' } as const, null as TmBinding | null)
const NOTICE = atom({ plugin: 'taskmaster-tui', key: 'handoverNotice' } as const, null as TmHandoverNotice | null)
const RR_POLARITY = { plugin: 'rr-tui', key: 'polarity' } as const

function hostOf($: EngineInterface): TmHost {
  return {
    now: () => $.clock.now(),
    sleep: (ms, signal) => $.clock.sleep(ms, signal ? { signal } : undefined),
    after: (ms, fn) => {
      $.clock.after(ms, fn)
    },
    call: async (tool, args) => {
      const r = await $.mcp.call('plugin:taskmaster:tm', tool, args)
      const text = r.content.map(block => (block.type === 'text' ? String((block as { text?: unknown }).text ?? '') : '')).join('\n')
      return { text, isError: r.isError }
    },
    storeGet: key => $.store.get(key),
    storeSet: (key, value) => $.store.set(key, value),
    storeDelete: key => $.store.delete(key),
    storeKeys: () => $.store.keys(),
    status: text => $.ui.status(text),
    toast: text => $.ui.toast(text),
    log: text => $.ui.log(text, { to: 'debug' }),
    fill: async text => (await $.prompt.fill({ text })).isFilled,
    copy: async (text, surface) => {
      const r = await $.ui.copy(surface === undefined ? { text } : { text, surface })
      return r.isCopied ? { isCopied: true, reason: '' } : { isCopied: false, reason: String(r.reason) }
    },
    openPane: async (id, title) => (await $.ui.open({ id, title, focus: true, closeOnEscape: true })).isPlaced,
    closePane: id => $.ui.close({ id }),
    focus: async (requestId, key) => {
      try {
        await $.ui.focus({ requestId, key })
      } catch {
        // The ring stays where it is; the element's autoFocus still marks the safe default.
      }
    },
    sessionId: () => $.session.id(),
    repoRoot: async () => (await $.session.repo())?.root ?? (await $.session.root()),
    cwd: () => $.session.cwd(),
    branch: async () => {
      try {
        const r = await $.process.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], { timeoutMs: 3000 })
        return r.exitCode === 0 ? r.stdout.trim() : ''
      } catch {
        return ''
      }
    },
  }
}

function rrOf($: EngineInterface): Rr {
  return {
    tokens: () => $.rr.tokens(),
    polarity: () => $.rr.polarity(),
    surface: a => $.rr.surface(a),
    surfaceProps: a => $.rr.surfaceProps(a),
    label: a => $.rr.label(a),
    signal: a => $.rr.signal(a),
    row: a => $.rr.row(a),
    rule: a => $.rr.rule(a),
    button: a => $.rr.button(a),
    buttonProps: a => $.rr.buttonProps(a),
    keycap: a => $.rr.keycap(a),
    chip: a => $.rr.chip(a),
  }
}

function writerOf($: EngineInterface): TmWriter {
  return {
    snapshot: async change => {
      await update($, SNAPSHOT, change)
    },
    cursor: async change => {
      await update($, CURSOR, change)
    },
    band: async change => {
      await update($, BAND, change)
    },
    pick: async id => {
      await update($, PICK, () => id)
    },
    details: async change => {
      await update($, DETAILS, change)
    },
    ticks: async change => {
      await update($, TICKS, change)
    },
    detailsOpen: async change => {
      await update($, DETAILS_OPEN, change)
    },
    summaries: async change => {
      await update($, SUMMARIES, change)
    },
    summaryOpen: async change => {
      await update($, SUMMARY_OPEN, change)
    },
    notice: async change => {
      await update($, NOTICE, change)
    },
  }
}

function ioOf($: EngineInterface): TmIo {
  return {
    readBinding: () => read($, BINDING),
    setBinding: async binding => {
      await update($, BINDING, () => binding)
    },
    setSnapshot: async snapshot => {
      await update($, SNAPSHOT, () => snapshot)
    },
    setFault: async fault => {
      await update($, FAULT, () => fault)
    },
  }
}

// A module instance may never see session.start: a userConfig change or any reload of an unchanged module re-runs
// register() with session.start not refiring. So nothing here waits for it: host, io, writer, refresher and flows are built
// by whichever hook needs them first, and demo data is seeded by an acting hook (session.start, command.run, ui.press,
// ui.input) or drawn as a pure fallback. `lastFault` null: no status line written by this instance yet.
const mod: {
  source: 'tm' | 'demo'
  scope: TmScope
  flows: TmFlows | null
  host: TmHost | null
  io: TmIo | null
  write: TmWriter | null
  refresher: Refresher | null
  sessionId: string
  lastFault: TmFault | null
  detailAsked: Set<string>
} = {
  source: 'tm',
  scope: { waitingOnly: true, phase: '' },
  flows: null,
  host: null,
  io: null,
  write: null,
  refresher: null,
  sessionId: '',
  lastFault: null,
  detailAsked: new Set(),
}

function ensureFlows($: EngineInterface): TmFlows {
  const host = (mod.host ??= hostOf($))
  const write = (mod.write ??= writerOf($))
  mod.io ??= ioOf($)
  // One refresh at a time, scheduled off the calling hook: a turn never waits on tm.
  if (mod.source === 'tm') mod.refresher ??= singleFlight(refreshRun, fn => host.after(0, fn))
  mod.flows ??= createFlows({
    host,
    write,
    actions: mod.source === 'demo' ? demoActions() : readOnlyActions(),
    afterWrite: (taskId, outcome) => {
      if (outcome === 'done') void unbindIf(taskId)
      mod.refresher?.request()
    },
    source: mod.source,
    // demo seeds DEMO_SUMMARIES instead, as it does the task details
    ...(mod.source === 'tm' ? { summary: (id: string) => readSummary(host, id) } : {}),
  })
  return mod.flows
}

// Demo mode keeps demo data in $.state (seeded once, then changed only by the flows); tm mode never shows demo data. A seed
// of another version (an older module's, kept across a reload) is replaced whole: its shape may not be today's.
async function ensureSeeded($: EngineInterface): Promise<void> {
  if (mod.source !== 'demo' || isCurrentDemo(await read($, SNAPSHOT))) return
  const now = await $.clock.now()
  await update($, SNAPSHOT, s => (isCurrentDemo(s) ? s : demoSnapshot(now)))
  await update($, DETAILS, () => DEMO_DETAILS)
  await update($, SUMMARIES, () => DEMO_SUMMARIES)
}

async function ready($: EngineInterface): Promise<TmFlows> {
  const flows = ensureFlows($)
  if (mod.sessionId === '') mod.sessionId = await $.session.id()
  await ensureSeeded($)
  return flows
}

/** What the surfaces draw: $.state, or in demo mode before the seed lands, the demo data itself (a render writes nothing). */
async function dataOf($: EngineInterface): Promise<{
  snapshot: TmSnapshot | null
  details: Readonly<Record<string, TmTaskDetail>>
  summaries: Readonly<Record<string, TmHandoverSummary>>
}> {
  const snapshot = await read($, SNAPSHOT)
  const details = await read($, DETAILS)
  const summaries = await read($, SUMMARIES)
  if (mod.source === 'demo') {
    return isCurrentDemo(snapshot)
      ? { snapshot, details, summaries }
      : { snapshot: demoSnapshot(await $.clock.now()), details: DEMO_DETAILS, summaries: DEMO_SUMMARIES }
  }
  return isDemoSnapshot(snapshot) ? { snapshot: null, details: {}, summaries: {} } : { snapshot, details, summaries }
}

/** A card's ticks: the $.state mirror once a toggle wrote it, else what $.store kept (another session, a reload). */
async function ticksFor($: EngineInterface, taskId: string): Promise<readonly string[]> {
  return (await read($, TICKS))[taskId] ?? ticksOf(await $.store.get(`${TICKS_PREFIX}${taskId}`))
}

// Ticks are kept 30 days, like the binding keys; an unreadable entry goes too. Never blocks the start.
async function pruneTicks($: EngineInterface): Promise<void> {
  try {
    const now = await $.clock.now()
    for (const key of await $.store.keys()) {
      if (key.startsWith(TICKS_PREFIX) && isStaleTicks(await $.store.get(key), now)) await $.store.delete(key)
    }
  } catch {
    // a store that cannot be read now is pruned at a later start
  }
}

/** The binding this session rehydrates: its own stored mirror; stale and unreadable mirrors are pruned first (spec §6.3). */
async function restoreBinding($: EngineInterface, host: TmHost): Promise<void> {
  try {
    await pruneBindings(host, await $.clock.now())
  } catch {
    // a store that cannot be read now is pruned at a later start
  }
  const stored = parseStoredBinding(await $.store.get(`binding:${mod.sessionId}`))
  if (stored !== null) await update($, BINDING, () => stored)
}

/** The handover this session's main agent just wrote, from the call's input and the server's receipt: the band's notice. */
async function noteHandover($: EngineInterface, host: TmHost, input: Readonly<Record<string, unknown>>, ran: TmRan): Promise<void> {
  const receipt = parseHandoverWritten(ranText(ran))
  if (receipt === null) return
  const notice = handoverNotice(input, receipt, await host.repoRoot())
  await update($, NOTICE, () => notice)
}

async function bind(taskId: string): Promise<void> {
  const { host, io } = mod
  if (host === null || io === null) return
  const binding = { taskId, at: await host.now() }
  await io.setBinding(binding)
  await host.storeSet(`binding:${mod.sessionId}`, binding)
}

async function unbind(): Promise<void> {
  const { host, io } = mod
  if (host === null || io === null) return
  await io.setBinding(null)
  await host.storeDelete(`binding:${mod.sessionId}`)
}

async function unbindIf(taskId: string): Promise<void> {
  if ((await mod.io?.readBinding())?.taskId === taskId) await unbind()
}

async function refreshRun(): Promise<void> {
  const { host, io } = mod
  if (host === null || io === null) return
  mod.detailAsked.clear()
  try {
    const fault = await refreshOnce(host, io, mod.scope)
    if (fault !== mod.lastFault) {
      host.status(FAULT_LINE[fault])
      mod.lastFault = fault
    }
    // A refreshed snapshot may carry edited handovers: their summaries are read again when next shown.
    await mod.flows?.forgetSummaries()
  } catch (error) {
    host.log(`taskmaster-tui: refresh failed: ${String(error)}`)
  }
}

// Lazy reads a drawing finds missing, off the render: a card's task detail, the open handover's summary.
function askDetail(taskId: string): void {
  const { host, write } = mod
  if (host === null || write === null || mod.detailAsked.has(taskId)) return
  mod.detailAsked.add(taskId)
  host.after(0, () => {
    void loadDetail(host, taskId).then(async detail => {
      if (detail !== null) await write.details(prev => ({ ...prev, [taskId]: detail }))
    })
  })
}

function askSummary(handoverId: string): void {
  const { host, flows } = mod
  if (host === null || flows === null) return
  host.after(0, () => {
    void flows.askSummary(handoverId)
  })
}

/**
 * Spec §6.4: each main-loop turn end asks for one refresh (single-flight, never awaited). The guard's turn.complete hook
 * calls this; the refresher exists once any hook built the flows (session.start, or the band's first draw after a reload).
 */
function refreshAfterTurn(): void {
  if (mod.source === 'tm') mod.refresher?.request()
}

// Press and input handlers run after the ui.press / ui.input hooks below have built the flows; they read mod.flows then.
const act = (run: (flows: TmFlows) => Promise<void>): void => {
  if (mod.flows !== null) void run(mod.flows)
}

export const register: Register = (on, options) => {
  mod.source = options.source === 'demo' ? 'demo' : 'tm'
  mod.scope = {
    waitingOnly: options.reviewScope !== 'all',
    phase: typeof options.reviewPhase === 'string' ? options.reviewPhase.trim() : '',
  }
  mod.flows = null
  mod.host = null
  mod.io = null
  mod.write = null
  mod.refresher = null
  mod.sessionId = ''
  mod.lastFault = null
  mod.detailAsked = new Set()
  onHandoverGuard(on, options, refreshAfterTurn)

  on('session.start', async ($, e, next) => {
    mod.sessionId = await $.session.id()
    await ready($)
    await pruneTicks($)
    for (const command of [
      { name: REVIEW, description: 'Walk the Taskmaster review queue: in-review tasks, P0/P1 issues, open decisions' },
      { name: HANDOVERS, description: 'The last five open Taskmaster handovers: copy for Telegram or resume' },
    ]) {
      try {
        await $.command.register(command)
      } catch {
        // already registered by an earlier load of this module: commands persist across reloads
      }
    }
    if (mod.source === 'tm' && mod.host !== null) {
      await restoreBinding($, mod.host)
      mod.refresher?.request()
    }
    return next(e)
  })

  // /clear carries this session's binding to the new id; /resume and a fork read the resumed id's (spec §6.3). A /clear also
  // drops the handover notice.
  on('classic.SessionStart', async ($, e, next) => {
    const result = await next(e)
    try {
      if (e.source === 'clear') await update($, NOTICE, () => null)
      if (mod.source === 'tm' && (e.source === 'clear' || e.source === 'resume' || e.source === 'fork')) {
        await ready($)
        const previous = mod.sessionId
        mod.sessionId = e.session_id
        const from = e.source === 'resume' ? e.session_id : previous
        const stored = from === '' ? null : parseStoredBinding(await $.store.get(`binding:${from}`))
        if (stored !== null) {
          await update($, BINDING, () => stored)
          if (from !== e.session_id) await $.store.set(`binding:${e.session_id}`, stored)
        }
        mod.refresher?.request()
      }
    } catch (error) {
      $.ui.log(`taskmaster-tui: session binding not carried: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return result
  })

  // This session's own tm calls: a successful pick / claim / status move binds or lets go (spec §6.3), a written handover
  // raises the band's notice, any write refreshes. A subagent's calls (`e.agentId` set) change nothing here.
  on('tool.call', { tool: /^mcp__plugin_taskmaster_tm__/ }, async ($, e, next) => {
    const ran = await next(e)
    if (e.agentId !== undefined) return ran
    try {
      await ready($)
      const tool = String(e.tool)
      const input = e as unknown as Record<string, unknown>
      if (/backlog_handover_create$/.test(tool) && isHandoverWritten(ran) && mod.host !== null) await noteHandover($, mod.host, input, ran as TmRan)
      const io = mod.io
      if (mod.source === 'tm' && io !== null) {
        const ok = callSucceeded(tool, ran as TmRan, input)
        const current = await io.readBinding()
        const change = bindingChange({ tool, input, ok }, current?.taskId ?? null)
        if (change.kind === 'set') await bind(change.taskId)
        if (change.kind === 'clear') await unbind()
        if (isWriteTool(tool)) mod.refresher?.request()
      }
    } catch (error) {
      $.ui.log(`taskmaster-tui: tool.call bookkeeping failed: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return ran
  })

  on('command.run', { command: 'tm-review' }, async $ => {
    await (await ready($)).openReview()
    return { text: 'Review queue opened.' }
  })

  on('command.run', { command: 'tm-handovers' }, async $ => {
    await (await ready($)).openHandovers()
    return { text: 'Handovers opened.' }
  })

  on('ui.press', { plugin: 'taskmaster-tui' }, async ($, e, next) => {
    await ready($)
    return next(e)
  })

  on('ui.input', { plugin: 'taskmaster-tui' }, async ($, e, next) => {
    await ready($)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const below = await next(e)
    if (e.surface === 'mobile') return below
    if (mod.source === 'tm') ensureFlows($) // after a reload, the next turn end finds a refresher to ask
    await $.state.get(RR_POLARITY)
    const notice = await read($, NOTICE)
    const model = bandModel((await dataOf($)).snapshot, notice)
    if (model === null) return below
    const ui = $.ui.resolve(e) as unknown as Ui
    const ours = await bandTree(ui, rrOf($), model, await read($, BAND), e.props.bodyColumns, e.props.maxRows, {
      openReview: () => act(f => f.openReview()),
      openHandovers: () => act(f => f.openHandovers()),
      askDone: id => act(f => f.bandAskDone(id)),
      confirmDone: id => act(f => f.bandConfirmDone(id)),
      cancel: () => act(f => f.bandCancel()),
      sendBack: id => act(f => f.openReview(id, 'note')),
      copyNotice: surface => act(f => (notice === null ? Promise.resolve() : f.copyNotice(notice, surface))),
    })
    const { Box } = ui
    return (
      <Box flexDirection="column">
        {ours}
        {below}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: 'tm-review' }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the review queue in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    const { snapshot, details } = await dataOf($)
    const cursor = await read($, CURSOR)
    if (mod.source === 'tm' && snapshot !== null && snapshot.reachable) {
      ensureFlows($)
      const item = cardPosition(reviewQueue(snapshot, cursor, details), cursor, snapshot.queueTotal).item
      if (item !== null && item.kind === 'task' && details[item.id] === undefined) askDetail(item.id)
    }
    const view = {
      snapshot,
      details,
      cursor,
      now: await $.clock.now(),
      ticks: (taskId: string) => ticksFor($, taskId),
      detailsOpen: await read($, DETAILS_OPEN),
    }
    return reviewPaneTree(
      $.ui.resolve(e) as unknown as Ui,
      rrOf($),
      view,
      {
        askDone: id => act(f => f.askDone(id)),
        confirmDone: id => act(f => f.confirmDone(id)),
        cancel: () => act(f => f.cancel()),
        askNote: id => act(f => f.askNote(id)),
        sendBack: (id, note) => act(f => f.sendBack(id, note)),
        skip: id => act(f => f.skip(id)),
        fill: text => act(f => f.fill(text)),
        toggleTick: (id, item) => act(f => f.toggleTick(id, item)),
        toggleDetails: () => act(f => f.toggleDetails()),
        openViewer: id => act(f => f.openViewer(id)),
        copyCheck: (id, text, surface) => act(f => f.copyCheck(id, text, surface)),
      },
      e.props.bodyColumns,
    )
  })

  on('ui.render', { component: 'Pane', requestId: 'tm-handovers' }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the handovers in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    const { snapshot, summaries } = await dataOf($)
    const view = { snapshot, pick: await read($, PICK), summaries, summaryOpen: await read($, SUMMARY_OPEN) }
    // The open summary a refresh forgot is read again (the toggle reads it the first time).
    if (mod.source === 'tm' && snapshot !== null && snapshot.reachable && view.summaryOpen !== '') {
      const picked = snapshot.handovers.find(entry => entry.id === view.pick) ?? snapshot.handovers[0]
      if (picked !== undefined && picked.id === view.summaryOpen && summaries[picked.id] === undefined) {
        ensureFlows($)
        askSummary(picked.id)
      }
    }
    return handoversPaneTree(
      $.ui.resolve(e) as unknown as Ui,
      rrOf($),
      view,
      {
        pick: id => act(f => f.pick(id)),
        copy: (handover, surface) => act(f => f.copyHandover(handover, surface)),
        resume: handover => act(f => f.resumeHandover(handover)),
        toggleSummary: (handover, open) => act(f => f.toggleSummary(handover, open)),
      },
      e.props.bodyColumns,
    )
  })

  on('ui.focus', { requestId: 'tm-handovers' }, async ($, e, next) => {
    const moved = await next(e)
    const element = e.element
    if (element !== undefined && element.startsWith('ho:')) {
      const id = element.slice(3)
      await update($, PICK, () => id)
      await update($, SUMMARY_OPEN, open => (open === id ? open : '')) // another row shows collapsed, and so does this one later
    }
    return moved
  }).catch(($, e, next) => next(e)) // a failed pick write never holds the focus move up: replay what the chain beneath settled
}
