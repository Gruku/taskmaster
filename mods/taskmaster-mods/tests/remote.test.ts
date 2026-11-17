// User intent: prove the mods read a team server the way team-server-007 D14 says: nothing changes and no process runs
// without a marker; with a marker or a latch one tm_read.py process carries the whole refresh; an interpreter that did not
// start is skipped but one that ran (slow, crashed) stands; each failure kind shows as the right fault; stale data draws
// with the offline fault; a write makes the next read fresh; the restore scanner keeps the same interpreter rule.
import { describe, expect, mock, test } from 'claude-code/testing'

import type { TmReply } from '../hooks/host'
import { parseHandovers } from '../hooks/parse'
import { didNotStart, PYTHONS, type RunOut } from '../hooks/python'
import { markerPaths, type RemoteDeps, RemoteFailure, remoteOf, SLOW_START_MS, TM_REMOTE_TIMEOUT_MS, type TmCall } from '../hooks/remote'
import { runScan } from '../hooks/restore/scan'
import { readMany } from '../hooks/tm'
import { pane, PLUGIN, SESSION, waitingOf } from './fixtures/inputs'
import { backlog, HANDOVERS_OPEN } from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const TM = { plugins: [RR_STUB] }
const ROOT = 'C:\\work\\proj'
const SCRIPT = 'C:\\tm\\hooks\\tm_read.py'
const MARKER = `${ROOT}/.taskmaster-server.json`
const LATCH = `${ROOT}/.git/taskmaster-server.json`
const CALLS: TmCall[] = [
  { tool: 'backlog_list_tasks', args: { status: 'in-review', limit: 50 } },
  { tool: 'backlog_query', args: { sql: 'SELECT 1' } },
]

type Step = RunOut | Error | { timeout: true }

/** A reader over a fake engine: `steps` answer the processes in order; the clock moves only when a step says so. */
function fakeOf(steps: Step[], files: readonly string[] = [MARKER, SCRIPT], elapsed = 0) {
  let clock = 0
  const runs: { argv: readonly string[]; stdin: string | undefined }[] = []
  const logs: string[] = []
  const deps: RemoteDeps = {
    exists: async path => files.includes(path),
    run: async (argv, timeoutMs, stdin) => {
      runs.push({ argv, stdin })
      const step = steps.shift() ?? new Error('no answer scripted')
      if ('timeout' in step) {
        clock += timeoutMs
        throw new Error('process timed out')
      }
      clock += elapsed
      if (step instanceof Error) throw step
      return step
    },
    now: async () => clock,
    log: text => void logs.push(text),
    script: SCRIPT,
  }
  return { remote: remoteOf(deps), runs, logs }
}

const json = (value: unknown): RunOut => ({ exitCode: 0, stdout: JSON.stringify(value), stderr: '' })
const answered = (texts: readonly string[], extra: Record<string, unknown> = {}): RunOut =>
  json({ v: 1, view: 'tools', revision: 3, data: { results: texts.map(text => ({ tool: 'x', text, is_error: false })) }, source: 'server', elapsed_ms: 50, ...extra })
const failed = (kind: string, message: string): RunOut => json({ error: { kind, message }, elapsed_ms: 40 })

async function failure(promise: Promise<unknown>): Promise<RemoteFailure> {
  try {
    await promise
  } catch (error) {
    if (error instanceof RemoteFailure) return error
    throw error
  }
  throw new Error('the read did not fail')
}

describe('detection', () => {
  test('no marker and no latch: no reader, and nothing runs', async () => {
    const { remote, runs } = fakeOf([], [SCRIPT])
    expect(await remote(ROOT)).toBeNull()
    expect(runs).toEqual([])
  })

  test('the marker, or the latch alone, makes the clone a server clone', async () => {
    expect(markerPaths(ROOT)).toEqual([MARKER, LATCH])
    expect(await fakeOf([], [MARKER]).remote(ROOT)).not.toBeNull()
    expect(await fakeOf([], [LATCH]).remote(ROOT)).not.toBeNull()
  })
})

