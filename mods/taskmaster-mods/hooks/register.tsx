// User intent: taskmaster-mods's front door — bind Claude Code's events to the Taskmaster surfaces: the band above the prompt
// (this session's task, its handover, keys to every pane), the Session tab, the review queue and handovers panes and the
// faults-only status line, reading live tm data and keeping this session's task binding across /clear, /resume and /branch.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type {
  TmBandMode,
  TmBinding,
  TmCursor,
  TmFault,
  TmHandover,
  TmHandoverList,
  TmHandoverMarks,
  TmHandoverNotice,
  TmHandoverSummary,
  TmNav,
  TmPhasePane,
  TmPhaseView,
  TmRestorePending,
  TmReviewList,
  TmSessionBurst,
  TmSessionHandover,
  TmSessionInfo,
  TmSnapshot,
  TmTaskDetail,
} from '../types'
import { demoActions, demoCloseHandover, tmActions, tmCloseHandover } from './actions'
import { bindingChange, callSucceeded, isWriteTool, parseStoredBinding, pruneBindings, ranText, type TmRan } from './binding'
import { DEMO_DETAILS, DEMO_RESUMED_FROM, DEMO_SUMMARIES, demoHandoverList, demoPhases, demoSnapshot, isCurrentDemo } from './demo'
import { bandTree } from './band'
import { createFlows, type TmFlows, type TmPane, type TmWriter } from './flows'
import { isHandoverWritten, onHandoverGuard } from './handover-guard'
import { onRestore, refreshRestore } from './restore/hooks'
import { FRESH_MARKS, listedHandovers, phaseHandovers } from './handover-list'
import { type HandoversView, handoversPaneTree } from './handovers'
import type { TmHost } from './host'
import { kitOf, type Ui } from './kit'
import { escLayer, FRESH_NAV, paneOf, shownRestore, shownSnapshot, type SidebarView, sidebarView } from './nav'
import { SIDEBAR_ESC, withSidebar } from './vocab'
import { KIT, kitGalleryTree } from './kit-gallery'
import { resumePrompt } from './launch'
import {
  bandModel,
  FRESH_CURSOR,
  handoverNotice,
  handoverPage,
  HANDOVERS,
  isStaleTicks,
  restoreHiddenKey,
  REVIEW,
  SESSION,
  sessionHandoverOf,
  TICKS_PREFIX,
  ticksOf,
  withNotice,
} from './model'
import { parseHandoverWritten, replyText } from './parse'
import { ALL, FRESH_PHASE_VIEWS, loadChoice, PHASE_PANES, phaseStoreOf } from './phases'
import { type Refresher, type RunOutcome, singleFlight } from './refresh'
import { reviewPaneTree } from './review'
import { FRESH_REVIEW_LIST, listView, reviewItems } from './review-list'
import { readResumedFrom, RESUME_PROMPT_RE, resumedIdOf } from './resumed'
import { sessionTree } from './session'
import type { Rr } from './rr'
import { FAULT_LINE, loadDetail, readHandover, readHandoverList, readPhases, readSummary, refreshOnce, type TmIo, type TmScope, writeTm } from './tm'

