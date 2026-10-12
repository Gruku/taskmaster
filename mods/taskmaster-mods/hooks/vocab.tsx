// User intent: the one element vocabulary every taskmaster-mods screen draws with (visual rework 2026-10-12, spec §15) —
// inks, glyph badges, the five-rung key ladder, labelled actions, the footer, list items, the frame, the phase picker and the
// Taskmaster sidebar — so every screen is built from the approved prototype's parts and nothing else.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderElement, RenderNode, UiPressArgument } from 'claude-code'

import { fit, HOTKEY, KEYCAP_FACE, type Kit, PANEL_TITLE, type Piece, stepUp, type StripHandlers, type StripView } from './kit'
import { truncate } from './model'
import type { TmPane } from './flows'
import type { SidebarView } from './nav'
import { atEnd, isDoneShown, orderPhases, pickerName, shownTabs, step } from './phases'
import type { RrGround, RrSignalKind } from './rr'

/** Footer keys (spec §15.8 S6, Task A0): `under` hides label-less hotkey Buttons under the footer text; `inline` draws `k: word`. */
export const FOOTER_KEYS: 'under' | 'inline' = 'under'
/** The phase list (S5, Task A0): `absolute` floats it over the list; `swap` replaces the list while it is open. */
export const MENU_PLACEMENT: 'absolute' | 'swap' = 'absolute'
/** Esc while the sidebar or the phase list is open (S4, Task A0): `refuse` closes that layer; `pane` lets Esc close the pane. */
export const SIDEBAR_ESC: 'refuse' | 'pane' = 'refuse'

// ── Inks (spec §15.2): the prototype's text classes as RR tokens ─────────────────────────────────────────────────────

export type Ink = 'bold' | 'default' | 'subtle' | 'disabled' | 'faint'

export function inkOf(k: Kit, ink: Ink): string {
  return ink === 'faint' ? k.t.border.strong : k.t.fg[ink]
}

/** One run of text in a colour. */
export type Seg = { readonly text: string; readonly color: string; readonly bold?: boolean }

/** Segments side by side, cut where `width` runs out (the last one shown ends in `…`): never wider than its room. */
export function line(k: Kit, segs: readonly Seg[], width: number): Piece {
  const { Box, Text } = k.ui
  const out: RenderNode[] = []
  let used = 0
  for (const s of segs) {
    if (used >= width) break
    const cut = truncate(s.text, width - used)
    if (cut === '') continue
    used += cut.length
    out.push(
      <Text color={s.color} bold={s.bold === true}>
        {cut}
      </Text>,
    )
  }
  return { el: <Box flexDirection="row">{out}</Box>, width: used }
}

// ── Glyph badges (component sheet BADGE): symbol + word in the tone, never a fill ─────────────────────────────────────

export const BADGE_W = 15
type BadgeTone = 'critical' | 'warning' | 'info' | 'success' | 'signature' | 'subtle' | 'disabled'
export type BadgeSpec = { readonly glyph: string; readonly tone: BadgeTone; readonly bold: boolean }
export const BADGES: Readonly<Record<string, BadgeSpec>> = {
  BLOCKED: { glyph: '▲', tone: 'critical', bold: true },
  ACTION: { glyph: '◆', tone: 'warning', bold: true },
  'IN REVIEW': { glyph: '●', tone: 'info', bold: true },
  'IN PROGRESS': { glyph: '●', tone: 'info', bold: true },
  TODO: { glyph: '○', tone: 'subtle', bold: false },
  DONE: { glyph: '✓', tone: 'success', bold: true },
  ARCHIVED: { glyph: '✓', tone: 'disabled', bold: false },
  INTERRUPTED: { glyph: '▲', tone: 'critical', bold: true },
  WIP: { glyph: '◆', tone: 'warning', bold: true },
  'HANDED OFF': { glyph: '●', tone: 'info', bold: true },
  UNKNOWN: { glyph: '?', tone: 'subtle', bold: false },
  OPEN: { glyph: '○', tone: 'subtle', bold: false },
  CLOSED: { glyph: '✓', tone: 'disabled', bold: false },
  'RESUMED FROM': { glyph: '↳', tone: 'signature', bold: false },
  'WRITTEN HERE': { glyph: '↳', tone: 'signature', bold: false },
  'FOR THIS TASK': { glyph: '↳', tone: 'signature', bold: false },
  RUNNING: { glyph: '○', tone: 'success', bold: false },
  REFUSED: { glyph: '▲', tone: 'critical', bold: true },
}
const UNKNOWN_BADGE: BadgeSpec = { glyph: '·', tone: 'subtle', bold: false }

function toneInk(k: Kit, tone: BadgeTone): string {
  if (tone === 'subtle') return k.t.fg.subtle
  if (tone === 'disabled') return k.t.fg.disabled
  if (tone === 'signature') return k.t.signatureText
  return k.t.tone[tone]
}

/** The ink a badge word draws in (a tally's count follows it). */
export const badgeInk = (k: Kit, word: string): string => toneInk(k, (BADGES[word.toUpperCase()] ?? UNKNOWN_BADGE).tone)

