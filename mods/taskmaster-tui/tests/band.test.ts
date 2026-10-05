// User intent: the band as a person sees it — composed with other mods' bands, out of a survey's way, sized to the room,
// its digit hotkeys opening the panes and its review form asking before it signs anything off.
import { describe, expect, mock, test } from 'claude-code/testing'

import type { TmSnapshot } from '../types'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }

type Drawn = { type: string; props?: Record<string, unknown>; children?: unknown[] }
const isEl = (n: unknown): n is Drawn => typeof n === 'object' && n !== null && 'type' in n
const num = (v: unknown): number => (typeof v === 'number' ? v : 0)

// Cells a drawn node takes across, laid out as the terminal lays it out: a Box in a row sums its children and gaps, in a
// column takes the widest; padding and borders count; plain Buttons draw `k: label`.
function widthOf(n: unknown): number {
  if (typeof n === 'string') return n.length
  if (typeof n === 'number') return String(n).length
  if (!isEl(n)) return 0
  const p = n.props ?? {}
  const kids = n.children ?? []
  if (p.display === 'none' || p.position === 'absolute') return 0
  if (n.type === 'Text') return kids.reduce<number>((sum, kid) => sum + widthOf(kid), 0)
  if (n.type === 'Button') {
    const label = String(p.label ?? kids.join(''))
    if (p.plain !== true) return label.length + 4
    return typeof p.hotkey === 'string' ? `${p.hotkey}: ${label}`.length : label.length
  }
  const frame =
    2 * num(p.paddingX ?? p.padding) + num(p.paddingLeft) + num(p.paddingRight) + (typeof p.borderStyle === 'string' ? 2 : 0)
  const widths = kids.map(widthOf)
  const column = p.flexDirection === 'column' || p.flexDirection === 'column-reverse'
  if (column) return frame + Math.max(0, ...widths)
  const gap = num(p.columnGap ?? p.gap)
  return frame + widths.reduce((sum, w) => sum + w, 0) + gap * Math.max(0, widths.length - 1)
}

const FAR: TmSnapshot = {
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [{ kind: 'task', id: 'docs-012', title: 'README', priority: 'low', humanAction: 'Read it', timestamp: '' }],
  queueTotal: 355,
  handovers: [],
  handoversTotal: 0,
  bound: {
    taskId: 'far-001',
    inferred: false,
    detail: {
      id: 'far-001',
      title: 'Bound task beyond the loaded window',
      status: 'in-review',
      priority: 'medium',
      lane: 'full',
      gateState: 'review-gate:pass',
      branch: '',
      humanAction: 'Check the far task',
    },
    pipeline: null,
  },
}

describe('band', () => {
  test('draws the bound task and needs-you above whatever the other mods drew', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const tree = JSON.stringify(await $.ui.render(BAND))
    expect(tree).toContain('unified-chat-022')
    expect(tree).toContain('5 waiting on you')
    expect(tree.indexOf('5 waiting on you')).toBeLessThan(tree.indexOf('BELOW'))
  })

  test('yields entirely to a survey', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const tree = JSON.stringify(await $.ui.render({ ...BAND, props: { ...BAND.props, hasSurvey: true } }))
    expect(tree).not.toContain('waiting on you')
    expect(tree).toContain('BELOW')
  })

  test('hidden when nothing is bound and nothing waits', { plugins: [RR_STUB] }, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.ui.render(BAND)).toEqual({ type: 'Text', children: ['BELOW'] })
  })

  test('a 30-column, 2-row band fits every row in 30 cells, keeps the check to do and the two most important rows', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface, props: { ...BAND.props, bodyColumns: 30, maxRows: 2 } })
      const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
      for (const text of texts) {
        expect(text.includes('\n')).toBe(false)
        expect(text.length).toBeLessThanOrEqual(30)
      }
      const drawn = (await ui.drawn()) as unknown as Drawn
      const ours = drawn.children?.[0]
      expect(isEl(ours) ? ours.children?.length : 0).toBe(2)
      for (const row of isEl(ours) ? (ours.children ?? []) : []) expect(widthOf(row), JSON.stringify(row)).toBeLessThanOrEqual(30)
      expect(widthOf(drawn)).toBeLessThanOrEqual(30)
      expect(texts.some(t => /^waiting on you: \S/.test(t)), JSON.stringify(texts)).toBe(true)
      expect(await ui.find({ type: 'Button', key: 'band-review' })).toBeDefined()
      expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeUndefined()
      await ui.unmount()
    }
  })

  test('the wide band shows id, check, and every action as one whole-chip button', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^waiting on you: Live check on dev/ })).toBeDefined()
    for (const [key, hotkey, label] of [
      ['band-done', 'd', 'done'],
      ['band-back', 'a', 'back to agent'],
      ['band-review', '1', 'review'],
      ['band-handovers', '2', 'handovers'],
    ] as const) {
      const wrapper = await ui.find({ type: 'Box', key: `${key}-box` })
      expect(wrapper?.children).toHaveLength(1)
      expect(wrapper?.children[0]).toMatchObject({ type: 'Button', props: { key, hotkey, label, plain: true }, hover: { bold: true } })
    }
  })

  test('1 opens the review queue and 2 the handovers', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-review' })
    await ui.press({ key: 'band-handovers' })
    expect(world.opened).toEqual(['tm-review', 'tm-handovers'])
  })

  test('d asks first, n cancels, y signs the bound task off and the band moves on', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    expect(await ui.find({ type: 'Text', text: /confirm done\?/ })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'band-no' })
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeDefined()
    await ui.press({ key: 'band-done' })
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /4 waiting on you/ })).toBeDefined()
  })

  test('a sends back the bound task even when the loaded queue window does not hold it', { plugins: [RR_STUB] }, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    stateOf(on, { 'taskmaster-tui.snapshot': FAR })
    await $.session.start(SESSION)
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-back' })
    expect(world.opened).toEqual(['tm-review'])
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    expect(await review.find({ type: 'Text', text: 'far-001' })).toBeDefined()
    expect(await review.find({ type: 'Text', text: 'Check the far task' })).toBeDefined()
    await review.input({ key: 'note', text: 'not yet' })
    expect(world.fills).toEqual(['Back to far-001: not yet'])
  })
})
