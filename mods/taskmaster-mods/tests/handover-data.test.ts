// User intent: the handovers pane's own data (spec §9) — up to 100 open handovers past the 30-entry index, closed ones on
// demand, their linked tasks' live status and phase in one query, and the close write — read and written as the server words
// them, failing soft so the pane never loses what it already shows.
import { describe, expect, test } from 'claude-code/testing'

import { CLOSE_REASON, demoCloseHandover, tmCloseHandover } from '../hooks/actions'
import { DEMO_SEED, DEMO_TASKS, demoHandoverList, demoPhases, demoSnapshot } from '../hooks/demo'
import type { TmHost } from '../hooks/host'
import { parseHandovers, parseLinkedTasks } from '../hooks/parse'
import { NO_TASK } from '../hooks/phases'
import { HANDOVER_PANE_LIMIT, linkedTasksSql, readHandoverList, TmUnreachable } from '../hooks/tm'
import * as R from './fixtures/replies'

type Answer = { text: string; isError?: boolean } | 'throw'

/** A TmHost that answers from `answer` and keeps every call and log; nothing else is used. */
function hostOf(answer: (tool: string, args: Record<string, unknown>) => Answer): {
  host: TmHost
  calls: { tool: string; args: Record<string, unknown> }[]
  logs: string[]
} {
  const calls: { tool: string; args: Record<string, unknown> }[] = []
  const logs: string[] = []
  const host = {
    sleep: (_ms: number, signal?: AbortSignal) =>
      new Promise<void>((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('aborted')))),
    call: async (tool: string, args: Record<string, unknown>) => {
      calls.push({ tool, args })
      const a = answer(tool, args)
      if (a === 'throw') throw new Error('tm: Connection closed')
      return { text: a.text, isError: a.isError === true }
    },
    log: (text: string) => void logs.push(text),
  } as unknown as TmHost
  return { host, calls, logs }
}

const server = (tool: string, args: Record<string, unknown>): Answer => {
  if (tool === 'backlog_handover_list') return { text: args.status === 'closed' ? R.HANDOVERS_CLOSED : R.HANDOVERS_OPEN }
  if (tool === 'backlog_query') return { text: R.LINKED_TASKS }
  return { text: `Error: ${tool}` }
}
const listArgs = (status: string) => ({ format: 'json', status, limit: HANDOVER_PANE_LIMIT, include_archived: true })

describe('handover list data', () => {
  test('a listed handover carries its status; anything but closed reads as open', () => {
    const open = parseHandovers(R.HANDOVERS_OPEN, '/proj')
    const closed = parseHandovers(R.HANDOVERS_CLOSED, '/proj')
    if (!open.ok || !closed.ok) throw new Error('unreadable fixture')
    expect(open.value.handovers.map(h => h.status)).toEqual(['open', 'open'])
    expect(closed.value.handovers.map(h => [h.id, h.status])).toEqual([['2026-10-02-row-counter-ported', 'closed']])
  })

  test('the linked-task table reads id, status, phase and title; a row whose cell was cut is left out', () => {
    const cut = `${R.LINKED_TASKS.split('\n').slice(0, -1).join('\n')}\n["a-very-long-task-id-that-the-server-cut","in-review","pha  Cut\n4 rows`
    const parsed = parseLinkedTasks(cut)
    if (!parsed.ok) throw new Error(parsed.reason)
    expect(parsed.value).toEqual([
      { id: 'unified-chat-022', status: 'in-review', phase: '2-0-4', title: 'Unified chat pre-build gets the full supervisor toolset; cookbook owns the order' },
      { id: 'tm-audit-030', status: 'in-progress', phase: '2-0-6', title: 'Agent tool-use audit fixes' },
      { id: 'tm-audit-029', status: 'done', phase: '2-0-3', title: 'Port the row counter' },
    ])
    expect(parseLinkedTasks('Error: not authorized: handover_tasks').ok).toBe(false)
    expect(parseLinkedTasks('task  title\n["a","todo",""]  A').ok).toBe(false)
  })

  test('the linked-task query names each id once, quoted, and reads status and phase in one cell', () => {
    expect(linkedTasksSql(['a-001', "it's-002"])).toBe(
      "SELECT json_array(t.id, t.status, coalesce(json_extract(t.doc,'$.phase'),'')) AS task, json_extract(t.doc,'$.title') AS title FROM entities t WHERE t.kind='task' AND t.deleted=0 AND t.id IN ('a-001','it''s-002')",
    )
  })

  test('open only: one list read past the index, then one task query for the linked ids', async () => {
    const { host, calls } = hostOf(server)
    const list = await readHandoverList(host, '/proj', false)
    expect(calls.map(c => c.tool)).toEqual(['backlog_handover_list', 'backlog_query'])
    expect(calls[0]?.args).toEqual(listArgs('open'))
    expect(calls[1]?.args).toEqual({ sql: linkedTasksSql(['unified-chat-022', 'tm-audit-030']), limit: 500 })
    expect(list?.closed).toBe(false)
    expect(list?.total).toBe(7)
    expect(list?.handovers.map(h => h.id)).toEqual(['2026-10-05-shipped-unified-chat-022', '2026-10-04-audit-fixes-landed'])
    expect(list?.tasks['tm-audit-030']).toEqual({ id: 'tm-audit-030', status: 'in-progress', phase: '2-0-6', title: 'Agent tool-use audit fixes' })
  })

  test('with closed: the closed read follows, both merge newest first, and the total stays the open count', async () => {
    const { host, calls } = hostOf(server)
    const list = await readHandoverList(host, '/proj', true)
    expect(calls.map(c => c.args.status ?? 'query')).toEqual(['open', 'closed', 'query'])
    expect(calls[1]?.args).toEqual(listArgs('closed'))
    expect(list?.closed).toBe(true)
    expect(list?.total).toBe(7)
    expect(list?.handovers.map(h => h.status)).toEqual(['open', 'open', 'closed'])
    expect(Object.keys(list?.tasks ?? {}).sort()).toEqual(['tm-audit-029', 'tm-audit-030', 'unified-chat-022'])
  })

  test('no linked task asks no query', async () => {
    const { host, calls } = hostOf((tool, args) => (tool === 'backlog_handover_list' ? { text: R.handoversOpen(3) } : server(tool, args)))
    const list = await readHandoverList(host, '/proj', false)
    expect(calls.map(c => c.tool)).toEqual(['backlog_handover_list'])
    expect(list?.tasks).toEqual({})
  })

  test('a failing or unreadable list read is null and logs; a failing task read keeps the list with no tasks', async () => {
    for (const bad of [() => 'throw' as const, () => ({ text: 'not json' }), () => ({ text: '{"error":"No backlog found."}' })]) {
      const { host, logs } = hostOf((tool, args) => (tool === 'backlog_handover_list' ? bad() : server(tool, args)))
      expect(await readHandoverList(host, '/proj', false)).toBeNull()
      expect(logs).toHaveLength(1)
    }
    const { host, logs } = hostOf((tool, args) => (tool === 'backlog_query' ? { text: 'Error: no such table: handover_tasks' } : server(tool, args)))
    const list = await readHandoverList(host, '/proj', false)
    expect(list?.handovers).toHaveLength(2)
    expect(list?.tasks).toEqual({})
    expect(logs).toHaveLength(1)
  })
})

