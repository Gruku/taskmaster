// User intent: /tm-kit — the visual rework's component sheet (spec §15.2, prototype Main.dc.html) drawn with the real
// vocabulary and sample data (demo source only): each element once, its rule beside it, so the parts are judged by eye.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import { demoSnapshot } from './demo'
import { badge, type Kit, type Piece, type StripHandlers, type StripView, TM_TAG } from './kit'
import { sidebarView } from './nav'
import {
  actionKey,
  bulkRow,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  glyphBadge,
  hints,
  item,
  itemRule,
  line,
  offlineRow,
  pagerPiece,
  paneToolbar,
  phasePicker,
  refreshPiece,
  sectionRow,
  sidebar,
  summaryRow,
  togglePiece,
} from './vocab'

export const KIT = 'tm-kit'
const NAME_W = 12

/** One sheet entry: the name, the element (one or more rows), and its rule in subtle ink. */
function entry(k: Kit, name: string, rows: readonly RenderNode[], rule: string, width: number): RenderNode {
  const { Box, Text } = k.ui
  return (
    <Box flexDirection="row" columnGap={2} alignItems="flex-start">
      <Text color={k.t.fg.bold} bold>
        {name.padEnd(NAME_W)}
      </Text>
      <Box flexDirection="column" width={Math.min(70, Math.max(30, width - NAME_W - 34))}>
        {[...rows]}
      </Box>
      <Text color={k.t.fg.subtle} wrap="truncate-end">
        {rule}
      </Text>
    </Box>
  )
}

