// User intent: the phase strip's behaviour as plain functions shared by review, handovers and restore (spec §5) — which tabs,
// in what order, what fits, where ‹ › and the digits land, what a remembered choice falls back to and where it is kept — so
// the strip moves both ways and is tested without drawing.
import type { TmPhase, TmPhasePane, TmPhaseView } from '../types'
import { phaseName } from './model'

/** The All tab's id (and the stored "every phase" choice). */
export const ALL = ''
/** The trailing "no task" tab's id (handovers, restore): what links no task. */
export const NO_TASK = '~none'
/** `◐ done` is off by default once more done phases than this exist. */
export const DONE_HIDE_OVER = 4
export const PHASE_PANES: readonly TmPhasePane[] = ['review', 'handovers', 'restore']
export const FRESH_PHASE_VIEW: TmPhaseView = { choice: ALL, phases: [], loaded: false, showDone: null, overlay: false }
export const FRESH_PHASE_VIEWS: Readonly<Record<TmPhasePane, TmPhaseView>> = {
  review: FRESH_PHASE_VIEW,
  handovers: FRESH_PHASE_VIEW,
  restore: FRESH_PHASE_VIEW,
}

export type PhaseTabKind = 'all' | 'active' | 'planned' | 'done' | 'none'
/** One tab of the strip: `dim` for an empty or done phase (drawn dim, still selectable). */
export type PhaseTab = {
  readonly id: string
  readonly name: string
  readonly kind: PhaseTabKind
  readonly count: number
  readonly date: string
  readonly dim: boolean
}
function tabOf(p: TmPhase): PhaseTab {
  const kind: PhaseTabKind =
    p.id === NO_TASK ? 'none' : p.status === 'active' ? 'active' : p.status === 'done' || p.status === 'archived' ? 'done' : 'planned'
  return { id: p.id, name: kind === 'none' ? 'no task' : phaseName(p), kind, count: p.count, date: p.date ?? '', dim: kind === 'done' || p.count === 0 }
}

/**
 * The strip's order: All, a choice the list does not hold (its bare id, until the list says), the active phase, planned
 * phases by `order`, done phases newest first (by date, then order), then no task. Phases sharing an `order` (seen live)
 * fall back to their id so the order never depends on the server's row order.
 */
export function orderPhases(phases: readonly TmPhase[], allCount: number, choice: string = ALL): PhaseTab[] {
  const tabs = phases.map(tabOf)
  const order = new Map(phases.map(p => [p.id, p.order]))
  const ord = (tab: PhaseTab): number => order.get(tab.id) ?? 0
  const of = (kind: PhaseTabKind): PhaseTab[] => tabs.filter(tab => tab.kind === kind)
  const planned = of('planned').sort((a, b) => ord(a) - ord(b) || a.id.localeCompare(b.id))
  const done = of('done').sort((a, b) =>
    a.date === b.date ? ord(b) - ord(a) || a.id.localeCompare(b.id) : a.date < b.date ? 1 : -1,
  )
  const all: PhaseTab = { id: ALL, name: 'All', kind: 'all', count: allCount, date: '', dim: false }
  const unknown: PhaseTab[] =
    choice === ALL || tabs.some(tab => tab.id === choice)
      ? []
      : [{ id: choice, name: choice === NO_TASK ? 'no task' : choice, kind: choice === NO_TASK ? 'none' : 'planned', count: 0, date: '', dim: true }]
  return [all, ...unknown, ...of('active'), ...planned, ...done, ...of('none')]
}

/** `◐ done`: the person's toggle, else on unless more than DONE_HIDE_OVER done phases exist. */
export function isDoneShown(tabs: readonly PhaseTab[], toggle: boolean | null): boolean {
  return toggle ?? tabs.filter(tab => tab.kind === 'done').length <= DONE_HIDE_OVER
}

/** The tabs ‹ › walk: every tab, less the done ones while `◐ done` is off; the chosen tab always stays. */
export function shownTabs(tabs: readonly PhaseTab[], choice: string, showDone: boolean): PhaseTab[] {
  return tabs.filter(tab => showDone || tab.kind !== 'done' || tab.id === choice)
}

/** ‹ (-1) or › (+1) from the chosen tab over the shown tabs; no wrap (the end stays put); an unknown choice steps to All. */
export function step(shown: readonly PhaseTab[], choice: string, dir: -1 | 1): string {
  const at = shown.findIndex(tab => tab.id === choice)
  if (at < 0) return ALL
  return shown[at + dir]?.id ?? choice
}

