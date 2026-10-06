// User intent: the handovers pane as a person uses it — five newest open handovers, copy as Telegram-ready text with the
// absolute path (or the path in a toast when there is no clipboard), the picked one's summary on `i`, resume into the prompt
// without closing the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_SUMMARIES, demoSnapshot } from '../hooks/demo'
import { dateOf, handoverCopyText } from '../hooks/model'
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
/** What a reader sees, top to bottom: every Text, and every Button's label. */
const flatOf = (tree: unknown): string[] =>
  elementsOf(tree).flatMap(e => (e.type === 'Text' ? [textOf(e)] : e.type === 'Button' ? [String(e.props?.label ?? '')] : []))
/** The picked handover's card: the one round-bordered Box. */
const cardOf = (tree: unknown): Drawn => {
  const cards = elementsOf(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round')
  expect(cards).toHaveLength(1)
  return cards[0]!
}

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

  test('the picked handover is a raised round card: head row, NEXT, i toggle; the other rows stay plain, no Next line under the list', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    const tree = await ui.drawn()
    const cards = elementsOf(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round')
    expect(cards).toHaveLength(1)
    const card = cards[0]!
    expect(card.props?.borderColor).toBe('#4d4c48') // rr-stub's border.strong, as on the review card
    const inCard = elementsOf(card)
    const keysIn = inCard.map(e => e.props?.key)
    expect(keysIn).toContain(`ho:${FIRST.id}`)
    expect(keysIn).toContain('summary')
    expect(keysIn).not.toContain(`ho:${SECOND.id}`)
    expect(flatOf(card)).toEqual(['→', `${dateOf(FIRST.created)}  ${FIRST.tldr}`, 'NEXT', FIRST.nextAction, '▸ summary'])
    // Other rows are plain list rows around the card, still buttons; nothing reads `Next:` any more.
    const keys = elementsOf(tree).map(e => e.props?.key)
    expect(keys.indexOf(`ho:${SECOND.id}`)).toBeGreaterThan(keys.indexOf('summary'))
    expect(textsOf(tree).filter(t => t.startsWith('Next'))).toEqual([])
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props).toMatchObject({ hotkey: 'i', label: '▸ summary' })
  })

  test('i expands the card: dim refs, DECISIONS and BLOCKERS labels with their items, then NEXT; the tldr is not repeated; i collapses it', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: 'summary' })
    expect((await ui.find({ type: 'Button', key: 'summary' }))?.props.label).toBe('▾ summary')
    const summary = DEMO_SUMMARIES[FIRST.id]!
    expect(summary.decisions.length).toBeGreaterThan(0)
    expect(summary.blockers.length).toBeGreaterThan(0)
    const card = cardOf(await ui.drawn())
    expect(flatOf(card)).toEqual([
      '→',
      `${dateOf(FIRST.created)}  ${FIRST.tldr}`,
      `${FIRST.branch} · ${FIRST.taskIds.join(', ')}`,
      'DECISIONS',
      ...summary.decisions,
      'BLOCKERS',
      ...summary.blockers,
      'NEXT',
      FIRST.nextAction,
      '▾ summary',
    ])
    // A blank row before each section and before NEXT.
    const tops = elementsOf(card).filter(e => e.props?.marginTop === 1).map(e => flatOf(e)[0])
    expect(tops).toEqual(['DECISIONS', 'BLOCKERS', 'NEXT'])
    await ui.press({ key: 'summary' })
    expect(flatOf(cardOf(await ui.drawn()))).toEqual(['→', `${dateOf(FIRST.created)}  ${FIRST.tldr}`, 'NEXT', FIRST.nextAction, '▸ summary'])
  })

  test('an empty part is left out: no tasks leaves the branch alone, no blockers drops the BLOCKERS section', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(THIRD.taskIds).toEqual([])
    expect(DEMO_SUMMARIES[THIRD.id]?.blockers).toEqual([])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: `ho:${THIRD.id}` })
    await ui.press({ key: 'summary' })
    const shown = flatOf(cardOf(await ui.drawn()))
    expect(shown).toContain(THIRD.branch)
    expect(shown).toContain('DECISIONS')
    expect(shown).not.toContain('BLOCKERS')
  })

  test('the card and the toggle follow the pick: another row is the card, collapsed, and coming back finds it collapsed too', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    await ui.press({ key: 'summary' })
    expect(flatOf(cardOf(await ui.drawn()))).toContain('DECISIONS')
    await ui.press({ key: `ho:${SECOND.id}` })
    expect(flatOf(cardOf(await ui.drawn()))).toEqual(['→', `${dateOf(SECOND.created)}  ${SECOND.tldr}`, 'NEXT', SECOND.nextAction, '▸ summary'])
    await ui.press({ key: `ho:${FIRST.id}` })
    expect(flatOf(cardOf(await ui.drawn()))).toEqual(['→', `${dateOf(FIRST.created)}  ${FIRST.tldr}`, 'NEXT', FIRST.nextAction, '▸ summary'])
  })

  test('a demo snapshot written before a reload (handovers without branch / taskIds) still draws, summary open', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const old = demoSnapshot(0)
    // What the module before DEMO_SEED 2 left in $.state: reason 'demo', handovers without branch / taskIds, no summaries.
    const stale = { ...old, reason: 'demo', handovers: old.handovers.map(({ branch: _b, taskIds: _t, ...rest }) => rest) }
    stateOf(on, { [`${PLUGIN}.snapshot`]: stale, [`${PLUGIN}.summaryOpen`]: FIRST.id })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    expect(await ui.find({ type: 'Text', text: 'HANDOVERS' })).toBeDefined()
    const shown = flatOf(cardOf(await ui.drawn()))
    expect(shown).toContain(`${FIRST.branch} · ${FIRST.taskIds.join(', ')}`)
    expect(shown).toContain(DEMO_SUMMARIES[FIRST.id]!.decisions[0])
  })

  test('a real handover with no branch or task_ids draws its card without a refs line', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    const { branch: _b, taskIds: _t, ...bare } = FIRST
    const real = { ...demoSnapshot(0), reason: '', handovers: [bare] }
    stateOf(on, { [`${PLUGIN}.snapshot`]: real, [`${PLUGIN}.summaryOpen`]: FIRST.id })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...HANDOVERS_PANE })
    expect(await ui.find({ type: 'Text', text: 'HANDOVERS' })).toBeDefined()
    expect(flatOf(cardOf(await ui.drawn())).slice(0, 3)).toEqual(['→', `${dateOf(FIRST.created)}  ${FIRST.tldr}`, 'loading summary…'])
  })

  test('before decisions and blockers arrive the card says "loading summary…"; a long tldr wraps whole in the head and every line fits', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    const long = 'Store write hang root cause found and an incremental rebuild verified in scratch against the CodeMaestro backlog'
    const real: TmSnapshot = { ...demoSnapshot(0), reason: '', handovers: [{ ...FIRST, tldr: long }] }
    stateOf(on, { [`${PLUGIN}.snapshot`]: real, [`${PLUGIN}.summaryOpen`]: FIRST.id })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...wide(40) })
    const tree = await ui.drawn()
    const shown = flatOf(cardOf(tree))
    expect(shown).toContain('loading summary…')
    expect(shown).not.toContain('DECISIONS')
    // The head is the whole tldr, wrapped over several lines (the first is the row button), none cut short.
    const head = shown.slice(1, shown.indexOf('loading summary…') - 1)
    expect(head.length).toBeGreaterThan(1)
    expect(head.join(' ')).toBe(`${dateOf(FIRST.created)}  ${long}`)
    expect(widthOf(tree)).toBeLessThanOrEqual(40)
  })
})
