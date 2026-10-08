// User intent: pin how every Taskmaster reply the mod reads becomes data — synthetic quirks from the server's own format
// strings, then the real captured replies from the legacy and native stores and the scratch store's write paths.
import { describe, expect, test } from 'claude-code/testing'

import {
  claimOk,
  continuityTotal,
  type ContinuityItem,
  firstParagraph,
  isRefusal,
  type ListRow,
  parseContinuity,
  parseGetTask,
  parseHandovers,
  parseHandoverSummary,
  parseHandoverWritten,
  parseIssueList,
  parseListTasks,
  parsePhases,
  parsePipeline,
  replyText,
  stripSeq,
} from '../hooks/parse'
import type { TmPipeline, TmTaskDetail } from '../types'
import { LEGACY } from './fixtures/captured/legacy'
import { NATIVE } from './fixtures/captured/native'
import { SCRATCH } from './fixtures/captured/scratch'
import { PHASES_ALL, PHASES_WAITING } from './fixtures/phases'
import * as R from './fixtures/replies'

type Reply = { readonly tool: string; readonly args: Readonly<Record<string, unknown>>; readonly isError: boolean; readonly text: string }
const repliesOf = (fixture: { readonly replies: unknown }) => fixture.replies as Readonly<Record<string, Reply | undefined>>
/** A captured reply by key; a missing key FAILS the test (never skips it): the fixture is stale, rerun the capture. */
const replyAt =
  (label: string, fixture: { readonly replies: unknown }) =>
  (key: string): Reply => {
    const reply = repliesOf(fixture)[key]
    if (reply === undefined) throw new Error(`${label} fixture ${key} is missing: rerun scripts/capture_fixtures.py`)
    return reply
  }
const value = <T>(parsed: { ok: true; value: T } | { ok: false; reason: string }): T => {
  if (!parsed.ok) throw new Error(`expected a parsed value, got: ${parsed.reason}`)
  return parsed.value
}
const itemKey = (i: ContinuityItem) => [i.id, i.type, i.next, i.actionClass, i.timestamp, i.taskId]

describe('list_tasks', () => {
  test('rows keep a multi-line human_action, a parenthesised title and a legacy P-priority', () => {
    expect(parseListTasks(R.LIST_IN_REVIEW)).toEqual({
      ok: true,
      value: {
        total: 2,
        rows: [
          {
            id: 'unified-chat-022',
            title: 'Unified chat pre-build gets the full supervisor toolset; cookbook owns the order',
            priority: 'critical',
            epic: 'unified-chat',
            status: 'in-review',
            humanAction:
              'Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):\nfirst unified message stays on intent/brief and does not build.',
          },
          {
            id: 'tm-audit-031',
            title: 'Handover auto-supersede (keeps newest)',
            priority: 'high',
            epic: 'tm-audit',
            status: 'in-review',
            humanAction: 'Open the viewer and confirm one open handover per thread.',
          },
        ],
      },
    })
  })

  test('an empty list is no rows, a capped list stops at its footer, an error or garbage is unreadable', () => {
    expect(parseListTasks(R.LIST_EMPTY)).toEqual({ ok: true, value: { rows: [], total: 0 } })
    expect(parseListTasks(R.LIST_CAPPED)).toEqual({
      ok: true,
      value: { total: 3, rows: [{ id: 'a-001', title: 'A', priority: 'low', epic: 'ep', status: 'in-review', humanAction: '' }] },
    })
    expect(parseListTasks('Error: no backlog here')).toEqual({ ok: false, reason: 'list_tasks: Error: no backlog here' })
    expect(parseListTasks('garbage')).toEqual({ ok: false, reason: 'list_tasks: no "**N tasks:**" header' })
  })
})

