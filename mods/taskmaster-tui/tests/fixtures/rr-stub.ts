// User intent: a stand-in $.rr for taskmaster-tui tests — `claude plugin test` does not load dependencies — with plain,
// predictable trees so tests assert words and keys, not colours; button props follow rr-tui's real recipe.
import type { Plugin } from 'claude-code/testing'

export const RR_STUB: Plugin = {
  name: 'rr-tui',
  register(on) {
    on('engine.create', async ($, e, next) => {
      const built = await next(e)
      const tone = { success: '#3a9a5b', warning: '#c4881d', critical: '#d14343', info: '#5b8fc7', signature: '#8a9eeb' }
      const tint = { page: tone, raised: tone, overlay: tone }
      const glyph = { success: '●', warning: '▲', critical: '◆', info: 'ⓘ', signature: '' }
      const keycap = { bg: '#8a9eeb', ink: '#0d0d0c' }
      const text = (s: string) => h('Text', {}, s) as never
      return {
        ...built,
        rr: {
          tokens: async () => ({
            polarity: 'dark' as const,
            surface: { page: '#141413', raised: '#1d1d1b', overlay: '#272725', recessed: '#0d0d0c' },
            fg: { bold: '#f5f3ed', default: '#d2cfc8', subtle: '#a09c95', disabled: '#66645f' },
            border: { default: '#343331', subtle: '#302f2d', strong: '#4d4c48', focus: '#5e79e6' },
            signatureText: '#8a9eeb',
            tone,
            tint12: tint,
            tint24: tint,
            keycap: { success: keycap, warning: keycap, critical: keycap, info: keycap, signature: keycap },
          }),
          polarity: async () => 'dark' as const,
          surface: async a => h('Box', { flexDirection: 'column' }, ...a.children) as never,
          surfaceProps: async () => ({ flexDirection: 'column' as const, paddingX: 1 }),
          label: async a => text(a.text.toUpperCase()),
          signal: async a => text(`${glyph[a.kind]} ${a.word}${a.detail ? `  ${a.detail}` : ''}`),
          row: async a => h('Box', { flexDirection: 'row', columnGap: 2 }, ...a.cells.map(c => (typeof c === 'string' ? text(c) : c))) as never,
          rule: async a => text('─'.repeat(a.width)),
          button: async a =>
            a.treatment === 'outline'
              ? { borderStyle: 'round' as const, borderColor: tone[a.tone], paddingX: 1 }
              : { backgroundColor: tone[a.tone], paddingX: 1 },
          buttonProps: async a => ({ plain: true as const, ...(a.key === undefined ? {} : { hotkey: a.key }), hover: { bold: true as const, color: '#f5f3ed' } }),
          keycap: async a => text(` ${a.key} `),
          chip: async a => text(` ${glyph[a.tone]} ${a.text} `),
        },
      }
    })
  },
}
