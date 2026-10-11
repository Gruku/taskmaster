// User intent: pin how the phase strip draws (spec §5) — real buttons for tabs, ‹ › that dim at the ends, digits only while
// the pane is focused, `+N ▾` for what does not fit, `◐ done`, the overlay list and the empty line with `0 All`.
import { describe, expect, test } from 'claude-code/testing'

import { OVERLAY_LIST, OVERLAY_PLACEMENT, phaseEmpty, phaseOverlay, phaseSection, phaseStrip, type StripView } from '../hooks/kit'
import { parsePhases } from '../hooks/parse'
import { ALL, FRESH_PHASE_VIEW } from '../hooks/phases'
import type { TmPhase } from '../types'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { PHASES_WAITING } from './fixtures/phases'
import { STRONG } from './fixtures/tokens'

const REAL = ((): TmPhase[] => {
  const parsed = parsePhases(PHASES_WAITING)
  if (!parsed.ok) throw new Error(parsed.reason)
  return parsed.value
})()
const handlers = { choose: (): void => undefined, toggleDone: (): void => undefined, overlay: (): void => undefined }
const strip = (over: Partial<StripView['view']> = {}, rest: Partial<StripView> = {}): StripView => ({
  pane: 'review',
  view: { ...FRESH_PHASE_VIEW, phases: REAL, loaded: true, ...over },
  allCount: 31,
  focused: true,
  digits: true,
  empty: 'Nothing waiting',
  ...rest,
})
const tabKeys = (tree: unknown): string[] =>
  walk(tree)
    .map(e => e.props?.key)
    .filter((key): key is string => typeof key === 'string' && key.startsWith('phase-tab:') && !key.endsWith('-box'))

describe('phase strip', () => {
  test('tabs are keyed buttons with digits while focused; All is 0; the chosen tab carries the signature fill', () => {
    const k = testKit()
    const tree = phaseStrip(k, strip({ choice: '2-0-4' }), handlers, 200)
    expect(tabKeys(tree).slice(0, 3)).toEqual(['phase-tab:', 'phase-tab:2-0-4', 'phase-tab:patch-1-9-5'])
    expect(byKey(tree, 'phase-tab:')?.props).toMatchObject({ hotkey: '0', label: 'All 31' })
    expect(byKey(tree, 'phase-tab:2-0-4')?.props).toMatchObject({ hotkey: '1', label: 'Patch 2.0.4 · active 249' })
    expect(byKey(tree, 'phase-tab:2-0-4-box')?.props?.backgroundColor).toBe(STRONG.signature)
    expect(byKey(tree, 'phase-tab:alpha-followup')?.props?.dimColor).toBe(true)
    expect(textsOf(tree)).toContain('│')
  })

  test('no digits while the pane is unfocused, while digits belong to another row, or while the overlay is open', () => {
    const k = testKit()
    for (const s of [strip({}, { focused: false }), strip({}, { digits: false }), strip({ overlay: true })]) {
      expect(byKey(phaseStrip(k, s, handlers, 200), 'phase-tab:')?.props?.hotkey).toBeUndefined()
    }
  })

  test('‹ h and › l always drawn; ‹ dims on All, › dims on the last shown tab', () => {
    const k = testKit()
    const first = phaseStrip(k, strip(), handlers, 200)
    expect(byKey(first, 'phase-prev')?.props).toMatchObject({ hotkey: 'h', label: '‹', dimColor: true })
    expect(byKey(first, 'phase-next')?.props).toMatchObject({ hotkey: 'l', label: '›' })
    expect(byKey(first, 'phase-next')?.props?.dimColor).toBeUndefined()
    const last = phaseStrip(k, strip({ choice: '2-1-0' }), handlers, 200)
    expect(byKey(last, 'phase-next')?.props?.dimColor).toBe(true)
  })

  test('what does not fit goes behind `+N ▾` (f); done tabs hide over four done phases until ◐ done; the chosen tab always shows', () => {
    const k = testKit()
    const narrow = phaseStrip(k, strip({ choice: '2-0-6' }), handlers, 60)
    expect(tabKeys(narrow)).toEqual(['phase-tab:', 'phase-tab:2-0-6'])
    expect(byKey(narrow, 'phase-more')?.props).toMatchObject({ hotkey: 'f', label: `+${REAL.length - 1} ▾` })
    const wide = phaseStrip(k, strip(), handlers, 400)
    expect(tabKeys(wide)).not.toContain('phase-tab:2-0-3')
    expect(byKey(wide, 'phase-done')?.props?.label).toBe('○ done')
    expect(tabKeys(phaseStrip(k, strip({ showDone: true }), handlers, 400))).toHaveLength(10)
    expect(byKey(phaseStrip(k, strip({ showDone: true }), handlers, 400), 'phase-done')?.props?.label).toBe('◐ done')
    expect(tabKeys(phaseStrip(k, strip({ choice: 'p1' }), handlers, 400))).toContain('phase-tab:p1')
  })

  test('narrow: ◐ done gives way, then All; a long chosen tab is cut; the strip never runs past its width', () => {
    const k = testKit()
    for (const width of [72, 48, 36, 28]) {
      for (const choice of [ALL, '2-0-4']) {
        for (const focused of [true, false]) {
          const tree = phaseStrip(k, strip({ choice }, { focused }), handlers, width)
          expect(widthOf(tree), `${width} ${choice} ${focused}`).toBeLessThanOrEqual(width)
        }
      }
    }
    expect(byKey(phaseStrip(k, strip(), handlers, 200), 'phase-done')).toBeDefined()
    const tight = phaseStrip(k, strip({ choice: '2-0-4' }), handlers, 36)
    expect(byKey(tight, 'phase-done')).toBeUndefined()
    expect(byKey(tight, 'phase-tab:2-0-4')?.props).toMatchObject({ hotkey: '1' })
    expect(String(byKey(tight, 'phase-tab:2-0-4')?.props?.label)).toMatch(/…$/)
  })

  test('nothing read yet: All alone, plus the remembered choice as its bare id; `▾` still opens the overlay', () => {
    const k = testKit()
    const tree = phaseStrip(k, strip({ phases: [], loaded: false, choice: '2-0-6' }), handlers, 200)
    expect(tabKeys(tree)).toEqual(['phase-tab:', 'phase-tab:2-0-6'])
    expect(byKey(tree, 'phase-tab:2-0-6')?.props?.label).toBe('2-0-6 0')
    expect(byKey(tree, 'phase-more')?.props?.label).toBe('▾')
  })
})

