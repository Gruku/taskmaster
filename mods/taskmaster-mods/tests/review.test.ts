// User intent: the review queue as a paged card list (spec §8) — cards in review order, five a page, only the focused one open,
// j / k and p / n to move, s to select, u to refresh — and the sign-off and send-back a person makes on the focused card.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, DEMO_PHASES, demoSnapshot } from '../hooks/demo'
import { FRESH_PHASE_VIEW, FRESH_PHASE_VIEWS } from '../hooks/phases'
import type { TmQueueItem, TmSnapshot } from '../types'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const REVIEW_PANE = pane('tm-review')
const SNAP = `${PLUGIN}.snapshot`
const REAL: TmSnapshot = { ...demoSnapshot(0), reason: '' }
/** Twelve waiting tasks, newest first: three pages. */
const TWELVE: TmQueueItem[] = Array.from({ length: 12 }, (_, i) => ({
  kind: 'task',
  id: `t-${String(i + 1).padStart(3, '0')}`,
  title: `Task ${i + 1}`,
  priority: 'high',
  humanAction: `Check ${i + 1}`,
  timestamp: new Date(Date.UTC(2026, 9, 10) - i * 3_600_000).toISOString(),
}))
const focusedId = async (ui: { findAll: (q: { type: 'Box' }) => Promise<{ props: Record<string, unknown> }[]> }): Promise<string> => {
  const boxes = await ui.findAll({ type: 'Box' })
  const open = boxes.filter(b => typeof b.props.key === 'string' && b.props.key.startsWith('card:') && b.props.borderStyle === 'round')
  return open.map(b => String(b.props.key).slice(5)).join(',')
}

