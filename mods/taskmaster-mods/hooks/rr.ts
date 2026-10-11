// User intent: the $.rr types taskmaster-mods draws with, derived from rr-tui's contract (laid as a dependency), never copied.
import type { EngineInterface } from 'claude-code'

export type Rr = EngineInterface['rr']
export type RrTokens = Awaited<ReturnType<Rr['tokens']>>
export type RrNode = Awaited<ReturnType<Rr['label']>>
export type RrTone = Parameters<Rr['keycap']>[0]['tone']
export type RrBoxProps = Awaited<ReturnType<Rr['button']>>
export type RrButtonProps = Awaited<ReturnType<Rr['buttonProps']>>
export type RrTreatment = Parameters<Rr['button']>[0]['treatment']
export type RrGround = NonNullable<Parameters<Rr['button']>[0]['on']>
export type RrStrength = Parameters<Rr['chip']>[0]['strength']
export type RrSignalKind = Parameters<Rr['signal']>[0]['kind']
export type RrTextProps = Awaited<ReturnType<Rr['badgeProps']>>
