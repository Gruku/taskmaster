// User intent: one interactive drawing kit for every taskmaster-mods surface (spec §4.1, §5) — keycaps, badges, panels,
// cards, toolbars, pagers, selection bars, confirm rows and the phase strip — in Reality Reprojection, synchronous after
// one fetch per render. JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { Elements, RenderNode, RenderSurface, UiPressArgument } from 'claude-code'

import { truncate } from './model'
import type { Rr, RrGround, RrSignalKind, RrStrength, RrTextProps, RrTokens, RrTone } from './rr'

export type Ui = Pick<Elements['terminal'], 'Box' | 'Text' | 'Button' | 'Input' | 'Select'>
/** A drawn piece and the cells it takes across, so a row can be fitted to the room before it is laid out. */
export type Piece = { readonly el: RenderNode; readonly width: number }
export type BadgeTable = Readonly<Record<RrStrength, Readonly<Record<RrGround, Readonly<Record<RrTone, RrTextProps>>>>>>
/** What every kit function draws with: fetched once per render (kitOf), then every call is synchronous. */
export type Kit = {
  readonly ui: Ui
  readonly t: RrTokens
  readonly surface: RenderSurface
  /** rr-tui's badge recipe for every strength, ground and tone. */
  readonly badges: BadgeTable
  /** Toasts a line: what a dim keycap says when pressed, instead of acting. */
  readonly say: (text: string) => void
}
/** The $.rr calls a kit needs. */
export type KitRr = Pick<Rr, 'tokens' | 'badgeProps'>

/**
 * What a keycap draws on the terminal (spec §14 item 5, Task 0.2): `overlay` lays RR's filled keycap over the engine's
 * `k: ` prefix, so it reads ` k  label`; `accent` leaves the engine's own accent-coloured `k: label`.
 */
export const KEYCAP_FACE: 'overlay' | 'accent' = 'overlay'
/** Where a panel's title sits, per surface (spec §14 item 4, Task 0.2): set into the top border, or a first row inside it. */
export const PANEL_TITLE: Readonly<Record<'terminal' | 'desktop', 'border' | 'row'>> = { terminal: 'border', desktop: 'row' }
/** The tag every Taskmaster panel title starts with. */
export const TM_TAG = '◆ TASKMASTER'
/** The only hotkeys the engine takes (probed 2026-10-11: `[`, `/`, `R` and space fail the render). */
export const HOTKEY = /^[a-z0-9]$/

const SIGNAL_GLYPH: Readonly<Record<RrSignalKind, string>> = { success: '●', warning: '▲', critical: '◆', info: 'ⓘ' }
const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const STRENGTHS: readonly RrStrength[] = [12, 24]
const RUNG: Readonly<Record<RrGround, number>> = { page: 0, raised: 1, overlay: 2 }

// rr-tui's badge recipe, fetched once per polarity and reused by every later render (a reload starts afresh).
const badgeCache = new Map<string, BadgeTable>()

export async function kitOf(ui: Ui, rr: KitRr, surface: RenderSurface, say: (text: string) => void): Promise<Kit> {
  const t = await rr.tokens()
  let badges = badgeCache.get(t.polarity)
  if (badges === undefined) {
    const cells = await Promise.all(
      STRENGTHS.flatMap(strength =>
        GROUNDS.flatMap(on => TONES.map(async tone => ({ strength, on, tone, props: await rr.badgeProps({ tone, strength, on }) }))),
      ),
    )
    const table = Object.fromEntries(STRENGTHS.map(strength => [strength, Object.fromEntries(GROUNDS.map(on => [on, {}]))])) as unknown as Record<
      RrStrength,
      Record<RrGround, Record<RrTone, RrTextProps>>
    >
    for (const cell of cells) table[cell.strength][cell.on][cell.tone] = cell.props
    badges = table
    badgeCache.set(t.polarity, badges)
  }
  return { ui, t, surface, badges, say }
}

