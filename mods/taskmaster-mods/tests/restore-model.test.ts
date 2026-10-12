// User intent: prove the restore pane calls a session unfinished by Taskmaster's word (spec §10.2) — the scanner's JSON is
// read safely, one query resolves every id it found, and the status order is DONE > HANDED OFF > INTERRUPTED > WIP.
import { describe, expect, test } from 'claude-code/testing'

import { NO_TASK } from '../hooks/phases'
import { classify, parseRefs, phaseCounts, refsSql, rowOf, scanOf } from '../hooks/restore/model'
import { NO_REFS, refsReply, ROOT, scanOf as scanWith, session } from './fixtures/restore'

const REFS = {
  tasks: {
    'a-1': { id: 'a-1', status: 'done', lane: 'full', phase: 'p1', title: 'Old work' },
    'b-2': { id: 'b-2', status: 'in-progress', lane: 'full', phase: 'p2', title: 'Current work' },
    'c-3': { id: 'c-3', status: 'todo', lane: '', phase: '', title: 'Phaseless' },
  },
  handovers: { 'h-open': 'open', 'h-closed': 'closed', 'h-old': 'superseded' },
}
const handover = (id: string) => ({ id, path: `${ROOT}\\.taskmaster\\handovers\\${id}.md`, tldr: `tldr ${id}`, next: `next ${id}` })

describe('reading the scanner', () => {
  test('a session missing fields takes empty values; a non-session or a non-scan is dropped', () => {
    const scan = scanOf({ scannedAt: 5, root: ROOT, sessions: [{ id: 'x', taskIds: ['a-1', 3, ''], handovers: [{ id: 'h' }, {}] }, { title: 'no id' }, 7] })
    expect(scan?.sessions).toHaveLength(1)
    expect(scan?.sessions[0]).toMatchObject({ id: 'x', title: 'x', cwd: '', ctx: 0, running: false, taskIds: ['a-1'] })
    expect(scan?.sessions[0]?.handovers).toEqual([{ id: 'h', path: '', tldr: '', next: '' }])
    expect(scanOf({ sessions: 'no' })).toBeNull()
    expect(scanOf(null)).toBeNull()
  })
})

