// User intent: the handovers pane as a person uses it — five newest open handovers, copy as Telegram-ready text with the
// absolute path (or the path in a toast when there is no clipboard), the picked one's summary on `i`, resume into the prompt
// without closing the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_SUMMARIES, demoSnapshot } from '../hooks/demo'
import { handoverCopyText } from '../hooks/model'
import type { TmHandover, TmSnapshot } from '../types'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { type Drawn, elementsOf, widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const HANDOVERS_PANE = pane('tm-handovers')
const SAMPLE = demoSnapshot(0).handovers
const [FIRST, SECOND, THIRD] = SAMPLE as readonly [TmHandover, TmHandover, TmHandover]
const wide = (columns: number) => ({ ...HANDOVERS_PANE, props: { ...HANDOVERS_PANE.props, bodyColumns: columns } })
const textOf = (e: Drawn): string => (e.children ?? []).join('')
/** Texts in drawing order, so a test can say what sits under what. */
const textsOf = (tree: unknown): string[] =>
  elementsOf(tree)
    .filter(e => e.type === 'Text')
    .map(textOf)

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
    expect(world.copies).toEqual([handoverCopyText(FIRST)])
    expect(world.copies[0]).toContain('C:\\Users\\demo\\project\\.taskmaster\\handovers\\')
    expect(world.toasts).toEqual([`Copied handover ${FIRST.id}`])
    world.copyResult = { isCopied: false, reason: 'no-clipboard' }
    await ui.press({ key: 'copy' })
    expect(world.toasts[1]).toContain(FIRST.path)
  })

  test('picking a row then r fills "Resume from handover <id> (<path>)" and keeps the pane open', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: `ho:${SECOND.id}` })
    await ui.press({ key: 'resume' })
    expect(world.fills).toEqual([`Resume from handover ${SECOND.id} (${SECOND.path})`])
    expect(world.closed).toEqual([])
  })

  test('i expands the picked handover under its row: full tldr, branch and tasks, decisions, blockers, Next; i collapses it', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props).toMatchObject({ hotkey: 'i', label: '▸ summary' })
    expect(textsOf(await ui.drawn()).filter(t => t.startsWith('Next: '))).toEqual([`Next: ${FIRST.nextAction}`])
    expect(await ui.find({ type: 'Text', text: /^branch: / })).toBeUndefined()

    await ui.press({ key: 'summary' })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props.label).toBe('▾ summary')
    const summary = DEMO_SUMMARIES[FIRST.id]!
    expect(summary.decisions.length).toBeGreaterThan(0)
    expect(summary.blockers.length).toBeGreaterThan(0)
    const shown = [
      FIRST.tldr,
      `branch: ${FIRST.branch} · tasks: ${FIRST.taskIds.join(', ')}`,
      ...summary.decisions.map(d => `decision: ${d}`),
      ...summary.blockers.map(b => `blocker: ${b}`),
      `Next: ${FIRST.nextAction}`,
    ]
    const tree = await ui.drawn()
    const elements = elementsOf(tree)
    const at = (text: string) => elements.findIndex(e => e.type === 'Text' && textOf(e) === text)
    const keyAt = (key: string) => elements.findIndex(e => e.props?.key === key)
    // In this order, under the picked row and above the next one; the standalone Next line is gone, so Next shows once.
    const order = shown.map(at)
    expect(order.every(i => i >= 0), JSON.stringify(textsOf(tree))).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
    expect(keyAt(`ho:${FIRST.id}-box`)).toBeLessThan(at(FIRST.tldr))
    expect(at(`Next: ${FIRST.nextAction}`)).toBeLessThan(keyAt(`ho:${SECOND.id}-box`))
    expect(textsOf(tree).filter(t => t.startsWith('Next: '))).toHaveLength(1)
    // Indented under the row.
    const holder = elements.find(e => e.type === 'Box' && (e.children ?? []).some(c => (c as Drawn).type === 'Text' && textOf(c as Drawn) === FIRST.tldr))
    expect(Number(holder?.props?.paddingLeft ?? 0)).toBeGreaterThan(0)

    await ui.press({ key: 'summary' })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props.label).toBe('▸ summary')
    expect(await ui.find({ type: 'Text', text: /^branch: / })).toBeUndefined()
  })

  test('an empty part is left out: no tasks drops "· tasks", no blockers drops the blocker lines', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(THIRD.taskIds).toEqual([])
    expect(DEMO_SUMMARIES[THIRD.id]?.blockers).toEqual([])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: `ho:${THIRD.id}` })
    await ui.press({ key: 'summary' })
    expect(await ui.find({ type: 'Text', text: `branch: ${THIRD.branch}` })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /tasks:/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /^blocker: / })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /^decision: / })).toBeDefined()
  })

  test('the toggle follows the pick: another row shows collapsed, and coming back finds it collapsed too', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: 'summary' })
    expect(await ui.find({ type: 'Text', text: FIRST.tldr })).toBeDefined()
    await ui.press({ key: `ho:${SECOND.id}` })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props.label).toBe('▸ summary')
    expect(await ui.find({ type: 'Text', text: /^branch: / })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: `Next: ${SECOND.nextAction}` })).toBeDefined()
    await ui.press({ key: `ho:${FIRST.id}` })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props.label).toBe('▸ summary')
    expect(await ui.find({ type: 'Text', text: FIRST.tldr })).toBeUndefined()
  })

  test('before decisions and blockers arrive the summary says "loading summary…"; the tldr wraps whole and every line fits', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    const long = 'Store write hang root cause found and an incremental rebuild verified in scratch against the CodeMaestro backlog'
    const real: TmSnapshot = { ...demoSnapshot(0), reason: '', handovers: [{ ...FIRST, tldr: long }] }
    stateOf(on, { [`${PLUGIN}.snapshot`]: real, [`${PLUGIN}.summaryOpen`]: FIRST.id })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(40) })
    expect(await ui.find({ type: 'Text', text: 'loading summary…' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^decision: / })).toBeUndefined()
    const tree = await ui.drawn()
    const texts = textsOf(tree)
    // The whole tldr is there, wrapped over several lines, none cut short.
    const start = texts.findIndex(t => t !== '' && long.startsWith(t))
    expect(start).toBeGreaterThanOrEqual(0)
    let joined = ''
    for (let i = start; joined.length < long.length && i < texts.length; i += 1) joined = joined === '' ? (texts[i] ?? '') : `${joined} ${texts[i]}`
    expect(joined).toBe(long)
    expect(widthOf(tree)).toBeLessThanOrEqual(40)
  })
})
