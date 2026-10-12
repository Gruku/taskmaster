// User intent: the restore pane's own hooks (spec §10) — /tm-restore, its pane, its keys, and the quiet session-start scan
// that tells the sidebar a burst of sessions closed unfinished — kept out of register.tsx, which lends only its flows (never `$`).
// JSX compiles to h(...): never name a variable `h` in this file.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On, PluginOptions } from 'claude-code'

import type { TmNav, TmPhasePane, TmPhaseView, TmRestorePending, TmSessionBurst, TmSnapshot } from '../../types'
import { demoPhases, demoRestore } from '../demo'
import type { TmFlows } from '../flows'
import { kitOf, type KitRr, outdatedNotice, type Ui } from '../kit'
import { platformOf } from '../launch'
import { RESTORE, restoreHiddenKey } from '../model'
import { FRESH_NAV, shownRestore, shownSnapshot, type SidebarView, sidebarView } from '../nav'
import { replyText } from '../parse'
import { FRESH_PHASE_VIEWS, withCounts } from '../phases'
import { withSidebar } from '../vocab'
import { restorePaneTree } from './draw'
import { createRestoreFlows, type RestoreDeps, type RestoreFlows } from './flows'
import { DEFAULT_DAYS, FRESH_RESTORE_DATA, FRESH_RESTORE_VIEW, paneRows, phaseCounts } from './model'
import type { RestoreHost } from './scan'

/** userConfig `briefModel`'s default (spec §10.6). */
export const BRIEF_MODEL = 'claude-haiku-5-5'

const DATA = atom({ plugin: 'taskmaster-mods', key: 'restoreData' } as const, FRESH_RESTORE_DATA)
const VIEW = atom({ plugin: 'taskmaster-mods', key: 'restoreView' } as const, FRESH_RESTORE_VIEW)
// Written here, read by the sidebar and the Session tab (Stage 2 declares both keys).
const PENDING = atom({ plugin: 'taskmaster-mods', key: 'restorePending' } as const, null as TmRestorePending | null)
const BURST = atom({ plugin: 'taskmaster-mods', key: 'sessionBurst' } as const, null as TmSessionBurst | null)
const PHASE_VIEWS = atom({ plugin: 'taskmaster-mods', key: 'phaseViews' } as const, FRESH_PHASE_VIEWS as Readonly<Record<TmPhasePane, TmPhaseView>>)
const RR_POLARITY = { plugin: 'rr-tui', key: 'polarity' } as const
// Read only, for the tab row and the sidebar (register.tsx writes them).
const NAV = atom({ plugin: 'taskmaster-mods', key: 'nav' } as const, FRESH_NAV as TmNav)
const SNAPSHOT = atom({ plugin: 'taskmaster-mods', key: 'snapshot' } as const, null as TmSnapshot | null)
const HIDDEN = atom({ plugin: 'taskmaster-mods', key: 'restoreHidden' } as const, null as number | null)

/** What register.tsx lends: its flows once an acting hook built them (Stage 2's launch, the strip's phase flows). */
export type RestoreLink = { readonly flows: () => TmFlows | null }

// Like register.tsx's `mod`: rebuilt by each register(), the flows built by the first restore hook that acts.
const rs: { source: 'tm' | 'demo'; briefModel: string; link: RestoreLink | null; flows: RestoreFlows | null } = {
  source: 'tm',
  briefModel: BRIEF_MODEL,
  link: null,
  flows: null,
}

function restoreHostOf($: EngineInterface): RestoreHost {
  return {
    run: async (argv, timeoutMs) => {
      const r = await $.process.run(argv, { timeoutMs })
      return { exitCode: r.exitCode, stdout: r.stdout, stderr: r.stderr }
    },
    paths: async () => {
      const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '.'
      const configDir = (await $.env.get('CLAUDE_CONFIG_DIR')) || `${home}/.claude`
      const temp = (await $.env.get('TEMP')) ?? (await $.env.get('TMPDIR')) ?? configDir
      return { configDir, cache: `${temp}/tm-restore-scan-cache.json`, script: `${$.plugin.root}/scanner/scan.py` }
    },
  }
}

