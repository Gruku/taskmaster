// User intent: pick the review queue's phase from inside the review pane with a two-way strip (spec §5) on a big backlog —
// tabs, h / l both ways, the overlay for what does not fit, done phases on demand — the choice kept per pane and project.
import { describe, expect, mock, test } from 'claude-code/testing'

import { OVERLAY_PLACEMENT } from '../hooks/kit'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type World, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const REVIEW_PANE = pane('tm-review')
const WIDE_PANE = { ...REVIEW_PANE, props: { ...REVIEW_PANE.props, bodyColumns: 400 } }
const PHASE_SQL =
  "SELECT json_array(p.id, p.status, json_extract(p.doc,'$.order'), (SELECT count(*) FROM entities t WHERE t.kind='task' AND t.deleted=0 AND t.status='in-review' AND json_extract(t.doc,'$.phase')=p.id AND coalesce(json_extract(t.doc,'$.human_action'),'')<>''), 0, CASE WHEN p.status IN ('done','archived') THEN substr(coalesce(json_extract(p.doc,'$.completed'),''),1,10) ELSE substr(coalesce(json_extract(p.doc,'$.start_date'),''),1,10) END) AS phase, json_extract(p.doc,'$.name') AS name FROM entities p WHERE p.kind='phase' AND p.deleted=0 ORDER BY json_extract(p.doc,'$.order') DESC"
const STRONG_SIGNATURE = '#4a5590' // rr-stub's 24% signature tint
const listArgs = (world: World) => world.calls.filter(c => c.tool === 'backlog_list_tasks').map(c => c.args)
const queries = (world: World) => world.calls.filter(c => c.tool === 'backlog_query')
const reviewKey = (world: World) => `phase:review:${world.root}`