/** The component sheet. `say(id)` answers every sample key; the picker moves the review phase for real (demo state only). */
export function kitGalleryTree(k: Kit, strip: StripView, on: StripHandlers, say: (id: string) => void, width: number): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const w = Math.min(70, Math.max(30, width - NAME_W - 34))
  const press = (id: string) => () => say(id)
  const key = (id: string, letter: string, label: string, extra: { rung?: 'primary' | 'secondary' | 'destructive'; dim?: string } = {}): Piece =>
    actionKey(k, { id, key: letter, label, onPress: press(id), ...extra })
  const head = { mark: { key: 'sel:docs-012', selected: false, onPress: press('sel:docs-012') }, badge: 'ACTION', ident: 'docs-012', title: 'README mentions the review queue', age: '30h' }
  const chip = badge(k, TM_TAG, 'signature', { on: 'raised' })
  const nav = sidebarView({ current: 'session', snapshot: demoSnapshot(Date.UTC(2026, 9, 12, 12)), restore: null, now: Date.UTC(2026, 9, 12, 12) })
  const rows: RenderNode[] = [
    entry(k, 'FRAME', [line(k, [{ text: '╭─ ', color: t.border.default }, { text: '◆ ', color: t.signatureText }, { text: 'TASKMASTER · ', color: t.fg.subtle }, { text: 'REVIEW', color: t.fg.bold, bold: true }, { text: ' ─── ● tm ok ─╮', color: t.border.default }], w).el], 'no tab row; pane named in title', width),
    entry(
      k,
      'BAND',
      [
        <Box flexDirection="row" columnGap={2} backgroundColor={t.surface.raised}>
          {chip.el}
          {glyphBadge(k, 'IN REVIEW').el}
          <Text color={t.fg.bold} bold>
            Unified chat…
          </Text>
          {key('g-band-d', 'd', 'mark task done').el}
        </Box>,
      ],
      'subject left; keys in a right column',
      width,
    ),
    entry(k, 'SIDEBAR', [sidebar(k, nav, pane => say(`nav:${pane}`))], 'm opens; key · pane · note · count', width),
    entry(k, 'TOOLBAR', [paneToolbar(k, { left: phasePicker(k, strip, on), right: [togglePiece(k, { id: 'g-toggle', label: '◐ show closed', on: false, onPress: press('g-toggle') }), pagerPiece(k, { page: 0, pages: 2, onTurn: () => say('page') }), refreshPiece(k, { label: 'refresh', onPress: press('refresh') })], width: w })], 'row 1: phase left; toggle, pager, u', width),
    entry(k, 'SUMMARY', [summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }], w)], 'row 2: sentence, then tallies', width),
    entry(k, 'BULK', [bulkRow(k, { keys: [key('g-bulk-off', 'b', 'mark 0 tasks done', { dim: 'Nothing selected' }), key('g-bulk', 'g', 'send 2 tasks to agent')], scopes: [{ id: 'g-all', label: 'all', onPress: press('g-all') }, { id: 'g-none', label: 'none', onPress: press('g-none') }], width: w })], 'row 3: count in the label; dim at 0', width),
    entry(k, 'SECTION', [sectionRow(k, 'HANDED OFF', '1 · open handover', w)], 'label column, dim note', width),
    entry(k, 'BADGE', [<Box flexDirection="row" columnGap={2}>{['BLOCKED', 'ACTION', 'IN REVIEW'].map(word => glyphBadge(k, word).el)}</Box>], 'symbol + word, semantic fg', width),
    entry(k, 'ITEM', [...item(k, head, [[{ text: 'patch-1-9 · lane light', color: t.fg.subtle }], fieldSegs(k, 'human', 'Proof-read the paragraph')], w), itemRule(k, w)], 'title / context / detail; ┄; no keys', width),
    entry(k, 'CARD', [focusedItem(k, { id: 'g-card', head: { ...head, mark: null, badge: 'BLOCKED', ident: 'uc-022', title: 'Unified chat pre-build', age: '1h' }, lines: [[{ text: 'patch-1-9 · lane full', color: t.fg.subtle }]], actions: [<Box flexDirection="row" columnGap={3}>{key('g-card-d', 'w', 'mark task done', { rung: 'primary' }).el}{key('g-card-a', 'a', 'send to agent').el}</Box>] }, w)], 'focused only: box + action row', width),
    entry(k, 'FIELD', [line(k, fieldSegs(k, 'next', 'Record the merge'), w).el], 'lowercase label beside its value', width),
    entry(k, 'SELECTION', [line(k, [{ text: '■ ', color: t.border.focus }, { text: 'picked   ', color: t.fg.default }, { text: '□ ', color: t.fg.disabled }, { text: 'not', color: t.fg.subtle }], w).el], 'mark column only', width),
    entry(k, 'KEY 1', [key('g-primary', 'e', 'mark task done', { rung: 'primary' }).el], 'primary: one fill per context', width),
    entry(k, 'KEY 2', [key('g-secondary', 'c', 'copy handover').el], 'secondary: lit letter, plain label', width),
    entry(k, 'KEY 3', [hints(k, [{ keys: 'j/k', word: 'move' }, { keys: 'p/n', word: 'page' }], w)], 'nav/meta: footer only, never capped', width),
    entry(k, 'KEY 4', [key('g-disabled', 'q', 'close 0 handovers', { dim: 'Nothing selected to close' }).el], 'disabled: whole cap dim', width),
    entry(k, 'KEY 5', [key('g-destructive', 'x', 'close handover', { rung: 'destructive' }).el], 'destructive: warning letter, y/n, last', width),
    entry(k, 'LABELS', [line(k, [{ text: 'c', color: t.signatureText, bold: true }, { text: ' copy handover', color: t.fg.default }, { text: '  not  ', color: t.fg.disabled }, { text: 'c copy', color: t.border.strong }], w).el], 'verb + object, beside its subject', width),
    entry(k, 'FOOTER', [footer(k, [{ keys: 'j/k', word: 'move', press: [{ key: 'j', onPress: press('key:j') }, { key: 'k', onPress: press('key:k') }] }, { keys: 's', word: 'select', press: [{ key: 's', onPress: press('key:s') }] }, { keys: 'm', word: 'sidebar' }], w)], 'last row; same order everywhere', width),
    entry(k, 'EMPTY', [emptyRow(k, 'Nothing waiting in Patch 1.8 · done 10-01', key('g-empty', '0', 'show all phases'), w)], 'sentence + one way out', width),
    entry(k, 'OFFLINE', [offlineRow(k, 'last ok 2m ago', key('g-retry', 'r', 'retry tm connection'), w)], 'critical word + retry', width),
  ]
  return (
    <Box flexDirection="column" rowGap={1} paddingX={1}>
      {rows}
    </Box>
  )
}
