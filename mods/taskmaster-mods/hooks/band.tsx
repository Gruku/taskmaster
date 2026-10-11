// User intent: the band above the prompt (spec §6) — at most two lines on their own raised ground under a `◆ TASKMASTER`
// chip, so it is never mistaken for the end of Claude's reply: this session's task, its handover, and a key to every pane.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmSessionHandover } from '../types'
import type { TmPane } from './flows'
import { badge, keycap, type Kit, label, type Piece, signal, TM_TAG } from './kit'
import { type BandModel, CONNECTING, oneLine, statusTone, statusWord, truncate } from './model'

export type BandHandlers = {
  readonly open: (pane: TmPane) => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly hideRestore: () => void
}

/** The cells the flexible text (the title, the tldr, a refusal) keeps before the fixed pieces start to shrink. */
const MIN_TEXT = 12
/** The fewest characters of a task id the band shows (line 1 at its tightest, a confirm question). */
const MIN_ID = 8
const GAP = 2
/** Squeeze levels: 0 every word; 4 keys, counts and a cut id only (the status badge goes last). */
const LEVELS = [0, 1, 2, 3, 4] as const
type Level = (typeof LEVELS)[number]

/** How the band names a D5 handover. */
export const HANDOVER_TAG: Readonly<Record<TmSessionHandover['tag'], string>> = {
  'written here': 'written here',
  'resumed from': 'resumed from',
  task: 'for this task',
}

type Flex = { readonly text: string; readonly color: string; readonly bold: boolean }
type Line = { readonly left: readonly Piece[]; readonly flex: Flex | null; readonly right: readonly Piece[] }

function text(k: Kit, value: string, color: string, bold = false): Piece {
  const { Text } = k.ui
  return {
    el: (
      <Text color={color} bold={bold} wrap="truncate-end">
        {value}
      </Text>
    ),
    width: value.length,
  }
}

const sum = (pieces: readonly Piece[]): number => pieces.reduce((n, p) => n + p.width, 0)

/** The pieces, in order, while they fit `room` with GAP between them (none at all when the first does not). */
function keep(pieces: readonly Piece[], room: number): RenderNode[] {
  const kept: RenderNode[] = []
  let used = -GAP
  for (const p of pieces) {
    if (used + GAP + p.width > room) break
    used += GAP + p.width
    kept.push(p.el)
  }
  return kept
}

/** The room a line leaves its flexible text: the inner width less every fixed piece and the gaps between them all. */
function roomOf(line: Line, inner: number): number {
  const items = line.left.length + line.right.length + (line.flex === null ? 0 : 1)
  return inner - sum(line.left) - sum(line.right) - GAP * Math.max(0, items - 1)
}

/** The first squeeze level at which the flexible text keeps MIN_TEXT cells (or all of itself); else the tightest. */
function settle(build: (level: Level) => Line, inner: number): Line {
  for (const level of LEVELS) {
    const line = build(level)
    const want = line.flex === null ? 0 : Math.min(MIN_TEXT, line.flex.text.length)
    if (roomOf(line, inner) >= want) return line
  }
  return build(4)
}

/** A line laid out: the fixed left pieces, the flexible text cut to its room, the right group pushed to the far end. */
function draw(k: Kit, line: Line, inner: number): RenderNode {
  const { Box, Text } = k.ui
  const room = roomOf(line, inner)
  const flex = line.flex === null || room < 1 ? null : truncate(line.flex.text, room)
  // Too narrow even for the keys: the left pieces stay, then as many right-hand keys as still fit, in order.
  const used = sum(line.left) + GAP * Math.max(0, line.left.length - 1) + (flex === null || flex === '' ? 0 : GAP + flex.length)
  const right = room < 0 ? keep(line.right, inner - used - GAP) : line.right.map(p => p.el)
  return (
    <Box flexDirection="row" columnGap={GAP} alignItems="flex-start">
      {line.left.map(p => p.el)}
      {flex === null || flex === '' || line.flex === null ? null : (
        <Text color={line.flex.color} bold={line.flex.bold} wrap="truncate-end">
          {flex}
        </Text>
      )}
      {right.length === 0 ? null : (
        <Box flexGrow={1} flexDirection="row" justifyContent="flex-end" columnGap={GAP}>
          {right}
        </Box>
      )}
    </Box>
  )
}

