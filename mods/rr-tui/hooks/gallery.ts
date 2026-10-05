// User intent: the /rr-gallery body — every $.rr element in every state with sample data, so the RR terminal look is judged
// and tuned on one screen against screenshots.
import type { RenderNode, TextHoverProps } from 'claude-code'

import type { RrGround, RrLevel, RrSignalKind, RrTokens, RrTone, RrTreatment } from '../types'
import * as kit from './kit'
import { RR_SOURCE } from './tokens'

const LEVELS: readonly RrLevel[] = ['raised', 'overlay', 'recessed']
const KINDS: readonly RrSignalKind[] = ['success', 'warning', 'critical', 'info']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
// The buttons taskmaster-tui draws (d done is the one primary), then one chip per tone; only the page row arms hotkeys, since
// two Buttons on one hotkey clash.
const PRIMARY_ROW: readonly { id: string; key: string; label: string; tone: RrTone }[] = [
  { id: 'back-to-agent', key: 'a', label: 'back to agent', tone: 'warning' },
  { id: 'skip', key: 's', label: 'skip', tone: 'signature' },
  { id: 'open', key: 'o', label: 'open', tone: 'signature' },
]
const TONE_SAMPLES: Readonly<Record<RrTone, { key: string; label: string }>> = {
  success: { key: 'r', label: 'resume' },
  warning: { key: 'a', label: 'back' },
  critical: { key: 'x', label: 'discard' },
  info: { key: 'c', label: 'copy' },
  signature: { key: 'o', label: 'open' },
}

const box = (props: Record<string, unknown>, ...children: unknown[]): RenderNode => h('Box', props, ...children) as RenderNode

// Round 2 addendum, a temporary experiment: can the whole chip be the click target? Box has no onPress, so two probes, each
// on a warning and a signature chip with their own digit hotkeys (d/a/s/o are taken by the primary row).
const WHOLE_CHIP: readonly { tone: RrTone; label: string; keys: readonly [string, string] }[] = [
  { tone: 'warning', label: 'back to agent', keys: ['5', '7'] },
  { tone: 'signature', label: 'open', keys: ['6', '8'] },
]
const WHOLE_CHIP_NOTES = [
  '1: one plain Button is the chip; the engine draws `key: label`, hover makes the label bold foreground-bold',
  '2: a blank plain Button is the click target; an absolute Box paints our keycap and bold label over it',
] as const

/** A Button the gallery asks register.tsx to draw, since only it owns press handlers. */
export type DemoButton = {
  readonly label: string
  readonly hotkey?: string
  readonly plain?: true
  readonly hover?: TextHoverProps
  /** The toast on press; `rr-gallery: pressed <id>` when absent. */
  readonly toast?: string
}
export type Demo = (id: string, button: DemoButton) => RenderNode

/** A keyed button assembled from kit.keyedButton's parts around the consumer's own Button (the recipe in types/index.d.ts). */
export function keyed(
  t: RrTokens,
  a: { id: string; treatment: RrTreatment; tone: RrTone; on?: RrGround; label: string },
  pressable: RenderNode,
): RenderNode {
  const parts = kit.keyedButton(t, a)
  return box({ key: `${a.id}-box`, ...parts.box }, box(parts.keycap, pressable), parts.label)
}

