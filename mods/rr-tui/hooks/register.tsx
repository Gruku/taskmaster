// User intent: rr-tui's front door — give every mod $.rr (Reality Reprojection tokens and element trees for the active
// polarity) and the /rr-gallery pane where the terminal look is tuned live against screenshots.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderNode } from 'claude-code'

import type { Rr, RrNode, RrPolarity, RrTokens } from '../types'
import { galleryTree } from './gallery'
import type { Demo } from './gallery'
import * as kit from './kit'
import { isPolarity, resolvePolarity, tokensFor } from './polarity'

const POLARITY = atom({ plugin: 'rr-tui', key: 'polarity' } as const, 'dark' as RrPolarity)
const OVERRIDE = atom({ plugin: 'rr-tui', key: 'override' } as const, 'none' as RrPolarity | 'none')
const GALLERY = 'rr-gallery'

const out = (node: RenderNode): RrNode => node as unknown as RrNode
const back = (nodes: readonly RrNode[]): RenderNode[] => nodes as unknown as RenderNode[]

// `$.state` is the single source of truth for the polarity: every $.rr call and every gallery draw resolves it afresh from the
// persisted override, the theme row and the setting, so a hot reload or a /clear can never leave $.rr and the gallery apart.
// Only the setting lives here: every load (a hot reload or a config change included) hands register() its options anew.
const mod = { setting: 'auto' }

async function effective($: EngineInterface): Promise<RrPolarity> {
  const override: unknown = await read($, OVERRIDE)
  if (isPolarity(override)) return override // anything else stored there counts as 'none'
  return resolvePolarity(mod.setting, (await $.config.list()).find(row => row.key === 'theme')?.value)
}

async function tokensOf($: EngineInterface): Promise<RrTokens> {
  return tokensFor(await effective($))
}

async function publish($: EngineInterface): Promise<void> {
  const active = await effective($)
  await update($, POLARITY, () => active)
}

export const register: Register = (on, options) => {
  mod.setting = typeof options.polarity === 'string' ? options.polarity : 'auto'

  // The noun's own bodies get no `$`, so they cannot read $.state. rr-tui's rr.* hooks below sit above them and answer every
  // call from $.state; a body runs only if such a hook failed, and then refuses rather than draw a polarity the gallery doesn't.
  on('engine.create', async ($, e, next) => {
    const built = await next(e)
    const refuse = async (): Promise<never> => {
      throw new Error('rr-tui: $.rr could not read the polarity from $.state (its rr.* hook failed; see the debug log)')
    }
    const rr: Rr = {
      tokens: refuse,
      polarity: refuse,
      surface: refuse,
      surfaceProps: refuse,
      label: refuse,
      signal: refuse,
      row: refuse,
      rule: refuse,
      button: refuse,
      keycap: refuse,
      chip: refuse,
    }
    return { ...built, rr }
  })

  on('rr.tokens', async $ => ({ value: await tokensOf($) }))
  on('rr.polarity', async $ => ({ value: await effective($) }))
  on('rr.surface', async ($, a) => ({ value: out(kit.surface(await tokensOf($), { ...a, children: back(a.children) })) }))
  on('rr.surfaceProps', async ($, a) => ({ value: kit.surfaceProps(await tokensOf($), a) }))
  on('rr.label', async ($, a) => ({ value: out(kit.label(await tokensOf($), a)) }))
  on('rr.signal', async ($, a) => ({ value: out(kit.signal(await tokensOf($), a)) }))
  on('rr.row', async ($, a) => ({ value: out(kit.row(await tokensOf($), { ...a, cells: back(a.cells) })) }))
  on('rr.rule', async ($, a) => ({ value: out(kit.rule(await tokensOf($), a)) }))
  on('rr.button', async ($, a) => ({ value: kit.button(await tokensOf($), a) }))
  on('rr.keycap', async ($, a) => ({ value: out(kit.keycap(await tokensOf($), a)) }))
  on('rr.chip', async ($, a) => ({ value: out(kit.chip(await tokensOf($), a)) }))

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: GALLERY, description: 'Open the Reality Reprojection gallery: every $.rr element in every state' })
    await publish($)
    return next(e)
  })

  on('config.set', { key: 'theme' }, async ($, e, next) => {
    const done = await next(e)
    if (done.deny === undefined) await publish($)
    return done
  })

  // /clear, /resume and /branch reset $.state without a new session.start; republish so the atom matches $.rr again. At startup
  // this fires before a session is bound, when $.config.list throws: leave that publish to session.start, which follows once
  // the REPL mounts. Never throw here — a missed publish only leaves the atom stale, since $.rr and the gallery resolve per call.
  on('classic.SessionStart', async ($, e, next) => {
    try {
      await publish($)
    } catch {
      // no session bound yet (startup); session.start publishes
    }
    return next(e)
  })

  on('command.run', { command: GALLERY }, async $ => {
    const opened = await $.ui.open({ id: GALLERY, title: 'RR gallery', focus: true, closeOnEscape: true })
    if (!opened.isPlaced) $.ui.toast('rr-gallery: widen the terminal to see the gallery')
    return { text: 'RR gallery opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: GALLERY }, async ($, e) => {
    const { Box, Button } = $.ui.resolve(e)
    await read($, POLARITY) // subscribes the pane, so a theme change redraws it
    const shown = await effective($)
    const t = tokensFor(shown)
    const flip = (to: RrPolarity | 'none') => async () => {
      await update($, OVERRIDE, () => to)
      await publish($)
    }
    const demo: Demo = (key, label, variant) => {
      const press = () => $.ui.toast(`rr-gallery: pressed ${key}`)
      return variant ? (
        <Button key={key} variant={variant} label={label} onPress={press} />
      ) : (
        <Button key={key} plain label={label} onPress={press} />
      )
    }
    return (
      <Box {...kit.surfaceProps(t, { level: 'page' })} flexGrow={1} rowGap={1}>
        <Box flexDirection="row" columnGap={2}>
          {kit.label(t, { text: `polarity ${shown}` })}
          <Button key="pol-dark" hotkey="1" plain label="dark" onPress={flip('dark')} />
          <Button key="pol-light" hotkey="2" plain label="light" onPress={flip('light')} />
          <Button key="pol-survivalist" hotkey="3" plain label="survivalist" onPress={flip('survivalist')} />
          <Button key="pol-auto" hotkey="0" plain label="auto" onPress={flip('none')} />
        </Box>
        {galleryTree(t, e.props.bodyColumns, demo)}
      </Box>
    )
  })
}
