// User intent: a second plugin that uses $.rr the way taskmaster-tui will — through the noun only — so tests see what
// consumers see.
import type { Plugin } from 'claude-code/testing'

export const CONSUMER: Plugin = {
  name: 'rr-probe-consumer',
  register(on) {
    on('command.run', { command: 'rr-probe' }, async $ => {
      const t = await $.rr.tokens()
      return { text: JSON.stringify({ polarity: await $.rr.polarity(), signatureText: t.signatureText }) }
    })
    on('command.run', { command: 'rr-probe-all' }, async $ => {
      const answers = {
        surface: await $.rr.surface({ level: 'raised', children: ['x'] }),
        surfaceProps: await $.rr.surfaceProps({ level: 'overlay' }),
        label: await $.rr.label({ text: 'review' }),
        signal: await $.rr.signal({ kind: 'info', word: 'note' }),
        row: await $.rr.row({ cells: ['a', 'b'] }),
        rule: await $.rr.rule({ width: 3 }),
        button: await $.rr.button({ treatment: 'outline', tone: 'success' }),
        buttonProps: await $.rr.buttonProps({ key: 'a' }),
        keycap: await $.rr.keycap({ key: 'd', tone: 'success' }),
        chip: await $.rr.chip({ text: 'refused', tone: 'critical', strength: 24 }),
      }
      return { text: JSON.stringify(answers) }
    })
  },
}
