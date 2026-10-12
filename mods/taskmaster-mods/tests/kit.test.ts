// User intent: pin the small helpers the vocabulary builds on (badge, label, signal, rule, fit) and the hard rules the
// rework keeps: no rails, no motion on hover, no shadows.
import { describe, expect, test } from 'claude-code/testing'

import { badge, fit, label, rule, signal } from '../hooks/kit'
import { testKit, textsOf, walk } from './fixtures/kit-ui'
import { STRONG, STUB_TOKENS as T, TONE } from './fixtures/tokens'

describe('badge, label, signal, rule, fit', () => {
  test('a badge is its word upper-cased and padded on the tone tint with bold ink; dim greys it', () => {
    const k = testKit()
    const b = badge(k, 'in review', 'info')
    expect(textsOf(b.el)).toEqual([' IN REVIEW '])
    expect(walk(b.el)[0]?.props).toMatchObject({ backgroundColor: STRONG.info, color: T.fg.bold, bold: true })
    expect(b.width).toBe(' IN REVIEW '.length)
    expect(walk(badge(k, 'wip', 'warning', { strength: 12 }).el)[0]?.props?.backgroundColor).toBe(TONE.warning)
    expect(walk(badge(k, '0 waiting', 'warning', { dim: true }).el)[0]?.props).toMatchObject({ color: T.fg.disabled, bold: false })
  })

  test('label is upper-cased signature text; signal is glyph, bold word and subtle detail', () => {
    const k = testKit()
    expect(textsOf(label(k, 'review').el)).toEqual(['REVIEW'])
    const s = signal(k, 'critical', 'offline', 'tm')
    expect(textsOf(s.el)).toEqual(['◆', ' offline', '  tm'])
    expect(s.width).toBe('◆ offline  tm'.length)
  })

  test('rule is dashed in the default border colour', () => {
    const k = testKit()
    expect(textsOf(rule(k, 5))).toEqual(['┄┄┄┄┄'])
    expect(walk(rule(k, 5))[0]?.props?.color).toBe(T.border.default)
  })

  test('fit keeps pieces in order while they fit; the first always stays', () => {
    const k = testKit()
    const a = label(k, 'aaaa')
    const b = label(k, 'bbbb')
    expect(fit([a, null, b], 9, 1)).toHaveLength(2)
    expect(fit([a, b], 8, 1)).toHaveLength(1)
    expect(fit([a, b], 2, 1)).toHaveLength(1)
  })
})
