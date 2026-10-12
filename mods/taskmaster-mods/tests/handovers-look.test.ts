// User intent: the handovers pane as the approved prototype draws it (spec §9, §15.6–15.7, Handovers.dc.html) — row 1 the
// picker with ◐ show closed, the pager and u; the sentence; the bulk close row; 3 items a page (title / slug · branch · linked
// tasks / next), the focused one boxed with r resume handover here filled and x close handover last; a dim footer.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_SUMMARIES, demoHandoverList, demoPhases, demoSnapshot } from '../hooks/demo'
import { FRESH_MARKS } from '../hooks/handover-list'
import { type HandoversHandlers, type HandoversView, handoversPaneTree } from '../hooks/handovers'
import { FRESH_PHASE_VIEW } from '../hooks/phases'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { elementsOf, widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

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
const keysOf = (tree: unknown): Record<string, string[]> => {
  const keys: Record<string, string[]> = {}
  for (const b of walk(tree).filter(e => e.type === 'Button')) if (typeof b.props?.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}

describe('the handovers pane, as drawn', () => {
  test('the frame, row 1, the sentence and the bulk close row', () => {
    const tree = handoversPaneTree(testKit(), view(), ON, 116)
    expect(walk(tree)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default })
    for (const text of ['HANDOVERS', ' All  6 ▾ ', '◐ show closed', ' 1 / 2 ', '5 open · newest 2h ago']) expect(textsOf(tree), text).toContain(text)
    expect(byKey(tree, 'show-closed')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'close-selected')?.props).toMatchObject({ hotkey: 'b', label: 'close 0 handovers', dimColor: true })
  })

  test('3 items a page; the focused one is boxed with its linked tasks and keys; collapsed ones carry none', () => {
    const tree = handoversPaneTree(testKit(), view(), ON, 116)
    expect(walk(tree).filter(e => String(e.props?.key ?? '').startsWith('ho-sel:')).filter(e => e.type === 'Button')).toHaveLength(3)
    expect(walk(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round' && String(e.props?.key ?? '').startsWith('card:')).map(e => e.props?.key)).toEqual([`card:${FIRST!.id}`])
    for (const [key, hotkey, label] of [
      ['resume', 'r', 'resume handover here'],
      ['new-tab', 'w', 'resume in new tab'],
      ['copy', 'c', 'copy handover'],
      ['summary', 'i', 'decisions·blockers ▸'],
      ['close', 'x', 'close handover'],
    ] as const) {
      expect(byKey(tree, key)?.props, key).toMatchObject({ hotkey, label })
    }
    expect(byKey(tree, 'resume-box')?.props?.backgroundColor).toBe(T.keycap.signature.bg)
    expect(walk(tree).find(e => (e.children ?? []).includes(' x '))?.props?.color).toBe(T.tone.warning)
    const texts = textsOf(tree)
    for (const text of ['○ OPEN         ', 'Shipped unified-chat-022 to dev', `${FIRST!.id} · main · 1 linked task`, 'next  ', 'task  ', '● IN REVIEW', 'unified-chat-022']) {
      expect(texts, text).toContain(text)
    }
    expect(byKey(tree, `ho:${SECOND!.id}`)?.props?.hotkey).toBeUndefined()
  })

  test('the footer owns j k s h l f p n u m, no hotkey on two Buttons; a y/n takes n from the footer', () => {
    const keys = keysOf(handoversPaneTree(testKit(), view(), ON, 116))
    for (const [hotkey, id] of [['j', 'move-down'], ['k', 'move-up'], ['s', 'key:s'], ['f', 'key:f'], ['n', 'key:n'], ['m', 'key:m']] as const) expect(keys[hotkey], hotkey).toEqual([id])
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    const asked = keysOf(handoversPaneTree(testKit(), view({ marks: { ...FRESH_MARKS, ask: { kind: 'one', ids: [FIRST!.id] } } }), ON, 116))
    expect(asked.n).toEqual(['close-no'])
  })

  test('a closed handover draws faint with ✓ CLOSED, and its x is disabled', () => {
    const closed = LIST.handovers.find(h => h.status === 'closed')!
    const tree = handoversPaneTree(testKit(), view({ marks: { ...FRESH_MARKS, showClosed: true }, pick: closed.id, page: 2 }), ON, 116)
    expect(textsOf(tree)).toContain('✓ CLOSED       ')
    expect(byKey(tree, 'close')?.props?.dimColor).toBe(true)
  })

  test('every row fits the pane at 60 and 100 columns', () => {
    for (const columns of [60, 100]) {
      const tree = handoversPaneTree(testKit(), view(), ON, columns)
      const rows = elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}`).toBeLessThanOrEqual(columns - 4)
    }
  })

  test('tight (36 columns): every item keeps its title as the ho:<id> pick Button', () => {
    const tree = handoversPaneTree(testKit(), view(), ON, 36)
    for (const ho of SNAP.handovers.slice(0, 3)) expect(byKey(tree, `ho:${ho.id}`)?.type, ho.id).toBe('Button')
  })
})