describe('get_task and pipeline', () => {
  test('slim fields, a multi-line human_action up to the next field, the links block ignored, a missing id is null', () => {
    expect(parseGetTask(R.GET_TASK_REVIEW)).toEqual({
      ok: true,
      value: {
        id: 'unified-chat-022',
        title: 'Unified chat pre-build gets the full supervisor toolset; cookbook owns the order',
        status: 'in-review',
        priority: 'critical',
        lane: 'full',
        gateState: 'review-gate:pass',
        branch: '',
        humanAction:
          'Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):\nfirst unified message stays on intent/brief and does not build.\n- second bullet of the check',
      },
    })
    expect(parseGetTask(R.GET_TASK_BOUND)).toEqual({
      ok: true,
      value: {
        id: 'tm-audit-030',
        title: 'Agent tool-use audit fixes',
        status: 'in-progress',
        priority: 'high',
        lane: 'full',
        gateState: 'review-gate:pass',
        branch: 'feat/tm-audit-030',
        humanAction: '',
      },
    })
    expect(parseGetTask(R.GET_TASK_MISSING)).toEqual({ ok: true, value: null })
    expect(parseGetTask('nonsense')).toEqual({ ok: false, reason: 'get_task: no "## `id` — title" header' })
  })

  test('pipeline: outstanding gates, ready for done, laneless, and a (none) gate state', () => {
    expect(parsePipeline(R.PIPELINE_BOUND)).toEqual({ ok: true, value: { laneless: false, lane: 'full', gateState: 'review-gate:pass', outstanding: ['merge'] } })
    expect(parsePipeline(R.PIPELINE_READY)).toEqual({ ok: true, value: { laneless: false, lane: 'full', gateState: '', outstanding: [] } })
    expect(parsePipeline(R.PIPELINE_LANELESS)).toEqual({ ok: true, value: { laneless: true, lane: '', gateState: '', outstanding: [] } })
    expect(parsePipeline('Error: task `x-001` not found')).toEqual({ ok: false, reason: 'task_pipeline: Error: task `x-001` not found' })
  })
})

describe('JSON replies', () => {
  test('continuity items keep id, type, next and timestamp; an error key is unreadable', () => {
    expect(value(parseContinuity(R.CONTINUITY_REVIEW)).map(itemKey)).toEqual([
      ['unified-chat-022', 'task', 'in-review', 'review', '2026-10-05T09:00', 'unified-chat-022'],
      ['tm-audit-031', 'task', 'in-review', 'review', '2026-10-04T12:00', 'tm-audit-031'],
      ['ISS-7', 'issue', 'P1 · open', 'review', '2026-10-01', ''],
    ])
    expect(parseContinuity(R.CONTINUITY_ERROR)).toEqual({ ok: false, reason: 'continuity_items: no backlog' })
    expect(parseContinuity('not json')).toEqual({ ok: false, reason: 'continuity_items: reply is not JSON' })
  })

  test('handovers: newest first, superseded hidden, the server total kept, absolute paths', () => {
    const parsed = value(parseHandovers(R.HANDOVERS_OPEN, '/proj'))
    expect(parsed.handovers.map(h => h.id)).toEqual(['2026-10-05-shipped-unified-chat-022', '2026-10-04-audit-fixes-landed'])
    expect(parsed.total).toBe(7)
    expect(parsed.handovers[0]).toEqual({
      id: '2026-10-05-shipped-unified-chat-022',
      created: '2026-10-05T09:10',
      tldr: 'Shipped unified-chat-022 to dev',
      nextAction: 'Live check, then sign off',
      path: '/proj/.taskmaster/handovers/2026-10-05-shipped-unified-chat-022.md',
      branch: 'main',
      taskIds: ['unified-chat-022'],
      thread: 'unified-chat',
    })
  })

  test('continuity: the server total is read when given, and null when the reply has none', () => {
    expect(continuityTotal(R.CONTINUITY_REVIEW)).toBe(3)
    expect(continuityTotal(R.CONTINUITY_DECIDE)).toBe(1)
    expect(continuityTotal(JSON.stringify({ view: 'action', items: [] }))).toBeNull()
    expect(continuityTotal('Error: No backlog found')).toBeNull()
  })

  test('claim ok is read from the JSON; anything else is not ok', () => {
    expect(claimOk(R.CLAIM_OK)).toBe(true)
    expect(claimOk(R.CLAIM_CONFLICT)).toBe(false)
    expect(claimOk('Error: x')).toBe(false)
  })
})

