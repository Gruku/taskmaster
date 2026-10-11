// User intent: pin the kit every surface is rebuilt on (spec §4.1) — keycaps that press by key, tinted badges, panels with
// their title in the border, stepped cards, fixed toolbar slots that dim — and the user's hard rules: no rails, no motion
// on hover, no shadows.
import { describe, expect, test } from 'claude-code/testing'

import { badge, checkbox, confirmRow, fit, helpFooter, KEYCAP_FACE, keycap, label, rule, signal } from '../hooks/kit'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STRONG, STUB_TOKENS as T, TONE } from './fixtures/tokens'

const noop = (): void => undefined

describe('keycap', () => {
  test('one plain Button keyed by id with its hotkey and label, in a keyed Box; the RR glyph sits over the engine prefix', () => {
    const k = testKit()
    const cap = keycap(k, { id: 'done', key: 'd', label: 'done', tone: 'success', onPress: noop })
    const box = byKey(cap.el, 'done-box')
    expect(box?.type).toBe('Box')
    const button = byKey(cap.el, 'done')
    expect(button).toMatchObject({ type: 'Button', props: { key: 'done', hotkey: 'd', label: 'done', plain: true }, hover: { bold: true, color: T.fg.bold } })
    expect(cap.width).toBe(3 + 'done'.length)
    expect(widthOf(cap.el)).toBe(cap.width)
    const glyph = walk(cap.el).find(e => e.type === 'Text' && (e.children ?? []).includes(' d '))
    if (KEYCAP_FACE === 'overlay') {
      expect(glyph?.props).toMatchObject({ backgroundColor: T.keycap.success.bg, color: T.keycap.success.ink, bold: true })
      expect(walk(cap.el).some(e => e.props?.position === 'absolute')).toBe(true)
    } else {
      expect(glyph).toBeUndefined()
    }
  })

  test('a hotkey the engine would refuse throws, naming the keycap', () => {
    const k = testKit()
    for (const bad of ['[', '/', 'R', ' ', 'ab', '']) {
      let refused = ''
      try {
        keycap(k, { id: 'x', key: bad, label: 'x', onPress: noop })
      } catch (error) {
        refused = String(error)
      }
      expect(refused.includes('keycap x') && refused.includes('one digit or one lowercase letter'), JSON.stringify(bad)).toBe(true)
    }
  })

  test('no key: the label alone, no glyph, no hotkey', () => {
    const cap = keycap(testKit(), { id: 'all', label: 'all', onPress: noop })
    expect(byKey(cap.el, 'all')?.props?.hotkey).toBeUndefined()
    expect(textsOf(cap.el)).toEqual([])
    expect(cap.width).toBe(3)
  })

  test('dim: the label dims and the glyph greys out; faded dims the label only; selected fills the signature 24% tint', () => {
    const k = testKit()
    const dim = keycap(k, { id: 'prev', key: 'p', label: '‹', dim: 'Already on the first page', onPress: noop })
    expect(byKey(dim.el, 'prev')?.props?.dimColor).toBe(true)
    if (KEYCAP_FACE === 'overlay') {
      expect(walk(dim.el).find(e => e.type === 'Text')?.props).toMatchObject({ backgroundColor: T.surface.overlay, color: T.fg.disabled })
    }
    const faded = keycap(k, { id: 'tab', key: '3', label: 'patch-1-8 ✓ 0', faded: true, onPress: noop })
    expect(byKey(faded.el, 'tab')?.props?.dimColor).toBe(true)
    const on = keycap(k, { id: 'sel', key: '1', label: 'All 31', selected: true, onPress: noop })
    expect(byKey(on.el, 'sel-box')?.props?.backgroundColor).toBe(STRONG.signature)
    expect(byKey(keycap(k, { id: 'plain', key: '2', label: 'x', onPress: noop }).el, 'plain-box')?.props?.backgroundColor).toBeUndefined()
  })

  test('on the desktop the glyph sits beside the native button, never laid over it', () => {
    const cap = keycap(testKit('desktop'), { id: 'copy', key: 'c', label: 'copy', onPress: noop })
    expect(walk(cap.el).some(e => e.props?.position === 'absolute')).toBe(false)
    expect(textsOf(cap.el)).toEqual([' c '])
    expect(byKey(cap.el, 'copy')?.props).toMatchObject({ hotkey: 'c', label: 'copy' })
  })

  test('a checkbox is □ / ■ on a keycap', () => {
    const k = testKit()
    expect(byKey(checkbox(k, { id: 'sel:a', selected: false, onPress: noop }).el, 'sel:a')?.props?.label).toBe('□')
    expect(byKey(checkbox(k, { id: 'sel:a', key: 's', selected: true, onPress: noop }).el, 'sel:a')?.props).toMatchObject({ label: '■', hotkey: 's' })
  })
})

describe('badge, label, signal, rule, footer, fit', () => {
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

  test('rule is dashed in the default border colour; the footer joins hints with · and cuts to width', () => {
    const k = testKit()
    expect(textsOf(rule(k, 5))).toEqual(['┄┄┄┄┄'])
    expect(walk(rule(k, 5))[0]?.props?.color).toBe(T.border.default)
    expect(textsOf(helpFooter(k, ['h l phase', 'f phases'], 80))).toEqual(['h l phase · f phases'])
    expect(textsOf(helpFooter(k, ['h l phase', 'f phases'], 8))).toEqual(['h l pha…'])
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

describe('confirm row', () => {
  test('question, then y yes and n no (no focused) on one row when it fits; stacked when narrow', () => {
    const k = testKit()
    const wide = confirmRow(k, { id: 'confirm', question: 'done tm-audit-031?', onYes: noop, onNo: noop, width: 60 })
    expect(wide).toHaveLength(1)
    expect(byKey(wide, 'confirm-yes')?.props).toMatchObject({ hotkey: 'y', label: 'yes' })
    expect(byKey(wide, 'confirm-no')?.props).toMatchObject({ hotkey: 'n', label: 'no', autoFocus: true })
    expect(textsOf(wide)[0]).toBe(' done tm-audit-031? ')
    const narrow = confirmRow(k, { id: 'confirm', question: 'done tm-audit-031?', onYes: noop, onNo: noop, width: 24 })
    expect(narrow).toHaveLength(2)
    expect(byKey(narrow[1], 'confirm-no')).toBeDefined()
  })
})
