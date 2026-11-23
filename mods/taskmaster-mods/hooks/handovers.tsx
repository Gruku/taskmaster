// User intent: the handovers pane in the rework's vocabulary (spec §9, §15.6–15.7, Handovers.dc.html) — the phase picker with
// ◐ show closed, one sentence, a bulk close row, a list of 3-line items (title / slug · branch · linked tasks / next) that fills
// the pane and scrolls, the focused one boxed with its linked tasks' live status and `r resume handover here` filled, `x close handover` last.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmCloseAsk, TmHandover, TmHandoverList, TmHandoverMarks, TmHandoverSummary, TmLinkedTask, TmPhase, TmPhaseView, TmSnapshot } from '../types'
import { allCount, closableSelection, isClosed, listedHandovers, moveTarget, phaseHandovers, tallyLine } from './handover-list'
import type { Kit, StripHandlers, StripView } from './kit'
import { ageLabel, CONNECTING, dateOf, handoverFocus, oneLine, phaseName } from './model'
import { pagedBy, positionLabel, rememberScroll, type ScrollWindow, windowOf } from './scroll-list'
import type { TmPane } from './flows'
import type { SidebarView } from './nav'
import { ALL, emptyLine, orderPhases, withClosed } from './phases'
import {
  actionKey,
  actionRows,
  askRow,
  badgeSeg,
  bulkRow,
  cardInner,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  footerRows,
  frameRows,
  focusedRows,
  bulkRows,
  scrollEntries,
  scrollListing,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  type ListEntry,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  tabRow,
  phaseFooter,
  phasePicker,
  refreshPiece,
  type Seg,
  snapshotHealth,
  statusBadgeWord,
  summaryRow,
  togglePiece,
  withPhaseMenu,
} from './vocab'

export type HandoversView = {
  readonly snapshot: TmSnapshot | null
  /** The pane's own read (null: not yet; the snapshot's window is drawn meanwhile). */
  readonly list: TmHandoverList | null
  readonly marks: TmHandoverMarks
  readonly phaseView: TmPhaseView
  readonly pick: string
  /** The index of the first handover the window shows. */
  readonly first: number
  /** Rows the pane body may show (Pane `scroll.bodyRows`); absent: the whole list is drawn. */
  readonly rows?: number
  readonly summaries: Readonly<Record<string, TmHandoverSummary>>
  readonly summaryOpen: string
  readonly now: number
  /** The pane holds the keyboard. */
  readonly focused: boolean
  /** tm is being retried after a transient failure: with no good snapshot the pane says so. */
  readonly connecting?: boolean
  /** Demo data: the frame's health says demo. */
  readonly demo?: boolean
  /** The tab row's panes and counts (absent: no tab row). */
  readonly tabs?: SidebarView
}