/** `▲ BLOCKED`: the glyph and the upper-case word in its tone; `pad` fills the label column; `dim` draws it faint. */
export function glyphBadge(k: Kit, word: string, a: { readonly pad?: boolean; readonly dim?: boolean } = {}): Piece {
  const { Text } = k.ui
  const upper = word.toUpperCase()
  const spec = BADGES[upper] ?? UNKNOWN_BADGE
  const text = `${spec.glyph} ${upper}`
  const shown = a.pad === true ? text.padEnd(BADGE_W) : text
  const dim = a.dim === true
  return {
    el: (
      <Text color={dim ? k.t.border.strong : toneInk(k, spec.tone)} bold={!dim && spec.bold}>
        {shown}
      </Text>
    ),
    width: shown.length,
  }
}

/** A task status as its badge word: `in-review` → `IN REVIEW`; '' → `UNKNOWN`. */
export const statusBadgeWord = (status: string): string => (status === '' ? 'UNKNOWN' : status.toUpperCase().replace(/-/g, ' '))

// ── The key ladder (spec §15.3) ─────────────────────────────────────────────────────────────────────────────────────

export type Rung = 'primary' | 'secondary' | 'destructive'
export type ActionSpec = {
  /** The Button's key; its wrapper Box is `<id>-box`. */
  readonly id: string
  /** One digit or one lowercase letter (anything else throws). */
  readonly key: string
  /** Verb + object, naming what it acts on: `mark task done`, `copy handover`. */
  readonly label: string
  /** Default secondary. One primary per context; destructive keys go last in their row and ask y/n. */
  readonly rung?: Rung
  /** Present: drawn disabled (rung 4); a press toasts this reason instead of acting. */
  readonly dim?: string
  readonly autoFocus?: boolean
  /** The ground it sits on (the hover steps one up from it); default page. */
  readonly on?: RrGround
  readonly onPress: (press: UiPressArgument) => void
}

function checkKey(id: string, key: string): void {
  if (!HOTKEY.test(key)) throw new Error(`taskmaster-mods: key ${id}: hotkey "${key}" is not one digit or one lowercase letter`)
}

/**
 * An action key: one plain Button (hotkey + label) with a 3-cell ` k ` glyph laid over the engine's `k: ` prefix. Primary
 * fills the RR keycap behind the whole key; secondary is a bold signature letter; destructive a warning letter; disabled dim.
 */
export function actionKey(k: Kit, a: ActionSpec): Piece {
  const { Box, Text, Button } = k.ui
  const { t } = k
  checkKey(a.id, a.key)
  const rung = a.rung ?? 'secondary'
  const why = a.dim
  const on = a.on ?? 'page'
  const filled = rung === 'primary' && why === undefined
  const letter = why !== undefined ? t.fg.disabled : filled ? t.keycap.signature.ink : rung === 'destructive' ? t.tone.warning : t.signatureText
  // The pointer inverts a Button; un-inverted + explicit colours keep a hovered primary one solid, lighter block (never black).
  const lit = { backgroundColor: t.fg.bold, color: t.keycap.signature.ink, inverse: false } as const
  const button = (
    <Button
      key={a.id}
      plain
      hotkey={a.key}
      label={a.label}
      {...(rung === 'primary' ? { variant: 'primary' as const } : {})}
      {...(why !== undefined ? { dimColor: true } : {})}
      {...(a.autoFocus === true ? { autoFocus: true as const } : {})}
      hover={filled ? { ...lit, bold: true } : { bold: true, color: t.fg.bold }}
      onPress={why === undefined ? a.onPress : () => k.say(why)}
    >
      {filled ? (
        <Text color={t.keycap.signature.ink} bold hover={lit}>
          {a.label}
        </Text>
      ) : null}
    </Button>
  )
  if (k.surface !== 'terminal') {
    // A desktop draws its own native button: the letter sits beside it, never over it.
    return {
      el: (
        <Box key={`${a.id}-box`} flexDirection="row" columnGap={1} flexShrink={0}>
          <Text color={why !== undefined ? t.fg.disabled : t.signatureText} bold>
            {a.key}
          </Text>
          {button}
        </Box>
      ),
      width: 2 + a.label.length,
    }
  }
  const glyph =
    KEYCAP_FACE === 'overlay' ? (
      <Box position="absolute" left={0} top={0}>
        <Text {...(filled ? { backgroundColor: t.keycap.signature.bg, hover: lit } : {})} color={letter} bold={why === undefined}>
          {` ${a.key} `}
        </Text>
      </Box>
    ) : null
  return {
    el: (
      <Box
        key={`${a.id}-box`}
        flexDirection="row"
        flexShrink={0}
        alignSelf="flex-start"
        {...(filled ? { backgroundColor: t.keycap.signature.bg, paddingRight: 1 } : {})}
        hover={{ backgroundColor: filled ? t.fg.bold : stepUp(t, on) }}
      >
        {button}
        {glyph}
      </Box>
    ),
    width: 3 + a.label.length + (filled ? 1 : 0),
  }
}