describe('write replies', () => {
  test('seq, export-pending and warning suffixes are stripped before reading', () => {
    expect(stripSeq(R.updated('a-001', 'status', 'in-progress'))).toBe('Updated `a-001` field `status` → in-progress')
    expect(stripSeq('Updated `a-001` field `status` → done (export pending: tasks/a-001.md) [seq 9]')).toBe('Updated `a-001` field `status` → done')
    expect(firstParagraph(stripSeq(R.WITH_WARNING))).toBe('Updated `tm-audit-031` field `status` → in-progress')
  })

  test('refusals are Error: and Cannot complete, with or without backticks; success is not a refusal', () => {
    for (const text of [R.GATE_REFUSAL, R.BUG_REFUSAL, R.WRONG_STATUS, R.GET_TASK_MISSING]) expect(isRefusal(text)).toBe(true)
    for (const text of [R.COMPLETED, R.KEYWORD_UPDATED, R.NO_CHANGE, R.NOT_PERSISTED]) expect(isRefusal(text)).toBe(false)
  })
})

/** What each real store's captured replies must read as, taken by hand from the fixture text (not from the parser). */
type StoreExpect = {
  readonly inReview: { readonly total: number; readonly shown: number; readonly withHumanAction: number; readonly first: ListRow }
  readonly waiting: { readonly total: number; readonly shown: number }
  readonly inProgress: { readonly total: number; readonly ids: readonly string[] }
  readonly review: { readonly count: number; readonly first: readonly unknown[]; readonly last: readonly unknown[] }
  readonly decide: { readonly count: number; readonly first: readonly unknown[] | null }
  readonly handovers: {
    readonly total: number
    readonly ids: readonly string[]
    readonly refs: { readonly at: number; readonly branch: string; readonly taskIds: readonly string[] }
  }
  readonly getReview: TmTaskDetail
  readonly pipelineReview: TmPipeline
  readonly getActive: TmTaskDetail
  readonly pipelineActive: TmPipeline
}

const LEGACY_EXPECT: StoreExpect = {
  inReview: {
    total: 22,
    shown: 22,
    withHumanAction: 0,
    first: { id: 'v3-skills-002', title: 'xxxxxxxxxx:handover xxxxx — xxxxx/xxxx xxxx', priority: 'critical', epic: 'x3-xxxxxx', status: 'in-review', humanAction: '' },
  },
  waiting: { total: 0, shown: 0 },
  inProgress: { total: 9, ids: ['store-perf-001', 'v3-release-010', 'jira-001'] },
  review: {
    count: 25,
    first: ['v3-polish-036', 'task', 'in-review', 'review', '2026-05-15T23:35', 'v3-polish-036'],
    last: ['ISS-027', 'issue', 'P1 · open', 'review', '2026-07-04T11:45:42Z', ''],
  },
  decide: { count: 0, first: null },
  handovers: {
    total: 14,
    ids: [
      '2026-10-05-xxxxxxxx-xxxxxxxxxx-xxx-xx-xxx-xxxx-spec',
      '2026-07-10-xxxxx-xxxxx-x2-8-0-xxxxxxxx-xxxxxx-xxxxx',
      '2026-06-18-xxxxxxx-task-xxxxxxx-task-bundle-001-xx',
      '2026-06-10-xxxxx-xxxx-xxxxxxx-xxxxxxxxxx-3-16-0-xxx',
      '2026-06-02-spec-x-merge-xxxxxx-xxxxxxx-to-xxxxx-xxx',
    ],
    refs: { at: 3, branch: 'xxxxxx', taskIds: ['tm-audit-006', 'tm-audit-021', 'tm-audit-022', 'tm-audit-019', 'tm-audit-020'] },
  },
  getReview: {
    id: 'v3-skills-002',
    title: 'xxxxxxxxxx:handover xxxxx — xxxxx/xxxx xxxx',
    status: 'in-review',
    priority: 'critical',
    lane: '',
    gateState: '',
    branch: '',
    humanAction: '',
  },
  pipelineReview: { laneless: true, lane: '', gateState: '', outstanding: [] },
  getActive: {
    id: 'store-perf-001',
    title: 'xxx B-082: xxxxx xxxxxx xxxxxxxxxx xxxxx xxxxxx xxxxx; xxxxxxxxxxx xxxx xxxxx the xxxx; xxxxxxx xxxx-xxxx xxxxxxx',
    status: 'in-progress',
    priority: 'critical',
    lane: 'full',
    gateState: 'review-gate:pass',
    branch: 'xxx/xxxxx-xxxx-xxxx',
    humanAction: '',
  },
  pipelineActive: { laneless: false, lane: 'full', gateState: 'review-gate:pass', outstanding: [] },
}

