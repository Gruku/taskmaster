// User intent: the review pane in the rework's vocabulary (spec §8, §15.6–15.7, Review.dc.html) — the phase picker, what waits
// on the person as one sentence and its tallies, a bulk row whose labels carry the count, three 3-line items a page with only
// the focused one boxed with its labelled actions (one filled), and one dim footer that owns the navigation keys.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmCursor, TmQueueItem, TmReviewList, TmSnapshot, TmTaskDetail } from '../types'
import { type Kit, kitOf, type StripHandlers, type StripView, type Ui } from './kit'
import { ageLabel, cardMode, CONNECTING, gateSignal, oneLine, splitCheck, type TmCheck, truncate, wrapText } from './model'
import { ALL, emptyLine, orderPhases } from './phases'
import {
  doneQuestion,
  isSelectable,
  KIND_WORD,
  KINDS,
  kindCounts,
  listView,
  neighbour,
  newestStamp,
  reviewItems,
  reviewKind,
  selectedTasks,
} from './review-list'
import type { Rr } from './rr'
import {
  actionKey,
  actionRows,
  askRow,
  bulkRow,
  CONTENT_COL,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  line,
  type ListEntry,
  listing,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  phaseFooter,
  phasePicker,
  refreshPiece,
  type Seg,
  summaryRow,
  withPhaseMenu,
} from './vocab'

export type ReviewView = {
  snapshot: TmSnapshot | null
  cursor: TmCursor
  list: TmReviewList
  details: Readonly<Record<string, TmTaskDetail>>
  now: number
  /** The ticked check items of a task (local UI state, never Taskmaster's). */
  ticks: (taskId: string) => Promise<readonly string[]>
  detailsOpen: boolean
  /** The phase picker (spec §15.4): this pane's phase view and counts. */
  strip: StripView
  surface: RenderSurface
  /** Toasts a line: a disabled key's why, a footer key at an end. */
  say: (text: string) => void
  /** tm is being retried after a transient failure: with no good snapshot the pane says "Connecting…". */
  connecting?: boolean
  /** Demo data: the frame's health says demo. */
  demo?: boolean
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
  /** s, or a click on a card's mark: select or unselect that task. */
  select: (id: string) => void
  /** `all`: select these tasks (this page's); `none`: clear the selection. */
  selectAll: (ids: readonly string[]) => void
  selectNone: () => void
  /** u: read the queue and the phases again (retry while offline). */
  refresh: () => void
  /** t: arm (or disarm) the focused card's checks for one digit. */
  armTick: () => void
  /** b / g: ask (y/n, or the shared note) for exactly these task ids. */
  bulkAsk: (kind: 'done' | 'agent', ids: readonly string[]) => void
  bulkCancel: () => void
  bulkDone: (ids: readonly string[]) => void
  /** `actions`: the check each card showed, by task id. */
  bulkSendBack: (ids: readonly string[], note: string, actions: Readonly<Record<string, string>>) => void
  /** m: the sidebar over the pane. */
  toggleSidebar: () => void
}

const NOT_SELECTABLE = 'Issues and decisions are not signed off from the list'
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

function healthOf(v: ReviewView): Health {
  const s = v.snapshot
  if (v.connecting === true && (s === null || !s.reachable)) return { kind: 'info', word: 'connecting' }
  if (s === null) return { kind: 'info', word: 'loading' }
  if (!s.reachable) return { kind: 'critical', word: 'offline' }
  return v.demo === true ? { kind: 'info', word: 'demo' } : { kind: 'success', word: 'tm ok' }
}