/** Keys in rows of `width`, 3 cells apart, wrapping to a new row rather than dropping a key. */
export function actionRows(k: Kit, keys: readonly Piece[], width: number): RenderNode[] {
  const { Box } = k.ui
  const rows: Piece[][] = []
  let used = 0
  for (const key of keys) {
    const last = rows[rows.length - 1]
    if (last !== undefined && used + 3 + key.width <= width) {
      last.push(key)
      used += 3 + key.width
    } else {
      rows.push([key])
      used = key.width
    }
  }
  return rows.map(r => (
    <Box flexDirection="row" columnGap={3} alignItems="flex-start">
      {r.map(p => p.el)}
    </Box>
  ))
}

/** A y/n: the question in bold warning ink, then `y yes` and `n no` (n holds the focus: Enter cancels). Two rows when narrow. */
export function askRow(
  k: Kit,
  a: { readonly id: string; readonly question: string; readonly onYes: () => void; readonly onNo: () => void; readonly width: number; readonly on?: RrGround },
): RenderNode[] {
  const { Box, Text } = k.ui
  const on = a.on ?? 'page'
  const yes = actionKey(k, { id: `${a.id}-yes`, key: 'y', label: 'yes', on, onPress: a.onYes })
  const no = actionKey(k, { id: `${a.id}-no`, key: 'n', label: 'no', on, autoFocus: true, onPress: a.onNo })
  const keys = (
    <Box flexDirection="row" columnGap={3}>
      {yes.el}
      {no.el}
    </Box>
  )
  const pair = yes.width + 3 + no.width
  const text = truncate(a.question, Math.max(4, a.width))
  const question = (
    <Text color={k.t.tone.warning} bold>
      {text}
    </Text>
  )
  if (text.length + 3 + pair > a.width) return [question, keys]
  return [
    <Box flexDirection="row" columnGap={3}>
      {question}
      {keys}
    </Box>,
  ]
}

// ── The footer (component sheet FOOTER): nav and meta keys, never capped ──────────────────────────────────────────────

/** A footer key's Button: keyed `id` when given (review's `focus-next`, handovers' `move-down`), else `key:<letter>`. */
export type FooterKey = { readonly key: string; readonly onPress: () => void; readonly id?: string }
/** One footer entry: what it shows (`j/k`, `move`) and the Buttons that carry its hotkeys (none: another element has the key). */
export type FooterItem = { readonly keys: string; readonly word: string; readonly press?: readonly FooterKey[] }

/** The footer's words alone, cut to `width`: keys subtle, words disabled, ` · ` faint. */
export function hints(k: Kit, items: readonly FooterItem[], width: number): RenderNode {
  const { Box, Text } = k.ui
  const room = Math.max(1, width)
  const out: RenderNode[] = []
  let used = 0
  const push = (text: string, color: string): void => {
    if (used >= room) return
    const cut = truncate(text, room - used)
    used += cut.length
    out.push(<Text color={color}>{cut}</Text>)
  }
  items.forEach((item, i) => {
    if (i > 0) push(' · ', k.t.border.strong)
    push(item.keys, k.t.fg.subtle)
    push(` ${item.word}`, k.t.fg.disabled)
  })
  return <Box flexDirection="row">{out}</Box>
}

/** A pane's last row: the hints, and every listed key pressable (FOOTER_KEYS says how the keys are drawn). */
export function footer(k: Kit, items: readonly FooterItem[], width: number): RenderNode {
  const { Box, Text, Button } = k.ui
  const keys = items.flatMap(item => item.press ?? [])
  for (const p of keys) checkKey(p.id ?? `key:${p.key}`, p.key)
  if (FOOTER_KEYS === 'inline') {
    return (
      <Box flexDirection="row" columnGap={2} flexWrap="wrap">
        {items.map(item => {
          const press = item.press ?? []
          if (press.length === 0) return <Text color={k.t.fg.subtle}>{`${item.keys} ${item.word}`}</Text>
          return (
            <Box flexDirection="row">
              {press.map((p, n) => (
                <Button key={p.id ?? `key:${p.key}`} plain dimColor hotkey={p.key} label={n === press.length - 1 ? item.word : ''} onPress={p.onPress} />
              ))}
            </Box>
          )
        })}
      </Box>
    )
  }
  const room = Math.max(1, width, keys.length * 3)
  return (
    <Box flexDirection="row" width={room}>
      {keys.map(p => (
        <Button key={p.id ?? `key:${p.key}`} plain hotkey={p.key} label="" onPress={p.onPress} />
      ))}
      <Box position="absolute" top={0} left={0} width={room}>
        <Box flexDirection="row" width={room}>
          {hints(k, items, Math.max(1, width))}
          <Box flexGrow={1} />
        </Box>
      </Box>
    </Box>
  )
}

// ── The list item (component sheet ITEM, CARD, FIELD, SELECTION; spec §15.6) ─────────────────────────────────────────

export const MARK_W = 2
/** The label column: the selection mark and the padded badge. */
export const LABEL_COL = MARK_W + BADGE_W
/** Where an item's detail lines and a section's content start. */
export const CONTENT_COL = LABEL_COL + 2

export type MarkSpec = { readonly key: string; readonly selected: boolean; readonly onPress: () => void; readonly dim?: string }