describe('closing a handover', () => {
  test('one update_status call to closed with the pane reason; the server success line is ok', async () => {
    const asked: { tool: string; args: Record<string, unknown> }[] = []
    const close = tmCloseHandover(async (tool, args) => {
      asked.push({ tool, args })
      return { text: `${R.handoverClosed(String(args.handover_id))} [seq 41]`, isError: false }
    })
    expect(await close('h-1')).toEqual({ ok: true })
    expect(asked).toEqual([{ tool: 'backlog_handover_update_status', args: { handover_id: 'h-1', status: 'closed', reason: CLOSE_REASON } }])
  })

  test('a refusal comes back as the server words it; a throw says unreachable; a timeout says it may have closed', async () => {
    expect(await tmCloseHandover(async () => ({ text: 'Handover not found: h-9', isError: false }))('h-9')).toEqual({ ok: false, refusal: 'Handover not found: h-9' })
    expect(await tmCloseHandover(async () => ({ text: 'Handover h-1 → status=closed (user-set).', isError: true }))('h-1')).toMatchObject({ ok: false })
    expect(
      await tmCloseHandover(async () => {
        throw new Error('tm: Connection closed')
      })('h-1'),
    ).toEqual({ ok: false, refusal: 'Taskmaster unreachable: tm: Connection closed' })
    const pending = Promise.resolve()
    const late = await tmCloseHandover(async () => {
      throw new TmUnreachable('backlog_handover_update_status: no reply within 15 s', true, pending)
    })('h-1')
    expect(late).toMatchObject({ ok: false, pending })
    expect(late.ok ? '' : late.refusal).toContain('it may still have been closed')
    expect(await demoCloseHandover()('anything')).toEqual({ ok: true })
  })
})

describe('demo handovers', () => {
  test('seed 7: the snapshot window plus two closed ones, every linked task known, and the strip counts agree', () => {
    // The handovers list arrived with seed 7; later seeds (8: restore) keep it.
    expect(DEMO_SEED).toBeGreaterThanOrEqual(7)
    const list = demoHandoverList(0)
    const open = demoSnapshot(0).handovers
    expect(list.handovers.filter(h => h.status === 'open').map(h => h.id)).toEqual(open.map(h => h.id))
    expect(list.handovers.filter(h => h.status === 'closed')).toHaveLength(2)
    expect(list.closed).toBe(true)
    for (const h of list.handovers) for (const id of h.taskIds) expect(DEMO_TASKS[id], id).toBeDefined()
    const count = (status: 'open' | 'closed', phase: string) =>
      list.handovers.filter(h => h.status === status && (phase === NO_TASK ? h.taskIds.length === 0 : h.taskIds.some(id => DEMO_TASKS[id]?.phase === phase))).length
    for (const p of demoPhases('handovers')) {
      expect(p.count, p.id).toBe(count('open', p.id))
      expect(p.closed, p.id).toBe(count('closed', p.id))
    }
  })
})
