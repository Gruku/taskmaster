// User intent: filter the review queue by phase from inside the review pane (f), on a big backlog where one phase's tasks
// are what the person wants to walk — the choice kept per project across restarts, the phase list read only when asked for.
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type World, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const REVIEW_PANE = pane('tm-review')
const PHASE_SQL =
  "SELECT json_array(p.id, p.status, json_extract(p.doc,'$.order'), (SELECT count(*) FROM entities t WHERE t.kind='task' AND t.deleted=0 AND t.status='in-review' AND json_extract(t.doc,'$.phase')=p.id AND coalesce(json_extract(t.doc,'$.human_action'),'')<>'')) AS phase, json_extract(p.doc,'$.name') AS name FROM entities p WHERE p.kind='phase' AND p.deleted=0 ORDER BY json_extract(p.doc,'$.order') DESC"
const listArgs = (world: World) => world.calls.filter(c => c.tool === 'backlog_list_tasks').map(c => c.args)
const queries = (world: World) => world.calls.filter(c => c.tool === 'backlog_query')

describe('review phase filter', () => {
  test('opening the pane reads the phase list once, with the exact query; ordinary refreshes do not', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(queries(world)).toEqual([])
    await $.command.run(command('tm-review'))
    await clock.settle()
    expect(queries(world).map(c => c.args)).toEqual([{ sql: PHASE_SQL, limit: 200 }])
    await $.turn.complete({ answer: '', reason: 'end_turn' } as never)
    await clock.settle()
    expect(queries(world)).toHaveLength(1)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'phase: all' })).toBeDefined()
  })

  test('f cycles all, the active phase, the others with waiting tasks newest first, then all; the list_tasks phase arg follows', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase' }))?.props).toMatchObject({ hotkey: 'f', label: 'phase' })
    const steps = [
      ['phase: Patch 2.0.4 (249)', '2-0-4'],
      ['phase: Release 2.1.0 (16)', '2-1-0'],
      ['phase: Patch 2.0.6 (33)', '2-0-6'],
      ['phase: Patch 2.0.3 (1)', '2-0-3'],
      ['phase: Patch 2.0.1 (8)', '2-0-1'],
      ['phase: Patch 1.9.5 (47)', 'patch-1-9-5'],
      ['phase: all', undefined],
    ] as const
    for (const [label, phase] of steps) {
      await ui.press({ key: 'phase' })
      await clock.settle()
      await ui.redraw()
      expect(await ui.find({ type: 'Text', text: label }), label).toBeDefined()
      const last = listArgs(world).at(-1)
      expect(last?.phase, label).toBe(phase)
    }
    expect(listArgs(world).at(-1)).toEqual({ status: 'in-review', limit: 50, waiting_on_human: true })
  })

  test('changing the filter puts the cursor back on the first card', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'skip' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
    await ui.press({ key: 'phase' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
  })

  test('the choice is kept per project in the store and a new session starts with it', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'phase' })
    await clock.settle()
    expect(world.store.get(`phase:${world.root}`)).toEqual({ phase: '2-0-4' })
  })

  test('a stored choice is the filter from the first refresh and the label', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(`phase:${world.root}`, { phase: '2-0-6' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true, phase: '2-0-6' }])
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'phase: Patch 2.0.6 (33)' })).toBeDefined()
  })

  test('another project keeps its own choice', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set('phase:somewhere-else', { phase: '2-0-6' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true }])
  })

  test('the reviewPhase option is the default; a stored choice, an empty one included, overrides it', { ...TM, options: { reviewPhase: ' p2 ' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true, phase: 'p2' }])
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    // p2 has no waiting tasks and is not active: the cycle does not offer it, so f goes to all
    await ui.press({ key: 'phase' })
    await clock.settle()
    expect(world.store.get(`phase:${world.root}`)).toEqual({ phase: '' })
    expect(listArgs(world).at(-1)).toEqual({ status: 'in-review', limit: 50, waiting_on_human: true })
  })

  test('an explicit all in the store overrides a non-empty reviewPhase option', { ...TM, options: { reviewPhase: 'p2' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(`phase:${world.root}`, { phase: '' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true }])
  })

  test('scope all counts every in-review task and sends no human_action condition', { ...TM, options: { reviewScope: 'all' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const sql = String(queries(world)[0]?.args.sql)
    expect(sql).not.toContain('human_action')
    expect(sql).toBe(PHASE_SQL.replace(" AND coalesce(json_extract(t.doc,'$.human_action'),'')<>''", ''))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'phase' })
    await clock.settle()
    await ui.press({ key: 'phase' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'phase: Release 2.1.0 (16)' })).toBeDefined()
    expect(listArgs(world).at(-1)).toEqual({ status: 'in-review', limit: 50, phase: '2-1-0' })
  })

  test('a failing or refusing phase read leaves the filter alone, logs, and the pane still draws', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.store.set(`phase:${world.root}`, { phase: '2-0-6' })
    const answers: Record<string, () => { text: string; isError?: boolean } | 'offline'> = {
      refused: () => ({ text: 'Error: unknown tool backlog_query', isError: true }),
      error: () => ({ text: 'Error: no such table: entities' }),
      garbage: () => ({ text: 'not a table' }),
      offline: () => 'offline',
    }
    let mode = 'refused'
    world.mcp = (tool, args) => (tool === 'backlog_query' ? (answers[mode]?.() ?? 'offline') : R.backlog(tool, args))
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'phase: 2-0-6' })).toBeDefined()
    for (const next of ['refused', 'error', 'garbage', 'offline']) {
      mode = next
      const before = world.logs.length
      await ui.press({ key: 'phase' })
      await clock.settle()
      await ui.redraw()
      expect(await ui.find({ type: 'Text', text: 'phase: 2-0-6' }), next).toBeDefined()
      expect(world.logs.length, next).toBeGreaterThan(before)
      expect(world.store.get(`phase:${world.root}`), next).toEqual({ phase: '2-0-6' })
      expect(await ui.find({ type: 'Text', text: 'unified-chat-022' }), next).toBeDefined()
    }
    expect(world.logs.every(line => line.to === 'debug')).toBe(true)
  })

  test('an empty queue under a filter still offers f, so the person is never stuck', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const quiet = JSON.stringify({ view: 'action', total: 0, items: [] })
    world.mcp = (tool, args) => {
      if (tool === 'backlog_list_tasks' && args.phase !== undefined) return { text: R.LIST_EMPTY }
      if (tool === 'backlog_issue_list') return { text: R.NO_ISSUES }
      if (tool === 'backlog_continuity_items') return { text: quiet }
      return R.backlog(tool, args)
    }
    world.store.set(`phase:${world.root}`, { phase: '2-0-6' })
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'phase: Patch 2.0.6 (33)' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Queue clear/ })).toBeDefined()
    await ui.press({ key: 'phase' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'phase: Patch 2.0.3 (1)' })).toBeDefined()
  })

  test('demo mode cycles its own phases and never touches the store or tm', { options: { source: 'demo' }, plugins: [RR_STUB] }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'phase: all' })).toBeDefined()
    await ui.press({ key: 'phase' })
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /^phase: (?!all)/ })).toBeDefined()
    expect(world.calls).toEqual([])
    expect([...world.store.keys()].filter(k => k.startsWith('phase:'))).toEqual([])
  })
})