/** The selection mark: `□` / `■` as a quiet Button (no hotkey: `s` in the footer selects the focused item); null draws two blanks. */
export function mark(k: Kit, m: MarkSpec | null): Piece {
  const { Box, Text, Button } = k.ui
  if (m === null) return { el: <Text>{'  '}</Text>, width: MARK_W }
  const glyph = m.selected ? '■' : '□'
  const color = m.dim !== undefined ? k.t.border.strong : m.selected ? k.t.border.focus : k.t.fg.disabled
  const why = m.dim
  return {
    el: (
      <Box key={`${m.key}-box`} flexDirection="row" paddingRight={1} flexShrink={0} hover={{ backgroundColor: stepUp(k.t, 'page') }}>
        <Button key={m.key} plain label={glyph} onPress={why === undefined ? m.onPress : () => k.say(why)}>
          <Text color={color}>{glyph}</Text>
        </Button>
      </Box>
    ),
    width: MARK_W,
  }
}

export type HeadSpec = {
  readonly mark: MarkSpec | null
  /** A BADGES word: `ACTION`, `OPEN`, `INTERRUPTED`. */
  readonly badge: string
  /** A bold id before the title (review); absent, the title itself is bold (handovers, restore). */
  readonly ident?: string
  readonly title: string
  /** Subtle words before the age (a branch, a ctx size). */
  readonly meta?: string
  /** `30h`, `16:29`: right-aligned in 5 cells. */
  readonly age: string
  readonly dim?: boolean
  /** Present: the title is a quiet Button keyed `key` (a click or the Tab ring picks the item: handovers' `ho:<id>`). */
  readonly pick?: { readonly key: string; readonly onPress: () => void }
}

/** An item's first row: mark, badge, id, title, then meta and age at the right; exactly `width` cells when it fits. */
export function itemHead(k: Kit, a: HeadSpec, width: number): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const dim = a.dim === true
  const faint = t.border.strong
  const m = mark(k, a.mark)
  const b = glyphBadge(k, a.badge, { pad: true, dim })
  const age = a.age.padStart(5)
  const identRoom = Math.max(4, width - m.width - b.width - age.length - 4)
  const ident = a.ident === undefined || a.ident === '' ? '' : `${truncate(a.ident, identRoom)}  `
  const metaWanted = a.meta === undefined || a.meta === '' ? '' : `${a.meta}   `
  const meta = width - m.width - b.width - ident.length - metaWanted.length - age.length >= 8 ? metaWanted : ''
  const room = Math.max(0, width - m.width - b.width - ident.length - meta.length - age.length - 2)
  const title = truncate(a.title, room)
  const gap = Math.max(0, width - m.width - b.width - ident.length - title.length - meta.length - age.length)
  const { Button } = k.ui
  const titleText = (
    <Text color={dim ? faint : ident === '' ? t.fg.bold : t.fg.default} bold={!dim && ident === ''}>
      {title}
    </Text>
  )
  return (
    <Box flexDirection="row">
      {m.el}
      {b.el}
      {ident === '' ? null : (
        <Text color={dim ? faint : t.fg.bold} bold={!dim}>
          {ident}
        </Text>
      )}
      {a.pick === undefined ? (
        titleText
      ) : (
        <Button key={a.pick.key} plain label={title === '' ? ' ' : title} onPress={a.pick.onPress}>
          {titleText}
        </Button>
      )}
      <Text>{' '.repeat(gap)}</Text>
      {meta === '' ? null : <Text color={dim ? faint : t.fg.subtle}>{meta}</Text>}
      <Text color={dim ? faint : t.fg.subtle}>{age}</Text>
    </Box>
  )
}

/** A detail line in the content column, cut to the room. */
export function itemLine(k: Kit, segs: readonly Seg[], width: number): RenderNode {
  const { Box } = k.ui
  return <Box paddingLeft={CONTENT_COL}>{line(k, segs, Math.max(1, width - CONTENT_COL)).el}</Box>
}

/** A field: the lowercase name in signature ink beside its value (`next  Record the merge`). */
export const fieldSegs = (k: Kit, name: string, value: string): Seg[] => [
  { text: `${name}  `, color: k.t.signatureText },
  { text: value, color: k.t.fg.default },
]

/** A collapsed item: its head and 1–2 detail lines; no keys. */
export function item(k: Kit, head: HeadSpec, lines: readonly (readonly Seg[])[], width: number): RenderNode[] {
  return [itemHead(k, head, width), ...lines.map(l => itemLine(k, l, width))]
}

/** The focused item: boxed in the focus border, its lines, any extra rows, then a blank row and its action rows. */
export function focusedItem(
  k: Kit,
  a: {
    readonly id: string
    readonly head: HeadSpec
    readonly lines: readonly (readonly Seg[])[]
    readonly extra?: readonly RenderNode[]
    readonly actions: readonly RenderNode[]
  },
  width: number,
): RenderNode {
  const { Box, Text } = k.ui
  const inner = Math.max(10, width - 4)
  return (
    <Box key={`card:${a.id}`} flexDirection="column" borderStyle="round" borderColor={k.t.border.focus} paddingX={1}>
      {itemHead(k, a.head, inner)}
      {a.lines.map(l => itemLine(k, l, inner))}
      {[...(a.extra ?? [])]}
      {a.actions.length === 0 ? null : <Text> </Text>}
      {a.actions.length === 0 ? null : (
        <Box flexDirection="column" paddingLeft={1}>
          {[...a.actions]}
        </Box>
      )}
    </Box>
  )
}

