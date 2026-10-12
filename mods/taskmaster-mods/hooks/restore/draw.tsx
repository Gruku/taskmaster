// User intent: the restore pane drawn in the rework's vocabulary (spec §10, §15.6–15.7, Restore.dc.html) — the burst page
// grouped UNFINISHED / HANDED OFF / IDLE then RUNNING NOW, 3-line items, the focused one boxed with r resume session filled and
// its fresh / --resume field, the bulk row with counts, one dim footer — so a burst of sessions reopens in one press.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import type { TmRestoreData, TmRestoreView } from '../../types'
import type { TmPane } from '../flows'
import type { Kit, StripHandlers, StripView } from '../kit'
import { truncate } from '../model'
import type { SidebarView } from '../nav'
import {
  actionKey,
  actionRows,
  badgeSeg,
  bulkRow,
  cardInner,
  contentCol,
  emptyRow,
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
  pageHeader,
  pageRows,
  pagesOf,
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
  readonly source: 'tm' | 'demo'
  /** The tab row's panes and counts (absent: no tab row). */
  readonly tabs?: SidebarView
}

export type RestoreHandlers = {
  readonly strip: StripHandlers
  readonly turnPage: (page: number) => void
  readonly rescan: () => void
  readonly toggleCompact: () => void
  readonly focus: (id: string) => void
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

function headOf(r: RestoreRow, v: RestorePaneView, on: RestoreHandlers): HeadSpec {
  const why = blockedWhy(r)
  return {
    mark: r.s.running ? null : { key: `sel:${r.s.id}`, selected: isTicked(r, v.view.selected, v.view.opened), onPress: () => on.toggle(r.s.id), ...(why === '' ? {} : { dim: why }) },
    badge: r.s.running ? 'RUNNING' : BADGE_OF[r.status],
    title: r.s.title,
    age: r.s.running ? when(r.s.closedEpoch, v.now) : hhmm(r.s.closedEpoch),
    dim: r.status === 'done' && !r.s.running,
  }
}

/** The focused session: where, task, handover, detail, the fresh / --resume field, its brief and marks, then r / i / c. */
function focusedCard(k: Kit, r: RestoreRow, v: RestorePaneView, on: RestoreHandlers, root: string, width: number): RenderNode {
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
  return focusedItem(k, { id, head: headOf(r, v, on), lines, extra, actions }, width)
}

/** The whole pane in its frame (`◆ TASKMASTER · RESTORE`), `width` cells across. */
export function restorePaneTree(k: Kit, v: RestorePaneView, on: RestoreHandlers, width: number): RenderNode {
  const { Text } = k.ui
  const { t } = k
  const inner = Math.max(10, width - 4)
  const tabs = v.tabs === undefined ? [] : [tabRow(k, v.tabs, { go: on.goTo, toggleSidebar: on.toggleSidebar }, inner), <Text> </Text>]
  const rows = v.rows
  const pages = pagesOf(rows, burstsOf(rows), v.strip.view.choice)
  const at = Math.min(Math.max(0, v.view.page), Math.max(0, pages.length - 1))
  const page = pages[at]
  const list = pageRows(rows, page)
  const focusId = list.some(r => r.s.id === v.view.focus) ? v.view.focus : (list[0]?.s.id ?? '')
  const ticked = list.filter(r => isTicked(r, v.view.selected, v.view.opened)).map(r => r.s.id)
  const running = runningRows(rows)
  const root = v.data.scan?.root ?? ''
  const index = list.findIndex(r => r.s.id === focusId)

  const top = paneToolbar(k, {
    left: phasePicker(k, v.strip, on.strip),
    right: [
      togglePiece(k, { id: 'restore-compact', label: `${v.view.compact ? '◐' : '○'} compact`, on: v.view.compact, onPress: on.toggleCompact }),
      pagerPiece(k, { page: at, pages: pages.length, onTurn: on.turnPage }),
      refreshPiece(k, { label: 'rescan', onPress: on.rescan }),
    ],
    width: inner,
  })
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'restore-down', key: 'j', onPress: () => (index < 0 || index >= list.length - 1 ? k.say('Already on the last session') : on.focus(list[index + 1]?.s.id ?? focusId)) },
        { id: 'restore-up', key: 'k', onPress: () => (index <= 0 ? k.say('Already on the first session') : on.focus(list[index - 1]?.s.id ?? focusId)) },
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
        { key: 'p', onPress: () => (at === 0 ? k.say('Already on the first page') : on.turnPage(at - 1)) },
        { key: 'n', onPress: () => (at >= pages.length - 1 ? k.say('Already on the last page') : on.turnPage(at + 1)) },
      ],
    },
    { keys: 'z', word: 'compact', press: [{ key: 'z', onPress: on.toggleCompact }] },
    { keys: 'u', word: 'rescan', press: [{ key: 'u', onPress: on.rescan }] },
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]

  const body: RenderNode[] = []
  if (v.data.error !== '') {
    body.push(line(k, [{ text: '▲ scan failed', color: t.tone.critical, bold: true }, { text: ` · ${v.data.error.replace(/^Scan failed: /, '')}`, color: t.fg.subtle }], inner).el)
  } else if (v.data.scan === null) {
    body.push(emptyRow(k, v.view.busy !== '' ? v.view.busy : 'No scan yet: u scans this repo’s sessions', null, inner))
  } else {
    if (v.source === 'tm' && v.data.refs === null && v.view.busy === '') {
      body.push(line(k, [{ text: '▲ tm offline', color: t.tone.critical, bold: true }, { text: ' · statuses read ? · resume still works', color: t.fg.subtle }], inner).el)
    }
    const tallies = STATUS_ORDER.map(status => ({ word: BADGE_OF[status], count: list.filter(r => r.status === status).length })).filter(x => x.count > 0)
    body.push(summaryRow(k, page === undefined ? 'No sessions in this window' : pageHeader(page, v.now), tallies, inner))
    const n = ticked.length
    // Too tight for a whole bulk label: the count stays, the trailing `sessions` goes (a key is never cut mid-word).
    const bulkLabel = (verb: string): string => (3 + `${verb} ${plural(n, 'session')}`.length <= inner ? `${verb} ${plural(n, 'session')}` : `${verb} ${n}`)
    body.push(
      bulkRow(k, {
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
      }),
    )
    if (v.view.busy !== '') body.push(line(k, [{ text: v.view.busy, color: t.tone.info }], inner).el)
    body.push(<Text> </Text>)
    if (list.length === 0) body.push(emptyRow(k, 'No sessions under this phase', actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose('') }), inner))
    for (const group of sectionsOf(list)) {
      body.push(sectionRow(k, SECTION_LABEL[group.section], `${group.rows.length} · ${SECTION_NOTE[group.section]}`, inner))
      const entries: ListEntry[] = group.rows.map(r =>
        r.s.id === focusId
          ? { focused: true, nodes: [focusedCard(k, r, v, on, root, inner)] }
          : { focused: false, nodes: item(k, headOf(r, v, on), [whereSegs(k, r, root, r.status === 'done'), taskSegs(k, r, r.status === 'done')], inner) },
      )
      body.push(...listing(k, entries, inner), <Text> </Text>)
    }
    if (running.length > 0) {
      body.push(sectionRow(k, 'RUNNING NOW', `${running.length} · open in other terminals, not resumable here`, inner))
      body.push(...listing(k, running.map(r => ({ focused: false, nodes: item(k, headOf(r, v, on), [whereSegs(k, r, root, false)], inner) })), inner))
    }
  }
  return paneFrame(k, { name: 'restore', health: healthOf(v), width }, [...tabs, top, ...withPhaseMenu(k, v.strip, on.strip, body), <Text> </Text>, footer(k, keys, inner)])
}
