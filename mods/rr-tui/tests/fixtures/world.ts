// User intent: the engine beneath rr-tui in tests — the theme row, commands, panes and toasts answered from memory — so
// polarity and the gallery can be driven without a session.
import type { On } from 'claude-code'

export type RrWorld = { opened: string[]; toasts: string[]; registered: string[]; theme: string }

// `configFails` makes every $.config.list throw, standing for a host whose config read fails during session.start.
export function rrWorldOf(on: On, theme = 'dark', opts: { configFails?: boolean } = {}): RrWorld {
  const world: RrWorld = { opened: [], toasts: [], registered: [], theme }
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('classic.SessionStart', () => ({}))
  on('config.list', () => {
    if (opts.configFails) throw new Error('config unavailable')
    return { value: [{ key: 'theme', label: 'Theme', kind: 'choice', value: world.theme, provider: { plugin: 'engine', tier: 'core' }, isLocked: false }] } as never
  })
  on('config.set', ($, e) => {
    if (e.key === 'theme' && typeof e.value === 'string') world.theme = e.value
    return { value: e.value }
  })
  on('command.register', ($, e) => {
    world.registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('ui.open', ($, e) => {
    world.opened.push(e.id)
    return { value: { isPlaced: true } } as never
  })
  on('ui.toast', ($, e) => {
    world.toasts.push(e.text)
    return { value: undefined }
  })
  return world
}

// The host's $.state, answered from memory so a test can stand where a session stands after a hot reload (state already
// there, no session.start) or after /clear (state wiped).
export type StateWorld = { values: Map<string, unknown>; reset: () => void }

export function stateWorldOf(on: On, seed: Readonly<Record<string, unknown>> = {}): StateWorld {
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
  return {
    values,
    reset: () => {
      values.clear()
      versions.clear()
    },
  }
}