/** The dim `┄` rule between two collapsed items, two cells in. */
export function itemRule(k: Kit, width: number): RenderNode {
  const { Text } = k.ui
  return <Text color={k.t.border.default}>{`  ${'┄'.repeat(Math.max(0, width - 2))}`}</Text>
}

export type ListEntry = { readonly focused: boolean; readonly nodes: readonly RenderNode[] }

/** Items in order, a rule between two collapsed neighbours (the focused box needs none). */
export function listing(k: Kit, entries: readonly ListEntry[], width: number): RenderNode[] {
  const out: RenderNode[] = []
  entries.forEach((entry, i) => {
    const prev = entries[i - 1]
    if (prev !== undefined && !prev.focused && !entry.focused) out.push(itemRule(k, width))
    out.push(...entry.nodes)
  })
  return out
}

// ── The frame (component sheet FRAME) ──────────────────────────────────────────────────────────────────────────────

export type Health = { readonly kind: RrSignalKind; readonly word: string }
const HEALTH_GLYPH: Readonly<Record<RrSignalKind, string>> = { success: '●', warning: '▲', critical: '▲', info: '○' }

/**
 * A pane: round border in border.default on the page ground, `◆ TASKMASTER · NAME` set into the top border (desktop: a first
 * row), the health at the top right, the children, and an optional overlay drawn last at the body's top-left (the sidebar).
 */
export function paneFrame(
  k: Kit,
  a: { readonly name: string; readonly health?: Health; readonly width: number },
  children: readonly RenderNode[],
  overlay: RenderNode | null = null,
): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const title = (
    <Text>
      <Text color={t.signatureText}>◆ </Text>
      <Text color={t.fg.subtle}>TASKMASTER · </Text>
      <Text color={t.fg.bold} bold>
        {a.name.toUpperCase()}
      </Text>
    </Text>
  )
  const health =
    a.health === undefined ? null : (
      <Text>
        <Text color={t.tone[a.health.kind]}>{`${HEALTH_GLYPH[a.health.kind]} `}</Text>
        <Text color={t.fg.subtle}>{a.health.word}</Text>
      </Text>
    )
  const frame = {
    flexDirection: 'column' as const,
    borderStyle: 'round',
    borderColor: t.border.default,
    backgroundColor: t.surface.page,
    paddingX: 1,
    width: a.width,
  }
  const over =
    overlay === null ? null : (
      <Box position="absolute" top={0} left={0}>
        {overlay}
      </Box>
    )
  if (PANEL_TITLE[k.surface === 'desktop' ? 'desktop' : 'terminal'] === 'row') {
    return (
      <Box {...frame}>
        <Box flexDirection="row">
          {title}
          <Box flexGrow={1} />
          {health}
        </Box>
        {[...children]}
        {over}
      </Box>
    )
  }
  return (
    <Box {...frame}>
      <Box position="absolute" top={-1} left={1} backgroundColor={t.surface.page} paddingX={1}>
        {title}
      </Box>
      {health === null ? null : (
        <Box position="absolute" top={-1} right={1} backgroundColor={t.surface.page} paddingX={1}>
          {health}
        </Box>
      )}
      {[...children]}
      {over}
    </Box>
  )
}

// ── Toolbar pieces (component sheet TOOLBAR): clickable, no hotkeys — their keys live in the footer ───────────────────

/** Row 1: the left piece (the picker, or Session's words), a spacer, then the right pieces 3 cells apart; narrow, the first right pieces give way. */
export function paneToolbar(k: Kit, a: { readonly left: Piece; readonly right: readonly Piece[]; readonly width: number }): RenderNode {
  const { Box } = k.ui
  const kept = [...a.right]
  const used = (): number => kept.reduce((n, p) => n + p.width, 0) + 3 * Math.max(0, kept.length - 1)
  while (kept.length > 1 && a.left.width + 2 + used() > a.width) kept.shift()
  return (
    <Box flexDirection="row" alignItems="flex-start">
      {a.left.el}
      <Box flexGrow={1} />
      <Box flexDirection="row" columnGap={3}>
        {kept.map(p => p.el)}
      </Box>
    </Box>
  )
}

function quietButton(k: Kit, id: string, text: string, color: string, onPress: () => void, why = ''): RenderNode {
  const { Box, Text, Button } = k.ui
  return (
    <Box key={`${id}-box`} flexDirection="row" hover={{ backgroundColor: stepUp(k.t, 'page') }}>
      <Button key={id} plain label={text} {...(why === '' ? {} : { dimColor: true })} onPress={why === '' ? onPress : () => k.say(why)}>
        <Text color={why === '' ? color : k.t.border.strong}>{text}</Text>
      </Button>
    </Box>
  )
}