describe('the one backlog_query', () => {
  test('asks for every task and handover id once, and only ids that look like ids', () => {
    const scan = scanWith([
      session('s1', { taskIds: ['a-1', 'b-2'], handovers: [handover('h-open')] }),
      session('s2', { taskIds: ["b-2", "x'); DROP TABLE entities; --"], handovers: [handover('h-closed')] }),
    ])
    expect(refsSql(scan)).toBe(
      "SELECT json_array('t', e.id, e.status, coalesce(json_extract(e.doc,'$.lane'),''), coalesce(json_extract(e.doc,'$.phase'),'')) AS ref, json_extract(e.doc,'$.title') AS title FROM entities e WHERE e.kind='task' AND e.deleted=0 AND e.id IN ('a-1','b-2')" +
        " UNION ALL SELECT json_array('h', e.id, e.status) AS ref, '' AS title FROM entities e WHERE e.kind='handover' AND e.deleted=0 AND e.id IN ('h-open','h-closed')",
    )
    expect(refsSql(scanWith([session('s3', { handovers: [handover('h-open')] })]))).toMatch(/^SELECT json_array\('h'/)
    expect(refsSql(scanWith([session('s4')]))).toBeNull()
  })

  test('its table reads into tasks and handover statuses; a refusal or a cut table does not read', () => {
    const reply = refsReply([
      '["t","b-2","in-progress","full","p2"]          Current work',
      '["t","c-3","todo","",""]                       Phaseless',
      '["h","h-open","open"]',
      '["t","broken"',
    ])
    expect(parseRefs(reply)).toEqual({
      ok: true,
      value: {
        tasks: {
          'b-2': { id: 'b-2', status: 'in-progress', lane: 'full', phase: 'p2', title: 'Current work' },
          'c-3': { id: 'c-3', status: 'todo', lane: '', phase: '', title: 'Phaseless' },
        },
        handovers: { 'h-open': 'open' },
      },
    })
    expect(parseRefs('Error: forbidden keyword DROP: this tool is read-only\n\nSchema: …')).toEqual({
      ok: false,
      reason: 'backlog_query: Error: forbidden keyword DROP: this tool is read-only',
    })
    expect(parseRefs('ref  title\n["h","h-open","open"]').ok).toBe(false)
  })
})

describe('classification (spec §10.2, first match wins)', () => {
  const cases: readonly [string, Parameters<typeof session>[1], string, string][] = [
    ['end-session with nothing after wins over an open handover', { endedSession: true, handovers: [handover('h-open')], interrupted: true }, 'done', 'ran end-session'],
    ['the bound task is done', { taskIds: ['b-2', 'a-1'] }, 'done', 'a-1 is done'],
    ['an earlier task being done does not count', { taskIds: ['a-1', 'b-2'] }, 'wip', 'no end-session and no open handover'],
    ['its last handover was closed', { handovers: [handover('h-open'), handover('h-closed')] }, 'done', 'handover h-closed is closed'],
    ['its last handover was superseded', { handovers: [handover('h-old')] }, 'done', 'handover h-old is superseded'],
    ['an open handover wins over an interrupt', { handovers: [handover('h-open')], interrupted: true }, 'handed-off', 'left handover h-open open'],
    ['closed mid-turn', { interrupted: true, handovers: [handover('h-gone')] }, 'interrupted', 'closed mid-turn: the agent was still working'],
    ['kept working after end-session', { endedSession: true, promptsAfterEnd: 2 }, 'wip', 'kept working after end-session'],
    ['nothing said', {}, 'wip', 'no end-session and no open handover'],
  ]
  for (const [name, over, status, why] of cases) {
    test(name, () => {
      expect(classify(session('s', over), REFS)).toEqual({ status, why })
    })
  }

  test('without tm only end-session can be told; the rest read ?', () => {
    expect(classify(session('s', { endedSession: true }), null).status).toBe('done')
    expect(classify(session('s', { interrupted: true }), null)).toEqual({ status: 'unknown', why: 'Taskmaster not reached: status unknown' })
  })
})

describe('rows and phase counts', () => {
  test('a row carries its bound task, phase and last handover status', () => {
    expect(rowOf(session('s', { taskIds: ['a-1', 'b-2'], handovers: [handover('h-open')] }), REFS)).toMatchObject({
      status: 'handed-off',
      boundId: 'b-2',
      task: { id: 'b-2', phase: 'p2' },
      phase: 'p2',
      handover: { id: 'h-open' },
      handoverStatus: 'open',
    })
    expect(rowOf(session('s', { taskIds: ['c-3'] }), REFS).phase).toBe(NO_TASK)
    expect(rowOf(session('s', { taskIds: ['zz-9'] }), REFS)).toMatchObject({ boundId: 'zz-9', task: null, phase: NO_TASK })
    expect(rowOf(session('s', { taskIds: ['b-2'], handovers: [handover('h-open')] }), null)).toMatchObject({ task: null, phase: NO_TASK, handoverStatus: '' })
  })

  test('phases count the closed sessions by bound task; running ones and other ids count nowhere', () => {
    const rows = [
      session('s1', { taskIds: ['b-2'] }),
      session('s2', { taskIds: ['b-2'] }),
      session('s3', { taskIds: ['b-2'], running: true }),
      session('s4', { taskIds: ['c-3'] }),
      session('s5'),
    ].map(s => rowOf(s, REFS))
    expect([...phaseCounts(rows)]).toEqual([
      ['p2', 2],
      [NO_TASK, 2],
    ])
    expect(phaseCounts(rows.map(r => rowOf(r.s, NO_REFS))).get(NO_TASK)).toBe(4)
  })
})