describe('a batch read', () => {
  test('one process, isolated, with every call on stdin', async () => {
    const { remote, runs } = fakeOf([answered(['a', 'b'])])
    const read = await (await remote(ROOT))!.read(CALLS)
    expect(read).toEqual({ replies: [{ text: 'a', isError: false }, { text: 'b', isError: false }], stale: null })
    expect(runs).toHaveLength(1)
    expect(runs[0]!.argv).toEqual(['python', '-I', SCRIPT, 'tools', '--root', ROOT])
    expect(JSON.parse(runs[0]!.stdin ?? '')).toEqual({ calls: CALLS })
  })

  test('a write makes the next read --fresh, once', async () => {
    const { remote, runs } = fakeOf([answered(['a', 'b']), answered(['a', 'b']), answered(['a', 'b'])])
    const reader = (await remote(ROOT))!
    await reader.read(CALLS)
    reader.wrote()
    await reader.read(CALLS)
    await reader.read(CALLS)
    expect(runs.map(r => r.argv.includes('--fresh'))).toEqual([false, true, false])
  })

  test('a stale answer is data, with the reason it is stale', async () => {
    const { remote } = fakeOf([answered(['a', 'b'], { source: 'stale', age_s: 42.4 })])
    const read = await (await remote(ROOT))!.read(CALLS)
    expect(read.stale).toBe('team server unreachable; showing data read 42 s ago')
    expect(read.replies.map(r => r.text)).toEqual(['a', 'b'])
  })

  test('a reply that is not the batch is unreadable', async () => {
    const reader = async (out: RunOut) => (await fakeOf([out]).remote(ROOT))!.read(CALLS)
    expect((await failure(reader(answered(['only one'])))).fault).toBe('unreadable')
    expect((await failure(reader({ exitCode: 0, stdout: 'not json', stderr: '' })))).toMatchObject({ fault: 'unreadable', message: 'tm_read.py printed no JSON' })
  })

  test('without tm_read.py beside the mod the clone is offline, saying where it looked', async () => {
    const err = await failure((await fakeOf([], [MARKER]).remote(ROOT))!.read(CALLS))
    expect(err).toMatchObject({ fault: 'offline', message: `tm_read.py not found at ${SCRIPT}` })
  })
})

describe('the interpreter rule (review I3, N1)', () => {
  test('a timeout stands: exactly one process ran, and the failure is transient', async () => {
    const { remote, runs } = fakeOf([{ timeout: true }])
    const err = await failure((await remote(ROOT))!.read(CALLS))
    expect(runs).toHaveLength(1)
    expect(err.fault).toBe('transient')
    expect(err.message).toBe(`python: no answer within ${TM_REMOTE_TIMEOUT_MS / 1000} s (the team server did not answer)`)
  })

  test('not found (9009, 127), a quick rejection, a SyntaxError or the too-old exit: the next interpreter is tried', async () => {
    for (const first of [
      { exitCode: 9009, stdout: '', stderr: '' },
      { exitCode: 127, stdout: '', stderr: 'python: command not found' },
      new Error('spawn python ENOENT'),
      { exitCode: 1, stdout: '', stderr: '  File "tm_read.py", line 9\n    x = f"{y}"\nSyntaxError: invalid syntax\n' },
      { exitCode: 3, stdout: '', stderr: 'tm_read.py needs Python 3.9 or newer\n' },
    ]) {
      const { remote, runs } = fakeOf([first, answered(['a', 'b'])])
      expect((await (await remote(ROOT))!.read(CALLS)).replies).toHaveLength(2)
      expect(runs.map(r => r.argv[0])).toEqual(['python', 'python3'])
    }
  })

  test('a crash (exit 1) stands: no second process, and the reply is unreadable', async () => {
    const { remote, runs } = fakeOf([{ exitCode: 1, stdout: '', stderr: 'Traceback\nImportError: no module named taskmaster\n' }])
    const err = await failure((await remote(ROOT))!.read(CALLS))
    expect(runs).toHaveLength(1)
    expect(err).toMatchObject({ fault: 'unreadable', message: 'tm_read.py: ImportError: no module named taskmaster' })
  })

  test('no interpreter starts: offline, naming them', async () => {
    const enoent = new Error('spawn ENOENT')
    const { remote, runs } = fakeOf([enoent, enoent, { exitCode: 9009, stdout: '', stderr: '' }])
    const err = await failure((await remote(ROOT))!.read(CALLS))
    expect(runs.map(r => r.argv[0])).toEqual([...PYTHONS])
    expect(err).toMatchObject({ fault: 'offline', message: 'py exited 9009' })
  })

  test('didNotStart reads only the not-started exits', () => {
    expect(didNotStart({ exitCode: 9009, stdout: '', stderr: '' })).toBe(true)
    expect(didNotStart({ exitCode: 2, stdout: '', stderr: 'usage: ...' })).toBe(false)
    expect(didNotStart({ exitCode: 3, stdout: '', stderr: '' }, 3)).toBe(true)
    expect(didNotStart({ exitCode: 3, stdout: '', stderr: '' })).toBe(false)
  })

  test('a slow start is logged, and a later timeout is worded as one', async () => {
    const { remote, logs } = fakeOf([answered(['a', 'b']), { timeout: true }], [MARKER, SCRIPT], SLOW_START_MS + 2050)
    const reader = (await remote(ROOT))!
    await reader.read(CALLS)
    expect(logs).toEqual(['taskmaster-mods: slow Python start (5 s)'])
    expect((await failure(reader.read(CALLS))).message).toContain('(Python is slow to start)')
  })
})

