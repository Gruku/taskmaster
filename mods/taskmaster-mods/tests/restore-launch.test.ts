// User intent: prove the restore pane reopens sessions the way the spec says (§10.3, §10.5, §10.6): fresh from a handover
// or --resume, a --resume recorded as `resumed-from` before it launches, one staggered launch for the ticked, a paste-ready
// copy built by the hardened launcher, and a cached state / next / blocker brief from the briefModel.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_BRIEF } from '../hooks/demo'
import { createRestoreFlows } from '../hooks/restore/flows'
import { briefKey, briefPrompt, briefText, copyTextOf, launchSpecOf, rowOf } from '../hooks/restore/model'
import { fakeRestore, NO_REFS, refsReply, ROOT, scanOf, session, T0 } from './fixtures/restore'

const HO = { id: 'h-1', path: 'C:\\work\\proj\\.taskmaster\\handovers\\h-1.md', tldr: 'Half done', next: 'Finish the rows' }
const SCAN = scanOf([
  session('heavy', { closedEpoch: T0, title: 'Heavy one', handovers: [HO], ctx: 431_000, taskIds: ['t-1'] }),
  session('light', { closedEpoch: T0 - 60, title: 'Light one', handovers: [{ ...HO, id: 'h-2', path: '' }], ctx: 90_000 }),
  session('plain', { closedEpoch: T0 - 120, title: 'Plain one', interrupted: true }),
  session('gone', { closedEpoch: T0 - 180, title: 'Gone one', cwdExists: false, cwd: 'C:\\gone' }),
  session('bad;id', { closedEpoch: T0 - 240, title: 'Bad id', handovers: [{ ...HO, id: 'h-3', path: '' }], interrupted: true }),
  session('ctl', { closedEpoch: T0 - 300, title: 'Control', cwd: 'C:\\work\\proj\u2028x', interrupted: true }),
])
const REFS = refsReply(['["t","t-1","in-progress","full","p1"]  Work', '["h","h-1","open"]', '["h","h-2","open"]', '["h","h-3","open"]'])

async function opened(over: Parameters<typeof fakeRestore>[0] = {}) {
  const f = fakeRestore({ scan: SCAN, tm: [REFS], ...over })
  const flows = createRestoreFlows(f.deps)
  await flows.open(4)
  return { f, flows }
}

describe('launch specs and the copy line (pure)', () => {
  const heavy = rowOf(SCAN.sessions[0]!, NO_REFS)
  const light = rowOf(SCAN.sessions[1]!, NO_REFS)
  test('fresh: a new session told to resume from the handover; --resume: the session id, compact only there', () => {
    expect(launchSpecOf(heavy, 'fresh', true, ROOT)).toEqual({ cwd: ROOT, title: 'Heavy one', mode: 'fresh', prompt: `Resume from handover h-1 (${HO.path})` })
    expect(launchSpecOf(heavy, 'resume', true, ROOT)).toEqual({ cwd: ROOT, title: 'Heavy one', mode: 'resume', sessionId: 'heavy', compact: true })
    expect(launchSpecOf(light, 'fresh', false, ROOT).prompt).toBe('Resume from handover h-2 (C:\\work\\proj\\.taskmaster\\handovers\\h-2.md)')
  })
  test('the copy line is the launcher\'s own command for the platform: folder first, every argument quoted', () => {
    expect(copyTextOf(heavy, 'resume', ROOT, 'windows')).toBe("Set-Location -LiteralPath 'C:\\work\\proj'; claude --resume heavy")
    expect(copyTextOf(heavy, 'fresh', ROOT, 'windows')).toBe(`Set-Location -LiteralPath 'C:\\work\\proj'; claude 'Resume from handover h-1 (${HO.path})'`)
    expect(copyTextOf(heavy, 'resume', ROOT, 'linux')).toBe("cd 'C:\\work\\proj' && claude --resume heavy")
  })
  test('a session id the launcher would not accept never becomes a copy line', () => {
    expect(() => copyTextOf(rowOf(SCAN.sessions[4]!, NO_REFS), 'resume', ROOT, 'windows')).toThrow()
  })
})

