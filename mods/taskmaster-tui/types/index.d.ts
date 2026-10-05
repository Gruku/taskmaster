// User intent: the shapes taskmaster-tui keeps in $.state — what Taskmaster says waits on the user, which task this session
// is on, where the review queue stands — declared once so every drawing and handler agrees.
export type TmPriority = 'critical' | 'high' | 'medium' | 'low'
export type TmQueueItem =
  | {
      readonly kind: 'task'
      readonly id: string
      readonly title: string
      readonly priority: TmPriority
      readonly humanAction: string
      readonly timestamp: string
    }
  | { readonly kind: 'issue'; readonly id: string; readonly title: string; readonly severity: string; readonly timestamp: string }
  | { readonly kind: 'decision'; readonly id: string; readonly title: string; readonly timestamp: string }
export type TmTaskDetail = {
  readonly id: string
  readonly title: string
  readonly status: string
  readonly priority: TmPriority
  readonly lane: string
  readonly gateState: string
  readonly branch: string
  readonly humanAction: string
  /** The card's `i: details` (from backlog_get_task); absent until a reader fills them. */
  readonly notes?: readonly string[]
  readonly links?: readonly string[]
  readonly pr?: string
}
export type TmPipeline = {
  readonly laneless: boolean
  readonly lane: string
  readonly gateState: string
  readonly outstanding: readonly string[]
}
export type TmHandover = {
  readonly id: string
  readonly created: string
  readonly tldr: string
  readonly nextAction: string
  readonly path: string
}
export type TmBound = {
  readonly taskId: string
  readonly inferred: boolean
  readonly detail: TmTaskDetail | null
  readonly pipeline: TmPipeline | null
}
export type TmSnapshot = {
  readonly reachable: boolean
  readonly reason: string
  readonly fetchedAt: number
  readonly queue: readonly TmQueueItem[]
  readonly queueTotal: number
  readonly handovers: readonly TmHandover[]
  readonly handoversTotal: number
  readonly bound: TmBound | null
}
export type TmFault = 'none' | 'offline' | 'unreadable'
export type TmBinding = { readonly taskId: string; readonly at: number }
export type TmCursor = {
  readonly currentId: string
  readonly done: readonly string[]
  readonly skipped: readonly string[]
  readonly mode: 'card' | 'confirm' | 'note'
  readonly refusal: string
}
/** `refusalId` names the task `refusal` was given for; the band shows a refusal only on that task (absent: shown nowhere). */
export type TmBandMode = { readonly confirmingId: string; readonly refusal: string; readonly refusalId?: string }

declare module 'claude-code' {
  interface PluginState {
    'taskmaster-tui': {
      snapshot: TmSnapshot | null
      fault: TmFault
      cursor: TmCursor
      details: Readonly<Record<string, TmTaskDetail>>
      pick: string
      band: TmBandMode
      binding: TmBinding | null
      /** Ticked check items per task id: a mirror of `$.store` `ticks:<id>` that redraws the card; never sent to Taskmaster. */
      ticks: Readonly<Record<string, readonly string[]>>
      detailsOpen: boolean
    }
  }
}