export type HandoversHandlers = {
  readonly pick: (id: string) => void
  /** j / k, p / n: focus this handover; `first` is the list's first visible handover then. */
  readonly move: (first: number, id: string) => void
  readonly select: (id: string) => void
  /** `all` (every open handover in the list) and `none` ([]). */
  readonly selectOnly: (ids: readonly string[]) => void
  readonly toggleSummary: (handover: TmHandover, open: boolean) => void
  readonly resume: (handover: TmHandover) => void
  readonly newTab: (handover: TmHandover, surface: RenderSurface) => void
  readonly copy: (handover: TmHandover, surface: RenderSurface) => void
  readonly askClose: (ask: TmCloseAsk) => void
  readonly confirmClose: () => void
  readonly cancelClose: () => void
  readonly toggleClosed: () => void
  readonly refresh: () => void
  readonly strip: StripHandlers
  /** m: the sidebar over the pane. */
  readonly toggleSidebar: () => void
  /** A tab in the tab row: that pane opens in place of this one. */
  readonly goTo: (pane: TmPane) => void
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

const healthOf = (v: HandoversView): Health => snapshotHealth(v.snapshot, { connecting: v.connecting === true, demo: v.demo === true })

/** Line 2: `<id> · <branch> · N linked tasks` (`no linked task` when none). */
function refsSegs(k: Kit, ho: TmHandover): Seg[] {
  const ids = Array.isArray(ho.taskIds) ? ho.taskIds : []
  const branch = typeof ho.branch === 'string' ? ho.branch : ''
  return [{ text: [ho.id, branch, ids.length === 0 ? 'no linked task' : `${plural(ids.length, 'linked task')}`].filter(Boolean).join(' · '), color: k.t.fg.subtle }]
}

/** A linked task in the focused card: `task  ● IN REVIEW  <id>  <title>  <phase>`. */
function taskSegs(k: Kit, id: string, task: TmLinkedTask | undefined, phases: readonly TmPhase[]): Seg[] {
  const { t } = k
  const phase = task === undefined || task.phase === '' ? '' : phaseName(phases.find(p => p.id === task.phase) ?? { id: task.phase, status: '', order: 0, count: 0, name: '' })
  return [
    { text: 'task  ', color: t.signatureText },
    badgeSeg(k, statusBadgeWord(task?.status ?? '')),
    { text: '  ', color: t.fg.default },
    { text: id, color: t.fg.bold, bold: true },
    { text: `  ${task === undefined ? 'not read' : oneLine(task.title)}`, color: task === undefined ? t.fg.subtle : t.fg.default },
    { text: phase === '' ? '' : `  ${phase}`, color: t.fg.subtle },
  ]
}

function summaryRows(k: Kit, summary: TmHandoverSummary | undefined, width: number): RenderNode[] {
  const { t } = k
  if (summary === undefined || summary.unavailable === true) return [itemLine(k, [{ text: summary === undefined ? 'loading summary…' : 'summary unavailable', color: t.fg.subtle }], width)]
  const listOf = (items: readonly string[] | undefined): readonly string[] => (Array.isArray(items) ? items : [])
  return (
    [
      ['decisions', listOf(summary.decisions)],
      ['blockers', listOf(summary.blockers)],
    ] as const
  )
    .filter(([, items]) => items.length > 0)
    .flatMap(([name, items]) => [itemLine(k, [{ text: name, color: t.signatureText }], width), ...items.map(entry => itemLine(k, [{ text: `· ${oneLine(entry)}`, color: t.fg.default }], width))])
}

/** One handover: a collapsed item, or (focused) the boxed card with its tasks, summary and keys, or its y/n. */
function handoverEntry(k: Kit, v: HandoversView, on: HandoversHandlers, ho: TmHandover, focused: boolean, tasks: Readonly<Record<string, TmLinkedTask>>, width: number): ListEntry {
  const closed = isClosed(ho)
  const selected = v.marks.selected.includes(ho.id)
  const age = ageLabel(ho.created, v.now)
  const head: HeadSpec = {
    mark: { key: `ho-sel:${ho.id}`, selected, onPress: () => on.select(ho.id), ...(closed ? { dim: 'A closed handover is not selected for closing' } : {}) },
    badge: closed ? 'CLOSED' : 'OPEN',
    title: oneLine(ho.tldr) || ho.id,
    age: age === '' ? dateOf(ho.created).slice(5) : age,
    dim: closed,
    pick: { key: `ho:${ho.id}`, onPress: () => on.pick(ho.id) },
  }
  const lines: Seg[][] = [refsSegs(k, ho), fieldSegs(k, 'next', oneLine(ho.nextAction) || '—')]
  if (!focused) return { focused: false, nodes: item(k, head, lines, width), rows: 1 + lines.length }
  const room = cardInner(width)
  const ids = Array.isArray(ho.taskIds) ? ho.taskIds : []
  const open = v.summaryOpen === ho.id
  const extra: RenderNode[] = [...ids.map(id => itemLine(k, taskSegs(k, id, tasks[id], v.phaseView.phases), room)), ...(open ? summaryRows(k, v.summaries[ho.id], room) : [])]
  const ask = v.marks.ask
  const actions =
    ask !== null && ask.kind === 'one' && ask.ids[0] === ho.id
      ? askRow(k, { id: 'close', question: `close ${ho.id}?`, onYes: on.confirmClose, onNo: on.cancelClose, width: room - 1 })
      : actionRows(
          k,
          [
            actionKey(k, { id: 'resume', key: 'r', label: 'resume handover here', rung: 'primary', onPress: () => on.resume(ho) }),
            actionKey(k, { id: 'new-tab', key: 'w', label: 'resume in new tab', onPress: press => on.newTab(ho, press.surface) }),
            actionKey(k, { id: 'copy', key: 'c', label: 'copy handover', onPress: press => on.copy(ho, press.surface) }),
            actionKey(k, { id: 'summary', key: 'i', label: `decisions·blockers ${open ? '▾' : '▸'}`, onPress: () => on.toggleSummary(ho, !open) }),
            actionKey(k, { id: 'close', key: 'x', label: 'close handover', rung: 'destructive', onPress: () => on.askClose({ kind: 'one', ids: [ho.id] }), ...(closed ? { dim: 'Already closed' } : {}) }),
          ],
          room - 1,
        )
  const spec = { id: ho.id, head, lines, extra, actions }
  return { focused: true, nodes: [focusedItem(k, spec, width)], rows: focusedRows(spec) }
}

/** The handovers pane (spec §9, §15.7), one frame. */
export function handoversPaneTree(k: Kit, v: HandoversView, on: HandoversHandlers, width: number): RenderNode {
  const { Text } = k.ui
  const { t } = k
  const inner = Math.max(10, width - 4)
  const tabs = v.tabs === undefined ? [] : [tabRow(k, v.tabs, { go: on.goTo, toggleSidebar: on.toggleSidebar }, inner), <Text> </Text>]
  const frame = (children: readonly RenderNode[]): RenderNode => paneFrame(k, { name: 'handovers', health: healthOf(v), width }, [...tabs, ...children])
  const tail: FooterItem[] = [
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]
  const s = v.snapshot
  if (s === null || !s.reachable) {
    if (v.connecting === true) return frame([<Text color={t.fg.subtle}>{CONNECTING}</Text>, <Text> </Text>, footer(k, tail, inner)])
    if (s === null) return frame([<Text color={t.fg.subtle}>Loading…</Text>, <Text> </Text>, footer(k, tail, inner)])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: on.refresh })
    return frame([offlineRow(k, s.reason, retry, inner), <Text> </Text>, footer(k, [{ keys: 'u', word: 'retry' }, ...tail], inner)])
  }
  const showClosed = v.marks.showClosed
  const tasks = v.list?.tasks ?? {}
  const listed = listedHandovers(v.list, s, showClosed)
  const shown = phaseHandovers(listed, v.phaseView.choice, tasks)
  const pg = handoverFocus(shown, v.first, v.pick)
  const picked = pg.picked
  const strip: StripView = {
    pane: 'handovers',
    view: showClosed ? { ...v.phaseView, phases: withClosed(v.phaseView.phases) } : v.phaseView,
    allCount: allCount(v.list, s, showClosed),
    focused: v.focused,
    digits: true,
    empty: showClosed ? 'No handovers' : 'No open handovers',
  }
  // The list: every handover, drawn into the rows the pane has left once the pinned rows are counted (spec §15.6).
  const entries = picked === undefined ? [] : shown.map(ho => handoverEntry(k, v, on, ho, ho.id === picked.id, tasks, inner))
  const scroll = scrollEntries(entries)
  // Set once the rows are known (below); the keys only run after the frame is drawn.
  const place: { win: ScrollWindow; room: number } = { win: windowOf({ entries: [], room: 1, first: 0, focus: -1 }), room: 1 }
  const step = (dir: -1 | 1): void => {
    const to = moveTarget(shown, place.win.start, picked?.id ?? '', dir)
    if (to === null) k.say(dir === 1 ? 'Already on the last handover' : 'Already on the first handover')
    else on.move(windowOf({ entries: scroll, room: place.room, first: place.win.start, focus: to.index }).start, to.id)
  }
  const turn = (dir: -1 | 1): void => {
    const to = pagedBy({ entries: scroll, room: place.room, first: place.win.start, focus: pg.index, dir })
    const id = shown[to.focus]?.id
    if (id === undefined || (to.focus === pg.index && to.first === place.win.start)) k.say(dir === 1 ? 'Already at the end' : 'Already at the top')
    else on.move(to.first, id)
  }
  const ask = v.marks.ask
  const asking = ask !== null
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'move-down', key: 'j', onPress: () => step(1) },
        { id: 'move-up', key: 'k', onPress: () => step(-1) },
      ],
    },
    {
      keys: 's',
      word: 'select',
      press: [
        {
          key: 's',
          onPress: () => {
            if (picked === undefined) return
            if (isClosed(picked)) k.say('A closed handover is not selected for closing')
            else on.select(picked.id)
          },
        },
      ],
    },
    { keys: 'b', word: 'close selected' },
    ...phaseFooter(k, strip, on.strip),
    ...(asking
      ? []
      : [
          {
            keys: 'p/n',
            word: 'page',
            press: [
              { key: 'p', onPress: () => turn(-1) },
              { key: 'n', onPress: () => turn(1) },
            ],
          },
        ]),
    { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: on.refresh }] },
    ...tail,
  ]
  // The window over the list in the room left, remembered for the wheel; and the toolbar that says where it stands.
  const placed = (pinned: number): RenderNode => {
    place.room = v.rows === undefined ? Number.POSITIVE_INFINITY : v.rows - pinned
    place.win = windowOf({ entries: scroll, room: place.room, first: v.first, focus: pg.index })
    rememberScroll('handovers', { entries: scroll, ids: shown.map(ho => ho.id), room: place.room, first: place.win.start, focus: pg.index })
    return paneToolbar(k, {
      left: phasePicker(k, strip, on.strip),
      right: [
        togglePiece(k, { id: 'show-closed', label: '◐ show closed', on: showClosed, onPress: on.toggleClosed }),
        ...(shown.length === 0 ? [] : [pagerPiece(k, { position: positionLabel(place.win, shown.length), atTop: pg.index <= 0, atEnd: pg.index >= shown.length - 1, onPage: turn })]),
        refreshPiece(k, { label: 'refresh', onPress: on.refresh }),
      ],
      width: inner,
    })
  }
  // The rows around the list: the frame, the tab row and its blank, the toolbar, a blank above the footer and the footer.
  const around = frameRows(k) + (tabs.length === 0 ? 0 : 2) + 1 + 1 + footerRows(keys, inner)
  if (picked === undefined) {
    const top = placed(around)
    const tab = orderPhases(strip.view.phases, strip.allCount, strip.view.choice).find(x => x.id === strip.view.choice)
    const way = strip.view.choice === ALL ? null : actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose(ALL) })
    return frame([top, ...withPhaseMenu(k, strip, on.strip, [<Text> </Text>, emptyRow(k, emptyLine(strip.empty, tab), way, inner)]), <Text> </Text>, footer(k, keys, inner)])
  }
  const closable = closableSelection(v.marks.selected, shown)
  // `all` takes every open handover in the list (the phase's, as listed), not a page's.
  const openIds = shown.filter(x => !isClosed(x)).map(x => x.id)
  const n = closable.length
  const bulkSpec = {
    keys: [actionKey(k, { id: 'close-selected', key: 'b', label: `close ${plural(n, 'handover')}`, onPress: () => on.askClose({ kind: 'bulk', ids: closable }), ...(n === 0 ? { dim: 'Nothing selected to close' } : {}) })],
    scopes: [
      { id: 'select-all', label: 'all', onPress: () => on.selectOnly(openIds) },
      { id: 'select-none', label: 'none', onPress: () => on.selectOnly([]) },
    ],
    width: inner,
  }
  const bulk: RenderNode[] =
    ask !== null && ask.kind === 'bulk'
      ? askRow(k, { id: 'bulk-close', question: `close ${plural(ask.ids.length, 'handover')}?`, onYes: on.confirmClose, onNo: on.cancelClose, width: inner })
      : [bulkRow(k, bulkSpec)]
  const bulkHeight = ask !== null && ask.kind === 'bulk' ? bulk.length : bulkRows(k, bulkSpec)
  // Over the list: the summary, the bulk row and a blank row.
  const top = placed(around + 1 + bulkHeight + 1)
  return frame([top, ...withPhaseMenu(k, strip, on.strip, [summaryRow(k, tallyLine(shown, v.now), [], inner), ...bulk, <Text> </Text>, ...scrollListing(k, entries, place.win, inner)]), <Text> </Text>, footer(k, keys, inner)])
}
