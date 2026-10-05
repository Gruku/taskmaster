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

const box = (props: Record<string, unknown>, ...children: unknown[]): RenderNode => h('Box', props, ...children) as RenderNode

export function galleryTree(t: RrTokens, width: number, demo: (key: string, label: string) => RenderNode): RenderNode {
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
