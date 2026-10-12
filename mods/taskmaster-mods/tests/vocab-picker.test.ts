// User intent: pin the phase picker that replaces the strip (spec §15.4) — ‹ [ phase  count ▾ ] › with h/l and f in the
// footer, the dropdown listing every shown phase with its count, done ones dim, 0–9 only while open, ◐ done as its last row.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_PHASES } from '../hooks/demo'
import type { StripView } from '../hooks/kit'
import { ALL, FRESH_PHASE_VIEW, orderPhases, pickerName } from '../hooks/phases'
import { MENU_PLACEMENT, phaseFooter, phaseMenu, phasePicker, withPhaseMenu } from '../hooks/vocab'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const view = (choice: string, overlay = false): StripView => ({
  pane: 'review',
  view: { ...FRESH_PHASE_VIEW, choice, phases: DEMO_PHASES, loaded: true, overlay },
  allCount: 5,
  focused: true,
  digits: true,
  empty: 'Nothing waiting',
})

function recorder() {
  const calls: string[] = []
  return {
    calls,
    on: {
      choose: (id: string) => void calls.push(`choose ${id}`),
      toggleDone: () => void calls.push('done'),
      overlay: (open: boolean) => void calls.push(`overlay ${open}`),
    },
  }
}

describe('the picker', () => {
  test('names: All, the active phase, a done one, a planned one', () => {
    expect(orderPhases(DEMO_PHASES, 5).map(pickerName)).toEqual(['All', 'Patch 1.9 · active', 'Release 2.0', 'Patch 1.8 ✓'])
  })

  test('‹ [ Patch 1.9 · active  3 ▾ ] ›: three quiet Buttons, no hotkeys; the field on the overlay ground', () => {
    const { on, calls } = recorder()
    const k = testKit()
    const picker = phasePicker(k, view('patch-1-9'), on)
    for (const key of ['phase-prev', 'phase-field', 'phase-next']) expect(byKey(picker.el, key)?.props?.hotkey, key).toBeUndefined()
    expect(textsOf(picker.el)).toEqual(['‹', ' ', ' Patch 1.9 · active  3 ▾ ', ' ', '›'])
    expect(walk(picker.el).find(e => e.children?.[0] === ' Patch 1.9 · active  3 ▾ ')?.props).toMatchObject({ backgroundColor: T.surface.overlay, color: T.fg.bold })
    expect(picker.width).toBe(widthOf(picker.el))
    expect(calls).toEqual([])
  })

  test('the footer carries h, l and f; h/l step both ways, no wrap; at an end it says why', () => {
    const said: string[] = []
    const { on, calls } = recorder()
    const k = testKit('terminal', said)
    const items = phaseFooter(k, view('patch-1-9'), on)
    expect(items.map(i => `${i.keys} ${i.word}`)).toEqual(['h/l phase', 'f phases'])
    const keys = Object.fromEntries(items.flatMap(i => i.press ?? []).map(p => [p.key, p.onPress]))
    keys.h?.()
    keys.l?.()
    keys.f?.()
    expect(calls).toEqual([`choose ${ALL}`, 'choose release-2', 'overlay true'])
    const atStart = Object.fromEntries(phaseFooter(k, view(ALL), on).flatMap(i => i.press ?? []).map(p => [p.key, p.onPress]))
    atStart.h?.()
    expect(said).toEqual(['Already on the first phase'])
  })
})

describe('the dropdown', () => {
  test('every shown phase with its count, digits 0–9, the chosen row highlighted and focused, done rows dim', () => {
    const { on, calls } = recorder()
    const k = testKit()
    const menu = phaseMenu(k, view('patch-1-9', true), on)
    expect(walk(menu)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.strong, backgroundColor: T.surface.overlay, width: 44 })
    expect(byKey(menu, 'phase-pick:')?.props).toMatchObject({ hotkey: '0' })
    expect(byKey(menu, 'phase-pick:patch-1-9')?.props).toMatchObject({ hotkey: '1', autoFocus: true })
    expect(byKey(menu, 'phase-pick:patch-1-9-box')?.props?.backgroundColor).toBe(T.tint24.overlay.signature)
    expect(byKey(menu, 'phase-pick:patch-1-8')?.props).toMatchObject({ hotkey: '3', dimColor: true })
    expect(String(byKey(menu, 'phase-pick:patch-1-9')?.props?.label)).toMatch(/^Patch 1\.9 · active +3$/)
    expect(byKey(menu, 'phase-done')?.props?.hotkey).toBeUndefined()
    expect(textsOf(menu)).toEqual(expect.arrayContaining(['◐ show done phases', 'on']))
    expect(calls).toEqual([])
  })

  test('closed: the body as it is; open: the menu floats over the body (or replaces it, by MENU_PLACEMENT)', () => {
    const { on } = recorder()
    const k = testKit()
    const body = [h('Text', {}, 'row one')!, h('Text', {}, 'row two')!]
    expect(withPhaseMenu(k, view('patch-1-9'), on, body)).toEqual(body)
    const open = withPhaseMenu(k, view('patch-1-9', true), on, body)
    expect(open).toHaveLength(1)
    const texts = textsOf(open)
    if (MENU_PLACEMENT === 'absolute') {
      expect(texts).toEqual(expect.arrayContaining(['row one', 'row two']))
      expect(walk(open).some(e => e.props?.position === 'absolute' && e.props?.left === 2)).toBe(true)
    } else {
      expect(texts).not.toContain('row one')
    }
  })
})
