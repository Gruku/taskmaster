// User intent: how taskmaster-tui looks — the band, the review card and the handovers list drawn from plain view data with
// Reality Reprojection pieces from $.rr; Buttons are drawn here because their press handlers must stay in this plugin.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { Elements, RenderChildren, RenderElement, RenderNode, RenderSurface, UiPressArgument } from 'claude-code'

import type { TmBandMode, TmCursor, TmHandover, TmQueueItem, TmSnapshot, TmTaskDetail } from '../types'
import {
  ageLabel,
  type BandModel,
  bandRows,
  cardMode,
  cardPosition,
  dateOf,
  metaLine,
  oneLine,
  PRIORITY_GLYPH,
  PRIORITY_TONE,
  PRIORITY_WORD,
  reviewQueue,
  truncate,
} from './model'
import type { Rr, RrNode, RrTokens, RrTone, RrTreatment } from './rr'

export type Ui = Pick<Elements['terminal'], 'Box' | 'Text' | 'Button' | 'Input'>

const node = (n: RrNode): RenderNode => n as unknown as RenderNode

/** A drawn piece and the cells it takes across, so a row can be fitted to the room before it is laid out. */
type Piece = { readonly el: RenderNode; readonly width: number }

// Widths of $.rr pieces, from what they draw: a signal is `glyph word`, a state chip ` glyph text ` (signature: no glyph),
// a keycap ` key `.
const signalWidth = (word: string): number => 2 + word.length
const stateChipWidth = (text: string, tone: RrTone): number => text.length + (tone === 'signature' ? 2 : 4)

/** Keeps the pieces in order while they fit `width` with `gap` between them; the first piece always stays. */
function fit(pieces: readonly (Piece | null)[], width: number, gap: number): RenderNode[] {
  const kept: RenderNode[] = []
  let used = 0
  for (const piece of pieces) {
    if (piece === null) continue
    const need = kept.length === 0 ? piece.width : used + gap + piece.width
    if (kept.length > 0 && need > width) continue
    kept.push(piece.el)
    used = need
  }
  return kept
}

type ChipSpec = {
  readonly id: string
  readonly label: string
  readonly treatment: RrTreatment
  readonly tone: RrTone
  readonly onPress: (e: UiPressArgument) => void
  readonly hotkey?: string
  readonly autoFocus?: boolean
}

/** RR's button recipe: the treatment's keyed wrapper Box around ONE plain Button, so the whole chip presses (spec §5.4). */
async function chip(ui: Ui, rr: Rr, c: ChipSpec): Promise<Piece> {
  const { Box, Button } = ui
  const [wrap, press] = await Promise.all([
    rr.button({ treatment: c.treatment, tone: c.tone }),
    rr.buttonProps(c.hotkey === undefined ? {} : { key: c.hotkey }),
  ])
  const focus = c.autoFocus === true ? { autoFocus: true as const } : {}
  const el = (
    <Box key={`${c.id}-box`} {...wrap}>
      <Button key={c.id} {...press} {...focus} label={c.label} onPress={c.onPress} />
    </Box>
  )
  const label = press.hotkey === undefined ? c.label.length : press.hotkey.length + 2 + c.label.length
  return { el, width: label + 2 * (wrap.paddingX ?? 0) + (wrap.borderStyle === undefined ? 0 : 2) }
}

/** A row of chips and states: top-aligned so an outlined primary never stretches its neighbours to three rows. */
function chipRow(ui: Ui, children: readonly RenderNode[]): RenderNode {
  const { Box } = ui
  return (
    <Box flexDirection="row" columnGap={1} alignItems="flex-start">
      {children}
    </Box>
  )
}

/** The legend for the one key with no button of its own. */
async function escLegend(ui: Ui, rr: Rr, t: RrTokens): Promise<Piece> {
  const { Box, Text } = ui
  return {
    el: (
      <Box flexDirection="row" columnGap={1}>
        {node(await rr.keycap({ key: 'Esc', tone: 'signature' }))}
        <Text color={t.fg.subtle}>close</Text>
      </Box>
    ),
    width: 5 + 1 + 5,
  }
}

