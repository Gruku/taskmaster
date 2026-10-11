// User intent: the phase strip's data contract per pane (spec §5) — one backlog_query a pane, the exact SQL, 6-cell rows
// with closed and date read back, the handovers' no task row read on its own — tested against the server's table shape.
import { describe, expect, test } from 'claude-code/testing'

import type { TmHost } from '../hooks/host'
import { parsePhases } from '../hooks/parse'
import { NO_TASK } from '../hooks/phases'
import { NO_TASK_SQL, phaseSql, readPhases } from '../hooks/tm'
import { NO_TASK_ROW, PHASES_DATED, PHASES_WAITING } from './fixtures/phases'

const DATE =
  "CASE WHEN p.status IN ('done','archived') THEN substr(coalesce(json_extract(p.doc,'$.completed'),''),1,10) ELSE substr(coalesce(json_extract(p.doc,'$.start_date'),''),1,10) END"
const TAIL = " AS phase, json_extract(p.doc,'$.name') AS name FROM entities p WHERE p.kind='phase' AND p.deleted=0 ORDER BY json_extract(p.doc,'$.order') DESC"

/** A TmHost that answers backlog_query from `answer` and keeps what it was asked and logged; nothing else is used. */
function hostOf(answer: (sql: string) => { text: string; isError?: boolean } | 'throw'): { host: TmHost; asked: string[]; logs: string[] } {
  const asked: string[] = []
  const logs: string[] = []
  const host = {
    sleep: (_ms: number, signal?: AbortSignal) =>
      new Promise<void>((_resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('aborted')))),
    call: async (_tool: string, args: Record<string, unknown>) => {
      const sql = String(args.sql)
      asked.push(sql)
      const a = answer(sql)
      if (a === 'throw') throw new Error('tm: Connection closed')
      return { text: a.text, isError: a.isError === true }
    },
    log: (text: string) => void logs.push(text),
  } as unknown as TmHost
  return { host, asked, logs }
}

describe('phase queries per pane', () => {
  test('review counts its scope, with closed 0 and the date; restore counts nothing; handovers count linked open and closed', () => {
    expect(phaseSql('review', true)).toBe(
      `SELECT json_array(p.id, p.status, json_extract(p.doc,'$.order'), (SELECT count(*) FROM entities t WHERE t.kind='task' AND t.deleted=0 AND t.status='in-review' AND json_extract(t.doc,'$.phase')=p.id AND coalesce(json_extract(t.doc,'$.human_action'),'')<>''), 0, ${DATE})${TAIL}`,
    )
    expect(phaseSql('review', false)).not.toContain('human_action')
    expect(phaseSql('restore', true)).toBe(`SELECT json_array(p.id, p.status, json_extract(p.doc,'$.order'), 0, 0, ${DATE})${TAIL}`)
    const handovers = phaseSql('handovers', true)
    expect(handovers).toContain("FROM handover_tasks ht JOIN entities t ON t.kind='task' AND t.id=ht.task_id AND t.deleted=0")
    expect(handovers).toContain("ho.status='open' WHERE json_extract(t.doc,'$.phase')=p.id)")
    expect(handovers).toContain("ho.status='closed' WHERE json_extract(t.doc,'$.phase')=p.id)")
    expect(handovers.endsWith(TAIL)).toBe(true)
    expect(handovers).not.toContain('UNION')
    expect(NO_TASK_SQL).toContain(`json_array('${NO_TASK}', 'none', 0,`)
    expect(NO_TASK_SQL).toContain('NOT EXISTS (SELECT 1 FROM handover_tasks ht WHERE ht.handover_id=ho.id)')
  })

  test('6-cell rows read closed and date; the older 4-cell rows read without them', () => {
    const dated = parsePhases(PHASES_DATED)
    if (!dated.ok) throw new Error(dated.reason)
    expect(dated.value[0]).toEqual({ id: '2-0-4', status: 'active', order: 14, count: 249, closed: 0, date: '2026-10-01', name: 'Patch 2.0.4 — Alpha Scope & Polish' })
    expect(dated.value[1]).toMatchObject({ id: '2-0-3', closed: 2, date: '2026-09-28' })
    expect(dated.value[2]).toMatchObject({ id: '2-1-0', date: '' })
    const old = parsePhases(PHASES_WAITING)
    if (!old.ok) throw new Error(old.reason)
    expect('date' in (old.value[0] ?? {})).toBe(false)
    expect('closed' in (old.value[0] ?? {})).toBe(false)
  })
})

describe('server table quirks', () => {
  test('a no task reply ending in `1 rows` parses', () => {
    const parsed = parsePhases('phase                      name\n["~none","none",0,3,5,""]  no task\n1 rows')
    if (!parsed.ok) throw new Error(parsed.reason)
    expect(parsed.value).toHaveLength(1)
  })

  test('HTML entities in a name are decoded', () => {
    const parsed = parsePhases('phase  name\n["a","done",1,0,0,""]  Scope &amp; Polish &lt;x&gt; &quot;q&quot; &#39;s&#39;\n1 rows')
    if (!parsed.ok) throw new Error(parsed.reason)
    expect(parsed.value[0]?.name).toBe(`Scope & Polish <x> "q" 's'`)
  })
})

describe('readPhases', () => {
  test('review and restore ask once with their own query', async () => {
    for (const pane of ['review', 'restore'] as const) {
      const { host, asked } = hostOf(() => ({ text: PHASES_DATED }))
      expect((await readPhases(host, pane, true))?.map(p => p.id)).toEqual(['2-0-4', '2-0-3', '2-1-0'])
      expect(asked).toEqual([phaseSql(pane, true)])
    }
  })

  test('handovers read the phases, then the no task row, and append it', async () => {
    const { host, asked } = hostOf(sql => ({ text: sql === NO_TASK_SQL ? NO_TASK_ROW : PHASES_DATED }))
    const phases = await readPhases(host, 'handovers', true)
    expect(asked).toEqual([phaseSql('handovers', true), NO_TASK_SQL])
    expect(phases?.at(-1)).toEqual({ id: NO_TASK, status: 'none', order: 0, count: 3, closed: 5, date: '', name: 'no task' })
  })

  test('a refused or failing no task read keeps the phases without that tab, and logs', async () => {
    for (const fail of ['refused', 'throw'] as const) {
      const { host, logs } = hostOf(sql =>
        sql === NO_TASK_SQL ? (fail === 'throw' ? 'throw' : { text: 'Error: NOT EXISTS is not allowed' }) : { text: PHASES_DATED },
      )
      expect((await readPhases(host, 'handovers', true))?.map(p => p.id), fail).toEqual(['2-0-4', '2-0-3', '2-1-0'])
      expect(logs.length, fail).toBe(1)
    }
  })

  test('an unreadable or failing phase read is null and logs', async () => {
    for (const answer of [() => ({ text: 'not a table' }), () => 'throw' as const, () => ({ text: 'Error: no such table', isError: true })]) {
      const { host, logs } = hostOf(answer)
      expect(await readPhases(host, 'review', true)).toBeNull()
      expect(logs.length).toBe(1)
    }
  })
})
