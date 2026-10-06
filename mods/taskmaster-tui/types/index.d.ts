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
  /** The list reply's `branch` and `task_ids`: the expanded summary's `branch: X · tasks: a, b`. */
  readonly branch: string
  readonly taskIds: readonly string[]
  /** The list reply's `thread`: the copy block's `Resume: <thread> — <next action>` line. */
  readonly thread: string
}
/**
 * A handover's body sections for the expanded summary (backlog_handover_get, sections decisions + blockers), one entry per
 * item. `unavailable`: the reader could not get them (refused, unreadable, offline); the card says so and `i` asks again.
 */
export type TmHandoverSummary = { readonly decisions: readonly string[]; readonly blockers: readonly string[]; readonly unavailable?: true }
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
  /** True when a count behind queueTotal was cut at the 50-row window with no server total: the band shows `N+`. */
  readonly queueCapped?: boolean
  /**
   * The last 5 open handovers, newest first (by `created`), superseded ones left out. Every producer (demo, the tm reader)
   * keeps this rule: the pane draws the list as given. `handoversTotal` is the server's count of all open handovers.
   */
  readonly handovers: readonly TmHandover[]
  readonly handoversTotal: number
  readonly bound: TmBound | null
}
export type TmFault = 'none' | 'offline' | 'unreadable'
/** The band's handover-written notice: the handover this session's main agent just wrote, and its Telegram-ready block. */
export type TmHandoverNotice = { readonly id: string; readonly tldr: string; readonly path: string; readonly text: string }
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
/** The cache-cold handover guard: when the main loop last ended a turn (null: none yet), and whether this session is spent. */
export type TmHandoverGuard = { readonly lastTurnEnd: number | null; readonly latch: 'none' | 'fired' | 'handover' }

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
      /** Decisions and blockers per handover id, read only when its summary is expanded. */
      summaries: Readonly<Record<string, TmHandoverSummary>>
      /** The handover whose summary is expanded ('' none); it shows only while that handover is the picked one. */
      summaryOpen: string
      /** The band's `HANDOVER  <tldr>  3: copy` row (null: none); cleared on copy, on /clear, or by a newer handover. */
      handoverNotice: TmHandoverNotice | null
      /** One handover prompt per session at most: `$.state` is per session, so /clear or a resume can earn another. */
      handoverGuard: TmHandoverGuard
    }
  }
}