function cardBadge(t: RrTokens, item: TmQueueItem): { glyph: string; word: string; color: string } {
  if (item.kind === 'task') {
    const tone = PRIORITY_TONE[item.priority]
    return { glyph: PRIORITY_GLYPH[item.priority], word: PRIORITY_WORD[item.priority], color: tone === 'quiet' ? t.fg.subtle : t.tone[tone] }
  }
  if (item.kind === 'issue') {
    return item.severity === 'P0'
      ? { glyph: '◆', word: `${item.severity} issue`, color: t.tone.critical }
      : { glyph: '▲', word: `${item.severity} issue`, color: t.tone.warning }
  }
  return { glyph: 'ⓘ', word: 'Decision', color: t.tone.info }
}

export type BandHandlers = {
  openReview: () => void
  openHandovers: () => void
  askDone: (id: string) => void
  confirmDone: (id: string) => void
  cancel: () => void
  sendBack: (id: string) => void
}

const MIN_TEXT = 12

export async function bandTree(
  ui: Ui,
  rr: Rr,
  m: BandModel,
  mode: TmBandMode,
  width: number,
  maxRows: number,
  on: BandHandlers,
): Promise<RenderElement> {
  const { Box, Text } = ui
  const t = await rr.tokens()
  const keep = bandRows(m, maxRows)
  const rows: RenderNode[] = []
  const task = m.task
  if (task !== null && keep.includes('task')) {
    // Gives way left to right: the label, then (review form) the id, so the check to do keeps at least MIN_TEXT cells of
    // its own after "waiting on you: ".
    const word = task.review ? 'review' : 'task'
    const ask = 'waiting on you: '
    const tail = task.inferred ? 2 + 'inferred'.length : 0
    const need = (task.review ? ask.length : 0) + MIN_TEXT
    let showLabel = true
    let showId = true
    const used = () => (showLabel ? word.length + 2 : 0) + (showId ? task.id.length + 2 : 0) + tail
    if (used() + need > width) showLabel = false
    if (task.review && used() + need > width) showId = false
    const room = Math.max(0, width - used())
    let body: RenderChildren = null
    if (task.review) {
      body = (
        <Text color={t.fg.default} wrap="truncate-end">
          {truncate(`${ask}${oneLine(task.humanAction)}`, room)}
        </Text>
      )
    } else {
      const showMeta = task.meta !== '' && room >= MIN_TEXT + 2 + task.meta.length
      const title = truncate(oneLine(task.title), showMeta ? room - 2 - task.meta.length : room)
      body =
        title === '' && !showMeta ? null : (
          <Box flexDirection="row" columnGap={2}>
            {title !== '' ? (
              <Text color={task.inferred ? t.fg.subtle : t.fg.default} wrap="truncate-end">
                {title}
              </Text>
            ) : null}
            {showMeta ? <Text color={t.fg.subtle}>{task.meta}</Text> : null}
          </Box>
        )
    }
    rows.push(
      <Box flexDirection="row" columnGap={2}>
        {showLabel ? node(await rr.label({ text: word })) : null}
        {showId ? (
          <Text color={task.inferred ? t.fg.subtle : t.fg.bold} bold={!task.inferred}>
            {truncate(task.id, width - tail)}
          </Text>
        ) : null}
        {body}
        {task.inferred ? <Text color={t.fg.subtle}>inferred</Text> : null}
      </Box>,
    )
  }
  if (task !== null && keep.includes('stage')) {
    if (task.review && mode.confirmingId === task.id) {
      const [yes, no] = await Promise.all([
        chip(ui, rr, { id: 'band-yes', hotkey: 'y', label: 'yes', treatment: 'chip', tone: 'success', onPress: () => on.confirmDone(mode.confirmingId) }),
        chip(ui, rr, { id: 'band-no', hotkey: 'n', label: 'no', treatment: 'chip', tone: 'signature', autoFocus: true, onPress: on.cancel }),
      ])
      const ask = stateChipWidth('confirm done?', 'warning') + yes.width + no.width + 2 <= width ? 'confirm done?' : 'done?'
      rows.push(chipRow(ui, [node(await rr.chip({ text: ask, tone: 'warning', strength: 24 })), yes.el, no.el]))
    } else if (task.review) {
      const [done, back] = await Promise.all([
        chip(ui, rr, { id: 'band-done', hotkey: 'd', label: 'done', treatment: 'chip', tone: 'success', onPress: () => on.askDone(task.id) }),
        chip(ui, rr, { id: 'band-back', hotkey: 'a', label: 'back to agent', treatment: 'chip', tone: 'warning', onPress: () => on.sendBack(task.id) }),
      ])
      let refused: Piece | null = null
      let reason: Piece | null = null
      if (mode.refusal !== '') {
        refused = { el: node(await rr.chip({ text: 'refused', tone: 'critical', strength: 24 })), width: stateChipWidth('refused', 'critical') }
        const room = width - (done.width + back.width + refused.width + 3)
        if (room >= MIN_TEXT) {
          const text = truncate(oneLine(mode.refusal), room)
          reason = {
            el: (
              <Text color={t.fg.default} wrap="truncate-end">
                {text}
              </Text>
            ),
            width: text.length,
          }
        }
      }
      rows.push(chipRow(ui, fit([done, back, refused, reason], width, 1)))
    } else {
      const pad = width >= 6 + MIN_TEXT ? 6 : 0
      rows.push(
        <Box flexDirection="row" paddingLeft={pad}>
          <Text color={t.fg.subtle} wrap="truncate-end">
            {truncate(task.stage, width - pad)}
          </Text>
        </Box>,
      )
    }
  }
  if (m.needsYou > 0 && keep.includes('needs')) {
    const [review, handovers] = await Promise.all([
      chip(ui, rr, { id: 'band-review', hotkey: '1', label: 'review', treatment: 'chip', tone: 'signature', onPress: on.openReview }),
      chip(ui, rr, { id: 'band-handovers', hotkey: '2', label: 'handovers', treatment: 'chip', tone: 'signature', onPress: on.openHandovers }),
    ])
    // The count says the most in the least room: its words shorten before the review chip goes.
    const words = [`${m.needsYou} waiting on you`, `${m.needsYou} waiting`, `${m.needsYou}`]
    const word = words.find(w => signalWidth(w) + 1 + review.width <= width) ?? truncate(words[0] ?? '', Math.max(1, width - 2))
    const signal = { el: node(await rr.signal({ kind: 'warning', word })), width: signalWidth(word) }
    rows.push(chipRow(ui, fit([signal, review, handovers], width, 1)))
  }
  return <Box flexDirection="column">{rows}</Box>
}

