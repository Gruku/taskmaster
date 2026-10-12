// User intent: the review pane as the approved prototype draws it (spec §15.6–15.7, Review.dc.html) — the frame, the picker
// row, one summary sentence with its tallies, the bulk row with counted labels, 3 items a page with only the focused one boxed
// and one filled key, a dim footer that owns the navigation keys, no shared hotkey, and every row inside the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf, widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const WIDE = { ...pane('tm-review'), props: { ...pane('tm-review').props, bodyColumns: 112 } }
type Ui = { findAll: (q: { type: 'Button' }) => Promise<{ props: Record<string, unknown> }[]> }
const hotkeys = async (ui: Ui): Promise<Record<string, string[]>> => {
  const keys: Record<string, string[]> = {}
  for (const b of await ui.findAll({ type: 'Button' })) if (typeof b.props.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}

describe('the review pane, as drawn', () => {
  test('the frame names REVIEW; row 1 is the picker, the pager and u refresh; row 2 the sentence and tallies', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    for (const text of ['REVIEW', 'demo', ' All  5 ▾ ', ' 1 / 2 ', '5 waiting on you · newest 1h ago', '▲ BLOCKED', ' 1', '◆ ACTION', ' 4', '● IN REVIEW', ' 0']) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    expect((await ui.find({ type: 'Button', key: 'refresh' }))?.props.label).toBe('u refresh')
  })

  test('3 items a page; only the focused one is boxed, its first action the one filled key; collapsed items carry no keys', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const marks = (await ui.findAll({ type: 'Button' })).filter(b => String(b.props.key).startsWith('sel:'))
    expect(marks.map(b => b.props.key)).toEqual(['sel:unified-chat-022', 'sel:tm-audit-031', 'sel:docs-012'])
    const boxes = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round' && String(e.props?.key ?? '').startsWith('card:'))
    expect(boxes.map(b => b.props?.key)).toEqual(['card:unified-chat-022'])
    for (const [key, hotkey, label] of [
      ['done', 'd', 'mark task done'],
      ['back', 'a', 'send task to agent'],
      ['open', 'o', 'open task'],
      ['viewer', 'v', 'task in viewer'],
      ['details', 'i', 'task detail ▸'],
      ['tick', 't', 'tick check 1-9'],
      ['copy', 'c', 'copy live check'],
      ['bulk-done', 'b', 'mark 0 tasks done'],
      ['bulk-agent', 'g', 'send 0 tasks to agent'],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, label })
    }
    const filled = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.backgroundColor === '#8a9eeb')
    expect(filled.map(e => e.props?.key)).toEqual(['done-box'])
    expect(await ui.find({ type: 'Text', text: 'human  ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^1 {2}$/ })).toBeDefined()
  })

  test('the footer owns j k s h l f p n u m; no hotkey is on two Buttons; the sel marks carry none', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const keys = await hotkeys(ui)
    for (const [hotkey, id] of [['j', 'focus-next'], ['k', 'focus-prev'], ['s', 'key:s'], ['h', 'key:h'], ['l', 'key:l'], ['f', 'key:f'], ['p', 'key:p'], ['n', 'key:n'], ['u', 'key:u'], ['m', 'key:m']] as const) {
      expect(keys[hotkey], hotkey).toEqual([id])
    }
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    expect(keys['0']).toBeUndefined()
    for (const text of ['j/k', ' move', 's', ' select', 'm', ' sidebar', 'esc', ' close']) expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
  })

  test('while a y/n is up the footer drops p/n, so only the y/n carries n', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'done' })
    expect((await hotkeys(ui)).n).toEqual(['confirm-no'])
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'sel:unified-chat-022' })
    await ui.press({ key: 'bulk-done' })
    expect((await hotkeys(ui)).n).toEqual(['bulk-no'])
  })

  test('f opens the dropdown over the list: digits pick only while it is open; s selects the focused task', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'key:f' })
    await ui.redraw()
    expect((await hotkeys(ui))['0']).toEqual(['phase-pick:'])
    await ui.press({ key: 'phase-pick:patch-1-9' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props.label).toBe(' Patch 1.9 · active  3 ▾ ')
    await ui.press({ key: 'key:s' })
    expect((await ui.find({ type: 'Button', key: 'sel:unified-chat-022' }))?.props.label).toBe('■')
  })

  test('every row fits the pane at 60 and 100 columns', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [60, 100]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE, props: { ...WIDE.props, bodyColumns: columns } })
      const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}: ${JSON.stringify(row).slice(0, 120)}`).toBeLessThanOrEqual(columns - 4)
      await ui.unmount()
    }
  })
})
