// User intent: how taskmaster-tui looks — the band, the review card and the handovers list drawn from plain view data with
// Reality Reprojection pieces from $.rr; Buttons are drawn here because their press handlers must stay in this plugin.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { Elements, RenderChildren, RenderElement, RenderNode, RenderSurface, UiPressArgument } from 'claude-code'

import type { TmBandMode, TmCursor, TmHandover, TmHandoverSummary, TmQueueItem, TmSnapshot, TmTaskDetail } from '../types'
import {
  ageLabel,
  type BandModel,
  bandRows,
  cardMode,
  cardPosition,
  dateOf,
  gateSignal,
  handoverRefs,
  oneLine,
  PRIORITY_GLYPH,
  PRIORITY_TONE,
  PRIORITY_WORD,
  queueDots,
  reviewQueue,
  splitCheck,
  truncate,
  wrapText,
} from './model'
import type { Rr, RrButtonProps, RrNode, RrTokens, RrTone, RrTreatment } from './rr'

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
  /** 24: the strong chip, a card's one primary (`done`). */
  readonly strength?: 12 | 24
}

/** RR's button recipe: the treatment's keyed wrapper Box around ONE plain Button, so the whole chip presses (spec §5.4). */
async function chip(ui: Ui, rr: Rr, c: ChipSpec): Promise<Piece> {
  const { Box, Button } = ui
  const [wrap, press] = await Promise.all([
    rr.button(c.strength === 24 ? { treatment: c.treatment, tone: c.tone, strength: 24 } : { treatment: c.treatment, tone: c.tone }),
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

/**
 * `y: yes` and `n: no` as ONE piece: no fitting may keep `y` without `n`, the focused safe default (Enter cancels).
 * Keys are `<prefix>-yes` / `<prefix>-no`.
 */
async function confirmPair(ui: Ui, rr: Rr, prefix: string, onYes: () => void, onNo: () => void): Promise<Piece> {
  const { Box } = ui
  const [yes, no] = await Promise.all([
    chip(ui, rr, { id: `${prefix}-yes`, hotkey: 'y', label: 'yes', treatment: 'chip', tone: 'success', onPress: onYes }),
    chip(ui, rr, { id: `${prefix}-no`, hotkey: 'n', label: 'no', treatment: 'chip', tone: 'signature', autoFocus: true, onPress: onNo }),
  ])
  return {
    el: (
      <Box flexDirection="row" columnGap={1}>
        {yes.el}
        {no.el}
      </Box>
    ),
    width: yes.width + 1 + no.width,
  }
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

/** The one key with no button of its own, as plain dim text (decided live 2026-10-06; no keycap). */
function escHint(ui: Ui, t: RrTokens): Piece {
  const { Text } = ui
  return { el: <Text color={t.fg.subtle}>esc close</Text>, width: 'esc close'.length }
}

/**
 * Lays chips out in rows of `width`, wrapping rather than dropping; to stay within two rows it may drop up to `optional`
 * trailing chips, the last first.
 */
function chipRows(ui: Ui, pieces: readonly Piece[], width: number, optional: number): RenderNode[] {
  const pack = (all: readonly Piece[]): Piece[][] => {
    const rows: Piece[][] = []
    let used = 0
    for (const piece of all) {
      const row = rows[rows.length - 1]
      if (row !== undefined && used + 1 + piece.width <= width) {
        row.push(piece)
        used += 1 + piece.width
      } else {
        rows.push([piece])
        used = piece.width
      }
    }
    return rows
  }
  let kept = [...pieces]
  let rows = pack(kept)
  for (let dropped = 0; dropped < optional && rows.length > 2; dropped += 1) {
    kept = kept.slice(0, -1)
    rows = pack(kept)
  }
  return rows.map(row => chipRow(ui, row.map(p => p.el)))
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
  /** `3` on the handover-written notice: copy its block. */
  copyNotice: (surface: RenderSurface) => void
}

const MIN_TEXT = 12
const MIN_ID = 8

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
      const pair = await confirmPair(ui, rr, 'band', () => on.confirmDone(mode.confirmingId), on.cancel)
      // The question always names the task it signs off: `done <id>?`, then `<id>?`; the id shortens to MIN_ID, never away.
      const room = width - pair.width - 1 - stateChipWidth('', 'warning')
      const least = Math.min(MIN_ID, task.id.length)
      const prefix = room - 'done '.length - 1 >= least ? 'done ' : ''
      const ask = `${prefix}${truncate(task.id, Math.max(least, room - prefix.length - 1))}?`
      const question = node(await rr.chip({ text: ask, tone: 'warning', strength: 24 }))
      // Too narrow for both: the pair goes first, so what the edge clips is the question, never `n`.
      const fits = stateChipWidth(ask, 'warning') + 1 + pair.width <= width
      rows.push(chipRow(ui, fits ? [question, pair.el] : [pair.el, question]))
    } else if (task.review) {
      const [done, back] = await Promise.all([
        chip(ui, rr, { id: 'band-done', hotkey: 'd', label: 'done', treatment: 'chip', tone: 'success', strength: 24, onPress: () => on.askDone(task.id) }),
        chip(ui, rr, { id: 'band-back', hotkey: 'a', label: 'back to agent', treatment: 'chip', tone: 'warning', onPress: () => on.sendBack(task.id) }),
      ])
      let refused: Piece | null = null
      let reason: Piece | null = null
      if (mode.refusal !== '' && mode.refusalId === task.id) {
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
  if (m.notice !== undefined && keep.includes('handover')) {
    // `HANDOVER  <tldr>  3: copy  2: handovers`: `2` only while the needs-you row (which has its own) is not drawn. Gives
    // way left to right after the tldr shrinks to MIN_TEXT: the handovers chip, then the label; `3: copy` always stays.
    const copy = await chip(ui, rr, { id: 'band-copy', hotkey: '3', label: 'copy', treatment: 'chip', tone: 'signature', onPress: press => on.copyNotice(press.surface) })
    const toPane = keep.includes('needs')
      ? null
      : await chip(ui, rr, { id: 'notice-handovers', hotkey: '2', label: 'handovers', treatment: 'chip', tone: 'signature', onPress: on.openHandovers })
    const label = 'handover'
    let showPane = toPane !== null
    let showLabel = true
    const used = () => (showLabel ? label.length + 2 : 0) + copy.width + (showPane && toPane !== null ? 1 + toPane.width : 0)
    if (showPane && width - used() - 2 < MIN_TEXT) showPane = false
    if (width - used() - 2 < MIN_TEXT) showLabel = false
    const tldr = truncate(oneLine(m.notice), width - used() - 2)
    rows.push(
      <Box flexDirection="row" columnGap={2}>
        {showLabel ? node(await rr.label({ text: label })) : null}
        {tldr !== '' ? (
          <Text color={t.fg.default} wrap="truncate-end">
            {tldr}
          </Text>
        ) : null}
        {chipRow(ui, showPane && toPane !== null ? [copy.el, toPane.el] : [copy.el])}
      </Box>,
    )
  }
  if (m.needsYou > 0 && keep.includes('needs')) {
    const [review, handovers] = await Promise.all([
      chip(ui, rr, { id: 'band-review', hotkey: '1', label: 'review', treatment: 'chip', tone: 'signature', onPress: on.openReview }),
      chip(ui, rr, { id: 'band-handovers', hotkey: '2', label: 'handovers', treatment: 'chip', tone: 'signature', onPress: on.openHandovers }),
    ])
    // The count says the most in the least room: its words shorten before the review chip goes.
    const count = m.needsCapped === true ? `${m.needsYou}+` : `${m.needsYou}`
    const words = [`${count} waiting on you`, `${count} waiting`, count]
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
  /** The ticked check items of a task (local UI state, never Taskmaster's). */
  ticks: (taskId: string) => Promise<readonly string[]>
  detailsOpen: boolean
}

export type ReviewHandlers = {
  askDone: (id: string) => void
  confirmDone: (id: string) => void
  cancel: () => void
  askNote: (id: string) => void
  sendBack: (id: string, note: string) => void
  skip: (id: string) => void
  fill: (text: string) => void
  toggleTick: (id: string, item: string) => void
  toggleDetails: () => void
  openViewer: (id: string) => void
  copyCheck: (id: string, text: string, surface: RenderSurface) => void
}

/** `done <id>?` in `room` cells of chip text: the id shortens to MIN_ID, never away; the word goes before it does. */
function namedAsk(id: string, room: number): string {
  const least = Math.min(MIN_ID, id.length)
  const prefix = room - 'done '.length - 1 >= least ? 'done ' : ''
  return `${prefix}${truncate(id, Math.max(least, room - prefix.length - 1))}?`
}

/** The strip above the card: REVIEW, the queue as dots, position and tally, and `esc close` at the right edge. */
async function headerStrip(ui: Ui, rr: Rr, t: RrTokens, n: number, total: number, done: number, width: number): Promise<RenderNode> {
  const { Box, Text } = ui
  const title = { el: node(await rr.label({ text: 'review' })), width: 'review'.length }
  const esc = escHint(ui, t)
  const dots = queueDots(n, total)
  const at = [`${n} of ${total} · ${done} done this pass`, `${n} of ${total} · ${done} done`, `${n}/${total}`]
  // Gives way right to left: the dots, then esc close, then the tally shortens.
  const room = (withDots: boolean, withEsc: boolean) =>
    width - title.width - 2 - (withDots ? dots.length + 2 : 0) - (withEsc ? esc.width + 2 : 0)
  const showDots = (at[0] ?? '').length <= room(true, true)
  const showEsc = at.some(a => a.length <= room(false, true))
  const pos = at.find(a => a.length <= room(showDots, showEsc)) ?? truncate(at[at.length - 1] ?? '', room(false, false))
  return (
    <Box flexDirection="row">
      <Box flexDirection="row" columnGap={2}>
        {title.el}
        {showDots ? <Text color={t.signatureText}>{dots}</Text> : null}
        <Text color={t.fg.subtle}>{pos}</Text>
      </Box>
      <Box flexGrow={1} />
      {showEsc ? (
        <Box flexDirection="row" paddingLeft={2}>
          {esc.el}
        </Box>
      ) : null}
    </Box>
  )
}

/** Wrapped lines in one colour: a card never draws a line wider than its room. */
function lines(ui: Ui, color: string, text: string, width: number, indent = 0): RenderNode[] {
  const { Text, Box } = ui
  return wrapText(text, width - indent).map(line =>
    indent === 0 ? (
      <Text color={color}>{line}</Text>
    ) : (
      <Box paddingLeft={indent}>
        <Text color={color}>{line}</Text>
      </Box>
    ),
  )
}

export async function reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderElement> {
  const { Box, Text, Button, Input } = ui
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
  const raised = await rr.surfaceProps({ level: 'raised' })
  const room = Math.max(8, inner - 2 * (raised.paddingX ?? 0) - 2)
  const detail = item.kind === 'task' ? v.details[item.id] : undefined
  const isTask = item.kind === 'task'
  const action = item.kind === 'task' ? item.humanAction || detail?.humanAction || '' : ''
  const check = splitCheck(action)
  const ticked = isTask ? await v.ticks(item.id) : []
  const mode = isTask ? cardMode(v.cursor, item) : 'card'

  // Header line: priority glyph + word, id, age, lane, gate; it gives way from the right, and the word goes before the id
  // shortens.
  const badge = cardBadge(t, item)
  const age = ageLabel(item.timestamp, v.now)
  const fullBadge = `${badge.glyph} ${badge.word.toUpperCase()}`
  const badgeText = fullBadge.length + 1 + item.id.length <= room ? fullBadge : badge.glyph
  const idText = truncate(item.id, Math.max(Math.min(MIN_ID, item.id.length), room - badgeText.length - 1))
  const gate = detail ? gateSignal(detail.gateState) : null
  const lane = detail ? detail.lane.toUpperCase() || 'NO LANE' : 'loading…'
  const headPieces: (Piece | null)[] = [
    { el: <Text color={badge.color}>{badgeText}</Text>, width: badgeText.length },
    {
      el: (
        <Text color={t.fg.bold} bold>
          {idText}
        </Text>
      ),
      width: idText.length,
    },
    age !== '' ? { el: <Text color={t.fg.subtle}>{`· ${age}`}</Text>, width: age.length + 2 } : null,
    isTask ? { el: <Text color={t.fg.subtle}>{`· ${lane}`}</Text>, width: lane.length + 2 } : null,
    gate !== null
      ? {
          el: (
            <Box flexDirection="row" columnGap={1}>
              <Text color={t.fg.subtle}>·</Text>
              {node(await rr.signal({ kind: gate.kind, word: gate.word }))}
            </Box>
          ),
          width: 2 + signalWidth(gate.word),
        }
      : null,
  ]
  const head = (
    <Box flexDirection="row" columnGap={1}>
      {fit(headPieces, room, 1)}
    </Box>
  )

  // The check: a section label with its dim detail, then one tickable item per line (digit keys for the first nine).
  const body: RenderNode[] = []
  if (isTask && check.items.length > 0) {
    if (check.label !== '') {
      const label = truncate(check.label, room)
      const left = room - label.length - 2
      body.push(
        <Box flexDirection="row" columnGap={2}>
          {node(await rr.label({ text: label }))}
          {check.detail !== '' && left >= MIN_TEXT ? <Text color={t.fg.subtle}>{truncate(check.detail, left)}</Text> : null}
        </Box>,
      )
      if (check.detail !== '' && left < MIN_TEXT) body.push(...lines(ui, t.fg.subtle, check.detail, room))
    } else if (check.detail !== '') {
      body.push(...lines(ui, t.fg.subtle, check.detail, room))
    }
    // Ticks press only in card mode. While the note Input or the confirm row is up, the items are plain text (marks still
    // shown, same indent): a digit typed into the note, or pressed mid-confirm, can never tick or change the count.
    const armed = mode === 'card'
    const rowProps = await Promise.all(check.items.map((_, i) => rr.buttonProps(i < 9 ? { key: String(i + 1) } : {})))
    check.items.forEach((entry, i) => {
      const press = rowProps[i]
      if (press === undefined) return
      const keyCells = press.hotkey === undefined ? 0 : press.hotkey.length + 2
      const lead = keyCells + 2
      const wrapped = wrapText(entry, room - lead)
      const mark = ticked.includes(entry) ? '☑' : '☐'
      const first = `${mark} ${wrapped[0] ?? ''}`
      body.push(
        <Box key={`tick-${i}-box`} flexDirection="column">
          {armed ? (
            <Button key={`tick-${i}`} {...press} label={first} onPress={() => on.toggleTick(item.id, entry)} />
          ) : (
            <Box paddingLeft={keyCells}>
              <Text color={t.fg.default}>{first}</Text>
            </Box>
          )}
          {wrapped.slice(1).map(line => (
            <Box paddingLeft={lead}>
              <Text color={t.fg.default}>{line}</Text>
            </Box>
          ))}
        </Box>,
      )
    })
  } else {
    const text = isTask
      ? '(no human_action recorded)'
      : item.kind === 'issue'
        ? 'An open P0/P1 issue: look at it with the agent.'
        : 'An open decision: resolve it with the decision skill (taskmaster:decision).'
    body.push(...lines(ui, isTask ? t.fg.subtle : t.fg.default, text, room))
  }

  // Details on demand: branch / PR, notes, links.
  const more: RenderNode[] = []
  if (isTask) {
    const press = await rr.buttonProps({ key: 'i' })
    const toggle = `${v.detailsOpen ? '▾' : '▸'} details`
    more.push(
      <Box key="details-box" flexDirection="row" marginTop={1}>
        {mode === 'card' ? (
          <Button key="details" {...press} label={toggle} onPress={on.toggleDetails} />
        ) : (
          <Box paddingLeft={3}>
            <Text color={t.fg.subtle}>{toggle}</Text>
          </Box>
        )}
      </Box>,
    )
    if (v.detailsOpen) {
      if (detail === undefined) more.push(<Text color={t.fg.subtle}>loading details…</Text>)
      else {
        const facts = [
          `branch: ${detail.branch || 'no branch'}`,
          ...(detail.pr ? [`PR: ${detail.pr}`] : []),
          ...(detail.notes ?? []).map(note => `note: ${note}`),
          ...(detail.links ?? []).map(link => `link: ${link}`),
        ]
        for (const fact of facts) more.push(...lines(ui, t.fg.subtle, fact, room, 2))
      }
    }
  }

  const refusal: RenderNode[] =
    v.cursor.refusal !== '' && v.cursor.currentId === item.id
      ? [node(await rr.chip({ text: 'refused', tone: 'critical', strength: 24 })), ...lines(ui, t.fg.default, v.cursor.refusal, room)]
      : []

  // Beneath the card: the one action row, or the confirm / note row that replaces it.
  let actions: RenderNode[]
  if (mode === 'confirm') {
    const pair = await confirmPair(ui, rr, 'confirm', () => on.confirmDone(item.id), on.cancel)
    const open = check.items.filter(entry => !ticked.includes(entry)).length
    const n = check.items.length
    const asks =
      open > 0 ? [`${open} of ${n} unchecked — done anyway?`, `${open}/${n} unchecked — done?`, `${open}/${n} unchecked?`] : [namedAsk(item.id, inner - 4)]
    const ask = asks.find(a => stateChipWidth(a, 'warning') <= inner) ?? asks[asks.length - 1] ?? namedAsk(item.id, inner - 4)
    const question = { el: node(await rr.chip({ text: ask, tone: 'warning', strength: 24 })), width: stateChipWidth(ask, 'warning') }
    // One row when the question and the pair fit; otherwise the question above and the pair below: never y without n.
    actions = question.width + 1 + pair.width <= inner ? [chipRow(ui, [question.el, pair.el])] : [question.el, chipRow(ui, [pair.el])]
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
      chipRow(ui, [cancel.el]),
    ]
  } else {
    const openText = item.kind === 'decision' ? `Resolve decision ${item.id} with the taskmaster:decision skill` : `Look at ${item.id}`
    const taskOnly = isTask
      ? await Promise.all([
          chip(ui, rr, { id: 'done', hotkey: 'd', label: 'done', treatment: 'chip', tone: 'success', strength: 24, onPress: () => on.askDone(item.id) }),
          chip(ui, rr, { id: 'back', hotkey: 'a', label: 'back to agent', treatment: 'chip', tone: 'warning', onPress: () => on.askNote(item.id) }),
        ])
      : []
    const always = await Promise.all([
      chip(ui, rr, { id: 'skip', hotkey: 's', label: 'skip', treatment: 'chip', tone: 'signature', onPress: () => on.skip(item.id) }),
      chip(ui, rr, { id: 'open', hotkey: 'o', label: 'open in prompt', treatment: 'chip', tone: 'signature', onPress: () => on.fill(openText) }),
    ])
    const extras = isTask
      ? await Promise.all([
          chip(ui, rr, { id: 'viewer', hotkey: 'v', label: 'viewer', treatment: 'chip', tone: 'signature', onPress: () => on.openViewer(item.id) }),
          chip(ui, rr, {
            id: 'copy',
            hotkey: 'c',
            label: 'copy',
            treatment: 'chip',
            tone: 'signature',
            onPress: press => on.copyCheck(item.id, action, press.surface),
          }),
        ])
      : []
    actions = chipRows(ui, [...taskOnly, ...always, ...extras], inner, extras.length)
  }

  return paneRoot(ui, rr, [
    await headerStrip(ui, rr, t, card.n, card.total, v.cursor.done.length, inner),
    <Box {...raised} borderStyle="round" borderColor={t.border.strong}>
      {head}
      <Text color={t.fg.default}>{truncate(oneLine(item.title), room)}</Text>
      <Box flexDirection="column" marginTop={1}>
        {body}
      </Box>
      {more}
      {refusal}
    </Box>,
    ...actions,
  ])
}

export type HandoversView = {
  snapshot: TmSnapshot | null
  pick: string
  summaries: Readonly<Record<string, TmHandoverSummary>>
  /** The handover whose summary is expanded; it shows only while that handover is the picked one. */
  summaryOpen: string
}

export type HandoverHandlers = {
  pick: (id: string) => void
  copy: (h: TmHandover, surface: RenderSurface) => void
  resume: (h: TmHandover) => void
  toggleSummary: (h: TmHandover, open: boolean) => void
}

/** The card's body indent: the refs, the sections and NEXT start where the head row's text does (after `→ `). */
const CARD_INDENT = 2

/**
 * The picked handover as a card (decided live 2026-10-06): the head row is the whole tldr, wrapped under itself, never cut;
 * then, expanded, the dim refs and the DECISIONS / BLOCKERS sections (read on demand); then NEXT and the `i` toggle.
 */
async function handoverCard(
  ui: Ui,
  rr: Rr,
  t: RrTokens,
  ho: TmHandover,
  open: boolean,
  summary: TmHandoverSummary | undefined,
  room: number,
  press: { row: RrButtonProps; toggle: RrButtonProps },
  on: HandoverHandlers,
): Promise<RenderNode[]> {
  const { Box, Text, Button } = ui
  const body = room - CARD_INDENT
  const prefix = `${dateOf(ho.created)}  `
  const head = wrapText(ho.tldr, room - CARD_INDENT - prefix.length)
  const nodes: RenderNode[] = [
    <Box key={`ho:${ho.id}-box`} flexDirection="column">
      <Box flexDirection="row" columnGap={1}>
        <Text color={t.signatureText}>→</Text>
        <Button key={`ho:${ho.id}`} {...press.row} label={`${prefix}${head[0] ?? ''}`.trimEnd()} onPress={() => on.pick(ho.id)} />
      </Box>
      {head.slice(1).map(line => (
        <Box paddingLeft={CARD_INDENT + prefix.length}>
          <Text color={t.fg.default}>{line}</Text>
        </Box>
      ))}
    </Box>,
  ]
  if (open) {
    const refs = handoverRefs(ho)
    if (refs !== '') nodes.push(...lines(ui, t.fg.subtle, refs, room, CARD_INDENT))
    if (summary === undefined || summary.unavailable === true) {
      // Unavailable: the reader could not get it (refused, unreadable, offline); `i` twice asks again.
      nodes.push(
        <Box flexDirection="column" marginTop={1} paddingLeft={CARD_INDENT}>
          {lines(ui, t.fg.subtle, summary === undefined ? 'loading summary…' : 'summary unavailable', body)}
        </Box>,
      )
    } else {
      // A reader's summary may lack a section: missing reads as empty, and an empty section is left out.
      const listOf = (items: readonly string[] | undefined): readonly string[] => (Array.isArray(items) ? items : [])
      for (const [label, items] of [
        ['decisions', listOf(summary.decisions)],
        ['blockers', listOf(summary.blockers)],
      ] as const) {
        if (items.length === 0) continue
        nodes.push(
          <Box flexDirection="column" marginTop={1} paddingLeft={CARD_INDENT}>
            {node(await rr.label({ text: label }))}
            {items.flatMap(item => lines(ui, t.fg.default, item, body))}
          </Box>,
        )
      }
    }
  }
  const next = oneLine(ho.nextAction) || '—'
  nodes.push(
    <Box flexDirection="row" columnGap={2} paddingLeft={CARD_INDENT} {...(open ? { marginTop: 1 } : {})}>
      {node(await rr.label({ text: 'next' }))}
      <Box flexDirection="column">{lines(ui, t.fg.default, next, body - 'next'.length - 2)}</Box>
    </Box>,
    <Box key="summary-box" flexDirection="row">
      <Button key="summary" {...press.toggle} label={`${open ? '▾' : '▸'} summary`} onPress={() => on.toggleSummary(ho, !open)} />
    </Box>,
  )
  return nodes
}

export async function handoversPaneTree(ui: Ui, rr: Rr, v: HandoversView, on: HandoverHandlers, width: number): Promise<RenderElement> {
  const { Box, Text, Button } = ui
  const t = await rr.tokens()
  const title = node(await rr.label({ text: 'handovers' }))
  if (v.snapshot === null || !v.snapshot.reachable) return paneStatus(ui, rr, t, title, v.snapshot)
  const list = v.snapshot.handovers
  const picked = list.find(entry => entry.id === v.pick) ?? list[0]
  if (picked === undefined) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>No open handovers.</Text>])
  const inner = Math.max(10, width - 2)
  const raised = await rr.surfaceProps({ level: 'raised' })
  const room = Math.max(8, inner - 2 * (raised.paddingX ?? 0) - 2)
  const esc = escHint(ui, t)
  const [rowPress, togglePress, copy, resume] = await Promise.all([
    rr.buttonProps({}),
    rr.buttonProps({ key: 'i' }),
    chip(ui, rr, { id: 'copy', hotkey: 'c', label: 'copy', treatment: 'chip', tone: 'signature', onPress: press => on.copy(picked, press.surface) }),
    chip(ui, rr, { id: 'resume', hotkey: 'r', label: 'resume', treatment: 'chip', tone: 'signature', onPress: () => on.resume(picked) }),
  ])
  const open = v.summaryOpen === picked.id
  const card = await handoverCard(ui, rr, t, picked, open, v.summaries[picked.id], room, { row: rowPress, toggle: togglePress }, on)
  return paneRoot(ui, rr, [
    title,
    ...list.map(entry =>
      entry.id === picked.id ? (
        <Box {...raised} borderStyle="round" borderColor={t.border.strong}>
          {card}
        </Box>
      ) : (
        <Box key={`ho:${entry.id}-box`} flexDirection="row" columnGap={1}>
          <Text color={t.fg.subtle}> </Text>
          <Button
            key={`ho:${entry.id}`}
            {...rowPress}
            label={truncate(`${dateOf(entry.created)}  ${oneLine(entry.tldr)}`, Math.max(10, width - 4))}
            onPress={() => on.pick(entry.id)}
          />
        </Box>
      ),
    ),
    <Text color={t.fg.subtle}>{`${list.length} of ${v.snapshot.handoversTotal} · superseded hidden`}</Text>,
    chipRow(ui, fit([copy, resume, esc], inner, 1)),
  ])
}
