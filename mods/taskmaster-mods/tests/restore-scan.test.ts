// User intent: prove the restore pane's outside readers fail to a reason, never a crash — scan.py under the first Python
// that runs, isolated, with this repo's root; the digest for a brief; one backlog_query that may be refused or slow.
import { describe, expect, test } from 'claude-code/testing'

import type { TmReply } from '../hooks/host'
import { type RestoreHost, resolveRefs, runDigest, runScan, type RunOut } from '../hooks/restore/scan'
import { refsReply, ROOT, scanOf, session } from './fixtures/restore'

const PATHS = { configDir: 'C:\\Users\\me\\.claude', cache: 'C:\\tmp\\tm-restore-scan-cache.json', script: 'C:\\mods\\taskmaster-mods/scanner/scan.py' }

function hostOf(answer: (argv: readonly string[]) => RunOut | Error): { rh: RestoreHost; runs: (readonly string[])[] } {
  const runs: (readonly string[])[] = []
  return {
    runs,
    rh: {
      paths: async () => PATHS,
      run: async argv => {
        runs.push(argv)
        const out = answer(argv)
        if (out instanceof Error) throw out
        return out
      },
    },
  }
}

const ok = (value: unknown): RunOut => ({ exitCode: 0, stdout: JSON.stringify(value), stderr: '' })

function tmOf(reply: (args: Record<string, unknown>) => TmReply) {
  const calls: Record<string, unknown>[] = []
  const logs: string[] = []
  return {
    calls,
    logs,
    tm: {
      call: async (tool: string, args: Record<string, unknown>) => {
        calls.push({ tool, ...args })
        return reply(args)
      },
      sleep: () => new Promise<void>(() => undefined),
      log: (text: string) => void logs.push(text),
    },
  }
}

describe('runScan', () => {
  test('runs the script isolated with the repo root, the window, the cache and this session left out', async () => {
    const { rh, runs } = hostOf(() => ok({ scannedAt: 1, root: ROOT, sessions: [session('a')] }))
    const scan = await runScan(rh, { root: ROOT, days: 3, exclude: 'me' })
    expect(runs).toEqual([
      ['python', '-I', PATHS.script, 'scan', '--config-dir', PATHS.configDir, '--root', ROOT, '--days', '3', '--cache', PATHS.cache, '--exclude', 'me'],
    ])
    expect(scan.sessions.map(s => s.id)).toEqual(['a'])
  })

  test('falls through python, python3 and py; the reason is the last failure', async () => {
    const tried = hostOf(argv =>
      argv[0] === 'python' ? new Error('spawn python ENOENT') : argv[0] === 'python3' ? { exitCode: 1, stdout: '', stderr: 'Traceback\nValueError: bad root\n' } : ok({ sessions: [] }),
    )
    expect((await runScan(tried.rh, { root: ROOT, days: 4, exclude: '' })).sessions).toEqual([])
    expect(tried.runs.map(argv => argv[0])).toEqual(['python', 'python3', 'py'])
    expect(tried.runs[2]).not.toContain('--exclude')
    const none = hostOf(argv => (argv[0] === 'py' ? { exitCode: 9, stdout: '', stderr: '' } : new Error('spawn ENOENT')))
    await expect(runScan(none.rh, { root: ROOT, days: 4, exclude: '' })).rejects.toThrow('py exited 9')
  })

  test('output that is not a scan is an error', async () => {
    await expect(runScan(hostOf(() => ({ exitCode: 0, stdout: 'oops', stderr: '' })).rh, { root: ROOT, days: 4, exclude: '' })).rejects.toThrow(
      'the scanner printed no JSON',
    )
    await expect(runScan(hostOf(() => ok({ nope: 1 })).rh, { root: ROOT, days: 4, exclude: '' })).rejects.toThrow('the scanner answered no session list')
  })
})

describe('runDigest', () => {
  test('reads the first prompt and the tail; a missing session is an error', async () => {
    const found = hostOf(() => ok({ first: 'USER: start', tail: 'USER: start\nASSISTANT: done' }))
    expect(await runDigest(found.rh, 'abc', ROOT)).toEqual({ first: 'USER: start', tail: 'USER: start\nASSISTANT: done' })
    expect(found.runs[0]).toEqual(['python', '-I', PATHS.script, 'digest', '--config-dir', PATHS.configDir, '--session', 'abc', '--root', ROOT])
    await expect(runDigest(hostOf(() => ok({ error: 'session not found' })).rh, 'zz', ROOT)).rejects.toThrow('session not found')
  })
})

describe('resolveRefs', () => {
  test('nothing to ask resolves empty without a call', async () => {
    const { tm, calls } = tmOf(() => ({ text: '', isError: false }))
    expect(await resolveRefs(tm, scanOf([session('a')]))).toEqual({ tasks: {}, handovers: {} })
    expect(calls).toEqual([])
  })

  test('one backlog_query, its table read', async () => {
    const { tm, calls } = tmOf(() => ({ text: refsReply(['["t","b-2","in-review","full","p2"]  Sign off', '["h","h-1","closed"]']), isError: false }))
    const refs = await resolveRefs(tm, scanOf([session('a', { taskIds: ['b-2'], handovers: [{ id: 'h-1', path: '', tldr: '', next: '' }] })]))
    expect(refs).toEqual({ tasks: { 'b-2': { id: 'b-2', status: 'in-review', lane: 'full', phase: 'p2', title: 'Sign off' } }, handovers: { 'h-1': 'closed' } })
    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ tool: 'backlog_query', limit: 500 })
  })

  test('an error reply or a refusal is null, with the reason logged', async () => {
    const scan = scanOf([session('a', { taskIds: ['b-2'] })])
    const failing = tmOf(() => ({ text: 'store locked', isError: true }))
    expect(await resolveRefs(failing.tm, scan)).toBeNull()
    expect(failing.logs).toEqual(['taskmaster-mods: restore refs not read: backlog_query: store locked'])
    const refused = tmOf(() => ({ text: 'Error: no such column: e.lane\n\nSchema: …', isError: false }))
    expect(await resolveRefs(refused.tm, scan)).toBeNull()
    expect(refused.logs).toEqual(['taskmaster-mods: restore refs unreadable: backlog_query: Error: no such column: e.lane'])
  })
})