/** The ladder a card and a hover climb: page, raised, overlay, then the signature tints above overlay (surface stepping). */
function rung(t: RrTokens, at: number): string {
  const ladder = [t.surface.page, t.surface.raised, t.surface.overlay, t.tint12.overlay.signature, t.tint24.overlay.signature]
  return ladder[Math.min(ladder.length - 1, Math.max(0, at))] ?? t.surface.overlay
}

/** One surface step up from a ground: every hover fill (colour only, never motion). */
export const stepUp = (t: RrTokens, on: RrGround): string => rung(t, RUNG[on] + 1)

/** Keeps the pieces in order while they fit `width` with `gap` between them; the first piece always stays. */
export function fit(pieces: readonly (Piece | null)[], width: number, gap: number): RenderNode[] {
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

/** Pieces side by side, top-aligned so a taller piece never stretches its neighbours. */
export function row(k: Kit, pieces: readonly Piece[], gap = 1): Piece {
  const { Box } = k.ui
  return {
    el: (
      <Box flexDirection="row" columnGap={gap} alignItems="flex-start">
        {pieces.map(p => p.el)}
      </Box>
    ),
    width: pieces.reduce((sum, p) => sum + p.width, 0) + gap * Math.max(0, pieces.length - 1),
  }
}

/** RR's section label: the word upper-cased in signature text (bold in survivalist, which has no hue). */
export function label(k: Kit, text: string): Piece {
  const { Text } = k.ui
  const word = text.toUpperCase()
  return {
    el: (
      <Text color={k.t.signatureText} bold={k.t.polarity === 'survivalist'}>
        {word}
      </Text>
    ),
    width: word.length,
  }
}

/** RR's signal: the glyph in the tone, the word bold, an optional subtle detail; colour never travels without its shape. */
export function signal(k: Kit, kind: RrSignalKind, word: string, detail = ''): Piece {
  const { Box, Text } = k.ui
  return {
    el: (
      <Box flexDirection="row">
        <Text color={k.t.tone[kind]}>{SIGNAL_GLYPH[kind]}</Text>
        <Text color={k.t.fg.bold} bold>
          {` ${word}`}
        </Text>
        {detail === '' ? null : <Text color={k.t.fg.subtle}>{`  ${detail}`}</Text>}
      </Box>
    ),
    width: 2 + word.length + (detail === '' ? 0 : 2 + detail.length),
  }
}

export type KeycapSpec = {
  /** The Button's key (its `ui.press` element); the wrapper Box is `<id>-box`. */
  readonly id: string
  /** One digit or one lowercase letter (anything else throws); absent: no hotkey and no glyph, the label alone. */
  readonly key?: string
  readonly label: string
  /** The glyph's fill; default signature. */
  readonly tone?: RrTone
  readonly onPress: (press: UiPressArgument) => void
  /** Present: drawn dim and inert — a press toasts this reason instead of acting (a fixed slot dims, never vanishes). */
  readonly dim?: string
  /** Drawn dim but still acting: an empty or done phase tab, selectable all the same. */
  readonly faded?: boolean
  /** Chosen (a phase tab, an on toggle): the signature 24% tint behind the whole keycap. */
  readonly selected?: boolean
  readonly autoFocus?: boolean
  /** The ground it sits on (hover and selected fills); default page. */
  readonly on?: RrGround
}

/** A keycap: RR's filled key glyph and a plain label, one Button that the key, a click or Enter presses. */
export function keycap(k: Kit, c: KeycapSpec): Piece {
  const { Box, Text, Button } = k.ui
  const { t } = k
  if (c.key !== undefined && !HOTKEY.test(c.key)) {
    throw new Error(`taskmaster-mods: keycap ${c.id}: hotkey "${c.key}" is not one digit or one lowercase letter`)
  }
  const on = c.on ?? 'page'
  const why = c.dim
  const cap = why === undefined ? t.keycap[c.tone ?? 'signature'] : { bg: t.surface.overlay, ink: t.fg.disabled }
  const fill = c.selected === true ? t.tint24[on].signature : undefined
  const glyph =
    c.key === undefined ? null : (
      <Text backgroundColor={cap.bg} color={cap.ink} bold>
        {` ${c.key} `}
      </Text>
    )
  const button = (
    <Button
      key={c.id}
      plain
      {...(c.key === undefined ? {} : { hotkey: c.key })}
      label={c.label}
      {...(why !== undefined || c.faded === true ? { dimColor: true } : {})}
      {...(c.autoFocus === true ? { autoFocus: true as const } : {})}
      hover={{ bold: true, color: t.fg.bold }}
      onPress={why === undefined ? c.onPress : () => k.say(why)}
    />
  )
  const width = (c.key === undefined ? 0 : 3) + c.label.length
  const ground = fill === undefined ? {} : { backgroundColor: fill }
  if (k.surface !== 'terminal') {
    // A desktop draws its own native button: the RR glyph sits beside it, never over it.
    return {
      el: (
        <Box key={`${c.id}-box`} flexDirection="row" columnGap={1} flexShrink={0} {...ground}>
          {glyph}
          {button}
        </Box>
      ),
      width: width + (glyph === null ? 0 : 1),
    }
  }
  return {
    el: (
      <Box key={`${c.id}-box`} flexDirection="row" flexShrink={0} {...ground} hover={{ backgroundColor: fill ?? stepUp(t, on) }}>
        {button}
        {glyph !== null && KEYCAP_FACE === 'overlay' ? (
          <Box position="absolute" left={0} top={0}>
            {glyph}
          </Box>
        ) : null}
      </Box>
    ),
    width,
  }
}

/** A card's select box: `□` / `■` as a keycap (the focused card's carries `s`). */
export function checkbox(
  k: Kit,
  a: { readonly id: string; readonly selected: boolean; readonly onPress: () => void; readonly key?: string; readonly dim?: string; readonly on?: RrGround },
): Piece {
  return keycap(k, { ...a, label: a.selected ? '■' : '□' })
}

/** A status badge: the word upper-cased on its tone's tint in bold ink (never a rail); dim for a zero count. */
export function badge(k: Kit, text: string, tone: RrTone, a: { readonly strength?: RrStrength; readonly on?: RrGround; readonly dim?: boolean } = {}): Piece {
  const { Text } = k.ui
  const word = ` ${text.toUpperCase()} `
  const on = a.on ?? 'page'
  const props: RrTextProps = a.dim === true ? { color: k.t.fg.disabled, backgroundColor: stepUp(k.t, on), bold: false } : k.badges[a.strength ?? 24][on][tone]
  return { el: <Text {...props}>{word}</Text>, width: word.length }
}

/** A subtle dashed separator across `width` cells. */
export function rule(k: Kit, width: number): RenderNode {
  const { Text } = k.ui
  return <Text color={k.t.border.default}>{'┄'.repeat(Math.max(0, Math.floor(width)))}</Text>
}

/** One subtle line of key hints, cut to `width`. */
export function helpFooter(k: Kit, items: readonly string[], width: number): RenderNode {
  const { Text } = k.ui
  return (
    <Text color={k.t.fg.subtle} wrap="truncate-end">
      {truncate(items.join(' · '), Math.max(1, width))}
    </Text>
  )
}

/**
 * The y/n confirm: the question on the warning tint, then `y yes` and `n no` (no is the focused safe default; Enter cancels).
 * One row when it fits, else the question above the pair: never y without n. Keys `<id>-yes` / `<id>-no`; drawn after the
 * pager, its `n` wins the clash (later wins).
 */
export function confirmRow(
  k: Kit,
  a: { readonly id: string; readonly question: string; readonly onYes: () => void; readonly onNo: () => void; readonly width: number; readonly on?: RrGround },
): RenderNode[] {
  const { Box, Text } = k.ui
  const on = a.on ?? 'page'
  const yes = keycap(k, { id: `${a.id}-yes`, key: 'y', label: 'yes', tone: 'success', onPress: a.onYes, on })
  const no = keycap(k, { id: `${a.id}-no`, key: 'n', label: 'no', autoFocus: true, onPress: a.onNo, on })
  const pair = row(k, [yes, no])
  const text = truncate(a.question, Math.max(4, a.width - 2))
  const question = <Text {...k.badges[24][on].warning}>{` ${text} `}</Text>
  if (text.length + 2 + 1 + pair.width > a.width) return [question, pair.el]
  return [
    <Box flexDirection="row" columnGap={1} alignItems="flex-start">
      {question}
      {pair.el}
    </Box>,
  ]
}

export type PanelSpec = {
  /** The whole title (panelTitle builds `◆ TASKMASTER · SESSION`). */
  readonly title: string
  /** The signal at the top right: `● tm ok`, `◆ offline`. */
  readonly health?: { readonly kind: RrSignalKind; readonly word: string }
  /** True: the body is not drawn; the panel is its frame and title alone. */
  readonly collapse?: boolean
  /** Cells across, the border included. */
  readonly width: number
}

export const panelTitle = (section: string): string => `${TM_TAG} · ${section.toUpperCase()}`

/**
 * A panel: full-perimeter round border, the raised fill across the whole box, the title in signature text set into the top
 * border (or on a first row: PANEL_TITLE), the health signal at the top right.
 */
export function panel(k: Kit, a: PanelSpec, children: readonly RenderNode[]): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const health = a.health === undefined ? null : signal(k, a.health.kind, a.health.word)
  const title = truncate(a.title, Math.max(1, a.width - 6 - (health === null ? 0 : health.width + 3)))
  const body = a.collapse === true ? [] : [...children]
  const frame = {
    flexDirection: 'column' as const,
    borderStyle: 'round',
    borderColor: t.border.strong,
    backgroundColor: t.surface.raised,
    paddingX: 1,
    width: a.width,
  }
  if (PANEL_TITLE[k.surface === 'desktop' ? 'desktop' : 'terminal'] === 'row') {
    return (
      <Box {...frame}>
        <Box flexDirection="row">
          <Text color={t.signatureText} bold>
            {title}
          </Text>
          <Box flexGrow={1} />
          {health === null ? null : health.el}
        </Box>
        {body}
      </Box>
    )
  }
  return (
    <Box {...frame}>
      <Box position="absolute" top={-1} left={1}>
        <Text color={t.signatureText} backgroundColor={t.surface.raised} bold>
          {` ${title} `}
        </Text>
      </Box>
      {health === null ? null : (
        <Box position="absolute" top={-1} right={1} backgroundColor={t.surface.raised} paddingX={1}>
          {health.el}
        </Box>
      )}
      {body}
    </Box>
  )
}