/** An item's dim context: collapsed `patch-1-9 · lane full · in review for 5h`; focused, the gate instead of the age. */
function contextOf(k: Kit, it: TmQueueItem, v: ReviewView, focused: boolean): Seg[] {
  const subtle = k.t.fg.subtle
  const age = ageLabel(it.timestamp, v.now)
  if (it.kind === 'issue') return [{ text: [`open ${it.severity} issue`, age === '' ? '' : `${age} old`].filter(Boolean).join(' · '), color: subtle }]
  if (it.kind === 'decision') return [{ text: ['open decision', age === '' ? '' : `${age} old`].filter(Boolean).join(' · '), color: subtle }]
  const d = v.details[it.id]
  if (d === undefined) return [{ text: [focused ? 'loading…' : `${it.priority} priority`, age === '' ? '' : `in review for ${age}`].filter(Boolean).join(' · '), color: subtle }]
  const head = [d.phase ?? '', `lane ${d.lane || 'none'}`].filter(Boolean).join(' · ')
  if (!focused) return [{ text: [head, age === '' ? '' : `in review for ${age}`].filter(Boolean).join(' · '), color: subtle }]
  const gate = gateSignal(d.gateState)
  return gate === null
    ? [{ text: head, color: subtle }]
    : [
        { text: `${head} · `, color: subtle },
        { text: '● ', color: k.t.tone[gate.kind] },
        { text: gate.word, color: subtle },
      ]
}

/** The `human` field: the check's label (and detail) when it lists items, else the whole human action on one line. */
function humanOf(check: TmCheck, action: string): string {
  if (check.items.length > 0) return [check.label, check.detail].filter(Boolean).join(' — ')
  return oneLine(action) || '(no human_action recorded)'
}

const actionOf = (it: TmQueueItem, v: ReviewView): string => (it.kind === 'task' ? it.humanAction || v.details[it.id]?.humanAction || '' : '')

/** The checks: `1  ☐ text` a row in the content column; armed by `t`, the first nine press by their digit. */
function checkRows(k: Kit, it: TmQueueItem, check: TmCheck, ticked: readonly string[], armed: boolean, ticking: boolean, on: ReviewHandlers, width: number): RenderNode[] {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const room = Math.max(8, width - CONTENT_COL - 3)
  return check.items.map((entry, i) => {
    const label = `${ticked.includes(entry) ? '☑' : '☐'} ${entry}`
    const shown = truncate(label, room)
    const digit = i < 9 ? String(i + 1) : ' '
    const key = armed && ticking && i < 9 ? digit : undefined
    if (!armed) {
      return (
        <Box paddingLeft={CONTENT_COL} flexDirection="row">
          <Text color={t.fg.disabled}>{`${digit}  `}</Text>
          <Text color={t.fg.default}>{shown}</Text>
        </Box>
      )
    }
    return (
      <Box paddingLeft={CONTENT_COL} flexDirection="row">
        {key === undefined ? <Text color={t.fg.disabled}>{`${digit}  `}</Text> : null}
        <Box key={`tick-${i}-col`} flexDirection="row">
          <Button key={`tick-${i}`} plain {...(key === undefined ? {} : { hotkey: key })} label={shown} onPress={() => on.toggleTick(it.id, entry)}>
            <Text color={t.fg.default}>{shown}</Text>
          </Button>
          {key === undefined ? null : (
            <Box position="absolute" left={0} top={0}>
              <Text color={t.signatureText} bold>{`${key}  `}</Text>
            </Box>
          )}
        </Box>
      </Box>
    )
  })
}