const NATIVE_TITLE_067 = 'xxxxxx: xxx xxxxxxxx_xxxxxxxx_xxxx xxxxxxxxxx to xxxxx_xxxxxx xxxxxx + /xxxxxxx + xxxxxx /xxxxxx xxxxxxx'
const NATIVE_EXPECT: StoreExpect = {
  inReview: {
    total: 757,
    shown: 40,
    withHumanAction: 10,
    first: { id: 'epic-b-067', title: NATIVE_TITLE_067, priority: 'critical', epic: 'xxxxx-xxxxxx', status: 'in-review', humanAction: '' },
  },
  waiting: { total: 354, shown: 40 },
  inProgress: { total: 122, ids: ['epic-v-0719-001', 'epic-v-0719-002', 'epic-w-086'] },
  review: {
    count: 40,
    first: ['epic-x-119', 'task', 'in-review', 'review', '2026-06-01T19:21', 'epic-x-119'],
    last: ['epic-c-011', 'task', 'in-review', 'review', '2026-05-28T19:09', 'epic-c-011'],
  },
  decide: { count: 9, first: ['DEC-026', 'decision', 'decide', '2026-07-29T02:53:40.451847+00:00', 'epic-aa-002'] },
  handovers: {
    total: 27,
    ids: [
      '2026-10-06-xxxxxx-xxxxxxxx-xxx-xxxxxx-xxxxxxx-66183',
      '2026-10-05-xxxxxxx-xxxxxxx-xxxx-022-full-pre-xxxxx',
      '2026-10-05-xxxxxxxx-the-1-5-0-xxxxxxx-xxxxx-full-xx',
      '2026-10-03-xxxxx-xxxxx-xxxxx-03-04-done-plan-05-xxx',
      '2026-10-02-xxxxxxxx-xxxxxxxx-xx-xxxx-phase-x-x2-xxx',
    ],
    refs: {
      at: 0,
      branch: '1.5.0/xxxxxxx/xxxxxx-xxxxxxxx-xxxxxxx',
      taskIds: ['epic-ac-012', 'epic-ac-013', 'epic-ac-014', 'epic-ac-015', 'epic-ac-016', 'epic-ac-017', 'epic-ad-007'],
    },
  },
  getReview: {
    id: 'epic-b-067',
    title: NATIVE_TITLE_067,
    status: 'in-review',
    priority: 'critical',
    lane: 'full',
    gateState: 'spec-review:pending',
    // The stored branch value carries a free-text note; the parser passes data through as it is.
    branch: 'xxxxxxx/epic-b-067 (xxxxxx, xxx xxxxxxx)',
    humanAction: '',
  },
  pipelineReview: { laneless: false, lane: 'full', gateState: 'spec-review:pending', outstanding: ['spec-review', 'plan-review', 'review-gate'] },
  getActive: {
    id: 'epic-v-0719-001',
    title: 'x11 — xxxxxxxxxxx-xxxxxxx gate xxxxxxx xxxx-xxxxxxxxxx (xxxx the xx-xxxx)',
    status: 'in-progress',
    priority: 'critical',
    lane: 'full',
    gateState: 'plan-review:pending',
    branch: 'xxxxxxx/xx-xxxxx-xxxxx',
    humanAction: '',
  },
  pipelineActive: { laneless: false, lane: 'full', gateState: 'plan-review:pending', outstanding: ['review-gate'] },
}

