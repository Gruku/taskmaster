// User intent: a session resumed from a handover can always say which one — the prompt the launcher writes is read back
// exactly, and the store entry round-trips per session id and tolerates junk.
import { describe, expect, test } from 'claude-code/testing'

import { resumePrompt } from '../hooks/launch'
import type { PhaseStore } from '../hooks/phases'
import { readResumedFrom, recordResumedFrom, RESUME_PROMPT_RE, resumedIdOf, resumedKey } from '../hooks/resumed'

function memoryStore(seed: Record<string, unknown> = {}): { store: PhaseStore; values: Map<string, unknown> } {
  const values = new Map(Object.entries(seed))
  return {
    values,
    store: {
      get: async key => values.get(key),
      set: async (key, value) => {
        values.set(key, value)
      },
      delete: async key => {
        values.delete(key)
      },
    },
  }
}

describe('resumed from', () => {
  test('the launcher prompt reads back to its handover id; other prompts do not match', () => {
    const prompt = resumePrompt('2026-10-05-shipped-unified-chat-022', 'C:\\p\\.taskmaster\\handovers\\2026-10-05-shipped-unified-chat-022.md')
    expect(RESUME_PROMPT_RE.test(prompt)).toBe(true)
    expect(resumedIdOf(prompt)).toBe('2026-10-05-shipped-unified-chat-022')
    expect(resumedIdOf('  Resume from handover H-0412 (x)')).toBe('H-0412')
    expect(resumedIdOf('Please Resume from handover H-0412')).toBeNull()
    expect(resumedIdOf('Resume from handover')).toBeNull()
  })

  test('recorded per session id under resumed-from:<id>, read back; junk and empty ids read as none', async () => {
    const { store, values } = memoryStore({ [resumedKey('junk')]: { handoverId: 3 } })
    expect(resumedKey('s-1')).toBe('resumed-from:s-1')
    await recordResumedFrom(store, 's-1', 'H-0412')
    expect(values.get('resumed-from:s-1')).toBe('H-0412')
    expect(await readResumedFrom(store, 's-1')).toBe('H-0412')
    expect(await readResumedFrom(store, 's-2')).toBeNull()
    expect(await readResumedFrom(store, 'junk')).toBeNull()
    await recordResumedFrom(store, '', 'H-1')
    await recordResumedFrom(store, 's-3', 'bad id; rm')
    expect([...values.keys()].sort()).toEqual(['resumed-from:junk', 'resumed-from:s-1'])
    expect(await readResumedFrom(store, '')).toBeNull()
  })
})
