// User intent: the stand-in RR tokens the kit's direct tests draw with — the values rr-stub.ts holds inline (a test plugin
// cannot import) — plain and predictable, so tests assert words, keys and recipes, not real colours.
import type { RrGround, RrStrength, RrTextProps, RrTokens, RrTone } from '../../hooks/rr'

export const TONE = { success: '#3a9a5b', warning: '#c4881d', critical: '#d14343', info: '#5b8fc7', signature: '#8a9eeb' } as const
export const STRONG = { success: '#1f5f35', warning: '#7a5410', critical: '#7d2525', info: '#2f557d', signature: '#4a5590' } as const
const KEYCAP = { bg: '#8a9eeb', ink: '#0d0d0c' }

export const STUB_TOKENS: RrTokens = {
  polarity: 'dark',
  surface: { page: '#141413', raised: '#1d1d1b', overlay: '#272725', recessed: '#0d0d0c' },
  fg: { bold: '#f5f3ed', default: '#d2cfc8', subtle: '#a09c95', disabled: '#66645f' },
  border: { default: '#343331', subtle: '#302f2d', strong: '#4d4c48', focus: '#5e79e6' },
  signatureText: '#8a9eeb',
  tone: TONE,
  tint12: { page: TONE, raised: TONE, overlay: TONE },
  tint24: { page: STRONG, raised: STRONG, overlay: STRONG },
  keycap: { success: KEYCAP, warning: KEYCAP, critical: KEYCAP, info: KEYCAP, signature: KEYCAP },
}

/** rr-tui's badge recipe over STUB_TOKENS. */
export const stubBadge = (a: { tone: RrTone; strength?: RrStrength; on?: RrGround }): RrTextProps => ({
  backgroundColor: ((a.strength ?? 24) === 24 ? STUB_TOKENS.tint24 : STUB_TOKENS.tint12)[a.on ?? 'page'][a.tone],
  color: STUB_TOKENS.fg.bold,
  bold: true,
})