describe('the reader kinds as faults (D14, review N2)', () => {
  const REFUSAL =
    'The server did not accept this key: it may have been revoked or rotated, or the server\'s key list was briefly unavailable. If the operator gave you a new key, run `taskmaster remote set-key`.'
  test("each kind is its fault, with the reader's own message", async () => {
    const kinds: [string, string][] = [
      ['unreachable', 'transient'],
      ['busy', 'transient'],
      ['server', 'transient'],
      ['refused', 'offline'],
      ['no-key', 'offline'],
      ['config', 'offline'],
      ['version', 'offline'],
      ['bad-request', 'offline'],
      ['bad-response', 'unreadable'],
    ]
    for (const [kind, fault] of kinds) {
      const message = kind === 'refused' ? REFUSAL : `the ${kind} message`
      const err = await failure((await fakeOf([failed(kind, message)]).remote(ROOT))!.read(CALLS))
      expect(err).toMatchObject({ fault, kind, message })
    }
  })

  test('readMany: transient and unreadable carry through; a refusal shows its text, not "re-enter your key"', async () => {
    const hostWith = (out: RunOut) => {
      const { remote } = fakeOf([out])
      return { call: async (): Promise<TmReply> => ({ text: 'mcp', isError: false }), sleep: async () => undefined, remote: () => remote(ROOT) }
    }
    const caught = async (out: RunOut) => {
      try {
        await readMany(hostWith(out), CALLS)
      } catch (error) {
        return error as { message: string; transient: boolean; unreadable: boolean }
      }
      throw new Error('no failure')
    }
    expect(await caught(failed('busy', 'rate limited; retry in 2 s'))).toMatchObject({ transient: true, unreadable: false })
    expect(await caught(failed('bad-response', 'bad envelope'))).toMatchObject({ transient: false, unreadable: true })
    const refused = await caught(failed('refused', 'The server did not accept this key: it may have been revoked'))
    expect(refused).toMatchObject({ transient: false, unreadable: false, message: 'The server did not accept this key: it may have been revoked' })
    expect(refused.message).not.toMatch(/re-enter/i)
  })
})

