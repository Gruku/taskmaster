// User intent: the handovers pane as a person uses it (spec §9) — opened by /tm-handovers, phases from linked tasks with a no
// task row, the focused card's keys (r resume here and recorded, w new tab, c Telegram copy, i decisions·blockers, x close
// with y/n), bulk Close N, `◐ closed`, j / k and u — in demo and against the tm server's own words.
import { describe, expect, mock, test } from 'claude-code/testing'

import { CLOSE_REASON } from '../hooks/actions'
import { demoHandoverList, demoSnapshot } from '../hooks/demo'
import { resumePrompt } from '../hooks/launch'
import { handoverCopyText, handoverPath } from '../hooks/model'
import { NO_TASK, phaseKey } from '../hooks/phases'
import { HANDOVER_PANE_LIMIT, linkedTasksSql, NO_TASK_SQL, phaseSql } from '../hooks/tm'
import type { TmHandover } from '../types'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type World, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const HANDOVERS_PANE = pane('tm-handovers')
const WIDE = { ...HANDOVERS_PANE, props: { ...HANDOVERS_PANE.props, bodyColumns: 140 } }
const SAMPLE = demoSnapshot(0).handovers
const [FIRST, SECOND, THIRD] = SAMPLE as readonly [TmHandover, TmHandover, TmHandover]
const OPEN_ID = '2026-10-05-shipped-unified-chat-022'
const AUDIT_ID = '2026-10-04-audit-fixes-landed'
type Ui = { findAll: (q: { type: string }) => Promise<{ key?: string; props: Record<string, unknown> }[]> }
const cardKeys = async (ui: Ui) =>
  (await ui.findAll({ type: 'Box' })).map(b => b.key ?? '').filter(key => key.startsWith('card:'))
/** The items drawn on the page: each title is its `ho:<id>` pick Button. */
const itemKeys = async (ui: Ui) =>
  (await ui.findAll({ type: 'Button' })).map(b => b.key ?? '').filter(key => key.startsWith('ho:'))
const writes = (world: World) => world.calls.filter(c => c.tool === 'backlog_handover_update_status')
const textsIn = (tree: unknown): string[] =>
  elementsOf(tree)
    .filter(e => e.type === 'Text')
    .map(e => (e.children ?? []).join(''))

describe('handovers pane, demo', () => {
  test('/tm-handovers opens it: three items, the first focused, the picker with a no task row, nothing asked of tm', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-handovers'))).toEqual({ text: 'Handovers opened.' })
    expect(world.opened).toEqual(['tm-handovers'])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    expect(await itemKeys(ui)).toEqual(SAMPLE.slice(0, 3).map(x => `ho:${x.id}`))
    expect(await cardKeys(ui)).toEqual([`card:${FIRST.id}`])
    expect((await ui.find({ type: 'Box', key: `card:${FIRST.id}` }))?.props.borderStyle).toBe('round')
    await ui.press({ key: 'phase-field' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: `phase-pick:${NO_TASK}` }))?.props.label).toMatch(/^no task +2$/)
    expect(world.calls).toEqual([])
  })

  test('a phase picked from the list narrows the items to the handovers its tasks are in; All goes back', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'phase-field' })
    await ui.redraw()
    await ui.press({ key: 'phase-pick:release-2' })
    await ui.redraw()
    expect(await itemKeys(ui)).toEqual(['ho:2026-10-02-store-read-hang'])
    await ui.press({ key: 'phase-field' })
    await ui.redraw()
    await ui.press({ key: 'phase-pick:' })
    await ui.redraw()
    expect(await itemKeys(ui)).toHaveLength(3)
  })

  test('c copies the Telegram block of the focused handover; r fills the resume line and keeps the pane', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: `ho:${SECOND.id}` })
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([handoverCopyText(SECOND)])
    await ui.press({ key: 'resume' })
    expect(world.fills).toEqual([resumePrompt(SECOND.id, SECOND.path)])
    expect(world.closed).toEqual([])
  })

  test('w hands Stage 2 a fresh launch, which demo only names', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: 'new-tab' })
    expect(world.toasts).toEqual([`Demo: would open handover ${FIRST.id}`])
  })

  test('c copies a handover only the pane lists (closed, past the snapshot window) in the same Telegram block', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'show-closed' })
    await ui.press({ key: 'page-next' })
    await ui.redraw()
    await ui.press({ key: 'ho:2026-09-30-row-counter-ported' })
    await ui.redraw()
    await ui.press({ key: 'copy' })
    const closed = demoHandoverList(0).handovers.find(x => x.id === '2026-09-30-row-counter-ported')!
    expect(world.copies).toEqual([handoverCopyText(closed)])
  })

  test('marks select, close 2 handovers asks y/n, y closes both with one toast; the items leave', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: `ho-sel:${FIRST.id}` })
    await ui.press({ key: `ho:${SECOND.id}` })
    await ui.press({ key: `ho-sel:${SECOND.id}` })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'close-selected' }))?.props.label).toBe('close 2 handovers')
    await ui.press({ key: 'close-selected' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'close 2 handovers?' })).toBeDefined()
    await ui.press({ key: 'bulk-close-yes' })
    await ui.redraw()
    expect(world.toasts).toEqual(['2 closed'])
    expect(await itemKeys(ui)).toEqual([THIRD, ...SAMPLE.slice(3, 5)].map(x => `ho:${x.id}`))
    expect((await ui.find({ type: 'Button', key: 'close-selected' }))?.props.label).toBe('close 0 handovers')
  })

  test('x asks on the card, n keeps it; ◐ show closed shows closed ones faint with x dim on them', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'close' })
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'close-yes' })).toBeDefined()
    await ui.press({ key: 'close-no' })
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'close-yes' })).toBeUndefined()
    expect(await itemKeys(ui)).toHaveLength(3)
    await ui.press({ key: 'show-closed' })
    await ui.press({ key: 'page-next' })
    await ui.redraw()
    expect(await itemKeys(ui)).toContain('ho:2026-09-30-row-counter-ported')
    await ui.press({ key: 'ho:2026-09-30-row-counter-ported' })
    await ui.redraw()
    expect(textsIn(await ui.drawn())).toContain('✓ CLOSED       ')
    await ui.press({ key: 'close' })
    expect(world.toasts).toEqual(['Already closed'])
  })

  test('j moves the focus down a card and across the page; k back up; k on the first one says so', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'move-up' })
    expect(world.toasts).toEqual(['Already on the first handover'])
    await ui.press({ key: 'move-down' })
    await ui.redraw()
    expect((await ui.find({ type: 'Box', key: `card:${SECOND.id}` }))?.props.borderStyle).toBe('round')
    await ui.press({ key: 'move-up' })
    await ui.redraw()
    expect((await ui.find({ type: 'Box', key: `card:${FIRST.id}` }))?.props.borderStyle).toBe('round')
  })
})