describe('resume', () => {
  test('r on a heavy handed-off session opens it fresh; nothing is recorded; it is marked reopened and unticked', async () => {
    const { f, flows } = await opened()
    await flows.resume(['heavy'])
    expect(f.launches).toEqual([[{ cwd: ROOT, title: 'Heavy one', mode: 'fresh', prompt: `Resume from handover h-1 (${HO.path})` }]])
    expect([...f.store.keys()].filter(k => k.startsWith('resumed-from:'))).toEqual([])
    expect(f.view.opened).toEqual({ heavy: T0 + 3600 })
    expect(f.view.selected).toEqual({ heavy: false })
    expect(f.view.launching).toEqual([])
  })

  test('a --resume records resumed-from:<id> before the launch; compact rides along', async () => {
    const { f, flows } = await opened()
    await flows.toggleCompact()
    let recordedFirst = false
    f.launchResult = specs => {
      recordedFirst = f.store.get('resumed-from:light') === 'h-2'
      return { opened: specs.length, copied: null, failed: [] }
    }
    await flows.resume(['light'])
    expect(recordedFirst).toBe(true)
    expect(f.launches[0]).toEqual([{ cwd: ROOT, title: 'Light one', mode: 'resume', sessionId: 'light', compact: true }])
  })

  test('a --resume of an unusable session id is refused before anything is recorded or launched', async () => {
    const { f, flows } = await opened()
    await flows.resume(['bad;id'])
    expect(f.toasts.at(-1)).toBe('Bad id: unusable session id')
    expect(f.launches).toEqual([])
    expect([...f.store.keys()]).toEqual([])
  })

  test('g launches every ticked session at once; what cannot reopen says why and stays out', async () => {
    const { f, flows } = await opened()
    await flows.resume(['heavy', 'gone', 'plain'])
    expect(f.toasts).toEqual(['Gone one: its folder is gone (C:\\gone)'])
    expect(f.launches[0]?.map(s => s.title)).toEqual(['Heavy one', 'Plain one'])
    await flows.resume([])
    expect(f.toasts.at(-1)).toBe('Nothing ticked to resume')
  })

  test('a launch that opened fewer tabs than asked marks nothing reopened', async () => {
    const { f, flows } = await opened()
    f.launchResult = specs => ({ opened: specs.length - 1, copied: null, failed: ['Plain one'] })
    await flows.resume(['heavy', 'plain'])
    expect(f.view.opened).toEqual({})
    expect(f.view.launching).toEqual([])
  })

  test('demo records nothing in the store', async () => {
    const { f, flows } = await opened({ source: 'demo' })
    await flows.resume(['demo-docs'])
    expect(f.launches).toHaveLength(1)
    expect([...f.store.keys()]).toEqual([])
  })
})

describe('copy', () => {
  test('c copies the reopen line and says so', async () => {
    const { f, flows } = await opened()
    await flows.copy('plain')
    expect(f.copies).toEqual(["Set-Location -LiteralPath 'C:\\work\\proj'; claude --resume plain"])
    expect(f.toasts).toEqual(["Copied: Set-Location -LiteralPath 'C:\\work\\proj'; claude --resume plain"])
  })

  test('the launcher\'s refusals hold for copy too: a control character or bad id copies nothing', async () => {
    const { f, flows } = await opened()
    await flows.copy('ctl')
    await flows.copy('bad;id')
    expect(f.copies).toEqual([])
    expect(f.toasts).toEqual(['Control: cannot copy (control characters)', 'Bad id: cannot copy (no session id)'])
  })
})

describe('brief', () => {
  test('the prompt is tag-structured over the digest; the reply becomes State / Next / Blocker lines', () => {
    const row = { ...rowOf(SCAN.sessions[0]!, NO_REFS), status: 'handed-off' as const, handoverStatus: 'open' }
    const prompt = briefPrompt(row, { first: 'USER: start', tail: 'ASSISTANT: stopped' })
    expect(prompt).toContain('<session title="Heavy one" branch="main" status="handed-off" task="t-1"/>')
    expect(prompt).toContain('<handover id="h-1" status="open">Half done | next: Finish the rows</handover>')
    expect(prompt).toContain('<transcript_tail>ASSISTANT: stopped</transcript_tail>')
    expect(prompt).toContain('<output_format>')
    expect(briefText('<state>Parser half done.</state><next>Finish rows.</next><blocker>none</blocker>')).toBe('State: Parser half done.\nNext: Finish rows.')
    expect(briefText('<state>s</state><blocker>Needs a key.</blocker>')).toBe('State: s\nNext: ?\nBlocker: Needs a key.')
    expect(briefText('  free text  ')).toBe('free text')
    expect(briefKey(row)).toBe(`brief:heavy:${T0}`)
  })

  test('i briefs once, caches by close, and hides on the second press', async () => {
    const { f, flows } = await opened()
    await flows.toggleBrief('plain')
    expect(f.view.briefs).toEqual({ plain: 'State: Stopped at the parser.\nNext: Finish the error rows.' })
    expect(f.prompts).toHaveLength(1)
    expect(f.runs.at(-1)?.slice(3)).toEqual(['digest', '--config-dir', 'C:\\Users\\me\\.claude', '--session', 'plain'])
    expect(f.store.get(`brief:plain:${T0 - 120}`)).toBe('State: Stopped at the parser.\nNext: Finish the error rows.')
    await flows.toggleBrief('plain')
    expect(f.view.briefs).toEqual({})
    await flows.toggleBrief('plain')
    expect(f.prompts).toHaveLength(1)
    expect(f.view.briefing).toEqual([])
  })

  test('g briefs every ticked one without a brief; failures read as the brief', async () => {
    const { f, flows } = await opened({ digest: { error: 'session not found' } })
    f.complete = () => ({ ok: false, reason: 'api-error' })
    await flows.brief(['plain', 'light'])
    expect(f.view.briefs).toEqual({ plain: 'Brief failed: session not found', light: 'Brief failed: session not found' })
    await flows.brief([])
    expect(f.toasts.at(-1)).toBe('Nothing ticked to brief')
  })

  test('demo briefs are canned and never run the scanner or a model', async () => {
    const { f, flows } = await opened({ source: 'demo' })
    await flows.toggleBrief('demo-u5')
    expect(f.view.briefs['demo-u5']).toBe(DEMO_BRIEF)
    expect(f.prompts).toEqual([])
  })
})
