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
  /** The task's phase id (backlog_get_task's `phase`); absent when it has none. */
  readonly phase?: string
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
  /** The list reply's `status`; absent (read as open) on a snapshot written before the handovers pane's `◐ closed`. */
  readonly status?: 'open' | 'closed'
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
/** A task a handover links (handover_tasks), as the handovers pane's card shows it: live status and phase ('' none). */
export type TmLinkedTask = { readonly id: string; readonly status: string; readonly phase: string; readonly title: string }
/**
 * The handovers pane's own read (spec §9): up to 100 open handovers and, when `closed`, up to 100 closed ones, newest first;
 * `total` is the server's count of open ones; `tasks` the linked tasks by id (one missing: not read, or gone).
 */
export type TmHandoverList = {
  readonly handovers: readonly TmHandover[]
  readonly total: number
  readonly closed: boolean
  readonly tasks: Readonly<Record<string, TmLinkedTask>>
}
/** A y/n close row: `one` from the focused card's `x`, `bulk` from `Close N`; `ids` are what `y` closes. */
export type TmCloseAsk = { readonly kind: 'one' | 'bulk'; readonly ids: readonly string[] }
/** The handovers pane's marks: the selected ids, the close row that is up (null: none), and `◐ closed`. */
export type TmHandoverMarks = { readonly selected: readonly string[]; readonly ask: TmCloseAsk | null; readonly showClosed: boolean }
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
/** The review pane's focused card (`currentId`, '' the page's first), what this pass signed off, and its confirm or note. */
export type TmCursor = {
  readonly currentId: string
  readonly done: readonly string[]
  readonly mode: 'card' | 'confirm' | 'note'
  readonly refusal: string
}
/**
 * The review list (spec §8): the page shown (0 first; the focused card's page wins), the selected task ids, `t`'s tick mode,
 * and the bulk row with the ids it was asked for, so `y` acts on exactly those whatever a refresh draws in between.
 */
export type TmReviewList = {
  readonly page: number
  readonly selected: readonly string[]
  /** `t` pressed: the focused card's checks own the digits 1–9 until one is ticked, or `t`, `f`, a move or a page turn. */
  readonly ticking: boolean
  /** The bulk row: `done` its y/n, `agent` its note, `busy` while the writes run one task at a time. */
  readonly bulk: 'none' | 'done' | 'agent' | 'busy'
  readonly bulkIds: readonly string[]
}
/** `refusalId` names the task `refusal` was given for; the band shows a refusal only on that task (absent: shown nowhere). */
export type TmBandMode = { readonly confirmingId: string; readonly refusal: string; readonly refusalId?: string }
/** The cache-cold handover guard: when the main loop last ended a turn (null: none yet), and whether this session is spent. */
export type TmHandoverGuard = {
  readonly lastTurnEnd: number | null
  readonly latch: 'none' | 'fired' | 'handover'
  /** A `/goal` is set in this session: the guard never compacts under it. Absent: none seen. */
  readonly goal?: boolean
}
/** A handover a scanned session wrote: its backlog_handover_create input and the server's receipt (spec §10.1). */
export type TmScannedHandover = { readonly id: string; readonly path: string; readonly tldr: string; readonly next: string }
/** One of this repo's sessions as scanner/scan.py reports it: transcript facts only (spec §10.1). */
export type TmScannedSession = {
  readonly id: string
  readonly cwd: string
  readonly cwdExists: boolean
  readonly branch: string
  readonly title: string
  readonly firstPrompt: string
  readonly lastPrompt: string
  readonly lastReply: string
  readonly lastEvent: string
  /** Closed mid-turn: an interrupt, or a prompt or tool call the agent never answered. */
  readonly interrupted: boolean
  readonly prompts: number
  /** The context its last request carried: what a --resume reopens. */
  readonly ctx: number
  readonly cost: number
  /** Epoch seconds the process exited (its cost-state), else its last record's time. */
  readonly closedEpoch: number
  /** It wrote a cost-state: it closed at least once (a running one was resumed since). */
  readonly hasClosed: boolean
  /** Open in a terminal now (its pid file names a live process). */
  readonly running: boolean
  readonly resumedAfterClose: boolean
  /** The tm task ids its own tool calls named, last touched last: the last is its bound task (spec §10.2). */
  readonly taskIds: readonly string[]
  readonly handovers: readonly TmScannedHandover[]
  readonly endedSession: boolean
  readonly promptsAfterEnd: number
}
export type TmScan = { readonly scannedAt: number; readonly root: string; readonly sessions: readonly TmScannedSession[] }
/** A scanned task as one backlog_query read it (spec §10.2): `phase` '' when it has none. */
export type TmRefTask = { readonly id: string; readonly status: string; readonly lane: string; readonly phase: string; readonly title: string }
/** What tm said of the scanned ids: tasks by id, handover statuses by id; an id tm does not hold is absent. */
export type TmRefs = { readonly tasks: Readonly<Record<string, TmRefTask>>; readonly handovers: Readonly<Record<string, string>> }
/**
 * The restore pane's data: the last scan (null before the first), what tm said of it (null: not resolved, so statuses read
 * `?`), why the last scan failed ('' when it did not), and the window it covered, in days.
 */
