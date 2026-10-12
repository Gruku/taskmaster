// User intent: the handovers pane (spec §9) drawn with the kit: toolbar (pager, `◐ closed`, refresh), the phase strip with its
// no task tab, a tally, the bulk Close bar, and one card per handover — the focused one expanded with NEXT, its linked tasks'
// live status and phase, decisions and blockers on `i`, and r / w / c / i / x — so a handover is found by phase and acted on.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmCloseAsk, TmHandover, TmHandoverList, TmHandoverMarks, TmHandoverSummary, TmLinkedTask, TmPhase, TmPhaseView, TmSnapshot } from '../types'
import { allCount, closableSelection, isClosed, listedHandovers, moveTarget, phaseHandovers, tallyLine } from './handover-list'
import {
  badge,
  card,
  checkbox,
  confirmRow,
  helpFooter,
  keycap,
  type Kit,
  label,
  pager,
  phaseEmpty,
  phaseSection,
  type Piece,
  row,
  selectionBar,
  signal,
  type StripHandlers,
  type StripView,
  toolbar,
} from './kit'
import { ageLabel, CONNECTING, dateOf, handoverPage, oneLine, PAGE_SIZE, phaseName, truncate, wrapText } from './model'
import { withClosed } from './phases'
import type { RrGround, RrTone } from './rr'

export type HandoversView = {
  readonly snapshot: TmSnapshot | null
  /** The pane's own read (null: not yet; the snapshot's window is drawn meanwhile). */
  readonly list: TmHandoverList | null
  readonly marks: TmHandoverMarks
  readonly phaseView: TmPhaseView
  readonly pick: string
  readonly page: number
  readonly summaries: Readonly<Record<string, TmHandoverSummary>>
  readonly summaryOpen: string
  readonly now: number
  /** The pane holds the keyboard: the strip's tabs carry 0–9. */
  readonly focused: boolean
  /** tm is being retried after a transient failure: with no good snapshot the pane says so. */
  readonly connecting?: boolean
}

export type HandoversHandlers = {
  readonly pick: (id: string) => void
  /** j / k: show this page and focus this handover. */
  readonly move: (page: number, id: string) => void
  /** p / n: show this page and pick its first handover. */
  readonly turnPage: (page: number, firstId: string) => void
  readonly select: (id: string) => void
  /** `all` (this page's open ones) and `none` ([]). */
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
}

/** A linked task's badge tone by status; any other status (archived, unknown) draws dim. */
const TASK_TONE: Readonly<Record<string, RrTone>> = {
  done: 'success',
  'in-review': 'info',
  'in-progress': 'warning',
  blocked: 'critical',
  todo: 'signature',
}
const statusWord = (status: string): string => (status === '' ? '?' : status.replace(/-/g, ' '))
const PHASE_CELLS = 18
const MIN_LABEL = 12

/** Pieces laid in rows of `width`, wrapping, never dropping one (a card's keys are fixed slots). */
function packRows(k: Kit, pieces: readonly Piece[], width: number): RenderNode[] {
  const rows: Piece[][] = []
  let used = 0
  for (const piece of pieces) {
    const last = rows[rows.length - 1]
    if (last !== undefined && used + 1 + piece.width <= width) {
      last.push(piece)
      used += 1 + piece.width
    } else {
      rows.push([piece])
      used = piece.width
    }
  }
  return rows.map(r => row(k, r).el)
}

/** Wrapped lines in one colour, indented. */
function lines(k: Kit, color: string, text: string, width: number, indent = 0): RenderNode[] {
  const { Box, Text } = k.ui
  return wrapText(text, width - indent).map(line => (
    <Box paddingLeft={indent}>
      <Text color={color}>{line}</Text>
    </Box>
  ))
}