for (const [label, fixture, want] of [
  ['legacy', LEGACY, LEGACY_EXPECT],
  ['native', NATIVE, NATIVE_EXPECT],
] as const) {
  describe(`captured ${label} store`, () => {
    const at = replyAt(label, fixture)

    test('the in-review lists (all, waiting on a human, a capped page) parse every shown row and keep the total', () => {
      const all = value(parseListTasks(at('list_in_review').text))
      expect([all.total, all.rows.length]).toEqual([want.inReview.total, want.inReview.shown])
      expect(all.rows[0]).toEqual(want.inReview.first)
      expect(all.rows.filter(r => r.status !== 'in-review').map(r => r.id)).toEqual([])
      expect(all.rows.filter(r => r.humanAction !== '').length).toBe(want.inReview.withHumanAction)

      const waiting = value(parseListTasks(at('list_waiting').text))
      expect([waiting.total, waiting.rows.length]).toEqual([want.waiting.total, want.waiting.shown])
      expect(waiting.rows.filter(r => r.status !== 'in-review' || r.humanAction === '').map(r => r.id)).toEqual([])

      const page = value(parseListTasks(at('list_in_progress').text))
      expect([page.total, page.rows.map(r => r.id)]).toEqual([want.inProgress.total, want.inProgress.ids])
      expect(page.rows.filter(r => r.status !== 'in-progress').map(r => r.id)).toEqual([])
    })

    test('continuity items parse and carry the class that was asked for', () => {
      const review = value(parseContinuity(at('continuity_review').text))
      expect(review.length).toBe(want.review.count)
      expect(review.filter(i => i.actionClass !== 'review').map(i => i.id)).toEqual([])
      expect([itemKey(review[0]!), itemKey(review.at(-1)!)]).toEqual([want.review.first, want.review.last])

      const decide = value(parseContinuity(at('continuity_decide').text))
      expect(decide.length).toBe(want.decide.count)
      expect(decide.filter(i => i.actionClass !== 'decide').map(i => i.id)).toEqual([])
      const first = decide[0]
      expect(first === undefined ? null : [first.id, first.type, first.actionClass, first.timestamp, first.taskId]).toEqual(want.decide.first)
    })

    test('the open-handover list parses newest first with the server total, refs and absolute paths', () => {
      const parsed = value(parseHandovers(at('handovers_open').text, 'C:\\root'))
      expect(parsed.handovers.map(h => h.id)).toEqual(want.handovers.ids)
      expect(parsed.total).toBe(want.handovers.total)
      expect(parsed.handovers.map(h => h.path)).toEqual(want.handovers.ids.map(id => `C:\\root\\.taskmaster\\handovers\\${id}.md`))
      const h = parsed.handovers[want.handovers.refs.at]!
      expect([h.branch, h.taskIds]).toEqual([want.handovers.refs.branch, want.handovers.refs.taskIds])
    })

    test('a real task and its pipeline parse; a missing id reads as missing, not unreadable', () => {
      expect(parseGetTask(at('get_review').text)).toEqual({ ok: true, value: want.getReview })
      expect(parsePipeline(at('pipeline_review').text)).toEqual({ ok: true, value: want.pipelineReview })
      expect(parseGetTask(at('get_active').text)).toEqual({ ok: true, value: want.getActive })
      expect(parsePipeline(at('pipeline_active').text)).toEqual({ ok: true, value: want.pipelineActive })
      expect(parseGetTask(at('get_missing').text)).toEqual({ ok: true, value: null })
      const pipelineMissing = at('pipeline_missing').text
      expect(isRefusal(pipelineMissing)).toBe(true)
      expect(parsePipeline(pipelineMissing)).toEqual({ ok: false, reason: 'task_pipeline: Error: task `zz-missing-999` not found' })
    })
  })
}

