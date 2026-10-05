// User intent: pin rr-tui's element contract — polarity choice, token shape, glyph+word signals, button treatments and a
// hue-free survivalist — without a session.
import { describe, expect, test } from 'claude-code/testing'

import * as kit from '../hooks/kit'
import { resolvePolarity, tokensFor } from '../hooks/polarity'
import { RR_TABLE } from '../hooks/tokens'
import type { RrPolarity, RrTone } from '../types'

const POLARITIES: readonly RrPolarity[] = ['dark', 'light', 'survivalist']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']

function colorsOf(node: unknown): string[] {
  if (node === null || typeof node !== 'object') return []
  const n = node as { props?: Record<string, unknown>; children?: unknown[] }
  const own = ['color', 'backgroundColor', 'borderColor'].map(k => n.props?.[k]).filter((v): v is string => typeof v === 'string')
  return [...own, ...(n.children ?? []).flatMap(colorsOf)]
}

function textOf(node: unknown): string {
  if (typeof node === 'string') return node
  if (node === null || typeof node !== 'object') return ''
  return ((node as { children?: unknown[] }).children ?? []).map(textOf).join('')
}

function everyElement(p: RrPolarity): unknown[] {
  const t = tokensFor(p)
  return [
    kit.surface(t, { level: 'raised', children: ['x'] }),
    kit.surface(t, { level: 'overlay', children: ['x'] }),
    kit.surface(t, { level: 'recessed', children: ['x'] }),
    kit.label(t, { text: 'review' }),
    ...(['success', 'warning', 'critical', 'info'] as const).map(kind => kit.signal(t, { kind, word: kind, detail: 'why' })),
    kit.row(t, { cells: ['a', 'b'], emphasis: 'strong' }),
    kit.row(t, { cells: ['a'], emphasis: 'quiet' }),
    kit.rule(t, { width: 12 }),
    ...TONES.flatMap(tone => [
      kit.keycap(t, { key: 'd', tone }),
      kit.chip(t, { text: tone, tone, strength: 12 }),
      kit.chip(t, { text: tone, tone, strength: 24 }),
      { props: kit.button(t, { treatment: 'outline', tone }) },
      { props: kit.button(t, { treatment: 'chip', tone }) },
    ]),
  ]
}

describe('polarity', () => {
  test('auto follows a theme named light, everything else is dark, and a pinned value wins', () => {
    expect(resolvePolarity('auto', 'light')).toBe('light')
    expect(resolvePolarity('auto', 'light-daltonized')).toBe('light')
    expect(resolvePolarity('auto', 'dark')).toBe('dark')
    expect(resolvePolarity('auto', undefined)).toBe('dark')
    expect(resolvePolarity('survivalist', 'light')).toBe('survivalist')
    expect(resolvePolarity('dark', 'light')).toBe('dark')
    expect(resolvePolarity('nonsense', 'light')).toBe('light')
  })
})

describe('tokens', () => {
  test('every polarity resolves every field to an opaque hex taken from the generated table', () => {
    for (const p of POLARITIES) {
      const t = tokensFor(p)
      expect(t.polarity).toBe(p)
      expect(t.signatureText).toBe(RR_TABLE[p]['signature-text'])
      expect(t.surface.recessed).toBe(RR_TABLE[p]['surface-recessed'])
      expect(t.tint24.overlay.critical).toBe(RR_TABLE[p]['tint24.critical@overlay'])
      for (const quoted of JSON.stringify(t).match(/"#[^"]*"/g) ?? []) expect(quoted).toMatch(/^"#[0-9a-f]{6}"$/)
    }
  })

  test('no alpha reaches the terminal: every table value is opaque hex', () => {
    for (const p of POLARITIES) for (const value of Object.values(RR_TABLE[p])) expect(value).toMatch(/^#[0-9a-f]{6}$/)
  })
})

describe('elements', () => {
  test('a signal carries glyph, word and detail; only the glyph takes the tone colour', () => {
    const t = tokensFor('dark')
    const tree = kit.signal(t, { kind: 'critical', word: 'refused', detail: 'outstanding gates' }) as unknown as {
      children: { props: { color: string } }[]
    }
    expect(textOf(tree)).toBe('◆ refused  outstanding gates')
    expect(tree.children[0]?.props.color).toBe(t.tone.critical)
    expect(tree.children[1]?.props.color).toBe(t.fg.bold)
  })

  test('outline is a round border in the tone, chip is the 12% tint on its ground, a state chip is 24% with its glyph', () => {
    const t = tokensFor('dark')
    expect(kit.button(t, { treatment: 'outline', tone: 'success' })).toEqual({ borderStyle: 'round', borderColor: t.tone.success })
    expect(kit.button(t, { treatment: 'chip', tone: 'warning', on: 'raised' })).toEqual({ backgroundColor: t.tint12.raised.warning, paddingX: 1 })
    const state = kit.chip(t, { text: 'confirm done?', tone: 'warning', strength: 24 }) as unknown as { props: { backgroundColor: string } }
    expect(state.props.backgroundColor).toBe(t.tint24.page.warning)
    expect(textOf(state)).toBe(' ▲ confirm done? ')
  })

  test('a rule is one line of the default border colour at the asked width', () => {
    const t = tokensFor('light')
    const rule = kit.rule(t, { width: 7 }) as unknown as { props: { color: string } }
    expect(textOf(rule)).toBe('───────')
    expect(rule.props.color).toBe(t.border.default)
  })

  test('survivalist carries no hue: every colour is a ground value, chips lose their tint, outlines go bold', () => {
    const surv = tokensFor('survivalist')
    const grounds = new Set(Object.entries(RR_TABLE.survivalist).filter(([k]) => /^ground-\d+$/.test(k)).map(([, v]) => v))
    for (const element of everyElement('survivalist')) for (const c of colorsOf(element)) expect(grounds.has(c)).toBe(true)
    expect(kit.button(surv, { treatment: 'chip', tone: 'critical' })).toEqual({ paddingX: 1 })
    expect(kit.button(surv, { treatment: 'outline', tone: 'success' })).toEqual({ borderStyle: 'bold', borderColor: surv.fg.bold })
    expect(textOf(kit.chip(surv, { text: 'refused', tone: 'critical', strength: 24 }))).toBe('[◆ refused]')
  })
})