const SNAPSHOT = atom({ plugin: 'taskmaster-mods', key: 'snapshot' } as const, null as TmSnapshot | null)
const CURSOR = atom({ plugin: 'taskmaster-mods', key: 'cursor' } as const, FRESH_CURSOR as TmCursor)
const DETAILS = atom({ plugin: 'taskmaster-mods', key: 'details' } as const, {} as Readonly<Record<string, TmTaskDetail>>)
const PICK = atom({ plugin: 'taskmaster-mods', key: 'pick' } as const, '')
const TICKS = atom({ plugin: 'taskmaster-mods', key: 'ticks' } as const, {} as Readonly<Record<string, readonly string[]>>)
const DETAILS_OPEN = atom({ plugin: 'taskmaster-mods', key: 'detailsOpen' } as const, false)
const SUMMARIES = atom({ plugin: 'taskmaster-mods', key: 'summaries' } as const, {} as Readonly<Record<string, TmHandoverSummary>>)
const SUMMARY_OPEN = atom({ plugin: 'taskmaster-mods', key: 'summaryOpen' } as const, '')
const BAND = atom({ plugin: 'taskmaster-mods', key: 'band' } as const, { confirmingId: '', refusal: '' } as TmBandMode)
const FAULT = atom({ plugin: 'taskmaster-mods', key: 'fault' } as const, 'none' as TmFault)
const BINDING = atom({ plugin: 'taskmaster-mods', key: 'binding' } as const, null as TmBinding | null)
const NOTICE = atom({ plugin: 'taskmaster-mods', key: 'handoverNotice' } as const, null as TmHandoverNotice | null)
const PHASE_VIEWS = atom({ plugin: 'taskmaster-mods', key: 'phaseViews' } as const, FRESH_PHASE_VIEWS as Readonly<Record<TmPhasePane, TmPhaseView>>)
const HANDOVER_PAGE = atom({ plugin: 'taskmaster-mods', key: 'handoverPage' } as const, 0)
const HANDOVER_LIST = atom({ plugin: 'taskmaster-mods', key: 'handoverList' } as const, null as TmHandoverList | null)
const HANDOVER_MARKS = atom({ plugin: 'taskmaster-mods', key: 'handoverMarks' } as const, FRESH_MARKS as TmHandoverMarks)
const REVIEW_LIST = atom({ plugin: 'taskmaster-mods', key: 'reviewList' } as const, FRESH_REVIEW_LIST as TmReviewList)
const SESSION_HANDOVER = atom({ plugin: 'taskmaster-mods', key: 'sessionHandover' } as const, null as TmSessionHandover | null)
const RESUMED_FROM = atom({ plugin: 'taskmaster-mods', key: 'resumedFrom' } as const, '')
const RESTORE_PENDING = atom({ plugin: 'taskmaster-mods', key: 'restorePending' } as const, null as TmRestorePending | null)
const RESTORE_HIDDEN = atom({ plugin: 'taskmaster-mods', key: 'restoreHidden' } as const, null as number | null)
const SESSION_BURST = atom({ plugin: 'taskmaster-mods', key: 'sessionBurst' } as const, null as TmSessionBurst | null)
const NAV = atom({ plugin: 'taskmaster-mods', key: 'nav' } as const, FRESH_NAV as TmNav)
const SESSION_INFO = atom({ plugin: 'taskmaster-mods', key: 'sessionInfo' } as const, null as TmSessionInfo | null)
const RR_POLARITY = { plugin: 'rr-tui', key: 'polarity' } as const

