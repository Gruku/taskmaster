// User intent: what the handovers pane lists and where its keys land (spec §9) — which handovers a phase holds through their
// linked tasks, the `◐ closed` toggle, j / k across pages, where a handover opened from the band or Session tab sits, and
// the one toast a close leaves — as plain functions, tested without drawing.
import type { TmHandover, TmHandoverList, TmHandoverMarks, TmLinkedTask, TmPhase, TmSnapshot } from '../types'
import { ageLabel, handoverPage, PAGE_SIZE } from './model'
import { ALL, NO_TASK } from './phases'

export const FRESH_MARKS: TmHandoverMarks = { selected: [], ask: null, showClosed: false }

type Tasks = Readonly<Record<string, TmLinkedTask>>
const taskIdsOf = (h: TmHandover): readonly string[] => (Array.isArray(h.taskIds) ? h.taskIds : [])
export const isClosed = (h: TmHandover): boolean => h.status === 'closed'

/**
 * The phases a handover counts under, as the strip's query counts it: each linked task's phase (once each); NO_TASK when it
 * links no task. A linked task not read (or with no phase) adds nothing, so such a handover shows under All only.
 */
export function phasesOf(h: TmHandover, tasks: Tasks): string[] {
  const ids = taskIdsOf(h)
  if (ids.length === 0) return [NO_TASK]
  return [...new Set(ids.map(id => tasks[id]?.phase ?? '').filter(phase => phase !== ''))]
}

/** What the pane lists before the phase: its own read, else the snapshot's window; closed ones only while `◐ closed` is on. */
export function listedHandovers(list: TmHandoverList | null, snapshot: TmSnapshot | null, showClosed: boolean): readonly TmHandover[] {
  const all = list?.handovers ?? snapshot?.handovers ?? []
  return showClosed ? all : all.filter(h => !isClosed(h))
}

/** The handovers under the chosen phase ('' All: every one listed). */
export function phaseHandovers(listed: readonly TmHandover[], choice: string, tasks: Tasks): readonly TmHandover[] {
  return choice === ALL ? listed : listed.filter(h => phasesOf(h, tasks).includes(choice))
}

/** The strip's All count: the server's open total (the read's, else the snapshot's), plus the closed ones read while shown. */
export function allCount(list: TmHandoverList | null, snapshot: TmSnapshot | null, showClosed: boolean): number {
  const open = list?.total ?? snapshot?.handoversTotal ?? 0
  return open + (showClosed && list !== null ? list.handovers.filter(isClosed).length : 0)
}

/** The line under the strip: `9 open · newest 3h ago`, with ` · 2 closed` while closed ones are listed; '' when empty. */
export function tallyLine(shown: readonly TmHandover[], now: number): string {
  if (shown.length === 0) return ''
  const closed = shown.filter(isClosed).length
  const age = ageLabel(shown[0]?.created ?? '', now)
  return [`${shown.length - closed} open`, ...(closed > 0 ? [`${closed} closed`] : []), ...(age === '' ? [] : [`newest ${age} ago`])].join(' · ')
}

/** j (+1) / k (-1): the next or previous handover from the picked one, across pages; null at either end. */
export function moveTarget(shown: readonly TmHandover[], page: number, pick: string, dir: -1 | 1): { readonly page: number; readonly id: string } | null {
  const picked = handoverPage(shown, page, pick).picked
  const at = picked === undefined ? -1 : shown.indexOf(picked)
  const to = shown[at + dir]
  if (at < 0 || to === undefined) return null
  return { page: Math.floor((at + dir) / PAGE_SIZE), id: to.id }
}

/**
 * Where a handover opened from the band or the Session tab shows: the chosen phase when it already holds it, else the first
 * of its phases the strip knows (no task included), else All; and its page there. Null when the list does not hold it.
 */
export function arrival(
  listed: readonly TmHandover[],
  id: string,
  tasks: Tasks,
  phases: readonly TmPhase[],
  choice: string,
): { readonly choice: string; readonly page: number } | null {
  const h = listed.find(x => x.id === id)
  if (h === undefined) return null
  const own = phasesOf(h, tasks)
  const known = own.filter(p => phases.some(x => x.id === p))
  const chosen = choice === ALL || own.includes(choice) ? choice : (known[0] ?? ALL)
  const at = phaseHandovers(listed, chosen, tasks).findIndex(x => x.id === id)
  return { choice: chosen, page: Math.max(0, Math.floor(at / PAGE_SIZE)) }
}

/** Selected ids still open in what is shown: what `Close N` counts and closes. */
export function closableSelection(selected: readonly string[], shown: readonly TmHandover[]): string[] {
  return shown.filter(h => !isClosed(h) && selected.includes(h.id)).map(h => h.id)
}

/** The one toast after closes (spec §11): `2 closed`, `1 closed · 1 refused: <id> (<server wording>)`. */
export function closeToast(closed: readonly string[], refused: readonly { readonly id: string; readonly refusal: string }[]): string {
  const head = `${closed.length} closed`
  if (refused.length === 0) return head
  return `${head} · ${refused.length} refused: ${refused.map(r => `${r.id} (${r.refusal})`).join(', ')}`
}

/** After closes: the list marks them closed (shown dimmed under `◐ closed`, hidden otherwise). */
export function markClosed(list: TmHandoverList, ids: readonly string[]): TmHandoverList {
  return { ...list, total: Math.max(0, list.total - ids.length), handovers: list.handovers.map(h => (ids.includes(h.id) ? { ...h, status: 'closed' as const } : h)) }
}

/** After closes: the snapshot's open window loses them, and its total counts them out (the band's count follows). */
export function withoutHandovers(s: TmSnapshot, ids: readonly string[]): TmSnapshot {
  return { ...s, handovers: s.handovers.filter(h => !ids.includes(h.id)), handoversTotal: Math.max(0, s.handoversTotal - ids.length) }
}
