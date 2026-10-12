// User intent: the keys the band and the Session tab share — open a pane, open one handover where it sits, copy any handover
// by id, launch a fresh session in a new tab — behave the same from every surface, and demo never opens a real terminal.
import { describe, expect, test } from 'claude-code/testing'

import { createFlows, type TmWriter } from '../hooks/flows'
import type { TmHost } from '../hooks/host'
import { psScript, resumePrompt } from '../hooks/launch'
import { hostOf } from '../hooks/register'
import type { TmHandover, TmHandoverNotice, TmSnapshot } from '../types'

const ho = (n: number): TmHandover => ({
  id: `h-${n}`,
  created: `2026-10-0${n % 9}T10:00`,
  tldr: `tldr ${n}`,
  nextAction: `next ${n}`,
  path: `C:\\p\\h-${n}.md`,
  branch: '',
  taskIds: [],
  thread: `thread-${n}`,
})
const SNAP: TmSnapshot = {
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [],
  queueTotal: 0,
  handovers: [1, 2, 3, 4, 5, 6, 7].map(ho),
  handoversTotal: 7,
  bound: null,
}
const NOTICE: TmHandoverNotice = { id: 'h-new', tldr: 'Wrote it', path: 'C:\\p\\h-new.md', text: 'Wrote it\nC:\\p\\h-new.md\nResume: x — y' }

