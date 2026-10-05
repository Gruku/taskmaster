// User intent: the inputs taskmaster-tui's tests hand the engine — a session, the band, the panes, a typed command — once.
import type { RenderInput, SessionStartInput } from 'claude-code'

export const PLUGIN = 'taskmaster-tui'

export const SESSION: SessionStartInput = { cwd: 'C:\\work\\proj', surface: 'terminal', isInteractive: true }

export const BAND: RenderInput<'AbovePrompt'> = {
  component: 'AbovePrompt',
  surface: 'terminal',
  requestId: 'band',
  viewport: { columns: 140, rows: 40, isFullscreen: true },
  props: { hasSurvey: false, isWorking: false, maxRows: 12, bodyColumns: 120, scroll: { offset: 0, bodyRows: 11 }, view: {} },
}

export const pane = (requestId: string): RenderInput<'Pane'> => ({
  component: 'Pane',
  surface: 'terminal',
  requestId,
  viewport: { columns: 180, rows: 48, isFullscreen: true },
  props: { title: requestId, isFocused: true, bodyColumns: 72, placement: 'dock', scroll: { offset: 0, bodyRows: 44 }, view: {} },
})

export const command = (name: string) => ({
  command: name,
  args: '',
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: true, columns: 180 },
})