/** `‹ 1 / 2 ›`: the arrows are quiet Buttons (`p` / `n` live in the footer); at an end the arrow dims and says why. */
export function pagerPiece(k: Kit, a: { readonly page: number; readonly pages: number; readonly onTurn: (page: number) => void }): Piece {
  const { Box, Text } = k.ui
  const pages = Math.max(1, a.pages)
  const at = Math.min(Math.max(0, a.page), pages - 1)
  const count = ` ${at + 1} / ${pages} `
  return {
    el: (
      <Box flexDirection="row">
        {quietButton(k, 'page-prev', '‹', k.t.fg.subtle, () => a.onTurn(at - 1), at === 0 ? 'Already on the first page' : '')}
        <Text color={k.t.fg.subtle}>{count}</Text>
        {quietButton(k, 'page-next', '›', k.t.fg.subtle, () => a.onTurn(at + 1), at === pages - 1 ? 'Already on the last page' : '')}
      </Box>
    ),
    width: count.length + 2,
  }
}

/** A toolbar toggle (`◐ show closed`): signature ink while on, disabled ink while off. */
export function togglePiece(k: Kit, a: { readonly id: string; readonly label: string; readonly on: boolean; readonly onPress: () => void }): Piece {
  return { el: quietButton(k, a.id, a.label, a.on ? k.t.signatureText : k.t.fg.disabled, a.onPress), width: a.label.length }
}

/** `u refresh` (`u rescan`): a quiet Button keyed `refresh`, the u subtle and the word disabled (`u` itself is a footer key). */
export function refreshPiece(k: Kit, a: { readonly label: string; readonly onPress: () => void }): Piece {
  const { Box, Text, Button } = k.ui
  return {
    el: (
      <Box key="refresh-box" flexDirection="row" hover={{ backgroundColor: stepUp(k.t, 'page') }}>
        <Button key="refresh" plain label={`u ${a.label}`} onPress={a.onPress}>
          <Text color={k.t.fg.subtle}>u</Text>
          <Text color={k.t.fg.disabled}>{` ${a.label}`}</Text>
        </Button>
      </Box>
    ),
    width: 2 + a.label.length,
  }
}

// ── Summary, bulk, section, label, empty and offline rows ───────────────────────────────────────────────────────────

export type Tally = { readonly word: string; readonly count: number }

/** Row 2: the sentence, then each tally as its badge and count (zero faint); tallies give way from the end. */
export function summaryRow(k: Kit, sentence: string, tallies: readonly Tally[], width: number): RenderNode {
  const { Box, Text } = k.ui
  const lead = line(k, [{ text: sentence, color: k.t.fg.subtle }], width)
  const pieces: Piece[] = tallies.map(tally => {
    const badge = glyphBadge(k, tally.word, { dim: tally.count === 0 })
    const count = ` ${tally.count}`
    return {
      el: (
        <Box flexDirection="row">
          {badge.el}
          <Text color={tally.count === 0 ? k.t.border.strong : badgeInk(k, tally.word)}>{count}</Text>
        </Box>
      ),
      width: badge.width + count.length,
    }
  })
  return (
    <Box flexDirection="row" columnGap={3}>
      {fit([lead, ...pieces], width, 3)}
    </Box>
  )
}

export type Scope = { readonly id: string; readonly label: string; readonly onPress: () => void }

/** Row 3: bulk keys at the left (their labels carry the count), scope Buttons (`all · none`) at the right. */
export function bulkRow(k: Kit, a: { readonly keys: readonly Piece[]; readonly scopes: readonly Scope[]; readonly width: number }): RenderNode {
  const { Box, Text } = k.ui
  const scopeWidth = a.scopes.reduce((n, s) => n + s.label.length, 0) + 3 * Math.max(0, a.scopes.length - 1)
  return (
    <Box flexDirection="row" alignItems="flex-start">
      <Box flexDirection="row" columnGap={3}>
        {fit(a.keys, Math.max(1, a.width - scopeWidth - 2), 3)}
      </Box>
      <Box flexGrow={1} />
      <Box flexDirection="row">
        {a.scopes.flatMap((s, i) => [...(i === 0 ? [] : [<Text color={k.t.border.strong}> · </Text>]), quietButton(k, s.id, s.label, k.t.fg.subtle, s.onPress)])}
      </Box>
    </Box>
  )
}

/** A section row in a list: `  HANDED OFF  1 · an open handover carries the next step`. */
export function sectionRow(k: Kit, label: string, note: string, width: number): RenderNode {
  return line(
    k,
    [
      { text: '  ', color: k.t.fg.subtle },
      { text: label, color: k.t.fg.subtle },
      { text: note === '' ? '' : `  ${note}`, color: k.t.fg.disabled },
    ],
    width,
  ).el
}

/** Session's label column: `  TASK` padded to the content column, then the segments. */
export function labelRow(k: Kit, label: string, segs: readonly Seg[], width: number): RenderNode {
  return line(k, [{ text: `  ${label.padEnd(LABEL_COL)}`, color: k.t.fg.subtle }, ...segs], width).el
}

/** Rows (action rows, a y/n) indented to the content column. */
export function underLabel(k: Kit, rows: readonly RenderNode[]): RenderNode {
  const { Box } = k.ui
  return (
    <Box flexDirection="column" paddingLeft={CONTENT_COL}>
      {[...rows]}
    </Box>
  )
}

