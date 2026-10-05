// User intent: the engine beneath rr-tui in tests — the theme row, commands, panes and toasts answered from memory — so
// polarity and the gallery can be driven without a session.
import type { On } from 'claude-code'

export type RrWorld = { opened: string[]; toasts: string[]; theme: string }

export function rrWorldOf(on: On, theme = 'dark'): RrWorld {
  const world: RrWorld = { opened: [], toasts: [], theme }
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('config.list', () =>
    ({ value: [{ key: 'theme', label: 'Theme', kind: 'choice', value: world.theme, provider: { plugin: 'engine', tier: 'core' }, isLocked: false }] }) as never,
  )
  on('config.set', ($, e) => {
    if (e.key === 'theme' && typeof e.value === 'string') world.theme = e.value
    return { value: e.value }
  })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
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
