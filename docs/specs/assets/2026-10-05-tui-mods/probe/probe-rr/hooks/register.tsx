// User intent: throwaway probe — can one mod add a `$` noun that returns finished element trees (the planned $.rr) to other mods?
import type { Register } from 'claude-code'
import type { ProbeRr } from '../types'

export const register: Register = on => {
  on('engine.create', async ($, e, next) => {
    const built = await next(e)
    const probeRr: ProbeRr = {
      version: () => 'probe-2',
      swatch: ({ label, fg, bg }) =>
        h('Box', { borderStyle: 'round', borderColor: fg, paddingX: 1 }, h('Text', { color: fg, backgroundColor: bg }, label)),
    }
    return { ...built, probeRr }
  })
}