/** EMPTY: one sentence and, when there is one, the one way out. */
export function emptyRow(k: Kit, sentence: string, way: Piece | null, width: number): RenderNode {
  const { Box } = k.ui
  const text = line(k, [{ text: sentence, color: k.t.fg.subtle }], Math.max(1, width - (way === null ? 0 : way.width + 3)))
  return (
    <Box flexDirection="row" columnGap={3}>
      {text.el}
      {way === null ? null : way.el}
    </Box>
  )
}

/** OFFLINE: `▲ tm offline · <detail>` and the retry key. */
export function offlineRow(k: Kit, detail: string, retry: Piece, width: number): RenderNode {
  const { Box } = k.ui
  const words = line(
    k,
    [
      { text: '▲ tm offline', color: k.t.tone.critical, bold: true },
      { text: detail === '' ? '' : ` · ${detail}`, color: k.t.fg.subtle },
    ],
    Math.max(12, width - retry.width - 3),
  )
  return (
    <Box flexDirection="row" columnGap={3}>
      {words.el}
      {retry.el}
    </Box>
  )
}

// ── The phase picker and its dropdown (spec §15.4) ─────────────────────────────────────────────────────────────────

export const MENU_W = 44

function tabsOf(s: StripView) {
  const tabs = orderPhases(s.view.phases, s.allCount, s.view.choice)
  const showDone = isDoneShown(tabs, s.view.showDone)
  return { tabs, showDone, shown: shownTabs(tabs, s.view.choice, showDone) }
}

/** `‹ [ Patch 1.9 · active  3 ▾ ] ›`: quiet Buttons; `h` / `l` / `f` are the footer's (phaseFooter). */
export function phasePicker(k: Kit, s: StripView, on: StripHandlers): Piece {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const { tabs, shown } = tabsOf(s)
  const choice = s.view.choice
  const chosen = tabs.find(tab => tab.id === choice) ?? tabs[0]
  const value = ` ${chosen === undefined ? 'All' : pickerName(chosen)}  ${chosen?.count ?? 0} ${s.view.overlay ? '▴' : '▾'} `
  return {
    el: (
      <Box flexDirection="row">
        {quietButton(k, 'phase-prev', '‹', t.fg.subtle, () => on.choose(step(shown, choice, -1)), atEnd(shown, choice, -1) ? 'Already on the first phase' : '')}
        <Text> </Text>
        <Box key="phase-field-box" flexDirection="row" hover={{ backgroundColor: stepUp(t, 'overlay') }}>
          <Button key="phase-field" plain label={value} onPress={() => on.overlay(!s.view.overlay)}>
            <Text backgroundColor={t.surface.overlay} color={t.fg.bold}>
              {value}
            </Text>
          </Button>
        </Box>
        <Text> </Text>
        {quietButton(k, 'phase-next', '›', t.fg.subtle, () => on.choose(step(shown, choice, 1)), atEnd(shown, choice, 1) ? 'Already on the last phase' : '')}
      </Box>
    ),
    width: value.length + 4,
  }
}

/** The footer's phase keys: `h/l phase` (h back, l on; at an end it says why) and `f phases` (opens or closes the list). */
export function phaseFooter(k: Kit, s: StripView, on: StripHandlers): FooterItem[] {
  const { shown } = tabsOf(s)
  const choice = s.view.choice
  const move = (dir: -1 | 1) => (): void => {
    if (atEnd(shown, choice, dir)) k.say(dir === -1 ? 'Already on the first phase' : 'Already on the last phase')
    else on.choose(step(shown, choice, dir))
  }
  return [
    { keys: 'h/l', word: 'phase', press: [{ key: 'h', onPress: move(-1) }, { key: 'l', onPress: move(1) }] },
    { keys: 'f', word: 'phases', press: [{ key: 'f', onPress: () => on.overlay(!s.view.overlay) }] },
  ]
}

/** The open list: each shown phase with its count, `0`–`9` on the first ten, the chosen row tinted and focused, done rows dim. */
export function phaseMenu(k: Kit, s: StripView, on: StripHandlers): RenderNode {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const { shown, showDone } = tabsOf(s)
  const room = MENU_W - 4 - 3
  const rows = shown.map((tab, i) => {
    const digit = i <= 9 ? String(i) : undefined
    const name = pickerName(tab)
    const count = String(tab.count)
    const text = `${truncate(name, room - count.length - 1).padEnd(room - count.length)}${count}`
    const chosen = tab.id === s.view.choice
    const id = `phase-pick:${tab.id}`
    return (
      <Box key={`${id}-box`} flexDirection="row" {...(chosen ? { backgroundColor: t.tint24.overlay.signature } : {})} hover={{ backgroundColor: chosen ? t.tint24.overlay.signature : stepUp(t, 'overlay') }}>
        <Button
          key={id}
          plain
          {...(digit === undefined ? {} : { hotkey: digit })}
          label={digit === undefined ? `   ${text}` : text}
          {...(tab.dim && !chosen ? { dimColor: true } : {})}
          {...(chosen ? { autoFocus: true as const } : {})}
          onPress={() => on.choose(tab.id)}
        >
          {chosen ? (
            <Text color={t.fg.bold} bold>
              {text}
            </Text>
          ) : null}
        </Button>
        {digit === undefined || KEYCAP_FACE !== 'overlay' ? null : (
          <Box position="absolute" left={0} top={0}>
            <Text color={chosen ? t.fg.bold : t.fg.disabled}>{`${digit}  `}</Text>
          </Box>
        )}
      </Box>
    )
  })
  const toggle = (
    <Box flexDirection="row">
      {quietButton(k, 'phase-done', '◐ show done phases', t.signatureText, on.toggleDone)}
      <Box flexGrow={1} />
      <Text color={t.fg.subtle}>{showDone ? 'on' : 'off'}</Text>
    </Box>
  )
  const close = SIDEBAR_ESC === 'refuse' ? 'esc' : 'f'
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={t.border.strong} backgroundColor={t.surface.overlay} paddingX={1} width={MENU_W}>
      {rows}
      <Text> </Text>
      {toggle}
      {hints(k, [{ keys: 'Tab', word: 'move' }, { keys: 'enter', word: 'pick' }, { keys: '0-9', word: 'pick' }, { keys: close, word: 'close' }], MENU_W - 4)}
    </Box>
  )
}

