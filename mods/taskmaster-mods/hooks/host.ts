// User intent: everything taskmaster-mods needs from the engine as plain closures bound once from `$` in register.tsx, so
// the logic modules stay testable and never hold `$` itself.
import type { RenderSurface } from 'claude-code'

export type TmReply = { readonly text: string; readonly isError: boolean }
export type TmCopyResult = { readonly isCopied: boolean; readonly reason: string }

export type TmHost = {
  now: () => Promise<number>
  sleep: (ms: number, signal?: AbortSignal) => Promise<void>
  after: (ms: number, fn: () => void) => void
  call: (tool: string, args: Record<string, unknown>) => Promise<TmReply>
  storeGet: (key: string) => Promise<unknown>
  storeSet: (key: string, value: unknown) => Promise<void>
  storeDelete: (key: string) => Promise<void>
  storeKeys: () => Promise<string[]>
  status: (text: string | undefined) => void
  toast: (text: string) => void
  log: (text: string) => void
  fill: (text: string) => Promise<boolean>
  copy: (text: string, surface: RenderSurface | undefined) => Promise<TmCopyResult>
  openPane: (id: string, title: string) => Promise<boolean>
  closePane: (id: string) => Promise<void>
  focus: (requestId: string, key: string) => Promise<void>
  sessionId: () => Promise<string>
  repoRoot: () => Promise<string>
  cwd: () => Promise<string>
  branch: () => Promise<string>
}