export function galleryTree(t: RrTokens, width: number, demo: Demo): RenderNode {
  const section = (name: string, ...rows: RenderNode[]) => box({ flexDirection: 'column' }, kit.label(t, { text: name }), ...rows)
  const primaryRow = box(
    { flexDirection: 'row', columnGap: 1, alignItems: 'flex-start' },
    keyed(t, { id: 'outline-page', treatment: 'outline', tone: 'success', label: 'done' }, demo('outline-page', { label: 'd', hotkey: 'd' })),
    ...PRIMARY_ROW.map(b => keyed(t, { id: `${b.id}-page`, treatment: 'chip', tone: b.tone, label: b.label }, demo(`${b.id}-page`, { label: b.key, hotkey: b.key }))),
  )
  const toneRow = (on: RrGround) =>
    box(
      { flexDirection: 'row', columnGap: 1 },
      ...TONES.map(tone =>
        keyed(t, { id: `tones-${tone}-${on}`, treatment: 'chip', tone, on, label: TONE_SAMPLES[tone].label }, demo(`tones-${tone}-${on}`, { label: TONE_SAMPLES[tone].key })),
      ),
    )
  const wholeChip = (variant: 1 | 2) =>
    box(
      { flexDirection: 'row', columnGap: 1, alignItems: 'flex-start' },
      kit.row(t, { cells: [String(variant)], emphasis: 'quiet' }),
      ...WHOLE_CHIP.map(({ tone, label, keys }) => {
        const id = `whole-chip-${variant}-${tone}`
        const key = keys[variant - 1] ?? ''
        const ground = kit.button(t, { treatment: 'chip', tone }).backgroundColor
        if (variant === 1) {
          return box(
            { key: `${id}-box`, ...kit.button(t, { treatment: 'chip', tone }) },
            demo(id, { label, hotkey: key, plain: true, hover: { bold: true, color: t.fg.bold } }),
          )
        }
        // Visible width: paddingX 1 each side, the ` k ` keycap, then ` label`. The plain Button draws `k: ` before its label,
        // so its blank label is three columns short of that width.
        const width = label.length + 6
        return box(
          { key: `${id}-box`, flexDirection: 'row', backgroundColor: ground },
          demo(id, { label: ' '.repeat(width - 3), hotkey: key, plain: true, toast: 'whole-chip variant 2 pressed' }),
          box(
            { position: 'absolute', top: 0, left: 0, width, paddingX: 1, flexDirection: 'row', backgroundColor: ground },
            kit.keycap(t, { key, tone }),
            h('Text', { color: t.fg.bold, bold: true }, ` ${label}`),
          ),
        )
      }),
    )
  return box(
    { flexDirection: 'column', rowGap: 1 },
    section(
      'surfaces',
      ...LEVELS.map(level => kit.surface(t, { level, children: [kit.row(t, { cells: [level, 'surface stepping, no shadow'] })] })),
    ),
    section(
      'voices',
      kit.row(t, { cells: ['DECLARATION OPENS'], emphasis: 'strong' }),
      kit.row(t, { cells: ['Narrator carries the content.'] }),
      kit.row(t, { cells: ['technical · metadata · 3h'], emphasis: 'quiet' }),
    ),
    section('signals', ...KINDS.map(kind => kit.signal(t, { kind, word: kind, detail: `${kind} detail` }))),
    section(
      'buttons',
      primaryRow,
      ...GROUNDS.map(on =>
        on === 'page'
          ? box({ flexDirection: 'column' }, kit.row(t, { cells: ['on page'], emphasis: 'quiet' }), toneRow(on))
          : kit.surface(t, { level: on, children: [kit.row(t, { cells: [`on ${on}`], emphasis: 'quiet' }), toneRow(on)] }),
      ),
    ),
    section('whole-chip click', wholeChip(1), wholeChip(2), ...WHOLE_CHIP_NOTES.map(note => kit.row(t, { cells: [note], emphasis: 'quiet' }))),
    section(
      'states',
      box(
        { flexDirection: 'row', columnGap: 1 },
        kit.chip(t, { text: 'refused', tone: 'critical', strength: 24 }),
        kit.chip(t, { text: 'confirm done?', tone: 'warning', strength: 24 }),
        kit.chip(t, { text: 'signed off', tone: 'success', strength: 24 }),
      ),
      box({ flexDirection: 'row', columnGap: 1 }, ...TONES.map(tone => kit.chip(t, { text: tone, tone, strength: 12 }))),
    ),
    section(
      'keys',
      box(
        { flexDirection: 'row', columnGap: 1 },
        kit.keycap(t, { key: 'd', tone: 'success' }),
        kit.row(t, { cells: ['done'] }),
        kit.keycap(t, { key: 'a', tone: 'warning' }),
        kit.row(t, { cells: ['back'] }),
        kit.keycap(t, { key: 's', tone: 'signature' }),
        kit.row(t, { cells: ['skip'] }),
        kit.keycap(t, { key: 'o', tone: 'signature' }),
        kit.row(t, { cells: ['open'] }),
      ),
    ),
    section('rule', kit.rule(t, { width: Math.max(10, width - 2) })),
    kit.row(t, { cells: [`${t.polarity} · tokens ${RR_SOURCE.sha256.slice(0, 12)}`], emphasis: 'quiet' }),
  )
}
