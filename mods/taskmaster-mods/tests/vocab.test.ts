// User intent: pin the visual rework's element vocabulary (spec §15.2–15.3) — inks, glyph badges, the five-rung key ladder,
// labelled actions, the y/n row and the footer — so every screen built on it shows the approved prototype's parts — and the hard UI rules (no motion on hover, no
// shadows, no one-sided coloured borders) on every vocabulary element.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_PHASES, demoSnapshot } from '../hooks/demo'
import { ALL, FRESH_PHASE_VIEW } from '../hooks/phases'
import { sidebarView } from '../hooks/nav'
import {
  actionKey,
  bulkRow,
  focusedItem,
  pagerPiece,
  paneToolbar,
  phaseMenu,
  phasePicker,
  refreshPiece,
  sidebar,
  item,
  togglePiece,
  actionRows,
  askRow,
  BADGE_W,
  FOOTER_KEYS,
  footer,
  glyphBadge,
  hints,
  inkOf,
  line,
  statusBadgeWord,
} from '../hooks/vocab'
import { byKey, hoversOf, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const noop = (): void => undefined

describe('inks and lines', () => {
  test('the prototype inks are RR tokens: faint is border.strong', () => {
    const k = testKit()
    expect([inkOf(k, 'bold'), inkOf(k, 'default'), inkOf(k, 'subtle'), inkOf(k, 'disabled'), inkOf(k, 'faint')]).toEqual([
      T.fg.bold,
      T.fg.default,
      T.fg.subtle,
      T.fg.disabled,
      T.border.strong,
    ])
  })

  test('a line cuts across its segments and is never wider than its room', () => {
    const k = testKit()
    const segs = [
      { text: 'next  ', color: T.signatureText },
      { text: 'Record the merge for tm-audit-030', color: T.fg.default },
    ]
    expect(line(k, segs, 80).width).toBe(39)
    const cut = line(k, segs, 12)
    expect(cut.width).toBe(12)
    expect(textsOf(cut.el)).toEqual(['next  ', 'Recor…'])
    expect(widthOf(cut.el)).toBe(12)
  })
})

describe('glyph badges', () => {
  test('glyph + upper-case word in the tone, padded to the label column in a list; never a fill', () => {
    const k = testKit()
    const b = glyphBadge(k, 'blocked', { pad: true })
    expect(b.width).toBe(BADGE_W)
    expect(walk(b.el)[0]).toMatchObject({ type: 'Text', props: { color: T.tone.critical, bold: true }, children: ['▲ BLOCKED      '] })
    expect(walk(b.el)[0]?.props?.backgroundColor).toBeUndefined()
    expect(walk(glyphBadge(k, 'OPEN').el)[0]).toMatchObject({ props: { color: T.fg.subtle, bold: false }, children: ['○ OPEN'] })
    expect(walk(glyphBadge(k, 'RESUMED FROM').el)[0]).toMatchObject({ props: { color: T.signatureText }, children: ['↳ RESUMED FROM'] })
    expect(walk(glyphBadge(k, 'IN REVIEW', { dim: true }).el)[0]).toMatchObject({ props: { color: T.border.strong, bold: false } })
    expect(walk(glyphBadge(k, 'whatever').el)[0]?.children).toEqual(['· WHATEVER'])
  })

  test('a task status reads as its badge word', () => {
    expect([statusBadgeWord('in-review'), statusBadgeWord('in-progress'), statusBadgeWord('')]).toEqual(['IN REVIEW', 'IN PROGRESS', 'UNKNOWN'])
  })
})

describe('the key ladder', () => {
  test('primary: one plain Button with its hotkey and verb + object label, filled with the RR keycap, ink label, glyph over k:', () => {
    const k = testKit()
    const key = actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })
    expect(byKey(key.el, 'done')).toMatchObject({ type: 'Button', props: { key: 'done', hotkey: 'd', label: 'mark task done', plain: true } })
    expect(byKey(key.el, 'done-box')?.props).toMatchObject({ backgroundColor: T.keycap.signature.bg, paddingRight: 1 })
    expect(textsOf(key.el)).toEqual(['mark task done', ' d '])
    expect(walk(key.el).some(e => e.props?.position === 'absolute')).toBe(true)
    expect(key.width).toBe(3 + 'mark task done'.length + 1)
    expect(widthOf(key.el)).toBe(key.width)
  })

  test('secondary: the letter bold in signature ink, no fill; destructive: the letter in warning ink', () => {
    const k = testKit()
    const copy = actionKey(k, { id: 'copy', key: 'c', label: 'copy handover', onPress: noop })
    expect(byKey(copy.el, 'copy-box')?.props?.backgroundColor).toBeUndefined()
    const glyph = walk(copy.el).find(e => e.type === 'Text' && (e.children ?? []).includes(' c '))
    expect(glyph?.props).toMatchObject({ color: T.signatureText, bold: true })
    expect(glyph?.props?.backgroundColor).toBeUndefined()
    expect(copy.width).toBe(3 + 'copy handover'.length)
    const close = actionKey(k, { id: 'close', key: 'x', label: 'close handover', rung: 'destructive', onPress: noop })
    expect(walk(close.el).find(e => (e.children ?? []).includes(' x '))?.props?.color).toBe(T.tone.warning)
  })

  test('disabled: the drawn tree is dim (label, glyph, no fill, narrower by the fill pad)', () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const off = actionKey(k, { id: 'bulk-done', key: 'b', label: 'mark 0 tasks done', rung: 'primary', dim: 'Nothing selected', onPress: noop })
    const button = byKey(off.el, 'bulk-done')
    expect(button?.props?.dimColor).toBe(true)
    expect(byKey(off.el, 'bulk-done-box')?.props?.backgroundColor).toBeUndefined()
    expect(walk(off.el).find(e => (e.children ?? []).includes(' b '))?.props?.color).toBe(T.fg.disabled)
    expect(off.width).toBe(3 + 'mark 0 tasks done'.length)
  })

  test('a bad hotkey throws naming the key; hovers change colour or background only, never position', () => {
    const k = testKit()
    expect(() => actionKey(k, { id: 'x', key: 'R', label: 'x', onPress: noop })).toThrow(/key x: hotkey "R"/)
    const keys = [actionKey(k, { id: 'a', key: 'a', label: 'send task to agent', onPress: noop }), actionKey(k, { id: 'd', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })]
    for (const hover of keys.flatMap(p => hoversOf(p.el))) {
      for (const prop of Object.keys(hover)) expect(['backgroundColor', 'color', 'bold', 'inverse']).toContain(prop)
    }
  })

  test('primary hover: every element carrying the fill steps to the lighter token with ink, un-inverted; the key hugs its label', () => {
    const k = testKit()
    const key = actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })
    const lit = { backgroundColor: T.fg.bold, color: T.keycap.signature.ink }
    const hoverOf = (e: unknown) => (e as { hover?: unknown } | undefined)?.hover
    expect(hoverOf(byKey(key.el, 'done-box'))).toMatchObject({ backgroundColor: T.fg.bold })
    expect(hoverOf(byKey(key.el, 'done'))).toMatchObject({ ...lit, bold: true, inverse: false })
    const label = walk(key.el).find(e => e.type === 'Text' && (e.children ?? []).includes('mark task done'))
    expect(hoverOf(label)).toMatchObject({ ...lit, inverse: false })
    const glyph = walk(key.el).find(e => e.type === 'Text' && (e.children ?? []).includes(' d '))
    expect(hoverOf(glyph)).toMatchObject(lit)
    expect(byKey(key.el, 'done-box')?.props?.alignSelf).toBe('flex-start')
  })

  test('desktop: the letter sits beside the native button, the primary is the native primary', () => {
    const k = testKit('desktop')
    const key = actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })
    expect(byKey(key.el, 'done')?.props).toMatchObject({ hotkey: 'd', variant: 'primary' })
    expect(walk(key.el).some(e => e.props?.position === 'absolute')).toBe(false)
  })

  test('action rows keep 3 cells between keys and wrap rather than drop one', () => {
    const k = testKit()
    const keys = ['mark task done', 'send task to agent', 'open task', 'task in viewer'].map((label, i) =>
      actionKey(k, { id: `k${i}`, key: 'abcd'[i] ?? 'z', label, onPress: noop }),
    )
    const wide = actionRows(k, keys, 200)
    expect(wide).toHaveLength(1)
    expect(walk(wide[0])[0]?.props?.columnGap).toBe(3)
    const narrow = actionRows(k, keys, 40)
    expect(narrow.length).toBeGreaterThan(1)
    for (const row of narrow) expect(widthOf(row)).toBeLessThanOrEqual(40)
    expect(narrow.flatMap(r => walk(r)).filter(e => e.type === 'Button')).toHaveLength(4)
  })
})

