// User intent: the $.rr types taskmaster-tui draws with, derived from rr-tui's contract (laid as a dependency), never copied.
import type { EngineInterface } from 'claude-code'

export type Rr = EngineInterface['rr']
export type RrTokens = Awaited<ReturnType<Rr['tokens']>>
export type RrNode = Awaited<ReturnType<Rr['label']>>
export type RrTone = Parameters<Rr['keycap']>[0]['tone']
export type RrBoxProps = Awaited<ReturnType<Rr['button']>>
export type RrButtonProps = Awaited<ReturnType<Rr['buttonProps']>>
export type RrTreatment = Parameters<Rr['button']>[0]['treatment']