/** One linked task: its status badge (padded to the card's widest), id, title, and its phase at the right. */
function taskRow(k: Kit, id: string, task: TmLinkedTask | undefined, phases: readonly TmPhase[], badgeCells: number, room: number, on: RrGround): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const known = task !== undefined
  const tone = known ? TASK_TONE[task.status] : undefined
  const mark = badge(k, statusWord(known ? task.status : ''), tone ?? 'signature', { on, ...(tone === undefined ? { dim: true } : {}) })
  const phase = known && task.phase !== '' ? truncate(phaseName(phases.find(p => p.id === task.phase) ?? { id: task.phase, status: '', order: 0, count: 0, name: '' }), PHASE_CELLS) : ''
  const titleRoom = Math.max(0, room - badgeCells - 1 - id.length - 1 - (phase === '' ? 0 : phase.length + 2))
  const title = known ? truncate(oneLine(task.title), titleRoom) : truncate('not read', titleRoom)
  return (
    <Box flexDirection="row">
      <Box width={badgeCells} flexShrink={0}>
        {mark.el}
      </Box>
      <Box flexDirection="row" columnGap={1} paddingLeft={1}>
        <Text color={t.fg.bold} bold>
          {id}
        </Text>
        {title === '' ? null : <Text color={known ? t.fg.default : t.fg.subtle}>{title}</Text>}
      </Box>
      <Box flexGrow={1} />
      {phase === '' ? null : <Text color={t.fg.subtle}>{phase}</Text>}
    </Box>
  )
}

/** The expanded summary: DECISIONS and BLOCKERS (read on `i`), or why they are not shown yet. */
function summaryNodes(k: Kit, summary: TmHandoverSummary | undefined, room: number): RenderNode[] {
  const { Box } = k.ui
  if (summary === undefined || summary.unavailable === true) {
    return [
      <Box flexDirection="column" marginTop={1}>
        {lines(k, k.t.fg.subtle, summary === undefined ? 'loading summary…' : 'summary unavailable', room)}
      </Box>,
    ]
  }
  const listOf = (items: readonly string[] | undefined): readonly string[] => (Array.isArray(items) ? items : [])
  return (
    [
      ['decisions', listOf(summary.decisions)],
      ['blockers', listOf(summary.blockers)],
    ] as const
  )
    .filter(([, items]) => items.length > 0)
    .map(([name, items]) => (
      <Box flexDirection="column" marginTop={1}>
        {label(k, name).el}
        {items.flatMap(item => lines(k, k.t.fg.default, item, room))}
      </Box>
    ))
}

type CardCtx = {
  readonly v: HandoversView
  readonly on: HandoversHandlers
  readonly tasks: Readonly<Record<string, TmLinkedTask>>
  readonly room: number
}