describe('the ask row', () => {
  test('the question in warning ink, then y yes and n no (n focused); one row when it fits, else two', () => {
    const k = testKit()
    const rows = askRow(k, { id: 'band', question: 'mark unified-chat-022 done?', onYes: noop, onNo: noop, width: 80 })
    expect(rows).toHaveLength(1)
    expect(byKey(rows[0], 'band-yes')?.props).toMatchObject({ hotkey: 'y', label: 'yes' })
    expect(byKey(rows[0], 'band-no')?.props).toMatchObject({ hotkey: 'n', label: 'no', autoFocus: true })
    expect(walk(rows[0]).find(e => e.type === 'Text' && (e.children ?? []).includes('mark unified-chat-022 done?'))?.props).toMatchObject({ color: T.tone.warning, bold: true })
    expect(askRow(k, { id: 'band', question: 'mark unified-chat-022 done?', onYes: noop, onNo: noop, width: 30 })).toHaveLength(2)
  })
})

describe('the footer', () => {
  const items = (calls: string[]) => [
    { keys: 'j/k', word: 'move', press: [{ key: 'j', onPress: () => void calls.push('j') }, { key: 'k', onPress: () => void calls.push('k') }] },
    { keys: 's', word: 'select', press: [{ key: 's', onPress: () => void calls.push('s') }] },
    { keys: 'esc', word: 'close' },
  ]

  test('one Button per listed key, keyed key:<letter>; the line reads keys subtle, words disabled, · faint', () => {
    const k = testKit()
    const tree = footer(k, items([]), 60)
    for (const key of ['j', 'k', 's']) expect(byKey(tree, `key:${key}`)?.props).toMatchObject({ hotkey: key, plain: true })
    expect(walk(tree).filter(e => e.type === 'Button')).toHaveLength(3)
    if (FOOTER_KEYS === 'under') {
      expect(textsOf(tree).slice(0, 7)).toEqual(['j/k', ' move', ' · ', 's', ' select', ' · ', 'esc'])
      expect(walk(tree).some(e => e.props?.position === 'absolute')).toBe(true)
      for (const b of walk(tree).filter(e => e.type === 'Button')) expect(b.props?.label).toBe('')
    }
  })

  test('the hint line alone is cut to its room; a bad footer key throws', () => {
    const k = testKit()
    const short = hints(k, items([]), 10)
    expect(textsOf(short).join('').length).toBeLessThanOrEqual(10)
    expect(() => footer(k, [{ keys: '[', word: 'x', press: [{ key: '[', onPress: noop }] }], 40)).toThrow(/hotkey "\["/)
  })
})