export type TmRestoreData = { readonly scan: TmScan | null; readonly refs: TmRefs | null; readonly error: string; readonly days: number }
export type TmResumeMode = 'fresh' | 'resume'
/**
 * The restore pane's own state: the page and focused card, explicit ticks and resume modes (absent: the default rule),
 * when each session was reopened, `◐ compact`, the busy line, and per-card busy flags and briefs.
 */
export type TmRestoreView = {
  readonly page: number
  readonly focus: string
  readonly selected: Readonly<Record<string, boolean>>
  readonly modes: Readonly<Record<string, TmResumeMode>>
  readonly opened: Readonly<Record<string, number>>
  readonly compact: boolean
  readonly busy: string
  readonly launching: readonly string[]
  readonly briefing: readonly string[]
  readonly briefs: Readonly<Record<string, string>>
}
/**
 * The band's and the Session tab's handover (spec D5): the one this session wrote, else the one it was resumed from, else the
 * newest open handover linked to its bound task. `created` is '' when the open list does not hold it.
 */
export type TmSessionHandover = {
  readonly id: string
  readonly tag: 'written here' | 'resumed from' | 'task'
  readonly tldr: string
  readonly path: string
  readonly created: string
  readonly next?: string
}
/** This session's branch and when it started, read once at `session.start`: the Session tab's toolbar words. */
export type TmSessionInfo = { readonly branch: string; readonly startedAt: number }
/** The Taskmaster sidebar (spec §15.8): whether it is open over the pane, and the pane `m` on the band reopens. */
export type TmNav = { readonly sidebar: boolean; readonly last: 'review' | 'handovers' | 'session' | 'restore' }
/** The newest closing burst the restore scan found with unfinished sessions (Stage 5 writes it): the sidebar's Restore entry. */
export type TmRestorePending = { readonly epoch: number; readonly unfinished: number }
/** This session's place in the last restore scan's burst (Stage 5 writes it): the Session tab's "part of <at> burst". */
export type TmSessionBurst = { readonly at: string; readonly others: number }

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
      /** The review pane's list: page, selection, tick mode and the bulk row (spec §8); reset when the pane opens. */
      reviewList: TmReviewList
      /** The handovers pane's page (0 first); reset when the pane opens. */
      handoverPage: number
      /** The handovers pane's own list (null until its first read: the pane draws the snapshot's window meanwhile). */
      handoverList: TmHandoverList | null
      /** The handovers pane's selection, its y/n close row and `◐ closed`. */
      handoverMarks: TmHandoverMarks
      /** The band's `HANDOVER  <tldr>  3: copy` row (null: none); cleared on copy, on /clear, or by a newer handover. */
      handoverNotice: TmHandoverNotice | null
      /** One guard run (fork, handover, compaction) per session at most: `$.state` is per session, so /clear or a resume can earn another. */
      handoverGuard: TmHandoverGuard
      /** The restore pane's last scan, what tm said of it, a scan failure and its window (spec §10). */
      restoreData: TmRestoreData
      /** The restore pane's page, focus, ticks, resume modes, reopened sessions, compact, busy flags and briefs. */
      restoreView: TmRestoreView
      /** D5, resolved after each refresh, a written or recorded handover, and at start; a newer handover notice wins when drawn. */
      sessionHandover: TmSessionHandover | null
      /** The handover this session was resumed from ('' none): mirrors `$.store` `resumed-from:<session id>` (spec §10.5). */
      resumedFrom: string
      /** Written by the restore scan (Stage 5); the band shows `4` while it is set and not hidden. Default null. */
      restorePending: TmRestorePending | null
      /** The burst epoch the band's `x` hid (also kept at `$.store` `restore-hidden:<epoch>`); null: none hidden here. */
      restoreHidden: number | null
      /** Written by the restore scan (Stage 5); the Session tab's SESSION section reads it. Default null. */
      sessionBurst: TmSessionBurst | null
      /** The Taskmaster sidebar: open over the pane or not, and the pane the band's `m` reopens (spec §15.8). */
      nav: TmNav
      /** This session's branch and start time (null until session.start ran in this instance). */
      sessionInfo: TmSessionInfo | null
    }
  }
}
