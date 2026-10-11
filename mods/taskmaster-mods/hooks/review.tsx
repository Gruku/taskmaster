// User intent: the review pane as a paged card list (spec §8) — the phase strip, what waits on the person by kind, five cards
// a page with only the focused one open, its check and actions, a selection for bulk work — drawn with the kit.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderElement, RenderNode, RenderSurface } from 'claude-code'

import type { TmCursor, TmQueueItem, TmReviewList, TmSnapshot, TmTaskDetail } from '../types'
import {
  badge,
  card as cardBox,
  confirmRow,
  fit,
  helpFooter,
  type Kit,
  keycap,
  kitOf,
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
  type Ui,
} from './kit'
import { ageLabel, cardMode, CONNECTING, gateSignal, oneLine, splitCheck, truncate, wrapText } from './model'
import { ALL } from './phases'
import {
  doneQuestion,
  isSelectable,
  KIND_TONE,
  KIND_WORD,
  KINDS,
  kindCounts,
  listView,
  neighbour,
  newestStamp,
  reviewItems,
  reviewKind,
} from './review-list'
import type { Rr, RrGround } from './rr'

export type ReviewView = {
  snapshot: TmSnapshot | null
  cursor: TmCursor
  list: TmReviewList
  details: Readonly<Record<string, TmTaskDetail>>
  now: number
  /** The ticked check items of a task (local UI state, never Taskmaster's). */
  ticks: (taskId: string) => Promise<readonly string[]>
  detailsOpen: boolean
  /** The phase strip (spec §5): this pane's phase view and counts. */
  strip: StripView
  /** The surface drawn on, for the kit (keycap face, panel title). */
  surface: RenderSurface
  /** Toasts a line: a dim keycap's why. */
  say: (text: string) => void
  /** tm is being retried after a transient failure: with no good snapshot the pane says "Connecting…". */
  connecting?: boolean
}

export type ReviewHandlers = {
  askDone: (id: string) => void
  confirmDone: (id: string) => void
  cancel: () => void
  askNote: (id: string) => void
  /** `humanAction`: the check the card shows, so the clear can tell an already-empty one from a failed clear. */
  sendBack: (id: string, note: string, humanAction: string) => void
  fill: (text: string) => void
  toggleTick: (id: string, item: string) => void
  toggleDetails: () => void
  openViewer: (id: string) => void
  copyCheck: (id: string, text: string, surface: RenderSurface) => void
  strip: StripHandlers
  /** j / k (or the Tab ring): this card takes the focus; `page` is its page. */
  focus: (id: string, page: number) => void
  /** p / n: show this page, its first card focused. */
  turnPage: (page: number) => void
  /** s, or a click on a card's box: select or unselect that task. */
  select: (id: string) => void
  /** `all`: select these tasks (this page's); `none`: clear the selection. */
  selectAll: (ids: readonly string[]) => void
  selectNone: () => void
  /** u: read the queue and the phases again (Retry while offline). */
  refresh: () => void
  /** t: arm (or disarm) the focused card's checks for one digit. */
  armTick: () => void
}

/** The id keeps at least this many cells before the title and age give way. */
const MIN_ID = 8
/** A card's cells inside its frame: the focused card's border and padding, or the unfocused card's wider padding. */
const CARD_FRAME = 4
/** Every kind badge is padded to the longest word, so the ids line up down the list. */
const KIND_CELLS = Math.max(...KINDS.map(kind => KIND_WORD[kind].length))

