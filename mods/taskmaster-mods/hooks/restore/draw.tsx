// User intent: the restore pane drawn in the rework's vocabulary (spec §10, §15.6–15.7, Restore.dc.html) — one list that fills the
// pane and scrolls, grouped UNFINISHED / HANDED OFF / IDLE then RUNNING NOW, 3-line items, the focused one boxed with r resume
// session filled and its fresh / --resume field, the bulk row with counts, one dim footer — so a burst of sessions reopens in one press.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import type { TmRestoreData, TmRestoreView } from '../../types'
import type { TmPane } from '../flows'
import type { Kit, StripHandlers, StripView } from '../kit'
import { truncate } from '../model'
import type { SidebarView } from '../nav'
import { pagedBy, positionLabel, rememberScroll, type ScrollWindow, windowOf } from '../scroll-list'
import {
  actionKey,
  actionRows,
  badgeSeg,
  bulkRow,
  cardInner,
  contentCol,
  emptyRow,
  focusedItem,
  focusedRows,
  footerRows,
  frameRows,
  bulkRows,
  scrollEntries,
  scrollListing,
  footer,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  line,
  type ListEntry,
  pagerPiece,
  paneFrame,
  paneToolbar,
  phaseFooter,
  phasePicker,
  refreshPiece,
  sectionRow,
  type Seg,
  statusBadgeWord,
  summaryRow,
  tabRow,
  togglePiece,
  withPhaseMenu,
} from '../vocab'
import {
  blockedWhy,
  burstsOf,
  ctxTone,
  detailOf,
  formatTokens,
  hhmm,
  isTicked,
  listHeader,
  listOf,
  recentIds,
  type RestoreRow,
  type RestoreStatus,
  resumeModeOf,
  runningRows,
  SECTION_LABEL,
  SECTION_NOTE,
  sectionsOf,
  STATUS_ORDER,
  when,
} from './model'

export type RestorePaneView = {
  readonly data: TmRestoreData
  readonly view: TmRestoreView
  /** The rows the pane lists (paneRows: this session left out), computed once with the strip's counts. */
  readonly rows: readonly RestoreRow[]
  readonly strip: StripView
  /** Epoch seconds. */
  readonly now: number
  /** Rows the pane body may show (Pane `scroll.bodyRows`); absent: the whole list is drawn. */
  readonly bodyRows?: number
  readonly source: 'tm' | 'demo'
  /** The tab row's panes and counts (absent: no tab row). */
  readonly tabs?: SidebarView
}

export type RestoreHandlers = {
  readonly strip: StripHandlers
  readonly rescan: () => void
  readonly toggleCompact: () => void
  /** j / k, p / n or a Tab: this session takes the focus; `first` is the list's first visible session then. */
  readonly focus: (id: string, first: number) => void
  readonly toggle: (id: string) => void
  readonly select: (changes: Readonly<Record<string, boolean>>) => void
  readonly toggleMode: (id: string) => void
  readonly resume: (ids: readonly string[]) => void
  readonly toggleBrief: (id: string) => void
  readonly brief: (ids: readonly string[]) => void
  readonly copy: (id: string) => void
  /** m: the sidebar over the pane. */
  readonly toggleSidebar: () => void
  /** A tab in the tab row: that pane opens in place of this one. */
  readonly goTo: (pane: TmPane) => void
}

