// User intent: the inputs rr-tui's tests hand the engine — a session, the gallery pane, a typed command — written once.
import type { RenderInput, SessionStartInput } from 'claude-code'

export const SESSION: SessionStartInput = { cwd: 'C:\\work', surface: 'terminal', isInteractive: true }

export const GALLERY_PANE: RenderInput<'Pane'> = {
  component: 'Pane',
  surface: 'terminal',
  requestId: 'rr-gallery',
  viewport: { columns: 180, rows: 48, isFullscreen: true },
  props: { title: 'RR gallery', isFocused: true, bodyColumns: 72, placement: 'dock', scroll: { offset: 0, bodyRows: 44 }, view: {} },
}

export const command = (name: string) => ({
  command: name,
  args: '',
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: true, columns: 180 },
})
