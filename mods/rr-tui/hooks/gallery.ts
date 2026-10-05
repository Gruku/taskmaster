// User intent: the /rr-gallery body — every $.rr element in every state with sample data, so the RR terminal look is judged
// and tuned on one screen against screenshots.
import type { RenderNode } from 'claude-code'

import type { RrButtonProps, RrGround, RrLevel, RrSignalKind, RrStrength, RrTokens, RrTone, RrTreatment } from '../types'
import * as kit from './kit'
import { RR_SOURCE } from './tokens'

const LEVELS: readonly RrLevel[] = ['raised', 'overlay', 'recessed']
const KINDS: readonly RrSignalKind[] = ['success', 'warning', 'critical', 'info']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
// The buttons taskmaster-tui draws (d done is the one primary), then one chip per tone on each ground. Only the page rows arm
// hotkeys: two Buttons on one hotkey clash.
const PRIMARY_ROW: readonly { id: string; key: string; label: string; tone: RrTone }[] = [
  { id: 'back-to-agent', key: 'a', label: 'back to agent', tone: 'warning' },
  { id: 'skip', key: 's', label: 'skip', tone: 'signature' },
  { id: 'open', key: 'o', label: 'open', tone: 'signature' },
]
const TONE_SAMPLES: Readonly<Record<RrTone, { key: string; label: string }>> = {
  success: { key: 'r', label: 'resume' },
  warning: { key: 'w', label: 'wait' },
  critical: { key: 'x', label: 'discard' },
  info: { key: 'c', label: 'copy' },
  signature: { key: 'y', label: 'confirm' },
}

const box = (props: Record<string, unknown>, ...children: unknown[]): RenderNode => h('Box', props, ...children) as RenderNode

/** The consumer's Button in the recipe, drawn by register.tsx, which owns the press handlers. */
export type Demo = (id: string, label: string, press: RrButtonProps) => RenderNode

/** The recipe's wrapper: the treatment's keyed Box (the key scopes the Button's hover) around the consumer's Button. */
export function treated(
  t: RrTokens,
  a: { id: string; treatment: RrTreatment; tone: RrTone; on?: RrGround; strength?: RrStrength },
  pressable: RenderNode,
): RenderNode {
  return box({ key: `${a.id}-box`, ...kit.button(t, a) }, pressable)
}

export function galleryTree(t: RrTokens, width: number, demo: Demo): RenderNode {
  const section = (name: string, ...rows: RenderNode[]) => box({ flexDirection: 'column' }, kit.label(t, { text: name }), ...rows)
  const primaryRow = box(
    { flexDirection: 'row', columnGap: 1, alignItems: 'flex-start' },
    treated(t, { id: 'outline-page', treatment: 'outline', tone: 'success' }, demo('outline-page', 'done', kit.buttonProps(t, { key: 'd' }))),
    // The card's primary since 2026-10-06: the strong (24%) chip, one row tall on the secondaries' baseline.
    treated(t, { id: 'strong-page', treatment: 'chip', tone: 'success', strength: 24 }, demo('strong-page', 'done', kit.buttonProps(t, {}))),
    ...PRIMARY_ROW.map(b =>
      treated(t, { id: `${b.id}-page`, treatment: 'chip', tone: b.tone }, demo(`${b.id}-page`, b.label, kit.buttonProps(t, { key: b.key }))),
    ),
  )
  const toneRow = (on: RrGround) =>
    box(
      { flexDirection: 'row', columnGap: 1 },
      ...TONES.map(tone => {
        const id = `tones-${tone}-${on}`
        const press = kit.buttonProps(t, on === 'page' ? { key: TONE_SAMPLES[tone].key } : {})
        return treated(t, { id, treatment: 'chip', tone, on }, demo(id, TONE_SAMPLES[tone].label, press))
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
          ? box({ flexDirection: 'column' }, kit.row(t, { cells: ['on page · hotkeys armed here only'], emphasis: 'quiet' }), toneRow(on))
          : kit.surface(t, { level: on, children: [kit.row(t, { cells: [`on ${on}`], emphasis: 'quiet' }), toneRow(on)] }),
      ),
    ),
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
