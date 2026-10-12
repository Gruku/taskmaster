// User intent: ticking a review check is two keystrokes, `t` then the check's digit (spec §8, D8) — the digits are the phase
// picker's while its list is open and no Button's the rest of the time, so a stray digit never ticks, and no digit is ever on two buttons at once.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS } from '../hooks/demo'
import { splitCheck } from '../hooks/model'
import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const WIDE = { ...pane('tm-review'), props: { ...pane('tm-review').props, bodyColumns: 140 } }
const ID = 'unified-chat-022'
const [FIRST] = splitCheck(DEMO_DETAILS[ID]!.humanAction).items as [string, string]
type Ui = { findAll: (q: { type: 'Button' }) => Promise<{ props: Record<string, unknown> }[]> }
/** Every hotkey drawn, with the Buttons that carry it. */
const hotkeys = async (ui: Ui): Promise<Record<string, string[]>> => {
  const keys: Record<string, string[]> = {}
  for (const b of await ui.findAll({ type: 'Button' })) {
    if (typeof b.props.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  }
  return keys
}
const shared = (keys: Record<string, string[]>): string[] => Object.keys(keys).filter(key => (keys[key] ?? []).length > 1)

describe('t + digit ticks a check', () => {
  test('unarmed, the checks carry no digit, and with the phase list shut no Button carries one', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'tick' }))?.props).toMatchObject({ hotkey: 't', label: 'tick check 1-9' })
    const keys = await hotkeys(ui)
    expect(keys['0']).toBeUndefined()
    expect(keys['1']).toBeUndefined()
    expect(shared(keys)).toEqual([])
    expect(await ui.find({ type: 'Text', text: 't 1-9' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' tick' })).toBeDefined()
  })

  test('t arms: the checks own 1-9, no digit is on two buttons; the footer says how out', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'tick' })
    const keys = await hotkeys(ui)
    expect(keys['1']).toEqual(['tick-0'])
    expect(keys['2']).toEqual(['tick-1'])
    expect(keys['0']).toBeUndefined()
    expect(shared(keys)).toEqual([])
    expect(await ui.find({ type: 'Text', text: '1-9' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' tick a check' })).toBeDefined()
  })

  test('the digit ticks once and disarms: then no Button carries a digit', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'tick-0' })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props).toMatchObject({ label: `☑ ${FIRST}` })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey).toBeUndefined()
    expect((await hotkeys(ui))['1']).toBeUndefined()
    expect(world.store.get(`ticks:${ID}`)).toMatchObject({ items: [FIRST] })
  })

  test('t again, f, j / k and a page turn each disarm', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const armed = async () => (await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey === '1'
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'tick' })
    expect(await armed()).toBe(false)
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'phase-field' })
    await ui.press({ key: 'phase-field' })
    expect(await armed()).toBe(false)
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'focus-next' })
    await ui.press({ key: 'focus-prev' })
    expect(await armed()).toBe(false)
  })

  test('d and a disarm, so the y/n and the note never meet an armed digit', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'done' })
    expect((await hotkeys(ui))['1']).toBeUndefined()
    await ui.press({ key: 'confirm-no' })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey).toBeUndefined()
    await ui.press({ key: 'tick' })
    await ui.press({ key: 'back' })
    await ui.press({ key: 'note-cancel' })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey).toBeUndefined()
  })

  test('a card with no check offers no t', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    for (let i = 0; i < 3; i += 1) await ui.press({ key: 'focus-next' })
    expect(await ui.find({ type: 'Button', key: 'tick' })).toBeUndefined()
  })

  test('the overlay open: its picks own 0-9 and nothing else carries a digit', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'phase-field' })
    const keys = await hotkeys(ui)
    expect(keys['0']).toEqual(['phase-pick:'])
    expect(shared(keys)).toEqual([])
  })
})
