// User intent: the two writes as a person triggers them — y signs off with exactly the call Taskmaster expects, a refusal
// stays on the card, back-to-agent makes its ordered calls and stops at the first refusal without losing the note, and
// neither a refresh nor a double press can sign off the wrong task or sign off twice.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import { SIGN_OFF, tmActions } from '../hooks/actions'
import { createFlows, type TmWriter } from '../hooks/flows'
import type { TmHost } from '../hooks/host'
import { FRESH_CURSOR } from '../hooks/model'
import type { TmBandMode, TmCursor } from '../types'
import { SCRATCH } from './fixtures/captured/scratch'
import { BAND, command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { type McpAnswer, type World, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const READS = /^backlog_(list_tasks|issue_list|continuity_items|handover_list|get_task|task_pipeline)$/
const writes = (world: World) => world.calls.filter(c => !READS.test(c.tool))
type Over = Record<string, (args: Record<string, unknown>) => McpAnswer | string>
const router = (over: Over) => (tool: string, args: Record<string, unknown>): McpAnswer => {
  const answer = over[tool]
  if (answer === undefined) return R.backlog(tool, args)
  const given = answer(args)
  return typeof given === 'string' ? { text: given } : given
}
/** Every Text drawn, joined: a refusal wraps over several Text rows. */
const shown = async (ui: { drawn: () => Promise<unknown> }): Promise<string> =>
  elementsOf(await ui.drawn())
    .filter(e => e.type === 'Text')
    .map(e => (e.children ?? []).join(''))
    .join(' ')
const ID = 'unified-chat-022'
const SIGNED = { tool: 'backlog_complete_task', args: { task_id: ID, done: SIGN_OFF } }
const STATUS = { tool: 'backlog_update_task', args: { task_id: ID, field: 'status', value: 'in-progress' } }
const NOTE = (note: string) => ({ tool: 'backlog_update_task', args: { task_id: ID, next_step: `Back from review: ${note}` } })
const CLEAR = { tool: 'backlog_update_task', args: { task_id: ID, field: 'human_action', value: '' } }
/** Each back-to-agent step answered as the server words a success. */
const backOk = (args: Record<string, unknown>): string =>
  args.next_step !== undefined ? R.KEYWORD_UPDATED : R.updated(String(args.task_id), String(args.field), String(args.value))

async function openQueue($: Engine, on: On, over: Over) {
  const clock = mock.clock(on)
  const world = worldOf(on, clock)
  world.mcp = router(over)
  await $.session.start(SESSION)
  await clock.settle()
  await $.command.run(command('tm-review'))
  await clock.settle()
  const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
  await clock.settle()
  return { clock, world, ui }
}

async function boundBand($: Engine, on: On, over: Over, store: Record<string, unknown> = { 'binding:sess-A': { taskId: ID, at: 0 } }) {
  const clock = mock.clock(on)
  const world = worldOf(on, clock, store)
  world.mcp = router(over)
  return { clock, world }
}

const REORDERED = [
  '**2 tasks:**',
  '- `tm-audit-031` — Handover auto-supersede (keeps newest) (critical, tm-audit, in-review)',
  '    waiting-on-human: Open the viewer.',
  '- `unified-chat-022` — Unified chat pre-build (high, unified-chat, in-review)',
  '    waiting-on-human: Live check on dev.',
].join('\n')

describe('sign off', () => {
  test('d then y sends backlog_complete_task(task_id, done) once and the next card shows', TM, async ($, on) => {
    const { world, ui, clock } = await openQueue($, on, { backlog_complete_task: () => R.COMPLETED })
    expect(await ui.find({ type: 'Text', text: ID })).toBeDefined()
    await ui.press({ key: 'done' })
    expect(writes(world)).toEqual([])
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(writes(world)).toEqual([SIGNED])
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /1 done this pass/ })).toBeDefined()
  })

  test('a gate refusal stays on the card as a ◆ signal with the server text, and nothing is counted done', TM, async ($, on) => {
    const { ui, clock } = await openQueue($, on, { backlog_complete_task: () => R.GATE_REFUSAL })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates for lane/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ID })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /0 done this pass/ })).toBeDefined()
  })

  test('"(not persisted)" is a refusal, not a sign-off', TM, async ($, on) => {
    const { ui, clock } = await openQueue($, on, { backlog_complete_task: () => R.COMPLETED_NOT_PERSISTED })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(await shown(ui)).toMatch(/Taskmaster did not save it: Completed `unified-chat-022` — \(not persisted\)/)
    expect(await ui.find({ type: 'Text', text: /0 done this pass/ })).toBeDefined()
  })

  test('an isError reply is shown verbatim as the refusal', TM, async ($, on) => {
    const { ui, clock } = await openQueue($, on, { backlog_complete_task: () => ({ text: 'store is locked by another writer', isError: true }) })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(await shown(ui)).toMatch(/store is locked by another writer/)
    expect(await ui.find({ type: 'Text', text: /0 done this pass/ })).toBeDefined()
  })

  test('a refresh that reorders the queue between d and y cannot retarget y', TM, async ($, on) => {
    const list = { text: R.LIST_IN_REVIEW }
    const { world, ui, clock } = await openQueue($, on, { backlog_list_tasks: () => list.text, backlog_complete_task: () => R.COMPLETED })
    await ui.press({ key: 'done' })
    list.text = REORDERED
    await $.turn.complete({ answer: '', reason: 'end_turn' } as never)
    await clock.settle()
    expect(await ui.find({ type: 'Button', key: 'confirm-yes' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ID })).toBeDefined()
    await ui.press({ key: 'confirm-yes' })
    await clock.settle()
    expect(writes(world)).toEqual([SIGNED])
  })

  test('a write may take longer than a read: 5 s still signs off; no answer in 15 s says unreachable and may still land', TM, async ($, on) => {
    const answer = { hangMs: 5000 }
    const { world, ui, clock } = await openQueue($, on, { backlog_complete_task: () => ({ ...answer, text: R.COMPLETED }) })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.advance(5000)
    await clock.settle()
    expect(await ui.find({ type: 'Text', text: /1 done this pass/ })).toBeDefined()
    answer.hangMs = 60_000
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    await clock.advance(15_000)
    await clock.settle()
    expect(await shown(ui)).toMatch(/Taskmaster unreachable: backlog_complete_task: no reply within 15 s — it may still have been saved/)
    expect(writes(world)).toHaveLength(2)
  })

  test('the band signs off the bound in-review task with the same call and lets the binding go', TM, async ($, on) => {
    const { clock, world } = await boundBand($, on, { backlog_complete_task: () => R.COMPLETED })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface: 'terminal' })
    await ui.press({ key: 'band-done' })
    await ui.press({ key: 'band-yes' })
    await clock.settle()
    expect(writes(world)).toEqual([SIGNED])
    expect(world.store.has('binding:sess-A')).toBe(false)
  })

  test('the band never signs off a task this session is not bound to (an inferred one): it refuses on that task, writes nothing', TM, async ($, on) => {
    const { clock, world } = await boundBand($, on, { backlog_complete_task: () => R.COMPLETED }, {})
    world.branch = `feat/${ID}`
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface: 'terminal' })
    expect(await ui.find({ type: 'Text', text: 'inferred' })).toBeDefined()
    await ui.press({ key: 'band-done' })
    await clock.settle()
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeDefined()
    expect(await shown(ui)).toMatch(/not this session's task/)
    await ui.press({ key: 'band-back' })
    await clock.settle()
    expect(world.opened).toEqual([])
    expect(writes(world)).toEqual([])
  })
})