/** The focused item: its context, the `human` field, the checks with `t` and `c` under them, details, a refusal, then its actions or y/n. */
async function focusedCard(k: Kit, it: TmQueueItem, head: HeadSpec, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderNode> {
  const { t } = k
  const room = width - 4
  if (it.kind !== 'task') {
    const what = it.kind === 'issue' ? `An open ${it.severity} issue: look at it with the agent.` : 'An open decision: resolve it with the decision skill (taskmaster:decision).'
    const open = actionKey(k, {
      id: 'open',
      key: 'o',
      label: 'open in prompt',
      rung: 'primary',
      onPress: () => on.fill(it.kind === 'decision' ? `Resolve decision ${it.id} with the taskmaster:decision skill` : `Look at ${it.id}`),
    })
    const lines: Seg[][] = [contextOf(k, it, v, true), ...wrapText(what, room - CONTENT_COL).map(text => [{ text, color: t.fg.default }])]
    return focusedItem(k, { id: it.id, head, lines, actions: actionRows(k, [open], room - 1) }, width)
  }
  const action = actionOf(it, v)
  const check = splitCheck(action)
  const ticked = await v.ticks(it.id)
  const mode = cardMode(v.cursor, it)
  const armed = mode === 'card'
  const lines: Seg[][] = [contextOf(k, it, v, true), fieldSegs(k, 'human', humanOf(check, action))]
  const extra: RenderNode[] = checkRows(k, it, check, ticked, armed, v.list.ticking, on, room)
  if (armed) {
    const keys = [
      ...(check.items.length === 0 ? [] : [actionKey(k, { id: 'tick', key: 't', label: 'tick check 1-9', onPress: on.armTick })]),
      actionKey(k, { id: 'copy', key: 'c', label: 'copy live check', onPress: press => on.copyCheck(it.id, action, press.surface) }),
    ]
    const { Box } = k.ui
    extra.push(
      <Box paddingLeft={CONTENT_COL} flexDirection="column">
        {actionRows(k, keys, room - CONTENT_COL)}
      </Box>,
    )
  }
  if (v.detailsOpen) {
    const d = v.details[it.id]
    const facts =
      d === undefined
        ? ['loading details…']
        : [`branch: ${d.branch || 'no branch'}`, ...(d.pr ? [`PR: ${d.pr}`] : []), ...(d.notes ?? []).map(note => `note: ${note}`), ...(d.links ?? []).map(link => `link: ${link}`)]
    for (const fact of facts) extra.push(itemLine(k, [{ text: fact, color: t.fg.subtle }], room))
  }
  if (v.cursor.refusal !== '' && v.cursor.currentId === it.id) {
    wrapText(oneLine(v.cursor.refusal), Math.max(10, room - CONTENT_COL - 11)).forEach((text, i) =>
      extra.push(itemLine(k, [{ text: i === 0 ? '▲ REFUSED  ' : '           ', color: t.tone.critical, bold: true }, { text, color: t.fg.default }], room)),
    )
  }
  if (mode === 'confirm') {
    const open = check.items.filter(entry => !ticked.includes(entry)).length
    const question = doneQuestion(open, check.items.length, it.id, room - 16)
    return focusedItem(k, { id: it.id, head, lines, extra, actions: askRow(k, { id: 'confirm', question, onYes: () => on.confirmDone(it.id), onNo: on.cancel, width: room - 1 }) }, width)
  }
  const actions = actionRows(
    k,
    [
      actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: () => on.askDone(it.id) }),
      actionKey(k, { id: 'back', key: 'a', label: 'send task to agent', onPress: () => on.askNote(it.id) }),
      actionKey(k, { id: 'open', key: 'o', label: 'open task', onPress: () => on.fill(`Look at ${it.id}`) }),
      actionKey(k, { id: 'viewer', key: 'v', label: 'task in viewer', onPress: () => on.openViewer(it.id) }),
      actionKey(k, { id: 'details', key: 'i', label: `task detail ${v.detailsOpen ? '▾' : '▸'}`, onPress: on.toggleDetails }),
    ],
    room - 1,
  )
  return focusedItem(k, { id: it.id, head, lines, extra, actions }, width)
}

/** The note for the agent, on its own: the card it answers and the Input, nothing else with a key. */
function composeNote(k: Kit, it: TmQueueItem, action: string, on: ReviewHandlers, inner: number): RenderNode[] {
  const { Text, Input, Button } = k.ui
  const { t } = k
  return [
    line(k, [{ text: 'note for the agent', color: t.signatureText, bold: true }], inner).el,
    line(k, [{ text: it.id, color: t.fg.bold, bold: true }, { text: `  ${oneLine(it.title)}`, color: t.fg.default }], inner).el,
    ...(action === '' ? [] : wrapText(action, inner).map(text => <Text color={t.fg.subtle}>{text}</Text>)),
    <Input key="note" label="note for the agent" placeholder="what should change" submitLabel="send back" autoFocus onSubmit={value => on.sendBack(it.id, value, action)} />,
    <Button key="note-cancel" plain label="cancel" onPress={on.cancel} />,
  ]
}

