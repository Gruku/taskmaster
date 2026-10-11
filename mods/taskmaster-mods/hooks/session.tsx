// User intent: the Session tab (spec §7; band `3`, /tm-session) — everything about this session in one ◆ TASKMASTER panel:
// its task with `d` done and `a` to agent, its handover with copy / open / new tab / decisions, and where it came from.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmHandoverSummary, TmSessionBurst, TmSessionHandover, TmSnapshot } from '../types'
import { HANDOVER_TAG } from './band'
import type { TmPane } from './flows'
import { badge, confirmRow, keycap, type Kit, label, panel, panelTitle, type Piece, row, rule } from './kit'
import { ageLabel, gateSignal, isReady, oneLine, statusTone, statusWord, truncate, wrapText } from './model'

export type SessionView = {
  readonly snapshot: TmSnapshot | null
  /** D5, with a newer notice already let in (withNotice). */
  readonly handover: TmSessionHandover | null
  readonly resumedFrom: string
  readonly burst: TmSessionBurst | null
  /** The pending restore burst's unfinished sessions (null: no `4`). */
  readonly restore: number | null
  readonly band: TmBandMode
  /** The handover's decisions and blockers when read; shown while `summaryOpen`. */
  readonly summary: TmHandoverSummary | undefined
  readonly summaryOpen: boolean
  readonly connecting: boolean
  readonly demo: boolean
  readonly now: number
}

export type SessionHandlers = {
  readonly open: (pane: TmPane) => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly sendBack: (taskId: string) => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly openHandover: (handoverId: string) => void
  readonly launch: (handover: TmSessionHandover, surface: RenderSurface) => void
  readonly toggleSummary: (handoverId: string, open: boolean) => void
}

/** The section label column: `HANDOVER` and two cells. */
const LABEL_W = 10

/** A section's first row: its label in the label column, then the pieces. */
function head(k: Kit, word: string, pieces: readonly RenderNode[]): RenderNode {
  const { Box } = k.ui
  return (
    <Box flexDirection="row" alignItems="flex-start">
      <Box width={LABEL_W} flexShrink={0}>
        {label(k, word).el}
      </Box>
      <Box flexDirection="row" columnGap={2} alignItems="flex-start">
        {pieces}
      </Box>
    </Box>
  )
}

/** A row under a section's first, in the content column. */
function under(k: Kit, children: readonly RenderNode[]): RenderNode {
  const { Box } = k.ui
  return (
    <Box flexDirection="column" paddingLeft={LABEL_W}>
      {children}
    </Box>
  )
}

function line(k: Kit, text: string, color: string, width: number): RenderNode {
  const { Text } = k.ui
  return (
    <Text color={color} wrap="truncate-end">
      {truncate(text, Math.max(1, width))}
    </Text>
  )
}

/** `full · patch-1-9 · review-gate pass ✓ · ready for done`: lane, phase, gate and where the pipeline stands. */
function metaOf(snapshot: TmSnapshot): string {
  const b = snapshot.bound
  const d = b?.detail ?? null
  if (b === null || d === null) return ''
  const gate = gateSignal(d.gateState)
  const p = b.pipeline
  const stage = p === null ? '' : p.laneless ? 'no pipeline' : p.outstanding.length === 0 ? 'ready for done' : `next: ${p.outstanding[0]}`
  return [d.lane || 'no lane', d.phase ?? '', gate === null ? '' : `${gate.word}${gate.kind === 'success' ? ' ✓' : ''}`, stage, b.inferred ? 'inferred from the branch' : '']
    .filter(Boolean)
    .join(' · ')
}

