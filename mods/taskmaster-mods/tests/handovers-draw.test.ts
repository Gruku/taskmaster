// User intent: pin how the handovers pane draws (spec §9) — toolbar slots, the strip with its no task tab, the tally and the
// Close bar, one card per handover with only the focused one expanded (NEXT, linked tasks with status and phase, r w c i x),
// closed ones dimmed on demand, the y/n rows, and every row within the pane's width.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_SUMMARIES, demoHandoverList, demoPhases, demoSnapshot } from '../hooks/demo'
import { FRESH_MARKS } from '../hooks/handover-list'
import { type HandoversHandlers, type HandoversView, handoversPaneTree } from '../hooks/handovers'
import { FRESH_PHASE_VIEW, NO_TASK } from '../hooks/phases'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'

const HOUR = 3_600_000
const NOW = 200 * HOUR
const SNAP = demoSnapshot(NOW)
const LIST = demoHandoverList(NOW)
const [FIRST, SECOND] = SNAP.handovers
const noop = (): void => undefined
const ON: HandoversHandlers = {
  pick: noop,
  move: noop,
  turnPage: noop,
  select: noop,
  selectOnly: noop,
  toggleSummary: noop,
  resume: noop,
  newTab: noop,
  copy: noop,
  askClose: noop,
  confirmClose: noop,
  cancelClose: noop,
  toggleClosed: noop,
  refresh: noop,
  strip: { choose: noop, toggleDone: noop, overlay: noop },
}
const view = (over: Partial<HandoversView> = {}): HandoversView => ({
  snapshot: SNAP,
  list: LIST,
  marks: FRESH_MARKS,
  phaseView: { ...FRESH_PHASE_VIEW, phases: demoPhases('handovers'), loaded: true },
  pick: '',
  page: 0,
  summaries: DEMO_SUMMARIES,
  summaryOpen: '',
  now: NOW,
  focused: true,
  ...over,
})
const draw = (over: Partial<HandoversView> = {}, width = 72) => handoversPaneTree(testKit(), view(over), ON, width)
const keys = (tree: unknown): string[] => walk(tree).flatMap(e => (typeof e.props?.key === 'string' ? [e.props.key] : []))
const cards = (tree: unknown) => walk(tree).filter(e => typeof e.props?.key === 'string' && e.props.key.startsWith('card:'))

