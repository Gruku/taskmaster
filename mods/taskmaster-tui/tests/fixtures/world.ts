// User intent: the engine beneath taskmaster-tui in tests — panes, prompt, clipboard, store, session and the tm server answered
// from memory, every effect recorded — so a test asserts what the mod asked for.
import type { On } from 'claude-code'
import type { MockClock } from 'claude-code/testing'

export type McpAnswer = { text: string; isError?: boolean } | 'offline' | { hangMs: number }

export type World = {
  opened: string[]
  closed: string[]
  toasts: string[]
  statuses: (string | undefined)[]
  logs: { text: string; to: string }[]
  fills: string[]
  copies: string[]
  copyResult: { isCopied: true } | { isCopied: false; reason: string }
  store: Map<string, unknown>
  sessionId: string
  root: string
  branch: string
  cwd: string
  calls: { tool: string; args: Record<string, unknown> }[]
  mcp: (tool: string, args: Record<string, unknown>) => McpAnswer
}

export function worldOf(on: On, clock: MockClock, store: Record<string, unknown> = {}): World {
  const world: World = {
    opened: [],
    closed: [],
    toasts: [],
    statuses: [],
    logs: [],
    fills: [],
    copies: [],
    copyResult: { isCopied: true },
    store: new Map(Object.entries(store)),
    sessionId: 'sess-A',
    root: 'C:\\work\\proj',
    branch: 'main',
    cwd: 'C:\\work\\proj',
    calls: [],
    mcp: () => 'offline',
  }
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  on('classic.SessionStart', () => ({}) as never)
  on('ui.render', () => ({ type: 'Text', children: ['BELOW'] }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('ui.open', ($, e) => {
    world.opened.push(e.id)
    return { value: { isPlaced: true } } as never
  })
  on('ui.close', ($, e) => {
    world.closed.push(e.id)
    return { value: undefined }
  })
  on('ui.toast', ($, e) => {
    world.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.status', ($, e) => {
    world.statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.log', ($, e) => {
    world.logs.push({ text: e.text, to: e.to })
    return { value: undefined }
  })
  on('ui.copy', ($, e) => {
    world.copies.push(e.text)
    return { value: world.copyResult } as never
  })
  on('prompt.fill', ($, e) => {
    world.fills.push(e.text)
    return { isFilled: true }
  })
  on('store.get', ($, e) => ({ value: world.store.get(e.key) }))
  on('store.set', ($, e) => {
    world.store.set(e.key, e.value)
    return { value: undefined }
  })
  on('store.delete', ($, e) => {
    world.store.delete(e.key)
    return { value: undefined }
  })
  on('store.keys', () => ({ value: [...world.store.keys()] }))
  on('session.id', () => ({ value: world.sessionId }))
  on('session.root', () => ({ value: world.root }))
  on('session.cwd', () => ({ value: world.cwd }))
  on('session.repo', () => ({ value: { root: world.root, remote: null, internal: false, name: null } }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: `${world.branch}\n`, stderr: '' } }) as never)
  on('mcp.call', async ($, e) => {
    world.calls.push({ tool: e.tool, args: e.args })
    const answer = world.mcp(e.tool, e.args)
    if (answer === 'offline') return { deny: 'tm: Connection closed' }
    if ('hangMs' in answer) {
      await clock.sleep(answer.hangMs)
      return { value: { content: [{ type: 'text', text: 'late' }], isError: false } } as never
    }
    return { value: { content: [{ type: 'text', text: answer.text }], isError: answer.isError === true } } as never
  })
  return world
}

/** `$.state` answered from memory beneath the plugins, seeded by `plugin.key`: a test starts from a state no flow reaches. */
export function stateOf(on: On, seed: Readonly<Record<string, unknown>> = {}): Map<string, unknown> {
  const values = new Map<string, unknown>(Object.entries(seed))
  const versions = new Map<string, number>(Object.keys(seed).map(k => [k, 1]))
  const nameOf = (e: { plugin: string; key: string }) => `${e.plugin}.${e.key}`
  on('state.get', ($, e) => ({ value: { value: values.get(nameOf(e)), version: versions.get(nameOf(e)) ?? 0 } }) as never)
  on('state.set', ($, e) => {
    const name = nameOf(e)
    const version = versions.get(name) ?? 0
    if (e.ifVersion !== undefined && e.ifVersion !== version) return { value: { isSet: false, version } } as never
    values.set(name, e.value)
    versions.set(name, version + 1)
    return { value: { isSet: true, version: version + 1 } } as never
  })
  return values
}
