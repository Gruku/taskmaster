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
 * RR's button with its key shown: a tinted chip (or, for the one primary action, the round tone outline) holding a keycap,
 * then a bold `foreground-bold` label. A Button's label takes no colour or weight, so the label is RR's own Text and the
 * Button carries only the key. A noun can't hand out a Button, so the consumer draws it inside `keycap`: a non-plain Button
 * whose label is the key, armed with `hotkey` = the key. It draws `[ d ]` (a plain Button with a hotkey would draw `d: d`).
 * Key or click presses it.
 *
 * @example
 * const p = await $.rr.keyedButton({ treatment: 'chip', tone: 'warning', on: 'raised', label: 'back to agent' })
 * <Box key="back-box" {...p.box}>
 *   <Box {...p.keycap}><Button key="back" label="a" hotkey="a" onPress={back} /></Box>
 *   {p.label}
 * </Box>
 * // Beside the 3-row outline, lay the row out with alignItems="flex-start" so chips stay one row tall.
 */
export type RrKeyedButton = {
  /** The button's own Box: the treatment of `button()` laid out as a row (an outline also pads 1). */
  readonly box: RrBoxProps
  /** The keycap Box around the consumer's Button: the tone's 24% tint on its ground (survivalist: a value step, no hue). */
  readonly keycap: RrBoxProps
  /** The label Text after the keycap: bold, foreground-bold, led by one space. */
  readonly label: RrNode
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
  keyedButton: (args: { treatment: RrTreatment; tone: RrTone; on?: RrGround; label: string }) => Promise<RrKeyedButton>
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
