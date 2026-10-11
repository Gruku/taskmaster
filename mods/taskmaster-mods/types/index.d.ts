// User intent: the shapes taskmaster-mods keeps in $.state — what Taskmaster says waits on the user, which task this session
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
 * item. `unavailable`: the reader could not get them and nothing was cached (the card says so; `i` asks again). `stale`: a
 * refresh happened since the read; still drawn, read again when shown.
 */
export type TmHandoverSummary = {
  readonly decisions: readonly string[]
  readonly blockers: readonly string[]
  readonly unavailable?: true
  readonly stale?: true
}
/** One phase of the backlog (backlog_query) as one pane counts it. */
export type TmPhase = {
  readonly id: string
  readonly status: string
  readonly order: number
  /** What the pane counts: review its waiting in-review tasks, handovers its open handovers, restore its sessions. */
  readonly count: number
  /** The phase name as the server holds it (`Release 1.5.0 — CM 1.0 Patches`), entities not decoded. */
  readonly name: string
  /** YYYY-MM-DD: when a done phase completed, else when it started; absent or '' when the backlog does not say. */
  readonly date?: string
  /** Handovers only: the closed handovers linked through the phase's tasks, added to `count` while `◐ closed` is on. */
  readonly closed?: number
}
/** The panes that carry a phase strip (spec §5); each keeps its own choice. */
export type TmPhasePane = 'review' | 'handovers' | 'restore'
/**
 * One pane's phase strip: the chosen id ('' All, '~none' no task), the phases read for it, whether they were read (a
 * remembered choice falls back to All only after), the `◐ done` toggle (null: the default rule) and the overlay list.
 */
export type TmPhaseView = {
  readonly choice: string
  readonly phases: readonly TmPhase[]
  readonly loaded: boolean
  readonly showDone: boolean | null
  readonly overlay: boolean
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
  /** True when a count behind queueTotal was cut at the 50-row window with no server total: the band shows `N+`. */
  readonly queueCapped?: boolean
  /**
   * The newest open handovers (up to 30, the pane pages them 5 at a time), newest first (by `created`), superseded ones left out. Every producer (demo, the tm reader)
   * keeps this rule: the pane draws the list as given. `handoversTotal` is the server's count of all open handovers.
   */
  readonly handovers: readonly TmHandover[]
  readonly handoversTotal: number
  readonly bound: TmBound | null
}
/** `connecting`: a transient failure (server still connecting, or cold and slow) being retried: no status line, a neutral note. */
export type TmFault = 'none' | 'offline' | 'unreadable' | 'connecting'
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
    'taskmaster-mods': {
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
      /** Each pane's phase strip, its choice mirrored from `$.store` `phase:<pane>:<repo root>` (spec §5). */
      phaseViews: Readonly<Record<TmPhasePane, TmPhaseView>>
      /** The handovers pane's page (0 first); reset when the pane opens. */
      handoverPage: number
      /** The band's `HANDOVER  <tldr>  3: copy` row (null: none); cleared on copy, on /clear, or by a newer handover. */
      handoverNotice: TmHandoverNotice | null
      /** One guard run (fork, handover, compaction) per session at most: `$.state` is per session, so /clear or a resume can earn another. */
      handoverGuard: TmHandoverGuard
    }
  }
}
