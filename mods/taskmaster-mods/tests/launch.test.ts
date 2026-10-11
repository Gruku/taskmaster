// User intent: prove the new-tab launcher builds a safe command on every platform (ported from cm-restore's quoting tests)
// and that a launch that cannot open a tab still leaves the command on the clipboard.
import { describe, expect, test } from 'claude-code/testing'

import {
  COMPACT_PROMPT,
  LAUNCH_STAGGER_MS,
  launchAll,
  launchCommand,
  type LaunchEnv,
  type LaunchIo,
  type LaunchSpec,
  launchSummary,
  macArgv,
  macTerminalFor,
  posixScript,
  psScript,
  resumePrompt,
  utf16leBase64,
  wtArgv,
} from '../hooks/launch'

const RESUME: LaunchSpec = { cwd: "C:\\Work\\O'Brien", title: 'Contract; Phase C', mode: 'resume', sessionId: 'b1c2-d3' }
const FRESH: LaunchSpec = {
  cwd: 'C:\\work\\proj',
  title: 'handover 2026-10-05-shipped',
  mode: 'fresh',
  prompt: resumePrompt('2026-10-05-shipped', "C:\\work\\proj\\.taskmaster\\handovers\\it's.md"),
}
const MAC: LaunchSpec = { ...RESUME, cwd: "/Users/me/O'Brien \"x\"" }

/** A LaunchIo that answers from `exits` (argv[0] → exit code; absent: could not start) and records what it ran and copied. */
function ioOf(env: LaunchEnv, exits: Record<string, number>, copies = true, uname = 'Darwin\n') {
  const runs: string[][] = []
  const copied: string[] = []
  const slept: number[] = []
  const io: LaunchIo = {
    env: async () => env,
    run: async argv => {
      runs.push([...argv])
      const code = exits[argv[0] ?? '']
      return code === undefined ? null : { exitCode: code, stdout: argv[0] === 'uname' ? uname : '' }
    },
    copy: async text => {
      copied.push(text)
      return copies
    },
    sleep: async ms => {
      slept.push(ms)
    },
  }
  return { io, runs, copied, slept }
}

