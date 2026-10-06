// User intent: taskmaster-tui's front door — bind Claude Code's events to the Taskmaster surfaces: the band above the prompt
// (this session's task and what waits on the user), the review queue and the handovers pane.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { TmBandMode, TmCursor, TmHandoverSummary, TmSnapshot, TmTaskDetail } from '../types'
import { demoActions, pendingActions } from './actions'
import { DEMO_DETAILS, DEMO_REASON, DEMO_SUMMARIES, demoSnapshot } from './demo'
import { bandTree, handoversPaneTree, reviewPaneTree, type Ui } from './draw'
import { createFlows, type TmFlows, type TmWriter } from './flows'
import type { TmHost } from './host'
import { bandModel, FRESH_CURSOR, HANDOVERS, isStaleTicks, REVIEW, TICKS_PREFIX, ticksOf } from './model'
import type { Rr } from './rr'

const SNAPSHOT = atom({ plugin: 'taskmaster-tui', key: 'snapshot' } as const, null as TmSnapshot | null)
const CURSOR = atom({ plugin: 'taskmaster-tui', key: 'cursor' } as const, FRESH_CURSOR as TmCursor)
const DETAILS = atom({ plugin: 'taskmaster-tui', key: 'details' } as const, {} as Readonly<Record<string, TmTaskDetail>>)
const PICK = atom({ plugin: 'taskmaster-tui', key: 'pick' } as const, '')
const TICKS = atom({ plugin: 'taskmaster-tui', key: 'ticks' } as const, {} as Readonly<Record<string, readonly string[]>>)
const DETAILS_OPEN = atom({ plugin: 'taskmaster-tui', key: 'detailsOpen' } as const, false)
const SUMMARIES = atom({ plugin: 'taskmaster-tui', key: 'summaries' } as const, {} as Readonly<Record<string, TmHandoverSummary>>)
const SUMMARY_OPEN = atom({ plugin: 'taskmaster-tui', key: 'summaryOpen' } as const, '')
const BAND = atom({ plugin: 'taskmaster-tui', key: 'band' } as const, { confirmingId: '', refusal: '' } as TmBandMode)
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
  }
}

// A module instance may never see session.start: a userConfig change or any reload of an unchanged module re-runs
// register() with session.start not refiring. So nothing here waits for it: the flows are built by whichever acting hook
// (session.start, command.run, ui.press, ui.input) runs first, and demo data is seeded there or drawn as a pure fallback.
const mod: { flows: TmFlows | null; source: 'tm' | 'demo' } = { flows: null, source: 'tm' }

function ensureFlows($: EngineInterface): TmFlows {
  mod.flows ??= createFlows({
    host: hostOf($),
    write: writerOf($),
    actions: mod.source === 'demo' ? demoActions() : pendingActions(),
    afterWrite: () => undefined,
    source: mod.source,
    // No `summary` reader yet: Task 3b adds one for tm (backlog_handover_get, sections decisions + blockers); demo seeds
    // DEMO_SUMMARIES instead, as it does the task details.
  })
  return mod.flows
}

const isDemo = (s: TmSnapshot | null): boolean => s !== null && s.reason === DEMO_REASON

// Demo mode keeps demo data in $.state (seeded once, then changed only by the flows); tm mode never shows demo data.
async function ensureSeeded($: EngineInterface): Promise<void> {
  if (mod.source !== 'demo' || isDemo(await read($, SNAPSHOT))) return
  const now = await $.clock.now()
  await update($, SNAPSHOT, s => (isDemo(s) ? s : demoSnapshot(now)))
  await update($, DETAILS, () => DEMO_DETAILS)
  await update($, SUMMARIES, () => DEMO_SUMMARIES)
}

async function ready($: EngineInterface): Promise<TmFlows> {
  const flows = ensureFlows($)
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
    return isDemo(snapshot)
      ? { snapshot, details, summaries }
      : { snapshot: demoSnapshot(await $.clock.now()), details: DEMO_DETAILS, summaries: DEMO_SUMMARIES }
  }
  return isDemo(snapshot) ? { snapshot: null, details: {}, summaries: {} } : { snapshot, details, summaries }
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

// Press and input handlers run after the ui.press / ui.input hooks below have built the flows; they read mod.flows then.
const act = (run: (flows: TmFlows) => Promise<void>): void => {
  if (mod.flows !== null) void run(mod.flows)
}

export const register: Register = (on, options) => {
  mod.source = options.source === 'demo' ? 'demo' : 'tm'
  mod.flows = null

  on('session.start', async ($, e, next) => {
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
    return next(e)
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
    await $.state.get(RR_POLARITY)
    const model = bandModel((await dataOf($)).snapshot)
    if (model === null) return below
    const ui = $.ui.resolve(e) as unknown as Ui
    const ours = await bandTree(ui, rrOf($), model, await read($, BAND), e.props.bodyColumns, e.props.maxRows, {
      openReview: () => act(f => f.openReview()),
      openHandovers: () => act(f => f.openHandovers()),
      askDone: id => act(f => f.bandAskDone(id)),
      confirmDone: id => act(f => f.bandConfirmDone(id)),
      cancel: () => act(f => f.bandCancel()),
      sendBack: id => act(f => f.openReview(id, 'note')),
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
    const view = {
      snapshot,
      details,
      cursor: await read($, CURSOR),
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