describe('review phase strip', () => {
  test('opening the pane reads the phase list once with the exact query; the strip shows All and the active phase', TM, async ($, on) => {
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
    expect((await ui.find({ type: 'Button', key: 'phase-tab:' }))?.props.label).toMatch(/^All \d+$/)
    expect((await ui.find({ type: 'Button', key: 'phase-tab:2-0-4' }))?.props.label).toBe('Patch 2.0.4 · active 249')
    expect((await ui.find({ type: 'Box', key: 'phase-tab:-box' }))?.props.backgroundColor).toBe(STRONG_SIGNATURE)
    expect((await ui.find({ type: 'Button', key: 'phase-more' }))?.props).toMatchObject({ hotkey: 'f' })
    expect(await ui.find({ type: 'Button', key: 'phase' })).toBeUndefined()
  })

  test('l and h step both ways and stop at All; each step is kept and the list_tasks phase arg follows', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    const steps = [
      ['phase-next', '2-0-4'],
      ['phase-next', 'patch-1-9-5'],
      ['phase-prev', '2-0-4'],
      ['phase-prev', ''],
    ] as const
    for (const [key, phase] of steps) {
      await ui.press({ key })
      await clock.settle()
      await ui.redraw()
      expect(world.store.get(reviewKey(world)), `${key} → ${phase}`).toEqual({ phase })
      expect(listArgs(world).at(-1)?.phase, `${key} → ${phase}`).toBe(phase === '' ? undefined : phase)
    }
    const calls = listArgs(world).length
    await ui.press({ key: 'phase-prev' })
    await clock.settle()
    expect(world.toasts).toContain('Already on the first phase')
    expect(listArgs(world)).toHaveLength(calls)
  })

  test('a tab press chooses that phase and fills it; the cursor goes back to the first card', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE_PANE })
    await ui.press({ key: 'skip' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
    await ui.press({ key: 'phase-tab:p2' })
    await clock.settle()
    await ui.redraw()
    expect(world.store.get(reviewKey(world))).toEqual({ phase: 'p2' })
    expect((await ui.find({ type: 'Box', key: 'phase-tab:p2-box' }))?.props.backgroundColor).toBe(STRONG_SIGNATURE)
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
  })

  test('done phases stay hidden over four until ◐ done; a chosen done phase stays drawn when they hide again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE_PANE })
    expect(await ui.find({ type: 'Button', key: 'phase-tab:2-0-3' })).toBeUndefined()
    await ui.press({ key: 'phase-done' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-done' }))?.props.label).toBe('◐ done')
    await ui.press({ key: 'phase-more' })
    await clock.settle()
    await ui.redraw()
    await ui.press({ key: 'phase-pick:2-0-3' })
    await clock.settle()
    await ui.press({ key: 'phase-done' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-done' }))?.props.label).toBe('○ done')
    expect((await ui.find({ type: 'Button', key: 'phase-tab:2-0-3' }))?.props).toMatchObject({ label: 'Patch 2.0.3 ✓ 1', dimColor: true })
  })

  test('f opens the overlay with every phase; a pick chooses it and closes the overlay', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'phase-more' })
    await clock.settle()
    await ui.redraw()
    expect(queries(world)).toHaveLength(2)
    expect((await ui.find({ type: 'Button', key: 'phase-pick:p1' }))?.props.label).toBe('Pre-Alpha · done · 0')
    if (OVERLAY_PLACEMENT === 'swap') expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
    await ui.press({ key: 'phase-pick:p2' })
    await clock.settle()
    await ui.redraw()
    expect(world.store.get(reviewKey(world))).toEqual({ phase: 'p2' })
    expect(await ui.find({ type: 'Button', key: 'phase-pick:p1' })).toBeUndefined()
    expect(listArgs(world).at(-1)?.phase).toBe('p2')
  })

  test('the legacy phase:<root> key moves to phase:review:<root> and is the filter from the first refresh', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(`phase:${world.root}`, { phase: '2-0-6' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true, phase: '2-0-6' }])
    expect(world.store.get(reviewKey(world))).toEqual({ phase: '2-0-6' })
    expect(world.store.has(`phase:${world.root}`)).toBe(false)
  })

  test('a stored choice is the filter and the chosen tab; another project keeps its own', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(reviewKey(world), { phase: '2-0-6' })
    world.store.set('phase:review:somewhere-else', { phase: 'p2' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true, phase: '2-0-6' }])
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Box', key: 'phase-tab:2-0-6-box' }))?.props.backgroundColor).toBe(STRONG_SIGNATURE)
  })

  test('the reviewPhase option is the default; a stored choice, an empty one included, overrides it', { ...TM, options: { reviewPhase: ' p2 ' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true, phase: 'p2' }])
  })

  test('an explicit All in the store overrides a non-empty reviewPhase option', { ...TM, options: { reviewPhase: 'p2' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(reviewKey(world), { phase: '' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world)).toEqual([{ status: 'in-review', limit: 50, waiting_on_human: true }])
  })

  test('a remembered phase the list no longer holds falls back to All with a toast, and the store follows', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.store.set(reviewKey(world), { phase: 'gone-phase' })
    await $.session.start(SESSION)
    await clock.settle()
    expect(listArgs(world).at(-1)?.phase).toBe('gone-phase')
    await $.command.run(command('tm-review'))
    await clock.settle()
    expect(world.toasts).toContain('taskmaster-mods: phase gone-phase no longer exists; showing All')
    expect(world.store.get(reviewKey(world))).toEqual({ phase: '' })
    expect(listArgs(world).at(-1)?.phase).toBeUndefined()
  })

  test('scope all counts every in-review task and sends no human_action condition', { ...TM, options: { reviewScope: 'all' } }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const sql = String(queries(world)[0]?.args.sql)
    expect(sql).toBe(PHASE_SQL.replace(" AND coalesce(json_extract(t.doc,'$.human_action'),'')<>''", ''))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase-tab:2-0-4' }))?.props.label).toBe('Patch 2.0.4 · active 252')
  })

  test('a failing or refusing phase read leaves the strip alone, logs, and the pane still draws', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.store.set(reviewKey(world), { phase: '2-0-6' })
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
    expect((await ui.find({ type: 'Button', key: 'phase-tab:2-0-6' }))?.props.label).toBe('2-0-6 0')
    for (const next of ['refused', 'error', 'garbage', 'offline']) {
      mode = next
      const before = world.logs.length
      await ui.press({ key: 'phase-more' })
      await clock.settle()
      await ui.redraw()
      expect(world.logs.length, next).toBeGreaterThan(before)
      expect((await ui.find({ type: 'Button', key: 'phase-tab:2-0-6' }))?.props.label, next).toBe('2-0-6 0')
      expect(world.store.get(reviewKey(world)), next).toEqual({ phase: '2-0-6' })
      await ui.press({ key: 'phase-close' })
      await ui.redraw()
      expect(await ui.find({ type: 'Text', text: 'unified-chat-022' }), next).toBeDefined()
    }
    expect(world.logs.every(line => line.to === 'debug')).toBe(true)
  })

  test('an empty queue under a phase says so and offers 0 All, which goes back to every phase', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const quiet = JSON.stringify({ view: 'action', total: 0, items: [] })
    world.mcp = (tool, args) => {
      if (tool === 'backlog_list_tasks' && args.phase !== undefined) return { text: R.LIST_EMPTY }
      if (tool === 'backlog_issue_list') return { text: R.NO_ISSUES }
      if (tool === 'backlog_continuity_items') return { text: quiet }
      return R.backlog(tool, args)
    }
    world.store.set(reviewKey(world), { phase: '2-0-6' })
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'Nothing waiting in Patch 2.0.6' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'phase-all' }))?.props).toMatchObject({ hotkey: '0', label: 'All' })
    await ui.press({ key: 'phase-all' })
    await clock.settle()
    expect(world.store.get(reviewKey(world))).toEqual({ phase: '' })
  })

  test('demo draws its own phases and steps them without touching the store or tm', { options: { source: 'demo' }, plugins: [RR_STUB] }, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase-tab:patch-1-9' }))?.props.label).toBe('Patch 1.9 · active 3')
    await ui.press({ key: 'phase-next' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Box', key: 'phase-tab:patch-1-9-box' }))?.props.backgroundColor).toBe(STRONG_SIGNATURE)
    expect(world.calls).toEqual([])
    expect([...world.store.keys()].filter(k => k.startsWith('phase:'))).toEqual([])
  })
})
