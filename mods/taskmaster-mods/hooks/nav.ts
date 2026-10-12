// User intent: the Taskmaster sidebar's navigation model (spec §15.8) — which pane an id is, what each sidebar entry counts,
// which layer Esc closes first — as plain functions, so the band, every pane and the restore module agree without drawing.
import type { TmNav, TmRestorePending, TmSnapshot } from '../types'
import type { TmPane } from './flows'
import { ageLabel, HANDOVERS, RESTORE, REVIEW, SESSION } from './model'
import { reviewKind } from './review-list'

export const FRESH_NAV: TmNav = { sidebar: false, last: 'session' }
/** False until the restore pane ships (Task E7 flips it): its sidebar entry draws dim and says so. */
export const RESTORE_BUILT = true
export const PANE_IDS: Readonly<Record<TmPane, string>> = { review: REVIEW, handovers: HANDOVERS, session: SESSION, restore: RESTORE }
export const PANE_TITLES: Readonly<Record<TmPane, string>> = { review: 'Review', handovers: 'Handovers', session: 'Session', restore: 'Restore' }
const PANES: readonly TmPane[] = ['review', 'handovers', 'session', 'restore']
export const TM_PANE_IDS: readonly string[] = PANES.map(pane => PANE_IDS[pane])

/** The Taskmaster pane a requestId is, or null (the kit gallery, another plugin's). */
export function paneOf(requestId: string): TmPane | null {
  return PANES.find(pane => PANE_IDS[pane] === requestId) ?? null
}

/** One sidebar row: its digit, name, an optional note and a count (both may be ''), and why it is dim (absent: live). */
export type SidebarEntry = {
  readonly pane: TmPane
  readonly key: string
  readonly name: string
  readonly count: string
  readonly note: string
  readonly dim?: string
}
export type SidebarView = { readonly current: TmPane; readonly entries: readonly SidebarEntry[]; readonly context: string }

/** `16:14`: an epoch in seconds as local hours and minutes. */
export function hhmm(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** What the sidebar lists (spec §15.8 S3): Review's waiting total and `▲ blocked ◆ action`, Handovers' open total and newest age, Restore's burst. */
export function sidebarView(a: {
  readonly current: TmPane
  readonly snapshot: TmSnapshot | null
  readonly restore: TmRestorePending | null
  readonly now: number
}): SidebarView {
  const s = a.snapshot
  const live = s !== null && s.reachable
  const kinds = { blocked: 0, action: 0 }
  for (const item of live ? s.queue : []) {
    const kind = reviewKind(item)
    if (kind === 'blocked' || kind === 'action') kinds[kind] += 1
  }
  const newest = live ? ageLabel(s.handovers[0]?.created ?? '', a.now) : ''
  const restore: SidebarEntry = RESTORE_BUILT
    ? { pane: 'restore', key: '4', name: 'Restore', count: a.restore === null ? '' : String(a.restore.unfinished), note: a.restore === null ? '' : `burst ${hhmm(a.restore.epoch)}` }
    : { pane: 'restore', key: '4', name: 'Restore', count: '', note: '', dim: 'Restore is not built yet' }
  const bound = s?.bound ?? null
  const context = s === null ? 'no task bound' : !s.reachable ? 'tm offline' : bound === null ? 'no task bound' : [bound.taskId, (bound.detail?.status ?? '').replace(/-/g, ' ')].filter(Boolean).join(' · ')
  return {
    current: a.current,
    context,
    entries: [
      {
        pane: 'review',
        key: '1',
        name: 'Review',
        count: live ? `${s.queueTotal}${s.queueCapped === true ? '+' : ''}` : '–',
        note: [kinds.blocked > 0 ? `▲${kinds.blocked}` : '', kinds.action > 0 ? `◆${kinds.action}` : ''].filter(Boolean).join(' '),
      },
      { pane: 'handovers', key: '2', name: 'Handovers', count: live ? String(s.handoversTotal) : '–', note: newest === '' ? '' : `newest ${newest}` },
      { pane: 'session', key: '3', name: 'Session', count: '', note: '' },
      restore,
    ],
  }
}

export type EscLayer = 'sidebar' | 'menu' | 'pane'

/** What the person's Esc closes (spec §15.8 S4): the sidebar, else the open phase list, else the pane itself. */
export function escLayer(nav: TmNav, menuOpen: boolean): EscLayer {
  if (nav.sidebar) return 'sidebar'
  return menuOpen ? 'menu' : 'pane'
}