/** A pane's root: RR's page ground painted by the pane itself (the host's pane background is not RR's page). */
async function paneRoot(ui: Ui, rr: Rr, children: readonly RenderChildren[]): Promise<RenderElement> {
  const { Box } = ui
  const ground = await rr.surfaceProps({ level: 'page' })
  return (
    <Box {...ground} flexGrow={1}>
      {children}
    </Box>
  )
}

/** What a pane shows before it has data, or when Taskmaster cannot be reached. */
async function paneStatus(ui: Ui, rr: Rr, t: RrTokens, title: RenderNode, s: TmSnapshot | null): Promise<RenderElement> {
  const { Text } = ui
  if (s === null) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>Loading…</Text>])
  return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'critical', word: 'Taskmaster unreachable', detail: s.reason }))])
}

export type ReviewView = {
  snapshot: TmSnapshot | null
  cursor: TmCursor
  details: Readonly<Record<string, TmTaskDetail>>
  now: number
}

export type ReviewHandlers = {
  askDone: (id: string) => void
  confirmDone: (id: string) => void
  cancel: () => void
  askNote: (id: string) => void
  sendBack: (id: string, note: string) => void
  skip: (id: string) => void
  fill: (text: string) => void
}

export async function reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderElement> {
  const { Box, Text, Input } = ui
  const t = await rr.tokens()
  const title = node(await rr.label({ text: 'review' }))
  if (v.snapshot === null || !v.snapshot.reachable) return paneStatus(ui, rr, t, title, v.snapshot)
  const card = cardPosition(reviewQueue(v.snapshot, v.cursor, v.details), v.cursor, v.snapshot.queueTotal)
  const item = card.item
  if (item === null) {
    const tally = `${v.cursor.done.length} done · ${v.cursor.skipped.length} skipped this pass`
    return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'success', word: 'Queue clear', detail: tally }))])
  }
  const inner = Math.max(10, width - 2)
  const rule = node(await rr.rule({ width: inner }))
  const age = ageLabel(item.timestamp, v.now)
  const badge = cardBadge(t, item)
  const detail = item.kind === 'task' ? v.details[item.id] : undefined
  const meta =
    item.kind === 'task' ? (detail ? metaLine(detail, true) : 'loading details…') : item.kind === 'issue' ? `${item.severity} issue · open` : 'open decision'
  const body =
    item.kind === 'task'
      ? item.humanAction || detail?.humanAction || '(no human_action recorded)'
      : item.kind === 'issue'
        ? 'An open P0/P1 issue: look at it with the agent.'
        : 'An open decision: resolve it with the decision skill (taskmaster:decision).'
  const openText = item.kind === 'decision' ? `Resolve decision ${item.id} with the taskmaster:decision skill` : `Look at ${item.id}`
  const refusal =
    v.cursor.refusal !== '' && v.cursor.currentId === item.id ? (
      <Box flexDirection="column">
        {node(await rr.chip({ text: 'refused', tone: 'critical', strength: 24 }))}
        <Text color={t.fg.default} wrap="wrap">
          {v.cursor.refusal}
        </Text>
      </Box>
    ) : null
  const mode = item.kind === 'task' ? cardMode(v.cursor, item) : 'card'
  const esc = await escLegend(ui, rr, t)
  let actions: RenderNode[]
  if (mode === 'confirm') {
    const [yes, no] = await Promise.all([
      chip(ui, rr, { id: 'confirm-yes', hotkey: 'y', label: 'yes', treatment: 'chip', tone: 'success', onPress: () => on.confirmDone(item.id) }),
      chip(ui, rr, { id: 'confirm-no', hotkey: 'n', label: 'no', treatment: 'chip', tone: 'signature', autoFocus: true, onPress: on.cancel }),
    ])
    const ask = { el: node(await rr.chip({ text: 'confirm done?', tone: 'warning', strength: 24 })), width: stateChipWidth('confirm done?', 'warning') }
    actions = [chipRow(ui, fit([ask, yes, no, esc], inner, 1))]
  } else if (mode === 'note') {
    const cancel = await chip(ui, rr, { id: 'note-cancel', label: 'cancel', treatment: 'chip', tone: 'signature', onPress: on.cancel })
    actions = [
      <Input
        key="note"
        label="note for the agent"
        placeholder="what should change"
        submitLabel="send back"
        autoFocus
        onSubmit={value => on.sendBack(item.id, value)}
      />,
      chipRow(ui, fit([cancel, esc], inner, 1)),
    ]
  } else {
    const pieces = await Promise.all([
      item.kind === 'task'
        ? chip(ui, rr, { id: 'done', hotkey: 'd', label: 'done', treatment: 'outline', tone: 'success', onPress: () => on.askDone(item.id) })
        : null,
      item.kind === 'task'
        ? chip(ui, rr, { id: 'back', hotkey: 'a', label: 'back to agent', treatment: 'chip', tone: 'warning', onPress: () => on.askNote(item.id) })
        : null,
      chip(ui, rr, { id: 'skip', hotkey: 's', label: 'skip', treatment: 'chip', tone: 'signature', onPress: () => on.skip(item.id) }),
      chip(ui, rr, { id: 'open', hotkey: 'o', label: 'open in prompt', treatment: 'chip', tone: 'signature', onPress: () => on.fill(openText) }),
    ])
    const buttons = pieces.filter((p): p is Piece => p !== null)
    const together = buttons.reduce((sum, p) => sum + p.width, 0) + buttons.length + esc.width
    // Esc close joins the buttons' row when it fits there; otherwise it takes its own row beneath them.
    actions = together <= inner ? [chipRow(ui, [...buttons.map(p => p.el), esc.el])] : [chipRow(ui, buttons.map(p => p.el)), esc.el]
  }
  return paneRoot(ui, rr, [
    <Box flexDirection="row" columnGap={2}>
      {title}
      <Text color={t.fg.subtle}>{`${card.n} of ${card.total} · ${v.cursor.done.length} done this pass`}</Text>
    </Box>,
    <Box flexDirection="row" columnGap={2}>
      <Text color={t.fg.bold} bold>
        {item.id}
      </Text>
      <Text color={badge.color}>{`${badge.glyph} ${badge.word}`}</Text>
      {age !== '' ? <Text color={t.fg.subtle}>{`· ${age}`}</Text> : null}
    </Box>,
    <Text color={t.fg.default} wrap="wrap">
      {item.title}
    </Text>,
    <Text color={t.fg.subtle}>{meta}</Text>,
    rule,
    <Text color={t.fg.default} wrap="wrap">
      {body}
    </Text>,
    rule,
    refusal,
    ...actions,
  ])
}

