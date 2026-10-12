// User intent: the band above the prompt (spec §15.5, direction C) — at most two lines on their own raised ground: this
// session's task and its handover as subject text on the left, their keys in one fixed right column, no counts — so it is
// never mistaken for transcript text and never competes with it.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmSessionHandover } from '../types'
import { badge, type Kit, type Piece, TM_TAG } from './kit'
import { ageLabel, type BandModel, CONNECTING, oneLine } from './model'
import { actionKey, badgeSeg, line, type Seg, statusBadgeWord } from './vocab'

export type BandHandlers = {
  readonly openSidebar: () => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly retry: () => void
}

/** What the band draws besides the model: the y/n or refusal state, its room, the time (handover age) and the offline reason. */
export type BandArgs = {
  readonly mode: TmBandMode
  readonly width: number
  readonly maxRows: number
  readonly now: number
  readonly reason: string
}

/** The right column's cells while the band is at least 80 cells wide: the keys line up under each other. */
export const RCW = 35
/** The subject keeps this many cells before the labels shorten. */
const MIN_TEXT = 12

/** How the band names a D5 handover. */
export const HANDOVER_TAG: Readonly<Record<TmSessionHandover['tag'], string>> = {
  'written here': 'written here',
  'resumed from': 'resumed from',
  task: 'for this task',
}

/** Each key's label at each squeeze level: whole, short, then the key alone. */
const LABELS = {
  done: ['mark task done', 'done', ''],
  sidebar: ['open sidebar', 'sidebar', ''],
  copy: ['copy handover', 'copy', ''],
  retry: ['retry tm connection', 'retry', ''],
  yes: ['yes', 'yes', ''],
  no: ['no', 'no', ''],
} as const
type Level = 0 | 1 | 2 | 3
const LEVELS: readonly Level[] = [0, 1, 2, 3]
const labelAt = (name: keyof typeof LABELS, level: Level): string => LABELS[name][Math.min(level, 2)] ?? ''

/** One line: what leads it (the chip, or the indent of line 2), its subject segments, and its keys. */
type BandLine = { readonly lead: Piece; readonly segs: readonly Seg[]; readonly keys: readonly Piece[] }

const keysWidth = (keys: readonly Piece[]): number => keys.reduce((n, p) => n + p.width, 0) + 3 * Math.max(0, keys.length - 1)