describe('captured scratch store (write paths)', () => {
  const at = replyAt('scratch', SCRATCH)
  const TITLE = 'Scratch review task (with parentheses) — and a dash'
  const HUMAN = 'Check the scratch thing\nsecond line of the check'

  test('the in-review row keeps its two-line human_action and its parenthesised title', () => {
    expect(parseListTasks(at('list_in_review').text)).toEqual({
      ok: true,
      value: { total: 1, rows: [{ id: 'fx-001', title: TITLE, priority: 'high', epic: 'fx', status: 'in-review', humanAction: HUMAN }] },
    })
  })

  test('get_task reads the in-review status and the whole human_action', () => {
    expect(parseGetTask(at('get_in_review').text)).toEqual({
      ok: true,
      value: {
        id: 'fx-001',
        title: TITLE,
        status: 'in-review',
        priority: 'high',
        lane: 'full',
        gateState: 'spec-review:pending',
        branch: '',
        humanAction: HUMAN,
      },
    })
  })

  test('back to agent: status, a cleared human_action (twice), the next_step note — each answer reads as success', () => {
    const read = (key: string) => firstParagraph(stripSeq(at(key).text))
    expect(['back_status', 'back_clear_human_action', 'back_clear_again', 'back_next_step'].map(read)).toEqual([
      'Updated `fx-001` field `status` → in-progress',
      'Updated `fx-001` field `human_action` →',
      // A second clear of an already-empty field: the server answers "(not persisted)", and that is not a refusal.
      'Updated `fx-001` field `human_action` → (not persisted)',
      'Updated `fx-001`: next_step → Back from review: tighten the copy',
    ])
    expect(['back_status', 'back_clear_human_action', 'back_clear_again', 'back_next_step'].map(key => isRefusal(read(key)))).toEqual([false, false, false, false])
  })

  test('refusals read as refusals, and the server sends them with isError false', () => {
    const keys = ['complete_missing', 'complete_again', 'in_review_without_human_action']
    expect(keys.map(key => isRefusal(at(key).text))).toEqual([true, true, true])
    expect(keys.map(key => at(key).isError)).toEqual([false, false, false])
  })

  test('sign-off on a full-lane task with open gates answers the gate refusal', () => {
    expect(firstParagraph(stripSeq(at('complete').text))).toBe(
      'Cannot complete `fx-001` — outstanding gates for lane `full`: spec-review, plan-review, review-gate. Record each (backlog_record_gate) or skip it (backlog_skip_gate).',
    )
  })

  test('claim status is JSON the claim reader understands: a released claim reads ok', () => {
    expect(claimOk(at('claim_status').text)).toBe(true)
  })
})

describe('handover replies', () => {
  test('a summary reads each section block into its list items; a missing section is empty', () => {
    expect(value(parseHandoverSummary(R.HANDOVER_SUMMARY))).toEqual({
      decisions: ['The cookbook owns the build order, not the pre-build', 'Pre-build hands the supervisor its full toolset'],
      blockers: ['Needs unifiedChatGenerate + unifiedChatBuild on dev'],
    })
    expect(value(parseHandoverSummary(R.HANDOVER_SUMMARY_DECISIONS_ONLY))).toEqual({ decisions: ['Audit fixes ride the native branch'], blockers: [] })
    expect(value(parseHandoverSummary('## Handover: h1\n'))).toEqual({ decisions: [], blockers: [] })
  })

  test('a summary item keeps its continuation lines and nested bullets; unbulleted prose is one item per paragraph', () => {
    const raw = [
      '## Handover: h1',
      '',
      '### decisions',
      '- First decision',
      '  continues here',
      '  - nested detail',
      '* Second decision',
      '### blockers',
      'One blocker in prose',
      'over two lines.',
      '',
      'Another blocker.',
    ].join('\n')
    expect(value(parseHandoverSummary(raw))).toEqual({
      decisions: ['First decision continues here nested detail', 'Second decision'],
      blockers: ['One blocker in prose over two lines.', 'Another blocker.'],
    })
  })

  test('a summary refusal or a missing handover is unreadable, with the reason', () => {
    expect(parseHandoverSummary('Handover not found: h9')).toEqual({ ok: false, reason: 'handover_get: Handover not found: h9' })
    expect(parseHandoverSummary('Error: x is not a canonical section')).toEqual({ ok: false, reason: 'handover_get: Error: x is not a canonical section' })
    expect(parseHandoverSummary('No backlog found.')).toEqual({ ok: false, reason: 'handover_get: No backlog found.' })
  })

  test('a "Handover written:" receipt gives the id, the absolute path and the Resume line; anything else is null', () => {
    expect(parseHandoverWritten(R.HANDOVER_WRITTEN)).toEqual({
      id: '2026-10-06-live-data-wired',
      path: 'C:\\work\\proj\\.taskmaster\\handovers\\2026-10-06-live-data-wired.md',
      resume: 'Resume: tui-mods — Run the Step 9 live check',
    })
    expect(parseHandoverWritten('Handover written: h1\n- File: .taskmaster/handovers/h1.md')).toEqual({ id: 'h1', path: '', resume: '' })
    expect(parseHandoverWritten('Error: tldr is required')).toBeNull()
    expect(parseHandoverWritten('')).toBeNull()
  })
})

