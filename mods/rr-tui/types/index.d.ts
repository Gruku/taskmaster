// User intent: the $.rr contract — Reality Reprojection for terminal mods: tokens for the active polarity and finished,
// non-interactive element trees any mod can place in its own drawing. Buttons stay with the consumer: they can't cross a noun.
export type RrPolarity = 'dark' | 'light' | 'survivalist'
export type RrTone = 'success' | 'warning' | 'critical' | 'info' | 'signature'
export type RrSignalKind = 'success' | 'warning' | 'critical' | 'info'
/**
 * A surface step. `'page'` paints RR bg-page and is for pane roots only: a pane paints its own ground because the host's pane
 * background is not RR's page. The band never paints its ground, so a band never uses `'page'`.
 */
export type RrLevel = 'page' | 'raised' | 'overlay' | 'recessed'
export type RrGround = 'page' | 'raised' | 'overlay'
export type RrTreatment = 'outline' | 'chip'
export type RrStrength = 12 | 24
export type RrNode =
  | string
  | { readonly type: string; readonly props?: Readonly<Record<string, unknown>>; readonly children?: readonly unknown[] }
export type RrBoxProps = {
  readonly flexDirection?: 'row' | 'column'
  readonly backgroundColor?: string
  readonly borderStyle?: 'single' | 'round' | 'bold'
  readonly borderColor?: string
  readonly paddingX?: number
  readonly width?: number | string
}
/**
 * RR's button recipe: the whole surface presses. The treatment's wrapper Box (`button()`: a chip's tint on its ground, or
 * the round tone outline for the one primary action) holds ONE plain Button carrying the label. `buttonProps()` gives that
 * Button its `hotkey` (one digit or one lowercase letter), so the engine draws `d: back to agent`, and a `hover` that turns
 * the label bold `foreground-bold` (style only, no motion). Give the wrapper Box a unique `key`: a Button's `hover` only
 * applies inside a keyed Box. A noun can't hand out a Button or its press handler, so the consumer draws the Button.
 *
 * @example
 * const wrap = await $.rr.button({ treatment: 'chip', tone: 'warning', on: 'raised' })
 * const press = await $.rr.buttonProps({ key: 'a' })
 * <Box key="back-box" {...wrap}>
 *   <Button key="back" {...press} label="back to agent" onPress={back} />
 * </Box>
 * // The primary: button({ treatment: 'outline', tone: 'success' }) around `d: done`. Beside its 3-row outline, lay the
 * // row out with alignItems="flex-start" so the chips stay one row tall.
 */
export type RrButtonProps = {
  readonly plain: true
  /** Present when `key` was given. Two Buttons on one hotkey in a site clash (the later wins). */
  readonly hotkey?: string
  readonly hover: { readonly bold: true; readonly color: string }
}
export type RrTokens = {
  readonly polarity: RrPolarity
  readonly surface: Readonly<Record<RrLevel, string>>
  readonly fg: { readonly bold: string; readonly default: string; readonly subtle: string; readonly disabled: string }
  readonly border: { readonly default: string; readonly subtle: string; readonly strong: string; readonly focus: string }
  readonly signatureText: string
  readonly tone: Readonly<Record<RrTone, string>>
  readonly tint12: Readonly<Record<RrGround, Readonly<Record<RrTone, string>>>>
  readonly tint24: Readonly<Record<RrGround, Readonly<Record<RrTone, string>>>>
  readonly keycap: Readonly<Record<RrTone, { readonly bg: string; readonly ink: string }>>
}
export type Rr = {
  tokens: () => Promise<RrTokens>
  polarity: () => Promise<RrPolarity>
  surface: (args: { level: RrLevel; children: readonly RrNode[]; width?: number | string; padX?: number }) => Promise<RrNode>
  surfaceProps: (args: { level: RrLevel; width?: number | string; padX?: number }) => Promise<RrBoxProps>
  label: (args: { text: string }) => Promise<RrNode>
  signal: (args: { kind: RrSignalKind; word: string; detail?: string }) => Promise<RrNode>
  row: (args: { cells: readonly RrNode[]; emphasis?: 'strong' | 'quiet' }) => Promise<RrNode>
  rule: (args: { width: number }) => Promise<RrNode>
  button: (args: { treatment: RrTreatment; tone: RrTone; on?: RrGround }) => Promise<RrBoxProps>
  buttonProps: (args: { key?: string }) => Promise<RrButtonProps>
  keycap: (args: { key: string; tone: RrTone }) => Promise<RrNode>
  chip: (args: { text: string; tone: RrTone; strength: RrStrength; on?: RrGround }) => Promise<RrNode>
}

declare module 'claude-code' {
  interface EngineInterface {
    rr: Rr
  }
  interface PluginState {
    'rr-tui': { polarity: RrPolarity; override: RrPolarity | 'none' }
  }
}