/** Wrapped lines in one colour, indented: a card never draws a line wider than its room. */
function lines(k: Kit, color: string, text: string, width: number, indent = 0): RenderNode[] {
  const { Box, Text } = k.ui
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

/** Pieces in rows of `width`, wrapping rather than dropping. */
function wrapRows(k: Kit, pieces: readonly Piece[], width: number): RenderNode[] {
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

/** The pane's page ground, painted by the pane itself (the host's pane background is not RR's page). */
async function root(k: Kit, rr: Rr, children: readonly RenderNode[]): Promise<RenderElement> {
  const { Box } = k.ui
  const ground = await rr.surfaceProps({ level: 'page' })
  return (
    <Box {...ground} flexGrow={1}>
      {[...children]}
    </Box>
  )
}

/** The pane before it has data: Loading, Connecting (neutral), or unreachable with `u retry` (spec §8, §11). */
function status(k: Kit, s: TmSnapshot | null, connecting: boolean, on: ReviewHandlers): RenderNode[] {
  const { Text } = k.ui
  const title = label(k, 'review').el
  if (connecting) return [title, <Text color={k.t.fg.subtle}>{CONNECTING}</Text>]
  if (s === null) return [title, <Text color={k.t.fg.subtle}>Loading…</Text>]
  const retry = keycap(k, { id: 'refresh', key: 'u', label: 'retry', onPress: on.refresh })
  return [title, signal(k, 'critical', 'Taskmaster unreachable', s.reason).el, retry.el]
}

type CardContext = {
  readonly item: TmQueueItem
  readonly focused: boolean
  readonly selected: boolean
  readonly room: number
  readonly v: ReviewView
  readonly on: ReviewHandlers
}

/** A card's one line: its box (`s` on the focused card), kind badge, id, title, and age at the right. */
function collapsedRow(k: Kit, c: CardContext, ground: RrGround): RenderNode {
  const { Box, Text } = k.ui
  const { item } = c
  const kind = reviewKind(item)
  const box = keycap(k, {
    id: `sel:${item.id}`,
    ...(c.focused && isSelectable(item) ? { key: 's' } : {}),
    label: c.selected ? '■' : '□',
    on: ground,
    onPress: () => c.on.select(item.id),
    ...(isSelectable(item) ? {} : { dim: 'Issues and decisions are not signed off from the list' }),
  })
  const least = Math.min(MIN_ID, item.id.length)
  // Gives way right to left: the title, then the age, then the kind word to its initial; the id shortens to MIN_ID, never away.
  const word = KIND_WORD[kind].padEnd(KIND_CELLS)
  const kindBadge = badge(k, box.width + 1 + word.length + 2 + 1 + least <= c.room ? word : word.slice(0, 1), KIND_TONE[kind], { on: ground })
  const age = ageLabel(item.timestamp, c.v.now)
  const fixed = box.width + 1 + kindBadge.width + 1
  const showAge = age !== '' && fixed + least + 2 + age.length <= c.room
  const idRoom = c.room - fixed - (showAge ? age.length + 2 : 0)
  const id = truncate(item.id, Math.max(least, idRoom))
  const titleRoom = idRoom - id.length - 1
  const title = titleRoom >= 4 ? truncate(oneLine(item.title), titleRoom) : ''
  return (
    <Box flexDirection="row" columnGap={1} alignItems="flex-start">
      {box.el}
      {kindBadge.el}
      <Text color={k.t.fg.bold} bold>
        {id}
      </Text>
      {title === '' ? null : <Text color={k.t.fg.default}>{title}</Text>}
      {showAge ? <Box flexGrow={1} /> : null}
      {showAge ? <Text color={k.t.fg.subtle}>{age}</Text> : null}
    </Box>
  )
}

/** The focused card's body: what it is, its check (tickable), details on demand, a refusal, then its actions or y/n. */
async function expandedBody(k: Kit, c: CardContext, ground: RrGround): Promise<RenderNode[]> {
  const { Box, Text } = k.ui
  const { t } = k
  const { item, v, on, room } = c
  const body: RenderNode[] = []
  if (item.kind !== 'task') {
    const what =
      item.kind === 'issue'
        ? `An open ${item.severity} issue: look at it with the agent.`
        : 'An open decision: resolve it with the decision skill (taskmaster:decision).'
    body.push(...lines(k, t.fg.default, what, room))
    const open = keycap(k, {
      id: 'open',
      key: 'o',
      label: 'open in prompt',
      on: ground,
      onPress: () => on.fill(item.kind === 'decision' ? `Resolve decision ${item.id} with the taskmaster:decision skill` : `Look at ${item.id}`),
    })
    body.push(open.el)
    return body
  }
  const detail = v.details[item.id]
  const action = item.humanAction || detail?.humanAction || ''
  const check = splitCheck(action)
  const ticked = await v.ticks(item.id)
  const mode = cardMode(v.cursor, item)

  const gate = detail === undefined ? null : gateSignal(detail.gateState)
  const lane = detail === undefined ? 'loading…' : detail.lane.toUpperCase() || 'NO LANE'
  body.push(
    <Box flexDirection="row" columnGap={1}>
      {fit([{ el: <Text color={t.fg.subtle}>{lane}</Text>, width: lane.length }, gate === null ? null : signal(k, gate.kind, gate.word)], room, 1)}
    </Box>,
  )

  // The check: a section label with its dim detail, then one tickable item per line; digits only while the card is armed.
  if (check.items.length > 0) {
    if (check.label !== '') {
      const head = label(k, truncate(check.label, room))
      const left = room - head.width - 2
      body.push(
        <Box flexDirection="row" columnGap={2}>
          {head.el}
          {check.detail !== '' && left >= 12 ? <Text color={t.fg.subtle}>{truncate(check.detail, left)}</Text> : null}
        </Box>,
      )
      if (check.detail !== '' && left < 12) body.push(...lines(k, t.fg.subtle, check.detail, room))
    } else if (check.detail !== '') {
      body.push(...lines(k, t.fg.subtle, check.detail, room))
    }
    // Ticks press only in card mode; under the y/n the items are plain text (marks still shown, same indent). A check carries
    // its digit only while `t` has armed the card, so the digits are the strip's the rest of the time.
    const armed = mode === 'card'
    check.items.forEach((entry, i) => {
      const key = armed && v.list.ticking && i < 9 ? String(i + 1) : undefined
      const lead = (key === undefined ? 0 : 3) + 2
      const wrapped = wrapText(entry, room - lead)
      const first = `${ticked.includes(entry) ? '☑' : '☐'} ${wrapped[0] ?? ''}`
      body.push(
        <Box key={`tick-${i}-col`} flexDirection="column">
          {armed ? (
            keycap(k, { id: `tick-${i}`, ...(key === undefined ? {} : { key }), label: first, on: ground, onPress: () => on.toggleTick(item.id, entry) }).el
          ) : (
            <Text color={t.fg.default}>{first}</Text>
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
    body.push(...lines(k, t.fg.subtle, '(no human_action recorded)', room))
  }

  if (v.detailsOpen) {
    if (detail === undefined) body.push(<Text color={t.fg.subtle}>loading details…</Text>)
    else {
      const facts = [
        `branch: ${detail.branch || 'no branch'}`,
        ...(detail.pr ? [`PR: ${detail.pr}`] : []),
        ...(detail.notes ?? []).map(note => `note: ${note}`),
        ...(detail.links ?? []).map(link => `link: ${link}`),
      ]
      for (const fact of facts) body.push(...lines(k, t.fg.subtle, fact, room, 2))
    }
  }

  if (v.cursor.refusal !== '' && v.cursor.currentId === item.id) {
    body.push(badge(k, 'refused', 'critical', { on: ground }).el, ...lines(k, t.fg.default, v.cursor.refusal, room))
  }

  if (mode === 'confirm') {
    const open = check.items.filter(entry => !ticked.includes(entry)).length
    const question = doneQuestion(open, check.items.length, item.id, room - 2)
    body.push(...confirmRow(k, { id: 'confirm', question, onYes: () => on.confirmDone(item.id), onNo: on.cancel, width: room, on: ground }))
    return body
  }
  const actions = [
    keycap(k, { id: 'done', key: 'd', label: 'done', tone: 'success', on: ground, onPress: () => on.askDone(item.id) }),
    keycap(k, { id: 'back', key: 'a', label: 'agent', tone: 'warning', on: ground, onPress: () => on.askNote(item.id) }),
    ...(check.items.length === 0 ? [] : [keycap(k, { id: 'tick', key: 't', label: 'tick', selected: v.list.ticking, on: ground, onPress: on.armTick })]),
    keycap(k, { id: 'open', key: 'o', label: 'open', on: ground, onPress: () => on.fill(`Look at ${item.id}`) }),
    keycap(k, { id: 'viewer', key: 'v', label: 'viewer', on: ground, onPress: () => on.openViewer(item.id) }),
    keycap(k, { id: 'copy', key: 'c', label: 'copy check', on: ground, onPress: press => on.copyCheck(item.id, action, press.surface) }),
    keycap(k, { id: 'details', key: 'i', label: `${v.detailsOpen ? '▾' : '▸'} more`, on: ground, onPress: on.toggleDetails }),
  ]
  body.push(...wrapRows(k, actions, room))
  return body
}

/**
 * The note for the agent, on its own: the card it answers and the Input. Nothing else with a key is drawn while it is up, so
 * a letter or digit typed into the note can never press anything.
 */
function composeNote(k: Kit, item: TmQueueItem, action: string, on: ReviewHandlers, inner: number): RenderNode[] {
  const { Box, Text, Input } = k.ui
  const { t } = k
  const cancel = keycap(k, { id: 'note-cancel', label: 'cancel', onPress: on.cancel })
  return [
    label(k, 'review · note for the agent').el,
    <Box flexDirection="row" columnGap={1}>
      <Text color={t.fg.bold} bold>
        {item.id}
      </Text>
      <Text color={t.fg.default}>{truncate(oneLine(item.title), Math.max(4, inner - item.id.length - 1))}</Text>
    </Box>,
    ...(action === '' ? [] : lines(k, t.fg.subtle, action, inner)),
    <Input
      key="note"
      label="note for the agent"
      placeholder="what should change"
      submitLabel="send back"
      autoFocus
      onSubmit={value => on.sendBack(item.id, value, action)}
    />,
    cancel.el,
  ]
}

export async function reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderElement> {
  const k = await kitOf(ui, rr, v.surface, v.say)
  const { Box, Text } = ui
  const { t } = k
  const s = v.snapshot
  if (s === null || !s.reachable) return root(k, rr, status(k, s, v.connecting === true, on))
  const inner = Math.max(10, width - 2)
  const items = reviewItems(s, v.cursor, v.details)
  const view = listView(items, v.cursor.currentId, v.list.page)
  const focused = view.focused

  if (focused !== null && focused.kind === 'task' && cardMode(v.cursor, focused) === 'note') {
    const action = focused.humanAction || v.details[focused.id]?.humanAction || ''
    return root(k, rr, composeNote(k, focused, action, on, inner))
  }

  // A narrow pane keeps the pager whole: refresh shortens to its glyph first.
  const bar = toolbar(k, {
    title: 'review',
    slots: [
      pager(k, { page: view.page, pages: view.pages, onTurn: page => on.turnPage(page) }),
      keycap(k, { id: 'refresh', key: 'u', label: inner >= 40 ? 'refresh' : '↻', onPress: on.refresh }),
    ],
    width: inner,
  })
  const done = v.cursor.done.length
  if (focused === null) {
    const clear = v.strip.view.choice === ALL ? signal(k, 'success', 'Queue clear', `${done} done this pass`).el : phaseEmpty(k, v.strip, on.strip)
    return root(k, rr, [bar, ...phaseSection(k, v.strip, on.strip, inner, [clear])])
  }

  const newest = ageLabel(newestStamp(items), v.now)
  const summary = [
    `${s.queueTotal} waiting on you`,
    ...(newest === '' ? [] : [`newest ${newest} ago`]),
    ...(s.queueTotal > items.length ? [`${items.length} listed`] : []),
    ...(done > 0 ? [`${done} done this pass`] : []),
  ].join(' · ')
  const counts = kindCounts(items)
  const kinds = KINDS.map(kind => badge(k, `${counts[kind]} ${KIND_WORD[kind]}`, KIND_TONE[kind], { dim: counts[kind] === 0 }))
  const pageTasks = view.entries.filter(isSelectable).map(item => item.id)
  const selection = selectionBar(k, {
    actions: [],
    ghosts: [
      { id: 'sel-all', label: 'all', onPress: () => on.selectAll(pageTasks) },
      { id: 'sel-none', label: 'none', onPress: on.selectNone },
    ],
    width: inner,
  })
  const room = Math.max(8, inner - CARD_FRAME)
  const cards: RenderNode[] = []
  for (const item of view.entries) {
    const c: CardContext = { item, focused: item.id === focused.id, selected: v.list.selected.includes(item.id), room, v, on }
    const ground: RrGround = c.selected ? 'overlay' : 'raised'
    cards.push(cardBox(k, { id: item.id, selected: c.selected, focused: c.focused }, collapsedRow(k, c, ground), c.focused ? await expandedBody(k, c, ground) : []))
  }
  const prev = neighbour(items, view, -1)
  const next = neighbour(items, view, 1)
  const up = keycap(k, {
    id: 'focus-prev',
    key: 'k',
    label: '↑',
    onPress: () => (prev === null ? undefined : on.focus(prev.id, prev.page)),
    ...(prev === null ? { dim: 'Already on the first card' } : {}),
  })
  const down = keycap(k, {
    id: 'focus-next',
    key: 'j',
    label: '↓',
    onPress: () => (next === null ? undefined : on.focus(next.id, next.page)),
    ...(next === null ? { dim: 'Already on the last card' } : {}),
  })
  const hints = v.list.ticking ? ['1-9 ticks a check', 't or f cancels'] : ['s select', 'h/l phase', 'f phases', '0-9 phase', 'p/n page', 't+1-9 tick', 'u refresh']

  return root(k, rr, [
    bar,
    ...phaseSection(k, v.strip, on.strip, inner, [
      <Text color={t.fg.subtle} wrap="truncate-end">
        {truncate(summary, inner)}
      </Text>,
      <Box flexDirection="row" columnGap={2}>
        {fit(kinds, inner, 2)}
      </Box>,
      selection,
      ...cards,
      <Box flexDirection="row" columnGap={1}>
        {up.el}
        {down.el}
        {helpFooter(k, hints, inner - up.width - down.width - 2)}
      </Box>,
    ]),
  ])
}
