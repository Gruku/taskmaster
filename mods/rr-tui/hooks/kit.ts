// User intent: Reality Reprojection's non-interactive terminal elements as plain-data trees, so every mod draws RR the same
// way: surfaces step instead of shadowing, and colour never travels without its shape and word.
import type { RenderNode } from 'claude-code'

import type { RrBoxProps, RrButtonProps, RrGround, RrLevel, RrSignalKind, RrStrength, RrTokens, RrTone, RrTreatment } from '../types'

export const SIGNAL_GLYPH: Readonly<Record<RrSignalKind, string>> = { success: '●', warning: '▲', critical: '◆', info: 'ⓘ' }

// Survivalist has no hue to tint a chip button with, so it steps one surface up from its ground instead.
const NEXT_STEP: Readonly<Record<RrGround, Exclude<RrLevel, 'page' | 'recessed'>>> = { page: 'raised', raised: 'overlay', overlay: 'raised' }

const el = (tag: 'Box' | 'Text', props: Record<string, unknown>, ...children: unknown[]): RenderNode =>
  h(tag, props, ...children) as RenderNode

export function surfaceProps(t: RrTokens, a: { level: RrLevel; width?: number | string; padX?: number }): RrBoxProps {
  return {
    flexDirection: 'column',
    backgroundColor: t.surface[a.level],
    paddingX: a.padX ?? 1,
    ...(a.width === undefined ? {} : { width: a.width }),
  }
}

export function surface(
  t: RrTokens,
  a: { level: RrLevel; children: readonly RenderNode[]; width?: number | string; padX?: number },
): RenderNode {
  return el('Box', { ...surfaceProps(t, a) }, ...a.children)
}

export function label(t: RrTokens, a: { text: string }): RenderNode {
  return el('Text', { color: t.signatureText, bold: t.polarity === 'survivalist' }, a.text.toUpperCase())
}

export function signal(t: RrTokens, a: { kind: RrSignalKind; word: string; detail?: string }): RenderNode {
  return el(
    'Box',
    { flexDirection: 'row' },
    el('Text', { color: t.tone[a.kind] }, SIGNAL_GLYPH[a.kind]),
    el('Text', { color: t.fg.bold, bold: true }, ` ${a.word}`),
    ...(a.detail ? [el('Text', { color: t.fg.subtle }, `  ${a.detail}`)] : []),
  )
}

export function row(t: RrTokens, a: { cells: readonly RenderNode[]; emphasis?: 'strong' | 'quiet' }): RenderNode {
  const color = a.emphasis === 'strong' ? t.fg.bold : a.emphasis === 'quiet' ? t.fg.subtle : t.fg.default
  const cells = a.cells.map(cell =>
    typeof cell === 'string' ? el('Text', { color, bold: a.emphasis === 'strong', wrap: 'truncate-end' }, cell) : cell,
  )
  return el('Box', { flexDirection: 'row', columnGap: 2 }, ...cells)
}

export function rule(t: RrTokens, a: { width: number }): RenderNode {
  return el('Text', { color: t.border.default }, '─'.repeat(Math.max(0, Math.floor(a.width))))
}

export function button(t: RrTokens, a: { treatment: RrTreatment; tone: RrTone; on?: RrGround }): RrBoxProps {
  if (t.polarity === 'survivalist') {
    return a.treatment === 'outline'
      ? { borderStyle: 'bold', borderColor: t.fg.bold, paddingX: 1 }
      : { backgroundColor: t.surface[NEXT_STEP[a.on ?? 'page']], paddingX: 1 }
  }
  return a.treatment === 'outline'
    ? { borderStyle: 'round', borderColor: t.tone[a.tone], paddingX: 1 }
    : { backgroundColor: t.tint12[a.on ?? 'page'][a.tone], paddingX: 1 }
}

export function buttonProps(t: RrTokens, a: { key?: string }): RrButtonProps {
  return { plain: true, ...(a.key === undefined ? {} : { hotkey: a.key }), hover: { bold: true, color: t.fg.bold } }
}

export function keycap(t: RrTokens, a: { key: string; tone: RrTone }): RenderNode {
  return el('Text', { backgroundColor: t.keycap[a.tone].bg, color: t.keycap[a.tone].ink, bold: true }, ` ${a.key} `)
}

export function chip(t: RrTokens, a: { text: string; tone: RrTone; strength: RrStrength; on?: RrGround }): RenderNode {
  const glyph = a.tone === 'signature' ? '' : `${SIGNAL_GLYPH[a.tone]} `
  if (t.polarity === 'survivalist') {
    return el('Text', { color: t.fg.bold, bold: a.strength === 24 }, `[${glyph}${a.text}]`)
  }
  const tint = (a.strength === 24 ? t.tint24 : t.tint12)[a.on ?? 'page'][a.tone]
  return el('Text', { backgroundColor: tint, color: t.fg.bold, bold: a.strength === 24 }, ` ${glyph}${a.text} `)
}
