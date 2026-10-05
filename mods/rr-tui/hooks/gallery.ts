// User intent: the /rr-gallery body — every $.rr element in every state with sample data, so the RR terminal look is judged
// and tuned on one screen against screenshots.
import type { RenderNode } from 'claude-code'

import type { RrGround, RrLevel, RrSignalKind, RrTokens, RrTone } from '../types'
import * as kit from './kit'
import { RR_SOURCE } from './tokens'

const LEVELS: readonly RrLevel[] = ['raised', 'overlay', 'recessed']
const KINDS: readonly RrSignalKind[] = ['success', 'warning', 'critical', 'info']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const CHIP_GROUNDS: readonly Exclude<RrGround, 'page'>[] = ['raised', 'overlay']
// Tuning round 1: a Button's label has no colour of its own, so three ways of drawing one are compared by eye.
const LABEL_SETS: readonly { tone: RrTone; labels: readonly string[] }[] = [
  { tone: 'success', labels: ['done'] },
  { tone: 'warning', labels: ['back to agent'] },
  { tone: 'signature', labels: ['skip', 'open'] },
]
const VARIANT_CAPTIONS = ['a  chip, engine label', 'b  variant=primary', 'c  plain › + own text'] as const
const VARIANT_NOTES = [
  'a: chip treatment; the engine draws the label in the terminal default foreground',
  'b: Button variant=primary; the engine draws [ label ] in its accent colour, no tint',
  'c: plain Button holding the glyph ›, then our Text in foreground-bold on the chip tint',
] as const
const VARIANT_WIDTH = 22

const slug = (text: string) => text.replace(/\s+/g, '-')

const box = (props: Record<string, unknown>, ...children: unknown[]): RenderNode => h('Box', props, ...children) as RenderNode

export type Demo = (key: string, label: string, variant?: 'primary') => RenderNode

export function galleryTree(t: RrTokens, width: number, demo: Demo): RenderNode {
  const section = (name: string, ...rows: RenderNode[]) => box({ flexDirection: 'column' }, kit.label(t, { text: name }), ...rows)
  const buttons = (on: RrGround) =>
    box(
      { flexDirection: 'row', columnGap: 1, alignItems: 'flex-start' },
      ...(on === 'page' ? [box(kit.button(t, { treatment: 'outline', tone: 'success' }), demo('outline-page', 'done'))] : []),
      box(kit.button(t, { treatment: 'chip', tone: 'warning', on }), demo(`back-${on}`, 'back to agent')),
      box(kit.button(t, { treatment: 'chip', tone: 'signature', on }), demo(`skip-${on}`, 'skip')),
      box(kit.button(t, { treatment: 'chip', tone: 'signature', on }), demo(`open-${on}`, 'open')),
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
      buttons('page'),
      ...CHIP_GROUNDS.map(on => kit.surface(t, { level: on, children: [kit.row(t, { cells: [`on ${on}`], emphasis: 'quiet' }), buttons(on)] })),
    ),
    section(
      'button labels',
      box(
        { flexDirection: 'row', columnGap: 1 },
        ...VARIANT_CAPTIONS.map(caption => box({ width: VARIANT_WIDTH }, kit.row(t, { cells: [caption], emphasis: 'quiet' }))),
      ),
      ...LABEL_SETS.map(({ tone, labels }) => {
        const chip = kit.button(t, { treatment: 'chip', tone })
        const cell = (...children: RenderNode[]) => box({ flexDirection: 'row', columnGap: 1, width: VARIANT_WIDTH }, ...children)
        return box(
          { flexDirection: 'row', columnGap: 1 },
          cell(...labels.map(text => box(chip, demo(`labels-a-${slug(text)}`, text)))),
          cell(...labels.map(text => demo(`labels-b-${slug(text)}`, text, 'primary'))),
          cell(
            ...labels.map(text =>
              box(
                { key: `labels-c-${slug(text)}-box`, flexDirection: 'row', ...chip },
                demo(`labels-c-${slug(text)}`, '›'),
                h('Text', { color: t.fg.bold }, ` ${text}`),
              ),
            ),
          ),
        )
      }),
      ...VARIANT_NOTES.map(note => kit.row(t, { cells: [note], emphasis: 'quiet' })),
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
