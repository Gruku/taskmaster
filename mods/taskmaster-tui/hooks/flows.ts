// User intent: what every key in the band, the review queue and the handovers pane does, in one place, so the band and the
// pane can never disagree and every write goes through one explicit confirmation and at most one write per task at a time.
import type { RenderSurface } from 'claude-code'

import type { TmBandMode, TmCursor, TmHandover, TmSnapshot, TmTaskDetail } from '../types'
import type { TmActions } from './actions'
import type { TmHost } from './host'
import { afterDone, afterSendBack, FRESH_CURSOR, handoverCopyText, HANDOVERS, REVIEW } from './model'

export type TmWriter = {
  snapshot: (change: (s: TmSnapshot | null) => TmSnapshot | null) => Promise<void>
  cursor: (change: (c: TmCursor) => TmCursor) => Promise<void>
  band: (change: (b: TmBandMode) => TmBandMode) => Promise<void>
  pick: (id: string) => Promise<void>
  details: (change: (d: Readonly<Record<string, TmTaskDetail>>) => Readonly<Record<string, TmTaskDetail>>) => Promise<void>
}

export type TmFlowDeps = {
  host: TmHost
  write: TmWriter
  actions: TmActions
  afterWrite: (taskId: string, outcome: 'done' | 'sent-back') => void
}

export type TmFlows = ReturnType<typeof createFlows>

const PROMPT_REFUSED = 'taskmaster-tui: the prompt did not take the text; close the dialog and try again'

export function createFlows(d: TmFlowDeps) {
  const busy = new Set<string>()
  const once = async (id: string, work: () => Promise<void>): Promise<void> => {
    if (busy.has(id)) return
    busy.add(id)
    try {
      await work()
    } finally {
      busy.delete(id)
    }
  }
  const open = async (id: string, title: string): Promise<void> => {
    if (!(await d.host.openPane(id, title))) d.host.toast('taskmaster-tui: widen the terminal to see the pane')
  }
  const fill = async (text: string): Promise<void> => {
    if (!(await d.host.fill(text))) d.host.toast(PROMPT_REFUSED)
  }

  return {
    openReview: async (pin = '', mode: TmCursor['mode'] = 'card'): Promise<void> => {
      await d.write.cursor(() => ({ ...FRESH_CURSOR, currentId: pin, mode }))
      await open(REVIEW, 'Review')
      if (mode === 'note') await d.host.focus(REVIEW, 'note')
    },
    openHandovers: async (): Promise<void> => {
      await d.write.pick('')
      await open(HANDOVERS, 'Handovers')
    },
    askDone: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'confirm', refusal: '', currentId: id }))
      await d.host.focus(REVIEW, 'confirm-no')
    },
    cancel: async (): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card' }))
    },
    confirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.done(id)
        if (out.ok) {
          await d.write.snapshot(s => (s === null ? s : afterDone(s, id)))
          await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', done: [...c.done, id] }))
          d.afterWrite(id, 'done')
          return
        }
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
      }),
    askNote: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'note', refusal: '', currentId: id }))
      await d.host.focus(REVIEW, 'note')
    },
    sendBack: (id: string, note: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.backToAgent(id, note)
        if (!out.ok) {
          await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
          return
        }
        await d.write.snapshot(s => (s === null ? s : afterSendBack(s, id)))
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '' }))
        await d.host.closePane(REVIEW)
        await fill(note.trim() ? `Back to ${id}: ${note.trim()}` : `Back to ${id}`)
        d.afterWrite(id, 'sent-back')
      }),
    skip: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', skipped: [...c.skipped, id] }))
    },
    fill,
    bandAskDone: async (id: string): Promise<void> => {
      await d.write.band(() => ({ confirmingId: id, refusal: '' }))
    },
    bandCancel: async (): Promise<void> => {
      await d.write.band(() => ({ confirmingId: '', refusal: '' }))
    },
    bandConfirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.done(id)
        if (out.ok) {
          await d.write.snapshot(s => (s === null ? s : afterDone(s, id)))
          await d.write.band(() => ({ confirmingId: '', refusal: '' }))
          d.afterWrite(id, 'done')
          return
        }
        await d.write.band(() => ({ confirmingId: '', refusal: out.refusal, refusalId: id }))
      }),
    pick: async (id: string): Promise<void> => {
      await d.write.pick(id)
    },
    copyHandover: async (h: TmHandover, surface: RenderSurface | undefined): Promise<void> => {
      const r = await d.host.copy(handoverCopyText(h), surface)
      d.host.toast(r.isCopied ? `Copied handover ${h.id}` : `Clipboard unavailable (${r.reason}): copy by hand from ${h.path}`)
    },
    resumeHandover: async (h: TmHandover): Promise<void> => {
      await fill(`Resume from handover ${h.id} (${h.path})`)
    },
  }
}
