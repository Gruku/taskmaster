// User intent: pin the kit every surface is rebuilt on (spec §4.1) — keycaps that press by key, tinted badges, panels with
// their title in the border, stepped cards, fixed toolbar slots that dim — and the user's hard rules: no rails, no motion
// on hover, no shadows.
import { describe, expect, test } from 'claude-code/testing'

import {
  badge,
  card,
  checkbox,
  confirmRow,
  fit,
  helpFooter,
  KEYCAP_FACE,
  keycap,
  label,
  pager,
  panel,
  PANEL_TITLE,
  panelTitle,
  rule,
  selectionBar,
  signal,
  toolbar,
} from '../hooks/kit'
import { byKey, hoversOf, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STRONG, STUB_TOKENS as T, TONE } from './fixtures/tokens'

const noop = (): void => undefined
const STYLE_ONLY = new Set(['backgroundColor', 'borderColor', 'borderStyle', 'borderDimColor', 'bold', 'color', 'dimColor', 'italic', 'underline'])

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

describe('panel, card, toolbar, pager, selection bar', () => {
  test('a terminal panel: round full border, raised fill, the title in signature text set into the top border, health top right', () => {
    const k = testKit()
    const p = panel(k, { title: panelTitle('session'), health: { kind: 'success', word: 'tm ok' }, width: 60 }, [label(k, 'body').el])
    const root = walk(p)[0]
    expect(root?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.strong, backgroundColor: T.surface.raised, width: 60 })
    const placed = walk(p).filter(e => e.props?.position === 'absolute')
    if (PANEL_TITLE.terminal === 'border') {
      expect(placed.map(e => e.props?.top)).toEqual([-1, -1])
      expect(textsOf(placed[0])).toEqual([' ◆ TASKMASTER · SESSION '])
      expect(walk(placed[0]).find(e => e.type === 'Text')?.props?.color).toBe(T.signatureText)
    }
    expect(textsOf(p)).toContain(' tm ok')
    expect(textsOf(p)).toContain('BODY')
  })

  test('the desktop panel puts its title on a first row; collapse drops the body; a long title is cut', () => {
    const desk = panel(testKit('desktop'), { title: panelTitle('restore'), width: 40 }, [])
    if (PANEL_TITLE.desktop === 'row') {
      expect(walk(desk).some(e => e.props?.position === 'absolute')).toBe(false)
      expect(textsOf(desk)[0]).toBe('◆ TASKMASTER · RESTORE')
    }
    const k = testKit()
    expect(textsOf(panel(k, { title: 'T', collapse: true, width: 30 }, [label(k, 'hidden').el]))).not.toContain('HIDDEN')
    expect(textsOf(panel(k, { title: panelTitle('a very long section name indeed'), width: 20 }, []))[0]?.length).toBeLessThanOrEqual(16)
  })

  test('a card steps one surface above its ground, selected one more, hover one more; focus is a full border and the body', () => {
    const k = testKit()
    const head = label(k, 'head').el
    const body = [label(k, 'body').el]
    const rest = walk(card(k, { id: 'a', selected: false, focused: false }, head, body))[0]
    expect(rest).toMatchObject({ props: { key: 'card:a', backgroundColor: T.surface.raised, paddingX: 2 }, hover: { backgroundColor: T.surface.overlay } })
    expect(rest?.props?.borderStyle).toBeUndefined()
    expect(textsOf(rest)).toEqual(['HEAD'])
    const chosen = walk(card(k, { id: 'b', selected: true, focused: true }, head, body))[0]
    expect(chosen).toMatchObject({ props: { backgroundColor: T.surface.overlay, borderStyle: 'round', borderColor: T.border.focus, paddingX: 1 } })
    expect(textsOf(chosen)).toEqual(['HEAD', 'BODY'])
    const onRaised = walk(card(k, { id: 'c', selected: true, focused: false, on: 'raised' }, head, body))[0]
    expect(onRaised?.props?.backgroundColor).toBe(TONE.signature)
  })

  test('toolbar: title at the left, slots at the right after a spacer; the title gives way first', () => {
    const k = testKit()
    const slot = keycap(k, { id: 'refresh', key: 'u', label: 'refresh', onPress: noop })
    const bar = toolbar(k, { title: 'handovers', slots: [slot], width: 60 })
    expect(textsOf(bar)[0]).toBe('HANDOVERS')
    expect(walk(bar).some(e => e.props?.flexGrow === 1)).toBe(true)
    expect(textsOf(toolbar(k, { title: 'handovers', slots: [slot], width: 16 }))[0]).toBe('HAN…')
  })

  test('pager: p ‹ and n › always drawn, dim at the ends, and the n / total count', () => {
    const k = testKit()
    const first = pager(k, { page: 0, pages: 4, onTurn: noop })
    expect(byKey(first.el, 'page-prev')?.props).toMatchObject({ hotkey: 'p', label: '‹', dimColor: true })
    expect(byKey(first.el, 'page-next')?.props?.dimColor).toBeUndefined()
    expect(textsOf(first.el)).toContain('1 / 4')
    const last = pager(k, { page: 9, pages: 4, onTurn: noop })
    expect(byKey(last.el, 'page-next')?.props?.dimColor).toBe(true)
    expect(textsOf(last.el)).toContain('4 / 4')
    expect(textsOf(pager(k, { page: 0, pages: 0, onTurn: noop }).el)).toContain('1 / 1')
  })

  test('selection bar: `<verb> <count>` keycaps (dim at 0), quiet ghost buttons with no hotkey at the right', () => {
    const k = testKit()
    const bar = selectionBar(k, {
      actions: [
        { id: 'bulk-done', key: 'b', verb: 'Done', count: 2, tone: 'success', onPress: noop },
        { id: 'bulk-agent', verb: 'To agent', count: 0, onPress: noop },
      ],
      ghosts: [
        { id: 'sel-all', label: 'all', onPress: noop },
        { id: 'sel-none', label: 'none', onPress: noop },
      ],
      width: 70,
    })
    expect(byKey(bar, 'bulk-done')?.props).toMatchObject({ label: 'Done 2', hotkey: 'b' })
    expect(byKey(bar, 'bulk-agent')?.props).toMatchObject({ label: 'To agent 0', dimColor: true })
    expect(byKey(bar, 'sel-all')?.props).toMatchObject({ label: 'all', dimColor: true, plain: true })
    expect(byKey(bar, 'sel-all')?.props?.hotkey).toBeUndefined()
    expect(textsOf(bar)).toContain('·')
  })

  test('hard rules: every hover only restyles (no offsets), and nothing draws a one-sided border or a shadow', () => {
    const k = testKit()
    const trees = [
      keycap(k, { id: 'a', key: 'a', label: 'a', onPress: noop }).el,
      card(k, { id: 'c', selected: true, focused: true }, label(k, 'x').el, []),
      panel(k, { title: 'p', width: 30 }, []),
      pager(k, { page: 1, pages: 3, onTurn: noop }).el,
      selectionBar(k, { actions: [{ id: 'x', verb: 'Go', count: 1, onPress: noop }], ghosts: [{ id: 'g', label: 'all', onPress: noop }], width: 40 }),
    ]
    for (const tree of trees) {
      for (const hover of hoversOf(tree)) for (const key of Object.keys(hover)) expect(STYLE_ONLY.has(key), key).toBe(true)
      for (const e of walk(tree)) {
        for (const key of Object.keys(e.props ?? {})) expect(/^border(Left|Right|Top|Bottom)|shadow/i.test(key), key).toBe(false)
      }
    }
  })
})