/** `1 waiting`, `2 handovers`, `4 restore` (+ `x hide`): zero and offline counts fade but still open their pane. */
function counts(k: Kit, m: BandModel, level: Level, on: BandHandlers): Piece[] {
  const offline = m.fault !== 'none'
  const n = (count: number, capped = false): string => (offline ? '–' : `${count}${capped ? '+' : ''}`)
  const waiting = [`${n(m.waiting, m.waitingCapped)} waiting on you`, `${n(m.waiting, m.waitingCapped)} waiting`][level] ?? n(m.waiting, m.waitingCapped)
  const handovers = level === 0 ? `${n(m.handovers)} handovers` : n(m.handovers)
  const pieces = [
    keycap(k, { id: 'band-review', key: '1', label: waiting, on: 'raised', faded: offline || m.waiting === 0, onPress: () => on.open('review') }),
    keycap(k, { id: 'band-handovers', key: '2', label: handovers, on: 'raised', faded: offline || m.handovers === 0, onPress: () => on.open('handovers') }),
  ]
  if (m.restore !== null) {
    pieces.push(
      keycap(k, { id: 'band-restore', key: '4', label: level <= 1 ? `${m.restore} restore` : `${m.restore}`, on: 'raised', onPress: () => on.open('restore') }),
      keycap(k, { id: 'band-restore-hide', key: 'x', label: level <= 2 ? 'hide' : '', on: 'raised', onPress: on.hideRestore }),
    )
  }
  return pieces
}

const tagChip = (k: Kit, level: Level): Piece => badge(k, level <= 2 ? TM_TAG : '◆ TM', 'signature', { on: 'raised' })
const sessionKey = (k: Kit, level: Level, on: BandHandlers): Piece =>
  keycap(k, { id: 'band-session', key: '3', label: level <= 2 ? 'session ›' : level === 3 ? 'session' : '', on: 'raised', onPress: () => on.open('session') })

/** `done <id>?`, then `<id>?`, the id cut to `room`, never under MIN_ID characters: null when even that does not fit. */
function question(id: string, room: number): string | null {
  for (const ask of [`done ${id}?`, `${id}?`]) if (ask.length <= room) return ask
  const least = Math.min(MIN_ID, id.length)
  return room - 1 >= least ? `${truncate(id, room - 1)}?` : null
}

/**
 * The band (spec §6). Line 1: the chip, the task (status badge, id, bold title), `d done` when it is ready, `3 session ›`.
 * Line 2: the D5 handover (`c copy`), or a y/n confirm, or a refusal; the counts sit on line 2 when there is one under a
 * task, else on line 1. Each line squeezes its words before its text drops under MIN_TEXT; one blank row above when room.
 */