describe('handovers pane, tm', () => {
  test('opening reads the phases, the no task row, the list past the index and the linked tasks, one call at a time', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    const before = world.calls.length
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    expect(world.calls.slice(before).map(c => c.args)).toEqual([
      { sql: phaseSql('handovers', true), limit: 200 },
      { sql: NO_TASK_SQL, limit: 200 },
      { format: 'json', status: 'open', limit: HANDOVER_PANE_LIMIT, include_archived: true },
      { sql: linkedTasksSql(['unified-chat-022', 'tm-audit-030']), limit: 500 },
    ])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: `ho:${AUDIT_ID}` })
    await ui.redraw()
    expect(textsIn(await ui.drawn())).toEqual(expect.arrayContaining(['● IN PROGRESS', 'tm-audit-030', '  Agent tool-use audit fixes', '  Patch 2.0.6']))
  })

  test('r fills the resume line and records this session as resumed from it', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'resume' })
    await clock.settle()
    expect(world.fills).toEqual([resumePrompt(OPEN_ID, handoverPath(world.root, OPEN_ID))])
    expect(world.store.has(`resumed-from:${world.sessionId}`)).toBe(true)
  })

  test('x then y closes with backlog_handover_update_status, toasts, refreshes and reads the list again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_handover_update_status' ? { text: R.handoverClosed(String(args.handover_id)) } : R.backlog(tool, args))
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const lists = world.calls.filter(c => c.tool === 'backlog_list_tasks').length
    const reads = world.calls.filter(c => c.tool === 'backlog_handover_list').length
    await ui.press({ key: 'close' })
    await ui.press({ key: 'close-yes' })
    await clock.settle()
    expect(writes(world).map(c => c.args)).toEqual([{ handover_id: OPEN_ID, status: 'closed', reason: CLOSE_REASON }])
    expect(world.toasts).toContain('1 closed')
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks').length).toBeGreaterThan(lists)
    expect(world.calls.filter(c => c.tool === 'backlog_handover_list').length).toBeGreaterThan(reads + 1)
    expect(await ui.find({ type: 'Button', key: 'close-yes' })).toBeUndefined()
  })

  test('a refused close keeps the card and the selection, and the toast carries the server wording', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_handover_update_status' ? { text: `Handover not found: ${String(args.handover_id)}` } : R.backlog(tool, args))
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: `ho-sel:${OPEN_ID}` })
    await ui.press({ key: 'close-selected' })
    await ui.press({ key: 'bulk-close-yes' })
    await clock.settle()
    await ui.redraw()
    expect(world.toasts).toContain(`0 closed · 1 refused: ${OPEN_ID} (Handover not found: ${OPEN_ID})`)
    expect(await itemKeys(ui)).toContain(`ho:${OPEN_ID}`)
    expect((await ui.find({ type: 'Button', key: 'close-selected' }))?.props.label).toBe('close 1 handover')
  })

  test('◐ show closed reads the closed handovers once; u refreshes the snapshot and reads the pane again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'show-closed' })
    await clock.settle()
    await ui.redraw()
    expect(world.calls.filter(c => c.tool === 'backlog_handover_list').map(c => c.args.status)).toEqual(['open', 'open', 'open', 'closed'])
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props.label).toMatch(/^ All +\d+ ▾ $/)
    expect(await itemKeys(ui)).toContain('ho:2026-10-02-row-counter-ported')
    const before = world.calls.length
    await ui.press({ key: 'refresh' })
    await clock.settle()
    const after = world.calls.slice(before)
    expect(after.some(c => c.tool === 'backlog_list_tasks')).toBe(true)
    expect(after.filter(c => c.tool === 'backlog_handover_list' && c.args.limit === HANDOVER_PANE_LIMIT).map(c => c.args.status)).toEqual(['open', 'closed'])
  })

  test('all selects the open handovers of this page only; b is close N handovers', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = (tool, args) => (tool === 'backlog_handover_list' ? { text: R.handoversOpen(7) } : R.backlog(tool, args))
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'select-all' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'close-selected' }))?.props).toMatchObject({ hotkey: 'b', label: 'close 3 handovers' })
  })

  test('a phase choice is kept per project for the handovers pane', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-handovers'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'phase-next' })
    await clock.settle()
    expect(world.store.get(phaseKey('handovers', world.root))).toEqual({ phase: '2-0-4' })
  })
})