/** The shared note for `g`, on its own like a card's note: the tasks it goes to and the Input, nothing else with a key. */
function composeBulkNote(k: Kit, items: readonly TmQueueItem[], v: ReviewView, on: ReviewHandlers, inner: number): RenderNode[] {
  const { Input, Button } = k.ui
  const { t } = k
  const ids = v.list.bulkIds
  const actions: Record<string, string> = {}
  for (const it of items) if (it.kind === 'task' && ids.includes(it.id)) actions[it.id] = actionOf(it, v)
  return [
    line(k, [{ text: `one note for ${plural(ids.length, 'task')}`, color: t.signatureText, bold: true }], inner).el,
    ...ids.map(id => {
      const it = items.find(x => x.id === id)
      return line(k, [{ text: id, color: t.fg.bold, bold: true }, { text: `  ${it === undefined ? '(no longer listed)' : oneLine(it.title)}`, color: t.fg.default }], inner).el
    }),
    <Input key="bulk-note" label={`note for the agent (${plural(ids.length, 'task')})`} placeholder="what should change" submitLabel="send back" autoFocus onSubmit={value => on.bulkSendBack(ids, value, actions)} />,
    <Button key="bulk-cancel" plain label="cancel" onPress={on.bulkCancel} />,
  ]
}

export async function reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderNode> {
  const k = await kitOf(ui, rr, v.surface, v.say)
  const { Text } = ui
  const { t } = k
  const s = v.snapshot
  const inner = Math.max(30, width - 4)
  const frame = (children: readonly RenderNode[]): RenderNode => paneFrame(k, { name: 'review', health: healthOf(v), width }, children)
  const tail: FooterItem[] = [
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]
  if (s === null || !s.reachable) {
    if (v.connecting === true) return frame([<Text color={t.fg.subtle}>{CONNECTING}</Text>, <Text> </Text>, footer(k, tail, inner)])
    if (s === null) return frame([<Text color={t.fg.subtle}>Loading…</Text>, <Text> </Text>, footer(k, tail, inner)])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: () => on.refresh() })
    return frame([offlineRow(k, s.reason, retry, inner), <Text> </Text>, footer(k, [{ keys: 'u', word: 'retry' }, ...tail], inner)])
  }
  const items = reviewItems(s, v.cursor, v.details)
  const view = listView(items, v.cursor.currentId, v.list.page)
  const focused = view.focused

  if (v.list.bulk === 'agent') return frame(composeBulkNote(k, items, v, on, inner))
  if (focused !== null && focused.kind === 'task' && cardMode(v.cursor, focused) === 'note') return frame(composeNote(k, focused, actionOf(focused, v), on, inner))

  const top = paneToolbar(k, {
    left: phasePicker(k, v.strip, on.strip),
    right: [pagerPiece(k, { page: view.page, pages: view.pages, onTurn: page => on.turnPage(page) }), refreshPiece(k, { label: 'refresh', onPress: () => on.refresh() })],
    width: inner,
  })
  const asking = v.list.bulk === 'done' || (focused !== null && cardMode(v.cursor, focused) === 'confirm')
  const prev = neighbour(items, view, -1)
  const next = neighbour(items, view, 1)
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'focus-next', key: 'j', onPress: () => (next === null ? k.say('Already on the last card') : on.focus(next.id, next.page)) },
        { id: 'focus-prev', key: 'k', onPress: () => (prev === null ? k.say('Already on the first card') : on.focus(prev.id, prev.page)) },
      ],
    },
    {
      keys: 's',
      word: 'select',
      press: [
        {
          key: 's',
          onPress: () => {
            if (focused === null) return
            if (isSelectable(focused)) on.select(focused.id)
            else k.say(NOT_SELECTABLE)
          },
        },
      ],
    },
    v.list.ticking ? { keys: '1-9', word: 'tick a check' } : { keys: 't 1-9', word: 'tick' },
    ...phaseFooter(k, v.strip, on.strip),
    ...(asking
      ? []
      : [
          {
            keys: 'p/n',
            word: 'page',
            press: [
              { key: 'p', onPress: () => (view.page === 0 ? k.say('Already on the first page') : on.turnPage(view.page - 1)) },
              { key: 'n', onPress: () => (view.page >= view.pages - 1 ? k.say('Already on the last page') : on.turnPage(view.page + 1)) },
            ],
          },
        ]),
    { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: () => on.refresh() }] },
    ...tail,
  ]
  const done = v.cursor.done.length

  if (focused === null) {
    const tab = orderPhases(v.strip.view.phases, v.strip.allCount, v.strip.view.choice).find(x => x.id === v.strip.view.choice)
    const empty =
      v.strip.view.choice === ALL
        ? emptyRow(k, `Queue clear · ${done} done this pass`, null, inner)
        : emptyRow(k, emptyLine(v.strip.empty, tab), actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose(ALL) }), inner)
    return frame([top, ...withPhaseMenu(k, v.strip, on.strip, [<Text> </Text>, empty]), <Text> </Text>, footer(k, keys, inner)])
  }

  const newest = ageLabel(newestStamp(items), v.now)
  const sentence = [
    `${s.queueTotal}${s.queueCapped === true ? '+' : ''} waiting on you`,
    ...(newest === '' ? [] : [`newest ${newest} ago`]),
    ...(s.queueTotal > items.length ? [`${items.length} listed`] : []),
    ...(done > 0 ? [`${done} done this pass`] : []),
  ].join(' · ')
  const counts = kindCounts(items)
  const summary = summaryRow(k, sentence, KINDS.map(kind => ({ word: KIND_WORD[kind].toUpperCase(), count: counts[kind] })), inner)
  const pageTasks = view.entries.filter(isSelectable).map(it => it.id)
  const chosen = selectedTasks(items, v.list.selected).map(it => it.id)
  const n = chosen.length
  const bulk: RenderNode[] =
    v.list.bulk === 'done'
      ? askRow(k, {
          id: 'bulk',
          question: `mark ${plural(v.list.bulkIds.length, 'task')} done: ${v.list.bulkIds.join(', ')}?`,
          onYes: () => on.bulkDone(v.list.bulkIds),
          onNo: on.bulkCancel,
          width: inner,
        })
      : v.list.bulk === 'busy'
        ? [line(k, [{ text: `working · one task at a time: ${v.list.bulkIds.length}`, color: t.tone.info }], inner).el]
        : [
            bulkRow(k, {
              keys: [
                actionKey(k, { id: 'bulk-done', key: 'b', label: `mark ${plural(n, 'task')} done`, onPress: () => on.bulkAsk('done', chosen), ...(n === 0 ? { dim: 'Nothing selected to mark done' } : {}) }),
                actionKey(k, { id: 'bulk-agent', key: 'g', label: `send ${plural(n, 'task')} to agent`, onPress: () => on.bulkAsk('agent', chosen), ...(n === 0 ? { dim: 'Nothing selected to send to the agent' } : {}) }),
              ],
              scopes: [
                { id: 'sel-all', label: 'all', onPress: () => on.selectAll(pageTasks) },
                { id: 'sel-none', label: 'none', onPress: on.selectNone },
              ],
              width: inner,
            }),
          ]
  const entries: ListEntry[] = []
  for (const it of view.entries) {
    const head: HeadSpec = {
      mark: { key: `sel:${it.id}`, selected: v.list.selected.includes(it.id), onPress: () => on.select(it.id), ...(isSelectable(it) ? {} : { dim: NOT_SELECTABLE }) },
      badge: KIND_WORD[reviewKind(it)].toUpperCase(),
      ident: it.id,
      title: oneLine(it.title),
      age: ageLabel(it.timestamp, v.now),
    }
    if (it.id === focused.id) {
      entries.push({ focused: true, nodes: [await focusedCard(k, it, head, v, on, inner)] })
    } else {
      const human = it.kind === 'task' ? humanOf(splitCheck(actionOf(it, v)), actionOf(it, v)) : ''
      entries.push({ focused: false, nodes: item(k, head, [contextOf(k, it, v, false), ...(human === '' ? [] : [fieldSegs(k, 'human', human)])], inner) })
    }
  }
  return frame([top, ...withPhaseMenu(k, v.strip, on.strip, [summary, ...bulk, <Text> </Text>, ...listing(k, entries, inner)]), <Text> </Text>, footer(k, keys, inner)])
}