describe('handovers pane', () => {
  test('toolbar: the pager, ○ closed and u refresh; the strip has All and a no task tab with open counts', () => {
    const tree = draw({}, 120)
    expect(textsOf(tree)[0]).toBe('HANDOVERS')
    expect(byKey(tree, 'page-prev')?.props).toMatchObject({ hotkey: 'p', dimColor: true })
    expect(byKey(tree, 'page-next')?.props).toMatchObject({ hotkey: 'n', dimColor: true })
    expect(byKey(tree, 'show-closed')?.props).toMatchObject({ label: '○ closed' })
    expect(byKey(tree, 'show-closed')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'refresh')?.props).toMatchObject({ hotkey: 'u', label: 'refresh' })
    expect(byKey(tree, 'phase-tab:')?.props).toMatchObject({ hotkey: '0', label: `All ${SNAP.handoversTotal}` })
    expect(byKey(tree, `phase-tab:${NO_TASK}`)?.props?.label).toBe('no task 2')
    expect(byKey(tree, 'phase-tab:patch-1-9')?.props?.label).toBe('Patch 1.9 · active 2')
  })

  test('the tally and the Close bar (dim with nothing selected, all · none); five cards, the first focused and bordered', () => {
    const tree = draw()
    expect(textsOf(tree)).toContain('5 open · newest 2h ago')
    expect(byKey(tree, 'close-selected')?.props).toMatchObject({ hotkey: 'b', label: 'Close 0', dimColor: true })
    expect(byKey(tree, 'select-all')?.props?.label).toBe('all')
    expect(byKey(tree, 'select-none')?.props?.label).toBe('none')
    expect(cards(tree).map(c => c.props?.key)).toEqual(SNAP.handovers.map(x => `card:${x.id}`))
    expect(cards(tree).map(c => c.props?.borderStyle)).toEqual(['round', undefined, undefined, undefined, undefined])
    expect(byKey(tree, `ho:${FIRST!.id}`)?.props?.label).toBe(`2h ago · ${FIRST!.tldr}`)
  })

  test('the focused card: s on its box only, NEXT, the refs line, and r w c i x as keycaps', () => {
    const tree = draw()
    expect(byKey(tree, `ho-sel:${FIRST!.id}`)?.props).toMatchObject({ hotkey: 's', label: '□' })
    expect(byKey(tree, `ho-sel:${SECOND!.id}`)?.props?.hotkey).toBeUndefined()
    const focused = cards(tree)[0]
    expect(textsOf(focused)).toContain('NEXT')
    expect(textsOf(focused)).toContain(FIRST!.nextAction)
    expect(textsOf(focused)).toContain(`${FIRST!.id} · ${FIRST!.branch}`)
    for (const [key, hotkey, text] of [
      ['resume', 'r', 'resume here'],
      ['new-tab', 'w', 'new tab'],
      ['copy', 'c', 'copy'],
      ['summary', 'i', '▸ decisions·blockers'],
      ['close', 'x', 'close'],
    ] as const) {
      expect(byKey(focused, key)?.props, key).toMatchObject({ hotkey, label: text })
    }
    expect(textsOf(cards(tree)[1])).not.toContain('NEXT')
  })

  test('linked tasks: a status badge, the id, the title and the phase name at the right', () => {
    const tree = draw({ pick: SECOND!.id })
    const texts = textsOf(cards(tree)[1])
    expect(texts).toEqual(expect.arrayContaining([' DONE ', 'tm-audit-030', 'Agent tool-use audit fixes', 'Patch 1.9']))
    expect(texts).toEqual(expect.arrayContaining([' IN REVIEW ', 'tm-audit-031']))
  })

  test('a linked task not read shows a dim ? and says so', () => {
    const tree = draw({ pick: SECOND!.id, list: { ...LIST, tasks: {} } })
    const texts = textsOf(cards(tree)[1])
    expect(texts).toEqual(expect.arrayContaining([' ? ', 'tm-audit-030', 'not read']))
  })

  test('i open: DECISIONS and BLOCKERS before NEXT; loading and unavailable say so', () => {
    const open = textsOf(cards(draw({ summaryOpen: FIRST!.id }))[0])
    expect(open.indexOf('DECISIONS')).toBeLessThan(open.indexOf('NEXT'))
    expect(open).toContain(DEMO_SUMMARIES[FIRST!.id]!.blockers[0])
    expect(byKey(draw({ summaryOpen: FIRST!.id }), 'summary')?.props?.label).toBe('▾ decisions·blockers')
    expect(textsOf(cards(draw({ summaryOpen: FIRST!.id, summaries: {} }))[0])).toContain('loading summary…')
    const gone = { [FIRST!.id]: { decisions: [], blockers: [], unavailable: true as const } }
    expect(textsOf(cards(draw({ summaryOpen: FIRST!.id, summaries: gone }))[0])).toContain('summary unavailable')
  })

  test('a phase narrows the cards; an empty phase says so and offers 0 All', () => {
    const phaseView = { ...FRESH_PHASE_VIEW, phases: demoPhases('handovers'), loaded: true }
    const release = draw({ phaseView: { ...phaseView, choice: 'release-2' } })
    expect(cards(release).map(c => c.props?.key)).toEqual(['card:2026-10-02-store-read-hang'])
    const empty = draw({ phaseView: { ...phaseView, choice: 'patch-1-8' } })
    expect(cards(empty)).toEqual([])
    expect(textsOf(empty)).toContain('No open handovers in Patch 1.8 · done 2026-10-05')
    expect(byKey(empty, 'phase-all')?.props).toMatchObject({ hotkey: '0', label: 'All' })
  })

  test('◐ closed: closed handovers join, dimmed with a CLOSED badge, x dim on them; tabs and All count them', () => {
    const marks = { ...FRESH_MARKS, showClosed: true }
    const tree = draw({ marks, page: 1, pick: '2026-09-30-row-counter-ported' }, 120)
    expect(byKey(tree, 'show-closed')?.props?.label).toBe('◐ closed')
    expect(byKey(tree, 'phase-tab:')?.props?.label).toBe(`All ${SNAP.handoversTotal + 2}`)
    expect(byKey(tree, `phase-tab:${NO_TASK}`)?.props?.label).toBe('no task 3')
    expect(cards(tree).map(c => c.props?.key)).toEqual(['card:2026-09-30-row-counter-ported', 'card:2026-09-29-store-spike-notes'])
    expect(byKey(tree, 'ho:2026-09-30-row-counter-ported')?.props?.dimColor).toBe(true)
    expect(textsOf(tree)).toContain(' CLOSED ')
    expect(byKey(tree, 'close')?.props?.dimColor).toBe(true)
    expect(byKey(tree, 'ho-sel:2026-09-30-row-counter-ported')?.props?.dimColor).toBe(true)
    expect(textsOf(tree)).toContain('5 open · 2 closed · newest 2h ago')
  })

  test('selection: a selected card steps up a surface, Close N counts it, the bulk y/n row asks under the bar', () => {
    const marks = { ...FRESH_MARKS, selected: [FIRST!.id, SECOND!.id] }
    const tree = draw({ marks })
    expect(byKey(tree, 'close-selected')?.props?.label).toBe('Close 2')
    expect(byKey(tree, `ho-sel:${FIRST!.id}`)?.props?.label).toBe('■')
    const bg = (i: number) => cards(tree)[i]?.props?.backgroundColor
    expect(bg(0)).toBe(bg(1))
    expect(bg(0)).not.toBe(bg(2))
    const asked = draw({ marks: { ...marks, ask: { kind: 'bulk', ids: [FIRST!.id, SECOND!.id] } } })
    expect(textsOf(asked)).toContain(' Close 2 handovers? ')
    expect(byKey(asked, 'bulk-close-yes')?.props).toMatchObject({ hotkey: 'y' })
    expect(byKey(asked, 'bulk-close-no')?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    const k = keys(asked)
    expect(k.indexOf('bulk-close-no')).toBeGreaterThan(k.indexOf('page-next'))
  })

  test('x asks on the card itself: y/n replaces the keys; the bulk row is not drawn', () => {
    const tree = draw({ marks: { ...FRESH_MARKS, ask: { kind: 'one', ids: [FIRST!.id] } } })
    const focused = cards(tree)[0]
    expect(textsOf(focused)).toContain(` Close ${FIRST!.id}? `)
    expect(byKey(focused, 'close-yes')?.props).toMatchObject({ hotkey: 'y' })
    expect(byKey(focused, 'close-no')?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect(byKey(focused, 'resume')).toBeUndefined()
    expect(byKey(tree, 'bulk-close-yes')).toBeUndefined()
  })

  test('j / k keycaps in the footer, dim at the ends; the help line names the keys', () => {
    const top = draw()
    expect(byKey(top, 'move-down')?.props).toMatchObject({ hotkey: 'j', label: '↓' })
    expect(byKey(top, 'move-down')?.props?.dimColor).toBeUndefined()
    expect(byKey(top, 'move-up')?.props).toMatchObject({ hotkey: 'k', label: '↑', dimColor: true })
    expect(textsOf(top).at(-1)).toContain('s select · b close selected · h/l phase · 0 all')
    const last = SNAP.handovers.at(-1)!.id
    expect(byKey(draw({ pick: last }), 'move-down')?.props?.dimColor).toBe(true)
  })

  test('before data, while connecting, and with tm down: the title and what is going on; u retries', () => {
    expect(textsOf(draw({ snapshot: null, list: null }))).toEqual(['HANDOVERS', 'Loading…'])
    expect(textsOf(draw({ snapshot: null, connecting: true }))).toEqual(['HANDOVERS', 'Connecting to Taskmaster…'])
    const down = draw({ snapshot: { ...SNAP, reachable: false, reason: 'tm: Connection closed' } })
    expect(textsOf(down)).toEqual(expect.arrayContaining(['HANDOVERS', ' Taskmaster unreachable', '  tm: Connection closed']))
    expect(byKey(down, 'refresh')?.props).toMatchObject({ hotkey: 'u', label: 'retry' })
  })

  test('before its own read the pane draws the snapshot window; every row fits the pane at 72 and 50 columns', () => {
    expect(cards(draw({ list: null }))).toHaveLength(5)
    for (const width of [72, 50]) {
      for (const over of [{}, { pick: SECOND!.id, summaryOpen: SECOND!.id }, { marks: { ...FRESH_MARKS, showClosed: true } }]) {
        expect(widthOf({ type: 'Box', props: { flexDirection: 'column' }, children: draw(over, width) }), `${width}`).toBeLessThanOrEqual(width)
      }
    }
  })
})
