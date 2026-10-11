// User intent: how taskmaster-mods looks — the handovers list drawn from plain view data with Reality Reprojection pieces
// from $.rr (the review pane is review.tsx, the band band.tsx); Buttons are drawn here because their press handlers must
// stay in this plugin.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderChildren, RenderElement, RenderNode, RenderSurface, UiPressArgument } from 'claude-code'

import type { TmHandover, TmHandoverSummary, TmSnapshot } from '../types'
import {
  CONNECTING,
  dateOf,
  handoverPage,
  handoverRefs,
  oneLine,
  PAGE_SIZE,
  truncate,
  wrapText,
} from './model'
import { fit, type Piece, type Ui } from './kit'
import type { Rr, RrButtonProps, RrNode, RrTokens, RrTone, RrTreatment } from './rr'

const node = (n: RrNode): RenderNode => n as unknown as RenderNode

// Widths of $.rr pieces, from what they draw: a signal is `glyph word`, a state chip ` glyph text ` (signature: no glyph),
// a keycap ` key `.
const signalWidth = (word: string): number => 2 + word.length
const stateChipWidth = (text: string, tone: RrTone): number => text.length + (tone === 'signature' ? 2 : 4)

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

/**
 * What a pane shows before it has data, while tm is retried after a transient failure (neutral, like Loading), or when
 * Taskmaster cannot be reached.
 */
async function paneStatus(ui: Ui, rr: Rr, t: RrTokens, title: RenderNode, s: TmSnapshot | null, connecting: boolean): Promise<RenderElement> {
  const { Text } = ui
  if (connecting) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>{CONNECTING}</Text>])
  if (s === null) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>Loading…</Text>])
  return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'critical', word: 'Taskmaster unreachable', detail: s.reason }))])
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



export type HandoversView = {
  snapshot: TmSnapshot | null
  pick: string
  summaries: Readonly<Record<string, TmHandoverSummary>>
  /** The handover whose summary is expanded; it shows only while that handover is the picked one. */
  summaryOpen: string
  /** The page shown, 0 first (clamped to the list). */
  page: number
  /** tm is being retried after a transient failure: with no good snapshot the pane says "Connecting…". */
  connecting?: boolean
}

export type HandoverHandlers = {
  pick: (id: string) => void
  copy: (h: TmHandover, surface: RenderSurface) => void
  resume: (h: TmHandover) => void
  toggleSummary: (h: TmHandover, open: boolean) => void
  /** n / p: show this page and pick its first handover. */
  turnPage: (page: number, firstId: string) => void
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
  if (v.snapshot === null || !v.snapshot.reachable) return paneStatus(ui, rr, t, title, v.snapshot, v.connecting === true)
  const list = v.snapshot.handovers
  const pg = handoverPage(list, v.page, v.pick)
  const picked = pg.picked
  if (picked === undefined) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>No open handovers.</Text>])
  const inner = Math.max(10, width - 2)
  const raised = await rr.surfaceProps({ level: 'raised' })
  const room = Math.max(8, inner - 2 * (raised.paddingX ?? 0) - 2)
  const esc = escHint(ui, t)
  const turn = (to: number, label: string, hotkey: string) =>
    chip(ui, rr, { id: label, hotkey, label, treatment: 'chip', tone: 'signature', onPress: () => on.turnPage(to, list[to * PAGE_SIZE]?.id ?? '') })
  const [rowPress, togglePress, copy, resume, prev, next] = await Promise.all([
    rr.buttonProps({}),
    rr.buttonProps({ key: 'i' }),
    chip(ui, rr, { id: 'copy', hotkey: 'c', label: 'copy', treatment: 'chip', tone: 'signature', onPress: press => on.copy(picked, press.surface) }),
    chip(ui, rr, { id: 'resume', hotkey: 'r', label: 'resume', treatment: 'chip', tone: 'signature', onPress: () => on.resume(picked) }),
    pg.page > 0 ? turn(pg.page - 1, 'prev', 'p') : null,
    pg.page < pg.pages - 1 ? turn(pg.page + 1, 'next', 'n') : null,
  ])
  const open = v.summaryOpen === picked.id
  const total = Math.max(v.snapshot.handoversTotal, list.length)
  const first = pg.page * PAGE_SIZE + 1
  const footer =
    pg.pages > 1
      ? `${first}–${first + pg.entries.length - 1} of ${total} · n/p page · superseded hidden`
      : `${list.length} of ${v.snapshot.handoversTotal} · superseded hidden`
  const card = await handoverCard(ui, rr, t, picked, open, v.summaries[picked.id], room, { row: rowPress, toggle: togglePress }, on)
  return paneRoot(ui, rr, [
    title,
    ...pg.entries.map(entry =>
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
    <Text color={t.fg.subtle}>{footer}</Text>,
    chipRow(ui, fit([copy, resume, prev, next, esc], inner, 1)),
  ])
}
