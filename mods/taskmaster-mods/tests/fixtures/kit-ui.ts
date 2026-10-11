// User intent: draw kit primitives in a test without a session — the engine's own plain-data trees (h with a tag name),
// a Kit over the stand-in tokens, and small finders — so each primitive is checked on its own.
import type { RenderSurface } from 'claude-code'

import type { Kit, Ui } from '../../hooks/kit'
import type { RrGround, RrTextProps, RrTone } from '../../hooks/rr'
import { isEl, type Drawn } from './measure'
import { STUB_TOKENS, stubBadge } from './tokens'

const tag = (type: 'Box' | 'Text' | 'Button' | 'Input' | 'Select') => (props: Record<string, unknown>) => {
  const { children, ...rest } = props
  const kids = children === undefined ? [] : Array.isArray(children) ? children : [children]
  return h(type as 'Box', rest, ...kids)
}

export const FAKE_UI = { Box: tag('Box'), Text: tag('Text'), Button: tag('Button'), Input: tag('Input'), Select: tag('Select') } as unknown as Ui

const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const table = (strength: 12 | 24): Record<RrGround, Record<RrTone, RrTextProps>> =>
  Object.fromEntries(GROUNDS.map(on => [on, Object.fromEntries(TONES.map(tone => [tone, stubBadge({ tone, strength, on })]))])) as Record<
    RrGround,
    Record<RrTone, RrTextProps>
  >

/** A Kit over STUB_TOKENS; `said` collects what a dim keycap would toast. */
export function testKit(surface: RenderSurface = 'terminal', said: string[] = []): Kit {
  return { ui: FAKE_UI, t: STUB_TOKENS, surface, badges: { 12: table(12), 24: table(24) }, say: text => void said.push(text) }
}

/** Every element of a tree, outermost first, including what a mapped list nested. */
export function walk(n: unknown): Drawn[] {
  if (Array.isArray(n)) return n.flatMap(walk)
  if (!isEl(n)) return []
  return [n, ...(n.children ?? []).flatMap(walk)]
}

export const byKey = (n: unknown, key: string): Drawn | undefined => walk(n).find(e => e.props?.key === key)

/** The text of every Text element, in order. */
export const textsOf = (n: unknown): string[] =>
  walk(n)
    .filter(e => e.type === 'Text')
    .map(e => (e.children ?? []).filter((c): c is string => typeof c === 'string').join(''))

/** Every `hover` the tree sets (hoisted out of props by the engine). */
export const hoversOf = (n: unknown): Record<string, unknown>[] =>
  walk(n).flatMap(e => {
    const hover = (e as Drawn & { hover?: Record<string, unknown> }).hover
    return hover === undefined ? [] : [hover]
  })