describe('back to agent', () => {
  test('status, the note as next_step, then the cleared human_action, in that order; the pane closes and the prompt holds the note', TM, async ($, on) => {
    const { world, ui, clock } = await openQueue($, on, { backlog_update_task: backOk })
    await ui.press({ key: 'back' })
    await ui.input({ key: 'note', text: 'tighten the copy' })
    await clock.settle()
    expect(writes(world)).toEqual([STATUS, NOTE('tighten the copy'), CLEAR])
    expect(world.closed).toEqual(['tm-review'])
    expect(world.fills).toEqual([`Back to ${ID}: tighten the copy`])
  })

  test('a blank note writes no next_step', TM, async ($, on) => {
    const { world, ui, clock } = await openQueue($, on, { backlog_update_task: backOk })
    await ui.press({ key: 'back' })
    await ui.input({ key: 'note', text: '   ' })
    await clock.settle()
    expect(writes(world)).toEqual([STATUS, CLEAR])
    expect(world.fills).toEqual([`Back to ${ID}`])
  })

  test('clearing a human_action that was already empty answers "(not persisted)", and that is a success', TM, async ($, on) => {
    const again = SCRATCH.replies.back_clear_again.text
    const { world, ui, clock } = await openQueue($, on, { backlog_update_task: args => (args.field === 'human_action' ? again : backOk(args)) })
    await ui.press({ key: 'back' })
    await ui.input({ key: 'note', text: 'x' })
    await clock.settle()
    expect(writes(world)).toHaveLength(3)
    expect(world.closed).toEqual(['tm-review'])
  })

  test('it stops at the first refusal, names the step, keeps the pane open, and the prompt still holds the note', TM, async ($, on) => {
    const illegal = 'Error: `unified-chat-022`: illegal transition `in-review` → `in-progress`. Legal: done'
    const { world, ui, clock } = await openQueue($, on, { backlog_update_task: () => illegal })
    await ui.press({ key: 'back' })
    await ui.input({ key: 'note', text: 'tighten the copy' })
    await clock.settle()
    expect(writes(world)).toEqual([STATUS])
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeDefined()
    expect(await shown(ui)).toMatch(/status: Error: `unified-chat-022`: illegal transition/)
    expect(world.closed).toEqual([])
    expect(world.fills).toEqual([`Back to ${ID}: tighten the copy`])
  })

  test('a clear that does not persist after the note was recorded names human_action; the note is in the prompt', TM, async ($, on) => {
    const { world, ui, clock } = await openQueue($, on, {
      backlog_update_task: args => (args.field === 'human_action' ? 'Error: store is read-only' : backOk(args)),
    })
    await ui.press({ key: 'back' })
    await ui.input({ key: 'note', text: 'tighten the copy' })
    await clock.settle()
    expect(writes(world)).toEqual([STATUS, NOTE('tighten the copy'), CLEAR])
    expect(await shown(ui)).toMatch(/human_action: Error: store is read-only/)
    expect(world.closed).toEqual([])
    expect(world.fills).toEqual([`Back to ${ID}: tighten the copy`])
  })

  test('band a opens the note on the bound task, and the note goes back on that task', TM, async ($, on) => {
    const { clock, world } = await boundBand($, on, { backlog_update_task: backOk })
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface: 'terminal' })
    await band.press({ key: 'band-back' })
    await clock.settle()
    expect(world.opened).toEqual(['tm-review'])
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    await review.input({ key: 'note', text: 'tighten the copy' })
    await clock.settle()
    expect(writes(world)).toEqual([STATUS, NOTE('tighten the copy'), CLEAR])
  })
})