export type HandoversView = { snapshot: TmSnapshot | null; pick: string }

export type HandoverHandlers = {
  pick: (id: string) => void
  copy: (h: TmHandover, surface: RenderSurface) => void
  resume: (h: TmHandover) => void
}

export async function handoversPaneTree(ui: Ui, rr: Rr, v: HandoversView, on: HandoverHandlers, width: number): Promise<RenderElement> {
  const { Box, Text, Button } = ui
  const t = await rr.tokens()
  const title = node(await rr.label({ text: 'handovers' }))
  if (v.snapshot === null || !v.snapshot.reachable) return paneStatus(ui, rr, t, title, v.snapshot)
  const list = v.snapshot.handovers
  const picked = list.find(entry => entry.id === v.pick) ?? list[0]
  if (picked === undefined) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>No open handovers.</Text>])
  const [rowPress, copy, resume, esc] = await Promise.all([
    rr.buttonProps({}),
    chip(ui, rr, { id: 'copy', hotkey: 'c', label: 'copy', treatment: 'chip', tone: 'signature', onPress: press => on.copy(picked, press.surface) }),
    chip(ui, rr, { id: 'resume', hotkey: 'r', label: 'resume', treatment: 'chip', tone: 'signature', onPress: () => on.resume(picked) }),
    escLegend(ui, rr, t),
  ])
  return paneRoot(ui, rr, [
    title,
    ...list.map(entry => (
      <Box key={`ho:${entry.id}-box`} flexDirection="row" columnGap={1}>
        <Text color={entry.id === picked.id ? t.signatureText : t.fg.subtle}>{entry.id === picked.id ? '→' : ' '}</Text>
        <Button
          key={`ho:${entry.id}`}
          {...rowPress}
          label={truncate(`${dateOf(entry.created)}  ${oneLine(entry.tldr)}`, Math.max(10, width - 4))}
          onPress={() => on.pick(entry.id)}
        />
      </Box>
    )),
    <Text color={t.fg.default} wrap="wrap">
      {`Next: ${oneLine(picked.nextAction) || '—'}`}
    </Text>,
    <Text color={t.fg.subtle}>{`${list.length} of ${v.snapshot.handoversTotal} · superseded hidden`}</Text>,
    chipRow(ui, fit([copy, resume, esc], Math.max(10, width - 2), 1)),
  ])
}
