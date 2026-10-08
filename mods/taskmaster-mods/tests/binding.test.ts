// User intent: pin which tool calls bind this session to a task, keep it, or let it go — and how the stored mirror is read and
// pruned — as pure decisions, so the band never shows the wrong task.
import { describe, expect, test } from 'claude-code/testing'

import {
  bindingChange,
  callSucceeded,
  inferTaskId,
  isWriteTool,
  parseStoredBinding,
  pruneBindings,
  STALE_MS,
  TM_TOOL_PREFIX as T,
} from '../hooks/binding'
import * as R from './fixtures/replies'

const call = (tool: string, input: Record<string, unknown>, ok = true) => ({ tool: T + tool, input, ok })

describe('binding transitions', () => {
  test('a pick (which takes the claim), a claim renewal or a move to in-progress binds that task; release and status do not', () => {
    expect(bindingChange(call('backlog_pick_task', { task_id: 'a-001' }), null)).toEqual({ kind: 'set', taskId: 'a-001' })
    expect(bindingChange(call('backlog_claim', { action: 'renew', task_id: 'a-001' }), null)).toEqual({ kind: 'set', taskId: 'a-001' })
    expect(bindingChange(call('backlog_claim', { action: 'release', task_id: 'a-001' }), 'a-001')).toEqual({ kind: 'keep' })
    expect(bindingChange(call('backlog_claim', { action: 'status', task_id: 'a-001' }), null)).toEqual({ kind: 'keep' })
    expect(bindingChange(call('backlog_update_task', { task_id: 'b-001', field: 'status', value: 'in-progress' }), 'a-001')).toEqual({
      kind: 'set',
      taskId: 'b-001',
    })
  })

  test('in-review keeps it; done, archive or back to todo clear it, but only for the bound task', () => {
    expect(bindingChange(call('backlog_update_task', { task_id: 'a-001', field: 'status', value: 'in-review' }), 'a-001')).toEqual({ kind: 'keep' })
    expect(bindingChange(call('backlog_complete_task', { task_id: 'a-001', target_status: 'in-review', human_action: 'x' }), 'a-001')).toEqual({
      kind: 'keep',
    })
    expect(bindingChange(call('backlog_complete_task', { task_id: 'a-001' }), 'a-001')).toEqual({ kind: 'clear' })
    expect(bindingChange(call('backlog_complete_task', { task_id: 'z-001' }), 'a-001')).toEqual({ kind: 'keep' })
    expect(bindingChange(call('backlog_archive_task', { task_id: 'a-001' }), 'a-001')).toEqual({ kind: 'clear' })
    expect(bindingChange(call('backlog_update_task', { task_id: 'a-001', field: 'status', value: 'todo' }), 'a-001')).toEqual({ kind: 'clear' })
    expect(bindingChange(call('backlog_update_task', { task_id: 'a-001', field: 'tldr', value: 'x' }), 'a-001')).toEqual({ kind: 'keep' })
  })

  test('a call the server refused changes nothing', () => {
    expect(bindingChange(call('backlog_pick_task', { task_id: 'a-001' }, false), null)).toEqual({ kind: 'keep' })
  })

  test('success is read from the reply: refusals, not-persisted, claim JSON and denies are failures', () => {
    expect(callSucceeded(T + 'backlog_pick_task', { text: 'Picked `a-001` — T (locked to this session)' })).toBe(true)
    expect(callSucceeded(T + 'backlog_pick_task', { text: R.WRONG_STATUS })).toBe(false)
    expect(callSucceeded(T + 'backlog_update_task', { text: R.NOT_PERSISTED })).toBe(false)
    expect(callSucceeded(T + 'backlog_claim', { text: R.CLAIM_OK })).toBe(true)
    expect(callSucceeded(T + 'backlog_claim', { text: R.CLAIM_CONFLICT })).toBe(false)
    expect(callSucceeded(T + 'backlog_pick_task', { deny: 'no' })).toBe(false)
    expect(callSucceeded(T + 'backlog_pick_task', { result: { content: [{ type: 'text', text: R.WRONG_STATUS }] } })).toBe(false)
  })

  test('clearing an already-empty human_action answers "(not persisted)": a success for that call only', () => {
    const cleared = 'Updated `fx-001` field `human_action` → (not persisted)'
    expect(callSucceeded(T + 'backlog_update_task', { text: cleared }, { task_id: 'fx-001', field: 'human_action', value: '' })).toBe(true)
    expect(callSucceeded(T + 'backlog_update_task', { text: cleared }, { task_id: 'fx-001', field: 'human_action', value: 'x' })).toBe(false)
    expect(callSucceeded(T + 'backlog_update_task', { text: cleared })).toBe(false)
    expect(callSucceeded(T + 'backlog_update_task', { text: R.NOT_PERSISTED }, { task_id: 'tm-audit-031', field: 'status', value: 'in-progress' })).toBe(
      false,
    )
  })

  test('reads never refresh; writes do', () => {
    for (const tool of ['backlog_get_task', 'backlog_list_tasks', 'backlog_status', 'backlog_continuity_items', 'backlog_task_pipeline', 'backlog_handover_list']) {
      expect(isWriteTool(T + tool)).toBe(false)
    }
    for (const tool of ['backlog_update_task', 'backlog_complete_task', 'backlog_pick_task', 'backlog_record_gate', 'backlog_handover_create']) {
      expect(isWriteTool(T + tool)).toBe(true)
    }
  })
})

describe('inference and the stored mirror', () => {
  test('a task id is read from a branch or worktree name, and nothing else is guessed', () => {
    expect(inferTaskId(['feat/tm-audit-030-fixes', 'proj'])).toBe('tm-audit-030')
    expect(inferTaskId(['main', 'unified-chat-022'])).toBe('unified-chat-022')
    expect(inferTaskId(['feat/database-native-foundation', 'taskmaster'])).toBeNull()
    expect(inferTaskId(['', ''])).toBeNull()
  })

  test('stored bindings parse strictly; only binding keys older than 30 days or unreadable are pruned', async () => {
    expect(parseStoredBinding({ taskId: 'a-001', at: 5 })).toEqual({ taskId: 'a-001', at: 5 })
    expect(parseStoredBinding({ taskId: '', at: 5 })).toBeNull()
    expect(parseStoredBinding('a-001')).toBeNull()
    const now = 100 * STALE_MS
    const store = new Map<string, unknown>([
      ['binding:fresh', { taskId: 'a-001', at: now - 1000 }],
      ['binding:stale', { taskId: 'b-001', at: now - STALE_MS - 1 }],
      ['binding:junk', 'x'],
      ['other', { taskId: 'c-001', at: 0 }],
    ])
    const pruned = await pruneBindings(
      {
        storeKeys: async () => [...store.keys()],
        storeGet: async key => store.get(key),
        storeDelete: async key => {
          store.delete(key)
        },
      },
      now,
    )
    expect(pruned).toBe(2)
    expect([...store.keys()].sort()).toEqual(['binding:fresh', 'other'])
  })
})
