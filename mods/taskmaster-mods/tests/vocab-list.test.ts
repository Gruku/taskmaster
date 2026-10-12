// User intent: pin the list and frame elements of the rework (spec §15.6–15.7) — the 3-line item in its label column, the
// focused card's box, the dim rule between items, the pane frame with its title and health, the toolbar, summary, bulk,
// section, empty and offline rows — each exactly as wide as its room.
import { describe, expect, test } from 'claude-code/testing'

import {
  actionKey,
  bulkRow,
  CONTENT_COL,
  emptyRow,
  fieldSegs,
  focusedItem,
  item,
  itemHead,
  itemRule,
  labelRow,
  listing,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  refreshPiece,
  sectionRow,
  summaryRow,
  togglePiece,
} from '../hooks/vocab'
import { LIST_PAGE_SIZE } from '../hooks/model'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const noop = (): void => undefined
const HEAD = { mark: { key: 'sel:docs-012', selected: false, onPress: noop }, badge: 'ACTION', ident: 'docs-012', title: 'README mentions the review queue', age: '30h' }

describe('the list item', () => {
  test('a head row is mark, padded badge, bold id, title, age right-aligned: exactly its width', () => {
    const k = testKit()
    const row = itemHead(k, HEAD, 80)
    expect(widthOf(row)).toBe(80)
    expect(textsOf(row)).toEqual(['□', '◆ ACTION       ', 'docs-012  ', 'README mentions the review queue', ' '.repeat(80 - 2 - 15 - 10 - 32 - 5), '  30h'])
    expect(byKey(row, 'sel:docs-012')?.props).toMatchObject({ plain: true, label: '□' })
    expect(byKey(row, 'sel:docs-012')?.props?.hotkey).toBeUndefined()
  })

  test('no ident: the title is bold; a long title is cut; meta sits before the age; dim draws faint', () => {
    const k = testKit()
    const row = itemHead(k, { mark: null, badge: 'OPEN', title: 'Shipped unified-chat-022 to dev and wrote it all down', meta: 'main', age: '2h' }, 50)
    expect(widthOf(row)).toBe(50)
    const title = walk(row).find(e => e.type === 'Text' && String(e.children?.[0]).startsWith('Shipped'))
    expect(title?.props).toMatchObject({ color: T.fg.bold, bold: true })
    expect(String(title?.children?.[0]).endsWith('…')).toBe(true)
    expect(textsOf(row)).toContain('main   ')
    const dim = itemHead(k, { ...HEAD, dim: true }, 60)
    // The first inked Text is the selection mark (its own ink); every one after it is faint.
    const inked = walk(dim).filter(e => e.type === 'Text' && e.props?.color !== undefined)
    expect(inked.slice(1).every(e => e.props?.color === T.border.strong)).toBe(true)
  })

  test('a selected mark is ■ in the focus ink; an unselectable one is drawn in the faint ink', async () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const on = itemHead(k, { ...HEAD, mark: { key: 'sel:a', selected: true, onPress: noop } }, 60)
    expect(walk(on).find(e => e.type === 'Text' && e.children?.[0] === '■')?.props?.color).toBe(T.border.focus)
  })

  test('detail lines sit in the content column; a field is a signature label and its value', () => {
    const k = testKit()
    const rows = item(k, HEAD, [[{ text: 'patch-1-9 · lane light', color: T.fg.subtle }], fieldSegs(k, 'human', 'Proof-read the paragraph')], 70)
    expect(rows).toHaveLength(3)
    expect(walk(rows[1])[0]?.props?.paddingLeft).toBe(CONTENT_COL)
    expect(textsOf(rows[2])).toEqual(['human  ', 'Proof-read the paragraph'])
    expect(walk(rows[2]).find(e => e.children?.[0] === 'human  ')?.props?.color).toBe(T.signatureText)
    for (const r of rows) expect(widthOf(r)).toBeLessThanOrEqual(70)
  })

  test('the focused item is boxed in the focus border, its actions under a blank row; rules only between collapsed items', () => {
    const k = testKit()
    const actions = [actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })]
    const card = focusedItem(k, { id: 'uc', head: { ...HEAD, ident: 'unified-chat-022' }, lines: [], actions: actions.map(a => a.el) }, 80)
    expect(walk(card)[0]?.props).toMatchObject({ key: 'card:uc', borderStyle: 'round', borderColor: T.border.focus, paddingX: 1 })
    expect(widthOf(card)).toBeLessThanOrEqual(80)
    const collapsed = { focused: false, nodes: item(k, HEAD, [], 80) }
    const rows = listing(k, [{ focused: true, nodes: [card] }, collapsed, collapsed], 80)
    expect(rows.filter(r => textsOf(r).join('').includes('┄'))).toHaveLength(1)
    expect(textsOf(itemRule(k, 10))).toEqual(['  ┄┄┄┄┄┄┄┄'])
    expect(LIST_PAGE_SIZE).toBe(3)
  })
})