describe('issue_list', () => {
  test('rows give id, severity, status and the title (a components tag and the tldr after it left out); the footer counts the hidden', () => {
    expect(value(parseIssueList(R.ISSUES_P1))).toEqual({
      issues: [
        { id: 'ISS-7', severity: 'P1', status: 'open', title: 'Viewer drops edits on slow disks' },
        { id: 'ISS-9', severity: 'P1', status: 'open', title: 'Export stalls — on big stores — Repro: open a 2k-task store' },
      ],
      hidden: 0,
    })
    expect(value(parseIssueList(`${R.ISSUES_P1}\n…12 more issues — narrow with filters or pass limit=0 for all`)).hidden).toBe(12)
  })

  test('no match is no rows; a refusal, a missing backlog or a line it cannot read is unreadable', () => {
    expect(value(parseIssueList('No issues match.'))).toEqual({ issues: [], hidden: 0 })
    expect(parseIssueList('No backlog found.')).toEqual({ ok: false, reason: 'issue_list: No backlog found.' })
    expect(parseIssueList("Error: status must be one of open, investigating; got 'x'")).toEqual({
      ok: false,
      reason: "issue_list: Error: status must be one of open, investigating; got 'x'",
    })
    expect(parseIssueList('- ISS-7 P1 open — ok\n**garbage')).toEqual({ ok: false, reason: 'issue_list: unreadable line "**garbage"' })
  })

  for (const [label, fixture, want] of [
    [
      'legacy',
      LEGACY,
      {
        p0: [] as string[],
        p1: ['ISS-017', 'ISS-018', 'ISS-027'],
        first: 'xxxxxxxxx/xxxxxxxx state xxxxxxxxxxx xxx xxxxxxxxx xxxxxx xxxxxx xxxxxxxxxx',
        capped: { ids: ['ISS-017'], hidden: 2 },
      },
    ],
    [
      'native',
      NATIVE,
      {
        p0: ['ISS-001', 'ISS-003', 'ISS-004', 'ISS-005', 'ISS-063'],
        p1: ['ISS-006', 'ISS-007', 'ISS-008', 'ISS-011', 'ISS-017', 'ISS-018', 'ISS-019', 'ISS-021', 'ISS-035', 'ISS-059', 'ISS-060', 'ISS-061', 'ISS-062', 'ISS-064', 'ISS-065'],
        first: 'xxxxxx .xxxxxx xxxxxxxxx xxx xxxx is xxxxxxxxxx — xxxxxxxxxx xxxxxxx xxxxxxxxxx xxxxxxx xxxxxxx of xxx',
        capped: { ids: ['ISS-006'], hidden: 14 },
      },
    ],
  ] as const) {
    test(`captured ${label}: open P0 and P1 issues, every row read, the capped page counts the rest`, () => {
      const at = replyAt(label, fixture)
      const p0 = value(parseIssueList(at('issues_p0').text))
      const p1 = value(parseIssueList(at('issues_p1').text))
      expect([p0.issues.map(i => i.id), p0.hidden]).toEqual([[...want.p0], 0])
      expect([p1.issues.map(i => i.id), p1.hidden]).toEqual([[...want.p1], 0])
      expect([...p0.issues, ...p1.issues].filter(i => i.status !== 'open').map(i => i.id)).toEqual([])
      expect(p0.issues.filter(i => i.severity !== 'P0').map(i => i.id)).toEqual([])
      expect(p1.issues.filter(i => i.severity !== 'P1').map(i => i.id)).toEqual([])
      expect(p1.issues[0]?.title).toBe(want.first)
      const capped = value(parseIssueList(at('issues_p1_capped').text))
      expect([capped.issues.map(i => i.id), capped.hidden]).toEqual([[...want.capped.ids], want.capped.hidden])
    })
  }
})

