// User intent: rr-tui's front door — give every mod $.rr (Reality Reprojection tokens and element trees for the active
// polarity) and the /rr-gallery pane where the terminal look is tuned live against screenshots.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderNode } from 'claude-code'

import type { Rr, RrNode, RrPolarity } from '../types'
import { galleryTree } from './gallery'
import * as kit from './kit'
import { resolvePolarity, tokensFor } from './polarity'

const POLARITY = atom({ plugin: 'rr-tui', key: 'polarity' } as const, 'dark' as RrPolarity)
const OVERRIDE = atom({ plugin: 'rr-tui', key: 'override' } as const, 'none' as RrPolarity | 'none')
const GALLERY = 'rr-gallery'

const out = (node: RenderNode): RrNode => node as unknown as RrNode
const back = (nodes: readonly RrNode[]): RenderNode[] => nodes as unknown as RenderNode[]

// Module state: a hot reload re-imports the module, so this starts fresh with it. A function that takes `$` must be declared
// at the top of the file (validator rule), so publish() reads this object instead of closure variables.
const mod = { setting: 'auto', theme: undefined as unknown, override: 'none' as RrPolarity | 'none', active: 'dark' as RrPolarity }
const tokens = () => tokensFor(mod.active)

async function publish($: EngineInterface): Promise<void> {
  mod.active = mod.override === 'none' ? resolvePolarity(mod.setting, mod.theme) : mod.override
  await update($, POLARITY, () => mod.active)
}

export const register: Register = (on, options) => {
  mod.setting = typeof options.polarity === 'string' ? options.polarity : 'auto'
  mod.active = resolvePolarity(mod.setting, mod.theme)

  on('engine.create', async ($, e, next) => {
    const built = await next(e)
    const rr: Rr = {
      tokens: async () => tokens(),
      polarity: async () => mod.active,
      surface: async a => out(kit.surface(tokens(), { ...a, children: back(a.children) })),
      surfaceProps: async a => kit.surfaceProps(tokens(), a),
      label: async a => out(kit.label(tokens(), a)),
      signal: async a => out(kit.signal(tokens(), a)),
      row: async a => out(kit.row(tokens(), { ...a, cells: back(a.cells) })),
      rule: async a => out(kit.rule(tokens(), a)),
      button: async a => kit.button(tokens(), a),
      keycap: async a => out(kit.keycap(tokens(), a)),
      chip: async a => out(kit.chip(tokens(), a)),
    }
    return { ...built, rr }
  })

  on('session.start', async ($, e, next) => {
    mod.theme = (await $.config.list()).find(row => row.key === 'theme')?.value
    mod.override = await read($, OVERRIDE)
    await publish($)
    await $.command.register({ name: GALLERY, description: 'Open the Reality Reprojection gallery: every $.rr element in every state' })
    return next(e)
  })

  on('config.set', { key: 'theme' }, async ($, e, next) => {
    const done = await next(e)
    if (done.deny === undefined) {
      mod.theme = done.value
      await publish($)
    }
    return done
  })

  on('command.run', { command: GALLERY }, async $ => {
    const opened = await $.ui.open({ id: GALLERY, title: 'RR gallery', focus: true, closeOnEscape: true })
    if (!opened.isPlaced) $.ui.toast('rr-gallery: widen the terminal to see the gallery')
    return { text: 'RR gallery opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: GALLERY }, async ($, e) => {
    const { Box, Button } = $.ui.resolve(e)
    const shown = await read($, POLARITY)
    const t = tokensFor(shown)
    const flip = (to: RrPolarity | 'none') => async () => {
      mod.override = to
      await update($, OVERRIDE, () => to)
      await publish($)
    }
    const demo = (key: string, label: string): RenderNode => (
      <Button key={key} plain label={label} onPress={() => $.ui.toast(`rr-gallery: pressed ${label}`)} />
    )
    return (
      <Box flexDirection="column" rowGap={1}>
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