describe('commands', () => {
  test('the fresh prompt names the handover and its path', () => {
    expect(resumePrompt('H-0412', 'C:\\p\\H-0412.md')).toBe('Resume from handover H-0412 (C:\\p\\H-0412.md)')
  })

  test('base64 matches the UTF-16LE encoding pwsh -EncodedCommand expects', () => {
    // "hi" -> 68 00 69 00
    expect(utf16leBase64('hi')).toBe('aABpAA==')
    expect(utf16leBase64('a')).toBe('YQA=')
  })

  test('wt argv carries no raw semicolons and quotes the path for PowerShell', () => {
    const argv = wtArgv(RESUME, 'C:\\cfg')
    expect(argv.some(a => a.includes(';'))).toBe(false)
    expect(argv.slice(0, 6)).toEqual(['wt.exe', '-w', '0', 'nt', '-d', "C:\\Work\\O'Brien"])
    expect(argv).toContain('Contract  Phase C')
    expect(psScript(RESUME, 'C:\\cfg')).toBe("$env:CLAUDE_CONFIG_DIR = 'C:\\cfg'; Set-Location -LiteralPath 'C:\\Work\\O''Brien'; claude --resume b1c2-d3")
    expect(wtArgv({ ...RESUME, cwd: 'C:\\a;b' }, undefined).some(a => a.includes(';'))).toBe(false)
  })

  test('a fresh launch quotes its prompt; compact applies to --resume only', () => {
    expect(psScript(FRESH, undefined)).toBe(
      "Set-Location -LiteralPath 'C:\\work\\proj'; claude 'Resume from handover 2026-10-05-shipped (C:\\work\\proj\\.taskmaster\\handovers\\it''s.md)'",
    )
    expect(psScript({ ...FRESH, compact: true }, undefined)).not.toContain('/compact')
    expect(psScript({ ...RESUME, compact: true }, undefined)).toContain(`claude --resume b1c2-d3 '${COMPACT_PROMPT}'`)
    expect(psScript({ ...FRESH, prompt: '' }, undefined)).toBe("Set-Location -LiteralPath 'C:\\work\\proj'; claude")
  })

  test('typographic single quotes are doubled in PowerShell, so a payload stays inside the literal', () => {
    const evil = { ...FRESH, cwd: 'C:/x\u2019; Write-Output INJECTED; \u2019', prompt: 'a\u2018b\u201A\u201B\u2019; calc; \u2019' }
    const script = psScript(evil, undefined)
    expect(script).toBe("Set-Location -LiteralPath 'C:/x\u2019\u2019; Write-Output INJECTED; \u2019\u2019'; claude 'a\u2018\u2018b\u201A\u201A\u201B\u201B\u2019\u2019; calc; \u2019\u2019'")
    // with every doubled pair removed, no quote is left to end a literal early
    expect(/['\u2018\u2019\u201A\u201B]/.test(script.replace(/(['\u2018\u2019\u201A\u201B])\1/g, '').replace(/^Set-Location -LiteralPath '|'; claude '|'$/g, ''))).toBe(false)
  })

  test('a builder never embeds a session id that is not a plain id', () => {
    expect(() => launchCommand('windows', { ...RESUME, sessionId: 'x; rm -rf' }, undefined)).toThrow()
    expect(() => launchCommand('mac', { ...RESUME, sessionId: '' }, undefined)).toThrow()
  })

  test('posix commands quote for zsh/bash and only set a config dir that was chosen', () => {
    expect(posixScript(MAC, undefined)).toBe(String.raw`cd '/Users/me/O'\''Brien "x"' && claude --resume b1c2-d3`)
    expect(posixScript({ ...MAC, compact: true }, '/Users/me/.claude-work')).toBe(
      String.raw`cd '/Users/me/O'\''Brien "x"' && CLAUDE_CONFIG_DIR='/Users/me/.claude-work' claude --resume b1c2-d3 ` + `'${COMPACT_PROMPT}'`,
    )
    expect(psScript(MAC, undefined)).not.toContain('CLAUDE_CONFIG_DIR')
    expect(launchCommand('mac', MAC, undefined)).toBe(posixScript(MAC, undefined))
    expect(launchCommand('linux', MAC, undefined)).toBe(posixScript(MAC, undefined))
    expect(launchCommand('windows', MAC, undefined)).toBe(psScript(MAC, undefined))
  })

  test('mac launchers: own terminal by TERM_PROGRAM, AppleScript strings escaped', () => {
    expect(macTerminalFor('iTerm.app')).toBe('iterm')
    expect(macTerminalFor('WezTerm')).toBe('wezterm')
    expect(macTerminalFor('Apple_Terminal')).toBe('terminal')
    expect(macTerminalFor(undefined)).toBe('terminal')
    const terminal = macArgv('terminal', MAC, undefined)
    expect(terminal[0]).toBe('osascript')
    expect(terminal).toContain(String.raw`do script "cd '/Users/me/O'\\''Brien \"x\"' && claude --resume b1c2-d3"`)
    const iterm = macArgv('iterm', MAC, undefined)
    expect(iterm).toContain('tell current window to create tab with default profile')
    expect(iterm.filter(a => a === '-e').length).toBe((iterm.length - 1) / 2)
    expect(macArgv('wezterm', MAC, undefined)).toEqual(['wezterm', 'cli', 'spawn', '--cwd', MAC.cwd, '--', '/bin/zsh', '-lc', posixScript(MAC, undefined)])
  })
})

