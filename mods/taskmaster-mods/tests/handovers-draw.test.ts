// User intent: pin how the handovers pane draws (spec §9, §15) — row 1 (picker with its no task row, ◐ show closed, pager, u),
// the tally and the bulk close row, three items a page with only the focused one boxed (next, linked tasks with status and
// phase, r w c i x), closed ones dimmed on demand, the y/n rows, and every row within the pane's width.
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
  toggleSidebar: noop,
  goTo: noop,
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
const cards = (tree: unknown) => walk(tree).filter(e => typeof e.props?.key === 'string' && e.props.key.startsWith('card:'))
const picks = (tree: unknown) => walk(tree).filter(e => e.type === 'Button' && String(e.props?.key ?? '').startsWith('ho:')).map(e => e.props?.key)
const overlay = (open = true) => ({ ...FRESH_PHASE_VIEW, phases: demoPhases('handovers'), loaded: true, overlay: open })

describe('handovers pane', () => {
  test('row 1: the picker, ◐ show closed, the pager and u refresh; the open list has All and a no task row with open counts', () => {
    const tree = draw({}, 120)
    expect(textsOf(tree)).toContain('HANDOVERS')
    expect(byKey(tree, 'page-prev')?.props).toMatchObject({ dimColor: true })
    expect(byKey(tree, 'page-prev')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'page-next')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'key:p')?.props?.hotkey).toBe('p')
    expect(byKey(tree, 'key:n')?.props?.hotkey).toBe('n')
    expect(byKey(tree, 'show-closed')?.props).toMatchObject({ label: '◐ show closed' })
    expect(byKey(tree, 'show-closed')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'refresh')?.props).toMatchObject({ label: 'u refresh' })
    expect(byKey(tree, 'key:u')?.props?.hotkey).toBe('u')
    expect(byKey(tree, 'phase-field')?.props?.label).toBe(` All  ${SNAP.handoversTotal} ▾ `)
    expect(walk(tree).filter(e => e.type === 'Button' && /^[0-9]$/.test(String(e.props?.hotkey ?? '')))).toEqual([])
    const open = draw({ phaseView: overlay() }, 120)
    expect(byKey(open, 'phase-pick:')?.props?.hotkey).toBe('0')
    expect(byKey(open, `phase-pick:${NO_TASK}`)?.props?.label).toMatch(/^no task +2$/)
    expect(byKey(open, 'phase-pick:patch-1-9')?.props?.label).toMatch(/^Patch 1\.9 · active +2$/)
  })

  test('the tally and the close row (dim with nothing selected, all · none); three items, the first focused and boxed', () => {
    const tree = draw()
    expect(textsOf(tree)).toContain('5 open · newest 2h ago')
    expect(byKey(tree, 'close-selected')?.props).toMatchObject({ hotkey: 'b', label: 'close 0 handovers', dimColor: true })
    expect(byKey(tree, 'select-all')?.props?.label).toBe('all')
    expect(byKey(tree, 'select-none')?.props?.label).toBe('none')
    expect(picks(tree)).toEqual(SNAP.handovers.slice(0, 3).map(x => `ho:${x.id}`))
    expect(cards(tree).map(c => c.props?.key)).toEqual([`card:${FIRST!.id}`])
    expect(cards(tree)[0]?.props?.borderStyle).toBe('round')
    expect(byKey(tree, `ho:${FIRST!.id}`)?.props?.label).toBe(FIRST!.tldr)
    expect(textsOf(tree)).toContain('   2h')
  })

  test("the focused item: no hotkey on any mark (s is the footer's), next, the refs line, and r w c i x as labelled keys", () => {
    const tree = draw({}, 120)
    expect(byKey(tree, `ho-sel:${FIRST!.id}`)?.props).toMatchObject({ label: '□' })
    expect(byKey(tree, `ho-sel:${FIRST!.id}`)?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'key:s')?.props?.hotkey).toBe('s')
    const focused = cards(tree)[0]
    expect(textsOf(focused)).toContain('next  ')
    expect(textsOf(focused)).toContain(FIRST!.nextAction)
    expect(textsOf(focused)).toContain(`${FIRST!.id} · ${FIRST!.branch} · 1 linked task`)
    for (const [key, hotkey, text] of [
      ['resume', 'r', 'resume handover here'],
      ['new-tab', 'w', 'resume in new tab'],
      ['copy', 'c', 'copy handover'],
      ['summary', 'i', 'decisions·blockers ▸'],
      ['close', 'x', 'close handover'],
    ] as const) {
      expect(byKey(focused, key)?.props, key).toMatchObject({ hotkey, label: text })
    }
  })

  test('linked tasks: a status badge, the id, the title and the phase name', () => {
    const tree = draw({ pick: SECOND!.id }, 120)
    const texts = textsOf(cards(tree)[0])
    expect(texts).toEqual(expect.arrayContaining(['✓ DONE', 'tm-audit-030', '  Agent tool-use audit fixes', '  Patch 1.9']))
    expect(texts).toEqual(expect.arrayContaining(['● IN REVIEW', 'tm-audit-031']))
  })

  test('a linked task not read shows an UNKNOWN badge and says so', () => {
    const tree = draw({ pick: SECOND!.id, list: { ...LIST, tasks: {} } }, 120)
    const texts = textsOf(cards(tree)[0])
    expect(texts.some(x => x.includes('UNKNOWN'))).toBe(true)
    expect(texts).toEqual(expect.arrayContaining(['tm-audit-030', '  not read']))
  })

  test('i open: decisions and blockers after next; loading and unavailable say so', () => {
    const open = textsOf(cards(draw({ summaryOpen: FIRST!.id }, 120))[0])
    expect(open.indexOf('decisions')).toBeGreaterThan(open.indexOf('next  '))
    expect(open).toContain(`· ${DEMO_SUMMARIES[FIRST!.id]!.blockers[0]}`)
    expect(byKey(draw({ summaryOpen: FIRST!.id }), 'summary')?.props?.label).toBe('decisions·blockers ▾')
    expect(textsOf(cards(draw({ summaryOpen: FIRST!.id, summaries: {} }))[0])).toContain('loading summary…')
    const gone = { [FIRST!.id]: { decisions: [], blockers: [], unavailable: true as const } }
    expect(textsOf(cards(draw({ summaryOpen: FIRST!.id, summaries: gone }))[0])).toContain('summary unavailable')
  })

  test('a phase narrows the items; an empty phase says so and offers 0 show all phases', () => {
    const phaseView = { ...FRESH_PHASE_VIEW, phases: demoPhases('handovers'), loaded: true }
    const release = draw({ phaseView: { ...phaseView, choice: 'release-2' } })
    expect(picks(release)).toEqual(['ho:2026-10-02-store-read-hang'])
    const empty = draw({ phaseView: { ...phaseView, choice: 'patch-1-8' } }, 120)
    expect(picks(empty)).toEqual([])
    expect(textsOf(empty)).toContain('No open handovers in Patch 1.8 · done 2026-10-05')
    expect(byKey(empty, 'phase-all')?.props).toMatchObject({ hotkey: '0', label: 'show all phases' })
  })

  test('◐ show closed: closed handovers join, faint with ✓ CLOSED, x dim on them; the picker and All count them', () => {
    const marks = { ...FRESH_MARKS, showClosed: true }
    const tree = draw({ marks, page: 1, pick: '2026-09-30-row-counter-ported' }, 120)
    expect(byKey(tree, 'show-closed')?.props?.label).toBe('◐ show closed')
    expect(byKey(tree, 'phase-field')?.props?.label).toBe(` All  ${SNAP.handoversTotal + 2} ▾ `)
    expect(byKey(draw({ marks, phaseView: overlay() }, 120), `phase-pick:${NO_TASK}`)?.props?.label).toMatch(/^no task +3$/)
    expect(picks(tree)).toContain('ho:2026-09-30-row-counter-ported')
    expect(picks(draw({ marks, page: 2 }, 120))).toEqual(['ho:2026-09-29-store-spike-notes'])
    expect(textsOf(tree)).toContain('✓ CLOSED       ')
    expect(byKey(tree, 'close')?.props?.dimColor).toBe(true)
    expect(textsOf(tree)).toContain('5 open · 2 closed · newest 2h ago')
  })

  test('selection: a selected mark is ■, close N counts it, the bulk y/n row asks in place of the close row', () => {
    const marks = { ...FRESH_MARKS, selected: [FIRST!.id, SECOND!.id] }
    const tree = draw({ marks })
    expect(byKey(tree, 'close-selected')?.props?.label).toBe('close 2 handovers')
    expect(byKey(tree, `ho-sel:${FIRST!.id}`)?.props?.label).toBe('■')
    expect(byKey(tree, `ho-sel:${SECOND!.id}`)?.props?.label).toBe('■')
    const asked = draw({ marks: { ...marks, ask: { kind: 'bulk', ids: [FIRST!.id, SECOND!.id] } } })
    expect(textsOf(asked)).toContain('close 2 handovers?')
    expect(byKey(asked, 'bulk-close-yes')?.props).toMatchObject({ hotkey: 'y' })
    expect(byKey(asked, 'bulk-close-no')?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect(byKey(asked, 'key:n')).toBeUndefined()
    expect(byKey(asked, 'close-selected')).toBeUndefined()
  })

  test('x asks on the card itself: y/n replaces the keys; no bulk y/n; the footer drops n', () => {
    const tree = draw({ marks: { ...FRESH_MARKS, ask: { kind: 'one', ids: [FIRST!.id] } } }, 120)
    const focused = cards(tree)[0]
    expect(textsOf(focused)).toContain(`close ${FIRST!.id}?`)
    expect(byKey(focused, 'close-yes')?.props).toMatchObject({ hotkey: 'y' })
    expect(byKey(focused, 'close-no')?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect(byKey(focused, 'resume')).toBeUndefined()
    expect(byKey(tree, 'bulk-close-yes')).toBeUndefined()
    expect(byKey(tree, 'key:n')).toBeUndefined()
  })

  test("j / k are the footer's label-less keys; the hint line names the keys", () => {
    const top = draw({}, 160)
    expect(byKey(top, 'move-down')?.props).toMatchObject({ hotkey: 'j', label: '' })
    expect(byKey(top, 'move-up')?.props).toMatchObject({ hotkey: 'k', label: '' })
    expect(textsOf(top).join('')).toContain('j/k move · s select · b close selected · h/l phase · f phases · p/n page · u refresh · m sidebar · esc close')
  })

  test('before data, while connecting, and with tm down: the frame and what is going on; u retries', () => {
    expect(textsOf(draw({ snapshot: null, list: null }))).toContain('Loading…')
    expect(textsOf(draw({ snapshot: null, connecting: true }))).toContain('Connecting to Taskmaster…')
    const down = draw({ snapshot: { ...SNAP, reachable: false, reason: 'tm: Connection closed' } })
    expect(textsOf(down)).toEqual(expect.arrayContaining(['HANDOVERS', '▲ tm offline', ' · tm: Connection closed']))
    expect(byKey(down, 'refresh')?.props).toMatchObject({ hotkey: 'u', label: 'retry tm connection' })
  })

  test('before its own read the pane draws the snapshot window; every row fits the pane at 72 and 50 columns', () => {
    expect(picks(draw({ list: null }))).toHaveLength(3)
    for (const width of [72, 50]) {
      for (const over of [{}, { pick: SECOND!.id, summaryOpen: SECOND!.id }, { marks: { ...FRESH_MARKS, showClosed: true } }]) {
        expect(widthOf(draw(over, width)), `${width}`).toBeLessThanOrEqual(width)
      }
    }
  })
})