describe('tmActions and flows, directly', () => {
  test('a status that did not persist is a refusal; a thrown error says unreachable', async () => {
    const lost = tmActions(async () => ({ text: R.NOT_PERSISTED, isError: false }))
    expect(await lost.backToAgent('x-001', '')).toEqual({ ok: false, refusal: `status: Taskmaster did not save it: ${R.NOT_PERSISTED}` })
    const down = tmActions(async () => {
      throw new Error('backlog_complete_task: tm: Connection closed')
    })
    expect(await down.done('x-001')).toEqual({ ok: false, refusal: 'Taskmaster unreachable: backlog_complete_task: tm: Connection closed' })
  })

  test('No change to … reads as success; a warning paragraph after the receipt is ignored', async () => {
    const replies = [R.WITH_WARNING, R.KEYWORD_UPDATED, R.NO_CHANGE]
    const calm = tmActions(async () => ({ text: replies.shift() ?? '', isError: false }))
    expect(await calm.backToAgent('tm-audit-031', 'note')).toEqual({ ok: true })
  })

  test('a second y, while the first write is in flight or after it landed, sends nothing (pane and band)', async () => {
    let calls = 0
    let release: () => void = () => undefined
    let cursor: TmCursor = { ...FRESH_CURSOR, mode: 'confirm', currentId: 'a-001' }
    let band: TmBandMode = { confirmingId: 'a-001', refusal: '' }
    const quiet = new Proxy({}, { get: () => async () => true }) as unknown as TmHost
    const held = {
      cursor: async (change: (c: TmCursor) => TmCursor) => {
        cursor = change(cursor)
      },
      band: async (change: (b: TmBandMode) => TmBandMode) => {
        band = change(band)
      },
    }
    const write = new Proxy(held, {
      get: (target, key) => (key in target ? target[key as keyof typeof held] : async () => undefined),
    }) as unknown as TmWriter
    const flows = createFlows({
      host: quiet,
      write,
      actions: {
        done: async () => {
          calls += 1
          await new Promise<void>(resolve => {
            release = resolve
          })
          return { ok: true }
        },
        backToAgent: async () => ({ ok: true }),
      },
      boundId: async () => 'a-001',
      afterWrite: () => undefined,
    })
    const settle = async () => {
      for (let i = 0; i < 20; i += 1) await Promise.resolve()
    }
    const first = flows.confirmDone('a-001')
    const second = flows.confirmDone('a-001')
    const fromBand = flows.bandConfirmDone('a-001')
    await settle()
    release()
    await Promise.all([first, second, fromBand])
    expect(calls).toBe(1)
    expect(cursor.done).toEqual(['a-001'])
    await flows.confirmDone('a-001')
    expect(calls).toBe(1)
    // The band's confirm on the same task went with the pane's sign-off: its y writes nothing either.
    expect(band.confirmingId).toBe('')
    await flows.bandConfirmDone('a-001')
    expect(calls).toBe(1)
  })
})
