// User intent: the one element vocabulary every taskmaster-mods screen draws with (visual rework 2026-10-12, spec §15) —
// inks, glyph badges, the five-rung key ladder, labelled actions, the footer, list items, the frame, the phase picker and the
// Taskmaster sidebar — so every screen is built from the approved prototype's parts and nothing else.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, UiPressArgument } from 'claude-code'

import { HOTKEY, KEYCAP_FACE, type Kit, type Piece, stepUp } from './kit'
import { truncate } from './model'
import type { RrGround } from './rr'

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
  const button = (
    <Button
      key={a.id}
      plain
      hotkey={a.key}
      label={a.label}
      {...(rung === 'primary' ? { variant: 'primary' as const } : {})}
      {...(why !== undefined ? { dimColor: true } : {})}
      {...(a.autoFocus === true ? { autoFocus: true as const } : {})}
      hover={{ bold: true, color: filled ? t.keycap.signature.ink : t.fg.bold }}
      onPress={why === undefined ? a.onPress : () => k.say(why)}
    >
      {filled ? (
        <Text color={t.keycap.signature.ink} bold>
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
        <Text {...(filled ? { backgroundColor: t.keycap.signature.bg } : {})} color={letter} bold={why === undefined}>
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
        {...(filled ? { backgroundColor: t.keycap.signature.bg, paddingRight: 1 } : {})}
        hover={{ backgroundColor: filled ? t.keycap.signature.bg : stepUp(t, on) }}
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