/** A list pane's body under row 1: as it is while the list is closed; with the list floating over it (or replacing it) while open. */
export function withPhaseMenu(k: Kit, s: StripView, on: StripHandlers, body: readonly RenderNode[]): RenderNode[] {
  const { Box } = k.ui
  if (!s.view.overlay) return [...body]
  const menu = phaseMenu(k, s, on)
  if (MENU_PLACEMENT === 'swap') return [menu]
  return [
    <Box flexDirection="column">
      {[...body]}
      <Box position="absolute" top={0} left={2}>
        {menu}
      </Box>
    </Box>,
  ]
}

// ── The Taskmaster sidebar (spec §15.8): an overlay at the pane body's top-left ────────────────────────────────────────

export const SIDEBAR_W = 34

/** The sidebar: one row per pane (`1`–`4`), the current one tinted and focused, the bound task, then its hints. */
export function sidebar(k: Kit, v: SidebarView, go: (pane: TmPane) => void): RenderNode {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const inner = SIDEBAR_W - 4
  const room = inner - 3
  const rows = v.entries.map(entry => {
    const current = entry.pane === v.current
    const right = [entry.note, entry.count.padStart(2)].filter(part => part.trim() !== '').join('  ')
    const text = `${truncate(entry.name, room - right.length - 1).padEnd(room - right.length)}${right}`
    const id = `nav:${entry.pane}`
    const why = entry.dim
    return (
      <Box key={`${id}-box`} flexDirection="row" {...(current ? { backgroundColor: t.tint24.overlay.signature } : {})} hover={{ backgroundColor: current ? t.tint24.overlay.signature : stepUp(t, 'overlay') }}>
        <Button
          key={id}
          plain
          hotkey={entry.key}
          label={text}
          {...(why !== undefined ? { dimColor: true } : {})}
          {...(current ? { autoFocus: true as const } : {})}
          onPress={why === undefined ? () => go(entry.pane) : () => k.say(why)}
        >
          {current ? (
            <Text color={t.fg.bold} bold>
              {text}
            </Text>
          ) : null}
        </Button>
        {KEYCAP_FACE !== 'overlay' ? null : (
          <Box position="absolute" left={0} top={0}>
            <Text color={current ? t.fg.bold : t.fg.disabled}>{`${entry.key}  `}</Text>
          </Box>
        )}
      </Box>
    )
  })
  const close = SIDEBAR_ESC === 'refuse' ? 'esc' : 'm'
  const title = (
    <Text>
      <Text color={t.signatureText}>◆ </Text>
      <Text color={t.fg.bold} bold>
        TASKMASTER
      </Text>
    </Text>
  )
  const desktop = PANEL_TITLE[k.surface === 'desktop' ? 'desktop' : 'terminal'] === 'row'
  return (
    <Box flexDirection="column" width={SIDEBAR_W} borderStyle="round" borderColor={t.border.strong} backgroundColor={t.surface.overlay} paddingX={1}>
      {desktop ? (
        title
      ) : (
        <Box position="absolute" top={-1} left={1} backgroundColor={t.surface.overlay} paddingX={1}>
          {title}
        </Box>
      )}
      <Text> </Text>
      {rows}
      <Text> </Text>
      {line(k, [{ text: v.context, color: t.fg.disabled }], inner).el}
      <Text> </Text>
      {hints(k, [{ keys: 'Tab', word: 'move' }, { keys: 'enter', word: 'open' }], inner)}
      {hints(k, [{ keys: '1-4', word: 'jump' }, { keys: close, word: 'close' }], inner)}
    </Box>
  )
}

/** A pane's body with the sidebar floating over its top-left while open; the body alone while it is shut. */
export function withSidebar(k: Kit, body: RenderElement, v: SidebarView | null, go: (pane: TmPane) => void): RenderElement {
  const { Box } = k.ui
  if (v === null) return body
  return (
    <Box flexDirection="column" flexGrow={1}>
      {body}
      <Box position="absolute" top={0} left={0}>
        {sidebar(k, v, go)}
      </Box>
    </Box>
  )
}