describe('review list', () => {
  test('/tm-review opens the review pane', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-review'))).toEqual({ text: 'Review queue opened.' })
    expect(world.opened).toEqual(['tm-review'])
  })

  test('cards in review order, the summary and the kinds row; only the first card is open, and it is focused', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    const keys = (await ui.findAll({ type: 'Box' })).map(b => String(b.props.key ?? '')).filter(key => key.startsWith('card:'))
    expect(keys).toEqual(['card:unified-chat-022', 'card:tm-audit-031', 'card:docs-012', 'card:ISS-7', 'card:DEC-4'])
    expect(await focusedId(ui)).toBe('unified-chat-022')
    expect(await ui.find({ type: 'Text', text: '5 waiting on you · newest 1h ago' })).toBeDefined()
    for (const text of [' 1 BLOCKED ', ' 4 ACTION ', ' 0 IN REVIEW ', ' BLOCKED   ', ' ACTION    ']) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    expect(await ui.findAll({ type: 'Button', key: 'done' })).toHaveLength(1)
    expect(await ui.find({ text: /second message builds with the full toolset/ })).toBeDefined()
  })

  test('the toolbar holds REVIEW, the pager (dim on one page), its count and u refresh; the footer j / k', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'REVIEW' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'page-prev' }))?.props).toMatchObject({ hotkey: 'p', dimColor: true })
    expect((await ui.find({ type: 'Button', key: 'page-next' }))?.props).toMatchObject({ hotkey: 'n', dimColor: true })
    expect(await ui.find({ type: 'Text', text: '1 / 1' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'refresh' }))?.props).toMatchObject({ hotkey: 'u', label: 'refresh' })
    expect((await ui.find({ type: 'Button', key: 'focus-prev' }))?.props).toMatchObject({ hotkey: 'k', dimColor: true })
    expect((await ui.find({ type: 'Button', key: 'focus-next' }))?.props).toMatchObject({ hotkey: 'j' })
  })

  test('j and k move the focus card by card, the card left closes; at either end they dim and say why', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'focus-prev' })
    expect(world.toasts).toEqual(['Already on the first card'])
    await ui.press({ key: 'focus-next' })
    expect(await focusedId(ui)).toBe('tm-audit-031')
    expect(await ui.findAll({ type: 'Button', key: 'done' })).toHaveLength(1)
    for (let i = 0; i < 3; i += 1) await ui.press({ key: 'focus-next' })
    expect(await focusedId(ui)).toBe('DEC-4')
    await ui.press({ key: 'focus-next' })
    expect(world.toasts).toEqual(['Already on the first card', 'Already on the last card'])
    await ui.press({ key: 'focus-prev' })
    expect(await focusedId(ui)).toBe('ISS-7')
  })

  test('five a page: n turns to the next page and focuses its first card; j and k cross pages', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [SNAP]: { ...REAL, queue: TWELVE, queueTotal: 12 } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    const press = async (key: string) => {
      await ui.press({ key })
      await ui.redraw()
    }
    expect(await ui.find({ type: 'Text', text: '1 / 3' })).toBeDefined()
    await press('page-next')
    expect(await ui.find({ type: 'Text', text: '2 / 3' })).toBeDefined()
    expect(await focusedId(ui)).toBe('t-006')
    await press('focus-prev')
    expect(await ui.find({ type: 'Text', text: '1 / 3' })).toBeDefined()
    expect(await focusedId(ui)).toBe('t-005')
    await press('focus-next')
    await press('page-next')
    expect(await ui.find({ type: 'Text', text: '3 / 3' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'page-next' }))?.props.dimColor).toBe(true)
  })

  test('a phase change starts over: page 1, its first card focused, nothing selected', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, {
      [SNAP]: { ...REAL, queue: TWELVE, queueTotal: 12 },
      [`${PLUGIN}.phaseViews`]: { ...FRESH_PHASE_VIEWS, review: { ...FRESH_PHASE_VIEW, phases: DEMO_PHASES, loaded: true } },
    })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    for (const key of ['page-next', 'sel:t-006', 'phase-next']) {
      await ui.press({ key })
      await ui.redraw()
    }
    expect(await ui.find({ type: 'Text', text: '1 / 3' })).toBeDefined()
    expect(await focusedId(ui)).toBe('t-001')
    await ui.press({ key: 'page-next' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'sel:t-006' }))?.props.label).toBe('□')
  })

  test('under a phase the All tab counts the sum across the phases, not the filtered queue', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, {
      [SNAP]: { ...REAL, queue: TWELVE, queueTotal: 2 },
      [`${PLUGIN}.phaseViews`]: { ...FRESH_PHASE_VIEWS, review: { ...FRESH_PHASE_VIEW, phases: DEMO_PHASES, loaded: true } },
    })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, props: { ...REVIEW_PANE.props, bodyColumns: 100 } })
    expect((await ui.find({ type: 'Button', key: 'phase-tab:' }))?.props.label).toBe('All 2')
    await ui.press({ key: 'phase-next' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-tab:' }))?.props.label).toBe('All 5')
  })

  test('s selects the focused task; a press on any box selects that card; all takes the page, none clears', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Button', key: 'sel:unified-chat-022' }))?.props).toMatchObject({ hotkey: 's', label: '□' })
    expect((await ui.find({ type: 'Button', key: 'sel:tm-audit-031' }))?.props.hotkey).toBeUndefined()
    await ui.press({ key: 'sel:unified-chat-022' })
    expect((await ui.find({ type: 'Button', key: 'sel:unified-chat-022' }))?.props.label).toBe('■')
    await ui.press({ key: 'sel:ISS-7' })
    expect(world.toasts).toEqual(['Issues and decisions are not signed off from the list'])
    await ui.press({ key: 'sel-all' })
    for (const id of ['unified-chat-022', 'tm-audit-031', 'docs-012']) {
      expect((await ui.find({ type: 'Button', key: `sel:${id}` }))?.props.label, id).toBe('■')
    }
    expect((await ui.find({ type: 'Button', key: 'sel:ISS-7' }))?.props.label).toBe('□')
    await ui.press({ key: 'sel-none' })
    expect((await ui.find({ type: 'Button', key: 'sel:unified-chat-022' }))?.props.label).toBe('□')
  })

  test('the Tab ring landing on a card box focuses that card', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    on('ui.focus', () => ({}))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await $.ui.focus({ component: 'Pane', requestId: 'tm-review', plugin: PLUGIN, element: 'sel:docs-012', origin: { kind: 'person' } })
    await ui.redraw()
    expect(await focusedId(ui)).toBe('docs-012')
  })

  test('o fills the prompt and keeps the pane open; an issue card says to look at it with the agent', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'open' })
    for (let i = 0; i < 3; i += 1) await ui.press({ key: 'focus-next' })
    expect(await ui.find({ type: 'Text', text: /An open P1 issue: look at it with the agent/ })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'done' })).toBeUndefined()
    await ui.press({ key: 'open' })
    expect(world.fills).toEqual(['Look at unified-chat-022', 'Look at ISS-7'])
    expect(world.closed).toEqual([])
  })

  test('d asks first and the focused n cancels; y signs off and the next card takes the focus', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Button', key: 'done' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: / 2 of 2 unchecked — done anyway\? / })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    expect(await ui.find({ type: 'Box', key: 'card:unified-chat-022' })).toBeUndefined()
    expect(await focusedId(ui)).toBe('tm-audit-031')
    expect(await ui.find({ type: 'Text', text: /4 waiting on you · newest 5h ago · 1 done this pass/ })).toBeDefined()
  })

  test('the y/n draws after the pager, so its n is the one the key presses (the later of two wins)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    const drawn = JSON.stringify(await ui.drawn())
    expect(drawn.indexOf('"confirm-no"')).toBeGreaterThan(drawn.indexOf('"page-next"'))
  })

  test('moving off a card takes its y/n down: a y can never land on a card the person left', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'focus-next' })
    await ui.press({ key: 'focus-prev' })
    expect(await ui.find({ type: 'Button', key: 'confirm-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'done' })).toBeDefined()
  })

  test('a refusal is shown on the card in the server words, and the card stays', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'focus-next' })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    expect(await ui.find({ type: 'Text', text: / REFUSED / })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates for lane/ })).toBeDefined()
    expect(await focusedId(ui)).toBe('tm-audit-031')
  })

  test('a takes a note on a page of its own, no other key drawn; then the pane closes and the prompt holds the note', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface: 'terminal' })
    await ui.press({ key: 'back' })
    expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
    expect((await ui.findAll({ type: 'Button' })).map(b => b.props.key)).toEqual(['note-cancel'])
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    await ui.input({ key: 'note', text: 'tighten the copy' })
    expect(world.closed).toEqual(['tm-review'])
    expect(world.fills).toEqual(['Back to unified-chat-022: tighten the copy'])
  })

  test('an empty queue says Queue clear with the pass tally', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [SNAP]: { ...REAL, queue: [], queueTotal: 0 } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: ' Queue clear' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '  0 done this pass' })).toBeDefined()
  })

  test('u asks tm for a fresh snapshot and the phases; demo says it has nothing newer', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    const reads = (tool: string) => world.calls.filter(c => c.tool === tool).length
    const [lists, queries] = [reads('backlog_list_tasks'), reads('backlog_query')]
    await ui.press({ key: 'refresh' })
    await clock.settle()
    expect(reads('backlog_list_tasks')).toBe(lists + 1)
    expect(reads('backlog_query')).toBe(queries + 1)
  })

  test('demo: u refreshes nothing and says so', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'refresh' })
    expect(world.toasts).toEqual(['taskmaster-mods: demo data has nothing newer to read'])
    expect(world.calls).toEqual([])
  })

  test('offline: the critical signal with the reason, and u retry', TM, async ($, on) => {
    const clock = mock.clock(on)
    worldOf(on, clock)
    await $.session.start(SESSION)
    await clock.advance(60_000)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: ' Taskmaster unreachable' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'refresh' }))?.props).toMatchObject({ hotkey: 'u', label: 'retry' })
  })

  test('every mode validates on the terminal and the desktop', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface })
      await ui.press({ key: 'done' })
      await ui.press({ key: 'confirm-no' })
      await ui.press({ key: 'sel:unified-chat-022' })
      await ui.press({ key: 'back' })
      expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
      await ui.press({ key: 'note-cancel' })
      await ui.press({ key: 'sel-none' })
      await ui.unmount()
    }
  })

  test('a narrow pane never shows y without n, and n keeps the focus', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [72, 34, 28, 20]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, props: { ...REVIEW_PANE.props, bodyColumns: columns } })
      await ui.press({ key: 'done' })
      expect(await ui.find({ type: 'Button', key: 'confirm-yes' }), `${columns}`).toBeDefined()
      expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus, `${columns}`).toBe(true)
      expect(await ui.find({ type: 'Text', text: /unche/ }), `${columns}`).toBeDefined()
      await ui.press({ key: 'confirm-no' })
      await ui.unmount()
    }
  })

  test('tm mode: a sign-off the server cannot take is refused on the card, and the card stays', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [SNAP]: REAL, [`${PLUGIN}.details`]: DEMO_DETAILS })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    await ui.redraw()
    await ui.press({ key: 'confirm-yes' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /^Taskmaster unreachable: backlog_complete_task: / })).toBeDefined()
    expect(await focusedId(ui)).toBe('unified-chat-022')
  })

  test('a refresh between d and y never retargets y (Review Focus 5)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    await ui.redraw()
    // The refusing task turns critical: a y retargeted to the list's head would sign it off (and be refused).
    const [first, second, ...rest] = demo.queue
    state.set(SNAP, { ...demo, queue: [{ ...(second as TmQueueItem & { kind: 'task' }), priority: 'critical', timestamp: new Date(0).toISOString() }, first!, ...rest] })
    await ui.redraw()
    await ui.press({ key: 'confirm-yes' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /outstanding gates/ })).toBeUndefined()
    expect((state.get(SNAP) as TmSnapshot).queue.map(i => i.id)).toEqual(['tm-audit-031', 'docs-012', 'ISS-7', 'DEC-4'])
  })
})