function depsOf($: EngineInterface): RestoreDeps {
  return {
    source: rs.source,
    rh: restoreHostOf($),
    tm: {
      call: async (tool, args) => {
        const r = await $.mcp.call('plugin:taskmaster:tm', tool, args)
        const text = r.content.map(block => (block.type === 'text' ? String((block as { text?: unknown }).text ?? '') : '')).join('\n')
        return { text: replyText(text, r.structuredContent), isError: r.isError }
      },
      sleep: (ms, signal) => $.clock.sleep(ms, signal ? { signal } : undefined),
      log: text => $.ui.log(text, { to: 'debug' }),
    },
    store: { get: key => $.store.get(key), set: (key, value) => $.store.set(key, value), delete: key => $.store.delete(key) },
    read: { data: () => read($, DATA), view: () => read($, VIEW) },
    write: {
      data: async change => {
        await update($, DATA, change)
      },
      view: async change => {
        await update($, VIEW, change)
      },
      signals: async (pending, burst) => {
        await update($, PENDING, () => pending)
        await update($, BURST, () => burst)
      },
    },
    now: () => $.clock.now(),
    wait: ms => $.clock.sleep(ms),
    sessionId: () => $.session.id(),
    repoRoot: async () => (await $.session.repo())?.root ?? (await $.session.root()),
    toast: text => $.ui.toast(text),
    // Through the main flows when they exist: the other Taskmaster panes close first and Restore becomes the band's last pane.
    openPane: async () => {
      const main = rs.link?.flows() ?? null
      if (main === null) return (await $.ui.open({ id: RESTORE, title: 'Restore', focus: true, closeOnEscape: true })).isPlaced
      await main.openPane('restore')
      return true
    },
    loadPhases: () => {
      const main = rs.link?.flows() ?? null
      if (main !== null) void main.loadPhases('restore').catch(() => undefined)
    },
    platform: async () =>
      platformOf({ os: await $.env.get('OS') }, async argv => {
        try {
          const r = await $.process.run(argv, { timeoutMs: 3000 })
          return { exitCode: r.exitCode, stdout: r.stdout }
        } catch {
          return null
        }
      }),
    launch: async specs => {
      const main = rs.link?.flows() ?? null
      if (main === null) {
        $.ui.toast('taskmaster-mods: still starting; press resume again')
        return { opened: 0, copied: null, failed: specs.map(s => s.title) }
      }
      return main.launch(specs)
    },
    copy: async text => (await $.ui.copy({ text })).isCopied,
    complete: async prompt => {
      const r = await $.model.complete({ model: rs.briefModel, prompt, maxTokens: 400, timeoutMs: 90_000 })
      return r.isAnswered ? { ok: true, text: r.text } : { ok: false, reason: r.reason }
    },
  }
}

function flowsOf($: EngineInterface): RestoreFlows {
  rs.flows ??= createRestoreFlows(depsOf($))
  return rs.flows
}

function kitRrOf($: EngineInterface): KitRr {
  return { tokens: () => $.rr.tokens(), badgeProps: a => $.rr.badgeProps(a) }
}

/** False when rr-tui lacks badgeProps or tint (before 0.2.0): the call throws, and the render says so instead of drawing blank. */
async function rrReady($: EngineInterface): Promise<boolean> {
  try {
    await $.rr.badgeProps({ tone: 'info', strength: 24, on: 'page' })
    await $.rr.tint({ fg: '#000000', bg: '#000000', alpha: 0 })
    return true
  } catch {
    return false
  }
}

function sayOf($: EngineInterface): (text: string) => void {
  return text => $.ui.toast(text)
}

/** The tab row's entries, by the same rules register.tsx's tabsFor uses for the other panes (nav.ts's shown* helpers). */
async function restoreTabsOf($: EngineInterface, now: number): Promise<SidebarView> {
  const snapshot = shownSnapshot(await read($, SNAPSHOT), rs.source, now)
  const pending = await read($, PENDING)
  const here = await read($, HIDDEN)
  const stored = pending !== null && here !== pending.epoch && (await $.store.get(restoreHiddenKey(pending.epoch))) === true
  return sidebarView({ current: 'restore', snapshot, restore: shownRestore(pending, here, stored), now })
}

// Press handlers run after the ui.press hook below built the flows; they read rs.flows then.
const act = (run: (flows: RestoreFlows) => Promise<void>): void => {
  if (rs.flows !== null) void run(rs.flows)
}
const actMain = (run: (flows: TmFlows) => Promise<void>): void => {
  const main = rs.link?.flows() ?? null
  if (main !== null) void run(main)
}

/** `u` on the restore pane (TmFlows.refresh('restore')): rescans the pane's window; a no-op before the flows exist. */
export async function refreshRestore(): Promise<void> {
  if (rs.flows !== null) await rs.flows.rescan()
}

