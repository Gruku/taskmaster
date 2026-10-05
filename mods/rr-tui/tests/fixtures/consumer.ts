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
  },
}
