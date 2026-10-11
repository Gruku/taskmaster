// User intent: remember which handover a session was resumed from (spec §10.5) — written before a `--resume` launch, by a
// fresh session's "Resume from handover" first prompt, or by handovers `r` — so the band and the Session tab can say it.
import type { PhaseStore } from './phases'

/** Matches resumePrompt's text (launch.ts) at the start of a prompt; group 1 is the handover id. */
export const RESUME_PROMPT_RE = /^\s*Resume from handover ([A-Za-z0-9][\w.-]*)/

export const resumedKey = (sessionId: string): string => `resumed-from:${sessionId}`

const HANDOVER_ID = /^[A-Za-z0-9][\w.-]*$/

/** The handover id a prompt resumes from, or null. */
export function resumedIdOf(prompt: string): string | null {
  return RESUME_PROMPT_RE.exec(prompt)?.[1] ?? null
}

export async function recordResumedFrom(store: PhaseStore, sessionId: string, handoverId: string): Promise<void> {
  if (sessionId === '' || !HANDOVER_ID.test(handoverId)) return
  await store.set(resumedKey(sessionId), handoverId)
}

/** The handover this session was resumed from; null when none was recorded or the entry is unreadable. */
export async function readResumedFrom(store: PhaseStore, sessionId: string): Promise<string | null> {
  if (sessionId === '') return null
  const stored = await store.get(resumedKey(sessionId))
  return typeof stored === 'string' && HANDOVER_ID.test(stored) ? stored : null
}