const BADGE_OF: Readonly<Record<RestoreStatus, string>> = {
  interrupted: 'INTERRUPTED',
  wip: 'WIP',
  unknown: 'UNKNOWN',
  'handed-off': 'HANDED OFF',
  done: 'DONE',
}
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`
const baseName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? path

function healthOf(v: RestorePaneView): Health {
  if (v.source === 'demo') return { kind: 'info', word: 'demo' }
  if (v.data.error !== '') return { kind: 'critical', word: 'scan failed' }
  if (v.data.scan !== null && v.data.refs === null && v.view.busy === '') return { kind: 'critical', word: 'tm offline' }
  return { kind: 'success', word: 'tm ok' }
}

/** Line 2: `taskmaster · feat/x · .worktrees/x · 512k ctx` (ctx warning past 400k, critical past 700k). */
function whereSegs(k: Kit, r: RestoreRow, root: string, dim: boolean): Seg[] {
  const { t } = k
  const cwd = r.s.cwd === root ? 'repo root' : root !== '' && r.s.cwd.startsWith(root) ? r.s.cwd.slice(root.length).replace(/^[\\/]+/, '').replace(/\\/g, '/') : r.s.cwd
  const tone = ctxTone(r.s.ctx)
  return [
    { text: `${baseName(root)} · ${r.s.branch || 'no branch'} · ${cwd}`, color: dim ? t.border.strong : t.fg.subtle },
    { text: r.s.ctx > 0 ? ` · ${formatTokens(r.s.ctx)} ctx` : '', color: dim ? t.border.strong : tone === null ? t.fg.subtle : t.tone[tone] },
  ]
}

/** Line 3: the bound task (`● IN PROGRESS  tm-audit-033 · <detail>`), else the detail beside `no task touched`. */
function taskSegs(k: Kit, r: RestoreRow, dim: boolean): Seg[] {
  const { t } = k
  const detail = detailOf(r)
  const words = `${detail.label.toLowerCase()} "${detail.text}"`
  if (r.task !== null) {
    return [
      dim ? { text: `· ${statusBadgeWord(r.task.status)}`, color: t.border.strong } : badgeSeg(k, statusBadgeWord(r.task.status)),
      { text: `  ${r.task.id}`, color: dim ? t.border.strong : t.fg.bold, bold: !dim },
      { text: ` · ${words}`, color: dim ? t.border.strong : t.fg.subtle },
    ]
  }
  return [
    { text: r.boundId === '' ? 'no task touched' : r.boundId, color: dim ? t.border.strong : t.fg.disabled },
    { text: ` · ${words}`, color: dim ? t.border.strong : t.fg.subtle },
  ]
}

function headOf(r: RestoreRow, v: RestorePaneView, on: RestoreHandlers, recent: ReadonlySet<string>): HeadSpec {
  const why = blockedWhy(r)
  return {
    mark: r.s.running ? null : { key: `sel:${r.s.id}`, selected: isTicked(r, v.view.selected, v.view.opened, recent.has(r.s.id)), onPress: () => on.toggle(r.s.id), ...(why === '' ? {} : { dim: why }) },
    badge: r.s.running ? 'RUNNING' : BADGE_OF[r.status],
    title: r.s.title,
    age: r.s.running ? when(r.s.closedEpoch, v.now) : hhmm(r.s.closedEpoch),
    dim: r.status === 'done' && !r.s.running,
  }
}

/** The focused session: where, task, handover, detail, the fresh / --resume field, its brief and marks, then r / i / c. */
function focusedCard(k: Kit, r: RestoreRow, v: RestorePaneView, on: RestoreHandlers, recent: ReadonlySet<string>, root: string, width: number): { readonly node: RenderNode; readonly rows: number } {
  const { Box, Text } = k.ui
  const { t } = k
  const id = r.s.id
  const room = cardInner(width)
  const col = contentCol(room)
  const lines: Seg[][] = [whereSegs(k, r, root, false)]
  if (r.task !== null) {
    lines.push([{ text: 'task  ', color: t.signatureText }, badgeSeg(k, statusBadgeWord(r.task.status)), { text: `  ${r.task.id}`, color: t.fg.bold, bold: true }, { text: r.phase === '' ? '' : ` · ${r.phase}`, color: t.fg.subtle }])
  }
  if (r.handover !== null) {
    lines.push([{ text: 'handover  ', color: t.signatureText }, { text: r.handover.id, color: t.fg.bold, bold: true }, { text: ` · next: ${r.handover.next || r.handover.tldr}`, color: t.fg.default }])
  }
  const detail = detailOf(r)
  lines.push([{ text: `${detail.label.toLowerCase()}  `, color: t.signatureText }, { text: detail.text, color: t.fg.default }])
  const mode = resumeModeOf(r, v.view.modes)
  const fresh = mode === 'fresh' && r.handover !== null
  const chosen = fresh ? ` fresh from ${truncate(r.handover?.id ?? '', 28)} ` : ' --resume '
  const other = fresh ? '  --resume' : r.handover === null ? '' : `  fresh from ${truncate(r.handover.id, 20)}`
  const switchKey = actionKey(k, {
    id: `mode:${id}`,
    key: 'v',
    label: 'switch resume mode',
    onPress: () => on.toggleMode(id),
    ...(r.handover === null ? { dim: `${r.s.title} wrote no handover: it reopens with --resume` } : {}),
  })
  // `v switch resume mode` sits on the via row when it fits there, else it joins the action row after `r`.
  const fieldRoom = Math.max(1, room - col - 5)
  const chosenText = truncate(chosen, fieldRoom)
  const inline = col + 5 + chosenText.length + 3 + switchKey.width <= room
  const otherRoom = Math.max(0, fieldRoom - chosenText.length - (inline ? 3 + switchKey.width : 0))
  const extra: RenderNode[] = [
    <Box paddingLeft={col} flexDirection="row">
      <Text color={t.signatureText}>via  </Text>
      <Text backgroundColor={t.surface.overlay} color={t.fg.bold}>
        {chosenText}
      </Text>
      <Text color={t.fg.disabled}>{truncate(other, otherRoom)}</Text>
      {inline ? <Text>{'   '}</Text> : null}
      {inline ? switchKey.el : null}
    </Box>,
  ]
  if (v.view.briefing.includes(id)) extra.push(itemLine(k, [{ text: 'Briefing…', color: t.tone.info }], room))
  const brief = v.view.briefs[id]
  if (brief !== undefined) {
    for (const entry of brief.split('\n')) {
      const [head, ...rest] = entry.split(': ')
      extra.push(itemLine(k, rest.length === 0 ? [{ text: entry, color: t.fg.default }] : [{ text: `${(head ?? '').toLowerCase()}  `, color: t.signatureText }, { text: rest.join(': '), color: t.fg.default }], room))
    }
  }
  const openedAt = v.view.opened[id]
  const marks = [...(openedAt === undefined ? [] : [`opened ${when(openedAt, v.now)}`]), ...(r.s.resumedAfterClose && !r.s.running ? ['resumed since'] : []), ...(r.s.cwdExists ? [] : ['folder missing'])]
  if (marks.length > 0) extra.push(itemLine(k, [{ text: marks.join(' · '), color: r.s.cwdExists ? t.fg.subtle : t.tone.critical }], room))
  const why = blockedWhy(r)
  const launching = v.view.launching.includes(id)
  const actions = actionRows(
    k,
    [
      actionKey(k, { id: `resume:${id}`, key: 'r', label: 'resume session', rung: 'primary', onPress: () => on.resume([id]), ...(why !== '' ? { dim: why } : launching ? { dim: `${r.s.title} is opening` } : {}) }),
      ...(inline ? [] : [switchKey]),
      actionKey(k, { id: `brief:${id}`, key: 'i', label: brief === undefined ? 'brief session' : 'hide brief', onPress: () => on.toggleBrief(id), ...(v.view.briefing.includes(id) ? { dim: `Briefing ${r.s.title}…` } : {}) }),
      actionKey(k, { id: `copy:${id}`, key: 'c', label: 'copy resume command', onPress: () => on.copy(id) }),
    ],
    room - 1,
  )
  const spec = { id, head: headOf(r, v, on, recent), lines, extra, actions }
  return { node: focusedItem(k, spec, width), rows: focusedRows(spec) }
}

/** The whole pane in its frame (`◆ TASKMASTER · RESTORE`), `width` cells across. */
export function restorePaneTree(k: Kit, v: RestorePaneView, on: RestoreHandlers, width: number): RenderNode {
  const { Text } = k.ui
  const { t } = k
  const inner = Math.max(10, width - 4)
  const tabs = v.tabs === undefined ? [] : [tabRow(k, v.tabs, { go: on.goTo, toggleSidebar: on.toggleSidebar }, inner), <Text> </Text>]
  const rows = v.rows
  const bursts = burstsOf(rows)
  const choice = v.strip.view.choice
  const list = listOf(rows, bursts, choice)
  const recent = recentIds(rows)
  // The focused session when it is listed; else the one at the stored first-visible index (clamped), so a list always has a focus.
  const stored = Number.isFinite(v.view.first) ? Math.min(Math.max(0, Math.floor(v.view.first)), Math.max(0, list.length - 1)) : 0
  const at = list.findIndex(r => r.s.id === v.view.focus)
  const index = at >= 0 ? at : list.length > 0 ? stored : -1
  const focusId = list[index]?.s.id ?? ''
  const ticked = list.filter(r => isTicked(r, v.view.selected, v.view.opened, recent.has(r.s.id))).map(r => r.s.id)
  const running = runningRows(rows)
  const root = v.data.scan?.root ?? ''
  const scanned = v.data.error === '' && v.data.scan !== null

  // The list: the sessions by section, then RUNNING NOW (items that take no focus), drawn into the rows left over (spec §15.6).
  const entries: ListEntry[] = []
  const ids: string[] = []
  const heading = (label: string, count: number, note: string): RenderNode => sectionRow(k, label, `${count} · ${note}`, inner)
  if (scanned) {
    for (const group of sectionsOf(list)) {
      const header = heading(SECTION_LABEL[group.section], group.rows.length, SECTION_NOTE[group.section])
      for (const r of group.rows) {
        if (r.s.id === focusId) {
          const card = focusedCard(k, r, v, on, recent, root, inner)
          entries.push({ focused: true, nodes: [card.node], rows: card.rows, group: group.section, header })
        } else {
          const lines = [whereSegs(k, r, root, r.status === 'done'), taskSegs(k, r, r.status === 'done')]
          entries.push({ focused: false, nodes: item(k, headOf(r, v, on, recent), lines, inner), rows: 1 + lines.length, group: group.section, header })
        }
        ids.push(r.s.id)
      }
    }
    const runHeader = heading('RUNNING NOW', running.length, 'open in other terminals, not resumable here')
    for (const r of running) {
      entries.push({ focused: false, nodes: item(k, headOf(r, v, on, recent), [whereSegs(k, r, root, false)], inner), rows: 2, group: 'running', header: runHeader })
      ids.push('')
    }
  }
  const scroll = scrollEntries(entries)
  // Set once the rows are known (below); the keys only run after the frame is drawn.
  const place: { win: ScrollWindow; room: number } = { win: windowOf({ entries: [], room: 1, first: 0, focus: -1 }), room: 1 }
  const moveTo = (to: number, first: number): void => {
    const id = ids[to]
    if (id !== undefined && id !== '') on.focus(id, first)
  }
  const step = (dir: -1 | 1): void => {
    if (index < 0 || index + dir < 0 || index + dir >= list.length) k.say(dir === 1 ? 'Already on the last session' : 'Already on the first session')
    else moveTo(index + dir, windowOf({ entries: scroll, room: place.room, first: place.win.start, focus: index + dir }).start)
  }
  const turn = (dir: -1 | 1): void => {
    // The sessions only: the window's worth is measured on the list, and RUNNING NOW is never a focus.
    const to = pagedBy({ entries: scroll.slice(0, list.length), room: place.room, first: place.win.start, focus: index, dir })
    if (index < 0 || (to.focus === index && to.first === place.win.start)) k.say(dir === 1 ? 'Already at the end' : 'Already at the top')
    else moveTo(to.focus, to.first)
  }

  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'restore-down', key: 'j', onPress: () => step(1) },
        { id: 'restore-up', key: 'k', onPress: () => step(-1) },
      ],
    },
    { keys: 's', word: 'tick', press: [{ key: 's', onPress: () => (focusId === '' ? undefined : on.toggle(focusId)) }] },
    // Ruling F11: v is the focused card's key; the footer only names it.
    { keys: 'v', word: 'resume mode' },
    ...phaseFooter(k, v.strip, on.strip),
    {
      keys: 'p/n',
      word: 'page',
      press: [
        { key: 'p', onPress: () => turn(-1) },
        { key: 'n', onPress: () => turn(1) },
      ],
    },
    { keys: 'z', word: 'compact', press: [{ key: 'z', onPress: on.toggleCompact }] },
    { keys: 'u', word: 'rescan', press: [{ key: 'u', onPress: on.rescan }] },
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]

  // The window over the list in the room left, remembered for the wheel; and the toolbar that says where it stands.
  const placed = (pinned: number): RenderNode => {
    place.room = v.bodyRows === undefined ? Number.POSITIVE_INFINITY : v.bodyRows - pinned
    place.win = windowOf({ entries: scroll, room: place.room, first: stored, focus: index })
    rememberScroll('restore', { entries: scroll, ids, room: place.room, first: place.win.start, focus: index })
    return paneToolbar(k, {
      left: phasePicker(k, v.strip, on.strip),
      right: [
        togglePiece(k, { id: 'restore-compact', label: `${v.view.compact ? '◐' : '○'} compact`, on: v.view.compact, onPress: on.toggleCompact }),
        ...(entries.length === 0 ? [] : [pagerPiece(k, { position: positionLabel(place.win, entries.length), atTop: place.win.start === 0 && index <= 0, atEnd: index >= list.length - 1, onPage: turn })]),
        refreshPiece(k, { label: 'rescan', onPress: on.rescan }),
      ],
      width: inner,
    })
  }
  // The rows around the list: the frame, the tab row and its blank, the toolbar, a blank above the footer and the footer.
  const around = frameRows(k) + (tabs.length === 0 ? 0 : 2) + 1 + 1 + footerRows(keys, inner)

  const body: RenderNode[] = []
  // What sits between the toolbar and the list, counted into the rows the list does not have.
  let above = 0
  if (v.data.error !== '') {
    body.push(line(k, [{ text: '▲ scan failed', color: t.tone.critical, bold: true }, { text: ` · ${v.data.error.replace(/^Scan failed: /, '')}`, color: t.fg.subtle }], inner).el)
  } else if (v.data.scan === null) {
    body.push(emptyRow(k, v.view.busy !== '' ? v.view.busy : 'No scan yet: u scans this repo’s sessions', null, inner))
  } else {
    if (v.source === 'tm' && v.data.refs === null && v.view.busy === '') {
      body.push(line(k, [{ text: '▲ tm offline', color: t.tone.critical, bold: true }, { text: ' · statuses read ? · resume still works', color: t.fg.subtle }], inner).el)
      above += 1
    }
    const tallies = STATUS_ORDER.map(status => ({ word: BADGE_OF[status], count: list.filter(r => r.status === status).length })).filter(x => x.count > 0)
    body.push(summaryRow(k, list.length === 0 ? 'No sessions in this window' : listHeader(rows, bursts, choice, v.now), tallies, inner))
    above += 1
    const n = ticked.length
    // Too tight for a whole bulk label: the count stays, the trailing `sessions` goes (a key is never cut mid-word).
    const bulkLabel = (verb: string): string => (3 + `${verb} ${plural(n, 'session')}`.length <= inner ? `${verb} ${plural(n, 'session')}` : `${verb} ${n}`)
    // `all` / `unfinished` take every session in the list (the phase's, as listed), not a page's.
    const bulkSpec = {
      keys: [
        actionKey(k, { id: 'restore-resume-all', key: 'b', label: bulkLabel(v.view.compact ? 'resume + compact' : 'resume'), onPress: () => on.resume(ticked), ...(n === 0 ? { dim: 'Nothing ticked to resume' } : {}) }),
        actionKey(k, { id: 'restore-brief-all', key: 'g', label: bulkLabel('brief'), onPress: () => on.brief(ticked), ...(n === 0 ? { dim: 'Nothing ticked to brief' } : {}) }),
      ],
      scopes: [
        { id: 'restore-all', label: 'all', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, blockedWhy(r) === '']))) },
        { id: 'restore-unfinished', label: 'unfinished', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, blockedWhy(r) === '' && r.status !== 'done']))) },
        { id: 'restore-none', label: 'none', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, false]))) },
      ],
      width: inner,
    }
    body.push(bulkRow(k, bulkSpec))
    above += bulkRows(k, bulkSpec)
    if (v.view.busy !== '') {
      body.push(line(k, [{ text: v.view.busy, color: t.tone.info }], inner).el)
      above += 1
    }
    body.push(<Text> </Text>)
    above += 1
    if (list.length === 0) {
      body.push(emptyRow(k, 'No sessions under this phase', actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose('') }), inner))
      above += 1
    }
  }
  const top = placed(around + above)
  if (scanned) body.push(...scrollListing(k, entries, place.win, inner))
  return paneFrame(k, { name: 'restore', health: healthOf(v), width }, [...tabs, top, ...withPhaseMenu(k, v.strip, on.strip, body), <Text> </Text>, footer(k, keys, inner)])
}