/** One handover: checkbox, age and tldr, refs; focused, the whole card (spec §9). */
function handoverCard(k: Kit, c: CardCtx, ho: TmHandover, focused: boolean): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const { v, on, room } = c
  const closed = isClosed(ho)
  const selected = v.marks.selected.includes(ho.id)
  const ground: RrGround = selected ? 'overlay' : 'raised'
  const box = checkbox(k, {
    id: `ho-sel:${ho.id}`,
    selected,
    on: ground,
    onPress: () => on.select(ho.id),
    ...(focused ? { key: 's' } : {}),
    ...(closed ? { dim: 'A closed handover is not selected for closing' } : {}),
  })
  const ids = Array.isArray(ho.taskIds) ? ho.taskIds : []
  const branch = typeof ho.branch === 'string' ? ho.branch : ''
  const refs = [branch, ids.length === 0 ? '' : `${ids.length} ${ids.length === 1 ? 'task' : 'tasks'}`].filter(Boolean).join(' · ')
  const closedMark = closed ? badge(k, 'closed', 'signature', { on: ground, dim: true }) : null
  const right = (refs === '' ? 0 : refs.length) + (closedMark === null ? 0 : closedMark.width + 1)
  const showRight = right > 0 && room - box.width - 1 - right - 2 >= MIN_LABEL
  const labelRoom = Math.max(1, room - box.width - 1 - (showRight ? right + 2 : 0))
  const age = ageLabel(ho.created, v.now)
  const text = `${age === '' ? dateOf(ho.created) : `${age} ago`} · ${oneLine(ho.tldr)}`
  const wrapped = focused ? wrapText(text, labelRoom) : [truncate(text, labelRoom)]
  const head = keycap(k, { id: `ho:${ho.id}`, label: wrapped[0] ?? '', faded: closed, on: ground, onPress: () => on.pick(ho.id) })
  const collapsed = (
    <Box flexDirection="row" alignItems="flex-start">
      {box.el}
      <Box paddingLeft={1}>{head.el}</Box>
      <Box flexGrow={1} />
      {showRight ? (
        <Box flexDirection="row" columnGap={1} paddingLeft={2}>
          {refs === '' ? null : <Text color={t.fg.subtle}>{refs}</Text>}
          {closedMark === null ? null : closedMark.el}
        </Box>
      ) : null}
    </Box>
  )
  if (!focused) return card(k, { id: ho.id, selected, focused: false }, collapsed, [])

  const indent = box.width + 1
  const body: RenderNode[] = wrapped.slice(1).map(line => (
    <Box paddingLeft={indent}>
      <Text color={closed ? t.fg.subtle : t.fg.default}>{line}</Text>
    </Box>
  ))
  body.push(...lines(k, t.fg.subtle, [ho.id, branch].filter(Boolean).join(' · '), room, indent))
  const open = v.summaryOpen === ho.id
  if (open) body.push(...summaryNodes(k, v.summaries[ho.id], room))
  const next = oneLine(ho.nextAction) || '—'
  body.push(
    <Box flexDirection="row" columnGap={2} {...(open ? { marginTop: 1 } : {})}>
      {label(k, 'next').el}
      <Box flexDirection="column">{lines(k, t.fg.default, next, room - 'next'.length - 2)}</Box>
    </Box>,
  )
  if (ids.length > 0) {
    const words = ids.map(id => statusWord(c.tasks[id]?.status ?? ''))
    const badgeCells = Math.max(...words.map(w => w.length)) + 2
    body.push(
      <Box flexDirection="column" marginTop={1}>
        {ids.map(id => taskRow(k, id, c.tasks[id], v.phaseView.phases, badgeCells, room, ground))}
      </Box>,
    )
  }
  const ask = v.marks.ask
  if (ask !== null && ask.kind === 'one' && ask.ids[0] === ho.id) {
    body.push(
      ...confirmRow(k, {
        id: 'close',
        question: `Close ${ho.id}?`,
        onYes: on.confirmClose,
        onNo: on.cancelClose,
        width: room,
        on: ground,
      }),
    )
  } else {
    const keys: Piece[] = [
      keycap(k, { id: 'resume', key: 'r', label: 'resume here', on: ground, onPress: () => on.resume(ho) }),
      keycap(k, { id: 'new-tab', key: 'w', label: 'new tab', on: ground, onPress: press => on.newTab(ho, press.surface) }),
      keycap(k, { id: 'copy', key: 'c', label: 'copy', on: ground, onPress: press => on.copy(ho, press.surface) }),
      keycap(k, { id: 'summary', key: 'i', label: `${open ? '▾' : '▸'} decisions·blockers`, on: ground, onPress: () => on.toggleSummary(ho, !open) }),
      keycap(k, {
        id: 'close',
        key: 'x',
        label: 'close',
        tone: 'warning',
        on: ground,
        onPress: () => on.askClose({ kind: 'one', ids: [ho.id] }),
        ...(closed ? { dim: 'Already closed' } : {}),
      }),
    ]
    body.push(
      <Box flexDirection="column" marginTop={1}>
        {packRows(k, keys, room)}
      </Box>,
    )
  }
  return card(k, { id: ho.id, selected, focused: true }, collapsed, body)
}

/** Before data, while tm is retried, or with tm unreachable: the title, then what is going on (and `u retry` when down). */
function statusNodes(k: Kit, v: HandoversView, on: HandoversHandlers): RenderNode[] | null {
  const { Text } = k.ui
  const s = v.snapshot
  if (s !== null && s.reachable) return null
  const title = label(k, 'handovers').el
  if (v.connecting === true) return [title, <Text color={k.t.fg.subtle}>{CONNECTING}</Text>]
  if (s === null) return [title, <Text color={k.t.fg.subtle}>Loading…</Text>]
  return [title, signal(k, 'critical', 'Taskmaster unreachable', s.reason).el, keycap(k, { id: 'refresh', key: 'u', label: 'retry', onPress: on.refresh }).el]
}