export type CardSpec = {
  /** The card's hover scope: its Box is keyed `card:<id>`. */
  readonly id: string
  readonly selected: boolean
  /** The focused card draws its full-perimeter focus border and its expanded body. */
  readonly focused: boolean
  /** The ground beneath the card; default page (a card sits one step up, a selected one two). */
  readonly on?: RrGround
}

/** A card: a surface step above its ground (selected one more), one step up again on hover, colour only; never a rail. */
export function card(k: Kit, a: CardSpec, collapsedRow: RenderNode, expandedBody: readonly RenderNode[]): RenderNode {
  const { Box } = k.ui
  const { t } = k
  const at = RUNG[a.on ?? 'page'] + 1 + (a.selected ? 1 : 0)
  // The unfocused card pads one more cell, so its text sits where the bordered (focused) card's does.
  const edge = a.focused ? { borderStyle: 'round', borderColor: t.border.focus, paddingX: 1 } : { paddingX: 2 }
  return (
    <Box key={`card:${a.id}`} flexDirection="column" backgroundColor={rung(t, at)} hover={{ backgroundColor: rung(t, at + 1) }} {...edge}>
      {collapsedRow}
      {a.focused ? [...expandedBody] : null}
    </Box>
  )
}

/** A pane's top row: its title, a spacer, then fixed slots (pager, toggles, refresh); inert slots dim, never vanish. */
export function toolbar(k: Kit, a: { readonly title: string; readonly slots: readonly Piece[]; readonly width: number }): RenderNode {
  const { Box } = k.ui
  const used = a.slots.reduce((sum, p) => sum + p.width + 2, 0)
  const title = label(k, truncate(a.title, Math.max(1, a.width - used)))
  return (
    <Box flexDirection="row" alignItems="flex-start">
      {title.el}
      <Box flexGrow={1} />
      <Box flexDirection="row" columnGap={2} alignItems="flex-start">
        {a.slots.map(p => p.el)}
      </Box>
    </Box>
  )
}