describe('the frame and the rows around a list', () => {
  test('the frame: round border in border.default, ◆ TASKMASTER · NAME in the border, the health at the right, no fill but page', () => {
    const k = testKit()
    const frame = paneFrame(k, { name: 'review', health: { kind: 'success', word: 'tm ok' }, width: 72 }, [])
    expect(walk(frame)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default, width: 72, backgroundColor: T.surface.page })
    expect(textsOf(frame)).toEqual(expect.arrayContaining(['◆ ', 'TASKMASTER · ', 'REVIEW', '● ', 'tm ok']))
    const desktop = paneFrame(testKit('desktop'), { name: 'review', width: 72 }, [])
    expect(walk(desktop).some(e => e.props?.position === 'absolute')).toBe(false)
  })

  test('an overlay is drawn last, absolute at the body top-left', () => {
    const k = testKit()
    const frame = paneFrame(k, { name: 'session', width: 72 }, [h('Text', {}, 'body')!], h('Text', {}, 'over')!)
    const kids = walk(frame)[0]?.children ?? []
    const last = kids[kids.length - 1] as { props?: Record<string, unknown> }
    expect(last.props).toMatchObject({ position: 'absolute', top: 0, left: 0 })
  })

  test('the toolbar keeps the left piece and drops toggles before u when narrow; pager arrows have no hotkey', () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const left = { el: h('Text', {}, 'picker')!, width: 30 }
    const right = [togglePiece(k, { id: 'show-closed', label: '◐ show closed', on: false, onPress: noop }), pagerPiece(k, { page: 0, pages: 2, onTurn: noop }), refreshPiece(k, { label: 'refresh', onPress: noop })]
    const wide = paneToolbar(k, { left, right, width: 100 })
    for (const key of ['show-closed', 'page-prev', 'page-next', 'refresh']) expect(byKey(wide, key), key).toBeDefined()
    expect(byKey(wide, 'page-prev')?.props?.hotkey).toBeUndefined()
    expect(textsOf(wide)).toContain(' 1 / 2 ')
    const narrow = paneToolbar(k, { left, right, width: 50 })
    expect(byKey(narrow, 'show-closed')).toBeUndefined()
    expect(byKey(narrow, 'refresh')).toBeDefined()
    expect(textsOf(refreshPiece(k, { label: 'rescan', onPress: noop }).el)).toEqual(['u', ' rescan'])
  })

  test('summary: the sentence, then each tally as its badge and count, a zero one faint; cut to the room', () => {
    const k = testKit()
    const row = summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }, { word: 'IN REVIEW', count: 0 }], 100)
    expect(textsOf(row)).toEqual(['5 waiting on you · newest 1h ago', '▲ BLOCKED', ' 1', '● IN REVIEW', ' 0'])
    expect(walk(row).find(e => e.children?.[0] === ' 0')?.props?.color).toBe(T.border.strong)
    expect(widthOf(summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }], 40))).toBeLessThanOrEqual(40)
  })

  test('bulk: keys at the left, scopes at the right as quiet buttons; section, label, empty and offline rows fit', () => {
    const k = testKit()
    const keys = [actionKey(k, { id: 'bulk-done', key: 'b', label: 'mark 0 tasks done', dim: 'Nothing selected', onPress: noop })]
    const bulk = bulkRow(k, { keys, scopes: [{ id: 'sel-all', label: 'all', onPress: noop }, { id: 'sel-none', label: 'none', onPress: noop }], width: 80 })
    expect(byKey(bulk, 'sel-all')?.props).toMatchObject({ label: 'all', plain: true })
    expect(byKey(bulk, 'sel-all')?.props?.hotkey).toBeUndefined()
    expect(textsOf(sectionRow(k, 'HANDED OFF', '1 · an open handover carries the next step', 80))).toEqual(['  ', 'HANDED OFF', '  1 · an open handover carries the next step'])
    const label = labelRow(k, 'TASK', [{ text: 'unified-chat-022', color: T.fg.bold }], 40)
    expect(textsOf(label)[0]).toBe(`  ${'TASK'.padEnd(17)}`)
    expect(widthOf(label)).toBeLessThanOrEqual(40)
    expect(textsOf(emptyRow(k, 'Nothing waiting in Patch 1.8', null, 60))).toEqual(['Nothing waiting in Patch 1.8'])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: noop })
    const off = offlineRow(k, 'tm: Connection closed', retry, 80)
    expect(textsOf(off).slice(0, 2)).toEqual(['▲ tm offline', ' · tm: Connection closed'])
    expect(widthOf(off)).toBeLessThanOrEqual(80)
  })
})
