// User intent: the lean review card the user approved live (2026-10-06) — a raised round card under a header strip, the check
// as a tickable checklist kept only locally, details on demand, viewer and copy, one action row that wraps rather than drops.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, demoSnapshot } from '../hooks/demo'
import { splitCheck } from '../hooks/model'
import type { TmSnapshot } from '../types'
import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { childKeys, type Drawn, elementsOf, widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const ID = 'unified-chat-022'
const CHECK = DEMO_DETAILS[ID]!.humanAction
const [FIRST, SECOND] = splitCheck(CHECK).items as [string, string]
const ACTIONS = ['done-box', 'back-box', 'skip-box', 'open-box', 'viewer-box', 'copy-box']
const wide = (columns: number) => ({ ...pane('tm-review'), props: { ...pane('tm-review').props, bodyColumns: columns } })
/** A tm-mode snapshot that is real data (not the demo marker), so tm-mode tests start from a state with a card. */
const REAL: TmSnapshot = { ...demoSnapshot(0), reason: '' }

describe('review card', () => {
  test('header strip, raised round card, header line, title, labelled checklist with digit keys', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    expect(await ui.find({ type: 'Text', text: 'REVIEW' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '●○○○○' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '1 of 5 · 0 done this pass' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^esc close$/ })).toBeDefined()
    const cards = (await ui.findAll({ type: 'Box' })).filter(b => b.props.borderStyle === 'round')
    expect(cards).toHaveLength(1)
    for (const text of ['◆ CRITICAL', ID, '· 1h', '· FULL', '● review-gate pass', 'LIVE CHECK ON DEV', 'needs unifiedChatGenerate + unifiedChatBuild granted']) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    expect(await ui.find({ type: 'Text', text: /^Unified chat pre-build gets the full supervisor toolset/ })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props).toMatchObject({ hotkey: '1', label: `☐ ${FIRST}` })
    expect((await ui.find({ type: 'Button', key: 'tick-1' }))?.props).toMatchObject({ hotkey: '2', label: `☐ ${SECOND}` })
    expect((await ui.find({ type: 'Button', key: 'details' }))?.props).toMatchObject({ hotkey: 'i', label: '▸ details' })
  })

  test('the action bar is one row when it fits, wraps to a second row before it drops anything, and drops only v and c', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const [columns, rows] of [
      [100, 1],
      [72, 2],
    ] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(columns) })
      const holding = elementsOf(await ui.drawn()).filter(b => childKeys(b).some(k => ACTIONS.includes(k)))
      expect(holding, `${columns}`).toHaveLength(rows)
      expect(holding.flatMap(childKeys), `${columns}`).toEqual(ACTIONS)
      await ui.unmount()
    }
    for (const columns of [100, 72, 48, 36, 28]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(columns) })
      const tree = (await ui.drawn()) as unknown as Drawn
      expect(widthOf(tree), `${columns}`).toBeLessThanOrEqual(columns)
      const keys = elementsOf(tree).flatMap(childKeys)
      for (const kept of ['done-box', 'back-box', 'skip-box', 'open-box']) expect(keys, `${columns}`).toContain(kept)
      await ui.unmount()
    }
  })

  test('a tick toggles ☐/☑, lives in $.store ticks:<id> (read before write), never in Taskmaster', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on), { [`ticks:${ID}`]: { at: 0, items: [SECOND] } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    expect((await ui.find({ type: 'Button', key: 'tick-1' }))?.props.label).toBe(`☑ ${SECOND}`)
    await ui.press({ key: 'tick-0' })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.label).toBe(`☑ ${FIRST}`)
    expect(world.store.get(`ticks:${ID}`)).toMatchObject({ items: [SECOND, FIRST] })
    await ui.press({ key: 'tick-1' })
    expect((await ui.find({ type: 'Button', key: 'tick-1' }))?.props.label).toBe(`☐ ${SECOND}`)
    expect(world.store.get(`ticks:${ID}`)).toMatchObject({ items: [FIRST] })
    expect(world.calls).toEqual([])
  })

  test('confirm names the task when all is ticked, and counts what is not', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Text', text: /2 of 2 unchecked — done anyway\?/ })).toBeDefined()
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'tick-0' })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Text', text: /1 of 2 unchecked — done anyway\?/ })).toBeDefined()
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'tick-1' })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Text', text: /done unified-chat-022\?/ })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus).toBe(true)
  })

  test('outside card mode the ticks and details are plain text: no keys to catch a digit typed in the note or pressed mid-confirm', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on), { [`ticks:${ID}`]: { at: 0, items: [SECOND] } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100), surface: 'terminal' })
    const disarmed = async (when: string) => {
      for (const key of ['tick-0', 'tick-1', 'details']) expect(await ui.find({ type: 'Button', key }), `${when} ${key}`).toBeUndefined()
      expect(await ui.find({ type: 'Text', text: `☐ ${FIRST}` }), when).toBeDefined()
      expect(await ui.find({ type: 'Text', text: `☑ ${SECOND}` }), when).toBeDefined()
      expect(await ui.find({ type: 'Text', text: '▸ details' }), when).toBeDefined()
    }
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Text', text: /1 of 2 unchecked — done anyway\?/ })).toBeDefined()
    await disarmed('confirm')
    expect(await ui.find({ type: 'Text', text: /1 of 2 unchecked — done anyway\?/ })).toBeDefined()
    await ui.press({ key: 'confirm-no' })
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props.hotkey).toBe('1')
    await ui.press({ key: 'back' })
    expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
    await disarmed('note')
    await ui.press({ key: 'note-cancel' })
    expect((await ui.find({ type: 'Button', key: 'details' }))?.props.hotkey).toBe('i')
  })

  test('i shows notes, links and branch/PR under the check, and hides them again', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    expect(await ui.find({ type: 'Text', text: /pull\/412/ })).toBeUndefined()
    await ui.press({ key: 'details' })
    expect((await ui.find({ type: 'Button', key: 'details' }))?.props.label).toBe('▾ details')
    for (const text of [/no branch/, /pull\/412/, /cookbook owns the step order/, /docs\/specs\/unified-chat\.md/]) {
      expect(await ui.find({ type: 'Text', text }), String(text)).toBeDefined()
    }
    await ui.press({ key: 'details' })
    expect(await ui.find({ type: 'Text', text: /pull\/412/ })).toBeUndefined()
  })

  test('v in demo only announces the viewer; c copies the check, and says so when there is no clipboard', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    await ui.press({ key: 'viewer' })
    expect(world.toasts).toEqual([`would open ${ID} in the viewer`])
    expect(world.calls).toEqual([])
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([CHECK])
    expect(world.toasts[1]).toBe(`Copied the check for ${ID}`)
    world.copyResult = { isCopied: false, reason: 'no-clipboard' }
    await ui.press({ key: 'copy' })
    expect(world.toasts[2]).toMatch(/^Clipboard unavailable \(no-clipboard\)/)
  })

  test('v in tm mode opens the viewer through the tm server and names the task; a failure says so', TM, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    stateOf(on, { [`${PLUGIN}.snapshot`]: REAL, [`${PLUGIN}.details`]: DEMO_DETAILS })
    world.mcp = () => ({ text: 'Opened backlog viewer at http://127.0.0.1:4100/' })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    await ui.press({ key: 'viewer' })
    expect(world.calls).toEqual([{ tool: 'backlog_open_viewer', args: {} }])
    expect(world.toasts).toEqual([`Viewer opened: look for ${ID}`])
    world.mcp = () => 'offline'
    await ui.press({ key: 'viewer' })
    expect(world.toasts[1]).toMatch(/the viewer did not open/)
  })

  test('more than nine items: digit keys for the first nine, every item still drawn and tickable', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    const action = Array.from({ length: 11 }, (_, i) => `- step ${i + 1}`).join('\n')
    const detail = { ...DEMO_DETAILS[ID]!, humanAction: action }
    stateOf(on, {
      [`${PLUGIN}.snapshot`]: { ...REAL, queue: [{ kind: 'task', id: ID, title: 't', priority: 'high', humanAction: action, timestamp: '' }], queueTotal: 1 },
      [`${PLUGIN}.details`]: { [ID]: detail },
    })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(100) })
    expect((await ui.find({ type: 'Button', key: 'tick-8' }))?.props.hotkey).toBe('9')
    expect((await ui.find({ type: 'Button', key: 'tick-9' }))?.props.hotkey).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'tick-10' }))?.props.label).toBe('☐ step 11')
  })

  test('session start prunes ticks older than 30 days and unreadable ones, and nothing else', DEMO, async ($, on) => {
    const day = 86_400_000
    const clock = mock.clock(on, { now: 100 * day })
    const world = worldOf(on, clock, {
      'ticks:old-001': { at: 60 * day, items: ['x'] },
      'ticks:new-001': { at: 99 * day, items: ['y'] },
      'ticks:junk-001': 'junk',
      'binding:sess-A': { taskId: 'x', at: 1 },
    })
    await $.session.start(SESSION)
    expect([...world.store.keys()].sort()).toEqual(['binding:sess-A', 'ticks:new-001'])
  })
})