/** `p ‹`  `n ›`  `1 / 4`: both keycaps always drawn, dim at the ends (a press there says why); `onTurn` gets the 0-based page. */
export function pager(
  k: Kit,
  a: { readonly page: number; readonly pages: number; readonly onTurn: (page: number) => void; readonly on?: RrGround },
): Piece {
  const { Box, Text } = k.ui
  const pages = Math.max(1, a.pages)
  const at = Math.min(Math.max(0, a.page), pages - 1)
  const on = a.on ?? 'page'
  const prev = keycap(k, { id: 'page-prev', key: 'p', label: '‹', on, onPress: () => a.onTurn(at - 1), ...(at === 0 ? { dim: 'Already on the first page' } : {}) })
  const next = keycap(k, { id: 'page-next', key: 'n', label: '›', on, onPress: () => a.onTurn(at + 1), ...(at === pages - 1 ? { dim: 'Already on the last page' } : {}) })
  const count = `${at + 1} / ${pages}`
  return {
    el: (
      <Box flexDirection="row" columnGap={1} alignItems="flex-start">
        {prev.el}
        {next.el}
        <Text color={k.t.fg.subtle}>{count}</Text>
      </Box>
    ),
    width: prev.width + next.width + count.length + 2,
  }
}

/** A bulk action: `<verb> <count>` as a keycap; dim (with why) while nothing is selected. */
export type BulkAction = {
  readonly id: string
  readonly key?: string
  readonly verb: string
  readonly count: number
  readonly tone?: RrTone
  readonly onPress: () => void
}
/** A ghost: a quiet text button (all · none · unfinished). */
export type Ghost = { readonly id: string; readonly label: string; readonly onPress: () => void }

