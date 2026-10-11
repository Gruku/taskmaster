// User intent: the band and the Session tab always name the handover that matters to this session (spec D5) — the one it
// wrote, else the one it was resumed from (recorded by a resume launch or a "Resume from handover" first prompt), else the
// newest open one for its task — and a handover the open list no longer holds is still found by id.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { resumePrompt } from '../hooks/launch'
import { sessionHandoverOf, withNotice } from '../hooks/model'
import { parseHandoverSlim } from '../hooks/parse'
import type { TmHandover, TmHandoverNotice, TmSessionHandover, TmSnapshot } from '../types'
import { PLUGIN, SESSION } from './fixtures/inputs'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const T = 'mcp__plugin_taskmaster_tm__'
const BOUND = { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } }
const AUDIT = '2026-10-04-audit-fixes-landed'
const SHIPPED = '2026-10-05-shipped-unified-chat-022'
const OLD = '2026-09-20-closed-one'
const SLIM_OLD = [
  `## Handover: ${OLD}`,
  '',
  '**tldr:** Store migration verified, rows match',
  '**next_action:** Sign off tm-audit-031',
  "**task_ids:** ['tm-audit-031', 'tm-audit-029']",
  '**status:** closed',
  '**created:** 2026-09-20T16:31:00',
].join('\n')
const SH = `${PLUGIN}.sessionHandover`

const ho = (id: string, created: string, taskIds: readonly string[], nextAction = ''): TmHandover => ({
  id,
  created,
  tldr: `tldr of ${id}`,
  nextAction,
  path: `/p/${id}.md`,
  branch: '',
  taskIds,
  thread: '',
})
const snap = (handovers: readonly TmHandover[], boundId: string | null): TmSnapshot => ({
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [],
  queueTotal: 0,
  handovers,
  handoversTotal: handovers.length,
  bound: boundId === null ? null : { taskId: boundId, inferred: false, detail: null, pipeline: null },
})
const NOTICE: TmHandoverNotice = { id: 'h-new', tldr: 'Wrote it here', path: '/p/h-new.md', text: 'block' }

function tmWorld(on: On, store: Record<string, unknown> = BOUND) {
  const clock = mock.clock(on)
  const world = worldOf(on, clock, store)
  world.mcp = (tool, args) => (tool === 'backlog_handover_get' ? { text: args.handover_id === OLD ? SLIM_OLD : `Handover not found: ${String(args.handover_id)}` } : R.backlog(tool, args))
  on('prompt.submit', ($, e) => ({ text: e.text }))
  return { clock, world, state: stateOf(on) }
}

describe('D5, purely', () => {
  const list = [ho('h-2', '2026-10-05T09:00', ['t-1'], 'next 2'), ho('h-1', '2026-10-04T09:00', ['t-1', 't-2'])]

  test('written here beats resumed from, which beats the newest open handover of the bound task', () => {
    const s = snap(list, 't-2')
    expect(sessionHandoverOf({ notice: NOTICE, resumedId: 'h-2', resumed: null, snapshot: s })).toEqual({
      id: 'h-new',
      tag: 'written here',
      tldr: 'Wrote it here',
      path: '/p/h-new.md',
      created: '',
    })
    expect(sessionHandoverOf({ notice: null, resumedId: 'h-2', resumed: null, snapshot: s })).toEqual({
      id: 'h-2',
      tag: 'resumed from',
      tldr: 'tldr of h-2',
      path: '/p/h-2.md',
      created: '2026-10-05T09:00',
      next: 'next 2',
    })
    expect(sessionHandoverOf({ notice: null, resumedId: '', resumed: null, snapshot: s })?.id).toBe('h-1')
    expect(sessionHandoverOf({ notice: null, resumedId: '', resumed: null, snapshot: snap(list, 't-1') })?.id).toBe('h-2')
  })

  test('a resumed-from handover outside the list comes from its read; unknown, it falls through to the task; nothing is null', () => {
    const away = ho('h-9', '2026-09-01T10:00', [])
    expect(sessionHandoverOf({ notice: null, resumedId: 'h-9', resumed: away, snapshot: snap(list, 't-1') })?.tag).toBe('resumed from')
    expect(sessionHandoverOf({ notice: null, resumedId: 'h-9', resumed: null, snapshot: snap(list, 't-1') })).toMatchObject({ id: 'h-2', tag: 'task' })
    expect(sessionHandoverOf({ notice: null, resumedId: '', resumed: null, snapshot: snap(list, null) })).toBeNull()
    expect(sessionHandoverOf({ notice: null, resumedId: '', resumed: null, snapshot: null })).toBeNull()
  })

  test('a drawing lets a newer notice win over what was resolved', () => {
    const resolved: TmSessionHandover = { id: 'h-2', tag: 'task', tldr: 't', path: '/p', created: '' }
    expect(withNotice(resolved, null, null)).toBe(resolved)
    expect(withNotice(resolved, NOTICE, snap(list, null))).toMatchObject({ id: 'h-new', tag: 'written here' })
    const written: TmSessionHandover = { id: 'h-new', tag: 'written here', tldr: 'Wrote it here', path: '/p/h-new.md', created: '' }
    expect(withNotice(written, NOTICE, null)).toBe(written)
  })

  test("the slim backlog_handover_get reply reads into a handover; not found is null; anything else is unreadable", () => {
    const read = parseHandoverSlim(SLIM_OLD, 'C:\\work\\proj')
    expect(read).toEqual({
      ok: true,
      value: {
        id: OLD,
        created: '2026-09-20T16:31:00',
        tldr: 'Store migration verified, rows match',
        nextAction: 'Sign off tm-audit-031',
        path: `C:\\work\\proj\\.taskmaster\\handovers\\${OLD}.md`,
        branch: '',
        taskIds: ['tm-audit-031', 'tm-audit-029'],
        thread: '',
      },
    })
    expect(parseHandoverSlim('Handover not found: x', '/r')).toEqual({ ok: true, value: null })
    expect(parseHandoverSlim('No backlog found.', '/r').ok).toBe(false)
  })
})

