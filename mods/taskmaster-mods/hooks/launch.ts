// User intent: open a Claude Code session in a new terminal tab from any taskmaster-mods surface (Session `w`, handovers `w`,
// restore) — fresh with a "Resume from handover" prompt, or `--resume <id>` — ported from cm-restore's launcher; when no
// terminal can be opened, the command goes on the clipboard instead, never lost.

/** Session ids are uuids: anything else never reaches a command line. */
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9-]*$/
export const isSessionId = (id: string): boolean => SESSION_ID.test(id)
export const LAUNCH_STAGGER_MS = 700
export const COMPACT_PROMPT = '/compact Keep where the work stopped, open decisions, branches and worktrees, and the next step.'

export type LaunchSpec = {
  readonly cwd: string
  readonly title: string
  readonly mode: 'fresh' | 'resume'
  /** fresh: the session's first prompt. */
  readonly prompt?: string
  /** resume: the session to reopen. */
  readonly sessionId?: string
  /** resume: open with /compact as the first prompt. */
  readonly compact?: boolean
}
/** `copied`: the commands put on the clipboard for what could not be opened; `failed`: neither opened nor copied. */
export type LaunchResult = { readonly opened: number; readonly copied: string | null; readonly failed: readonly string[] }
export type Platform = 'windows' | 'mac' | 'linux'
/** The environment a launch reads, each from its own literal `$.env.get`. */
export type LaunchEnv = { readonly os?: string; readonly termProgram?: string; readonly configDir?: string }
export type LaunchIo = {
  readonly env: () => Promise<LaunchEnv>
  /** A command by its argv; null when it could not start. */
  readonly run: (argv: readonly string[]) => Promise<{ readonly exitCode: number; readonly stdout: string } | null>
  readonly copy: (text: string) => Promise<boolean>
  readonly sleep: (ms: number) => Promise<void>
}

/** The fresh session's first prompt; RESUME_PROMPT_RE (resumed.ts) reads the id back out of it. */
export function resumePrompt(handoverId: string, path: string): string {
  return `Resume from handover ${handoverId} (${path})`
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/** What `pwsh -EncodedCommand` takes: the script as UTF-16LE, base64. */
export function utf16leBase64(text: string): string {
  const bytes: number[] = []
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i)
    bytes.push(c & 0xff, c >> 8)
  }
  let out = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const n = ((bytes[i] ?? 0) << 16) | ((bytes[i + 1] ?? 0) << 8) | (bytes[i + 2] ?? 0)
    out += B64.charAt((n >> 18) & 63) + B64.charAt((n >> 12) & 63)
    out += i + 1 < bytes.length ? B64.charAt((n >> 6) & 63) : '='
    out += i + 2 < bytes.length ? B64.charAt(n & 63) : '='
  }
  return out
}