/** The selection bar: primary bulk actions with counts at the left, ghost selectors at the right. */
export function selectionBar(
  k: Kit,
  a: { readonly actions: readonly BulkAction[]; readonly ghosts: readonly Ghost[]; readonly width: number; readonly on?: RrGround },
): RenderNode {
  const { Box, Button, Text } = k.ui
  const { t } = k
  const on = a.on ?? 'page'
  const actions = a.actions.map(x =>
    keycap(k, {
      id: x.id,
      ...(x.key === undefined ? {} : { key: x.key }),
      label: `${x.verb} ${x.count}`,
      tone: x.tone ?? 'signature',
      on,
      onPress: x.onPress,
      ...(x.count === 0 ? { dim: `Nothing selected to ${x.verb.toLowerCase()}` } : {}),
    }),
  )
  const ghostWidth = a.ghosts.reduce((sum, g) => sum + g.label.length, 0) + 3 * Math.max(0, a.ghosts.length - 1)
  const ghosts = a.ghosts.flatMap((g, i) => [
    ...(i === 0 ? [] : [<Text color={t.fg.subtle}>·</Text>]),
    <Box key={`${g.id}-box`} flexDirection="row">
      <Button key={g.id} plain dimColor label={g.label} hover={{ bold: true, color: t.fg.bold }} onPress={g.onPress} />
    </Box>,
  ])
  return (
    <Box flexDirection="row" alignItems="flex-start">
      <Box flexDirection="row" columnGap={1} alignItems="flex-start">
        {fit(actions, Math.max(1, a.width - ghostWidth - 2), 1)}
      </Box>
      <Box flexGrow={1} />
      <Box flexDirection="row" columnGap={1}>
        {ghosts}
      </Box>
    </Box>
  )
}
