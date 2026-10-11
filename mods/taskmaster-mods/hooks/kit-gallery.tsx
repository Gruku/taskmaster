// User intent: /tm-kit — every taskmaster-mods kit primitive on one screen with sample data (demo source only), so the kit
// is judged by eye on the terminal and the desktop before the band and panes are rebuilt on it.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import {
  badge,
  card,
  checkbox,
  confirmRow,
  helpFooter,
  keycap,
  type Kit,
  label,
  pager,
  panel,
  panelTitle,
  phaseStrip,
  row,
  rule,
  selectionBar,
  signal,
  type StripHandlers,
  type StripView,
  toolbar,
} from './kit'
import type { RrTone } from './rr'

export const KIT = 'tm-kit'
const TONE_KEYS: readonly { tone: RrTone; key: string }[] = [
  { tone: 'success', key: 'r' },
  { tone: 'warning', key: 'w' },
  { tone: 'critical', key: 'x' },
  { tone: 'info', key: 'i' },
  { tone: 'signature', key: 'e' },
]

/** `press(id)` is what every sample does (a toast naming it); the strip acts for real on the review pane's demo phases. */
export function kitGalleryTree(k: Kit, strip: StripView, onStrip: StripHandlers, press: (id: string) => void, width: number): RenderNode {
  const { Box, Text } = k.ui
  const w = Math.max(30, width - 2)
  const tap = (id: string) => () => press(id)
  const section = (name: string, ...rows: RenderNode[]): RenderNode => (
    <Box flexDirection="column" marginTop={1}>
      {label(k, name).el}
      {rows}
    </Box>
  )
  const head = (id: string, selected: boolean) =>
    row(k, [checkbox(k, { id: `sel:${id}`, selected, onPress: tap(`select ${id}`) }), badge(k, 'blocked', 'critical'), label(k, id)]).el
  return (
    <Box flexDirection="column">
      {toolbar(k, {
        title: 'kit',
        slots: [pager(k, { page: 0, pages: 3, onTurn: n => press(`turn ${n}`) }), keycap(k, { id: 'g-refresh', key: 'u', label: 'refresh', onPress: tap('refresh') })],
        width: w,
      })}
      {section('phase strip', phaseStrip(k, strip, onStrip, w))}
      {section(
        'keycaps',
        row(k, TONE_KEYS.map(x => keycap(k, { id: `g-${x.tone}`, key: x.key, label: x.tone, tone: x.tone, onPress: tap(x.tone) }))).el,
        row(k, [
          keycap(k, { id: 'g-dim', key: 'z', label: 'dim', dim: 'Nothing to do here', onPress: tap('dim') }),
          keycap(k, { id: 'g-faded', key: 'q', label: 'faded', faded: true, onPress: tap('faded') }),
          keycap(k, { id: 'g-selected', key: 'v', label: 'selected', selected: true, onPress: tap('selected') }),
          keycap(k, { id: 'g-plain', label: 'no key', onPress: tap('plain') }),
        ]).el,
      )}
      {section(
        'badges',
        row(k, TONE_KEYS.map(x => badge(k, x.tone, x.tone))).el,
        row(k, [...TONE_KEYS.map(x => badge(k, x.tone, x.tone, { strength: 12 })), badge(k, '0 waiting', 'warning', { dim: true })]).el,
        row(k, [signal(k, 'success', 'tm ok'), signal(k, 'critical', 'offline', 'tm')], 2).el,
      )}
      {section(
        'panel',
        panel(k, { title: panelTitle('session'), health: { kind: 'success', word: 'tm ok' }, width: w }, [
          <Text color={k.t.fg.default}>A panel body line.</Text>,
          rule(k, w - 4),
          <Text color={k.t.fg.subtle}>No task bound to this session</Text>,
        ]),
        panel(k, { title: panelTitle('restore'), collapse: true, width: w }, []),
      )}
      {section(
        'cards',
        card(k, { id: 'g1', selected: false, focused: true }, head('tm-audit-031', false), [
          <Text color={k.t.fg.default}>human: verify prod row counts</Text>,
        ]),
        card(k, { id: 'g2', selected: true, focused: false }, head('tm-audit-044', true), []),
        card(k, { id: 'g3', selected: false, focused: false }, head('docs-012', false), []),
      )}
      {section(
        'selection',
        selectionBar(k, {
          actions: [
            { id: 'g-bulk-done', verb: 'Done', count: 2, tone: 'success', onPress: tap('bulk done') },
            { id: 'g-bulk-agent', verb: 'To agent', count: 0, tone: 'warning', onPress: tap('bulk agent') },
          ],
          ghosts: [
            { id: 'g-all', label: 'all', onPress: tap('all') },
            { id: 'g-none', label: 'none', onPress: tap('none') },
          ],
          width: w,
        }),
      )}
      {section('confirm', ...confirmRow(k, { id: 'g-confirm', question: 'done tm-audit-031?', onYes: tap('yes'), onNo: tap('no'), width: w }))}
      {helpFooter(k, ['h l phase', 'f phases', '0-9 jump', 'p n page', 'u refresh'], w)}
    </Box>
  )
}