/** Flows over a recording host and an in-memory writer; `exits` answers each command by argv[0] (absent: cannot start). */
function rig(a: { source?: 'tm' | 'demo'; notice?: TmHandoverNotice | null; exits?: Record<string, number>; copies?: boolean; read?: TmHandover | null } = {}) {
  const seen = { opened: [] as string[], toasts: [] as string[], copies: [] as string[], runs: [] as string[][], focus: [] as string[], page: -1, pick: '' }
  let notice = a.notice ?? null
  let snapshot: TmSnapshot | null = SNAP
  const host = new Proxy(
    {
      openPane: async (id: string) => {
        seen.opened.push(id)
        return true
      },
      toast: (text: string) => {
        seen.toasts.push(text)
      },
      copy: async (text: string) => {
        seen.copies.push(text)
        return a.copies === false ? { isCopied: false, reason: 'no-clipboard' } : { isCopied: true, reason: '' }
      },
      focus: async (requestId: string, key: string) => {
        seen.focus.push(`${requestId}/${key}`)
      },
      launchEnv: async () => ({ os: 'Windows_NT' }),
      run: async (argv: readonly string[]) => {
        seen.runs.push([...argv])
        const code = (a.exits ?? { 'wt.exe': 0 })[argv[0] ?? '']
        return code === undefined ? null : { exitCode: code, stdout: '' }
      },
      sleep: async () => undefined,
      after: () => undefined,
    },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmHost
  const held = {
    notice: async (change: (n: TmHandoverNotice | null) => TmHandoverNotice | null) => {
      notice = change(notice)
    },
    snapshot: async (change: (s: TmSnapshot | null) => TmSnapshot | null) => {
      snapshot = change(snapshot)
    },
    handoverPage: async (page: number) => {
      seen.page = page
    },
    pick: async (id: string) => {
      seen.pick = id
    },
  }
  const write = new Proxy(held, { get: (target, key) => (key in target ? target[key as keyof typeof held] : async () => undefined) }) as unknown as TmWriter
  const flows = createFlows({
    host,
    write,
    actions: { done: async () => ({ ok: true }), backToAgent: async () => ({ ok: true }) },
    afterWrite: () => undefined,
    boundId: async () => null,
    source: a.source ?? 'tm',
    ...(a.read === undefined ? {} : { readHandover: async () => a.read ?? null }),
  })
  return { flows, seen }
}

describe('opening panes', () => {
  test('1 2 3 4 open review, handovers, session and restore', async () => {
    const { flows, seen } = rig()
    for (const pane of ['review', 'handovers', 'session', 'restore'] as const) await flows.openPane(pane)
    expect(seen.opened).toEqual(['tm-review', 'tm-handovers', 'tm-session', 'tm-restore'])
  })

  test('one handover opens the pane on its page, picked and focused; one not listed opens page 1 and says so', async () => {
    const { flows, seen } = rig()
    await flows.openHandover('h-7')
    expect([seen.page, seen.pick, seen.opened, seen.focus]).toEqual([2, 'h-7', ['tm-handovers'], ['tm-handovers/ho:h-7']])
    await flows.openHandover('h-99')
    expect([seen.page, seen.pick]).toEqual([0, ''])
    expect(seen.toasts).toEqual(['taskmaster-mods: handover h-99 is not in the open list'])
  })
})

describe('copy by id', () => {
  test("this session's notice copies its own block; a listed handover its Telegram block", async () => {
    const { flows, seen } = rig({ notice: NOTICE })
    await flows.copyHandover('h-new')
    await flows.copyHandover('h-2')
    expect(seen.copies).toEqual([NOTICE.text, 'tldr 2\nC:\\p\\h-2.md\nResume: thread-2 — next 2'])
    expect(seen.toasts).toEqual(['Copied handover h-new', 'Copied handover h-2'])
  })

  test('a handover outside the list is read by id; unknown says not found; no clipboard names the path', async () => {
    const away = { ...ho(9), id: 'h-away', path: 'C:\\p\\h-away.md' }
    const { flows, seen } = rig({ read: away, copies: false })
    await flows.copyHandover('h-away')
    expect(seen.copies).toEqual(['tldr 9\nC:\\p\\h-away.md\nResume: thread-9 — next 9'])
    expect(seen.toasts).toEqual(['Clipboard unavailable (no-clipboard): copy by hand from C:\\p\\h-away.md'])
    const none = rig({ read: null })
    await none.flows.copyHandover('h-gone')
    expect(none.seen.toasts).toEqual(['taskmaster-mods: handover h-gone not found'])
  })
})

describe('launch', () => {
  const spec = { cwd: 'C:\\work\\proj', title: 'handover h-2', mode: 'fresh' as const, prompt: resumePrompt('h-2', 'C:\\p\\h-2.md') }

  test('opens a Windows Terminal tab with the fresh prompt and toasts once', async () => {
    const { flows, seen } = rig()
    expect(await flows.launch([spec])).toEqual({ opened: 1, copied: null, failed: [] })
    expect(seen.runs[0]?.slice(0, 4)).toEqual(['wt.exe', '-w', '0', 'nt'])
    expect(seen.toasts).toEqual(['Opened 1 tab'])
  })

  test('no terminal: the command is copied and the toast says to paste it', async () => {
    const { flows, seen } = rig({ exits: {} })
    await flows.launch([spec])
    expect(seen.copies).toEqual([psScript(spec, undefined)])
    expect(seen.toasts).toEqual(['Copied — paste into a new terminal'])
  })

  test('demo runs nothing and says what it would open', async () => {
    const { flows, seen } = rig({ source: 'demo' })
    expect(await flows.launch([spec])).toEqual({ opened: 0, copied: null, failed: [] })
    expect(seen.runs).toEqual([])
    expect(seen.toasts).toEqual(['Demo: would open handover h-2'])
  })
})

describe('launch through the real host bindings', () => {
  const spec = { cwd: 'C:\work\proj', title: 'handover h-2', mode: 'fresh' as const, prompt: resumePrompt('h-2', 'C:\p\h-2.md') }
  /** The engine's `$` reduced to what the launch path touches: hostOf maps it, the flows run over it. */
  function over(run: (argv: readonly string[], o: { timeoutMs: number }) => Promise<{ exitCode: number; stdout: string }>) {
    const seen = { runs: [] as { argv: readonly string[]; timeoutMs: number }[], logs: [] as string[], toasts: [] as string[], copies: [] as string[] }
    const engine = {
      env: { get: async (name: string) => (name === 'OS' ? 'Windows_NT' : undefined) },
      process: {
        run: async (argv: readonly string[], o: { timeoutMs: number }) => {
          seen.runs.push({ argv, timeoutMs: o.timeoutMs })
          return run(argv, o)
        },
      },
      clock: { sleep: async () => undefined },
      ui: {
        log: (text: string) => void seen.logs.push(text),
        toast: (text: string) => void seen.toasts.push(text),
        copy: async (c: { text: string }) => {
          seen.copies.push(c.text)
          return { isCopied: true, reason: '' }
        },
      },
    } as unknown as Parameters<typeof hostOf>[0]
    const quiet = new Proxy({}, { get: () => async () => undefined }) as unknown as TmWriter
    const flows = createFlows({
      host: hostOf(engine),
      write: quiet,
      actions: { done: async () => ({ ok: true }), backToAgent: async () => ({ ok: true }) },
      afterWrite: () => undefined,
      boundId: async () => null,
      source: 'tm',
    })
    return { flows, seen }
  }

  test('the argv reaches process.run as separate arguments, unjoined, with the launch timeout', async () => {
    const { flows, seen } = over(async () => ({ exitCode: 0, stdout: '' }))
    expect(await flows.launch([spec])).toEqual({ opened: 1, copied: null, failed: [] })
    const wt = seen.runs.find(r => r.argv[0] === 'wt.exe')
    expect(wt?.argv.slice(0, 4)).toEqual(['wt.exe', '-w', '0', 'nt'])
    expect(wt?.argv.length).toBeGreaterThan(4)
    expect(wt?.timeoutMs).toBe(15_000)
  })

  test('a run that throws or times out reads as not opened: the command is copied and the reason is logged', async () => {
    const { flows, seen } = over(async () => {
      throw new Error('timed out')
    })
    const r = await flows.launch([spec])
    expect(r.opened).toBe(0)
    expect(seen.copies).toEqual([psScript(spec, undefined)])
    expect(seen.logs.some(l => l.includes('timed out'))).toBe(true)
  })
})