describe('D5, live', () => {
  test('with no record the band handover is the newest open one for the bound task', TM, async ($, on) => {
    const { clock, state } = tmWorld(on)
    await $.session.start(SESSION)
    await clock.settle()
    expect(state.get(SH)).toMatchObject({ id: AUDIT, tag: 'task', next: 'Record the merge for tm-audit-030' })
  })

  test('a resumed-from record from a --resume launch is read at start; listed, it needs no extra read', TM, async ($, on) => {
    const { clock, world, state } = tmWorld(on, { ...BOUND, 'resumed-from:sess-A': SHIPPED })
    await $.session.start(SESSION)
    await clock.settle()
    expect(state.get(`${PLUGIN}.resumedFrom`)).toBe(SHIPPED)
    expect(state.get(SH)).toMatchObject({ id: SHIPPED, tag: 'resumed from' })
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get')).toEqual([])
  })

  test('a resumed-from handover the open list does not hold is read once by id, then shown', TM, async ($, on) => {
    const { clock, world, state } = tmWorld(on, { ...BOUND, 'resumed-from:sess-A': OLD })
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get').map(c => c.args)).toEqual([{ handover_id: OLD }])
    expect(state.get(SH)).toMatchObject({ id: OLD, tag: 'resumed from', tldr: 'Store migration verified, rows match' })
    await $.turn.complete({ answer: '', reason: 'end_turn' } as never)
    await clock.settle()
    expect(world.calls.filter(c => c.tool === 'backlog_handover_get')).toHaveLength(1)
  })

  test('a first prompt that resumes from a handover records it for this session and shows it', TM, async ($, on) => {
    const { clock, world, state } = tmWorld(on)
    await $.session.start(SESSION)
    await clock.settle()
    await $.prompt.submit({ text: resumePrompt(SHIPPED, `C:\\work\\proj\\.taskmaster\\handovers\\${SHIPPED}.md`), origin: { kind: 'composer' }, wait: false } as never)
    await clock.settle()
    expect(world.store.get('resumed-from:sess-A')).toBe(SHIPPED)
    expect(state.get(SH)).toMatchObject({ id: SHIPPED, tag: 'resumed from' })
    await $.prompt.submit({ text: 'carry on', origin: { kind: 'composer' }, wait: false } as never)
    expect(world.store.get('resumed-from:sess-A')).toBe(SHIPPED)
  })

  test('a handover written here wins over the record; /resume reads the resumed id\'s record', TM, async ($, on) => {
    const { clock, world, state } = tmWorld(on, { ...BOUND, 'resumed-from:sess-A': SHIPPED, 'resumed-from:sess-R': AUDIT })
    on('tool.call', ($, e) => ({ result: { content: [{ type: 'text', text: R.HANDOVER_WRITTEN }] }, text: R.HANDOVER_WRITTEN }) as never)
    await $.session.start(SESSION)
    await clock.settle()
    await $.tool.call({ tool: `${T}backlog_handover_create`, tldr: 'Live data wired into the band', next_action: 'Run it' } as never)
    await clock.settle()
    expect(state.get(SH)).toMatchObject({ id: '2026-10-06-live-data-wired', tag: 'written here', tldr: 'Live data wired into the band' })
    world.sessionId = 'sess-R'
    await $.classic.SessionStart({ source: 'resume', session_id: 'sess-R' } as never)
    await clock.settle()
    expect(state.get(`${PLUGIN}.resumedFrom`)).toBe(AUDIT)
  })
})
