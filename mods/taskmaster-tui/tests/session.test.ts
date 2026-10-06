// User intent: this session's task binding as a person relies on it — set by its own pick, kept in review, cleared when
// done, carried through /clear and /resume, never borrowed from another session, stale mirrors pruned.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import { BAND, SESSION } from './fixtures/inputs'
import { backlog } from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const T = 'mcp__plugin_taskmaster_tm__'
const NOW = Date.parse('2026-10-05T12:00:00Z')
const DAY = 24 * 3_600_000

function toolAnswers(on: On, answers: Record<string, string>): void {
  on('tool.call', ($, e) => {
    const text = answers[String(e.tool).replace(T, '')] ?? 'ok'
    return { result: { content: [{ type: 'text', text }] }, text } as never
  })
}
const call = (tool: string, input: Record<string, unknown>) => ({ tool: T + tool, ...input }) as never
const band = async ($: Engine): Promise<string> => JSON.stringify(await $.ui.render(BAND))

describe('session binding', () => {
  test('its own pick binds the session: the band shows the task and its stage, the store mirrors it', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    toolAnswers(on, { backlog_pick_task: 'Picked `tm-audit-030` — Agent tool-use audit fixes (locked to this session)' })
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call(call('backlog_pick_task', { task_id: 'tm-audit-030' }))
    await clock.settle()
    expect(world.store.get('binding:sess-A')).toEqual({ taskId: 'tm-audit-030', at: NOW })
    const tree = await band($)
    expect(tree).toContain('tm-audit-030')
    expect(tree).toContain('IN PROGRESS → next: merge')
    expect(tree).toContain('FULL · review-gate:pass')
  })

  test('in review keeps the binding; done clears it and its store key', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    toolAnswers(on, {
      backlog_pick_task: 'Picked `tm-audit-030` — T',
      backlog_update_task: 'Updated `tm-audit-030` field `status` → in-review [seq 3]',
      backlog_complete_task: 'Completed `tm-audit-030` — T [seq 4]',
    })
    await $.session.start(SESSION)
    await $.tool.call(call('backlog_pick_task', { task_id: 'tm-audit-030' }))
    await $.tool.call(call('backlog_update_task', { task_id: 'tm-audit-030', field: 'status', value: 'in-review' }))
    await clock.settle()
    expect(world.store.has('binding:sess-A')).toBe(true)
    await $.tool.call(call('backlog_complete_task', { task_id: 'tm-audit-030', done: 'x' }))
    await clock.settle()
    expect(world.store.has('binding:sess-A')).toBe(false)
    const tree = await band($)
    expect(tree).not.toContain('tm-audit-030')
    expect(tree).toContain('4 waiting on you')
  })

  test('a refused pick binds nothing', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    toolAnswers(on, { backlog_pick_task: 'Error: task `tm-audit-030` is `done`, expected one of: todo, in-progress, in-review' })
    await $.session.start(SESSION)
    await $.tool.call(call('backlog_pick_task', { task_id: 'tm-audit-030' }))
    await clock.settle()
    expect(world.store.has('binding:sess-A')).toBe(false)
  })

  test("a subagent's pick binds nothing: only this session's main loop moves the band", TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    toolAnswers(on, { backlog_pick_task: 'Picked `tm-audit-030` — T (locked to this session)' })
    await $.session.start(SESSION)
    await $.tool.call({ tool: `${T}backlog_pick_task`, task_id: 'tm-audit-030', agentId: 'ag1' } as never)
    await clock.settle()
    expect(world.store.has('binding:sess-A')).toBe(false)
    expect(await band($)).not.toContain('tm-audit-030')
  })

  test('/clear carries this session binding to the new id; /resume reads the resumed id binding', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    toolAnswers(on, { backlog_pick_task: 'Picked `tm-audit-030` — T' })
    await $.session.start(SESSION)
    await $.tool.call(call('backlog_pick_task', { task_id: 'tm-audit-030' }))
    await clock.settle()
    await $.classic.SessionStart({ source: 'clear', session_id: 'sess-B' } as never)
    await clock.settle()
    expect(world.store.get('binding:sess-B')).toEqual({ taskId: 'tm-audit-030', at: NOW })
    world.store.set('binding:sess-R', { taskId: 'unified-chat-022', at: NOW - DAY })
    await $.classic.SessionStart({ source: 'resume', session_id: 'sess-R' } as never)
    await clock.settle()
    const tree = await band($)
    expect(tree).toContain('unified-chat-022')
    expect(tree).toContain('waiting on you:')
  })

  test('another session binding is never shown; stale mirrors are pruned and fresh ones kept', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock, {
      'binding:sess-OTHER': { taskId: 'tm-audit-031', at: NOW - 3_600_000 },
      'binding:sess-OLD': { taskId: 'x-001', at: NOW - 31 * DAY },
    })
    world.mcp = backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.store.has('binding:sess-OTHER')).toBe(true)
    expect(world.store.has('binding:sess-OLD')).toBe(false)
    const tree = await band($)
    expect(tree).not.toContain('tm-audit-031')
    expect(tree).toContain('4 waiting on you')
  })

  test('a task id in the branch name shows dimmed as inferred, only when Taskmaster knows the task', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    world.branch = 'feat/tm-audit-030-fixes'
    await $.session.start(SESSION)
    await clock.settle()
    const tree = await band($)
    expect(tree).toContain('tm-audit-030')
    expect(tree).toContain('inferred')
  })

  test('an unknown task id in the branch name shows nothing', TM, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, clock)
    world.mcp = backlog
    world.branch = 'feat/zz-missing-999'
    await $.session.start(SESSION)
    await clock.settle()
    const tree = await band($)
    expect(tree).not.toContain('zz-missing-999')
    expect(tree).not.toContain('inferred')
  })
})
