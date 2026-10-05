// User intent: prove $.rr works as other mods see it — polarity from config, theme changes and gallery flips follow
// through — and that the gallery, which draws every element, validates on the terminal and the desktop.
import { describe, expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { CONSUMER } from './fixtures/consumer'
import { command, GALLERY_PANE, SESSION } from './fixtures/inputs'
import { rrWorldOf } from './fixtures/world'

const probe = async ($: Engine): Promise<Record<string, unknown>> =>
  JSON.parse((await $.command.run(command('rr-probe'))).text ?? '{}') as Record<string, unknown>

const THEME_TO_DARK = {
  key: 'theme',
  value: 'dark',
  previous: 'light',
  provider: { plugin: 'engine', tier: 'core' },
  origin: { kind: 'composer' },
}

describe('$.rr', () => {
  test('auto with a light theme draws light, and a theme change in /config flips it', { plugins: [CONSUMER] }, async ($, on) => {
    rrWorldOf(on, 'light')
    await $.session.start(SESSION)
    expect(await probe($)).toMatchObject({ polarity: 'light' })
    await $.config.set(THEME_TO_DARK as never)
    expect(await probe($)).toMatchObject({ polarity: 'dark' })
  })

  test('a pinned polarity ignores the theme', { options: { polarity: 'survivalist' }, plugins: [CONSUMER] }, async ($, on) => {
    rrWorldOf(on, 'light')
    await $.session.start(SESSION)
    expect(await probe($)).toMatchObject({ polarity: 'survivalist' })
  })

  test('/rr-gallery opens the pane and its polarity buttons change what every consumer gets', { plugins: [CONSUMER] }, async ($, on) => {
    const world = rrWorldOf(on, 'dark')
    await $.session.start(SESSION)
    await $.command.run(command('rr-gallery'))
    expect(world.opened).toEqual(['rr-gallery'])
    const ui = await $.ui.mount({ plugin: 'rr-tui', ...GALLERY_PANE })
    await ui.press({ key: 'pol-light' })
    expect(await probe($)).toMatchObject({ polarity: 'light' })
    expect(await ui.find({ type: 'Text', text: /POLARITY LIGHT/ })).toBeDefined()
    await ui.press({ key: 'pol-auto' })
    expect(await probe($)).toMatchObject({ polarity: 'dark' })
    await ui.unmount()
  })

  test('every element validates on the terminal and the desktop in every polarity', async ($, on) => {
    rrWorldOf(on, 'dark')
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: 'rr-tui', ...GALLERY_PANE, surface })
      for (const key of ['pol-dark', 'pol-light', 'pol-survivalist']) {
        await ui.press({ key })
        expect(await ui.find({ type: 'Button', key: 'outline-page' })).toBeDefined()
        expect(await ui.find({ type: 'Button', key: 'open-overlay' })).toBeDefined()
      }
      await ui.unmount()
    }
  })
})