/** PowerShell reads U+2018, U+2019, U+201A and U+201B as single quotes too: all five are doubled. */
export const psQuote = (s: string): string => `'${s.replace(/['\u2018\u2019\u201A\u201B]/g, '$&$&')}'`
export const shQuote = (s: string): string => `'${s.replace(/'/g, `'\\''`)}'`
export const asQuote = (s: string): string => `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`

/** `claude` and what follows it: `--resume <id>` (with /compact when asked), or the fresh prompt quoted. */
function claudeLine(spec: LaunchSpec, quote: (s: string) => string): string {
  if (spec.mode === 'resume' && !SESSION_ID.test(spec.sessionId ?? '')) throw new Error('launch: unusable session id')
  if (spec.mode === 'resume') return `claude --resume ${spec.sessionId ?? ''}${spec.compact === true ? ` ${quote(COMPACT_PROMPT)}` : ''}`
  return spec.prompt === undefined || spec.prompt === '' ? 'claude' : `claude ${quote(spec.prompt)}`
}

/** PowerShell: the Windows Terminal tab, and the Windows clipboard fallback. Only a config dir the person chose is set. */
export function psScript(spec: LaunchSpec, dir: string | undefined): string {
  return [...(dir ? [`$env:CLAUDE_CONFIG_DIR = ${psQuote(dir)}`] : []), `Set-Location -LiteralPath ${psQuote(spec.cwd)}`, claudeLine(spec, psQuote)].join('; ')
}

/** zsh / bash: macOS and Linux. */
export function posixScript(spec: LaunchSpec, dir: string | undefined): string {
  const env = dir ? `CLAUDE_CONFIG_DIR=${shQuote(dir)} ` : ''
  return `cd ${shQuote(spec.cwd)} && ${env}${claudeLine(spec, shQuote)}`
}

export function launchCommand(p: Platform, spec: LaunchSpec, dir: string | undefined): string {
  return p === 'windows' ? psScript(spec, dir) : posixScript(spec, dir)
}

/** wt.exe splits its own command line on ';', so the script travels base64-encoded and no argument carries a raw ';'. */
export function wtArgv(spec: LaunchSpec, dir: string | undefined): string[] {
  const title = spec.title.replace(/[;"]/g, ' ').slice(0, 40)
  const at = spec.cwd.includes(';') ? [] : ['-d', spec.cwd]
  return ['wt.exe', '-w', '0', 'nt', ...at, '--title', title, 'pwsh', '-NoExit', '-EncodedCommand', utf16leBase64(psScript(spec, dir))]
}

export type MacTerminal = 'iterm' | 'wezterm' | 'terminal'

export function macTerminalFor(termProgram: string | undefined): MacTerminal {
  if (termProgram === 'iTerm.app') return 'iterm'
  if (termProgram === 'WezTerm') return 'wezterm'
  return 'terminal'
}

/** argv that opens the command in a new tab (iTerm2, WezTerm) or window (Terminal.app). WezTerm runs it under zsh -l (Homebrew's PATH lives in .zprofile): posixScript's quoting is valid zsh, not fish's. */
export function macArgv(term: MacTerminal, spec: LaunchSpec, dir: string | undefined): string[] {
  const cmd = posixScript(spec, dir)
  if (term === 'wezterm') return ['wezterm', 'cli', 'spawn', '--cwd', spec.cwd, '--', '/bin/zsh', '-lc', cmd]
  const script =
    term === 'iterm'
      ? [
          'tell application "iTerm"',
          'activate',
          'if (count of windows) = 0 then',
          'create window with default profile',
          'else',
          'tell current window to create tab with default profile',
          'end if',
          `tell current session of current window to write text ${asQuote(cmd)}`,
          'end tell',
        ]
      : ['tell application "Terminal"', 'activate', `do script ${asQuote(cmd)}`, 'end tell']
  return ['osascript', ...script.flatMap(line => ['-e', line])]
}

export async function platformOf(env: LaunchEnv, run: LaunchIo['run']): Promise<Platform> {
  if (env.os === 'Windows_NT') return 'windows'
  return (await run(['uname', '-s']))?.stdout.trim() === 'Darwin' ? 'mac' : 'linux'
}

const CONTROL = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/

/**
 * Why a spec cannot be launched at all ('' when it can). Control characters (Ctrl-C, ESC, C1, line separators) can escape
 * quoting in a typed terminal; on macOS the script is typed into the person's own shell, where a backslash inside single
 * quotes is not safe in fish, so it is refused there.
 */
export function launchRefusal(spec: LaunchSpec, p: Platform, dir: string | undefined): string {
  if (spec.cwd === '') return 'no folder'
  const typed = [spec.cwd, spec.prompt ?? '', dir ?? '']
  if (typed.some(t => CONTROL.test(t))) return 'control characters'
  if (p === 'mac' && typed.some(t => t.includes('\\'))) return 'backslash'
  if (spec.mode === 'resume' && !SESSION_ID.test(spec.sessionId ?? '')) return 'no session id'
  return ''
}

async function openOne(io: LaunchIo, p: Platform, env: LaunchEnv, spec: LaunchSpec, dir: string | undefined): Promise<boolean> {
  const ran = async (argv: readonly string[]) => (await io.run(argv))?.exitCode === 0
  if (p === 'windows') return ran(wtArgv(spec, dir))
  if (p === 'mac') {
    // The person's own terminal first; Terminal.app is always there. First use asks for Automation permission.
    const preferred = macTerminalFor(env.termProgram)
    for (const term of preferred === 'terminal' ? (['terminal'] as const) : ([preferred, 'terminal'] as const)) {
      if (await ran(macArgv(term, spec, dir))) return true
    }
  }
  return false
}

/**
 * Opens each spec in a new tab, 700 ms apart. What could not be opened is copied as one block of commands (one a line);
 * what could not be copied either is named in `failed`, as is a spec with no folder or session id.
 */
export async function launchAll(io: LaunchIo, specs: readonly LaunchSpec[]): Promise<LaunchResult> {
  if (specs.length === 0) return { opened: 0, copied: null, failed: [] }
  const env = await io.env()
  const p = await platformOf(env, io.run)
  const dir = env.configDir || undefined
  let opened = 0
  let tried = 0
  const left: LaunchSpec[] = []
  const failed: string[] = []
  for (const spec of specs) {
    const why = launchRefusal(spec, p, dir)
    if (why !== '') {
      failed.push(`${spec.title} (${why})`)
      continue
    }
    if (tried > 0) await io.sleep(LAUNCH_STAGGER_MS)
    tried += 1
    if (await openOne(io, p, env, spec, dir)) opened += 1
    else left.push(spec)
  }
  if (left.length === 0) return { opened, copied: null, failed }
  const text = left.map(spec => launchCommand(p, spec, dir)).join('\n')
  if (await io.copy(text)) return { opened, copied: text, failed }
  return { opened, copied: null, failed: [...failed, ...left.map(spec => spec.title)] }
}

/** The one toast a launch ends with. */
export function launchSummary(r: LaunchResult): string {
  const parts: string[] = []
  if (r.opened > 0) parts.push(r.opened === 1 ? 'Opened 1 tab' : `Opened ${r.opened} tabs`)
  if (r.copied !== null) {
    const n = r.copied.split('\n').length
    parts.push(n === 1 ? 'Copied — paste into a new terminal' : `Copied ${n} commands — paste each into a new terminal`)
  }
  if (r.failed.length > 0) parts.push(`Could not open: ${r.failed.join(', ')}`)
  return parts.length === 0 ? 'Nothing to open' : parts.join(' · ')
}