const STYLE_ONLY = new Set(['backgroundColor', 'borderColor', 'borderStyle', 'borderDimColor', 'bold', 'color', 'dimColor', 'italic', 'underline', 'inverse'])

describe('the hard UI rules, on the vocabulary', () => {
  test('every hover only restyles (no offsets), and nothing draws a one-sided border or a shadow', () => {
    const k = testKit()
    const strip = { pane: 'review' as const, view: { ...FRESH_PHASE_VIEW, choice: ALL, phases: DEMO_PHASES, loaded: true, overlay: true }, allCount: 5, focused: true, digits: true, empty: 'Nothing waiting' }
    const on = { choose: noop, toggleDone: noop, overlay: noop }
    const keys = [
      actionKey(k, { id: 'p', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop }),
      actionKey(k, { id: 's', key: 'a', label: 'send task to agent', onPress: noop }),
      actionKey(k, { id: 'x', key: 'x', label: 'drop task', rung: 'destructive', onPress: noop }),
      actionKey(k, { id: 'z', key: 'z', label: 'nothing yet', dim: 'why', onPress: noop }),
    ]
    const head = { mark: { key: 'sel:a', selected: true, onPress: noop }, badge: 'ACTION', ident: 'docs-012', title: 'README mentions the review queue', age: '30h' }
    const view = sidebarView({ current: 'session', snapshot: demoSnapshot(Date.UTC(2026, 9, 12, 12, 0)), restore: null, now: Date.UTC(2026, 9, 12, 12, 0) })
    const trees = [
      ...keys.map(p => p.el),
      ...item(k, head, [], 80),
      focusedItem(k, { id: 'a', head, lines: [], actions: keys.map(p => p.el) }, 80),
      paneToolbar(k, { left: phasePicker(k, strip, on), right: [togglePiece(k, { id: 't', label: 'show closed', on: true, onPress: noop }), pagerPiece(k, { page: 0, pages: 2, onTurn: noop }), refreshPiece(k, { label: 'refresh', onPress: noop })], width: 100 }),
      bulkRow(k, { keys, scopes: [{ id: 'sel-all', label: 'all', onPress: noop }], width: 80 }),
      phaseMenu(k, strip, on),
      sidebar(k, view, noop),
      footer(k, [{ keys: 'h l', word: 'phase', press: [{ key: 'h', onPress: noop }] }], 80),
    ]
    for (const tree of trees) {
      for (const hover of hoversOf(tree)) for (const key of Object.keys(hover)) expect(STYLE_ONLY.has(key), key).toBe(true)
      for (const e of walk(tree)) {
        for (const key of Object.keys(e.props ?? {})) expect(/^border(Left|Right|Top|Bottom)|shadow/i.test(key), key).toBe(false)
      }
    }
  })
})