/** Why `d` (and `a`) cannot act on the bound task, or '' when they can. */
function whyNot(snapshot: TmSnapshot, forDone: boolean): string {
  const b = snapshot.bound
  if (b === null) return 'No task bound to this session'
  if (!snapshot.reachable || b.detail === null) return 'Taskmaster is offline: try again once it answers'
  if (b.inferred) return `${b.taskId} is only inferred from the branch: pick it to bind it to this session`
  if (!forDone || isReady(b.detail, b.pipeline)) return ''
  const p = b.pipeline
  return p !== null && !p.laneless && p.outstanding.length > 0 ? `Not ready for done: ${p.outstanding[0]} is outstanding` : 'Not ready for done yet'
}

function taskSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - LABEL_W
  const s = v.snapshot
  const b = s?.bound ?? null
  if (s === null || b === null) return [head(k, 'task', [line(k, 'No task bound to this session', t.fg.subtle, room)])]
  const d = b.detail
  const first: Piece[] = []
  if (s.reachable && d !== null) first.push(badge(k, statusWord(d.status), statusTone(d.status), { on: 'raised', strength: 12 }))
  first.push({ el: line(k, b.taskId, t.fg.subtle, b.taskId.length), width: b.taskId.length })
  const used = first.reduce((n, p) => n + p.width + 2, 0)
  const title = d === null ? '' : oneLine(d.title)
  const rows: RenderNode[] = [
    head(k, 'task', [...first.map(p => p.el), ...(title === '' || room - used < 4 ? [] : [line(k, title, b.inferred ? t.fg.subtle : t.fg.bold, room - used)])]),
  ]
  const meta = metaOf(s)
  if (meta !== '') rows.push(under(k, [line(k, meta, t.fg.subtle, room)]))
  if (v.band.confirmingId === b.taskId) {
    rows.push(under(k, confirmRow(k, { id: 'session', question: `done ${b.taskId}?`, onYes: () => on.confirmDone(b.taskId), onNo: on.cancel, width: room, on: 'raised' })))
    return rows
  }
  const noDone = whyNot(s, true)
  const noAgent = whyNot(s, false)
  const keys = row(k, [
    keycap(k, { id: 'session-done', key: 'd', label: 'done', tone: 'success', on: 'raised', onPress: () => on.askDone(b.taskId), ...(noDone === '' ? {} : { dim: noDone }) }),
    keycap(k, { id: 'session-agent', key: 'a', label: 'agent', tone: 'warning', on: 'raised', onPress: () => on.sendBack(b.taskId), ...(noAgent === '' ? {} : { dim: noAgent }) }),
  ], 2)
  const human = d === null ? '' : oneLine(d.humanAction)
  const { Box } = k.ui
  rows.push(
    under(k, [
      <Box flexDirection="row" columnGap={2} alignItems="flex-start">
        {human === '' ? null : line(k, `human: ${human}`, t.fg.default, room - keys.width - 2)}
        <Box flexGrow={1} />
        {keys.el}
      </Box>,
    ]),
  )
  if (v.band.refusal !== '' && v.band.refusalId === b.taskId) {
    rows.push(under(k, [badge(k, 'refused', 'critical', { on: 'raised' }).el, ...wrapText(v.band.refusal, room).map(text => line(k, text, t.fg.default, room))]))
  }
  return rows
}

function handoverSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - LABEL_W
  const ho = v.handover
  if (ho === null) return [head(k, 'handover', [line(k, 'No handover for this session yet', t.fg.subtle, room)])]
  const age = ageLabel(ho.created, v.now)
  const tag = badge(k, HANDOVER_TAG[ho.tag], 'signature', { on: 'raised', strength: 12 })
  const id = `${ho.id}${age === '' ? '' : ` · ${age} ago`}`
  const rows: RenderNode[] = [
    head(k, 'handover', [tag.el, line(k, id, t.fg.subtle, room - tag.width - 2)]),
    under(k, [line(k, oneLine(ho.tldr) || ho.id, t.fg.default, room)]),
  ]
  if (ho.next !== undefined && ho.next !== '') rows.push(under(k, [line(k, `Next: ${oneLine(ho.next)}`, t.fg.subtle, room)]))
  rows.push(
    under(k, [
      row(k, [
        keycap(k, { id: 'session-copy', key: 'c', label: 'copy', on: 'raised', onPress: press => on.copy(ho.id, press.surface) }),
        keycap(k, { id: 'session-open', key: 'h', label: 'in handovers', on: 'raised', onPress: () => on.openHandover(ho.id) }),
        keycap(k, { id: 'session-launch', key: 'w', label: 'new tab', on: 'raised', onPress: press => on.launch(ho, press.surface) }),
        keycap(k, { id: 'session-summary', key: 'i', label: 'decisions·blockers', on: 'raised', selected: v.summaryOpen, onPress: () => on.toggleSummary(ho.id, !v.summaryOpen) }),
      ], 2).el,
    ]),
  )
  if (v.summaryOpen) {
    const sum = v.summary
    if (sum === undefined) rows.push(under(k, [line(k, 'loading decisions and blockers…', t.fg.subtle, room)]))
    else if (sum.unavailable === true) rows.push(under(k, [line(k, 'Decisions and blockers unavailable: i asks again', t.fg.subtle, room)]))
    else {
      for (const [word, items] of [['decisions', sum.decisions], ['blockers', sum.blockers]] as const) {
        rows.push(under(k, [label(k, word).el, ...(items.length === 0 ? [line(k, 'none', t.fg.subtle, room)] : items.map(item => line(k, `· ${oneLine(item)}`, t.fg.default, room)))]))
      }
    }
  }
  return rows
}

function sessionSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - LABEL_W
  const where = [
    v.resumedFrom === '' ? 'not resumed from a handover' : `resumed from ${v.resumedFrom}`,
    v.burst === null ? '' : `part of the ${v.burst.at} burst (${v.burst.others} others open)`,
  ]
    .filter(Boolean)
    .join(' · ')
  const s = v.snapshot
  const live = s !== null && s.reachable
  const n = (count: number): string => (live ? `${count}` : '–')
  const counts: Piece[] = [
    keycap(k, { id: 'session-review', key: '1', label: `${n(s?.queueTotal ?? 0)}${s?.queueCapped === true ? '+' : ''} waiting on you`, on: 'raised', faded: !live || s.queueTotal === 0, onPress: () => on.open('review') }),
    keycap(k, { id: 'session-handovers', key: '2', label: `${n(s?.handoversTotal ?? 0)} handovers`, on: 'raised', faded: !live || s.handoversTotal === 0, onPress: () => on.open('handovers') }),
  ]
  if (v.restore !== null) counts.push(keycap(k, { id: 'session-restore', key: '4', label: `${v.restore} unfinished · restore`, on: 'raised', onPress: () => on.open('restore') }))
  return [head(k, 'session', [line(k, where, v.resumedFrom === '' && v.burst === null ? t.fg.subtle : t.fg.default, room)]), row(k, counts, 3).el]
}

/** The Session tab's panel (spec §7): TASK, HANDOVER and SESSION between dashed rules, the health at the top right. */
export function sessionTree(k: Kit, v: SessionView, on: SessionHandlers, width: number): RenderNode {
  const inner = Math.max(LABEL_W + 8, width - 4)
  const s = v.snapshot
  const health =
    v.connecting && (s === null || !s.reachable)
      ? { kind: 'info' as const, word: 'connecting' }
      : s === null
        ? { kind: 'info' as const, word: 'loading' }
        : !s.reachable
          ? { kind: 'critical' as const, word: 'offline' }
          : v.demo
            ? { kind: 'info' as const, word: 'demo' }
            : { kind: 'success' as const, word: 'tm ok' }
  return panel(k, { title: panelTitle('session'), health, width }, [
    ...taskSection(k, v, on, inner),
    rule(k, inner),
    ...handoverSection(k, v, on, inner),
    rule(k, inner),
    ...sessionSection(k, v, on, inner),
  ])
}