describe('launchAll', () => {
  test('Windows: one wt.exe per spec, 700 ms apart; nothing copied', async () => {
    const { io, runs, copied, slept } = ioOf({ os: 'Windows_NT' }, { 'wt.exe': 0 })
    const r = await launchAll(io, [FRESH, RESUME])
    expect(r).toEqual({ opened: 2, copied: null, failed: [] })
    expect(runs.map(a => a[0])).toEqual(['wt.exe', 'wt.exe'])
    expect(slept).toEqual([LAUNCH_STAGGER_MS])
    expect(copied).toEqual([])
    expect(launchSummary(r)).toBe('Opened 2 tabs')
  })

  test('mac: the own terminal first, then Terminal.app; uname decides the platform', async () => {
    const { io, runs } = ioOf({ termProgram: 'iTerm.app' }, { uname: 0, osascript: 1 })
    const r = await launchAll(io, [MAC])
    expect(runs.map(a => a[0])).toEqual(['uname', 'osascript', 'osascript'])
    expect(runs[1]).toContain('tell application "iTerm"')
    expect(runs[2]).toContain('tell application "Terminal"')
    expect(r.opened).toBe(0)
    expect(r.copied).toBe(posixScript(MAC, undefined))
  })

  test('no terminal opens: every command is copied as one block, one a line, and the toast says to paste', async () => {
    const { io, copied } = ioOf({ os: 'Windows_NT', configDir: 'C:\\cfg' }, {})
    const r = await launchAll(io, [FRESH, RESUME])
    expect(copied).toEqual([`${psScript(FRESH, 'C:\\cfg')}\n${psScript(RESUME, 'C:\\cfg')}`])
    expect(launchSummary(r)).toBe('Copied 2 commands — paste each into a new terminal')
    expect(launchSummary({ opened: 0, copied: psScript(FRESH, undefined), failed: [] })).toBe('Copied — paste into a new terminal')
  })

  test('linux has no launcher: the command is copied; with no clipboard either, the titles are named', async () => {
    const { io, runs } = ioOf({ termProgram: 'iTerm.app' }, { uname: 0, osascript: 0 }, false, 'Linux\n')
    const r = await launchAll(io, [{ ...MAC, title: 'Pricing' }])
    expect(runs.map(a => a[0])).toEqual(['uname'])
    expect(r).toEqual({ opened: 0, copied: null, failed: ['Pricing'] })
    expect(launchSummary(r)).toBe('Could not open: Pricing')
  })

  test('a spec with no folder or no usable session id is refused before anything runs', async () => {
    const { io, runs } = ioOf({ os: 'Windows_NT' }, { 'wt.exe': 0 })
    const r = await launchAll(io, [
      { ...RESUME, title: 'A', sessionId: 'x; rm -rf' },
      { ...FRESH, title: 'B', cwd: '' },
      FRESH,
    ])
    expect(r).toEqual({ opened: 1, copied: null, failed: ['A (no session id)', 'B (no folder)'] })
    expect(runs).toHaveLength(1)
    expect(launchSummary(r)).toBe('Opened 1 tab · Could not open: A (no session id), B (no folder)')
    expect(await launchAll(io, [])).toEqual({ opened: 0, copied: null, failed: [] })
  })

  test('control characters in the folder, prompt or config dir refuse the launch and run nothing', async () => {
    const { io, runs } = ioOf({ os: 'Windows_NT' }, { 'wt.exe': 0 })
    const r = await launchAll(io, [
      { ...FRESH, title: 'P', prompt: 'hi\x03there' },
      { ...FRESH, title: 'C', cwd: 'C:\a\x1bb' },
    ])
    expect(r).toEqual({ opened: 0, copied: null, failed: ['P (control characters)', 'C (control characters)'] })
    const withDir = ioOf({ os: 'Windows_NT', configDir: 'C:\cfg\x15' }, { 'wt.exe': 0 })
    expect((await launchAll(withDir.io, [FRESH])).failed).toEqual(['handover 2026-10-05-shipped (control characters)'])
    expect(runs).toHaveLength(0)
    expect(withDir.runs).toHaveLength(0)
  })

  test('C1 controls and line separators are refused too', async () => {
    const { io, runs } = ioOf({ os: 'Windows_NT' }, { 'wt.exe': 0 })
    const r = await launchAll(io, [
      { ...FRESH, title: 'A', prompt: 'x\u009by' },
      { ...FRESH, title: 'B', cwd: 'C:\a\u2028b' },
    ])
    expect(r.failed).toEqual(['A (control characters)', 'B (control characters)'])
    expect(runs).toHaveLength(0)
  })

  test('a backslash is refused on mac (fish would end the quote early) and fine on windows', async () => {
    const evil = { ...FRESH, title: 'E', prompt: "a\'; echo PWNED; #" }
    const mac = ioOf({ termProgram: 'iTerm.app' }, { uname: 0, osascript: 0 })
    expect(await launchAll(mac.io, [evil])).toEqual({ opened: 0, copied: null, failed: ['E (backslash)'] })
    expect(mac.runs.map(a => a[0])).toEqual(['uname'])
    const win = ioOf({ os: 'Windows_NT' }, { 'wt.exe': 0 })
    expect(await launchAll(win.io, [evil])).toEqual({ opened: 1, copied: null, failed: [] })
  })
})
