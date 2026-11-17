// User intent: measure a drawn tree the way the terminal lays it out, so tests can prove a band or pane row fits its room.
export type Drawn = { type: string; props?: Record<string, unknown>; children?: unknown[] }

export const isEl = (n: unknown): n is Drawn => typeof n === 'object' && n !== null && 'type' in n
const num = (v: unknown): number => (typeof v === 'number' ? v : 0)

/** Cells a drawn node takes across: a Box in a row sums its children and gaps, in a column takes the widest; padding and
 * borders count; plain Buttons draw `k: label`. */
export function widthOf(n: unknown): number {
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

/** Every element in the tree, outermost first. */
export function elementsOf(n: unknown): Drawn[] {
  if (!isEl(n)) return []
  return [n, ...(n.children ?? []).flatMap(elementsOf)]
}

/** The keys of an element's direct children. */
export const childKeys = (n: Drawn): string[] =>
  (n.children ?? []).flatMap(c => (isEl(c) && typeof c.props?.key === 'string' ? [c.props.key] : []))

/**
 * Rows a drawn node takes down the terminal: Text and Button one (a Text counts its newlines), a column Box the sum of its
 * children plus its border and vertical padding, a row Box its tallest child plus the same. Absolute and display:none nodes
 * take none. Limit: a Text wider than its box would wrap in the engine; this counts it as one row.
 */
export function heightOf(n: unknown): number {
  if (typeof n === 'string') return n.split('\n').length
  if (typeof n === 'number') return 1
  if (!isEl(n)) return 0
  const p = n.props ?? {}
  const kids = n.children ?? []
  if (p.display === 'none' || p.position === 'absolute') return 0
  if (n.type === 'Text') return Math.max(1, kids.reduce<number>((sum, kid) => sum + (typeof kid === 'string' ? kid.split('\n').length - 1 : 0), 1))
  if (n.type === 'Button') return 1
  const frame = (typeof p.borderStyle === 'string' ? 2 : 0) + 2 * num(p.paddingY ?? p.padding) + num(p.paddingTop) + num(p.paddingBottom)
  const heights = kids.map(heightOf)
  const column = p.flexDirection === 'column' || p.flexDirection === 'column-reverse'
  const gap = num(p.rowGap ?? p.gap)
  return frame + (column ? heights.reduce((s, h) => s + h, 0) + gap * Math.max(0, heights.length - 1) : Math.max(0, ...heights))
}