/** True when ‹ or › has nowhere to go (its keycap dims). */
export function atEnd(shown: readonly PhaseTab[], choice: string, dir: -1 | 1): boolean {
  const at = shown.findIndex(tab => tab.id === choice)
  return at < 0 ? dir === -1 : shown[at + dir] === undefined
}

/** A remembered choice the read list no longer holds falls back to All; `lost` names it ('' when kept). */
export function fallback(view: TmPhaseView): { readonly choice: string; readonly lost: string } {
  if (!view.loaded || view.choice === ALL || view.phases.some(p => p.id === view.choice)) return { choice: view.choice, lost: '' }
  return { choice: ALL, lost: view.choice }
}

/** An empty selection's line: `Nothing waiting in Patch 1.8 · done 2026-10-05`; just `<empty>` under All. */
export function emptyLine(empty: string, tab: PhaseTab | undefined): string {
  if (tab === undefined || tab.kind === 'all') return empty
  return `${empty} in ${tab.name}${tab.kind === 'done' && tab.date !== '' ? ` · done ${tab.date}` : ''}`
}

/** Restore's phases: the list's phases with the pane's own counts (absent: 0), and a no task tab when the counts name one. */
export function withCounts(phases: readonly TmPhase[], counts: ReadonlyMap<string, number>): TmPhase[] {
  const counted = phases.filter(p => p.id !== NO_TASK).map(p => ({ ...p, count: counts.get(p.id) ?? 0 }))
  const none = counts.get(NO_TASK)
  return none === undefined ? counted : [...counted, { id: NO_TASK, status: 'none', order: 0, count: none, name: 'no task' }]
}

/** Handovers with `◐ closed` on: each tab counts its open and closed handovers. */
export function withClosed(phases: readonly TmPhase[]): TmPhase[] {
  return phases.map(p => ({ ...p, count: p.count + (p.closed ?? 0) }))
}

// ── Where a choice is kept: `$.store` `phase:<pane>:<repo root>`, machine-wide, one per pane and project ────────────────

export const phaseKey = (pane: TmPhasePane, root: string): string => `phase:${pane}:${root}`
/** Before the strip, review kept its filter at `phase:<repo root>`; the first read moves it. */
export const legacyPhaseKey = (root: string): string => `phase:${root}`

/** A stored choice: its id ('' All, kept as an explicit choice), or null for none / unreadable. */
export function storedPhase(value: unknown): string | null {
  const phase = typeof value === 'object' && value !== null ? (value as { phase?: unknown }).phase : undefined
  return typeof phase === 'string' ? phase : null
}

export type PhaseStore = {
  readonly get: (key: string) => Promise<unknown>
  readonly set: (key: string, value: unknown) => Promise<void>
  readonly delete: (key: string) => Promise<void>
}

export const phaseStoreOf = (host: {
  readonly storeGet: (key: string) => Promise<unknown>
  readonly storeSet: (key: string, value: unknown) => Promise<void>
  readonly storeDelete: (key: string) => Promise<void>
}): PhaseStore => ({ get: key => host.storeGet(key), set: (key, value) => host.storeSet(key, value), delete: key => host.storeDelete(key) })

/** A pane's remembered choice, or null; review's legacy key moves to `phase:review:<root>` on this first read. */
export async function loadChoice(store: PhaseStore, pane: TmPhasePane, root: string): Promise<string | null> {
  const stored = storedPhase(await store.get(phaseKey(pane, root)))
  if (stored !== null || pane !== 'review') return stored
  const legacy = storedPhase(await store.get(legacyPhaseKey(root)))
  if (legacy === null) return null
  await store.set(phaseKey('review', root), { phase: legacy })
  await store.delete(legacyPhaseKey(root))
  return legacy
}

export async function saveChoice(store: PhaseStore, pane: TmPhasePane, root: string, choice: string): Promise<void> {
  await store.set(phaseKey(pane, root), { phase: choice })
}

/** The picker's and the dropdown's name for a tab (spec §15.4): `All`, `Patch 1.9 · active`, `Patch 1.8 ✓`, `no task`. */
export function pickerName(tab: PhaseTab): string {
  if (tab.kind === 'all') return 'All'
  if (tab.kind === 'active') return `${tab.name} · active`
  if (tab.kind === 'done') return `${tab.name} ✓`
  return tab.name
}