/** Exported so a test can drive the real bindings. */
export function hostOf($: EngineInterface): TmHost {
  return {
    now: () => $.clock.now(),
    sleep: (ms, signal) => $.clock.sleep(ms, signal ? { signal } : undefined),
    after: (ms, fn) => {
      $.clock.after(ms, fn)
    },
    call: async (tool, args) => {
      const r = await $.mcp.call('plugin:taskmaster:tm', tool, args)
      const text = r.content.map(block => (block.type === 'text' ? String((block as { text?: unknown }).text ?? '') : '')).join('\n')
      return { text: replyText(text, r.structuredContent), isError: r.isError }
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
    panes: async () => (await $.ui.panes()).map(pane => pane.id),
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
    launchEnv: async () => ({
      os: await $.env.get('OS'),
      termProgram: await $.env.get('TERM_PROGRAM'),
      configDir: await $.env.get('CLAUDE_CONFIG_DIR'),
    }),
    run: async (argv, timeoutMs) => {
      try {
        const r = await $.process.run(argv, { timeoutMs })
        return { exitCode: r.exitCode, stdout: r.stdout }
      } catch (error) {
        $.ui.log(`taskmaster-mods: ${argv[0] ?? 'command'} did not run: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
        return null
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
    badgeProps: a => $.rr.badgeProps(a),
    tint: a => $.rr.tint(a),
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
    phaseView: async (pane, change) => {
      await update($, PHASE_VIEWS, all => ({ ...all, [pane]: change(all[pane]) }))
    },
    handoverPage: async page => {
      await update($, HANDOVER_PAGE, () => page)
    },
    handoverList: async change => {
      await update($, HANDOVER_LIST, change)
    },
    handoverMarks: async change => {
      await update($, HANDOVER_MARKS, change)
    },
    reviewList: async change => {
      await update($, REVIEW_LIST, change)
    },
    resumedFrom: async id => {
      await update($, RESUMED_FROM, () => id)
    },
    restoreHidden: async epoch => {
      await update($, RESTORE_HIDDEN, () => epoch)
    },
    nav: async change => {
      await update($, NAV, change)
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
// by the first hook that acts (session.start, classic.SessionStart, turn.complete, tool.call, command.run, ui.press,
// ui.input) from its own `$`, and that first act asks for one refresh in tm mode (a changed reviewScope applies at once).
// A draw never builds them and never writes: it only reads $.state, and asks for missing data only through what an acting
// hook built. `lastFault` null: no status line written by this instance yet. `resumedCache`: resumed-from handovers the open
// list does not hold, read once by id (null: not found); `resolve` re-runs D5 with an acting hook's `$`.
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
  refreshAsked: boolean
  phaseRestore: Promise<void> | null
  resumedAsked: Set<string>
  resumedCache: Map<string, TmHandover | null>
  resolve: (() => Promise<void>) | null
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
  refreshAsked: false,
  phaseRestore: null,
  resumedAsked: new Set(),
  resumedCache: new Map(),
  resolve: null,
}

function ensureFlows($: EngineInterface): TmFlows {
  const host = (mod.host ??= hostOf($))
  const write = (mod.write ??= writerOf($))
  mod.io ??= ioOf($)
  mod.resolve ??= () => resolveHandover($)
  // One refresh at a time, scheduled off the calling hook: a turn never waits on tm.
  if (mod.source === 'tm') mod.refresher ??= singleFlight(refreshRun, (fn, delayMs) => host.after(delayMs, fn))
  mod.flows ??= createFlows({
    host,
    write,
    actions: mod.source === 'demo' ? demoActions() : tmActions((tool, args) => writeTm(host, tool, args)),
    afterWrite: (taskId, outcome) => {
      if (outcome === 'done') void unbindIf(taskId)
      mod.refresher?.request()
    },
    boundId: () => boundTaskId($),
    source: mod.source,
    // demo seeds DEMO_SUMMARIES instead, as it does the task details; every demo handover is in its open list
    ...(mod.source === 'tm' ? { summary: (id: string) => readSummary(host, id), readHandover: (id: string) => readHandover(host, id) } : {}),
    refreshRestore,
    readPhases: mod.source === 'demo' ? pane => Promise.resolve(demoPhases(pane)) : pane => readPhases(host, pane, mod.scope.waitingOnly),
    // demo seeds its handover list with the snapshot and never re-reads it
    ...(mod.source === 'tm'
      ? {
          readHandoverList: async (closed: boolean) => readHandoverList(host, await host.repoRoot(), closed),
        }
      : {}),
    closeHandover: mod.source === 'demo' ? demoCloseHandover() : tmCloseHandover((tool, args) => writeTm(host, tool, args)),
    findHandover: async (id: string) => (await read($, HANDOVER_LIST))?.handovers.find(ho => ho.id === id) ?? null,
    phaseChosen: (pane, phase) => {
      if (pane !== 'review') return
      mod.scope = { ...mod.scope, phase }
      mod.refresher?.request()
    },
    ...(mod.source === 'tm' ? { refresh: () => mod.refresher?.request() } : {}),
    resolve: () => resolveHandover($),
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
  await update($, HANDOVER_LIST, () => demoHandoverList(now))
  await update($, PHASE_VIEWS, all => ({
    review: { ...all.review, phases: demoPhases('review'), loaded: true },
    handovers: { ...all.handovers, phases: demoPhases('handovers'), loaded: true },
    restore: { ...all.restore, phases: demoPhases('restore'), loaded: true },
  }))
  await update($, RESUMED_FROM, () => DEMO_RESUMED_FROM)
  await resolveHandover($)
}

/**
 * Each pane's remembered phase (`$.store` `phase:<pane>:<repo root>`, an explicit All included; review's legacy
 * `phase:<repo root>` moves there on this read), once per instance and before the first refresh asks the server. Review's
 * replaces the reviewPhase option's default and is the scope; the atom mirrors every pane's for the drawings.
 */
async function restorePhases($: EngineInterface, host: TmHost): Promise<void> {
  const chosen: Partial<Record<TmPhasePane, string>> = {}
  try {
    const root = await host.repoRoot()
    const store = phaseStoreOf(host)
    for (const pane of PHASE_PANES) {
      const stored = await loadChoice(store, pane, root)
      if (stored !== null) chosen[pane] = stored
    }
  } catch {
    // an unreadable store keeps the option's default (review) and All (the others)
  }
  if (chosen.review !== undefined) mod.scope = { ...mod.scope, phase: chosen.review }
  const review = mod.scope.phase
  await update($, PHASE_VIEWS, all => ({
    review: all.review.choice === review ? all.review : { ...all.review, choice: review },
    handovers: { ...all.handovers, choice: chosen.handovers ?? all.handovers.choice },
    restore: { ...all.restore, choice: chosen.restore ?? all.restore.choice },
  }))
}

/** Every acting hook's entry: the flows from its `$`, the session id, demo's seed, and tm's first refresh of this instance. */
async function ready($: EngineInterface): Promise<TmFlows> {
  const flows = ensureFlows($)
  if (mod.sessionId === '') mod.sessionId = await $.session.id()
  await ensureSeeded($)
  if (mod.source === 'tm' && mod.host !== null) await (mod.phaseRestore ??= restorePhases($, mod.host))
  if (mod.source === 'tm' && !mod.refreshAsked) {
    mod.refreshAsked = true
    mod.refresher?.request()
  }
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
    const shown = shownSnapshot(snapshot, 'demo', await $.clock.now())
    return shown === snapshot ? { snapshot, details, summaries } : { snapshot: shown, details: DEMO_DETAILS, summaries: DEMO_SUMMARIES }
  }
  const shown = shownSnapshot(snapshot, 'tm', 0)
  return shown === null && snapshot !== null ? { snapshot: null, details: {}, summaries: {} } : { snapshot, details, summaries }
}

/** The restore burst the band offers (`4`): pending (the scan wrote it) and hidden neither here nor, for that burst, anywhere. */
async function restoreShown($: EngineInterface): Promise<TmRestorePending | null> {
  const pending = await read($, RESTORE_PENDING)
  const here = await read($, RESTORE_HIDDEN)
  return shownRestore(pending, here, pending !== null && here !== pending.epoch && (await $.store.get(restoreHiddenKey(pending.epoch))) === true)
}

/** The sidebar over a pane while `nav.sidebar` is on (null: off): its entries from the snapshot and the pending restore burst. */
async function sidebarFor($: EngineInterface, current: TmPane): Promise<SidebarView | null> {
  if (!(await read($, NAV)).sidebar) return null
  return tabsFor($, current)
}

/** The tab row atop every pane (always drawn): the sidebar's entries, counts and current pane. */
async function tabsFor($: EngineInterface, current: TmPane): Promise<SidebarView> {
  const { snapshot } = await dataOf($)
  return sidebarView({ current, snapshot, restore: await restoreShown($), now: await $.clock.now() })
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

/**
 * D5 (spec §3): the handover this session shows — written here (the notice) > resumed from (`resumedFrom`) > the newest open
 * one linked to the bound task. A resumed-from handover the open list does not hold is read by id once (tm), off the caller;
 * D5 runs again when it lands.
 */
async function resolveHandover($: EngineInterface): Promise<void> {
  const { snapshot } = await dataOf($)
  const resumedId = await read($, RESUMED_FROM)
  const host = mod.host
  // Read by id only once a reachable snapshot's open list has been looked at and does not hold it.
  const unlisted = snapshot !== null && snapshot.reachable && !snapshot.handovers.some(h => h.id === resumedId)
  if (resumedId !== '' && unlisted && mod.source === 'tm' && host !== null && !mod.resumedAsked.has(resumedId)) {
    mod.resumedAsked.add(resumedId)
    host.after(0, () => {
      void readHandover(host, resumedId).then(async found => {
        mod.resumedCache.set(resumedId, found)
        await mod.resolve?.()
      })
    })
  }
  const next = sessionHandoverOf({ notice: await read($, NOTICE), resumedId, resumed: mod.resumedCache.get(resumedId) ?? null, snapshot })
  await update($, SESSION_HANDOVER, prev => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next))
}

/** This session's resumed-from record (`$.store` `resumed-from:<session id>`) into `resumedFrom`; unreadable reads as none. */
async function loadResumed($: EngineInterface, host: TmHost, sessionId: string): Promise<void> {
  let id = ''
  try {
    id = (await readResumedFrom(phaseStoreOf(host), sessionId)) ?? ''
  } catch {
    // a store that cannot be read now: no resumed-from line this time
  }
  await update($, RESUMED_FROM, () => id)
}

/** The handover this session's main agent just wrote, from the call's input and the server's receipt: the band's notice. */
async function noteHandover($: EngineInterface, host: TmHost, input: Readonly<Record<string, unknown>>, ran: TmRan): Promise<void> {
  const receipt = parseHandoverWritten(ranText(ran))
  if (receipt === null) return
  const notice = handoverNotice(input, receipt, await host.repoRoot())
  await update($, NOTICE, () => notice)
  await resolveHandover($)
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

/**
 * The task the band's d and a may act on (ruling F1): tm, this session's binding, never a queue or branch lookup; demo, the
 * seeded bound task (demo has no binding).
 */
async function boundTaskId($: EngineInterface): Promise<string | null> {
  if (mod.source === 'tm') return (await read($, BINDING))?.taskId ?? null
  const bound = (await dataOf($)).snapshot?.bound ?? null
  return bound === null || bound.inferred ? null : bound.taskId
}

/** One refresh through the refresher an acting hook built; `final`: no retry remains, so a transient failure is reported. */
async function refreshRun(final: boolean): Promise<RunOutcome> {
  const { host, io } = mod
  if (host === null || io === null) return 'done'
  mod.detailAsked.clear()
  try {
    const { fault, transient } = await refreshOnce(host, io, mod.scope, final)
    if (fault !== mod.lastFault) {
      host.status(FAULT_LINE[fault])
      mod.lastFault = fault
    }
    if (transient) return 'transient'
    // A refreshed snapshot may carry edited handovers: their summaries are read again when next shown.
    await mod.flows?.forgetSummaries()
    await mod.resolve?.()
  } catch (error) {
    host.log(`taskmaster-mods: refresh failed: ${String(error)}`)
  }
  return 'done'
}

// Lazy reads a drawing finds missing, off the render and only through what an acting hook built (a draw builds nothing):
// a card's task detail, the open handover's summary.
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
  mod.refreshAsked = false
  mod.phaseRestore = null
  mod.resumedAsked = new Set()
  mod.resumedCache = new Map()
  mod.resolve = null
  onHandoverGuard(on, options)
  onRestore(on, options, { flows: () => mod.flows })

  on('session.start', async ($, e, next) => {
    mod.sessionId = await $.session.id()
    await ready($)
    const branch = (await mod.host?.branch()) ?? ''
    const startedAt = await $.clock.now()
    await update($, SESSION_INFO, () => ({ branch, startedAt }))
    await pruneTicks($)
    // A bulk run does not outlive its session: a row left `busy` by one that was cut off would show "working" for good.
    await update($, REVIEW_LIST, l => (l.bulk === 'busy' ? { ...l, bulk: 'none' as const, bulkIds: [] } : l))
    for (const command of [
      { name: REVIEW, description: 'Walk the Taskmaster review queue: in-review tasks, P0/P1 issues, open decisions' },
      { name: HANDOVERS, description: 'Taskmaster handovers by phase: resume, open in a new tab, copy for Telegram, close' },
      { name: SESSION, description: "This session in Taskmaster: its task, its handover and where it came from" },
      ...(mod.source === 'demo' ? [{ name: KIT, description: 'The taskmaster-mods kit gallery: every primitive with sample data (demo source)' }] : []),
    ]) {
      try {
        await $.command.register(command)
      } catch {
        // already registered by an earlier load of this module: commands persist across reloads
      }
    }
    if (mod.source === 'tm' && mod.host !== null) {
      await restoreBinding($, mod.host)
      await loadResumed($, mod.host, mod.sessionId)
      mod.refresher?.request()
    }
    await resolveHandover($)
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
        // No stored binding is no binding: a resumed session never keeps the task the one before it was on.
        await update($, BINDING, () => stored)
        if (stored !== null && from !== e.session_id) await $.store.set(`binding:${e.session_id}`, stored)
        if (mod.host !== null) await loadResumed($, mod.host, e.session_id)
        await resolveHandover($)
        mod.refresher?.request()
      }
    } catch (error) {
      $.ui.log(`taskmaster-mods: session binding not carried: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return result
  })

  // A main-loop turn end asks for a refresh with this hook's own `$` (spec §6.4: single-flight, never awaited by the turn).
  // The engine allows one unmatched turn.complete per plugin and the handover guard holds it; `$` never crosses an import,
  // so this one stands beside it with a matcher every turn end meets (`answer` is always a string).
  on('turn.complete', { answer: /^/ }, async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined || mod.source !== 'tm') return result
    try {
      await ready($)
      mod.refresher?.request()
    } catch (error) {
      $.ui.log(`taskmaster-mods: refresh not asked: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
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
      $.ui.log(`taskmaster-mods: tool.call bookkeeping failed: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return ran
  })

  // A fresh session launched from a handover starts with resumePrompt's text: it records where it came from (spec §10.5).
  on('prompt.submit', { text: RESUME_PROMPT_RE }, async ($, e, next) => {
    const entered = await next(e)
    try {
      const id = resumedIdOf(e.text)
      if (id !== null) await (await ready($)).recordResumed(id)
    } catch (error) {
      $.ui.log(`taskmaster-mods: resumed-from not recorded: ${error instanceof Error ? error.message : String(error)}`, { to: 'debug' })
    }
    return entered
  })

  on('command.run', { command: 'tm-review' }, async $ => {
    await (await ready($)).openReview()
    return { text: 'Review queue opened.' }
  })

  on('command.run', { command: 'tm-handovers' }, async $ => {
    await (await ready($)).openHandovers()
    return { text: 'Handovers opened.' }
  })

  on('command.run', { command: SESSION }, async $ => {
    await (await ready($)).openPane('session')
    return { text: 'Session opened.' }
  })

  on('command.run', { command: KIT }, async $ => {
    if (mod.source !== 'demo') return { text: 'The kit gallery shows in demo mode (taskmaster-mods Data source = demo in /config).' }
    await ready($)
    const opened = await $.ui.open({ id: KIT, title: 'Kit', focus: true, closeOnEscape: true })
    if (!opened.isPlaced) $.ui.toast('taskmaster-mods: widen the terminal to see the pane')
    return { text: 'Kit gallery opened.' }
  })

  on('ui.press', { plugin: 'taskmaster-mods' }, async ($, e, next) => {
    await ready($)
    return next(e)
  })

  on('ui.input', { plugin: 'taskmaster-mods' }, async ($, e, next) => {
    await ready($)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const below = await next(e)
    if (e.surface === 'mobile') return below
    await $.state.get(RR_POLARITY)
    const { snapshot } = await dataOf($)
    const pending = await restoreShown($)
    const handover = withNotice(await read($, SESSION_HANDOVER), await read($, NOTICE), snapshot)
    const connecting = mod.source === 'tm' && (await read($, FAULT)) === 'connecting'
    const model = bandModel(snapshot, { handover, restore: pending?.unfinished ?? null, connecting })
    if (model === null) return below
    const ui = $.ui.resolve(e) as unknown as Ui
    const k = await kitOf(ui, rrOf($), e.surface, text => mod.host?.toast(text))
    const ours = bandTree(
      k,
      model,
      { mode: await read($, BAND), width: e.props.bodyColumns, maxRows: e.props.maxRows, now: await $.clock.now(), reason: snapshot?.reason ?? '' },
      {
        openSidebar: () => act(f => f.openSidebar()),
        askDone: id => act(f => f.bandAskDone(id)),
        confirmDone: id => act(f => f.bandConfirmDone(id)),
        cancel: () => act(f => f.bandCancel()),
        copy: (id, surface) => act(f => f.copyHandover(id, surface)),
        retry: () => act(f => f.retry()),
      },
    )
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
    const list = await read($, REVIEW_LIST)
    // The focused card's task detail is read on demand (lane, gate, notes); the collapsed cards need none.
    if (mod.source === 'tm' && snapshot !== null && snapshot.reachable) {
      const item = listView(reviewItems(snapshot, cursor, details), cursor.currentId, list.page).focused
      if (item !== null && item.kind === 'task' && details[item.id] === undefined) askDetail(item.id)
    }
    const views = await read($, PHASE_VIEWS)
    // Demo draws its phases before the seed lands, as dataOf draws its snapshot (a render writes nothing).
    const phaseView = mod.source === 'demo' && !views.review.loaded ? { ...views.review, phases: demoPhases('review'), loaded: true } : views.review
    const view = {
      snapshot,
      details,
      cursor,
      list,
      now: await $.clock.now(),
      ticks: (taskId: string) => ticksFor($, taskId),
      detailsOpen: await read($, DETAILS_OPEN),
      connecting: mod.source === 'tm' && (await read($, FAULT)) === 'connecting',
      surface: e.surface,
      say: (text: string) => mod.host?.toast(text),
      demo: mod.source === 'demo',
      tabs: await tabsFor($, 'review'),
      // The digits are the strip's, save while `t` has armed the focused card's checks.
      strip: { pane: 'review' as const, view: phaseView, allCount: phaseView.choice === ALL ? (snapshot?.queueTotal ?? 0) : phaseView.phases.reduce((n, p) => n + p.count, 0), focused: e.props.isFocused, digits: !list.ticking, empty: 'Nothing waiting' },
    }
    const ui = $.ui.resolve(e) as unknown as Ui
    const tree = await reviewPaneTree(
      ui,
      rrOf($),
      view,
      {
        askDone: id => act(f => f.askDone(id)),
        confirmDone: id => act(f => f.confirmDone(id)),
        cancel: () => act(f => f.cancel()),
        askNote: id => act(f => f.askNote(id)),
        sendBack: (id, note, humanAction) => act(f => f.sendBack(id, note, humanAction)),
        fill: text => act(f => f.fill(text)),
        toggleTick: (id, item) => act(f => f.toggleTick(id, item)),
        toggleDetails: () => act(f => f.toggleDetails()),
        openViewer: id => act(f => f.openViewer(id)),
        copyCheck: (id, text, surface) => act(f => f.copyCheck(id, text, surface)),
        strip: {
          choose: id => act(f => f.choosePhase('review', id)),
          toggleDone: () => act(f => f.toggleDone('review')),
          overlay: open => act(f => f.setOverlay('review', open)),
        },
        focus: (id, page) => act(f => f.focusCard(id, page)),
        turnPage: page => act(f => f.turnReviewPage(page)),
        select: id => act(f => f.toggleSelect(id)),
        selectAll: ids => act(f => f.selectAll(ids)),
        selectNone: () => act(f => f.selectNone()),
        refresh: () => act(f => f.refresh('review')),
        armTick: () => act(f => f.armTick()),
        bulkAsk: (kind, ids) => act(f => f.bulkAsk(kind, ids)),
        bulkCancel: () => act(f => f.bulkCancel()),
        bulkDone: ids => act(f => f.bulkDone(ids)),
        bulkSendBack: (ids, note, actions) => act(f => f.bulkSendBack(ids, note, actions)),
        toggleSidebar: () => act(f => f.toggleSidebar()),
        goTo: pane => act(f => f.goTo(pane)),
      },
      e.props.bodyColumns,
    )
    const k = await kitOf(ui, rrOf($), e.surface, text => mod.host?.toast(text))
    // The frame paints its own page ground; the column only makes the tree one element for the sidebar overlay.
    const { Box } = ui
    return withSidebar(
      k,
      <Box flexDirection="column" flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'review'),
      pane => act(f => f.goTo(pane)),
    )
  })

  on('ui.render', { component: 'Pane', requestId: 'tm-handovers' }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the handovers in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    const { snapshot, summaries } = await dataOf($)
    const now = await $.clock.now()
    const stored = await read($, HANDOVER_LIST)
    const views = await read($, PHASE_VIEWS)
    const demo = mod.source === 'demo'
    // Demo draws its list and phases before the seed lands, as dataOf draws its snapshot (a render writes nothing).
    const view: HandoversView = {
      snapshot,
      list: demo && stored === null ? demoHandoverList(now) : stored,
      marks: await read($, HANDOVER_MARKS),
      phaseView: demo && !views.handovers.loaded ? { ...views.handovers, phases: demoPhases('handovers'), loaded: true } : views.handovers,
      pick: await read($, PICK),
      page: await read($, HANDOVER_PAGE),
      summaries,
      summaryOpen: await read($, SUMMARY_OPEN),
      now,
      focused: e.props.isFocused,
      connecting: mod.source === 'tm' && (await read($, FAULT)) === 'connecting',
      demo,
      tabs: await tabsFor($, 'handovers'),
    }
    // The open summary is read again when a refresh marked it stale (the toggle reads it the first time); the stale one
    // stays drawn meanwhile.
    if (mod.source === 'tm' && snapshot !== null && snapshot.reachable && view.summaryOpen !== '') {
      const shown = phaseHandovers(listedHandovers(view.list, snapshot, view.marks.showClosed), view.phaseView.choice, view.list?.tasks ?? {})
      const picked = handoverPage(shown, view.page, view.pick).picked
      const held = picked === undefined ? undefined : summaries[picked.id]
      if (picked !== undefined && picked.id === view.summaryOpen && (held === undefined || held.stale === true)) askSummary(picked.id)
    }
    const ui = $.ui.resolve(e) as unknown as Ui
    const k = await kitOf(ui, rrOf($), e.surface, text => mod.host?.toast(text))
    const tree = handoversPaneTree(
      k,
      view,
      {
        pick: id => act(f => f.pick(id)),
        move: (page, id) => act(f => f.moveTo(page, id)),
        turnPage: (page, firstId) => act(f => f.turnPage(page, firstId)),
        select: id => act(f => f.selectHandover(id)),
        selectOnly: ids => act(f => f.selectHandovers(ids)),
        toggleSummary: (handover, open) => act(f => f.toggleSummary(handover, open)),
        resume: handover =>
          act(async f => {
            if (await f.resumeHandover(handover)) await f.recordResumed(handover.id)
          }),
        // Stage 2's launch only names what it would open in demo.
        newTab: (handover, surface) =>
          act(async f => {
            await f.launch([await f.handoverLaunch(handover)], surface)
          }),
        copy: (handover, surface) => act(f => f.copyHandover(handover.id, surface)),
        askClose: ask => act(f => f.askClose(ask)),
        confirmClose: () => act(f => f.confirmClose()),
        cancelClose: () => act(f => f.cancelClose()),
        toggleClosed: () => act(f => f.toggleClosed()),
        // u: Stage 3's refresh (snapshot and this pane's phases), then the pane's own list.
        refresh: () =>
          act(async f => {
            await f.refresh('handovers')
            await f.loadHandovers()
          }),
        strip: {
          choose: id => act(f => f.choosePhase('handovers', id)),
          toggleDone: () => act(f => f.toggleDone('handovers')),
          overlay: open => act(f => f.setOverlay('handovers', open)),
        },
        toggleSidebar: () => act(f => f.toggleSidebar()),
        goTo: pane => act(f => f.goTo(pane)),
      },
      e.props.bodyColumns,
    )
    // The frame paints its own page ground; the column only makes the tree one element for the sidebar overlay.
    return withSidebar(
      k,
      <Box flexDirection="column" flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'handovers'),
      pane => act(f => f.goTo(pane)),
    )
  })

  on('ui.render', { component: 'Pane', requestId: SESSION }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the Session tab in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    const { snapshot, summaries } = await dataOf($)
    const handover = withNotice(await read($, SESSION_HANDOVER), await read($, NOTICE), snapshot)
    const open = handover !== null && (await read($, SUMMARY_OPEN)) === handover.id
    const held = handover === null ? undefined : summaries[handover.id]
    // Read when open and missing, or stale after a refresh (the stale one stays drawn meanwhile), as the handovers pane does.
    if (mod.source === 'tm' && handover !== null && open && (held === undefined || held.stale === true)) askSummary(handover.id)
    const cwd = await $.session.cwd()
    const ui = $.ui.resolve(e) as unknown as Ui
    const k = await kitOf(ui, rrOf($), e.surface, text => mod.host?.toast(text))
    const tree = sessionTree(
      k,
      {
        snapshot,
        handover,
        resumedFrom: await read($, RESUMED_FROM),
        burst: await read($, SESSION_BURST),
        band: await read($, BAND),
        summary: held,
        summaryOpen: open,
        connecting: mod.source === 'tm' && (await read($, FAULT)) === 'connecting',
        demo: mod.source === 'demo',
        now: await $.clock.now(),
        info: await read($, SESSION_INFO),
        tabs: await tabsFor($, 'session'),
      },
      {
        goTo: pane => act(f => f.goTo(pane)),
        refresh: () => act(f => f.retry()),
        toggleSidebar: () => act(f => f.toggleSidebar()),
        askDone: id => act(f => f.bandAskDone(id)),
        confirmDone: id => act(f => f.bandConfirmDone(id)),
        cancel: () => act(f => f.bandCancel()),
        sendBack: id => act(f => f.bandSendBack(id)),
        copy: (id, surface) => act(f => f.copyHandover(id, surface)),
        openHandover: id => act(f => f.openHandover(id)),
        launch: (h, surface) =>
          act(async f => {
            await f.launch([{ cwd, title: `handover ${h.id}`, mode: 'fresh', prompt: resumePrompt(h.id, h.path) }], surface)
          }),
        toggleSummary: (id, show) => act(f => f.toggleSummary({ id }, show)),
      },
      e.props.bodyColumns,
    )
    const ground = await $.rr.surfaceProps({ level: 'page' })
    const { Box } = ui
    return withSidebar(
      k,
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'session'),
      pane => act(f => f.goTo(pane)),
    )
  })

  on('ui.render', { component: 'Pane', requestId: KIT }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the kit gallery in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    const ui = $.ui.resolve(e) as unknown as Ui
    const say = (text: string) => mod.host?.toast(text)
    const k = await kitOf(ui, rrOf($), e.surface, say)
    const views = await read($, PHASE_VIEWS)
    const view = views.review.loaded ? views.review : { ...views.review, phases: demoPhases('review'), loaded: true }
    const strip = { pane: 'review' as const, view, allCount: 5, focused: e.props.isFocused, digits: true, empty: 'Nothing waiting' }
    const tree = kitGalleryTree(
      k,
      strip,
      {
        choose: id => act(f => f.choosePhase('review', id)),
        toggleDone: () => act(f => f.toggleDone('review')),
        overlay: open => act(f => f.setOverlay('review', open)),
      },
      id => say(`pressed ${id}`),
      e.props.bodyColumns,
    )
    const ground = await $.rr.surfaceProps({ level: 'page' })
    const { Box } = ui
    return (
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>
    )
  })

  // The Tab ring landing on a card's box focuses that card (it expands); a confirm or refusal on the card left behind goes.
  on('ui.focus', { requestId: 'tm-review' }, async ($, e, next) => {
    const moved = await next(e)
    const element = e.element
    if (element !== undefined && element.startsWith('sel:')) {
      const id = element.slice(4)
      if ((await read($, CURSOR)).currentId !== id) {
        await update($, CURSOR, c => ({ ...c, currentId: id, mode: 'card' as const, refusal: '' }))
        await update($, REVIEW_LIST, l => (l.ticking ? { ...l, ticking: false } : l))
      }
    }
    return moved
  }).catch(($, e, next) => next(e)) // a failed focus write never holds the move up: replay what the chain beneath settled

  on('ui.focus', { requestId: 'tm-handovers' }, async ($, e, next) => {
    const moved = await next(e)
    // A card's row or its select box takes the focus: that card is the picked one.
    const id = e.element?.match(/^ho(?:-sel)?:(.+)$/)?.[1]
    if (id !== undefined) {
      await update($, PICK, () => id)
      await update($, SUMMARY_OPEN, open => (open === id ? open : '')) // another row shows collapsed, and so does this one later
    }
    return moved
  }).catch(($, e, next) => next(e)) // a failed pick write never holds the focus move up: replay what the chain beneath settled

  // Esc closes the innermost layer first (spec §15.8 S4): the person's close of a Taskmaster pane is refused while its sidebar
  // or phase list is open, and closes that instead. Every close that goes through lets the sidebar go with its pane.
  on('ui.close', async ($, e, next) => {
    const pane = paneOf(e.id)
    if (pane === null) return next(e)
    if (e.origin.kind === 'person' && SIDEBAR_ESC === 'refuse') {
      const nav = await read($, NAV)
      const views = await read($, PHASE_VIEWS)
      const menuOpen = pane !== 'session' && views[pane].overlay
      const layer = escLayer(nav, menuOpen)
      if (layer === 'sidebar') {
        await update($, NAV, n => ({ ...n, sidebar: false }))
        return { value: undefined }
      }
      if (layer === 'menu' && pane !== 'session') {
        await update($, PHASE_VIEWS, all => ({ ...all, [pane]: { ...all[pane], overlay: false } }))
        return { value: undefined }
      }
    }
    const closed = await next(e)
    await update($, NAV, n => (n.sidebar ? { ...n, sidebar: false } : n))
    return closed
  }).catch(($, e, next) => next(e)) // a failed read or write never keeps a pane open: replay what the chain beneath settled
}