describe('overlay and empty line', () => {
  test('the overlay lists every phase, done ones included, with status, count and date; the chosen one is focused', () => {
    const k = testKit()
    const tree = phaseOverlay(k, strip({ choice: '2-0-6', overlay: true }), handlers, 80)
    expect(textsOf(tree)).toContain('PHASES')
    expect(byKey(tree, 'phase-close')?.props).toMatchObject({ hotkey: 'f', label: 'close' })
    if (OVERLAY_LIST === 'buttons') {
      expect(byKey(tree, 'phase-pick:')?.props).toMatchObject({ hotkey: '0', label: 'All · every phase · 31' })
      expect(byKey(tree, 'phase-pick:2-0-3')?.props?.label).toBe('Patch 2.0.3 · done · 1')
      expect(byKey(tree, 'phase-pick:2-0-6')?.props?.autoFocus).toBe(true)
    } else {
      expect(byKey(tree, 'phase-select')?.props?.value).toBe('2-0-6')
    }
  })

  test('phaseSection draws strip then body; while the overlay is open it draws the overlay per OVERLAY_PLACEMENT', () => {
    const k = testKit()
    const body = [phaseEmpty(k, strip(), handlers)]
    expect(phaseSection(k, strip(), handlers, 120, body)).toHaveLength(2)
    const open = phaseSection(k, strip({ overlay: true }), handlers, 120, body)
    if (OVERLAY_PLACEMENT === 'swap') {
      expect(open).toHaveLength(2)
      expect(textsOf(open[1])).toContain('PHASES')
    } else {
      expect(walk(open).some(e => e.props?.position === 'absolute')).toBe(true)
    }
  })

  test('an empty selection names the phase and offers 0 All; under All it is the bare words', () => {
    const k = testKit()
    const filtered = phaseEmpty(k, strip({ choice: '2-0-6' }), handlers)
    expect(textsOf(filtered)).toContain('Nothing waiting in Patch 2.0.6')
    expect(byKey(filtered, 'phase-all')?.props).toMatchObject({ hotkey: '0', label: 'All' })
    const all = phaseEmpty(k, strip({ choice: ALL }), handlers)
    expect(textsOf(all)).toEqual(['Nothing waiting'])
    expect(byKey(all, 'phase-all')).toBeUndefined()
  })
})
