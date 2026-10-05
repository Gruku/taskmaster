// User intent: pin rr-tui's element contract — polarity choice, token shape, glyph+word signals, button treatments and a
// hue-free survivalist — without a session.
import { describe, expect, test } from 'claude-code/testing'

import * as kit from '../hooks/kit'
import { resolvePolarity, tokensFor } from '../hooks/polarity'
import { RR_TABLE } from '../hooks/tokens'
import type { RrGround, RrPolarity, RrTone } from '../types'

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
    kit.surface(t, { level: 'page', children: ['x'] }),
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
      ...(['page', 'raised', 'overlay'] as const).map(on => ({ props: kit.button(t, { treatment: 'chip', tone, on }) })),
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
      expect(t.surface.page).toBe(RR_TABLE[p]['bg-page'])
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

  test('a page surface paints bg-page, for pane roots', () => {
    for (const p of POLARITIES) {
      const page = kit.surface(tokensFor(p), { level: 'page', children: ['x'] }) as unknown as { props: { backgroundColor: string } }
      expect(page.props.backgroundColor).toBe(RR_TABLE[p]['bg-page'])
    }
  })

  test('a survivalist chip button steps one surface up from its ground, value only', () => {
    const surv = tokensFor('survivalist')
    const grounds = new Set(Object.entries(RR_TABLE.survivalist).filter(([k]) => /^ground-\d+$/.test(k)).map(([, v]) => v))
    const steps: readonly [RrGround, string][] = [
      ['page', surv.surface.raised],
      ['raised', surv.surface.overlay],
      ['overlay', surv.surface.raised],
    ]
    for (const [on, expected] of steps) {
      const bg = kit.button(surv, { treatment: 'chip', tone: 'warning', on }).backgroundColor ?? ''
      expect(bg).toBe(expected)
      expect(bg).not.toBe(surv.surface[on])
      expect(grounds.has(bg)).toBe(true)
      // RR's survivalist ramp is a warm neutral (blue sits a step or two under red and green), so value-only is a spread of
      // at most 2 per channel rather than r == g == b exactly.
      const [r, g, b] = [1, 3, 5].map(i => parseInt(bg.slice(i, i + 2), 16)) as [number, number, number]
      expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThanOrEqual(2)
    }
  })

  test('survivalist carries no hue: every colour is a ground value, chip buttons step a surface, outlines go bold', () => {
    const surv = tokensFor('survivalist')
    const grounds = new Set(Object.entries(RR_TABLE.survivalist).filter(([k]) => /^ground-\d+$/.test(k)).map(([, v]) => v))
    for (const element of everyElement('survivalist')) for (const c of colorsOf(element)) expect(grounds.has(c)).toBe(true)
    expect(kit.button(surv, { treatment: 'chip', tone: 'critical' })).toEqual({ backgroundColor: surv.surface.raised, paddingX: 1 })
    expect(kit.button(surv, { treatment: 'outline', tone: 'success' })).toEqual({ borderStyle: 'bold', borderColor: surv.fg.bold })
    expect(textOf(kit.chip(surv, { text: 'refused', tone: 'critical', strength: 24 }))).toBe('[◆ refused]')
  })
})