export function bandTree(k: Kit, m: BandModel, mode: TmBandMode, width: number, maxRows: number, on: BandHandlers): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const inner = Math.max(1, width - 2)
  const task = m.task
  const confirming = task !== null && mode.confirmingId === task.id
  const refused = task !== null && mode.refusal !== '' && mode.refusalId === task.id
  const second = task !== null ? confirming || refused || m.handover !== null : m.handover !== null
  const countsBelow = task !== null && second
  const lines: RenderNode[] = []

  if (m.fault === 'connecting' && task === null) {
    lines.push(draw(k, { left: [tagChip(k, 0)], flex: { text: CONNECTING, color: t.fg.subtle, bold: false }, right: [] }, inner))
  } else {
    lines.push(
      draw(
        k,
        settle(level => {
          const left: Piece[] = [tagChip(k, level)]
          let flex: Flex | null = null
          // Offline at the tightest is its glyph alone: the status line still says `◆ tm offline`.
          if (m.fault === 'offline') left.push(signal(k, 'critical', level <= 3 ? 'offline' : ''))
          if (task !== null) {
            if (m.fault !== 'offline' && task.status !== '' && level <= 3) left.push(badge(k, statusWord(task.status), statusTone(task.status), { on: 'raised', strength: 12 }))
            left.push(text(k, level <= 3 ? task.id : truncate(task.id, MIN_ID), t.fg.subtle))
            const title = oneLine(task.title)
            flex = title === '' ? null : { text: title, color: task.inferred ? t.fg.subtle : t.fg.bold, bold: !task.inferred }
          }
          // A task only inferred from the branch says so, and never offers d (ruling F1: d acts on this session's binding).
          const right: Piece[] = task !== null && task.inferred ? [text(k, 'inferred', t.fg.subtle)] : []
          if (task !== null && task.ready) {
            right.push(keycap(k, { id: 'band-done', key: 'd', label: level <= 3 ? 'done' : '', tone: 'success', on: 'raised', onPress: () => on.askDone(task.id) }))
          }
          if (!countsBelow) right.push(...counts(k, m, level, on))
          right.push(sessionKey(k, level, on))
          return { left, flex, right }
        }, inner),
        inner,
      ),
    )
  }

  if (second && task !== null && confirming) {
    const yes = keycap(k, { id: 'band-yes', key: 'y', label: 'yes', tone: 'success', on: 'raised', onPress: () => on.confirmDone(task.id) })
    const no = keycap(k, { id: 'band-no', key: 'n', label: 'no', autoFocus: true, on: 'raised', onPress: on.cancel })
    const ask = question(task.id, inner - yes.width - no.width - 2 * GAP - 2)
    const props = k.badges[24].raised.warning
    if (ask === null) {
      // Too narrow for one row: the question above the pair (never y without n).
      lines.push(<Text {...props}>{` ${truncate(`${task.id}?`, inner - 2)} `}</Text>)
      lines.push(draw(k, { left: [yes, no], flex: null, right: [] }, inner))
    } else {
      const asked: Piece = { el: <Text {...props}>{` ${ask} `}</Text>, width: ask.length + 2 }
      lines.push(draw(k, { left: [asked, yes, no], flex: null, right: counts(k, m, 2, on) }, inner))
    }
  } else if (second && task !== null && refused) {
    lines.push(
      draw(
        k,
        settle(level => ({
          left: [badge(k, 'refused', 'critical', { on: 'raised' })],
          flex: { text: oneLine(mode.refusal), color: t.fg.default, bold: false },
          right: counts(k, m, level, on),
        }), inner),
        inner,
      ),
    )
  } else if (second && m.handover !== null) {
    const ho = m.handover
    lines.push(
      draw(
        k,
        settle(level => {
          const left: Piece[] = [label(k, level <= 2 ? '↳ handover' : '↳')]
          if (level <= 1) left.push(text(k, `${HANDOVER_TAG[ho.tag]} ·`, t.fg.subtle))
          const right: Piece[] = [keycap(k, { id: 'band-copy', key: 'c', label: level <= 3 ? 'copy' : '', on: 'raised', onPress: press => on.copy(ho.id, press.surface) })]
          if (countsBelow) right.push(...counts(k, m, level, on))
          return { left, flex: { text: oneLine(ho.tldr) || ho.id, color: t.fg.default, bold: false }, right }
        }, inner),
        inner,
      ),
    )
  }

  // The rows the prompt area gives: a blank row above only with room to spare; a confirm keeps its y/n over line 1.
  const blank = maxRows >= lines.length + 1 && maxRows >= 3
  const room = Math.max(1, maxRows - (blank ? 1 : 0))
  const kept = lines.length <= room ? lines : confirming ? lines.slice(-room) : lines.slice(0, room)
  return (
    <Box flexDirection="column" paddingTop={blank ? 1 : 0}>
      <Box flexDirection="column" backgroundColor={t.surface.raised} paddingX={1}>
        {kept}
      </Box>
    </Box>
  )
}