/** The band (spec §15.5). Squeezes level by level (short labels, the short chip, keys alone) until every subject keeps MIN_TEXT. */
export function bandTree(k: Kit, m: BandModel, a: BandArgs, on: BandHandlers): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const inner = Math.max(1, a.width - 2)
  const task = m.task
  const offline = m.fault === 'offline'
  const confirming = task !== null && a.mode.confirmingId === task.id
  const refused = task !== null && a.mode.refusal !== '' && a.mode.refusalId === task.id
  const sp = (n: number): Seg => ({ text: ' '.repeat(n), color: t.fg.default })

  const build = (level: Level): BandLine[] => {
    const chip = badge(k, level <= 1 ? TM_TAG : '◆ TM', 'signature', { on: 'raised' })
    const lead1: Piece = {
      el: (
        <Box flexDirection="row">
          {chip.el}
          <Text>{'  '}</Text>
        </Box>
      ),
      width: chip.width + 2,
    }
    const lead2: Piece = { el: <Text>{'  '}</Text>, width: 2 }
    const key = (id: string, letter: string, name: keyof typeof LABELS, press: Parameters<typeof actionKey>[1]['onPress'], extra: { autoFocus?: boolean } = {}): Piece =>
      actionKey(k, { id, key: letter, label: labelAt(name, level), on: 'raised', onPress: press, ...extra })
    const sidebar = key('band-sidebar', 'm', 'sidebar', () => on.openSidebar())
    const lines: BandLine[] = []

    if (offline) {
      lines.push({
        lead: lead1,
        segs: [
          { text: '▲ tm offline', color: t.tone.critical, bold: true },
          { text: a.reason === '' ? '' : ` · ${oneLine(a.reason)}`, color: t.fg.subtle },
        ],
        keys: [key('band-retry', 'r', 'retry', () => on.retry())],
      })
    } else if (m.fault === 'connecting' && task === null) {
      lines.push({ lead: lead1, segs: [{ text: CONNECTING, color: t.fg.subtle }], keys: [sidebar] })
    } else if (task === null) {
      lines.push({ lead: lead1, segs: [{ text: 'no task bound to this session', color: t.fg.disabled }], keys: [sidebar] })
    } else {
      const title = oneLine(task.title)
      lines.push({
        lead: lead1,
        segs: [
          ...(task.status === '' || level >= 3 ? [] : [badgeSeg(k, statusBadgeWord(task.status)), sp(2)]),
          { text: task.id, color: t.fg.subtle },
          ...(task.inferred ? [sp(2), { text: 'inferred', color: t.fg.subtle }] : []),
          ...(title === '' ? [] : [sp(2), { text: title, color: task.inferred ? t.fg.subtle : t.fg.bold, bold: !task.inferred }]),
        ],
        keys: [...(task.ready ? [key('band-done', 'd', 'done', () => on.askDone(task.id))] : []), sidebar],
      })
    }

    if (task !== null && confirming) {
      lines.push({
        lead: lead2,
        segs: [{ text: `mark ${task.id} done?`, color: t.tone.warning, bold: true }],
        keys: [key('band-yes', 'y', 'yes', () => on.confirmDone(task.id)), key('band-no', 'n', 'no', () => on.cancel(), { autoFocus: true })],
      })
    } else if (task !== null && refused) {
      lines.push({
        lead: lead2,
        segs: [
          { text: '▲ REFUSED  ', color: t.tone.critical, bold: true },
          { text: oneLine(a.mode.refusal), color: t.fg.default },
        ],
        keys: [],
      })
    } else if (m.handover !== null) {
      const ho = m.handover
      const age = ageLabel(ho.created, a.now)
      lines.push({
        lead: lead2,
        segs: [
          { text: `↳ ${HANDOVER_TAG[ho.tag]} `, color: t.fg.subtle },
          { text: ho.id, color: t.signatureText },
          { text: age === '' ? '' : ` · ${age}`, color: t.fg.subtle },
          { text: ' · ', color: t.fg.subtle },
          { text: oneLine(ho.tldr), color: t.fg.default },
        ],
        keys: [key('band-copy', 'c', 'copy', press => on.copy(ho.id, press.surface))],
      })
    } else if (task !== null && !offline) {
      lines.push({ lead: lead2, segs: [{ text: '↳ no handover yet for this task', color: t.fg.disabled }], keys: [] })
    }
    return lines
  }

  const subjectLen = (l: BandLine): number => l.segs.reduce((n, s) => n + s.text.length, 0)
  const columnOf = (lines: readonly BandLine[], level: Level): number => {
    const natural = Math.max(0, ...lines.map(l => keysWidth(l.keys)))
    return level === 0 && inner >= 80 ? Math.max(RCW, natural) : natural
  }
  const roomOf = (l: BandLine, column: number): number => inner - l.lead.width - column - 2
  let level: Level = 3
  for (const at of LEVELS) {
    const lines = build(at)
    const column = columnOf(lines, at)
    if (lines.every(l => roomOf(l, column) >= Math.min(MIN_TEXT, subjectLen(l)))) {
      level = at
      break
    }
  }
  const lines = build(level)
  const column = columnOf(lines, level)
  const drawn = lines.map(l => {
    const subject = line(k, l.segs, Math.max(0, roomOf(l, column)))
    return (
      <Box flexDirection="row">
        {l.lead.el}
        {subject.el}
        <Box flexGrow={1} />
        <Text>{'  '}</Text>
        <Box flexDirection="row" columnGap={3} width={column}>
          {l.keys.map(p => p.el)}
        </Box>
      </Box>
    )
  })

  // The rows the prompt area gives: a blank row above only with room to spare; a confirm keeps its y/n over line 1.
  const blank = a.maxRows >= drawn.length + 1 && a.maxRows >= 3
  const room = Math.max(1, a.maxRows - (blank ? 1 : 0))
  const kept = drawn.length <= room ? drawn : confirming ? drawn.slice(-room) : drawn.slice(0, room)
  return (
    <Box flexDirection="column" paddingTop={blank ? 1 : 0}>
      <Box flexDirection="column" backgroundColor={t.surface.raised} paddingX={1}>
        {kept}
      </Box>
    </Box>
  )
}