describe('replyText', () => {
  test('a {"result": text} reply is unwrapped, from structuredContent first; anything else comes back as it was', () => {
    const list = '**2 tasks:**\n- `x-001` — T (high, x, in-review)'
    expect(replyText(JSON.stringify({ result: list }))).toBe(list)
    expect(replyText(`  {"result": ${JSON.stringify(list)}}\n`)).toBe(list)
    expect(replyText('ignored', { result: list })).toBe(list)
    expect(replyText(list)).toBe(list)
    const claim = '{"result": "x", "ok": true}'
    expect(replyText(claim)).toBe(claim)
    expect(replyText('{"result": 3}')).toBe('{"result": 3}')
    expect(replyText('{"result": broken')).toBe('{"result": broken')
    expect(replyText(list, { other: 1 })).toBe(list)
  })
})

describe('parsePhases', () => {
  const phases = (text: string) => {
    const parsed = parsePhases(text)
    if (!parsed.ok) throw new Error(parsed.reason)
    return parsed.value
  }

  test('the real table: header and footer skipped, every phase read with its order and count, names kept as the server holds them', () => {
    const all = phases(PHASES_WAITING)
    expect(all).toHaveLength(20)
    expect(all[0]).toEqual({ id: '2-1-0', status: 'planned', order: 17, count: 16, name: 'Release 2.1.0 — Alpha Scope & Polish' })
    expect(all.find(p => p.status === 'active')).toMatchObject({ id: '2-0-4', order: 14, count: 249 })
    expect(all.find(p => p.id === '2-0-2')?.name).toBe('Patch 2.0.2 — Alpha Scope &amp; Polish')
    expect(all.at(-1)).toMatchObject({ id: 'p1', status: 'done', order: 1, count: 0 })
    expect(phases(PHASES_ALL).find(p => p.id === 'patch-1-9-5')?.count).toBe(423)
  })

  test('a capped footer, a name cut at 80 chars, a missing order and an empty table all read', () => {
    const cut = 'A very long phase name that the server cut off at eighty characters, right in the mi'
    const text = ['phase                       name', `["a","planned",3,2]           ${cut}`, '["b","done",null,0]          B', '2 rows (capped)'].join('\n')
    expect(phases(text)).toEqual([
      { id: 'a', status: 'planned', order: 3, count: 2, name: cut },
      { id: 'b', status: 'done', order: 0, count: 0, name: 'B' },
    ])
    expect(phases('phase  name\n0 rows')).toEqual([])
  })

  test('an error, garbage and a table with no footer are unreadable', () => {
    expect(parsePhases('Error: no such table: entities')).toEqual({ ok: false, reason: 'backlog_query: Error: no such table: entities' })
    expect(parsePhases('garbage').ok).toBe(false)
    expect(parsePhases('').ok).toBe(false)
    expect(parsePhases('phase  name\n["a","planned",3,2]  A').ok).toBe(false)
  })

  test('a row whose JSON cell is cut or malformed is skipped, the others stay', () => {
    const text = ['phase  name', '["a","planned",3,2]  A', '["b","planned",3', '[not json]  C', '3 rows'].join('\n')
    expect(phases(text).map(p => p.id)).toEqual(['a'])
  })
})