export function onRestore(on: On, options: PluginOptions, link: RestoreLink): void {
  rs.source = options.source === 'demo' ? 'demo' : 'tm'
  rs.briefModel = typeof options.briefModel === 'string' && options.briefModel.trim() !== '' ? options.briefModel.trim() : BRIEF_MODEL
  rs.link = link
  rs.flows = null

  // An interactive start only (a `-p` run restores nothing): the command, then a quiet 3-day scan off the start (§10.4).
  on('session.start', { isInteractive: true }, async ($, e, next) => {
    const started = await next(e)
    try {
      await $.command.register({ name: RESTORE, description: 'Sessions closed together last time, unfinished first: resume them', argumentHint: '[days]' })
    } catch {
      // already registered by an earlier load of this module: commands persist across reloads
    }
    try {
      const flows = flowsOf($)
      $.clock.after(0, () => {
        void flows.startScan().catch(() => undefined)
      })
    } catch {
      // the start never fails over the quiet scan: the pane's own command scans again
    }
    return started
  })

  on('command.run', { command: RESTORE }, async ($, e) => {
    const days = Number.parseFloat(e.args.trim())
    return { text: await flowsOf($).open(Number.isFinite(days) && days > 0 ? days : DEFAULT_DAYS) }
  })

  on('ui.press', { requestId: RESTORE }, async ($, e, next) => {
    flowsOf($)
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: RESTORE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text>Open the restore pane in the terminal or the desktop app.</Text>
    await $.state.get(RR_POLARITY)
    if (!(await rrReady($))) return outdatedNotice($.ui.resolve(e))
    const now = await $.clock.now()
    const stored = await read($, DATA)
    // Demo draws its sessions before the first scan lands, as the other panes draw their seed (a render writes nothing).
    const data = rs.source === 'demo' && stored.scan === null ? { ...stored, ...demoRestore(now) } : stored
    const rows = paneRows(data, await $.session.id())
    const views = await read($, PHASE_VIEWS)
    const phases = rs.source === 'demo' && !views.restore.loaded ? demoPhases('restore') : views.restore.phases
    const strip = {
      pane: 'restore' as const,
      view: { ...views.restore, phases: withCounts(phases, phaseCounts(rows)) },
      allCount: rows.filter(r => !r.s.running).length,
      focused: e.props.isFocused,
      digits: true,
      empty: 'No sessions',
    }
    const tabs = await restoreTabsOf($, now)
    const k = await kitOf($.ui.resolve(e) as unknown as Ui, kitRrOf($), e.surface, sayOf($))
    const tree = restorePaneTree(
      k,
      { data, view: await read($, VIEW), rows, strip, now: now / 1000, source: rs.source, tabs },
      {
        strip: {
          // A new phase starts at its page 1, as review and handovers do.
          choose: id => {
            actMain(f => f.choosePhase('restore', id))
            act(f => f.turnPage(0))
          },
          toggleDone: () => actMain(f => f.toggleDone('restore')),
          overlay: open => actMain(f => f.setOverlay('restore', open)),
        },
        turnPage: page => act(f => f.turnPage(page)),
        rescan: () => actMain(f => f.refresh('restore')),
        toggleCompact: () => act(f => f.toggleCompact()),
        focus: id => act(f => f.focus(id)),
        toggle: id => act(f => f.toggle(id)),
        select: changes => act(f => f.select(changes)),
        toggleMode: id => act(f => f.toggleMode(id)),
        resume: ids => act(f => f.resume(ids)),
        toggleBrief: id => act(f => f.toggleBrief(id)),
        brief: ids => act(f => f.brief(ids)),
        copy: id => act(f => f.copy(id)),
        toggleSidebar: () => actMain(f => f.toggleSidebar()),
        goTo: pane => actMain(f => f.goTo(pane)),
      },
      e.props.bodyColumns,
    )
    const sidebar = (await read($, NAV)).sidebar ? tabs : null
    // The frame paints its own page ground; the column only makes the tree one element for the sidebar overlay.
    return withSidebar(
      k,
      <Box flexDirection="column" flexGrow={1}>
        {tree}
      </Box>,
      sidebar,
      pane => actMain(f => f.goTo(pane)),
    )
  })

  // Tab onto a card's box focuses that card (opens it), as j / k do.
  on('ui.focus', { requestId: RESTORE }, async ($, e, next) => {
    const moved = await next(e)
    const element = e.element
    if (element !== undefined && element.startsWith('sel:')) {
      const id = element.slice(4)
      await update($, VIEW, v => (v.focus === id ? v : { ...v, focus: id }))
    }
    return moved
  }).catch(($, e, next) => next(e))
}