/** The handovers pane's children, drawn on the page ground the caller lays (spec §9). */
export function handoversPaneTree(k: Kit, v: HandoversView, on: HandoversHandlers, width: number): RenderNode[] {
  const { Box, Text } = k.ui
  const status = statusNodes(k, v, on)
  if (status !== null) return status
  const inner = Math.max(20, width - 2)
  const showClosed = v.marks.showClosed
  const tasks = v.list?.tasks ?? {}
  const listed = listedHandovers(v.list, v.snapshot, showClosed)
  const shown = phaseHandovers(listed, v.phaseView.choice, tasks)
  const pg = handoverPage(shown, v.page, v.pick)
  const picked = pg.picked
  const strip: StripView = {
    pane: 'handovers',
    view: showClosed ? { ...v.phaseView, phases: withClosed(v.phaseView.phases) } : v.phaseView,
    allCount: allCount(v.list, v.snapshot, showClosed),
    focused: v.focused,
    digits: true,
    empty: showClosed ? 'No handovers' : 'No open handovers',
  }
  const bar = toolbar(k, {
    title: 'handovers',
    slots: [
      pager(k, { page: pg.page, pages: pg.pages, onTurn: n => on.turnPage(n, shown[n * PAGE_SIZE]?.id ?? '') }),
      keycap(k, { id: 'show-closed', label: `${showClosed ? '◐' : '○'} closed`, selected: showClosed, onPress: on.toggleClosed }),
      keycap(k, { id: 'refresh', key: 'u', label: 'refresh', onPress: on.refresh }),
    ],
    width: inner,
  })
  const down = picked === undefined ? null : moveTarget(shown, pg.page, picked.id, 1)
  const up = picked === undefined ? null : moveTarget(shown, pg.page, picked.id, -1)
  const moves = row(k, [
    keycap(k, { id: 'move-down', key: 'j', label: '↓', onPress: () => down !== null && on.move(down.page, down.id), ...(down === null ? { dim: 'Already on the last handover' } : {}) }),
    keycap(k, { id: 'move-up', key: 'k', label: '↑', onPress: () => up !== null && on.move(up.page, up.id), ...(up === null ? { dim: 'Already on the first handover' } : {}) }),
  ])
  const footer = (
    <Box flexDirection="row" columnGap={2}>
      {moves.el}
      {helpFooter(k, ['s select', 'b close selected', 'h/l phase', '0 all', '1-9 jump', 'p/n page', 'u refresh', 'esc close'], Math.max(1, inner - moves.width - 2))}
    </Box>
  )
  if (picked === undefined) return [bar, ...phaseSection(k, strip, on.strip, inner, [phaseEmpty(k, strip, on.strip)]), footer]

  const closable = closableSelection(v.marks.selected, shown)
  // `all` selects this page's open handovers, as review's does its page.
  const openIds = pg.entries.filter(x => !isClosed(x)).map(x => x.id)
  const body: RenderNode[] = [
    <Text color={k.t.fg.subtle}>{truncate(tallyLine(shown, v.now), inner)}</Text>,
    selectionBar(k, {
      actions: [{ id: 'close-selected', key: 'b', verb: 'Close', count: closable.length, tone: 'warning', onPress: () => on.askClose({ kind: 'bulk', ids: closable }) }],
      ghosts: [
        { id: 'select-all', label: 'all', onPress: () => on.selectOnly(openIds) },
        { id: 'select-none', label: 'none', onPress: () => on.selectOnly([]) },
      ],
      width: inner,
    }),
  ]
  const ask = v.marks.ask
  if (ask !== null && ask.kind === 'bulk') {
    const n = ask.ids.length
    body.push(...confirmRow(k, { id: 'bulk-close', question: `Close ${n} handover${n === 1 ? '' : 's'}?`, onYes: on.confirmClose, onNo: on.cancelClose, width: inner }))
  }
  const ctx: CardCtx = { v, on, tasks, room: inner - 4 }
  body.push(...pg.entries.map(ho => handoverCard(k, ctx, ho, ho.id === picked.id)))
  return [bar, ...phaseSection(k, strip, on.strip, inner, body), footer]
}
