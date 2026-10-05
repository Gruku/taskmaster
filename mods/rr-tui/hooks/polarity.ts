// User intent: decide which RR polarity a terminal draws in — follow the Claude Code theme unless the user pinned one, never
// guess from COLORTERM — and turn the generated table into the RrTokens contract.
import type { RrGround, RrPolarity, RrTokens, RrTone } from '../types'
import { RR_TABLE } from './tokens'

const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
const cache = new Map<RrPolarity, RrTokens>()

export function isPolarity(value: unknown): value is RrPolarity {
  return value === 'dark' || value === 'light' || value === 'survivalist'
}

export function resolvePolarity(setting: string, theme: unknown): RrPolarity {
  if (isPolarity(setting)) return setting
  return typeof theme === 'string' && theme.toLowerCase().includes('light') ? 'light' : 'dark'
}

export function tokensFor(polarity: RrPolarity): RrTokens {
  const hit = cache.get(polarity)
  if (hit) return hit
  const table = RR_TABLE[polarity]
  const pick = (key: string): string => {
    const value = table[key]
    if (value === undefined) throw new Error(`rr-tui: ${key} is missing from the generated table (rerun scripts/gen_tokens.py)`)
    return value
  }
  const perTone = (prefix: string) =>
    Object.fromEntries(TONES.map(tone => [tone, pick(`${prefix}.${tone}`)])) as Record<RrTone, string>
  const tint = (strength: 12 | 24) =>
    Object.fromEntries(
      GROUNDS.map(g => [g, Object.fromEntries(TONES.map(tone => [tone, pick(`tint${strength}.${tone}@${g}`)]))]),
    ) as Record<RrGround, Record<RrTone, string>>
  const made: RrTokens = {
    polarity,
    surface: {
      page: pick('bg-page'),
      raised: pick('surface-raised'),
      overlay: pick('surface-overlay'),
      recessed: pick('surface-recessed'),
    },
    fg: {
      bold: pick('foreground-bold'),
      default: pick('foreground-default'),
      subtle: pick('foreground-subtle'),
      disabled: pick('foreground-disabled'),
    },
    border: { default: pick('border-default'), subtle: pick('border-subtle'), strong: pick('border-strong'), focus: pick('border-focus') },
    signatureText: pick('signature-text'),
    tone: perTone('tone'),
    tint12: tint(12),
    tint24: tint(24),
    keycap: Object.fromEntries(TONES.map(tone => [tone, { bg: pick(`keycap.${tone}`), ink: pick(`keycap-ink.${tone}`) }])) as Record<
      RrTone,
      { bg: string; ink: string }
    >,
  }
  cache.set(polarity, made)
  return made
}