describe('in a session', () => {
  const serverAnswer = (argv: readonly string[], stdin: string | undefined) => {
    if (!argv.includes('tools')) return null
    const calls = (JSON.parse(stdin ?? '{}') as { calls: TmCall[] }).calls
    return { exitCode: 0, stdout: JSON.stringify({ v: 1, data: { results: calls.map(c => ({ tool: c.tool, text: backlog(c.tool, c.args).text, is_error: false })) }, source: 'server', elapsed_ms: 80 }) }
  }
  // The engine hands `$.fs.exists` a normalized path (backslashes on Windows, `..` resolved).
  const serverClone = (path: string) => /^C:[\\/]work[\\/]proj[\\/]\.taskmaster-server\.json$/.test(path) || /[\\/]hooks[\\/]tm_read\.py$/.test(path)

  test('solo: the refresh makes its MCP calls and no process runs', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = backlog
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.calls.map(c => c.tool)).toContain('backlog_list_tasks')
    expect(world.runs.filter(argv => argv.some(a => a.endsWith('tm_read.py')))).toEqual([])
  })

  test('a marker: one tm_read.py process carries the refresh, no MCP read is made, and the queue draws', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = backlog
    world.exists = serverClone
    world.answer = serverAnswer
    await $.session.start(SESSION)
    await clock.settle()
    const reads = world.runs.filter(argv => argv.includes('tools'))
    expect(reads).toHaveLength(1)
    expect(reads[0]!.slice(0, 2)).toEqual(['python', '-I'])
    expect(reads[0]!.slice(3)).toEqual(['tools', '--root', ROOT])
    const sent = (JSON.parse(world.stdins[world.runs.indexOf(reads[0]!)] ?? '{}') as { calls: TmCall[] }).calls.map(c => c.tool)
    expect(sent).toEqual(['backlog_list_tasks', 'backlog_issue_list', 'backlog_issue_list', 'backlog_continuity_items', 'backlog_continuity_items', 'backlog_handover_list'])
    expect(world.calls.filter(c => c.tool === 'backlog_list_tasks')).toEqual([])
    expect(world.statuses).not.toContain('◆ tm offline')
    expect(await waitingOf($)).toContain('waiting on you')
  })

  test('a stale answer draws the queue with the offline fault', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.exists = serverClone
    world.answer = (argv, stdin) => {
      const out = serverAnswer(argv, stdin)
      return out === null ? null : { ...out, stdout: out.stdout.replace('"source":"server"', '"source":"stale","age_s":30') }
    }
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses.at(-1)).toBe('◆ tm offline')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
  })

  test('a refused key faults at once with the reader\'s text and is not retried', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.exists = serverClone
    world.answer = argv =>
      argv.includes('tools') ? { exitCode: 0, stdout: JSON.stringify({ error: { kind: 'refused', message: 'The server did not accept this key' } }) } : null
    await $.session.start(SESSION)
    await clock.settle()
    expect(world.statuses).toEqual(['◆ tm offline'])
    await clock.advance(60_000)
    await clock.settle()
    expect(world.runs.filter(argv => argv.includes('tools'))).toHaveLength(1)
  })
})

describe('host paths and the restore scanner', () => {
  test('a handover entry carrying the host\'s path still points under this clone\'s root', () => {
    const doc = JSON.parse(HANDOVERS_OPEN) as { handovers: Record<string, unknown>[] }
    doc.handovers = doc.handovers.map(h => ({ ...h, path: `/srv/tm/.taskmaster/handovers/${String(h.id)}.md` }))
    const parsed = parseHandovers(JSON.stringify(doc), ROOT)
    expect(parsed.ok).toBe(true)
    if (parsed.ok) for (const h of parsed.value.handovers) expect(h.path.startsWith(`${ROOT}\\.taskmaster\\handovers\\`)).toBe(true)
  })

  test('the scanner keeps the rule: 9009 moves on, a crash stands', async () => {
    const runs: string[] = []
    const rh = (answers: Record<string, RunOut>) => ({
      paths: async () => ({ configDir: 'c', cache: 'k.json', script: 's.py' }),
      run: async (argv: readonly string[]) => {
        runs.push(argv[0] ?? '')
        return answers[argv[0] ?? ''] ?? { exitCode: 9009, stdout: '', stderr: '' }
      },
    })
    const ok = { exitCode: 0, stdout: JSON.stringify({ scannedAt: 1, root: ROOT, sessions: [] }), stderr: '' }
    expect((await runScan(rh({ python3: ok }), { root: ROOT, days: 1, exclude: '' })).sessions).toEqual([])
    expect(runs).toEqual(['python', 'python3'])
    runs.length = 0
    await expect(runScan(rh({ python: { exitCode: 1, stdout: '', stderr: 'Traceback\nValueError: bad root\n' }, python3: ok }), { root: ROOT, days: 1, exclude: '' })).rejects.toThrow(
      'ValueError: bad root',
    )
    expect(runs).toEqual(['python'])
  })
})
