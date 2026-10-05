// User intent: the handovers pane as a person uses it — five newest open handovers, copy as Telegram-ready text with the
// absolute path (or the path in a toast when there is no clipboard), resume into the prompt without closing the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { demoSnapshot } from '../hooks/demo'
import { handoverCopyText } from '../hooks/model'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const HANDOVERS_PANE = pane('tm-handovers')
const SAMPLE = demoSnapshot(0).handovers

describe('handovers', () => {
  test('/tm-handovers opens the handovers pane', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-handovers'))).toEqual({ text: 'Handovers opened.' })
    expect(world.opened).toEqual(['tm-handovers'])
  })

  test('five newest open handovers, one Button each, the footer, and c / r as whole-chip buttons', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    const rows = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('ho:'))
    expect(rows.map(b => b.key)).toEqual(SAMPLE.map(h => `ho:${h.id}`))
    expect(await ui.find({ type: 'Text', text: '5 of 6 · superseded hidden' })).toBeDefined()
    for (const [key, hotkey] of [
      ['copy', 'c'],
      ['resume', 'r'],
    ] as const) {
      const wrapper = await ui.find({ type: 'Box', key: `${key}-box` })
      expect(wrapper?.children[0]).toMatchObject({ type: 'Button', props: { key, hotkey, label: key, plain: true } })
    }
  })

  test('c copies tldr, next action and absolute path; with no clipboard the toast carries the path', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([handoverCopyText(SAMPLE[0]!)])
    expect(world.copies[0]).toContain('C:\\Users\\demo\\project\\.taskmaster\\handovers\\')
    expect(world.toasts).toEqual([`Copied handover ${SAMPLE[0]!.id}`])
    world.copyResult = { isCopied: false, reason: 'no-clipboard' }
    await ui.press({ key: 'copy' })
    expect(world.toasts[1]).toContain(SAMPLE[0]!.path)
  })

  test('picking a row then r fills "Resume from handover <id> (<path>)" and keeps the pane open', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    const second = SAMPLE[1]!
    await ui.press({ key: `ho:${second.id}` })
    await ui.press({ key: 'resume' })
    expect(world.fills).toEqual([`Resume from handover ${second.id} (${second.path})`])
    expect(world.closed).toEqual([])
  })
})
