// User intent: the Session tab (spec §7, §15.7; band `m`, /tm-session) — this session's task, its handover and where it came
// from in the round-1 label column, each action named for its subject and drawn under what it acts on, one filled key, one
// dim footer — and every row inside the pane, so nothing is clipped at the right edge.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmHandoverSummary, TmSessionBurst, TmSessionHandover, TmSessionInfo, TmSnapshot } from '../types'
import { HANDOVER_TAG } from './band'
import type { TmPane } from './flows'
import type { Kit } from './kit'
import type { SidebarView } from './nav'
import { ageLabel, gateSignal, isReady, oneLine, wrapText } from './model'
import {
  actionKey,
  actionRows,
  askRow,
  badgeSeg,
  CONTENT_COL,
  fieldSegs,
  footer,
  type Health,
  itemRule,
  labelRow,
  line,
  paneFrame,
  paneToolbar,
  refreshPiece,
  type Seg,
  statusBadgeWord,
  tabRow,
  underLabel,
} from './vocab'

export type SessionView = {
  readonly snapshot: TmSnapshot | null
  /** D5, with a newer notice already let in (withNotice). */
  readonly handover: TmSessionHandover | null
  readonly resumedFrom: string
  readonly burst: TmSessionBurst | null
  readonly band: TmBandMode
  /** The handover's decisions and blockers when read; shown while `summaryOpen`. */
  readonly summary: TmHandoverSummary | undefined
  readonly summaryOpen: boolean
  readonly connecting: boolean
  readonly demo: boolean
  readonly now: number
  /** This session's branch and start (null: not read yet): the toolbar's words. */
  readonly info: TmSessionInfo | null
  /** The tab row's panes and counts (absent: no tab row). */
  readonly tabs?: SidebarView
}

export type SessionHandlers = {
  readonly goTo: (pane: TmPane) => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly sendBack: (taskId: string) => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly openHandover: (handoverId: string) => void
  readonly launch: (handover: TmSessionHandover, surface: RenderSurface) => void
  readonly toggleSummary: (handoverId: string, open: boolean) => void
  readonly refresh: () => void
  readonly toggleSidebar: () => void
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

/** `full · patch-1-9 · review-gate pass ✓ · ready for done`: lane, phase and gate subtle, the pass and ready in success ink. */
function metaSegs(k: Kit, s: TmSnapshot): Seg[] {
  const { t } = k
  const b = s.bound
  const d = b?.detail ?? null
  if (b === null || d === null) return []
  const gate = gateSignal(d.gateState)
  const p = b.pipeline
  const segs: Seg[] = [{ text: [d.lane || 'no lane', d.phase ?? '', gate === null ? '' : gate.word].filter(Boolean).join(' · '), color: t.fg.subtle }]
  if (gate?.kind === 'success') segs.push({ text: ' ✓', color: t.tone.success })
  const stage = p === null ? '' : p.laneless ? 'no pipeline' : p.outstanding.length === 0 ? 'ready for done' : `next: ${p.outstanding[0]}`
  if (stage !== '') segs.push({ text: ' · ', color: t.fg.subtle }, { text: stage, color: stage === 'ready for done' ? t.tone.success : t.fg.subtle })
  if (b.inferred) segs.push({ text: ' · inferred from the branch', color: t.fg.subtle })
  return segs
}

const blank = (k: Kit): RenderNode => {
  const { Text } = k.ui
  return <Text> </Text>
}

function taskSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - CONTENT_COL
  const s = v.snapshot
  const b = s?.bound ?? null
  if (s === null || b === null) return [labelRow(k, 'TASK', [{ text: 'No task bound to this session', color: t.fg.disabled }], inner)]
  const d = b.detail
  const title = d === null ? '' : oneLine(d.title)
  const rows: RenderNode[] = [
    labelRow(
      k,
      'TASK',
      [
        ...(s.reachable && d !== null ? [badgeSeg(k, statusBadgeWord(d.status)), { text: '  ', color: t.fg.default }] : []),
        { text: b.taskId, color: t.fg.bold, bold: true },
        ...(title === '' ? [] : [{ text: `  ${title}`, color: b.inferred ? t.fg.subtle : t.fg.default }]),
      ],
      inner,
    ),
  ]
  const meta = metaSegs(k, s)
  if (meta.length > 0) rows.push(labelRow(k, '', meta, inner))
  const human = d === null ? '' : oneLine(d.humanAction)
  if (human !== '') rows.push(labelRow(k, '', fieldSegs(k, 'human', human), inner))
  rows.push(blank(k))
  if (v.band.confirmingId === b.taskId) {
    rows.push(underLabel(k, askRow(k, { id: 'session', question: `mark ${b.taskId} done?`, onYes: () => on.confirmDone(b.taskId), onNo: on.cancel, width: room })))
    return rows
  }
  const noDone = whyNot(s, true)
  const noAgent = whyNot(s, false)
  rows.push(
    underLabel(
      k,
      actionRows(
        k,
        [
          actionKey(k, { id: 'session-done', key: 'd', label: 'mark task done', rung: 'primary', onPress: () => on.askDone(b.taskId), ...(noDone === '' ? {} : { dim: noDone }) }),
          actionKey(k, { id: 'session-agent', key: 'a', label: 'send task to agent', onPress: () => on.sendBack(b.taskId), ...(noAgent === '' ? {} : { dim: noAgent }) }),
        ],
        room,
      ),
    ),
  )
  if (v.band.refusal !== '' && v.band.refusalId === b.taskId) {
    const words = wrapText(oneLine(v.band.refusal), Math.max(10, room - 11))
    words.forEach((text, i) =>
      rows.push(labelRow(k, '', [{ text: i === 0 ? '▲ REFUSED  ' : '           ', color: t.tone.critical, bold: true }, { text, color: t.fg.default }], inner)),
    )
  }
  return rows
}

function handoverSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - CONTENT_COL
  const ho = v.handover
  if (ho === null) return [labelRow(k, 'HANDOVER', [{ text: 'No handover for this session yet', color: t.fg.disabled }], inner)]
  const age = ageLabel(ho.created, v.now)
  const rows: RenderNode[] = [
    labelRow(
      k,
      'HANDOVER',
      [
        badgeSeg(k, HANDOVER_TAG[ho.tag]),
        { text: '  ', color: t.fg.default },
        { text: ho.id, color: t.fg.default },
        { text: age === '' ? '' : ` · ${age} ago`, color: t.fg.subtle },
      ],
      inner,
    ),
    labelRow(k, '', [{ text: oneLine(ho.tldr) || ho.id, color: t.fg.bold, bold: true }], inner),
  ]
  if (ho.next !== undefined && ho.next !== '') rows.push(labelRow(k, '', fieldSegs(k, 'next', oneLine(ho.next)), inner))
  rows.push(blank(k))
  rows.push(
    underLabel(
      k,
      actionRows(
        k,
        [
          actionKey(k, { id: 'session-copy', key: 'c', label: 'copy handover', onPress: press => on.copy(ho.id, press.surface) }),
          actionKey(k, { id: 'session-open', key: 'h', label: 'show in handovers', onPress: () => on.openHandover(ho.id) }),
          actionKey(k, { id: 'session-launch', key: 'w', label: 'resume in new tab', onPress: press => on.launch(ho, press.surface) }),
          actionKey(k, { id: 'session-summary', key: 'i', label: `handover decisions·blockers ${v.summaryOpen ? '▾' : '▸'}`, onPress: () => on.toggleSummary(ho.id, !v.summaryOpen) }),
        ],
        room,
      ),
    ),
  )
  if (v.summaryOpen) {
    const sum = v.summary
    if (sum === undefined) rows.push(labelRow(k, '', [{ text: 'loading decisions and blockers…', color: t.fg.subtle }], inner))
    else if (sum.unavailable === true) rows.push(labelRow(k, '', [{ text: 'Decisions and blockers unavailable: i asks again', color: t.fg.subtle }], inner))
    else {
      for (const [word, items] of [['decisions', sum.decisions], ['blockers', sum.blockers]] as const) {
        rows.push(labelRow(k, '', [{ text: word, color: t.signatureText }], inner))
        if (items.length === 0) rows.push(labelRow(k, '', [{ text: 'none', color: t.fg.subtle }], inner))
        for (const entry of items) rows.push(labelRow(k, '', [{ text: `· ${oneLine(entry)}`, color: t.fg.default }], inner))
      }
    }
  }
  return rows
}

function sessionSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const where: Seg[] =
    v.resumedFrom === ''
      ? [{ text: 'not resumed from a handover', color: t.fg.disabled }]
      : [
          { text: 'resumed from ', color: t.fg.subtle },
          { text: v.resumedFrom, color: t.fg.default },
        ]
  if (v.burst !== null) where.push({ text: ` · part of the ${v.burst.at} burst · ${v.burst.others} others open`, color: t.fg.subtle })
  const rows: RenderNode[] = [labelRow(k, 'SESSION', where, inner)]
  if (v.burst !== null) {
    rows.push(blank(k))
    rows.push(underLabel(k, actionRows(k, [actionKey(k, { id: 'session-restore', key: '4', label: 'show burst in restore', onPress: () => on.goTo('restore') })], inner - CONTENT_COL)))
  }
  return rows
}

function healthOf(v: SessionView): Health {
  const s = v.snapshot
  if (v.connecting && (s === null || !s.reachable)) return { kind: 'info', word: 'connecting' }
  if (s === null) return { kind: 'info', word: 'loading' }
  if (!s.reachable) return { kind: 'critical', word: 'offline' }
  return v.demo ? { kind: 'info', word: 'demo' } : { kind: 'success', word: 'tm ok' }
}

/** The Session tab (spec §15.7): the frame, `this session · <branch> · started <age> ago`, TASK / HANDOVER / SESSION, the footer. */
export function sessionTree(k: Kit, v: SessionView, on: SessionHandlers, width: number): RenderNode {
  const { t } = k
  const inner = Math.max(CONTENT_COL + 12, width - 4)
  const started = v.info === null ? '' : ageLabel(new Date(v.info.startedAt).toISOString(), v.now)
  const words = line(
    k,
    [
      { text: 'this session', color: t.fg.subtle },
      { text: v.info === null ? '' : [` · ${v.info.branch || 'no branch'}`, started === '' ? '' : ` · started ${started} ago`].join(''), color: t.fg.disabled },
    ],
    Math.max(12, inner - 14),
  )
  const top = paneToolbar(k, { left: words, right: [refreshPiece(k, { label: 'refresh', onPress: on.refresh })], width: inner })
  const keys = footer(
    k,
    [
      { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: on.refresh }] },
      { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
      { keys: 'esc', word: 'close' },
    ],
    inner,
  )
  const tabs = v.tabs === undefined ? [] : [tabRow(k, v.tabs, { go: on.goTo, toggleSidebar: on.toggleSidebar }, inner), blank(k)]
  return paneFrame(k, { name: 'session', health: healthOf(v), width }, [
    ...tabs,
    top,
    blank(k),
    ...taskSection(k, v, on, inner),
    itemRule(k, inner),
    ...handoverSection(k, v, on, inner),
    itemRule(k, inner),
    ...sessionSection(k, v, on, inner),
    blank(k),
    keys,
  ])
}
